// Exercise the actual firmware queue without an ESP32, including two threads.
#include "ghost_uplink.h"
#include <assert.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <threads.h>

static ghost_uplink_t queue;

static void test_bound_and_ownership(void)
{
    ghost_uplink_init(&queue);assert(ghost_uplink_begin(&queue));
    int16_t pcm[GHOST_UPLINK_SAMPLES];
    for(unsigned i=0;i<GHOST_UPLINK_SAMPLES;i++) pcm[i]=1234;
    assert(!ghost_uplink_push(&queue,pcm,sizeof(pcm)+2,0,0));
    assert(!ghost_uplink_push(&queue,pcm,3,0,0));
    assert(!ghost_uplink_push(&queue,NULL,2,0,0));
    assert(ghost_uplink_push(&queue,pcm,sizeof(pcm),1,1));
    const ghost_uplink_block_t *held=ghost_uplink_peek(&queue);
    for(unsigned i=1;i<GHOST_UPLINK_SLOTS;i++) assert(ghost_uplink_push(&queue,pcm,sizeof(pcm),i+1,i));
    assert(!ghost_uplink_push(&queue,pcm,sizeof(pcm),99,99));
    assert(!ghost_uplink_begin(&queue)); // In-flight write still owns the client.
    for(unsigned i=0;i<GHOST_UPLINK_SAMPLES;i++) assert(held->samples[i]==1234);
    ghost_uplink_cancel(&queue);
    assert(!ghost_uplink_push(&queue,pcm,sizeof(pcm),99,99));
    while(ghost_uplink_peek(&queue)) ghost_uplink_release(&queue);
    assert(ghost_uplink_idle(&queue));
    for(unsigned slot=0;slot<GHOST_UPLINK_SLOTS;slot++)
        for(unsigned i=0;i<GHOST_UPLINK_SAMPLES;i++) assert(queue.blocks[slot].samples[i]==0);
}

static void test_partial_tail_and_rollover(void)
{
    ghost_uplink_init(&queue);
    // Cross the unsigned counter boundary without confusing empty/full slots.
    atomic_store(&queue.head,UINT_MAX-2);atomic_store(&queue.tail,UINT_MAX-2);
    assert(ghost_uplink_begin(&queue));
    int16_t pcm[320];memset(pcm,0x35,sizeof(pcm));
    for(unsigned i=0;i<5;i++) assert(ghost_uplink_push(&queue,pcm,sizeof(pcm),i,100+i));
    assert(ghost_uplink_push(&queue,NULL,0,5,106));
    assert(!ghost_uplink_push(&queue,pcm,sizeof(pcm),6,107));
    for(unsigned i=0;i<6;i++) {
        const ghost_uplink_block_t *b=ghost_uplink_peek(&queue);assert(b);
        assert(b->frames==i);assert(b->bytes==(i==5?0:sizeof(pcm)));
        if(b->bytes) assert(memcmp(b->samples,pcm,sizeof(pcm))==0);
        ghost_uplink_release(&queue);
    }
    assert(ghost_uplink_idle(&queue));assert(ghost_uplink_begin(&queue));
    assert(ghost_uplink_push(&queue,pcm,sizeof(pcm),7,108));
    ghost_uplink_release(&queue);
}

static int producer(void *unused)
{
    int16_t pcm[GHOST_UPLINK_SAMPLES];
    for(unsigned i=0;i<10000;i++) {
        for(unsigned j=0;j<GHOST_UPLINK_SAMPLES;j++) pcm[j]=(int16_t)i;
        while(!ghost_uplink_push(&queue,pcm,sizeof(pcm),i,i)) thrd_yield();
    }
    while(!ghost_uplink_push(&queue,NULL,0,10000,10000)) thrd_yield();
    return 0;
}

static int consumer(void *unused)
{
    unsigned expected=0;
    while(true) {
        const ghost_uplink_block_t *b=ghost_uplink_peek(&queue);
        if(!b) {thrd_yield();continue;}
        assert(b->frames==expected);assert(b->captured_us==expected);
        if(!b->bytes) {assert(expected==10000);ghost_uplink_release(&queue);break;}
        assert(b->bytes==GHOST_UPLINK_SAMPLES*sizeof(int16_t));
        for(unsigned j=0;j<GHOST_UPLINK_SAMPLES;j++) assert(b->samples[j]==(int16_t)expected);
        if(expected%13==0) thrd_yield(); // Hold a slot during producer activity.
        ghost_uplink_release(&queue);expected++;
    }
    return 0;
}

int main(void)
{
    test_bound_and_ownership();test_partial_tail_and_rollover();
    ghost_uplink_init(&queue);assert(ghost_uplink_begin(&queue));
    thrd_t writer,reader;assert(thrd_create(&writer,producer,NULL)==thrd_success);
    assert(thrd_create(&reader,consumer,NULL)==thrd_success);
    thrd_join(writer,NULL);thrd_join(reader,NULL);assert(ghost_uplink_idle(&queue));
    return 0;
}
