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
const params = new URLSearchParams(location.search);
// The study-match view runs in its own browser window, opened from the patient card's widget.
const popup = params.get('view') === 'matches';
const plural = (count, one, few, many) => count === 1 ? one : count >= 2 && count <= 4 ? few : many;
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

function heading(title, subtitle, action='') {
  return `<div class="page-heading"><div><div class="eyebrow">ONKOLOGICKÁ AMBULANCE</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${action}</div>`;
}

// Studies with no contradicted criterion. Unclear or missing data never hides a study:
// the doctor decides, the model never excludes.
const reviewable = () => currentResults().filter(result => result.summary.status !== 'not_met');

function trialWidget() {
  const studies = reviewable();
  if (!studies.length) return '';
  const complete = studies.filter(result => result.summary.badge).length;
  const detail = complete ? `${complete} ${plural(complete, 'splňuje', 'splňují', 'splňuje')} všechna kritéria · ostatní k ověření` : 'Žádné kritérium pacienta nevylučuje · ověřit s týmem studie';
  return `<button class="trial-widget" data-action="open-matches"><span class="widget-icon">${icon('lightbulb')}</span><span class="widget-copy"><span class="widget-eyebrow">KLINICKÉ STUDIE</span><strong>${studies.length} ${plural(studies.length, 'studie', 'studie', 'studií')} k posouzení</strong><small>${detail}</small></span>${icon('arrow-up-right')}</button>`;
}

function openMatchesWindow() {
  const first = reviewable().find(result => result.summary.badge) || reviewable()[0];
  const query = new URLSearchParams({view:'matches', mode:state.mode, patient:state.patient.id, ...(first ? {trial:first.trial_id} : {})});
  const opened = window.open(`/?${query}`, 'trialscreen-matches', 'popup,width=1180,height=860');
  if (!opened) throw new Error('Prohlížeč zablokoval nové okno. Povolte vyskakovací okna pro tuto stránku.');
  opened.focus();
}

function renderPatient() {
  const patient = state.patient;
  const labPriority = lab => /hemoglobin|kreatinin|neutrofil|trombocyt|bilirubin|albumin|^PSA$/i.test(lab.name) ? 0 : typeof lab.value === 'number' ? 1 : 2;
  const displayedLabs = [...patient.latest_labs].sort((first,second)=>labPriority(first)-labPriority(second)).slice(0,7);
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
  return heading('Karta pacienta', 'Ambulantní přehled a podklady pro klinické studie') + `
    <section class="medical-sheet" aria-label="Zdravotní karta">
      <div class="identity-row"><div class="patient-avatar">${icon('user-round')}</div><div class="identity-copy"><h2>${escapeHtml(patient.name)}</h2><div class="identity-meta"><span class="id-label">${escapeHtml(patient.id)}</span><span>·</span><span>${patient.synthetic ? 'Syntetická ukázka' : 'Pseudonymizovaný záznam'}</span>${patient.deceased ? '<span class="status-pill status-not_met">Záznam o úmrtí</span>' : ''}</div></div><div class="record-meta">Poslední dostupný záznam<strong>${date(patient.snapshot_at)}</strong>${patient.synthetic ? 'Ukázková dokumentace' : 'Posunuté datum datasetu'}</div></div>
      <div class="metric-grid">${metrics.map(metric=>`<div class="metric"><div class="metric-label">${icon(metric.icon)}${metric.name}</div><div class="metric-value">${escapeHtml(metric.value)}<small>${metric.unit || ''}</small></div>${metric.evidence ? evidenceButton(metric.evidence,`Zdroj: ${metric.name}`) : '<div class="metric-foot">Údaj není doložen</div>'}</div>`).join('')}</div>
    </section>
    ${trialWidget()}
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
  const results = [...currentResults()].sort((first, second) => Number(first.summary.status === 'not_met') - Number(second.summary.status === 'not_met'));
  const header = heading('Shoda se studiemi', `${escapeHtml(state.patient.name)} · ${escapeHtml(state.patient.id)} · Posouzení dostupných kritérií`);
  if (!results.length) return header + `<div class="empty-state">${icon('clipboard-search')}<h2>Pacient zatím nebyl posouzen</h2><p>Žádný výsledek neznamená vyloučení. Pro tohoto pacienta zatím není uložené porovnání.</p></div>`;
  if (!results.some(result=>result.trial_id===state.trialId)) state.trialId=results[0].trial_id;
  const result=results.find(item=>item.trial_id===state.trialId);
  const trial=trialById(state.trialId);
  const visible=result.decisions.filter(decision=>state.filter==='all'||decision.status===state.filter);
  return header + `<div class="matches-layout"><aside class="study-list" aria-label="Posouzené studie">${results.map(item=>{const study=trialById(item.trial_id);return `<button class="study-choice ${item.trial_id===state.trialId?'active':''}" data-trial="${escapeHtml(item.trial_id)}"><div class="study-protocol">${escapeHtml(study?.protocol||item.trial_id)}</div><div class="study-name">${escapeHtml(study?.title||'Klinická studie')}</div><div class="study-score"><span>${item.summary.counts.met}/${item.summary.total} kritérií splněno</span><strong title="Podíl splněných jednotlivých kritérií, nikoli pravděpodobnost způsobilosti">${item.summary.match_percent}%</strong></div>${resultProgress(item)}</button>`;}).join('')}</aside>
    <section class="match-detail"><div class="trial-head"><div class="eyebrow">${escapeHtml(trial?.protocol)} · ${state.mode==='demo'?'SYNTETICKÁ STUDIE':'REGISTROVANÁ STUDIE'}</div><h2>${escapeHtml(trial?.title)}</h2><div class="trial-links">${pill(result.summary.status)}${result.registry.url?`<a href="${escapeHtml(result.registry.url)}" target="_blank" rel="noreferrer">${escapeHtml(result.registry.nct_id||'Registr')} ${icon('external-link')}</a>`:''}<span class="muted">${result.registry.site_recruiting?'Nábor na pracovišti uveden v registru':'Nábor na pracovišti nepotvrzen'}</span></div></div>
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
  if(state.debugTab==='patients') content=`<table class="data-table"><thead><tr><th>Pacient</th><th>Věk / pohlaví</th><th>Diagnózy</th><th>Zprávy</th><th>Laboratoř</th><th></th></tr></thead><tbody>${shown.map(patient=>`<tr><td><span class="cell-title">${escapeHtml(patient.name)}</span><div class="cell-subtitle id-label">${escapeHtml(patient.id)}</div></td><td>${escapeHtml(patient.age||'Neuvedeno')} / ${patient.sex==='male'?'M':'Ž'}</td><td>${escapeHtml(patient.diagnoses||'Neuvedeno')}</td><td>${patient.notes}</td><td>${patient.labs}</td><td><button class="icon-button" data-patient="${escapeHtml(patient.id)}" title="Otevřít kartu" aria-label="Otevřít ${escapeHtml(patient.id)}">${icon('arrow-up-right')}</button></td></tr>`).join('')}</tbody></table>`;
  else if(state.debugTab==='trials') content=`<table class="data-table"><thead><tr><th>Protokol / zadavatel</th><th>Indikace</th><th>Registr</th><th>Propojení</th><th>Nábor</th><th></th></tr></thead><tbody>${shown.map(trial=>`<tr><td><span class="cell-title">${escapeHtml(trial.protocol)}</span><div class="cell-subtitle">${escapeHtml(trial.sponsor)}</div></td><td>${escapeHtml(trial.diagnosis)}</td><td>${trial.registry?.nct_id?`<a href="${escapeHtml(trial.registry.url)}" target="_blank" rel="noreferrer">${escapeHtml(trial.registry.nct_id)}</a>`:'<span class="muted">Nepropojeno</span>'}</td><td><span class="status-pill ${trial.registry?.state==='resolved'?'status-met':'status-missing'}">${trial.registry?.state==='resolved'?'Ověřené ID':trial.registry?.state==='unresolved'?'Nenalezeno':'Neověřeno'}</span></td><td><span class="cell-subtitle">${escapeHtml(trial.registry?.status||'Nezjištěno')}</span></td><td><button class="icon-button" data-debug-trial="${escapeHtml(trial.id)}" aria-label="Detail ${escapeHtml(trial.protocol)}" title="Zdrojová data">${icon('code-xml')}</button></td></tr>`).join('')}</tbody></table>`;
  else content=`<div class="summary-line"><span>Model: <strong>${escapeHtml(state.data.model)}</strong></span><span>Výsledky pacienta: <strong>${currentResults().length}</strong></span></div><pre class="json-view">${escapeHtml(JSON.stringify({job:state.job,results:currentResults()},null,2))}</pre>`;
  return heading('Data a ladění','Zdrojové tabulky, ověřené identifikátory a výstupy modelu',`<button class="secondary-button" data-view="patient">${icon('arrow-left')}Karta pacienta</button>`)+`<div class="debug-toolbar"><div class="table-tabs">${[['patients','Pacienti',patients.length],['trials','Studie',trials.length],['runs','Výstupy',currentResults().length]].map(([tab,title,count])=>`<button data-debug-tab="${tab}" class="${tab===state.debugTab?'selected':''}">${title}<small>${count}</small></button>`).join('')}</div>${state.mode==='real'?`<button class="secondary-button" data-action="registry">${icon('refresh-cw')}Propojit registry</button>`:''}</div>${state.debugTab!=='runs'?`<div class="search-field debug-search">${icon('search')}<input id="debug-search" value="${escapeHtml(state.query)}" placeholder="Hledat v tabulce" aria-label="Hledat v tabulce"></div>`:''}<div class="table-wrap">${content}</div>${state.debugTab!=='runs'?`<div class="table-foot"><span>${records.length} záznamů</span><div class="pager"><button class="icon-button" data-page="${state.page-1}" ${state.page===0?'disabled':''} title="Předchozí strana" aria-label="Předchozí strana">${icon('chevron-left')}</button><span>${state.page+1} / ${pageCount}</span><button class="icon-button" data-page="${state.page+1}" ${state.page>=pageCount-1?'disabled':''} title="Další strana" aria-label="Další strana">${icon('chevron-right')}</button></div></div>`:''}`;
}

function render() {
  if(!state.patient)return;
  document.querySelector('#main').innerHTML=state.view==='patient'?renderPatient():state.view==='matches'?renderMatches():renderDebug();
  document.body.classList.toggle('popup',popup);
  document.querySelector('.footer-link').hidden=state.view==='debug';
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
  if(!popup)history.replaceState(null,'',`?patient=${encodeURIComponent(identifier)}`);
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

async function startJob(payload) {
  state.job=await api('/api/jobs',{method:'POST',body:JSON.stringify(payload)});
  pollJob(state.job.id);
}

async function pollJob(identifier, failures=0) {
  try {
    state.job=await api(`/api/jobs/${identifier}`);
    const banner=document.querySelector('#job-banner');banner.hidden=false;
    const active=['queued','running'].includes(state.job.state);
    banner.innerHTML=`${active?'<span class="spinner"></span>':icon(state.job.state==='error'?'circle-alert':'circle-check')}<span>${escapeHtml(state.job.message)}${state.job.total?` · ${state.job.done}/${state.job.total}`:''}${state.job.errors.length?` · ${escapeHtml(state.job.errors.join('; '))}`:''}</span>${active?'':'<button data-action="dismiss-job" aria-label="Zavřít stav zpracování">Zavřít</button>'}`;
    refreshIcons();
    if(active)setTimeout(()=>pollJob(identifier),1800);
    else {state.data=await api('/api/bootstrap');if(state.mode==='real')await selectPatient(state.patient.id);}
  }catch(error){
    // Keep polling through transient failures; give up after a few in a row.
    if(failures<5)setTimeout(()=>pollJob(identifier,failures+1),3000);else toast(error.message);
  }
}

document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try{
    if(button.dataset.view){state.view=button.dataset.view;render();scrollTo(0,0);}
    if(button.dataset.mode){if(state.mode==='real')state.realPatientId=state.patient.id;await setMode(button.dataset.mode);}
    if(button.dataset.source)await showSource(button.dataset.source,button.dataset.quote||'');
    if(button.hasAttribute('data-close-dialog'))button.closest('dialog').close();
    if(button.dataset.trial){state.trialId=button.dataset.trial;state.expanded.clear();render();}
    if(button.dataset.criterion){const identifier=button.dataset.criterion;state.expanded.has(identifier)?state.expanded.delete(identifier):state.expanded.add(identifier);render();}
    if(button.dataset.debugTab){state.debugTab=button.dataset.debugTab;state.query='';state.page=0;render();}
    if(button.dataset.page!==undefined){state.page=Number(button.dataset.page);render();}
    if(button.dataset.patient){state.view='patient';if(state.mode==='real')await selectPatient(button.dataset.patient);else render();}
    if(button.dataset.debugTrial){const trial=state.mode==='demo'?trialById(button.dataset.debugTrial):await api(`/api/trials/${encodeURIComponent(button.dataset.debugTrial)}`);document.querySelector('#source-title').textContent=trial.protocol;document.querySelector('#source-meta').textContent='Zdrojová metadata studie';document.querySelector('#source-text').textContent=JSON.stringify(trial,null,2);document.querySelector('#source-dialog').showModal();}
    if(button.dataset.action==='open-matches')openMatchesWindow();
    if(button.dataset.action==='registry')await startJob({kind:'registry'});
    if(button.dataset.action==='dismiss-job')document.querySelector('#job-banner').hidden=true;
  }catch(error){toast(error.message);}
});

document.addEventListener('change',async event=>{
  try{
    if(event.target.id==='criterion-filter'){state.filter=event.target.value;render();}
  }catch(error){toast(error.message);}
});

document.addEventListener('input',event=>{
  if(event.target.id==='debug-search'){const cursor=event.target.selectionStart;state.query=event.target.value;state.page=0;render();const field=document.querySelector('#debug-search');field.focus();field.setSelectionRange(cursor,cursor);}
});

async function initialize(){
  try{
    state.data=await api('/api/bootstrap');
    if(popup){
      document.title='TrialScreen | Shoda se studiemi';
      state.view='matches';
      if(params.get('mode')==='demo'){state.mode='demo';state.demo=await api('/api/demo');state.patient=state.demo.patient;}
      else await selectPatient(params.get('patient'));
      state.trialId=params.get('trial');
      render();
      return;
    }
    if(!state.data.patients.length)throw new Error('Dataset nebyl nalezen v projektové složce.');
    const requested=params.get('patient');
    const patient=state.data.patients.find(item=>item.id===requested)||state.data.patients.find(item=>item.target&&!item.deceased&&item.notes>=5&&item.notes<=15&&item.labs>0)||state.data.patients[0];
    await selectPatient(patient.id);
    const active=state.data.jobs.find(job=>['queued','running'].includes(job.state));if(active)pollJob(active.id);
  }catch(error){document.querySelector('#main').innerHTML=`<div class="empty-state">${icon('circle-alert')}<h2>Data se nepodařilo načíst</h2><p>${escapeHtml(error.message)}</p></div>`;refreshIcons();}
}
initialize();