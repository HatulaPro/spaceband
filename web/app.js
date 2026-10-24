import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const MAX_PX = 44;  // largest on-screen star diameter (CSS px)
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmt = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });

let D, N;                       // data, artist count
let P3, P2, P;                  // positions: 3D, 2D (z=0), current (interpolated)
let geom, points, mat, lines;   // three.js objects
let mix = 0;                    // 0 = 3D, 1 = 2D
let posDirty = true;
const S = { mode: 3, sel: -1, scene: -1, hover: -1, labels: true };
let norm;                       // normalised names for search
let sx, sy, sz, sv;             // per-frame projected screen coords
const tweens = [];

// ---------------------------------------------------------------- load
async function load() {
  let res;
  try {
    res = await fetch('data/map.json');
    if (!res.ok) throw new Error(res.status);
    D = await res.json();
  } catch (e) {
    const el = $('loading');
    el.classList.add('err');
    el.innerHTML = `<div>No map data found in <code>web/data/map.json</code>.<br>
      Build it with <code>python pipeline/crawl.py</code> and then <code>python pipeline/build.py</code>,<br>
      and serve this folder with <code>python serve.py</code>.</div>`;
    return;
  }
  N = D.names.length;
  init();
}

// ---------------------------------------------------------------- scene setup
const canvas = $('sky');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, 1, 0.02, 5000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.09;
controls.rotateSpeed = 0.6;
controls.zoomSpeed = 1.1;
controls.minDistance = 0.15;
controls.zoomToCursor = true;   // wheel zooms toward what's under the pointer
controls.maxDistance = 900;

const isPhone = () => innerWidth <= 760;
function updateViewOffset() {
  const w = innerWidth, h = innerHeight;
  if (isPhone() && !$('panel').hidden) camera.setViewOffset(w, h, 0, h * 0.24, w, h);
  else camera.clearViewOffset();
}
function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  updateViewOffset();
  camera.updateProjectionMatrix();
  if (mat) mat.uniforms.uScale.value = (h / 2) / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * renderer.getPixelRatio();
}
addEventListener('resize', resize);

const NEAR_FAR = 28.0, NEAR_FULL = 6.0;  // distance band where dimmed stars fade back in
const VERT = /* glsl */`
  #define NEAR_FAR ${NEAR_FAR.toFixed(1)}
  #define NEAR_FULL ${NEAR_FULL.toFixed(1)}
  attribute float size;
  attribute float hl;
  attribute vec3 color;
  uniform float uScale;
  uniform float uFocus;
  uniform float uPx;
  uniform float uMax;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    float s = size;
    float a = 0.9;
    if (uFocus > 0.5) {
      if (hl < 0.5)      { float near = smoothstep(NEAR_FAR, NEAR_FULL, -mv.z); a = 0.07 + 0.5 * near; s *= 0.7 + 0.3 * near; }
      else if (hl < 1.5) { a = 1.0;  s *= 1.35; }
      else               { a = 1.0;  s *= 2.4; }
    }
    gl_PointSize = clamp(s * uScale / -mv.z, 1.6 * uPx, uMax * uPx);
    vColor = color;
    vAlpha = a;
    gl_Position = projectionMatrix * mv;
  }`;
const FRAG = /* glsl */`
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c) * 2.0;
    if (d > 1.0) discard;
    float core = smoothstep(0.72, 0.38, d);
    float halo = exp(-d * d * 6.0) * 0.35;
    float a = clamp(core + halo, 0.0, 1.0) * vAlpha;
    gl_FragColor = vec4(mix(vColor, vec3(1.0), smoothstep(0.35, 0.0, d) * 0.22), a);
  }`;

function init() {
  P3 = new Float32Array(D.pos3);
  P2 = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) { P2[i * 3] = D.pos2[i * 2]; P2[i * 3 + 1] = D.pos2[i * 2 + 1]; }
  P = new Float32Array(P3);
  const col = new Float32Array(N * 3);
  for (let i = 0; i < N * 3; i++) col[i] = D.rgb[i] / 255;
  const size = new Float32Array(N);
  let lo = Infinity, hi = -Infinity;
  const lu = D.users.map((u) => Math.log1p(u));
  for (const v of lu) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  for (let i = 0; i < N; i++) size[i] = 0.45 + 2.1 * Math.pow((lu[i] - lo) / (hi - lo || 1), 1.6);

  geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(P, 3));
  geom.setAttribute('color', new THREE.BufferAttribute(col, 3));
  geom.setAttribute('size', new THREE.BufferAttribute(size, 1));
  geom.setAttribute('hl', new THREE.BufferAttribute(new Float32Array(N), 1));
  geom.computeBoundingSphere();
  mat = new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false,
    blending: THREE.NormalBlending,
    uniforms: { uScale: { value: 1 }, uFocus: { value: 0 }, uPx: { value: renderer.getPixelRatio() }, uMax: { value: MAX_PX } },
  });
  points = new THREE.Points(geom, mat);
  points.frustumCulled = false;
  scene.add(points);

  sx = new Float32Array(N); sy = new Float32Array(N); sz = new Float32Array(N); sv = new Uint8Array(N);
  norm = D.names.map(normalise);
  D.sceneName = D.clusters.map(sceneName);

  resize();
  buildScenes();
  buildLabels();
  $('stats').textContent = `${N.toLocaleString()} artists in ${D.clusters.length} scenes, from ListenBrainz listening data`;

  // opening move: drift in from deep space
  camera.position.set(0, 0, 520);
  controls.target.set(0, 0, 0);
  flyCamera(new THREE.Vector3(0, 0, 0), new THREE.Vector3(60, 40, 205), 1800);
  $('loading').classList.add('gone');

  const fromHash = decodeURIComponent(location.hash.slice(1));
  if (fromHash) {
    const i = D.mbids.indexOf(fromHash);
    if (i >= 0) setTimeout(() => select(i), 400);
  }
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------- helpers
function normalise(s) {
  return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}
function hex(i) {
  const r = D.rgb[i * 3], g = D.rgb[i * 3 + 1], b = D.rgb[i * 3 + 2];
  return `rgb(${r},${g},${b})`;
}
function sceneName(c) {
  if (c.genres && c.genres.length) return c.genres.slice(0, 2).join(' / ');
  return `around ${D.names[c.top[0]]}`;
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
function pos(i) { return new THREE.Vector3(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]); }
function posIn(mode, i) {
  const A = mode === 2 ? P2 : P3;
  return new THREE.Vector3(A[i * 3], A[i * 3 + 1], A[i * 3 + 2]);
}
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function tween(ms, fn, done) {
  if (reduceMotion) ms = 1;
  const t0 = performance.now();
  const tw = { t0, ms, fn, done };
  tweens.push(tw);
  return tw;
}
function flyCamera(target, camPos, ms = 1000) {
  for (const t of tweens) if (t.kind === 'cam') t.dead = true;
  const t0 = controls.target.clone(), c0 = camera.position.clone();
  const tw = tween(ms, (k) => {
    controls.target.lerpVectors(t0, target, k);
    camera.position.lerpVectors(c0, camPos, k);
  });
  tw.kind = 'cam';
}
function flyTo(p, dist) {
  if (S.mode === 2) {
    flyCamera(p.clone(), new THREE.Vector3(p.x, p.y, p.z + dist * 1.6), 1000);
  } else {
    const dir = camera.position.clone().sub(controls.target).normalize();
    flyCamera(p.clone(), p.clone().add(dir.multiplyScalar(dist)), 1100);
  }
}

// ---------------------------------------------------------------- highlight
function setHighlight(list, selIdx) {
  const hl = geom.attributes.hl.array;
  hl.fill(0);
  if (list) for (const j of list) hl[j] = 1;
  if (selIdx >= 0) hl[selIdx] = 2;
  geom.attributes.hl.needsUpdate = true;
  mat.uniforms.uFocus.value = list || selIdx >= 0 ? 1 : 0;
}

function drawLines(i) {
  if (lines) { scene.remove(lines); lines.geometry.dispose(); lines = null; }
  if (i < 0) return;
  const nb = D.nbrs[i].slice(0, 25);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(nb.length * 6), 3));
  const c = new Float32Array(nb.length * 6);
  nb.forEach((j, k) => {
    for (let a = 0; a < 3; a++) { c[k * 6 + a] = D.rgb[i * 3 + a] / 255; c[k * 6 + 3 + a] = D.rgb[j * 3 + a] / 255; }
  });
  g.setAttribute('color', new THREE.BufferAttribute(c, 3));
  lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  lines.userData = { i, nb };
  lines.frustumCulled = false;
  scene.add(lines);
  updateLines();
}
function updateLines() {
  if (!lines) return;
  const { i, nb } = lines.userData;
  const a = lines.geometry.attributes.position.array;
  nb.forEach((j, k) => {
    for (let d = 0; d < 3; d++) { a[k * 6 + d] = P[i * 3 + d]; a[k * 6 + 3 + d] = P[j * 3 + d]; }
  });
  lines.geometry.attributes.position.needsUpdate = true;
}

// ---------------------------------------------------------------- selection
function select(i, { fly = true } = {}) {
  S.sel = i;
  S.scene = -1;
  markScene(-1);
  if (i < 0) {
    setHighlight(null, -1);
    drawLines(-1);
    $('panel').hidden = true;
    updateViewOffset();
    history.replaceState(null, '', location.pathname + location.search);
    return;
  }
  setHighlight(D.nbrs[i].slice(0, 25), i);
  drawLines(i);
  if (fly) flyTo(pos(i), 38);
  renderPanel(i);
  history.replaceState(null, '', '#' + D.mbids[i]);
}

function focusScene(c) {
  if (S.scene === c) { S.scene = -1; setHighlight(null, -1); markScene(-1); return; }
  select(-1);
  S.scene = c;
  const members = [];
  for (let i = 0; i < N; i++) if (D.cluster[i] === c) members.push(i);
  setHighlight(members, -1);
  markScene(c);
  const cl = D.clusters[c];
  const cen = S.mode === 2 ? new THREE.Vector3(cl.c2[0], cl.c2[1], 0) : new THREE.Vector3(...cl.c3);
  const d = members.map((i) => pos(i).distanceTo(cen)).sort((a, b) => a - b);
  const r = d[Math.floor(d.length * 0.85)] || 10;
  flyTo(cen, r * 2.2 + 18);
}

const trackCache = new Map();
const genreCache = new Map();
function renderPanel(i) {
  const c = D.cluster[i];
  const cl = D.clusters[c];
  const mbid = D.mbids[i];
  const genres = D.genres[mbid];
  const nb = D.nbrs[i].slice(0, 25);
  const listeners = D.users[i] ? `${D.users[i].toLocaleString()} listeners` : '';
  const plays = D.listens[i] ? `${fmt.format(D.listens[i])} plays` : '';
  $('panel-body').innerHTML = `
    <h2>${esc(D.names[i])}</h2>
    <button class="scene-chip" style="color:${cl.color}" data-scene="${c}" title="Show this scene">
      <i class="dot" style="background:${cl.color}"></i><span>${esc(D.sceneName[c])}</span></button>
    <p class="facts">${[listeners, plays].filter(Boolean).join(', ')}</p>
    <p class="genres" id="genres">${genres ? esc(genres.join(', ')) : ''}</p>
    <div class="links">
      <a href="https://listenbrainz.org/artist/${mbid}/" target="_blank" rel="noopener">ListenBrainz</a>
      <a href="https://musicbrainz.org/artist/${mbid}" target="_blank" rel="noopener">MusicBrainz</a>
    </div>
    <h3>Most similar</h3>
    <ol class="sim">${nb.map((j, k) => `
      <li><button data-i="${j}"><span class="rank">${k + 1}</span><i class="dot" style="background:${hex(j)};color:${hex(j)}"></i>
        <span>${esc(D.names[j])}</span><span class="meta">${D.users[j] ? fmt.format(D.users[j]) : ''}</span></button></li>`).join('')}
    </ol>
    ${nb.length ? '' : '<p class="muted">No similar artists on the map.</p>'}
    <h3>Top tracks</h3>
    <ul class="tracks" id="tracks"><li class="muted">Loading from ListenBrainz…</li></ul>`;
  $('panel').hidden = false;
  $('panel').scrollTop = 0;
  updateViewOffset();

  // live extras: top recordings (ListenBrainz) and genres (MusicBrainz) if not cached in the map
  const showTracks = (rows) => {
    if (S.sel !== i) return;
    const el = $('tracks');
    if (!rows || !rows.length) { el.innerHTML = '<li class="muted">No track data.</li>'; return; }
    el.innerHTML = rows.slice(0, 6).map((r) => `<li><span>${esc(r.recording_name)}</span>
      <span class="meta">${fmt.format(r.total_listen_count || 0)} plays</span></li>`).join('');
  };
  if (trackCache.has(mbid)) showTracks(trackCache.get(mbid));
  else fetch(`https://api.listenbrainz.org/1/popularity/top-recordings-for-artist/${mbid}`)
    .then((r) => (r.ok ? r.json() : [])).then((rows) => { trackCache.set(mbid, rows); showTracks(rows); })
    .catch(() => showTracks(null));

  if (!genres) {
    const showGenres = (g) => { if (S.sel === i && g && g.length) $('genres').textContent = g.join(', '); };
    if (genreCache.has(mbid)) showGenres(genreCache.get(mbid));
    else fetch(`https://musicbrainz.org/ws/2/artist/${mbid}?inc=genres&fmt=json`)
      .then((r) => (r.ok ? r.json() : null))
      .then((js) => {
        const g = js ? (js.genres || []).sort((a, b) => b.count - a.count).slice(0, 4).map((x) => x.name) : [];
        genreCache.set(mbid, g); showGenres(g);
      }).catch(() => {});
  }
}
$('panel-body').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.i) select(+b.dataset.i);
  else if (b.dataset.scene) focusScene(+b.dataset.scene);
});
$('close').addEventListener('click', () => select(-1));

// ---------------------------------------------------------------- search
const q = $('q'), results = $('results');
let hits = [], active = -1;
function search(text) {
  const t = normalise(text.trim());
  if (!t) return [];
  const exact = [], starts = [], word = [], sub = [];
  for (const i of D.byPop) {
    const n = norm[i];
    const k = n.indexOf(t);
    if (k < 0) continue;
    if (n === t) exact.push(i);
    else if (k === 0) starts.push(i);
    else if (/[\s\-_.&(/]/.test(n[k - 1])) word.push(i);
    else sub.push(i);
    if (exact.length + starts.length >= 10) break;
  }
  return [...exact, ...starts, ...word, ...sub].slice(0, 10);
}
function showResults() {
  const open = document.activeElement === q && q.value.trim().length > 0;
  results.hidden = !open;
  q.parentElement.setAttribute('aria-expanded', String(open));
  if (!open) return;
  if (!hits.length) { results.innerHTML = `<li class="empty">No artist on the map matches “${esc(q.value.trim())}”.</li>`; return; }
  results.innerHTML = hits.map((i, k) => `<li role="option" id="r${k}" data-i="${i}" aria-selected="${k === active}">
    <i class="dot" style="background:${hex(i)};color:${hex(i)}"></i><span>${esc(D.names[i])}</span>
    <span class="meta">${esc(D.sceneName[D.cluster[i]])}</span></li>`).join('');
}
q.addEventListener('input', () => { if (!D) return; hits = search(q.value); active = hits.length ? 0 : -1; showResults(); });
q.addEventListener('focus', showResults);
q.addEventListener('blur', () => setTimeout(showResults, 120));
q.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') { active = Math.min(active + 1, hits.length - 1); showResults(); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); showResults(); e.preventDefault(); }
  else if (e.key === 'Enter' && active >= 0) { pick(hits[active]); }
  else if (e.key === 'Escape') { q.value = ''; hits = []; q.blur(); }
});
results.addEventListener('mousedown', (e) => {
  const li = e.target.closest('li[data-i]');
  if (li) { e.preventDefault(); pick(+li.dataset.i); }
});
function pick(i) { q.value = D.names[i]; q.blur(); results.hidden = true; select(i); }
addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== q) { e.preventDefault(); q.focus(); q.select(); }
  else if (e.key === 'Escape' && document.activeElement !== q) { if (S.sel >= 0) select(-1); else if (S.scene >= 0) focusScene(S.scene); }
});

// ---------------------------------------------------------------- scenes list
function buildScenes() {
  $('scene-list').innerHTML = D.clusters.map((c, k) => `<li><button data-c="${k}">
    <i class="dot" style="background:${c.color};color:${c.color}"></i>
    <span><span class="name">${esc(D.sceneName[k])}</span>
    <span class="sub">${esc(c.top.slice(0, 3).map((i) => D.names[i]).join(', '))}</span></span>
    <span class="meta">${c.size.toLocaleString()}</span></button></li>`).join('');
  $('scene-list').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-c]');
    if (b) focusScene(+b.dataset.c);
  });
}
function markScene(c) {
  for (const b of $('scene-list').querySelectorAll('button')) b.classList.toggle('on', +b.dataset.c === c);
}
$('scenes-btn').addEventListener('click', () => {
  const s = $('scenes');
  s.hidden = !s.hidden;
  $('scenes-btn').setAttribute('aria-expanded', String(!s.hidden));
});

// ---------------------------------------------------------------- 2D / 3D
function setMode(m) {
  if (m === S.mode || !D) return;
  S.mode = m;
  $('v3').classList.toggle('on', m === 3); $('v3').setAttribute('aria-pressed', String(m === 3));
  $('v2').classList.toggle('on', m === 2); $('v2').setAttribute('aria-pressed', String(m === 2));
  const from = mix, to = m === 2 ? 1 : 0;
  for (const t of tweens) if (t.kind === 'mix') t.dead = true;
  const tw = tween(1300, (k) => { mix = from + (to - from) * k; posDirty = true; });
  tw.kind = 'mix';
  // camera: keep the selected artist (or the current focus) in view
  let tgt;
  if (S.sel >= 0) tgt = posIn(m, S.sel);
  else if (S.scene >= 0) { const c = D.clusters[S.scene]; tgt = m === 2 ? new THREE.Vector3(c.c2[0], c.c2[1], 0) : new THREE.Vector3(...c.c3); }
  else tgt = new THREE.Vector3(0, 0, 0);
  const dist = Math.max(camera.position.distanceTo(controls.target), 40);
  if (m === 2) {
    controls.enableRotate = false;
    controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
    controls.touches.ONE = THREE.TOUCH.PAN;
    flyCamera(tgt, new THREE.Vector3(tgt.x, tgt.y, dist), 1300);
  } else {
    controls.enableRotate = true;
    controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
    controls.touches.ONE = THREE.TOUCH.ROTATE;
    flyCamera(tgt, tgt.clone().add(new THREE.Vector3(dist * 0.35, dist * 0.25, dist * 0.9)), 1300);
  }
}
$('v3').addEventListener('click', () => setMode(3));
$('v2').addEventListener('click', () => setMode(2));
$('labels-btn').addEventListener('click', () => {
  S.labels = !S.labels;
  $('labels-btn').setAttribute('aria-pressed', String(S.labels));
});
$('reset-btn').addEventListener('click', () => {
  select(-1);
  if (S.scene >= 0) focusScene(S.scene);
  flyCamera(new THREE.Vector3(), S.mode === 2 ? new THREE.Vector3(0, 0, 240) : new THREE.Vector3(60, 40, 205), 1200);
});

// ---------------------------------------------------------------- picking
const mouse = { x: -1, y: -1, down: null, inside: false, pointers: 0, multi: false };
canvas.addEventListener('pointermove', (e) => {
  if (e.pointerType !== 'mouse') return;  // touch has no hover: taps are handled on pointerup
  mouse.x = e.clientX; mouse.y = e.clientY; mouse.inside = true;
});
canvas.addEventListener('pointerleave', () => { mouse.inside = false; S.hover = -1; $('tip').hidden = true; });
canvas.addEventListener('pointerdown', (e) => {
  mouse.pointers++;
  if (mouse.pointers > 1) mouse.multi = true;  // pinch or two-finger pan, never a tap
  mouse.down = [e.clientX, e.clientY];
  canvas.classList.add('dragging');
});
addEventListener('pointercancel', () => { mouse.pointers = Math.max(0, mouse.pointers - 1); mouse.down = null; });
addEventListener('pointerup', (e) => {
  canvas.classList.remove('dragging');
  mouse.pointers = Math.max(0, mouse.pointers - 1);
  const wasMulti = mouse.multi;
  if (mouse.pointers === 0) mouse.multi = false;
  if (wasMulti) { mouse.down = null; return; }
  if (!mouse.down || e.target !== canvas) { mouse.down = null; return; }
  const moved = Math.hypot(e.clientX - mouse.down[0], e.clientY - mouse.down[1]);
  mouse.down = null;
  if (moved > (e.pointerType === 'mouse' ? 5 : 12)) return;
  const touch = e.pointerType !== 'mouse';
  const hit = touch ? pickAt(e.clientX, e.clientY, 18) : S.hover;
  if (hit >= 0) select(hit);
  else if (S.sel >= 0 && !touch) select(-1, { fly: false });  // on phones, close with × instead
});

const _m = new THREE.Matrix4();
function project() {
  camera.updateMatrixWorld();
  _m.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  const e = _m.elements, w = innerWidth / 2, h = innerHeight / 2;
  for (let i = 0; i < N; i++) {
    const x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
    const cw = e[3] * x + e[7] * y + e[11] * z + e[15];
    if (cw <= 0.1) { sv[i] = 0; continue; }
    const cx = (e[0] * x + e[4] * y + e[8] * z + e[12]) / cw;
    const cy = (e[1] * x + e[5] * y + e[9] * z + e[13]) / cw;
    sx[i] = (cx + 1) * w; sy[i] = (1 - cy) * h; sz[i] = cw;
    sv[i] = cx > -1.1 && cx < 1.1 && cy > -1.1 && cy < 1.1 ? 1 : 0;
  }
}
// on-screen diameter of a star in CSS px, clamped exactly like the vertex shader
function pxSize(i) {
  let s = geom.attributes.size.array[i] * mat.uniforms.uScale.value / renderer.getPixelRatio() / sz[i];
  const hl = geom.attributes.hl.array[i];
  if (mat.uniforms.uFocus.value > 0.5) s *= hl > 1.5 ? 2.4 : hl > 0.5 ? 1.35 : 0.7 + 0.3 * nearness(i);
  return Math.min(Math.max(s, 1.6), MAX_PX);
}
// 0..1: how close a star is to the camera (dimmed stars fade back in, same curve as the shader)
function nearness(i) {
  const t = Math.min(1, Math.max(0, (NEAR_FAR - sz[i]) / (NEAR_FAR - NEAR_FULL)));
  return t * t * (3 - 2 * t);
}
// radius of the solid part of the disc (the fragment shader's core fades out at ~0.72 of the radius)
const coreR = (i) => pxSize(i) * 0.36;
function pickAt(mx, my, minR = 6) {
  const focus = mat.uniforms.uFocus.value > 0.5;
  const hl = geom.attributes.hl.array;
  let best = -1, bestD = Infinity, bestF = -1;
  for (let i = 0; i < N; i++) {
    if (!sv[i]) continue;
    const dx = sx[i] - mx, dy = sy[i] - my;
    const d2 = dx * dx + dy * dy;
    const cr = coreR(i);
    const r = Math.max(minR, cr + 3);
    if (d2 > r * r) continue;
    const edge = Math.sqrt(d2) - cr;
    const f = focus ? (hl[i] > 0.5 ? 1 : 0) : 0;
    if (f > bestF || (f === bestF && edge < bestD)) { best = i; bestD = edge; bestF = f; }
  }
  return best;
}

// ---------------------------------------------------------------- labels
const POOL = 140;
const pool = [];
function buildLabels() {
  const root = $('labels');
  for (let k = 0; k < POOL; k++) {
    const el = document.createElement('div');
    el.className = 'lbl';
    el.style.display = 'none';
    root.appendChild(el);
    pool.push({ el, text: '', cls: '' });
  }
}
function placeLabels() {
  const boxes = [];
  let used = 0;
  const W = innerWidth, H = innerHeight;
  const overlaps = (x, y, w, h) => {
    for (const b of boxes) if (x < b[0] + b[2] && x + w > b[0] && y < b[1] + b[3] && y + h > b[1]) return true;
    return false;
  };
  const put = (text, x, y, cls, color, anchorCenter, force) => {
    if (used >= POOL) return false;
    const fs = cls === 'scene' ? 20 : cls === 'sel' ? 17 : cls === 'big' ? 14 : 12.5;
    const w = text.length * fs * 0.56 + 6, h = fs + 4;
    const bx = anchorCenter ? x - w / 2 : x, by = y - h / 2;
    if (bx > W || by > H || bx + w < 0 || by + h < 0) return false;
    if (!force && overlaps(bx, by, w, h)) return false;
    boxes.push([bx, by, w, h]);
    const p = pool[used++];
    if (p.text !== text) { p.el.textContent = text; p.text = text; }
    const c = 'lbl ' + cls;
    if (p.cls !== c) { p.el.className = c; p.cls = c; }
    p.el.style.color = color || '';
    p.el.style.transform = `translate(${bx.toFixed(1)}px, ${by.toFixed(1)}px)`;
    p.el.style.display = '';
    return true;
  };
  // the cursor area stays clear so the tooltip is readable
  if (S.hover >= 0) boxes.push([mouse.x - 10, mouse.y - 30, 220, 40]);

  if (S.labels || S.sel >= 0) {
    const off = (i) => coreR(i) + 6;
    if (S.sel >= 0 && sv[S.sel]) put(D.names[S.sel], sx[S.sel] + off(S.sel), sy[S.sel], 'sel', null, false, true);
    if (S.sel >= 0) for (const j of D.nbrs[S.sel].slice(0, 25)) if (sv[j]) put(D.names[j], sx[j] + off(j), sy[j], 'nbr');
  }
  if (S.labels) {
    // Candidates are ranked by on-screen size, not global popularity: zoomed out that
    // favours well-known artists (they are drawn bigger), zoomed in it favours whatever
    // is close to you, however obscure. Dimmed stars count once they are near.
    const focusOn = mat.uniforms.uFocus.value > 0.5;
    const hlA = geom.attributes.hl.array;
    const cand = [];
    const minPx = S.scene >= 0 ? 2.5 : 4;
    for (let i = 0; i < N; i++) {
      if (!sv[i] || i === S.sel) continue;
      const dim = focusOn && hlA[i] < 0.5;
      if (dim && nearness(i) < 0.35) continue;
      const px = pxSize(i);
      if (px < minPx) continue;
      cand.push([dim ? px * 0.6 : px, i, dim]);
    }
    cand.sort((a, b) => b[0] - a[0]);
    const label = ([, i, dim]) => {
      const px = pxSize(i);
      put(D.names[i], sx[i] + px * 0.36 + 5, sy[i], dim ? 'dim' : px > 12 ? 'big' : '');
    };
    let k = 0;
    for (; k < cand.length && cand[k][0] >= 14 && used < POOL; k++) label(cand[k]);
    const dist = camera.position.distanceTo(controls.target);
    // scene names when zoomed out
    if (S.sel < 0 && dist > 110) {
      const v = new THREE.Vector3();
      D.clusters.forEach((c, ci) => {
        if (S.scene >= 0 && S.scene !== ci) return;
        if (c.size < N * 0.004 && S.scene !== ci) return;
        v.set(c.c3[0] * (1 - mix) + c.c2[0] * mix, c.c3[1] * (1 - mix) + c.c2[1] * mix, c.c3[2] * (1 - mix));
        v.project(camera);
        if (v.z > 1) return;
        put(D.sceneName[ci], (v.x + 1) * W / 2, (1 - v.y) * H / 2, 'scene', c.color, true);
      });
    }
    for (; k < cand.length && used < POOL; k++) label(cand[k]);
  }
  for (let k = used; k < POOL; k++) if (pool[k].el.style.display !== 'none') pool[k].el.style.display = 'none';
}

// ---------------------------------------------------------------- loop
let last = 0;
function frame(now) {
  requestAnimationFrame(frame);
  for (let k = tweens.length - 1; k >= 0; k--) {
    const t = tweens[k];
    if (t.dead) { tweens.splice(k, 1); continue; }
    const p = Math.min(1, (now - t.t0) / t.ms);
    t.fn(ease(p));
    if (p >= 1) { tweens.splice(k, 1); t.done && t.done(); }
  }
  if (posDirty) {
    for (let i = 0; i < N * 3; i++) P[i] = P3[i] + (P2[i] - P3[i]) * mix;
    geom.attributes.position.needsUpdate = true;
    updateLines();
    posDirty = false;
  }
  controls.update();
  project();

  if (mouse.inside && !mouse.down) {
    const h = pickAt(mouse.x, mouse.y);
    if (h !== S.hover) {
      S.hover = h;
      canvas.classList.toggle('pointing', h >= 0);
    }
    const tip = $('tip');
    if (h >= 0) {
      tip.innerHTML = `${esc(D.names[h])}<span class="meta">${esc(D.sceneName[D.cluster[h]])}</span>`;
      tip.style.left = `${mouse.x + 14}px`; tip.style.top = `${mouse.y - 32}px`;
      tip.hidden = false;
    } else tip.hidden = true;
  }
  placeLabels();
  renderer.render(scene, camera);
}

load();
