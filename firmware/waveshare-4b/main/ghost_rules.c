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
