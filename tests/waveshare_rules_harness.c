#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ghost_rules.h"

static void norm(const char *in, const char *want)
{
    char out[96];
    bool ok = ghost_server_url_normalise(in, out, sizeof(out));
    if (!want) { if (ok) { fprintf(stderr, "accepted %s -> %s\n", in, out); assert(!ok); } return; }
    if (!ok || strcmp(out, want)) { fprintf(stderr, "%s -> %s (want %s)\n", in, ok ? out : "REJECTED", want); assert(0); }
}

int main(void)
{
    assert(ghost_server_url_ok("http://192.168.80.165:8780"));
    assert(ghost_server_url_ok("http://10.0.0.2"));
    assert(ghost_server_url_ok("http://172.20.1.1:80"));
    assert(!ghost_server_url_ok("http://8.8.8.8:8780"));           // public
    assert(!ghost_server_url_ok("http://172.32.0.1"));              // outside 172.16/12
    assert(!ghost_server_url_ok("https://192.168.80.165:8780"));
    assert(!ghost_server_url_ok("http://ghost.local:8780"));        // names
    assert(!ghost_server_url_ok("http://user:pw@192.168.1.2"));
    assert(!ghost_server_url_ok("http://192.168.1.2:8780/x"));
    assert(!ghost_server_url_ok("http://192.168.1.2:0"));
    assert(!ghost_server_url_ok("http://192.168.1.2:70000"));
    assert(!ghost_server_url_ok(NULL));

    norm("192.168.80.165", "http://192.168.80.165:8780");
    norm("  192.168.80.165:9000/ ", "http://192.168.80.165:9000");
    norm("http://192.168.80.165:8780/", "http://192.168.80.165:8780");
    norm("http://10.1.2.3", "http://10.1.2.3:8780");
    norm("https://192.168.80.165", NULL);
    norm("ftp://192.168.80.165", NULL);
    norm("example.com", NULL);
    norm("8.8.8.8", NULL);
    norm("", NULL);
    norm("   /", NULL);

    assert(ghost_pair_code_ok("123456"));
    assert(!ghost_pair_code_ok("12345"));
    assert(!ghost_pair_code_ok("1234567"));
    assert(!ghost_pair_code_ok("12a456"));
    assert(!ghost_pair_code_ok(NULL));
    // Buttons: always up while busy; fade after 8 s of no touch once idle.
    assert(ghost_controls_visible(true, 999999, 999999, GHOST_CONTROLS_HIDE_MS));
    assert(ghost_controls_visible(false, 0, 999999, GHOST_CONTROLS_HIDE_MS));
    assert(ghost_controls_visible(false, 7999, 999999, GHOST_CONTROLS_HIDE_MS));
    assert(!ghost_controls_visible(false, 8000, 999999, GHOST_CONTROLS_HIDE_MS));
    assert(ghost_controls_visible(false, 999999, 3000, GHOST_CONTROLS_HIDE_MS));  // just finished talking
    assert(!ghost_controls_visible(false, 999999, 8000, GHOST_CONTROLS_HIDE_MS));
    assert(ghost_controls_visible(false, 999999, 999999, 0));                     // hiding switched off
    puts("rules ok");
    return 0;
}
