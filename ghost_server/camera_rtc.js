// Receive-only camera video. Same-origin SDP exchange keeps camera secrets private.
(function(root){
 class CameraRTC {
  constructor(index,image,onState){
   this.index=index;this.image=image;this.onState=onState;this.generation=0;this.timer=null;this.pc=null;this.abort=null;
   this.video=document.createElement('video');this.video.autoplay=true;this.video.muted=true;this.video.playsInline=true;
   this.video.hidden=true;image.parentElement.append(this.video);
  }
  stop(){
   this.generation++;clearTimeout(this.timer);clearInterval(this.watchdog);this.abort?.abort();this.abort=null;
   if(this.pc){this.pc.onconnectionstatechange=null;this.pc.ontrack=null;this.pc.close();this.pc=null;}
   this.video.onplaying=null;this.video.srcObject=null;this.video.hidden=true;this.image.hidden=false;
  }
  async start(quality){
   this.stop();const owner=this.generation;this.quality=quality;this.live=false;
   const valid=()=>owner===this.generation;
   const fail=()=>{if(valid()){this.stop();this.onState('fallback');}};
   try{
    const pc=new RTCPeerConnection({iceServers:[],bundlePolicy:'max-bundle'});this.pc=pc;
    this.abort=new AbortController();this.onState('connecting');
    this.timer=setTimeout(fail,12000);
    pc.addTransceiver('video',{direction:'recvonly'});
    pc.ontrack=e=>{
     if(!valid())return;
     this.video.srcObject=e.streams[0]||new MediaStream([e.track]);
     this.video.hidden=false;
     this.video.onplaying=()=>{if(valid()){
      this.live=true;clearTimeout(this.timer);this.image.hidden=true;this.video.hidden=false;this.onState('live');
      let frame=-1,progress=Date.now();clearInterval(this.watchdog);
      this.watchdog=setInterval(()=>{
       if(!valid())return;
       const count=this.video.getVideoPlaybackQuality?.().totalVideoFrames??this.video.currentTime;
       if(count!==frame){frame=count;progress=Date.now();}
       else if(Date.now()-progress>8000)fail();
      },1000);
     }};
     this.video.play().catch(fail);
    };
    pc.onconnectionstatechange=()=>{
     if(!valid())return;
     if(pc.connectionState==='failed'||pc.connectionState==='closed')fail();
     else if(pc.connectionState==='disconnected'){
      clearTimeout(this.timer);this.onState('reconnecting');this.timer=setTimeout(fail,4000);
     }else if(pc.connectionState==='connected'&&this.live){clearTimeout(this.timer);this.onState('live');}
    };
    await pc.setLocalDescription(await pc.createOffer());
    // Give local gathering a short budget. Slow/VPN interfaces must not delay
    // the offer; the relay's LAN candidates also permit peer-reflexive checks.
    if(pc.iceGatheringState!=='complete')await new Promise((resolve,reject)=>{
     const signal=this.abort.signal;
     let gatheringTimer;
     const finish=()=>{clearTimeout(gatheringTimer);pc.removeEventListener('icegatheringstatechange',change);signal.removeEventListener('abort',cancel);resolve();};
     const change=()=>{if(pc.iceGatheringState==='complete')finish();};
     const cancel=()=>{clearTimeout(gatheringTimer);pc.removeEventListener('icegatheringstatechange',change);reject(Error('closed'));};
     pc.addEventListener('icegatheringstatechange',change);signal.addEventListener('abort',cancel,{once:true});
     gatheringTimer=setTimeout(finish,1500);change();
    });
    if(!valid())return;
    const response=await fetch(base+'/webrtc/'+this.index+'?quality='+quality,{method:'POST',cache:'no-store',
     signal:this.abort.signal,headers:{'Content-Type':'application/sdp','X-Ghost-Camera':'1'},body:pc.localDescription.sdp});
    if(!valid())return;
    if(!response.ok)throw Error('camera unavailable');
    const sdp=await response.text();if(!valid())return;
    await pc.setRemoteDescription({type:'answer',sdp});
   }catch(_){fail();}
  }
 }
 root.GhostCameraRTC=CameraRTC;
})(window);
