// ============================
// Config
// ============================
const API_BASE = "http://127.0.0.1:8000"; // backend started with: uvicorn backend:app --reload

// ============================
// Graph setup (empty to start)
// ============================
const Graph = ForceGraph3D()(document.getElementById("graph"))
  .graphData({ nodes: [], links: [] })
  // Title-only tooltip (with safe spacing fallback)
  .nodeLabel(n => {
    const label = n.label || n.id || "";
    return label || (n.id ? n.id.replace(/[_-]+/g, " ") : "");
  })
  .linkColor(() => "rgba(255,255,255,0.45)")
  .linkWidth(1.4)
  .backgroundColor("#06070a")
  .linkOpacity(0.55)
  .linkDirectionalParticles(2)
  .linkDirectionalParticleSpeed(0.004)
  // Colored glossy spheres + soft circular glow
  .nodeThreeObject(node => makeNodeMesh(node));

// Attach interactions
Graph.onNodeClick(node => showCard(node));

// ============================
// Space background (subtle)
// ============================
addSpaceBackground(Graph);   // sky + stars + subdued shooting stars
setupLights(Graph);          // ambient + point light that follows camera

// ============================
// Starter graph (so canvas isn't empty on load)
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

// --- Info card helpers ---
const infoCard = document.getElementById("infoCard");
const closeCardBtn = document.getElementById("closeCard");
const cardTitle = document.getElementById("cardTitle");
const cardSummary = document.getElementById("cardSummary");
const cardSources = document.getElementById("cardSources");

function showCard(node) {
  cardTitle.textContent = node.label || node.id || "Node";
  cardSummary.textContent = node.summary || "No summary available.";

  const srcs = Array.isArray(node.sources) ? node.sources : [];
  if (srcs.length) {
    const items = srcs.map(s => {
      const year = s.year ? ` (${s.year})` : "";
      const srcTag = s.source ? ` — <em>${s.source}</em>` : "";
      const safeTitle = (s.title || "").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return `<li><a href="${s.url}" target="_blank" rel="noopener">${safeTitle}</a>${year}${srcTag}</li>`;
    }).join("");
    cardSources.innerHTML = `<h4>Sources</h4><ul>${items}</ul>`;
  } else {
    cardSources.innerHTML = `<h4>Sources</h4><p>No sources listed.</p>`;
  }
  infoCard.style.display = "block";
}
closeCardBtn.addEventListener("click", () => (infoCard.style.display = "none"));

// ============================
// Fetch + render helpers
// ============================
async function fetchMap(topic) {
  const url = `${API_BASE}/map?topic=${encodeURIComponent(topic)}`;
  const res = await fetch(url);

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  const data = await res.json();

  // Basic sanity checks so we don't break the graph
  if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.links)) {
    throw new Error("Bad map data: nodes/links missing.");
  }
  return data;
}

function setLoading(loading) {
  if (loading) {
    generateBtn.disabled = true;
    generateBtn.textContent = "Generating…";
  } else {
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate Map";
  }
}

// ============================
// Events
// ============================
generateBtn.addEventListener("click", async () => {
  const topic = (topicInput.value || "").trim();
  if (!topic) {
    alert("Enter a topic first.");
    return;
  }

  try {
    setLoading(true);
    const data = await fetchMap(topic);
    Graph.graphData(data);
  } catch (err) {
    console.error(err);
    alert("Could not generate a map. Make sure the backend is running at 127.0.0.1:8000 and check the terminal for errors.");
  } finally {
    setLoading(false);
  }
});

// Optional: press Enter to generate
topicInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") generateBtn.click();
});


// ===================================================================
//                     Visual Helpers (space + nodes + lights)
// ===================================================================

// Cache a circular glow texture so sprites have no square edges
let _glowTexture = null;
function getGlowTexture() {
  if (_glowTexture) return _glowTexture;
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');

  // radial gradient (center bright → edge transparent)
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

// Build a high-quality node: colored glossy 3D sphere + soft additive glow
function makeNodeMesh(node) {
  const size = Math.max(3, Math.cbrt(node.val || 6));
  const group = new THREE.Group();

  const col = new THREE.Color(node.color || "#58c7ff");

  // Colored glossy core
  const coreGeom = new THREE.SphereGeometry(size, 28, 28);
  const coreMat  = new THREE.MeshStandardMaterial({
    color: col,                 // colored sphere
    metalness: 0.55,
    roughness: 0.28,
    emissive: col.clone().multiplyScalar(0.15),
    emissiveIntensity: 0.7
  });
  const core = new THREE.Mesh(coreGeom, coreMat);
  group.add(core);

  // Soft circular glow using a texture (no square artifacts)
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: getGlowTexture(),
    color: col,
    transparent: true,
    opacity: 0.5,
    depthWrite: false,
    blending: THREE.AdditiveBlending
  }));
  glow.scale.set(size * 4.8, size * 4.8, 1);
  group.add(glow);

  return group;
}

// Add sky, stars that twinkle subtly, and background shooting stars (very subtle)
function addSpaceBackground(graph) {
  try {
    const scene = graph.scene();

    // Sky dome
    const skyGeo = new THREE.SphereGeometry(6000, 32, 32);
    const skyMat = new THREE.MeshBasicMaterial({ color: 0x05070d, side: THREE.BackSide });
    const sky = new THREE.Mesh(skyGeo, skyMat);
    scene.add(sky);

    // Starfield (points)
    const starCount = 1400;
    const positions = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount; i++) {
      const r = 3600 + Math.random() * 2400; // keep far
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
      color: 0xaec9ff,
      size: 1.1,
      transparent: true,
      opacity: 0.55,              // subtler stars
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    const stars = new THREE.Points(starGeo, starMat);
    stars.rotation.y = Math.random() * Math.PI;
    scene.add(stars);

    // Shooting stars pool (subtle, backgroundy)
    const shootingStars = [];
    const MAX_SHOOTERS = 2;       // fewer on screen

    function spawnShootingStar() {
      const startR = 4400 + Math.random() * 800; // further back
      const theta = Math.random() * Math.PI;
      const phi = Math.random() * Math.PI * 2;
      const start = new THREE.Vector3(
        startR * Math.sin(theta) * Math.cos(phi),
        startR * Math.sin(theta) * Math.sin(phi),
        startR * Math.cos(theta)
      );

      // Drift across background (slow)
      const dir = new THREE.Vector3(
        (Math.random() - 0.5),
        (Math.random() - 0.5),
        (Math.random() - 0.5)
      ).normalize();

      const length = 40 + Math.random() * 60; // shorter trail
      const geom = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, 0),
        dir.clone().multiplyScalar(length)
      ]);
      const mat = new THREE.LineBasicMaterial({
        color: 0x7aa8ff,          // dimmer
        transparent: true,
        opacity: 0.35,            // lower opacity
        depthWrite: false,
        blending: THREE.AdditiveBlending
      });
      const line = new THREE.Line(geom, mat);
      line.position.copy(start);

      scene.add(line);
      shootingStars.push({
        mesh: line,
        velocity: dir.multiplyScalar(6 + Math.random() * 8), // slower
        life: 0,
        maxLife: 2.2 + Math.random() * 1.2
      });

      if (shootingStars.length > MAX_SHOOTERS) {
        const old = shootingStars.shift();
        scene.remove(old.mesh);
      }
    }

    function animateBackground() {
      const t = performance.now() / 1000;

      // gentle twinkle/rotation
      stars.rotation.y += 0.00008;
      starMat.opacity = 0.52 + 0.05 * Math.sin(t * 0.7);

      // Rare spawn so it feels background
      if (Math.random() < 0.004) spawnShootingStar();

      // Update shooters
      for (let i = shootingStars.length - 1; i >= 0; i--) {
        const s = shootingStars[i];
        s.mesh.position.add(s.velocity);
        s.life += 0.016;
        const fade = Math.max(0, 1 - s.life / s.maxLife);
        s.mesh.material.opacity = 0.35 * fade;

        if (s.life >= s.maxLife) {
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

// Basic lighting so colored spheres render as glossy 3D
function setupLights(graph) {
  const scene = graph.scene();
  const camera = graph.camera();

  const hemi = new THREE.HemisphereLight(0x2a3f66, 0x000000, 0.85);
  scene.add(hemi);

  const ambient = new THREE.AmbientLight(0xffffff, 0.22);
  scene.add(ambient);

  // Point light that follows the camera (adds specular highlight)
  const camLight = new THREE.PointLight(0xffffff, 1.0, 0, 2);
  scene.add(camLight);

  function followCamera() {
    camLight.position.copy(camera.position);
    requestAnimationFrame(followCamera);
  }
  followCamera();
}


// ===================================================================
//                Typewriter placeholder for topic input
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

startTypewriter(topicInput, demoTopics, {
  typeDelay: 75,     // ms per character while typing
  eraseDelay: 40,    // ms per character while deleting
  holdDelay: 1100,   // pause at end of word
  loopDelay: 600     // pause before starting next word
});

function startTypewriter(el, phrases, opts = {}) {
  const type = opts.typeDelay ?? 80;
  const erase = opts.eraseDelay ?? 45;
  const hold = opts.holdDelay ?? 1000;
  const loop = opts.loopDelay ?? 500;

  let i = 0, j = 0, deleting = false, timerId = null;

  function tick() {
    // If user is interacting or has typed something, pause the animation
    if (document.activeElement === el || (el.value && el.value.length > 0)) {
      el.placeholder = "";
      timerId = setTimeout(tick, 400);
      return;
    }

    const word = phrases[i] || "";
    if (!deleting) {
      j++;
      el.placeholder = word.slice(0, j);
      if (j === word.length) {
        deleting = true;
        timerId = setTimeout(tick, hold);
        return;
      }
      timerId = setTimeout(tick, type);
    } else {
      j--;
      el.placeholder = word.slice(0, j) || " ";
      if (j === 0) {
        deleting = false;
        i = (i + 1) % phrases.length;
        timerId = setTimeout(tick, loop);
        return;
      }
      timerId = setTimeout(tick, erase);
    }
  }

  tick();

  // Pause/resume on focus/blur
  el.addEventListener("focus", () => {
    if (timerId) clearTimeout(timerId);
    el.placeholder = "";
  });
  el.addEventListener("blur", () => {
    if (!el.value) tick();
  });

  return {
    stop: () => timerId && clearTimeout(timerId),
    start: () => tick()
  };
}
