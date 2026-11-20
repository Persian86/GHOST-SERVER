#include "ghost_rules.h"
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#ifdef ESP_PLATFORM
#include "lwip/sockets.h"   /* inet_pton, AF_INET */
#include "lwip/inet.h"
#else
#include <arpa/inet.h>
#endif

bool ghost_server_url_ok(const char *url)
{
    char host[32], tail = 0;
    unsigned port = 8780;
    if (!url || strncmp(url, "http://", 7)) return false;
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

bool ghost_pair_code_ok(const char *code)
{
    return code && strlen(code) == 6 && strspn(code, "0123456789") == 6;
}

bool ghost_server_url_normalise(const char *typed, char *out, size_t capacity)
{
    if (!typed || !out || capacity < 16) return false;
    while (*typed == ' ') typed++;
    char work[96];
    size_t n = strlen(typed);
    while (n && (typed[n - 1] == ' ' || typed[n - 1] == '/')) n--;
    if (!n || n >= sizeof(work)) return false;
    memcpy(work, typed, n); work[n] = 0;
    const char *host = strncmp(work, "http://", 7) ? work : work + 7;
    if (strstr(work, "://") && host == work) return false;     // https:// or anything else
    int written = snprintf(out, capacity, strchr(host, ':') ? "http://%s" : "http://%s:8780", host);
    if (written < 0 || (size_t)written >= capacity) return false;
    return ghost_server_url_ok(out);
}

bool ghost_controls_visible(bool busy, int64_t ms_since_touch, int64_t ms_since_busy, int64_t hide_ms)
{
    if (busy || hide_ms <= 0) return true;
    return (ms_since_touch >= 0 && ms_since_touch < hide_ms) ||
           (ms_since_busy >= 0 && ms_since_busy < hide_ms);
}

bool ghost_sleepy(bool idle, int64_t ms_since_touch, int64_t ms_since_busy)
{
    return idle && ms_since_touch >= GHOST_SLEEP_MS && ms_since_busy >= GHOST_SLEEP_MS;
}

int16_t ghost_wake_gain(int32_t sample)
{
    int32_t v = sample * 2;
    if (v > 32767) return 32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}

void ghost_card_text(char *dst, size_t capacity, const char *src)
{
    if(!dst || !capacity) return;
    size_t n=0;
    const unsigned char *p=(const unsigned char *)(src?src:"");
    while(*p && n+1<capacity) {
        if(p[0]==0xE2 && p[1]==0x80 && p[2]==0xA6) {           // U+2026 ellipsis
            for(int i=0;i<3 && n+1<capacity;i++) dst[n++]='.';
            p+=3; continue;
        }
        if(*p>=0x80) {                                           // skip one UTF-8 character
            p++; while((*p&0xC0)==0x80) p++;
            dst[n++]=' '; continue;
        }
        dst[n++]=*p<0x20?' ':(char)*p; p++;
    }
    dst[n]=0;
}

bool ghost_https_url(const char *server, int port, const char *path, char *out, size_t capacity)
{
    if (!ghost_server_url_ok(server) || port <= 0 || port > 65535 || !path || !out) return false;
    const char *host = server + 7;
    size_t len = strcspn(host, ":");
    int written = snprintf(out, capacity, "https://%.*s:%d%s", (int)len, host, port, path);
    return written > 0 && (size_t)written < capacity;
}

bool ghost_pem_ok(const char *pem, size_t capacity)
{
    static const char begin[] = "-----BEGIN CERTIFICATE-----", end[] = "-----END CERTIFICATE-----";
    if (!pem) return false;
    size_t n = strnlen(pem, capacity);
    if (n == capacity || strncmp(pem, begin, sizeof(begin) - 1)) return false;
    const char *body = pem + sizeof(begin) - 1;
    const char *stop = strstr(body, end);
    if (!stop || stop == body) return false;
    for (const char *c = stop + sizeof(end) - 1; *c; c++)
        if (*c != '\n' && *c != '\r') return false;              // exactly one certificate
    size_t data = 0;
    for (const char *c = body; c < stop; c++) {
        char ch = *c;
        if (ch != '\n' && ch != '\r') data++;
        if (!((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') ||
              ch == '+' || ch == '/' || ch == '=' || ch == '\n' || ch == '\r')) return false;
    }
    return data >= 4;
}
