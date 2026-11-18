#include "ghost_wake.h"
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include "esp_log.h"
#include "esp_ota_ops.h"
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
// Bump for a materially changed initializer so a repaired version can retry.
#define WAKE_INITIALIZER_VERSION 0x00020101u

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
    const esp_partition_t *container=esp_ota_get_running_partition();
    if(!models || !container) {boot_guard(0);return false;}
    // ESP-SR distinguishes flash models from filesystem models through this
    // field. srmodel_load alone leaves it NULL (unlike srmodel_mmap_init),
    // causing MultiNet7 to format a filesystem path from a NULL base path.
    // The already-mapped file pointers remain relative to the embedded bundle;
    // this identifies their real container, without remapping or writing it.
    models->partition=(esp_partition_t *)container;
    char *name=models?esp_srmodel_filter(models,ESP_MN_PREFIX,ESP_MN_ENGLISH):NULL;
    if(!name || !(recognizer=esp_mn_handle_from_name(name))) {boot_guard(0);return false;}
    model=recognizer->create(name,5000);
    if(!model) {boot_guard(0);return false;}
    chunk=recognizer->get_samp_chunksize(model);
    if(recognizer->get_samp_rate(model)!=16000 || chunk<=0 || chunk>2048) goto failed;
    frame=calloc(chunk,sizeof(*frame));
    if(!frame) goto failed;
    if(esp_mn_commands_alloc(recognizer,model)!=ESP_OK) goto failed;
    if(esp_mn_commands_add(1,"hey ghost")!=ESP_OK || esp_mn_commands_update()!=NULL) {
        esp_mn_commands_free();goto failed;
    }
    recognizer->set_det_threshold(model,0.90f);
    nvs_handle_t handle;uint8_t saved;
    if(nvs_open("ghost_audio",NVS_READONLY,&handle)==ESP_OK) {
        if(nvs_get_u8(handle,"wake",&saved)==ESP_OK && saved<=1) enabled=saved;
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
    while(count) {
        size_t n=(size_t)(chunk-used);if(n>count) n=count;
        memcpy(frame+used,samples,n*sizeof(*frame));used+=n;samples+=n;count-=n;
        if(used!=chunk) continue;
        used=0;
        esp_mn_state_t state=recognizer->detect(model,frame);
        if(state==ESP_MN_STATE_DETECTED) {
            esp_mn_results_t *result=recognizer->get_results(model);
            bool match=result && result->num>0 && result->command_id[0]==1 && result->prob[0]>=0.90f;
            recognizer->clean(model);
            if(match) {ESP_LOGW("ghost_wake","Local wake phrase detected");return true;}
        } else if(state==ESP_MN_STATE_TIMEOUT) recognizer->clean(model);
    }
    return false;
}
