#include "badge_scene.h"
#include "badge_dashboard.h"
#include "bsp/esp-bsp.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "src/draw/snapshot/lv_snapshot.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include <assert.h>
#include <math.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static lv_obj_t *hud, *scene;
static lv_draw_buf_t hud_draw;
static lv_image_dsc_t background_image, scene_image[2];
static uint16_t *hud_pixels, *oriented_hud, *canvas[2];
static int circle_left[360], circle_right[360];
static float global_c = 1, global_s;
/* Internal RAM: one bit per bilinear cell, so flat backdrop pixels need only
 * one external-memory sample. Text coverage is always conservatively marked. */
static uint8_t hud_detail[(360*360+7)/8];
static uint32_t hud_detail_cells;
static bool oriented_valid;
static unsigned slot;
static uint32_t hud_revision;
static int last_x = 1000, last_y, last_lean, last_angle = 10000;
static bool last_dizzy;
static int bx0, by0, bx1, by1;
static uint32_t hud_us, rotate_us, compose_us, present_us;

static lv_image_dsc_t image(void *data)
{
    return (lv_image_dsc_t){.header = {.magic = LV_IMAGE_HEADER_MAGIC,
        .cf = LV_COLOR_FORMAT_RGB565, .w = 360, .h = 360, .stride = 720},
        .data_size = BADGE_FRAME_BYTES, .data = data};
}

void badge_scene_init(uint8_t *background)
{
    hud_pixels = heap_caps_malloc(BADGE_FRAME_BYTES, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    assert(hud_pixels);
    oriented_hud = heap_caps_malloc(BADGE_FRAME_BYTES,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    assert(oriented_hud);
    memcpy(oriented_hud,background,BADGE_FRAME_BYTES);
    for (int y = 0; y < 360; ++y) {
        float half = sqrtf(180.0f*180.0f-(y-179.5f)*(y-179.5f));
        circle_left[y] = fmaxf(0,ceilf(179.5f-half));
        circle_right[y] = fminf(360,floorf(179.5f+half)+1);
    }
    background_image = image(background);
    hud = lv_obj_create(NULL);
    lv_obj_remove_style_all(hud);
    lv_obj_set_size(hud,360,360);
    lv_obj_remove_flag(hud,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *back = lv_image_create(hud);
    lv_image_set_src(back,&background_image);
    lv_obj_set_pos(back,0,0);
    badge_dashboard_create(hud);
    assert(lv_draw_buf_init(&hud_draw,360,360,LV_COLOR_FORMAT_RGB565,720,
                            hud_pixels,BADGE_FRAME_BYTES) == LV_RESULT_OK);
    for (unsigned i = 0; i < 2; ++i) {
        canvas[i] = heap_caps_malloc(BADGE_FRAME_BYTES,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
        assert(canvas[i]);
        memcpy(canvas[i],background,BADGE_FRAME_BYTES);
        scene_image[i] = image(canvas[i]);
    }
    scene = lv_image_create(lv_screen_active());
    lv_obj_set_pos(scene,0,0);
    lv_image_set_src(scene,&scene_image[0]);
    lv_image_set_pivot(scene,180,180);
    /* Rotation is composed in RGB565 below. A native LVGL arbitrary-angle
     * image transform forced a costly color/alpha path (~8 FPS on this board). */
    lv_obj_remove_flag(scene,LV_OBJ_FLAG_CLICKABLE);
}

void badge_scene_blend(uint8_t *dst, const uint8_t *from, const uint8_t *to, float amount)
{
    unsigned t = (unsigned)lroundf(fminf(1,fmaxf(0,amount))*256);
    uint16_t *out = (uint16_t *)dst;
    const uint16_t *a = (const uint16_t *)from, *b = (const uint16_t *)to;
    uint8_t *oa = dst+2*BADGE_SPRITE_PIXELS;
    const uint8_t *aa = from+2*BADGE_SPRITE_PIXELS, *ba = to+2*BADGE_SPRITE_PIXELS;
    for (unsigned i = 0; i < BADGE_SPRITE_PIXELS; ++i) {
        unsigned aw = aa[i]*(256-t), bw = ba[i]*t, total = aw+bw;
        oa[i] = (total+128)>>8;
        if (!oa[i]) { out[i] = 0; continue; }
        unsigned r = ((a[i]>>11)*aw+(b[i]>>11)*bw+total/2)/total;
        unsigned g = (((a[i]>>5)&63)*aw+((b[i]>>5)&63)*bw+total/2)/total;
        unsigned bl = ((a[i]&31)*aw+(b[i]&31)*bw+total/2)/total;
        out[i] = (r<<11)|(g<<5)|bl;
    }
}

static uint16_t over(uint16_t bg, uint16_t fg, unsigned a)
{
    if (a == 255) return fg;
    unsigned inv = 255-a;
    unsigned r = ((fg>>11)*a+(bg>>11)*inv+127)/255;
    unsigned g = (((fg>>5)&63)*a+((bg>>5)&63)*inv+127)/255;
    unsigned b = ((fg&31)*a+(bg&31)*inv+127)/255;
    return (r<<11)|(g<<5)|b;
}

static void bounds(const uint8_t *sprite)
{
    const uint8_t *alpha = sprite+2*BADGE_SPRITE_PIXELS;
    bx0 = BADGE_SPRITE_W; by0 = BADGE_SPRITE_H; bx1 = by1 = 0;
    for (int y = 0; y < BADGE_SPRITE_H; ++y) for (int x = 0; x < BADGE_SPRITE_W; ++x) {
        if (!alpha[y*BADGE_SPRITE_W+x]) continue;
        if (x < bx0) bx0 = x;
        if (x+1 > bx1) bx1 = x+1;
        if (y < by0) by0 = y;
        if (y+1 > by1) by1 = y+1;
    }
}

/* Blend three separated RGB565 channels in parallel, at 5-bit precision. */
static inline uint16_t mix565(uint16_t a, uint16_t b, unsigned weight)
{
    const uint32_t mask = 0x07E0F81F;
    uint32_t pa = ((uint32_t)a | ((uint32_t)a<<16)) & mask;
    uint32_t pb = ((uint32_t)b | ((uint32_t)b<<16)) & mask;
    uint32_t result = ((pa*(32-weight)+pb*weight)>>5)&mask;
    return (uint16_t)(result|(result>>16));
}

static inline void mark_detail(unsigned i)
{
    /* Expand by a cell so a 2x2 destination block touching text/edges always
     * takes the full-resolution path, including the bilinear support. */
    unsigned x=i%360, y=i/360;
    for (int dy=-1;dy<=1;++dy) {
        if ((int)y+dy<0 || (int)y+dy>=360) continue;
        unsigned start=i+dy*360-(x>0), count=1+(x>0)+(x<359);
        unsigned bits=((1u<<count)-1)<<(start&7), byte=start>>3;
        hud_detail[byte]|=bits;
        if (bits>>8) hud_detail[byte+1]|=bits>>8;
    }
}

static void classify_hud(void)
{
    const uint16_t *background = (const uint16_t *)background_image.data;
    memset(hud_detail,0,sizeof(hud_detail));
    hud_detail_cells=0;
    for (int y=0;y<360;++y) for (int x=0;x<360;++x) {
        unsigned i=y*360+x;
        if (hud_pixels[i] != background[i]) {
            mark_detail(i);
            if (x) mark_detail(i-1);
            if (y) mark_detail(i-360);
            if (x && y) mark_detail(i-361);
        }
        if (x<359 && y<359) {
            uint16_t edges=(hud_pixels[i]^hud_pixels[i+1])|(hud_pixels[i]^hud_pixels[i+360])|
                           (hud_pixels[i]^hud_pixels[i+361]);
            if (edges&0xe79c) mark_detail(i);
        }
    }
    for (unsigned i=0;i<sizeof(hud_detail);++i) hud_detail_cells+=__builtin_popcount(hud_detail[i]);
}

static inline uint16_t hud_sample(int32_t u, int32_t v)
{
    int sx=u>>16,sy=v>>16;
    unsigned fx=(u>>11)&31,fy=(v>>11)&31;
    if (sx<0) { sx=0;fx=0; }
    if (sy<0) { sy=0;fy=0; }
    if (sx>=359) { sx=358;fx=32; }
    if (sy>=359) { sy=358;fy=32; }
    const uint16_t *p=hud_pixels+sy*360+sx;
    return mix565(mix565(p[0],p[1],fx),mix565(p[360],p[361],fx),fy);
}

static void rotate_hud(int angle, uint16_t *destination)
{
    if (!angle) { memcpy(destination,hud_pixels,BADGE_FRAME_BYTES); return; }
    int32_t cs = lroundf(global_c*65536), sn = lroundf(global_s*65536);
    int32_t origin_u = lroundf((179.5f-global_c*179.5f-global_s*179.5f)*65536);
    int32_t origin_v = lroundf((179.5f+global_s*179.5f-global_c*179.5f)*65536);
    for (int y=0;y<360;y+=2) {
        int left=circle_left[y]<circle_left[y+1]?circle_left[y]:circle_left[y+1];
        int right=circle_right[y]>circle_right[y+1]?circle_right[y]:circle_right[y+1];
        left&=~1;right=(right+1)&~1;
        int32_t u = origin_u+cs*left+sn*y;
        int32_t v = origin_v-sn*left+cs*y;
        for (int x=left;x<right;x+=2,u+=2*cs,v-=2*sn) {
            int32_t cu=u+(cs+sn)/2,cv=v+(cs-sn)/2;
            int sx=cu>>16,sy=cv>>16;
            if (sx<0) sx=0;
            if (sy<0) sy=0;
            if (sx>359) sx=359;
            if (sy>359) sy=359;
            unsigned cell=sy*360+sx;
            if (hud_detail[cell>>3]&(1u<<(cell&7))) {
                destination[y*360+x]=hud_sample(u,v);
                destination[y*360+x+1]=hud_sample(u+cs,v-sn);
                destination[(y+1)*360+x]=hud_sample(u+sn,v+cs);
                destination[(y+1)*360+x+1]=hud_sample(u+cs+sn,v+cs-sn);
            } else {
                sx=(cu+32768)>>16;sy=(cv+32768)>>16;
                if (sx<0) sx=0;
                if (sy<0) sy=0;
                if (sx>359) sx=359;
                if (sy>359) sy=359;
                uint16_t color=hud_pixels[sy*360+sx];
                /* Native pixel stores avoid tiny memcpy calls and alias casts. */
                destination[y*360+x]=color;
                destination[y*360+x+1]=color;
                destination[(y+1)*360+x]=color;
                destination[(y+1)*360+x+1]=color;
            }
        }
    }
}

static void compose(uint16_t *out, const uint8_t *sprite, float dx, float dy, float lean)
{
    if (bx1 <= bx0 || by1 <= by0) return;
    /* Reduce rotation if its extent cannot fit between the text cards. */
    float c, s, x0, y0, x1, y1;
    for (int attempt = 0; ; ++attempt) {
        float rad = lean*0.01745329252f;
        c = cosf(rad); s = sinf(rad); x0 = y0 = 1000; x1 = y1 = -1000;
        for (int i = 0; i < 4; ++i) {
            float x = 104+(i&1?bx1:bx0)-180;
            float y = 80+(i&2?by1:by0)-245;
            float px = 180+c*x-s*y, py = 245+s*x+c*y;
            x0 = fminf(x0,px); y0 = fminf(y0,py); x1 = fmaxf(x1,px); y1 = fmaxf(y1,py);
        }
        if ((x1-x0 <= 152 && y1-y0 <= 245) || attempt >= 12) break;
        lean *= 0.7f;
    }
    dx = fmaxf(108-x0,fminf(260-x1,dx));
    dy = fmaxf(90-y0,fminf(335-y1,dy));
    /* Combine character lean with whole-scene rotation, sampling only once. */
    float combined_c = global_c*c-global_s*s, combined_s = global_s*c+global_c*s;
    float pivot_x = 180+global_c*dx-global_s*(65+dy);
    float pivot_y = 180+global_s*dx+global_c*(65+dy);
    x0 = y0 = 1000; x1 = y1 = -1000;
    for (int i = 0; i < 4; ++i) {
        float x = (i&1?bx1:bx0)-76, y = (i&2?by1:by0)-165;
        float px = pivot_x+combined_c*x-combined_s*y, py = pivot_y+combined_s*x+combined_c*y;
        x0 = fminf(x0,px); y0 = fminf(y0,py); x1 = fmaxf(x1,px); y1 = fmaxf(y1,py);
    }
    int left = fmaxf(0,floorf(x0)-1), right = fminf(360,ceilf(x1)+1);
    int top = fmaxf(0,floorf(y0)-1), bottom = fminf(360,ceilf(y1)+1);
    int32_t cs = lroundf(combined_c*65536), sn = lroundf(combined_s*65536);
    const uint16_t *rgb = (const uint16_t *)sprite;
    const uint8_t *alpha = sprite+2*BADGE_SPRITE_PIXELS;
    int32_t origin_u = lroundf((combined_c*(0.5f-pivot_x)+combined_s*(0.5f-pivot_y)+75.5f)*65536);
    int32_t origin_v = lroundf((-combined_s*(0.5f-pivot_x)+combined_c*(0.5f-pivot_y)+164.5f)*65536);
    for (int ty = top; ty < bottom; ty += 16) for (int tx = left; tx < right; tx += 16)
    for (int y = ty; y < ty+16 && y < bottom; ++y) {
        int tile_right = right < tx+16 ? right : tx+16;
        int32_t u = origin_u+cs*tx+sn*y;
        int32_t v = origin_v-sn*tx+cs*y;
        for (int x = tx; x < tile_right; ++x, u += cs, v -= sn) {
            int sx = u>>16, sy = v>>16;
            if (sx < bx0-1 || sx >= bx1 || sy < by0-1 || sy >= by1 ||
                sx < 0 || sy < 0 || sx >= BADGE_SPRITE_W-1 || sy >= BADGE_SPRITE_H-1) continue;
            unsigned i = sy*BADGE_SPRITE_W+sx;
            unsigned a0 = alpha[i];
            if (!((u|v)&0xf800)) {
                /* At exact sample positions the bilinear result is pixel i. */
                if (a0) out[y*360+x]=over(out[y*360+x],rgb[i],a0);
                continue;
            }
            unsigned a1 = alpha[i+1], a2 = alpha[i+BADGE_SPRITE_W], a3 = alpha[i+BADGE_SPRITE_W+1];
            if (!(a0|a1|a2|a3)) continue;
            unsigned fx = (u>>11)&31, fy = (v>>11)&31;
            uint16_t fg;
            unsigned a;
            if ((a0&a1&a2&a3) == 255) {
                fg = mix565(mix565(rgb[i],rgb[i+1],fx),mix565(rgb[i+BADGE_SPRITE_W],rgb[i+BADGE_SPRITE_W+1],fx),fy);
                a = 255;
            } else {
                unsigned w0 = a0*(32-fx)*(32-fy), w1 = a1*fx*(32-fy), w2 = a2*(32-fx)*fy, w3 = a3*fx*fy;
                unsigned total = w0+w1+w2+w3;
                a = (total+512)>>10;
                if (!a) continue;
                uint16_t p0 = rgb[i], p1 = rgb[i+1], p2 = rgb[i+BADGE_SPRITE_W], p3 = rgb[i+BADGE_SPRITE_W+1];
                unsigned r = ((p0>>11)*w0+(p1>>11)*w1+(p2>>11)*w2+(p3>>11)*w3+total/2)/total;
                unsigned g = (((p0>>5)&63)*w0+((p1>>5)&63)*w1+((p2>>5)&63)*w2+((p3>>5)&63)*w3+total/2)/total;
                unsigned b = ((p0&31)*w0+(p1&31)*w1+(p2&31)*w2+(p3&31)*w3+total/2)/total;
                fg = (r<<11)|(g<<5)|b;
            }
            out[y*360+x] = over(out[y*360+x],fg,a);
        }
    }
}

static void stars(uint16_t *out, float strength, float phase)
{
    /* Keep the effect below the name and above the seated head. */
    for (int i = 0; i < 3; ++i) {
        float t = phase*3.2f+i*2.0943951f;
        int cx = lroundf(180+33*cosf(t)), cy = lroundf(139+9*sinf(t));
        int radius = 4+(sinf(t)>0);
        uint16_t color = i==1 ? 0xE615 : 0xF651;
        unsigned a = lroundf(strength*230);
        for (int y = -radius; y <= radius; ++y) for (int x = -radius; x <= radius; ++x) {
            if (abs(x)+abs(y) > radius || (abs(x)>1 && abs(y)>1)) continue;
            int px = cx+x, py = cy+y;
            if (px>=110 && px<260 && py>=100 && py<160) {
                int rx = lroundf(179.5f+global_c*(px-179.5f)-global_s*(py-179.5f));
                int ry = lroundf(179.5f+global_s*(px-179.5f)+global_c*(py-179.5f));
                if (rx>=0 && rx<360 && ry>=0 && ry<360) out[ry*360+rx] = over(out[ry*360+rx],color,a);
            }
        }
    }
}

bool badge_scene_present(const uint8_t *sprite, bool pose_changed, float dx, float dy,
                         float lean, float orientation, float dizzy, float phase)
{
    int64_t started = esp_timer_get_time();
    int x = lroundf(dx*4), y = lroundf(dy*4), rotation = lroundf(lean*10);
    int angle = ((int)lroundf(orientation*10)%3600+3600)%3600;
    bool effects = dizzy > 0;
    uint32_t revision = badge_dashboard_revision();
    bool hud_changed = revision != hud_revision;
    bool snapshot_ok = true;
    if (hud_changed) {
        bsp_display_lock(-1);
        revision = badge_dashboard_revision();
        lv_obj_update_layout(hud);
        if (lv_snapshot_take_to_draw_buf(hud,LV_COLOR_FORMAT_RGB565,&hud_draw) == LV_RESULT_OK) {
            hud_revision = revision;
        }
        else snapshot_ok = false;
        bsp_display_unlock();
    }
    hud_us = esp_timer_get_time()-started;
    if (!snapshot_ok) return false;
    if (hud_changed) classify_hud();
    started = esp_timer_get_time();
    bool turned = angle != last_angle;
    if (turned) {
        global_c = cosf(angle*0.001745329252f); global_s = sinf(angle*0.001745329252f);
    }
    bool direct_background=turned || hud_changed;
    if (direct_background) { rotate_hud(angle,canvas[slot]); oriented_valid=false; }
    rotate_us = esp_timer_get_time()-started;
    started = esp_timer_get_time();
    bool composed = pose_changed || hud_changed || turned || x!=last_x || y!=last_y || rotation!=last_lean || effects || last_dizzy;
    if (composed) {
        if (pose_changed || bx1 <= bx0) bounds(sprite);
        if (!direct_background) {
            if (!oriented_valid) { rotate_hud(angle,oriented_hud); oriented_valid=true; }
            memcpy(canvas[slot],oriented_hud,BADGE_FRAME_BYTES);
        }
        compose(canvas[slot],sprite,x/4.0f,y/4.0f,rotation/10.0f);
        if (effects) stars(canvas[slot],dizzy,phase);
    }
    compose_us = esp_timer_get_time()-started;
    started = esp_timer_get_time();
    if (composed || angle != last_angle) {
        bsp_display_lock(-1);
        if (composed) {
            lv_image_cache_drop(&scene_image[slot]);
            lv_image_set_src(scene,&scene_image[slot]);
            lv_obj_invalidate(scene);
            slot ^= 1;
        }
        bsp_display_unlock();
    }
    bool changed = composed || angle != last_angle;
    last_x = x; last_y = y; last_lean = rotation; last_angle = angle; last_dizzy = effects;
    present_us = esp_timer_get_time()-started;
    return changed;
}

void badge_scene_timings(void)
{
    printf("BADGE_RENDER {\"hud_us\":%u,\"rotate_us\":%u,\"compose_us\":%u,\"present_us\":%u,\"detail_cells\":%u}\n",
           (unsigned)hud_us,(unsigned)rotate_us,(unsigned)compose_us,(unsigned)present_us,(unsigned)hud_detail_cells);
}

int badge_scene_selftest(void)
{
    int failures = 0;
    const uint16_t palette[] = {0,0xffff,0xf800,0x07e0,0x001f,0xa536,0x7bef};
    for (unsigned i=0;i<sizeof(palette)/sizeof(palette[0]);++i)
        for (unsigned j=0;j<sizeof(palette)/sizeof(palette[0]);++j)
            for (unsigned w=0;w<=32;++w) {
                uint16_t a=palette[i],b=palette[j];
                unsigned r=((a>>11)*(32-w)+(b>>11)*w)>>5;
                unsigned g=(((a>>5)&63)*(32-w)+((b>>5)&63)*w)>>5;
                unsigned bl=((a&31)*(32-w)+(b&31)*w)>>5;
                if (mix565(a,b,w) != ((r<<11)|(g<<5)|bl)) ++failures;
            }
    printf("BADGE_TEST %s RGB565 packed bilinear arithmetic\n",failures?"FAIL":"PASS");
    uint8_t *buffer=heap_caps_calloc(3,BADGE_SPRITE_BYTES,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!buffer) { printf("BADGE_TEST FAIL alpha test allocation\n"); return failures+1; }
    uint8_t *a=buffer,*b=buffer+BADGE_SPRITE_BYTES,*out=buffer+2*BADGE_SPRITE_BYTES;
    ((uint16_t *)a)[0]=0xf800; a[2*BADGE_SPRITE_PIXELS]=255;
    badge_scene_blend(out,a,b,0.5f);
    bool alpha_ok=((uint16_t *)out)[0]==0xf800 && out[2*BADGE_SPRITE_PIXELS]==128;
    ((uint16_t *)b)[0]=0x001f; b[2*BADGE_SPRITE_PIXELS]=128; a[2*BADGE_SPRITE_PIXELS]=128;
    badge_scene_blend(out,a,b,0.5f);
    uint16_t purple=((uint16_t *)out)[0];
    alpha_ok=alpha_ok && out[2*BADGE_SPRITE_PIXELS]==128 && (purple>>11)>=15 && (purple&31)>=15;
    badge_scene_blend(out,a,b,1);
    alpha_ok=alpha_ok && ((uint16_t *)out)[0]==0x001f && out[2*BADGE_SPRITE_PIXELS]==128 && out[2*BADGE_SPRITE_PIXELS+1]==0;
    printf("BADGE_TEST %s premultiplied alpha preserves edge brightness\n",alpha_ok?"PASS":"FAIL");
    free(buffer);
    return failures+!alpha_ok;
}
