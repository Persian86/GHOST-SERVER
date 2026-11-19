#include "ghost_uplink.h"
#include <string.h>

_Static_assert((GHOST_UPLINK_SLOTS & (GHOST_UPLINK_SLOTS-1))==0,
               "Power-of-two capacity preserves slots across counter rollover");

void ghost_uplink_init(ghost_uplink_t *q,ghost_uplink_block_t *blocks)
{
    memset(q,0,sizeof(*q));
    q->blocks=blocks;memset(blocks,0,sizeof(*blocks)*GHOST_UPLINK_SLOTS);
    atomic_init(&q->head,0);atomic_init(&q->tail,0);atomic_init(&q->cancelled,true);
}

bool ghost_uplink_idle(const ghost_uplink_t *q)
{
    return atomic_load_explicit(&q->head,memory_order_acquire)==
           atomic_load_explicit(&q->tail,memory_order_acquire);
}

bool ghost_uplink_begin(ghost_uplink_t *q)
{
    if(!ghost_uplink_idle(q)) return false;
    atomic_store(&q->cancelled,false);q->accepting=true;return true;
}

bool ghost_uplink_cancelled(const ghost_uplink_t *q) {return atomic_load(&q->cancelled);}
void ghost_uplink_cancel(ghost_uplink_t *q) {atomic_store(&q->cancelled,true);}

bool ghost_uplink_push(ghost_uplink_t *q,const int16_t *samples,size_t bytes,
                       unsigned frames,int64_t captured_us)
{
    if(bytes>sizeof(q->blocks[0].samples) || bytes%2 || (bytes && !samples) ||
       !q->accepting || ghost_uplink_cancelled(q)) return false;
    unsigned tail=atomic_load_explicit(&q->tail,memory_order_relaxed);
    unsigned head=atomic_load_explicit(&q->head,memory_order_acquire);
    if(tail-head>=GHOST_UPLINK_SLOTS) return false;
    ghost_uplink_block_t *block=&q->blocks[tail%GHOST_UPLINK_SLOTS];
    block->bytes=bytes;block->frames=frames;block->captured_us=captured_us;
    if(bytes) memcpy(block->samples,samples,bytes);
    else q->accepting=false;
    atomic_store_explicit(&q->tail,tail+1,memory_order_release);return true;
}

const ghost_uplink_block_t *ghost_uplink_peek(ghost_uplink_t *q)
{
    unsigned head=atomic_load_explicit(&q->head,memory_order_relaxed);
    if(head==atomic_load_explicit(&q->tail,memory_order_acquire)) return NULL;
    return &q->blocks[head%GHOST_UPLINK_SLOTS];
}

void ghost_uplink_release(ghost_uplink_t *q)
{
    unsigned head=atomic_load_explicit(&q->head,memory_order_relaxed);
    if(head==atomic_load_explicit(&q->tail,memory_order_acquire)) return;
    ghost_uplink_block_t *block=&q->blocks[head%GHOST_UPLINK_SLOTS];
    memset(block->samples,0,block->bytes);block->bytes=0;
    atomic_store_explicit(&q->head,head+1,memory_order_release);
}
