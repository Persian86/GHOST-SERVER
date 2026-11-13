// Exercise touch zoom/pan and the async controls without real household feeds.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.style={};this.dataset={};this.hidden=false;this.disabled=false;this.textContent='';this.clientWidth=1000;this.clientHeight=600;this.naturalWidth=1000;this.naturalHeight=600}
 append(...items){this.children.push(...items)}
 setAttribute(){} setPointerCapture(){}
 querySelector(name){return this.children.find(x=>x.tagName===name)}
 remove(){}
}
const ids=new Map(),calls=[];
const $=id=>{if(!ids.has(id))ids.set(id,new Element());return ids.get(id)};
const ctx={document:{getElementById:$,createElement:tag=>new Element(tag)},window:{addEventListener(){}},
 setTimeout(){},setInterval(){return 1},clearInterval(){},console,
 fetch:async(url,options)=>{calls.push([url,options]);return{ok:true,json:async()=>url.endsWith('/recordings/0')?{id:'clip',name:'Front'}:url.endsWith('/media')?[]:{state:'saved'}}}};
vm.createContext(ctx);
const html=fs.readFileSync('ghost_server/camera_viewer.html','utf8');
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('__CAMERA_DATA__','["Front","Back"]')
 .replace('__CAMERA_BASE__','"/cam/test-token"'),ctx);
const feeds=$('feeds');assert.equal(feeds.children.length,2);assert.equal(feeds.children[0].children[0].src,'/cam/test-token/stream/0');
function touch(id,x,y){return{pointerId:id,clientX:x,clientY:y}}
async function run(){
 assert.equal($('capture').disabled,true);
 const f=feeds.children[0];f.onpointerdown(touch(1,100,100));f.onpointerup(touch(1,100,100));
 assert.equal(feeds.children[1].hidden,true);assert.equal($('capture').disabled,false);
 $('plus').onclick();assert.match(f.children[0].style.transform,/scale\(1.25\)/);
 f.onpointerdown(touch(1,100,100));f.onpointermove(touch(1,10000,10000));f.onpointerup(touch(1,10000,10000));
 assert.match(f.children[0].style.transform,/translate\(125px,75px\)/); // bounded pan
 $('reset').onclick();assert.match(f.children[0].style.transform,/scale\(1\)/);
 f.onpointerdown(touch(1,100,100));f.onpointerdown(touch(2,200,100));f.onpointermove(touch(2,300,100));
 assert.match(f.children[0].style.transform,/scale\(2\)/); // two-finger pinch
 f.onpointerup(touch(1,100,100));f.onpointerup(touch(2,300,100));
 await $('capture').onclick();assert.equal(calls.at(-1)[0],'/cam/test-token/capture/0');
 await $('record').onclick();assert.equal($('stop').hidden,false);assert.equal($('record').disabled,true);
 $('all').onclick();assert.equal($('stop').hidden,false); // Stop stays available in grid
 await $('stop').onclick();assert.equal($('stop').hidden,true);
 await $('gallery').onclick();assert.equal($('saved').hidden,false);assert.equal(feeds.hidden,true);
 assert.equal($('items').textContent,'No photos or videos saved yet.');
 assert(calls.filter(([,o])=>o.method!=='GET').every(([,o])=>o.headers['X-Ghost-Camera']==='1'));
 console.log('PASS: camera selection, touch pinch, bounded pan, capture, record/stop and Saved');
}
run().catch(e=>{console.error(e);process.exitCode=1});
