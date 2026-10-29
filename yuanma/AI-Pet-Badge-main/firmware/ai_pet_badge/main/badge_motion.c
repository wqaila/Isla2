#include "badge_motion.h"
#include <string.h>

const char *const badge_motion_names[] = {
    "idle", "stand_up", "blend_to_action", "action", "blend_to_sit", "sit_down", "stand_pause",
    "dizzy_enter", "dizzy", "recover"
};
static float smooth(float t) { if (t < 0) t = 0; if (t > 1) t = 1; return t*t*(3-2*t); }
bool badge_motion_dizzy(const badge_motion_t *m) { return m->stage >= MOTION_DIZZY_ENTER; }
bool badge_motion_blending(const badge_motion_t *m)
{
    return m->stage == MOTION_BLEND_ACTION || m->stage == MOTION_BLEND_SIT || m->stage == MOTION_DIZZY_ENTER;
}
float badge_motion_blend(const badge_motion_t *m, int64_t now)
{
    return smooth((now-m->stage_us)/250000.0f);
}
float badge_motion_dizzy_strength(const badge_motion_t *m, int64_t now)
{
    if (!badge_motion_dizzy(m)) return 0;
    if (m->stage == MOTION_RECOVER) return 1-smooth((now-m->stage_us)/400000.0f);
    return smooth((now-m->dizzy_us)/250000.0f);
}
void badge_motion_init(badge_motion_t *m, uint32_t sit, const uint32_t counts[3], uint32_t fps)
{
    memset(m,0,sizeof(*m));
    memcpy(m->count,counts,sizeof(m->count));
    m->sit = m->clip = m->active = sit; m->frame = counts[sit]-1;
    m->fps = fps; m->pending = BADGE_NO_ACTION;
}
static void enter(badge_motion_t *m, badge_motion_stage_t stage, int64_t now)
{
    m->stage = stage; m->stage_us = now; m->next_us = now+1000000/m->fps;
    if (badge_motion_blending(m)) ++m->blend_epoch;
}
void badge_motion_tick(badge_motion_t *m, int64_t now, uint32_t request, bool shake)
{
    if (shake && !badge_motion_dizzy(m)) {
        m->pending = BADGE_NO_ACTION; m->clip = m->sit; m->frame = m->count[m->sit]-1;
        m->dizzy_us = now; enter(m,MOTION_DIZZY_ENTER,now);
        return;
    }
    if (badge_motion_dizzy(m)) {
        if (m->stage == MOTION_DIZZY_ENTER && now-m->stage_us >= 250000) enter(m,MOTION_DIZZY,now);
        if (m->stage == MOTION_DIZZY && now-m->dizzy_us >= 3000000) enter(m,MOTION_RECOVER,now);
        if (m->stage == MOTION_RECOVER && now-m->stage_us >= 400000) enter(m,MOTION_IDLE,now);
        return;
    }
    if (request < 3) m->pending = request;
    if (m->stage == MOTION_IDLE) {
        if (m->pending != BADGE_NO_ACTION) {
            m->active = m->pending; m->pending = BADGE_NO_ACTION;
            if (m->standing_idle) {
                m->clip = m->active; m->frame = 0;
                enter(m,MOTION_BLEND_ACTION,now);
            } else {
                m->clip = m->sit; m->frame = m->count[m->sit]-2;
                enter(m,MOTION_STAND_UP,now);
            }
        }
        return;
    }
    if (badge_motion_blending(m)) {
        if (now-m->stage_us < 250000) return;
        if (m->stage == MOTION_BLEND_ACTION) { m->frame = 1; enter(m,MOTION_ACTION,now); }
        else if (m->standing_idle) {
            m->frame = m->count[m->sit]-1; ++m->completed; enter(m,MOTION_IDLE,now);
        }
        else { m->frame = 1; enter(m,MOTION_SIT_DOWN,now); }
        return;
    }
    if (m->stage == MOTION_STAND_PAUSE) {
        if (now-m->stage_us >= 250000) { m->frame = 1; enter(m,MOTION_SIT_DOWN,now); }
        return;
    }
    if (now < m->next_us) return;
    m->next_us += 1000000/m->fps;
    if (m->next_us < now-1000000/m->fps) m->next_us = now+1000000/m->fps;
    switch (m->stage) {
    case MOTION_STAND_UP:
        if (m->frame) --m->frame;
        else if (m->active == m->sit) enter(m,MOTION_STAND_PAUSE,now);
        else { m->clip = m->active; m->frame = 0; enter(m,MOTION_BLEND_ACTION,now); }
        break;
    case MOTION_ACTION:
        if (++m->frame >= m->count[m->clip]) {
            m->clip = m->sit; m->frame = m->standing_idle?m->count[m->sit]-1:0;
            enter(m,MOTION_BLEND_SIT,now);
        }
        break;
    case MOTION_SIT_DOWN:
        if (++m->frame >= m->count[m->sit]) {
            m->frame = m->count[m->sit]-1; ++m->completed; enter(m,MOTION_IDLE,now);
        }
        break;
    default: break;
    }
}
