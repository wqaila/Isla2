#include "badge_imu.h"
#include "badge_imu_filter.h"
#include "bsp/esp-bsp.h"
#include "qmi8658.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>
#include <string.h>

static const char *TAG="badge_imu";
static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static badge_imu_state_t state={.flat=true};
static bool inhibit;

void badge_imu_get(badge_imu_state_t *out)
{
    portENTER_CRITICAL(&mux); *out=state; portEXIT_CRITICAL(&mux);
}
void badge_imu_inhibit(bool value)
{
    portENTER_CRITICAL(&mux); inhibit=value; portEXIT_CRITICAL(&mux);
}
void badge_imu_calibrate(void)
{
    printf("BADGE_IMU_CALIBRATION disabled: fixed screen; previous NVS calibration retained\n");
}
void badge_imu_status(void)
{
    badge_imu_state_t s; badge_imu_get(&s);
    printf("BADGE_IMU {\"mode\":\"shake_only\",\"gyro_enabled\":false,\"orientation_enabled\":false,"
           "\"sensor_enable\":%u,\"available\":%s,\"flat\":%s,\"stable\":%s,\"calibrated\":false,"
           "\"angle\":0,\"dx\":0,\"dy\":0,\"lean\":0,"
           "\"accel_g\":[%.4f,%.4f,%.4f],\"gyro_rads\":[0,0,0],"
           "\"gravity\":[%.4f,%.4f,%.4f],\"shakes\":%u,\"errors\":%u}\n",
           s.sensor_enable,s.available?"true":"false",s.flat?"true":"false",s.stable?"true":"false",
           s.accel[0],s.accel[1],s.accel[2],s.gravity[0],s.gravity[1],s.gravity[2],
           (unsigned)s.shakes,(unsigned)s.errors);
}

static esp_err_t connect(qmi8658_dev_t *dev, uint8_t *enabled)
{
    if (dev->dev_handle) { i2c_master_bus_rm_device(dev->dev_handle); dev->dev_handle=NULL; }
    esp_err_t err=qmi8658_init(dev,bsp_i2c_get_handle(),QMI8658_ADDRESS_HIGH);
    /* MCU resets do not reset the sensor. Start from its documented POR state,
     * then explicitly select little-endian data for the BSP's byte decoder. */
    if (err==ESP_OK) err=qmi8658_write_register(dev,0x60,0xB0);
    if (err==ESP_OK) vTaskDelay(pdMS_TO_TICKS(25));
    if (err==ESP_OK) err=qmi8658_enable_sensors(dev,QMI8658_DISABLE_ALL);
    if (err==ESP_OK) err=qmi8658_write_register(dev,QMI8658_CTRL1,0x40);
    if (err==ESP_OK) err=qmi8658_set_accel_range(dev,QMI8658_ACCEL_RANGE_8G);
    if (err==ESP_OK) err=qmi8658_set_accel_odr(dev,QMI8658_ACCEL_ODR_125HZ);
    if (err==ESP_OK) err=qmi8658_enable_sensors(dev,QMI8658_ENABLE_ACCEL);
    if (err==ESP_OK) err=qmi8658_read_register(dev,QMI8658_CTRL7,enabled,1);
    if (err==ESP_OK && (*enabled & 0x0F)!=QMI8658_ENABLE_ACCEL) err=ESP_ERR_INVALID_RESPONSE;
    qmi8658_set_accel_unit_mps2(dev,true);
    if (err==ESP_OK) {
        uint8_t config[2];
        err=qmi8658_read_register(dev,QMI8658_CTRL1,config,2);
        if (err==ESP_OK && (config[0]!=0x40 || config[1]!=0x26)) err=ESP_ERR_INVALID_RESPONSE;
        if (err==ESP_OK) ESP_LOGI(TAG,"QMI config: CTRL1=%02x CTRL2=%02x CTRL7=%02x",config[0],config[1],*enabled);
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    return err;
}

static void imu_task(void *arg)
{
    (void)arg;
    badge_imu_filter_t filter; badge_imu_filter_init(&filter,0);
    qmi8658_dev_t dev={0};
    bool ready=false;
    int failures=0;
    int64_t retry_at=0;
    uint8_t enabled=0;
    TickType_t wake=xTaskGetTickCount();
    while (true) {
        int64_t now=esp_timer_get_time();
        bool blocked;
        portENTER_CRITICAL(&mux); blocked=inhibit; portEXIT_CRITICAL(&mux);
        if (!ready && now>=retry_at) {
            ready=connect(&dev,&enabled)==ESP_OK;
            retry_at=now+5000000;
            if (ready) { failures=0; ESP_LOGI(TAG,"Shake only: accelerometer 8g/125Hz, gyro OFF, screen fixed"); }
            else { portENTER_CRITICAL(&mux); ++state.errors; portEXIT_CRITICAL(&mux); }
        }
        if (ready) {
            float accel[3];
            esp_err_t err=qmi8658_read_accel(&dev,&accel[0],&accel[1],&accel[2]);
            if (err==ESP_OK) {
                for (int i=0;i<3;++i) accel[i]/=ONE_G;
                if (!badge_imu_accel_valid(accel)) err=ESP_ERR_INVALID_RESPONSE;
            }
            if (err!=ESP_OK) {
                portENTER_CRITICAL(&mux); ++state.errors; portEXIT_CRITICAL(&mux);
                if (++failures>=5) {
                    ready=false; retry_at=now+5000000;
                    badge_imu_filter_lost(&filter);
                    ESP_LOGW(TAG,"Shake sensor unavailable; retrying in 5s");
                }
            } else {
                failures=0;
                badge_imu_filter_step(&filter,accel,NULL,now,blocked);
            }
        }
        portENTER_CRITICAL(&mux);
        state.available=ready; state.sensor_enable=ready?enabled:0;
        state.flat=filter.flat; state.stable=ready && filter.stable;
        state.calibrated=false; state.angle=state.dx=state.dy=state.lean=0;
        memcpy(state.accel,filter.accel,sizeof(state.accel));
        memset(state.gyro,0,sizeof(state.gyro));
        memcpy(state.gravity,filter.gravity,sizeof(state.gravity));
        state.shakes=filter.shakes; state.sample_us=filter.last_us;
        portEXIT_CRITICAL(&mux);
        xTaskDelayUntil(&wake,pdMS_TO_TICKS(10));
    }
}

void badge_imu_start(void)
{
    configASSERT(xTaskCreatePinnedToCore(imu_task,"badge_imu",4096,NULL,5,NULL,0)==pdPASS);
}
