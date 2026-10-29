#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Acceleration-only shake detection; presentation and legacy gyro outputs are zero. */
typedef struct {
    float gravity[3], linear[3];
    float angle, dx, dy, lean;
    float accel[3], gyro[3];
    int64_t last_us, stable_since, rearm_at;
    int64_t peak_us[3];
    int peak_sign[3];
    uint32_t shakes;
    bool initialized, flat, stable, armed, inhibited;
} badge_imu_filter_t;

void badge_imu_filter_init(badge_imu_filter_t *s, float zero);
bool badge_imu_accel_valid(const float a[3]);
bool badge_imu_filter_step(badge_imu_filter_t *s, const float a[3], const float w[3],
                           int64_t now, bool inhibited);
void badge_imu_filter_lost(badge_imu_filter_t *s);
int badge_imu_filter_selftest(void);
