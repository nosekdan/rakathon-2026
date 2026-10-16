const state = {data:null, patient:null, demo:null, mode:'real', view:'patient', trialId:null, debugTab:'patients', filter:'all', query:'', page:0, expanded:new Set(), job:null};
const labels = {met:'Splněno', not_met:'Nesplněno', unknown:'Lidská kontrola', missing:'Chybí údaje'};
const statusIcons = {met:'circle-check', not_met:'circle-x', unknown:'circle-help', missing:'file-question'};
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const icon = name => `<i data-lucide="${name}"></i>`;
const date = value => value ? new Date(value).toLocaleDateString('cs-CZ') : 'Neuvedeno';
const pill = status => `<span class="status-pill status-${status}">${icon(statusIcons[status])}${labels[status]}</span>`;
const allTrials = () => state.mode === 'demo' ? state.demo.trials : state.data.trials;
const trialById = identifier => allTrials().find(trial => trial.id === identifier);
const currentResults = () => state.patient?.results || [];
const refreshIcons = () => globalThis.lucide?.createIcons();
let patientRequest = 0;
let toastTimer;

async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
  }
  return response.json();
}

function toast(message) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {element.hidden=true;}, 6500);
}

function evidenceButton(evidence, title='Zobrazit zdroj', extraClass='icon-button') {
  return `<button class="${extraClass}" data-source="${escapeHtml(evidence.source_id)}" data-quote="${escapeHtml(evidence.quote || '')}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${icon('file-text')}${extraClass === 'evidence-link' ? escapeHtml(title) : ''}</button>`;
}

function sourceTitle(identifier, result=null) {
  const source = state.patient.sources.find(item => item.id === identifier) || result?.registry?.sources?.[identifier];
  return source?.title || (identifier === 'registry' ? 'ClinicalTrials.gov / kritéria' : identifier === 'demo-protocol' ? 'Syntetický protokol' : identifier);
}

function patientSelector() {
  const patients = state.mode === 'demo' ? [state.demo.patient] : [state.patient, ...state.data.patients.filter(patient => patient.id !== state.patient.id).slice(0,11)];
  const options = patients.map(patient => `<option value="${escapeHtml(patient.id)}" ${patient.id === state.patient?.id ? 'selected' : ''}>${escapeHtml(patient.name)} · ${escapeHtml(patient.diagnoses || 'bez kódu')}</option>`).join('');
  return `<div class="patient-selector"><span>Pacient</span><select id="patient-select" aria-label="Vybrat pacienta">${options}</select><div class="search-field">${icon('search')}<input id="patient-search" placeholder="Hledat ID nebo diagnózu" aria-label="Hledat pacienta"></div></div>`;
}

function heading(title, subtitle, action=true) {
  return `<div class="page-heading"><div><div class="eyebrow">ONKOLOGICKÁ AMBULANCE</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${action ? `<button class="primary-button" data-action="screen">${icon('scan-line')}Posoudit studie</button>` : ''}</div>`;
}

function renderPatient() {
  const patient = state.patient;
  const labPriority = lab => /hemoglobin|kreatinin|neutrofil|trombocyt|bilirubin|albumin|^PSA$/i.test(lab.name) ? 0 : typeof lab.value === 'number' ? 1 : 2;
  const displayedLabs = [...patient.latest_labs].sort((first,second)=>labPriority(first)-labPriority(second)).slice(0,7);
  const positive = currentResults().filter(result => result.summary.badge);
  const demographicsSource = patient.synthetic ? 'demo-note' : 'cohort';
  const ageEvidence = {source_id:demographicsSource,quote:patient.synthetic ? 'Věk: 64 let' : patient.age ? `${String(patient.age).includes('-') ? 'vek_pasmo' : 'vek'}: ${patient.age}` : ''};
  const genderEvidence = {source_id:demographicsSource,quote:patient.synthetic ? 'Pohlaví: muž' : `pohlavi: ${patient.sex}`};
  const metrics = [
    {name:'Pohlaví',value:patient.sex === 'male' ? 'Muž' : patient.sex === 'female' ? 'Žena' : 'Neuvedeno',icon:'user-round',evidence:genderEvidence},
    {name:'Věk',value:patient.age || '—',unit:patient.age ? 'let' : '',icon:'calendar-days',evidence:ageEvidence},
    {name:'Hmotnost',value:patient.vitals.weight?.value || '—',unit:patient.vitals.weight ? 'kg' : '',icon:'weight',evidence:patient.vitals.weight},
    {name:'Výška',value:patient.vitals.height?.value || '—',unit:patient.vitals.height ? 'cm' : '',icon:'ruler',evidence:patient.vitals.height},
    {name:'WHO / ECOG',value:patient.vitals.ecog?.value ?? '—',unit:patient.vitals.ecog ? '/ 4' : '',icon:'activity',evidence:patient.vitals.ecog}
  ];
  return heading('Karta pacienta', 'Ambulantní přehled a podklady pro klinické studie') + patientSelector() + `
    <section class="medical-sheet" aria-label="Zdravotní karta">
      <div class="identity-row"><div class="patient-avatar">${icon('user-round')}</div><div class="identity-copy"><h2>${escapeHtml(patient.name)}</h2><div class="identity-meta"><span class="id-label">${escapeHtml(patient.id)}</span><span>·</span><span>${patient.synthetic ? 'Syntetická ukázka' : 'Pseudonymizovaný záznam'}</span>${patient.deceased ? '<span class="status-pill status-not_met">Záznam o úmrtí</span>' : ''}</div></div><div class="record-meta">Poslední dostupný záznam<strong>${date(patient.snapshot_at)}</strong>${patient.synthetic ? 'Ukázková dokumentace' : 'Posunuté datum datasetu'}</div></div>
      <div class="metric-grid">${metrics.map(metric=>`<div class="metric"><div class="metric-label">${icon(metric.icon)}${metric.name}</div><div class="metric-value">${escapeHtml(metric.value)}<small>${metric.unit || ''}</small></div>${metric.evidence ? evidenceButton(metric.evidence,`Zdroj: ${metric.name}`) : '<div class="metric-foot">Údaj není doložen</div>'}</div>`).join('')}</div>
    </section>
    ${positive.length ? `<button class="eligibility-notice" data-action="open-matches"><span class="notice-icon">${icon('flask-conical')}</span><span><strong>${positive.length} ${positive.length === 1 ? 'studie k posouzení' : 'studie k posouzení'}</strong><small>Dostupná kritéria splněna · Ověřit způsobilost s týmem studie</small></span>${icon('arrow-up-right')}</button>` : ''}
    <div class="record-columns"><section><div class="section-heading">${icon('notebook-tabs')}<h3>Poslední záznamy</h3><span class="count">${patient.notes} zpráv</span></div>
      ${patient.recent_notes.length ? patient.recent_notes.slice(0,6).map(note=>`<button class="note-entry" data-source="${escapeHtml(note.id)}"><span class="note-date">${date(note.date)}</span><span><span class="note-title">${escapeHtml(note.title)}</span><span class="note-id">${escapeHtml(note.id)}</span></span>${icon('arrow-up-right')}</button>`).join('') : '<p class="empty-note">V dostupném exportu nejsou textové zprávy.</p>'}
      <div class="snapshot-note">${icon('clock-3')}Časové údaje odpovídají snapshotu dokumentace.</div></section>
      <div><section><div class="section-heading">${icon('clipboard-plus')}<h3>Diagnózy v záznamu</h3>${evidenceButton({source_id:demographicsSource,quote:patient.synthetic?'Diagnóza: karcinom prostaty C61.':`diagnozy: ${patient.diagnoses}`},'Zdroj diagnóz')}</div>${patient.diagnoses ? patient.diagnoses.split(/[,;\s]+/).filter(Boolean).map(code=>`<div class="diagnosis-line"><span class="code-tag">${escapeHtml(code)}</span><span>${code.startsWith('C61')?'Zhoubný novotvar prostaty':code.startsWith('C64')?'Zhoubný novotvar ledviny':'Diagnóza MKN-10'}</span></div>`).join('') : '<p class="empty-note">Kódy nejsou v kohortě uvedeny.</p>'}</section>
      <section class="section-gap"><div class="section-heading">${icon('test-tubes')}<h3>Laboratorní výsledky</h3><span class="count">Poslední hodnoty</span></div>${displayedLabs.length ? `<table class="mini-table"><tbody>${displayedLabs.map(lab=>`<tr><td>${escapeHtml(lab.name)}<div class="cell-subtitle">${date(lab.date)}</div></td><td>${escapeHtml(lab.value)} <span class="muted">${escapeHtml(lab.unit)}</span></td><td>${evidenceButton({source_id:lab.source_id},`Zdroj: ${lab.name}`)}</td></tr>`).join('')}</tbody></table>` : '<p class="empty-note">Laboratorní hodnoty nejsou v dostupném záznamu doloženy.</p>'}</section></div></div>`;
}

function resultProgress(result) {
  return `<div class="progress-track" aria-label="Rozložení stavů kritérií">${Object.entries(result.summary.counts).map(([status,count])=>`<span class="bg-${status}" style="width:${100*count/(result.summary.total||1)}%" title="${labels[status]}: ${count}"></span>`).join('')}</div>`;
}

function renderMatches() {
  const results = currentResults();
  const header = heading('Shoda se studiemi', `${escapeHtml(state.patient.name)} · Posouzení dostupných kritérií`) + patientSelector();
  if (!results.length) return header + `<div class="empty-state">${icon('clipboard-search')}<h2>Pacient zatím nebyl posouzen</h2><p>Žádný výsledek neznamená vyloučení. Pro tohoto pacienta zatím není uložené porovnání.</p><button class="primary-button" data-action="screen">${icon('scan-line')}Vybrat studie</button></div>`;
  if (!results.some(result=>result.trial_id===state.trialId)) state.trialId=results[0].trial_id;
  const result=results.find(item=>item.trial_id===state.trialId);
  const trial=trialById(state.trialId);
  const visible=result.decisions.filter(decision=>state.filter==='all'||decision.status===state.filter);
  return header + `<div class="matches-layout"><aside class="study-list" aria-label="Posouzené studie">${results.map(item=>{const study=trialById(item.trial_id);return `<button class="study-choice ${item.trial_id===state.trialId?'active':''}" data-trial="${item.trial_id}"><div class="study-protocol">${escapeHtml(study?.protocol||item.trial_id)}</div><div class="study-name">${escapeHtml(study?.title||'Klinická studie')}</div><div class="study-score"><span>${item.summary.counts.met}/${item.summary.total} kritérií splněno</span><strong title="Podíl splněných jednotlivých kritérií, nikoli pravděpodobnost způsobilosti">${item.summary.match_percent}%</strong></div>${resultProgress(item)}</button>`;}).join('')}</aside>
    <section class="match-detail"><div class="trial-head"><div class="eyebrow">${escapeHtml(trial?.protocol)} · ${state.mode==='demo'?'SYNTETICKÁ STUDIE':'REGISTROVANÁ STUDIE'}</div><h2>${escapeHtml(trial?.title)}</h2><div class="trial-links">${pill(result.summary.status)}${result.registry.clinical_eu_url?`<a href="${escapeHtml(result.registry.clinical_eu_url)}" target="_blank" rel="noreferrer">ClinicalTrials.eu ${icon('external-link')}</a>`:''}${result.registry.url?`<a href="${escapeHtml(result.registry.url)}" target="_blank" rel="noreferrer">${escapeHtml(result.registry.nct_id||'Registr')} ${icon('external-link')}</a>`:''}<span class="muted">${result.registry.site_recruiting?'Nábor na pracovišti uveden v registru':'Nábor na pracovišti nepotvrzen'}</span></div></div>
    <div class="stats-strip">${Object.entries(result.summary.counts).map(([status,count])=>`<div class="stat ${status}"><b>${count}</b>${labels[status]}</div>`).join('')}</div>
    ${result.future_watch.map(watch=>`<div class="watch-banner"><strong>${icon('calendar-clock')}${escapeHtml(watch.text)}</strong><small>${escapeHtml(watch.caveat)}</small></div>`).join('')}
    <div class="filter-row"><h3>Podmínky účasti <span class="muted">(${result.summary.total})</span></h3><select id="criterion-filter" aria-label="Filtrovat stav kritérií"><option value="all">Všechny stavy</option>${Object.entries(labels).map(([value,label])=>`<option value="${value}" ${state.filter===value?'selected':''}>${label}</option>`).join('')}</select></div>
    <div>${visible.length?visible.map(decision=>renderCriterion(decision,result)).join(''):'<p class="empty-note">V tomto stavu nejsou žádná kritéria.</p>'}</div>
    <div class="assessment-foot"><div>${result.summary.counts.met}/${result.summary.total} jednotlivých podmínek splněno. Skóre není pravděpodobnost způsobilosti; rozhoduje logika všech podmínek.</div><div>Snapshot: ${date(result.snapshot_at)} · Zpracováno: ${date(result.assessed_at)} · ${escapeHtml(result.model)}</div>${result.limitations.map(limit=>`<div>${escapeHtml(limit)}</div>`).join('')}</div></section></div>`;
}

function renderCriterion(decision,result) {
  const criterion=result.criteria.criteria.find(item=>item.id===decision.criterion_id);
  const expanded=state.expanded.has(decision.criterion_id);
  return `<article class="criterion-row"><button class="criterion-toggle" data-criterion="${escapeHtml(criterion.id)}" aria-expanded="${expanded}"><span class="criterion-index">${String(result.criteria.criteria.indexOf(criterion)+1).padStart(2,'0')}</span><span class="criterion-copy"><span class="criterion-title">${escapeHtml(criterion.requirement)}</span><span class="criterion-reason">${escapeHtml(decision.reason)}</span></span>${pill(decision.status)}${icon(expanded?'chevron-up':'chevron-down')}</button>${expanded?`<div class="evidence-area"><div class="evidence-block"><div class="evidence-label">POŽADAVEK STUDIE${criterion.role==='exclusion'?' · VYLUČOVACÍ PODMÍNKA':''}</div><div class="evidence-quote"><mark>${escapeHtml(criterion.source.quote)}</mark></div>${evidenceButton(criterion.source,'Původní znění kritéria','evidence-link')}</div><div class="evidence-label">DOKLAD V DOKUMENTACI</div>${decision.evidence.length?decision.evidence.map(evidence=>`<div class="evidence-block"><div class="evidence-quote"><mark>${escapeHtml(evidence.quote)}</mark></div>${evidenceButton(evidence,sourceTitle(evidence.source_id,result),'evidence-link')}</div>`).join(''):`<p class="empty-note">${decision.status==='missing'?'Potřebný údaj nebyl v posouzených zdrojích nalezen. Nejde o důkaz nesplnění.':'Pro tento závěr není ověřitelný doklad.'}</p><div class="cell-subtitle">Rozsah: ${result.patient_facts.source_ids.length} posouzených zdrojů</div>${result.patient_facts.source_ids.slice(0,4).map(identifier=>evidenceButton({source_id:identifier},sourceTitle(identifier,result),'evidence-link')).join('<br>')}`}${decision.action?`<div class="next-action">${icon('arrow-right')}<span>${escapeHtml(decision.action)}</span></div>`:''}</div>`:''}</article>`;
}

function renderDebug() {
  const patients=state.mode==='demo'?[state.demo.patient]:state.data.patients;
  const trials=allTrials();
  let records=state.debugTab==='patients'?patients:trials;
  records=records.filter(item=>JSON.stringify(item).toLocaleLowerCase('cs').includes(state.query.toLocaleLowerCase('cs')));
  const pageCount=Math.max(1,Math.ceil(records.length/20));
  state.page=Math.min(state.page,pageCount-1);
  const shown=records.slice(state.page*20,(state.page+1)*20);
  let content='';
  if(state.debugTab==='patients') content=`<table class="data-table"><thead><tr><th>Pacient</th><th>Věk / pohlaví</th><th>Diagnózy</th><th>Zprávy</th><th>Laboratoř</th><th></th></tr></thead><tbody>${shown.map(patient=>`<tr><td><span class="cell-title">${escapeHtml(patient.name)}</span><div class="cell-subtitle id-label">${escapeHtml(patient.id)}</div></td><td>${escapeHtml(patient.age||'Neuvedeno')} / ${patient.sex==='male'?'M':'Ž'}</td><td>${escapeHtml(patient.diagnoses||'Neuvedeno')}</td><td>${patient.notes}</td><td>${patient.labs}</td><td><button class="icon-button" data-patient="${patient.id}" title="Otevřít kartu" aria-label="Otevřít ${escapeHtml(patient.id)}">${icon('arrow-up-right')}</button></td></tr>`).join('')}</tbody></table>`;
  else if(state.debugTab==='trials') content=`<table class="data-table"><thead><tr><th>Protokol / zadavatel</th><th>Indikace</th><th>Registr</th><th>ClinicalTrials.eu</th><th>Nábor</th><th></th></tr></thead><tbody>${shown.map(trial=>`<tr><td><span class="cell-title">${escapeHtml(trial.protocol)}</span><div class="cell-subtitle">${escapeHtml(trial.sponsor)}</div></td><td>${escapeHtml(trial.diagnosis)}</td><td>${trial.registry?.nct_id?`<a href="${escapeHtml(trial.registry.url)}" target="_blank" rel="noreferrer">${escapeHtml(trial.registry.nct_id)}</a>`:'<span class="muted">Nepropojeno</span>'}</td><td><span class="status-pill ${trial.registry?.clinical_eu_verified?'status-met':'status-missing'}">${trial.registry?.clinical_eu_verified?'Ověřené ID':'Neověřeno'}</span></td><td><span class="cell-subtitle">${escapeHtml(trial.registry?.status||'Nezjištěno')}</span></td><td><button class="icon-button" data-debug-trial="${trial.id}" aria-label="Detail ${escapeHtml(trial.protocol)}" title="Zdrojová data">${icon('code-xml')}</button></td></tr>`).join('')}</tbody></table>`;
  else content=`<div class="summary-line"><span>Model: <strong>${escapeHtml(state.data.model)}</strong></span><span>Výsledky pacienta: <strong>${currentResults().length}</strong></span></div><pre class="json-view">${escapeHtml(JSON.stringify({job:state.job,results:currentResults()},null,2))}</pre>`;
  return heading('Data a ladění','Zdrojové tabulky, ověřené identifikátory a výstupy modelu',false)+`<div class="debug-toolbar"><div class="table-tabs">${[['patients','Pacienti',patients.length],['trials','Studie',trials.length],['runs','Výstupy',currentResults().length]].map(([tab,title,count])=>`<button data-debug-tab="${tab}" class="${tab===state.debugTab?'selected':''}">${title}<small>${count}</small></button>`).join('')}</div>${state.mode==='real'?`<button class="secondary-button" data-action="registry">${icon('refresh-cw')}Propojit registry</button>`:''}</div>${state.debugTab!=='runs'?`<div class="search-field debug-search">${icon('search')}<input id="debug-search" value="${escapeHtml(state.query)}" placeholder="Hledat v tabulce" aria-label="Hledat v tabulce"></div>`:''}<div class="table-wrap">${content}</div>${state.debugTab!=='runs'?`<div class="table-foot"><span>${records.length} záznamů</span><div class="pager"><button class="icon-button" data-page="${state.page-1}" ${state.page===0?'disabled':''} title="Předchozí strana" aria-label="Předchozí strana">${icon('chevron-left')}</button><span>${state.page+1} / ${pageCount}</span><button class="icon-button" data-page="${state.page+1}" ${state.page>=pageCount-1?'disabled':''} title="Další strana" aria-label="Další strana">${icon('chevron-right')}</button></div></div>`:''}`;
}

function render() {
  if(!state.patient)return;
  document.querySelector('#main').innerHTML=state.view==='patient'?renderPatient():state.view==='matches'?renderMatches():renderDebug();
  document.querySelectorAll('[data-view]').forEach(button=>{button.classList.toggle('active',button.dataset.view===state.view);button.setAttribute('aria-current',button.dataset.view===state.view?'page':'false');});
  document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('selected',button.dataset.mode===state.mode));
  document.querySelector('#demo-banner').hidden=state.mode!=='demo';
  document.querySelector('#footer-mode').textContent=state.mode==='demo'?'Syntetické ukázkové záznamy':'Pseudonymizovaná data · Posunutá data záznamů';
  refreshIcons();
}

async function selectPatient(identifier) {
  const sequence=++patientRequest;
  const patient=await api(`/api/patients/${encodeURIComponent(identifier)}`);
  if(sequence!==patientRequest||state.mode!=='real')return;
  state.patient=patient;state.trialId=null;state.expanded.clear();state.filter='all';
  render();
}

async function setMode(mode) {
  ++patientRequest;state.mode=mode;state.trialId=null;state.expanded.clear();state.filter='all';state.query='';state.page=0;
  if(mode==='demo') {state.demo ||= await api('/api/demo');state.patient=state.demo.patient;render();}
  else await selectPatient(state.realPatientId || state.data.patients.find(patient=>patient.target&&!patient.deceased)?.id || state.data.patients[0].id);
}

async function showSource(identifier,quote='') {
  const source=state.mode==='demo'?state.demo.patient.sources.find(item=>item.id===identifier):await api(`/api/patients/${encodeURIComponent(state.patient.id)}/sources/${encodeURIComponent(identifier)}${state.trialId?`?trial_id=${encodeURIComponent(state.trialId)}`:''}`);
  if(!source)throw new Error('Zdroj nebyl nalezen.');
  document.querySelector('#source-title').textContent=source.title;
  document.querySelector('#source-meta').innerHTML=`${escapeHtml(source.id)} · ${date(source.date)}${source.url?` <a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">Otevřít původní stránku</a>`:''}`;
  const position=quote?source.text.indexOf(quote):-1;
  document.querySelector('#source-text').innerHTML=position>=0?`${escapeHtml(source.text.slice(0,position))}<mark>${escapeHtml(quote)}</mark>${escapeHtml(source.text.slice(position+quote.length))}`:escapeHtml(source.text);
  const dialog=document.querySelector('#source-dialog');
  if(!dialog.open)dialog.showModal();
  requestAnimationFrame(()=>document.querySelector('#source-text mark')?.scrollIntoView({block:'center'}));
}

function openScreenDialog() {
  if(state.mode==='demo'){state.view='matches';render();return;}
  const available=state.data.trials.filter(trial=>trial.registry?.clinical_eu_verified);
  const relevant=trial=>state.patient.diagnoses.includes('C61')&&/prostat/i.test(trial.diagnosis)||state.patient.diagnoses.includes('C64')&&/ledvin|renální/i.test(trial.diagnosis);
  available.sort((first,second)=>Number(relevant(second))-Number(relevant(first)));
  document.querySelector('#screen-options').innerHTML=available.length?available.map((trial,index)=>`<label class="screen-option"><input type="checkbox" name="trial" value="${trial.id}" ${index<2&&relevant(trial)?'checked':''}><span><strong>${escapeHtml(trial.protocol)}</strong>${escapeHtml(trial.title)}<small>${escapeHtml(trial.registry.status)} · ${trial.registry.site_recruiting?'Pracoviště nabírá dle registru':'Nábor na pracovišti nepotvrzen'}</small></span></label>`).join(''):'<p class="empty-note">Dosud nejsou ověřena propojení s ClinicalTrials.eu. Otevřete Data a ladění a propojte registry.</p>';
  document.querySelector('#confirm-screen').disabled=!available.length;
  updateSelectionCount();document.querySelector('#screen-dialog').showModal();refreshIcons();
}

function updateSelectionCount(){const count=document.querySelectorAll('#screen-options input:checked').length;document.querySelector('#screen-counter').textContent=`${count} / 5 vybraných studií`;document.querySelector('#confirm-screen').disabled=count===0||count>5;}

async function startJob(payload) {
  state.job=await api('/api/jobs',{method:'POST',body:JSON.stringify(payload)});
  pollJob(state.job.id);
}

async function pollJob(identifier) {
  try {
    state.job=await api(`/api/jobs/${identifier}`);
    const banner=document.querySelector('#job-banner');banner.hidden=false;
    const active=['queued','running'].includes(state.job.state);
    banner.innerHTML=`${active?'<span class="spinner"></span>':icon(state.job.state==='error'?'circle-alert':'circle-check')}<span>${escapeHtml(state.job.message)}${state.job.total?` · ${state.job.done}/${state.job.total}`:''}${state.job.errors.length?` · ${escapeHtml(state.job.errors.join('; '))}`:''}</span>${active?'':'<button data-action="dismiss-job" aria-label="Zavřít stav zpracování">Zavřít</button>'}`;
    refreshIcons();
    if(active)setTimeout(()=>pollJob(identifier),1800);
    else {state.data=await api('/api/bootstrap');if(state.mode==='real')await selectPatient(state.patient.id);}
  }catch(error){toast(error.message);}
}

document.addEventListener('click',async event=>{
  const button=event.target.closest('button,a.brand');if(!button)return;
  try{
    if(button.matches('a.brand')){event.preventDefault();state.view='patient';render();}
    if(button.dataset.view){state.view=button.dataset.view;render();}
    if(button.dataset.mode){if(state.mode==='real')state.realPatientId=state.patient.id;await setMode(button.dataset.mode);}
    if(button.dataset.source)await showSource(button.dataset.source,button.dataset.quote||'');
    if(button.hasAttribute('data-close-dialog'))button.closest('dialog').close();
    if(button.dataset.trial){state.trialId=button.dataset.trial;state.expanded.clear();render();}
    if(button.dataset.criterion){const identifier=button.dataset.criterion;state.expanded.has(identifier)?state.expanded.delete(identifier):state.expanded.add(identifier);render();}
    if(button.dataset.debugTab){state.debugTab=button.dataset.debugTab;state.query='';state.page=0;render();}
    if(button.dataset.page!==undefined){state.page=Number(button.dataset.page);render();}
    if(button.dataset.patient){state.view='patient';if(state.mode==='real')await selectPatient(button.dataset.patient);else render();}
    if(button.dataset.debugTrial){const trial=state.mode==='demo'?trialById(button.dataset.debugTrial):await api(`/api/trials/${button.dataset.debugTrial}`);document.querySelector('#source-title').textContent=trial.protocol;document.querySelector('#source-meta').textContent='Zdrojová metadata studie';document.querySelector('#source-text').textContent=JSON.stringify(trial,null,2);document.querySelector('#source-dialog').showModal();}
    if(button.dataset.action==='open-matches'){state.view='matches';state.trialId=currentResults().find(result=>result.summary.badge)?.trial_id;render();}
    if(button.dataset.action==='screen')openScreenDialog();
    if(button.dataset.action==='registry')await startJob({kind:'registry'});
    if(button.dataset.action==='dismiss-job')document.querySelector('#job-banner').hidden=true;
    if(button.id==='confirm-screen'){const selected=[...document.querySelectorAll('#screen-options input:checked')].map(input=>input.value);document.querySelector('#screen-dialog').close();await startJob({kind:'screen',patient_id:state.patient.id,trial_ids:selected});}
  }catch(error){toast(error.message);}
});

document.addEventListener('change',async event=>{
  try{
    if(event.target.id==='patient-select')await selectPatient(event.target.value);
    if(event.target.id==='criterion-filter'){state.filter=event.target.value;render();}
    if(event.target.name==='trial')updateSelectionCount();
  }catch(error){toast(error.message);}
});

document.addEventListener('input',event=>{
  if(event.target.id==='debug-search'){const cursor=event.target.selectionStart;state.query=event.target.value;state.page=0;render();const field=document.querySelector('#debug-search');field.focus();field.setSelectionRange(cursor,cursor);}
  if(event.target.id==='patient-search'){
    const query=event.target.value.toLowerCase();
    const patients=(state.mode==='demo'?[state.demo.patient]:state.data.patients).filter(patient=>`${patient.id} ${patient.diagnoses} ${patient.name}`.toLowerCase().includes(query)).slice(0,50);
    const select=document.querySelector('#patient-select');
    select.innerHTML=patients.map(patient=>`<option value="${escapeHtml(patient.id)}">${escapeHtml(patient.name)} · ${escapeHtml(patient.diagnoses||'bez kódu')}</option>`).join('');
    select.value=patients.some(patient=>patient.id===state.patient.id)?state.patient.id:'';
  }
});

async function initialize(){
  try{
    state.data=await api('/api/bootstrap');
    document.querySelector('#dataset-count').textContent=`${state.data.patients.length.toLocaleString('cs-CZ')} pacientů · ${state.data.trials.length} studií`;
    if(!state.data.patients.length)throw new Error('Dataset nebyl nalezen v projektové složce.');
    const requested=new URLSearchParams(location.search).get('patient');
    const patient=state.data.patients.find(item=>item.id===requested)||state.data.patients.find(item=>item.target&&!item.deceased&&item.notes>=5&&item.notes<=15&&item.labs>0)||state.data.patients[0];
    await selectPatient(patient.id);
    const active=state.data.jobs.find(job=>['queued','running'].includes(job.state));if(active)pollJob(active.id);
  }catch(error){document.querySelector('#main').innerHTML=`<div class="empty-state">${icon('circle-alert')}<h2>Data se nepodařilo načíst</h2><p>${escapeHtml(error.message)}</p></div>`;refreshIcons();}
}
initialize();