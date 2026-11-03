"use strict";

// ------------------------------------------------------------------ basics

window.addEventListener("error", (e) => showError(e.message + (e.error?.stack ? "\n" + e.error.stack : "")));
function showError(text) {
  let el = document.getElementById("errors");
  if (!el) { el = document.createElement("pre"); el.id = "errors"; el.className = "banner bad"; el.style.cssText = "position:fixed;left:12px;bottom:12px;right:12px;z-index:99;white-space:pre-wrap;max-height:40vh;overflow:auto"; document.body.appendChild(el); }
  el.textContent = String(text).slice(0, 2000);
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });

let ws = null;
let S = null;                       // latest engine snapshot
let route = parseRoute();
const live = { pose: null, energy: {}, start: {} };
const ui = { filter: "all", search: "", listening: null, toastUntil: 0, compact: false, calibDone: null, neutral: null, deleteArmed: null };

function parseRoute() {
  const parts = (location.hash || "#/library").replace(/^#\/?/, "").split("/");
  return { page: parts[0] || "library", id: parts[1] || null };
}
window.addEventListener("hashchange", () => { route = parseRoute(); render(true); });

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "blob";
  ws.onopen = () => send({ op: "preview", on: !document.hidden && route.page !== "overlay" });
  ws.onclose = () => { S && (S.disconnected = true); render(true); setTimeout(connect, 1000); };
  ws.onmessage = (e) => {
    if (e.data instanceof Blob) return showPreview(e.data);
    const m = JSON.parse(e.data);
    try { (handlers[m.type] || (() => {}))(m); } catch (err) { showError(`${m.type}: ${err.message}\n${err.stack}`); }
  };
}
function send(op) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(op)); }
document.addEventListener("visibilitychange", () => send({ op: "preview", on: !document.hidden && route.page !== "overlay" }));

const handlers = {
  state: (m) => { S = m; render(); },
  armed: (m) => { if (S) { S.armed = m.armed; render(); } },
  pose: onPose,
  motion: onMotion,
  swing: () => {},
  event: onEvent,
  capture: onCapture,
  captured: () => { ui.toastUntil = Date.now() + 12000; beep(880, 120); beep(1320, 180, 0.12, 0.12); },
  calibration: onCalibration,
  near: (m) => { if (S) { S.near = m; render(); } },
  drill: onDrill,
  neutral: (m) => { ui.neutral = m.phase === "cancelled" ? null : m; if (m.phase === "done") setTimeout(() => { ui.neutral = null; render(); }, 2500); render(true); },
  added: (m) => { if (m.id && route.page === "library" && m.focus) location.hash = `#/gesture/${m.id}`; },
};

// ------------------------------------------------------------------ rendering

function editing() {
  const ae = document.activeElement;
  return ae && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName) && ae.type !== "range" && ae.type !== "checkbox";
}

// Only touch the page when what it shows changed: the engine sends updates many times a second,
// and replacing a button between your press and release swallows the click.
function setHTML(el, html) {
  if (!el || el._html === html) return false;
  el.innerHTML = html;
  el._html = html;
  return true;
}

function render(force = false) {
  if (!S) { setHTML($("#root"), `<div class="main"><p class="muted">Connecting to the engine…</p></div>`); return; }
  document.body.classList.toggle("overlay", route.page === "overlay");
  renderModal();
  if (!force && editing()) { renderChips(); return; }
  const page = route.page;
  if (page === "overlay" || page === "hud") { setHTML($("#root"), hudPage()); return; }
  const body = page === "gesture" ? gesturePage(route.id) : page === "engine" ? enginePage()
    : page === "metrics" ? metricsPage() : libraryPage();
  if (setHTML($("#root"), `<div class="app">${sidebar()}<main class="main" id="main">${banners()}${body}</main></div>`)) afterRender();
}

function afterRender() {
  drawSkeleton();
  for (const [name, e] of Object.entries(live.energy)) setMeter(name, e, live.start[name]);
}

function banners() {
  let out = "";
  if (S.demo) out += `<div class="banner info">Demo: sample data on simulated weapons and webcam, in its own folder. Your real profile and play log aren't touched, and no keys are sent. A demo player makes a move every few seconds.</div>`;
  if (S.disconnected) out += `<div class="banner bad">Disconnected from the engine — is it still running?</div>`;
  if (!S.keys_ok) out += `<div class="banner warn">Key output is blocked. Open <b>System Settings → Privacy &amp; Security → Accessibility</b>, turn on ${esc(S.permission_app || "your terminal app")}, then quit and reopen it.</div>`;
  return out;
}

const ICONS = {
  library: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/></svg>`,
  capture: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>`,
  hud: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-7 4 14 3-7h4"/></svg>`,
  engine: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>`,
  metrics: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>`,
  logo: `<svg aria-hidden="true" width="28" height="28" viewBox="0 0 28 28"><rect x="1" y="1" width="26" height="26" rx="8" fill="#ff6b3d"/><path d="M8 19V9l6 6 6-6v10" fill="none" stroke="#1a0d07" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

function sidebar() {
  const p = S.profile;
  const options = (p.all || []).map((x) => `<option value="${esc(x.path)}" ${x.path === p.path ? "selected" : ""}>${esc(x.name)} · ${x.gestures}</option>`).join("");
  const nav = (id, label, href) => `<a href="${href}" class="${route.page === id ? "on" : ""}">${ICONS[id]}${label}</a>`;
  return `<nav class="side" aria-label="Main">
    <div class="brand">${ICONS.logo}<div><b>MOCAP</b><span>Gesture engine</span></div></div>
    <label class="field">Profile<select data-change="profile" aria-label="Profile">${options || `<option>${esc(p.name)}</option>`}</select></label>
    <div class="nav">
      ${nav("library", "Library", "#/library")}
      <a href="#" data-act="capture" class="">${ICONS.capture}Capture</a>
      ${nav("hud", "Live HUD", "#/hud")}
      ${nav("engine", "Engine", "#/engine")}
      ${nav("metrics", "Metrics", "#/metrics")}
    </div>
    <div class="arm-card">
      <div class="row between"><span class="muted" style="font-size:12px">Engine</span>
        <button class="arm-pill ${S.armed ? "on" : ""}" data-act="arm">${S.armed ? "ARMED" : "DISARMED"}</button></div>
      <span style="font-size:12px;color:#a7aec0">${armHint()}</span>
    </div>
  </nav>`;
}

function armHint() {
  const g = S.gestures.find((x) => x.action === "arm_toggle" && x.ready);
  return g ? `${esc(g.name)} to ${S.armed ? "disarm" : "arm"}.` : `Record a pose and set it to "Toggle armed" to arm hands-free.`;
}

function chipsHtml() {
  const c = [];
  c.push(S.camera.ok ? `<span class="chip"><i class="dot ok"></i>Webcam ${S.camera.fps} fps</span>` : `<span class="chip"><i class="dot bad"></i>Webcam off</span>`);
  for (const d of S.devices) {
    const bat = d.info?.battery && d.info.battery !== "unknown" ? ` · ${d.info.battery}` : "";
    c.push(`<span class="chip"><i class="dot ${d.role || ""}"></i>${esc(cap(d.role || "unassigned"))} · ${esc(d.name)} · ${d.rate} Hz${bat}</span>`);
  }
  if (!S.devices.length) c.push(`<span class="chip"><i class="dot warn"></i>No weapons connected</span>`);
  c.push(S.dry_run ? `<span class="chip"><i class="dot warn"></i>Dry run</span>` : S.keys_ok ? `<span class="chip"><i class="dot ok"></i>Keys OK</span>` : `<span class="chip"><i class="dot bad"></i>Keys blocked</span>`);
  if (S.settings.require_wow_front) c.push(S.wow_front ? `<span class="chip"><i class="dot ok"></i>WoW in front</span>` : `<span class="chip"><i class="dot warn"></i>${esc(S.front_app || "Another app")} in front</span>`);
  return c.join("");
}
function renderChips() { const el = $("#chips"); if (el) el.innerHTML = chipsHtml(); }
const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;

// ------------------------------------------------------------------ gesture visuals

const SOURCE_COLOR = { melee: "#f5b544", bow: "#7aa2ff", webcam: "#4fd1a5" };
const BONES = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [15, 19], [16, 20], [11, 23], [12, 24], [23, 24], [23, 25], [25, 27], [24, 26], [26, 28]];

function traceSvg(trace, color, w = 320, h = 180) {
  if (!trace || !trace.length) return "";
  const n = trace.length, pad = 18;
  const maxAbs = Math.max(1, ...trace.flat().map(Math.abs));
  const lines = [0, 1, 2].map((axis) => trace.map((p, i) => `${(pad + (i / (n - 1)) * (w - 2 * pad)).toFixed(1)},${(h / 2 - (p[axis] / maxAbs) * (h / 2 - pad)).toFixed(1)}`).join(" "));
  const op = [1, 0.6, 0.35];
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="M${pad} ${h / 2}H${w - pad}" stroke="#1e2330"/>${lines.map((pts, i) => `<polyline points="${pts}" fill="none" stroke="${color}" stroke-opacity="${op[i]}" stroke-width="${i ? 2 : 3}" stroke-linejoin="round" stroke-linecap="round"/>`).join("")}</svg>`;
}

function skeletonSvg(skel, aspect, color, w = 320, h = 180) {
  if (!skel) return "";
  const pts = skel.map((p) => [p[0] * (aspect || 1.78), p[1], p[2]]);
  const vis = pts.filter((p, i) => p[2] > 0.5 && i !== 0 || i === 0);
  const xs = vis.map((p) => p[0]), ys = vis.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const s = Math.min((w - 40) / Math.max(maxX - minX, 0.01), (h - 30) / Math.max(maxY - minY, 0.01));
  const ox = (w - (maxX - minX) * s) / 2, oy = (h - (maxY - minY) * s) / 2;
  const P = (p) => [w - (ox + (p[0] - minX) * s), oy + (p[1] - minY) * s];   // mirrored, like a mirror
  const bones = BONES.filter(([a, b]) => pts[a][2] > 0.5 && pts[b][2] > 0.5).map(([a, b]) => { const A = P(pts[a]), B = P(pts[b]); return `<line x1="${A[0].toFixed(1)}" y1="${A[1].toFixed(1)}" x2="${B[0].toFixed(1)}" y2="${B[1].toFixed(1)}"/>`; }).join("");
  const head = P(pts[0]);
  const shoulder = Math.hypot(P(pts[11])[0] - P(pts[12])[0], P(pts[11])[1] - P(pts[12])[1]);
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><g stroke="${color}" stroke-width="3.5" stroke-linecap="round">${bones}</g><circle cx="${head[0].toFixed(1)}" cy="${head[1].toFixed(1)}" r="${Math.max(6, shoulder * 0.28).toFixed(1)}" fill="none" stroke="${color}" stroke-width="3.5"/></svg>`;
}

const FIGURE = (paths, extra = "") => `<svg viewBox="0 0 320 180" aria-hidden="true"><g transform="translate(34 24)" stroke="#4fd1a5" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" fill="none"><circle cx="126" cy="24" r="9"/><path d="${paths}"/></g>${extra}</svg>`;
const JOG_SVG = FIGURE("M114 42h24M114 42l4 38h16l4-38M118 80l-4 26-2 26M134 80l14 12-6 18M114 42l-10 16 6 16M138 42l10 14-2 18", `<path d="M88 124h20M82 138h20M92 110h16" stroke="#6b7488" stroke-width="3" stroke-linecap="round"/>`);
const HOP_SVG = `<svg viewBox="0 0 320 180" aria-hidden="true"><ellipse cx="160" cy="166" rx="22" ry="4" fill="#1c2029"/><g transform="translate(34 10)" stroke="#4fd1a5" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" fill="none"><circle cx="126" cy="24" r="9"/><path d="M114 42h24M114 42l4 38h16l4-38M118 80l-6 20 2 20M134 80l6 20-2 20M114 42l-12 16M138 42l12 16"/></g><path d="M250 130V60M242 68l8-8 8 8" stroke="#6b7488" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

// A body move: the figure where it started (faint) and ended, with trails for the joints that
// travelled. Points are [wrist L, wrist R, elbow L, elbow R, ankle L, ankle R, knee L, knee R,
// body], in shoulder widths from the hips, y up.
function moveSvg(m, color = "#4fd1a5", w = 320, h = 180) {
  if (!m || !m.points || !m.points.length) return "";
  const P = m.points, last = P.length - 1;
  const at = (f, i) => [P[f][i][0] + (i < 8 ? P[f][8][0] : 0), P[f][i][1] + (i < 8 ? P[f][8][1] : 0)];
  const body = (f) => {
    const o = P[f][8], sh = [[o[0] - 0.5, o[1] + 1.35], [o[0] + 0.5, o[1] + 1.35]], hip = [[o[0] - 0.25, o[1]], [o[0] + 0.25, o[1]]];
    return { sh, hip, head: [o[0], o[1] + 1.8], j: Array.from({ length: 8 }, (_, i) => at(f, i)) };
  };
  const frames = [body(0), body(last)];
  const all = frames.flatMap((b) => [...b.sh, ...b.hip, b.head, ...b.j]).concat(m.active.flatMap((i) => P.map((_, f) => at(f, i))));
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sc = Math.min((w - 40) / Math.max(x1 - x0, 0.5), (h - 30) / Math.max(y1 - y0, 0.5));
  const X = (p) => (w / 2 + (p[0] - (x0 + x1) / 2) * sc).toFixed(1), Y = (p) => (h / 2 - (p[1] - (y0 + y1) / 2) * sc).toFixed(1);
  const line = (a, b) => `M${X(a)} ${Y(a)}L${X(b)} ${Y(b)}`;
  const figure = (b) => [line(b.sh[0], b.sh[1]), line(b.hip[0], b.hip[1]), line([(b.sh[0][0] + b.sh[1][0]) / 2, b.sh[0][1]], [(b.hip[0][0] + b.hip[1][0]) / 2, b.hip[0][1]]),
    line(b.sh[0], b.j[2]), line(b.j[2], b.j[0]), line(b.sh[1], b.j[3]), line(b.j[3], b.j[1]),
    line(b.hip[0], b.j[6]), line(b.j[6], b.j[4]), line(b.hip[1], b.j[7]), line(b.j[7], b.j[5])].join("");
  const head = (b, o) => `<circle cx="${X(b.head)}" cy="${Y(b.head)}" r="${(0.22 * sc).toFixed(1)}" fill="none" stroke="${color}" stroke-width="3" opacity="${o}"/>`;
  const trails = m.active.map((i) => {
    const pts = P.map((_, f) => at(f, i));
    const end = pts[pts.length - 1];
    return `<polyline points="${pts.map((p) => `${X(p)},${Y(p)}`).join(" ")}" fill="none" stroke="#ff6b3d" stroke-width="2.5" stroke-dasharray="1 5" stroke-linecap="round"/><circle cx="${X(end)}" cy="${Y(end)}" r="4" fill="#ff6b3d"/>`;
  }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><g stroke="${color}" stroke-width="3" stroke-linecap="round" fill="none"><path d="${figure(frames[0])}" opacity=".25"/>${head(frames[0], 0.25)}<path d="${figure(frames[1])}"/>${head(frames[1], 1)}</g>${trails}</svg>`;
}

// What you actually recorded for a body move: your skeleton at the start (faint) and at the
// furthest point (solid), with trails for the joints the move is judged on. Mirrored, like a mirror.
function realMoveSvg(r, color = "#4fd1a5", w = 320, h = 180) {
  if (!r || !r.frames || !r.frames.length) return "";
  const a = r.aspect || 1.78;
  const frames = r.frames.map((f) => f.map((p) => [p[0] * a, p[1], p[2]]));
  const trails = Object.entries(r.trails || {}).map(([j, pts]) => pts.map((p) => [p[0] * a, p[1]]));
  const all = frames.flatMap((f) => f.filter((p, i) => p[2] > 0.5 && i !== 0 || i === 0)).concat(trails.flat());
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const sc = Math.min((w - 40) / Math.max(maxX - minX, 0.01), (h - 24) / Math.max(maxY - minY, 0.01));
  const ox = (w - (maxX - minX) * sc) / 2, oy = (h - (maxY - minY) * sc) / 2;
  const P = (p) => [(w - (ox + (p[0] - minX) * sc)).toFixed(1), (oy + (p[1] - minY) * sc).toFixed(1)];
  const fig = (f, op, width) => {
    const bones = BONES.filter(([i, j]) => f[i][2] > 0.5 && f[j][2] > 0.5).map(([i, j]) => { const A = P(f[i]), B = P(f[j]); return `M${A[0]} ${A[1]}L${B[0]} ${B[1]}`; }).join("");
    const hd = P(f[0]), shoulder = Math.hypot(f[11][0] - f[12][0], f[11][1] - f[12][1]) * sc;
    return `<g opacity="${op}" stroke="${color}" stroke-width="${width}" stroke-linecap="round" fill="none"><path d="${bones}"/><circle cx="${hd[0]}" cy="${hd[1]}" r="${Math.max(5, shoulder * 0.28).toFixed(1)}"/></g>`;
  };
  const solid = frames.length > 1 ? frames[1] : frames[0];
  const figs = (frames.length > 1 ? fig(frames[0], 0.25, 3) : "") + fig(solid, 1, 3.5);
  const tr = trails.map((pts) => { const end = P(pts[pts.length - 1]), far = P(pts.reduce((m, p) => (Math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) > Math.hypot(m[0] - pts[0][0], m[1] - pts[0][1]) ? p : m), pts[0]));
    return `<polyline points="${pts.map((p) => P(p).join(",")).join(" ")}" fill="none" stroke="#ff6b3d" stroke-width="2.5" stroke-dasharray="1 5" stroke-linecap="round"/><circle cx="${far[0]}" cy="${far[1]}" r="4" fill="#ff6b3d"/>`; }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${figs}${tr}</svg>`;
}

// A real photo of you doing it, with your skeleton drawn on top: both mirrored (like a mirror)
// and cropped to you. With every frame of a take, hovering plays it (start → peak → end).
function photoSvg(rec, { w = 320, h = 180, all = false, trails = true, color = "#4fd1a5", label = "" } = {}) {
  if (!rec || !rec.frames || !rec.frames.length) return "";
  const a = rec.aspect || 1.78;
  const peak = Math.min(rec.peak || 0, rec.frames.length - 1);
  const shown = all ? rec.frames.map((_, i) => i) : [peak];
  const M = (p) => [a - p[0] * a, p[1]];                       // mirrored picture coordinates
  const pts = [];
  for (const i of shown) (rec.frames[i].lm || []).forEach((p, j) => { if ((j === 0 || j >= 11) && j <= 28 && p[2] > 0.5) pts.push(M(p)); });
  const tr = trails ? Object.values(rec.trails || {}).filter((t) => t.length > 1).map((t) => t.map(M)) : [];
  tr.forEach((t) => pts.push(...t));
  let x0 = 0, x1 = a, y0 = 0, y1 = 1;
  if (pts.length > 3) {
    x0 = Math.min(...pts.map((p) => p[0])); x1 = Math.max(...pts.map((p) => p[0]));
    y0 = Math.min(...pts.map((p) => p[1])); y1 = Math.max(...pts.map((p) => p[1]));
    const pad = 0.14 * Math.max(x1 - x0, y1 - y0) + 0.02;
    x0 -= pad; x1 += pad; y0 -= pad * 1.4; y1 += pad;               // room for the head
  }
  let bw = x1 - x0, bh = y1 - y0;
  const r = w / h;
  if (bw / bh < r) { const nw = bh * r; x0 -= (nw - bw) / 2; bw = nw; } else { const nh = bw / r; y0 -= (nh - bh) / 2; bh = nh; }
  if (bw <= a) x0 = Math.max(0, Math.min(x0, a - bw));
  if (bh <= 1) y0 = Math.max(0, Math.min(y0, 1 - bh));
  const u = bh / h;                                                  // one screen pixel
  const f = (n) => n.toFixed(4);
  const skel = (lm) => {
    if (!lm) return "";
    const P = lm.map(M);
    const bones = BONES.filter(([i, j]) => lm[i][2] > 0.5 && lm[j][2] > 0.5).map(([i, j]) => `M${f(P[i][0])} ${f(P[i][1])}L${f(P[j][0])} ${f(P[j][1])}`).join("");
    const sh = Math.hypot(P[11][0] - P[12][0], P[11][1] - P[12][1]);
    const head = lm[0][2] > 0.5 ? `<circle cx="${f(P[0][0])}" cy="${f(P[0][1])}" r="${f(Math.max(sh * 0.3, 5 * u))}"/>` : "";
    const dots = [15, 16, 27, 28].filter((j) => lm[j][2] > 0.5).map((j) => `<circle cx="${f(P[j][0])}" cy="${f(P[j][1])}" r="${f(3 * u)}" fill="${color}" stroke="none"/>`).join("");
    return `<g fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="${bones}" stroke="#000" stroke-opacity=".45" stroke-width="5" vector-effect="non-scaling-stroke"/><g stroke="${color}" stroke-width="2.5"><path d="${bones}" vector-effect="non-scaling-stroke"/>${head.replace("<circle", '<circle vector-effect="non-scaling-stroke"')}</g>${dots}</g>`;
  };
  const n = shown.length, step = 0.32, dur = (n * step).toFixed(2);
  const frames = shown.map((i, k) => {
    const fr = rec.frames[i];
    const anim = n > 1 ? ` style="--an:fr${n};--dur:${dur}s;--dl:-${(((n - k) % n) * step).toFixed(2)}s"` : "";
    return `<g class="fr${i === peak ? " pk" : ""}"${anim}><image href="/photos/${esc(fr.file)}" x="0" y="0" width="${f(a)}" height="1" preserveAspectRatio="none" transform="matrix(-1 0 0 1 ${f(a)} 0)"/>${skel(fr.lm)}</g>`;
  }).join("");
  const trails2 = tr.map((t) => { const far = t.reduce((m, p) => (Math.hypot(p[0] - t[0][0], p[1] - t[0][1]) > Math.hypot(m[0] - t[0][0], m[1] - t[0][1]) ? p : m), t[0]);
    return `<polyline points="${t.map((p) => `${f(p[0])},${f(p[1])}`).join(" ")}" fill="none" stroke="#ff6b3d" stroke-width="2.5" stroke-dasharray="1 5" stroke-linecap="round" vector-effect="non-scaling-stroke"/><circle cx="${f(far[0])}" cy="${f(far[1])}" r="${f(4 * u)}" fill="#ff6b3d"/>`; }).join("");
  // Where the crop runs past the picture, a blurred, darkened copy of it fills in (no black bars).
  const pk = rec.frames[peak].file, fid = `pb${pk.slice(0, 10)}${w}x${h}`;
  const sc = Math.max(bw / a, bh), bgw = a * sc, bgh = sc;
  const fill = x0 < 0 || y0 < 0 || x0 + bw > a || y0 + bh > 1
    ? `<defs><filter id="${fid}" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="${f(bh * 0.04)}"/></filter></defs><g filter="url(#${fid})" opacity=".45"><image href="/photos/${esc(pk)}" x="${f(x0 + (bw - bgw) / 2)}" y="${f(y0 + (bh - bgh) / 2)}" width="${f(bgw)}" height="${f(bgh)}" preserveAspectRatio="none" transform="matrix(-1 0 0 1 ${f(2 * x0 + bw)} 0)"/></g>` : "";
  return `<svg class="photo${n > 1 ? " flip" : ""}" viewBox="${f(x0)} ${f(y0)} ${f(bw)} ${f(bh)}" preserveAspectRatio="xMidYMid slice" role="img" aria-label="${esc(label || "Photo of you doing it, with your skeleton")}"><rect x="${f(x0)}" y="${f(y0)}" width="${f(bw)}" height="${f(bh)}" fill="#07080b"/>${fill}${frames}${trails2}</svg>`;
}

// One frame (a take's peak) as a photo record.
const peakRec = (p) => (p ? { aspect: p.aspect, peak: 0, frames: [{ file: p.file, lm: p.lm }], credit: p.credit } : null);

const SAMPLE_NOTE = `<span class="muted" style="font-size:12px">A real person doing something like it, with the skeleton the tracker finds. When you capture your own moves, this shows you, at the peak of each take.</span>`;

function creditLine(rec) {
  const c = rec && rec.credit;
  return c ? `<span class="muted credit" style="font-size:11px">Sample photo: ${esc(c.author || "unknown")} · ${esc(c.license || "")} · Wikimedia Commons</span>` : "";
}

const SCOPE_LABEL = { auto: "Auto", whole: "Whole body", left_arm: "Left arm", right_arm: "Right arm", arms: "Both arms", left_leg: "Left leg", right_leg: "Right leg", legs: "Both legs", body: "Whole-body position", head: "Head", weapon: "Weapon only (gyro)" };

function frameSvg(g) {
  if (g.photo) return photoSvg(g.photo, { all: true, label: `${g.name}: you doing it` });
  if (g.kind === "motion") return traceSvg(g.trace, SOURCE_COLOR[g.source]);
  if (g.kind === "move") return g.real ? realMoveSvg(g.real) : moveSvg(g.move);
  if (g.kind === "pose") return skeletonSvg(g.skeleton, g.aspect, "#4fd1a5");
  if (g.kind === "jog") return JOG_SVG;
  if (g.kind === "hop") return HOP_SVG;
  return "";
}

function spark(values, color, w = 100, h = 36, dashed = false) {
  if (!values || values.length < 2) return "";
  const max = Math.max(...values, 0.01);
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 3 - (v / max) * (h - 6)).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" ${dashed ? 'stroke-dasharray="4 4"' : ""}/></svg>`;
}

function kindLabel(g) {
  return g.kind === "motion" ? `${cap(g.source)} · motion` : g.kind === "move" ? "Body move · webcam" : g.kind === "pose" ? "Pose" : g.kind === "jog" ? "Movement · held" : "Movement";
}

function firesText(g) {
  if (g.action === "arm_toggle") return "Toggles armed";
  if (g.kind === "jog") return "Key held while you jog";
  if (g.mode === "hold") return "Key held while you hold it";
  return g.kind === "pose" ? "Fires once per hold" : g.kind === "hop" ? "Fires once per hop" : "Fires once per move";
}

const DEFAULT_HOW = {
  motion: ["Do it the way you recorded it, then pause.", "Speed and force don't matter; direction does."],
  pose: ["Get into the pose and hold it for a moment.", "Drop it before doing it again."],
  move: ["Do it the way you recorded it, then settle for a moment.", "Big and clear beats fast."],
  jog: ["Jog on the spot to keep running.", "Stop and you stop."],
  hop: ["A small hop, both feet off the floor."],
};
const REC_TIPS = {
  motion: ["Do it 5 times, pausing between takes.", "Grip the weapon the same way every take.", "Fires by accident? Record moves to ignore."],
  pose: ["Stand where you play, upper body in frame.", "Hold still through the 2 s capture.", "Fires by accident? Record more neutral."],
  move: ["Do it 5 times, settling for a second between takes.", "Whole body in frame, weapons still.", "Fires by accident? Press \"Wasn't me\"."],
  jog: ["Nothing to record: it watches your knees.", "Needs your knees in frame."],
  hop: ["Nothing to record: it watches your hips.", "Needs your hips in frame."],
};

function tooltipHtml(g) {
  const type = g.tooltip?.type || S.settings.tooltip_type;
  if (type === "Off") return "";
  const key = g.key_label && g.key_label !== "—" ? g.key_label : "no key";
  if (type === "Spell card") {
    return `<div class="tip card" role="tooltip"><b>${esc(g.casts || g.name)}</b><span class="sub">Key ${esc(key)} · ${esc(kindLabel(g))}</span><span class="line">Gesture: ${esc(g.name)}</span><span class="foot">${esc(firesText(g))}</span></div>`;
  }
  if (type === "Recording tips") {
    return `<div class="tip rec" role="tooltip"><b>Recording: ${esc(g.name)}</b>${(REC_TIPS[g.kind] || []).map((l) => `<span class="line">${esc(l)}</span>`).join("")}</div>`;
  }
  const steps = g.tooltip?.steps?.length ? g.tooltip.steps : DEFAULT_HOW[g.kind] || [];
  return `<div class="tip" role="tooltip"><b>${esc(g.name)}</b><span class="sub">${esc(kindLabel(g))}</span>${steps.map((l) => `<span class="line">${esc(l)}</span>`).join("")}<span class="foot">${esc(firesText(g))}</span></div>`;
}

function keyCap(g, cls = "") {
  if (g.action === "arm_toggle") return `<span class="key ${cls}" style="color:#ffb3a1">ARM</span>`;
  const unset = !g.key_label || g.key_label === "—";
  return `<span class="key ${cls} ${unset ? "unset" : ""}">${unset ? "Set key" : esc(g.key_label)}</span>`;
}

// ------------------------------------------------------------------ library

function category(g) {
  if (g.action === "arm_toggle") return "system";
  if (g.kind === "jog" || g.kind === "hop") return "movement";
  if (g.kind === "pose") return "poses";
  if (g.kind === "move") return "body";
  return g.source;
}

function libraryPage() {
  const cats = { all: S.gestures.length, melee: 0, bow: 0, body: 0, poses: 0, movement: 0, system: 0 };
  S.gestures.forEach((g) => cats[category(g)]++);
  const q = ui.search.trim().toLowerCase();
  const shown = S.gestures.filter((g) => (ui.filter === "all" || category(g) === ui.filter) && (!q || `${g.name} ${g.casts}`.toLowerCase().includes(q)));
  const filterBtn = (id, label, dot) => `<button class="${ui.filter === id ? "on" : ""}" data-act="filter" data-v="${id}" aria-pressed="${ui.filter === id}">${dot ? `<i class="dot ${dot}"></i>` : ""}${label} <span>${cats[id]}</span></button>`;
  const tiles = shown.map(tileHtml).join("");
  const empty = !S.gestures.length ? `<div class="empty"><b>No gestures yet.</b><span>Tap the weapon shafts together (or press Capture) and do a move five times. Then press the key you bound in WoW. That's it.</span><span class="muted">Record Neutral first on the Engine page so poses know what "doing nothing" looks like.</span><div class="row wrap"><button class="primary" data-act="capture">Capture your first move</button>${S.demo ? "" : `<button data-act="open-demo">Try it with sample data</button>`}</div></div>` : "";
  return `
  <div class="toolbar"><div class="chips grow" id="chips">${chipsHtml()}</div>
    <button class="primary capture-btn" data-act="capture">${ICONS.capture}Capture a move<small>or tap the shafts · ⌥C</small></button></div>
  <div class="head"><div class="lead"><h1>Library</h1><p class="soft">Every move you've captured. Each one presses one key you bound in WoW.</p></div>
    <div class="toolbar">
      <label class="search">${'<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>'}<input type="text" data-change="search" data-live="search" placeholder="Find a move or spell" aria-label="Find a move or spell" value="${esc(ui.search)}"></label>
      <label class="inline muted">Tooltips <select data-change="setting" data-k="tooltip_type">${["How to do it", "Spell card", "Recording tips", "Off"].map((t) => `<option ${S.settings.tooltip_type === t ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    </div></div>
  <div class="filters" role="group" aria-label="Filter">${filterBtn("all", "All")}${filterBtn("melee", "Melee", "melee")}${filterBtn("bow", "Bow", "bow")}${filterBtn("body", "Body moves", "pose")}${filterBtn("poses", "Poses", "pose")}${filterBtn("movement", "Movement", "move")}${filterBtn("system", "System", "live")}</div>
  ${empty}
  <div class="grid" id="tiles">${tiles}</div>
  <div class="toolbar"><span class="muted">Add without capturing:</span>
    <button class="small" data-act="capture-pose">+ Pose</button>
    <button class="small" data-act="capture-move">+ Body move</button>
    <button class="small" data-act="add" data-kind="jog">+ Jog in place</button>
    <button class="small" data-act="add" data-kind="hop">+ Hop</button></div>`;
}

function tileHtml(g) {
  const isNew = g.id === S.last_captured;
  const st = g.stats || { fired: 0, near: 0 };
  const warn = g.calibration && g.calibration.margin !== null && g.calibration.margin < 1.2;
  const statLine = isNew ? `<span class="stat" style="color:#ffb3a1">Just captured</span>`
    : !g.ready ? `<span class="stat" style="color:var(--warn)">${g.count ? "Needs a key" : "Not recorded yet"}</span>`
    : `<span class="stat">Fired ${st.fired}× this session${st.near ? ` · ${st.near} near-miss` : ""}</span>`;
  const src = g.kind === "motion" ? g.source : g.kind === "pose" || g.kind === "move" ? "pose" : "move";
  return `<a href="#/gesture/${g.id}" class="tile ${isNew ? "new" : ""} ${g.enabled ? "" : "off"}" data-gid="${g.id}">
    <div class="frame">${frameSvg(g)}<span class="badge"><i class="dot ${src}"></i>${esc(g.kind === "motion" ? cap(g.source) + (g.scope === "weapon" ? " · gyro only" : "") : g.kind === "move" ? `Body move · ${(SCOPE_LABEL[g.scope_effective] || "").toLowerCase()}` : g.kind === "pose" ? (g.action === "arm_toggle" ? "System pose" : "Pose") + (g.scope && g.scope !== "whole" ? ` · ${SCOPE_LABEL[g.scope].toLowerCase()}` : "") : "Movement")}</span>
      ${isNew ? `<span class="badge right new">NEW</span>` : warn ? `<span class="badge right warn">close to ${esc(g.calibration.nearest)}</span>` : ""}</div>
    <div class="tile-body"><div class="tile-row"><b>${esc(g.name)}</b>${keyCap(g)}</div>
      <span class="casts">${esc(g.casts || "")}</span>${statLine}</div>
    ${tooltipHtml(g)}</a>`;
}

// ------------------------------------------------------------------ gesture detail

function gesturePage(id) {
  const g = S.gestures.find((x) => x.id === id);
  if (!g) return `<p><a href="#/library">Library</a> / not found</p>`;
  const st = g.stats || { fired: 0, near: 0 };
  const color = SOURCE_COLOR[g.source] || "#4fd1a5";
  const listening = ui.listening && ui.listening.id === g.id;
  return `
  <div class="muted" style="font-size:13px"><a href="#/library">Library</a> / ${esc(g.name)}</div>
  <div class="head"><div class="lead" style="gap:10px">
      <input type="text" data-change="gfield" data-k="name" value="${esc(g.name)}" aria-label="Gesture name" style="font-family:'Chakra Petch',Inter,sans-serif;font-size:30px;font-weight:700;background:transparent;border-color:transparent;padding:2px 6px;margin-left:-6px">
      <div class="chips"><span class="chip"><i class="dot ${g.kind === "motion" ? g.source : "pose"}"></i>${esc(kindLabel(g))}</span>
        ${g.casts ? `<span class="chip">Casts ${esc(g.casts)}</span>` : ""}
        ${g.id === S.last_captured ? `<span class="chip" style="color:#ffb3a1">Just captured</span>` : ""}</div></div>
    <div class="toolbar">
      ${g.action === "arm_toggle" ? "" : `<button class="key big ${listening ? "listening" : ""}" data-act="rebind" data-id="${g.id}" title="Click, then press the key you bound in WoW">${listening ? "Press a key…" : g.key_label && g.key_label !== "—" ? esc(g.key_label) : "Set key"}</button>`}
      ${g.kind === "pose" ? `<select data-change="gfield" data-k="action" aria-label="What it does"><option value="key" ${g.action === "key" ? "selected" : ""}>Sends a key</option><option value="arm_toggle" ${g.action === "arm_toggle" ? "selected" : ""}>Toggles armed</option></select>` : ""}
      ${scopeSelect(g)}
      <label class="inline muted"><input type="checkbox" data-change="gcheck" data-k="enabled" ${g.enabled ? "checked" : ""}> Enabled</label>
    </div></div>
  <div class="cols">
    <div class="wide">${takesPanel(g, color)}${activityPanel(g)}</div>
    <div class="narrow">${firingPanel(g, st)}${tooltipPanel(g)}
      <div class="toolbar"><button class="danger" data-act="delete" data-id="${g.id}">${ui.deleteArmed === g.id ? "Click again to delete" : "Delete gesture"}</button></div></div>
  </div>`;
}

// Which part of you a gesture is judged on. "Auto" (body moves) = the parts that moved in your takes.
function scopeSelect(g) {
  const opts = g.kind === "move" ? ["auto", "whole", "left_arm", "right_arm", "arms", "left_leg", "right_leg", "legs", "body"]
    : g.kind === "pose" ? ["whole", "left_arm", "right_arm", "arms", "left_leg", "right_leg", "legs", "head"]
    : g.kind === "motion" ? ["auto", "weapon"] : null;
  if (!opts) return "";
  const label = (o) => o === "auto" ? (g.kind === "move" ? `Auto: ${SCOPE_LABEL[g.auto_scope || "whole"].toLowerCase()}` : "Weapon + where your hand is") : SCOPE_LABEL[o];
  return `<label class="inline muted" title="Judge it on this part of you only; the rest of your body doesn't matter">Judged on <select data-change="gfield" data-k="scope" aria-label="Judged on">${opts.map((o) => `<option value="${o}" ${(g.scope || (g.kind === "pose" ? "whole" : "auto")) === o ? "selected" : ""}>${esc(label(o))}</option>`).join("")}</select></label>`;
}

function takesPanel(g, color) {
  if (g.kind === "jog" || g.kind === "hop") {
    return `<section class="panel"><h2>How it's detected</h2><div class="frame" style="border-radius:10px;max-width:420px">${frameSvg(g)}</div>
      <p class="soft">${g.kind === "jog" ? "The webcam watches your feet (heel kicks) or knees take turns lifting. Three steps within 1.5 s starts it; it stops 0.7 s after your last step. Turn it on or off on the Engine page." : "The webcam watches your hips rise well above normal and land again within 0.7 s. Standing up from a crouch doesn't count. Turn it on or off on the Engine page."}</p></section>`;
  }
  if (g.kind === "pose") {
    return `<section class="panel"><h2>Recording <span class="muted" style="font-weight:400;font-size:12px">${g.count} frames</span></h2>
      ${g.photo ? `<div class="frame big-photo">${photoSvg(g.photo, { w: 520, h: 292 })}</div>${creditLine(g.photo)}`
        : `<div class="chart" style="max-width:520px">${g.skeleton ? skeletonSvg(g.skeleton, g.aspect, "#4fd1a5") : `<p class="muted">Not recorded yet.</p>`}</div>`}
      <div class="toolbar"><button data-act="capture-for" data-id="${g.id}">● ${g.count ? "Add more" : "Record"}</button>${g.count ? `<button class="ghost" data-act="clear" data-id="${g.id}">Clear</button>` : ""}</div></section>`;
  }
  if (g.kind === "move") {
    const takes = g.takes || [];
    return `<section class="panel"><h2><span>Takes <span class="muted" style="font-weight:400;font-size:12px">${takes.length} recorded</span></span>
      <span class="toolbar"><button class="small" data-act="capture-for" data-id="${g.id}">● Add takes</button>${takes.length ? `<button class="small ghost" data-act="rerecord" data-id="${g.id}">Re-record all</button>` : ""}</span></h2>
      ${g.photo ? `<div class="frame big-photo">${photoSvg(g.photo, { w: 520, h: 292, all: true })}</div>${g.photo.credit ? SAMPLE_NOTE : `<span class="muted" style="font-size:12px">Your most typical take, with your skeleton on top and dotted trails for what it's judged on (${esc(SCOPE_LABEL[g.scope_effective].toLowerCase())}). Hover to play it.</span>`}${creditLine(g.photo)}`
        : g.real ? `<div class="frame" style="border-radius:10px;max-width:520px">${realMoveSvg(g.real)}</div><span class="muted" style="font-size:12px">Your recording: faint where you started, solid at the furthest point, dotted trails for what it's judged on (${esc(SCOPE_LABEL[g.scope_effective].toLowerCase())}).</span>`
        : g.move ? `<div class="frame" style="border-radius:10px;max-width:520px">${moveSvg(g.move)}</div><span class="muted" style="font-size:12px">Your average take: faint figure where it starts, solid where it ends, dotted trails for the joints that travel.</span>` : `<p class="muted">Not recorded yet.</p>`}
      <div class="takes">${takes.map((t, i) => `<div class="take">${g.take_photos?.[i] ? `<div class="take-photo">${photoSvg(peakRec(g.take_photos[i]), { w: 120, h: 120, trails: false, label: `Take ${i + 1}` })}</div>` : g.take_real?.[i] ? `<div style="height:96px">${realMoveSvg(g.take_real[i], "#4fd1a5", 96, 96)}</div>` : spark(t, "#4fd1a5", 100, 36)}<div class="row between" style="font-size:12px"><span>Take ${i + 1}</span><button class="small ghost" data-act="remove-take" data-id="${g.id}" data-i="${i}">Remove</button></div></div>`).join("")}</div></section>`;
  }
  const takes = g.takes || [];
  const n = takes[0]?.length || 24;
  const W = 720, H = 200;
  const max = Math.max(0.01, ...takes.flat());
  const path = (vals) => vals.map((v, i) => `${(i / (n - 1)) * W},${H - 10 - (v / max) * (H - 30)}`).join(" ");
  const lines = takes.map((t, i) => `<polyline points="${path(t)}" fill="none" stroke="${color}" stroke-opacity="${(g.take_match?.[i] ?? 1) < 0.5 ? 0.9 : 0.45}" stroke-width="2" ${(g.take_match?.[i] ?? 1) < 0.5 ? 'stroke-dasharray="6 6"' : ""}/>`).join("");
  const consistency = g.calibration ? Math.round(Math.max(0, 1 - g.calibration.spread) * 100) : null;
  return `<section class="panel">
    <h2><span>Takes <span class="muted" style="font-weight:400;font-size:12px">${takes.length} recorded${consistency !== null ? ` · they agree ${consistency}%` : ""}</span></span>
      <span class="toolbar"><button class="small" data-act="capture-for" data-id="${g.id}">● Add takes</button>${takes.length ? `<button class="small ghost" data-act="rerecord" data-id="${g.id}">Re-record all</button>` : ""}</span></h2>
    ${g.photo ? `<div class="frame big-photo">${photoSvg(g.photo, { w: 520, h: 292, all: true, color })}</div>${g.photo.credit ? SAMPLE_NOTE : `<span class="muted" style="font-size:12px">Your most typical take, the dotted trail is your ${esc(g.source === "bow" ? "bow" : "weapon")} hand. Hover to play it.</span>`}${creditLine(g.photo)}` : ""}
    ${takes.length ? `<div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:200px" role="img" aria-label="Your takes overlaid"><path d="M0 ${H / 2}H${W}" stroke="#1a1f29"/>${lines}</svg>
      <div class="legend" style="padding-top:6px"><span><i style="background:${color}"></i>Your takes (weapon rotation speed)</span><span><i style="background:${color};opacity:.9"></i>Dashed = odd one out</span></div></div>
    <div class="takes">${takes.map((t, i) => { const m = g.take_match?.[i] ?? 1; const odd = m < 0.5; return `<div class="take ${odd ? "odd" : ""}">${g.take_photos?.[i] ? `<div class="take-photo">${photoSvg(peakRec(g.take_photos[i]), { w: 120, h: 120, trails: false, color, label: `Take ${i + 1}` })}</div>` : ""}${spark(t, color, 100, 36, odd)}<div class="row between" style="font-size:12px"><span style="${odd ? "color:var(--warn)" : ""}">${odd ? "Odd one out" : `Take ${i + 1}`}</span>${odd ? `<button class="small" data-act="remove-take" data-id="${g.id}" data-i="${i}">Remove</button>` : `<span style="color:var(--ok)">${m.toFixed(2)}</span>`}</div></div>`; }).join("")}</div>`
    : `<p class="muted">Not recorded yet.</p>`}
  </section>`;
}

function activityPanel(g) {
  const rows = S.activity.filter((a) => a.gid === g.id).slice(-12).reverse();
  return `<section class="panel"><h2>Activity</h2>${rows.length ? `<ol class="log">${rows.map((a) => `<li><span class="t">${fmtTime(a.t)}</span><span class="${a.sent ? "sent" : a.outcome.startsWith("near") ? "near" : a.outcome.startsWith("held") ? "held" : ""}">${esc(a.outcome)}${a.sent && a.key ? ` ${esc(a.key)}` : ""}</span><span>${esc(a.detail || "")}</span></li>`).join("")}</ol>` : `<p class="muted">Nothing yet this session.</p>`}</section>`;
}

function histogram(hist) {
  if (!hist || !hist.length) return `<p class="muted" style="font-size:12px">The chart fills in as you play: each move you make lands somewhere on it.</p>`;
  const bins = 16, maxR = 2.4, w = 320, h = 110;
  const counts = { fired: Array(bins).fill(0), near: Array(bins).fill(0), other: Array(bins).fill(0) };
  for (const [r, o] of hist) { const b = Math.min(bins - 1, Math.floor((r / maxR) * bins)); (counts[o === "fired" ? "fired" : o === "near" ? "near" : "other"])[b]++; }
  const top = Math.max(1, ...Object.values(counts).flatMap((c) => c));
  const bw = (w - 20) / bins;
  let bars = "";
  for (let b = 0; b < bins; b++) {
    let y = h - 10;
    for (const [k, col] of [["fired", "#4fd1a5"], ["near", "#f5b544"], ["other", "#4a5268"]]) {
      const v = counts[k][b]; if (!v) continue;
      const bh = (v / top) * (h - 24); y -= bh;
      bars += `<rect x="${10 + b * bw + 1}" y="${y}" width="${bw - 2}" height="${bh}" rx="2" fill="${col}"/>`;
    }
  }
  const lineX = 10 + (1 / maxR) * (w - 20);
  return `<svg viewBox="0 0 ${w} ${h}" style="height:110px" role="img" aria-label="Where your moves landed relative to the firing line"><path d="M0 ${h - 10}H${w}" stroke="#252a36"/>${bars}<path d="M${lineX} 6V${h - 6}" stroke="#e8eaf0" stroke-width="2" stroke-dasharray="4 4"/></svg>
    <div class="legend"><span><i style="background:#4fd1a5"></i>Fired</span><span><i style="background:#f5b544"></i>Near misses</span><span><i style="background:#4a5268"></i>Your other moves</span><span>Line = firing threshold</span></div>`;
}

function firingPanel(g, st) {
  const cal = g.calibration;
  const modeSeg = g.kind === "pose" && g.action !== "arm_toggle" ? `<div class="seg" role="radiogroup" aria-label="When it fires"><button class="${g.mode !== "hold" ? "on" : ""}" data-act="gmode" data-id="${g.id}" data-v="tap">Once per hold</button><button class="${g.mode === "hold" ? "on" : ""}" data-act="gmode" data-id="${g.id}" data-v="hold">Hold key while held</button></div>` : "";
  const calNote = cal ? (cal.margin !== null && cal.margin < 1.2
      ? `<div class="note warn">This move is close to <b>${esc(cal.nearest)}</b>. Make them more different, or record moves to ignore. (margin ${cal.margin})</div>`
      : `<div class="note ok">Calibrated from your takes${cal.with_camera ? " and hand paths" : ""}${cal.nearest ? ` · nearest other move: ${esc(cal.nearest)} (margin ${cal.margin})` : ""}.</div>`) : "";
  const sens = g.kind === "motion" || g.kind === "pose" ? `<label class="inline" style="font-size:12px;color:#a7aec0">Strict<input type="range" min="0.5" max="2" step="0.05" value="${g.sensitivity ?? 1}" data-change="gfield-num" data-k="sensitivity" style="flex:1" aria-label="Sensitivity">Loose</label>` : "";
  const camNote = g.kind === "motion" && g.count ? (g.uses_camera
      ? `<div class="note ok">Uses the camera too: ${g.camera_takes} of ${g.count} takes have your hand's path.${g.twins?.length ? ` The gyro alone can't tell it from <b>${g.twins.map(esc).join(", ")}</b>, so without a view of your hand it's held back rather than guessed.` : ""}</div>`
      : `<div class="note info">Gyro only${g.camera_takes ? ` (${g.camera_takes} of ${g.count} takes have a hand path)` : ""}. Record takes with the webcam on to add where your hand is.</div>`) : "";
  return `<section class="panel"><h2>Firing</h2>${modeSeg}${camNote}
    ${g.kind === "motion" ? `<div class="stack"><b style="font-size:13px">How close a move has to be</b>${histogram(g.history)}</div>` : ""}
    ${sens}${calNote}
    <div class="stats"><div><b>${st.fired}</b><span>fired</span></div><div><b style="color:var(--warn)">${st.near}</b><span>near misses</span></div><div><b>${g.count}</b><span>${g.kind === "pose" ? "frames" : "takes"}</span></div></div>
    ${g.action !== "arm_toggle" ? `<button class="ghost small" data-act="test" data-id="${g.id}">Test key (fires in 3 s)</button>` : ""}</section>`;
}

function tooltipPanel(g) {
  const t = g.tooltip || { type: null, steps: [] };
  const steps = t.steps || [];
  const opts = [["", `Library default (${S.settings.tooltip_type})`], ["How to do it", "How to do it"], ["Spell card", "Spell card"], ["Recording tips", "Recording tips"], ["Off", "Off"]];
  return `<section class="panel"><h2>Tooltip <select data-change="gtooltip-type" aria-label="Tooltip type">${opts.map(([v, l]) => `<option value="${esc(v)}" ${(t.type || "") === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></h2>
    <label class="field">Casts (for the Spell card)<input type="text" data-change="gfield" data-k="casts" value="${esc(g.casts || "")}" placeholder="Optional"></label>
    <label class="field">Step 1<input type="text" data-change="gstep" data-i="0" value="${esc(steps[0] || "")}" placeholder="${esc(DEFAULT_HOW[g.kind]?.[0] || "")}"></label>
    <label class="field">Step 2<input type="text" data-change="gstep" data-i="1" value="${esc(steps[1] || "")}" placeholder="${esc(DEFAULT_HOW[g.kind]?.[1] || "")}"></label>
    <span class="muted" style="font-size:12px">Preview</span>
    <div style="position:relative;min-height:110px">${tooltipHtml(g).replace('class="tip', 'style="display:flex;position:static" class="tip')}</div></section>`;
}

// ------------------------------------------------------------------ engine page

function enginePage() {
  const s = S.settings;
  const devCards = S.devices.map(deviceCard).join("") || `<article class="panel"><b>Weapons</b><p class="soft">No Joy-Cons connected. Hold the small sync button on each Joy-Con's rail until the lights run, then connect them in System Settings → Bluetooth. Joy-Con (L) becomes the bow, (R) the melee weapon; you can swap that here.</p></article>`;
  const neutral = ui.neutral;
  const neutralStatus = neutral ? (neutral.phase === "countdown" ? `Starting in ${Math.ceil(neutral.remaining)}… stand normally` : neutral.phase === "capture" ? `Recording · ${Math.ceil(neutral.remaining)} s left · ${neutral.count || 0} frames` : `Got ${neutral.count} frames`) : `${S.neutral_count} frames recorded`;
  const sw = (k, title, sub, beta) => `<label class="switch-row"><span><b>${title}${beta ? `<span class="pill">beta</span>` : ""}</b><small>${sub}</small></span><input type="checkbox" role="switch" data-change="setting-bool" data-k="${k}" ${s[k] ? "checked" : ""}></label>`;
  const profiles = (S.profile.all || []).map((p) => `<option value="${esc(p.path)}" ${p.path === S.profile.path ? "selected" : ""}>${esc(p.name)} (${p.gestures})</option>`).join("");
  return `
  <div class="toolbar"><div class="chips grow" id="chips">${chipsHtml()}</div></div>
  <div class="head"><div class="lead"><h1>Engine</h1><p class="soft">What it listens to, what it recognizes, and what it's allowed to press.</p></div></div>
  <section class="stack"><span class="eyebrow">Inputs</span><div class="inputs">
    <article class="panel"><h2><span><i class="dot pose"></i> Webcam</span><span class="muted" style="font-size:12px;font-weight:400">${S.camera.ok ? `${S.camera.fps} fps` : "off"}</span></h2>
      <div class="stage"><div class="mirror"><img id="preview" alt=""><canvas id="skeleton"></canvas></div><span class="msg" id="camera-msg">${esc(S.camera.ok ? "" : S.camera.message)}</span></div>
      <div class="checks" id="checks">${checksHtml()}</div>
      <div class="row between"><span class="soft">Neutral · <span id="neutral-status">${esc(neutralStatus)}</span></span><span class="toolbar">${neutral && neutral.phase !== "done" ? `<button class="small" data-act="neutral-cancel">Cancel</button>` : `<button class="small" data-act="neutral">● Record 6 s</button>`}${S.neutral_count && !(neutral && neutral.phase !== "done") ? `<button class="small ghost" data-act="clear-neutral">Clear</button>` : ""}</span></div></article>
    ${devCards}</div></section>
  <div class="cols">
    <div class="wide">
      <section class="panel"><h2>Recognizers</h2>
        <div class="switch-row"><span><b>Poses</b><small>Webcam. Matches your body against your recordings, measured from the shoulders so distance doesn't matter.</small></span><span class="muted" style="font-size:12px">always on</span></div>
        <div class="switch-row"><span><b>Weapon motions</b><small>Bow and melee. Matched by direction at any speed. Locked out for ${s.lockout_ms} ms after a hit so the return stroke can't fire.</small></span><span class="muted" style="font-size:12px">always on</span></div>
        ${sw("body_moves", "Body moves", "Webcam only, no weapon: a kick, a duck, a two-handed shove. Matched on where your hands, feet and body went. A body move made while a weapon motion fires is left to the weapon.")}
        ${sw("jog_enabled", "Jog in place", "Feet or knees take turns lifting → holds a key down.", true)}
        ${sw("hop_enabled", "Hop", "Hips rise and land quickly → one key press.", true)}
        ${sw("clash_capture", "Weapon clash", "Tap the shafts together (not the tips, where the Joy-Cons are) to open quick capture.")}
      </section>
      <section class="panel"><h2>Camera + gyro <span class="muted" style="font-size:12px;font-weight:400">${S.fusion.camera_recent ? `camera delay ${S.fusion.lag_ms} ms${S.fusion.lag_learned ? " (learned)" : " (default)"}` : "camera not seeing you"}</span></h2>
        ${sw("fusion", "Match motions on gyro + hand position", "The gyro decides alone when it's sure; otherwise it waits a moment for the camera. Lets the same swing at head height and at the waist be two moves.")}
        ${slider("fusion_wait_ms", "Longest wait for the camera", 50, 300, 10, "ms")}
        ${sw("camera_bias", "Recalibrate the gyro when you're still", "When the camera sees your weapon hand hold still, the gyro's reading is its drift. Works even for drift too big for the gyro to notice.")}
        ${sw("auto_align", "Fix a slipped Joy-Con from your moves", "Moves recognized with the camera agreeing show if the Joy-Con turned on the pipe; it's corrected a few degrees at a time.")}
        ${sw("metrics", "Keep a play log for Metrics", "Counts what fired and why, on this computer only.")}
      </section>
      <section class="panel"><h2>Timing</h2>
        ${slider("lockout_ms", "Lockout after a weapon hit", 100, 700, 25, "ms")}
        ${slider("pose_hold_ms", "Pose hold time", 100, 800, 25, "ms")}
        ${slider("capture_takes", "Takes per capture", 3, 8, 1, "")}
        <label class="field">How capture works<select data-change="setting" data-k="capture_style">
          <option value="countdown" ${S.settings.capture_style !== "free" ? "selected" : ""}>Countdown takes: 3-2-1, record, repeat</option>
          <option value="free" ${S.settings.capture_style === "free" ? "selected" : ""}>Just do it: it finds each take by itself</option></select></label>
        ${S.settings.capture_style !== "free" ? slider("capture_countdown_s", "Countdown before each take", 1, 5, 1, "s") + slider("capture_window_s", "Recording time per take", 1.5, 5, 0.5, "s") : ""}
      </section>
    </div>
    <div class="narrow">
      <section class="panel"><h2>Profiles</h2>
        <label class="field">Active<select data-change="profile">${profiles}</select></label>
        <label class="field">Rename<input type="text" data-change="profile-rename" value="${esc(S.profile.name)}"></label>
        <div class="row wrap"><input type="text" id="new-profile" placeholder="New profile name (e.g. Marksmanship)" style="flex:1;min-width:160px">
          <button class="small" data-act="profile-new">New</button><button class="small ghost" data-act="profile-copy">Copy current</button></div></section>
      <section class="panel"><h2>Outputs &amp; safety</h2>
        <div class="note ${S.keys_ok ? "ok" : "warn"}">${S.dry_run ? "Dry run: nothing is sent." : S.keys_ok ? "Key output allowed (Accessibility)." : `Key output blocked: allow ${esc(S.permission_app || "your terminal app")} in Accessibility.`}</div>
        <div class="note ${S.hotkeys.ok ? "ok" : "info"}">${esc(S.hotkeys.message)}</div>
        <label class="switch-row"><span><b>Only while WoW is in front</b><small>Keys are held back when another app is in front.</small></span><input type="checkbox" role="switch" data-change="setting-bool" data-k="require_wow_front" ${s.require_wow_front ? "checked" : ""}></label>
        <label class="field">WoW app name<input type="text" data-change="setting" data-k="wow_app" value="${esc(s.wow_app)}"></label>
        <label class="switch-row"><span><b>Buzz on hits</b><small>The weapon rumbles when it recognizes a motion.</small></span><input type="checkbox" role="switch" data-change="setting-bool" data-k="rumble" ${s.rumble ? "checked" : ""}></label>
        <label class="switch-row"><span><b>Flash what fired</b><small>Shows the gesture and key in the overlay.</small></span><input type="checkbox" role="switch" data-change="setting-bool" data-k="flash_on_fire" ${s.flash_on_fire ? "checked" : ""}></label>
        <div class="note info"><b>One gesture = one key press.</b> The engine never sends key sequences or repeats, and never reads the game.</div>
      </section>
    </div>
  </div>`;
}

function slider(k, label, min, max, step, unit) {
  const v = S.settings[k];
  return `<label class="field">${label}<span class="row"><input type="range" min="${min}" max="${max}" step="${step}" value="${v}" data-change="setting-num" data-k="${k}" data-unit="${unit}" style="flex:1"><output style="min-width:60px;text-align:right">${v}${unit ? " " + unit : ""}</output></span></label>`;
}

function deviceCard(d) {
  const c = d.calibrated;
  const ign = S.ignore_counts[d.role] || 0;
  return `<article class="panel"><h2><span><i class="dot ${d.role || ""}"></i> ${esc(cap(d.role || "Unassigned"))} · ${esc(d.name)}</span><span class="muted" style="font-size:12px;font-weight:400">${d.rate} Hz${d.info?.battery && d.info.battery !== "unknown" ? ` · ${esc(d.info.battery)}` : ""}${d.info?.simulated ? " · simulated" : ""}</span></h2>
    <label class="inline">Role <select data-change="role" data-device="${esc(d.name)}">${["bow", "melee"].map((r) => `<option value="${r}" ${d.role === r ? "selected" : ""}>${cap(r)}</option>`).join("")}</select></label>
    <div class="stack"><span class="muted" style="font-size:12px">Motion right now · a move starts at the yellow line</span><div class="row"><div class="meter ${d.role || ""}" id="meter-${cssId(d.name)}"><i></i><b style="left:${Math.min(100, (d.trigger / 12) * 100)}%"></b></div></div></div>
    <div class="checks"><span class="${c.bias ? "ok" : ""}">Gyro bias ${c.bias ? "✓" : "–"}</span><span class="${c.mount ? "ok" : ""}">Mount ${c.mount ? "✓" : "–"}</span><span class="${c.trigger ? "ok" : ""}">Trigger ${c.trigger ? d.trigger : "default"}</span></div>
    <div class="row wrap" style="font-size:12px"><label class="inline">Held in <select data-change="hand" data-device="${esc(d.name)}">${[["auto", `Auto${d.hand_found ? ` (${d.hand_found})` : ""}`], ["left", "Left hand"], ["right", "Right hand"]].map(([v, l]) => `<option value="${v}" ${d.hand === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <span class="muted">Drift ${d.bias_dps}°/s · mount auto-fix ${d.auto_align_deg}°</span></div>
    <div class="row wrap"><button class="small" data-act="calibrate" data-device="${esc(d.name)}">Calibrate (10 s)</button><span class="muted" style="font-size:12px">Re-run after remounting the Joy-Con: your recordings keep working.</span></div>
    <div class="row between"><span class="muted" style="font-size:12px">${ign ? `${ign} moves marked "ignore"` : "Moves to ignore: none yet"}</span>${ign ? `<button class="small ghost" data-act="clear-ignore" data-role="${d.role}">Clear</button>` : ""}</div>
  </article>`;
}
const cssId = (s) => String(s).replace(/[^a-zA-Z0-9]/g, "_");

function checksHtml() {
  const lm = live.pose?.lm;
  const vis = (i) => lm && lm[i][2] > 0.5;
  const items = [["Shoulders", vis(11) && vis(12)], ["Arms", vis(13) && vis(14) && vis(15) && vis(16)], ["Hips", vis(23) && vis(24)], ["Knees", vis(25) && vis(26)]];
  return items.map(([n, ok]) => `<span class="${ok ? "ok" : ""}">${n} ${ok ? "✓" : "–"}</span>`).join("");
}

// ------------------------------------------------------------------ HUD / overlay

function hudPage() {
  const overlay = route.page === "overlay";
  const c = S.capture;
  const toast = S.last_captured && Date.now() < ui.toastUntil ? S.gestures.find((g) => g.id === S.last_captured) : null;
  const fired = S.activity.filter((a) => a.sent || a.outcome === "near miss").slice(-6).reverse();
  const near = S.near && Date.now() / 1000 - S.near.t < 20 ? S.near : null;
  const meter = (label, role, id) => `<div class="row" style="font-size:12px"><i class="dot ${role}"></i><span style="width:52px;color:#a7aec0">${label}</span><div class="meter ${role === "pose" ? "webcam" : role}" id="${id}"><i></i>${role !== "pose" ? `<b style="left:25%"></b>` : ""}</div></div>`;
  const devMeters = S.devices.map((d) => meter(cap(d.role || "?"), d.role || "", `meter-${cssId(d.name)}`)).join("");
  return `<div class="hud-wrap"><aside class="hud ${ui.compact ? "compact" : ""}" aria-label="Live HUD">
    <div class="row">${ICONS.logo}<span style="flex:1;display:flex;flex-direction:column;line-height:1.15"><b>${esc(S.profile.name)}</b><small class="muted">${S.gestures.length} gestures</small></span>
      <button class="arm-pill ${S.armed ? "on" : ""}" data-act="arm">${S.armed ? "ARMED" : "DISARMED"}</button>
      ${overlay ? `<button class="small ghost" data-act="compact" aria-label="${ui.compact ? "Expand" : "Collapse"}">${ui.compact ? "▾" : "▴"}</button><button class="small ghost" data-act="overlay-hide" aria-label="Hide the HUD (show it again from the ⚔ menu)" title="Hide (⚔ menu → HUD over the game brings it back)">✕</button>` : ""}</div>
    ${overlay ? "" : `<a href="#/library" class="small" style="align-self:flex-start">← Back to the app</a>`}
    <div class="hud-body stack" style="gap:14px">
    ${S.drill ? drillHtml(S.drill, true) : c ? captureHtml(c, true) : `
      ${toast ? `<div class="toast" role="status"><b style="font-size:13px">✓ Saved: ${esc(toast.name)} → ${esc(toast.key_label)} is live</b><div class="row"><button class="small" data-act="undo">Undo</button>${overlay ? "" : `<a class="small" href="#/gesture/${toast.id}">Tune it</a>`}<button class="small ghost" data-act="toast-close" aria-label="Close">✕</button></div></div>` : ""}
      <div class="panel" style="padding:12px 14px;gap:10px">${meter("Webcam", "pose", "meter-webcam")}${devMeters}</div>
      <div class="stack" style="gap:6px"><span class="eyebrow">Just fired</span>
        <ol class="fired">${fired.map((a, i) => `<li class="${i === 0 && a.sent ? "top" : ""} ${a.sent ? "" : "dim"}"><span class="key" style="${a.sent ? "" : "border-style:dashed;color:var(--muted)"}">${esc(a.key && a.key !== "—" ? a.key : "·")}</span><span class="n"><b style="font-size:13px">${esc(a.gesture)}</b><small>${esc(a.casts ? a.casts + " · " : "")}${esc(a.sent ? a.detail : a.detail)}</small></span><small>${fmtTime(a.t)}</small></li>`).join("") || `<li class="dim"><small>Nothing yet. Arm it and make a move.</small></li>`}</ol></div>
      ${S.last_fire && Date.now() / 1000 - S.last_fire.t < 15 ? `<div class="row between" style="font-size:12px"><span class="muted">Last: <b class="soft">${esc(S.last_fire.name)}</b></span><button class="small ghost" data-act="wrong" title="That fired but you didn't mean it: count it as wrong and ignore that move from now on">✗ Wasn't me</button></div>` : ""}
      ${near ? `<div class="note warn stack"><span><b>${esc(near.name)}</b> almost fired (${near.match}). Was that meant to be it?</span><div class="row"><button class="small" data-act="near" data-v="loosen">Yes, loosen it</button><button class="small ghost" data-act="near" data-v="ignore">No, ignore that move</button><button class="small ghost" data-act="near" data-v="dismiss">Not now</button></div></div>` : ""}
      <div class="stack muted" style="font-size:12px;gap:4px;border-top:1px solid #1e2330;padding-top:10px"><span><b class="soft">Tap the shafts together</b> or ⌥C to capture a new move</span><span>${esc(armHint())}</span></div>`}
    </div></aside></div>`;
}

// ------------------------------------------------------------------ modals

function renderModal() {
  const el = $("#modal");
  if (route.page === "overlay" || route.page === "hud") { setHTML(el, ""); return; }
  if (S.capture && S.capture.phase === "perform" && S.capture.style === "countdown") { captureStage(el, S.capture); return; }
  if (S.capture) { setHTML(el, `<div class="modal-back"><section class="modal" aria-label="Quick capture">${captureHtml(S.capture, false)}</section></div>`); return; }
  if (S.drill) { setHTML(el, `<div class="modal-back"><section class="modal" aria-label="Accuracy test">${drillHtml(S.drill, false)}</section></div>`); return; }
  if (S.calibration || ui.calibDone) { setHTML(el, `<div class="modal-back"><section class="modal" aria-label="Calibrate">${calibHtml()}</section></div>`); return; }
  setHTML(el, "");
}

// ---------------------------------------------------------------- capture, like recording software

const KIND_LINE = (c) => c.kind === "motion" ? `<i class="dot ${c.source}"></i><b>${cap(c.source)} motion</b>`
  : c.kind === "move" ? `<i class="dot pose"></i><b>Body move</b> · webcam only, weapons still`
  : c.kind === "pose" ? `<i class="dot pose"></i><b>Pose</b> · get into it and hold it while it records`
  : `<i class="dot warn"></i><b>Swing a weapon, do a body move, or hold a pose</b> · it works out which`;

function takeOverlay(c) {
  const left = c.take_remaining;
  if (c.take_phase === "countdown") return `<div class="cap-count" key="${Math.ceil(left)}">${Math.max(1, Math.ceil(left))}</div><div class="cap-sub">Get ready</div>`;
  if (c.take_phase === "recording") {
    const done = c.take_length ? 1 - left / c.take_length : 0;
    return `${done < 0.22 ? `<div class="cap-count go">GO!</div>` : ""}<div class="cap-rec"><i></i>REC ${left.toFixed(1)} s</div><div class="cap-bar"><i style="width:${Math.round(done * 100)}%"></i></div>`;
  }
  if (c.take_phase === "checking") return `<div class="cap-sub">Checking…</div>`;
  if (c.take_phase === "review") return `<div class="cap-result ${c.take_message.startsWith("✓") ? "ok" : "again"}">${esc(c.take_message)}</div>`;
  return "";
}

function captureStage(el, c) {
  if (!el.querySelector(".capture-shell")) {
    setHTML(el, `<div class="modal-back center"><section class="modal wide capture-shell" aria-label="Capture">
      <div id="cap-head"></div>
      <div class="stage cap-stage"><div class="mirror"><img class="preview-img" alt=""><canvas class="skel-canvas"></canvas></div>
        <div class="cap-over" id="cap-over" aria-live="polite"></div>
        <span class="cap-nocam">${S.camera.ok ? "" : esc(S.camera.message || "Webcam off")}</span></div>
      <div class="cap-strip" id="cap-strip" aria-label="Your takes"></div>
      <div id="cap-body" class="stack" style="gap:12px"></div></section></div>`);
    drawSkeleton();
  }
  const trig = { clash: "Started by a weapons clash", hotkey: "Started with ⌥C", ui: "Started from the app" }[c.trigger] || "";
  setHTML($("#cap-head"), `<div class="modal-title"><div class="stack" style="gap:2px"><div class="row" style="gap:10px">${ICONS.capture.replace('stroke="currentColor"', 'stroke="#ff6b3d"')}<b>${c.editing ? "Add takes" : "Capture"}</b><span class="muted" style="font-size:13px">Take ${Math.min(c.count + 1, c.kind === "pose" ? 1 : c.target)} of ${c.kind === "pose" ? 1 : c.target}</span></div><span class="muted" style="font-size:12px">${esc(trig)}</span></div><button class="small ghost" data-act="capture-cancel">Cancel</button></div>`);
  setHTML($("#cap-over"), takeOverlay(c));
  setHTML($("#cap-strip"), takeStrip(c));
  $(".cap-stage").classList.toggle("recording", c.take_phase === "recording");
  const target = c.kind === "pose" ? 1 : c.target;
  const finish = (c.kind === "motion" || c.kind === "move") && c.count >= 3 && c.count < c.target ? `<button data-act="capture-finish">Finish with ${c.count} takes →</button>` : "";
  setHTML($("#cap-body"), `<div class="row between wrap"><span class="row" style="gap:8px;font-size:13px">${KIND_LINE(c)}</span>
      <div class="dots">${Array.from({ length: target }, (_, i) => `<i class="${i < c.count ? "on" : ""}"></i>`).join("")}</div></div>
    ${c.kind ? "" : `<div class="row wrap"><button data-act="capture-pose">It's a pose →</button><button data-act="capture-move">It's a body move →</button></div>`}
    <div class="row between" style="border-top:1px solid #1e2330;padding-top:12px"><span class="muted" style="font-size:12px">Each take: 3-2-1, then ${S.settings.capture_window_s} s of recording. Nothing fires while capturing. Esc cancels.</span>${finish}</div>`);
}

// The takes you've kept so far, like the take list in recording software.
function takeStrip(c) {
  const target = c.kind === "pose" ? 1 : c.target;
  const ph = c.take_photos || [];
  return Array.from({ length: Math.max(target, ph.length) }, (_, i) => {
    const p = ph[i], done = i < c.count || (c.kind === "pose" && ph.length);
    const color = c.kind === "motion" ? SOURCE_COLOR[c.source] : "#4fd1a5";
    return `<div class="cap-take ${done ? "done" : ""} ${done && i === c.count - 1 && c.take_phase === "review" ? "fresh" : ""}">${p ? photoSvg(peakRec(p), { w: 96, h: 96, trails: false, color, label: `Take ${i + 1}` }) : `<span>${done ? "✓" : i + 1}</span>`}<small>Take ${i + 1}</small></div>`;
  }).join("");
}

function captureHtml(c, inHud) {
  const stepCls = (i) => (c.phase === "perform" ? (i === 0 ? "on" : "") : i === 0 ? "done" : i === 1 ? "on" : "");
  const steps = `<ol class="steps"><li class="${stepCls(0)}"><span class="n">${c.phase === "bind" ? "✓" : "1"}</span>Perform</li><li class="${stepCls(1)}"><span class="n">2</span>Bind key</li><li><span class="n">3</span>Live</li></ol>`;
  const trig = { clash: "Started by a weapons clash", hotkey: "Started with ⌥C", ui: "Started from the app" }[c.trigger] || "";
  const title = `<div class="modal-title"><div class="stack" style="gap:2px"><div class="row" style="gap:10px">${ICONS.capture.replace('stroke="currentColor"', 'stroke="#ff6b3d"')}<b>${c.editing ? "Add takes" : "Quick capture"}</b></div><span class="muted" style="font-size:12px">${esc(trig)}</span></div><button class="small ghost" data-act="capture-cancel">Cancel</button></div>`;
  if (c.phase === "perform") {
    let body;
    if (!c.ready) {
      body = `<div class="countdown">${Math.ceil(c.ready_in)}</div><p class="soft" style="text-align:center">Get ready…</p>`;
    } else if (c.style === "countdown") {
      body = `<div class="cap-hud ${c.take_phase === "recording" ? "recording" : ""}">${takeOverlay(c)}</div>
        <div class="row between"><span class="row" style="gap:8px;font-size:12px">${KIND_LINE(c)}</span><div class="dots">${Array.from({ length: c.kind === "pose" ? 1 : c.target }, (_, i) => `<i class="${i < c.count ? "on" : ""}"></i>`).join("")}</div></div>`;
    } else if (c.kind === "pose") {
      body = `<div class="note info">Hold the pose still.</div><div class="meter webcam" style="height:10px"><i style="width:${Math.round(c.pose_progress * 100)}%"></i></div><p class="soft">${c.pose_progress > 0 ? "HOLD IT" : "Get into the pose…"}</p>`;
    } else if (c.kind === "motion") {
      const color = SOURCE_COLOR[c.source];
      const n = (c.traces[0] || []).length || 24, W = 448, H = 150;
      const max = Math.max(0.01, ...c.traces.flat());
      const lines = c.traces.map((t, i) => `<polyline points="${t.map((v, j) => `${(j / (n - 1)) * W},${H - 8 - (v / max) * (H - 20)}`).join(" ")}" fill="none" stroke="${color}" stroke-opacity="${i === c.traces.length - 1 ? 1 : 0.35}" stroke-width="${i === c.traces.length - 1 ? 4 : 2}"/>`).join("");
      body = `<div class="note info row"><i class="dot ${c.source}"></i><span style="flex:1"><b>${cap(c.source)}</b> · detected from the first take</span><span class="pill">motion</span></div>
        <div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:150px" role="img" aria-label="Your takes so far">${lines}</svg></div>
        <div class="row between"><div class="big-count"><b>${c.count}</b><span class="muted">of ${c.target} takes</span></div><div class="dots ${c.source}">${Array.from({ length: c.target }, (_, i) => `<i class="${i < c.count ? "on" : ""}"></i>`).join("")}</div></div>
        <p class="soft">${esc(c.message || (c.count ? "Again. Pause between takes." : "Do the move!"))}</p>`;
    } else if (c.kind === "move") {
      const n = (c.traces[0] || []).length || 16, W = 448, H = 150;
      const max = Math.max(0.01, ...c.traces.flat());
      const lines = c.traces.map((t, i) => `<polyline points="${t.map((v, j) => `${(j / Math.max(1, t.length - 1)) * W},${H - 8 - (v / max) * (H - 20)}`).join(" ")}" fill="none" stroke="#4fd1a5" stroke-opacity="${i === c.traces.length - 1 ? 1 : 0.35}" stroke-width="${i === c.traces.length - 1 ? 4 : 2}"/>`).join("");
      body = `<div class="note info row"><i class="dot pose"></i><span style="flex:1"><b>Body move</b> · webcam only</span><span class="pill">move</span></div>
        <div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:150px" role="img" aria-label="How fast you moved in each take">${lines}</svg></div>
        <div class="row between"><div class="big-count"><b>${c.count}</b><span class="muted">of ${c.target} takes</span></div><div class="dots">${Array.from({ length: c.target }, (_, i) => `<i class="${i < c.count ? "on" : ""}"></i>`).join("")}</div></div>
        <p class="soft">${esc(c.message || (c.count ? "Again. Settle for a second between takes." : "Do the move! Keep the weapons still."))}</p>`;
    } else {
      body = `<div class="note info">Do the move with either weapon, get into a pose and hold it, or do a body move with the weapons still. The engine works out which.</div><div class="countdown" style="font-size:40px">Go!</div>
        <div class="row"><button data-act="capture-pose">It's a pose →</button><button data-act="capture-move">It's a body move →</button></div>`;
    }
    const finish = (c.kind === "motion" || c.kind === "move") && c.count >= 3 && c.count < c.target ? `<button data-act="capture-finish">Finish with ${c.count} takes →</button>` : "";
    return `${title}${steps}${body}<div class="row between" style="border-top:1px solid #1e2330;padding-top:14px"><span class="muted" style="font-size:12px">Nothing fires while capturing.</span>${finish}</div>`;
  }
  // bind
  const listening = !c.key;
  return `${title}${steps}
    <div class="note info row"><i class="dot ${c.kind === "motion" ? c.source : "pose"}"></i><span style="flex:1"><b>${c.count} ${c.kind === "pose" ? "frames" : "takes"}</b> · ${esc(c.kind === "motion" ? cap(c.source) + " motion" : c.kind === "move" ? "Body move" : "Pose")}${c.kind === "move" && c.auto_scope ? ` · judged on: <b>${esc(SCOPE_LABEL[c.auto_scope].toLowerCase())}</b> (what moved; change it on its page)` : ""}</span></div>
    ${(c.take_photos || []).some(Boolean) ? `<div class="cap-strip">${takeStrip(c)}</div>` : ""}
    <div class="keybox"><div class="row between"><b>Key</b>${listening ? `<span style="font-size:12px;color:#ff9a76">● Listening</span>` : ""}</div>
      <span class="soft" style="font-size:13px">Press the key this move should send: the one you bound in WoW.${inHud ? " (Works while WoW has focus.)" : ""}</span>
      <div class="row">${c.key ? `<span class="key huge">${esc(c.key_label)}</span>` : `<span class="key huge listening">…</span>`}</div>
      ${c.conflict ? `<span style="font-size:12px;color:var(--warn)">Already used by ${esc(c.conflict)}.</span>` : c.key ? `<span style="font-size:12px;color:var(--ok)">✓ Not used by another gesture. Press another key to change it.</span>` : ""}</div>
    <div class="row wrap" style="align-items:stretch"><label class="field" style="flex:1;min-width:140px">Name<input type="text" data-change="cfield" data-k="name" value="${esc(c.name)}"></label>
      <label class="field" style="flex:1;min-width:140px">Casts (optional)<input type="text" data-change="cfield" data-k="casts" value="${esc(c.casts)}" placeholder="Spell name"></label></div>
    <div class="seg" role="radiogroup" aria-label="When it fires"><button class="${c.mode !== "hold" ? "on" : ""}" data-act="cmode" data-v="tap">Once per move</button><button class="${c.mode === "hold" ? "on" : ""}" data-act="cmode" data-v="hold">Hold while held</button></div>
    <label class="field">Tooltip (how to do it)<input type="text" data-change="cfield" data-k="tooltip_text" value="${esc(c.tooltip_text)}" placeholder="e.g. Hold the weapon level, then spin"></label>
    <div class="row between" style="border-top:1px solid #1e2330;padding-top:14px"><button class="ghost" data-act="capture-redo">Redo</button><button class="primary" data-act="capture-save" ${c.key ? "" : "disabled"}>Save and go live</button></div>`;
}

function calibHtml() {
  if (ui.calibDone) {
    const r = ui.calibDone.results || {};
    return `<div class="modal-title"><b>Calibrated</b><button class="small ghost" data-act="calib-close">Done</button></div>
      <div class="note ok stack"><span>Gyro bias: ${r.bias ? r.bias.join(", ") + " rad/s" : "—"}</span><span>Calibration poses ${r.pose_angle ?? "—"}° apart ${r.realigned ? "· re-aligned to your recordings" : "· saved as reference"}</span><span>Trigger strength: ${r.trigger ? r.trigger.toFixed(2) : "default"}</span></div>`;
  }
  const c = S.calibration;
  return `<div class="modal-title"><div class="stack" style="gap:2px"><b>Calibrate ${esc(c.device)}</b><span class="muted" style="font-size:12px">Step ${c.index + 1} of ${c.total}</span></div><button class="small ghost" data-act="calib-cancel">Cancel</button></div>
    <ol class="steps" style="grid-template-columns:repeat(4,minmax(0,1fr))">${["Still", "Forward", "Up", "Guard"].map((n, i) => `<li class="${i < c.index ? "done" : i === c.index ? "on" : ""}"><span class="n">${i < c.index ? "✓" : i + 1}</span>${n}</li>`).join("")}</ol>
    <p style="font-size:18px">${esc(c.instruction)}</p>
    ${c.phase === "countdown" ? `<div class="countdown">${Math.ceil(c.remaining)}</div>` : `<div class="meter" style="height:10px"><i style="width:${Math.max(4, 100 - c.remaining * 25)}%"></i></div><p class="soft">Hold it…</p>`}
    ${c.message ? `<div class="note warn">${esc(c.message)}</div>` : ""}`;
}

function onCapture(m) {
  if (!S) return;
  if (m.phase === "done" || m.phase === "cancelled") { S.capture = null; if (m.message) flash(m.message); }
  else {
    const prev = S.capture;
    if (m.style === "countdown" && prev) {
      const sec = (x) => x.take_phase === "countdown" ? Math.ceil(x.take_remaining) : null;
      if (m.take_phase === "countdown" && sec(m) !== sec(prev)) beep(660, 90, 0.1);
      if (m.take_phase === "recording" && prev.take_phase !== "recording") beep(1320, 200, 0.14);
      if (m.take_phase === "review" && prev.take_phase !== "review") m.take_message.startsWith("✓") ? beep(990, 120, 0.1) : beep(300, 220, 0.1);
    }
    if (prev && m.count > prev.count) beep(1180, 70, 0.1);
    if (prev && !prev.ready && m.ready) beep(990, 160);
    S.capture = m;
  }
  render();
}

function onCalibration(m) {
  if (!S) return;
  if (m.phase === "done") { S.calibration = null; ui.calibDone = m; beep(880, 120); beep(1320, 180, 0.12, 0.12); }
  else S.calibration = m;
  render();
}

// ------------------------------------------------------------------ live data

let previewUrl = null;
function showPreview(blob) {
  const imgs = $$("#preview, img.preview-img");
  if (!imgs.length) return;
  const url = URL.createObjectURL(blob);
  imgs[0].onload = () => { if (previewUrl && previewUrl !== url) URL.revokeObjectURL(previewUrl); previewUrl = url; };
  imgs.forEach((img) => { img.src = url; img.classList.add("live"); });
}

function onPose(m) {
  live.pose = m;
  drawSkeleton();
  const checks = $("#checks"); if (checks) checks.innerHTML = checksHtml();
  const top = Math.max(0, ...Object.values(m.matches || {}));
  const el = $("#meter-webcam i"); if (el) el.style.width = `${Math.round(top * 100)}%`;
}

// The skeleton the webcam sees, drawn over the video wherever there's a stage on screen (Engine
// page, capture). The stage is mirrored like a mirror, so it moves the way you do.
function drawSkeleton() {
  for (const canvas of $$("#skeleton, canvas.skel-canvas")) {
    const img = canvas.id === "skeleton" ? $("#preview") : canvas.parentElement.querySelector("img.preview-img");
    drawSkeletonOn(canvas, img);
  }
}

function drawSkeletonOn(canvas, img) {
  const m = live.pose;
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1, cw = canvas.clientWidth, ch = canvas.clientHeight;
  if (canvas.width !== cw * dpr) { canvas.width = cw * dpr; canvas.height = ch * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  if (!m || !m.lm) return;
  const iw = img?.naturalWidth || 16, ih = img?.naturalHeight || 9;
  const sc = Math.max(cw / iw, ch / ih), ox = (cw - iw * sc) / 2, oy = (ch - ih * sc) / 2;
  const P = (p) => [ox + p[0] * iw * sc, oy + p[1] * ih * sc];
  const big = canvas.classList.contains("skel-canvas");
  ctx.lineWidth = big ? 4 : 3; ctx.lineCap = "round";
  const rec = S?.capture?.take_phase === "recording";
  ctx.strokeStyle = rec ? "rgba(255,107,61,.95)" : m.latched || m.best ? "rgba(79,209,165,.95)" : "rgba(122,162,255,.9)";
  for (const [a, b] of BONES) {
    if (m.lm[a][2] < 0.5 || m.lm[b][2] < 0.5) continue;
    const A = P(m.lm[a]), B = P(m.lm[b]);
    ctx.beginPath(); ctx.moveTo(...A); ctx.lineTo(...B); ctx.stroke();
  }
  if (big) {
    ctx.fillStyle = ctx.strokeStyle;
    for (const i of [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]) {
      if (m.lm[i][2] < 0.5) continue;
      const p = P(m.lm[i]);
      ctx.beginPath(); ctx.arc(p[0], p[1], i === 0 ? 7 : 4.5, 0, Math.PI * 2); ctx.fill();
    }
  }
}

function onMotion(m) {
  live.energy[m.device] = m.energy;
  live.start[m.device] = m.start;
  setMeter(m.device, m.energy, m.start);
}
function setMeter(name, energy, start) {
  const el = $(`#meter-${cssId(name)}`);
  if (!el) return;
  el.querySelector("i").style.width = `${Math.min(100, (energy / 12) * 100)}%`;
  const b = el.querySelector("b"); if (b && start) b.style.left = `${Math.min(100, (start / 12) * 100)}%`;
}

function onEvent(m) {
  if (!S) return;
  S.activity.push(m);
  if (S.activity.length > 60) S.activity.shift();
  if (m.sent && S.settings.flash_on_fire) flash(`<span class="key">${esc(m.key)}</span><b>${esc(m.gesture)}</b>${m.casts ? `<span class="muted">→ ${esc(m.casts)}</span>` : ""}`, true);
  const tile = m.gid && $(`.tile[data-gid="${m.gid}"]`);
  if (tile && m.sent) { tile.classList.add("flash"); setTimeout(() => tile.classList.remove("flash"), 450); }
  if (route.page === "hud" || route.page === "overlay" || (route.page === "gesture" && route.id === m.gid)) render();
}

let flashTimer = null;
function flash(html, isHtml = false) {
  const el = $("#flash");
  el.innerHTML = isHtml ? html : esc(html);
  el.classList.remove("hidden");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => el.classList.add("hidden"), 1300);
}

// ------------------------------------------------------------------ sound

let audio = null;
function unlockAudio() { if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)(); if (audio.state === "suspended") audio.resume(); }
function beep(freq, ms, gain = 0.12, when = 0) {
  if (!audio) return;
  const t = audio.currentTime + when, o = audio.createOscillator(), g = audio.createGain();
  o.frequency.value = freq; g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000);
  o.connect(g).connect(audio.destination); o.start(t); o.stop(t + ms / 1000 + 0.02);
}

// ------------------------------------------------------------------ interactions

const actions = {
  arm: () => send({ op: "set_armed", value: !S.armed }),
  capture: () => { unlockAudio(); send({ op: "capture_start", trigger: "ui" }); },
  "capture-pose": () => { unlockAudio(); send({ op: "capture_start", trigger: "ui", kind: "pose" }); },
  "capture-move": () => { unlockAudio(); send({ op: "capture_start", trigger: "ui", kind: "move" }); },
  "capture-for": (el) => { unlockAudio(); send({ op: "capture_start", trigger: "ui", id: el.dataset.id }); },
  "capture-cancel": () => send({ op: "capture_cancel" }),
  "capture-finish": () => send({ op: "capture_finish" }),
  "capture-save": () => send({ op: "capture_save" }),
  "capture-redo": () => { send({ op: "capture_cancel" }); setTimeout(() => send({ op: "capture_start", trigger: "ui" }), 100); },
  cmode: (el) => send({ op: "capture_update", fields: { mode: el.dataset.v } }),
  undo: () => { ui.toastUntil = 0; send({ op: "capture_undo" }); },
  filter: (el) => { ui.filter = el.dataset.v; render(true); },
  add: (el) => send({ op: "add_gesture", kind: el.dataset.kind }),
  rebind: (el, e) => { e.stopPropagation(); ui.listening = { id: el.dataset.id }; render(true); },
  gmode: (el) => send({ op: "update_gesture", id: el.dataset.id, fields: { mode: el.dataset.v } }),
  "remove-take": (el) => send({ op: "remove_take", id: el.dataset.id, index: +el.dataset.i }),
  clear: (el) => send({ op: "clear_samples", id: el.dataset.id }),
  rerecord: (el) => { send({ op: "clear_samples", id: el.dataset.id }); setTimeout(() => send({ op: "capture_start", trigger: "ui", id: el.dataset.id }), 100); },
  test: (el) => send({ op: "test_key", id: el.dataset.id }),
  delete: (el) => {
    if (ui.deleteArmed === el.dataset.id) { send({ op: "delete_gesture", id: el.dataset.id }); ui.deleteArmed = null; location.hash = "#/library"; return; }
    ui.deleteArmed = el.dataset.id; render(true); setTimeout(() => { ui.deleteArmed = null; render(); }, 3000);
  },
  neutral: () => { unlockAudio(); send({ op: "record_neutral" }); },
  "clear-neutral": () => send({ op: "clear_neutral" }),
  "clear-ignore": (el) => send({ op: "clear_ignore", role: el.dataset.role }),
  calibrate: (el) => { unlockAudio(); ui.calibDone = null; send({ op: "calibrate", device: el.dataset.device }); },
  "calib-cancel": () => send({ op: "calibrate_cancel" }),
  "calib-close": () => { ui.calibDone = null; render(true); },
  near: (el) => send({ op: "near", choice: el.dataset.v }),
  compact: () => { ui.compact = !ui.compact; send({ op: "overlay_size", height: ui.compact ? 76 : null }); render(true); },
  wrong: () => send({ op: "report_wrong", ignore: true }),
  "neutral-cancel": () => send({ op: "neutral_cancel" }),
  "open-demo": () => { send({ op: "open_demo" }); flash("Opening the demo in a new window…"); },
  "overlay-hide": () => send({ op: "overlay_hide" }),
  "toast-close": () => { ui.toastUntil = 0; render(true); },
  "drill-start": () => { unlockAudio(); ui.drillResult = null; send({ op: "drill_start", reps: +($("#drill-reps")?.value || 3) }); },
  "drill-stop": () => send({ op: "drill_stop" }),
  "drill-close": () => { ui.drillResult = null; render(true); },
  "metrics-refresh": () => loadMetrics(true),
  "profile-new": () => { const n = $("#new-profile").value.trim(); if (n) send({ op: "profile_new", name: n }); },
  "profile-copy": () => { const n = $("#new-profile").value.trim() || `${S.profile.name} copy`; send({ op: "profile_new", name: n, copy: true }); },
};

// Buttons act when you press and release on them, even if the button was redrawn in between
// (a plain "click" is lost when that happens). Keyboard presses arrive as clicks.
// In-app links (#/...) get the same treatment.
const actKey = (el) => el.matches("[data-act]") ? JSON.stringify(el.dataset) : `link:${el.getAttribute("href")}`;
const pressable = (t) => t.closest("[data-act], a[href^='#/']");
let pressed = null;
document.addEventListener("pointerdown", (e) => {
  const el = e.button === 0 && pressable(e.target);
  pressed = el ? actKey(el) : null;
});
document.addEventListener("pointerup", (e) => {
  const el = pressable(e.target);
  const was = pressed;
  pressed = null;
  if (!el || !was || actKey(el) !== was || e.button !== 0) return;
  if (el.matches("[data-act]")) actions[el.dataset.act]?.(el, e);
  else { ui.navigatedAt = Date.now(); location.hash = el.getAttribute("href"); }
});
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (el) { e.preventDefault(); if (e.detail === 0) actions[el.dataset.act]?.(el, e); return; }
  if (ui.listening && !e.target.closest("[data-act=rebind]")) { ui.listening = null; render(true); }
});

const changes = {
  profile: (el) => send({ op: "profile_switch", path: el.value }),
  "profile-rename": (el) => send({ op: "profile_rename", name: el.value.trim() }),
  search: (el) => { ui.search = el.value; },
  setting: (el) => send({ op: "settings", fields: { [el.dataset.k]: el.value } }),
  "setting-bool": (el) => send({ op: "settings", fields: { [el.dataset.k]: el.checked } }),
  "setting-num": (el) => send({ op: "settings", fields: { [el.dataset.k]: +el.value } }),
  role: (el) => send({ op: "device_role", device: el.dataset.device, role: el.value }),
  hand: (el) => send({ op: "device_hand", device: el.dataset.device, hand: el.value }),
  gfield: (el) => send({ op: "update_gesture", id: route.id, fields: { [el.dataset.k]: el.value } }),
  "gfield-num": (el) => send({ op: "update_gesture", id: route.id, fields: { [el.dataset.k]: +el.value } }),
  gcheck: (el) => send({ op: "update_gesture", id: route.id, fields: { [el.dataset.k]: el.checked } }),
  "gtooltip-type": (el) => { const g = S.gestures.find((x) => x.id === route.id); send({ op: "update_gesture", id: route.id, fields: { tooltip: { ...(g.tooltip || {}), type: el.value || null } } }); },
  gstep: (el) => {
    const g = S.gestures.find((x) => x.id === route.id);
    const steps = [...(g.tooltip?.steps || [])]; steps[+el.dataset.i] = el.value.trim();
    send({ op: "update_gesture", id: route.id, fields: { tooltip: { ...(g.tooltip || {}), steps: steps.filter((s) => s) } } });
  },
  cfield: (el) => send({ op: "capture_update", fields: { [el.dataset.k]: el.value } }),
};
document.addEventListener("change", (e) => { const el = e.target.closest("[data-change]"); if (el) changes[el.dataset.change]?.(el, e); });
document.addEventListener("input", (e) => {
  const el = e.target;
  if (el.dataset?.live === "search") { ui.search = el.value; const grid = $("#tiles"); if (grid) { const q = ui.search.toLowerCase(); $$(".tile", grid).forEach((t) => { const g = S.gestures.find((x) => x.id === t.dataset.gid); t.classList.toggle("hidden", !!q && !`${g.name} ${g.casts}`.toLowerCase().includes(q)); }); } }
  if (el.type === "range") { const out = el.parentElement.querySelector("output"); if (out) out.textContent = `${el.value}${el.dataset.unit ? " " + el.dataset.unit : ""}`; }
});

// Escape gets you out of whatever is open: capture, calibration, the accuracy test, a Neutral
// recording, the "Calibrated" summary. (While choosing a key, Escape stops listening instead.)
window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !S || ui.listening || editing()) return;
  if (S.capture && S.capture.phase === "bind") return;   // handled by the key-capture listener below
  if (S.capture) send({ op: "capture_cancel" });
  else if (S.calibration) send({ op: "calibrate_cancel" });
  else if (S.drill) send({ op: "drill_stop" });
  else if (ui.calibDone) { ui.calibDone = null; render(true); }
  else if (ui.neutral && ui.neutral.phase !== "done") send({ op: "neutral_cancel" });
  else if (route.page === "hud") location.hash = "#/library";
  else return;
  e.preventDefault();
});

const MODIFIERS = new Set(["ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight", "CapsLock", "Fn"]);
window.addEventListener("keydown", (e) => {
  const bindingStep = S?.capture?.phase === "bind" && route.page !== "overlay";
  if (!ui.listening && !bindingStep) return;
  if (bindingStep && editing()) return;          // typing a name, not choosing a key
  if (MODIFIERS.has(e.code)) return;
  e.preventDefault();
  const noMods = !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey;
  if (e.code === "Escape" && noMods) { if (ui.listening) { ui.listening = null; render(true); } else send({ op: "capture_cancel" }); return; }
  const mods = [];
  if (e.ctrlKey) mods.push("ctrl"); if (e.altKey) mods.push("alt"); if (e.shiftKey) mods.push("shift"); if (e.metaKey) mods.push("cmd");
  const key = { code: e.code, mods };
  if (ui.listening) { send({ op: "update_gesture", id: ui.listening.id, fields: { key } }); ui.listening = null; }
  else send({ op: "capture_key", key });
}, true);

// ------------------------------------------------------------------ metrics + accuracy test

function onDrill(m) {
  if (!S) return;
  if (m.phase === "done") { S.drill = null; ui.drillResult = m.summary; beep(880, 120); beep(1320, 180, 0.12, 0.12); if (route.page !== "metrics" && route.page !== "overlay") location.hash = "#/metrics"; loadMetrics(true); }
  else if (m.phase === "stopped" || m.phase === "empty") { S.drill = null; if (m.phase === "empty") flash("Record some moves first: the test asks for the ones you have."); }
  else {
    const prev = S.drill;
    if (!prev || prev.phase !== m.phase || prev.index !== m.index) {
      if (m.phase === "go") beep(990, 120);
      if (m.phase === "settle" && m.last) m.last.outcome === "correct" ? beep(1320, 90, 0.1) : beep(330, 160, 0.1);
    }
    S.drill = m;
  }
  render();
}

function drillHtml(d, inHud) {
  const name = esc(d.name || "");
  const color = SOURCE_COLOR[d.source] || "#4fd1a5";
  const last = d.last;
  const lastHtml = last ? `<span class="muted" style="font-size:12px">Last: ${esc(last.want_name)} → ${last.outcome === "correct" ? `<b style="color:var(--ok)">✓ ${last.latency_ms} ms</b>` : last.outcome === "wrong" ? `<b style="color:var(--bad)">✗ got ${esc(S.gestures.find((g) => g.id === last.got)?.name || last.got)}</b>` : `<b style="color:var(--warn)">nothing${last.detail ? ` (${esc(last.detail)})` : ""}</b>`}</span>` : "";
  const prompt = d.phase === "ready" ? `<span class="muted">Get ready</span><b class="drill-name" style="color:${color}">${name}</b>`
    : d.phase === "go" ? `<span style="color:var(--ok)">Now!</span><b class="drill-name" style="color:${color}">${name}</b><div class="meter" style="height:8px"><i style="width:${Math.max(3, (d.remaining / 4) * 100)}%"></i></div>`
    : `<span class="muted">…</span><b class="drill-name muted">${name}</b>`;
  return `<div class="modal-title"><div class="stack" style="gap:2px"><b>Accuracy test</b><span class="muted" style="font-size:12px">${d.index + 1} of ${d.total} · no keys are sent</span></div><button class="small ghost" data-act="drill-stop">Stop</button></div>
    <div class="drill stack" style="align-items:center;text-align:center;gap:8px;padding:${inHud ? "6px" : "18px"} 0">${prompt}</div>${lastHtml}`;
}

async function loadMetrics(force = false) {
  const now = Date.now();
  if (!force && ui.metricsAt && now - ui.metricsAt < 2000) return;
  ui.metricsAt = now;
  try {
    const r = await fetch("/api/metrics"); ui.metrics = await r.json();
    if (!ui.lab) { const l = await fetch("/static/lab-summary.json", { cache: "no-store" }); ui.lab = l.ok ? await l.json() : {}; }
  } catch (e) { ui.metrics = ui.metrics || null; }
  if (route.page === "metrics" && !editing()) render();
}

const BAR = { ok: "#4fd1a5", bad: "#ff6b6b", warn: "#f5b544", none: "#3a4152", gyro: "#7aa2ff", cam: "#4fd1a5", both: "#ff6b3d" };

function stackBar(segs, w = 100) {
  // segs: [[value 0-100, color, title]]
  return `<div class="sbar" role="img" aria-label="${esc(segs.map((s) => `${s[2]} ${s[0]}%`).join(", "))}">${segs.filter((s) => s[0] > 0).map(([v, c, t]) => `<i style="width:${v}%;background:${c}" title="${esc(t)} ${v}%"></i>`).join("")}</div>`;
}

function kpi(value, label, color) { return `<div><b style="${color ? `color:${color}` : ""}">${value ?? "—"}</b><span>${label}</span></div>`; }

function barRows(obj, color, total) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return `<p class="muted" style="font-size:12px">Nothing yet.</p>`;
  const max = total || Math.max(...entries.map(([, v]) => v), 1);
  return `<div class="bars">${entries.map(([k, v]) => `<div class="barrow"><span>${esc(k)}</span><div class="sbar"><i style="width:${Math.max(2, (v / max) * 100)}%;background:${color}"></i></div><b>${v}</b></div>`).join("")}</div>`;
}

function metricsPage() {
  if (!ui.metrics) { loadMetrics(true); return `<div class="head"><div class="lead"><h1>Metrics</h1><p class="soft">Loading…</p></div></div>`; }
  const m = ui.metrics, ses = m.session, hist = m.history || [];
  const dr = ui.drillResult || [...hist].reverse().flatMap((d) => d.drills).slice(-1)[0] || null;
  const fusion = ses.fusion || {};
  const fusedN = (fusion.agreed || 0) + (fusion.rescued || 0) + (fusion.corrected || 0) + (fusion.vetoed || 0);
  const dec = ses.decisions || {};
  return `
  <div class="head"><div class="lead"><h1>Metrics</h1><p class="soft">How often it gets it right: test it on demand, watch it while you play, and see how it was tested.</p></div>
    <div class="toolbar"><button class="small ghost" data-act="metrics-refresh">Refresh</button></div></div>
  <section class="panel"><h2>Accuracy test <span class="muted" style="font-size:12px;font-weight:400">your moves, your hardware, no keys sent</span></h2>
    <p class="soft" style="margin:0">The app asks for each of your moves a few times in random order and scores what it recognized. Run it after recording or re-calibrating.</p>
    <div class="row wrap"><label class="inline">Each move <select id="drill-reps">${[2, 3, 5].map((n) => `<option value="${n}" ${n === 3 ? "selected" : ""}>${n}×</option>`).join("")}</select></label>
      <button data-act="drill-start">▶ Start the test</button></div>
    ${dr && dr.per ? drillResultHtml(dr) : dr ? `<div class="stats">${kpi(dr.accuracy + "%", "right", "var(--ok)")}${kpi(dr.wrong + "%", "wrong key", dr.wrong ? "var(--bad)" : "")}${kpi(dr.n, "prompts")}</div>` : `<p class="muted" style="font-size:12px">No test run yet.</p>`}
  </section>
  <div class="cols">
    <div class="wide">
      <section class="panel"><h2>This session</h2>
        <div class="stats">${kpi(ses.motions, "weapon motions")}${kpi(dec.fired || 0, "fired", "var(--ok)")}${kpi(dec.near || 0, "near misses", "var(--warn)")}${kpi(dec.held || 0, "held back")}${kpi(ses.latency_ms?.median != null ? ses.latency_ms.median + " ms" : null, "typical delay")}</div>
        <b style="font-size:13px">What the camera did to motion decisions</b>
        ${fusedN || fusion.gyro_only ? stackBar([[pct(fusion.agreed, ses.motions), BAR.ok, "agreed with the gyro"], [pct(fusion.rescued, ses.motions), BAR.both, "rescued a move the gyro would have missed"], [pct(fusion.corrected, ses.motions), BAR.gyro, "corrected the gyro's pick"], [pct(fusion.vetoed, ses.motions), BAR.bad, "stopped a wrong fire"], [pct(fusion.gyro_only, ses.motions), BAR.none, "gyro was sure on its own"], [pct(fusion.no_view, ses.motions), BAR.warn, "couldn't see your hand"]])
          + `<div class="legend"><span><i style="background:${BAR.ok}"></i>Agreed ${fusion.agreed || 0}</span><span><i style="background:${BAR.both}"></i>Rescued ${fusion.rescued || 0}</span><span><i style="background:${BAR.gyro}"></i>Corrected ${fusion.corrected || 0}</span><span><i style="background:${BAR.bad}"></i>Stopped a wrong fire ${fusion.vetoed || 0}</span><span><i style="background:${BAR.none}"></i>Gyro sure alone ${fusion.gyro_only || 0}</span><span><i style="background:${BAR.warn}"></i>No view ${fusion.no_view || 0}</span></div>`
          : `<p class="muted" style="font-size:12px">Make some moves with the webcam on.</p>`}
        ${ses.camera_wait_ms?.median != null ? `<span class="muted" style="font-size:12px">Waited for the camera on ${Math.round((ses.camera_wait_ms.share || 0) * 100)}% of motions, typically ${ses.camera_wait_ms.median} ms.</span>` : ""}
        <div class="cols" style="gap:16px"><div class="wide stack"><b style="font-size:13px">Keys sent</b>${barRows(ses.fires, BAR.ok)}</div>
          <div class="narrow stack"><b style="font-size:13px">Why moves didn't fire</b>${barRows(ses.reasons, BAR.none)}</div></div>
        ${Object.keys(ses.body_moves || {}).length ? `<span class="muted" style="font-size:12px">Body moves (webcam only): ${Object.entries(ses.body_moves).map(([k, v]) => `${k} ${v}`).join(", ")}</span>` : ""}
        <div class="row wrap muted" style="font-size:12px;gap:14px"><span>Gyro drift corrected from camera stillness: <b class="soft">${ses.calibration?.camera_bias || 0}×</b></span><span>Mount auto-fix steps: <b class="soft">${ses.calibration?.auto_align || 0}</b></span><span>Your corrections: <b class="soft">${Object.entries(ses.corrections || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}</b></span><span>Held back: <b class="soft">${Object.entries(ses.held || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "nothing"}</b></span></div>
      </section>
      <section class="panel"><h2>Day by day <span class="muted" style="font-size:12px;font-weight:400">${m.enabled ? "from your play log" : "play log is off"}</span></h2>${historyChart(hist)}</section>
    </div>
    <div class="narrow">${labPanel(ui.lab)}</div>
  </div>`;
}

const pct = (v, total) => total ? Math.round(((v || 0) / total) * 1000) / 10 : 0;

function drillResultHtml(dr) {
  const rows = Object.entries(dr.per || {});
  const names = Object.fromEntries(rows.map(([id, p]) => [id, p.name]));
  const cols = [...new Set(rows.flatMap(([id]) => Object.keys(dr.confusion?.[id] || {})))];
  const colName = (c) => c === "(nothing)" ? "nothing" : names[c] || S.gestures.find((g) => g.id === c)?.name || c;
  return `<div class="stats">${kpi(dr.accuracy + "%", "right", "var(--ok)")}${kpi(dr.wrong + "%", "wrong key", dr.wrong ? "var(--bad)" : "")}${kpi(dr.missed + "%", "missed", dr.missed ? "var(--warn)" : "")}${kpi(dr.latency_ms != null ? dr.latency_ms + " ms" : null, "typical delay")}${kpi(dr.n, "prompts")}</div>
    <div class="table-wrap"><table class="grid"><thead><tr><th>Asked for</th>${cols.map((c) => `<th>${esc(colName(c))}</th>`).join("")}<th>Right</th></tr></thead><tbody>
    ${rows.map(([id, p]) => `<tr><th>${esc(p.name)}</th>${cols.map((c) => { const v = dr.confusion?.[id]?.[c] || 0; const good = c === id; return `<td class="${v ? (good ? "hit" : c === "(nothing)" ? "miss" : "bad") : ""}">${v || ""}</td>`; }).join("")}<td>${Math.round((p.correct / p.n) * 100)}%</td></tr>`).join("")}
    </tbody></table></div>`;
}

function historyChart(hist) {
  if (!hist.length) return `<p class="muted" style="font-size:12px">Play a session and it shows up here: keys fired, near misses and your test scores per day.</p>`;
  const W = 640, H = 150, n = hist.length, bw = Math.min(40, (W - 40) / n - 6);
  const max = Math.max(1, ...hist.map((d) => d.fired + d.near + d.rejected + d.held));
  let bars = "";
  hist.forEach((d, i) => {
    const x = 30 + i * ((W - 40) / n);
    let y = H - 20;
    for (const [v, c] of [[d.fired, BAR.ok], [d.near, BAR.warn], [d.held, BAR.gyro], [d.rejected, BAR.none]]) {
      const h = (v / max) * (H - 40); y -= h;
      if (v) bars += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="2" fill="${c}"><title>${d.day}: ${v}</title></rect>`;
    }
    const acc = d.drills.length ? d.drills[d.drills.length - 1].accuracy : null;
    if (acc != null) bars += `<circle cx="${x + bw / 2}" cy="${H - 20 - (acc / 100) * (H - 40)}" r="4" fill="#fff"><title>test ${acc}%</title></circle>`;
    bars += `<text x="${x + bw / 2}" y="${H - 5}" fill="#6b7488" font-size="10" text-anchor="middle">${d.day.slice(5)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Motions per day">${bars}</svg>
    <div class="legend"><span><i style="background:${BAR.ok}"></i>Fired</span><span><i style="background:${BAR.warn}"></i>Near misses</span><span><i style="background:${BAR.gyro}"></i>Held back</span><span><i style="background:${BAR.none}"></i>Didn't match</span><span><i style="background:#fff"></i>Test score (same scale, %)</span></div>`;
}

function labPanel(lab) {
  if (!lab) return "";
  const out = [];
  const tri = (c, w, r) => stackBar([[c, BAR.ok, "right"], [w, BAR.bad, "wrong key"], [r, BAR.none, "rejected (safe miss)"]]);
  if (lab.real_fusion) {
    const f = lab.real_fusion;
    out.push(`<section class="panel"><h2>Camera + gyro on real people</h2><p class="muted" style="font-size:12px;margin:0">${esc(f.source)}</p>
      ${f.rows.map((r) => `<b style="font-size:12px">${esc(r.label)}</b>${[["Gyro", "gyro"], ["Camera", "camera"], ["Both", "fused"]].map(([l, k]) => `<div class="barrow"><span>${l}</span>${tri(r[k].correct, r[k].wrong, r[k].rejected)}<b>${r[k].correct}%</b></div>`).join("")}`).join("")}
      <div class="legend"><span><i style="background:${BAR.ok}"></i>Right</span><span><i style="background:${BAR.bad}"></i>Wrong key</span><span><i style="background:${BAR.none}"></i>Safe miss</span></div>
      ${f.largest?.gyro ? `<div class="note ok">At 95%+ right and ≤1% wrong keys: <b>${f.largest.gyro}</b> moves with the gyro alone, <b>${f.largest.fused}</b> with camera + gyro.</div>` : ""}
      ${f.hand ? `<span class="muted" style="font-size:12px">Found the weapon hand from one move ${f.hand.single_move_correct_pct}% of the time, from three ${f.hand.three_moves_correct_pct}%.</span>` : ""}</section>`);
  }
  if (lab.camera_only) {
    const c = lab.camera_only;
    out.push(`<section class="panel"><h2>Body moves: camera alone on real people</h2><p class="muted" style="font-size:12px;margin:0">${esc(c.source)}</p>
      ${c.rows.map((r) => `<div class="barrow"><span>${esc(r.label)}</span>${tri(r.correct, r.wrong, r.rejected)}<b>${r.correct}%</b></div>`).join("")}
      <span class="muted" style="font-size:12px">${c.largest} of 27 actions together stay at 95%+ right with at most 1% wrong keys. Moves never recorded fired something ${c.open_set_false_fire}% of the time.</span></section>`);
  }
  if (lab.sim_fusion) {
    const f = lab.sim_fusion;
    out.push(`<section class="panel"><h2>Joy-Con on a pipe + webcam (simulated)</h2>
      ${f.sets.map((s) => `<b style="font-size:12px">${esc(s.label)}</b><div class="barrow"><span>Gyro</span>${stackBar([[s.gyro.correct_pct, BAR.ok, "right"]])}<b>${s.gyro.correct}/${s.gyro.moves}</b></div>${s.camera_only ? `<div class="barrow"><span>Camera</span>${stackBar([[s.camera_only.correct_pct, BAR.ok, "right"]])}<b>${s.camera_only.correct}/${s.camera_only.moves}</b></div>` : ""}<div class="barrow"><span>Both</span>${stackBar([[s.fused.correct_pct, BAR.ok, "right"]])}<b>${s.fused.correct}/${s.fused.moves}</b></div>`).join("")}
      ${f.auto_align ? `<div class="note info">Joy-Con slipped 35° on the pipe: ${f.auto_align["no correction"]?.last_40_correct_pct}% right uncorrected, ${f.auto_align["auto-align, camera + gyro decide"]?.last_40_correct_pct}% after auto-fixing itself from your moves.</div>` : ""}
      ${f.bias ? `<div class="note info">Uncalibrated gyro drifting ~20°/s: gyro alone still off by ${f.bias["gyro-only still tracker"]?.offset_error_at_end_deg_s}°/s, with camera stillness ${f.bias["+ camera stillness"]?.offset_error_at_end_deg_s}°/s.</div>` : ""}</section>`);
  }
  if (lab.real_gyro) {
    const g = lab.real_gyro;
    out.push(`<section class="panel"><h2>Gyro alone on real people</h2><p class="muted" style="font-size:12px;margin:0">${esc(g.source)}</p>
      ${g.sets.map((s) => `<div class="barrow"><span style="width:150px">${esc(s.label)}</span>${tri(s.correct, s.wrong, s.rejected)}<b>${s.correct}%</b></div>`).join("")}
      <span class="muted" style="font-size:12px">Takes recorded: ${g.takes.map((t) => `${t.takes} → ${t.correct}%`).join(" · ")}</span></section>`);
  }
  if (lab.poses || lab.body) {
    out.push(`<section class="panel"><h2>Poses and movement</h2><div class="stats">${lab.poses ? kpi(lab.poses.same_person + "%", "poses right (real photos)", "var(--ok)") + kpi(lab.poses.neutral_false + "%", "false triggers standing") : ""}${lab.body ? kpi(`${lab.body.jog_found}/${lab.body.jog_clips}`, "jog clips found") + kpi(lab.body.false_jogs + lab.body.false_hops, `false jogs/hops in ${lab.body.quiet_clips} clips`) : ""}</div></section>`);
  }
  return out.join("") + `<p class="muted" style="font-size:12px">Details: docs/verification.md · generated ${esc(lab.generated || "")}</p>`;
}

setInterval(() => { if (S && route.page === "metrics" && !editing() && !S.drill) loadMetrics(); }, 2500);

setInterval(() => { if (S && (route.page === "hud" || route.page === "overlay") && !editing()) render(); }, 1000);

connect();
render();
