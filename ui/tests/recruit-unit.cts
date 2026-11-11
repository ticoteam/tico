// No browser: exercise submit cancellation, explicit creation and the stale-permission boundary.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

// A bot creation the page's recruit flow posts to the hub.
interface Created {url: string; body: Record<string, string>}

(async () => {
  let allowed = true, created: Created[] = [], opened: string[] = [], dialog: unknown, resolveCatalog!: (value: unknown) => void,
    submitHandlerAtShow: ((event: {preventDefault(): void}) => unknown) | undefined;
  const submit = {}, status = {classList:{add(){},remove(){},toggle(){}}} as {classList: {add(): void; remove(): void; toggle(): void}; textContent?: string},
    button = {dataset:{recruitCreate:'support'}} as {dataset: {recruitCreate: string}; onclick?: () => Promise<void>};
  const results = {innerHTML:'',querySelectorAll:()=>[button],replaceChildren(){}};
  const form = {querySelector:()=>submit,elements:{title:{get value(){return values.title;},focus(){}},brief:{get value(){return values.brief;},focus(){}}}} as {
    querySelector(): unknown; elements: unknown; onsubmit?: (event: {preventDefault(): void}) => unknown};
  const makeDialog = (): typeof dialog => ({id:'',className:'',innerHTML:'',setAttribute(){},showModal(){
    submitHandlerAtShow=form.onsubmit;
  },close(){},addEventListener(){},
    querySelectorAll:()=>[],querySelector:(selector: string)=>({form,'[data-recruit-status]':status,'[data-recruit-candidates]':results} as Record<string, unknown>)[selector]});
  const escape = (value: unknown) => String(value).replace(/[&<>"']/g, (c: string) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'} as Record<string, string>)[c]!);
  const values: Record<string, string> = {title:'Support teammate',brief:'Answer tickets using our team docs.',tools:'Mailbox',reviewer:'Support lead'};
  const context = vm.createContext({
    console,Set,FormData:class {get(name: string){return values[name];}},
    document:{createElement:()=>dialog=makeDialog(),body:{append(){}}},
    $:()=>undefined,esc:escape,S:{me:{id:'owner'},emps:[],route:'#/tasks'},SETTINGS:'#/settings',
    settingsCanCreateBots:()=>allowed,settingsShow(){},settingsFieldClear(){},settingsFieldError(){},catalogCards:(x: {cards: unknown})=>x.cards,
    get:()=>new Promise(resolve=>{resolveCatalog=resolve;}),
    post:async(url: string, body: Record<string, string>)=>{created.push({url,body});return url==='/v2/seats'?{id:'seat-1'}:{};},refresh:async()=>{},loadSettings:async()=>{},toast(){},settingsEditBot(){}
  });
  // recruit.ts calls the shared helpers errorMessage (errors.ts) and eventElement (dom.ts); load them first, as index.html does.
  for (const helper of ['errors.js','dom.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../../build/ui/app',helper),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../build/ui/app/recruit.js'),'utf8'),context);
  context.recruitSeat=async (id: string)=>{opened.push(id);};
  const opening = context.settingsRecruitFlow({title:values.title,brief:values.brief,template:'support-owner'});
  assert.equal(typeof submitHandlerAtShow,'function','submission is intercepted before the form is shown');
  let prevented = false;
  form.onsubmit!({preventDefault(){prevented=true;}});
  assert.equal(prevented,true,'a valid prefilled form cannot default-submit while the catalog is pending');
  assert.equal(results.innerHTML,'','pending submission does not offer candidates');
  assert.equal(created.length,0,'pending submission cannot create a bot');
  resolveCatalog({cards:[{slug:'support',template:'support-owner',name:'Support Agent',summary:'Answer support tickets',instructions:'Template rules'}]});
  await opening;
  assert.equal(created.length,0,'opening an external handoff cannot create a bot');
  prevented=false;
  await form.onsubmit!({preventDefault(){prevented=true;}});
  assert.equal(prevented,true,'catalog-backed submission also cancels browser navigation');
  assert.match(results.innerHTML,/Choose & create Support Agent/);
  assert.equal(created.length,0,'reviewing candidates cannot create a bot');
  allowed=false;
  await button.onclick!();
  assert.equal(created.length,0,'a permission change before selection prevents creation');
  allowed=true;
  await button.onclick!();
  assert.equal(created.length,1);
  assert.equal(created[0]!.url,'/v2/bots');
  assert.equal(created[0]!.body.template,'support');
  assert.equal(created[0]!.body.status,'planned');
  assert.match(created[0]!.body.instructions!,/Template rules[\s\S]*Answer tickets using our team docs/);
  assert.match(created[0]!.body.instructions!,/Reviewer: Support lead/);
  assert.equal(created[0]!.body.operator,'owner');
  context.get=async()=>{throw new Error('Catalog unavailable');};
  await context.settingsRecruitFlow({title:values.title,brief:values.brief});
  assert.equal(status.textContent,'Catalog unavailable','catalog failure remains visible');
  prevented=false;
  await form.onsubmit!({preventDefault(){prevented=true;}});
  assert.equal(prevented,true,'catalog failure cannot restore browser submission');
  assert.equal(created.filter(c=>c.url==='/v2/bots').length,1,'catalog failure cannot create another bot');
  // Recruit candidates needs no catalog here: it makes a Recruiting seat and opens it (backend/recruiting.py).
  assert.deepEqual(created.slice(1).map(c=>[c.url,c.body.title]),[['/v2/seats','Support teammate']]);
  assert.deepEqual(opened,['seat-1'],'the new seat opens for its job description and candidates');
  console.log('Recruit submit cancellation, handoff, explicit creation and stale-permission checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
