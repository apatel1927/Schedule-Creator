// UI smoke tests using only Node built-ins (no browser dependencies).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const elements = new Map();
const element = sel => {
  if (!elements.has(sel)) elements.set(sel, {
    classList: {add() {}, remove() {}}, innerHTML: '', textContent: '', value: '',
  });
  return elements.get(sel);
};
const document = {
  querySelector: element, querySelectorAll: () => [], addEventListener() {},
  body: {style: {}},
};
const context = vm.createContext({document, window: {}, console, setTimeout() {}, clearTimeout() {}});
const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
// Exercise rendering helpers without booting a network session.
vm.runInContext(source.slice(0, source.indexOf("document.addEventListener('click'")), context);
vm.runInContext(`
  state.store='1';
  state.cursor=new Date('2026-11-02T12:00:00');
  state.data={
    stores:[{id:1,name:'Night Market',active:1,open_time:'10:00',close_time:'04:00',
      slot_minutes:480,break_minutes:30,staffing_json:'[5,0,0,0,0,0,0]',
      shift_templates_json:JSON.stringify([
        {id:'day',name:'Day',start_time:'10:00',end_time:'18:00',break_minutes:30,staffing:[2,0,0,0,0,0,0]},
        {id:'evening',name:'Evening',start_time:'20:00',end_time:'04:00',break_minutes:30,staffing:[3,0,0,0,0,0,0]}
      ])}],
    employees:[{id:1,name:'Alex',active:1}],
    employee_stores:[{employee_id:1,store_id:1,rate_cents:2000}],
    shifts:[{id:10,employee_id:1,store_id:1,shift_date:'2026-11-02',
      start_time:'20:00',end_time:'04:00',break_minutes:30,status:'draft'}],
    entries:[]
  };
`, context);
vm.runInContext('showStaffing(1)', context);
const form = element('#modal-content').innerHTML;
assert.match(form, /Store closes \(next day if earlier\)/);
assert.match(form, /name="block_evening_day_0"/);
assert.match(form, /value="3"/);
assert.match(form, /Add another shift \(e\.g\. evening\)/);

const schedule = vm.runInContext('renderWeekTable()', context);
assert.match(schedule, /8p–4a \(\+1 day\)/);
assert.match(schedule, /Add another/);

// Exercise the two creation forms that must never be inert for an owner.
vm.runInContext(`state.session={role:'owner',csrf:'qa'};showStore();`, context);
assert.match(element('#modal-content').innerHTML, /id="store-form"/);
assert.match(element('#modal-content').innerHTML, /name="color"/);
vm.runInContext('showEmployee()', context);
assert.match(element('#modal-content').innerHTML, /id="employee-form"/);
assert.match(element('#modal-content').innerHTML, /name="store_ids"/);
assert.match(element('#modal-content').innerHTML, /checked/);

// Verify actual API payloads assembled by the UI forms (including the selected store).
context.FormData = class {
  constructor(form) {this.form=form;}
  get(k) {return this.form.fields[k] ?? null;}
  has(k) {return this.form.fields[k] !== undefined;}
  getAll(k) {return this.form.fields[k] instanceof Array ? this.form.fields[k] : [];} 
};
vm.runInContext(`api=async(path,body)=>{globalThis.lastRequest={path,body};return {id:99};};
  refresh=async()=>{};toast=()=>{};closeModal=()=>{};`, context);
// Mimic real browsers: a <form> containing <input name="id"> exposes that input as
// form.id (named-property shadowing), so the form's own id must be read via getAttribute.
const fakeForm=(formId,fields)=>({id:{name:'id',value:fields.id},getAttribute:k=>k==='id'?formId:null,fields,elements:{}});
const storeForm=fakeForm('store-form',{id:'',name:'QA Store',location:'Test City',color:'#3c8d82'});
const employeeForm=fakeForm('employee-form',{id:'',name:'QA Employee',role:'Cashier',rate:'18.50',max_days:'5',store_ids:['1']});
// The async API calls use our mocked endpoint but the form payload is identical
// to the real requests, whose persistence is verified by the Python HTTP tests.
context.qaForm=storeForm;
async function verify() {
  context.lastRequest=null;
  await vm.runInContext('submitModal(qaForm)', context);
  assert.ok(context.lastRequest,'store form submit must call the API');
  assert.equal(context.lastRequest.path,'/api/stores');
  assert.equal(context.lastRequest.body.name,'QA Store');
  context.qaForm=employeeForm;
  await vm.runInContext('submitModal(qaForm)', context);
  assert.equal(context.lastRequest.path,'/api/employees');
  assert.equal(context.lastRequest.body.name,'QA Employee');
  assert.equal(context.lastRequest.body.rate,'18.50');
  assert.equal(context.lastRequest.body.store_ids.join(','),'1');
  console.log('PASS: store/employee forms, requests, overnight staffing, and add-another shift');
}
verify().catch(e=>{console.error(e);process.exitCode=1;});
