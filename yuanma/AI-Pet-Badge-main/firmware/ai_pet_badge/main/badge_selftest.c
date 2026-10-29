/* Deterministic replay. Uses local state only; never injects movement into the live UI. */
#include "badge_motion.h"
#include <stdio.h>
#ifndef BADGE_HOST_MOTION_SELFTEST
#include "badge_imu_filter.h"
#endif

#define CHECK(condition, name) do { \
    if (!(condition)) { ++failures; printf("BADGE_TEST FAIL %s\n",name); } \
    else printf("BADGE_TEST PASS %s\n",name); \
} while (0)

#ifndef BADGE_HOST_MOTION_SELFTEST
static void still(badge_imu_filter_t *s, int64_t *now, int count, bool inhibited)
{
    const float a[3]={0,-1,0},w[3]={0,0,0};
    while (count--) { *now+=10000; badge_imu_filter_step(s,a,w,*now,inhibited); }
}
static unsigned shake(badge_imu_filter_t *s, int64_t *now, bool inhibited)
{
    unsigned before=s->shakes;
    float a[3]={1.8f,-1,0},w[3]={0,0,0};
    for (int i=0;i<18;++i) {
        a[0]=i<8?1.8f:-1.8f;
        *now+=10000; badge_imu_filter_step(s,a,w,*now,inhibited);
    }
    return s->shakes-before;
}

int badge_imu_filter_selftest(void)
{
    int failures=0;
    badge_imu_filter_t f;
    int64_t now=1000000;
    badge_imu_filter_init(&f,180);
    still(&f,&now,100,false);
    CHECK(f.stable && !f.flat && f.angle==0 && f.armed,"upright arms shake without old calibration");
    const float directions[8][3]={{0,-1,0},{-0.7071068f,-0.7071068f,0},{-1,0,0},
        {-0.7071068f,0.7071068f,0},{0,1,0},{0.7071068f,0.7071068f,0},{1,0,0},{0,0,1}};
    const float gyro[3]={100,-80,30};
    bool fixed=true;
    for (int turn=0;turn<3;++turn) for (int pose=0;pose<8;++pose) for (int i=0;i<30;++i) {
        now+=10000; badge_imu_filter_step(&f,directions[pose],gyro,now,false);
        if (f.angle!=0 || f.dx!=0 || f.dy!=0 || f.lean!=0) fixed=false;
    }
    CHECK(fixed,"all orientations keep screen and character fixed");
    CHECK(f.shakes==0,"orientation changes do not trigger stars");
    CHECK(f.gyro[0]==0 && f.gyro[1]==0 && f.gyro[2]==0,"gyro input is ignored");
    badge_imu_filter_init(&f,0); still(&f,&now,100,false);
    CHECK(shake(&f,&now,false)==1,"opposite peaks trigger once");
    CHECK(shake(&f,&now,true)==0,"dizzy suppresses repeat shake");
    still(&f,&now,100,false);
    CHECK(shake(&f,&now,false)==0,"recovery cooldown blocks shake");
    still(&f,&now,250,false);
    CHECK(shake(&f,&now,false)==1,"quiet cooldown rearms");
    badge_imu_filter_init(&f,180);
    still(&f,&now,100,false);
    float a[3]={1.8f,-1,0};
    for (int i=0;i<8;++i) { now+=10000; badge_imu_filter_step(&f,a,NULL,now,false); }
    still(&f,&now,100,false);
    CHECK(f.shakes==0,"single pickup impulse does not shake");
    now+=2000000;
    a[0]=0; a[1]=-1;
    badge_imu_filter_step(&f,a,NULL,now,false);
    CHECK(f.angle==0 && !f.armed && f.shakes==0,"long sample gap resets shake arming safely");
    badge_imu_filter_init(&f,0); still(&f,&now,100,false);
    for (int i=0;i<8;++i) { a[0]=1.8f; now+=10000; badge_imu_filter_step(&f,a,NULL,now,false); }
    still(&f,&now,75,false);
    for (int i=0;i<8;++i) { a[0]=-1.8f; now+=10000; badge_imu_filter_step(&f,a,NULL,now,false); }
    CHECK(f.shakes==0,"peaks outside 600ms do not pair");
    badge_imu_filter_init(&f,0); still(&f,&now,100,false);
    for (int i=0;i<18;++i) {
        a[0]=0; a[1]=-1+(i<8?1.8f:-1.8f); a[2]=0;
        now+=10000; badge_imu_filter_step(&f,a,NULL,now,false);
    }
    CHECK(f.shakes==1,"vertical opposite peaks trigger");
    badge_imu_filter_init(&f,0);
    for (int i=0;i<100;++i) { a[0]=a[1]=0; a[2]=1; now+=10000; badge_imu_filter_step(&f,a,NULL,now,false); }
    for (int i=0;i<18;++i) { a[0]=i<8?1.8f:-1.8f; now+=10000; badge_imu_filter_step(&f,a,NULL,now,false); }
    CHECK(f.shakes==1 && f.flat,"flat shake still triggers without rotating screen");
    badge_imu_filter_lost(&f);
    CHECK(!f.armed && !f.initialized && f.shakes==1 && f.angle==0,"disconnect clears peaks but preserves shake count");
    const float zero[3]={0,0,0};
    CHECK(!badge_imu_filter_step(&f,zero,NULL,now+10000,false),"invalid acceleration is rejected");
    const float clipped[3]={7.9998f,7.9998f,7.9998f};
    CHECK(!badge_imu_accel_valid(clipped),"stuck all-axis saturation is rejected");
    return failures;
}

#endif

int badge_motion_selftest(void)
{
    int failures=0;
    const uint32_t counts[3]={106,106,36};
    badge_motion_t m;
    badge_motion_init(&m,2,counts,24);
    badge_motion_tick(&m,1000000,BADGE_NO_ACTION,false);
    CHECK(m.stage==MOTION_IDLE && m.frame==35,"seated idle has no autoplay");
    badge_motion_tick(&m,1010000,0,false);
    CHECK(m.stage==MOTION_STAND_UP,"tap starts standing transition");
    badge_motion_tick(&m,1020000,1,false);
    CHECK(m.pending==1,"latest touch queued");
    badge_motion_tick(&m,1030000,0,true);
    CHECK(m.stage==MOTION_DIZZY_ENTER && m.pending==BADGE_NO_ACTION && m.clip==2 && m.frame==35,
          "shake cancels active and queued action");
    CHECK(badge_motion_blend(&m,1030000)==0 && badge_motion_blend(&m,1280000)==1,
          "dizzy transition has smooth endpoints");
    uint32_t epoch=m.blend_epoch;
    badge_motion_tick(&m,1400000,0,true);
    CHECK(m.stage==MOTION_DIZZY && m.pending==BADGE_NO_ACTION && m.blend_epoch==epoch,
          "dizzy ignores taps and repeated shakes");
    badge_motion_tick(&m,4030000,1,false);
    CHECK(m.stage==MOTION_RECOVER,"dizzy lasts three seconds");
    badge_motion_tick(&m,4430000,1,true);
    CHECK(m.stage==MOTION_IDLE && m.pending==BADGE_NO_ACTION && m.completed==0,
          "recovery returns seated without cancelled action");
    badge_motion_tick(&m,4440000,1,false);
    for (int i=1;i<=220;++i) badge_motion_tick(&m,4440000+i*50000LL,BADGE_NO_ACTION,false);
    CHECK(m.stage==MOTION_IDLE && m.completed==1 && m.frame==35,"normal jump completes seated");
    const uint32_t standing_counts[3]={26,20,19};
    badge_motion_init(&m,2,standing_counts,24);
    m.standing_idle=true;
    badge_motion_tick(&m,1000000,BADGE_NO_ACTION,false);
    CHECK(m.stage==MOTION_IDLE && m.frame==18,"standing idle has no autoplay");
    for (unsigned action=0;action<3;++action) {
        int64_t start=2000000+action*3000000LL;
        badge_motion_tick(&m,start,action,false);
        CHECK(m.stage==MOTION_BLEND_ACTION && m.clip==action,"standing action blends directly from neutral");
        bool frames_valid=true;
        for (int i=1;i<=50;++i) {
            badge_motion_tick(&m,start+i*50000LL,BADGE_NO_ACTION,false);
            if (m.frame>=m.count[m.clip]) frames_valid=false;
            if (m.stage==MOTION_IDLE) break;
        }
        CHECK(frames_valid,"standing frames stay inside clip");
        CHECK(m.stage==MOTION_IDLE && m.clip==2 && m.frame==18 && m.completed==action+1,
              "standing action returns to static neutral");
    }
    badge_motion_tick(&m,12000000,0,false);
    badge_motion_tick(&m,12010000,1,false);
    badge_motion_tick(&m,12020000,BADGE_NO_ACTION,true);
    CHECK(m.stage==MOTION_DIZZY_ENTER && m.pending==BADGE_NO_ACTION && m.frame==18,
          "standing shake cancels active and queued action");
    badge_motion_tick(&m,12300000,2,false);
    badge_motion_tick(&m,15020000,2,false);
    badge_motion_tick(&m,15420000,2,false);
    CHECK(m.stage==MOTION_IDLE && m.pending==BADGE_NO_ACTION && m.completed==3 && m.frame==18,
          "standing dizzy recovers without resuming cancelled action");
    return failures;
}

#ifdef BADGE_HOST_SELFTEST
int main(void) { return badge_imu_filter_selftest()+badge_motion_selftest(); }
#elif defined(BADGE_HOST_MOTION_SELFTEST)
int main(void) { return badge_motion_selftest(); }
#endif
