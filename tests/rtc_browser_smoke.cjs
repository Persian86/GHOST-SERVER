// Optional real relay smoke check: npm install --no-save playwright-core, set CHROME.
const {chromium}=require('playwright-core');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME,headless:true,args:['--disable-features=WebRtcHideLocalIpsWithMdns']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:800}});
  page.on('pageerror',e=>console.error('Browser:',e.message));
  page.on('requestfailed',r=>{if(r.failure()?.errorText!=='net::ERR_ABORTED')console.error('Request:',new URL(r.url()).pathname,r.failure()?.errorText)});
  await page.addInitScript(()=>{window.smokePeers=[];const Original=window.RTCPeerConnection;window.RTCPeerConnection=class extends Original{constructor(c){super(c);window.smokePeers.push(this)}async createOffer(...a){try{return await super.createOffer(...a)}catch(e){console.error('RTC offer:',e.message);throw e}}async setLocalDescription(...a){try{return await super.setLocalDescription(...a)}catch(e){console.error('RTC local:',e.message);throw e}}}});
  await page.goto(process.argv[2],{waitUntil:'domcontentloaded'});
  try{await page.waitForFunction(()=>[...document.querySelectorAll('figure video')].filter(v=>v.videoWidth===640&&!v.hidden).length===2,null,{timeout:30000})}
  catch(e){console.error(await page.evaluate(()=>({labels:[...document.querySelectorAll('figcaption')].map(e=>e.textContent),videos:[...document.querySelectorAll('video')].map(v=>({width:v.videoWidth,state:v.readyState,hidden:v.hidden})),peers:window.smokePeers.map(pc=>({ice:pc.iceConnectionState,state:pc.connectionState,gather:pc.iceGatheringState,remote:!!pc.remoteDescription}))})));throw e}
  await page.locator('figure').first().click();
  await page.waitForFunction(()=>document.querySelector('figure video').videoWidth===960&&!document.querySelector('figure video').hidden,null,{timeout:20000});
  const before=await page.locator('figure video').first().evaluate(v=>v.currentTime);
  await page.waitForFunction(t=>document.querySelector('figure video').currentTime>t+.5,before,{timeout:10000});
  await page.locator('#all').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('figure video')].filter(v=>v.videoWidth===640&&!v.hidden).length===2,null,{timeout:20000});
  await page.route('**/webrtc/**',route=>route.fulfill({status:503,body:'Synthetic relay unavailable'}));
  await page.locator('figure').first().click();
  await page.waitForFunction(()=>{
   const f=document.querySelector('figure'),im=f.querySelector('img'),v=f.querySelector('video');
   return v.hidden&&!im.hidden&&im.naturalWidth===640;
  },null,{timeout:20000});
  await page.locator('#gallery').click();
  assert.equal(await page.evaluate(()=>window.smokePeers.every(pc=>pc.connectionState==='closed')),true);
  console.log('PASS: real go2rtc/Chrome video, two-camera grid, enlargement, advancing video, grid return, relay-failure preview and gallery cleanup');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
