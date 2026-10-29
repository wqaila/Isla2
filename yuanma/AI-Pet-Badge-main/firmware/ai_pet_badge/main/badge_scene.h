#pragma once
#include <stdbool.h>
#include <stdint.h>

#define BADGE_SPRITE_W 160
#define BADGE_SPRITE_H 256
#define BADGE_SPRITE_PIXELS (BADGE_SPRITE_W * BADGE_SPRITE_H)
#define BADGE_SPRITE_BYTES (BADGE_SPRITE_PIXELS * 3)
#define BADGE_FRAME_BYTES (360 * 360 * 2)

void badge_scene_init(uint8_t *background);
int badge_scene_selftest(void);
void badge_scene_timings(void);
/* Copies a premultiplied-alpha interpolation into dst (RGB565 + A8 planes). */
void badge_scene_blend(uint8_t *dst, const uint8_t *from, const uint8_t *to, float amount);
bool badge_scene_present(const uint8_t *sprite, bool pose_changed, float dx, float dy,
                         float lean, float orientation, float dizzy, float phase);
