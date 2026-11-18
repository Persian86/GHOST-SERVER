#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "freertos/queue.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "lvgl.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "ghost_config.h"
#include "cJSON.h"

enum {IDLE, CONNECTING, LISTENING, THINKING, SPEAKING, ERROR_STATE, SETUP};
static atomic_int state = SETUP;
static atomic_bool abort_turn, finish_turn, remote_done, disconnected;
static ghost_config_t config;
static esp_codec_dev_handle_t microphone, speaker;
static esp_websocket_client_handle_t socket_client;
static RingbufHandle_t playback;
static QueueHandle_t commands;
static lv_obj_t *avatar, *mouth, *status_label, *talk_label, *talk_button;
static char error_text[160] = "Tap Ghost to try again";
static char packet[4097];
static int packet_opcode;
static uint32_t setup_hold;

static void status_message(const char *message)
{
    if (bsp_display_lock(1000)) {
        lv_label_set_text(status_label,message);
        bsp_display_unlock();
    }
}

static lv_obj_t *shape(lv_obj_t *parent, int x, int y, int w, int h, uint32_t color, int radius)
{
    lv_obj_t *obj=lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_set_pos(obj,x,y); lv_obj_set_size(obj,w,h);
    lv_obj_set_style_bg_color(obj,lv_color_hex(color),0);
    lv_obj_set_style_bg_opa(obj,LV_OPA_COVER,0);
    lv_obj_set_style_radius(obj,radius,0);
    lv_obj_remove_flag(obj,LV_OBJ_FLAG_SCROLLABLE);
    return obj;
}

static void user_tap(lv_event_t *event)
{
    if (state == SETUP) return;
    int command = state == IDLE || state == ERROR_STATE ? 1 : state == LISTENING ? 2 : 3;
    xQueueSend(commands,&command,0);
}

static void setup_touch(lv_event_t *event)
{
    lv_event_code_t code=lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) setup_hold=lv_tick_get();
    if (code == LV_EVENT_LONG_PRESSED_REPEAT && lv_tick_elaps(setup_hold)>5000) {
        abort_turn=true;
        nvs_handle_t nvs;
        if (nvs_open("ghost_room",NVS_READWRITE,&nvs) == ESP_OK) {
            nvs_erase_key(nvs,"config"); nvs_commit(nvs); nvs_close(nvs);
            esp_restart();
        }
    }
}

static void animate(lv_timer_t *timer)
{
    static int last=-1;
    float t=esp_timer_get_time()/1000000.0f;
    lv_obj_set_y(avatar,95+(int)(sinf(t*1.5f)*7));
    int current=state;
    lv_obj_set_height(mouth,current==SPEAKING ? 10+(int)(fabsf(sinf(t*7))*10) : 9);
    if (current==last) return;
    last=current;
    const char *labels[]={"Tap Ghost to talk", "Connecting to Ghost...", "Listening - tap when finished",
        "Thinking...", "Speaking", error_text, "Setting up Ghost"};
    if (current!=SETUP) lv_label_set_text(status_label,labels[current]);
    lv_label_set_text(talk_label,current==IDLE || current==ERROR_STATE ? "Tap to talk" :
        current==LISTENING ? "Finish speaking" : "Cancel");
    lv_obj_set_style_bg_color(talk_button,lv_color_hex(current==LISTENING?0x3c766c:0x65508c),0);
}

static void build_ui(void)
{
    lv_obj_t *screen=lv_screen_active();
    lv_obj_set_style_bg_color(screen,lv_color_hex(0x171526),0);
    lv_obj_set_style_text_color(screen,lv_color_hex(0xeeeafb),0);
    lv_obj_t *title=lv_label_create(screen);
    lv_label_set_text(title,"Ghost");
    lv_obj_set_style_text_font(title,&lv_font_montserrat_28,0);
    lv_obj_align(title,LV_ALIGN_TOP_LEFT,24,18);
    lv_obj_t *setup=lv_button_create(screen);
    lv_obj_set_size(setup,94,48); lv_obj_align(setup,LV_ALIGN_TOP_RIGHT,-18,12);
    lv_obj_set_style_bg_color(setup,lv_color_hex(0x302943),0);
    lv_obj_add_event_cb(setup,setup_touch,LV_EVENT_ALL,NULL);
    lv_obj_t *setup_text=lv_label_create(setup);lv_label_set_text(setup_text,"Setup");lv_obj_center(setup_text);
    avatar=lv_obj_create(screen);lv_obj_remove_style_all(avatar);
    lv_obj_set_size(avatar,280,245);lv_obj_set_pos(avatar,100,95);
    lv_obj_remove_flag(avatar,LV_OBJ_FLAG_SCROLLABLE);
    // Native LVGL silhouette: dome, soft drapes, scalloped hem and glossy eyes.
    shape(avatar,55,0,170,220,0xf2f0ff,84);
    shape(avatar,25,83,75,97,0xddd9ee,38);
    shape(avatar,180,83,75,97,0xddd9ee,38);
    shape(avatar,51,110,178,106,0xf2f0ff,30);
    for(int i=0;i<4;i++) shape(avatar,55+i*43,179,42,56+(i%2)*7,0xf2f0ff,22);
    for(int side=0;side<2;side++) {
        int x=91+side*68;
        shape(avatar,x-5,56,40,48,0xffffff,24);
        shape(avatar,x,59,30,40,0x4c9edc,20);
        shape(avatar,x+5,62,20,30,0x13223c,15);
        shape(avatar,x+8,65,8,10,0xffffff,5);
        shape(avatar,x+20,83,4,5,0xc3f3ff,3);
    }
    shape(avatar,73,108,24,10,0xf5adc7,8);
    shape(avatar,185,108,24,10,0xf5adc7,8);
    mouth=shape(avatar,126,119,30,9,0x342338,14);
    status_label=lv_label_create(screen);
    lv_obj_set_width(status_label,440);lv_obj_set_height(status_label,76);
    lv_obj_set_style_text_align(status_label,LV_TEXT_ALIGN_CENTER,0);
    lv_obj_set_style_text_font(status_label,&lv_font_montserrat_20,0);
    lv_obj_align(status_label,LV_ALIGN_BOTTOM_MID,0,-76);
    talk_button=lv_button_create(screen);
    lv_obj_set_size(talk_button,320,56);lv_obj_align(talk_button,LV_ALIGN_BOTTOM_MID,0,-14);
    lv_obj_set_style_radius(talk_button,22,0);
    lv_obj_add_event_cb(talk_button,user_tap,LV_EVENT_CLICKED,NULL);
    talk_label=lv_label_create(talk_button);lv_obj_center(talk_label);
    lv_obj_set_style_text_font(talk_label,&lv_font_montserrat_20,0);
    lv_timer_create(animate,33,NULL);
}

static void fail(const char *message)
{
    if(state==ERROR_STATE) return;
    strlcpy(error_text,message,sizeof(error_text));
    ESP_LOGW("ghost_voice","%s",error_text);
    state=ERROR_STATE; abort_turn=true;
}

static void websocket_event(void *arg,esp_event_base_t base,int32_t event_id,void *event_data)
{
    esp_websocket_event_data_t *data=event_data;
    if (event_id==WEBSOCKET_EVENT_DISCONNECTED || event_id==WEBSOCKET_EVENT_ERROR) {
        disconnected=true; return;
    }
    if (event_id!=WEBSOCKET_EVENT_DATA) return;
    if (data->op_code==0x8) {
        if (data->data_len>=2) {
            unsigned code=((unsigned char)data->data_ptr[0]<<8)|(unsigned char)data->data_ptr[1];
            ESP_LOGW("ghost_voice","WebSocket closed with code %u",code);
            if(code==4401 || code==4403) fail("Pair and approve this device on Ghost Server.");
            else if(code==4400) fail("Server rejected the audio stream (4400).");
            else if(code==4429) fail("Another voice session is active. Wait and retry.");
            else if(code==4426) fail("Server requires HTTPS. This LAN prototype needs a TLS upgrade.");
        }
        disconnected=true; return;
    }
    if (data->op_code==0x9 || data->op_code==0xa) return;
    if(data->payload_offset==0) packet_opcode=data->op_code;
    if(data->payload_len <= 0 || data->payload_len > 4096 || data->payload_offset<0 ||
        data->data_len<0 || data->payload_offset+data->data_len > data->payload_len) {
        fail("Invalid server reply.");return;
    }
    memcpy(packet+data->payload_offset,data->data_ptr,data->data_len);
    if(data->payload_offset+data->data_len!=data->payload_len) return;
    if(packet_opcode==0x2) {
        if (state!=THINKING && state!=SPEAKING) {fail("Unexpected audio. Tap to retry.");return;}
        if(data->payload_len%2 ||
            xRingbufferSend(playback,packet,data->payload_len,pdMS_TO_TICKS(3000))!=pdTRUE) {
            fail("Playback stalled. Tap to try again."); return;
        }
        state=SPEAKING; return;
    }
    if(packet_opcode!=0x1 || data->payload_len > 512) {
        fail("Invalid server reply.");return;
    }
    packet[data->payload_len]=0;
    cJSON *json=cJSON_Parse(packet);
    cJSON *type=cJSON_GetObjectItemCaseSensitive(json,"type");
    if(cJSON_IsString(type)) {
        if(!strcmp(type->valuestring,"ready") && state==CONNECTING) {
            ESP_LOGW("ghost_voice","Voice ready; sending 16 kHz mono PCM at 20 ms intervals");
            state=LISTENING;
        }
        else if(!strcmp(type->valuestring,"thinking")) state=THINKING;
        else if(!strcmp(type->valuestring,"speaking")) state=SPEAKING;
        else if(!strcmp(type->valuestring,"done")) remote_done=true;
        else if(!strcmp(type->valuestring,"error")) {
            const cJSON *message=cJSON_GetObjectItemCaseSensitive(json,"message");
            fail(cJSON_IsString(message)?message->valuestring:"Ghost voice could not connect. Check the server, then retry.");
        }
    }
    cJSON_Delete(json);
}

static void drain_playback(void)
{
    size_t len;void *item;
    while((item=xRingbufferReceive(playback,&len,0))) vRingbufferReturnItem(playback,item);
}

static void play_task(void *arg)
{
    int16_t stereo[2048];
    while(true) {
        size_t len; void *item=xRingbufferReceiveUpTo(playback,&len,pdMS_TO_TICKS(50),2048);
        if(!item) continue;
        if(!abort_turn) {
            bsp_audio_poweramp_enable(true);
            const int16_t *mono=item;
            for(size_t i=0;i<len/2;i++) stereo[2*i]=stereo[2*i+1]=mono[i];
            if(esp_codec_dev_write(speaker,stereo,len*2)!=ESP_OK) fail("Speaker error. Restart Ghost.");
        }
        vRingbufferReturnItem(playback,item);
    }
}

static void voice_task(void *arg)
{
    char uri[160],headers[200];
    int command;
    int16_t samples[320];
    int16_t capture[640];
    while(true) {
        if(xQueueReceive(commands,&command,pdMS_TO_TICKS(20))!=pdTRUE || command!=1) continue;
        if(state!=IDLE && state!=ERROR_STATE) continue;
        if(!ghost_wifi_ready()) {fail("Wi-Fi disconnected. Reconnecting...");continue;}
        drain_playback(); abort_turn=false; finish_turn=false;remote_done=false;disconnected=false;
        state=CONNECTING;
        snprintf(uri,sizeof(uri),"ws://%.88s/api/v1/room-voice",config.server+7);
        snprintf(headers,sizeof(headers),"Authorization: Bearer %.159s\r\n",config.key);
        esp_websocket_client_config_t ws={.uri=uri,.headers=headers,.disable_auto_reconnect=true,
            .network_timeout_ms=12000,.buffer_size=4096,.task_stack=6144};
        socket_client=esp_websocket_client_init(&ws);
        if(!socket_client) {fail("Not enough memory to connect.");continue;}
        esp_websocket_register_events(socket_client,WEBSOCKET_EVENT_ANY,websocket_event,NULL);
        if(esp_websocket_client_start(socket_client)!=ESP_OK) fail("Could not open Ghost connection.");
        int64_t started=esp_timer_get_time(),record_started=0;
        int64_t next_frame_at=0;
        unsigned frames=0;
        bool sent_stop=false;
        while(!abort_turn) {
            while(xQueueReceive(commands,&command,0)==pdTRUE) {
                if(command==2 && state==LISTENING) finish_turn=true;
                if(command==3) abort_turn=true;
            }
            if(abort_turn) break;
            if(state==LISTENING) {
                if(!record_started) record_started=esp_timer_get_time();
                if(finish_turn || esp_timer_get_time()-record_started>19000000) {
                    state=THINKING;
                    sent_stop=esp_websocket_client_send_text(socket_client,"{\"type\":\"stop\"}",15,pdMS_TO_TICKS(3000))>0;
                    if(!sent_stop) fail("Connection lost. Tap to try again.");
                } else {
                    // DMA can already contain several frames when recording starts.
                    // Never flush that backlog to the server faster than real time.
                    int64_t wait_us=next_frame_at-esp_timer_get_time();
                    if(wait_us>0) vTaskDelay(pdMS_TO_TICKS((wait_us+999)/1000));
                    int64_t read_started=esp_timer_get_time();
                    next_frame_at=read_started+20000;
                    esp_err_t read_result=esp_codec_dev_read(microphone,capture,sizeof(capture));
                    int64_t read_us=esp_timer_get_time()-read_started;
                    if(read_result!=ESP_OK) {
                        ESP_LOGW("ghost_voice","Microphone read failed: %d",(int)read_result);
                        fail("Microphone read failed. Restart Ghost.");
                    } else {
                        // ES7210's first physical microphone; avoid summing opposite-phase inputs.
                        for(int i=0;i<320;i++) samples[i]=capture[i*2];
                        if(esp_websocket_client_send_bin(socket_client,(char *)samples,sizeof(samples),pdMS_TO_TICKS(3000))!=(int)sizeof(samples))
                            fail("Audio upload failed. Check Wi-Fi and retry.");
                        frames++;
                        if(frames==1 || frames%50==0) ESP_LOGW("ghost_voice",
                            "Capture frames=%u elapsed_ms=%lld last_read_us=%lld",frames,
                            (long long)((esp_timer_get_time()-record_started)/1000),(long long)read_us);
                    }
                }
            } else vTaskDelay(pdMS_TO_TICKS(10));
            if(remote_done) {
                // A byte ring item may still be inside the codec write call.
                size_t free_space=xRingbufferGetCurFreeSize(playback);
                if(free_space>=64000-64) {vTaskDelay(pdMS_TO_TICKS(150));break;}
            }
            if(disconnected && !remote_done && !abort_turn) {fail("Connection ended. Check Ghost Server and retry.");break;}
            if(esp_timer_get_time()-started>100000000 ||
                (state==CONNECTING && esp_timer_get_time()-started>20000000)) {fail("Ghost timed out. Tap to retry.");break;}
        }
        if(abort_turn) drain_playback();
        esp_websocket_client_stop(socket_client);
        esp_websocket_client_destroy(socket_client);socket_client=NULL;
        bsp_audio_poweramp_enable(false);
        memset(samples,0,sizeof(samples));
        memset(capture,0,sizeof(capture));
        if(state!=ERROR_STATE) state=IDLE;
    }
}

void app_main(void)
{
    // Do not erase the original NVS partition on initialization failure.
    esp_err_t nvs_result=nvs_flash_init();
    if(!bsp_display_start()) return;
    bsp_display_brightness_set(75);
    commands=xQueueCreate(4,sizeof(int));
    playback=xRingbufferCreate(64000,RINGBUF_TYPE_BYTEBUF);
    if(!commands || !playback) return;
    bsp_display_lock(0);build_ui();bsp_display_unlock();
    if(nvs_result!=ESP_OK) {status_message("Settings storage error. Original data was not erased.");return;}
    if(!ghost_config_connect(&config,status_message)) return;
    // Configure the shared bus BEFORE constructing either codec. BSP's default
    // is 22.05 kHz mono, which differs from this application's stereo DMA layout.
    const i2s_std_config_t bus={
        .clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg=I2S_STD_PHILIP_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,I2S_SLOT_MODE_STEREO),
        .gpio_cfg={.mclk=BSP_I2S_MCLK,.bclk=BSP_I2S_SCLK,.ws=BSP_I2S_LCLK,
            .dout=BSP_I2S_DOUT,.din=BSP_I2S_DSIN,
            .invert_flags={.mclk_inv=false,.bclk_inv=false,.ws_inv=false}}
    };
    if(bsp_audio_init(&bus)!=ESP_OK) {fail("Audio bus did not initialize.");return;}
    microphone=bsp_audio_codec_microphone_init();speaker=bsp_audio_codec_speaker_init();
    if(!microphone || !speaker) {fail("Audio hardware did not initialize.");return;}
    // Match the official Waveshare codec example's 16 kHz, 16-bit stereo I2S bus.
    esp_codec_dev_sample_info_t audio={.sample_rate=16000,.channel=2,.bits_per_sample=16};
    if(esp_codec_dev_open(speaker,&audio)!=ESP_OK || esp_codec_dev_open(microphone,&audio)!=ESP_OK) {
        fail("Audio format error. Restart Ghost.");return;
    }
    esp_codec_dev_set_in_gain(microphone,24.0f);
    esp_codec_dev_set_out_vol(speaker,45);
    bsp_audio_poweramp_enable(false);
    if(xTaskCreate(play_task,"ghost_speaker",8192,NULL,5,NULL)!=pdPASS ||
        xTaskCreate(voice_task,"ghost_voice",8192,NULL,4,NULL)!=pdPASS) {fail("Not enough memory for voice.");return;}
    state=IDLE;
}
