const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const html=fs.readFileSync(path.join(__dirname,'../ghost_server/static/admin.html'),'utf8');
for(const match of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
const source=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].find(m=>m[1].includes('let breakinLoaded='))[1];
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,innerHTML:'',textContent:''});return elements.get(id)};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let saved=null,confirmCount=0;
const response={settings:{enabled:true,test_mode:true,lights:['light.hall'],sirens:['siren.house'],duration_s:120,home_address:'<img src=x onerror=bad()>',initiators:['phone'],recipients:['other']},devices:[{id:'phone',name:'<script>bad()</script>',ready:true,approved:true}],items:[]};
const ctx={console,Date,Number,JSON,esc,$:el,document:{querySelectorAll:()=>[]},helpLocation:loc=>esc(loc?.address||'Not shared'),confirm:()=>{confirmCount++;return false},api:async(url,opts)=>{if(opts?.method==='PUT'){saved=JSON.parse(opts.body);return {ok:true}}return response}};
vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
 await ctx.loadBreakIn();assert(el('#breakinDevices').innerHTML.includes('&lt;script&gt;'));assert(!el('#breakinDevices').innerHTML.includes('<script>bad'));
 el('#breakinAddress').value='unsaved address';await ctx.loadBreakIn();assert.equal(el('#breakinAddress').value,'unsaved address','Refresh clobbered unsaved configuration');
 el('#breakinTest').checked=false;await ctx.saveBreakIn();assert(confirmCount>0 && saved===null,'Live setup bypassed confirmation');
 el('#breakinTest').checked=true;el('#breakinDuration').value='120';await ctx.saveBreakIn();assert(saved.test_mode===true&&saved.lights[0]==='light.hall');
 response.items=[{id:'bi_'+'a'.repeat(24),source_name:'<b>private</b>',test:true,status:'active',created:1,location:{address:'<script>address</script>'},actions:[{entity:'siren.house',state:'unconfirmed'}],deliveries:[{name:'<script>recipient</script>',transport:'not_confirmed'}]}];
 await ctx.loadBreakIn();const output=el('#breakinHistory').innerHTML;assert(!output.includes('<script>'));assert(output.includes('Result unconfirmed')&&output.includes('Delivery unconfirmed'));
 console.log('PASS: all admin scripts parse; Break-in setup preserves drafts, escapes data, confirms live changes and reports uncertainty');
})().catch(e=>{console.error(e);process.exit(1)});
