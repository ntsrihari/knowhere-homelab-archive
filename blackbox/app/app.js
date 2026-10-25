'use strict';

const $ = (id) => document.getElementById(id);
const state = {
  summary: null, trips: [], tracks: [], candidates: [], fuel: [], costs: [], quality: [], imports: [],
  vehicles: [], liveDrives: [], activeVehicleId: null, selectedRoute: null, basemapVisible: true,
  expandedTimelineDays: null, timelineDateKeys: [],
  loadedRouteKey: null, mapViews: {},
  mapProvider: localStorage.getItem('knowhere-blackbox-map-provider') === 'carto' ? 'carto' : 'osm',
  cartoKey: localStorage.getItem('knowhere-blackbox-carto-key') || ''
};
const NUM = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 });
const ICON = { gps: 'GPS', network: 'NETWORK', fused: 'FUSED', live: 'LIVE' };

function esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function money(v, exact=false) { if (v == null || !Number.isFinite(Number(v))) return '—'; return new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:exact?2:0,minimumFractionDigits:exact?2:0}).format(Number(v)); }
function fixed(v, n=1) { return v == null || !Number.isFinite(Number(v)) ? '—' : new Intl.NumberFormat('en-IN',{maximumFractionDigits:n,minimumFractionDigits:0}).format(Number(v)); }
function localDate(value, withTime=true) {
  if (!value) return '—'; const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).replace('T',' ');
  return new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric',...(withTime?{hour:'2-digit',minute:'2-digit',hour12:false}:{})}).format(d);
}
function fuelDate(value) {
  if (!value) return '—';
  const d = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${String(value).replace(' ','T')}:00+05:30`);
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
}
function dateOnly(value) { return value ? value.slice(0,10) : '—'; }
function km(v) { return v == null || !Number.isFinite(Number(v)) ? '—' : `${fixed(v,2)} km`; }
function duration(min) { if (min == null) return '—'; const total=Math.max(0,Math.round(Number(min))),h=Math.floor(total/60),m=total%60; return h ? `${h}h ${m}m` : `${m} min`; }
function toast(message, bad=false) { const node=$('toast'); if (!node) return; node.textContent=message; node.dataset.kind=bad?'error':'success'; node.classList.add('show'); window.clearTimeout(toast.timer); toast.timer=window.setTimeout(()=>node.classList.remove('show'),4200); }
async function api(path, options={}) {
  const resp=await fetch(path,options);
  if (resp.status===401) { toast('Authentication required. Reload and sign in.',true); throw new Error('Authentication required'); }
  if (!resp.ok) { let msg=`Request failed (${resp.status})`; try { const j=await resp.json();msg=j.error||msg; } catch {} throw new Error(msg); }
  if (path==='/api/export/database') return resp.blob();
  return resp.json();
}
function vehicleName(v) { return v ? [v.name, (v.make && v.model) ? `${v.make} ${v.model}` : (v.make||v.model)].filter(Boolean).join(' · ') : 'Unassigned'; }
function selectedVehicle() { return state.vehicles.find(v=>Number(v.id)===Number(state.activeVehicleId)) || state.vehicles[0] || null; }
function setActiveVehicle(id) {
  const found=state.vehicles.find(v=>Number(v.id)===Number(id)); if (!found) return;
  state.activeVehicleId=Number(found.id); localStorage.setItem('knowhere-blackbox-active-vehicle',String(found.id));
  $('active-vehicle').value=String(found.id); fillVehicleOptions(); renderVehicleCards(); renderVehicleForm(); renderVehicleUrl(); updateFuelUnits();
  renderOverview();
  if (state.selectedRoute?.type === 'live' && Number(state.selectedRoute.drive?.vehicle_id) !== Number(found.id)) { state.selectedRoute=null; loadMostRecentRoute(); }
  else if (state.selectedRoute?.type === 'gpx' && !state.tracks.some(t=>Number(t.id)===Number(state.selectedRoute.id)&&Number(t.vehicle_id)===Number(found.id))) { state.selectedRoute=null; loadMostRecentRoute(); }
  else if (state.selectedRoute?.type === 'candidate') { state.selectedRoute=null; loadMostRecentRoute(); }
}
function setOptions(id, rows, placeholder='Choose vehicle') {
  const el=$(id); if (!el) return; const old=el.value;
  el.innerHTML=rows.map(v=>`<option value="${v.id}">${esc(vehicleName(v))}</option>`).join('');
  if (rows.some(v=>String(v.id)===old)) el.value=old;
  else if (rows.length) el.value=String(state.activeVehicleId || rows[0].id);
}
function fillVehicleOptions() {
  const rows=state.vehicles;
  setOptions('active-vehicle',rows); setOptions('vehicle-edit-id',rows); setOptions('fuel-vehicle-id',rows); setOptions('cost-vehicle-id',rows);
  const imp=$('import-vehicle-id'); if (imp) { const old=imp.value; imp.innerHTML='<option value="">Leave unassigned</option>'+rows.map(v=>`<option value="${v.id}">${esc(vehicleName(v))}</option>`).join(''); if(rows.some(v=>String(v.id)===old))imp.value=old; }
  ['fuel-vehicle-id','cost-vehicle-id'].forEach(id=>{if($(id))$(id).value=String(state.activeVehicleId||rows[0]?.id||'');});
}
function goTab(tab) {
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===`page-${tab}`));
  $('page-crumb').textContent=({overview:'OVERVIEW',trips:'TRIP HISTORY',tracks:'GPS & DRIVES',vehicles:'VEHICLES',fuel:'FUEL & COSTS',quality:'DATA QUALITY'})[tab]||tab.toUpperCase();
  if (tab==='trips') renderTrips();
  if (tab==='tracks') { renderTracks(); renderCandidates(); if(state.selectedRoute) renderMap('track-map'); else loadMostRecentRoute(); }
  if (tab==='vehicles') { renderVehicleCards(); renderVehicleForm(); renderVehicleUrl(); renderLiveDrives(); }
  if (tab==='fuel') renderFuel();
  if (tab==='quality') renderQuality();
  setTimeout(()=>Object.keys(state.mapViews).forEach(renderMap),40);
}
function initNavigation() {
  document.querySelectorAll('.nav-item').forEach(b=>b.addEventListener('click',()=>goTab(b.dataset.tab)));
  document.querySelectorAll('[data-goto]').forEach(b=>b.addEventListener('click',()=>goTab(b.dataset.goto)));
}
async function loadAll() {
  try {
    const [summary,trips,tracks,candidates,fuel,costs,quality,vehicles,liveDrives] = await Promise.all([
      api('/api/summary'),api('/api/trips'),api('/api/tracks'),api('/api/detected-trips'),api('/api/fuel'),api('/api/costs'),api('/api/quality'),api('/api/vehicles'),api('/api/live-drives')
    ]);
    state.summary=summary;state.trips=trips;state.tracks=tracks;state.candidates=candidates;state.fuel=fuel;state.costs=costs;state.quality=quality.issues;state.imports=quality.imports;state.vehicles=vehicles;state.liveDrives=liveDrives;
    const remembered=Number(localStorage.getItem('knowhere-blackbox-active-vehicle'));
    state.activeVehicleId=vehicles.some(v=>Number(v.id)===remembered)?remembered:(vehicles[0]?.id||null);
    fillVehicleOptions();
    if($('active-vehicle') && state.activeVehicleId) $('active-vehicle').value=String(state.activeVehicleId);
    renderOverview();renderTrips();renderTracks();renderCandidates();renderFuel();renderQuality();renderVehicleCards();renderVehicleForm();renderVehicleUrl();renderLiveDrives();
    $('footer-state').textContent=`DATABASE ONLINE · ${summary.imports.length} SOURCE FILES · REFRESHED ${new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'})}`;
    if(!state.selectedRoute) loadMostRecentRoute();
  } catch (e) { $('footer-state').textContent='DATABASE LOAD FAILED'; toast(e.message,true); }
}
async function refreshLive() {
  try {
    const [vehicles,drives]=await Promise.all([api('/api/vehicles'),api('/api/live-drives')]);
    state.vehicles=vehicles;state.liveDrives=drives;
    renderTracks();
    if(!state.activeVehicleId || !vehicles.some(v=>Number(v.id)===Number(state.activeVehicleId))) state.activeVehicleId=vehicles[0]?.id||null;
    fillVehicleOptions(); renderVehicleCards();renderVehicleUrl();renderLiveDrives();renderOverview();
    if(state.selectedRoute?.type==='live' && state.selectedRoute.id) await selectLiveDrive(state.selectedRoute.id,false);
    if(state.selectedRoute?.type==='live' && !state.selectedRoute.id) loadMostRecentRoute();
    $('footer-state').textContent=`LIVE SYNC · ${new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'})}`;
  } catch (_) { /* transient network loss should not erase the last rendered view */ }
}
function renderOverview() {
  if(!state.summary)return;
  const v=selectedVehicle(); if(!v)return;
  const vehicle=[v.make,v.model,v.year].filter(Boolean).join(' ');
  const id=Number(v.id);
  const archived=state.trips.filter(t=>Number(t.vehicle_id)===id);
  const live=state.liveDrives.filter(d=>Number(d.vehicle_id)===id);
  const tracks=state.tracks.filter(t=>Number(t.vehicle_id)===id);
  const historicKm=archived.reduce((sum,t)=>sum+Number(t.distance_km||0),0);
  const gpsPointCount=tracks.reduce((sum,t)=>sum+Number(t.point_count||0),0)+live.reduce((sum,d)=>sum+Number(d.point_count||0),0);
  const modelledFuelCost=live.reduce((sum,d)=>sum+(Number.isFinite(Number(d.estimated_fuel_cost_inr))?Number(d.estimated_fuel_cost_inr):0),0);
  const pricedDrives=live.filter(d=>d.estimated_fuel_cost_inr!=null).length;
  $('vehicle-heading').textContent=`${v.name} logbook`;
  $('vehicle-subhead').textContent=`${vehicle ? vehicle+' · ' : ''}This view is scoped to the selected vehicle. Running cost is modelled from GPS distance, not measured from vehicle electronics.`;
  $('kpi-trips').textContent=fixed(live.length,0);
  $('kpi-trips-foot').textContent=`${live.filter(d=>d.status==='active').length} active · ${live.filter(d=>d.status!=='active').length} completed GPS sessions`;
  $('kpi-distance').textContent=NUM.format(historicKm);
  $('kpi-distance-foot').textContent=`${archived.length} historical trip summaries · CSV archive only`;
  $('kpi-points').textContent=NUM.format(gpsPointCount);
  $('kpi-points-foot').textContent=`${tracks.length} assigned GPX files · ${live.length} live GPS sessions`;
  $('kpi-fuel-spend').textContent=pricedDrives?money(modelledFuelCost):'—';
  $('kpi-fuel-foot').textContent=pricedDrives?`${pricedDrives} GPS session(s) with a cost model · estimate, not receipt`:'No priced GPS drives yet; check this vehicle’s efficiency and fuel price.';
  $('trip-nav-count').textContent=state.trips.length;
  $('track-nav-count').textContent=state.tracks.length+state.liveDrives.length;
  const recent=[...live.map(d=>d.started_utc),...tracks.map(t=>t.end_utc||t.start_utc),...archived.map(t=>t.start_date)].filter(Boolean).sort();
  $('last-activity').textContent=recent.length?localDate(recent.at(-1)):'No activity yet';
  const first=recent[0],last=recent.at(-1);
  $('activity-range').textContent=first&&last?`${dateOnly(String(first))} — ${dateOnly(String(last))}`:'New drives appear when GPSLogger sends points';
  const w=state.quality.filter(x=>x.level==='warning'||x.level==='review').length;
  const notice=$('primary-warning');
  const missingModels=state.vehicles.filter(x=>!x.effective_km_per_l||!x.effective_fuel_price_per_litre);
  const missingConnections=state.vehicles.filter(x=>!String(x.connection_name||'').trim());
  const connectionCounts=new Map();state.vehicles.forEach(x=>{const n=String(x.connection_name||'').trim().toLocaleLowerCase();if(n)connectionCounts.set(n,(connectionCounts.get(n)||0)+1);});
  const duplicateConnections=state.vehicles.filter(x=>connectionCounts.get(String(x.connection_name||'').trim().toLocaleLowerCase())>1);
  if(missingModels.some(x=>Number(x.id)===id)) {
    notice.className='notice notice-warn';notice.innerHTML=`<span class="notice-icon">!</span><div><strong>Fuel model needed for ${esc(v.name)}</strong><span>Set expected efficiency and current price under Vehicles & automation, or log full-to-full refuelling with odometer readings. Cost is estimated per GPS kilometre; it is not actual fuel-burn telemetry.</span></div>`;
  } else if(duplicateConnections.length) {
    const names=[...new Set(duplicateConnections.map(x=>x.connection_name))].join(', ');
    notice.className='notice notice-warn';notice.innerHTML=`<span class="notice-icon">!</span><div><strong>Car selection trigger is ambiguous</strong><span>These head-unit Bluetooth names are duplicated: ${esc(names)}. Automatic profile switching cannot safely distinguish the vehicles until each has a unique connection name.</span></div>`;
  } else if(missingConnections.length) {
    const names=missingConnections.map(x=>x.name).join(', ');
    notice.className='notice notice-info';notice.innerHTML=`<span class="notice-icon">i</span><div><strong>Automatic car switching still needs setup</strong><span>Enter a distinct paired head-unit Bluetooth name for each car and configure Automate to switch the matching GPSLogger profile. This detects Bluetooth connection as a proxy for Android Auto, not Android Auto's internal state. Missing: ${esc(names)}.</span></div>`;
  } else if(w) {
    notice.className='notice notice-warn';notice.innerHTML=`<span class="notice-icon">!</span><div><strong>${w} data-quality items need review</strong><span>Open Data quality to review incomplete historical entries and sparse GPS samples.</span></div>`;
  } else {
    notice.className='notice notice-info';notice.innerHTML='<span class="notice-icon">✓</span><div><strong>Vehicle data is scoped and ready</strong><span>Incoming GPS points use the selected vehicle’s unique URL key. Estimated fuel cost follows distance × unit price ÷ efficiency; actual purchases are logged separately.</span></div>';
  }
  const m=$('month-caption');if(m)m.textContent=`${v.name} · archived summaries only · excludes live GPS sessions`;
  renderMonthly();renderTrackPreview();renderCandidatePreview();
}

function renderMonthly() {
  const box=$('monthly-chart'),v=selectedVehicle();
  const grouped=new Map();
  for(const t of state.trips.filter(t=>!v||Number(t.vehicle_id)===Number(v.id))){const month=String(t.start_date||'').slice(0,7);if(!/^\d{4}-\d{2}$/.test(month))continue;const cur=grouped.get(month)||{month,distance_km:0,trips:0};cur.distance_km+=Number(t.distance_km||0);cur.trips++;grouped.set(month,cur);}
  const rows=[...grouped.values()].sort((a,b)=>a.month.localeCompare(b.month)).slice(-12);
  if(!rows.length){box.innerHTML='<div class="empty-mini">No archived trip summaries for this vehicle yet. Live GPS sessions are listed separately.</div>';return;}
  const max=Math.max(1,...rows.map(r=>r.distance_km));
  box.innerHTML=rows.map(r=>{const h=Math.max(4,r.distance_km/max*112);const label=new Date(`${r.month}-01T00:00:00+05:30`).toLocaleDateString('en-IN',{month:'short',year:'2-digit',timeZone:'Asia/Kolkata'});return `<div class="month-bar"><div class="bar-num">${fixed(r.distance_km,0)}</div><div class="bar" style="height:${h}px" title="${esc(r.month)}: ${fixed(r.distance_km,2)} km · ${r.trips} summaries"></div><div class="bar-month">${esc(label)}</div></div>`;}).join('');
}

function renderTrackPreview() {
  const box=$('track-preview'),v=selectedVehicle(),vid=Number(v?.id);const entries=[...state.liveDrives.filter(d=>Number(d.vehicle_id)===vid).map(d=>({...d,entryType:'live'})),...state.tracks.filter(t=>Number(t.vehicle_id)===vid).map(t=>({...t,entryType:'gpx'}))].sort((a,b)=>(b.started_utc||b.start_utc||b.end_utc||'').localeCompare(a.started_utc||a.start_utc||a.end_utc||'')).slice(0,5);
  if(!entries.length){box.innerHTML=`<div class="empty-mini">No tracks assigned to ${esc(v?.name||'this vehicle')} yet. Import a GPX with this vehicle selected, or connect its dedicated GPSLogger profile.</div>`;return;}
  box.innerHTML=entries.map(t=>{
    const live=t.entryType==='live';const date=t.started_utc||t.start_utc;
    return `<button class="track-card" data-kind="${live?'live':'gpx'}" data-id="${t.id}"><span class="track-glyph">${live?'●':'⌖'}</span><span class="track-card-main"><strong>${esc(live?t.vehicle_name:(t.track_name||t.filename))}</strong><span>${localDate(date)} · ${live?`${fixed(t.distance_km,2)} km estimated`:`${localDate(t.end_utc)} end`}</span></span><span class="track-card-metric">${live?money(t.estimated_fuel_cost_inr):t.point_count}<em>${live?'fuel est.':'points'}</em></span></button>`;
  }).join('');
  box.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{goTab('tracks');b.dataset.kind==='live'?selectLiveDrive(Number(b.dataset.id)):selectTrack(Number(b.dataset.id));}));
}
function candidatesForActiveVehicle() {
  const ownedFiles=new Set(state.tracks.filter(t=>Number(t.vehicle_id)===Number(state.activeVehicleId)).map(t=>t.filename));
  if(!ownedFiles.size)return [];
  return state.candidates.filter(c=>Array.isArray(c.source_files)&&c.source_files.some(name=>ownedFiles.has(name)));
}
function renderCandidatePreview() {
  const box=$('candidate-preview'),items=[...candidatesForActiveVehicle()].sort((a,b)=>b.start_utc.localeCompare(a.start_utc)).slice(0,4);
  $('candidate-count').textContent=`${candidatesForActiveVehicle().length}`;
  if(!items.length){box.innerHTML='<div class="empty-mini">No historical candidate drives. Candidate detection is best-effort; live drives are captured separately.</div>';return;}
  box.innerHTML=items.map((c,i)=>`<button class="candidate-card" data-candidate="${c.id}"><span class="candidate-index">${String(i+1).padStart(2,'0')}</span><span class="candidate-card-body"><strong>${localDate(c.start_utc)} · ${duration(c.duration_min)}</strong><span>${c.points} moving samples · heuristic</span></span><span class="candidate-metrics">${fixed(c.estimated_path_km,1)} km<small>peak ${fixed(c.max_speed_kmh,0)} km/h</small></span></button>`).join('');
  box.querySelectorAll('[data-candidate]').forEach(b=>b.addEventListener('click',()=>{goTab('tracks');showCandidate(Number(b.dataset.candidate));}));
}
function renderTrips() {
  const rows=state.trips||[],q=($('trip-search')?.value||'').trim().toLowerCase(),month=$('trip-month')?.value||'';
  const months=[...new Set(rows.map(t=>(t.start_date||'').slice(0,7)).filter(Boolean))].sort().reverse();
  if($('trip-month').dataset.ready!=='1'){$('trip-month').innerHTML='<option value="">All months</option>'+months.map(m=>`<option value="${m}">${new Date(`${m}-01T00:00:00+05:30`).toLocaleDateString('en-IN',{month:'long',year:'numeric',timeZone:'Asia/Kolkata'})}</option>`).join('');$('trip-month').dataset.ready='1';}
  const filtered=rows.filter(t=>(!month||(t.start_date||'').startsWith(month))&&(!q||[t.title,t.start_name,t.end_name,t.start_date,t.vehicle_name].some(x=>String(x||'').toLowerCase().includes(q))));
  $('trip-result-count').textContent=`${filtered.length} archived trips`;
  $('trips-empty').classList.toggle('hidden',filtered.length>0);
  $('trips-table').innerHTML=filtered.map(t=>`<tr><td class="td-main">${esc(t.title||'Untitled trip')}<span class="td-sub">${fuelDate(t.start_date)} · ${esc(t.vehicle_name||'Unassigned')}</span></td><td>${esc(t.start_name||'Unknown')}<span class="td-sub">→ ${esc(t.end_name||'Unknown')}</span></td><td class="table-distance">${km(t.distance_km)}</td><td>${duration(t.duration_min)}</td><td>${fixed(t.avg_speed_kmh,0)} / ${fixed(t.top_speed_kmh,0)} km/h</td><td>${t.estimated_fuel_cost_inr==null?'<span class="muted">Model not set</span>':`<strong>${money(t.estimated_fuel_cost_inr)}</strong><span class="td-sub">estimated fuel only</span>`}</td></tr>`).join('');
}
function normalizePoint(p) {
  const lat=Number(p.latitude??p.lat),lon=Number(p.longitude??p.lon);
  if(!Number.isFinite(lat)||!Number.isFinite(lon)||lat < -90||lat>90||lon < -180||lon>180)return null;
  return {lat,lon,ts_utc:p.ts_utc||p.timestamp||null,speed_mps:p.speed_mps==null?null:Number(p.speed_mps),accuracy_m:p.accuracy_m==null?null:Number(p.accuracy_m),source:p.source||'gps',elevation_m:p.elevation_m==null?null:Number(p.elevation_m)};
}
function normalizeCandidate(c) {return (c.coordinates||[]).map(p=>({lat:Number(p[0]),lon:Number(p[1]),ts_utc:p[2],speed_mps:p[3]==null?null:Number(p[3])/3.6,source:'gps'})).filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));}
function localDateKey(value) {
  if(!value)return 'unknown-date';
  const d=new Date(value);if(Number.isNaN(d.getTime()))return String(value).slice(0,10)||'unknown-date';
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(d).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
function timelineDayLabel(key) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(key))return 'Date unavailable';
  const d=new Date(`${key}T12:00:00+05:30`);
  return new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(d);
}
function clockTime(value) {
  if(!value)return '—';const d=new Date(value);if(Number.isNaN(d.getTime()))return '—';
  return new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
}
function getTimelineDays() {
  const vehicleId=Number(state.activeVehicleId),days=new Map();
  const getDay=(value)=>{const key=localDateKey(value);if(!days.has(key))days.set(key,{key,live:[],candidates:[],tracks:[]});return days.get(key);};
  for(const d of state.liveDrives.filter(d=>Number(d.vehicle_id)===vehicleId))getDay(d.started_utc).live.push(d);
  for(const c of candidatesForActiveVehicle())getDay(c.start_utc).candidates.push(c);
  for(const t of state.tracks.filter(t=>Number(t.vehicle_id)===vehicleId))getDay(t.start_utc||t.end_utc).tracks.push(t);
  return [...days.values()].sort((a,b)=>b.key.localeCompare(a.key)).map(day=>{
    const sessions=[
      ...day.live.map(d=>({kind:'live',id:Number(d.id),start:d.started_utc,end:d.ended_utc||d.last_moving_utc||d.last_point_utc||d.started_utc,data:d})),
      ...day.candidates.map(c=>({kind:'candidate',id:Number(c.id),start:c.start_utc,end:c.end_utc,data:c}))
    ].sort((a,b)=>(a.start||'').localeCompare(b.start||''));
    sessions.forEach((item,i)=>item.number=i+1);
    day.sessions=sessions;
    return day;
  });
}
function renderTracks() {
  const box=$('tracks-list');if(!box)return;
  const days=getTimelineDays();state.timelineDateKeys=days.map(d=>d.key);
  const sessionCount=days.reduce((n,d)=>n+d.sessions.length,0);
  $('track-count-large').textContent=`${sessionCount} sessions · ${days.length} dates`;
  $('track-nav-count').textContent=String(sessionCount);
  const toggle=$('toggle-days');
  if(toggle)toggle.textContent=state.expandedTimelineDays===null||state.expandedTimelineDays.size===days.length?'Collapse all':'Expand all';
  if(!days.length){box.innerHTML='<div class="empty-state">No sessions yet. Import a GPSLogger GPX or configure the live GPSLogger URL for a vehicle.</div>';renderLiveDrives();return;}
  const allOpen=state.expandedTimelineDays===null;
  box.innerHTML=days.map(day=>{
    const open=allOpen||state.expandedTimelineDays.has(day.key);
    const totalLabel=day.sessions.length===1?'1 session':`${day.sessions.length} sessions`;
    const rawLabel=day.tracks.length===1?'1 source recording':`${day.tracks.length} source recordings`;
    const sessionHtml=day.sessions.length?day.sessions.map(session=>{
      const live=session.kind==='live',d=session.data,selected=state.selectedRoute?.type===session.kind&&Number(state.selectedRoute.id)===session.id;
      const start=clockTime(session.start),end=clockTime(session.end);
      const distance=live?Number(d.distance_km||0):Number(d.estimated_path_km||0);
      const mins=Number(d.duration_min||0);
      const peak=live?(d.max_speed_kmh??null):d.max_speed_kmh;
      const average=live?(d.avg_moving_speed_kmh??null):d.avg_moving_speed_kmh;
      const sessionCost=live?d.estimated_fuel_cost_inr:null;
      const sub=live?`${d.status==='active'?'Currently recording':'GPSLogger session'} · ${d.point_count||0} samples`:`GPX-derived candidate · ${d.points} moving samples`;
      const costLine=sessionCost!=null?`<span class="session-cost">${money(sessionCost)} <small>fuel estimate</small></span>`:'';
      return `<article class="timeline-session ${selected?'selected':''}"><button type="button" class="timeline-session-open" data-kind="${session.kind}" data-id="${session.id}" aria-label="Open session ${session.number}, ${timelineDayLabel(day.key)}, ${start}"><span class="session-index">${String(session.number).padStart(2,'0')}</span><span class="timeline-session-body"><span class="timeline-session-title"><strong>Session ${session.number}</strong><span class="session-time">${esc(start)}–${esc(end)}</span></span><span class="timeline-session-sub">${esc(sub)}</span><span class="timeline-session-metrics"><b>${km(distance)}</b><span>${duration(mins)}</span>${average==null?'':`<span>avg ${fixed(average,0)} km/h</span>`}${peak==null?'':`<span>peak ${fixed(peak,0)} km/h</span>`}</span></span>${costLine}<span class="session-chevron" aria-hidden="true">↗</span></button></article>`;
    }).join(''):'<div class="timeline-empty-day">No driving session was confidently detected on this date.</div>';
    const filesHtml=day.tracks.length?`<details class="timeline-sources"><summary>Source recordings <span>${day.tracks.length}</span></summary><div class="timeline-source-list">${day.tracks.slice().sort((a,b)=>(a.start_utc||'').localeCompare(b.start_utc||'')).map(t=>{
      const selected=state.selectedRoute?.type==='gpx'&&Number(state.selectedRoute.id)===Number(t.id);
      return `<button type="button" class="timeline-source ${selected?'selected':''}" data-kind="gpx" data-id="${Number(t.id)}"><span class="source-file-glyph">GPX</span><span class="timeline-source-body"><strong>${esc(t.track_name||t.filename)}</strong><small>${clockTime(t.start_utc)}–${clockTime(t.end_utc)} · ${t.point_count} points · ${t.speed_point_count} with speed</small></span><span aria-hidden="true">↗</span></button>`;
    }).join('')}</div></details>`:'';
    return `<details class="timeline-day" data-day="${esc(day.key)}" ${open?'open':''}><summary class="timeline-day-summary"><span class="timeline-day-icon" aria-hidden="true">${day.sessions.length?'▦':'○'}</span><span class="timeline-day-title"><strong>${esc(timelineDayLabel(day.key))}</strong><small>${esc(totalLabel)}${day.tracks.length?` · ${esc(rawLabel)}`:''}</small></span><span class="timeline-day-count">${day.sessions.length}</span><span class="timeline-day-chevron" aria-hidden="true">⌄</span></summary><div class="timeline-day-content">${sessionHtml}${filesHtml}</div></details>`;
  }).join('');
  box.querySelectorAll('.timeline-day').forEach(d=>d.addEventListener('toggle',()=>{
    if(state.expandedTimelineDays===null)state.expandedTimelineDays=new Set([...box.querySelectorAll('.timeline-day[open]')].map(x=>x.dataset.day));
    if(d.open)state.expandedTimelineDays.add(d.dataset.day);else state.expandedTimelineDays.delete(d.dataset.day);
    if(toggle)toggle.textContent=state.expandedTimelineDays.size===days.length?'Collapse all':'Expand all';
  }));
  box.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{
    const id=Number(b.dataset.id),kind=b.dataset.kind;
    if(kind==='live')selectLiveDrive(id);else if(kind==='candidate')showCandidate(id);else selectTrack(id);
  }));
  renderLiveDrives();
}

function renderCandidates() {
  const tbody=$('candidates-table');if(!tbody)return;const rows=[...candidatesForActiveVehicle()].sort((a,b)=>b.start_utc.localeCompare(a.start_utc));
  $('candidates-empty').classList.toggle('hidden',rows.length>0);
  tbody.innerHTML=rows.map(c=>`<tr><td>${localDate(c.start_utc)}</td><td>${localDate(c.end_utc)}</td><td>${duration(c.duration_min)}</td><td>${km(c.estimated_path_km)}</td><td>${fixed(c.avg_moving_speed_kmh,1)} km/h</td><td>${fixed(c.max_speed_kmh,1)} km/h</td><td>${c.points}</td></tr>`).join('');
}
async function selectTrack(id) {
  try {
    const data=await api(`/api/tracks/${id}`);const track=data.track;
    const points=data.points.map(normalizePoint).filter(Boolean);
    state.selectedRoute={type:'gpx',id:Number(id),title:track.track_name||track.filename,vehicleName:track.vehicle_name||'Unassigned',points,created:track.start_utc};state.loadedRouteKey=`gpx:${id}`;
    $('selected-track-title').textContent=state.selectedRoute.title;$('selected-track-points').textContent=`${points.length} points`;
    $('selected-track-stats').innerHTML=routeStats(points);
    $('track-map-status').textContent=`${state.selectedRoute.vehicleName} · imported GPX · ${localDate(track.start_utc)} to ${localDate(track.end_utc)}`;
    renderMap('track-map',true);renderMap('map',true);renderTracks();
  }catch(e){toast(`Could not open track: ${e.message}`,true);}
}
async function selectLiveDrive(id, updateOverview=true) {
  try {
    const data=await api(`/api/live-drives/${id}`);const points=data.points.map(normalizePoint).filter(Boolean);
    state.selectedRoute={type:'live',id:Number(id),title:`${data.vehicle_name} · ${localDate(data.started_utc,false)}`,vehicleName:data.vehicle_name,points,drive:data};state.loadedRouteKey=`live:${id}`;
    $('selected-track-title').textContent=`${data.vehicle_name} · ${localDate(data.started_utc,false)}`;$('selected-track-points').textContent=`${points.length} live points`;
    $('selected-track-stats').innerHTML=routeStats(points,data);
    $('track-map-status').textContent=`${data.status==='active'?'Active capture':'Recorded drive'} · GPS distance ${km(data.distance_km)} · estimated fuel ${money(data.estimated_fuel_cost_inr)}`;
    renderMap('track-map',updateOverview);renderMap('map',updateOverview);renderTracks();
  }catch(e){toast(`Could not open drive: ${e.message}`,true);}
}
function showCandidate(id) {
  const c=state.candidates.find(x=>Number(x.id)===Number(id));if(!c)return;
  const points=normalizeCandidate(c);state.selectedRoute={type:'candidate',id:Number(id),title:`Candidate · ${localDate(c.start_utc,false)}`,vehicleName:'Unassigned',points,candidate:c};state.loadedRouteKey=`candidate:${id}`;
  $('selected-track-title').textContent=state.selectedRoute.title;$('selected-track-points').textContent=`${points.length} samples`;$('selected-track-stats').innerHTML=routeStats(points,c);
  $('track-map-status').textContent=`Heuristic candidate · sampled path ${km(c.estimated_path_km)}; not odometer-verified.`;
  renderMap('track-map',true);renderMap('map',true);renderTracks();
}
function routeStats(points, meta=null) {
  const speeds=points.map(p=>p.speed_mps).filter(x=>Number.isFinite(x)&&x>=0).map(x=>x*3.6);
  const distance=Number(meta?.distance_km ?? meta?.estimated_path_km);
  const displayedDistance=Number.isFinite(distance)&&distance>0?km(distance):roughDistance(points);
  const elapsed=meta?.duration_min!=null?duration(meta.duration_min):(points.length>1?duration((new Date(points.at(-1).ts_utc)-new Date(points[0].ts_utc))/60000):'—');
  return `<span class="track-stat"><b>${esc(displayedDistance)}</b> distance</span><span class="track-stat"><b>${points.length}</b> points</span><span class="track-stat"><b>${elapsed}</b> duration</span><span class="track-stat"><b>${speeds.length?fixed(Math.max(...speeds),0)+' km/h':'—'}</b> GPS peak</span>`;
}
function roughDistance(points) {let m=0;for(let i=1;i<points.length;i++){if(points[i].ts_utc&&points[i-1].ts_utc&&((new Date(points[i].ts_utc)-new Date(points[i-1].ts_utc))/1000)>300)continue;m+=haversine(points[i-1].lat,points[i-1].lon,points[i].lat,points[i].lon);}return m>0?km(m/1000):'—';}
function haversine(lat1,lon1,lat2,lon2){const r=Math.PI/180,d1=(lat2-lat1)*r,d2=(lon2-lon1)*r,a=Math.sin(d1/2)**2+Math.cos(lat1*r)*Math.cos(lat2*r)*Math.sin(d2/2)**2;return 6371000*2*Math.asin(Math.min(1,Math.sqrt(a)));}
function loadMostRecentRoute() {
  if(state.selectedRoute)return;
  const vehicleId=Number(state.activeVehicleId);
  const live=state.liveDrives.find(d=>Number(d.vehicle_id)===vehicleId);
  if(live){selectLiveDrive(live.id);return;}
  const track=state.tracks.find(t=>Number(t.vehicle_id)===vehicleId&&t.point_count>0);
  if(track){selectTrack(track.id);return;}
  const candidate=state.candidates[0];if(candidate){showCandidate(candidate.id);return;}
  renderMap('map',true);renderMap('track-map',true);
}

/* Lightweight slippy map: only requests the raster tiles currently visible in the map viewport. */
function project(lat,lon,z){const s=256*Math.pow(2,z),clat=Math.max(-85.05112878,Math.min(85.05112878,lat)),sin=Math.sin(clat*Math.PI/180);return{x:(lon+180)/360*s,y:(0.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*s};}
function unproject(x,y,z){const s=256*Math.pow(2,z),lon=x/s*360-180,n=Math.PI-2*Math.PI*y/s,lat=180/Math.PI*Math.atan(Math.sinh(n));return{lat,lon};}
function mapBounds(points){const p=points.filter(x=>Number.isFinite(x.lat)&&Number.isFinite(x.lon));if(!p.length)return null;return{minLat:Math.min(...p.map(x=>x.lat)),maxLat:Math.max(...p.map(x=>x.lat)),minLon:Math.min(...p.map(x=>x.lon)),maxLon:Math.max(...p.map(x=>x.lon))};}
function fitView(view){const b=mapBounds(view.points);if(!b)return;const lat=(b.minLat+b.maxLat)/2,lon=(b.minLon+b.maxLon)/2;const w=Math.max(240,view.el.clientWidth-110),h=Math.max(150,view.el.clientHeight-95);let best=4;for(let z=2;z<=18;z++){const p1=project(b.maxLat,b.minLon,z),p2=project(b.minLat,b.maxLon,z);if(Math.abs(p2.x-p1.x)<=w&&Math.abs(p2.y-p1.y)<=h)best=z;else break;}view.zoom=best;view.center={lat,lon};}
function setTileStatus(view,message,error=true) {
  if(!view)return;
  if(!message){view.tileStatus.classList.add('hidden');view.tileStatus.textContent='';return;}
  view.tileStatus.classList.remove('hidden');view.tileStatus.dataset.kind=error?'error':'info';view.tileStatus.textContent=message;
}
function mapTileUrl(z,x,y) {
  if(state.mapProvider==='carto') {
    if(!state.cartoKey)return null;
    return `https://basemaps.cartocdn.com/rastertiles/voyager/${z}/${x}/${y}.png?key=${encodeURIComponent(state.cartoKey)}`;
  }
  return `https://tile.openstreetmap.org/${z}/${x}/${y}.png`;
}
function mapAttribution(view) {
  view.attrib.innerHTML=state.mapProvider==='carto'
    ? '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a> · <a href="https://carto.com/attributions" target="_blank" rel="noopener noreferrer">© CARTO</a>'
    : '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a>';
}
function clearMapTiles(view) {view.tileNodes.forEach(img=>img.remove());view.tileNodes.clear();view.visibleTileKeys.clear();view.tiles.replaceChildren();}
function resetAllMapTiles() {for(const view of Object.values(state.mapViews)){clearMapTiles(view);mapAttribution(view);renderMap(view.id);}}
function ensureMap(id){
  if(state.mapViews[id])return state.mapViews[id];const el=$(id);if(!el)return null;
  el.innerHTML=`<div class="map-tile-layer" aria-hidden="true"></div><svg class="map-overlay" role="img" aria-label="Selected GPS route" preserveAspectRatio="none"><g class="map-route-layer"></g></svg><div class="map-controls"><button type="button" data-map-action="in" aria-label="Zoom in">+</button><button type="button" data-map-action="out" aria-label="Zoom out">−</button><button type="button" data-map-action="fit">Fit</button><button type="button" data-map-action="reload" aria-label="Retry map tiles">↻</button></div><div class="map-attribution"><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a></div><div class="map-tile-status hidden" role="status" aria-live="polite"></div><div class="map-fallback hidden">Street tiles hidden. Route coordinates stay local.</div>`;
  const view={id,el,tiles:el.querySelector('.map-tile-layer'),svg:el.querySelector('.map-overlay'),route:el.querySelector('.map-route-layer'),controls:el.querySelector('.map-controls'),attrib:el.querySelector('.map-attribution'),fallback:el.querySelector('.map-fallback'),tileStatus:el.querySelector('.map-tile-status'),center:{lat:19.076,lon:72.8777},zoom:11,points:[],fitPending:false,drag:null,tileNodes:new Map(),visibleTileKeys:new Set(),failedTiles:new Set()};state.mapViews[id]=view;mapAttribution(view);
  view.controls.querySelectorAll('button').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();const action=b.dataset.mapAction;if(action==='fit')fitView(view);else if(action==='reload'){clearMapTiles(view);view.failedTiles.clear();setTileStatus(view,'Retrying map tiles…',false);}else view.zoom=Math.max(3,Math.min(19,view.zoom+(action==='in'?1:-1)));renderMap(id);}));
  el.addEventListener('pointerdown',e=>{if(e.target.closest('.map-controls,.map-attribution,.map-tile-status'))return;view.drag={x:e.clientX,y:e.clientY,center:project(view.center.lat,view.center.lon,view.zoom)};el.setPointerCapture?.(e.pointerId);el.classList.add('dragging');});
  el.addEventListener('pointermove',e=>{if(!view.drag)return;const dx=e.clientX-view.drag.x,dy=e.clientY-view.drag.y;view.center=unproject(view.drag.center.x-dx,view.drag.center.y-dy,view.zoom);renderMap(id);});
  const finish=()=>{view.drag=null;el.classList.remove('dragging');};el.addEventListener('pointerup',finish);el.addEventListener('pointercancel',finish);
  el.addEventListener('wheel',e=>{e.preventDefault();view.zoom=Math.max(3,Math.min(19,view.zoom+(e.deltaY<0?1:-1)));renderMap(id);},{passive:false});
  if(window.ResizeObserver){view.ro=new ResizeObserver(()=>renderMap(id));view.ro.observe(el);}
  return view;
}
function renderMap(id,fitIfNeeded=false){
  const view=ensureMap(id);if(!view)return;
  if(state.selectedRoute?.points)view.points=state.selectedRoute.points;else if(!view.points.length)view.points=[];
  if(fitIfNeeded&&view.points.length)view.fitPending=true;
  const w=view.el.clientWidth,h=view.el.clientHeight;if(w<40||h<40)return;
  if(view.fitPending&&view.points.length){fitView(view);view.fitPending=false;}
  const center=project(view.center.lat,view.center.lon,view.zoom),left=center.x-w/2,top=center.y-h/2;
  view.el.classList.toggle('tiles-disabled',!state.basemapVisible);view.tiles.hidden=!state.basemapVisible;view.attrib.hidden=!state.basemapVisible;view.fallback.classList.toggle('hidden',state.basemapVisible);
  if(state.basemapVisible){
    if(state.mapProvider==='carto'&&!state.cartoKey){clearMapTiles(view);setTileStatus(view,'CARTO Voyager needs a free personal API key. Open “Map source and privacy” above, save your key, then retry.',true);}
    else {
      const x0=Math.floor(left/256),x1=Math.floor((left+w)/256),y0=Math.floor(top/256),y1=Math.floor((top+h)/256),n=Math.pow(2,view.zoom),visible=new Set();let newTiles=0;
      for(let ty=y0;ty<=y1;ty++){if(ty<0||ty>=n)continue;for(let tx=x0;tx<=x1;tx++){
        const key=`${state.mapProvider}/${view.zoom}/${tx}/${ty}`;const sourceX=((tx%n)+n)%n;visible.add(key);let img=view.tileNodes.get(key);
        if(!img){img=document.createElement('img');img.alt='';img.draggable=false;img.decoding='async';img.loading='eager';img.referrerPolicy='strict-origin-when-cross-origin';img.dataset.tileKey=key;img.dataset.failed='false';img.addEventListener('error',()=>{img.dataset.failed='true';view.failedTiles.add(img.dataset.tileKey);if(view.visibleTileKeys.has(img.dataset.tileKey))setTileStatus(view,`Map tiles failed from ${state.mapProvider==='osm'?'OpenStreetMap':'CARTO Voyager'}. Check whether Pi-hole or the browser is blocking the tile host, then retry or change the source.`,true);});img.addEventListener('load',()=>{img.dataset.failed='false';view.failedTiles.delete(img.dataset.tileKey);const failed=[...view.tileNodes.values()].some(node=>node.dataset.failed==='true'&&view.visibleTileKeys.has(node.dataset.tileKey));if(!failed)setTileStatus(view,'');});img.src=mapTileUrl(view.zoom,sourceX,ty);view.tileNodes.set(key,img);view.tiles.appendChild(img);newTiles++;}
        img.style.left=`${tx*256-left}px`;img.style.top=`${ty*256-top}px`;
      }}
      for(const [key,img] of view.tileNodes){if(!visible.has(key)){img.remove();view.tileNodes.delete(key);view.failedTiles.delete(key);}}
      view.visibleTileKeys=visible;
      const hasVisibleFailure=[...view.tileNodes.values()].some(node=>node.dataset.failed==='true'&&visible.has(node.dataset.tileKey));
      if(hasVisibleFailure)setTileStatus(view,`Map tiles failed from ${state.mapProvider==='osm'?'OpenStreetMap':'CARTO Voyager'}. Check Pi-hole/browser filtering, then retry or change the source.`,true);
      else if(newTiles)setTileStatus(view,'Loading map tiles…',false);
      else if(![...view.tileNodes.values()].some(node=>node.complete))setTileStatus(view,'Loading map tiles…',false);
      else setTileStatus(view,'');
    }
  }else{clearMapTiles(view);setTileStatus(view,'');}
  view.svg.setAttribute('viewBox',`0 0 ${w} ${h}`);view.svg.setAttribute('width',w);view.svg.setAttribute('height',h);
  const pts=view.points||[];
  if(!pts.length){view.route.innerHTML=`<text x="${w/2}" y="${h/2-8}" text-anchor="middle" class="map-empty-title">Select a recorded route</text><text x="${w/2}" y="${h/2+16}" text-anchor="middle" class="map-empty-sub">Choose a GPS track or drive from the list.</text>`;return;}
  const xy=pts.map(p=>{const q=project(p.lat,p.lon,view.zoom);return{x:q.x-left,y:q.y-top,p};});
  const segments=[];let segment=[];
  for(let i=0;i<xy.length;i++){const cur=xy[i],prev=xy[i-1];const gap=prev&&prev.p.ts_utc&&cur.p.ts_utc?(new Date(cur.p.ts_utc)-new Date(prev.p.ts_utc))/1000:0;if(prev&&(gap>300||haversine(prev.p.lat,prev.p.lon,cur.p.lat,cur.p.lon)>12000)){if(segment.length)segments.push(segment);segment=[];}segment.push(cur);}if(segment.length)segments.push(segment);
  const d=segments.map(seg=>seg.length===1?`M ${seg[0].x.toFixed(1)} ${seg[0].y.toFixed(1)}`:'M '+seg.map(p=>`${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' L ')).join(' ');
  const start=xy[0],end=xy.at(-1);const circles=`<circle cx="${start.x}" cy="${start.y}" r="6" class="route-start"><title>Start ${esc(start.p.ts_utc?localDate(start.p.ts_utc):'')}</title></circle><circle cx="${end.x}" cy="${end.y}" r="6" class="route-end"><title>End ${esc(end.p.ts_utc?localDate(end.p.ts_utc):'')}</title></circle>`;
  const sampleMarks=xy.filter((o,i)=>o.p.source==='network'&&i%Math.max(1,Math.ceil(xy.length/65))===0).map(o=>`<circle cx="${o.x}" cy="${o.y}" r="3" class="route-low-confidence"><title>Lower confidence network-derived point</title></circle>`).join('');
  view.route.innerHTML=`<path d="${d}" class="route-casing"/><path d="${d}" class="route-line"/>${sampleMarks}${circles}`;
}

function setBasemap(visible){
  state.basemapVisible=visible;
  document.querySelectorAll('[data-toggle-basemap]').forEach(b=>{b.textContent=visible?'Hide tiles':'Show tiles';b.setAttribute('aria-pressed',String(visible));});
  Object.keys(state.mapViews).forEach(renderMap);
}
function setMapProvider(provider){
  state.mapProvider=provider==='carto'?'carto':'osm';localStorage.setItem('knowhere-blackbox-map-provider',state.mapProvider);
  document.querySelectorAll('[data-map-provider]').forEach(s=>s.value=state.mapProvider);
  for(const v of Object.values(state.mapViews)){clearMapTiles(v);mapAttribution(v);setTileStatus(v,'');}
  Object.keys(state.mapViews).forEach(renderMap);
}
function saveCartoKey(input){const key=String(input.value||'').trim();state.cartoKey=key;if(key)localStorage.setItem('knowhere-blackbox-carto-key',key);else localStorage.removeItem('knowhere-blackbox-carto-key');document.querySelectorAll('[data-carto-key]').forEach(el=>el.value=key);toast(key?'CARTO key saved in this browser only.':'CARTO key removed from this browser.');if(state.mapProvider==='carto')resetAllMapTiles();}
function setupExternalMap(){
  document.querySelectorAll('[data-toggle-basemap]').forEach(b=>b.addEventListener('click',()=>setBasemap(!state.basemapVisible)));
  document.querySelectorAll('[data-map-provider]').forEach(s=>{s.value=state.mapProvider;s.addEventListener('change',()=>setMapProvider(s.value));});
  document.querySelectorAll('[data-carto-key]').forEach(el=>el.value=state.cartoKey);
  document.querySelectorAll('.save-carto-key').forEach(b=>b.addEventListener('click',()=>saveCartoKey(b.closest('.map-key-row').querySelector('[data-carto-key]'))));
  document.querySelectorAll('[data-reload-map]').forEach(b=>b.addEventListener('click',()=>{for(const v of Object.values(state.mapViews)){clearMapTiles(v);setTileStatus(v,'Retrying map tiles…',false);}Object.keys(state.mapViews).forEach(renderMap);}));
  $('focus-track').addEventListener('click',()=>{for(const v of Object.values(state.mapViews)){if(v.points?.length)fitView(v);}Object.keys(state.mapViews).forEach(renderMap);});
}

function renderFuel() {
  if(!state.summary)return;
  $('fuel-count-kpi').textContent=state.summary.fuel.count;$('fuel-spend-kpi').textContent=money(state.summary.fuel.cost_inr);$('other-cost-kpi').textContent=money(state.summary.costs.total_inr);
  $('fuel-empty').classList.toggle('hidden',state.fuel.length>0);$('cost-empty').classList.toggle('hidden',state.costs.length>0);
  $('fuel-table').innerHTML=state.fuel.map(f=>`<tr><td>${fuelDate(f.date_local)}<span class="td-sub">${esc(f.vehicle_name||'Unassigned')}</span></td><td class="num">${f.odometer_km==null?'—':`${fixed(f.odometer_km,0)} km`}</td><td class="num">${fixed(f.litres,2)} ${f.fuel_type==='ev'?'kWh':f.fuel_type==='cng'?'kg':'L'}</td><td>${f.full_tank===1?'Yes':f.full_tank===0?'No':'—'}</td><td class="num">${f.reported_km_per_l==null?'—':`${fixed(f.reported_km_per_l,2)} ${f.fuel_type==='ev'?'km/kWh':f.fuel_type==='cng'?'km/kg':'km/L'}`}</td><td class="num">${money(f.price_inr,true)}</td><td><span class="source-tag ${f.entry_source==='Black Box'?'source-manual':'source-history'}">${esc(f.entry_source||'Historical import')}</span></td></tr>`).join('');
  $('cost-table').innerHTML=state.costs.map(c=>`<tr><td>${fuelDate(c.date_local)}<span class="td-sub">${esc(c.vehicle_name||'Unassigned')}</span></td><td>${esc(c.title||'Unlabelled')}</td><td class="num">${c.odometer_km==null?'—':`${fixed(c.odometer_km,0)} km`}</td><td class="num">${money(c.cost_inr,true)}</td><td><span class="source-tag ${c.entry_source==='Black Box'?'source-manual':'source-history'}">${esc(c.entry_source||'Historical import')}</span></td></tr>`).join('');
}
function renderQuality() {
  const issues=state.quality||[],box=$('quality-list');
  box.innerHTML=issues.length?issues.map(i=>`<article class="quality-card level-${esc(i.level)}"><div class="quality-level">${esc(i.level.toUpperCase())}</div><div class="quality-body"><strong>${esc(i.title)}</strong><p>${esc(i.detail)}</p></div></article>`).join(''):'<div class="notice notice-info"><span class="notice-icon">✓</span><div><strong>No known issues detected</strong><span>This does not guarantee GPS accuracy or data completeness.</span></div></div>';
  const imports=state.imports||[];$('imports-empty').classList.toggle('hidden',imports.length>0);
  $('imports-table').innerHTML=imports.map(i=>`<tr><td class="td-main">${esc(i.filename)}</td><td>${esc(i.kind)}</td><td class="num">${i.record_count}</td><td class="num">${esc(i.imported_at)}</td><td class="num">${esc(i.sha256.slice(0,16))}…</td></tr>`).join('');
}
function renderVehicleCards() {
  const box=$('vehicle-cards');if(!box)return;
  box.innerHTML=state.vehicles.map(v=>{
    const selected=Number(v.id)===Number(state.activeVehicleId),ready=Boolean(v.effective_km_per_l&&v.effective_fuel_price_per_litre),last=v.last_point_received_at?`Last GPS ${localDate(v.last_point_received_at)}`:'No live GPS yet';
    return `<button class="vehicle-card ${selected?'selected':''}" data-vehicle-id="${v.id}"><span class="vehicle-card-top"><span class="vehicle-type-icon">${v.fuel_type==='ev'?'EV':'CAR'}</span><span class="vehicle-state ${ready?'ready':'needs-setup'}">${ready?'Cost model ready':'Set cost model'}</span></span><strong>${esc(v.name)}</strong><span class="vehicle-meta">${esc([v.make,v.model,v.year].filter(Boolean).join(' ')||'Add make and model')}</span><span class="vehicle-card-bottom"><span>${ready?`${fixed(v.effective_km_per_l,1)} ${unitsFor(v).eff} · ${money(v.effective_fuel_price_per_litre,true)} ${unitsFor(v).price}`:'Efficiency / unit price missing'}</span><span>${v.active_drive?'Drive active':esc(last)}</span></span></button>`;
  }).join('');
  box.querySelectorAll('[data-vehicle-id]').forEach(b=>b.addEventListener('click',()=>{setActiveVehicle(Number(b.dataset.vehicleId));$('vehicle-edit-id').value=String(b.dataset.vehicleId);renderVehicleForm();}));
}
function unitsFor(v) {
  const type=v?.fuel_type||'petrol';
  if(type==='ev') return {eff:'km/kWh',price:'per kWh',qty:'Energy added (kWh)',unit:'kWh'};
  if(type==='cng') return {eff:'km/kg',price:'per kg',qty:'CNG added (kg)',unit:'kg'};
  return {eff:'km/L',price:'per litre',qty:'Fuel added (litres)',unit:'L'};
}
function updateFuelUnits(){
  const id=Number($('fuel-vehicle-id')?.value||state.activeVehicleId);
  const v=state.vehicles.find(x=>Number(x.id)===id)||selectedVehicle();
  const label=$('fuel-quantity-label'); if(label) label.textContent=unitsFor(v).qty;
}
function renderVehicleForm() {
  const v=state.vehicles.find(x=>Number(x.id)===Number($('vehicle-edit-id')?.value))||selectedVehicle();if(!v)return;
  $('vehicle-edit-id').value=String(v.id);$('vehicle-name').value=v.name||'';$('vehicle-make').value=v.make||'';$('vehicle-model').value=v.model||'';$('vehicle-year').value=v.year||'';$('vehicle-fuel-type').value=v.fuel_type||'petrol';$('vehicle-km-per-l').value=v.km_per_l??'';$('vehicle-fuel-price').value=v.fuel_price_per_litre??'';$('vehicle-connection-name').value=v.connection_name||'';
  const u=unitsFor(v);
  $('vehicle-efficiency-label').textContent=`Expected efficiency (${u.eff})`;
  $('vehicle-price-label').textContent=`Fuel / energy price ${u.price} (₹)`;
  updateFuelUnits();
  const model=$('vehicle-model-note');
  if(model) model.textContent=v.effective_km_per_l&&v.effective_fuel_price_per_litre
    ? `Current model: ${fixed(v.effective_km_per_l,2)} ${u.eff} (${v.efficiency_source||'unknown'}), ${money(v.effective_fuel_price_per_litre,true)} ${u.price} (${v.fuel_price_source||'unknown'}); estimated ${money(v.estimated_cost_per_km_inr,true)} per km.`
    : 'Enter an efficiency and unit price, or log fuel entries so the model can learn from records.';
}

function renderVehicleUrl() {
  const v=selectedVehicle();if(!v)return;
  const base=($('ingest-base-url').value||'').trim().replace(/\/+$/,'');
  const url=`${base}/api/location?key=${v.ingest_key}&lat=%LAT&lon=%LON&timestamp=%TIME&speed=%SPD&accuracy=%ACC&altitude=%ALT&bearing=%DIR`;
  $('vehicle-gps-url').value=url;
  $('gps-url-status').textContent=`Profile: ${v.name} · ${v.connection_name||'Bluetooth device name not configured'} · ${v.effective_km_per_l?`${fixed(v.effective_km_per_l,1)} ${unitsFor(v).eff}`:'efficiency needed'} · ${v.effective_fuel_price_per_litre?`${money(v.effective_fuel_price_per_litre,true)} ${unitsFor(v).price}`:'unit price needed'}`;
}
function renderLiveDrives() {
  const rows=state.liveDrives||[];$('live-drives-empty').classList.toggle('hidden',rows.length>0);
  $('live-drives-table').innerHTML=rows.slice(0,25).map(d=>`<tr><td>${esc(d.vehicle_name)}</td><td>${localDate(d.started_utc)}</td><td>${duration(d.duration_min)}</td><td class="table-distance">${km(d.distance_km)}</td><td>${d.estimated_fuel_cost_inr==null?'<span class="muted">Configure this vehicle\'s model</span>':`${money(d.estimated_fuel_cost_inr)}<span class="td-sub">${fixed(d.estimated_fuel_litres,2)} ${unitsFor(state.vehicles.find(v=>Number(v.id)===Number(d.vehicle_id))).unit} estimated</span>`}</td><td><span class="source-tag ${d.status==='active'?'source-active':'source-history'}">${d.status==='active'?'ACTIVE':'COMPLETED'}</span></td></tr>`).join('');
}
function setupVehiclePage() {
  $('active-vehicle').addEventListener('change',()=>setActiveVehicle(Number($('active-vehicle').value)));
  $('vehicle-edit-id').addEventListener('change',()=>{setActiveVehicle(Number($('vehicle-edit-id').value));renderVehicleForm();});
  $('fuel-vehicle-id').addEventListener('change',updateFuelUnits);
  $('vehicle-fuel-type').addEventListener('change',()=>{const type=$('vehicle-fuel-type').value;const u=unitsFor({fuel_type:type});$('vehicle-efficiency-label').textContent=`Expected efficiency (${u.eff})`;$('vehicle-price-label').textContent=`Fuel / energy price ${u.price} (₹)`;const id=Number($('fuel-vehicle-id').value);const v=state.vehicles.find(x=>Number(x.id)===id);if(v)updateFuelUnits();});
  $('ingest-base-url').addEventListener('input',renderVehicleUrl);
  $('copy-gps-url').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('vehicle-gps-url').value);toast('GPSLogger URL copied. Keep it private; it contains a write key.');}catch{ $('vehicle-gps-url').select();toast('Clipboard blocked by browser; select and copy the URL.');}});
  $('vehicle-form').addEventListener('submit',async e=>{
    e.preventDefault();const btn=$('save-vehicle'),err=$('vehicle-form-error');btn.disabled=true;err.classList.add('hidden');
    const f=Object.fromEntries(new FormData(e.currentTarget).entries());const payload={id:Number(f.id),name:f.name,make:f.make,model:f.model,year:f.year,fuel_type:f.fuel_type,km_per_l:f.km_per_l===''?null:Number(f.km_per_l),fuel_price_per_litre:f.fuel_price_per_litre===''?null:Number(f.fuel_price_per_litre),connection_name:f.connection_name};
    try{const data=await api('/api/vehicles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});await loadAll();setActiveVehicle(Number(data.vehicle.id));toast('Vehicle profile saved. Cost estimates will update on incoming GPS points.');}
    catch(ex){err.textContent=ex.message;err.classList.remove('hidden');}finally{btn.disabled=false;}
  });
}
function setupImport() {
  const dialog=$('import-dialog'),input=$('file-input'),zone=$('dropzone');
  $('open-import').addEventListener('click',()=>{if($('import-vehicle-id'))$('import-vehicle-id').value=String(state.activeVehicleId||'');dialog.showModal();});$('track-import-shortcut').addEventListener('click',()=>dialog.showModal());
  input.addEventListener('change',renderSelectedFiles);
  $('clear-files').addEventListener('click',()=>{input.value='';renderSelectedFiles();$('import-result').classList.add('hidden');});
  for(const ev of ['dragenter','dragover'])zone.addEventListener(ev,e=>{e.preventDefault();zone.classList.add('dragover');});
  for(const ev of ['dragleave','drop'])zone.addEventListener(ev,e=>{e.preventDefault();zone.classList.remove('dragover');});
  zone.addEventListener('drop',e=>{if(e.dataTransfer?.files?.length){input.files=e.dataTransfer.files;renderSelectedFiles();}});
  $('start-import').addEventListener('click',runImport);
}
function indiaLocalDateTime(){const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(p=>[p.type,p.value]));return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;}
function prepareManualDialog(dialogId,formId,dateId,errorId){
  const dialog=$(dialogId),form=$(formId),date=$(dateId),error=$(errorId);
  const open=()=>{error.classList.add('hidden');error.textContent='';if(!date.value)date.value=indiaLocalDateTime();if(form.elements.vehicle_id)form.elements.vehicle_id.value=String(state.activeVehicleId||state.vehicles[0]?.id||'');if(dialogId==='fuel-dialog')updateFuelUnits();dialog.showModal();};
  if(dialogId==='fuel-dialog')$('add-fuel').addEventListener('click',open);else $('add-cost').addEventListener('click',open);
  const close=()=>dialog.close();$(dialogId==='fuel-dialog'?'close-fuel-dialog':'close-cost-dialog').addEventListener('click',close);$(dialogId==='fuel-dialog'?'cancel-fuel':'cancel-cost').addEventListener('click',close);
  form.addEventListener('submit',async e=>{
    e.preventDefault();error.classList.add('hidden');error.textContent='';const button=$(dialogId==='fuel-dialog'?'save-fuel':'save-cost');button.disabled=true;button.textContent='Saving…';
    const raw=Object.fromEntries(new FormData(form).entries()),payload={...raw};
    for(const key of ['vehicle_id','odometer_km','litres','price_inr','cost_inr'])if(key in payload)payload[key]=payload[key]===''?null:Number(payload[key]);
    if('full_tank' in payload)payload.full_tank=payload.full_tank==='yes';
    try{await api(dialogId==='fuel-dialog'?'/api/fuel':'/api/costs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});form.reset();date.value=indiaLocalDateTime();dialog.close();await loadAll();goTab('fuel');toast(dialogId==='fuel-dialog'?'Refuelling saved. The fuel model may update from valid records.':'Expense saved.');}
    catch(err){error.textContent=err.message;error.classList.remove('hidden');}finally{button.disabled=false;button.textContent=dialogId==='fuel-dialog'?'Save fill-up':'Save expense';}
  });
}
function setupManualEntries(){$('fuel-date').value=indiaLocalDateTime();$('cost-date').value=indiaLocalDateTime();prepareManualDialog('fuel-dialog','fuel-entry-form','fuel-date','fuel-form-error');prepareManualDialog('cost-dialog','cost-entry-form','cost-date','cost-form-error');updateFuelUnits();}
function renderSelectedFiles(){const files=[...$('file-input').files];$('file-list').textContent=files.length?files.map(f=>`${f.name} · ${Math.round(f.size/1024)} KB`).join('\n'):'No files selected';}
async function runImport(){
  const files=[...$('file-input').files];if(!files.length){toast('Choose at least one CSV or GPX file.',true);return;}
  const btn=$('start-import');btn.disabled=true;btn.textContent='Importing…';const form=new FormData();files.forEach(f=>form.append('files',f,f.name));if($('import-vehicle-id').value)form.append('vehicle_id',$('import-vehicle-id').value);
  try{const data=await api('/api/import',{method:'POST',body:form});$('import-result').classList.remove('hidden');$('import-result').innerHTML=data.results.map(r=>`<div class="result-row"><strong class="${r.status==='error'?'result-err':'result-ok'}">${esc(r.status==='error'?'ERROR':r.status.toUpperCase())}</strong> · ${esc(r.filename)} · ${r.records||0} records${r.error?`<br>${esc(r.error)}`:''}</div>`).join('');await loadAll();toast('Import finished. Review the result details.',data.results.some(r=>r.status==='error'));}
  catch(e){toast(e.message,true);}finally{btn.disabled=false;btn.textContent='Import selected files';}
}
function setupDatabaseDownload(){document.querySelectorAll('a[href="/api/export/database"]').forEach(a=>a.addEventListener('click',()=>toast('Preparing a private SQLite backup…')));}
function setupFilters(){$('trip-search').addEventListener('input',renderTrips);$('trip-month').addEventListener('change',renderTrips);}
function setupTimeline(){const button=$('toggle-days');if(!button)return;button.addEventListener('click',()=>{const all=state.timelineDateKeys.length;const allOpen=state.expandedTimelineDays===null||state.expandedTimelineDays.size===all;state.expandedTimelineDays=allOpen?new Set():null;renderTracks();});}
window.addEventListener('DOMContentLoaded',()=>{
  initNavigation();setupImport();setupManualEntries();setupDatabaseDownload();setupExternalMap();setupFilters();setupTimeline();setupVehiclePage();renderSelectedFiles();loadAll();
  window.setInterval(refreshLive,15000);
});
