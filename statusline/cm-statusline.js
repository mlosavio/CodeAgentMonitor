#!/usr/bin/env node
/**
 * cm-statusline.js — segmento di claude-code-monitor per la statusline di Claude Code.
 *
 * Mostra costo, tempo attivo, messaggi e consumo dei limiti della sessione corrente:
 *
 *     $3.42 · 18m · 26 · 5h 31%
 *
 * Se avevi già una statusline, la si può avvolgere invece di sostituirla:
 * `statusline.wrap_command` in config.json (o la variabile CM_SL_WRAP) indica un
 * comando da eseguire, di cui si cattura l'output e a cui si appende il segmento.
 * Quel comando non viene mai modificato: gira come processo figlio.
 *
 *     mia-statusline │ $3.42 · 18m · 26 · 5h 31%
 *
 * Perché Node e non Python: su questa macchina l'avvio di python costa 205-239 ms
 * (node 80-91). La statusline viene ridisegnata di continuo e ogni render è un
 * processo nuovo, quindi Python sfonderebbe il budget prima ancora di lavorare.
 * I prezzi restano single-source: si legge lo stesso pricing.json del CLI, si
 * duplica solo la formula — e `--selftest` la confronta col CLI.
 *
 * Stato incrementale in ~/.claude/cache/cm-statusline/<sessionId>.{sum,keys}.json
 * così ogni render legge solo i byte aggiunti al transcript.
 *
 * Qualunque errore => stampa l'output avvolto invariato ed esce 0: la statusline
 * non deve mai rompersi né rallentare Claude Code.
 *
 * Variabili d'ambiente (tutte facoltative):
 *   CM_CONFIG           percorso esatto di config.json
 *   CM_PROJECT_DIR      cartella del progetto (ci cerca config.json dentro)
 *   CM_SL_DISABLE=1     disattiva il segmento (resta solo la statusline avvolta)
 *   CM_SL_WRAP          comando da avvolgere, JSON o separato da spazi
 *   CM_SL_BUDGET_MS     budget di calcolo per render (default 150)
 *   CM_SL_MAX_BYTES     arretrato massimo letto per render (default 8388608)
 *   CM_SL_IDLE_GAP      soglia di inattività in secondi (default 300)
 */

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const HOME = os.homedir();
const CLAUDE_DIR = path.join(HOME, '.claude');
const CACHE_DIR = path.join(CLAUDE_DIR, 'cache', 'cm-statusline');
/**
 * Dove sta config.json, in ordine:
 *   1. CM_CONFIG            percorso esatto del file
 *   2. CM_PROJECT_DIR       cartella del progetto
 *   3. ~/.claude/cm-statusline.json  ({"project_dir": "..."}), scritto da install.py
 * Se non si trova, il segmento non viene mostrato ma la statusline continua a
 * funzionare: mai rompere quello che l'utente aveva già.
 */
function resolveConfigPath() {
  if (process.env.CM_CONFIG) return process.env.CM_CONFIG;
  if (process.env.CM_PROJECT_DIR) return path.join(process.env.CM_PROJECT_DIR, 'config.json');
  try {
    const link = JSON.parse(
      fs.readFileSync(path.join(CLAUDE_DIR, 'cm-statusline.json'), 'utf8'));
    if (link && link.project_dir) return path.join(link.project_dir, 'config.json');
    if (link && link.config) return link.config;
  } catch (e) { /* non installato: nessun segmento */ }
  return null;
}

const PRICING_PATH = resolveConfigPath();

const IDLE_GAP = Number(process.env.CM_SL_IDLE_GAP || 300);

const STATE_VERSION = 1;
const SEP = '\x1b[2m │ \x1b[0m';

const FIELDS = ['input', 'output', 'cache_read', 'cache_w5m', 'cache_w1h', 'web_search', 'web_fetch'];

// --------------------------------------------------------------------------- //
// Listino e formula di costo (port di claude_monitor.py)
// --------------------------------------------------------------------------- //

const DEFAULT_PRICING = {
  cache_multipliers: { read: 0.10, write_5m: 1.25, write_1h: 2.00 },
  server_tools: { web_search_request: 0.01, web_fetch_request: 0.0 },
  models: {}, aliases: {}, free_models: ['<synthetic>'],
};

function loadPricing() {
  for (const file of (PRICING_PATH ? [PRICING_PATH] : [])) {
    try {
      const p = JSON.parse(fs.readFileSync(file, 'utf8'));
      for (const k of Object.keys(DEFAULT_PRICING)) if (p[k] === undefined) p[k] = DEFAULT_PRICING[k];
      const st = fs.statSync(file);
      p._sig = `${st.size}-${Math.round(st.mtimeMs)}`;
      return p;
    } catch (e) { /* provo il successivo */ }
  }
  return Object.assign({ _sig: 'none' }, DEFAULT_PRICING);
}

/** Opzioni del segmento, con i default se il file non le contiene. */
function slOptions(pricing) {
  const d = {
    enabled: true, wrap_command: null,
    show_cost: true, show_active_time: true, show_messages: true,
    show_limits: true, separator: ' · ', limit_warn_pct: 75, limit_critical_pct: 90,
    budget_ms: 150, max_bytes_per_render: 8 * 1024 * 1024, wrap_timeout_ms: 1500,
  };
  Object.assign(d, (pricing && pricing.statusline) || {});
  // le variabili d'ambiente restano l'ultima parola, per poter provare al volo
  if (process.env.CM_SL_BUDGET_MS) d.budget_ms = Number(process.env.CM_SL_BUDGET_MS);
  if (process.env.CM_SL_MAX_BYTES) d.max_bytes_per_render = Number(process.env.CM_SL_MAX_BYTES);
  return d;
}

const DATE_SUFFIX = /-\d{8}$/;

function normalizeModel(model, pricing) {
  if (!model) return '';
  let m = String(model).trim();
  const alias = pricing.aliases || {};
  if (alias[m]) return alias[m];
  m = m.replace('[1m]', '');
  if ((pricing.models || {})[m]) return m;
  const stripped = m.replace(DATE_SUFFIX, '');
  return alias[stripped] || stripped;
}

function newTok() { return [0, 0, 0, 0, 0, 0, 0]; }

/** usage grezza -> array allineato a FIELDS */
function extractUsage(u) {
  const cc = u.cache_creation || {};
  let w5 = cc.ephemeral_5m_input_tokens || 0;
  let w1 = cc.ephemeral_1h_input_tokens || 0;
  const totalW = u.cache_creation_input_tokens || 0;
  if (w5 + w1 === 0 && totalW) w5 = totalW;  // dettaglio TTL assente
  const stu = u.server_tool_use || {};
  return [
    u.input_tokens || 0,
    u.output_tokens || 0,
    u.cache_read_input_tokens || 0,
    w5, w1,
    stu.web_search_requests || 0,
    stu.web_fetch_requests || 0,
  ];
}

function costOf(model, tok, pricing) {
  if ((pricing.free_models || []).indexOf(model) !== -1) return 0;
  const st = pricing.server_tools || {};
  const web = tok[5] * (st.web_search_request || 0) + tok[6] * (st.web_fetch_request || 0);
  const price = (pricing.models || {})[model];
  if (!price) return web;
  const cmul = pricing.cache_multipliers || {};
  const pin = price.in / 1e6, pout = price.out / 1e6;
  return tok[0] * pin
       + tok[1] * pout
       + tok[2] * pin * (cmul.read === undefined ? 0.10 : cmul.read)
       + tok[3] * pin * (cmul.write_5m === undefined ? 1.25 : cmul.write_5m)
       + tok[4] * pin * (cmul.write_1h === undefined ? 2.00 : cmul.write_1h)
       + web;
}

// --------------------------------------------------------------------------- //
// Riconoscimento dei turni utente (port di is_human_prompt)
// --------------------------------------------------------------------------- //

const SYNTHETIC_PREFIXES = [
  '[request interrupted',
  '<local-command-stdout>',
  '<local-command-stderr>',
  'api error:',
  'caveat: the messages below were generated by the user while running local commands',
];

function isHumanPrompt(row) {
  if (row.type !== 'user' || row.isMeta) return false;
  const content = (row.message || {}).content;
  let text;
  if (typeof content === 'string') {
    text = content;
  } else if (Array.isArray(content)) {
    let hasText = false, hasImage = false, hasResult = false;
    for (const b of content) {
      if (!b || typeof b !== 'object') continue;
      if (b.type === 'tool_result') hasResult = true;
      else if (b.type === 'text') hasText = true;
      else if (b.type === 'image') hasImage = true;
    }
    if (hasResult || (!hasText && !hasImage)) return false;
    if (hasImage) return true;
    text = content.filter(b => b && b.type === 'text').map(b => b.text || '').join(' ');
  } else {
    return false;
  }
  const s = text.trim();
  if (!s) return false;
  const low = s.toLowerCase();
  return !SYNTHETIC_PREFIXES.some(p => low.startsWith(p));
}

// --------------------------------------------------------------------------- //
// Tempo attivo: unione di cluster.
// Equivale esattamente alla somma dei gap ordinati <= idle di finalize(), ma è
// aggiornabile in ordine qualsiasi e richiede poche centinaia di voci invece di
// tutti i timestamp. Serve perché i transcript riemettono timestamp vecchi.
// --------------------------------------------------------------------------- //

function addCluster(clusters, ts, idle) {
  let lo = ts, hi = ts;
  const keep = [];
  for (let i = 0; i < clusters.length; i++) {
    const c = clusters[i];
    if (c[0] - idle <= ts && c[1] + idle >= ts) {
      if (c[0] < lo) lo = c[0];
      if (c[1] > hi) hi = c[1];
    } else {
      keep.push(c);
    }
  }
  keep.push([lo, hi]);
  keep.sort((a, b) => a[0] - b[0]);
  return keep;
}

function activeSeconds(clusters) {
  let total = 0;
  for (const c of clusters) total += c[1] - c[0];
  return total;
}

// --------------------------------------------------------------------------- //
// Formattatori (port di h_cost / h_dur)
// --------------------------------------------------------------------------- //

function hCost(v) {
  if (v >= 100) return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  if (v >= 1) return '$' + v.toFixed(2);
  if (v > 0) return '$' + v.toFixed(4);
  return '$0';
}

function hDur(sec) {
  const s = Math.floor(sec);
  if (s >= 3600) return Math.floor(s / 3600) + 'h' + String(Math.floor((s % 3600) / 60)).padStart(2, '0') + 'm';
  if (s >= 60) return Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
  return s + 's';
}

// --------------------------------------------------------------------------- //
// File di una sessione
// --------------------------------------------------------------------------- //

function walkJsonl(dir, out) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return out; }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walkJsonl(full, out);
    else if (e.isFile() && e.name.endsWith('.jsonl')) out.push(full);
  }
  return out;
}

/** transcript principale + subagent (ricorsivo: i workflow annidano ancora) */
function sessionFiles(transcriptPath) {
  const out = [];
  try { if (fs.statSync(transcriptPath).isFile()) out.push(transcriptPath); } catch (e) { /* ignora */ }
  const sub = path.join(transcriptPath.replace(/\.jsonl$/i, ''), 'subagents');
  walkJsonl(sub, out);
  return out;
}

function findTranscript(sessionId) {
  const base = path.join(CLAUDE_DIR, 'projects');
  let dirs;
  try { dirs = fs.readdirSync(base, { withFileTypes: true }); } catch (e) { return null; }
  let exact = null, prefix = null;
  for (const d of dirs) {
    if (!d.isDirectory()) continue;
    let files;
    try { files = fs.readdirSync(path.join(base, d.name)); } catch (e) { continue; }
    for (const f of files) {
      if (!f.endsWith('.jsonl')) continue;
      const stem = f.slice(0, -6);
      if (stem === sessionId) exact = path.join(base, d.name, f);
      else if (stem.startsWith(sessionId) && !prefix) prefix = path.join(base, d.name, f);
    }
  }
  return exact || prefix;
}

// --------------------------------------------------------------------------- //
// Stato su disco
// --------------------------------------------------------------------------- //

function hashStr(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

function posSig(files) {
  const parts = Object.keys(files).sort().map(k => k + ':' + files[k].pos);
  return hashStr(parts.join('|'));
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return null; }
}

function writeJsonAtomic(file, obj) {
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(obj));
  fs.renameSync(tmp, file);
}

function emptyState(sessionId, pricingSig) {
  return {
    v: STATE_VERSION,
    session_id: sessionId,
    pricing_sig: pricingSig,
    pos_sig: '',
    files: {},
    models: {},          // modello -> array FIELDS
    counts: { user: 0, assistant: 0, bad_lines: 0 },
    clusters: [],
    first_ts: null,
    last_ts: null,
    backlog: 0,
    render: null,
    updated_at: 0,
  };
}

// --------------------------------------------------------------------------- //
// Motore incrementale
// --------------------------------------------------------------------------- //

/**
 * Ingerisce le righe nuove dei file di sessione.
 * `keys` è la mappa di dedup completa: chiave -> [FIELDS..., modelIdx].
 * I totali in state.models si aggiornano per delta, quindi il caso normale
 * non itera mai tutte le chiavi.
 */
function ingest(state, keys, files, pricing, opts) {
  const deadline = opts.deadline;
  const maxBytes = opts.maxBytes;
  let budgetLeft = maxBytes;
  let backlog = 0;
  let changed = false;

  const stats = [];
  for (const f of files) {
    let st;
    try { st = fs.statSync(f); } catch (e) { continue; }
    stats.push([f, st]);
  }

  // rilevamento di riscritture in place (fork / compattazione): stato non valido
  for (const [f, st] of stats) {
    const prev = state.files[f];
    if (!prev) continue;
    if (st.size < prev.pos || (st.size === prev.size && Math.round(st.mtimeMs) !== prev.mtime)) {
      return { reset: true, changed: false, backlog: 0 };
    }
  }

  for (const [f, st] of stats) {
    const prev = state.files[f] || { pos: 0, size: 0, mtime: 0 };
    if (st.size <= prev.pos) {
      state.files[f] = { pos: prev.pos, size: st.size, mtime: Math.round(st.mtimeMs) };
      continue;
    }
    if (deadline && Date.now() > deadline) { backlog += st.size - prev.pos; continue; }

    let want = st.size - prev.pos;
    if (want > budgetLeft) { backlog += want - budgetLeft; want = budgetLeft; }
    if (want <= 0) { backlog += st.size - prev.pos; continue; }

    let fd;
    try { fd = fs.openSync(f, 'r'); } catch (e) { continue; }
    const buf = Buffer.allocUnsafe(want);
    let read = 0;
    try { read = fs.readSync(fd, buf, 0, want, prev.pos); } finally { fs.closeSync(fd); }
    if (read <= 0) continue;

    const cut = buf.lastIndexOf(0x0a, read - 1);
    if (cut === -1) { backlog += read; continue; }  // nessuna riga completa
    const text = buf.toString('utf8', 0, cut + 1);
    budgetLeft -= (cut + 1);
    changed = true;

    let lineNo = 0;
    for (const line of text.split('\n')) {
      if (!line) continue;
      if ((++lineNo & 2047) === 0 && deadline && Date.now() > deadline) {
        // budget esaurito a metà blocco: fermo qui, la posizione resta coerente
        break;
      }
      ingestLine(state, keys, line, pricing);
    }
    state.files[f] = { pos: prev.pos + cut + 1, size: st.size, mtime: Math.round(st.mtimeMs) };
    if (state.files[f].pos < st.size) backlog += st.size - state.files[f].pos;
  }
  return { reset: false, changed, backlog };
}

function ingestLine(state, keys, line, pricing) {
  let row;
  try { row = JSON.parse(line); } catch (e) { state.counts.bad_lines++; return; }
  if (!row || typeof row !== 'object') return;

  const type = row.type;
  if (type === 'ai-title') return;

  if (row.timestamp) {
    const ts = Date.parse(row.timestamp) / 1000;
    if (!Number.isNaN(ts)) {
      if (state.first_ts === null || ts < state.first_ts) state.first_ts = ts;
      if (state.last_ts === null || ts > state.last_ts) state.last_ts = ts;
      state.clusters = addCluster(state.clusters, ts, IDLE_GAP);
    }
  }

  if (type === 'user') {
    if (!row.isSidechain && isHumanPrompt(row)) state.counts.user++;
    return;
  }
  if (type !== 'assistant') return;

  const msg = row.message || {};
  const usage = msg.usage;
  if (!usage || typeof usage !== 'object') return;

  const model = normalizeModel(msg.model || '', pricing);
  const key = (row.requestId || '') + '|' + (msg.id || row.uuid || '');
  const tok = extractUsage(usage);

  let bucket = state.models[model];
  if (!bucket) bucket = state.models[model] = newTok();

  const prev = keys[key];
  if (!prev) {
    keys[key] = tok.slice();
    for (let i = 0; i < 7; i++) bucket[i] += tok[i];
    state.counts.assistant++;
  } else {
    // stessa richiesta riemessa durante lo streaming: tengo il massimo per campo
    for (let i = 0; i < 7; i++) {
      if (tok[i] > prev[i]) { bucket[i] += tok[i] - prev[i]; prev[i] = tok[i]; }
    }
  }
}

function totalsOf(state, pricing) {
  let cost = 0;
  for (const model of Object.keys(state.models)) cost += costOf(model, state.models[model], pricing);
  const duration = (state.first_ts !== null && state.last_ts !== null)
    ? state.last_ts - state.first_ts : 0;
  return { cost, duration, active: activeSeconds(state.clusters) };
}

// --------------------------------------------------------------------------- //
// Calcolo del segmento
// --------------------------------------------------------------------------- //

function computeSegment(payload, opts) {
  const sessionId = payload.session_id || payload.sessionId;
  if (!sessionId) return null;
  let transcript = payload.transcript_path || payload.transcriptPath;
  if (!transcript || !fs.existsSync(transcript)) transcript = findTranscript(sessionId);
  if (!transcript) return null;

  const pricing = opts.pricing;
  const sumFile = path.join(CACHE_DIR, sessionId + '.sum.json');
  const keysFile = path.join(CACHE_DIR, sessionId + '.keys.json');

  let state = opts.cold ? null : readJson(sumFile);
  if (state && (state.v !== STATE_VERSION || state.pricing_sig !== pricing._sig)) state = null;
  let keys = null;
  let cold = !state;
  if (state) {
    // keys.json serve solo se qualcosa è cresciuto: lo carico pigramente
    const files = sessionFiles(transcript);
    let grown = false;
    for (const f of files) {
      let st; try { st = fs.statSync(f); } catch (e) { continue; }
      const prev = state.files[f];
      if (!prev || st.size !== prev.size || Math.round(st.mtimeMs) !== prev.mtime) { grown = true; break; }
    }
    if (!grown && state.render && !state.backlog) {
      return state.render;  // caso normale: una lettura, zero scritture
    }
    keys = readJson(keysFile);
    if (!keys || keys.pos_sig !== state.pos_sig) { state = null; keys = null; cold = true; }
  }
  if (!state) { state = emptyState(sessionId, pricing._sig); keys = { pos_sig: '', k: {} }; }

  const files = sessionFiles(transcript);
  const res = ingest(state, keys.k, files, pricing, {
    deadline: opts.deadline,
    maxBytes: opts.maxBytes,
  });
  if (res.reset && !cold) {
    // transcript riscritto: ricostruisco da zero (raro)
    state = emptyState(sessionId, pricing._sig);
    keys = { pos_sig: '', k: {} };
    ingest(state, keys.k, files, pricing, { deadline: opts.deadline, maxBytes: opts.maxBytes });
  }

  const t = totalsOf(state, pricing);
  state.backlog = res.backlog || 0;
  const prefix = state.backlog > 0 ? '~' : '';
  // senza listino il costo sarebbe uno zero fasullo: meglio non mostrarlo affatto
  const havePrices = Object.keys(pricing.models || {}).length > 0;
  const opt = opts.sl || slOptions(pricing);
  const parts = [];
  if (opt.show_cost && havePrices) parts.push(prefix + hCost(t.cost));
  if (opt.show_active_time) parts.push(hDur(t.active));
  if (opt.show_messages) parts.push(String(state.counts.assistant));
  const segment = parts.join(opt.separator);
  state.render = segment;
  state.updated_at = Date.now();
  state.pos_sig = posSig(state.files);

  if (!opts.noWrite) {
    try {
      fs.mkdirSync(CACHE_DIR, { recursive: true });
      keys.pos_sig = state.pos_sig;
      writeJsonAtomic(keysFile, keys);
      writeJsonAtomic(sumFile, state);
    } catch (e) { /* la cache è un'ottimizzazione, non un requisito */ }
  }
  return { segment, state, totals: t };
}

// --------------------------------------------------------------------------- //
// Consumo rispetto ai limiti dell'abbonamento
//
// Sorgente primaria: il payload della statusline (rate_limits.five_hour), che
// Claude Code riempie dagli header della risposta API — sempre fresco.
// Ripiego: ~/.claude.json -> cachedUsageUtilization, l'unico punto su disco dove
// il dato sopravvive fra una sessione e l'altra. Claude Code stesso lo considera
// scaduto dopo un'ora, e qui si fa lo stesso.
// --------------------------------------------------------------------------- //

const LIMIT_STALE_MS = 60 * 60 * 1000;
const CONFIG_MAX_BYTES = 8 * 1024 * 1024;

function limitFromPayload(payload) {
  const rl = payload && payload.rate_limits;
  const w = rl && rl.five_hour;
  if (!w || typeof w.used_percentage !== 'number') return null;
  return { pct: w.used_percentage, fresh: true };
}

function limitFromConfig() {
  const file = path.join(HOME, '.claude.json');
  try {
    const st = fs.statSync(file);
    if (st.size > CONFIG_MAX_BYTES) return null;   // file di config enorme: lascio perdere
    const cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
    const cached = cfg && cfg.cachedUsageUtilization;
    if (!cached) return null;
    if (cached.fetchedAtMs && Date.now() - cached.fetchedAtMs > LIMIT_STALE_MS) return null;
    const w = cached.utilization && cached.utilization.five_hour;
    if (!w || typeof w.utilization !== 'number') return null;
    // qui l'unità è 0-100 intero, non 0-1 come negli header interni
    if (w.resets_at) {
      const resets = Date.parse(w.resets_at);
      if (!Number.isNaN(resets) && resets < Date.now()) return null;  // finestra già girata
    }
    return { pct: w.utilization, fresh: false };
  } catch (e) {
    return null;
  }
}

function limitSegment(payload, opt) {
  const lim = limitFromPayload(payload) || limitFromConfig();
  if (!lim) return null;
  const pct = Math.round(lim.pct);
  let color = '\x1b[2m';                                       // sotto controllo: discreto
  if (pct >= opt.limit_critical_pct) color = '\x1b[31m';       // rosso
  else if (pct >= opt.limit_warn_pct) color = '\x1b[38;5;208m';  // arancione
  return color + '5h ' + pct + '%' + (lim.fresh ? '' : '~') + '\x1b[0m';
}

// --------------------------------------------------------------------------- //
// La statusline preesistente, eseguita come processo figlio
// --------------------------------------------------------------------------- //

/**
 * Esegue la statusline che l'utente aveva già e ne restituisce l'output.
 * `cmd` è un array tipo ["node", "/percorso/altra-statusline.js"]; se manca,
 * il nostro segmento è l'unica cosa mostrata.
 */
function runWrapped(cmd, raw, timeoutMs) {
  return new Promise(resolve => {
    if (!Array.isArray(cmd) || !cmd.length) return resolve('');
    let done = false;
    const finish = out => { if (!done) { done = true; resolve(out); } };
    let child;
    try {
      child = spawn(cmd[0], cmd.slice(1), {
        stdio: ['pipe', 'pipe', 'ignore'],
        windowsHide: true,
        shell: false,
      });
    } catch (e) { return finish(''); }

    const timer = setTimeout(() => { try { child.kill(); } catch (e) {} finish(''); },
                             timeoutMs || 1500);
    let out = '';
    child.stdout.on('data', d => { out += d.toString(); });
    child.on('error', () => { clearTimeout(timer); finish(''); });
    child.on('close', () => { clearTimeout(timer); finish(out); });
    try { child.stdin.end(raw); } catch (e) { /* potrebbe non leggere stdin */ }
  });
}

// --------------------------------------------------------------------------- //
// Selftest: parsing completo a freddo, da confrontare col CLI
// --------------------------------------------------------------------------- //

function selftest(sessionId) {
  const pricing = loadPricing();
  const transcript = findTranscript(sessionId);
  if (!transcript) { console.log('sessione non trovata: ' + sessionId); process.exit(1); }
  const files = sessionFiles(transcript);
  const state = emptyState(sessionId, pricing._sig);
  const keys = { pos_sig: '', k: {} };
  const t0 = Date.now();
  ingest(state, keys.k, files, pricing, { deadline: 0, maxBytes: Infinity });
  const t = totalsOf(state, pricing);
  const ms = Date.now() - t0;
  console.log('transcript : ' + transcript);
  console.log('file       : ' + files.length);
  console.log('costo      : ' + t.cost.toFixed(6) + '   ' + hCost(t.cost));
  console.log('durata     : ' + Math.round(t.duration) + ' s   ' + hDur(t.duration));
  console.log('attivo     : ' + Math.round(t.active) + ' s   ' + hDur(t.active));
  console.log('utente     : ' + state.counts.user);
  console.log('assistant  : ' + state.counts.assistant);
  console.log('righe rotte: ' + state.counts.bad_lines);
  console.log('modelli    :');
  for (const m of Object.keys(state.models).sort()) {
    const tok = state.models[m];
    console.log('   ' + m.padEnd(26) + ' in=' + tok[0] + ' out=' + tok[1] +
                ' cr=' + tok[2] + ' w5=' + tok[3] + ' w1=' + tok[4] +
                '  ' + hCost(costOf(m, tok, pricing)));
  }
  console.log('tempo      : ' + ms + ' ms');
}

// --------------------------------------------------------------------------- //
// main
// --------------------------------------------------------------------------- //

function main() {
  const argv = process.argv.slice(2);
  if (argv[0] === '--selftest') { selftest(argv[1] || ''); return; }

  let raw = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', d => { raw += d; });
  process.stdin.on('end', () => {
    // alcune shell antepongono un BOM UTF-8: romperebbe JSON.parse qui e a valle
    raw = raw.replace(/^\uFEFF/, '');

    const pricing = loadPricing();
    const opt = slOptions(pricing);
    let wrap = opt.wrap_command;
    if (process.env.CM_SL_WRAP) {
      try { wrap = JSON.parse(process.env.CM_SL_WRAP); }
      catch (e) { wrap = process.env.CM_SL_WRAP.split(' '); }
    }
    const wrapped = runWrapped(wrap, raw, opt.wrap_timeout_ms);

    let segment = null;
    if (!process.env.CM_SL_DISABLE && opt.enabled) {
      try {
        let payload = {};
        try { payload = JSON.parse(raw) || {}; } catch (e) { payload = {}; }
        const out = computeSegment(payload, {
          pricing: pricing,
          sl: opt,
          deadline: Date.now() + opt.budget_ms,
          maxBytes: opt.max_bytes_per_render,
        });
        if (typeof out === 'string') segment = out;              // valore da cache
        else if (out && out.segment) segment = out.segment;
        if (!segment && payload.cost && payload.cost.total_cost_usd) {
          segment = '~' + hCost(payload.cost.total_cost_usd);    // placeholder a freddo
        }
        const limits = opt.show_limits ? limitSegment(payload, opt) : null;
        if (limits) segment = segment ? segment + opt.separator + limits : limits;
      } catch (e) {
        segment = null;
        if (process.env.CM_SL_DEBUG) process.stderr.write('[cm-statusline] ' + (e && e.stack || e) + '\n');
      }
    }

    wrapped.then(before => {
      let text = before || '';
      if (segment) text += (text ? SEP : '') + segment;
      try { process.stdout.write(text); } catch (e) { /* niente da fare */ }
    }).catch(() => {
      try { process.stdout.write(''); } catch (e) {}
    });
  });
  process.stdin.on('error', () => { try { process.stdout.write(''); } catch (e) {} });
}

try { main(); } catch (e) { try { process.stdout.write(''); } catch (e2) {} }
