// Real admin script with a fake DOM and no network requests.
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const html=fs.readFileSync(path.join(__dirname,'../ghost_server/static/admin.html'),'utf8');
const source=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
new vm.Script(source);
const nodes=new Map(),node=k=>{if(!nodes.has(k))nodes.set(k,{innerHTML:'',textContent:'',value:'',checked:false,classList:{toggle(){},add(){},remove(){}},style:{},addEventListener(){}});return nodes.get(k)};
const context={console,window:{addEventListener(){}},document:{querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),addEventListener(){},activeElement:null},
 location:{hostname:'localhost',origin:'http://localhost',protocol:'http:'},setInterval:()=>0,clearInterval(){},setTimeout:()=>0,structuredClone,
 fetch:async()=>({ok:true,status:200,json:async()=>({logged_in:false,needs_setup:false})}),confirm:()=>true,prompt:()=>null,Date,JSON};
vm.createContext(context);vm.runInContext(source,context);
context.sample={devices:[{id:'dev_one',name:'<img src=x>',permitted:true}],routines:[{id:'rt_one',name:'<script>bad</script>',steps:[{kind:'briefing'}],enabled:true,device_id:''}],
 runs:[{id:'rr_one',name:'Test',status:'waiting_approval',steps:[{kind:'home'}],next_step:0,device_id:'dev_one',results:[{step:0,text:'<img src=x>'}]}]};
vm.runInContext('api=async()=>sample;drawRoutines();',context);
setImmediate(async()=>{
 assert(node('#routineList').innerHTML.includes('&lt;script&gt;')&&!node('#routineList').innerHTML.includes('<script>'));
 assert(node('#routineRuns').innerHTML.includes('&lt;img')&&!node('#routineRuns').innerHTML.includes('<img'));
 assert(node('#routineRuns').innerHTML.includes('Resume after owner approval'));
 vm.runInContext("routineDraft=[{kind:'timer',seconds:1500,label:'Focus'}]",context);
 node('#rs0minutes').value='25';node('#rs0label').value='Focus';node('#routineName').value='Unsaved edit';
 const step=JSON.parse(vm.runInContext('JSON.stringify(readRoutineDraft())',context));
 assert.deepEqual(step,[{kind:'timer',seconds:1500,label:'Focus'}]);
 await vm.runInContext('drawRoutines()',context);
 assert.equal(node('#routineName').value,'Unsaved edit','Background progress refresh must not wipe the editor');
 node('#rs0minutes').value='-1';assert.throws(()=>vm.runInContext('readRoutineDraft()',context));
 vm.runInContext("routineTemplate('work')",context);
 assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(routineDraft)',context)),[{kind:'briefing'},{kind:'timer',seconds:1500,label:'Focus'}]);
 assert(vm.runInContext("TABS.includes('Routines')",context));
 console.log('PASS: routine tab/editor, safe templates, escaped progress, timer parsing, approval controls and refresh preserves unsaved edits');
});
