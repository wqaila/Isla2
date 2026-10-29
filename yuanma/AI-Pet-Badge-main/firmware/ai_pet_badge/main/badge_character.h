#pragma once
#include <stdbool.h>

/* Profiles share the renderer and IMU; each keeps its own embedded asset pack. */
#ifdef BADGE_CHARACTER_QINGLAN
#define BADGE_CHARACTER_ID "qinglan"
#define BADGE_CHARACTER_NAME "青岚"
#define BADGE_CHARACTER_GLYPHS 0x9752, 0x5C9A
#define BADGE_CHARACTER_IDLE_CLIP "wave"
#define BADGE_CHARACTER_STANDING true
#define BADGE_CHARACTER_CLIPS "walk", "jump", "wave"
#else
#define BADGE_CHARACTER_ID "elysia"
#define BADGE_CHARACTER_NAME "爱莉希雅"
#define BADGE_CHARACTER_GLYPHS 0x7231, 0x8389, 0x5E0C, 0x96C5
#define BADGE_CHARACTER_IDLE_CLIP "sit"
#define BADGE_CHARACTER_STANDING false
#define BADGE_CHARACTER_CLIPS "walk", "jump", "sit"
#endif
