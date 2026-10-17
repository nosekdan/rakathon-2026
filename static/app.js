const state = {data:null, patient:null, demo:null, mode:'real', view:'patient', trialId:null, debugTab:'patients', filter:'all', query:'', page:0, expanded:new Set(), job:null, overview:null, overviewTab:'trials', overviewTrial:null, overviewPatient:null, overviewQuery:'', overviewFilter:'all', overviewLimit:40, details:{}, pending:new Set(), editing:null};
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
  return heading('Karta pacienta', 'Ambulantní přehled a podklady pro klinické studie', trialWidget()) + `
    <section class="medical-sheet" aria-label="Zdravotní karta">
      <div class="identity-row"><div class="patient-avatar">${icon('user-round')}</div><div class="identity-copy"><h2>${escapeHtml(patient.name)}</h2><div class="identity-meta"><span class="id-label">${escapeHtml(patient.id)}</span><span>·</span><span>${patient.synthetic ? 'Syntetická ukázka' : 'Pseudonymizovaný záznam'}</span>${patient.deceased ? '<span class="status-pill status-not_met">Záznam o úmrtí</span>' : ''}</div></div><div class="record-meta">Poslední dostupný záznam<strong>${date(patient.snapshot_at)}</strong>${patient.synthetic ? 'Ukázková dokumentace' : 'Posunuté datum datasetu'}</div></div>
      <div class="metric-grid">${metrics.map(metric=>`<div class="metric"><div class="metric-label">${icon(metric.icon)}${metric.name}</div><div class="metric-value">${escapeHtml(metric.value)}<small>${metric.unit || ''}</small></div>${metric.evidence ? evidenceButton(metric.evidence,`Zdroj: ${metric.name}`) : '<div class="metric-foot">Údaj není doložen</div>'}</div>`).join('')}</div>
    </section>
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

// Database overview: every study and patient in our database, with what we extracted and clinician edits.
const outcomes = {met:'Splňuje kritéria', review:'K ověření', not_met:'Nesplňuje'};
const outcome = result => result.status === 'met' ? 'met' : result.status === 'not_met' ? 'not_met' : 'review';
const outcomeRank = {met:0, review:1, not_met:2};
const byOutcome = (first, second) => Number(second.result.badge) - Number(first.result.badge) || outcomeRank[outcome(first.result)] - outcomeRank[outcome(second.result)] || second.result.match_percent - first.result.match_percent;
const slimResult = result => ({trial_id:result.trial_id, assessed_at:result.assessed_at, ...result.summary});
// Search looks only at identifiers and names, ignoring case, spaces and slashes, so a birth number
// (rodné číslo) matches with or without its slash once records carry one.
const normalize = value => String(value ?? '').toLocaleLowerCase('cs').replace(/[\s/]+/g, '');
const searchHit = fields => !state.overviewQuery || fields.some(field => normalize(field).includes(normalize(state.overviewQuery)));
const sexLabel = sex => sex === 'male' ? 'M' : sex === 'female' ? 'Ž' : '—';
const sexName = sex => sex === 'male' ? 'Muž' : sex === 'female' ? 'Žena' : 'Neuvedeno';
const alertTag = result => result.badge ? `<span class="alert-tag" title="Všechna kritéria splněna, studie nabírá na pracovišti">${icon('bell-ring')}</span>` : '';
const recruitment = {RECRUITING:'Nabírá', NOT_YET_RECRUITING:'Zatím nenabírá', ACTIVE_NOT_RECRUITING:'Aktivní, nenabírá', ENROLLING_BY_INVITATION:'Nábor na pozvání', COMPLETED:'Ukončena', TERMINATED:'Předčasně ukončena', SUSPENDED:'Pozastavena', WITHDRAWN:'Stažena'};
const openStatuses = ['RECRUITING', 'ENROLLING_BY_INVITATION'];
const overviewFilters = {trials:[['all','Všechny'],['open','Otevřené']], patients:[['all','Všichni'],['mine','Moji pacienti']]};
// Needs the clinician–patient assignment from the hospital system; shown so the workflow is visible.
const disabledFilters = {mine:'Napojí se na přiřazení pacientů z nemocničního systému (NIS).'};
const loadingBlock = `<div class="loading"><span class="spinner"></span>Načítání údajů</div>`;
const inPlay = results => results.filter(result => outcome(result) !== 'not_met').length;
const factGroups = [['zaklad','Základní údaje','user-round'], ['nador','Diagnóza a nádor','microscope'], ['laborator','Laboratoř','test-tubes'], ['lecba','Léčba','pill'], ['ostatni','Ostatní údaje','notebook-tabs']];
// Model facts carry no category, so group them by name; seeded facts name theirs explicitly.
const factCategory = fact => fact.category || (/věk|pohlaví|výšk|hmotnost|váha|ecog|who|bmi/i.test(fact.name) ? 'zaklad' : /diagn|histolog|stadi|tnm|gleason|metast|nádor|tumor|grade|mutac|brca|biomark|pd-?l1|hrr/i.test(fact.name) ? 'nador' : /léčb|terap|lék|chemo|radio|operac|chirurg|linie|režim|hormon|adt/i.test(fact.name) ? 'lecba' : /hemoglobin|hgb|trombo|plt|neutro|leuko|kreatinin|egfr|alt|ast|bilirubin|albumin|psa|testosteron|glukóz|laborat/i.test(fact.name) ? 'laborator' : 'ostatni');
const patientSummary = identifier => (state.mode === 'demo' ? [state.demo.patient] : state.data.patients).find(patient => patient.id === identifier);

async function loadOverview() {
  if (state.mode === 'demo') {
    const patient = state.demo.patient;
    state.overview = {patients:[{id:patient.id, facts:null, verified:0, edited:0, results:patient.results.map(slimResult)}], trials:state.demo.trials.map(trial => ({...trial, criteria:patient.results.find(result => result.trial_id === trial.id)?.criteria.criteria.length ?? null}))};
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
    if (kind === 'facts') return {facts:null};
    return {...trialById(identifier), criteria:state.demo.patient.results.find(result => result.trial_id === identifier)?.criteria || null};
  }
  return api(kind === 'facts' ? `/api/patients/${encodeURIComponent(identifier)}/facts` : `/api/trials/${encodeURIComponent(identifier)}`);
}

async function saveEdit(kind, ref, body) {
  if (state.mode === 'demo') throw new Error('V syntetickém demu nelze údaje upravovat.');
  const owner = kind === 'fact' ? state.overviewPatient : state.overviewTrial;
  const path = kind === 'fact' ? `/api/patients/${encodeURIComponent(owner)}/facts/${encodeURIComponent(ref)}` : kind === 'criterion' ? `/api/trials/${encodeURIComponent(owner)}/criteria/${encodeURIComponent(ref)}` : `/api/trials/${encodeURIComponent(owner)}/review`;
  await api(path, {method:'PUT', body:JSON.stringify(body)});
  delete state.details[detailKey(kind === 'fact' ? 'facts' : 'trial', owner)];
  state.editing = null;
  await loadOverview();
  render();
  toast('Uloženo. Původní extrakce zůstává zachována.');
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
  return `<div class="score-cell"><strong>${result.match_percent}%</strong><span class="muted">${result.counts.met}/${result.total}</span></div><div class="progress-track">${Object.entries(result.counts).map(([status,count]) => `<span class="bg-${status}" style="width:${100*count/(result.total||1)}%" title="${labels[status]}: ${count}"></span>`).join('')}</div>`;
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

function reviewTags(edit) {
  if (!edit) return '';
  return `${edit.value !== null ? `<span class="status-pill status-missing" title="Upraveno ${date(edit.edited_at)}">${icon('pencil')}Upraveno</span>` : ''}${edit.verified ? `<span class="status-pill status-met" title="Ověřeno ${date(edit.edited_at)}">${icon('shield-check')}Ověřeno lékařem</span>` : ''}`;
}

// Edit and confirm controls shared by facts and criteria. `ref` is "fact:F3" or "criterion:ecog".
function editControls(ref, edit, label) {
  const [kind, identifier] = ref.split(':');
  return `<span class="edit-actions"><button class="icon-button" data-edit="${escapeHtml(ref)}" title="Upravit ${escapeHtml(label)}" aria-label="Upravit ${escapeHtml(label)}">${icon('pencil')}</button><button class="icon-button ${edit?.verified ? 'verified' : ''}" data-verify="${escapeHtml(kind)}" data-ref="${escapeHtml(identifier)}" data-value="${escapeHtml(edit?.value ?? '')}" data-verified="${edit?.verified ? '' : '1'}" title="${edit?.verified ? 'Zrušit ověření' : 'Potvrdit jako správné'}" aria-label="${edit?.verified ? 'Zrušit ověření' : 'Potvrdit jako správné'}">${icon(edit?.verified ? 'shield-check' : 'check')}</button></span>`;
}

function editForm(ref, current, original, edit) {
  return `<form class="edit-form" data-edit-form="${escapeHtml(ref)}"><input name="value" value="${escapeHtml(current)}" aria-label="Nová hodnota" required><label class="check-label"><input type="checkbox" name="verified" ${edit?.verified ? 'checked' : ''}>Ověřeno</label><button class="primary-button" type="submit">Uložit</button><button class="secondary-button" type="button" data-cancel-edit>Zrušit</button>${edit?.value !== null && edit?.value !== undefined ? `<button class="secondary-button" type="button" data-reset-edit="${escapeHtml(ref)}">${icon('undo-2')}Vrátit původní</button>` : ''}<div class="cell-subtitle">Původně extrahováno: ${escapeHtml(original)}</div></form>`;
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
      const ref = `criterion:${criterion.id}`;
      const current = criterion.edit?.value ?? criterion.requirement;
      if (state.editing === ref) return `<li>${editForm(ref, current, criterion.requirement, criterion.edit)}</li>`;
      return `<li><div class="editable-row"><span><span class="criterion-title">${escapeHtml(current)}</span><span class="criterion-reason">${escapeHtml(criterion.source.quote)}</span><span class="tag-row">${reviewTags(criterion.edit)}${criterion.source.unverified ? '<span class="status-pill status-unknown">Citace v registru neověřena</span>' : ''}</span></span>${state.mode === 'real' ? editControls(ref, criterion.edit, 'kritérium') : ''}</div></li>`;
    }).join('')}</ol>` : '';
  }).join('') + (trial.criteria.complete && !trial.criteria.limitations?.length ? '' : `<div class="assessment-foot">${trial.criteria.complete ? '' : '<div>Extrakce kritérií není úplná, ověřte původní znění v registru.</div>'}${(trial.criteria.limitations || []).map(limit => `<div>${escapeHtml(limit)}</div>`).join('')}</div>`);
}

function reviewBanner(full) {
  if (!full?.criteria || state.mode !== 'real') return '';
  const review = full.review;
  const edited = full.criteria.criteria.filter(criterion => criterion.edit?.value !== null && criterion.edit?.value !== undefined).length;
  return review?.verified
    ? `<div class="review-banner verified">${icon('shield-check')}<span><strong>Kritéria studie potvrzena lékařem</strong><small>${date(review.edited_at)}${edited ? ` · ${edited} ${plural(edited, 'kritérium upraveno', 'kritéria upravena', 'kritérií upraveno')}` : ''}</small></span><button class="secondary-button" data-review="0">Zrušit potvrzení</button></div>`
    : `<div class="review-banner">${icon('shield-alert')}<span><strong>Kritéria čekají na kontrolu lékařem</strong><small>Zkontrolujte extrakci, případně kritéria upravte a studii potvrďte.</small></span><button class="primary-button" data-review="1">${icon('shield-check')}Potvrdit kritéria</button></div>`;
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
  const counts = tally(rows.map(row => row.result));
  const patients = rows.length ? tallyStrip(counts) + `<div class="table-wrap"><table class="data-table"><thead><tr><th>Pacient</th><th>Věk / pohlaví</th><th>Diagnózy</th><th>Výsledek</th><th>Shoda kritérií</th><th>Posouzeno</th><th></th></tr></thead><tbody>${rows.map(({patient, result}) => `<tr><td><span class="cell-title">${escapeHtml(patient.name)}</span><div class="cell-subtitle id-label">${escapeHtml(patient.id)}</div></td><td>${escapeHtml(patient.age || '—')} / ${sexLabel(patient.sex)}</td><td>${escapeHtml(patient.diagnoses || 'Neuvedeno')}</td><td><span class="result-cell">${pill(result.status)}${alertTag(result)}</span></td><td>${scoreCell(result)}</td><td>${date(result.assessed_at)}</td><td>${openButton(patient.id, trial.id)}</td></tr>`).join('')}</tbody></table></div><div class="assessment-foot">Výsledek „Nesplňuje“ je návrh k ověření lékařem, nikoli vyloučení ze studie.</div>` : `<p class="empty-note">Studie zatím nebyla porovnána s žádným pacientem.</p>`;
  return `<section class="match-detail">${head}${reviewBanner(full)}${sectionTitle('info','Údaje o studii')}${info}${sectionTitle('list-checks','Podmínky účasti', full?.criteria ? full.criteria.criteria.length : '')}${criteria}${sectionTitle('users-round','Posouzení pacienti', rows.length)}${patients}</section>`;
}

function renderOverviewTrials() {
  const {patients, trials} = state.overview;
  const rowsFor = trialId => patients.flatMap(entry => entry.results.filter(result => result.trial_id === trialId).map(result => ({patient:patientSummary(entry.id) || {id:entry.id, name:entry.id}, result}))).sort(byOutcome);
  const filter = {all:() => true, open:trial => openStatuses.includes(trial.registry?.status)}[state.overviewFilter];
  const listed = trials.filter(trial => filter(trial) && searchHit([trial.protocol, trial.title, trial.diagnosis, trial.sponsor, trial.registry?.nct_id])).map(trial => ({trial, rows:rowsFor(trial.id)}))
    .sort((first, second) => inPlay(second.rows.map(row => row.result)) - inPlay(first.rows.map(row => row.result)) || second.rows.length - first.rows.length || Number(Boolean(second.trial.criteria)) - Number(Boolean(first.trial.criteria)));
  if (!trials.length) return `<p class="empty-note">V databázi zatím nejsou žádné studie. Vložte workbook studií (*.xlsx) do projektové složky, nebo spusťte ukázková data (seed_demo.py).</p>`;
  if (!listed.length) return `<p class="empty-note">Žádná studie neodpovídá hledání.</p>`;
  if (!listed.some(item => item.trial.id === state.overviewTrial)) state.overviewTrial = listed.find(item => item.trial.id === state.trialId)?.trial.id || listed[0].trial.id;
  const selected = listed.find(item => item.trial.id === state.overviewTrial);
  return `<div class="matches-layout"><aside class="study-list" aria-label="Studie v databázi">${listed.slice(0, state.overviewLimit).map(({trial, rows}) => {const counts = tally(rows.map(row => row.result));return `<button class="study-choice ${trial.id === state.overviewTrial ? 'active' : ''}" data-overview-trial="${escapeHtml(trial.id)}"><div class="study-protocol">${escapeHtml(trial.protocol)} · ${escapeHtml(recruitment[trial.registry?.status] || 'Stav nezjištěn')}</div><div class="study-name">${escapeHtml(trial.title)}</div><div class="study-score"><span>${trial.criteria ? `${trial.criteria} kritérií` : trial.registry?.state === 'resolved' ? 'Kritéria neextrahována' : 'Nepropojeno s registrem'}${rows.length ? ` · ${counts.met + counts.review} z ${rows.length} k posouzení` : ''}</span>${rows.length ? `<strong>${counts.met + counts.review}</strong>` : ''}</div>${rows.length ? tallyTrack(counts) : ''}</button>`;}).join('')}${listPager(listed.length)}</aside>${renderTrialDetail(selected.trial, selected.rows)}</div>`;
}

function factRow(patientId, fact) {
  const ref = `fact:${fact.id}`;
  const current = fact.edit?.value ?? fact.value;
  if (state.editing === ref) return `<tr><td><span class="cell-title">${escapeHtml(fact.name)}</span></td><td colspan="4">${editForm(ref, current, fact.value, fact.edit)}</td></tr>`;
  return `<tr class="${fact.edit?.verified ? 'row-verified' : ''}"><td><span class="cell-title">${escapeHtml(fact.name)}</span><div class="cell-subtitle id-label">${escapeHtml(fact.id)}</div></td><td><span class="fact-value">${escapeHtml(current)}</span>${fact.edit?.value !== null && fact.edit?.value !== undefined ? `<div class="cell-subtitle">Původně: ${escapeHtml(fact.value)}</div>` : ''}<span class="tag-row">${reviewTags(fact.edit)}</span></td><td>${fact.date ? date(fact.date) : '<span class="muted">—</span>'}</td><td><span class="cell-subtitle quote-cell">${escapeHtml(fact.evidence.quote)}</span></td><td><span class="row-actions">${sourceButton(patientId, fact.evidence, 'Zobrazit v dokumentu')}${state.mode === 'real' ? editControls(ref, fact.edit, fact.name) : ''}</span></td></tr>`;
}

function renderPatientDetail(summary, entry) {
  const record = detail('facts', summary.id);
  const head = `<div class="trial-head"><div class="eyebrow">${escapeHtml(summary.id)} · ${summary.synthetic ? 'SYNTETICKÁ UKÁZKA' : 'PSEUDONYMIZOVANÝ ZÁZNAM'}</div><h2>${escapeHtml(summary.name)}</h2><div class="trial-links"><span class="muted">${escapeHtml(summary.age || 'Věk neuveden')} · ${sexName(summary.sex)} · ${escapeHtml(summary.diagnoses || 'Diagnóza neuvedena')}</span>${summary.deceased ? '<span class="status-pill status-not_met">Záznam o úmrtí</span>' : ''}</div></div>`;
  if (!record) return `<section class="match-detail">${head}${loadingBlock}</section>`;
  if (record.error) return `<section class="match-detail">${head}<p class="empty-note">${escapeHtml(record.error)}</p></section>`;
  if (!record.facts) return `<section class="match-detail">${head}<div class="empty-state">${icon('database')}<h2>V naší databázi zatím nejsou žádné údaje</h2><p>Dokumentace pacienta ještě nebyla zpracována (precompute.py), nebo se od posledního zpracování změnila.</p></div></section>`;
  const verified = record.facts.filter(fact => fact.edit?.verified).length;
  const edited = record.facts.filter(fact => fact.edit?.value !== null && fact.edit?.value !== undefined).length;
  const summaryLine = `<div class="facts-summary"><span><strong>${record.facts.length}</strong> údajů</span><span><strong>${verified}</strong> ověřeno lékařem</span><span><strong>${edited}</strong> upraveno</span><span>Snapshot dokumentace: <strong>${date(record.snapshot_at)}</strong></span><span>Zdroj: <strong>${escapeHtml(record.model)}</strong></span></div>${record.complete ? '' : '<div class="watch-banner"><strong>' + icon('triangle-alert') + 'Zpracován jen výběr dokumentace</strong><small>Chybějící údaj nemusí znamenat, že v dokumentaci není.</small></div>'}`;
  const groups = factGroups.map(([category, title, name]) => {
    const facts = record.facts.filter(fact => factCategory(fact) === category);
    return facts.length ? sectionTitle(name, title, facts.length) + `<div class="table-wrap"><table class="data-table fact-table"><thead><tr><th>Údaj</th><th>Hodnota</th><th>Datum</th><th>Citace ze zdroje</th><th></th></tr></thead><tbody>${facts.map(fact => factRow(summary.id, fact)).join('')}</tbody></table></div>` : '';
  }).join('');
  return `<section class="match-detail">${head}${summaryLine}${groups}<div class="assessment-foot">${(record.limitations || []).map(limit => `<div>${escapeHtml(limit)}</div>`).join('')}<div>Úpravy lékaře se ukládají vedle původní extrakce, ta se nepřepisuje.</div></div></section>`;
}

function renderOverviewPatients() {
  const entries = Object.fromEntries(state.overview.patients.map(entry => [entry.id, entry]));
  const all = state.mode === 'demo' ? [state.demo.patient] : state.data.patients;
  const listed = all.filter(patient => searchHit([patient.id, patient.name, patient.diagnoses, patient.rc]))
    .sort((first, second) => Number(Boolean(entries[second.id]?.facts)) - Number(Boolean(entries[first.id]?.facts)) || inPlay(entries[second.id]?.results || []) - inPlay(entries[first.id]?.results || []) || Number(second.target) - Number(first.target));
  if (!listed.length) return `<p class="empty-note">Žádný pacient neodpovídá hledání.</p>`;
  if (!listed.some(patient => patient.id === state.overviewPatient)) state.overviewPatient = listed.find(patient => patient.id === state.patient.id)?.id || listed[0].id;
  const selected = listed.find(patient => patient.id === state.overviewPatient);
  return `<div class="matches-layout"><aside class="study-list" aria-label="Pacienti">${listed.slice(0, state.overviewLimit).map(patient => {const entry = entries[patient.id];return `<button class="study-choice ${patient.id === state.overviewPatient ? 'active' : ''}" data-overview-patient="${escapeHtml(patient.id)}"><div class="study-protocol id-label">${escapeHtml(patient.id)}</div><div class="study-name">${escapeHtml(patient.name)}</div><div class="study-score"><span>${escapeHtml(patient.age || '—')} / ${sexLabel(patient.sex)} · ${escapeHtml(patient.diagnoses || '—')}<br>${entry?.facts ? `${entry.facts} údajů v databázi${entry.verified ? ` · ${entry.verified} ověřeno` : ''}` : '<span class="muted">Nezpracováno</span>'}</span>${entry?.facts ? `<strong>${entry.facts}</strong>` : ''}</div></button>`;}).join('')}${listPager(listed.length)}</aside>${renderPatientDetail(selected, entries[selected.id])}</div>`;
}

function renderOverview() {
  const {patients, trials} = state.overview;
  const all = state.mode === 'demo' ? [state.demo.patient] : state.data.patients;
  const processed = patients.filter(entry => entry.facts).length;
  const header = heading('Přehled databáze', `${trials.length} ${plural(trials.length, 'studie', 'studie', 'studií')} (${trials.filter(trial => trial.criteria).length} s kritérii) · ${all.length} ${plural(all.length, 'pacient', 'pacienti', 'pacientů')} (${processed} ${plural(processed, 'zpracovaný', 'zpracovaní', 'zpracovaných')})`);
  const tabs = [['trials','Studie',trials.length],['patients','Pacienti',all.length]];
  const placeholder = state.overviewTab === 'trials' ? 'Hledat protokol, název, indikaci, NCT' : 'Hledat ID pacienta (později rodné číslo), jméno, diagnózu';
  const chips = overviewFilters[state.overviewTab].map(([value,title]) => disabledFilters[value] ? `<button class="chip" disabled title="${escapeHtml(disabledFilters[value])}">${icon('lock')}${title}</button>` : `<button class="chip ${state.overviewFilter === value ? 'selected' : ''}" data-overview-filter="${value}">${title}</button>`).join('');
  return header + `<div class="debug-toolbar"><div class="table-tabs">${tabs.map(([tab,title,count]) => `<button data-overview-tab="${tab}" class="${tab === state.overviewTab ? 'selected' : ''}">${title}<small>${count}</small></button>`).join('')}</div><div class="search-field overview-search">${icon('search')}<input id="overview-search" value="${escapeHtml(state.overviewQuery)}" placeholder="${placeholder}" aria-label="Hledat"></div></div><div class="chip-row" role="group" aria-label="Filtr">${chips}</div>` + (state.overviewTab === 'trials' ? renderOverviewTrials() : renderOverviewPatients());
}

async function openMatch(patientId, trialId) {
  if (state.mode === 'real' && patientId !== state.patient.id) await selectPatient(patientId);
  state.view = 'matches'; state.trialId = trialId; state.expanded.clear(); state.filter = 'all';
  render(); scrollTo(0, 0);
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
  document.querySelector('#main').innerHTML=state.view==='patient'?renderPatient():state.view==='matches'?renderMatches():state.view==='overview'?renderOverview():renderDebug();
  document.body.classList.toggle('popup',popup);
  document.querySelector('.footer-link').hidden=state.view==='debug';
  document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('selected',button.dataset.mode===state.mode));
  const banner=document.querySelector('#demo-banner');
  banner.hidden=state.mode!=='demo'&&!state.data?.seeded;
  banner.querySelector('span').textContent=state.mode==='demo'?'Syntetický pacient a studie. Nejde o skutečné klinické výsledky.':'Ukázková databáze: studie jsou smyšlené, údaje pacientů jsou vybrané pravidly (bez LLM) ze skutečné dokumentace.';
  document.querySelectorAll('.app-nav [data-view]').forEach(button=>button.classList.toggle('selected',button.dataset.view===state.view));
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

async function showSource(identifier,quote='',patientId=state.patient.id) {
  const source=state.mode==='demo'?state.demo.patient.sources.find(item=>item.id===identifier):await api(`/api/patients/${encodeURIComponent(patientId)}/sources/${encodeURIComponent(identifier)}${state.trialId?`?trial_id=${encodeURIComponent(state.trialId)}`:''}`);
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
    if(button.dataset.view){if(button.dataset.view==='overview')await loadOverview();state.view=button.dataset.view;render();scrollTo(0,0);}
    if(button.dataset.overviewTab){state.overviewTab=button.dataset.overviewTab;state.overviewQuery='';state.overviewFilter='all';state.overviewLimit=40;render();}
    if(button.dataset.overviewFilter){state.overviewFilter=button.dataset.overviewFilter;state.overviewLimit=40;render();}
    if(button.dataset.action==='overview-more'){state.overviewLimit+=40;render();}
    if(button.dataset.overviewTrial){state.overviewTrial=button.dataset.overviewTrial;render();}
    if(button.dataset.overviewPatient){state.overviewPatient=button.dataset.overviewPatient;render();}
    if(button.dataset.openPatient)await openMatch(button.dataset.openPatient,button.dataset.openTrial);
    if(button.dataset.edit){state.editing=button.dataset.edit;render();document.querySelector('.edit-form input[name=value]')?.focus();}
    if(button.hasAttribute('data-cancel-edit')){state.editing=null;render();}
    if(button.dataset.verify)await saveEdit(button.dataset.verify,button.dataset.ref,{value:button.dataset.value||null,verified:button.dataset.verified==='1'});
    if(button.dataset.resetEdit){const [kind,ref]=button.dataset.resetEdit.split(':');await saveEdit(kind,ref,{value:null,verified:button.form.verified.checked});}
    if(button.dataset.review)await saveEdit('study','study',{verified:button.dataset.review==='1'});
    if(button.dataset.mode){if(state.mode==='real')state.realPatientId=state.patient.id;await setMode(button.dataset.mode);}
    if(button.dataset.source)await showSource(button.dataset.source,button.dataset.quote||'',button.dataset.sourcePatient||state.patient.id);
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

document.addEventListener('submit',async event=>{
  const form=event.target.closest('[data-edit-form]');if(!form)return;
  event.preventDefault();
  const [kind,ref]=form.dataset.editForm.split(':');
  try{await saveEdit(kind,ref,{value:form.value.value,verified:form.verified.checked});}catch(error){toast(error.message);}
});

document.addEventListener('change',async event=>{
  try{
    if(event.target.id==='criterion-filter'){state.filter=event.target.value;render();}
  }catch(error){toast(error.message);}
});

document.addEventListener('input',event=>{
  if(event.target.id==='overview-search'){const cursor=event.target.selectionStart;state.overviewQuery=event.target.value;state.overviewLimit=40;render();const field=document.querySelector('#overview-search');field.focus();field.setSelectionRange(cursor,cursor);}
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