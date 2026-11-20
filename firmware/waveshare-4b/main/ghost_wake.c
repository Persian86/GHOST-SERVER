#include "ghost_wake.h"
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_mn_iface.h"
#include "esp_mn_models.h"
#include "esp_mn_speech_commands.h"
#include "model_path.h"
#include "nvs.h"

extern const unsigned char ghost_wake_bin[] asm("_binary_ghost_wake_bin_start");
static const esp_mn_iface_t *recognizer;
static model_iface_data_t *model;
static int16_t *frame;
static int chunk,used;
static atomic_bool ready,enabled=true;
static atomic_int sensitivity=70;
static int applied_sensitivity=-1;
static float threshold;
// Bump for a materially changed initializer so a repaired version can retry.
#define WAKE_INITIALIZER_VERSION 0x00020301u

static esp_err_t boot_guard(uint32_t value)
{
    nvs_handle_t handle;
    esp_err_t result=nvs_open("ghost_audio",NVS_READWRITE,&handle);
    if(result==ESP_OK) {
        result=nvs_set_u32(handle,"wake_guard",value);
        if(result==ESP_OK) result=nvs_commit(handle);
        nvs_close(handle);
    }
    return result;
}

bool ghost_wake_ready(void) {return ready;}
bool ghost_wake_enabled(void) {return ready && enabled;}
int ghost_wake_sensitivity(void) {return sensitivity;}

esp_err_t ghost_wake_set_sensitivity(int value)
{
    if(value<0 || value>100) return ESP_ERR_INVALID_ARG;
    if(!ready) return ESP_ERR_INVALID_STATE;
    if(value==sensitivity) return ESP_OK;
    nvs_handle_t handle;
    esp_err_t result=nvs_open("ghost_audio",NVS_READWRITE,&handle);
    if(result==ESP_OK) {
        result=nvs_set_u8(handle,"wake_sense",value);
        if(result==ESP_OK) result=nvs_commit(handle);
        nvs_close(handle);
    }
    if(result==ESP_OK) sensitivity=value;
    return result;
}

esp_err_t ghost_wake_set_enabled(bool value)
{
    if(!ready) return ESP_ERR_INVALID_STATE;
    nvs_handle_t handle;
    esp_err_t result=nvs_open("ghost_audio",NVS_READWRITE,&handle);
    if(result==ESP_OK) {
        result=nvs_set_u8(handle,"wake",value);
        if(result==ESP_OK) result=nvs_commit(handle);
        nvs_close(handle);
    }
    if(result==ESP_OK) enabled=value;
    return result;
}

bool ghost_wake_init(void)
{
    nvs_handle_t guard_handle;uint32_t previous=0;
    if(nvs_open("ghost_audio",NVS_READONLY,&guard_handle)==ESP_OK) {
        nvs_get_u32(guard_handle,"wake_guard",&previous);nvs_close(guard_handle);
    }
    if(previous==WAKE_INITIALIZER_VERSION) {
        ESP_LOGW("ghost_wake","Previous initialization failed; using tap-to-talk for this firmware");
        return false;
    }
    // Persist before entering third-party initialization. An unexpected panic
    // leaves this marker, so the next boot skips that initializer safely.
    if(boot_guard(WAKE_INITIALIZER_VERSION)!=ESP_OK) return false;
    // Model is embedded in this application, preserving the original model partition.
    srmodel_list_t *models=srmodel_load(ghost_wake_bin);
    // MultiNet7 requires a separate fst bundle, even when mn7_en includes vocab.
    // Missing fst selects a filesystem fallback with a NULL path. Fail safely
    // before entering the third-party constructor if the bundle is incomplete.
    if(!models || !esp_srmodel_filter(models,"fst",NULL)) {
        ESP_LOGW("ghost_wake","Command dictionary missing; using tap-to-talk");boot_guard(0);return false;
    }
    char *name=models?esp_srmodel_filter(models,ESP_MN_PREFIX,ESP_MN_ENGLISH):NULL;
    if(!name || !(recognizer=esp_mn_handle_from_name(name))) {boot_guard(0);return false;}
    model=recognizer->create(name,5000);
    if(!model) {boot_guard(0);return false;}
    chunk=recognizer->get_samp_chunksize(model);
    if(recognizer->get_samp_rate(model)!=16000 || chunk<=0 || chunk>2048) goto failed;
    frame=calloc(chunk,sizeof(*frame));
    if(!frame) goto failed;
    if(esp_mn_commands_alloc(recognizer,model)!=ESP_OK) goto failed;
    // Same command id for both spellings: "hi ghost" catches a clipped "hey".
    if(esp_mn_commands_add(1,"hey ghost")!=ESP_OK || esp_mn_commands_add(1,"hi ghost")!=ESP_OK ||
        esp_mn_commands_update()!=NULL) {
        esp_mn_commands_free();goto failed;
    }
    nvs_handle_t handle;uint8_t saved;
    if(nvs_open("ghost_audio",NVS_READONLY,&handle)==ESP_OK) {
        if(nvs_get_u8(handle,"wake",&saved)==ESP_OK && saved<=1) enabled=saved;
        if(nvs_get_u8(handle,"wake_sense",&saved)==ESP_OK && saved<=100) sensitivity=saved;
        nvs_close(handle);
    }
    if(boot_guard(0)!=ESP_OK) {esp_mn_commands_free();goto failed;}
    ready=true;
    ESP_LOGW("ghost_wake","Local Hey Ghost phrase recognizer ready (experimental); chunk=%d",chunk);
    return true;
failed:
    free(frame);frame=NULL;recognizer->destroy(model);model=NULL;
    boot_guard(0);
    ESP_LOGW("ghost_wake","Hands-free unavailable; tap-to-talk remains available");
    return false;
}

void ghost_wake_reset(void)
{
    used=0;
    if(model) recognizer->clean(model);
}

bool ghost_wake_feed(const int16_t *samples,size_t count)
{
    if(!ghost_wake_enabled()) return false;
    if(applied_sensitivity!=sensitivity) {
        applied_sensitivity=sensitivity;
        threshold=0.80f-0.007f*applied_sensitivity;
        recognizer->set_det_threshold(model,threshold);
        ghost_wake_reset();
    }
    static int64_t reported_at;
    static unsigned frames;
    static unsigned maximum_rms,maximum_detect_us;
    static uint64_t fed_samples;   // audio actually reaching the recognizer
    fed_samples+=count;
    int64_t energy=0;
    for(size_t i=0;i<count;i++) energy+=(int64_t)samples[i]*samples[i];
    unsigned rms=count?(unsigned)sqrtf((float)energy/count):0;
    if(rms>maximum_rms) maximum_rms=rms;
    while(count) {
        size_t n=(size_t)(chunk-used);if(n>count) n=count;
        memcpy(frame+used,samples,n*sizeof(*frame));used+=n;samples+=n;count-=n;
        if(used!=chunk) continue;
        used=0;
        int64_t began=esp_timer_get_time();
        esp_mn_state_t state=recognizer->detect(model,frame);
        unsigned duration=esp_timer_get_time()-began;
        if(duration>maximum_detect_us) maximum_detect_us=duration;
        frames++;
        if(state==ESP_MN_STATE_DETECTED) {
            esp_mn_results_t *result=recognizer->get_results(model);
            bool match=result && result->num>0 && result->command_id[0]==1 && result->prob[0]>=threshold;
            if(result && result->num>0) ESP_LOGW("ghost_wake","Candidate confidence=%d threshold=%d",
                (int)(result->prob[0]*100),(int)(threshold*100));
            recognizer->clean(model);
            if(match) {ESP_LOGW("ghost_wake","Local wake phrase detected");return true;}
        } else if(state==ESP_MN_STATE_TIMEOUT) recognizer->clean(model);
    }
    if(esp_timer_get_time()-reported_at>5000000) {
        // audio_ms vs window_ms: equal means no microphone audio was dropped.
        ESP_LOGW("ghost_wake","Idle frames=%u chunk=%d audio_ms=%u window_ms=%u max_rms=%u max_detect_us=%u sensitivity=%d internal_free=%u",
            frames,chunk,(unsigned)(fed_samples/16),(unsigned)((esp_timer_get_time()-reported_at)/1000),
            maximum_rms,maximum_detect_us,applied_sensitivity,(unsigned)esp_get_free_internal_heap_size());
        reported_at=esp_timer_get_time();frames=0;maximum_rms=0;maximum_detect_us=0;fed_samples=0;
    }
    return false;
}
