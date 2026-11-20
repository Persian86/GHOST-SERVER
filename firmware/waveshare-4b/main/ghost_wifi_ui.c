#include "ghost_wifi_ui.h"
#include "ghost_config.h"
#include "ghost_audio.h"
#include "ghost_wake.h"
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "esp_wifi.h"

static lv_obj_t *panel,*list,*message,*password,*refresh;
static lv_obj_t *server_field,*code_field,*repair_keyboard;
static char entered_server[96],entered_code[7];
static atomic_bool busy;
static atomic_bool visible;
static ghost_network_t networks[20],selected;
static char entered_password[65];
static bool keep_password;
static lv_obj_t *volume_label;
static lv_obj_t *sensitivity_label;

static void volume_changed(lv_event_t *event)
{
    lv_obj_t *slider=lv_event_get_target(event);
    int value=lv_slider_get_value(slider);
    if(lv_event_get_code(event)==LV_EVENT_RELEASED) {
        esp_err_t result=ghost_audio_set_volume(value);
        if(result!=ESP_OK) {
            lv_slider_set_value(slider,ghost_audio_volume(),LV_ANIM_OFF);
            lv_label_set_text(volume_label,"Could not save volume. Try again.");return;
        }
    }
    lv_label_set_text_fmt(volume_label,value?"Speaker volume: %d / 100":"Speaker volume: Muted",value);
}

bool ghost_wifi_ui_visible(void) {return visible;}
static void show_networks(void);
static void header(const char *title,lv_event_cb_t back);

static lv_obj_t *label(lv_obj_t *parent,const char *text,int x,int y,int width)
{
    lv_obj_t *obj=lv_label_create(parent);lv_label_set_text(obj,text);
    lv_obj_set_pos(obj,x,y);lv_obj_set_width(obj,width);
    lv_obj_set_style_text_color(obj,lv_color_hex(0xeeeafb),0);
    lv_obj_set_style_text_font(obj,&lv_font_montserrat_16,0);return obj;
}

static void close_page(lv_event_t *event)
{
    if(busy) return;
    if(password) lv_textarea_set_text(password,"");
    lv_obj_delete(panel);panel=NULL;list=NULL;password=NULL;server_field=NULL;code_field=NULL;repair_keyboard=NULL;
    visible=false;
    memset(entered_password,0,sizeof(entered_password));
}

static void back_page(lv_event_t *event) {if(!busy) show_networks();}

static void wake_changed(lv_event_t *event)
{
    lv_obj_t *toggle=lv_event_get_target(event);
    bool enabled=lv_obj_has_state(toggle,LV_STATE_CHECKED);
    if(ghost_wake_set_enabled(enabled)!=ESP_OK) {
        if(ghost_wake_enabled()) lv_obj_add_state(toggle,LV_STATE_CHECKED);
        else lv_obj_remove_state(toggle,LV_STATE_CHECKED);
        lv_label_set_text(message,"Could not save hands-free setting. Try again.");
    }
}

static void sensitivity_changed(lv_event_t *event)
{
    lv_obj_t *slider=lv_event_get_target(event);
    int value=lv_slider_get_value(slider);
    if(lv_event_get_code(event)==LV_EVENT_RELEASED && ghost_wake_set_sensitivity(value)!=ESP_OK) {
        lv_slider_set_value(slider,ghost_wake_sensitivity(),LV_ANIM_OFF);
        lv_label_set_text(message,"Could not save sensitivity. Try again.");return;
    }
    lv_label_set_text_fmt(sensitivity_label,"Sensitivity: %d / 100",value);
}

static void wake_settings_tap(lv_event_t *event)
{
    if(busy || !ghost_wake_ready()) return;
    header("Wake sensitivity",back_page);
    label(panel,"Hey Ghost - experimental local detection",20,75,440);
    sensitivity_label=label(panel,"",20,110,440);
    lv_label_set_text_fmt(sensitivity_label,"Sensitivity: %d / 100",ghost_wake_sensitivity());
    lv_obj_t *slider=lv_slider_create(panel);lv_obj_set_pos(slider,38,169);lv_obj_set_size(slider,404,18);
    lv_slider_set_range(slider,0,100);lv_slider_set_value(slider,ghost_wake_sensitivity(),LV_ANIM_OFF);
    lv_obj_set_style_bg_color(slider,lv_color_hex(0xb9a4ff),LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(slider,lv_color_hex(0xeeeafb),LV_PART_KNOB);
    lv_obj_set_ext_click_area(slider,18);
    lv_obj_add_event_cb(slider,sensitivity_changed,LV_EVENT_VALUE_CHANGED,NULL);
    lv_obj_add_event_cb(slider,sensitivity_changed,LV_EVENT_RELEASED,NULL);
    label(panel,"Higher wakes more easily, but can trigger by mistake. Lower it if TV or background speech wakes Ghost.",20,218,440);
    label(panel,"Release to save. Close Setup before testing: detection pauses here. Say Hey Ghost, wait for Listening, then ask.",20,307,440);
    message=label(panel,"",20,422,440);
}

static void header(const char *title,lv_event_cb_t back)
{
    lv_obj_clean(panel);list=NULL;password=NULL;server_field=NULL;code_field=NULL;repair_keyboard=NULL;
    lv_obj_t *heading=label(panel,title,20,18,335);
    lv_obj_set_style_text_font(heading,&lv_font_montserrat_24,0);
    lv_obj_t *button=lv_button_create(panel);lv_obj_set_pos(button,382,10);lv_obj_set_size(button,80,46);
    lv_obj_set_style_bg_color(button,lv_color_hex(0x493d68),0);
    lv_obj_t *text=lv_label_create(button);lv_label_set_text(text,"Back");lv_obj_center(text);
    lv_obj_add_event_cb(button,back,LV_EVENT_CLICKED,NULL);
}

static void reveal_password(lv_event_t *event)
{
    lv_textarea_set_password_mode(password,!lv_obj_has_state(lv_event_get_target(event),LV_STATE_CHECKED));
}

static void connect_worker(void *arg)
{
    esp_err_t result=ghost_wifi_update(selected.ssid,entered_password,keep_password);
    memset(entered_password,0,sizeof(entered_password));
    bsp_display_lock(0);
    lv_textarea_set_text(password,"");
    if(result==ESP_OK) {
        lv_label_set_text(message,"Connected. Pairing kept. Tap Back to return.");
    } else if(result==ESP_ERR_INVALID_STATE) {
        lv_label_set_text(message,"Complete Ghost pairing using the setup hotspot first.");
    } else {
        lv_label_set_text(message,"Could not connect. Previous network restored; check the password.");
    }
    busy=false;bsp_display_unlock();vTaskDelete(NULL);
}

static void connect_tap(lv_event_t *event)
{
    if(busy) return;
    strlcpy(entered_password,lv_textarea_get_text(password),sizeof(entered_password));
    char current[33];int rssi;ghost_wifi_status(current,sizeof(current),&rssi);
    keep_password=selected.secured && !entered_password[0] && !strcmp(current,selected.ssid);
    busy=true;lv_label_set_text(message,"Connecting... please wait.");
    if(xTaskCreate(connect_worker,"wifi_connect",8192,NULL,3,NULL)!=pdPASS) {
        busy=false;memset(entered_password,0,sizeof(entered_password));
        lv_label_set_text(message,"Not enough memory. Please try again.");
    }
}

static void select_network(lv_event_t *event)
{
    if(busy) return;
    size_t index=(size_t)lv_event_get_user_data(event);
    selected=networks[index];header("Connect Wi-Fi",back_page);
    label(panel,selected.ssid,20,65,440);
    password=lv_textarea_create(panel);lv_obj_set_pos(password,20,96);lv_obj_set_size(password,440,49);
    lv_textarea_set_one_line(password,true);lv_textarea_set_password_mode(password,true);
    lv_textarea_set_max_length(password,64);
    lv_textarea_set_placeholder_text(password,selected.secured?"Wi-Fi password":"Open network - no password");
    lv_obj_t *show=lv_checkbox_create(panel);lv_obj_set_pos(show,20,158);
    lv_checkbox_set_text(show,"Show password");lv_obj_add_event_cb(show,reveal_password,LV_EVENT_VALUE_CHANGED,NULL);
    lv_obj_t *connect=lv_button_create(panel);lv_obj_set_pos(connect,328,150);lv_obj_set_size(connect,132,48);
    lv_obj_set_style_bg_color(connect,lv_color_hex(0x65508c),0);
    lv_obj_t *text=lv_label_create(connect);lv_label_set_text(text,"Connect");lv_obj_center(text);
    lv_obj_add_event_cb(connect,connect_tap,LV_EVENT_CLICKED,NULL);
    message=label(panel,"Pairing is kept when changing Wi-Fi.",20,204,440);
    lv_obj_t *keyboard=lv_keyboard_create(panel);lv_obj_set_size(keyboard,480,226);
    // Keyboard defaults to bottom alignment: a positive y offset hides it below the screen.
    lv_obj_align(keyboard,LV_ALIGN_BOTTOM_MID,0,0);
    lv_keyboard_set_textarea(keyboard,password);
}

static void scan_worker(void *arg)
{
    size_t count=0;
    esp_err_t result=ghost_wifi_scan(networks,20,&count);
    bsp_display_lock(0);
    lv_obj_clean(list);
    if(result!=ESP_OK) lv_label_set_text(message,"Scan failed. Tap Refresh to try again.");
    else if(!count) lv_label_set_text(message,"No networks found. Check the router and tap Refresh.");
    else {
        lv_label_set_text(message,"Available 2.4 GHz networks - tap one to connect.");
        for(size_t i=0;i<count;i++) {
            char title[110];
            const char *strength=networks[i].rssi>=-60?"Strong":networks[i].rssi>=-75?"Fair":"Weak";
            snprintf(title,sizeof(title),"%.32s\n%s  %d dBm  |  %s",networks[i].ssid,strength,networks[i].rssi,networks[i].secured?"Password":"Open");
            lv_obj_t *button=lv_list_add_button(list,NULL,title);
            lv_obj_set_style_min_height(button,61,0);
            lv_obj_set_style_bg_color(button,lv_color_hex(0x302943),0);
            lv_obj_set_style_text_color(button,lv_color_hex(0xeeeafb),0);
            lv_obj_add_event_cb(button,select_network,LV_EVENT_CLICKED,(void *)i);
        }
    }
    lv_obj_remove_state(refresh,LV_STATE_DISABLED);
    busy=false;bsp_display_unlock();vTaskDelete(NULL);
}

static void scan_tap(lv_event_t *event)
{
    if(busy) return;
    busy=true;lv_label_set_text(message,"Scanning for Wi-Fi...");
    lv_obj_add_state(refresh,LV_STATE_DISABLED);
    if(xTaskCreate(scan_worker,"wifi_scan",8192,NULL,3,NULL)!=pdPASS) {
        busy=false;lv_obj_remove_state(refresh,LV_STATE_DISABLED);
        lv_label_set_text(message,"Not enough memory. Please try again.");
    }
}


// ── Server & pairing: move this screen to another Ghost Server (or re-pair) ──
static void repair_worker(void *arg)
{
    esp_err_t result=ghost_server_repair(entered_server,entered_code);
    memset(entered_code,0,sizeof(entered_code));
    bsp_display_lock(0);
    if(code_field) lv_textarea_set_text(code_field,"");
    const char *text=
        result==ESP_OK?"Paired. If Ghost Server asks, approve this screen on its Security page. Tap Back.":
        result==ESP_ERR_INVALID_ARG?"Use the server's home-network address, e.g. 192.168.80.165":
        result==ESP_ERR_INVALID_SIZE?"The pairing code is six digits.":
        result==ESP_ERR_WIFI_NOT_CONNECT?"Wi-Fi isn't connected. Connect Wi-Fi first.":
        result==ESP_ERR_TIMEOUT?"Couldn't reach that server. Check the address and that it's running.":
        result==ESP_ERR_NOT_FOUND?"Wrong or expired code. Make a new one on the admin page.":
        result==ESP_ERR_INVALID_STATE?"Busy, or pairing is paused after wrong codes. Wait and retry.":
        "Couldn't save the pairing. Try again.";
    if(message) lv_label_set_text(message,text);
    if(result==ESP_OK && server_field) {
        char now[96];ghost_config_server(now,sizeof(now));lv_textarea_set_text(server_field,now);
    }
    busy=false;bsp_display_unlock();vTaskDelete(NULL);
}

static void repair_tap(lv_event_t *event)
{
    if(busy || !server_field || !code_field) return;
    strlcpy(entered_server,lv_textarea_get_text(server_field),sizeof(entered_server));
    strlcpy(entered_code,lv_textarea_get_text(code_field),sizeof(entered_code));
    busy=true;lv_label_set_text(message,"Pairing... please wait.");
    if(xTaskCreate(repair_worker,"ghost_repair",8192,NULL,3,NULL)!=pdPASS) {
        busy=false;memset(entered_code,0,sizeof(entered_code));
        lv_label_set_text(message,"Not enough memory. Please try again.");
    }
}

static void field_focused(lv_event_t *event)
{
    lv_obj_t *field=lv_event_get_target(event);
    if(!repair_keyboard) return;
    lv_keyboard_set_textarea(repair_keyboard,field);
    lv_keyboard_set_mode(repair_keyboard,field==code_field?LV_KEYBOARD_MODE_NUMBER:LV_KEYBOARD_MODE_TEXT_LOWER);
}

static void server_settings_tap(lv_event_t *event)
{
    if(busy) return;
    header("Ghost Server",back_page);
    char now[96];ghost_config_server(now,sizeof(now));
    label(panel,"Server address",20,62,290);
    server_field=lv_textarea_create(panel);lv_obj_set_pos(server_field,20,86);lv_obj_set_size(server_field,290,49);
    lv_textarea_set_one_line(server_field,true);lv_textarea_set_max_length(server_field,95);
    lv_textarea_set_text(server_field,now);
    lv_textarea_set_placeholder_text(server_field,"192.168.80.165");
    lv_obj_add_event_cb(server_field,field_focused,LV_EVENT_FOCUSED,NULL);
    label(panel,"Pairing code",326,62,134);
    code_field=lv_textarea_create(panel);lv_obj_set_pos(code_field,326,86);lv_obj_set_size(code_field,134,49);
    lv_textarea_set_one_line(code_field,true);lv_textarea_set_max_length(code_field,6);
    lv_textarea_set_accepted_chars(code_field,"0123456789");
    lv_textarea_set_placeholder_text(code_field,"123456");
    lv_obj_add_event_cb(code_field,field_focused,LV_EVENT_FOCUSED,NULL);
    lv_obj_t *pair=lv_button_create(panel);lv_obj_set_pos(pair,326,146);lv_obj_set_size(pair,134,48);
    lv_obj_set_style_bg_color(pair,lv_color_hex(0x65508c),0);
    lv_obj_t *text=lv_label_create(pair);lv_label_set_text(text,"Re-pair");lv_obj_center(text);
    lv_obj_add_event_cb(pair,repair_tap,LV_EVENT_CLICKED,NULL);
    message=label(panel,"Make a code on the Ghost Server admin page (New pairing code). Wi-Fi is kept.",20,146,296);
    repair_keyboard=lv_keyboard_create(panel);lv_obj_set_size(repair_keyboard,480,226);
    lv_obj_align(repair_keyboard,LV_ALIGN_BOTTOM_MID,0,0);
    lv_keyboard_set_mode(repair_keyboard,LV_KEYBOARD_MODE_NUMBER);
    lv_keyboard_set_textarea(repair_keyboard,code_field);
    lv_obj_add_state(code_field,LV_STATE_FOCUSED);
}

static void show_networks(void)
{
    if(password) lv_textarea_set_text(password,"");
    header("Setup",close_page);
    char ssid[33],summary[120];int rssi;
    if(ghost_wifi_status(ssid,sizeof(ssid),&rssi)) snprintf(summary,sizeof(summary),"Connected: %.32s  (%d dBm)",ssid,rssi);
    else snprintf(summary,sizeof(summary),"Disconnected. Saved network: %.32s",ssid);
    label(panel,summary,20,64,300);
    lv_obj_t *server=lv_button_create(panel);lv_obj_set_pos(server,330,58);lv_obj_set_size(server,130,40);
    lv_obj_set_style_bg_color(server,lv_color_hex(0x493d68),0);
    lv_obj_t *server_text=lv_label_create(server);lv_label_set_text(server_text,"Server");lv_obj_center(server_text);
    lv_obj_add_event_cb(server,server_settings_tap,LV_EVENT_CLICKED,NULL);
    volume_label=label(panel,"",20,102,440);
    lv_label_set_text_fmt(volume_label,"Speaker volume: %d / 100",ghost_audio_volume());
    lv_obj_t *volume=lv_slider_create(panel);lv_obj_set_pos(volume,38,152);lv_obj_set_size(volume,404,16);
    lv_slider_set_range(volume,0,100);lv_slider_set_value(volume,ghost_audio_volume(),LV_ANIM_OFF);
    lv_obj_set_style_bg_color(volume,lv_color_hex(0xb9a4ff),LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(volume,lv_color_hex(0xeeeafb),LV_PART_KNOB);
    lv_obj_set_ext_click_area(volume,16);
    lv_obj_add_event_cb(volume,volume_changed,LV_EVENT_VALUE_CHANGED,NULL);
    lv_obj_add_event_cb(volume,volume_changed,LV_EVENT_RELEASED,NULL);
    if(!ghost_audio_ready()) lv_obj_add_state(volume,LV_STATE_DISABLED);
    lv_obj_t *wake=lv_checkbox_create(panel);lv_obj_set_pos(wake,20,190);
    lv_checkbox_set_text(wake,ghost_wake_ready()?"Hey Ghost (test)":"Hey Ghost unavailable");
    lv_obj_set_style_text_font(wake,&lv_font_montserrat_16,0);
    if(ghost_wake_enabled()) lv_obj_add_state(wake,LV_STATE_CHECKED);
    if(!ghost_wake_ready()) lv_obj_add_state(wake,LV_STATE_DISABLED);
    lv_obj_add_event_cb(wake,wake_changed,LV_EVENT_VALUE_CHANGED,NULL);
    lv_obj_t *tune=lv_button_create(panel);lv_obj_set_pos(tune,285,183);lv_obj_set_size(tune,175,42);
    lv_obj_set_style_bg_color(tune,lv_color_hex(0x493d68),0);
    lv_obj_t *tune_label=lv_label_create(tune);lv_label_set_text(tune_label,"Wake sensitivity");lv_obj_center(tune_label);
    lv_obj_add_event_cb(tune,wake_settings_tap,LV_EVENT_CLICKED,NULL);
    if(!ghost_wake_ready()) lv_obj_add_state(tune,LV_STATE_DISABLED);
    message=label(panel,"",20,240,440);
    list=lv_list_create(panel);lv_obj_set_pos(list,14,284);lv_obj_set_size(list,452,120);
    lv_obj_set_style_bg_color(list,lv_color_hex(0x242036),0);
    refresh=lv_button_create(panel);lv_obj_set_pos(refresh,20,420);lv_obj_set_size(refresh,440,46);
    lv_obj_set_style_bg_color(refresh,lv_color_hex(0x65508c),0);
    lv_obj_t *text=lv_label_create(refresh);lv_label_set_text(text,"Refresh available networks");lv_obj_center(text);
    lv_obj_add_event_cb(refresh,scan_tap,LV_EVENT_CLICKED,NULL);
    scan_tap(NULL);
}

void ghost_wifi_ui_open(void)
{
    if(panel) return;
    visible=true;
    panel=lv_obj_create(lv_screen_active());lv_obj_set_pos(panel,0,0);lv_obj_set_size(panel,480,480);
    lv_obj_remove_style_all(panel);lv_obj_set_size(panel,480,480);
    lv_obj_set_style_bg_color(panel,lv_color_hex(0x171526),0);lv_obj_set_style_bg_opa(panel,LV_OPA_COVER,0);
    lv_obj_remove_flag(panel,LV_OBJ_FLAG_SCROLLABLE);show_networks();
}
