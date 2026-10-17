const state = {data:null, patient:null, demo:null, mode:'real', view:'nis', nisQuery:'', stripOpen:false, groupsOpen:new Set(), trialId:null, debugTab:'patients', filter:'all', query:'', page:0, expanded:new Set(), job:null, overview:null, overviewTab:'trials', overviewTrial:null, overviewPatient:null, overviewQuery:'', overviewFilter:'all', overviewLimit:40, details:{}, pending:new Set(), editing:null, draft:null, editMode:false};
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
// TrialScreen runs in its own browser window, opened from the widget on the (simulated) NIS patient card.
const popup = ['matches', 'overview'].includes(params.get('view'));
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
const closedStatuses = ['COMPLETED', 'TERMINATED', 'WITHDRAWN', 'SUSPENDED', 'ACTIVE_NOT_RECRUITING'];
const reviewable = () => currentResults().filter(result => result.summary.status !== 'not_met' && !closedStatuses.includes(result.registry?.status));

function trialWidget() {
  const studies = reviewable();
  if (!studies.length) return '';
  const complete = studies.filter(result => result.summary.badge).length;
  const best = [...studies].sort(byFit)[0];
  const bestLine = `Nejlepší shoda: ${escapeHtml(trialById(best.trial_id)?.protocol || best.trial_id)} · ${best.summary.match_percent} %`;
  const detail = complete ? `${complete} ${plural(complete, 'splňuje', 'splňují', 'splňuje')} všechna kritéria · ostatní k ověření` : 'Žádné kritérium pacienta nevylučuje · ověřit s týmem studie';
  return `<button class="trial-widget" data-action="open-matches"><span class="widget-icon">${icon('lightbulb')}</span><span class="widget-copy"><span class="widget-eyebrow">TRIALSCREEN · KLINICKÉ STUDIE</span><strong>${studies.length} ${plural(studies.length, 'studie', 'studie', 'studií')} k posouzení</strong><small>${bestLine}</small><small>${detail}</small></span>${icon('arrow-up-right')}</button>`;
}

function openMatchesWindow() {
  const first = reviewable().find(result => result.summary.badge) || reviewable()[0];
  const query = new URLSearchParams({view:'matches', mode:state.mode, patient:state.patient.id, ...(first ? {trial:first.trial_id} : {})});
  const opened = window.open(`/?${query}`, 'trialscreen-matches', 'popup,width=1180,height=860');
  if (!opened) throw new Error('Prohlížeč zablokoval nové okno. Povolte vyskakovací okna pro tuto stránku.');
  opened.focus();
}

// The patient card: the always-visible sheet (identity and key metrics) and the record columns below it.
function patientSheet() {
  const patient = state.patient;
  const labPriority = lab => /hemoglobin|kreatinin|neutrofil|trombocyt|bilirubin|albumin|^PSA$/i.test(lab.name) ? 0 : typeof lab.value === 'number' ? 1 : 2;
  const displayedLabs = [...patient.latest_labs].sort((first,second)=>labPriority(first)-labPriority(second)).slice(0,7);
  const demographicsSource = patient.synthetic ? 'demo-note' : 'cohort';
  const metrics = patientMetrics(patient);
  const sheet = `
    <section class="medical-sheet" aria-label="Zdravotní karta">
      <div class="identity-row"><div class="patient-avatar">${icon('user-round')}</div><div class="identity-copy"><h2>${escapeHtml(patient.name)}</h2><div class="identity-meta"><span class="id-label">${escapeHtml(patient.id)}</span><span>·</span><span>${patient.synthetic ? 'Syntetická ukázka' : 'Pseudonymizovaný záznam'}</span>${patient.deceased ? '<span class="status-pill status-not_met">Záznam o úmrtí</span>' : ''}</div></div><div class="record-meta">Poslední dostupný záznam<strong>${date(patient.snapshot_at)}</strong>${patient.synthetic ? 'Ukázková dokumentace' : 'Posunuté datum datasetu'}</div></div>
      <div class="metric-grid">${metrics.map(metric=>`<div class="metric"><div class="metric-label">${icon(metric.icon)}${metric.name}</div><div class="metric-value">${escapeHtml(metric.value)}<small>${metric.unit || ''}</small></div>${metric.evidence ? evidenceButton(metric.evidence,`Zdroj: ${metric.name}`) : '<div class="metric-foot">Údaj není doložen</div>'}</div>`).join('')}</div>
    </section>`;
  const records = `
    <div class="record-columns"><section><div class="section-heading">${icon('notebook-tabs')}<h3>Poslední záznamy</h3><span class="count">${patient.notes} zpráv</span></div>
      ${patient.recent_notes.length ? patient.recent_notes.slice(0,6).map(note=>`<button class="note-entry" data-source="${escapeHtml(note.id)}"><span class="note-date">${date(note.date)}</span><span><span class="note-title">${escapeHtml(note.title)}</span><span class="note-id">${escapeHtml(note.id)}</span></span>${icon('arrow-up-right')}</button>`).join('') : '<p class="empty-note">V dostupném exportu nejsou textové zprávy.</p>'}
      <div class="snapshot-note">${icon('clock-3')}Časové údaje odpovídají snapshotu dokumentace.</div></section>
      <div><section><div class="section-heading">${icon('clipboard-plus')}<h3>Diagnózy v záznamu</h3>${evidenceButton(patient.evidence?.dx||{source_id:demographicsSource,quote:`diagnozy: ${patient.diagnoses}`},'Zdroj diagnóz')}</div>${diagnosisCodes(patient).length ? diagnosisCodes(patient).map(code=>`<div class="diagnosis-line"><span class="code-tag">${escapeHtml(code)}</span><span>${diagnosisName(code)}</span></div>`).join('') : '<p class="empty-note">Kódy nejsou v kohortě uvedeny.</p>'}</section>
      <section class="section-gap"><div class="section-heading">${icon('test-tubes')}<h3>Laboratorní výsledky</h3><span class="count">Poslední hodnoty</span></div>${displayedLabs.length ? `<table class="mini-table"><tbody>${displayedLabs.map(lab=>`<tr><td>${escapeHtml(lab.name)}<div class="cell-subtitle">${date(lab.date)}</div></td><td>${escapeHtml(lab.value)} <span class="muted">${escapeHtml(lab.unit)}</span></td><td>${evidenceButton({source_id:lab.source_id},`Zdroj: ${lab.name}`)}</td></tr>`).join('')}</tbody></table>` : '<p class="empty-note">Laboratorní hodnoty nejsou v dostupném záznamu doloženy.</p>'}</section></div></div>`;
  return {sheet, records};
}

function patientMetrics(patient) {
  const demographicsSource = patient.synthetic ? 'demo-note' : 'cohort';
  const ageEvidence = patient.evidence?.age || {source_id:demographicsSource,quote:patient.age ? `${String(patient.age).includes('-') ? 'vek_pasmo' : 'vek'}: ${patient.age}` : ''};
  const genderEvidence = patient.evidence?.sex || {source_id:demographicsSource,quote:`pohlavi: ${patient.sex}`};
  return [
    {name:'Pohlaví',value:sexName(patient.sex),icon:'user-round',evidence:genderEvidence},
    {name:'Věk',value:patient.age || '—',unit:patient.age ? 'let' : '',icon:'calendar-days',evidence:ageEvidence},
    {name:'Hmotnost',value:patient.vitals.weight?.value || '—',unit:patient.vitals.weight ? 'kg' : '',icon:'weight',evidence:patient.vitals.weight},
    {name:'Výška',value:patient.vitals.height?.value || '—',unit:patient.vitals.height ? 'cm' : '',icon:'ruler',evidence:patient.vitals.height},
    {name:'WHO / ECOG',value:patient.vitals.ecog?.value ?? '—',unit:patient.vitals.ecog ? '/ 4' : '',icon:'activity',evidence:patient.vitals.ecog}
  ];
}
const diagnosisCodes = patient => (patient.diagnoses || '').split(/[,;\s]+/).filter(Boolean);
const diagnosisName = code => code.startsWith('C61') ? 'Zhoubný novotvar prostaty' : code.startsWith('C64') ? 'Zhoubný novotvar ledviny' : 'Diagnóza MKN-10';

// TrialScreen window: the same patient card above the study matches; records, diagnoses and labs fold out on click.
function patientCard() {
  const {sheet, records} = patientSheet();
  const label = state.stripOpen ? 'Skrýt záznamy, diagnózy a laboratoř' : 'Zobrazit záznamy, diagnózy a laboratoř';
  return `<div class="patient-card">${sheet}<button class="card-toggle" data-action="toggle-strip" aria-expanded="${state.stripOpen}">${icon(state.stripOpen ? 'chevron-up' : 'chevron-down')}${label}</button>${state.stripOpen ? records : ''}</div>`;
}

// Simulated hospital information system (NIS): the start page, deliberately in a different, denser hospital style.
// TrialScreen only appears as the embedded widget, which opens the TrialScreen window.
// Same rule as the widget: candidates only in studies that are not closed to recruitment.
function candidateCounts() {
  const closed = new Set((state.overview?.trials || []).filter(trial => closedStatuses.includes(trial.registry?.status)).map(trial => trial.id));
  return Object.fromEntries((state.overview?.patients || []).map(entry => [entry.id, entry.results.filter(result => isCandidate(result) && !closed.has(result.trial_id)).length]));
}

function nisPatients() {
  const counts = candidateCounts();
  return cohort().filter(patient => searchHit([patient.id, patient.name, patient.diagnoses], state.nisQuery))
    .sort((first, second) => (counts[second.id] || 0) - (counts[first.id] || 0) || Number(second.target) - Number(first.target) || (second.notes || 0) - (first.notes || 0))
    .slice(0, 60);
}

const nisBox = (title, body, extra = '') => `<section class="nis-box"><div class="nis-box-head"><span>${title}</span>${extra}</div>${body}</section>`;
const nisSource = (evidence, label = 'zdroj') => evidence?.source_id ? `<button class="nis-src" data-source="${escapeHtml(evidence.source_id)}" data-quote="${escapeHtml(evidence.quote || '')}">${label}</button>` : '';

function renderNis() {
  const patient = state.patient;
  const counts = candidateCounts();
  const list = nisPatients().map(item => `<button class="nis-row ${item.id === patient.id ? 'active' : ''}" data-patient="${escapeHtml(item.id)}"><span class="nis-row-name">${escapeHtml(item.name)}</span><span>${escapeHtml(item.age || '—')}</span><span>${sexLabel(item.sex)}</span><span>${escapeHtml(item.diagnoses || '—')}</span><span>${counts[item.id] ? `<span class="nis-flag" title="TrialScreen: ${counts[item.id]} studií k posouzení">KS ${counts[item.id]}</span>` : ''}</span></button>`).join('');
  const widget = trialWidget() || `<div class="trial-widget muted-widget"><span class="widget-copy"><span class="widget-eyebrow">TRIALSCREEN · KLINICKÉ STUDIE</span><small>${currentResults().length ? 'Žádná nabírající studie bez nesplněného kritéria' : 'Pacient zatím nebyl posouzen'}</small></span></div>`;
  const basics = `<table class="nis-table nis-kv"><tbody>${patientMetrics(patient).map(metric => `<tr><th>${metric.name}</th><td>${escapeHtml(metric.value)} ${metric.unit || ''}</td><td>${metric.evidence ? nisSource(metric.evidence) : '<span class="nis-dim">nedoloženo</span>'}</td></tr>`).join('')}</tbody></table>`;
  const diagnoses = diagnosisCodes(patient).length ? `<table class="nis-table"><thead><tr><th>MKN-10</th><th>Název</th></tr></thead><tbody>${diagnosisCodes(patient).map(code => `<tr><td><b>${escapeHtml(code)}</b></td><td>${diagnosisName(code)}</td></tr>`).join('')}</tbody></table>` : '<p class="nis-empty">Diagnózy nejsou uvedeny.</p>';
  const notes = patient.recent_notes.length ? `<table class="nis-table"><thead><tr><th>Datum</th><th>Dokument</th><th>Číslo</th></tr></thead><tbody>${patient.recent_notes.slice(0, 10).map(note => `<tr><td>${date(note.date)}</td><td><button class="nis-doc" data-source="${escapeHtml(note.id)}">${escapeHtml(note.title)}</button></td><td class="nis-dim">${escapeHtml(note.id)}</td></tr>`).join('')}</tbody></table>` : '<p class="nis-empty">V exportu nejsou textové zprávy.</p>';
  const labs = patient.latest_labs.length ? `<table class="nis-table"><thead><tr><th>Vyšetření</th><th>Hodnota</th><th>Jednotka</th><th>Datum</th><th></th></tr></thead><tbody>${patient.latest_labs.slice(0, 14).map(lab => `<tr><td>${escapeHtml(lab.name)}</td><td><b>${escapeHtml(lab.value)}</b></td><td>${escapeHtml(lab.unit)}</td><td>${date(lab.date)}</td><td>${nisSource({source_id:lab.source_id})}</td></tr>`).join('')}</tbody></table>` : '<p class="nis-empty">Bez laboratorních výsledků.</p>';
  return `<div class="nis-layout"><aside class="nis-list" aria-label="Pacienti ambulance"><div class="nis-list-head">Objednaní pacienti · ${cohort().length}</div><div class="nis-search"><input id="nis-search" value="${escapeHtml(state.nisQuery)}" placeholder="Hledat: jméno, ID, MKN" aria-label="Hledat pacienta"></div><div class="nis-row nis-row-head"><span>Pacient</span><span>Věk</span><span>Pohl.</span><span>Dg.</span><span></span></div>${list || '<p class="nis-empty">Nikdo neodpovídá hledání.</p>'}</aside>
    <section class="nis-main"><div class="nis-patient-head"><div class="nis-patient-id"><h2>${escapeHtml(patient.name)}</h2><div class="nis-meta"><span>ID ${escapeHtml(patient.id)}</span><span>${escapeHtml(patient.age || '—')} let</span><span>${sexName(patient.sex)}</span><span>Dg. ${escapeHtml(patient.diagnoses || '—')}</span><span>Posl. záznam ${date(patient.snapshot_at)}</span>${patient.deceased ? '<span class="nis-alert">Záznam o úmrtí</span>' : ''}</div></div>${widget}</div>
      <div class="nis-tabs"><span class="active">Přehled</span><span>Dekurz</span><span>Žádanky</span><span>Medikace</span><span>Výkony</span></div>
      <div class="nis-grid">${nisBox('Základní údaje', basics)}${nisBox('Diagnózy', diagnoses, nisSource(patient.evidence?.dx || {source_id:patient.synthetic ? 'demo-note' : 'cohort', quote:`diagnozy: ${patient.diagnoses}`}))}${nisBox('Dokumentace', notes, `<span class="nis-dim">${patient.notes} zpráv</span>`)}${nisBox('Laboratoř – poslední hodnoty', labs)}</div></section></div>`;
}

function resultProgress(result) {
  return `<div class="progress-track" aria-label="Rozložení stavů kritérií">${Object.entries(result.summary.counts).map(([status,count])=>`<span class="bg-${status}" style="width:${100*count/(result.summary.total||1)}%" title="${labels[status]}: ${count}"></span>`).join('')}</div>`;
}

// Studies: fewest red (not met) criteria first, then highest match %, then highest share of amber (clinician review).
const share = (result, status) => result.summary.counts[status] / (result.summary.total || 1);
const byFit = (first, second) => first.summary.counts.not_met - second.summary.counts.not_met
  || second.summary.match_percent - first.summary.match_percent
  || share(second, 'unknown') - share(first, 'unknown');
// Rule-engine results count only criteria assessable from documentation; administrative ones (consent, ...) are
// checked at screening. Model results keep met / all criteria.
const metLabel = summary => summary.assessable != null
  ? `${summary.counts.met}/${summary.assessable} posouditelných splněno`
  : `${summary.counts.met}/${summary.total} kritérií splněno`;
const percentTitle = summary => summary.assessable != null
  ? `Splněno z kritérií posouditelných z dokumentace; ${summary.administrative} administrativních (souhlas apod.) ověří zkoušející. Chybějící údaj se nepočítá jako splněný. Nejde o pravděpodobnost způsobilosti.`
  : 'Podíl splněných jednotlivých kritérií, nikoli pravděpodobnost způsobilosti';
// Criteria within a study: met, not met, clinician review, missing (protocol order kept within a status).
const criterionOrder = {met:0, not_met:1, unknown:2, missing:3};

function renderMatches() {
  const results = [...currentResults()].sort(byFit);
  const header = heading('Shoda se studiemi', `${escapeHtml(state.patient.name)} · ${escapeHtml(state.patient.id)} · Posouzení dostupných kritérií`) + patientCard();
  if (!results.length) return header + `<div class="empty-state">${icon('clipboard-search')}<h2>Pacient zatím nebyl posouzen</h2><p>Žádný výsledek neznamená vyloučení. Pro tohoto pacienta zatím není uložené porovnání.</p></div>`;
  if (!results.some(result=>result.trial_id===state.trialId)) state.trialId=results[0].trial_id;
  const result=results.find(item=>item.trial_id===state.trialId);
  const trial=trialById(state.trialId);
  const visible=result.decisions.filter(decision=>state.filter==='all'||decision.status===state.filter).sort((first,second)=>criterionOrder[first.status]-criterionOrder[second.status]);
  return header + `<div class="matches-layout"><aside class="study-list" aria-label="Posouzené studie">${results.map(item=>{const study=trialById(item.trial_id);return `<button class="study-choice ${item.trial_id===state.trialId?'active':''}" data-trial="${escapeHtml(item.trial_id)}"><div class="study-protocol">${escapeHtml(study?.protocol||item.trial_id)}</div><div class="study-name">${escapeHtml(study?.title||'Klinická studie')}</div><div class="study-score"><span>${metLabel(item.summary)}</span><strong title="${escapeHtml(percentTitle(item.summary))}">${item.summary.match_percent}%</strong></div>${resultProgress(item)}</button>`;}).join('')}</aside>
    <section class="match-detail"><div class="trial-head"><div class="eyebrow">${escapeHtml(trial?.protocol)} · ${state.mode==='demo'?'SYNTETICKÁ STUDIE':'REGISTROVANÁ STUDIE'}</div><h2>${escapeHtml(trial?.title)}</h2><div class="trial-links">${pill(result.summary.status)}${result.registry.url?`<a href="${escapeHtml(result.registry.url)}" target="_blank" rel="noreferrer">${escapeHtml(result.registry.nct_id||'Registr')} ${icon('external-link')}</a>`:''}<span class="muted">${result.registry.site_recruiting?'Nábor na pracovišti uveden v registru':'Nábor na pracovišti nepotvrzen'}</span></div></div>
    <div class="stats-strip">${Object.entries(result.summary.counts).map(([status,count])=>`<div class="stat ${status}"><b>${count}</b>${labels[status]}</div>`).join('')}</div>
    ${result.future_watch.map(watch=>`<div class="watch-banner"><strong>${icon('calendar-clock')}${escapeHtml(watch.text)}</strong><small>${escapeHtml(watch.caveat)}</small></div>`).join('')}
    <div class="filter-row"><h3>Podmínky účasti <span class="muted">(${result.summary.total})</span></h3><select id="criterion-filter" aria-label="Filtrovat stav kritérií"><option value="all">Všechny stavy</option>${Object.entries(labels).map(([value,label])=>`<option value="${value}" ${state.filter===value?'selected':''}>${label}</option>`).join('')}</select></div>
    <div>${visible.length?visible.map(decision=>renderCriterion(decision,result)).join(''):'<p class="empty-note">V tomto stavu nejsou žádná kritéria.</p>'}</div>
    <div class="assessment-foot"><div>${result.summary.counts.met}/${result.summary.total} jednotlivých podmínek splněno${result.summary.assessable != null ? ` (${result.summary.match_percent} % z ${result.summary.assessable} posouditelných z dokumentace; ${result.summary.administrative} administrativních ověří zkoušející)` : ''}. Skóre není pravděpodobnost způsobilosti; rozhoduje logika všech podmínek.</div><div>Snapshot: ${date(result.snapshot_at)} · Zpracováno: ${date(result.assessed_at)} · ${escapeHtml(result.model)}</div>${result.limitations.map(limit=>`<div>${escapeHtml(limit)}</div>`).join('')}</div></section></div>`;
}

// Clinicians fill in what the screening could not decide (or correct it); the summary is recomputed on the server.
function decisionForm(decision) {
  const own=decision.clinician;
  if(state.mode==='demo'||(!own&&!['unknown','missing'].includes(decision.status)))return '';
  const id=escapeHtml(decision.criterion_id);
  const original=own?`<div class="cell-subtitle">Původně: ${labels[decision.original.status]} · ${escapeHtml(decision.original.reason)} · upraveno ${date(own.edited_at)}</div>`:'';
  return `<div class="decision-form"><div class="evidence-label">DOPLNĚNÍ LÉKAŘEM</div>${original}<input data-decide-value="${id}" maxlength="500" placeholder="Hodnota (např. ECOG 1, souhlas podepsán 12. 10.)" value="${escapeHtml(own?.value||'')}"><input data-decide-note="${id}" maxlength="2000" placeholder="Poznámka (nepovinné)" value="${escapeHtml(own?.note||'')}"><div class="decision-actions"><button class="secondary-button" data-decide="met" data-decide-criterion="${id}">${icon('circle-check')}Splněno</button><button class="secondary-button" data-decide="not_met" data-decide-criterion="${id}">${icon('circle-x')}Nesplněno</button>${own?`<button class="secondary-button" data-decide="clear" data-decide-criterion="${id}">${icon('rotate-ccw')}Zrušit doplnění</button>`:''}</div></div>`;
}

async function decideCriterion(criterionId, choice) {
  const field=name=>document.querySelector(`[data-decide-${name}="${CSS.escape(criterionId)}"]`)?.value.trim()||null;
  const body=choice==='clear'?{status:null}:{status:choice,value:field('value'),note:field('note')};
  const patientId=encodeURIComponent(state.patient.id);
  await api(`/api/patients/${patientId}/criteria/${encodeURIComponent(state.trialId)}/${encodeURIComponent(criterionId)}`,{method:'PUT',body:JSON.stringify(body)});
  state.patient=await api(`/api/patients/${patientId}`);
  render();
  toast(choice==='clear'?'Doplnění zrušeno.':'Uloženo. Shoda se studií přepočítána.');
}

function renderCriterion(decision,result) {
  const criterion=result.criteria.criteria.find(item=>item.id===decision.criterion_id);
  const expanded=state.expanded.has(decision.criterion_id);
  return `<article class="criterion-row"><button class="criterion-toggle" data-criterion="${escapeHtml(criterion.id)}" aria-expanded="${expanded}"><span class="criterion-index">${String(result.criteria.criteria.indexOf(criterion)+1).padStart(2,'0')}</span><span class="criterion-copy"><span class="criterion-title">${escapeHtml(criterion.requirement)}</span><span class="criterion-reason">${escapeHtml(decision.reason)}</span></span>${decision.clinician?`<span class="status-pill status-missing" title="Doplněno ${date(decision.clinician.edited_at)}">${icon('pencil')}Lékař</span>`:''}${pill(decision.status)}${icon(expanded?'chevron-up':'chevron-down')}</button>${expanded?`<div class="evidence-area"><div class="evidence-block"><div class="evidence-label">POŽADAVEK STUDIE${criterion.role==='exclusion'?' · VYLUČOVACÍ PODMÍNKA':''}</div><div class="evidence-quote"><mark>${escapeHtml(criterion.source.quote)}</mark></div>${evidenceButton(criterion.source,'Původní znění kritéria','evidence-link')}</div><div class="evidence-label">DOKLAD V DOKUMENTACI</div>${decision.evidence.length?decision.evidence.map(evidence=>`<div class="evidence-block"><div class="evidence-quote"><mark>${escapeHtml(evidence.quote)}</mark></div>${evidenceButton(evidence,sourceTitle(evidence.source_id,result),'evidence-link')}</div>`).join(''):`<p class="empty-note">${decision.status==='missing'?'Potřebný údaj nebyl v posouzených zdrojích nalezen. Nejde o důkaz nesplnění.':'Pro tento závěr není ověřitelný doklad.'}</p><div class="cell-subtitle">Rozsah: ${result.patient_facts.source_ids.length} posouzených zdrojů</div>${result.patient_facts.source_ids.slice(0,4).map(identifier=>evidenceButton({source_id:identifier},sourceTitle(identifier,result),'evidence-link')).join('<br>')}`}${decision.action?`<div class="next-action">${icon('arrow-right')}<span>${escapeHtml(decision.action)}</span></div>`:''}${decisionForm(decision)}</div>`:''}</article>`;
}

// Database overview: every study and patient in our database, with what we extracted and clinician edits.
const outcomes = {met:'Splňuje kritéria', review:'K ověření', not_met:'Nesplňuje'};
const outcome = result => result.status === 'met' ? 'met' : result.status === 'not_met' ? 'not_met' : 'review';
const outcomeRank = {met:0, review:1, not_met:2};
const byOutcome = (first, second) => Number(second.result.badge) - Number(first.result.badge) || outcomeRank[outcome(first.result)] - outcomeRank[outcome(second.result)] || second.result.match_percent - first.result.match_percent;
const slimResult = result => ({trial_id:result.trial_id, assessed_at:result.assessed_at, ...result.summary});
// Search looks only at identifiers and names, ignoring case, spaces and slashes, so a birth number
// (rodné číslo) matches with or without its slash once records carry one.
const normalize = value => String(value ?? '').toLocaleLowerCase('cs').replace(/[\s/]+/g, '');
const searchHit = (fields, query = state.overviewQuery) => !query || fields.some(field => normalize(field).includes(normalize(query)));
const sexLabel = sex => sex === 'male' ? 'M' : sex === 'female' ? 'Ž' : '—';
const sexName = sex => sex === 'male' ? 'Muž' : sex === 'female' ? 'Žena' : 'Neuvedeno';
const alertTag = result => result.badge ? `<span class="alert-tag" title="Všechna kritéria splněna, studie nabírá na pracovišti">${icon('bell-ring')}</span>` : '';
const recruitment = {RECRUITING:'Nabírá', NOT_YET_RECRUITING:'Zatím nenabírá', ACTIVE_NOT_RECRUITING:'Aktivní, nenabírá', ENROLLING_BY_INVITATION:'Nábor na pozvání', COMPLETED:'Ukončena', TERMINATED:'Předčasně ukončena', SUSPENDED:'Pozastavena', WITHDRAWN:'Stažena'};
const openStatuses = ['RECRUITING', 'ENROLLING_BY_INVITATION'];
const overviewFilters = {trials:[['all','Všechny'],['open','Otevřené'],['candidates','S kandidáty']], patients:[['all','Všichni'],['candidates','S kandidátní studií'],['mine','Moji pacienti']]};
// Candidate = no criterion contradicted by the record (missing or unclear never hides a patient).
const isCandidate = result => result.counts.not_met === 0;
const groupOf = result => isCandidate(result) ? 'candidate' : result.future ? 'future' : result.counts.not_met === 1 ? 'near' : 'other';
const groups = [['candidate', 'Kandidáti', 'Žádné kritérium nesplněno – posoudit', 'circle-check'], ['near', 'Blízko', 'Právě jedno kritérium nesplněno', 'circle-dot'], ['future', 'Budoucí', 'Jediná překážka je progrese nebo časový odstup', 'calendar-clock'], ['other', 'Nevhodní', 'Více nesplněných kritérií', 'circle-x']];
// Needs the clinician–patient assignment from the hospital system; shown so the workflow is visible.
const disabledFilters = {mine:'Napojí se na přiřazení pacientů z nemocničního systému (NIS).'};
const loadingBlock = `<div class="loading"><span class="spinner"></span>Načítání údajů</div>`;
const inPlay = results => results.filter(result => outcome(result) !== 'not_met').length;
const factGroups = [['zaklad','Základní údaje','user-round'], ['nador','Diagnóza a nádor','microscope'], ['laborator','Laboratoř','test-tubes'], ['lecba','Léčba','pill'], ['ostatni','Ostatní údaje','notebook-tabs']];
// Model facts carry no category, so group them by name; seeded facts name theirs explicitly.
const factCategory = fact => fact.category || (/věk|pohlaví|výšk|hmotnost|váha|ecog|who|bmi/i.test(fact.name) ? 'zaklad' : /diagn|histolog|stadi|tnm|gleason|metast|nádor|tumor|grade|mutac|brca|biomark|pd-?l1|hrr/i.test(fact.name) ? 'nador' : /léčb|terap|lék|chemo|radio|operac|chirurg|linie|režim|hormon|adt/i.test(fact.name) ? 'lecba' : /hemoglobin|hgb|trombo|plt|neutro|leuko|kreatinin|egfr|alt|ast|bilirubin|albumin|psa|testosteron|glukóz|laborat/i.test(fact.name) ? 'laborator' : 'ostatni');
const cohort = () => state.mode === 'demo' ? state.demo.patients : state.data.patients;
const patientSummary = identifier => cohort().find(patient => patient.id === identifier);

async function loadOverview() {
  if (state.mode === 'demo') {
    // The synthetic cohort lives in the page, so demo edits change it in memory only.
    const results = state.demo.patients.flatMap(patient => patient.results);
    state.overview = {
      patients:state.demo.patients.map(patient => ({id:patient.id, facts:patient.facts.length, verified:patient.facts.filter(fact => fact.edit?.verified).length, edited:patient.facts.filter(fact => fact.edit?.value).length, results:patient.results.map(slimResult)})),
      trials:state.demo.trials.map(trial => ({...trial, criteria:results.find(result => result.trial_id === trial.id)?.criteria.criteria.length ?? null})),
    };
  } else state.overview = await api('/api/overview');
}

// Detail payloads load on first view and are kept until an edit invalidates them.
const detailKey = (kind, identifier) => `${state.mode}:${kind}:${identifier}`;
function detail(kind, identifier) {
  const key = detailKey(kind, identifier);
  if (key in state.details) return state.details[key];
  if (!state.pending.has(key)) {
    state.pending.add(key);
    fetchDetail(kind, identifier).then(value => {state.details[key] = value;}).catch(error => {state.details[key] = {error:error.message};}).finally(() => {state.pending.delete(key); if (state.view === 'overview') render();});
  }
  return null;
}

async function fetchDetail(kind, identifier) {
  if (state.mode === 'demo') {
    if (kind === 'facts') return {model:'Syntetická ukázka', snapshot_at:patientSummary(identifier).snapshot_at, complete:true, limitations:['Syntetický pacient. Úpravy v demu se neukládají.'], facts:patientSummary(identifier).facts};
    const criteria = state.demo.patients.flatMap(patient => patient.results).find(result => result.trial_id === identifier)?.criteria;
    return {...trialById(identifier), criteria:criteria ? structuredClone(criteria) : null};
  }
  return api(kind === 'facts' ? `/api/patients/${encodeURIComponent(identifier)}/facts` : `/api/trials/${encodeURIComponent(identifier)}`);
}

// Facts of the loaded patient detail, so saves update the page in place without reloading.
function loadedFacts(owner) {
  const record = state.details[detailKey('facts', owner)];
  return record && !record.error ? record.facts || [] : [];
}

// Applies clinician changes ({F3: {value, verified}}) to the page first, then saves; rolls back on failure.
async function saveFacts(changes, request) {
  const owner = state.overviewPatient;
  const facts = loadedFacts(owner).filter(fact => fact.id in changes);
  const previous = new Map(facts.map(fact => [fact, fact.edit]));
  const now = new Date().toISOString();
  facts.forEach(fact => {fact.edit = {...fact.edit, ...changes[fact.id], edited_at:now};});
  refreshEntry(owner);
  render();
  if (state.mode === 'demo') return;
  try {
    const saved = await request(owner);
    facts.forEach(fact => {fact.edit = saved[fact.id] || fact.edit;});
  } catch (error) {
    previous.forEach((edit, fact) => {fact.edit = edit;});
    refreshEntry(owner);
    render();
    throw error;
  }
}

// Recount the patient's list entry locally; reloading the whole overview is slow on real records.
function refreshEntry(owner) {
  const entry = state.overview?.patients.find(item => item.id === owner);
  const facts = loadedFacts(owner);
  if (!entry) return;
  entry.verified = facts.filter(fact => fact.edit?.verified).length;
  entry.edited = facts.filter(fact => fact.edit?.value !== null && fact.edit?.value !== undefined).length;
}

function saveFact(identifier, value) {
  const fact = loadedFacts(state.overviewPatient).find(item => item.id === identifier);
  const body = {value, verified:Boolean(fact?.edit?.verified)};
  return saveFacts({[identifier]:body}, owner => api(`/api/patients/${encodeURIComponent(owner)}/facts/${encodeURIComponent(identifier)}`, {method:'PUT', body:JSON.stringify(body)}).then(edit => ({[identifier]:edit})));
}

function confirmAll(verified) {
  const changes = Object.fromEntries(loadedFacts(state.overviewPatient).map(fact => [fact.id, {value:fact.edit?.value ?? null, verified}]));
  return saveFacts(changes, owner => api(`/api/patients/${encodeURIComponent(owner)}/facts`, {method:'PUT', body:JSON.stringify({verified})}));
}

// Spreadsheet-style editing once "Upravit" is on: click a value, Enter or Tab saves and moves on,
// Esc cancels, clicking elsewhere saves. An emptied cell goes back to the extracted value.
function editableValue(ref, current, original) {
  if (!state.editMode) return `<span class="fact-value">${escapeHtml(current)}</span>`;
  if (state.editing !== ref) return `<span class="fact-value editable" data-cell="${escapeHtml(ref)}" title="Kliknutím upravíte">${escapeHtml(current)}</span>`;
  return `<input class="cell-input" data-cell-input="${escapeHtml(ref)}" data-original="${escapeHtml(original)}" data-current="${escapeHtml(current)}" value="${escapeHtml(state.draft ?? current)}" aria-label="Upravit hodnotu"><span class="cell-hint">Enter uložit · Tab další · Esc zrušit · prázdné = původní hodnota</span>`;
}

function startEditing(ref) {
  state.editing = ref; state.draft = null;
  render();
}

async function commitCell(input, step) {
  const ref = input.dataset.cellInput;
  if (state.editing !== ref) return;
  const refs = [...document.querySelectorAll('[data-cell], [data-cell-input]')].map(element => element.dataset.cell || element.dataset.cellInput);
  const next = step ? refs[refs.indexOf(ref) + step] : null;
  const value = input.value.trim();
  state.editing = next || null; state.draft = null;
  if (value === input.dataset.current) {render(); return;}
  await saveFact(ref, value && value !== input.dataset.original ? value : null);
}

function tally(results) {
  const counts = {met:0, review:0, not_met:0};
  results.forEach(result => counts[outcome(result)]++);
  return counts;
}

function tallyTrack(counts) {
  const total = counts.met + counts.review + counts.not_met;
  return `<div class="progress-track">${[['met','met'],['review','unknown'],['not_met','not_met']].map(([key,color]) => `<span class="bg-${color}" style="width:${100*counts[key]/(total||1)}%" title="${outcomes[key]}: ${counts[key]}"></span>`).join('')}</div>`;
}

function tallyStrip(counts) {
  return `<div class="stats-strip">${[['met','met'],['review','unknown'],['not_met','not_met']].map(([key,color]) => `<div class="stat ${color}"><b>${counts[key]}</b>${outcomes[key]}</div>`).join('')}<div class="stat"><b>${counts.met + counts.review + counts.not_met}</b>Posouzeno</div></div>`;
}

function scoreCell(result) {
  return `<div class="score-cell"><strong title="${escapeHtml(percentTitle(result))}">${result.match_percent}%</strong><span class="muted">${result.counts.met}/${result.assessable ?? result.total}</span></div><div class="progress-track">${Object.entries(result.counts).map(([status,count]) => `<span class="bg-${status}" style="width:${100*count/(result.total||1)}%" title="${labels[status]}: ${count}"></span>`).join('')}</div>`;
}

function openButton(patientId, trialId) {
  return `<button class="icon-button" data-open-patient="${escapeHtml(patientId)}" data-open-trial="${escapeHtml(trialId)}" title="Otevřít posouzení" aria-label="Otevřít posouzení">${icon('arrow-up-right')}</button>`;
}

function sourceButton(patientId, evidence, title) {
  return evidence?.source_id ? `<button class="icon-button" data-source="${escapeHtml(evidence.source_id)}" data-quote="${escapeHtml(evidence.quote || '')}" data-source-patient="${escapeHtml(patientId)}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${icon('file-text')}</button>` : '';
}

// Items are [label, html]; values are escaped by the caller.
function infoGrid(items) {
  return `<dl class="info-grid">${items.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>`;
}

function sectionTitle(name, title, count='') {
  return `<div class="section-heading section-gap">${icon(name)}<h3>${title}</h3>${count !== '' ? `<span class="count">${count}</span>` : ''}</div>`;
}

function listPager(total) {
  return total > state.overviewLimit ? `<button class="secondary-button list-more" data-action="overview-more">Zobrazit další (${total - state.overviewLimit})</button>` : '';
}

function editedTag(fact) {
  const edited = fact.edit?.value !== null && fact.edit?.value !== undefined;
  return edited ? `<div class="cell-subtitle">Původně: ${escapeHtml(fact.value)}</div><span class="tag-row"><span class="status-pill status-missing" title="Upraveno ${date(fact.edit.edited_at)}">${icon('pencil')}Upraveno lékařem</span></span>` : '';
}

// The registry prefixes the eligibility text with structured age and sex limits.
function registryLimits(registry) {
  const text = registry?.sources?.registry?.text || '';
  if (!text) return [];
  const field = name => text.match(new RegExp(`^${name}: (.*)$`, 'm'))?.[1];
  const age = value => !value || value === 'not specified' ? 'Neuvedeno' : escapeHtml(value.replace(/\s*Years?$/i, ' let').replace(/\s*Months?$/i, ' měsíců'));
  const sex = {ALL:'Bez omezení', MALE:'Pouze muži', FEMALE:'Pouze ženy'};
  return [['Minimální věk', age(field('Minimum age'))], ['Maximální věk', age(field('Maximum age'))], ['Pohlaví', sex[field('Sex')] || 'Neuvedeno']];
}

function criteriaSection(trial) {
  const registry = trial.registry;
  if (!trial.criteria && registry?.state !== 'resolved') return `<p class="empty-note">Studie zatím není propojena s ClinicalTrials.gov, podmínky účasti proto nejsou k dispozici. Propojení spustíte v sekci Data a ladění → Propojit registry.</p>`;
  if (!trial.criteria) return `<p class="empty-note">Kritéria zatím nebyla extrahována modelem. Níže je původní znění z registru.</p><pre class="eligibility-text">${escapeHtml(registry.sources?.registry?.text || '')}</pre>`;
  const groups = [['inclusion','Vstupní kritéria','circle-check'], ['exclusion','Vylučovací kritéria','circle-x']];
  return groups.map(([role, title, name]) => {
    const items = trial.criteria.criteria.filter(criterion => criterion.role === role);
    return items.length ? sectionTitle(name, title, items.length) + `<ol class="criteria-list">${items.map(criterion => {
      return `<li><span class="criterion-title">${escapeHtml(criterion.requirement)}</span><span class="criterion-reason">${escapeHtml(criterion.source.quote)}</span>${criterion.source.unverified ? '<span class="tag-row"><span class="status-pill status-unknown">Citace v registru neověřena</span></span>' : ''}</li>`;
    }).join('')}</ol>` : '';
  }).join('') + (trial.criteria.complete && !trial.criteria.limitations?.length ? '' : `<div class="assessment-foot">${trial.criteria.complete ? '' : '<div>Extrakce kritérií není úplná, ověřte původní znění v registru.</div>'}${(trial.criteria.limitations || []).map(limit => `<div>${escapeHtml(limit)}</div>`).join('')}</div>`);
}

function renderTrialDetail(trial, rows) {
  const registry = trial.registry;
  const head = `<div class="trial-head"><div class="eyebrow">${escapeHtml(trial.protocol)}${trial.hospital_id ? ` · ${escapeHtml(trial.hospital_id)}` : ''}${trial.synthetic ? ' · UKÁZKOVÁ STUDIE' : ''}</div><h2>${escapeHtml(trial.title)}</h2><div class="trial-links">${registry?.url ? `<a href="${escapeHtml(registry.url)}" target="_blank" rel="noreferrer">${escapeHtml(registry.nct_id || 'Registr')} ${icon('external-link')}</a>` : ''}${registry?.title && registry.title !== trial.title ? `<span class="muted">${escapeHtml(registry.title)}</span>` : ''}</div></div>`;
  const full = detail('trial', trial.id);
  const info = infoGrid([
    ['Indikace', escapeHtml(trial.diagnosis || 'Neuvedeno')],
    ['Fáze', escapeHtml(trial.phase || 'Neuvedeno')],
    ['Zadavatel', escapeHtml(trial.sponsor || 'Neuvedeno')],
    ['Stav studie', escapeHtml(recruitment[registry?.status] || registry?.status || 'Nezjištěno')],
    ['Nábor na pracovišti', registry && registry.state !== 'unresolved' ? (registry.site_recruiting ? 'Uveden v registru' : 'Nepotvrzen') : 'Nezjištěno'],
    ['Místa v ČR (Motol, Homolka)', registry?.sites?.length ? registry.sites.map(site => escapeHtml(site.facility)).join('<br>') : '—'],
    ...(full && !full.error ? registryLimits(full.registry) : []),
  ]);
  const criteria = !full ? loadingBlock : full.error ? `<p class="empty-note">${escapeHtml(full.error)}</p>` : criteriaSection(full);
  const row = ({patient, result}) => `<tr><td><span class="cell-title">${escapeHtml(patient.name)}</span><div class="cell-subtitle id-label">${escapeHtml(patient.id)}</div></td><td>${escapeHtml(patient.age || '—')} / ${sexLabel(patient.sex)}</td><td>${escapeHtml(patient.diagnoses || 'Neuvedeno')}</td><td><span class="result-cell">${pill(result.status)}${alertTag(result)}</span></td><td>${scoreCell(result)}</td><td>${openButton(patient.id, trial.id)}</td></tr>`;
  const table = items => `<div class="table-wrap"><table class="data-table"><thead><tr><th>Pacient</th><th>Věk / pohlaví</th><th>Diagnózy</th><th>Výsledek</th><th>Shoda kritérií</th><th></th></tr></thead><tbody>${items.map(row).join('')}</tbody></table></div>`;
  const grouped = groups.map(([key, title, hint, name]) => {
    const items = rows.filter(item => groupOf(item.result) === key).sort((first, second) => second.result.match_percent - first.result.match_percent);
    if (!items.length) return '';
    const all = state.groupsOpen.has(`${trial.id}:${key}`);
    const more = items.length > 15 && !all ? `<button class="secondary-button group-more" data-group-more="${escapeHtml(`${trial.id}:${key}`)}">Zobrazit všech ${items.length}</button>` : '';
    return `<details class="patient-group group-${key}" ${key === 'other' ? '' : 'open'}><summary>${icon(name)}<strong>${title}</strong><span class="count">${items.length}</span><span class="muted">${hint}</span></summary>${table(all ? items : items.slice(0, 15))}${more}</details>`;
  }).join('');
  const patients = rows.length ? grouped + `<div class="assessment-foot">Výsledek „Nesplňuje“ je návrh k ověření lékařem, nikoli vyloučení ze studie.</div>` : `<p class="empty-note">Studie zatím nebyla porovnána s žádným pacientem.</p>`;
  return `<section class="match-detail">${head}${sectionTitle('info','Údaje o studii')}${info}${sectionTitle('list-checks','Podmínky účasti', full?.criteria ? full.criteria.criteria.length : '')}${criteria}${sectionTitle('users-round','Posouzení pacienti', rows.length)}${patients}</section>`;
}

function renderOverviewTrials() {
  const {patients, trials} = state.overview;
  const rowsFor = trialId => patients.flatMap(entry => entry.results.filter(result => result.trial_id === trialId).map(result => ({patient:patientSummary(entry.id) || {id:entry.id, name:entry.id}, result}))).sort(byOutcome);
  const filter = {all:() => true, open:trial => openStatuses.includes(trial.registry?.status), candidates:trial => rowsFor(trial.id).some(row => isCandidate(row.result))}[state.overviewFilter] || (() => true);
  const listed = trials.filter(trial => filter(trial) && searchHit([trial.protocol, trial.title, trial.diagnosis, trial.sponsor, trial.registry?.nct_id])).map(trial => ({trial, rows:rowsFor(trial.id)}))
    .sort((first, second) => second.rows.filter(row => isCandidate(row.result)).length - first.rows.filter(row => isCandidate(row.result)).length || second.rows.length - first.rows.length || Number(Boolean(second.trial.criteria)) - Number(Boolean(first.trial.criteria)));
  if (!trials.length) return `<p class="empty-note">V databázi zatím nejsou žádné studie. Vložte workbook studií (*.xlsx) do projektové složky, nebo spusťte ukázková data (seed_demo.py).</p>`;
  if (!listed.length) return `<p class="empty-note">Žádná studie neodpovídá hledání.</p>`;
  if (!listed.some(item => item.trial.id === state.overviewTrial)) state.overviewTrial = listed.find(item => item.trial.id === state.trialId)?.trial.id || listed[0].trial.id;
  const selected = listed.find(item => item.trial.id === state.overviewTrial);
  return `<div class="matches-layout"><aside class="study-list" aria-label="Studie v databázi">${listed.slice(0, state.overviewLimit).map(({trial, rows}) => {const counts = tally(rows.map(row => row.result));return `<button class="study-choice ${trial.id === state.overviewTrial ? 'active' : ''}" data-overview-trial="${escapeHtml(trial.id)}"><div class="study-protocol">${escapeHtml(trial.protocol)} · ${escapeHtml(recruitment[trial.registry?.status] || 'Stav nezjištěn')}</div><div class="study-name">${escapeHtml(trial.title)}</div><div class="study-score"><span>${trial.criteria ? `${trial.criteria} kritérií` : trial.registry?.state === 'resolved' ? 'Kritéria neextrahována' : 'Nepropojeno s registrem'}${rows.length ? ` · ${rows.filter(row => isCandidate(row.result)).length} kandidátů z ${rows.length}` : ''}</span>${rows.length ? `<strong>${rows.filter(row => isCandidate(row.result)).length}</strong>` : ''}</div>${rows.length ? tallyTrack(counts) : ''}</button>`;}).join('')}${listPager(listed.length)}</aside>${renderTrialDetail(selected.trial, selected.rows)}</div>`;
}

function factRow(patientId, fact) {
  const ref = fact.id;
  const current = fact.edit?.value ?? fact.value;
  return `<tr><td><span class="cell-title">${escapeHtml(fact.name)}</span><div class="cell-subtitle id-label">${escapeHtml(fact.id)}</div></td><td class="value-cell">${editableValue(ref, current, fact.value)}${state.editing !== ref ? editedTag(fact) : ''}</td><td>${fact.date ? date(fact.date) : '<span class="muted">—</span>'}</td><td><span class="cell-subtitle quote-cell">${escapeHtml(fact.evidence.quote)}</span></td><td>${sourceButton(patientId, fact.evidence, 'Zobrazit v dokumentu')}</td></tr>`;
}

// One confirmation for the whole extraction, plus an edit mode that unlocks the values.
function reviewBanner(facts) {
  const verified = facts.filter(fact => fact.edit?.verified);
  const latest = verified.map(fact => fact.edit.edited_at).sort().at(-1);
  const editButton = `<button class="secondary-button" data-facts-edit>${icon(state.editMode ? 'check' : 'pencil')}${state.editMode ? 'Hotovo' : 'Upravit'}</button>`;
  if (verified.length === facts.length) return `<div class="review-banner verified">${icon('shield-check')}<span><strong>Údaje potvrzeny lékařem</strong><small>${date(latest)} · všech ${facts.length} údajů</small></span>${editButton}<button class="secondary-button" data-confirm-all="0">Zrušit potvrzení</button></div>`;
  return `<div class="review-banner">${icon('shield-alert')}<span><strong>${verified.length ? `Potvrzeno ${verified.length} z ${facts.length} údajů` : 'Údaje čekají na potvrzení lékařem'}</strong><small>${state.editMode ? 'Klikněte na hodnotu a přepište ji. Enter uloží a přejde na další.' : 'Zkontrolujte extrahované údaje, případně je upravte a potvrďte.'}</small></span>${editButton}<button class="primary-button" data-confirm-all="1">${icon('shield-check')}Potvrdit vše</button></div>`;
}

function renderPatientDetail(summary) {
  const record = detail('facts', summary.id);
  const head = `<div class="trial-head"><div class="eyebrow">${escapeHtml(summary.id)} · ${summary.synthetic ? 'SYNTETICKÁ UKÁZKA' : 'PSEUDONYMIZOVANÝ ZÁZNAM'}</div><h2>${escapeHtml(summary.name)}</h2><div class="trial-links"><span class="muted">${escapeHtml(summary.age || 'Věk neuveden')} · ${sexName(summary.sex)} · ${escapeHtml(summary.diagnoses || 'Diagnóza neuvedena')}</span>${summary.deceased ? '<span class="status-pill status-not_met">Záznam o úmrtí</span>' : ''}</div></div>`;
  if (!record) return `<section class="match-detail">${head}${loadingBlock}</section>`;
  if (record.error) return `<section class="match-detail">${head}<p class="empty-note">${escapeHtml(record.error)}</p></section>`;
  if (!record.facts) return `<section class="match-detail">${head}<div class="empty-state">${icon('database')}<h2>V naší databázi zatím nejsou žádné údaje</h2><p>Dokumentace pacienta ještě nebyla zpracována (precompute.py), nebo se od posledního zpracování změnila.</p></div></section>`;
  const edited = record.facts.filter(fact => fact.edit?.value !== null && fact.edit?.value !== undefined).length;
  const summaryLine = `<div class="facts-summary"><span><strong>${record.facts.length}</strong> údajů</span><span><strong>${edited}</strong> upraveno lékařem</span><span>Snapshot dokumentace: <strong>${date(record.snapshot_at)}</strong></span><span>Zdroj: <strong>${escapeHtml(record.model)}</strong></span></div>${record.complete ? '' : '<div class="watch-banner"><strong>' + icon('triangle-alert') + 'Zpracován jen výběr dokumentace</strong><small>Chybějící údaj nemusí znamenat, že v dokumentaci není.</small></div>'}`;
  const groups = factGroups.map(([category, title, name]) => {
    const facts = record.facts.filter(fact => factCategory(fact) === category);
    return facts.length ? sectionTitle(name, title, facts.length) + `<div class="table-wrap"><table class="data-table fact-table ${state.editMode ? 'editing' : ''}"><thead><tr><th>Údaj</th><th>Hodnota</th><th>Datum</th><th>Citace ze zdroje</th><th></th></tr></thead><tbody>${facts.map(fact => factRow(summary.id, fact)).join('')}</tbody></table></div>` : '';
  }).join('');
  return `<section class="match-detail">${head}${reviewBanner(record.facts)}${summaryLine}${groups}<div class="assessment-foot">${(record.limitations || []).map(limit => `<div>${escapeHtml(limit)}</div>`).join('')}<div>Úpravy lékaře se ukládají vedle původní extrakce, ta se nepřepisuje.</div></div></section>`;
}

function renderOverviewPatients() {
  const entries = Object.fromEntries(state.overview.patients.map(entry => [entry.id, entry]));
  const all = cohort();
  const wanted = state.overviewFilter === 'candidates' ? (patient => (entries[patient.id]?.results || []).some(isCandidate)) : (() => true);
  const listed = all.filter(patient => wanted(patient) && searchHit([patient.id, patient.name, patient.diagnoses, patient.rc]))
    .sort((first, second) => (entries[second.id]?.results || []).filter(isCandidate).length - (entries[first.id]?.results || []).filter(isCandidate).length || Number(Boolean(entries[second.id]?.facts)) - Number(Boolean(entries[first.id]?.facts)) || Number(second.target) - Number(first.target));
  if (!listed.length) return `<p class="empty-note">Žádný pacient neodpovídá hledání.</p>`;
  if (!listed.some(patient => patient.id === state.overviewPatient)) state.overviewPatient = listed.find(patient => patient.id === state.patient.id)?.id || listed[0].id;
  const selected = listed.find(patient => patient.id === state.overviewPatient);
  return `<div class="matches-layout"><aside class="study-list" aria-label="Pacienti">${listed.slice(0, state.overviewLimit).map(patient => {const entry = entries[patient.id];return `<button class="study-choice ${patient.id === state.overviewPatient ? 'active' : ''}" data-overview-patient="${escapeHtml(patient.id)}"><div class="study-protocol id-label">${escapeHtml(patient.id)}</div><div class="study-name">${escapeHtml(patient.name)}</div><div class="study-score"><span>${escapeHtml(patient.age || '—')} / ${sexLabel(patient.sex)} · ${escapeHtml(patient.diagnoses || '—')}<br>${entry?.facts ? `${entry.results.filter(isCandidate).length} kandidátních studií · ${entry.facts} údajů${entry.verified ? ` · ${entry.verified} ověřeno` : ''}` : '<span class="muted">Nezpracováno</span>'}</span>${entry?.facts ? `<strong title="Kandidátní studie">${entry.results.filter(isCandidate).length}</strong>` : ''}</div></button>`;}).join('')}${listPager(listed.length)}</aside>${renderPatientDetail(selected)}</div>`;
}

function renderOverview() {
  const {patients, trials} = state.overview;
  const all = cohort();
  const processed = patients.filter(entry => entry.facts).length;
  const header = heading('Přehled databáze', `${trials.length} ${plural(trials.length, 'studie', 'studie', 'studií')} (${trials.filter(trial => trial.criteria).length} s kritérii) · ${all.length} ${plural(all.length, 'pacient', 'pacienti', 'pacientů')} (${processed} ${plural(processed, 'zpracovaný', 'zpracovaní', 'zpracovaných')})`);
  const tabs = [['trials','Studie',trials.length],['patients','Pacienti',all.length]];
  const placeholder = state.overviewTab === 'trials' ? 'Hledat protokol, název, indikaci, NCT' : 'Hledat ID pacienta (později rodné číslo), jméno, diagnózu';
  const chips = overviewFilters[state.overviewTab].map(([value,title]) => disabledFilters[value] ? `<button class="chip" disabled title="${escapeHtml(disabledFilters[value])}">${icon('lock')}${title}</button>` : `<button class="chip ${state.overviewFilter === value ? 'selected' : ''}" data-overview-filter="${value}">${title}</button>`).join('');
  return header + `<div class="debug-toolbar"><div class="table-tabs">${tabs.map(([tab,title,count]) => `<button data-overview-tab="${tab}" class="${tab === state.overviewTab ? 'selected' : ''}">${title}<small>${count}</small></button>`).join('')}</div><div class="search-field overview-search">${icon('search')}<input id="overview-search" value="${escapeHtml(state.overviewQuery)}" placeholder="${placeholder}" aria-label="Hledat"></div></div><div class="chip-row" role="group" aria-label="Filtr">${chips}</div>` + (state.overviewTab === 'trials' ? renderOverviewTrials() : renderOverviewPatients());
}

async function openMatch(patientId, trialId) {
  if (patientId !== state.patient.id) await selectPatient(patientId);
  state.view = 'matches'; state.trialId = trialId; state.expanded.clear(); state.filter = 'all';
  render(); scrollTo(0, 0);
}

function renderDebug() {
  const patients=cohort();
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
  return heading('Data a ladění','Zdrojové tabulky, ověřené identifikátory a výstupy modelu',`<button class="secondary-button" data-view="nis">${icon('arrow-left')}Karta pacienta</button>`)+`<div class="debug-toolbar"><div class="table-tabs">${[['patients','Pacienti',patients.length],['trials','Studie',trials.length],['runs','Výstupy',currentResults().length]].map(([tab,title,count])=>`<button data-debug-tab="${tab}" class="${tab===state.debugTab?'selected':''}">${title}<small>${count}</small></button>`).join('')}</div>${state.mode==='real'?`<button class="secondary-button" data-action="registry">${icon('refresh-cw')}Propojit registry</button>`:''}</div>${state.debugTab!=='runs'?`<div class="search-field debug-search">${icon('search')}<input id="debug-search" value="${escapeHtml(state.query)}" placeholder="Hledat v tabulce" aria-label="Hledat v tabulce"></div>`:''}<div class="table-wrap">${content}</div>${state.debugTab!=='runs'?`<div class="table-foot"><span>${records.length} záznamů</span><div class="pager"><button class="icon-button" data-page="${state.page-1}" ${state.page===0?'disabled':''} title="Předchozí strana" aria-label="Předchozí strana">${icon('chevron-left')}</button><span>${state.page+1} / ${pageCount}</span><button class="icon-button" data-page="${state.page+1}" ${state.page>=pageCount-1?'disabled':''} title="Další strana" aria-label="Další strana">${icon('chevron-right')}</button></div></div>`:''}`;
}

function render() {
  if(!state.patient)return;
  document.querySelector('#main').innerHTML=state.view==='nis'?renderNis():state.view==='matches'?renderMatches():state.view==='overview'?renderOverview():renderDebug();
  document.body.classList.toggle('popup',popup);
  document.body.classList.toggle('nis-mode',state.view==='nis'&&!popup);
  document.querySelector('.footer-link').hidden=state.view==='debug';
  document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('selected',button.dataset.mode===state.mode));
  const banner=document.querySelector('#demo-banner');
  banner.hidden=state.mode!=='demo'&&!state.data?.seeded;
  banner.querySelector('span').textContent=state.mode==='demo'?'Syntetičtí pacienti a studie. Nejde o skutečné klinické výsledky.':'Ukázková databáze: studie jsou smyšlené, údaje pacientů jsou vybrané pravidly (bez LLM) ze skutečné dokumentace.';
  if(popup)document.title=`TrialScreen | ${state.view==='overview'?'Přehled databáze':'Shoda se studiemi'}`;
  const cellInput=document.querySelector('[data-cell-input]');
  // Until the user types, keep the whole value selected so typing replaces it, as in a spreadsheet.
  if(cellInput&&document.activeElement!==cellInput){cellInput.focus();if(state.draft===null)cellInput.select();else cellInput.setSelectionRange(cellInput.value.length,cellInput.value.length);}
  document.querySelectorAll('.app-nav [data-view]').forEach(button=>button.classList.toggle('selected',button.dataset.view===state.view));
  document.querySelector('#footer-mode').textContent=state.mode==='demo'?'Syntetické ukázkové záznamy':'Pseudonymizovaná data · Posunutá data záznamů';
  refreshIcons();
}

async function selectPatient(identifier) {
  if(state.mode==='demo'){state.patient=patientSummary(identifier);state.trialId=null;state.expanded.clear();state.filter='all';render();return;}
  const sequence=++patientRequest;
  const patient=await api(`/api/patients/${encodeURIComponent(identifier)}`);
  if(sequence!==patientRequest||state.mode!=='real')return;
  state.patient=patient;state.trialId=null;state.expanded.clear();state.filter='all';
  if(!popup)history.replaceState(null,'',`?patient=${encodeURIComponent(identifier)}`);
  render();
}

// Older servers return a single demo patient; normalize so the page works against either.
async function loadDemo() {
  const demo = await api('/api/demo');
  demo.patients ||= [demo.patient];
  demo.patients.forEach(patient => {patient.facts ||= [];});
  return demo;
}

async function setMode(mode) {
  ++patientRequest;state.mode=mode;state.trialId=null;state.expanded.clear();state.filter='all';state.query='';state.page=0;
  if(mode==='demo') {state.demo ||= await loadDemo();state.patient=state.demo.patient;render();}
  else await selectPatient(state.realPatientId || state.data.patients.find(patient=>patient.target&&!patient.deceased)?.id || state.data.patients[0].id);
}

async function showSource(identifier,quote='',patientId=state.patient.id) {
  const source=state.mode==='demo'?patientSummary(patientId)?.sources.find(item=>item.id===identifier):await api(`/api/patients/${encodeURIComponent(patientId)}/sources/${encodeURIComponent(identifier)}${state.trialId?`?trial_id=${encodeURIComponent(state.trialId)}`:''}`);
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
    // Save an open cell before acting, so the click is not lost to the re-render that saving causes.
    const cellInput=document.querySelector('[data-cell-input]');
    // The page updates synchronously; the request finishes in the background.
    if(cellInput&&state.editing)commitCell(cellInput,0).catch(error=>toast(error.message));
    if(button.dataset.view){if(button.dataset.view==='overview')await loadOverview();state.view=button.dataset.view;render();scrollTo(0,0);}
    if(button.dataset.overviewTab){state.overviewTab=button.dataset.overviewTab;state.editMode=false;state.editing=null;state.overviewQuery='';state.overviewFilter='all';state.overviewLimit=40;render();}
    if(button.dataset.overviewFilter){state.overviewFilter=button.dataset.overviewFilter;state.overviewLimit=40;render();}
    if(button.dataset.action==='overview-more'){state.overviewLimit+=40;render();}
    if(button.dataset.overviewTrial){state.overviewTrial=button.dataset.overviewTrial;render();}
    if(button.dataset.overviewPatient){state.overviewPatient=button.dataset.overviewPatient;state.editMode=false;state.editing=null;render();}
    if(button.dataset.openPatient)await openMatch(button.dataset.openPatient,button.dataset.openTrial);
    if(button.hasAttribute('data-facts-edit')){state.editMode=!state.editMode;state.editing=null;render();}
    if(button.dataset.confirmAll)await confirmAll(button.dataset.confirmAll==='1');
    if(button.dataset.mode){if(state.mode==='real')state.realPatientId=state.patient.id;await setMode(button.dataset.mode);}
    if(button.dataset.source)await showSource(button.dataset.source,button.dataset.quote||'',button.dataset.sourcePatient||state.patient.id);
    if(button.hasAttribute('data-close-dialog'))button.closest('dialog').close();
    if(button.dataset.trial){state.trialId=button.dataset.trial;state.expanded.clear();render();}
    if(button.dataset.decide)await decideCriterion(button.dataset.decideCriterion,button.dataset.decide);
    if(button.dataset.criterion){const identifier=button.dataset.criterion;state.expanded.has(identifier)?state.expanded.delete(identifier):state.expanded.add(identifier);render();}
    if(button.dataset.debugTab){state.debugTab=button.dataset.debugTab;state.query='';state.page=0;render();}
    if(button.dataset.page!==undefined){state.page=Number(button.dataset.page);render();}
    if(button.dataset.patient){state.view='nis';await selectPatient(button.dataset.patient);}
    if(button.dataset.groupMore){state.groupsOpen.add(button.dataset.groupMore);render();}
    if(button.dataset.action==='toggle-strip'){state.stripOpen=!state.stripOpen;render();}
    if(button.dataset.action==='open-trialscreen'){const opened=window.open(`/?${new URLSearchParams({view:'overview',mode:state.mode,patient:state.patient.id})}`,'trialscreen-matches','popup,width=1180,height=860');opened?.focus();}
    if(button.dataset.debugTrial){const trial=state.mode==='demo'?trialById(button.dataset.debugTrial):await api(`/api/trials/${encodeURIComponent(button.dataset.debugTrial)}`);document.querySelector('#source-title').textContent=trial.protocol;document.querySelector('#source-meta').textContent='Zdrojová metadata studie';document.querySelector('#source-text').textContent=JSON.stringify(trial,null,2);document.querySelector('#source-dialog').showModal();}
    if(button.dataset.action==='open-matches')openMatchesWindow();
    if(button.dataset.action==='registry')await startJob({kind:'registry'});
    if(button.dataset.action==='dismiss-job')document.querySelector('#job-banner').hidden=true;
  }catch(error){toast(error.message);}
});

document.addEventListener('click',event=>{
  const cell=event.target.closest('[data-cell]');
  if(cell&&state.editMode)startEditing(cell.dataset.cell);
});

document.addEventListener('keydown',event=>{
  const input=event.target.closest('[data-cell-input]');if(!input)return;
  if(event.key==='Escape'){event.preventDefault();state.editing=null;state.draft=null;render();}
  else if(event.key==='Enter'||event.key==='Tab'){event.preventDefault();commitCell(input,event.shiftKey?-1:1).catch(error=>toast(error.message));}
});

// Clicking elsewhere saves, like a spreadsheet; clicking a button is saved by the click handler instead.
// A re-render also blurs the input it replaces, so check after the event settles that this input is
// still on the page and still the one being edited.
document.addEventListener('focusout',event=>{
  const input=event.target.closest('[data-cell-input]');if(!input||event.relatedTarget?.closest('button'))return;
  setTimeout(()=>{if(input.isConnected&&document.activeElement!==input&&state.editing===input.dataset.cellInput)commitCell(input,0).catch(error=>toast(error.message));});
});

document.addEventListener('change',async event=>{
  try{
    if(event.target.id==='criterion-filter'){state.filter=event.target.value;render();}
  }catch(error){toast(error.message);}
});

document.addEventListener('input',event=>{
  if(event.target.dataset.cellInput)state.draft=event.target.value;
  if(event.target.id==='nis-search'){const cursor=event.target.selectionStart;state.nisQuery=event.target.value;render();const field=document.querySelector('#nis-search');field.focus();field.setSelectionRange(cursor,cursor);}
  if(event.target.id==='overview-search'){const cursor=event.target.selectionStart;state.overviewQuery=event.target.value;state.overviewLimit=40;render();const field=document.querySelector('#overview-search');field.focus();field.setSelectionRange(cursor,cursor);}
  if(event.target.id==='debug-search'){const cursor=event.target.selectionStart;state.query=event.target.value;state.page=0;render();const field=document.querySelector('#debug-search');field.focus();field.setSelectionRange(cursor,cursor);}
});

async function initialize(){
  try{
    state.data=await api('/api/bootstrap');
    if(popup&&params.get('view')==='overview'){
      document.title='TrialScreen | Přehled databáze';
      await selectPatient(params.get('patient')||state.data.patients[0].id);
      await loadOverview();state.view='overview';render();return;
    }
    if(popup){
      document.title='TrialScreen | Shoda se studiemi';
      state.view='matches';
      if(params.get('mode')==='demo'){state.mode='demo';state.demo=await loadDemo();state.patient=state.demo.patients.find(patient=>patient.id===params.get('patient'))||state.demo.patient;}
      else await selectPatient(params.get('patient'));
      state.trialId=params.get('trial');
      render();
      return;
    }
    if(!state.data.patients.length)throw new Error('Dataset nebyl nalezen v projektové složce.');
    const requested=params.get('patient');
    const patient=state.data.patients.find(item=>item.id===requested)||state.data.patients.find(item=>item.target&&!item.deceased&&item.notes>=5&&item.notes<=15&&item.labs>0)||state.data.patients[0];
    await selectPatient(patient.id);
    loadOverview().then(()=>{if(state.view==='nis')render();}).catch(()=>{});
    const active=state.data.jobs.find(job=>['queued','running'].includes(job.state));if(active)pollJob(active.id);
  }catch(error){document.querySelector('#main').innerHTML=`<div class="empty-state">${icon('circle-alert')}<h2>Data se nepodařilo načíst</h2><p>${escapeHtml(error.message)}</p></div>`;refreshIcons();}
}
initialize();