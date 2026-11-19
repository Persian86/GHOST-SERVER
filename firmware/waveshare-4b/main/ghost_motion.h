#pragma once
#include <stdint.h>

typedef enum {GHOST_REST,GHOST_ATTENTIVE,GHOST_PONDER,GHOST_TALK,GHOST_WAIT,GHOST_CONCERNED} ghost_pose_t;
typedef struct {float position,velocity;} ghost_spring_t;
typedef struct {
    uint32_t random;
    float sway_phase,bob_phase,cloth_phase,breath_phase;
    float blink_wait,blink_time,gaze_wait,gaze_target_x,gaze_target_y;
    float mouth;
    ghost_spring_t x,y,gaze_x,gaze_y,attention,brow,drape[2],hem[4];
} ghost_motion_t;
typedef struct {
    float x,y,breath,blink,gaze_x,gaze_y,mouth,brow;
    float drape[2],hem[4],trail;
} ghost_motion_frame_t;

void ghost_motion_init(ghost_motion_t *motion,uint32_t seed);
void ghost_motion_step(ghost_motion_t *motion,float seconds,ghost_pose_t pose,
                       float speech,ghost_motion_frame_t *frame);
