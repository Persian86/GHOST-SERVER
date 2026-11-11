// Exercise the actual admin script against a fake DOM; never contacts a server.
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const html=fs.readFileSync(path.join(__dirname,'../ghost_server/static/admin.html'),'utf8');
const source=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
new vm.Script(source);
const nodes=new Map(),node=key=>{if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',value:'',checked:false,classList:{toggle(){},add(){},remove(){}},style:{},addEventListener(){}});return nodes.get(key)};
const context={console,window:{addEventListener(){}},document:{querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),addEventListener(){},activeElement:null},
 location:{hostname:'localhost',origin:'http://localhost',protocol:'http:'},setInterval:()=>0,clearInterval(){},setTimeout:()=>0,
 fetch:async()=>({ok:true,status:200,json:async()=>({logged_in:false,needs_setup:false})}),confirm:()=>true,prompt:()=>null,Date,JSON};
vm.createContext(context);vm.runInContext(source,context);
const sample={people:[{id:1,name:'<img src=x>',preferences:{display_name:'" onfocus="bad',reply_length:'short',language:'auto',units:'metric',vocabulary:[{heard:'Zoe',intended:'</textarea><script>bad</script>'}]}}],
devices:[{id:'dev_one',name:'Personal <phone>',kind:'phone',person_id:1}],languages:{auto:'Follow the conversation'}};
context.sample=sample;
vm.runInContext(`api=async()=>sample;drawPersonal();`,context);
setImmediate(()=>{
 const form=node('#personalForms').innerHTML;
 assert(!form.includes('<script>bad') && form.includes('&lt;/textarea&gt;'));
 assert(form.includes('&quot; onfocus=&quot;bad'));
 assert(node('#personalDevices').innerHTML.includes('selected>&lt;img'));
 assert.deepEqual(JSON.parse(vm.runInContext(`JSON.stringify(vocabularyLines('Zoe → Zoey\\nparramata -> Parramatta'))`,context)),[{heard:'Zoe',intended:'Zoey'},{heard:'parramata',intended:'Parramatta'}]);
 for(const input of ['bad line','x → one\nx → two','x → one → two']){
  context.input=input;assert.throws(()=>vm.runInContext('vocabularyLines(input)',context));
 }
 console.log('PASS: full script syntax, profile/assignment rendering, HTML escaping and vocabulary parser validation');
});
