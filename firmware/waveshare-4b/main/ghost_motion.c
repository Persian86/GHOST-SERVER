#include "ghost_motion.h"
#include <math.h>
#include <string.h>

static float limit(float x,float low,float high) {return fminf(high,fmaxf(low,x));}
static float random_unit(ghost_motion_t *m)
{
    uint32_t x=m->random;x^=x<<13;x^=x>>17;x^=x<<5;m->random=x;
    return (x>>8)/16777216.0f;
}
static float phase(float value,float step) {return fmodf(value+step,6.2831853f);}
static void spring(ghost_spring_t *s,float target,float frequency,float dt)
{
    // Critically damped integration, bounded to 1/120 s substeps.
    int steps=(int)ceilf(dt*120.0f);float h=dt/steps;
    for(int i=0;i<steps;i++) {
        s->velocity+=(frequency*frequency*(target-s->position)-2*frequency*s->velocity)*h;
        s->position+=s->velocity*h;
    }
}

void ghost_motion_init(ghost_motion_t *m,uint32_t seed)
{
    memset(m,0,sizeof(*m));m->random=seed?seed:1;
    m->blink_wait=2.5f+random_unit(m)*2.5f;m->blink_time=-1;
    m->gaze_wait=1.5f+random_unit(m)*2;
}

void ghost_motion_step(ghost_motion_t *m,float dt,ghost_pose_t pose,float speech,ghost_motion_frame_t *f)
{
    // Freeze safely on invalid input; never feed NaN or a multi-second jump
    // into the spring integrator after Setup or a delayed display callback.
    if(!isfinite(dt) || dt<=0) dt=1.0f/30;
    dt=fminf(dt,0.1f);
    if(!isfinite(speech)) speech=0;
    speech=limit(speech,0,1);
    m->sway_phase=phase(m->sway_phase,dt*0.55f);
    m->bob_phase=phase(m->bob_phase,dt*1.12f);
    m->cloth_phase=phase(m->cloth_phase,dt*1.65f);
    m->breath_phase=phase(m->breath_phase,dt*1.35f);
    spring(&m->attention,pose==GHOST_ATTENTIVE?1:0,5,dt);
    float attentive=limit(m->attention.position,0,1);
    float sway=sinf(m->sway_phase)*10+sinf(m->sway_phase*2)*2;
    float bob=sinf(m->bob_phase)*8+sinf(m->breath_phase)*1.5f;
    spring(&m->x,sway*(1-attentive*0.55f),5,dt);
    spring(&m->y,bob*(1-attentive*0.4f)-attentive*3,5,dt);
    f->x=limit(m->x.position,-13,13);f->y=limit(m->y.position,-13,11);
    f->breath=sinf(m->breath_phase)*1.5f;
    f->trail=limit(-m->x.velocity*0.23f,-4,4);
    for(int side=0;side<2;side++) {
        float wave=sinf(m->cloth_phase+side*1.6f)*3.5f;
        float lift=pose==GHOST_WAIT?sinf(m->bob_phase*2)*1.5f:0;
        spring(&m->drape[side],wave-m->y.velocity*0.20f+lift,6,dt);
        f->drape[side]=limit(m->drape[side].position,-7,7);
    }
    for(int i=0;i<4;i++) {
        spring(&m->hem[i],sinf(m->cloth_phase+i*0.85f)*3-m->y.velocity*0.16f,5,dt);
        f->hem[i]=limit(m->hem[i].position,-5,5);
    }
    m->gaze_wait-=dt;
    if(m->gaze_wait<=0) {
        m->gaze_wait=2.5f+random_unit(m)*3.5f;
        m->gaze_target_x=(random_unit(m)-0.5f)*5;
        m->gaze_target_y=(random_unit(m)-0.5f)*2;
    }
    float gx=pose==GHOST_PONDER?-3:pose==GHOST_ATTENTIVE || pose==GHOST_TALK?0:m->gaze_target_x;
    float gy=pose==GHOST_PONDER?-2.5f:pose==GHOST_ATTENTIVE || pose==GHOST_TALK?0:m->gaze_target_y;
    spring(&m->gaze_x,gx,9,dt);spring(&m->gaze_y,gy,9,dt);
    f->gaze_x=limit(m->gaze_x.position,-3,3);f->gaze_y=limit(m->gaze_y.position,-3,2);
    m->blink_wait-=dt;
    if(m->blink_time<0 && m->blink_wait<=0) m->blink_time=0;
    f->blink=0;
    if(m->blink_time>=0) {
        m->blink_time+=dt;float t=m->blink_time;
        if(t<0.07f) f->blink=t/0.07f;
        else if(t<0.11f) f->blink=1;
        else if(t<0.25f) f->blink=1-(t-0.11f)/0.14f;
        else {m->blink_time=-1;m->blink_wait=2.8f+random_unit(m)*4;}
        f->blink=limit(f->blink,0,1);
        f->blink=f->blink*f->blink*(3-2*f->blink);
    }
    float target=pose==GHOST_TALK?speech:0;
    m->mouth+=(target-m->mouth)*(1-expf(-dt/(target>m->mouth?0.09f:0.20f)));
    f->mouth=limit(m->mouth,0,1);
    spring(&m->brow,pose==GHOST_PONDER?-2:pose==GHOST_CONCERNED?2:-attentive,8,dt);
    f->brow=m->brow.position;
}
