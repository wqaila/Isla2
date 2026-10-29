#include "badge_imu_filter.h"
#include <float.h>
#include <string.h>

static float magnitude_squared(const float *v)
{
    return v[0]*v[0]+v[1]*v[1]+v[2]*v[2];
}
static float absolute(float v) { return v < 0 ? -v : v; }

void badge_imu_filter_init(badge_imu_filter_t *s, float unused_zero)
{
    (void)unused_zero; /* Previous screen calibration is deliberately not applied. */
    memset(s,0,sizeof(*s));
    s->flat=true;
}

void badge_imu_filter_lost(badge_imu_filter_t *s)
{
    s->initialized=s->stable=s->armed=false;
    s->stable_since=s->last_us=0;
    memset(s->peak_sign,0,sizeof(s->peak_sign));
    memset(s->linear,0,sizeof(s->linear));
}

bool badge_imu_accel_valid(const float a[3])
{
    for (int i=0;i<3;++i) {
        if (!(a[i]<=FLT_MAX && a[i]>=-FLT_MAX)) return false;
    }
    float norm2=magnitude_squared(a);
    return norm2>=0.0001f && norm2<=256 &&
        !(absolute(a[0])>7.99f && absolute(a[1])>7.99f && absolute(a[2])>7.99f);
}

bool badge_imu_filter_step(badge_imu_filter_t *s, const float a[3], const float unused_gyro[3],
                           int64_t now, bool inhibited)
{
    (void)unused_gyro;
    if (!badge_imu_accel_valid(a)) { badge_imu_filter_lost(s); return false; }
    float norm2=magnitude_squared(a);
    float dt=s->last_us?(now-s->last_us)*0.000001f:0.01f;
    if (dt<=0 || dt>0.1f) { badge_imu_filter_lost(s); dt=0.01f; }
    s->last_us=now;
    memcpy(s->accel,a,sizeof(s->accel));
    if (!s->initialized) {
        memcpy(s->gravity,a,sizeof(s->gravity));
        s->initialized=true;
        if (s->rearm_at<now+500000) s->rearm_at=now+500000;
    }
    /* Rotation changes gravity's direction but not its magnitude. Require a
     * dynamic magnitude as well as opposite linear peaks to reject turns. */
    bool dynamic=norm2<0.85f*0.85f || norm2>1.15f*1.15f;
    float correction=dynamic?0:dt/(0.12f+dt);
    for (int i=0;i<3;++i) {
        s->gravity[i]+=correction*(a[i]-s->gravity[i]);
        s->linear[i]+=dt/(0.015f+dt)*(a[i]-s->gravity[i]-s->linear[i]);
    }
    s->flat=s->gravity[0]*s->gravity[0]+s->gravity[1]*s->gravity[1]<0.25f*0.25f;
    s->stable=norm2>0.88f*0.88f && norm2<1.12f*1.12f && magnitude_squared(s->linear)<0.12f*0.12f;
    if (!s->stable) s->stable_since=0;
    else if (!s->stable_since) s->stable_since=now;
    bool settled=s->stable_since && now-s->stable_since>=500000;
    if (inhibited || s->inhibited) {
        s->armed=false;
        s->rearm_at=now+2000000;
        memset(s->peak_sign,0,sizeof(s->peak_sign));
    }
    s->inhibited=inhibited;
    if (!inhibited && settled && now>=s->rearm_at) s->armed=true;
    bool shake=false;
    if (s->armed && dynamic) {
        int axis=0;
        for (int i=1;i<3;++i) if (absolute(s->linear[i])>absolute(s->linear[axis])) axis=i;
        if (absolute(s->linear[axis])>0.8f) {
            int sign=s->linear[axis]>0?1:-1;
            int64_t age=now-s->peak_us[axis];
            if (s->peak_sign[axis]==-sign && age>=40000 && age<=600000) shake=true;
            else if (s->peak_sign[axis]!=sign || age>600000) {
                s->peak_sign[axis]=sign; s->peak_us[axis]=now;
            }
        }
    }
    if (shake) {
        ++s->shakes; s->armed=false; s->rearm_at=now+2000000;
        memset(s->peak_sign,0,sizeof(s->peak_sign));
    }
    /* Compatibility diagnostics: there is no presentation pose or gyro input. */
    s->angle=s->dx=s->dy=s->lean=0;
    memset(s->gyro,0,sizeof(s->gyro));
    return shake;
}
