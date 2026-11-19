#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>

// One capture producer and one network consumer. The consumer owns a slot
// until release, including while a network write blocks. Keep queue control
// (including atomics) in internal RAM; allocate only the blocks in PSRAM.
#define GHOST_UPLINK_SAMPLES 1600
#define GHOST_UPLINK_SLOTS 32
typedef struct {
    size_t bytes; // Zero is an ordered end-of-question marker.
    unsigned frames;
    int64_t captured_us;
    int16_t samples[GHOST_UPLINK_SAMPLES];
} ghost_uplink_block_t;
typedef struct {
    atomic_uint head,tail;
    atomic_bool cancelled;
    bool accepting; // Only the capture producer changes this field.
    ghost_uplink_block_t *blocks;
} ghost_uplink_t;

void ghost_uplink_init(ghost_uplink_t *queue,ghost_uplink_block_t *blocks);
bool ghost_uplink_begin(ghost_uplink_t *queue);
bool ghost_uplink_push(ghost_uplink_t *queue,const int16_t *samples,size_t bytes,
                       unsigned frames,int64_t captured_us);
const ghost_uplink_block_t *ghost_uplink_peek(ghost_uplink_t *queue);
void ghost_uplink_release(ghost_uplink_t *queue);
void ghost_uplink_cancel(ghost_uplink_t *queue);
bool ghost_uplink_cancelled(const ghost_uplink_t *queue);
bool ghost_uplink_idle(const ghost_uplink_t *queue);
