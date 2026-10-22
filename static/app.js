/* ShiftLedger browser UI — no external scripts or third-party services. */
const state = {
  session: null, data: null, accounts: null, view: 'schedule',
  store: 'all', mode: 'week', scheduleLayout: 'table', cursor: new Date(), estimate: null,
  payrollStart: '', payrollEnd: '', payroll: null, payrollStore: 'all'
};
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const localDay = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const parseDay = str => new Date(`${str}T12:00:00`);
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const monday = d => addDays(d, -(d.getDay()+6)%7);
const shortDate = str => parseDay(str).toLocaleDateString(undefined, {month:'short',day:'numeric',year:'numeric'});
const monthLabel = d => d.toLocaleDateString(undefined, {month:'long',year:'numeric'});
const hours = n => (n/60).toFixed(2);
const money = n => (n / 100).toLocaleString('en-US', {style:'currency',currency:'USD'});
const time12 = s => { if (!s) return ''; const [h,m] = s.split(':').map(Number); return `${(h%12)||12}${m ? ':'+String(m).padStart(2,'0') : ''}${h<12?'a':'p'}`; };
const shiftLabel = s => `${time12(s.start_time)}–${time12(s.end_time)}${s.end_time<=s.start_time?' (+1 day)':''}`;
const timeMinute = s => {const [h,m]=s.split(':').map(Number);return h*60+m;};
const clockTime = n => `${String(Math.floor((n+1440)%1440/60)).padStart(2,'0')}:${String((n+1440)%60).padStart(2,'0')}`;
const shiftMins = s => {let [a,b]=[s.start_time,s.end_time].map(v=>{const [h,m]=v.split(':').map(Number);return h*60+m;});let duration=(b-a+1440)%1440;return Math.max(0,duration-(s.break_minutes||0));};
const isOwner = () => state.session?.role === 'owner';
const getStore = id => state.data?.stores.find(s => s.id === Number(id));
const getEmployee = id => state.data?.employees.find(e => e.id === Number(id));
const avatar = (employee, extra='') => `<div class="avatar a${(employee?.id||0)%4} ${extra}">${esc((employee?.name||'?').split(/\s+/).slice(0,2).map(w=>w[0]).join('').toUpperCase())}</div>`;
const badge = (label, color='') => `<span class="badge ${color}">${esc(label)}</span>`;
const empty = (title, detail, action='', label='') => `<div class="empty"><div class="empty-icon">▦</div><h3>${esc(title)}</h3><p>${esc(detail)}</p>${action?`<button class="btn primary" data-action="${esc(action)}">＋ ${esc(label)}</button>`:''}</div>`;
const fmtMonthDay = d => d.toLocaleDateString(undefined,{month:'short',day:'numeric'});

async function api(path, body) {
  const options = body === undefined ? {credentials:'same-origin'} : {
    method:'POST', credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':state.session?.csrf||''},body:JSON.stringify(body)
  };
  let response;
  try { response=await fetch(path,options); } catch { throw Error('Cannot connect to the server. Check your Unraid container.'); }
  let payload;
  try { payload=await response.json(); } catch { throw Error('Server returned an invalid response'); }
  if (!response.ok) {
    if (response.status===401 && path!=='/api/login') logoutLocal();
    throw Error(payload.error || 'Request failed');
  }
  return payload;
}
function toast(message,error=false) {
  const el=$('#toast'); el.textContent=message;el.className='toast'+(error?' error':'');
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.add('hidden'),3900);
}
function logoutLocal() {
  state.session=null; state.data=null;$('#app').classList.add('hidden');$('#login-screen').classList.remove('hidden');
  $('#login-pass').value='';
}
async function loadData() {
  state.data=await api('/api/data');
  if(!state.data.stores.some(s=>String(s.id)===String(state.store))) {
    state.store=String(state.data.stores.find(s=>s.active)?.id||'all');
  }
}
async function loadEstimate() {
  const mondayDay=localDay(monday(state.cursor));
  const q=new URLSearchParams({week_start:mondayDay});
  if(state.store!=='all')q.set('store_id',state.store);
  state.estimate=await api('/api/schedule/estimate?'+q);
}
async function start() {
  $('#header-today').textContent=new Date().toLocaleDateString(undefined,{weekday:'short',month:'long',day:'numeric',year:'numeric'});
  const today=new Date(); state.payrollEnd=localDay(today); state.payrollStart=localDay(addDays(today,-13));
  try {const session=await api('/api/session'); if (!session.authenticated) return;state.session=session;await loadData();try{await loadEstimate();}catch{state.estimate=null;}showApp();}
  catch(e){toast(e.message,true);}
}
function showApp() {
  $('#login-screen').classList.add('hidden');$('#app').classList.remove('hidden');
  $('#current-user').textContent=state.session.username;
  $('#current-role').textContent=isOwner()?'Owner account':'Store manager';
  $('#user-avatar').textContent=state.session.username[0].toUpperCase();
  $$('.owner-only').forEach(el=>el.classList.toggle('hidden',!isOwner()));
  if (state.view==='payroll'&&!isOwner()) state.view='schedule';
  render();
}
function allStores() {return state.data?.stores||[];}
function visibleShifts() {return state.data.shifts.filter(s=>state.store==='all'||s.store_id===Number(state.store));}
function visibleEntries() {return state.data.entries.filter(e=>state.store==='all'||e.store_id===Number(state.store));}
function storeOptions(selected='all',includeAll=true) {return (includeAll?'<option value="all">All stores</option>':'')+allStores().filter(s=>s.active||String(s.id)===String(selected)).map(s=>`<option value="${s.id}" ${String(s.id)===String(selected)?'selected':''}>${esc(s.name)}</option>`).join('');}
function heading(eyebrow,title,desc,buttons='') {return `<div class="page-heading"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p>${esc(desc)}</p></div><div class="heading-actions">${buttons}</div></div>`;}
function statistic(label,number,caption,icon,variant='') {return `<div class="stat-card"><div class="stat-top">${esc(label)}<span class="stat-icon ${variant}">${icon}</span></div><div class="stat-number">${esc(number)}</div><div class="stat-sub">${esc(caption)}</div></div>`;}
function commonStoreSelector() {return `<select id="global-store" aria-label="Filter by store">${storeOptions(state.store)}</select>`;}
function render() {
  if (!state.data) return;
  $$('.nav-link').forEach(el=>el.classList.toggle('active',el.dataset.view===state.view));
  const titles={overview:'Overview',schedule:'Schedule',employees:'Employees',availability:'Availability',time:'Time & tasks',payroll:'Payroll',stores:'Stores & settings',managers:'Manager logins'};
  $('#crumb-page').textContent=titles[state.view];
  $('#page').innerHTML=({overview:renderOverview,schedule:renderSchedule,employees:renderEmployees,availability:renderAvailability,time:renderTime,payroll:renderPayroll,stores:renderStores,managers:renderManagers}[state.view]||renderSchedule)();
}
function renderOverview() {
  const now=new Date(), first=localDay(monday(now)), last=localDay(addDays(monday(now),6));
  const shifts=visibleShifts().filter(s=>s.shift_date>=first&&s.shift_date<=last);
  const logged=visibleEntries().filter(e=>e.entry_date>=first&&e.entry_date<=last);
  const staff=new Set(state.data.employee_stores.filter(x=>state.store==='all'||x.store_id===Number(state.store)).map(x=>x.employee_id));
  const upcoming=visibleShifts().filter(s=>s.shift_date>=localDay(now)).slice(0,7);
  const latest=visibleEntries().slice().sort((a,b)=>b.entry_date.localeCompare(a.entry_date)||b.id-a.id).slice(0,6);
  return heading('GOOD TO SEE YOU','Team overview','A quick look at what is happening across your locations.',commonStoreSelector())+
    `<div class="stats-grid">${statistic('Scheduled shifts',shifts.length,'This Monday–Sunday','▦')}${statistic('Planned hours',hours(shifts.reduce((x,s)=>x+shiftMins(s),0)),'Future schedule, not payroll','◷','blue')}${statistic('Actual hours',hours(logged.reduce((x,e)=>x+e.minutes,0)),'Recorded hours this week','↗','orange')}${statistic('Team members',staff.size,'Assigned to selected stores','♙','purple')}</div>
    <div class="dash-layout"><section class="card"><div class="card-header"><div><h3>Coming up next</h3><p>Upcoming shifts across your selected locations</p></div><button class="text-btn" data-view="schedule">View calendar →</button></div><div class="card-inner">${upcoming.length?upcoming.map(s=>{const e=getEmployee(s.employee_id);return `<div class="list-line"><div class="date-square"><span>${parseDay(s.shift_date).toLocaleDateString('en',{weekday:'short'}).toUpperCase()}</span><strong>${s.shift_date.slice(-2)}</strong></div>${avatar(e)}<div class="grow"><strong>${esc(e?.name||'Unknown')}</strong><small>${esc(getStore(s.store_id)?.name||'')} · ${time12(s.start_time)}–${time12(s.end_time)}</small></div>${badge(s.status,s.status==='draft'?'draft':'')}</div>`}).join(''):empty('No upcoming shifts','Build your first schedule using the calendar.','new-shift','Add shift')}</div></section>
    <section class="card"><div class="card-header"><div><h3>Recently logged hours</h3><p>Shifts and additional paid tasks</p></div><button class="text-btn" data-view="time">View all →</button></div><div class="card-inner">${latest.length?latest.map(x=>`<div class="list-line">${avatar(getEmployee(x.employee_id))}<div class="grow"><strong>${esc(getEmployee(x.employee_id)?.name||'Unknown')}</strong><small>${esc(x.category)} · ${shortDate(x.entry_date)}</small></div><strong>${hours(x.minutes)} h</strong></div>`).join(''):empty('No hours logged','When employees work, record actual hours here.','new-entry','Log hours')}</div></section></div>`;
}
function getRange() {
  if (state.mode==='week') {const first=monday(state.cursor);return {first,last:addDays(first,6),label:`${fmtMonthDay(first)} – ${fmtMonthDay(addDays(first,6))}, ${addDays(first,6).getFullYear()}`,days:7};}
  const first=new Date(state.cursor.getFullYear(),state.cursor.getMonth(),1,12);
  const start=monday(first), end=new Date(first.getFullYear(),first.getMonth()+1,0,12),last=addDays(start,Math.ceil((Math.round((end-start)/86400000)+1)/7)*7-1);
  return {first:start,last,label:monthLabel(state.cursor),days:Math.round((last-start)/86400000)+1};
}
function renderCalendar() {
  const {first,days}=getRange();const shifts=visibleShifts();const today=localDay(new Date());
  let cells=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(day=>`<div class="day-name">${day}</div>`).join('');
  for(let i=0;i<days;i++){
    const day=addDays(first,i),iso=localDay(day),off=state.mode==='month'&&day.getMonth()!==state.cursor.getMonth();
    const dayShifts=shifts.filter(s=>s.shift_date===iso);const max=state.mode==='week'?20:3;
    cells+=`<div class="day-cell ${off?'outside':''} ${iso===today?'today':''} ${state.mode==='week'?'week':''}"><div class="day-number"><span class="date-pill">${day.getDate()}</span><button class="day-add" data-action="new-shift" data-date="${iso}" title="Add shift on ${iso}">＋</button></div>${dayShifts.slice(0,max).map(s=>{let e=getEmployee(s.employee_id),store=getStore(s.store_id);let color=store?.color||'#238e7a';return `<button class="shift-pill ${s.status==='draft'?'draft':''}" style="border-left-color:${esc(color)}" data-action="edit-shift" data-id="${s.id}"><span class="shift-person">${esc(e?.name||'Employee')}</span><span class="shift-time">${shiftLabel(s)}</span></button>`;}).join('')}${dayShifts.length>max?`<button class="shift-more text-btn" data-action="view-day" data-date="${iso}">+${dayShifts.length-max} more — view day</button>`:''}</div>`;
  }
  return `<div class="calendar-grid">${cells}</div>`;
}
function renderWeekTable() {
  const beginning=monday(state.cursor);
  const days=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
  const shownStores=allStores().filter(s=>state.store==='all'||s.id===Number(state.store));
  return shownStores.map(store=>{
    const shifts=visibleShifts().filter(s=>s.store_id===store.id&&s.shift_date>=localDay(beginning)&&s.shift_date<=localDay(addDays(beginning,6)));
    const rows=days.map((day,i)=>{
      const date=localDay(addDays(beginning,i)), daily=shifts.filter(s=>s.shift_date===date);
      return (daily.length?daily:[null]).map((shift,index)=>`<tr><td><strong>${index===0?day:''}</strong><small>${index===0?shortDate(date):''}</small></td><td>${shift?esc(getEmployee(shift.employee_id)?.name||'Unknown employee'):'<span class="muted">No shifts</span>'}</td><td>${shift?shiftLabel(shift):'—'}</td><td>${shift?hours(shiftMins(shift)):'—'}</td><td>${shift?badge(shift.status,shift.status==='draft'?'draft':''):'—'}</td><td class="actions no-print">${shift?`<button class="text-btn" data-action="edit-shift" data-id="${shift.id}">Edit</button> ${index===daily.length-1?`<button class="text-btn" data-action="new-shift" data-date="${date}" data-store="${store.id}">＋ Add another</button>`:''}`:`<button class="text-btn" data-action="new-shift" data-date="${date}" data-store="${store.id}">＋ Add shift</button>`}</td></tr>`).join('');
    }).join('');
    return `<section class="card staffweek-table"><div class="staffweek-table-header"><h3>${esc(store.name)}</h3><span>${shifts.length} shifts · ${hours(shifts.reduce((sum,s)=>sum+shiftMins(s),0))} paid hours</span></div><div class="table-card"><table class="data-table"><thead><tr><th>Day / date</th><th>Employee</th><th>Shift</th><th>Hours</th><th>Status</th><th class="no-print">Change</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }).join('')||empty('No stores available','Create a store to start planning the weekly schedule.','new-store','Add store');
}
function renderSchedule() {
  const range=getRange();const count=visibleShifts().filter(s=>s.shift_date>=localDay(range.first)&&s.shift_date<=localDay(range.last)).length;
  const estimate=state.estimate, week=localDay(monday(state.cursor));
  const help=state.store==='all'?'Select one store above to auto-generate shifts for that location.':'Generate draft shifts from your store staffing setup, then click any shift to change the assigned employee or hours.';
  const setupActions=allStores().length?`<button class="btn" data-action="staffing">⚙ Staffing setup</button><button class="btn primary" data-action="generate-week">✦ Generate week</button>`:'<button class="btn primary" data-view="stores">＋ Create a store</button>';
  return heading('STAFFWEEK SCHEDULER','Schedule','A dated weekly schedule for each store. Auto-generate a week, then make any changes you need.',`<button class="btn secondary no-print" data-action="print-schedule">Print / Save PDF</button><button class="btn no-print" data-action="bulk">↻ Repeating shifts</button><button class="btn no-print" data-action="new-shift">＋ Add shift</button>`)+
  `<div class="filter-strip"><span class="filter-label">SCHEDULE STORE</span>${commonStoreSelector()}<span class="tab-note">${esc(help)}</span><div class="spacer"></div>${setupActions}</div>
  <div class="stats-grid">${statistic('Week of',shortDate(week),'Monday–Sunday','▦')}${statistic('Scheduled shifts',estimate?.shift_count??'—',`${estimate?.draft_count??0} drafts this week`,'♙')}${statistic('Scheduled hours',estimate?hours(estimate.planned_minutes):'—','Planned paid hours, less unpaid breaks','◷','blue')}${statistic('Estimated weekly wages',estimate?money(estimate.estimated_base_cents):'—','Straight-time wages only; excludes overtime, taxes & extras','＄','purple')}</div>
  ${estimate?.unfilled_slots>0?`<div class="manager-permissions"><strong>${estimate.unfilled_slots} staffing slots not scheduled.</strong> Generate the week to fill openings based on the store template and available employees. Shift changes are never overwritten.</div>`:''}`+
  `<section class="card calendar-card no-print"><div class="calendar-toolbar"><div class="toolbar-group"><div class="calendar-month-title">${state.scheduleLayout==='table'?esc(`${fmtMonthDay(monday(state.cursor))} – ${fmtMonthDay(addDays(monday(state.cursor),6))}, ${addDays(monday(state.cursor),6).getFullYear()}`):esc(range.label)}</div><div class="calendar-nav"><button class="icon-btn" data-action="prev" title="Previous">‹</button><button class="icon-btn" data-action="today" title="Today">◎</button><button class="icon-btn" data-action="next" title="Next">›</button></div><input id="week-jump" type="date" value="${localDay(monday(state.cursor))}" aria-label="Jump to any week" title="Jump to any week"></div><div class="toolbar-group"><div class="segmented"><button data-action="view-table" class="${state.scheduleLayout==='table'?'active':''}">Weekly table</button><button data-action="view-calendar" class="${state.scheduleLayout==='calendar'?'active':''}">Calendar</button></div></div>${state.scheduleLayout==='calendar'?`<div class="segmented"><button data-action="calendar-week" class="${state.mode==='week'?'active':''}">Week</button><button data-action="calendar-month" class="${state.mode==='month'?'active':''}">Month</button></div>`:''}</div></section>
  <div id="schedule-print-content">${state.scheduleLayout==='table'?renderWeekTable():`<section class="card calendar-card">${renderCalendar()}<div class="calendar-footer"><div class="legend"><span><i></i> Published</span><span><i class="draft"></i> Draft</span></div><span>${count} shift${count===1?'':'s'} in this view</span></div></section>`}</div>
  <div class="filter-strip" style="margin-top:15px"><span class="tab-note">Estimate is based on scheduled shifts at current store rates. Actual payroll includes separately logged shopping, pulling, and other paid tasks.</span><div class="spacer"></div><button class="btn" data-action="publish-range">✓ Publish visible dates</button></div>`;
}
function renderEmployees() {
  const employees=state.data.employees.filter(e=>state.store==='all'||state.data.employee_stores.some(es=>es.employee_id===e.id&&es.store_id===Number(state.store)));
  const addBtn='<button class="btn primary" data-action="new-employee">＋ Add employee</button>';
  return heading('THE PEOPLE BEHIND THE WORK','Employees','Manage your team, store assignments and hourly pay rates.',`${commonStoreSelector()}${addBtn}`)+
  `<div class="card table-card">${employees.length?`<table class="data-table"><thead><tr><th>Employee</th><th>Assigned stores</th><th>Hourly rate by store</th><th>Max days / week</th><th>Status</th><th></th></tr></thead><tbody>${employees.map(e=>{const assoc=state.data.employee_stores.filter(x=>x.employee_id===e.id);return `<tr><td><div class="table-person">${avatar(e)}<div><strong>${esc(e.name)}</strong><small>${esc(e.role||'Team member')}</small></div></div></td><td>${esc(assoc.map(x=>getStore(x.store_id)?.name).filter(Boolean).join(' · '))}</td><td>${assoc.map(x=>`<div style="margin:3px 0;white-space:nowrap"><strong class="currency">${money(x.rate_cents??e.rate_cents)}/hr</strong><span class="inline-detail"> · ${esc(getStore(x.store_id)?.name||'')}</span> <button class="text-btn" data-action="edit-rate" data-employee="${e.id}" data-store="${x.store_id}">Edit rate</button></div>`).join('')}</td><td>${Number(e.max_days||5)}</td><td>${badge(e.active?'Active':'Inactive',e.active?'':'gray')}</td><td class="actions"><button class="text-btn" data-action="edit-employee" data-id="${e.id}">Edit →</button></td></tr>`}).join('')}</tbody></table>`:empty('No employees yet','Add team members, then assign them to one or more stores.','new-employee', 'Add employee')}</div>`;
}
function availableDays(employee) {
  try {const days=JSON.parse(employee.availability_json);return Array.isArray(days)&&days.length===7?days:[1,1,1,1,1,1,1];}
  catch{return [1,1,1,1,1,1,1];}
}
function renderAvailability() {
  const people=state.data.employees.filter(e=>e.active&&(state.store==='all'||state.data.employee_stores.some(es=>es.employee_id===e.id&&es.store_id===Number(state.store))));
  const days=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
  return heading('STAFFWEEK AVAILABILITY','Weekly availability','Select the weekdays each employee can work. Auto-scheduling and manual shifts respect these settings.',commonStoreSelector())+
    `<div class="staffweek-hint">Availability is shared across all stores assigned to an employee. The maximum number of workdays per week is configured under Employees.</div>
    ${people.length?people.map(e=>`<section class="card availability-panel"><div class="availability-heading">${avatar(e)}<div><h3>${esc(e.name)}</h3><p>${esc(e.role||'Team member')} · Up to ${Number(e.max_days||5)} days per week</p></div></div><div class="availability-week">${days.map((day,i)=>`<label><input name="available_day" type="checkbox" data-employee="${e.id}" data-weekday="${i}" ${availableDays(e)[i]?'checked':''}><span>${day}</span></label>`).join('')}</div></section>`).join(''):empty('No employees assigned','Create an employee and assign them to this store to set availability.','new-employee','Add employee')}`;
}
function renderTime() {
  const entries=visibleEntries().slice().sort((a,b)=>b.entry_date.localeCompare(a.entry_date)||b.id-a.id);
  const sum=entries.reduce((n,e)=>n+e.minutes,0);
  return heading('EVERY HOUR COUNTS','Time & tasks','Record hours actually worked—scheduled shifts, shopping, pulling orders and more.',`<button class="btn primary" data-action="new-entry">＋ Log hours</button>`)+
  `<div class="stats-grid">${statistic('Recorded entries',entries.length,'All dates in the selected stores','▤')}${statistic('Total recorded hours',hours(sum),'Includes all paid work categories','◷','blue')}${statistic('Extra task hours',hours(entries.filter(e=>e.category!=='Shift').reduce((n,e)=>n+e.minutes,0)),'Shopping, pulling, deliveries & more','↗','orange')}${statistic('Employees with hours',new Set(entries.map(e=>e.employee_id)).size,'Unique employees','♙','purple')}</div><div class="filter-strip"><span class="filter-label">LOCATION</span>${commonStoreSelector()}<div class="spacer"></div><span class="small-text muted">Payroll uses actual recorded time, never just planned shifts.</span></div>
  <div class="card table-card">${entries.length?`<table class="data-table"><thead><tr><th>Employee</th><th>Date / store</th><th>Work type</th><th>Time / duration</th><th>Hours</th><th></th></tr></thead><tbody>${entries.map(e=>`<tr><td><div class="table-person">${avatar(getEmployee(e.employee_id))}<div><strong>${esc(getEmployee(e.employee_id)?.name||'Unknown')}</strong><small>${esc(getEmployee(e.employee_id)?.role||'')}</small></div></div></td><td><strong>${shortDate(e.entry_date)}</strong><small>${esc(getStore(e.store_id)?.name||'')}</small></td><td>${badge(e.category,e.category==='Shift'?'':'orange')}${e.notes?`<small>${esc(e.notes)}</small>`:''}</td><td>${e.start_time?`${time12(e.start_time)}–${time12(e.end_time)}${e.break_minutes?`<small>${e.break_minutes}m unpaid break</small>`:''}`:'Manual hours'}</td><td><strong>${hours(e.minutes)}</strong></td><td class="actions"><button class="text-btn" data-action="edit-entry" data-id="${e.id}">Edit →</button></td></tr>`).join('')}</tbody></table>`:empty('No time entries yet','Log worked shifts or paid tasks like shopping and pulling orders.','new-entry','Log hours')}</div>`;
}
function renderPayroll() {
  if(!isOwner()) return empty('Owner only','Payroll estimates are restricted to the owner.');
  const p=state.payroll;
  const rows=p?.employees||[];
  return heading('CLEAR NUMBERS, EVERY PAY PERIOD','Payroll estimate','Review actual paid hours across all stores, including extra tasks.',`<button class="btn secondary no-print" data-action="print-payroll">Print / Save PDF</button><button class="btn no-print" data-action="csv-payroll">↓ Export CSV</button>`)+
  `<div class="card" style="padding:20px;margin-bottom:17px"><div class="filter-strip" style="margin:0"><span class="filter-label">FROM</span><input type="date" id="payroll-start" value="${esc(state.payrollStart)}" aria-label="Payroll start date" style="padding:9px"><span class="filter-label">TO</span><input type="date" id="payroll-end" value="${esc(state.payrollEnd)}" aria-label="Payroll end date" style="padding:9px"><span class="filter-label">STORE</span><select id="payroll-store">${storeOptions(state.payrollStore)}</select><button class="btn primary" data-action="calculate-pay">Calculate pay →</button></div></div>
  <div class="stats-grid">${statistic('Employees paid',rows.length,'With recorded hours','♙')}${statistic('Total hours',hours(p?.total_minutes||0),'Actual hours in date range','◷','blue')}${statistic('Overtime hours',hours(p?.total_overtime_minutes||0),'Beyond 40 per Monday–Sunday week','↗','orange')}${statistic('Estimated gross pay',money(p?.total_gross_cents||0),'Before deductions and taxes','＄','purple')}</div>
  <div class="card table-card">${rows.length?`<table class="data-table"><thead><tr><th>Employee</th><th>Total hours</th><th>OT hours</th><th>Base wages</th><th>OT premium</th><th>Gross estimate</th></tr></thead><tbody>${rows.map(r=>`<tr><td><div class="table-person">${avatar(getEmployee(r.employee_id))}<div><strong>${esc(r.name)}</strong><small>${r.entry_count} time records</small></div></div></td><td><strong>${hours(r.minutes)}</strong></td><td>${hours(r.overtime_minutes)}</td><td>${money(r.base_cents)}</td><td>${money(r.premium_cents)}</td><td><strong>${money(r.gross_cents)}</strong></td></tr>`).join('')}</tbody></table>`:empty('No payroll entries in this range','Record worked hours in Time & tasks, then calculate the period.','new-entry','Log hours')}</div>
  <p class="payroll-note"><strong>Gross-pay estimate only.</strong> Overtime preview assumes Monday–Sunday workweeks, combines hours across stores, and applies a weighted regular-rate calculation for hours beyond 40 per week. It does not calculate taxes, tips, bonuses, deductions, exemptions or jurisdiction-specific rules. Review with your payroll provider before paying employees.</p>`;
}
function renderStores() {
  const cards=allStores().map(s=>{const people=state.data.employee_stores.filter(e=>e.store_id===s.id).length;
    return `<div class="card store-card"><div class="store-head"><div class="store-mark" style="background:${esc(s.color)}">⌂</div>${badge(s.active?'Active':'Inactive',s.active?'':'gray')}</div><h3>${esc(s.name)}</h3><p>${esc(s.location||'No location details added')}</p><div class="store-meta"><span>${people} assigned employee${people===1?'':'s'}</span><button class="text-btn" data-action="edit-store" data-id="${s.id}">Edit store →</button><button class="text-btn" data-action="staffing" data-store="${s.id}">Staffing setup →</button></div></div>`}).join('');
  return heading('ONE HOME FOR EVERY LOCATION','Your stores','Set up locations and assign each team its weekly staffing plan.','<button class="btn primary" data-action="new-store">＋ Add store</button>')+
    (isOwner()?'<div class="manager-permissions">Signed in as <strong>Owner</strong>. You can create stores here, then create manager logins under <strong>Managers & access</strong> and assign each manager to one or more stores.</div>':'<div class="manager-permissions">You are signed in as a <strong>Manager</strong>. You can add stores (you get access automatically), add and edit employees, and edit staffing for your stores. Only the owner manages login accounts and payroll.</div>')+
    `<div class="store-grid">${cards||empty('No stores available','Create your first store.','new-store','Add store')}</div>`;
}
function renderManagers() {
  if(!isOwner()) return '';
  const users=state.accounts?.users?.filter(u=>u.role==='manager')||[];
  const links=state.accounts?.user_stores||[];
  return heading('GIVE THE RIGHT ACCESS','Managers & access','Create logins and decide which locations each manager can schedule.',`<button class="btn primary" data-action="new-manager">＋ Add manager</button>`)+
   `<div class="manager-permissions"><strong>Owner:</strong> all stores, employees, wages, payroll and manager accounts.<br><strong>Manager:</strong> can add stores and employees, and manage schedules, hours and pay rates for their stores. Payroll reports and manager-account administration stay owner-only.</div>
   <div class="card table-card">${users.length?`<table class="data-table"><thead><tr><th>Manager account</th><th>Store access</th><th>Status</th><th></th></tr></thead><tbody>${users.map(u=>`<tr><td><div class="table-person"><div class="avatar a3">${esc(u.username[0].toUpperCase())}</div><div><strong>${esc(u.username)}</strong><small>Manager login</small></div></div></td><td>${esc(links.filter(x=>x.user_id===u.id).map(x=>getStore(x.store_id)?.name).filter(Boolean).join(' · '))}</td><td>${badge(u.active?'Active':'Disabled',u.active?'':'gray')}</td><td class="actions"><button class="text-btn" data-action="edit-manager" data-id="${u.id}">Edit →</button></td></tr>`).join('')}</tbody></table>`:empty('No managers added','Create a manager login to delegate schedules and time entry.','new-manager','Add manager')}</div>
   <div style="margin-top:18px"><button class="btn" data-action="change-password">⚿ Change my password</button></div>`;
}

function openModal(title, markup) {
  $('#modal-title').textContent=title;
  $('#modal-content').innerHTML=`<div class="modal-body"><div id="modal-error" class="form-error hidden" role="alert"></div>${markup}</div>`;
  $('#modal-root').classList.remove('hidden');
  document.body.style.overflow='hidden';
  setTimeout(()=>$('#modal-content input:not([type=hidden]),#modal-content select')?.focus(),0);
}
function closeModal(){ $('#modal-root').classList.add('hidden');$('#modal-content').innerHTML='';document.body.style.overflow=''; }
const input = (name,label,value='',type='text',attrs='') => `<label for="f-${name}">${esc(label)}</label><input id="f-${name}" name="${name}" type="${type}" value="${esc(value)}" ${attrs}>`;
const selectField=(name,label,options)=>`<label for="f-${name}">${esc(label)}</label><select id="f-${name}" name="${name}">${options}</select>`;
const textArea=(name,label,value='')=>`<label for="f-${name}">${esc(label)}</label><textarea id="f-${name}" name="${name}" maxlength="500">${esc(value)}</textarea>`;
const footer = (label,extra='')=>`<div class="modal-footer">${extra}<button class="btn" type="button" data-action="close-modal">Cancel</button><button class="btn primary" type="submit">${esc(label)} →</button></div>`;
const hourChoices = ['Shift','Shopping','Pulling orders','Delivery','Restocking','Training','Inventory','Other'];
function storeSelectField(selected) {
  const options=allStores().filter(s=>s.active||s.id===selected).map(s=>`<option value="${s.id}" ${s.id===selected?'selected':''}>${esc(s.name)}</option>`).join('');
  return selectField('store_id','Store',options);
}
function employeeChoices(store,selected) {
  const emps=state.data.employees.filter(e=>e.active||e.id===selected).filter(e=>state.data.employee_stores.some(x=>x.employee_id===e.id&&x.store_id===Number(store)));
  return emps.map(e=>`<option value="${e.id}" ${e.id===selected?'selected':''}>${esc(e.name)}</option>`).join('');
}
function employeesSelect(store,selected){return selectField('employee_id','Employee',employeeChoices(store,selected));}
function statusChoice(value='draft'){return selectField('status','Schedule status',`<option value="draft" ${value==='draft'?'selected':''}>Draft — not finalized</option><option value="published" ${value==='published'?'selected':''}>Published</option>`);}
function ensureStaffFor(store) {
  if(state.data.employee_stores.some(x=>x.store_id===Number(store)&&getEmployee(x.employee_id)?.active))return true;
  state.view='employees';render();
  toast('Add an employee assigned to this store first. The employee form is opening.',true);
  showEmployee();
  return false;
}
function ensureActiveStore() {
  if(allStores().some(x=>x.active))return true;
  state.view='stores';render();
  toast('Create a store first; the store form is opening.',true);
  showStore();
  return false;
}
function showShift(s,preferredDate='',preferredStore) {
  if (!ensureActiveStore())return;
  const store=s?.store_id|| preferredStore || (state.store!=='all'?Number(state.store):allStores().find(x=>x.active)?.id);
  if(!ensureStaffFor(store))return;
  const initial=preferredDate||s?.shift_date||localDay(new Date());
  openModal(s?'Edit shift':'Schedule a shift',`<form id="shift-form"><input type="hidden" name="id" value="${s?.id||''}"><div class="two-col"><div>${storeSelectField(store)}</div><div>${input('date','Shift date',initial,'date','required')}</div></div>${employeesSelect(store,s?.employee_id)}<div class="two-col"><div>${input('start_time','Start time',s?.start_time||getStore(store)?.open_time||'09:00','time','required')}</div><div>${input('end_time','End time',s?.end_time||'17:00','time','required')}</div></div><p class="helper">If the shift ends after midnight, choose the next-day clock time (example: 8 PM to 4 AM). To schedule a second employee at the same time, use Add another on the weekly table.</p>${input('break_minutes','Unpaid break (minutes)',s?.break_minutes||0,'number','min="0" max="600" required')}${statusChoice(s?.status)}${textArea('notes','Notes (optional)',s?.notes||'')}${footer(s?'Save changes':'Create shift',s?`<button class="btn danger delete" type="button" data-action="delete-shift" data-id="${s.id}">Archive shift</button>`:'')}${s?`<button class="btn" style="width:100%;margin-top:12px" type="button" data-action="record-shift" data-id="${s.id}">◷ Record these hours as worked</button>`:''}</form>`);
}
function showBulk() {
  if (!ensureActiveStore())return;
  const store=state.store==='all'?allStores().find(x=>x.active)?.id:Number(state.store);
  if(!ensureStaffFor(store))return;
  const start=localDay(new Date()),end=localDay(addDays(new Date(),55));
  const names=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  openModal('Build repeating shifts',`<form id="bulk-form"><p class="tab-note">Choose an employee and the weekdays they work. Create up to 184 days of shifts in one step. Exact duplicate shifts are skipped.</p><div class="two-col"><div>${storeSelectField(store)}</div><div>${employeesSelect(store)}</div></div><div class="two-col"><div>${input('start_date','From date',start,'date','required')}</div><div>${input('end_date','Through date',end,'date','required')}</div></div><label>Repeat on these weekdays</label><div class="check-row">${names.map((n,i)=>`<label><input name="weekdays" type="checkbox" value="${i}" ${i<5?'checked':''}>${n}</label>`).join('')}</div><div class="two-col"><div>${input('start_time','Start time','09:00','time','required')}</div><div>${input('end_time','End time','17:00','time','required')}</div></div>${input('break_minutes','Unpaid break (minutes)',0,'number','min="0" max="600" required')}${statusChoice()}${textArea('notes','Notes (optional)')}${footer('Create repeating shifts')}</form>`);
}
function storeChecks(ids) {return `<div class="check-row">${allStores().filter(s=>s.active||ids.includes(s.id)).map(s=>`<label><input type="checkbox" name="store_ids" value="${s.id}" ${ids.includes(s.id)?'checked':''}>${esc(s.name)}</label>`).join('')}</div>`;}
function showEmployee(e) {
  if(!allStores().some(s=>s.active)){
    state.view='stores';render();toast('Create an active store before adding employees',true);showStore();return;
  }
  const ids=e?state.data.employee_stores.filter(x=>x.employee_id===e.id).map(x=>x.store_id):state.store==='all'?[allStores().find(x=>x.active)?.id].filter(Boolean):[Number(state.store)];
  openModal(e?'Edit employee':'Add employee',`<form id="employee-form"><input type="hidden" name="id" value="${e?.id||''}">${input('name','Full name',e?.name||'','text','required maxlength="120" placeholder="Employee name"')}${input('role','Position / role',e?.role||'','text','maxlength="100" placeholder="Cashier, stocker, etc."')}<div class="two-col"><div>${input('rate','Default hourly pay ($)',e?(e.rate_cents/100).toFixed(2):'15.00','number','min="0" step="0.01" required')}</div><div>${input('max_days','Maximum days per week',e?.max_days||5,'number','min="1" max="7" step="1" required')}</div></div><p class="helper">Store-specific rates can be adjusted separately in the employee list. Existing time records retain their original rate.</p><label>Assigned stores</label>${storeChecks(ids)}${e?`<div class="form-checkbox"><input id="active-employee" name="active" type="checkbox" ${e.active?'checked':''}><label for="active-employee">Active employee</label></div>`:''}${footer(e?'Save employee':'Create employee')}</form>`);
}
function showRate(employeeId,storeId) {
  const employee=getEmployee(employeeId),store=getStore(storeId),assoc=state.data.employee_stores.find(x=>x.employee_id===employeeId&&x.store_id===storeId);
  if(!employee||!store||!assoc)return;
  openModal('Edit hourly pay rate',`<form id="rate-form"><input name="employee_id" type="hidden" value="${employeeId}"><input name="store_id" type="hidden" value="${storeId}"><div class="manager-permissions"><strong>${esc(employee.name)}</strong><br>${esc(store.name)} · This rate is used for future time entries at this store.</div>${input('rate','Hourly pay rate ($)',((assoc.rate_cents??employee.rate_cents)/100).toFixed(2),'number','min="0" step="0.01" required')}<p class="helper">Changing the rate does not rewrite previously recorded work or past payroll estimates.</p>${footer('Update pay rate')}</form>`);
}
function showStore(s) {
  const colors=['#3c8d82','#397bb2','#b57747','#745fc5','#c05f76','#337f66'];
  openModal(s?'Edit store':'Add store',`<form id="store-form"><input type="hidden" name="id" value="${s?.id||''}">${input('name','Store name',s?.name||'','text','required maxlength="100" placeholder="Downtown Store"')}${input('location','Address / location (optional)',s?.location||'','text','maxlength="200" placeholder="City, state or address"')}<label>Store color</label><div class="check-row">${colors.map(c=>`<label style="border-color:${c}50"><input name="color" type="radio" value="${c}" ${(s?.color||'#3c8d82')===c?'checked':''}><span style="background:${c};width:14px;height:14px;border-radius:50%;display:inline-block"></span></label>`).join('')}</div>${s?`<div class="form-checkbox"><input id="active-store" name="active" type="checkbox" ${s.active?'checked':''}><label for="active-store">Active store</label></div>`:''}${footer(s?'Save store':'Create store')}</form>`);
}
const staffingWeekdays=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
function staffingBlock(block) {
  const id=block.id;
  return `<section class="staffing-block" data-block-id="${esc(id)}"><div class="staffing-block-head"><strong>Shift coverage</strong><button type="button" class="text-btn" data-action="remove-staffing-block">Remove</button></div>
  ${input(`block_${id}_name`,'Shift name',block.name,'text','required maxlength="60" placeholder="Day / Evening"')}
  <div class="staffing-block-times"><div>${input(`block_${id}_start`,'Starts',block.start_time,'time','required')}</div><div>${input(`block_${id}_end`,'Ends',block.end_time,'time','required')}</div><div>${input(`block_${id}_break`,'Unpaid break (min)',block.break_minutes,'number','required min="0" max="600" step="1"')}</div></div>
  <p class="staffing-count-label">Employees needed in this shift on each day</p><div class="staffing-counts">${staffingWeekdays.map((day,i)=>`<label>${day}<input aria-label="${day} ${esc(block.name)} employees" type="number" name="block_${id}_day_${i}" min="0" max="20" step="1" required value="${Number(block.staffing[i]??0)}"></label>`).join('')}</div></section>`;
}
function showStaffing(storeId) {
  if(!ensureActiveStore())return;
  const store=getStore(storeId)||(state.store==='all'?allStores().find(s=>s.active):getStore(state.store));
  if(!store)return toast('Create a store before setting staffing hours',true);
  let staffing;try{staffing=JSON.parse(store.staffing_json);}catch{staffing=[1,1,1,1,1,1,1];}
  let blocks;try{blocks=JSON.parse(store.shift_templates_json);}catch{blocks=null;}
  if(!Array.isArray(blocks)||!blocks.length)blocks=[{id:'main',name:'Day shift',start_time:store.open_time||'09:00',end_time:clockTime(timeMinute(store.open_time||'09:00')+Number(store.slot_minutes||480)),break_minutes:store.break_minutes||0,staffing}];
  openModal(`Staffing plan · ${store.name}`,`<form id="staffing-form"><input type="hidden" name="store_id" value="${store.id}"><p class="tab-note">Set your operating hours and add separate day/evening shifts. For each shift, enter how many employees you want on each weekday. The generator fills each position independently with available staff.</p>
    <div class="two-col"><div>${input('open_time','Store opens',store.open_time||'09:00','time','required')}</div><div>${input('close_time','Store closes (next day if earlier)',store.close_time||'17:00','time','required')}</div></div>
    <p class="helper">Example: Open 10:00 AM, close 4:00 AM. A shift from 8:00 PM to 4:00 AM is an overnight shift ending the next day.</p>
    <div id="staffing-blocks">${blocks.map(staffingBlock).join('')}</div>
    <button class="btn secondary" type="button" data-action="add-staffing-block">＋ Add another shift (e.g. evening)</button>
    <p class="helper">Enter 2 or 3 under Evening to schedule that many employees at the same time. Use 0 for a shift you do not need that day. Generating a week preserves existing shifts and only fills empty positions. Maximum 40 planned hours per employee per week.</p>${footer('Save staffing plan')}</form>`);
}
function showEntry(e) {
  if(!ensureActiveStore())return;
  const store=e?.store_id||(state.store==='all'?allStores().find(s=>s.active)?.id:Number(state.store));
  if(!ensureStaffFor(store))return;
  const mode=e?.start_time?'times':'manual';
  const categoryOptions=[...new Set([...hourChoices,e?.category].filter(Boolean))].map(x=>`<option value="${esc(x)}" ${x===(e?.category||'Shift')?'selected':''}>${esc(x)}</option>`).join('');
  openModal(e?'Edit recorded hours':'Record worked hours',`<form id="entry-form"><input type="hidden" name="id" value="${e?.id||''}"><div class="two-col"><div>${storeSelectField(store)}</div><div>${input('date','Work date',e?.entry_date||localDay(new Date()),'date','required')}</div></div>${employeesSelect(store,e?.employee_id)}${selectField('category','Work category',categoryOptions)}${selectField('mode','How would you like to enter time?',`<option value="manual" ${mode==='manual'?'selected':''}>Total hours</option><option value="times" ${mode==='times'?'selected':''}>Start and end times</option>`)}<div id="manual-hours" class="${mode==='times'?'hidden':''}">${input('hours','Total hours worked',e?hours(e.minutes):'2.00','number','min="0.01" max="23.99" step="0.01"')}</div><div id="timed-hours" class="${mode==='manual'?'hidden':''}"><div class="two-col"><div>${input('start_time','Start time',e?.start_time||'09:00','time')}</div><div>${input('end_time','End time',e?.end_time||'17:00','time')}</div></div>${input('break_minutes','Unpaid break (minutes)',e?.break_minutes||0,'number','min="0" max="600"')}</div>${isOwner()?input('rate','Rate override ($/hr, optional)',e?(e.rate_cents/100).toFixed(2):'','number','min="0" step="0.01" placeholder="Current store rate"'):''}${textArea('notes','What work was done? (optional)',e?.notes||'')}<p class="helper">This creates an actual time record used in gross-pay estimates. Planned shifts do not count until recorded.</p>${footer(e?'Save hours':'Add hours',e?`<button class="btn danger delete" type="button" data-action="delete-entry" data-id="${e.id}">Archive entry</button>`:'')}</form>`);
}
function showManager(u) {
  if(!isOwner())return;
  const ids=u?state.accounts.user_stores.filter(x=>x.user_id===u.id).map(x=>x.store_id):[allStores().find(s=>s.active)?.id].filter(Boolean);
  if(!ensureActiveStore())return;
  openModal(u?'Edit manager':'Create manager login',`<form id="manager-form"><input type="hidden" name="id" value="${u?.id||''}">${input('username','Login username',u?.username||'','text','required autocomplete="username" maxlength="80" pattern="[a-zA-Z0-9_\\-]+" title="Letters, numbers, hyphens and underscores only"')}${input('password',u?'New password (leave blank to keep current)':'Initial password','', 'password',`${u?'':'required'} autocomplete="new-password"`)}<label>Stores this manager can access (select at least one)</label>${storeChecks(ids)}${u?`<div class="form-checkbox"><input type="checkbox" id="manager-active" name="active" ${u.active?'checked':''}><label for="manager-active">Account enabled</label></div>`:''}<p class="tab-note">Give the manager this username and password to sign in on the normal login page. Managers can add stores and employees and manage schedules, hours and rates for their stores; payroll and login accounts stay owner-only.</p>${footer(u?'Save manager':'Create login')}</form>`);
}
function showChangePassword() {
  openModal('Change my password',`<form id="password-form">${input('current_password','Current password','','password','required autocomplete="current-password"')}${input('new_password','New password','','password','required autocomplete="new-password"')}${footer('Change password')}</form>`);
}

async function refresh() {
  await loadData();
  try{await loadEstimate();}catch{state.estimate=null;}
  if(state.view==='managers'&&isOwner())await loadAccounts();
  if(state.view==='payroll'&&isOwner())await loadPayroll();
  render();
}
async function loadAccounts() {state.accounts=await api('/api/users');}
async function loadPayroll() {const q=new URLSearchParams({start:state.payrollStart,end:state.payrollEnd});if(state.payrollStore!=='all')q.set('store_id',state.payrollStore);state.payroll=await api('/api/payroll?'+q);}
async function setView(view) {
  if ((view==='payroll'||view==='managers')&&!isOwner())return;
  state.view=view;
  try {if(view==='payroll')await loadPayroll();if(view==='managers')await loadAccounts();if(view==='schedule')await loadEstimate();render();}
  catch(e){toast(e.message,true);render();}
}
async function handleAction(action,btn) {
  const id=Number(btn.dataset.id);
  if(action==='close-modal')return closeModal();
  if(action==='add-staffing-block'){
    const target=$('#staffing-blocks');
    if(!target)return;
    if($$('.staffing-block',target).length>=12)return toast('Up to 12 shift types per store',true);
    const closing=$('#f-close_time')?.value||'04:00';
    const id='s'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);
    target.insertAdjacentHTML('beforeend',staffingBlock({id,name:'Evening shift',start_time:clockTime(timeMinute(closing)-480),end_time:closing,break_minutes:30,staffing:[0,0,0,0,0,0,0]}));
    target.lastElementChild.scrollIntoView({block:'nearest'});
    return;
  }
  if(action==='remove-staffing-block'){
    const target=$('#staffing-blocks'),blocks=$$('.staffing-block',target);
    if(blocks.length<=1)return toast('Keep at least one shift type',true);
    btn.closest('.staffing-block')?.remove();
    return;
  }
  if(action==='new-shift')return showShift(null,btn.dataset.date,Number(btn.dataset.store)||undefined);
  if(action==='edit-shift')return showShift(state.data.shifts.find(x=>x.id===id));
  if(action==='bulk')return showBulk();
  if(action==='new-employee')return showEmployee();
  if(action==='edit-employee')return showEmployee(state.data.employees.find(x=>x.id===id));
  if(action==='edit-rate')return showRate(Number(btn.dataset.employee),Number(btn.dataset.store));
  if(action==='new-store')return showStore();
  if(action==='edit-store')return showStore(getStore(id));
  if(action==='staffing')return showStaffing(Number(btn.dataset.store)||undefined);
  if(action==='generate-week'){
    if(state.store==='all')return toast('Select a single store to generate its weekly schedule',true);
    const result=await api('/api/schedule/generate-week',{store_id:Number(state.store),week_start:localDay(monday(state.cursor))});
    await refresh();
    if(result.unfilled.length){const amount=result.unfilled.reduce((sum,day)=>sum+day.remaining,0);toast(`${result.created} draft shifts created; ${amount} unfilled slots. Add staff or edit your plan.`,true);}
    else toast(`${result.created} draft shifts created. Review and publish when ready.`);
    return;
  }
  if(action==='new-entry')return showEntry();
  if(action==='edit-entry')return showEntry(state.data.entries.find(x=>x.id===id));
  if(action==='new-manager')return showManager();
  if(action==='edit-manager')return showManager(state.accounts?.users.find(x=>x.id===id));
  if(action==='change-password')return showChangePassword();
  if(action==='view-table'){state.scheduleLayout='table';state.mode='week';return render();}
  if(action==='view-day'){
    state.cursor=parseDay(btn.dataset.date);
    state.scheduleLayout='table';state.mode='week';
    await loadEstimate();return render();
  }
  if(action==='view-calendar'){state.scheduleLayout='calendar';return render();}
  if(action==='print-schedule'){state.scheduleLayout='table';state.mode='week';render();window.print();return;}
  if(action==='print-payroll'){window.print();return;}
  if(action==='prev'||action==='next'){state.cursor=state.scheduleLayout==='calendar'&&state.mode==='month'?new Date(state.cursor.getFullYear(),state.cursor.getMonth()+(action==='next'?1:-1),1,12):addDays(state.cursor,action==='next'?7:-7);await loadEstimate();return render();}
  if(action==='today'){state.cursor=new Date();await loadEstimate();return render();}
  if(action==='calendar-week'||action==='calendar-month'){state.mode=action==='calendar-week'?'week':'month';await loadEstimate();return render();}
  if(action==='delete-shift'||action==='delete-entry'){
    if(!confirm(`Archive this ${action==='delete-shift'?'shift':'time entry'}? Changes will be preserved in the audit history.`))return;
    await api(action==='delete-shift'?'/api/shifts/delete':'/api/entries/delete',{id});closeModal();await refresh();toast('Record archived');return;
  }
  if(action==='record-shift'){
    if(!confirm('Record this planned shift as hours actually worked? You can edit the resulting time entry afterward.'))return;
    await api('/api/entries/from-shift',{shift_id:id});closeModal();await refresh();toast('Shift hours recorded');return;
  }
  if(action==='publish-range'){
    if(state.store==='all')return toast('Choose one store before publishing the visible schedule',true);
    const range=getRange();const result=await api('/api/shifts/publish',{store_id:Number(state.store),start:localDay(range.first),end:localDay(range.last)});
    await refresh();toast(`${result.published} shifts published`);return;
  }
  if(action==='calculate-pay'){
    state.payrollStart=$('#payroll-start').value;state.payrollEnd=$('#payroll-end').value;state.payrollStore=$('#payroll-store').value;
    await loadPayroll();render();toast('Payroll estimate refreshed');return;
  }
  if(action==='csv-payroll'){
    if(!state.payroll)return toast('Calculate payroll first',true);
    const q=new URLSearchParams({start:state.payrollStart,end:state.payrollEnd});if(state.payrollStore!=='all')q.set('store_id',state.payrollStore);
    window.location.assign('/api/payroll.csv?'+q);return;
  }
}

async function submitModal(form) {
  const values=new FormData(form);
  // getAttribute: a form containing <input name="id"> shadows form.id with that input element.
  const kind=form.getAttribute('id');
  const v=name=>values.get(name)?.toString()||'';
  let body,endpoint,ok;
  if(kind==='shift-form'){
    endpoint='/api/shifts';body={id:v('id')||undefined,employee_id:Number(v('employee_id')),store_id:Number(v('store_id')),date:v('date'),start_time:v('start_time'),end_time:v('end_time'),break_minutes:Number(v('break_minutes')),status:v('status'),notes:v('notes')};ok='Shift saved';
  } else if(kind==='bulk-form'){
    endpoint='/api/shifts/bulk';body={employee_id:Number(v('employee_id')),store_id:Number(v('store_id')),start_date:v('start_date'),end_date:v('end_date'),start_time:v('start_time'),end_time:v('end_time'),break_minutes:Number(v('break_minutes')),status:v('status'),weekdays:values.getAll('weekdays').map(Number),notes:v('notes')};ok='Repeating schedule created';
  } else if(kind==='employee-form'){
    endpoint='/api/employees';body={id:v('id')||undefined,name:v('name'),role:v('role'),rate:v('rate'),max_days:Number(v('max_days')),store_ids:values.getAll('store_ids').map(Number),active:form.elements.active?values.has('active'):true};ok='Employee saved';
  } else if(kind==='store-form'){
    endpoint='/api/stores';body={id:v('id')||undefined,name:v('name'),location:v('location'),color:v('color')||'#3c8d82',active:form.elements.active?values.has('active'):true};ok='Store saved';
  } else if(kind==='staffing-form'){
    const templates=$$('.staffing-block',form).map(row=>{
      const id=row.dataset.blockId;
      return {id,name:v(`block_${id}_name`),start_time:v(`block_${id}_start`),end_time:v(`block_${id}_end`),break_minutes:Number(v(`block_${id}_break`)),staffing:Array.from({length:7},(_,i)=>Number(v(`block_${id}_day_${i}`)))};
    });
    const first=templates[0];
    const duration=(timeMinute(first.end_time)-timeMinute(first.start_time)+1440)%1440;
    body={store_id:Number(v('store_id')),open_time:v('open_time'),close_time:v('close_time'),slot_minutes:duration,break_minutes:first.break_minutes,staffing:Array.from({length:7},(_,i)=>templates.reduce((sum,b)=>sum+b.staffing[i],0)),templates};
    endpoint='/api/store-staffing';ok='Weekly staffing plan saved';
  } else if(kind==='rate-form'){
    endpoint='/api/store-rate';body={employee_id:Number(v('employee_id')),store_id:Number(v('store_id')),rate:v('rate')};ok='Hourly rate updated';
  } else if(kind==='entry-form'){
    endpoint='/api/entries';const mode=v('mode');body={id:v('id')||undefined,employee_id:Number(v('employee_id')),store_id:Number(v('store_id')),date:v('date'),category:v('category'),notes:v('notes'),hours:mode==='manual'?v('hours'):undefined,start_time:mode==='times'?v('start_time'):undefined,end_time:mode==='times'?v('end_time'):undefined,break_minutes:mode==='times'?Number(v('break_minutes')):0};if(isOwner()&&v('rate'))body.rate=v('rate');ok='Worked hours saved';
  } else if(kind==='manager-form'){
    endpoint='/api/users';body={id:v('id')||undefined,username:v('username'),password:v('password'),store_ids:values.getAll('store_ids').map(Number),active:form.elements.active?values.has('active'):true};ok='Manager account saved';
  } else if(kind==='password-form'){
    endpoint='/api/change-password';body={current_password:v('current_password'),new_password:v('new_password')};ok='Password changed. Please sign in again.';
  } else return;
  const result=await api(endpoint,body);
  closeModal();
  if(kind==='password-form'){logoutLocal();toast(ok);return;}
  await refresh();toast(kind==='bulk-form'?`${result.created} new shifts created`:ok);
}

document.addEventListener('click',async event=>{
  const nav=event.target.closest('[data-view]');if(nav){await setView(nav.dataset.view);return;}
  const btn=event.target.closest('[data-action]');if(!btn)return;
  try {await handleAction(btn.dataset.action,btn);}catch(e){toast(e.message,true);}
});
document.addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target;const formId=form.getAttribute('id');const submit=form.querySelector('button[type=submit]');
  if(submit)submit.disabled=true;
  try {
    if(formId==='login-form'){
      $('#login-error').textContent='';
      state.session=await api('/api/login',{username:$('#login-user').value.trim(),password:$('#login-pass').value});
      await loadData();showApp();
    } else await submitModal(form);
  }catch(e){if(formId==='login-form')$('#login-error').textContent=e.message;else {const error=$('#modal-error');if(error){error.textContent=`Could not save: ${e.message}`;error.classList.remove('hidden');error.scrollIntoView({block:'nearest'});}toast(e.message,true);}}
  finally {if(submit)submit.disabled=false;}
});
document.addEventListener('change',async event=>{
  const t=event.target;
  if(t.id==='week-jump'){
    if(t.value){state.cursor=parseDay(t.value);try{await loadEstimate();render();}catch(e){toast(e.message,true);}}
    return;
  }
  if(t.name==='available_day'){
    const employee=getEmployee(Number(t.dataset.employee));
    if(!employee)return;
    const previous=availableDays(employee);
    const selected=previous.map((yes,i)=>i).filter(i=>i===Number(t.dataset.weekday)?t.checked:Boolean(previous[i]));
    t.disabled=true;
    try {await api('/api/availability',{employee_id:employee.id,weekdays:selected});await refresh();toast(`${employee.name}'s availability saved`);}
    catch(e){t.checked=!t.checked;t.disabled=false;toast(e.message,true);}
    return;
  }
  if(t.id==='global-store'){state.store=t.value;try{await loadEstimate();render();}catch(e){toast(e.message,true);}return;}
  if(t.name==='store_id'&&t.closest('#shift-form,#bulk-form,#entry-form')){
    const employee=t.closest('form').elements.employee_id;
    if(employee)employee.innerHTML=employeeChoices(Number(t.value),Number(employee.value));
  }
  if(t.name==='mode'&&t.closest('#entry-form')){
    $('#manual-hours').classList.toggle('hidden',t.value!=='manual');$('#timed-hours').classList.toggle('hidden',t.value!=='times');
  }
});
$('#login-form').addEventListener('reset',()=>{$('#login-error').textContent='';});
$('#logout-btn').addEventListener('click',async()=>{try{await api('/api/logout',{});}finally{logoutLocal();}});
$('#close-modal').addEventListener('click',closeModal);
$('#modal-root').addEventListener('click',event=>{if(event.target.id==='modal-root')closeModal();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('#modal-root').classList.contains('hidden'))closeModal();});
start();
