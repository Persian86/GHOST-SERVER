const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const images=Array.from({length:7},()=>({parentElement:{querySelector:()=>({})}}));
const urls=new Map(),revoked=[],events={},calls=[];let sequence=0,release;
function packet(index,n){const b=Buffer.alloc(9);b.writeUInt32BE(index);b.writeUInt32BE(1,4);b[8]=n;return b;}
const all=Buffer.concat([packet(0,1),packet(0,2),packet(0,3),packet(6,7)]);
const chunks=[all.subarray(0,5),all.subarray(5,17),all.subarray(17)];
const reader={async read(){if(chunks.length)return{value:chunks.shift(),done:false};return new Promise(r=>release=r)},async cancel(){release?.({done:true})}};
const context={document:{querySelectorAll:()=>images},cameras:images.map((_,i)=>'Camera '+i),base:'/cam/test',notice:{},
 fetch:async(url,opts)=>{calls.push([url,opts]);return{ok:true,status:200,body:{getReader:()=>reader}}},
 URL:{createObjectURL(blob){const url='blob:'+ ++sequence;urls.set(url,blob);return url},revokeObjectURL(url){revoked.push(url)}},
 Blob,AbortController,Uint8Array,DataView,Date,setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
 window:{addEventListener(name,fn){events[name]=fn}}};
vm.runInNewContext(fs.readFileSync('ghost_server/camera_wall.js','utf8'),context);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(calls.length,1);assert.equal(calls[0][0],'/cam/test/live');
 assert.equal(sequence,2); // One decode each for cameras 0 and 6, rather than queued decodes.
 assert.equal(new Uint8Array(await urls.get(images[0].src).arrayBuffer())[0],1);
 images[0].onload();
 assert.equal(new Uint8Array(await urls.get(images[0].src).arrayBuffer())[0],3); // Skip the stale middle frame.
 assert.equal(new Uint8Array(await urls.get(images[6].src).arrayBuffer())[0],7);
 events.pagehide();assert.equal(calls[0][1].signal.aborted,true);
 assert.equal(revoked.length,3);
 await reader.cancel();
 console.log('PASS: seven cameras use one connection; fragmented packets, newest-frame decode and leaving the page');
})().catch(e=>{console.error(e);process.exitCode=1});
