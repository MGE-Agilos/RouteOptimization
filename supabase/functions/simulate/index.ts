/**
 * Supabase Edge Function — simulation trafic OLLN
 * Recalcule betweenness + gravity + local_access sur le graphe modifié.
 */

const GRAPH_URL =
  "https://mge-agilos.github.io/RouteOptimization/data/graph.json";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface NodeAdj {
  x: number;
  y: number;
  out: Array<{ to: string; eid: string; len: number }>;
}

interface EdgeData {
  id: string;
  u: string;
  v: string;
  key: number;
  length: number;
  highway: string;
  ref: string;
  gravity: number;
  local: number;
}

interface GraphData {
  nodes: string[];
  node_adj: Record<string, NodeAdj>;
  edges: EdgeData[];
  base_type_max_bc: Record<string, number>;
  gravity_scale: number;
  aadt_max: Record<string, number>;
  national_bc_boost: Record<string, number>;
}

// Cache en mémoire pour les instances chaudes
let graphCache: GraphData | null = null;

// ── Chargement du graphe ───────────────────────────────────────────────────────

async function loadGraph(): Promise<GraphData> {
  if (graphCache) return graphCache;
  const resp = await fetch(GRAPH_URL);
  if (!resp.ok) throw new Error(`Impossible de charger le graphe: ${resp.status}`);
  graphCache = await resp.json() as GraphData;
  return graphCache;
}

// ── Min-heap simple ───────────────────────────────────────────────────────────

class MinHeap {
  private h: Array<[number, string]> = [];

  push(dist: number, node: string) {
    this.h.push([dist, node]);
    this._bubbleUp(this.h.length - 1);
  }

  pop(): [number, string] | undefined {
    if (!this.h.length) return undefined;
    const top = this.h[0];
    const last = this.h.pop()!;
    if (this.h.length) { this.h[0] = last; this._sinkDown(0); }
    return top;
  }

  get size() { return this.h.length; }

  private _bubbleUp(i: number) {
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.h[p][0] <= this.h[i][0]) break;
      [this.h[p], this.h[i]] = [this.h[i], this.h[p]];
      i = p;
    }
  }

  private _sinkDown(i: number) {
    const n = this.h.length;
    while (true) {
      let m = i;
      const l = 2 * i + 1, r = 2 * i + 2;
      if (l < n && this.h[l][0] < this.h[m][0]) m = l;
      if (r < n && this.h[r][0] < this.h[m][0]) m = r;
      if (m === i) break;
      [this.h[m], this.h[i]] = [this.h[i], this.h[m]];
      i = m;
    }
  }
}

// ── Betweenness approximée (Brandes, k sources aléatoires) ──────────────────

function approxEdgeBetweenness(
  nodes: string[],
  nodeAdj: Record<string, NodeAdj>,
  closedEdges: Set<string>,
  k: number,
  seed: number,
): Map<string, number> {
  // Pseudo-random shuffle (LCG)
  const rng = (() => {
    let s = seed >>> 0;
    return () => { s = (Math.imul(1664525, s) + 1013904223) >>> 0; return s / 4294967296; };
  })();

  const shuffled = [...nodes].sort(() => rng() - 0.5);
  const sources = shuffled.slice(0, Math.min(k, nodes.length));
  const bc = new Map<string, number>();

  for (const src of sources) {
    // Dijkstra depuis src sur les arêtes non-fermées
    const dist   = new Map<string, number>();
    const sigma  = new Map<string, number>(); // nb chemins les plus courts
    const pred   = new Map<string, Array<{ from: string; eid: string }>>();
    const stack: string[] = [];

    dist.set(src, 0);
    sigma.set(src, 1);
    const heap = new MinHeap();
    heap.push(0, src);

    while (heap.size > 0) {
      const item = heap.pop()!;
      const [d, u] = item;
      if (d > (dist.get(u) ?? Infinity)) continue;
      stack.push(u);

      for (const { to: v, eid, len } of nodeAdj[u]?.out ?? []) {
        if (closedEdges.has(eid)) continue;
        const nd = d + len;
        const dv = dist.get(v) ?? Infinity;
        if (nd < dv) {
          dist.set(v, nd);
          sigma.set(v, sigma.get(u)!);
          pred.set(v, [{ from: u, eid }]);
          heap.push(nd, v);
        } else if (nd === dv) {
          sigma.set(v, (sigma.get(v) ?? 0) + (sigma.get(u) ?? 0));
          pred.get(v)!.push({ from: u, eid });
        }
      }
    }

    // Rétro-propagation (Brandes)
    const delta = new Map<string, number>();
    while (stack.length) {
      const w = stack.pop()!;
      for (const { from, eid } of pred.get(w) ?? []) {
        const sig_u = sigma.get(from) ?? 0;
        const sig_w = sigma.get(w) ?? 1;
        const c = (sig_u / sig_w) * (1 + (delta.get(w) ?? 0));
        bc.set(eid, (bc.get(eid) ?? 0) + c);
        delta.set(from, (delta.get(from) ?? 0) + c);
      }
    }
  }

  return bc;
}

// ── Calcul des véhicules ──────────────────────────────────────────────────────

function computeVehicles(
  graph: GraphData,
  closedEdges: Set<string>,
): Map<string, number> {
  const { nodes, node_adj, edges, base_type_max_bc, gravity_scale,
          aadt_max, national_bc_boost } = graph;

  // Betweenness approximée (k=150)
  const bc = approxEdgeBetweenness(nodes, node_adj, closedEdges, 150, 42);

  // Anchor 90e percentile par type (sur les arêtes ouvertes)
  const typeValues = new Map<string, number[]>();
  for (const e of edges) {
    if (closedEdges.has(e.id)) continue;
    const hw = e.highway;
    const bv = bc.get(e.id) ?? 0;
    if (!typeValues.has(hw)) typeValues.set(hw, []);
    typeValues.get(hw)!.push(bv);
  }

  const anchor = new Map<string, number>();
  for (const [hw, vals] of typeValues) {
    if (base_type_max_bc[hw]) {
      anchor.set(hw, base_type_max_bc[hw]);
    } else {
      const sorted = [...vals].sort((a, b) => a - b);
      const p90idx = Math.floor(sorted.length * 0.9);
      anchor.set(hw, Math.max(sorted[p90idx] ?? 1e-9, 1e-9));
    }
  }

  const result = new Map<string, number>();
  for (const e of edges) {
    if (closedEdges.has(e.id)) { result.set(e.id, 0); continue; }

    const hw          = e.highway;
    const hw_anchor   = Math.max(anchor.get(hw) ?? 1e-9, 1e-9);
    const aadt_default = aadt_max[hw] ?? 1500;
    let bc_norm       = (bc.get(e.id) ?? 0) / hw_anchor;

    // Boost routes nationales sous-estimées en bordure de graphe
    const boost = national_bc_boost[e.ref] ?? 1.0;
    if (boost > 1.0 && bc_norm < 0.30) {
      bc_norm = Math.min(bc_norm * boost, 0.85);
    } else {
      bc_norm = Math.min(bc_norm, 1.5);
    }

    const bw_veh = Math.round(bc_norm * aadt_default);
    const gv_veh = Math.round(e.gravity * gravity_scale);
    const lv_veh = e.local;

    result.set(e.id, bw_veh + gv_veh + lv_veh);
  }

  return result;
}

// ── Union-Find : détection des routes isolées ─────────────────────────────────

function detectIsolated(
  graph: GraphData,
  closedEdges: Set<string>,
): Set<string> {
  const parent: Record<string, string> = {};
  const rank:   Record<string, number> = {};

  function find(x: string): string {
    while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
    return x;
  }
  function union(a: string, b: string) {
    const pa = find(a), pb = find(b);
    if (pa === pb) return;
    if ((rank[pa] ?? 0) < (rank[pb] ?? 0)) { parent[pa] = pb; }
    else if ((rank[pa] ?? 0) > (rank[pb] ?? 0)) { parent[pb] = pa; }
    else { parent[pb] = pa; rank[pa] = (rank[pa] ?? 0) + 1; }
  }

  // Initialiser Union-Find sur arêtes ouvertes
  for (const e of graph.edges) {
    if (!closedEdges.has(e.id)) { parent[e.id] = e.id; rank[e.id] = 0; }
  }

  // Construire voisinage (arêtes partageant un nœud)
  const nodeEdges = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (closedEdges.has(e.id)) continue;
    for (const n of [e.u, e.v]) {
      if (!nodeEdges.has(n)) nodeEdges.set(n, []);
      nodeEdges.get(n)!.push(e.id);
    }
  }

  for (const neighbors of nodeEdges.values()) {
    for (let i = 1; i < neighbors.length; i++) union(neighbors[0], neighbors[i]);
  }

  const compSize: Record<string, number> = {};
  for (const e of graph.edges) {
    if (closedEdges.has(e.id)) continue;
    const r = find(e.id);
    compSize[r] = (compSize[r] ?? 0) + 1;
  }

  const maxSize = Math.max(...Object.values(compSize), 0);
  const mainRoot = Object.entries(compSize).find(([, v]) => v === maxSize)?.[0];

  const isolated = new Set<string>();
  for (const e of graph.edges) {
    if (!closedEdges.has(e.id) && mainRoot && find(e.id) !== mainRoot) {
      isolated.add(e.id);
    }
  }
  return isolated;
}

// ── Stats ─────────────────────────────────────────────────────────────────────

function scoreToCategory(s: number): string {
  if (s >= 65) return "Critique";
  if (s >= 40) return "Élevé";
  if (s >= 20) return "Modéré";
  return "Faible";
}

// ── Handler principal ─────────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

  try {
    const { signs = [] } = await req.json();
    const graph = await loadGraph();

    // Arêtes fermées (+ directions inverses OSM)
    const closedEdges = new Set<string>(signs.map((s: { edge_id: string }) => s.edge_id));
    for (const s of signs) {
      const parts = s.edge_id.split(":");
      if (parts.length < 2) continue;
      const reversePrefix = `${parts[1]}:${parts[0]}:`;
      for (const e of graph.edges) {
        if (e.id.startsWith(reversePrefix)) closedEdges.add(e.id);
      }
    }

    const isolated  = detectIsolated(graph, closedEdges);
    const vehicles  = computeVehicles(graph, closedEdges);
    const signMap   = new Map(signs.map((s: { edge_id: string; type: string }) => [s.edge_id, s.type]));

    const features: unknown[] = [];
    let totalVeh = 0, count = 0, critique = 0, eleve = 0, modere = 0, faible = 0;

    for (const e of graph.edges) {
      const isClosed   = closedEdges.has(e.id);
      const isIsolated = isolated.has(e.id);
      const veh  = (isClosed || isIsolated) ? 0 : (vehicles.get(e.id) ?? 0);
      const score = Math.min(100, Math.round(veh / 100 * 10) / 10);
      const cat  = scoreToCategory(score);

      features.push({ edge_id: e.id, score, vehicles: veh, category: cat,
                       closed: isClosed || isIsolated,
                       sign: signMap.get(e.id) ?? null });

      if (!isClosed && !isIsolated) {
        totalVeh += veh; count++;
        if (cat === "Critique") critique++;
        else if (cat === "Élevé") eleve++;
        else if (cat === "Modéré") modere++;
        else faible++;
      }
    }

    const stats = {
      total: graph.edges.length,
      avg:   count ? Math.round(totalVeh / count / 100 * 10) / 10 : 0,
      critique, eleve, modere, faible,
    };

    return new Response(JSON.stringify({ features, stats }), {
      headers: { ...CORS, "Content-Type": "application/json" },
    });

  } catch (err) {
    console.error(err);
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 500,
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  }
});
