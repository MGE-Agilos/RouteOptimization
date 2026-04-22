/* ── State ───────────────────────────────────────────────────── */
const state = {
  baseGeoJSON:  null,
  simGeoJSON:   null,
  baseStats:    null,
  simStats:     null,
  adjacency:    null,   // {edge_id: [neighbor_edge_ids]}
  signs:        [],
  selectedEdge: null,
  selectedSign: null,
  viewMode:     "current",
  simDone:      false,
};

/* ── Sign definitions ────────────────────────────────────────── */
const SIGN_TYPES = [
  { type:"closure",   icon:"🚧", name:"Fermeture de route",    desc:"Supprime la route du réseau. Le trafic se redistribue.", effect:"Réduit trafic local",    cls:"effect-dec"  },
  { type:"zone30",    icon:"🔴", name:"Zone 30 km/h",          desc:"Ralentit la circulation, rend la route moins attractive.", effect:"Trafic détourné (-)", cls:"effect-dec"  },
  { type:"zone50",    icon:"🟠", name:"Limite 50 km/h",        desc:"Limitation modérée, légère redistribution du trafic.",    effect:"Léger détournement", cls:"effect-dec"  },
  { type:"priority",  icon:"⭐", name:"Route prioritaire",     desc:"Rend la route plus attractive, attire plus de trafic.",   effect:"Augmente le trafic", cls:"effect-inc"  },
  { type:"oneway",    icon:"➡️", name:"Sens unique",           desc:"Supprime la direction inverse.",                         effect:"Flux dirigé",        cls:"effect-neut" },
  { type:"deviation", icon:"🔄", name:"Déviation / Travaux",   desc:"Ferme la route (travaux). Le trafic cherche un itinéraire alternatif.", effect:"Redistribution totale", cls:"effect-inc" },
];

/* ── Score helpers ───────────────────────────────────────────── */
function scoreColor(score) {
  if (score >= 65) return "#e74c3c";
  if (score >= 40) return "#e67e22";
  if (score >= 20) return "#f1c40f";
  return "#2ecc71";
}
function deltaColor(delta) {
  if (delta <= -10) return "#27ae60";
  if (delta <=  -3) return "#2ecc71";
  if (delta <    3) return "#95a5a6";
  if (delta <   10) return "#e67e22";
  return "#e74c3c";
}
function scoreWeight(score) {
  if (score >= 65) return 4;
  if (score >= 40) return 3;
  if (score >= 20) return 2;
  return 1.2;
}
function scoreToCategory(score) {
  if (score >= 65) return "Critique";
  if (score >= 40) return "Élevé";
  if (score >= 20) return "Modéré";
  return "Faible";
}
function fmtVehicles(v) {
  if (!v && v !== 0) return "–";
  return v >= 1000 ? `${(v / 1000).toFixed(1).replace(".", ",")} k` : String(v);
}

/* ── Map init ────────────────────────────────────────────────── */
const map = L.map("map", { zoomControl: false, attributionControl: true })
  .setView([50.668, 4.612], 13);

L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: "© OpenStreetMap, © CARTO",
  maxZoom: 19,
}).addTo(map);

L.control.zoom({ position: "bottomright" }).addTo(map);

const roadLayer = L.layerGroup().addTo(map);
const signLayer = L.layerGroup().addTo(map);

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
      color = deltaColor(p.delta || 0); weight = Math.max(2, scoreWeight(p.score));
      opacity = 0.9; dashArray = null;
    } else {
      color = scoreColor(p.score); weight = scoreWeight(p.score);
      opacity = 0.9; dashArray = null;
    }

    const visLine = L.polyline(latlngs, { color, weight, opacity, dashArray, interactive: false });
    const hitLine = L.polyline(latlngs, { color: "#000", weight: 16, opacity: 0, interactive: true });

    let hoverTimer = null;

    hitLine.on("click", e => {
      L.DomEvent.stopPropagation(e);
      visLine.setStyle({ weight: weight + 3, opacity: 1 });
      setTimeout(() => visLine.setStyle({ weight, opacity }), 400);
      openSignPanel(p);
    });

    hitLine.on("mouseover", e => {
      visLine.setStyle({ weight: weight + 2, opacity: 1 });
      hoverTimer = setTimeout(() => {
        L.popup({ closeButton: false, offset: [0, -4] })
          .setLatLng(e.latlng)
          .setContent(buildPopupHtml(p, mode))
          .openOn(map);
      }, 2000);
    });

    hitLine.on("mouseout", () => {
      clearTimeout(hoverTimer);
      map.closePopup();
      visLine.setStyle({ weight, opacity });
    });

    roadLayer.addLayer(visLine);
    roadLayer.addLayer(hitLine);
  });
}

function buildPopupHtml(p, mode) {
  const catColor = { Critique:"#e74c3c", "Élevé":"#e67e22", Modéré:"#e8a020", Faible:"#27ae60" }[p.category] || "#aaa";
  let extra = "";
  if (mode === "diff" && p.delta) {
    const col = p.delta > 0 ? "#e74c3c" : "#27ae60";
    extra = `<div style="color:${col};font-weight:700;margin-top:4px">${p.delta > 0 ? "▲ +" : "▼ "}${p.delta} pts vs. base</div>`;
  }
  if (p.closed) extra = `<div style="color:#e74c3c;font-weight:700;margin-top:4px">🚧 Route fermée</div>`;

  return `<div style="min-width:175px">
    <div style="font-weight:700;font-size:14px;margin-bottom:2px">${p.name}</div>
    <div style="font-size:11px;color:#666;margin-bottom:8px">${p.highway} · ${p.length} m</div>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="font-size:20px;font-weight:800;color:${catColor}">${p.score.toFixed(1)}</span>
      <span style="font-size:11px;background:${catColor}22;color:${catColor};padding:2px 7px;border-radius:4px;font-weight:600">${p.category}</span>
    </div>
    <div style="font-size:12px;color:#444;background:#f5f5f5;border-radius:5px;padding:5px 8px;display:flex;justify-content:space-between">
      <span>🚗 Véhicules/jour</span><strong>${fmtVehicles(p.vehicles)}</strong>
    </div>
    ${p.veh_delta != null && p.veh_delta !== 0 ? `
    <div style="font-size:11px;margin-top:4px;padding:3px 8px;border-radius:4px;
      background:${p.veh_delta > 0 ? "#fdecea" : "#eafaf1"};color:${p.veh_delta > 0 ? "#c0392b" : "#1e8449"}">
      ${p.veh_delta > 0 ? "▲ +" : "▼ "}${p.veh_delta.toLocaleString("fr-BE")} véh/jour vs base
    </div>` : ""}
    ${extra}
    <div style="font-size:10px;color:#999;margin-top:6px">Cliquez pour poser un panneau</div>
  </div>`;
}

/* ── Sign markers ────────────────────────────────────────────── */
function renderSignMarkers() {
  signLayer.clearLayers();
  state.signs.forEach(s => {
    const feat = (state.simGeoJSON || state.baseGeoJSON)?.features.find(f => f.properties.edge_id === s.edge_id);
    if (!feat) return;
    const coords = feat.geometry.coordinates;
    const mid = coords[Math.floor(coords.length / 2)];
    const def = SIGN_TYPES.find(t => t.type === s.type);
    const icon = L.divIcon({
      className: "",
      html: `<div style="font-size:20px;filter:drop-shadow(0 2px 4px rgba(0,0,0,.8));cursor:pointer;line-height:1">${def?.icon || "📍"}</div>`,
      iconSize: [24, 24], iconAnchor: [12, 12],
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
  fill.style.width = `${score}%`;
  fill.style.background = scoreColor(score);
  document.getElementById("sp-score-big").textContent = score.toFixed(1);
  document.getElementById("sp-score-big").style.color = scoreColor(score);
  document.getElementById("sp-score-cat").textContent = props.category;
  document.getElementById("sp-score-cat").style.color = scoreColor(score);

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
  state.signs = state.signs.filter(s => s.edge_id !== p.edge_id);
  const def = SIGN_TYPES.find(t => t.type === state.selectedSign);
  state.signs.push({ id: Date.now(), edge_id: p.edge_id, type: state.selectedSign,
    roadName: p.name, highway: p.highway, icon: def?.icon, label: def?.name });
  closeSignPanel();
  renderSignsList();
  renderSignMarkers();
  state.simDone = false;
  document.getElementById("btn-simulate").disabled = false;
  showToast(`${def?.icon} ${def?.name} ajouté sur ${p.name}`, "success");
}

/* ── Signs list ──────────────────────────────────────────────── */
function renderSignsList() {
  const el = document.getElementById("signs-list");
  if (state.signs.length === 0) {
    el.innerHTML = `<div class="empty-state"><div class="icon">🗺️</div>Cliquez sur une route pour y poser un panneau.</div>`;
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
  if (state.signs.length === 0) resetToBase();
}

/* ── Local simulation (no backend) ──────────────────────────── */

// Multiplicateurs d'effet par type de panneau
const SIGN_EFFECTS = {
  closure:   { scoreMul: 0,    vehMul: 0,    closed: true  },
  deviation: { scoreMul: 0,    vehMul: 0,    closed: true  },
  zone30:    { scoreMul: 0.62, vehMul: 0.50, closed: false },
  zone50:    { scoreMul: 0.82, vehMul: 0.78, closed: false },
  priority:  { scoreMul: 1.28, vehMul: 1.35, closed: false },
  oneway:    { scoreMul: 0.90, vehMul: 0.95, closed: false },
};

function simulateLocally() {
  const signMap = {};
  state.signs.forEach(s => { signMap[s.edge_id] = s.type; });

  // Passe 1 : appliquer les effets directs sur les segments signalés
  const propMap = {};
  state.baseGeoJSON.features.forEach(feat => {
    const p = feat.properties;
    const stype = signMap[p.edge_id];
    if (!stype) {
      propMap[p.edge_id] = { ...p, delta: 0, veh_delta: 0 };
      return;
    }
    const eff = SIGN_EFFECTS[stype] || { scoreMul: 1, vehMul: 1, closed: false };
    const newScore = Math.min(100, Math.max(0, p.score * eff.scoreMul));
    const newVeh   = Math.round((p.vehicles || 0) * eff.vehMul);
    propMap[p.edge_id] = {
      ...p,
      score:     Math.round(newScore * 10) / 10,
      vehicles:  newVeh,
      category:  scoreToCategory(newScore),
      closed:    eff.closed,
      sign:      stype,
      delta:     Math.round((newScore - p.score) * 10) / 10,
      veh_delta: newVeh - (p.vehicles || 0),
    };
  });

  // Passe 1b : marquer les routes isolées (toutes leurs connexions sont fermées)
  if (state.adjacency) {
    const closedIds = new Set(state.signs.map(s => s.edge_id));
    state.baseGeoJSON.features.forEach(feat => {
      const eid = feat.properties.edge_id;
      if (closedIds.has(eid)) return; // déjà fermée explicitement
      const ownNeighbors = state.adjacency[eid] || [];
      const hasOpenExit = ownNeighbors.some(nid => !closedIds.has(nid));
      if (!hasOpenExit && ownNeighbors.length > 0) {
        // Route coupée de tout le réseau : trafic = 0
        const p = propMap[eid];
        propMap[eid] = {
          ...p,
          vehicles:  0,
          score:     0,
          category:  "Faible",
          closed:    true,
          delta:     Math.round((0 - feat.properties.score) * 10) / 10,
          veh_delta: -(feat.properties.vehicles || 0),
        };
      }
    });
  }

  // Passe 2 : redistribuer le trafic perdu vers les routes adjacentes
  if (state.adjacency) {
    state.signs.forEach(s => {
      const eff = SIGN_EFFECTS[s.type];
      if (!eff || !eff.closed) return;

      const base = state.baseGeoJSON.features.find(f => f.properties.edge_id === s.edge_id);
      if (!base) return;

      const lostVeh   = base.properties.vehicles || 0;
      const lostScore = base.properties.score    || 0;
      if (lostVeh === 0 && lostScore === 0) return;

      // Collecter toutes les routes fermées (pour le test d'accessibilité)
      const closedIds = new Set(state.signs.map(sg => sg.edge_id));

      const neighbors = (state.adjacency[s.edge_id] || [])
        .filter(nid => {
          if (propMap[nid]?.closed) return false;
          // Vérifier que la route voisine a au moins une connexion ouverte :
          // si tous ses propres voisins sont fermés, elle est isolée du réseau.
          const ownNeighbors = state.adjacency[nid] || [];
          const hasOpenExit = ownNeighbors.some(nnid => !closedIds.has(nnid));
          return hasOpenExit;
        });

      if (neighbors.length === 0) return;

      // Distribuer proportionnellement au véhicule de base du voisin
      const totalNeighborVeh = neighbors.reduce((sum, nid) =>
        sum + (propMap[nid]?.vehicles || 1), 0);

      neighbors.forEach(nid => {
        const np = propMap[nid];
        if (!np) return;
        const share = (np.vehicles || 1) / Math.max(totalNeighborVeh, 1);
        const extraVeh   = Math.round(lostVeh * share * 0.7);   // 70% redistribué
        const extraScore = lostScore * share * 0.5;

        const newVeh   = np.vehicles + extraVeh;
        const newScore = Math.min(100, np.score + extraScore);
        propMap[nid] = {
          ...np,
          vehicles:  newVeh,
          score:     Math.round(newScore * 10) / 10,
          category:  scoreToCategory(newScore),
          delta:     Math.round((newScore - (state.baseGeoJSON.features.find(f => f.properties.edge_id === nid)?.properties.score || newScore)) * 10) / 10,
          veh_delta: newVeh - (state.baseGeoJSON.features.find(f => f.properties.edge_id === nid)?.properties.vehicles || 0),
        };
      });
    });
  }

  const features = state.baseGeoJSON.features.map(feat =>
    ({ ...feat, properties: propMap[feat.properties.edge_id] || feat.properties })
  );

  return { type: "FeatureCollection", features };
}

function computeStats(geojson) {
  const scores = geojson.features.map(f => f.properties.score);
  const cats   = geojson.features.map(f => f.properties.category);
  if (!scores.length) return {};
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  return {
    total:    scores.length,
    avg:      Math.round(avg * 10) / 10,
    max:      Math.max(...scores),
    critique: cats.filter(c => c === "Critique").length,
    eleve:    cats.filter(c => c === "Élevé").length,
    modere:   cats.filter(c => c === "Modéré").length,
    faible:   cats.filter(c => c === "Faible").length,
  };
}

function simulate() {
  if (state.signs.length === 0) return;
  showLoading("Simulation en cours…");

  setTimeout(() => {
    try {
      state.simGeoJSON = simulateLocally();
      state.simStats   = computeStats(state.simGeoJSON);
      state.simDone    = true;

      setViewMode(state.viewMode === "current" ? "simulated" : state.viewMode);
      updateStats();
      renderSignMarkers();
      document.getElementById("btn-simulate").disabled = true;
      document.getElementById("view-modes").style.display = "flex";
      showToast("Simulation terminée ✓", "success");
    } catch (e) {
      showToast("Erreur : " + e.message, "error");
    } finally {
      hideLoading();
    }
  }, 300);
}

function resetToBase() {
  state.simGeoJSON = null; state.simStats = null;
  state.simDone = false; state.viewMode = "current";
  document.getElementById("view-modes").style.display = "none";
  setViewMode("current");
  updateStats();
}

function resetAll() {
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
  const geojson = mode === "current" ? state.baseGeoJSON : state.simGeoJSON;
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
  if (!state.simDone || !state.baseStats) { compareSection.style.display = "none"; return; }
  compareSection.style.display = "block";

  const b = state.baseStats, a = state.simStats;
  function row(label, bv, av, lowerBetter = true) {
    const diff = av - bv;
    const better = lowerBetter ? diff < -0.5 : diff > 0.5;
    const worse  = lowerBetter ? diff > 0.5  : diff < -0.5;
    const cls    = better ? "better" : worse ? "worse" : "same";
    const arrow  = diff > 0.5 ? "▲" : diff < -0.5 ? "▼" : "=";
    return `<div class="compare-row">
      <span class="lbl">${label}</span>
      <div class="vals"><span class="before">${bv}</span><span class="arrow">${arrow}</span><span class="after ${cls}">${av}</span></div>
    </div>`;
  }
  document.getElementById("compare-rows").innerHTML =
    row("Score moyen", b.avg, a.avg) +
    row("Score max",   b.max, a.max) +
    row("Critique",    b.critique, a.critique) +
    row("Élevé",       b.eleve, a.eleve) +
    row("Faible",      b.faible, a.faible, false);
}

/* ── Bootstrap ───────────────────────────────────────────────── */
async function loadNetwork() {
  showLoading("Chargement du réseau OSM…");
  try {
    const [geoRes, statRes, adjRes] = await Promise.all([
      fetch("data/network.json"),
      fetch("data/stats.json"),
      fetch("data/adjacency.json"),
    ]);
    state.baseGeoJSON = await geoRes.json();
    state.baseStats   = await statRes.json();
    state.adjacency   = await adjRes.json();

    const poiEl = document.getElementById("poi-count");
    if (poiEl && state.baseStats.n_poi != null)
      poiEl.textContent = `${state.baseStats.n_poi} POI OSM`;

    renderRoads(state.baseGeoJSON, "current");
    updateStats();

    const coords = [];
    state.baseGeoJSON.features.forEach(f => {
      if (f.geometry?.coordinates)
        f.geometry.coordinates.forEach(([lng, lat]) => coords.push([lat, lng]));
    });
    if (coords.length) map.fitBounds(L.latLngBounds(coords), { padding: [20, 20] });

    showToast(`${state.baseGeoJSON.features.length} segments chargés`, "success");
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
function hideLoading() { document.getElementById("loading").classList.remove("visible"); }

let toastTimer;
function showToast(msg, type = "") {
  const el = document.getElementById("toast");
  el.textContent = msg; el.className = `show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, 3000);
}

/* ── Wire events ─────────────────────────────────────────────── */
document.getElementById("sign-panel-close").addEventListener("click", closeSignPanel);
document.getElementById("btn-apply-sign").addEventListener("click", applySign);
document.getElementById("btn-simulate").addEventListener("click", simulate);
document.getElementById("btn-reset").addEventListener("click", resetAll);

document.querySelectorAll(".vm-btn").forEach(btn =>
  btn.addEventListener("click", () => { if (state.simDone) setViewMode(btn.dataset.mode); }));

map.on("click", closeSignPanel);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSignPanel(); });

document.getElementById("view-modes").style.display = "none";
loadNetwork();
