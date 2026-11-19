#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "freertos/queue.h"
#include "esp_system.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_websocket_client.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "lvgl.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "ghost_config.h"
#include "ghost_wifi_ui.h"
#include "ghost_audio.h"
#include "ghost_wake.h"
#include "ghost_uplink.h"
#include "cJSON.h"

enum {IDLE, CONNECTING, LISTENING, THINKING, SPEAKING, ERROR_STATE, SETUP};
static atomic_int state = SETUP;
static atomic_int mic_level;
static atomic_int speech_level;
static atomic_bool auto_finish;
static atomic_bool continuous_supported,remote_follow_up,follow_up;
static atomic_int output_volume=70;
static atomic_bool audio_ready;
static atomic_bool abort_turn, finish_turn, remote_done, disconnected;
static ghost_config_t config;
static esp_codec_dev_handle_t microphone, speaker;
static esp_websocket_client_handle_t socket_client;
static RingbufHandle_t playback;
static QueueHandle_t commands;
static ghost_uplink_t *uplink;
static lv_obj_t *avatar, *mouth, *status_label, *talk_label, *talk_button;
static lv_obj_t *end_button;
static lv_obj_t *mic_meter,*mic_caption;
static lv_obj_t *eyes[2],*irises[2],*drapes[2],*hem[4];
static char error_text[160] = "Tap Ghost to try again";
static char packet[4097];
static int packet_opcode;

bool ghost_audio_ready(void) {return audio_ready;}
int ghost_audio_volume(void) {return output_volume;}

static esp_err_t apply_volume(int volume)
{
    if(esp_codec_dev_set_out_vol(speaker,volume)!=ESP_OK) return ESP_FAIL;
    return esp_codec_dev_set_out_mute(speaker,volume==0)==ESP_OK?ESP_OK:ESP_FAIL;
}

esp_err_t ghost_audio_set_volume(int volume)
{
    if(volume<0 || volume>100) return ESP_ERR_INVALID_ARG;
    if(!audio_ready) return ESP_ERR_INVALID_STATE;
    int previous=output_volume;
    if(volume==previous) return ESP_OK;
    esp_err_t result=apply_volume(volume);
    if(result==ESP_OK) {
        nvs_handle_t handle;
        result=nvs_open("ghost_audio",NVS_READWRITE,&handle);
        if(result==ESP_OK) {
            result=nvs_set_u8(handle,"volume",(uint8_t)volume);
            if(result==ESP_OK) result=nvs_commit(handle);
            nvs_close(handle);
        }
    }
    if(result==ESP_OK) output_volume=volume;
    else apply_volume(previous);
    return result;
}

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
    if (state == SETUP || ghost_wifi_ui_visible()) return;
    int command = state == IDLE || state == ERROR_STATE ? 1 : state == LISTENING ? 2 : 3;
    xQueueSend(commands,&command,0);
}

static void end_tap(lv_event_t *event)
{
    int command=3;
    if(state==CONNECTING || state==LISTENING || state==THINKING || state==SPEAKING) {
        abort_turn=true;
        ghost_uplink_cancel(uplink);
        xQueueSend(commands,&command,0);
    }
}

static void setup_touch(lv_event_t *event)
{
    if(lv_event_get_code(event)!=LV_EVENT_CLICKED) return;
    if(state==CONNECTING || state==LISTENING || state==THINKING || state==SPEAKING) {
        lv_label_set_text(status_label,"Finish or cancel the conversation before opening Wi-Fi settings.");return;
    }
    ghost_wifi_ui_open();
}

static void animate(lv_timer_t *timer)
{
    static int last=-1;
    static int last_wake=-1;
    static float mouth_open;
    static int64_t next_blink,blink_start;
    static int64_t previous_frame;
    static int64_t report_at;
    static unsigned frame_count,slow_frames,max_frame_ms;
    if(ghost_wifi_ui_visible()) {previous_frame=0;return;} // No hidden redraws.
    int64_t now=esp_timer_get_time();
    unsigned elapsed=previous_frame?(unsigned)((now-previous_frame)/1000):33;
    previous_frame=now;
    if(elapsed>max_frame_ms) max_frame_ms=elapsed;
    if(elapsed>80) slow_frames++;
    frame_count++;
    if(!report_at) report_at=now;
    if(now-report_at>=5000000) {
        ESP_LOGW("ghost_motion","Frames=%u slow_over_80ms=%u max_interval_ms=%u",frame_count,slow_frames,max_frame_ms);
        report_at=now;frame_count=slow_frames=max_frame_ms=0;
    }
    float dt=fminf(elapsed/1000.0f,0.1f);
    float t=now/1000000.0f;
    if(!next_blink) next_blink=now+3000000;
    if(now>=next_blink) {blink_start=now;next_blink=now+3000000+esp_random()%4000000;}
    float blink=blink_start && now-blink_start<200000?sinf((now-blink_start)*3.141593f/200000.0f):0;
    lv_obj_set_y(avatar,95+(int)(sinf(t*1.15f)*7+sinf(t*0.47f)*2));
    lv_obj_set_x(avatar,100+(int)(sinf(t*0.55f)*4));
    int current=state;
    for(int side=0;side<2;side++) {
        lv_obj_set_y(drapes[side],83+(int)(sinf(t*1.3f+side*1.6f)*4));
        lv_obj_set_style_transform_scale_y(eyes[side],256-(int)(blink*235),0);
        lv_obj_set_x(irises[side],5+(current==THINKING?-3:(int)(sinf(t*0.35f)*2)));
        lv_obj_set_y(irises[side],3+(current==THINKING?-2:0));
    }
    for(int i=0;i<4;i++) lv_obj_set_y(hem[i],179+(int)(sinf(t*1.4f+i*0.9f)*3));
    lv_bar_set_value(mic_meter,current==LISTENING?mic_level:0,LV_ANIM_OFF);
    float target=current==SPEAKING?speech_level/100.0f:0;
    // Keep attack/release timing stable even when a display frame is delayed.
    mouth_open+=(target-mouth_open)*(1.0f-expf(-dt/(target>mouth_open?0.055f:0.13f)));
    lv_obj_set_height(mouth,9+(int)(mouth_open*19));
    lv_obj_set_width(mouth,30-(int)(mouth_open*6));
    int wake=ghost_wake_enabled();
    if (current==last && wake==last_wake) return;
    last_wake=wake;
    last=current;
    if(current==LISTENING) {lv_obj_remove_flag(mic_meter,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(mic_caption,LV_OBJ_FLAG_HIDDEN);}
    else {lv_obj_add_flag(mic_meter,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(mic_caption,LV_OBJ_FLAG_HIDDEN);}
    const char *labels[]={wake?"Say Hey Ghost, or tap to talk":"Tap Ghost to talk", "Connecting to Ghost...",
        follow_up?"Anything else? Listening...":auto_finish?"Listening - pause when finished":"Listening - tap when finished",
        "Thinking...", "Speaking", error_text, "Setting up Ghost"};
    if (current!=SETUP) lv_label_set_text(status_label,labels[current]);
    bool active=current==CONNECTING || current==LISTENING || current==THINKING || current==SPEAKING;
    if(active) {
        lv_obj_remove_flag(end_button,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_width(talk_button,300);lv_obj_align(talk_button,LV_ALIGN_BOTTOM_LEFT,20,-14);
    } else {
        lv_obj_add_flag(end_button,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_width(talk_button,320);lv_obj_align(talk_button,LV_ALIGN_BOTTOM_MID,0,-14);
    }
    if(active && current!=LISTENING) lv_obj_add_state(talk_button,LV_STATE_DISABLED);
    else lv_obj_remove_state(talk_button,LV_STATE_DISABLED);
    lv_label_set_text(talk_label,current==IDLE || current==ERROR_STATE ? "Tap to talk" :
        current==LISTENING ? "Send now" : "Please wait...");
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
    mic_caption=lv_label_create(screen);lv_label_set_text(mic_caption,"Mic");lv_obj_set_pos(mic_caption,24,59);
    mic_meter=lv_bar_create(screen);lv_obj_set_pos(mic_meter,64,65);lv_obj_set_size(mic_meter,180,8);
    lv_bar_set_range(mic_meter,0,100);lv_obj_set_style_bg_color(mic_meter,lv_color_hex(0x6fddb0),LV_PART_INDICATOR);
    lv_obj_add_flag(mic_meter,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(mic_caption,LV_OBJ_FLAG_HIDDEN);
    avatar=lv_obj_create(screen);lv_obj_remove_style_all(avatar);
    lv_obj_set_size(avatar,280,245);lv_obj_set_pos(avatar,100,95);
    lv_obj_remove_flag(avatar,LV_OBJ_FLAG_SCROLLABLE);
    // Native LVGL silhouette: dome, soft drapes, scalloped hem and glossy eyes.
    shape(avatar,55,0,170,220,0xf2f0ff,84);
    drapes[0]=shape(avatar,25,83,75,97,0xddd9ee,38);
    drapes[1]=shape(avatar,180,83,75,97,0xddd9ee,38);
    shape(avatar,51,110,178,106,0xf2f0ff,30);
    for(int i=0;i<4;i++) hem[i]=shape(avatar,55+i*43,179,42,56+(i%2)*7,0xf2f0ff,22);
    for(int side=0;side<2;side++) {
        int x=91+side*68;
        eyes[side]=shape(avatar,x-5,56,40,48,0xffffff,24);
        lv_obj_set_style_transform_pivot_y(eyes[side],24,0);
        irises[side]=shape(eyes[side],5,3,30,40,0x4c9edc,20);
        shape(irises[side],5,3,20,30,0x13223c,15);
        shape(irises[side],8,6,8,10,0xffffff,5);
        shape(irises[side],20,24,4,5,0xc3f3ff,3);
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
    end_button=lv_button_create(screen);lv_obj_set_size(end_button,120,56);
    lv_obj_align(end_button,LV_ALIGN_BOTTOM_RIGHT,-20,-14);
    lv_obj_set_style_bg_color(end_button,lv_color_hex(0x493d68),0);
    lv_obj_add_event_cb(end_button,end_tap,LV_EVENT_CLICKED,NULL);
    lv_obj_t *end_label=lv_label_create(end_button);lv_label_set_text(end_label,"End");lv_obj_center(end_label);
    lv_obj_add_flag(end_button,LV_OBJ_FLAG_HIDDEN);
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
        ESP_LOGW("ghost_voice","Transport event=%ld close=%d handshake=%d errno=%d",(long)event_id,
            data->close_status_code,data->error_handle.esp_ws_handshake_status_code,data->error_handle.esp_transport_sock_errno);
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
            continuous_supported=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(json,"continuous"));
            ESP_LOGW("ghost_voice","Voice ready; continuous=%d follow_up=%d",(int)continuous_supported,(int)follow_up);
            state=LISTENING;
        }
        else if(!strcmp(type->valuestring,"thinking")) state=THINKING;
        else if(!strcmp(type->valuestring,"speaking")) state=SPEAKING;
        else if(!strcmp(type->valuestring,"done")) {
            remote_follow_up=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(json,"follow_up"));
            remote_done=true;
        }
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

static bool upload_audio(const int16_t *samples,size_t bytes,unsigned frames)
{
    int64_t began=esp_timer_get_time();
    int sent=esp_websocket_client_send_bin(socket_client,(const char *)samples,bytes,pdMS_TO_TICKS(3000));
    int64_t duration_ms=(esp_timer_get_time()-began)/1000;
    if(sent!=(int)bytes || duration_ms>250) {
        char unused_ssid[33];int rssi;ghost_wifi_status(unused_ssid,sizeof(unused_ssid),&rssi);
        ESP_LOGW("ghost_voice","Upload frames=%u bytes=%u sent=%d duration_ms=%lld wifi_rssi=%d internal_free=%u",
            frames,(unsigned)bytes,sent,(long long)duration_ms,rssi,
            (unsigned)esp_get_free_internal_heap_size());
    }
    // A failed WebSocket write may have sent part of the frame. Never retry it
    // on the same stream, which could duplicate audio or corrupt framing.
    if(sent!=(int)bytes) {fail("Audio upload stalled. Tap to retry.");return false;}
    return true;
}

static bool queue_audio(const int16_t *samples,size_t bytes,unsigned frames)
{
    if(ghost_uplink_push(uplink,samples,bytes,frames,esp_timer_get_time())) return true;
    if(!abort_turn) fail("Audio connection too slow. Move closer to Wi-Fi and retry.");
    return false;
}

static void upload_task(void *arg)
{
    int64_t next_send=0;
    while(true) {
        const ghost_uplink_block_t *block=ghost_uplink_peek(uplink);
        if(!block) {vTaskDelay(pdMS_TO_TICKS(5));continue;}
        if(!ghost_uplink_cancelled(uplink)) {
            // Keep writes paced even when a short stall leaves queued audio.
            // The capture task never waits for this network operation.
            while(block->bytes && esp_timer_get_time()<next_send && !ghost_uplink_cancelled(uplink))
                vTaskDelay(pdMS_TO_TICKS(5));
            if(!ghost_uplink_cancelled(uplink)) {
                if(block->bytes) {
                    int64_t now=esp_timer_get_time();
                    if(now-block->captured_us>3000000) {
                        fail("Audio connection too slow. Tap to retry.");
                        ghost_uplink_cancel(uplink);
                    } else {
                        next_send=now+(int64_t)block->bytes*1000000/32000;
                        if(!upload_audio(block->samples,block->bytes,block->frames)) ghost_uplink_cancel(uplink);
                    }
                } else {
                    static const char stop[]="{\"type\":\"stop\"}";
                    if(esp_websocket_client_send_text(socket_client,stop,sizeof(stop)-1,pdMS_TO_TICKS(3000))!=(int)sizeof(stop)-1)
                        fail("Connection lost. Tap to try again.");
                    next_send=0;
                }
            }
        } else next_send=0;
        // Release only after the write completes. Erases this PCM block and
        // publishes ownership back to capture before socket teardown/reuse.
        ghost_uplink_release(uplink);
    }
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
            int64_t energy=0;
            for(size_t i=0;i<len/2;i++) {stereo[2*i]=stereo[2*i+1]=mono[i];energy+=(int64_t)mono[i]*mono[i];}
            int level=len?(int)(sqrtf((float)energy/(len/2))/45.0f):0;
            speech_level=level>100?100:level;
            if(esp_codec_dev_write(speaker,stereo,len*2)!=ESP_OK) fail("Speaker error. Restart Ghost.");
        }
        vRingbufferReturnItem(playback,item);
        speech_level=0;
    }
}

static void voice_task(void *arg)
{
    char uri[160],headers[200];
    int command;
    // Five 20 ms microphone frames per bounded 100 ms network message.
    // Static storage keeps the larger batch off the voice task's stack.
    static int16_t samples[1600];
    int16_t capture[640];
    int64_t wake_after=0;
    bool idle_monitoring=false;
    float noise_floor=60;
    while(true) {
        bool wake_turn=false;
        if(xQueueReceive(commands,&command,0)!=pdTRUE) {
            bool monitor=(state==IDLE || state==ERROR_STATE) && ghost_wake_enabled() &&
                !ghost_wifi_ui_visible() && ghost_wifi_ready() && esp_timer_get_time()>=wake_after;
            if(!monitor) {
                if(idle_monitoring) {ghost_wake_reset();idle_monitoring=false;}
                vTaskDelay(pdMS_TO_TICKS(20));continue;
            }
            if(!idle_monitoring) {ghost_wake_reset();idle_monitoring=true;}
            memset(capture,0,sizeof(capture));
            if(esp_codec_dev_read(microphone,capture,sizeof(capture))!=ESP_OK) {
                wake_after=esp_timer_get_time()+1000000;continue;
            }
            int64_t energy=0;
            for(int i=0;i<320;i++) {samples[i]=capture[i*2];energy+=(int64_t)samples[i]*samples[i];}
            float rms=sqrtf((float)energy/320);
            if(rms<noise_floor*2) noise_floor=noise_floor*0.995f+rms*0.005f;
            if(!ghost_wake_feed(samples,320)) {vTaskDelay(1);continue;}
            // Setup or a saved toggle may have changed during inference.
            if(!ghost_wake_enabled() || ghost_wifi_ui_visible()) continue;
            command=1;wake_turn=true;
        }
        if(command!=1) continue;
        ghost_wake_reset();idle_monitoring=false;
        if(state!=IDLE && state!=ERROR_STATE) continue;
        if(ghost_wifi_ui_visible()) continue;
        if(!ghost_wifi_ready()) {fail("Wi-Fi disconnected. Reconnecting...");continue;}
        drain_playback(); abort_turn=false; finish_turn=false;remote_done=false;disconnected=false;
        if(!ghost_uplink_begin(uplink)) {fail("Audio is still closing. Tap to retry.");continue;}
        mic_level=0;
        continuous_supported=false;remote_follow_up=false;follow_up=false;
        auto_finish=wake_turn;
        state=CONNECTING;
        snprintf(uri,sizeof(uri),"ws://%.88s/api/v1/room-voice?continuous=1",config.server+7);
        snprintf(headers,sizeof(headers),"Authorization: Bearer %.159s\r\n",config.key);
        esp_websocket_client_config_t ws={.uri=uri,.headers=headers,.disable_auto_reconnect=true,
            .network_timeout_ms=12000,.buffer_size=4096,.task_stack=6144};
        socket_client=esp_websocket_client_init(&ws);
        if(!socket_client) {fail("Not enough memory to connect.");continue;}
        esp_websocket_register_events(socket_client,WEBSOCKET_EVENT_ANY,websocket_event,NULL);
        if(esp_websocket_client_start(socket_client)!=ESP_OK) fail("Could not open Ghost connection.");
        int64_t started=esp_timer_get_time(),record_started=0;
        int64_t connecting_started=started;
        int64_t next_frame_at=0;
        unsigned frames=0;
        size_t buffered=0;
        int64_t last_speech=0;
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
                    bool flushed=!buffered || queue_audio(samples,buffered*sizeof(samples[0]),frames);
                    buffered=0;
                    if(flushed) {
                        // Ordered after all audio, including a short final block.
                        queue_audio(NULL,0,frames);
                    }
                } else {
                    // DMA can already contain several frames when recording starts.
                    // Never flush that backlog to the server faster than real time.
                    int64_t wait_us=next_frame_at-esp_timer_get_time();
                    if(wait_us>0) vTaskDelay(pdMS_TO_TICKS((wait_us+999)/1000));
                    int64_t read_started=esp_timer_get_time();
                    next_frame_at=read_started+20000;
                    memset(capture,0,sizeof(capture));
                    esp_err_t read_result=esp_codec_dev_read(microphone,capture,sizeof(capture));
                    int64_t read_us=esp_timer_get_time()-read_started;
                    if(read_result!=ESP_OK) {
                        ESP_LOGW("ghost_voice","Microphone read failed: %d",(int)read_result);
                        fail("Microphone read failed. Restart Ghost.");
                    } else {
                        // ES7210's first physical microphone; avoid summing opposite-phase inputs.
                        int peak=0;int64_t energy=0;
                        for(int i=0;i<320;i++) {
                            int value=capture[i*2];samples[buffered+i]=value;
                            int magnitude=value<0?-value:value;
                            if(magnitude>peak) peak=magnitude;
                            energy+=(int64_t)value*value;
                        }
                        unsigned rms=(unsigned)sqrtf((float)energy/320.0f);
                        if(auto_finish) {
                            int64_t now=esp_timer_get_time();
                            if(rms>fmaxf(120,noise_floor*2.5f)) last_speech=now;
                            if(last_speech && now-last_speech>1300000) finish_turn=true;
                            if(!last_speech && now-record_started>(follow_up?10000000:6000000)) {
                                if(follow_up) {ESP_LOGW("ghost_voice","Conversation ended after quiet");abort_turn=true;}
                                else finish_turn=true;
                            }
                        }
                        // Perceptual speech meter: -50 dBFS is quiet, -18 dBFS is full.
                        float dbfs=20.0f*log10f(fmaxf((float)rms,1.0f)/32768.0f);
                        int meter=(int)((dbfs+50.0f)*100.0f/32.0f);
                        if(meter<0) meter=0;
                        if(meter>100) meter=100;
                        mic_level=(mic_level*2+meter)/3;
                        frames++;
                        buffered+=320;
                        if(buffered==1600) {if(!abort_turn) queue_audio(samples,sizeof(samples),frames);buffered=0;}
                        if(frames==1 || frames%50==0) {
                            char unused_ssid[33];int rssi;ghost_wifi_status(unused_ssid,sizeof(unused_ssid),&rssi);
                            ESP_LOGW("ghost_voice","Capture frames=%u elapsed_ms=%lld last_read_us=%lld peak=%d rms=%u wifi_rssi=%d",frames,
                                (long long)((esp_timer_get_time()-record_started)/1000),(long long)read_us,peak,rms,rssi);
                        }
                    }
                }
            } else vTaskDelay(pdMS_TO_TICKS(10));
            if(remote_done) {
                // A byte ring item may still be inside the codec write call.
                size_t free_space=xRingbufferGetCurFreeSize(playback);
                if(free_space>=64000-64) {
                    vTaskDelay(pdMS_TO_TICKS(150));
                    if(!continuous_supported || !remote_follow_up || abort_turn) break;
                    bsp_audio_poweramp_enable(false);speech_level=0;
                    // Discard microphone DMA backlog/room echo before opening
                    // the next activity, while continuing to honor End.
                    for(int i=0;i<16 && !abort_turn;i++) {
                        while(xQueueReceive(commands,&command,0)==pdTRUE) if(command==3) abort_turn=true;
                        if(!abort_turn && esp_codec_dev_read(microphone,capture,sizeof(capture))!=ESP_OK)
                            fail("Microphone read failed. Restart Ghost.");
                    }
                    if(abort_turn) break;
                    if(!ghost_uplink_begin(uplink)) {fail("Previous audio is still closing. Tap to retry.");break;}
                    record_started=0;last_speech=0;next_frame_at=0;frames=0;buffered=0;
                    finish_turn=false;remote_done=false;remote_follow_up=false;
                    follow_up=true;auto_finish=true;state=CONNECTING;connecting_started=esp_timer_get_time();
                    static const char start[]="{\"type\":\"start\"}";
                    if(esp_websocket_client_send_text(socket_client,start,sizeof(start)-1,pdMS_TO_TICKS(3000))!=(int)sizeof(start)-1)
                        fail("Could not continue conversation. Tap to retry.");
                    else ESP_LOGW("ghost_voice","Opening follow-up on the same voice session");
                }
            }
            if(disconnected && !remote_done && !abort_turn) {fail("Connection ended. Check Ghost Server and retry.");break;}
            if(esp_timer_get_time()-started>(continuous_supported?300000000:100000000) ||
                (state==CONNECTING && esp_timer_get_time()-connecting_started>20000000)) {fail("Ghost timed out. Tap to retry.");break;}
        }
        ghost_uplink_cancel(uplink);
        // A send may already be in progress. Never destroy/reuse its client
        // until the network worker has released every queued block.
        while(!ghost_uplink_idle(uplink)) vTaskDelay(pdMS_TO_TICKS(10));
        if(abort_turn) {
            if(state!=ERROR_STATE && esp_websocket_client_is_connected(socket_client)) {
                static const char cancel[]="{\"type\":\"cancel\"}";
                esp_websocket_client_send_text(socket_client,cancel,sizeof(cancel)-1,pdMS_TO_TICKS(1000));
            }
            drain_playback();
        }
        esp_websocket_client_stop(socket_client);
        esp_websocket_client_destroy(socket_client);socket_client=NULL;
        bsp_audio_poweramp_enable(false);
        speech_level=0;auto_finish=false;follow_up=false;wake_after=esp_timer_get_time()+2500000;
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
    uplink=heap_caps_malloc(sizeof(*uplink),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!commands || !playback || !uplink) return;
    ghost_uplink_init(uplink);
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
    esp_codec_dev_set_in_gain(microphone,30.0f);
    nvs_handle_t audio_settings;uint8_t saved_volume;
    if(nvs_open("ghost_audio",NVS_READONLY,&audio_settings)==ESP_OK) {
        if(nvs_get_u8(audio_settings,"volume",&saved_volume)==ESP_OK && saved_volume<=100)
            output_volume=saved_volume;
        nvs_close(audio_settings);
    }
    if(apply_volume(output_volume)!=ESP_OK) {fail("Speaker volume did not initialize.");return;}
    audio_ready=true;
    status_message("Preparing local Hey Ghost detection...");
    ghost_wake_init(); // Recognition failure leaves manual tap-to-talk available.
    bsp_audio_poweramp_enable(false);
    if(xTaskCreate(upload_task,"ghost_upload",6144,NULL,4,NULL)!=pdPASS ||
        xTaskCreate(play_task,"ghost_speaker",8192,NULL,5,NULL)!=pdPASS ||
        xTaskCreate(voice_task,"ghost_voice",16384,NULL,4,NULL)!=pdPASS) {fail("Not enough memory for voice.");return;}
    state=IDLE;
}
