// One fetch avoids the per-host connection ceiling of an MJPEG image per tile.
// Decode at most one frame per camera; replace its pending frame with the latest.
(function () {
 const images=[...document.querySelectorAll('#feeds figure img')];
 const state=images.map(()=>({busy:false,next:null,url:null,last:0}));
 let controller=null,retry=null,closed=false;
 function show(i,bytes){
  const s=state[i],im=images[i];if(!s||closed)return;
  if(s.busy){s.next=bytes;return}s.busy=true;
  const url=URL.createObjectURL(new Blob([bytes],{type:'image/jpeg'}));
  const finish=ok=>{
   URL.revokeObjectURL(url);s.url=null;s.busy=false;if(ok)s.last=Date.now();
   const next=s.next;s.next=null;if(next)show(i,next);
  };
  im.onload=()=>finish(true);im.onerror=()=>finish(false);
  s.url=url;im.src=url;
 }
 async function connect(){
  if(closed)return;controller=new AbortController();
  let reader;
  try{
   const response=await fetch(base+'/live',{cache:'no-store',signal:controller.signal});
   if(response.status===404||response.status===410){notice.textContent='Camera link expired. Open Cameras again.';closed=true;return}
   if(!response.ok||!response.body)throw Error('stream');
   reader=response.body.getReader();let buffer=new Uint8Array(0);
   while(!closed){
    const {value,done}=await reader.read();if(done)break;
    const joined=new Uint8Array(buffer.length+value.length);joined.set(buffer);joined.set(value,buffer.length);buffer=joined;
    let offset=0;
    while(buffer.length-offset>=8){
     const header=new DataView(buffer.buffer,buffer.byteOffset+offset,8),i=header.getUint32(0),length=header.getUint32(4);
     if(i>=images.length||!length||length>4194304)throw Error('invalid frame');
     if(buffer.length-offset<8+length)break;
     show(i,buffer.slice(offset+8,offset+8+length));offset+=8+length;
    }
    buffer=buffer.slice(offset);
    if(buffer.length>4194312)throw Error('invalid frame');
   }
  }catch(_){/* reconnect without including private stream details */}
  finally{if(reader){try{await reader.cancel()}catch(_){}}}
  if(!closed)retry=setTimeout(connect,1500);
 }
 const freshness=setInterval(()=>{
  images.forEach((im,i)=>{const label=im.parentElement.querySelector('figcaption'),s=state[i];
   if(label)label.textContent=cameras[i]+(!s.last?' · Connecting…':Date.now()-s.last>5000?' · Reconnecting…':'');
  });
 },1000);
 window.addEventListener('pagehide',()=>{
  closed=true;clearTimeout(retry);clearInterval(freshness);controller?.abort();
  state.forEach(s=>{if(s.url)URL.revokeObjectURL(s.url);s.next=null});
 });
 connect();
})();
