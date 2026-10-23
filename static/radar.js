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
  function matches(o) {
    const action = actionsFor(o);
    const localMatch = state.local === 'all' || (state.local === 'visible' ? !action.hidden : state.local === 'hidden' ? !!action.hidden : !action.hidden && !!action[state.local]);
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
    const compensation = [...new Set([o.compensation,o.compensation_usd,o.compensation_inr].map(text).filter(Boolean))];
    if (compensation.length) card.append(node('p',compensation.join(' · '),'facts'));
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
    const filtered = items.filter(matches);
    for (const [q,label] of Object.entries(queues)) { const b = $(`queue-${q}`); b.textContent = `${label} ${q === 'all' ? filtered.length : filtered.filter(o => queueOf(o) === q).length}`; b.setAttribute('aria-pressed',String(q === state.queue)); }
    const visible = filtered.filter(o => state.queue === 'all' || queueOf(o) === state.queue);
    visible.sort((a,b) => {
      if (state.sort === 'deadline') return (timestamp(a.deadline_at) ?? Infinity) - (timestamp(b.deadline_at) ?? Infinity) || text(a.title).localeCompare(text(b.title));
      if (state.sort === 'newest') return (timestamp(b.last_seen_at) ?? 0) - (timestamp(a.last_seen_at) ?? 0) || text(a.title).localeCompare(text(b.title));
      return (Number(b.fit_score) || 0) - (Number(a.fit_score) || 0) || (timestamp(a.deadline_at) ?? Infinity) - (timestamp(b.deadline_at) ?? Infinity) || text(a.title).localeCompare(text(b.title));
    });
    $('results').replaceChildren(...visible.map(makeCard)); $('empty').hidden = visible.length > 0;
    $('result-count').textContent = `${visible.length} ${visible.length === 1 ? 'opportunity' : 'opportunities'} · ${queues[state.queue]}`;
    $('active-filters').textContent = ['search','track','eligibility','availability','local'].filter(k => state[k] !== defaults[k]).map(k => k === 'search' ? `Search: ${state[k]}` : $(k).selectedOptions[0].textContent).join(' · ');
  }
  for (const [q,label] of Object.entries(queues)) { const b = node('button',label); b.id = `queue-${q}`; b.type = 'button'; b.addEventListener('click',() => { state.queue = q; updateURL(); render(); }); $('queues').append(b); }
  for (const name of Object.keys(defaults).filter(n => n !== 'queue')) $(name).addEventListener(name === 'search' ? 'input' : 'change',() => { state[name] = $(name).value; updateURL(); render(); });
  $('clear').addEventListener('click',() => { Object.assign(state,defaults,{queue:'all'}); for (const name of Object.keys(defaults).filter(n => n !== 'queue')) $(name).value = state[name]; updateURL(); render(); });
  $('result-count').tabIndex = -1;
  window.addEventListener('popstate',() => { restoreURL(); render(); });
  const successes = health.map(s => timestamp(s.last_success)).filter(n => n !== null);
  const latest = successes.length ? Math.max(...successes) : null;
  $('health-summary').textContent = `Source health · ${latest === null ? 'no successful scan recorded' : `last success ${date(new Date(latest).toISOString())}`} · ${health.length} sources`;
  if (!health.length) $('health-list').append(node('p','No source telemetry is recorded. Scanner activity is not confirmed.'));
  for (const s of health) { const row = node('div','','health-row'); row.append(node('strong',`${text(s.source) || 'Unnamed source'} · ${text(s.status) || 'unknown'}`),node('p',`Count: ${Number.isFinite(s.count) ? s.count : 'not recorded'} · Attempt: ${date(s.last_attempt)} · Success: ${date(s.last_success)}`)); if (text(s.error)) row.append(node('p',`Error: ${text(s.error)}`)); $('health-list').append(row); }
  const scan = $('scan');
  if (scan) scan.addEventListener('click',async () => { scan.disabled = true; $('scan-message').textContent = 'Requesting scan…'; const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(),120000); try { const response = await fetch('/api/scan',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{Accept:'application/json'}}); if (!response.ok) throw new Error(`Scan request failed (${response.status}).`); const data = await response.json(); $('scan-message').textContent = text(data.message) || 'Scan request accepted. Reload to inspect updated source health.'; } catch (err) { $('scan-message').textContent = err.name === 'AbortError' ? 'Scan request timed out. Check source health before retrying.' : text(err.message) || 'Scan could not be requested.'; } finally { clearTimeout(timeout); scan.disabled = false; } });
  restoreURL(); render();
})();
