#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool available, flat, stable, calibrated;
    uint8_t sensor_enable;
    float angle, dx, dy, lean;
    float accel[3], gyro[3], gravity[3];
    uint32_t shakes, errors;
    int64_t sample_us;
} badge_imu_state_t;

void badge_imu_start(void);
void badge_imu_get(badge_imu_state_t *out);
void badge_imu_inhibit(bool value);
void badge_imu_status(void);
void badge_imu_calibrate(void);
