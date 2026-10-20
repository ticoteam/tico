// No browser: exercise explicit creation and the stale-permission boundary around the existing bot API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

(async () => {
  let allowed = true, created = [], dialog;
  const submit = {}, status = {}, button = {dataset:{recruitCreate:'support'}};
  const results = {innerHTML:'',querySelectorAll:()=>[button],replaceChildren(){}};
  const form = {querySelector:()=>submit};
  const makeDialog = () => ({id:'',className:'',innerHTML:'',setAttribute(){},showModal(){},close(){},
    querySelectorAll:()=>[],querySelector:selector=>({form,'[data-recruit-status]':status,'[data-recruit-candidates]':results})[selector]});
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const values = {title:'Support teammate',brief:'Answer tickets using our team docs.',tools:'Mailbox',reviewer:'Support lead'};
  const context = vm.createContext({
    console,Set,FormData:class {get(name){return values[name];}},
    document:{createElement:()=>dialog=makeDialog(),body:{append(){}}},
    $:()=>undefined,esc:escape,S:{me:{id:'owner'},emps:[],route:'#/tasks'},SETTINGS:'#/settings',
    settingsCanCreateBots:()=>allowed,settingsShow(){},catalogCards:x=>x.cards,
    get:async()=>({cards:[{slug:'support',template:'support',name:'Support Agent',summary:'Answer support tickets',instructions:'Template rules'}]}),
    post:async(url,body)=>{created.push({url,body});return {};},refresh:async()=>{},loadSettings:async()=>{},toast(){},settingsEditBot(){}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/recruit.js'),'utf8'),context);
  await context.settingsRecruitFlow({title:'External handoff',brief:'A sufficiently long description',template:'support'});
  assert.equal(created.length,0,'opening an external handoff cannot create a bot');
  await form.onsubmit({preventDefault(){}});
  assert.equal(created.length,0,'reviewing candidates cannot create a bot');
  allowed=false;
  await button.onclick();
  assert.equal(created.length,0,'a permission change before selection prevents creation');
  allowed=true;
  await button.onclick();
  assert.equal(created.length,1);
  assert.equal(created[0].url,'/v2/bots');
  assert.equal(created[0].body.template,'support');
  assert.equal(created[0].body.status,'planned');
  assert.match(created[0].body.instructions,/Template rules[\s\S]*Answer tickets using our team docs/);
  assert.match(created[0].body.instructions,/Reviewer: Support lead/);
  assert.equal(created[0].body.operator,'owner');
  console.log('Recruit handoff, explicit creation and stale-permission checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
