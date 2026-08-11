/* Graph Visualiser — the knowledge graph, drawn.
 *
 * ── Why there is a physics engine in here ──────────────────────────────────
 * The rest of this app vendors React and htm and compiles nothing, so that the
 * page works on a network with no route to the public internet and no version
 * can change under a deployment that was signed off against a different one.
 * A graph library would be the first exception to that, and the exception is
 * not worth it: the layout below is a few hundred lines of force integration
 * against ~105 nodes and ~350 edges, which is a size where the naive O(n²)
 * repulsion is genuinely free (5.5k pairs a tick) and every quadtree, WebGL
 * renderer and plugin system in a real graph library is answering a question
 * this page does not ask.
 *
 * ── Why the simulation does not go through React ───────────────────────────
 * React owns the toolbar, the legend and the inspector. It does not own the
 * SVG: sixty re-renders a second over 450 elements is the one thing that would
 * make this tab slow. `ForceGraph` builds the nodes and edges once per filter
 * change and then mutates `transform` and `x1/y1/x2/y2` directly on each tick.
 * State that React needs back — hover, selection — comes out through callbacks.
 */

import htm from "/static/vendor/htm.module.js";

const { createElement, useState, useEffect, useRef, useMemo, useCallback } = React;
const html = htm.bind(createElement);

/* ── palette ──────────────────────────────────────────────────────────────
 * Colour carries the source, because "which system is this from" is the first
 * question anyone asks of this picture. Shape carries the kind — schema, table,
 * concept — so the two questions never compete for the same channel, and the
 * legend can answer both without a colour appearing twice. */
const SOURCE_COLOURS = {
  PEP: "#1f7ae0",
  CLMS: "#0f8a5f",
  CrewPortal: "#b45309",
  ServiceNow: "#7c3aed",
  CAC: "#c2384a",
};
const FALLBACK_COLOUR = "#64748b";
const colourOf = (source) => SOURCE_COLOURS[source] || FALLBACK_COLOUR;

const NODE_KINDS = [
  ["schema", "Schemas"],
  ["table", "Tables"],
  ["concept", "Concepts"],
];
const EDGE_KINDS = [
  ["joins_to", "Joins"],
  ["belongs_to", "Belongs to"],
  ["contains", "Contains"],
];

const radiusOf = (n) =>
  n.kind === "crew" ? 15 : n.kind === "schema" ? 17 : n.kind === "table" ? 8.5 : 6.5;

/* A crew identifier, as typed into the search box. `IGA60406` and `C60406` are
 * the two spellings the bridge translates between; anything else is a search for
 * a table or a column and must stay one, or typing "crew" would fire a data
 * query on every keystroke. */
const CREW_ID = /^(IGA\d{3,}|C\d{3,})$/i;
const CREW_COLOUR = "#ff6a55";

const SVG_NS = "http://www.w3.org/2000/svg";
const el = (name, attrs) => {
  const node = document.createElementNS(SVG_NS, name);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  return node;
};
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const hexagon = (r) => {
  const points = Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 3) * i - Math.PI / 2;
    return `${(Math.cos(a) * r).toFixed(2)},${(Math.sin(a) * r).toFixed(2)}`;
  });
  return `M${points[0]} L${points.slice(1).join(" L")} Z`;
};

/* ── the simulation ───────────────────────────────────────────────────────── */

class ForceGraph {
  constructor(svg, { onSelect, onHover }) {
    this.svg = svg;
    this.onSelect = onSelect;
    this.onHover = onHover;

    /* Positions live here and outlive any one `setData` call. Filtering is a
     * question about the same graph, so hiding the concepts and showing them
     * again must not reshuffle the tables — the reader's memory of where things
     * were is the most valuable thing on the page. */
    this.pos = new Map();
    this.view = { k: 1, x: 0, y: 0 };
    this.nodes = [];
    this.edges = [];
    this.adjacency = new Map();
    this.selected = null;
    this.alpha = 0;
    this.frame = null;
    this.matches = null;

    this._build();
    this._bindPanZoom();
  }

  _build() {
    const defs = el("defs", {});
    for (const [kind, colour] of [["joins", "#94a3b8"], ["cross", "#c2384a"]]) {
      const marker = el("marker", {
        id: `arrow-${kind}`,
        viewBox: "0 0 10 10",
        refX: "9",
        refY: "5",
        markerWidth: "5",
        markerHeight: "5",
        orient: "auto-start-reverse",
        markerUnits: "userSpaceOnUse",
      });
      marker.appendChild(el("path", { d: "M0,1 L9,5 L0,9 z", fill: colour }));
      defs.appendChild(marker);
    }
    this.svg.appendChild(defs);

    this.viewport = el("g", { class: "gv-viewport" });
    this.edgeLayer = el("g", { class: "gv-edges" });
    this.nodeLayer = el("g", { class: "gv-nodes" });
    this.viewport.appendChild(this.edgeLayer);
    this.viewport.appendChild(this.nodeLayer);
    this.svg.appendChild(this.viewport);
  }

  size() {
    const r = this.svg.getBoundingClientRect();
    return { w: r.width || 900, h: r.height || 600 };
  }

  /* ── data ──────────────────────────────────────────────────────────────── */

  setData(nodes, edges) {
    this.nodes = nodes;
    this.edges = edges;
    this._seed();

    this.adjacency = new Map();
    for (const n of nodes) this.adjacency.set(n.id, new Set([n.id]));
    for (const e of edges) {
      this.adjacency.get(e.from)?.add(e.to);
      this.adjacency.get(e.to)?.add(e.from);
    }

    this.edgeLayer.replaceChildren();
    this.nodeLayer.replaceChildren();

    for (const e of edges) {
      const line = el("line", {
        class: `gv-edge gv-${e.kind}`
          + (e.cross_source ? " gv-cross" : "")
          + (e.kind === "joins_to" && !e.verified ? " gv-unverified" : ""),
        "vector-effect": "non-scaling-stroke",
      });
      if (e.kind === "joins_to") {
        line.setAttribute("marker-end", `url(#arrow-${e.cross_source ? "cross" : "joins"})`);
      }
      e._el = line;
      this.edgeLayer.appendChild(line);
    }

    const crewMode = nodes.some((n) => n.kind === "crew");

    for (const n of nodes) {
      const state = !crewMode || n.crewRows === undefined ? ""
        : n.crewError ? " gv-crew-unreadable"
        : n.crewRows > 0 ? " gv-crew-holds" : " gv-crew-empty";
      const g = el("g", {
        class: `gv-node gv-${n.kind}${state}`, "data-id": n.id,
      });
      const r = radiusOf(n);
      const colour = colourOf(n.source);

      /* Shape is the node kind and nothing else. A concept is a diamond because
       * it is not a thing in the warehouse — it is the layer the agents reason
       * over, and a reader who cannot tell it from a table at a glance will read
       * this picture as a schema diagram with 52 phantom tables in it. */
      if (n.kind === "concept") {
        g.appendChild(el("rect", {
          class: "gv-shape",
          x: -r, y: -r, width: r * 2, height: r * 2,
          transform: "rotate(45)", rx: 1.5,
          fill: colour, "vector-effect": "non-scaling-stroke",
        }));
      } else if (n.kind === "crew") {
        // The one node on the page that is a person rather than a structure, so
        // it gets the one shape nothing else uses.
        g.appendChild(el("path", {
          class: "gv-shape",
          d: hexagon(r),
          fill: CREW_COLOUR, stroke: "#fff", "stroke-width": 2,
          "vector-effect": "non-scaling-stroke",
        }));
      } else {
        g.appendChild(el("circle", {
          class: "gv-shape", r,
          fill: n.kind === "schema" ? colour : "#fff",
          stroke: colour,
          "stroke-width": n.kind === "schema" ? 2 : 2.2,
          "vector-effect": "non-scaling-stroke",
        }));
      }

      const label = el("text", {
        class: "gv-label",
        x: 0,
        y: r + 10,
        "text-anchor": "middle",
        "font-size": n.kind === "schema" ? 12 : 9,
      });
      // Enrichment gives concepts titles like "Appreciation Comment (Qualitative
      // Text)". Useful in the inspector, unreadable as a caption on a 7px
      // diamond with fifty others around it.
      const text = n.kind === "schema" ? n.name : n.label || n.name;
      label.textContent = text.length > 30 ? `${text.slice(0, 29)}…` : text;
      g.appendChild(label);

      n._el = g;
      n._label = label;
      n._r = r;
      this._bindNode(n, g);
      this.nodeLayer.appendChild(g);
    }

    this._applyLabelScale();
    this.reheat(1);
  }

  /* Seed a node the first time it is seen: schemas evenly around a ring, and
   * everything else scattered near the schema that declared it. A cold start
   * from random positions converges to the same clustering eventually, but it
   * spends the first two seconds looking like an explosion, and the reader is
   * watching during exactly those two seconds. */
  _seed() {
    const { w, h } = this.size();
    const cx = w / 2;
    const cy = h / 2;
    const schemas = this.nodes.filter((n) => n.kind === "schema");
    const ring = Math.min(w, h) * 0.3;
    const anchors = new Map();

    schemas.forEach((n, i) => {
      const a = (i / Math.max(1, schemas.length)) * Math.PI * 2 - Math.PI / 2;
      const at = { x: cx + Math.cos(a) * ring, y: cy + Math.sin(a) * ring };
      anchors.set(n.source, at);
      n._anchor = at;
      if (!this.pos.has(n.id)) this.pos.set(n.id, { ...at, vx: 0, vy: 0, fixed: false });
    });

    for (const n of this.nodes) {
      if (this.pos.has(n.id)) continue;
      const at = anchors.get(n.source) || { x: cx, y: cy };
      const a = Math.random() * Math.PI * 2;
      const d = 40 + Math.random() * 90;
      this.pos.set(n.id, {
        x: at.x + Math.cos(a) * d,
        y: at.y + Math.sin(a) * d,
        vx: 0, vy: 0, fixed: false,
      });
    }
  }

  /* ── forces ────────────────────────────────────────────────────────────── */

  reheat(alpha = 0.55) {
    this.alpha = Math.max(this.alpha, alpha);
    if (!this.frame) this.frame = requestAnimationFrame(() => this._tick());
  }

  _tick() {
    this.frame = null;
    const nodes = this.nodes;
    const n = nodes.length;
    if (!n) return;

    const { w, h } = this.size();
    const cx = w / 2;
    const cy = h / 2;
    const a = this.alpha;
    const P = this.pos;

    // Repulsion. Softened by a floor on the distance so two nodes that land on
    // top of each other get a firm shove rather than an infinite one.
    for (let i = 0; i < n; i++) {
      const pi = P.get(nodes[i].id);
      const chargeI = nodes[i].kind === "schema" ? 2600 : 900;
      for (let j = i + 1; j < n; j++) {
        const pj = P.get(nodes[j].id);
        let dx = pi.x - pj.x;
        let dy = pi.y - pj.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        if (d2 > 360000) continue;      // far enough to ignore; keeps the loop cheap
        const chargeJ = nodes[j].kind === "schema" ? 2600 : 900;
        const f = ((chargeI + chargeJ) / 2 / d2) * a;
        const d = Math.sqrt(d2);
        const ux = (dx / d) * f;
        const uy = (dy / d) * f;
        pi.vx += ux; pi.vy += uy;
        pj.vx -= ux; pj.vy -= uy;
      }
    }

    // Springs. A `contains` edge is the shortest because it is the one holding a
    // source's cluster together; a cross-source join is the longest because the
    // two clusters it ties should stay legibly apart rather than collapse.
    for (const e of this.edges) {
      const p1 = P.get(e.from);
      const p2 = P.get(e.to);
      if (!p1 || !p2) continue;
      const rest = e.kind === "contains" ? 78
        : e.kind === "belongs_to" ? 62
        : e.kind === "identifies" ? 150
        : e.cross_source ? 190 : 108;
      // The crew member is tied loosely to everything they appear in, so they
      // settle in the middle of their own evidence rather than dragging one
      // source's cluster out of shape to reach it.
      const k = e.kind === "contains" ? 0.035
        : e.kind === "belongs_to" ? 0.022
        : e.kind === "identifies" ? 0.008 : 0.018;
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = ((d - rest) / d) * k * a;
      const fx = dx * f;
      const fy = dy * f;
      p1.vx += fx; p1.vy += fy;
      p2.vx -= fx; p2.vy -= fy;
    }

    // Weak gravity, plus an anchor on the schema nodes only. Left ungravitated,
    // a source with no cross-source join drifts off the canvas for as long as
    // the simulation runs.
    for (const node of nodes) {
      const p = P.get(node.id);
      p.vx += (cx - p.x) * 0.0009 * a;
      p.vy += (cy - p.y) * 0.0009 * a;
      if (node._anchor) {
        p.vx += (node._anchor.x - p.x) * 0.012 * a;
        p.vy += (node._anchor.y - p.y) * 0.012 * a;
      }
    }

    for (const node of nodes) {
      const p = P.get(node.id);
      if (p.fixed) { p.vx = 0; p.vy = 0; continue; }
      p.vx *= 0.84;
      p.vy *= 0.84;
      p.x += clamp(p.vx, -30, 30);
      p.y += clamp(p.vy, -30, 30);
    }

    this._draw();
    this.alpha *= 0.985;
    if (this.alpha > 0.006) this.frame = requestAnimationFrame(() => this._tick());
  }

  _draw() {
    const P = this.pos;
    for (const node of this.nodes) {
      const p = P.get(node.id);
      node._el.setAttribute("transform", `translate(${p.x.toFixed(1)},${p.y.toFixed(1)})`);
    }
    for (const e of this.edges) {
      const p1 = P.get(e.from);
      const p2 = P.get(e.to);
      if (!p1 || !p2) continue;
      // Trimmed to the node edge so an arrowhead lands on the boundary rather
      // than under the circle it is pointing at.
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const r1 = (this._radius.get(e.from) || 8) + 1;
      const r2 = (this._radius.get(e.to) || 8) + (e.kind === "joins_to" ? 6 : 1);
      const l = e._el;
      l.setAttribute("x1", (p1.x + (dx / d) * r1).toFixed(1));
      l.setAttribute("y1", (p1.y + (dy / d) * r1).toFixed(1));
      l.setAttribute("x2", (p2.x - (dx / d) * r2).toFixed(1));
      l.setAttribute("y2", (p2.y - (dy / d) * r2).toFixed(1));
    }
  }

  get _radius() {
    if (!this.__radius || this.__radiusFor !== this.nodes) {
      this.__radius = new Map(this.nodes.map((n) => [n.id, n._r]));
      this.__radiusFor = this.nodes;
    }
    return this.__radius;
  }

  /* ── interaction ───────────────────────────────────────────────────────── */

  _bindNode(node, g) {
    let dragging = false;
    let moved = false;

    const down = (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      dragging = true;
      moved = false;
      const p = this.pos.get(node.id);
      p.fixed = true;
      g.setPointerCapture?.(ev.pointerId);

      const move = (e2) => {
        if (!dragging) return;
        moved = true;
        const w = this._toWorld(e2);
        p.x = w.x; p.y = w.y; p.vx = 0; p.vy = 0;
        this.reheat(0.35);
      };
      const up = () => {
        dragging = false;
        // A dragged node stays put: the reader moved it there on purpose, and
        // having it spring back is the single most irritating thing a force
        // layout can do. Double-click releases it.
        if (!moved) { p.fixed = false; this.select(node.id); }
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    };

    g.addEventListener("pointerdown", down);
    g.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      this.pos.get(node.id).fixed = false;
      this.reheat(0.4);
    });
    g.addEventListener("pointerenter", () => this._highlight(node.id, true));
    g.addEventListener("pointerleave", () => this._highlight(null, true));
  }

  _bindPanZoom() {
    this.svg.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const rect = this.svg.getBoundingClientRect();
      const px = ev.clientX - rect.left;
      const py = ev.clientY - rect.top;
      const v = this.view;
      const k = clamp(v.k * Math.exp(-ev.deltaY * 0.0015), 0.12, 5);
      v.x = px - ((px - v.x) * k) / v.k;
      v.y = py - ((py - v.y) * k) / v.k;
      v.k = k;
      this._applyView();
    }, { passive: false });

    this.svg.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".gv-node")) return;
      const start = { x: ev.clientX, y: ev.clientY, vx: this.view.x, vy: this.view.y };
      let moved = false;
      const move = (e2) => {
        moved = true;
        this.view.x = start.vx + (e2.clientX - start.x);
        this.view.y = start.vy + (e2.clientY - start.y);
        this._applyView();
      };
      const up = () => {
        if (!moved) this.select(null);
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  _toWorld(ev) {
    const rect = this.svg.getBoundingClientRect();
    const v = this.view;
    return {
      x: (ev.clientX - rect.left - v.x) / v.k,
      y: (ev.clientY - rect.top - v.y) / v.k,
    };
  }

  _applyView() {
    const v = this.view;
    this.viewport.setAttribute("transform", `translate(${v.x},${v.y}) scale(${v.k})`);
    this._applyLabelScale();
  }

  /* Labels are counter-scaled so they stay the same size on screen at every
   * zoom. Without this, zooming out to see the whole graph produces a page of
   * unreadable specks and zooming in produces text the size of the nodes. */
  _applyLabelScale() {
    const k = this.view.k;
    for (const n of this.nodes) {
      const base = n.kind === "schema" ? 12 : 9;
      n._label.setAttribute("font-size", (base / k).toFixed(2));
      n._label.setAttribute("y", (n._r + 10 / k).toFixed(1));
    }
    this._applyLabels();
  }

  /* Which captions are on.
   *
   * Counter-scaling keeps a label readable at any zoom; it does nothing about a
   * hundred of them competing for the same square inch. Whole-graph zoom is
   * exactly where they collide and exactly where they are least useful — the
   * shape of the clusters is the information at that distance — so the minor
   * labels come in as you zoom toward them, and hover, selection and search
   * reveal them regardless. `all` and `none` are there for when the reader
   * disagrees, which is what the button is for.
   */
  setLabelMode(mode) {
    this.labelMode = mode;
    this._applyLabels();
  }

  _applyLabels() {
    const mode = this.labelMode || "auto";
    this.svg.classList.toggle(
      "gv-labels-all", mode === "all" || (mode === "auto" && this.view.k >= 0.85),
    );
    this.svg.classList.toggle("gv-labels-none", mode === "none");
  }

  select(id) {
    this.selected = id;
    this._highlight(id, false);
    this.onSelect?.(id);
  }

  /* Hovering dims everything that is not adjacent. This is the feature the tab
   * is for: "what does this table join to" is unanswerable on a 350-edge
   * picture by looking, and immediate once everything else fades. */
  _highlight(id, transient) {
    const focus = id || (transient ? this.selected : id);
    this.onHover?.(transient ? id : null);
    if (!focus) {
      this.svg.classList.remove("gv-focused");
      for (const n of this.nodes) n._el.classList.remove("gv-near", "gv-focus");
      for (const e of this.edges) e._el.classList.remove("gv-near");
      return;
    }
    const near = this.adjacency.get(focus) || new Set([focus]);
    this.svg.classList.add("gv-focused");
    for (const n of this.nodes) {
      n._el.classList.toggle("gv-near", near.has(n.id));
      n._el.classList.toggle("gv-focus", n.id === focus);
    }
    for (const e of this.edges) {
      e._el.classList.toggle("gv-near", e.from === focus || e.to === focus);
    }
  }

  /* Search marks rather than filters. Removing the non-matches would answer
   * "where is MENTOR_FEEDBACK" by deleting everything it connects to, which is
   * the context that makes the answer worth having. */
  mark(query) {
    /* A crew identifier is never a text search term. It matches no table name,
     * so marking on it dims the entire graph behind zero matches — at exactly
     * the moment the crew node arrives to be looked at. Guarded here rather than
     * at the call sites because there are two, and the one that re-marks after
     * `setData` is the one that fired. */
    const typed = (query || "").trim();
    const q = CREW_ID.test(typed) ? "" : typed.toLowerCase();
    this.matches = q;
    let first = null;
    for (const n of this.nodes) {
      const hit = q && (
        n.name.toLowerCase().includes(q)
        || (n.label || "").toLowerCase().includes(q)
        || (n.kind === "table" && (n.detail.columns || [])
          .some((c) => c.name.toLowerCase().includes(q)))
      );
      n._el.classList.toggle("gv-match", !!hit);
      if (hit && !first) first = n;
    }
    this.svg.classList.toggle("gv-searching", !!q);
    return first;
  }

  centreOn(id) {
    const p = this.pos.get(id);
    if (!p) return;
    const { w, h } = this.size();
    this.view.k = Math.max(this.view.k, 1.1);
    this.view.x = w / 2 - p.x * this.view.k;
    this.view.y = h / 2 - p.y * this.view.k;
    this._applyView();
  }

  fit() {
    if (!this.nodes.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of this.nodes) {
      const p = this.pos.get(n.id);
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    const { w, h } = this.size();
    const pad = 60;
    const k = clamp(Math.min((w - pad * 2) / Math.max(1, maxX - minX),
                             (h - pad * 2) / Math.max(1, maxY - minY)), 0.12, 2.2);
    this.view.k = k;
    this.view.x = w / 2 - ((minX + maxX) / 2) * k;
    this.view.y = h / 2 - ((minY + maxY) / 2) * k;
    this._applyView();
  }

  scatter() {
    for (const p of this.pos.values()) p.fixed = false;
    this.pos.clear();
    this._seed();
    this.reheat(0.9);
  }

  destroy() {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = null;
  }
}

/* ── inspector ────────────────────────────────────────────────────────────── */

const ROLE_ORDER = ["identity", "measure", "dimension", "temporal", "free_text",
                    "temporal_control", "audit"];

function TableDetail({ node }) {
  const d = node.detail;
  const byRole = {};
  for (const c of d.columns || []) (byRole[c.role || "unclassified"] ??= []).push(c.name);
  const roles = Object.keys(byRole).sort(
    (a, b) => (ROLE_ORDER.indexOf(a) + 99) % 99 - (ROLE_ORDER.indexOf(b) + 99) % 99,
  );

  return html`
    <${React.Fragment}>
      ${d.grain ? html`<p class="gv-grain">${d.grain}</p>` : null}
      ${d.description ? html`<p class="gv-desc">${d.description}</p>` : null}
      <dl class="gv-facts">
        <dt>Schema</dt><dd>${node.schema || "—"}</dd>
        <dt>Engine</dt><dd>${node.engine || "—"}</dd>
        <dt>Columns</dt><dd>${(d.columns || []).length}</dd>
        ${d.scd2 ? html`<dt>Versioning</dt><dd>SCD-2 — needs a currency filter</dd>` : null}
        ${d.measures?.length
          ? html`<dt>Measures</dt><dd>${d.measures.join(", ")}</dd>` : null}
      </dl>
      ${roles.map((role) => html`
        <div class="gv-cols" key=${role}>
          <h5>${role.replace(/_/g, " ")} <span>${byRole[role].length}</span></h5>
          <div class="gv-chiprow">
            ${byRole[role].map((c) => html`<code key=${c}>${c}</code>`)}
          </div>
        </div>`)}
      ${Object.keys(d.decodes || {}).length ? html`
        <div class="gv-cols">
          <h5>code meanings</h5>
          ${Object.entries(d.decodes).map(([col, map]) => html`
            <div class="gv-decode" key=${col}>
              <code>${col}</code>
              <span>${Object.entries(map).slice(0, 10)
                .map(([k, v]) => `${k}=${v}`).join(" · ")}</span>
            </div>`)}
        </div>` : null}
    <//>`;
}

/* Where one crew member's record actually is — and, more usefully, is not. */
function CrewPanel({ crew, loading, error, onGo, onClear }) {
  if (loading) return html`<div class="gv-crewpanel gv-muted">Looking up…</div>`;
  if (error) return html`<div class="gv-crewpanel gv-crewerr">${error}</div>`;
  if (!crew) return null;

  const s = crew.stats;
  const holding = crew.presence.filter((p) => p.rows > 0);
  const empty = crew.presence.filter((p) => !p.error && !p.rows);
  const broken = crew.presence.filter((p) => p.error);
  const bySource = Object.entries(crew.rows_by_source).sort((a, b) => b[1] - a[1]);

  return html`
    <div class="gv-crewpanel">
      <div class="gv-insp-top">
        <div>
          <div class="gv-kind" style=${{ color: CREW_COLOUR }}>crew member</div>
          <h3>${crew.identifier}</h3>
          <code class="gv-id">
            ${Object.entries(crew.keys).map(([k, v]) => `${k} ${v}`).join(" · ")}
          </code>
        </div>
        <button class="gv-x" onClick=${onClear} aria-label="Clear">✕</button>
      </div>

      <dl class="gv-facts">
        <dt>Appears in</dt>
        <dd>${s.tables_holding} of ${s.tables_probed} crew-bearing tables</dd>
        <dt>Across</dt><dd>${s.sources_holding} of ${s.sources_probed} sources</dd>
        <dt>Rows</dt><dd>${s.total_rows}</dd>
      </dl>

      <div class="gv-cols">
        <h5>rows by source</h5>
        ${bySource.map(([src, n]) => html`
          <div class="gv-bar-row" key=${src}>
            <span class="gv-bar-label" style=${{ color: colourOf(src) }}>${src}</span>
            <span class="gv-bar-track">
              <i style=${{
                width: `${Math.max(2, (n / Math.max(...bySource.map((b) => b[1]), 1)) * 100)}%`,
                background: n ? colourOf(src) : "transparent",
              }}></i>
            </span>
            <b>${n}</b>
          </div>`)}
      </div>

      ${holding.length ? html`
        <div class="gv-cols">
          <h5>holds rows <span>${holding.length}</span></h5>
          ${holding.sort((a, b) => b.rows - a.rows).map((p) => html`
            <button class="gv-link" key=${p.node_id} onClick=${() => onGo(p.node_id)}>
              <span class="gv-link-name" style=${{ color: colourOf(p.source) }}>
                ${p.table}
              </span>
              <span class="gv-link-meta">${p.rows} on ${p.column} = ${p.value}</span>
            </button>`)}
        </div>` : null}

      ${/* The half that matters. A source with no rows is missing data, not a
            low score, and every warning the pipeline emits about coverage is
            explained by exactly this list. */
        empty.length ? html`
        <div class="gv-cols">
          <h5>no rows <span>${empty.length}</span></h5>
          <div class="gv-chiprow">
            ${empty.map((p) => html`
              <code key=${p.node_id} class="gv-empty-chip" title=${`${p.source} · ${p.column}`}>
                ${p.table}
              </code>`)}
          </div>
        </div>` : null}

      ${broken.length ? html`
        <div class="gv-cols">
          <h5>could not read <span>${broken.length}</span></h5>
          <div class="gv-chiprow">
            ${broken.map((p) => html`
              <code key=${p.node_id} title=${p.error}>${p.table}</code>`)}
          </div>
        </div>` : null}

      ${crew.warnings?.length ? html`
        <ul class="gv-crewnotes">
          ${crew.warnings.map((w, i) => html`<li key=${i}>${w}</li>`)}
        </ul>` : null}
    </div>`;
}

function Inspector({ node, edges, nodesById, onGo, onClose }) {
  if (!node) {
    return html`
      <div class="gv-inspector gv-empty">
        <p><b>Nothing selected.</b></p>
        <p>Click a node for what it holds and what it connects to. Hover to
           isolate its neighbourhood. Drag to reposition — a dragged node stays
           where you put it, double-click releases it.</p>
      </div>`;
  }

  const touching = edges.filter((e) => e.from === node.id || e.to === node.id);
  const joins = touching.filter((e) => e.kind === "joins_to");
  const concepts = touching.filter((e) => e.kind === "belongs_to");

  return html`
    <div class="gv-inspector">
      <div class="gv-insp-top">
        <div>
          <div class="gv-kind" style=${{ color: colourOf(node.source) }}>
            ${node.kind} · ${node.source}
          </div>
          <h3>${node.label || node.name}</h3>
          ${node.label !== node.name ? html`<code class="gv-id">${node.name}</code>` : null}
        </div>
        <button class="gv-x" onClick=${onClose} aria-label="Close">✕</button>
      </div>

      ${/* In crew mode the first thing to say about a table is whether this
            person is in it. It is the question that was asked. */
        node.crewRows !== undefined ? html`
        <div class=${`gv-crewrows ${node.crewRows ? "has" : "none"}`}>
          ${node.crewError ? `Could not read this table — ${node.crewError}`
            : node.crewRows ? `${node.crewRows} row${node.crewRows === 1 ? "" : "s"} for this crew member`
            : "No rows for this crew member"}
        </div>` : null}

      ${node.kind === "table" ? html`<${TableDetail} node=${node} />` : null}

      ${node.kind === "concept" ? html`
        <${React.Fragment}>
          <p class="gv-desc">${node.detail.description}</p>
          <div class="gv-cols">
            <h5>drawn from <span>${(node.detail.source_tables || []).length}</span></h5>
            <div class="gv-chiprow">
              ${(node.detail.source_tables || []).map((t) => html`<code key=${t}>${t}</code>`)}
            </div>
          </div>
        <//>` : null}

      ${node.kind === "schema" ? html`
        <${React.Fragment}>
          <p class="gv-desc">${node.detail.description}</p>
          <dl class="gv-facts">
            <dt>Engine</dt>
            <dd>${node.detail.warehouse ? "Snowflake warehouse" : "local file extract"}</dd>
            ${node.schema ? html`<dt>Schema</dt><dd>${node.schema}</dd>` : null}
            <dt>Join key</dt><dd>${node.detail.join_key || "—"}</dd>
            <dt>Crew master</dt><dd>${node.detail.crew_master || "—"}</dd>
            <dt>Tables</dt><dd>${node.detail.tables}</dd>
            <dt>Concepts</dt><dd>${node.detail.concepts}</dd>
          </dl>
        <//>` : null}

      ${joins.length ? html`
        <div class="gv-cols">
          <h5>joins <span>${joins.length}</span></h5>
          ${joins.map((e) => {
            const otherId = e.from === node.id ? e.to : e.from;
            const other = nodesById[otherId];
            return html`
              <button class="gv-link" key=${e.id} onClick=${() => onGo(otherId)}>
                <span class="gv-link-name" style=${{ color: colourOf(other?.source) }}>
                  ${other?.name || otherId}
                </span>
                <span class="gv-link-meta">
                  ${e.detail.source_column} = ${e.detail.target_column}
                  ${e.cross_source ? " · crosses sources" : ""}
                  ${e.verified ? "" : " · unverified"}
                  ${e.detail.cardinality ? ` · ${e.detail.cardinality}` : ""}
                </span>
              </button>`;
          })}
        </div>` : null}

      ${concepts.length ? html`
        <div class="gv-cols">
          <h5>concepts <span>${concepts.length}</span></h5>
          ${concepts.map((e) => {
            const otherId = e.from === node.id ? e.to : e.from;
            const other = nodesById[otherId];
            return html`
              <button class="gv-link" key=${e.id} onClick=${() => onGo(otherId)}>
                <span class="gv-link-name">${other?.label || otherId}</span>
              </button>`;
          })}
        </div>` : null}
    </div>`;
}

/* ── the tab ──────────────────────────────────────────────────────────────── */

const LABEL_MODES = ["auto", "all", "none"];

const DEFAULT_FILTERS = {
  kinds: { schema: true, table: true, concept: true },
  edgeKinds: { contains: true, belongs_to: true, joins_to: true },
  sources: {},
  minConfidence: 0,
  verifiedOnly: false,
  crossOnly: false,
};

export function GraphVisualiser({ focus = "" }) {
  const [payload, setPayload] = useState(null);
  const [crew, setCrew] = useState(null);
  const [crewError, setCrewError] = useState("");
  const [crewLoading, setCrewLoading] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [origin, setOrigin] = useState("artifacts");
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedId, setSelectedId] = useState(null);
  const [query, setQuery] = useState("");
  const [labelMode, setLabelMode] = useState("auto");

  const svgRef = useRef(null);
  const engineRef = useRef(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    fetch(`/api/graph-view?origin=${origin}`)
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
        return body;
      })
      .then((data) => {
        if (!live) return;
        setPayload(data);
        setFilters((f) => ({
          ...f,
          sources: Object.fromEntries((data.stats.sources || []).map((s) => [s, true])),
        }));
      })
      .catch((e) => live && setError(String(e.message || e)))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [origin]);

  // The engine outlives every render; React only ever hands it new data.
  useEffect(() => {
    if (!svgRef.current || engineRef.current) return;
    engineRef.current = new ForceGraph(svgRef.current, {
      onSelect: (id) => setSelectedId(id),
    });
    const ro = new ResizeObserver(() => engineRef.current?.reheat(0.12));
    ro.observe(svgRef.current);
    return () => { ro.disconnect(); engineRef.current?.destroy(); engineRef.current = null; };
  }, []);

  /* Look a crew identifier up against the live data.
   *
   * Debounced, and gated on the identifier pattern: the search box is primarily
   * a find-a-table box, and firing a fan of COUNT(*) on every keystroke of
   * "crew_id" would be a data query per character. */
  const lookup = useCallback((ident) => {
    if (!ident) { setCrew(null); setCrewError(""); return; }
    setCrewLoading(true);
    setCrewError("");
    fetch(`/api/graph-view/crew/${encodeURIComponent(ident)}`)
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
        return body;
      })
      .then(setCrew)
      .catch((e) => { setCrew(null); setCrewError(String(e.message || e)); })
      .finally(() => setCrewLoading(false));
  }, []);

  useEffect(() => {
    const ident = (focus || query).trim();
    if (!CREW_ID.test(ident)) { if (!focus) setCrew(null); return; }
    const t = setTimeout(() => lookup(ident.toUpperCase()), focus ? 0 : 350);
    return () => clearTimeout(t);
  }, [query, focus, lookup]);

  /* The crew member joins the same graph rather than replacing it.
   *
   * A separate "crew view" would answer where they appear and lose what those
   * tables are — the whole value is that MENTOR_FEEDBACK is still sitting next
   * to PEP_QUESTION_FEEDBACK and its concepts when you see six rows on it. */
  const withCrew = useMemo(() => {
    if (!payload) return null;
    if (!crew) return payload;

    const holding = crew.presence.filter((p) => p.rows > 0);
    const crewNode = {
      id: `__crew__${crew.identifier}`,
      kind: "crew",
      name: crew.identifier,
      label: crew.identifier,
      source: "",
      engine: "",
      schema: "",
      in_subset: true,
      detail: crew,
    };
    const edges = holding.map((p) => ({
      id: `identifies:${crewNode.id}:${p.node_id}`,
      from: crewNode.id,
      to: p.node_id,
      kind: "identifies",
      label: `${p.rows} row${p.rows === 1 ? "" : "s"}`,
      confidence: 1,
      verified: true,
      cross_source: false,
      detail: p,
    }));
    /* Probed-and-empty is marked on the node, not left to the absence of an
     * edge. "No rows here" and "this table has no crew key at all" look
     * identical once you are only drawing the edges that exist, and they are
     * completely different facts: one is a gap in this person's record, the
     * other is a reference table that was never about a person. */
    const probed = new Map(crew.presence.map((p) => [p.node_id, p]));
    const nodes = payload.nodes.map((n) => {
      const p = probed.get(n.id);
      return p ? { ...n, crewRows: p.error ? null : p.rows, crewError: p.error } : n;
    });

    return {
      ...payload,
      nodes: [crewNode, ...nodes],
      edges: [...payload.edges, ...edges],
    };
  }, [payload, crew]);

  const visible = useMemo(() => {
    if (!withCrew) return { nodes: [], edges: [] };
    const f = filters;
    const nodes = withCrew.nodes.filter(
      (n) => n.kind === "crew" || (f.kinds[n.kind] && (f.sources[n.source] ?? true)),
    );
    const ids = new Set(nodes.map((n) => n.id));
    const edges = withCrew.edges.filter((e) => {
      // An IDENTIFIES edge is the answer to the question that was asked. It is
      // not subject to the join filters, which are about the schema.
      if (e.kind === "identifies") return ids.has(e.from) && ids.has(e.to);
      if (!f.edgeKinds[e.kind]) return false;
      if (!ids.has(e.from) || !ids.has(e.to)) return false;
      if (e.kind !== "joins_to") return true;
      if (e.confidence < f.minConfidence) return false;
      if (f.verifiedOnly && !e.verified) return false;
      if (f.crossOnly && !e.cross_source) return false;
      return true;
    });
    return { nodes, edges };
  }, [withCrew, filters]);

  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || !payload) return;
    engine.setData(visible.nodes, visible.edges);
    engine.mark(query);
    const t = setTimeout(() => engine.fit(), 700);
    return () => clearTimeout(t);
  }, [visible, payload]);

  // `mark` ignores crew identifiers itself, so this only ever centres on a
  // genuine table/column hit.
  useEffect(() => {
    const first = engineRef.current?.mark(query);
    if (first && query.length > 2) engineRef.current.centreOn(first.id);
  }, [query]);

  // Land on the crew member once their node is in the graph.
  useEffect(() => {
    if (!crew) return;
    const id = `__crew__${crew.identifier}`;
    const t = setTimeout(() => {
      engineRef.current?.select(id);
      engineRef.current?.centreOn(id);
    }, 900);
    return () => clearTimeout(t);
  }, [crew]);

  useEffect(() => { engineRef.current?.setLabelMode(labelMode); }, [labelMode, visible]);

  const nodesById = useMemo(
    () => Object.fromEntries((withCrew?.nodes || []).map((n) => [n.id, n])),
    [withCrew],
  );
  const selected = selectedId ? nodesById[selectedId] : null;

  const goTo = useCallback((id) => {
    setSelectedId(id);
    engineRef.current?.select(id);
    engineRef.current?.centreOn(id);
  }, []);

  const toggle = (group, key) =>
    setFilters((f) => ({ ...f, [group]: { ...f[group], [key]: !f[group][key] } }));

  const stats = payload?.stats;

  return html`
    <div class="gv">
      <div class="gv-bar">
        <div class="gv-search">
          <input
            type="search" value=${query}
            placeholder="Find a table, concept, column — or an IGA…"
            onInput=${(e) => setQuery(e.target.value)} />
        </div>

        <div class="gv-group">
          ${NODE_KINDS.map(([k, label]) => html`
            <button class="gv-toggle" key=${k} aria-pressed=${filters.kinds[k]}
                    onClick=${() => toggle("kinds", k)}>
              <i class=${`gv-swatch gv-sw-${k}`}></i>${label}
            </button>`)}
        </div>

        <div class="gv-group">
          ${EDGE_KINDS.map(([k, label]) => html`
            <button class="gv-toggle" key=${k} aria-pressed=${filters.edgeKinds[k]}
                    onClick=${() => toggle("edgeKinds", k)}>${label}</button>`)}
        </div>

        <div class="gv-group gv-right">
          <button class="gv-toggle" title="Cycle label density"
                  onClick=${() => setLabelMode(
                    (m) => LABEL_MODES[(LABEL_MODES.indexOf(m) + 1) % LABEL_MODES.length])}>
            Labels: ${labelMode}
          </button>
          <button class="gv-toggle" onClick=${() => engineRef.current?.fit()}>Fit</button>
          <button class="gv-toggle" onClick=${() => engineRef.current?.scatter()}>Relayout</button>
          <select class="gv-select" value=${origin}
                  onChange=${(e) => setOrigin(e.target.value)}>
            <option value="artifacts">graphs/ (built)</option>
            <option value="cosmos">Cosmos (live)</option>
          </select>
        </div>
      </div>

      <div class="gv-bar gv-bar2">
        <div class="gv-group">
          ${(stats?.sources || []).map((s) => html`
            <button class="gv-toggle gv-src" key=${s} aria-pressed=${filters.sources[s] ?? true}
                    onClick=${() => toggle("sources", s)}>
              <i class="gv-swatch" style=${{ background: colourOf(s) }}></i>${s}
            </button>`)}
        </div>

        <div class="gv-group gv-right">
          <label class="gv-slider">
            join confidence ≥ <b>${filters.minConfidence.toFixed(2)}</b>
            <input type="range" min="0" max="1" step="0.05"
                   value=${filters.minConfidence}
                   onInput=${(e) => setFilters((f) => ({
                     ...f, minConfidence: Number(e.target.value) }))} />
          </label>
          <button class="gv-toggle" aria-pressed=${filters.verifiedOnly}
                  onClick=${() => setFilters((f) => ({ ...f, verifiedOnly: !f.verifiedOnly }))}>
            Verified only</button>
          <button class="gv-toggle" aria-pressed=${filters.crossOnly}
                  onClick=${() => setFilters((f) => ({ ...f, crossOnly: !f.crossOnly }))}>
            Cross-source only</button>
        </div>
      </div>

      <div class="gv-body">
        <div class="gv-canvas">
          <svg ref=${svgRef} class="gv-svg"></svg>

          ${loading ? html`<div class="gv-veil">Loading the graph…</div>` : null}
          ${error ? html`<div class="gv-veil gv-error">
            <b>Could not load the graph</b><span>${error}</span>
            ${origin === "artifacts" ? html`<span class="gv-hint">
              Run <code>crewperf build-graph PEP</code> for each source, then
              <code>crewperf build-bridge</code>.</span>` : null}
          </div>` : null}

          ${stats ? html`
            <div class="gv-count">
              ${`${visible.nodes.length} of ${withCrew.nodes.length} nodes`
                + ` · ${visible.edges.length} of ${withCrew.edges.length} edges`}
            </div>` : null}

          <div class="gv-legend">
            <div><i class="gv-swatch gv-sw-schema"></i>schema</div>
            <div><i class="gv-swatch gv-sw-table"></i>table</div>
            <div><i class="gv-swatch gv-sw-concept"></i>concept</div>
            ${crew ? html`
              <${React.Fragment}>
                <div><i class="gv-swatch gv-sw-crew"></i>crew member</div>
                <div><i class="gv-line gv-line-crew"></i>appears in</div>
                <div><i class="gv-swatch gv-sw-empty"></i>no rows for them</div>
              <//>` : null}
            <div><i class="gv-line"></i>join</div>
            <div><i class="gv-line gv-line-cross"></i>cross-source</div>
            <div><i class="gv-line gv-line-dash"></i>unverified</div>
          </div>
        </div>

        <aside class="gv-side">
          ${stats ? html`
            <div class="gv-summary">
              <div><span>Schemas</span><b>${stats.schemas}</b></div>
              <div><span>Tables</span><b>${stats.tables}</b></div>
              <div><span>Concepts</span><b>${stats.concepts}</b></div>
              <div><span>Joins</span><b>${stats.joins}</b></div>
              <div><span>Verified</span><b>${stats.verified_joins}</b></div>
              <div><span>Cross-source</span><b>${stats.cross_source_joins}</b></div>
            </div>` : null}

          ${payload?.warnings?.length ? html`
            <details class="gv-warn">
              <summary>${payload.warnings.length} note${payload.warnings.length === 1 ? "" : "s"} from the build</summary>
              <ul>${payload.warnings.map((w, i) => html`<li key=${i}>${w}</li>`)}</ul>
            </details>` : null}

          <${CrewPanel} crew=${crew} loading=${crewLoading} error=${crewError}
                        onGo=${goTo}
                        onClear=${() => { setCrew(null); setCrewError(""); setQuery(""); }} />

          <${Inspector} node=${selected} edges=${withCrew?.edges || []}
                        nodesById=${nodesById} onGo=${goTo}
                        onClose=${() => { setSelectedId(null); engineRef.current?.select(null); }} />
        </aside>
      </div>
    </div>`;
}
