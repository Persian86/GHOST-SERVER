const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let peers=[],requests=[],timers=new Map(),sequence=0,respond;
class Peer {
 constructor(config){this.config=config;this.iceGatheringState='complete';this.connectionState='new';peers.push(this)}
 addTransceiver(kind,opts){assert.equal(kind,'video');assert.equal(opts.direction,'recvonly')}
 async createOffer(){return{type:'offer',sdp:'v=0\r\nm=video 9 RTP/AVP 96\r\na=recvonly\r\n'}}
 async setLocalDescription(d){this.localDescription=d}
 async setRemoteDescription(d){this.remoteDescription=d}
 close(){this.closed=true}
}
const image={hidden:false,parentElement:{append(v){this.video=v}}};
const video={hidden:true,play:()=>Promise.resolve()};
const context={window:{},document:{createElement:()=>video},RTCPeerConnection:Peer,AbortController,
 base:'/cam/scoped',setInterval(){return 1},clearInterval(){},Date,setTimeout(fn){timers.set(++sequence,fn);return sequence},clearTimeout(id){timers.delete(id)},
 fetch:async(url,options)=>{requests.push({url,options});return new Promise(resolve=>respond=resolve)}};
vm.runInNewContext(fs.readFileSync('ghost_server/camera_rtc.js','utf8'),context);
const statuses=[],player=new context.window.GhostCameraRTC(0,image,s=>statuses.push(s));
async function tick(){await new Promise(setImmediate)}
(async()=>{
 const startup=player.start('preview');await tick();
 assert.equal(peers.length,1);assert.deepEqual(Array.from(peers[0].config.iceServers),[]);
 assert.equal(requests[0].url,'/cam/scoped/webrtc/0?quality=preview');
 assert.equal(requests[0].options.headers['X-Ghost-Camera'],'1');
 respond({ok:true,text:async()=> 'v=0\r\nanswer'});await startup;
 assert.equal(peers[0].remoteDescription.type,'answer');
 peers[0].ontrack({streams:[{}]});video.onplaying();
 assert.equal(image.hidden,true);assert.equal(video.hidden,false);assert.equal(statuses.at(-1),'live');
 const next=player.start('main');await tick();assert.equal(peers[0].closed,true);
 assert.match(requests.at(-1).url,/quality=main$/);
 player.stop();respond({ok:true,text:async()=> 'old answer'});await next;
 assert.equal(peers[1].remoteDescription,undefined);assert.equal(peers[1].closed,true);
 assert.equal(requests.at(-1).options.signal.aborted,true);
 const failed=player.start('preview');await tick();respond({ok:false});await failed;
 assert.equal(statuses.at(-1),'fallback');assert.equal(image.hidden,false);
 const timeout=player.start('preview');await tick();
 [...timers.values()].at(-1)();respond({ok:false});await timeout;
 assert.equal(statuses.at(-1),'fallback');assert.equal(peers.at(-1).closed,true);
 console.log('PASS: receive-only video, private signalling, quality switch, stale replies, fallback and timeout cleanup');
})().catch(e=>{console.error(e);process.exitCode=1});
