// Mixed camera wall: direct WebRTC where configured, one latest-JPEG fallback.
(function () {
 const images=[...document.querySelectorAll('#feeds figure img')];
 const options=typeof cameraOptions==='undefined'?[]:cameraOptions;
 const states=images.map(()=>({busy:false,next:null,url:null,last:0,rtc:false,status:'',player:null,quality:''}));
 let controller=null,retry=null,closed=false,version=0,selection=-1;
 function visible(i){return selection===-1||selection===i;}
 function show(i,bytes){
  const s=states[i],im=images[i];if(!s||closed||s.rtc||!visible(i))return;
  if(s.busy){s.next=bytes;return}s.busy=true;
  const url=URL.createObjectURL(new Blob([bytes],{type:'image/jpeg'}));
  const finish=ok=>{
   URL.revokeObjectURL(url);s.url=null;s.busy=false;if(ok)s.last=Date.now();
   const next=s.next;s.next=null;if(next)show(i,next);
  };
  im.onload=()=>finish(true);im.onerror=()=>finish(false);s.url=url;im.src=url;
 }
 function needed(){return states.map((s,i)=>visible(i)&&!s.rtc?i:-1).filter(i=>i>=0);}
 function restart(){
  version++;controller?.abort();controller=null;clearTimeout(retry);
  if(!closed&&needed().length)connect(version);
 }
 async function connect(owner){
  if(closed||owner!==version)return;
  const ids=needed();if(!ids.length)return;
  const abort=new AbortController();controller=abort;let reader;
  try{
   const suffix=ids.length===images.length?'':'?only='+ids.join(',');
   const response=await fetch(base+'/live'+suffix,{cache:'no-store',signal:abort.signal});
   if(response.status===404||response.status===410){notice.textContent='Camera link expired. Open Cameras again.';stop();return}
   if(!response.ok||!response.body)throw Error('stream');
   reader=response.body.getReader();let buffer=new Uint8Array(0);
   while(!closed&&owner===version){
    const {value,done}=await reader.read();if(done)break;
    const joined=new Uint8Array(buffer.length+value.length);joined.set(buffer);joined.set(value,buffer.length);buffer=joined;
    let offset=0;
    while(buffer.length-offset>=8){
     const header=new DataView(buffer.buffer,buffer.byteOffset+offset,8),i=header.getUint32(0),length=header.getUint32(4);
     if(i>=images.length||!length||length>4194304)throw Error('invalid frame');
     if(buffer.length-offset<8+length)break;
     show(i,buffer.slice(offset+8,offset+8+length));offset+=8+length;
    }
    buffer=buffer.slice(offset);if(buffer.length>4194312)throw Error('invalid frame');
   }
  }catch(_){/* Private stream details are never displayed. */}
  finally{if(reader){try{await reader.cancel()}catch(_){}}}
  if(!closed&&owner===version)retry=setTimeout(()=>connect(owner),1500);
 }
 function select(index){
  if(closed)return;selection=index;
  states.forEach((s,i)=>{
   if(!s.player)return;
   if(!visible(i)){s.player.stop();s.rtc=false;s.quality='';return;}
   const quality=index===i&&options[i]?.main?'main':'preview';
   if(s.quality===quality)return;
   s.quality=quality;s.rtc=true;s.status='connecting';
   s.player.start(quality);
  });
  restart();
 }
 if(window.GhostCameraRTC&&typeof RTCPeerConnection!=='undefined')states.forEach((s,i)=>{
  if(options[i]?.enabled)s.player=new window.GhostCameraRTC(i,images[i],status=>{
   s.status=status;
   if(status==='fallback'){s.rtc=false;s.last=0;restart();}
  });
 });
 const freshness=setInterval(()=>{
  images.forEach((im,i)=>{
   const label=im.parentElement.querySelector('figcaption'),s=states[i];
   const status=s.rtc?(s.status==='live'?' · Direct live':s.status==='reconnecting'?' · Reconnecting…':' · Connecting direct…'):
    !s.last?' · Connecting preview…':Date.now()-s.last>5000?' · Reconnecting…':s.player?' · Preview fallback':'';
   if(label)label.textContent=cameras[i]+status;
  });
 },1000);
 function stop(){
  closed=true;version++;clearTimeout(retry);clearInterval(freshness);controller?.abort();
  states.forEach(s=>{s.player?.stop();if(s.url)URL.revokeObjectURL(s.url);s.next=null});
 }
 window.GhostCameraWall={select,stop};window.addEventListener('pagehide',stop);
 if(typeof cameraLifetime!=='undefined')setTimeout(()=>{
  stop();notice.textContent='Camera link expired. Open Cameras again.';
 },cameraLifetime);
 select(typeof selected==='undefined'?-1:selected);
})();
