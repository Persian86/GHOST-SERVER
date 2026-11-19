#include "ghost_motion.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>

static void bounded(const ghost_motion_frame_t *f)
{
    assert(isfinite(f->x) && fabsf(f->x)<=13);
    assert(isfinite(f->y) && f->y>=-13 && f->y<=11);
    assert(isfinite(f->blink) && f->blink>=0 && f->blink<=1);
    assert(isfinite(f->mouth) && f->mouth>=0 && f->mouth<=1);
    assert(fabsf(f->gaze_x)<=3 && f->gaze_y>=-3 && f->gaze_y<=2);
    assert(fabsf(f->trail)<=4);
    for(int i=0;i<2;i++) assert(isfinite(f->drape[i]) && fabsf(f->drape[i])<=7);
    for(int i=0;i<4;i++) assert(isfinite(f->hem[i]) && fabsf(f->hem[i])<=5);
}

int main(void)
{
    ghost_motion_t m;ghost_motion_frame_t f,previous;
    ghost_motion_init(&m,123);int blink_frames=0,open_frames=0;
    float max_delta=0;
    for(int i=0;i<18000;i++) {
        // Ten minutes across every pose, with recurring delayed frames.
        ghost_pose_t pose=(ghost_pose_t)((i/150)%6);
        float dt=i%127==0?0.1f:1.0f/30;
        ghost_motion_step(&m,dt,pose,i%20<10?1:0,&f);bounded(&f);
        if(i && dt<0.04f) {
            float delta=fabsf(f.x-previous.x)+fabsf(f.y-previous.y);
            if(delta>max_delta) max_delta=delta;
        }
        if(f.blink>0.8f) blink_frames++;
        if(f.blink==0) open_frames++;
        previous=f;
    }
    assert(blink_frames>50 && open_frames>15000);assert(max_delta<1.5f);

    // Speech mouth opens gradually, then decays without an abrupt snap.
    ghost_motion_init(&m,1);
    ghost_motion_step(&m,1.0f/30,GHOST_TALK,1,&f);
    assert(f.mouth>0 && f.mouth<0.5f);
    for(int i=0;i<30;i++) ghost_motion_step(&m,1.0f/30,GHOST_TALK,1,&f);
    assert(f.mouth>0.95f);
    ghost_motion_step(&m,1.0f/30,GHOST_REST,0,&f);
    assert(f.mouth>0.7f);
    for(int i=0;i<60;i++) ghost_motion_step(&m,1.0f/30,GHOST_REST,0,&f);
    assert(f.mouth<0.001f);

    // Frame-rate changes should not change the overall path/posture.
    ghost_motion_t a,b;ghost_motion_frame_t fa,fb;
    ghost_motion_init(&a,123);ghost_motion_init(&b,123);
    for(int i=0;i<300;i++) ghost_motion_step(&a,1.0f/30,GHOST_ATTENTIVE,0,&fa);
    for(int i=0;i<600;i++) ghost_motion_step(&b,1.0f/60,GHOST_ATTENTIVE,0,&fb);
    assert(fabsf(fa.x-fb.x)<0.15f && fabsf(fa.y-fb.y)<0.25f);
    assert(fabsf(fa.drape[0]-fb.drape[0])<0.25f);

    // Setup pauses and invalid inputs must not destabilize the next frame.
    ghost_motion_step(&m,30,GHOST_PONDER,NAN,&f);bounded(&f);
    ghost_motion_step(&m,NAN,GHOST_REST,INFINITY,&f);bounded(&f);
    ghost_motion_step(&m,-1,GHOST_REST,0,&f);bounded(&f);
    puts("Motion bounds, easing, blink, delayed-frame and frame-rate checks passed");
    return 0;
}
