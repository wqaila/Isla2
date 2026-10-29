#include "badge_storage.h"
#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include <dirent.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static bool mounted;
static esp_err_t mount_error=ESP_ERR_INVALID_STATE;

void badge_storage_start(void)
{
    /* A user's existing card must never be formatted on a mount failure. */
    const esp_vfs_fat_sdmmc_mount_config_t mount={
        .format_if_mount_failed=false,.max_files=5,.allocation_unit_size=16*1024
    };
    bsp_sdcard_cfg_t config={.mount=&mount};
    mount_error=bsp_sdcard_sdmmc_mount(&config);
    mounted=mount_error==ESP_OK;
    if (mounted) ESP_LOGI("badge_sd","SD mounted without formatting");
    else ESP_LOGW("badge_sd","SD unavailable (%s); character continues from Flash",esp_err_to_name(mount_error));
}

bool badge_storage_mounted(void) { return mounted; }

void badge_storage_status(void)
{
    uint64_t total=0,free=0;
    bool readable=false;
    unsigned entries=0;
    esp_err_t info=mounted?esp_vfs_fat_info(BSP_SD_MOUNT_POINT,&total,&free):mount_error;
    if (mounted) {
        DIR *directory=opendir(BSP_SD_MOUNT_POINT);
        if (directory) {
            readable=true;
            struct dirent *entry;
            while ((entry=readdir(directory))) {
                if (strcmp(entry->d_name,".") && strcmp(entry->d_name,"..")) ++entries;
            }
            closedir(directory);
        }
    }
    printf("BADGE_SD {\"mounted\":%s,\"readable\":%s,\"format_on_failure\":false,"
           "\"total_bytes\":%" PRIu64 ",\"free_bytes\":%" PRIu64 ",\"root_entries\":%u,\"error\":\"%s\"}\n",
           mounted?"true":"false",readable?"true":"false",total,free,entries,esp_err_to_name(info));
}
