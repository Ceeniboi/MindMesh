// ============================
// Config
// ============================
const API_BASE = "http://127.0.0.1:8000"; // start: uvicorn backend:app --reload

// Basic global error logger so silent script errors don't block UI without clues
window.addEventListener("error", (e) => {
  console.error("[JS ERROR]", e.message, e.error || "");
});
window.addEventListener("unhandledrejection", (e) => {
  console.error("[PROMISE REJECTION]", e.reason);
});

// ============================
// Graph setup
// ============================
const Graph = ForceGraph3D()(document.getElementById("graph"))
  .graphData({ nodes: [], links: [] })
  .nodeLabel(n => (n.label || n.id || "").trim() || (n.id ? n.id.replace(/[_-]+/g, " ") : ""))
  .linkColor(() => "rgba(255,255,255,0.45)")
  .linkWidth(1.4)
  .backgroundColor("#06070a")
  .linkOpacity(0.55)
  .linkDirectionalParticles(2)
  .linkDirectionalParticleSpeed(0.004)
  .nodeThreeObject(node => makeNodeMesh(node));

Graph.onNodeClick(node => showCard(node)); // lazy dossier load on click

// ============================
// Space background + lights
// ============================
addSpaceBackground(Graph);
setupLights(Graph);

// ============================
// Starter graph
// ============================
const starterData = {
  nodes: [
    { id: "MindMesh", label: "MindMesh", val: 12, color: "#00ff88" },
    { id: "Type a topic ↑", label: "Type a topic ↑", val: 6, color: "#58c7ff" }
  ],
  links: [{ source: "MindMesh", target: "Type a topic ↑" }]
};
Graph.graphData(starterData);

// ============================
// DOM references
// ============================
const topicInput = document.getElementById("topicInput");
const generateBtn = document.getElementById("generateBtn");

// Info card
const infoCard = document.getElementById("infoCard");
const closeCardBtn = document.getElementById("closeCard");
const cardTitle = document.getElementById("cardTitle");
const cardSummary = document.getElementById("cardSummary");

// Panels
const cardDefinition = document.getElementById("cardDefinition");
const cardKeyIdeas = document.getElementById("cardKeyIdeas");
const cardAliases = document.getElementById("cardAliases");
const cardPapers = document.getElementById("cardPapers");
const cardQuotes = document.getElementById("cardQuotes");
const cardDatasets = document.getElementById("cardDatasets");
const cardControversies = document.getElementById("cardControversies");
const cardPrompts = document.getElementById("cardPrompts");
const cardTimeline = document.getElementById("cardTimeline");


// ============================
// Progress bars (per tab)
// ============================
const progressEls = {
  overview: document.getElementById("prog-overview"),
  key_ideas: document.getElementById("prog-key_ideas"),
  aliases: document.getElementById("prog-aliases"),
  papers: document.getElementById("prog-papers"),
  quotes: document.getElementById("prog-quotes"),
  datasets_tools: document.getElementById("prog-datasets_tools"),
  controversies: document.getElementById("prog-controversies"),
  search_prompts: document.getElementById("prog-search_prompts"),
  timeline: document.getElementById("prog-timeline"),
};


// ============================
// Tabs (robust + accessible)
// ============================
const tabBar = document.getElementById("tabBar");
const panels = Array.from(document.querySelectorAll(".tab-panel"));

function getTabButtons() {
  return Array.from(tabBar.querySelectorAll('.tab-btn'));
}

function activateTab(tabName) {
  const btns = getTabButtons();
  btns.forEach(b => {
    const isActive = b.dataset.tab === tabName;
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", isActive ? "true" : "false");
    b.tabIndex = isActive ? 0 : -1;
  });
  panels.forEach(p => {
    const show = p.dataset.tab === tabName;
    p.hidden = !show;
  });
}

function firstAvailableTab() {
  // Keep “overview” default if present, otherwise first visible
  const btns = getTabButtons();
  const pref = btns.find(b => b.dataset.tab === "overview");
  return (pref || btns[0])?.dataset.tab;
}

// Click (event delegation, so it works even if buttons re-render)
tabBar.addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  activateTab(btn.dataset.tab);
});

// Keyboard: ←/→ to switch tabs
tabBar.addEventListener("keydown", (e) => {
  const btns = getTabButtons();
  const current = document.activeElement.closest(".tab-btn");
  if (!current) return;
  const idx = btns.indexOf(current);
  if (e.key === "ArrowRight") {
    e.preventDefault();
    const next = btns[(idx + 1) % btns.length];
    next.focus(); activateTab(next.dataset.tab);
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    const prev = btns[(idx - 1 + btns.length) % btns.length];
    prev.focus(); activateTab(prev.dataset.tab);
  }
});



// ============================
// State
// ============================
let CURRENT_TOPIC = null;
const dossierCache = new Map(); // key: `${topic}||${node}`
let dossierAbort = null;
const cacheKey = (topic, id) => `${topic}||${id}`;

// ============================
// Helpers
// ============================
function escapeHTML(s) { return (s ?? "").toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function li(text) { return `<li>${escapeHTML(text)}</li>`; }

function renderList(el, arr, renderItem) {
  el.innerHTML = "";
  if (!Array.isArray(arr) || arr.length === 0) {
    el.innerHTML = `<li class="muted">No items.</li>`;
    return;
  }
  el.innerHTML = arr.map(item => renderItem ? renderItem(item) : li(item)).join("");
}

function renderQuotes(el, quotes) {
  el.innerHTML = "";
  if (!Array.isArray(quotes) || quotes.length === 0) {
    el.innerHTML = `<p class="muted">No quotes.</p>`;
    return;
  }
  el.innerHTML = quotes.map(q => {
    const quote = escapeHTML(q.quote || "");
    const st = escapeHTML(q.source_title || "");
    const url = q.url ? `<a href="${q.url}" target="_blank" rel="noopener">${st || "source"}</a>` : st;
    return `<blockquote><p>“${quote}”</p><cite>— ${url}</cite></blockquote>`;
  }).join("");
}

function googleLink(q) { return `https://www.google.com/search?q=${encodeURIComponent(q)}`; }

async function fetchMap(topic) {
  const url = `${API_BASE}/map?topic=${encodeURIComponent(topic)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  const data = await res.json();
  if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.links)) {
    throw new Error("Bad map data: nodes/links missing.");
  }
  return data;
}

async function fetchDossier(topic, id, { signal } = {}) {
  const url = `${API_BASE}/node?topic=${encodeURIComponent(topic)}&id=${encodeURIComponent(id)}`;
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  return res.json(); // { topic, id, dossier, error? }
}

function setLoading(loading) {
  generateBtn.disabled = loading;
  generateBtn.textContent = loading ? "Generating…" : "Generate Map";
}

function setAllTabsLoading(isLoading) {
  if (!progressEls) return;
  Object.values(progressEls).forEach(el => {
    if (el) el.style.display = isLoading ? "block" : "none";
  });
}


function clearPanels() {
  cardDefinition.textContent = "—";
  cardKeyIdeas.innerHTML = "";
  cardAliases.innerHTML = "";
  cardPapers.innerHTML = "";
  cardQuotes.innerHTML = `<p class="muted">No quotes.</p>`;
  cardDatasets.innerHTML = "";
  cardControversies.innerHTML = "";
  cardPrompts.innerHTML = "";
  cardTimeline.innerHTML = "";
}

function renderDossier(d) {
  // Overview
  cardDefinition.textContent = d.definition || "—";
  // Key ideas
  renderList(cardKeyIdeas, d.key_ideas, s => li(escapeHTML(s)));
  // Aliases
  renderList(cardAliases, d.aliases, s => li(escapeHTML(s)));
  // Papers
  renderList(cardPapers, d.papers, p => {
    const title = escapeHTML(p.title || "");
    const url = p.url ? `<a href="${p.url}" target="_blank" rel="noopener">${title}</a>` : title;
    const year = p.year != null ? ` <span class="year">(${escapeHTML(p.year)})</span>` : "";
    const why = p.reason ? `<div class="why">${escapeHTML(p.reason)}</div>` : "";
    return `<li>${url}${year}${why}</li>`;
  });
  // Quotes
  renderQuotes(cardQuotes, d.quotes);
  // Datasets/Tools
  renderList(cardDatasets, d.datasets_tools, dt => {
    const name = escapeHTML(dt.name || "");
    const url = dt.url ? `<a href="${dt.url}" target="_blank" rel="noopener">${name}</a>` : name;
    const note = dt.note ? ` — <span class="muted">${escapeHTML(dt.note)}</span>` : "";
    return `<li>${url}${note}</li>`;
  });
  // Controversies
  renderList(cardControversies, d.controversies, s => li(escapeHTML(s)));
  // Search prompts
  renderList(cardPrompts, d.search_prompts, q => {
    const qText = escapeHTML(q);
    return `<li><code>${qText}</code> <a class="out" href="${googleLink(q)}" target="_blank" rel="noopener">Search ↗︎</a></li>`;
  });
  // Timeline
  renderList(cardTimeline, d.timeline, t => {
    const year = (t && t.year != null) ? `<span class="badge">${escapeHTML(t.year)}</span>` : `<span class="badge">—</span>`;
    const ev = escapeHTML(t && t.event ? t.event : "");
    return `<li>${year} <span>${ev}</span></li>`;
  });
}

async function showCard(node) {
  const topic = CURRENT_TOPIC || (topicInput.value || "").trim();

  // Show card + reset state
  cardTitle.textContent = node.label || node.id || "Node";
  infoCard.style.display = "block";
  cardSummary.textContent = "Loading dossier…";
  setAllTabsLoading(true);
  clearPanels();
  activateTab(firstAvailableTab()); // ensure tabs actually switch a panel now

  if (!topic) {
    setAllTabsLoading(false);
    cardSummary.textContent = "Enter a topic and generate a map first.";
    return;
  }

  const key = `${topic}||${node.id}`;
  if (dossierCache.has(key)) {
    const { dossier } = dossierCache.get(key);
    cardSummary.textContent = "";           // ⬅ clear placeholder
    renderDossier(dossier);
    setAllTabsLoading(false);
    return;
  }

  if (dossierAbort) dossierAbort.abort();
  dossierAbort = new AbortController();

  try {
    const payload = await fetchDossier(topic, node.id, { signal: dossierAbort.signal });
    dossierCache.set(key, payload);
    cardSummary.textContent = "";           // ⬅ clear placeholder
    renderDossier(payload.dossier);
  } catch (err) {
    if (err.name === "AbortError") return;
    console.error("[dossier] fetch failed", err);
    cardSummary.textContent = node.summary || "No summary available.";
    if (Array.isArray(node.sources) && node.sources.length) {
      cardPapers.innerHTML = node.sources.map(s => {
        const title = (s.title || "").replace(/</g,"&lt;").replace(/>/g,"&gt;");
        const year = s.year != null ? ` <span class="year">(${s.year})</span>` : "";
        const srcTag = s.source ? ` — <em>${s.source}</em>` : "";
        return `<li><a href="${s.url}" target="_blank" rel="noopener">${title}</a>${year}${srcTag}</li>`;
      }).join("");
    }
  } finally {
    setAllTabsLoading(false);
    if (dossierAbort && dossierAbort.signal.aborted === false) dossierAbort = null;
  }
}



// Close card
closeCardBtn.addEventListener("click", () => (infoCard.style.display = "none"));

// ============================
// Events
// ============================
generateBtn.addEventListener("click", async () => {
  const topic = (topicInput.value || "").trim();
  if (!topic) { alert("Enter a topic first."); return; }

  try {
    setLoading(true);
    const data = await fetchMap(topic);
    CURRENT_TOPIC = topic;
    dossierCache.clear();
    if (dossierAbort) dossierAbort.abort();
    infoCard.style.display = "none";
    Graph.graphData(data); // graph renders immediately
  } catch (err) {
    console.error(err);
    alert("Map failed. Ensure backend at 127.0.0.1:8000 is running and check console.");
  } finally {
    setLoading(false);
  }
});

// Enter key
topicInput.addEventListener("keydown", (e) => { if (e.key === "Enter") generateBtn.click(); });

/* ===========================
   Visual Helpers (space + nodes + lights)
=========================== */

// Cache a circular glow texture so sprites have no square edges
let _glowTexture = null;
function getGlowTexture() {
  if (_glowTexture) return _glowTexture;
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');

  const grd = ctx.createRadialGradient(size/2, size/2, 0, size/2, size/2, size/2);
  grd.addColorStop(0.0, "rgba(255,255,255,0.90)");
  grd.addColorStop(0.35,"rgba(255,255,255,0.30)");
  grd.addColorStop(1.0, "rgba(255,255,255,0.00)");

  ctx.fillStyle = grd;
  ctx.fillRect(0,0,size,size);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
  _glowTexture = tex;
  return _glowTexture;
}

function makeNodeMesh(node) {
  const size = Math.max(3, Math.cbrt(node.val || 6));
  const group = new THREE.Group();

  const col = new THREE.Color(node.color || "#58c7ff");

  const coreGeom = new THREE.SphereGeometry(size, 28, 28);
  const coreMat  = new THREE.MeshStandardMaterial({
    color: col, metalness: 0.55, roughness: 0.28,
    emissive: col.clone().multiplyScalar(0.15), emissiveIntensity: 0.7
  });
  const core = new THREE.Mesh(coreGeom, coreMat);
  group.add(core);

  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: getGlowTexture(), color: col, transparent: true, opacity: 0.5,
    depthWrite: false, blending: THREE.AdditiveBlending
  }));
  glow.scale.set(size * 4.8, size * 4.8, 1);
  group.add(glow);

  return group;
}

function addSpaceBackground(graph) {
  try {
    const scene = graph.scene();

    const skyGeo = new THREE.SphereGeometry(6000, 32, 32);
    const skyMat = new THREE.MeshBasicMaterial({ color: 0x05070d, side: THREE.BackSide });
    const sky = new THREE.Mesh(skyGeo, skyMat);
    scene.add(sky);

    const starCount = 1400;
    const positions = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount; i++) {
      const r = 3600 + Math.random() * 2400;
      const theta = Math.acos(2 * Math.random() - 1);
      const phi = 2 * Math.PI * Math.random();
      const x = r * Math.sin(theta) * Math.cos(phi);
      const y = r * Math.sin(theta) * Math.sin(phi);
      const z = r * Math.cos(theta);
      positions.set([x, y, z], i * 3);
    }
    const starGeo = new THREE.BufferGeometry();
    starGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    const starMat = new THREE.PointsMaterial({
      color: 0xaec9ff, size: 1.1, transparent: true, opacity: 0.55,
      depthWrite: false, blending: THREE.AdditiveBlending
    });
    const stars = new THREE.Points(starGeo, starMat);
    stars.rotation.y = Math.random() * Math.PI;
    scene.add(stars);

    const shootingStars = [];
    const MAX_SHOOTERS = 2;

    function spawnShootingStar() {
      const startR = 4400 + Math.random() * 800;
      const theta = Math.random() * Math.PI;
      const phi = Math.random() * Math.PI * 2;
      const start = new THREE.Vector3(
        startR * Math.sin(theta) * Math.cos(phi),
        startR * Math.sin(theta) * Math.sin(phi),
        startR * Math.cos(theta)
      );

      const dir = new THREE.Vector3(
        (Math.random() - 0.5),
        (Math.random() - 0.5),
        (Math.random() - 0.5)
      ).normalize();

      const length = 40 + Math.random() * 60;
      const geom = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, 0),
        dir.clone().multiplyScalar(length)
      ]);
      const mat = new THREE.LineBasicMaterial({
        color: 0x7aa8ff, transparent: true, opacity: 0.35,
        depthWrite: false, blending: THREE.AdditiveBlending
      });
      const line = new THREE.Line(geom, mat);
      line.position.copy(start);

      const scene = graph.scene();
      scene.add(line);
      shootingStars.push({ mesh: line, velocity: dir.multiplyScalar(6 + Math.random() * 8), life: 0, maxLife: 2.2 + Math.random() * 1.2 });

      if (shootingStars.length > MAX_SHOOTERS) {
        const old = shootingStars.shift();
        scene.remove(old.mesh);
      }
    }

    function animateBackground() {
      const t = performance.now() / 1000;
      stars.rotation.y += 0.00008;
      starMat.opacity = 0.52 + 0.05 * Math.sin(t * 0.7);
      if (Math.random() < 0.004) spawnShootingStar();
      for (let i = shootingStars.length - 1; i >= 0; i--) {
        const s = shootingStars[i];
        s.mesh.position.add(s.velocity);
        s.life += 0.016;
        const fade = Math.max(0, 1 - s.life / s.maxLife);
        s.mesh.material.opacity = 0.35 * fade;
        if (s.life >= s.maxLife) {
          const scene = graph.scene();
          scene.remove(s.mesh);
          shootingStars.splice(i, 1);
        }
      }
      requestAnimationFrame(animateBackground);
    }
    animateBackground();
  } catch (e) {
    console.warn("Space background failed; using flat color.", e);
  }
}

function setupLights(graph) {
  const scene = graph.scene();
  const camera = graph.camera();

  const hemi = new THREE.HemisphereLight(0x2a3f66, 0x000000, 0.85);
  scene.add(hemi);

  const ambient = new THREE.AmbientLight(0xffffff, 0.22);
  scene.add(ambient);

  const camLight = new THREE.PointLight(0xffffff, 1.0, 0, 2);
  scene.add(camLight);

  function followCamera() {
    camLight.position.copy(camera.position);
    requestAnimationFrame(followCamera);
  }
  followCamera();
}

// ===================================================================
// Typewriter placeholder (unchanged)
// ===================================================================
const demoTopics = [
  "Neural machine translation",
  "Graph neural networks",
  "Quantum computing",
  "Reinforcement learning",
  "Photosynthesis",
  "Genome editing",
  "Transformers in NLP"
];

startTypewriter(topicInput, demoTopics, { typeDelay: 75, eraseDelay: 40, holdDelay: 1100, loopDelay: 600 });

function startTypewriter(el, phrases, opts = {}) {
  const type = opts.typeDelay ?? 80;
  const erase = opts.eraseDelay ?? 45;
  const hold = opts.holdDelay ?? 1000;
  const loop = opts.loopDelay ?? 500;

  let i = 0, j = 0, deleting = false, timerId = null;

  function tick() {
    if (document.activeElement === el || (el.value && el.value.length > 0)) {
      el.placeholder = "";
      timerId = setTimeout(tick, 400);
      return;
    }
    const word = phrases[i] || "";
    if (!deleting) {
      j++; el.placeholder = word.slice(0, j);
      if (j === word.length) { deleting = true; timerId = setTimeout(tick, hold); return; }
      timerId = setTimeout(tick, type);
    } else {
      j--; el.placeholder = word.slice(0, j) || " ";
      if (j === 0) { deleting = false; i = (i + 1) % phrases.length; timerId = setTimeout(tick, loop); return; }
      timerId = setTimeout(tick, erase);
    }
  }
  tick();
  el.addEventListener("focus", () => { if (timerId) clearTimeout(timerId); el.placeholder = ""; });
  el.addEventListener("blur", () => { if (!el.value) tick(); });
  return { stop: () => timerId && clearTimeout(timerId), start: () => tick() };
}
