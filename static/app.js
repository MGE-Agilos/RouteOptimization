/* ── State ───────────────────────────────────────────────────── */
const state = {
  baseGeoJSON:   null,
  simGeoJSON:    null,
  baseStats:     null,
  simStats:      null,
  signs:         [],          // [{id, edge_id, type, roadName, highway}]
  selectedEdge:  null,        // {edge_id, name, highway, score, length, maxspeed}
  selectedSign:  null,        // sign type string
  viewMode:      "current",   // "current" | "simulated" | "diff"
  simDone:       false,
};

/* ── Sign definitions ────────────────────────────────────────── */
const SIGN_TYPES = [
  {
    type:   "closure",
    icon:   "🚧",
    name:   "Fermeture de route",
    desc:   "Supprime la route du réseau. Le trafic se redistribue.",
    effect: "Réduit trafic local",
    cls:    "effect-dec",
  },
  {
    type:   "zone30",
    icon:   "🔴",
    name:   "Zone 30 km/h",
    desc:   "Ralentit la circulation, rend la route moins attractive.",
    effect: "Trafic détourné (-)",
    cls:    "effect-dec",
  },
  {
    type:   "zone50",
    icon:   "🟠",
    name:   "Limite 50 km/h",
    desc:   "Limitation modérée, légère redistribution du trafic.",
    effect: "Léger détournement",
    cls:    "effect-dec",
  },
  {
    type:   "priority",
    icon:   "⭐",
    name:   "Route prioritaire",
    desc:   "Rend la route plus attractive, attire plus de trafic.",
    effect: "Augmente le trafic",
    cls:    "effect-inc",
  },
  {
    type:   "oneway",
    icon:   "➡️",
    name:   "Sens unique",
    desc:   "Supprime la direction inverse. Réduit la capacité bidirectionnelle.",
    effect: "Flux dirigé",
    cls:    "effect-neut",
  },
  {
    type:   "deviation",
    icon:   "🔄",
    name:   "Déviation / Travaux",
    desc:   "Ferme la route (travaux). Le trafic cherche un itinéraire alternatif.",
    effect: "Redistribution totale",
    cls:    "effect-inc",
  },
];

/* ── Score → colour ─────────────────────────────────────────── */
function scoreColor(score) {
  if (score >= 65) return "#e74c3c";
  if (score >= 40) return "#e67e22";
  if (score >= 20) return "#f1c40f";
  return "#2ecc71";
}

function deltaColor(delta) {
  if (delta <= -10) return "#27ae60";
  if (delta <= -3)  return "#2ecc71";
  if (delta <   3)  return "#95a5a6";
  if (delta <  10)  return "#e67e22";
  return "#e74c3c";
}

function scoreWeight(score) {
  if (score >= 65) return 4;
  if (score >= 40) return 3;
  if (score >= 20) return 2;
  return 1.2;
}

/* ── Map init ────────────────────────────────────────────────── */
const map = L.map("map", {
  zoomControl: false,
  attributionControl: true,
}).setView([50.668, 4.612], 13);

L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: "© OpenStreetMap, © CARTO",
  maxZoom: 19,
}).addTo(map);

L.control.zoom({ position: "bottomright" }).addTo(map);

let roadLayer  = L.layerGroup().addTo(map);
let signLayer  = L.layerGroup().addTo(map);

/* ── Render roads ────────────────────────────────────────────── */
function renderRoads(geojson, mode) {
  roadLayer.clearLayers();
  if (!geojson) return;

  geojson.features.forEach(feat => {
    const p = feat.properties;
    if (feat.geometry.type !== "LineString") return;

    const latlngs = feat.geometry.coordinates.map(([lng, lat]) => [lat, lng]);

    let color, weight, opacity, dashArray;

    if (p.closed) {
      color = "#888"; weight = 2; opacity = 0.7; dashArray = "6 5";
    } else if (mode === "diff") {
      color     = deltaColor(p.delta || 0);
      weight    = Math.max(2, scoreWeight(p.score));
      opacity   = 0.9;
      dashArray = null;
    } else {
      color     = scoreColor(p.score);
      weight    = scoreWeight(p.score);
      opacity   = 0.9;
      dashArray = null;
    }

    // Ligne visible (non-interactive)
    const visLine = L.polyline(latlngs, {
      color, weight, opacity, dashArray, interactive: false,
    });

    // Zone de détection invisible et large (16px) pour faciliter le clic
    const hitLine = L.polyline(latlngs, {
      color: "#000", weight: 16, opacity: 0, interactive: true,
    });

    let hoverTimer = null;

    hitLine.on("click", (e) => {
      L.DomEvent.stopPropagation(e);
      // Highlight visuel au clic
      visLine.setStyle({ weight: weight + 3, opacity: 1 });
      setTimeout(() => visLine.setStyle({ weight, opacity }), 400);
      openSignPanel(p);
    });

    hitLine.on("mouseover", function (e) {
      visLine.setStyle({ weight: weight + 2, opacity: 1 });
      const latlng = e.latlng;
      hoverTimer = setTimeout(() => {
        L.popup({ closeButton: false, offset: [0, -4], className: "road-popup" })
          .setLatLng(latlng)
          .setContent(buildPopupHtml(p, mode))
          .openOn(map);
      }, 2000);
    });

    hitLine.on("mouseout", function () {
      clearTimeout(hoverTimer);
      map.closePopup();
      visLine.setStyle({ weight, opacity });
    });

    roadLayer.addLayer(visLine);
    roadLayer.addLayer(hitLine);
  });
}

function fmtVehicles(v) {
  if (!v) return "–";
  return v >= 1000 ? `${(v/1000).toFixed(1).replace(".",",")} k` : String(v);
}

function buildPopupHtml(p, mode) {
  const catColor = {
    Critique: "#e74c3c", "Élevé": "#e67e22",
    Modéré: "#e8a020", Faible: "#27ae60",
  }[p.category] || "#aaa";

  let extra = "";
  if (mode === "diff" && p.delta !== 0) {
    const arrow = p.delta > 0 ? "▲" : "▼";
    const col   = p.delta > 0 ? "#e74c3c" : "#27ae60";
    extra = `<div style="color:${col};font-weight:700;margin-top:4px">
      ${arrow} ${p.delta > 0 ? "+" : ""}${p.delta} pts vs. base</div>`;
  }
  if (p.closed) {
    extra = `<div style="color:#e74c3c;font-weight:700;margin-top:4px">🚧 Route fermée</div>`;
  }

  return `
    <div style="min-width:175px">
      <div style="font-weight:700;font-size:14px;margin-bottom:2px">${p.name}</div>
      <div style="font-size:11px;color:#666;margin-bottom:8px">${p.highway} · ${p.length} m</div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="font-size:20px;font-weight:800;color:${catColor}">${p.score}</span>
        <span style="font-size:11px;background:${catColor}22;color:${catColor};
          padding:2px 7px;border-radius:4px;font-weight:600">${p.category}</span>
      </div>
      <div style="font-size:12px;color:#444;background:#f5f5f5;border-radius:5px;
                  padding:5px 8px;display:flex;justify-content:space-between">
        <span>🚗 Véhicules/jour</span>
        <strong>${fmtVehicles(p.vehicles)}</strong>
      </div>
      ${p.veh_delta != null && p.veh_delta !== 0 ? `
      <div style="font-size:11px;margin-top:4px;padding:3px 8px;border-radius:4px;
                  background:${p.veh_delta > 0 ? '#fdecea' : '#eafaf1'};
                  color:${p.veh_delta > 0 ? '#c0392b' : '#1e8449'}">
        ${p.veh_delta > 0 ? '▲' : '▼'} ${p.veh_delta > 0 ? '+' : ''}${p.veh_delta.toLocaleString('fr-BE')} véh/jour vs base
      </div>` : ''}
      ${extra}
      <div style="font-size:10px;color:#999;margin-top:6px">Cliquez pour poser un panneau</div>
    </div>`;
}

/* ── Sign markers ────────────────────────────────────────────── */
function renderSignMarkers() {
  signLayer.clearLayers();
  state.signs.forEach(s => {
    const feat = (state.simGeoJSON || state.baseGeoJSON)?.features
      .find(f => f.properties.edge_id === s.edge_id);
    if (!feat) return;
    const coords = feat.geometry.coordinates;
    const mid = coords[Math.floor(coords.length / 2)];
    const def = SIGN_TYPES.find(t => t.type === s.type);

    const icon = L.divIcon({
      className: "",
      html: `<div style="font-size:20px;filter:drop-shadow(0 2px 4px rgba(0,0,0,.8));
               cursor:pointer;line-height:1">${def?.icon || "📍"}</div>`,
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    });

    L.marker([mid[1], mid[0]], { icon })
      .bindTooltip(`${def?.icon} ${def?.name} – ${s.roadName}`, { direction: "top" })
      .addTo(signLayer);
  });
}

/* ── Sign panel ──────────────────────────────────────────────── */
function openSignPanel(props) {
  state.selectedEdge = props;
  state.selectedSign = null;

  document.getElementById("sp-road-name").textContent = props.name;
  document.getElementById("sp-road-meta").textContent =
    `${props.highway} · ${props.length} m${props.maxspeed ? " · " + props.maxspeed + " km/h" : ""}`;
  document.getElementById("sp-vehicles").textContent =
    props.vehicles ? `🚗 ${props.vehicles.toLocaleString("fr-BE")} véhicules/jour (estimé)` : "";

  const score = props.score;
  const fill  = document.getElementById("sp-score-fill");
  const big   = document.getElementById("sp-score-big");
  const cat   = document.getElementById("sp-score-cat");
  fill.style.width = `${score}%`;
  fill.style.background = scoreColor(score);
  big.textContent = score;
  big.style.color = scoreColor(score);
  cat.textContent = props.category;
  cat.style.color = scoreColor(score);

  // Render sign type cards
  const container = document.getElementById("sign-types-list");
  container.innerHTML = "";
  SIGN_TYPES.forEach(def => {
    const card = document.createElement("div");
    card.className = "sign-card";
    card.dataset.type = def.type;
    card.innerHTML = `
      <div class="sc-icon">${def.icon}</div>
      <div class="sc-info">
        <div class="sc-name">${def.name}</div>
        <div class="sc-desc">${def.desc}</div>
      </div>
      <span class="sc-effect ${def.cls}">${def.effect}</span>`;
    card.addEventListener("click", () => {
      document.querySelectorAll(".sign-card").forEach(c => c.classList.remove("selected"));
      card.classList.add("selected");
      state.selectedSign = def.type;
      document.getElementById("btn-apply-sign").disabled = false;
    });
    container.appendChild(card);
  });

  document.getElementById("btn-apply-sign").disabled = true;
  document.getElementById("sign-panel").classList.add("open");
}

function closeSignPanel() {
  document.getElementById("sign-panel").classList.remove("open");
  state.selectedEdge = null;
  state.selectedSign = null;
}

/* ── Apply a sign ────────────────────────────────────────────── */
function applySign() {
  if (!state.selectedEdge || !state.selectedSign) return;
  const p = state.selectedEdge;

  // Remove previous sign on same edge
  state.signs = state.signs.filter(s => s.edge_id !== p.edge_id);

  const def = SIGN_TYPES.find(t => t.type === state.selectedSign);
  state.signs.push({
    id: Date.now(),
    edge_id:  p.edge_id,
    type:     state.selectedSign,
    roadName: p.name,
    highway:  p.highway,
    icon:     def?.icon,
    label:    def?.name,
  });

  closeSignPanel();
  renderSignsList();
  renderSignMarkers();
  state.simDone = false;
  document.getElementById("btn-simulate").disabled = false;
  showToast(`${def?.icon} ${def?.name} ajouté sur ${p.name}`, "success");
}

/* ── Signs sidebar list ──────────────────────────────────────── */
function renderSignsList() {
  const el = document.getElementById("signs-list");
  if (state.signs.length === 0) {
    el.innerHTML = `<div class="empty-state">
      <div class="icon">🗺️</div>
      Cliquez sur une route pour y poser un panneau.
    </div>`;
    return;
  }

  el.innerHTML = state.signs.map(s => `
    <div class="sign-item" data-id="${s.id}">
      <div class="sign-icon">${s.icon}</div>
      <div class="sign-info">
        <div class="sign-name">${s.label}</div>
        <div class="sign-road">${s.roadName}</div>
      </div>
      <button class="sign-remove" onclick="removeSign(${s.id})" title="Retirer">×</button>
    </div>`).join("");
}

function removeSign(id) {
  state.signs = state.signs.filter(s => s.id !== id);
  renderSignsList();
  renderSignMarkers();
  if (state.signs.length === 0) {
    resetToBase();
  }
}

/* ── Simulation ──────────────────────────────────────────────── */
async function simulate() {
  if (state.signs.length === 0) return;
  showLoading("Simulation en cours…");

  try {
    const res = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ signs: state.signs.map(s => ({ edge_id: s.edge_id, type: s.type })) }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    state.simGeoJSON = data.geojson;
    state.simStats   = data.stats;
    state.baseStats  = data.base_stats;
    state.simDone    = true;

    setViewMode(state.viewMode === "current" ? "simulated" : state.viewMode);
    updateStats();
    renderSignMarkers();
    showToast("Simulation terminée ✓", "success");
    document.getElementById("btn-simulate").disabled = true;
    document.getElementById("view-modes").style.display = "flex";
  } catch (e) {
    showToast("Erreur : " + e.message, "error");
  } finally {
    hideLoading();
  }
}

function resetToBase() {
  state.simGeoJSON = null;
  state.simStats   = null;
  state.simDone    = false;
  state.viewMode   = "current";
  document.getElementById("view-modes").style.display = "none";
  setViewMode("current");
  updateStats();
}

async function resetAll() {
  state.signs = [];
  renderSignsList();
  signLayer.clearLayers();
  resetToBase();
  document.getElementById("btn-simulate").disabled = true;
  showToast("Réinitialisation effectuée", "success");
}

/* ── View mode ───────────────────────────────────────────────── */
function setViewMode(mode) {
  state.viewMode = mode;
  document.querySelectorAll(".vm-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.mode === mode));

  const geojson =
    mode === "current"   ? state.baseGeoJSON :
    mode === "simulated" ? state.simGeoJSON  :
    /* diff */              state.simGeoJSON;

  renderRoads(geojson, mode);
}

/* ── Stats display ───────────────────────────────────────────── */
function updateStats() {
  const s = state.simDone ? state.simStats : state.baseStats;
  if (!s) return;

  document.getElementById("stat-critique").textContent = s.critique;
  document.getElementById("stat-eleve").textContent    = s.eleve;
  document.getElementById("stat-modere").textContent   = s.modere;
  document.getElementById("stat-faible").textContent   = s.faible;

  const compareSection = document.getElementById("compare-section");
  if (!state.simDone || !state.baseStats) {
    compareSection.style.display = "none";
    return;
  }
  compareSection.style.display = "block";

  const b = state.baseStats;
  const a = state.simStats;

  function row(label, bv, av, lowerIsBetter = true) {
    const diff = av - bv;
    const better = lowerIsBetter ? diff < -0.5 : diff > 0.5;
    const worse  = lowerIsBetter ? diff > 0.5  : diff < -0.5;
    const cls    = better ? "better" : worse ? "worse" : "same";
    const arrow  = diff > 0.5 ? "▲" : diff < -0.5 ? "▼" : "=";
    return `<div class="compare-row">
      <span class="lbl">${label}</span>
      <div class="vals">
        <span class="before">${bv}</span>
        <span class="arrow">${arrow}</span>
        <span class="after ${cls}">${av}</span>
      </div>
    </div>`;
  }

  document.getElementById("compare-rows").innerHTML =
    row("Score moyen", b.avg, a.avg) +
    row("Score max",   b.max, a.max) +
    row("Critique",    b.critique, a.critique) +
    row("Élevé",       b.eleve,   a.eleve) +
    row("Faible",      b.faible,  a.faible, false);
}

/* ── Bootstrap ───────────────────────────────────────────────── */
async function loadNetwork() {
  showLoading("Chargement du réseau OSM…");
  try {
    const res = await fetch("/api/network");
    const geojson = await res.json();
    state.baseGeoJSON = geojson;

    const sRes  = await fetch("/api/stats");
    const stats = await sRes.json();
    state.baseStats = stats;

    const poiEl = document.getElementById("poi-count");
    if (poiEl && stats.n_poi != null)
      poiEl.textContent = `${stats.n_poi} POI OSM`;

    renderRoads(geojson, "current");
    updateStats();

    // Fit map
    const coords = [];
    geojson.features.forEach(f => {
      if (f.geometry?.coordinates) {
        f.geometry.coordinates.forEach(([lng, lat]) => coords.push([lat, lng]));
      }
    });
    if (coords.length) map.fitBounds(L.latLngBounds(coords), { padding: [20, 20] });

    showToast(`${geojson.features.length} segments chargés`, "success");
  } catch (e) {
    showToast("Erreur de chargement : " + e.message, "error");
  } finally {
    hideLoading();
  }
}

/* ── UI helpers ──────────────────────────────────────────────── */
function showLoading(msg = "Calcul en cours…") {
  document.getElementById("loading-msg").textContent = msg;
  document.getElementById("loading").classList.add("visible");
}
function hideLoading() {
  document.getElementById("loading").classList.remove("visible");
}

let toastTimer;
function showToast(msg, type = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, 3000);
}

/* ── Wire events ─────────────────────────────────────────────── */
document.getElementById("sign-panel-close").addEventListener("click", closeSignPanel);
document.getElementById("btn-apply-sign").addEventListener("click", applySign);
document.getElementById("btn-simulate").addEventListener("click", simulate);
document.getElementById("btn-reset").addEventListener("click", resetAll);

document.querySelectorAll(".vm-btn").forEach(btn =>
  btn.addEventListener("click", () => {
    if (!state.simDone) return;
    setViewMode(btn.dataset.mode);
  })
);

map.on("click", closeSignPanel);

// Close panel on Escape
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeSignPanel();
});

// Init
document.getElementById("view-modes").style.display = "none";
loadNetwork();
