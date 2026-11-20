#include "ghost_config.h"
#include "ghost_rules.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <strings.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_http_client.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "nvs.h"
#include "cJSON.h"
#include "lwip/inet.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"

static EventGroupHandle_t events;
static ghost_status_fn status_fn;
static ghost_config_t pending;
static char csrf[33], pair_code[7];
static atomic_bool submitting, reconnect_enabled;
static const int GOT_IP = 1;
static ghost_config_t *current_config;
static atomic_bool wifi_operation;

// ── HTTPS certificate pin ────────────────────────────────────────────────────
static SemaphoreHandle_t pin_lock;
static char pin_pem[GHOST_PIN_MAX], pin_fp[24];
static int pin_port;

// SHA-256 of the certificate's DER bytes (what Ghost Server shows as its fingerprint).
static bool pem_sha256(const char *pem, unsigned char digest[32])
{
    const char *begin = strstr(pem, "-----BEGIN CERTIFICATE-----");
    const char *end = strstr(pem, "-----END CERTIFICATE-----");
    if (!begin || !end || end <= begin) return false;
    begin += strlen("-----BEGIN CERTIFICATE-----");
    size_t span = end - begin, n = 0;
    char *b64 = malloc(span + 1);
    unsigned char *der = malloc(span);
    bool ok = false;
    if (b64 && der) {
        for (const char *c = begin; c < end; c++) if (*c != '\n' && *c != '\r') b64[n++] = *c;
        size_t der_len = 0;
        ok = mbedtls_base64_decode(der, span, &der_len, (const unsigned char *)b64, n) == 0 && der_len > 0 &&
             mbedtls_sha256(der, der_len, digest, 0) == 0;
    }
    free(b64); free(der);
    return ok;
}

static void set_pin(const char *pem, int port)
{
    unsigned char digest[32];
    char fp[24] = "";
    if (pem[0] && pem_sha256(pem, digest))
        snprintf(fp, sizeof(fp), "%02X%02X %02X%02X %02X%02X %02X%02X", digest[0], digest[1], digest[2],
                 digest[3], digest[4], digest[5], digest[6], digest[7]);
    xSemaphoreTake(pin_lock, portMAX_DELAY);
    strlcpy(pin_pem, fp[0] ? pem : "", sizeof(pin_pem));
    pin_port = fp[0] ? port : 0;
    strlcpy(pin_fp, fp, sizeof(pin_fp));
    xSemaphoreGive(pin_lock);
}

bool ghost_server_pin(char *pem, size_t capacity, int *port)
{
    if (!pin_lock) return false;
    xSemaphoreTake(pin_lock, portMAX_DELAY);
    bool secure = pin_pem[0] && pin_port > 0 && strlen(pin_pem) < capacity;
    if (secure) {strlcpy(pem, pin_pem, capacity); *port = pin_port;}
    xSemaphoreGive(pin_lock);
    return secure;
}

void ghost_server_fingerprint(char *out, size_t capacity)
{
    if (!pin_lock) {if (capacity) out[0] = 0; return;}
    xSemaphoreTake(pin_lock, portMAX_DELAY);
    strlcpy(out, pin_fp, capacity);
    xSemaphoreGive(pin_lock);
}

// Saved next to the config blob so older firmware ignores it. An empty pem clears it.
static esp_err_t save_pin(const char *pem, int port)
{
    nvs_handle_t nvs;
    esp_err_t result = nvs_open("ghost_room", NVS_READWRITE, &nvs);
    if (result != ESP_OK) return result;
    if (pem[0]) {
        result = nvs_set_str(nvs, "tls_pem", pem);
        if (result == ESP_OK) result = nvs_set_u16(nvs, "tls_port", (uint16_t)port);
    } else {
        nvs_erase_key(nvs, "tls_pem"); nvs_erase_key(nvs, "tls_port");
    }
    if (result == ESP_OK) result = nvs_commit(nvs);
    nvs_close(nvs);
    if (result == ESP_OK) set_pin(pem, port);
    return result;
}

static void load_pin(void)
{
    nvs_handle_t nvs;
    char *pem = calloc(1, GHOST_PIN_MAX);
    uint16_t port = 0;
    size_t size = GHOST_PIN_MAX;
    if (pem && nvs_open("ghost_room", NVS_READONLY, &nvs) == ESP_OK) {
        if (nvs_get_str(nvs, "tls_pem", pem, &size) == ESP_OK && nvs_get_u16(nvs, "tls_port", &port) == ESP_OK &&
            ghost_pem_ok(pem, GHOST_PIN_MAX) && port)
            set_pin(pem, port);
        nvs_close(nvs);
    }
    free(pem);
}

// GET <server>/api/v1/tls (public, plain HTTP): the certificate to pin, checked
// against the fingerprint sent with it and the shape we accept.
static bool fetch_tls(const char *server, char *pem, size_t capacity, int *port)
{
    char url[128];
    snprintf(url, sizeof(url), "%.95s/api/v1/tls", server);
    esp_http_client_config_t http = {.url=url, .timeout_ms=8000, .disable_auto_redirect=true};
    esp_http_client_handle_t client = esp_http_client_init(&http);
    if (!client) return false;
    bool ok = false;
    char *body = calloc(1, 3072);
    if (body && esp_http_client_open(client, 0) == ESP_OK && esp_http_client_fetch_headers(client) >= 0 &&
        esp_http_client_get_status_code(client) == 200) {
        int len = esp_http_client_read_response(client, body, 3071);
        cJSON *json = len > 0 ? cJSON_Parse(body) : NULL;
        const cJSON *enabled = cJSON_GetObjectItemCaseSensitive(json, "enabled");
        const cJSON *got_pem = cJSON_GetObjectItemCaseSensitive(json, "pem");
        const cJSON *got_port = cJSON_GetObjectItemCaseSensitive(json, "port");
        const cJSON *sha = cJSON_GetObjectItemCaseSensitive(json, "sha256");
        unsigned char digest[32];
        if (cJSON_IsTrue(enabled) && cJSON_IsString(got_pem) && cJSON_IsNumber(got_port) && cJSON_IsString(sha) &&
            got_port->valueint > 0 && got_port->valueint <= 65535 && ghost_pem_ok(got_pem->valuestring, capacity) &&
            pem_sha256(got_pem->valuestring, digest)) {
            char hex[65];
            for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02X", digest[i]);
            if (!strcasecmp(hex, sha->valuestring)) {
                strlcpy(pem, got_pem->valuestring, capacity); *port = got_port->valueint; ok = true;
            }
        }
        cJSON_Delete(json);
    }
    free(body);
    esp_http_client_cleanup(client);
    return ok;
}

// Pairing's own copy of the server certificate; one pairing runs at a time.
static char pair_pem[GHOST_PIN_MAX];
static int pair_port;

bool ghost_wifi_ready(void) { return events && (xEventGroupGetBits(events) & GOT_IP); }

static void network_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) xEventGroupSetBits(events, GOT_IP);
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(events, GOT_IP);
        if(reconnect_enabled) esp_wifi_connect();
    }
}

static bool join(const ghost_config_t *cfg)
{
    wifi_config_t wifi = {0};
    memcpy(wifi.sta.ssid, cfg->ssid, strlen(cfg->ssid));
    memcpy(wifi.sta.password, cfg->password, strlen(cfg->password));
    wifi.sta.pmf_cfg.capable = true;
    reconnect_enabled=false;
    esp_wifi_disconnect();
    vTaskDelay(pdMS_TO_TICKS(150));
    xEventGroupClearBits(events, GOT_IP);
    if (esp_wifi_set_config(WIFI_IF_STA, &wifi) != ESP_OK) return false;
    reconnect_enabled=true;
    esp_wifi_connect();
    return xEventGroupWaitBits(events, GOT_IP, false, true, pdMS_TO_TICKS(20000)) & GOT_IP;
}

bool ghost_wifi_status(char *ssid, size_t capacity, int *rssi)
{
    wifi_ap_record_t ap;
    if(esp_wifi_sta_get_ap_info(&ap)==ESP_OK && ghost_wifi_ready()) {
        strlcpy(ssid,(const char *)ap.ssid,capacity);*rssi=ap.rssi;return true;
    }
    strlcpy(ssid,current_config?current_config->ssid:"",capacity);*rssi=-127;return false;
}

esp_err_t ghost_wifi_scan(ghost_network_t *networks,size_t capacity,size_t *count)
{
    *count=0;
    if(submitting || atomic_exchange(&wifi_operation,true)) return ESP_ERR_INVALID_STATE;
    bool was_retrying=reconnect_enabled;
    bool paused_join=!ghost_wifi_ready();
    if(paused_join) {
        // A failed saved-network join must not prevent scanning for its replacement.
        reconnect_enabled=false;esp_wifi_disconnect();vTaskDelay(pdMS_TO_TICKS(150));
    }
    wifi_scan_config_t scan={.show_hidden=false};
    esp_err_t result=esp_wifi_scan_start(&scan,true);
    if(result==ESP_OK) {
        wifi_ap_record_t *records=calloc(24,sizeof(*records));
        if(!records) result=ESP_ERR_NO_MEM;
        else {
            uint16_t found=24;
            result=esp_wifi_scan_get_ap_records(&found,records);
            for(unsigned i=0;result==ESP_OK && i<found && *count<capacity;i++) {
                if(!records[i].ssid[0]) continue;
                bool duplicate=false;
                for(size_t j=0;j<*count;j++) if(!strcmp(networks[j].ssid,(const char *)records[i].ssid)) duplicate=true;
                if(duplicate) continue;
                ghost_network_t *item=&networks[(*count)++];
                strlcpy(item->ssid,(const char *)records[i].ssid,sizeof(item->ssid));
                item->rssi=records[i].rssi;item->secured=records[i].authmode!=WIFI_AUTH_OPEN;
            }
            free(records);
        }
        esp_wifi_clear_ap_list();
    }
    if(paused_join) {reconnect_enabled=was_retrying;if(was_retrying) esp_wifi_connect();}
    wifi_operation=false;
    return result;
}

esp_err_t ghost_wifi_update(const char *ssid,const char *password,bool keep_password)
{
    if(!current_config || !current_config->key[0]) return ESP_ERR_INVALID_STATE;
    if(!ssid[0] || strlen(ssid)>32 || strlen(password)>64) return ESP_ERR_INVALID_ARG;
    if(submitting || atomic_exchange(&wifi_operation,true)) return ESP_ERR_INVALID_STATE;
    ghost_config_t next=*current_config;
    if(!keep_password || strcmp(ssid,current_config->ssid)) strlcpy(next.password,password,sizeof(next.password));
    strlcpy(next.ssid,ssid,sizeof(next.ssid));
    esp_err_t result=ESP_FAIL;
    if(join(&next)) {
        nvs_handle_t nvs;
        result=nvs_open("ghost_room",NVS_READWRITE,&nvs);
        if(result==ESP_OK) {
            result=nvs_set_blob(nvs,"config",&next,sizeof(next));
            if(result==ESP_OK) result=nvs_commit(nvs);
            nvs_close(nvs);
        }
        if(result==ESP_OK) {*current_config=next;esp_wifi_set_ps(WIFI_PS_NONE);}
    }
    if(result!=ESP_OK) join(current_config); // Restore the saved network on a failed change.
    memset(&next,0,sizeof(next));
    wifi_operation=false;return result;
}

// Only plain HTTP on an explicit private LAN IPv4 address (see ghost_rules.c).
// No credentials may be posted to a redirected, public or userinfo URL.
static bool server_ok(const char *url) {return ghost_server_url_ok(url);}

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
        "<label>Ghost Server<input name='server' value='http://192.168.80.165:8780' maxlength='95' required></label>"
        "<label>Ghost pairing code<input name='code' inputmode='numeric' pattern='[0-9]{6}' maxlength='6' required></label>"
        "<p>Create the six-digit code on Ghost Server. Use trusted home Wi-Fi: this prototype uses local HTTP.</p>"
        "<button>Connect Ghost</button></form>", csrf);
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'");
    esp_err_t result = httpd_resp_sendstr(req, page); free(page); return result;
}

// Pairs with `server` using a six-digit code; on success copies the new device key
// into `key`. `http_status` gets the server's answer (0 = it couldn't be reached).
static bool pair_with(const char *server, const char *code, char *key, size_t key_capacity, int *http_status)
{
    char url[128], response[1024] = {0}, name[48];
    *http_status = 0;
    pair_pem[0] = 0; pair_port = 0;
    // HTTPS when the server has a certificate, so the device key never crosses Wi-Fi in the clear.
    bool secure = fetch_tls(server, pair_pem, sizeof(pair_pem), &pair_port);
    if (secure) {
        if (!ghost_https_url(server, pair_port, "/api/v1/pair", url, sizeof(url))) return false;
    } else {
        pair_pem[0] = 0; pair_port = 0;
        snprintf(url, sizeof(url), "%.95s/api/v1/pair", server);
    }
    // Name each screen apart on the admin page (bedroom, lounge, office all look alike).
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(name, sizeof(name), "Ghost room screen %02X%02X", mac[4], mac[5]);
    esp_http_client_config_t http = {.url=url, .timeout_ms=12000, .disable_auto_redirect=true,
        .cert_pem=secure ? pair_pem : NULL, .skip_cert_common_name_check=secure};
    esp_http_client_handle_t client = esp_http_client_init(&http);
    if (!client) return false;
    char body[200];
    snprintf(body, sizeof(body), "{\"code\":\"%s\",\"name\":\"%s\",\"kind\":\"room\",\"info\":{\"model\":\"waveshare-4b\"}}", code, name);
    bool ok = false;
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    if (esp_http_client_open(client, strlen(body)) == ESP_OK &&
        esp_http_client_write(client, body, strlen(body)) == (int)strlen(body)) {
        esp_http_client_fetch_headers(client);
        int len = esp_http_client_read_response(client, response, sizeof(response)-1);
        *http_status = esp_http_client_get_status_code(client);
        if (len > 0 && *http_status == 200) {
            cJSON *json = cJSON_Parse(response);
            const cJSON *got = cJSON_GetObjectItemCaseSensitive(json, "device_key");
            if (cJSON_IsString(got) && strlen(got->valuestring) < key_capacity) {
                strlcpy(key, got->valuestring, key_capacity); ok = true;
            }
            cJSON_Delete(json);
        }
    }
    esp_http_client_cleanup(client);
    memset(response, 0, sizeof(response)); memset(body, 0, sizeof(body));
    return ok;
}

static bool pair_device(void)
{
    int status;
    return pair_with(pending.server, pair_code, pending.key, sizeof(pending.key), &status);
}

static esp_err_t save_config(const ghost_config_t *cfg)
{
    nvs_handle_t nvs;
    esp_err_t result = nvs_open("ghost_room", NVS_READWRITE, &nvs);
    if (result == ESP_OK) {
        result = nvs_set_blob(nvs, "config", cfg, sizeof(*cfg));
        if (result == ESP_OK) result = nvs_commit(nvs);
        nvs_close(nvs);
    }
    return result;
}

void ghost_config_server(char *out, size_t capacity)
{
    strlcpy(out, current_config ? current_config->server : "", capacity);
}

esp_err_t ghost_server_repair(const char *typed_server, const char *code)
{
    if (!current_config || !current_config->ssid[0]) return ESP_ERR_INVALID_STATE;
    char server[96];
    if (!ghost_server_url_normalise(typed_server, server, sizeof(server))) return ESP_ERR_INVALID_ARG;
    if (!ghost_pair_code_ok(code)) return ESP_ERR_INVALID_SIZE;
    if (!ghost_wifi_ready()) return ESP_ERR_WIFI_NOT_CONNECT;
    if (submitting || atomic_exchange(&wifi_operation, true)) return ESP_ERR_INVALID_STATE;
    ghost_config_t next = *current_config;
    strlcpy(next.server, server, sizeof(next.server));
    int status = 0;
    esp_err_t result;
    if (!pair_with(next.server, code, next.key, sizeof(next.key), &status)) {
        // 0: unreachable; 400: wrong or expired code; 429: pairing paused after wrong codes.
        result = status == 0 ? ESP_ERR_TIMEOUT : status == 429 ? ESP_ERR_INVALID_STATE : ESP_ERR_NOT_FOUND;
    } else {
        // Only replace the working pairing once the new one is saved.
        result = save_config(&next);
        if (result == ESP_OK) {
            *current_config = next;
            result = save_pin(pair_pem, pair_port);   // the new server's certificate (or none)
        }
    }
    memset(&next, 0, sizeof(next));
    wifi_operation = false;
    return result;
}

static void save_task(void *arg)
{
    status_fn("Connecting to home Wi-Fi...");
    if (join(&pending) && pair_device() && save_config(&pending) == ESP_OK && save_pin(pair_pem, pair_port) == ESP_OK) {
        status_fn("Paired. Approve this device on Ghost Server if requested.");
        vTaskDelay(pdMS_TO_TICKS(1500)); esp_restart();
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
    if (xTaskCreate(save_task,"setup_save",12288,NULL,4,NULL) != pdPASS) {
        submitting = false; status_fn("Not enough memory for setup. Restart and try again.");
    }
    return ESP_OK;
}

bool ghost_config_connect(ghost_config_t *cfg, ghost_status_fn status)
{
    current_config=cfg;
    status_fn = status;
    pin_lock = xSemaphoreCreateMutex();
    load_pin();
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
            bool joined = join(cfg);
            esp_wifi_set_ps(WIFI_PS_NONE);
            if (!joined) {
                // After a power cut the router often takes longer to come back than
                // the 20 s join window. A paired screen must never fall back to the
                // setup hotspot for that: keep retrying in the background (the
                // disconnect handler reconnects) and let Setup change the network.
                char message[120];
                snprintf(message, sizeof(message), "Waiting for Wi-Fi \"%.32s\"... Ghost keeps trying.", cfg->ssid);
                status(message);
            }
            return true;
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

// Screens paired before HTTPS learn their server's certificate once, in the
// background (a slow server never delays the wake word or a conversation).
static void secure_task(void *arg)
{
    static char pem[GHOST_PIN_MAX];
    int port = 0;
    for (;;) {
        char fp[24];
        ghost_server_fingerprint(fp, sizeof(fp));
        if (fp[0]) {vTaskDelay(pdMS_TO_TICKS(60000)); continue;}           // already secure
        if (!ghost_wifi_ready() || !current_config || !current_config->key[0] || submitting) {
            vTaskDelay(pdMS_TO_TICKS(5000)); continue;
        }
        char server[96];
        strlcpy(server, current_config->server, sizeof(server));
        bool got = fetch_tls(server, pem, sizeof(pem), &port);
        if (got && !atomic_exchange(&wifi_operation, true)) {
            if (!strcmp(server, current_config->server)) {
                esp_err_t saved = save_pin(pem, port);
                ESP_LOGW("ghost_tls", "Pinned Ghost Server certificate (port %d): %s", port,
                         saved == ESP_OK ? "saved" : esp_err_to_name(saved));
            }
            wifi_operation = false;
            continue;
        }
        vTaskDelay(pdMS_TO_TICKS(got ? 2000 : 60000));
    }
}

void ghost_server_secure_start(void)
{
    if (xTaskCreate(secure_task, "ghost_tls", 6144, NULL, 2, NULL) != pdPASS)
        ESP_LOGW("ghost_tls", "No memory for the HTTPS setup task");
}
