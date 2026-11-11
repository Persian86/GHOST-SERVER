// Verify the real admin script's opt-in setting, including failure rollback.
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const html=fs.readFileSync(path.join(__dirname,'../ghost_server/static/admin.html'),'utf8');
const source=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
const nodes=new Map(),node=k=>{if(!nodes.has(k))nodes.set(k,{innerHTML:'',textContent:'',checked:false,style:{},addEventListener(){},classList:{toggle(){},add(){},remove(){}}});return nodes.get(k)};
const ctx={console,window:{addEventListener(){}},document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){}},location:{hostname:'localhost',origin:'http://localhost',protocol:'http:'},setInterval:()=>0,setTimeout:()=>0,fetch:async()=>({ok:true,json:async()=>({logged_in:false})})};
vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
  assert(html.includes('id="handoffEnabled"') && html.includes('Turning this off clears pending recaps'));
  vm.runInContext(`data={settings:{}}; captured=null; api=async(url,opts)=>{captured={url,body:JSON.parse(opts.body)};return {};};`,ctx);
  await vm.runInContext('setHandoff(true)',ctx);
  assert.equal(vm.runInContext('captured.url',ctx),'/admin/api/settings');
  assert.equal(vm.runInContext('captured.body.handoff_enabled',ctx),true);
  assert(node('#handoffStatus').textContent.includes('enabled'));
  await vm.runInContext('setHandoff(false)',ctx);
  assert(node('#handoffStatus').textContent.includes('cleared'));
  vm.runInContext(`api=async()=>{throw Error('Save failed');}`,ctx);
  await vm.runInContext('setHandoff(true)',ctx);
  assert.equal(node('#handoffEnabled').checked,false);
  assert.equal(node('#handoffStatus').textContent,'Save failed');
  console.log('PASS: handoff opt-in, settings payload, off/clear message and error rollback');
})().catch(e=>{console.error(e);process.exitCode=1});
