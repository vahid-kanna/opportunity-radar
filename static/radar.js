'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const text = value => typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  const node = (tag, value, cls) => { const el = document.createElement(tag); el.textContent = text(value); if (cls) el.className = cls; return el; };
  const read = id => { try { const data = JSON.parse($(id).textContent); return Array.isArray(data) ? data.filter(x => x && typeof x === 'object') : []; } catch { return []; } };
  const items = read('opportunities-data');
  const health = read('health-data');
  const queues = {best:'Best matches',review:'Needs verification',watch:'Watchlist',resources:'Resources',explore:'Explore',all:'All'};
  const defaultQueue = items.some(o => o.queue === 'best') ? 'best' : 'review';
  const defaults = {queue:defaultQueue,search:'',sort:'fit',track:'all',eligibility:'all',availability:'all',local:'visible'};
  const state = {...defaults};
  let local = Object.create(null);
  const storageKey = 'opportunity-radar.device-actions.v1';
  const storageWarning = () => { $('storage-note').textContent = 'Browser storage is unavailable. Actions work for this page only; they are not saved or sent to an account.'; };
  try { const saved = JSON.parse(localStorage.getItem(storageKey) || '{}'); if (saved && typeof saved === 'object' && !Array.isArray(saved)) local = Object.assign(Object.create(null),saved); } catch { storageWarning(); }
  const key = o => text(o.id) || text(o.source_url) || JSON.stringify([o.title,o.organization]);
  const actionsFor = o => { const value = local[key(o)]; return value && typeof value === 'object' ? value : {}; };
  const timestamp = value => { if (!value) return null; const n = Date.parse(value); return Number.isFinite(n) ? n : null; };
  const date = value => { const n = timestamp(value); return n === null ? 'Not recorded' : new Date(n).toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'}); };
  const safeURL = value => { try { const url = new URL(text(value)); return ['https:','http:'].includes(url.protocol) && !url.username && !url.password ? url : null; } catch { return null; } };
  const link = (value,label,cls) => { const url = safeURL(value); if (!url) return null; const a = node('a',label,cls); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; };
  const queueOf = o => Object.hasOwn(queues,o.queue) && o.queue !== 'all' ? o.queue : 'review';
  const isFunding = o => o.track_group === 'funding' || ['grant','pilot','accelerator','funding','investment'].includes(text(o.kind).toLowerCase());
  const pay = o => [...new Set([o.compensation,o.compensation_usd,o.compensation_inr].map(text).filter(v => v.trim()))].join(' · ') || 'Not disclosed';
  let visibleItems = [];
  function matches(o, includeHidden = false) {
    const action = actionsFor(o);
    const localMatch = state.local === 'all' || (state.local === 'visible' ? includeHidden || !action.hidden : state.local === 'hidden' ? !!action.hidden : !action.hidden && !!action[state.local]);
    const haystack = ['title','organization','track','track_group','kind','location','fit_reason','source'].map(k => text(o[k])).join(' ').toLowerCase();
    return localMatch && (!state.search || haystack.includes(state.search.trim().toLowerCase())) &&
      (state.track === 'all' || (state.track === 'funding' ? isFunding(o) : o.track_group === state.track)) &&
      (state.eligibility === 'all' || (o.eligibility_state || 'unknown') === state.eligibility) &&
      (state.availability === 'all' || (o.availability_state || 'unknown') === state.availability);
  }
  function restoreURL() {
    const params = new URLSearchParams(location.search);
    for (const name of Object.keys(defaults)) {
      const value = params.get(name);
      if (name === 'queue') state.queue = Object.hasOwn(queues,value) ? value : defaultQueue;
      else if (name === 'search') state.search = value || '';
      else state[name] = value && Array.from($(name).options).some(o => o.value === value) ? value : defaults[name];
    }
    for (const name of Object.keys(defaults).filter(n => n !== 'queue')) $(name).value = state[name];
  }
  function updateURL() {
    const url = new URL(location.href);
    for (const name of Object.keys(defaults)) { if (state[name] === defaults[name]) url.searchParams.delete(name); else url.searchParams.set(name,state[name]); }
    // Keep the queue explicit so a shared URL does not change when data changes.
    url.searchParams.set('queue',state.queue);
    history.replaceState(null,'',url);
  }
  function makeCard(o,index) {
    const card = node('article','','opp-card'); card.dataset.id = key(o);
    const heading = node('h2',text(o.title) || 'Untitled opportunity'); heading.id = `opportunity-${index}`; card.setAttribute('aria-labelledby',heading.id); card.append(heading);
    card.append(node('p',[text(o.organization) || 'Organization not recorded',text(o.location),text(o.track) || text(o.track_group),text(o.kind)].filter(Boolean).join(' · '),'org'));
    const badges = node('div','','badges');
    const eligibility = text(o.eligibility_state) || 'unknown'; const availability = text(o.availability_state) || 'unknown'; const verification = text(o.verification_state) || 'unverified';
    for (const [label,value,good] of [['Eligibility',eligibility,eligibility === 'eligible'],['Availability',availability,availability === 'open'],['Evidence',verification,verification === 'verified']]) badges.append(node('span',`${label}: ${value}`,`badge ${good ? 'badge-good' : 'badge-caution'}`));
    card.append(badges,node('p',text(o.fit_reason) || 'Fit is not established. Check the eligibility and source evidence.','reason'));
    card.append(node('p',`Pay / funding: ${pay(o)}`,'facts'));
    card.append(node('p',timestamp(o.deadline_at) !== null ? `Deadline: ${date(o.deadline_at)}` : 'Deadline not confirmed','facts'));
    const actions = node('div','','actions');
    const destination = safeURL(o.apply_url) || safeURL(o.source_url);
    const primary = destination ? link(destination.href,text(o.action_label) || 'View source','primary') : null;
    if (primary) actions.append(primary);
    for (const [field,label,activeLabel] of [['saved','Save','Saved'],['applied','Applied','Applied'],['hidden','Hide','Unhide']]) {
      const active = !!actionsFor(o)[field]; const button = node('button',active ? activeLabel : label); button.type = 'button'; button.setAttribute('aria-pressed',String(active)); button.setAttribute('aria-label',`${active ? activeLabel : label}: ${text(o.title)} (device-only)`); button.title = 'Device-only · not synced or submitted';
      button.addEventListener('click',() => { local[key(o)] = {...actionsFor(o),[field]:!active}; try { localStorage.setItem(storageKey,JSON.stringify(local)); } catch { storageWarning(); } render(); const replacement = Array.from($('results').querySelectorAll('article')).find(c => c.dataset.id === key(o)); if (replacement) replacement.querySelectorAll('button')[['saved','applied','hidden'].indexOf(field)].focus(); else $('result-count').focus(); });
      actions.append(button);
    }
    card.append(actions,node('p',destination ? `Destination: ${destination.hostname} · opens in a new tab` : 'No safe destination recorded','destination'));
    const details = node('details','','evidence'); const evidence = Array.isArray(o.evidence) ? o.evidence.filter(e => e && typeof e === 'object') : [];
    details.append(node('summary',`Evidence & details (${evidence.length})`));
    details.append(node('p',`Last seen: ${date(o.last_seen_at)} · Last verified: ${date(o.last_verified_at)}`));
    if (text(o.eligibility)) details.append(node('p',`Eligibility notes: ${text(o.eligibility)}`));
    if (text(o.experience)) details.append(node('p',`Experience: ${text(o.experience)}`));
    if (!evidence.length) details.append(node('p','No field-level source evidence is recorded. A listing alone does not confirm eligibility or an active opening.'));
    for (const e of evidence) { const block = node('blockquote'); block.append(node('strong',text(e.field) || 'Source excerpt'),node('p',text(e.quote) || 'No excerpt recorded')); const source = link(e.url,'Read evidence source'); if (source) block.append(source); details.append(block); }
    const source = link(o.source_url,'Original discovery source'); if (source) details.append(source);
    card.append(details); return card;
  }
  function render() {
    const filtered = items.filter(o => matches(o));
    for (const [q,label] of Object.entries(queues)) { const b = $(`queue-${q}`); b.textContent = `${label} ${q === 'all' ? filtered.length : filtered.filter(o => queueOf(o) === q).length}`; b.setAttribute('aria-pressed',String(q === state.queue)); }
    const visible = filtered.filter(o => state.queue === 'all' || queueOf(o) === state.queue);
    visible.sort((a,b) => {
      if (state.sort === 'deadline') return (timestamp(a.deadline_at) ?? Infinity) - (timestamp(b.deadline_at) ?? Infinity) || text(a.title).localeCompare(text(b.title));
      if (state.sort === 'newest') return (timestamp(b.last_seen_at) ?? 0) - (timestamp(a.last_seen_at) ?? 0) || text(a.title).localeCompare(text(b.title));
      return (Number(b.fit_score) || 0) - (Number(a.fit_score) || 0) || (timestamp(a.deadline_at) ?? Infinity) - (timestamp(b.deadline_at) ?? Infinity) || text(a.title).localeCompare(text(b.title));
    });
    visibleItems = visible;
    $('download-csv').disabled = visible.length === 0;
    $('results').replaceChildren(...visible.map(makeCard)); $('empty').hidden = visible.length > 0;
    const inQueue = o => state.queue === 'all' || queueOf(o) === state.queue;
    const hiddenMatches = state.local === 'visible' ? items.filter(o => inQueue(o) && actionsFor(o).hidden && matches(o,true)).length : 0;
    const hasBest = items.some(o => queueOf(o) === 'best');
    $('empty').textContent = !items.length ? 'No opportunities are loaded. Source health below the page title can show whether discovery has run.' :
      hiddenMatches ? `${hiddenMatches} matching ${hiddenMatches === 1 ? 'opportunity is' : 'opportunities are'} hidden on this device. Choose Include hidden in Filters to restore them.` :
      state.queue === 'best' && !hasBest ? 'No verified best matches yet. Needs verification contains leads that still need eligibility, availability or evidence checks; it does not mean there are no leads.' :
      'No opportunities match these filters. Try All or clear filters.';
    $('result-count').textContent = `${visible.length} ${visible.length === 1 ? 'opportunity' : 'opportunities'} · ${queues[state.queue]}`;
    $('active-filters').textContent = ['search','track','eligibility','availability','local'].filter(k => state[k] !== defaults[k]).map(k => k === 'search' ? `Search: ${state[k]}` : $(k).selectedOptions[0].textContent).join(' · ');
  }
  for (const [q,label] of Object.entries(queues)) { const b = node('button',label); b.id = `queue-${q}`; b.type = 'button'; b.addEventListener('click',() => { state.queue = q; updateURL(); render(); }); $('queues').append(b); }
  for (const name of Object.keys(defaults).filter(n => n !== 'queue')) $(name).addEventListener(name === 'search' ? 'input' : 'change',() => { state[name] = $(name).value; updateURL(); render(); });
  $('clear').addEventListener('click',() => { Object.assign(state,defaults,{queue:'all'}); for (const name of Object.keys(defaults).filter(n => n !== 'queue')) $(name).value = state[name]; updateURL(); render(); });
  $('result-count').tabIndex = -1;
  // Export only fields displayed by cards, never raw payloads, evidence or device actions.
  const csvCell = value => {
    let s = text(value);
    if (/^[\s\uFEFF]*[=+\-@]/.test(s) || /^[\t\r\n]/.test(s)) s = `'${s}`;
    return `"${s.replaceAll('"','""')}"`;
  };
  $('download-csv').addEventListener('click',() => {
    const rows = [['Title','Organization','Location','Track','Kind','Queue','Eligibility','Availability','Evidence status','Pay / funding','Deadline','Fit reason','Destination']];
    for (const o of visibleItems) rows.push([text(o.title) || 'Untitled opportunity',text(o.organization) || 'Organization not recorded',o.location,text(o.track) || text(o.track_group),o.kind,queues[queueOf(o)],text(o.eligibility_state) || 'unknown',text(o.availability_state) || 'unknown',text(o.verification_state) || 'unverified',pay(o),timestamp(o.deadline_at) !== null ? date(o.deadline_at) : 'Deadline not confirmed',text(o.fit_reason) || 'Fit is not established. Check the eligibility and source evidence.',(safeURL(o.apply_url) || safeURL(o.source_url))?.href || '']);
    const blob = new Blob(['\uFEFF',rows.map(row => row.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob); const a = node('a'); a.href = url; a.download = 'opportunity-radar-visible.csv'; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    $('share-message').textContent = `Downloaded ${visibleItems.length} visible results. Evidence and device actions are not included.`;
  });
  $('copy-link').addEventListener('click',async () => {
    updateURL(); const url = location.href;
    $('share-fallback').hidden = true;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(url);
      $('share-message').textContent = 'Filtered link copied. Device-only actions are not shared.';
    } catch {
      $('share-fallback').hidden = false; $('share-url').value = url; $('share-url').focus(); $('share-url').select();
      $('share-message').textContent = 'Clipboard unavailable. Copy the selected link below. Device-only actions are not shared.';
    }
  });
  window.addEventListener('popstate',() => { restoreURL(); render(); });
  const successes = health.map(s => timestamp(s.last_success)).filter(n => n !== null);
  const latest = successes.length ? Math.max(...successes) : null;
  const now = Date.now(); const staleAfter = 24 * 60 * 60 * 1000;
  const age = value => { const at = timestamp(value); if (at === null) return 'not recorded'; if (at > now + 60000) return 'future timestamp; check clock'; const minutes = Math.max(0,Math.floor((now-at)/60000)); return minutes < 60 ? `${minutes}m ago` : minutes < 1440 ? `${Math.floor(minutes/60)}h ago` : `${Math.floor(minutes/1440)}d ago`; };
  const sourceState = s => {
    const success = timestamp(s.last_success); const status = text(s.status).toLowerCase();
    const failed = !!text(s.error) || !['success','ok','not_run','unknown',''].includes(status);
    const stale = success !== null && now - success >= staleAfter;
    const fresh = !failed && ['success','ok'].includes(status) && success !== null && now-success >= -60000 && !stale;
    return {failed,stale,fresh,missing:success === null};
  };
  const states = health.map(sourceState); const count = field => states.filter(s => s[field]).length;
  $('health-summary').textContent = `Source health · ${count('fresh')}/${health.length} fresh · ${count('failed')} failed · ${count('stale')} stale${count('missing') ? ` · ${count('missing')} never succeeded` : ''} · last success ${latest === null ? 'not recorded' : age(new Date(latest).toISOString())}`;
  if (health.length) $('health-list').append(node('p','Fresh = successful last attempt and last success under 24h old. Stale = last success at least 24h ago. A failed attempt can also be stale. Ages are evaluated when the page loads.','health-note'));
  if (!health.length) $('health-list').append(node('p','No source telemetry is recorded. Scanner activity is not confirmed.'));
  for (const s of health) { const status = sourceState(s); const row = node('div','',`health-row ${status.fresh ? 'health-good' : 'health-warning'}`); row.append(node('strong',`${text(s.source) || 'Unnamed source'} · ${text(s.status) || 'unknown'} · ${status.failed ? 'failed attempt' : status.fresh ? 'fresh' : 'not confirmed fresh'}${status.stale ? ' · stale' : ''}${status.missing ? ' · never succeeded' : ''}`),node('p',`Count: ${Number.isFinite(s.count) ? s.count : 'not recorded'} · Attempt: ${date(s.last_attempt)} (${age(s.last_attempt)}) · Success: ${date(s.last_success)} (${age(s.last_success)})`)); if (text(s.error)) row.append(node('p',`Error: ${text(s.error)}`)); $('health-list').append(row); }
  const scan = $('scan');
  if (scan) scan.addEventListener('click',async () => { scan.disabled = true; $('scan-message').textContent = 'Requesting scan…'; const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(),120000); try { const response = await fetch('/api/scan',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{Accept:'application/json'}}); if (!response.ok) throw new Error(`Scan request failed (${response.status}).`); const data = await response.json(); $('scan-message').textContent = text(data.message) || 'Scan request accepted. Reload to inspect updated source health.'; } catch (err) { $('scan-message').textContent = err.name === 'AbortError' ? 'Scan request timed out. Check source health before retrying.' : text(err.message) || 'Scan could not be requested.'; } finally { clearTimeout(timeout); scan.disabled = false; } });
  restoreURL(); render();
})();
