#include "ghost_config.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_http_client.h"
#include "esp_random.h"
#include "esp_system.h"
#include "nvs.h"
#include "cJSON.h"
#include "lwip/inet.h"

static EventGroupHandle_t events;
static ghost_status_fn status_fn;
static ghost_config_t pending;
static char csrf[33], pair_code[7];
static bool submitting;
static const int GOT_IP = 1;

bool ghost_wifi_ready(void) { return events && (xEventGroupGetBits(events) & GOT_IP); }

static void network_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) xEventGroupSetBits(events, GOT_IP);
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(events, GOT_IP);
        esp_wifi_connect();
    }
}

static bool join(const ghost_config_t *cfg)
{
    wifi_config_t wifi = {0};
    memcpy(wifi.sta.ssid, cfg->ssid, strlen(cfg->ssid));
    memcpy(wifi.sta.password, cfg->password, strlen(cfg->password));
    wifi.sta.pmf_cfg.capable = true;
    xEventGroupClearBits(events, GOT_IP);
    if (esp_wifi_set_config(WIFI_IF_STA, &wifi) != ESP_OK) return false;
    esp_wifi_connect();
    return xEventGroupWaitBits(events, GOT_IP, false, true, pdMS_TO_TICKS(20000)) & GOT_IP;
}

// First release intentionally supports only HTTP on an explicit private LAN IPv4 address.
// No credentials may be posted to a redirected, public or userinfo URL.
static bool server_ok(const char *url)
{
    char host[32], tail = 0;
    unsigned port = 8780;
    if (strncmp(url, "http://", 7)) return false;
    const char *p = url + 7;
    const char *colon = strchr(p, ':');
    size_t len = colon ? (size_t)(colon - p) : strlen(p);
    if (!len || len >= sizeof(host)) return false;
    memcpy(host, p, len); host[len] = 0;
    if (colon && (sscanf(colon + 1, "%u%c", &port, &tail) != 1 || !port || port > 65535)) return false;
    struct in_addr addr;
    if (inet_pton(AF_INET, host, &addr) != 1) return false;
    uint32_t ip = ntohl(addr.s_addr);
    return (ip >> 24) == 10 || (ip >> 20) == 0xac1 || (ip >> 16) == 0xc0a8;
}

static bool field(const char *form, const char *name, char *out, size_t cap)
{
    size_t n = strlen(name);
    for (const char *p = form; p && *p; p = strchr(p, '&')) {
        if (*p == '&') ++p;
        if (strncmp(p, name, n) || p[n] != '=') continue;
        p += n + 1;
        size_t used = 0;
        while (*p && *p != '&') {
            unsigned char ch = *p++;
            if (ch == '+') ch = ' ';
            else if (ch == '%') {
                if (!p[0] || !p[1] || !isxdigit((unsigned char)p[0]) || !isxdigit((unsigned char)p[1])) return false;
                char hex[3] = {p[0], p[1], 0}; ch = strtoul(hex, NULL, 16); p += 2;
            }
            if (!ch || used + 1 >= cap) return false;
            out[used++] = ch;
        }
        out[used] = 0; return true;
    }
    return false;
}

static esp_err_t setup_page(httpd_req_t *req)
{
    char *page = malloc(2200);
    if (!page) return ESP_ERR_NO_MEM;
    snprintf(page, 2200,
        "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Ghost room setup</title><style>body{background:#181526;color:#eeeafb;font:18px system-ui;max-width:480px;margin:24px auto;padding:20px}"
        "input,button{box-sizing:border-box;width:100%%;padding:14px;margin:8px 0 18px;border-radius:14px;border:1px solid #a99bc9;background:#302943;color:white;font:inherit}button{background:#65508c}</style>"
        "<h1>Meet Ghost</h1><p>Connect this room device to your trusted home Wi-Fi and Ghost Server.</p>"
        "<form method='post' action='/save'><input type='hidden' name='csrf' value='%s'>"
        "<label>Wi-Fi name<input name='ssid' maxlength='32' required></label>"
        "<label>Wi-Fi password<input type='password' name='password' maxlength='64'></label>"
        "<label>Ghost Server<input name='server' value='http://192.168.80.244:8780' maxlength='95' required></label>"
        "<label>Ghost pairing code<input name='code' inputmode='numeric' pattern='[0-9]{6}' maxlength='6' required></label>"
        "<p>Create the six-digit code on Ghost Server. Use trusted home Wi-Fi: this prototype uses local HTTP.</p>"
        "<button>Connect Ghost</button></form>", csrf);
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'");
    esp_err_t result = httpd_resp_sendstr(req, page); free(page); return result;
}

static bool pair_device(void)
{
    char url[128], response[1024] = {0};
    snprintf(url, sizeof(url), "%s/api/v1/pair", pending.server);
    esp_http_client_config_t http = {.url=url, .timeout_ms=12000, .disable_auto_redirect=true};
    esp_http_client_handle_t client = esp_http_client_init(&http);
    if (!client) return false;
    char body[160];
    snprintf(body, sizeof(body), "{\"code\":\"%s\",\"name\":\"Ghost Waveshare room\",\"kind\":\"room\",\"info\":{\"model\":\"waveshare-4b\"}}", pair_code);
    bool ok = false;
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    if (esp_http_client_open(client, strlen(body)) == ESP_OK &&
        esp_http_client_write(client, body, strlen(body)) == (int)strlen(body)) {
        esp_http_client_fetch_headers(client);
        int len = esp_http_client_read_response(client, response, sizeof(response)-1);
        if (len > 0 && esp_http_client_get_status_code(client) == 200) {
            cJSON *json = cJSON_Parse(response);
            const cJSON *key = cJSON_GetObjectItemCaseSensitive(json, "device_key");
            if (cJSON_IsString(key) && strlen(key->valuestring) < sizeof(pending.key)) {
                strlcpy(pending.key, key->valuestring, sizeof(pending.key)); ok = true;
            }
            cJSON_Delete(json);
        }
    }
    esp_http_client_cleanup(client);
    memset(response, 0, sizeof(response)); memset(body, 0, sizeof(body));
    return ok;
}

static void save_task(void *arg)
{
    status_fn("Connecting to home Wi-Fi...");
    if (join(&pending) && pair_device()) {
        nvs_handle_t nvs;
        if (nvs_open("ghost_room", NVS_READWRITE, &nvs) == ESP_OK) {
            esp_err_t err = nvs_set_blob(nvs, "config", &pending, sizeof(pending));
            if (err == ESP_OK) err = nvs_commit(nvs);
            nvs_close(nvs);
            if (err == ESP_OK) {
                status_fn("Paired. Approve this device on Ghost Server if requested.");
                vTaskDelay(pdMS_TO_TICKS(1500)); esp_restart();
            }
        }
    }
    status_fn("Setup failed. Check Wi-Fi, server and pairing code, then try again.");
    memset(&pending, 0, sizeof(pending)); memset(pair_code, 0, sizeof(pair_code));
    submitting = false;
    vTaskDelete(NULL);
}

static esp_err_t save_page(httpd_req_t *req)
{
    char form[1800], token[33];
    if (submitting || req->content_len <= 0 || req->content_len >= sizeof(form)) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid setup request");
    int got = 0;
    while (got < req->content_len) {
        int n = httpd_req_recv(req, form + got, req->content_len - got);
        if (n <= 0) return ESP_FAIL;
        got += n;
    }
    form[got] = 0;
    if (!field(form,"csrf",token,sizeof(token)) || strcmp(token,csrf) ||
        !field(form,"ssid",pending.ssid,sizeof(pending.ssid)) || !pending.ssid[0] ||
        !field(form,"password",pending.password,sizeof(pending.password)) ||
        !field(form,"server",pending.server,sizeof(pending.server)) || !server_ok(pending.server) ||
        !field(form,"code",pair_code,sizeof(pair_code)) || strlen(pair_code) != 6 ||
        strspn(pair_code,"0123456789") != 6) {
        memset(form,0,sizeof(form));
        return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Check the fields. Use a private LAN IPv4 server address and a six-digit Ghost code.");
    }
    memset(form,0,sizeof(form)); submitting = true;
    httpd_resp_sendstr(req,"Connecting. Check Ghost's screen. If setup fails, return to the setup page and try again.");
    if (xTaskCreate(save_task,"setup_save",8192,NULL,4,NULL) != pdPASS) {
        submitting = false; status_fn("Not enough memory for setup. Restart and try again.");
    }
    return ESP_OK;
}

bool ghost_config_connect(ghost_config_t *cfg, ghost_status_fn status)
{
    status_fn = status;
    events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,network_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,network_event,NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    nvs_handle_t nvs;
    size_t size = sizeof(*cfg);
    bool loaded = false;
    if (nvs_open("ghost_room",NVS_READONLY,&nvs) == ESP_OK) {
        loaded = nvs_get_blob(nvs,"config",cfg,&size) == ESP_OK && size == sizeof(*cfg);
        nvs_close(nvs);
    }
    if (loaded) {
        cfg->ssid[32]=0; cfg->password[64]=0; cfg->server[95]=0; cfg->key[159]=0;
        if (server_ok(cfg->server) && cfg->key[0]) {
            status("Connecting to home Wi-Fi...");
            if (join(cfg)) { esp_wifi_set_ps(WIFI_PS_NONE); return true; }
        }
    }
    uint32_t randoms[4]; esp_fill_random(randoms,sizeof(randoms));
    for (int i=0;i<4;++i) snprintf(csrf+i*8,9,"%08lx",(unsigned long)randoms[i]);
    wifi_config_t ap = {0};
    snprintf((char *)ap.ap.ssid,sizeof(ap.ap.ssid),"Ghost-Setup-%04lx",(unsigned long)(randoms[0]&0xffff));
    snprintf((char *)ap.ap.password,sizeof(ap.ap.password),"%08lx%04lx",(unsigned long)randoms[1],(unsigned long)(randoms[2]&0xffff));
    ap.ap.authmode=WIFI_AUTH_WPA2_PSK; ap.ap.max_connection=2; ap.ap.channel=1;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP,&ap));
    char message[200];
    snprintf(message,sizeof(message),"Connect to %s\nPassword: %s\nOpen http://192.168.4.1",ap.ap.ssid,ap.ap.password);
    status(message);
    httpd_config_t http = HTTPD_DEFAULT_CONFIG();
    http.stack_size=8192;
    httpd_handle_t server;
    ESP_ERROR_CHECK(httpd_start(&server,&http));
    httpd_uri_t home={.uri="/",.method=HTTP_GET,.handler=setup_page};
    httpd_uri_t save={.uri="/save",.method=HTTP_POST,.handler=save_page};
    ESP_ERROR_CHECK(httpd_register_uri_handler(server,&home));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server,&save));
    return false;
}
