#pragma once
#include <stdbool.h>
#include <stdint.h>

#define BADGE_NO_ACTION UINT32_MAX
typedef enum {
    MOTION_IDLE, MOTION_STAND_UP, MOTION_BLEND_ACTION, MOTION_ACTION,
    MOTION_BLEND_SIT, MOTION_SIT_DOWN, MOTION_STAND_PAUSE,
    MOTION_DIZZY_ENTER, MOTION_DIZZY, MOTION_RECOVER
} badge_motion_stage_t;
typedef struct {
    badge_motion_stage_t stage;
    uint32_t sit, count[3], fps, clip, frame, active, pending, completed, blend_epoch;
    int64_t next_us, stage_us, dizzy_us;
    bool standing_idle;
} badge_motion_t;
extern const char *const badge_motion_names[];
void badge_motion_init(badge_motion_t *m, uint32_t sit, const uint32_t counts[3], uint32_t fps);
void badge_motion_tick(badge_motion_t *m, int64_t now, uint32_t request, bool shake);
bool badge_motion_dizzy(const badge_motion_t *m);
bool badge_motion_blending(const badge_motion_t *m);
float badge_motion_blend(const badge_motion_t *m, int64_t now);
float badge_motion_dizzy_strength(const badge_motion_t *m, int64_t now);
int badge_motion_selftest(void);
