// Syntax and rendering/escaping checks for the real admin JavaScript, no browser/server.
const fs=require('fs');const vm=require('vm');const path=require('path');const assert=require('assert');
const html=fs.readFileSync(path.join(__dirname,'../ghost_server/static/admin.html'),'utf8');
const scripts=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
scripts.forEach(s=>new vm.Script(s));
const source=scripts.join('\n');
const nodes=new Map();const node=key=>{if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',value:'',checked:false,classList:{toggle(){},add(){},remove(){}},style:{},addEventListener(){}});return nodes.get(key)};
const context={console,window:{addEventListener(){}},document:{querySelector:node,querySelectorAll:()=>[],getElementById:node,addEventListener(){},activeElement:null},
 location:{hostname:'localhost',origin:'http://localhost',protocol:'http:'},setInterval:()=>0,clearInterval(){},setTimeout:()=>0,
 fetch:async()=>({ok:true,status:200,json:async()=>({logged_in:false,needs_setup:false})}),confirm:()=>true,prompt:()=>null,Date,JSON};
vm.createContext(context);
vm.runInContext(source,context);
vm.runInContext(`
data={devices:[{id:'dev_test',name:'<img src=x onerror=alert(1)>',kind:'tablet',access_role:'guest'}]};
api=async function(url,options){
 if(url==='/admin/api/action-approvals')return {requests:[{id:'ap_test',name:'<script>alert(1)<\\/script>',operation:'device_action',arguments:'{"text":"<img src=x>"}',expires:Date.now()/1000+60}]};
 return {two_step:{on:true},settings:{household_access:true,approve_new_devices:true,security_alerts:true},lockouts_7d:0,sessions:[],pending:[],keys:[],log:[],tls:{enabled:false}};
};
drawSecurity();`,context);
setImmediate(()=>{
 const approvals=node('#actionApprovals').innerHTML;
 assert(approvals.includes('&lt;img') && !approvals.includes('<img'));
 assert(approvals.includes('Approve once') && approvals.includes('Decline'));
 assert(node('#accessDevices').innerHTML.includes('selected>Guest'));
 assert(node('#s_access').checked);
 console.log('PASS: complete admin script syntax, access-role rendering, escaped exact-argument preview, approval controls');
});
