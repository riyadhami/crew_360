/* Crew Performance — the whole front end.
 *
 * React without a build step: `htm` is a tagged template that produces the same
 * calls JSX compiles to, so these are ordinary React components and there is no
 * toolchain, no node_modules and nothing to rebuild before a deploy.
 *
 * ── The one rule this file exists to keep ──────────────────────────────────
 * An answer has two halves and they are never mixed. The visible half is plain
 * English: crew names, a score out of ten, the reasons in the words the mentors
 * and the reports actually used. The other half — the SQL, the signal
 * identifiers, the weights, the audit — lives behind `Detail` and opens on a
 * click.
 *
 * The split is made on the server (`_result_payload`), not here, because it is a
 * property of the answer rather than of this page. What this file must not do is
 * undo it: nothing out of `payload.detail` may be rendered outside `Detail`.
 */

import htm from "/static/vendor/htm.module.js";
import { GraphVisualiser } from "/visualiser/visualiser.js";

const { createElement, useState, useEffect, useRef, useCallback, Fragment } = React;
const html = htm.bind(createElement);

/* ── helpers ─────────────────────────────────────────────────────────────── */

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* The narrative comes back as prose with the occasional bold run and bullet
 * list. A full markdown library is a dependency for three constructs, and the
 * input is model output rendered into a page — so it is escaped FIRST and the
 * three constructs are re-introduced afterwards. Nothing else can become
 * markup, whatever the model writes. */
function renderProse(text) {
  const src = String(text ?? "").trim();
  if (!src) return null;
  const blocks = src.split(/\n{2,}/);
  const out = blocks.map((block) => {
    const lines = block.split("\n");
    const bullets = lines.every((l) => /^\s*[-*•]\s+/.test(l));
    if (bullets) {
      const items = lines.map((l) => inline(l.replace(/^\s*[-*•]\s+/, "")));
      return `<ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul>`;
    }
    return `<p>${inline(block)}</p>`;
  });
  return { __html: out.join("") };

  function inline(s) {
    return escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|\s)\*(?!\s)(.+?)\*(?=\s|$)/g, "$1<em>$2</em>");
  }
}

const fmt = (v) => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  return String(v);
};

/* Score bands are presentational only, and they attach to the SCORE — never to
 * anything beside it. Tinting the first chip by the composite put an amber wash
 * on "grade A", which reads as a warning about the grade and is a claim about
 * the assessment that nothing here is making. They are deliberately wide and
 * unlabelled: a colour is a hint about where to look, and naming 6.4/10 "amber"
 * in a legend would invent a grading rule the scoring system does not have. */
const band = (score) =>
  score === null || score === undefined ? "n" : score >= 6.5 ? "g" : score >= 4 ? "w" : "b";

/* ── icons ───────────────────────────────────────────────────────────────── */

const Icon = {
  menu: () => html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>`,
  close: () => html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`,
  send: () => html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15M13 6l6 6-6 6"/></svg>`,
};

/* ── crew card ───────────────────────────────────────────────────────────── */

function CrewCard({ member }) {
  const facts = [
    member.grade && `grade ${member.grade}`,
    member.assessment_mark !== null && member.assessment_mark !== undefined
      && `assessment mark ${member.assessment_mark}`,
    member.assessments ? `${member.assessments} assessment${member.assessments === 1 ? "" : "s"}` : null,
    `confidence ${member.confidence}`,
    member.based_on,
  ].filter(Boolean);

  return html`
    <div class="card">
      <div class="card-top">
        <div class="who">
          <h3>${member.name}</h3>
          <div class="sub">
            ${[member.iga, member.base, member.designation].filter(Boolean).join(" · ")}
          </div>
        </div>
        <div class="score">
          <div class="n ${band(member.score)}">
            ${member.score === null || member.score === undefined ? "—" : member.score}
          </div>
          <div class="of">out of ${member.out_of}</div>
        </div>
      </div>

      <div class="chips">
        ${facts.map((f, i) => html`<span class="chip n" key=${i}>${f}</span>`)}
      </div>

      <div class="reasons">
        ${member.strengths?.length ? html`
          <div class="reason-set">
            <h4>What stands out</h4>
            <ul>${member.strengths.map((s, i) => html`<li key=${i}>${s}</li>`)}</ul>
          </div>` : null}
        ${member.watch?.length ? html`
          <div class="reason-set">
            <h4>What to watch</h4>
            <ul>${member.watch.map((s, i) => html`<li key=${i}>${s}</li>`)}</ul>
          </div>` : null}
      </div>
    </div>`;
}

/* ── result table (a data question's rows ARE its answer) ────────────────── */

function ResultTable({ table }) {
  if (!table?.rows?.length) return null;
  return html`
    <div class="tablewrap">
      <table>
        <thead><tr>${table.columns.map((c, i) => html`<th key=${i}>${c}</th>`)}</tr></thead>
        <tbody>
          ${table.rows.slice(0, 100).map((row, i) => html`
            <tr key=${i}>${row.map((v, j) => html`<td key=${j}>${fmt(v)}</td>`)}</tr>`)}
        </tbody>
      </table>
    </div>`;
}

/* ── detail: everything technical, and nothing until asked ───────────────── */

function Detail({ detail, crew }) {
  const [open, setOpen] = useState(null);
  if (!detail) return null;

  const reasoning = [
    detail.ranked_on && `Ranked on ${detail.ranked_on}.`,
    ...(detail.reasoning || []),
    ...(detail.unavailable || []).map((u) => `Not available: ${u}`),
    ...(detail.audit || []).map((a) => `Audit — ${a}`),
    ...(detail.rejections || []).map((r) => `Query rejected before it ran: ${r}`),
    detail.routed && `Routed as ${detail.routed}.`,
  ].filter(Boolean);

  const tabs = [
    reasoning.length && ["reasoning", "Why this answer"],
    detail.sql && ["sql", "SQL"],
    // Counted, not total: a label reading "Signals (14)" over a table of one
    // is the clog this panel was trimmed to remove, restated in the tab.
    detail.signals?.length &&
      ["signals", `Signals (${detail.signals.filter(counted).length})`],
  ].filter(Boolean);

  if (!tabs.length) return null;
  const toggle = (k) => setOpen(open === k ? null : k);

  return html`
    <div class="detail">
      <div class="detail-bar">
        ${tabs.map(([k, label]) => html`
          <button class="toggle" key=${k} aria-pressed=${open === k}
                  onClick=${() => toggle(k)}>${label}</button>`)}
      </div>

      ${open === "reasoning" ? html`
        <div class="panel">
          <h4>How this was worked out</h4>
          <ul>${reasoning.map((r, i) => html`<li key=${i}>${r}</li>`)}</ul>
        </div>` : null}

      ${open === "sql" ? html`
        <div class="panel">
          <h4>Query that produced this</h4>
          <pre class="sql">${detail.sql}</pre>
        </div>` : null}

      ${open === "signals" ? html`<${SignalsPanel} detail=${detail} crew=${crew} />` : null}
    </div>`;
}

/* Did this signal actually count towards the score?
 *
 * A question that names an aspect is ranked on that aspect alone, and every
 * other attribute comes back at zero weight rather than being deleted — see
 * `weighting.refocus`. That is right for the payload and wrong for the panel:
 * ask about leave and the table was one weighted row under thirteen zeroes.
 * Guarded with Number() so a null weight — unknown, not zero — also falls out
 * of the counted set rather than reading as 0%. */
const counted = (s) => Number(s.weight) > 0;

function SignalsPanel({ detail, crew }) {
  /* Grouped by crew member, never flat.
   *
   * A ranking answers for several people and every one of them carries the same
   * signal names — `initiative_rate`, `pass_rate_grooming`, and so on. Rendered
   * as one list that is five identical-looking blocks whose numbers happen to
   * differ, which reads as the same signal repeated rather than as five crew.
   * The rows always carried `iga`; nothing was showing it. */
  const [showRest, setShowRest] = useState(false);
  const names = Object.fromEntries((crew || []).map((m) => [m.iga, m.name]));
  const order = [];
  const groups = {};
  for (const s of detail.signals) {
    const key = s.iga || "";
    if (!(key in groups)) { groups[key] = []; order.push(key); }
    groups[key].push(s);
  }
  const hidden = detail.signals.filter((s) => !counted(s)).length;

  const rows = (list) => list.map((s, i) => html`
    <tr key=${i}>
      <td>${s.signal}${s.measures
        ? html`<div class="id">${s.measures}</div>` : null}</td>
      <td class="id">${s.identifier}</td>
      <td class="num">${fmt(s.value)}</td>
      <td class="num">${s.percentile === null ? "—" : Math.round(s.percentile)}</td>
      <td class="num">${s.weight === null ? "—"
        : `${(s.weight * 100).toFixed(s.weight < 0.01 && s.weight > 0 ? 1 : 0)}%`}</td>
    </tr>`);

  return html`
    <div class="panel">
      <h4>What went into the score</h4>
      ${order.map((iga) => {
        const shown = groups[iga].filter((s) => showRest || counted(s));
        if (!shown.length) return null;
        return html`
        <div class="sig-group" key=${iga}>
          ${order.length > 1 ? html`
            <div class="sig-who">${names[iga] || iga}
              ${names[iga] ? html`<span class="id"> · ${iga}</span>` : null}</div>` : null}
          <div class="tablewrap sigwrap">
            <table class="sig">
              <thead><tr>
                <th>Signal</th><th>Identifier</th><th class="num">Value</th>
                <th class="num">Percentile</th><th class="num">Weight</th>
              </tr></thead>
              <tbody>${rows(shown)}</tbody>
            </table>
          </div>
        </div>`;
      })}

      ${/* Not merely decoration. A narrow ranking and a complete one look
            identical once the uncounted rows are hidden, so the count stays on
            the page and stays openable — the panel exists to be checked. */
        hidden ? html`
        <button class="toggle sig-more" aria-pressed=${showRest}
                onClick=${() => setShowRest(!showRest)}>
          ${showRest ? "Hide" : "Show"} ${hidden} signal${hidden === 1 ? "" : "s"}
          ${" "}this question did not count
        </button>` : null}
    </div>`;
}

/* ── one message ─────────────────────────────────────────────────────────── */

function Message({ msg }) {
  if (msg.role === "user") {
    return html`
      <div class="msg user">
        <div class="avatar you">You</div>
        <div class="bubble"><div class="prose">${msg.text}</div></div>
      </div>`;
  }

  const prose = renderProse(msg.answer);
  return html`
    <div class="msg">
      <div class="avatar bot">CP</div>
      <div class="bubble">
        ${msg.error ? html`<div class="err">${msg.error}</div>` : null}

        ${msg.pending ? html`
          <div class="stages">
            ${(msg.stages || []).map((s, i) => html`
              <div class="stage" key=${i}><b>${s.stage}</b><span>${s.detail}</span></div>`)}
            <div class="stage"><span class="pulse"></span></div>
          </div>` : null}

        ${prose ? html`<div class="prose" dangerouslySetInnerHTML=${prose}></div>` : null}
        ${msg.crew?.length ? html`
          <div class="cards">
            ${msg.crew.map((m) => html`<${CrewCard} key=${m.iga} member=${m} />`)}
          </div>` : null}
        <${ResultTable} table=${msg.table} />
        ${!msg.pending ? html`<${Detail} detail=${msg.detail} crew=${msg.crew} />` : null}
      </div>
    </div>`;
}

/* ── welcome ─────────────────────────────────────────────────────────────── */

const SUGGESTIONS = [
  "How is IGA60406 performing?",
  "Rank the weakest 3 crew at DEL",
  "Which crew get the best feedback?",
  "How many crew are based at BLR?",
];

function Welcome({ onPick }) {
  return html`
    <div class="hero">
      <h2>Crew Performance</h2>
      <p>Ask about a crew member, a base, or the fleet. Every answer says who,
         how they scored and why — the queries and the workings are one click away.</p>
      <div class="suggests">
        ${SUGGESTIONS.map((s) => html`
          <button class="suggest" key=${s} onClick=${() => onPick(s)}>${s}</button>`)}
      </div>
    </div>`;
}

/* ── composer ────────────────────────────────────────────────────────────── */

function Composer({ onSend, busy }) {
  const [text, setText] = useState("");
  const ref = useRef(null);

  const grow = (el) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 170)}px`;
  };

  const submit = () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    if (ref.current) ref.current.style.height = "auto";
    onSend(q);
  };

  return html`
    <div class="composer-wrap">
      <div class="composer">
        <textarea
          ref=${ref} rows="1" value=${text} disabled=${busy}
          placeholder=${busy ? "Working…" : "What is your question?"}
          onInput=${(e) => { setText(e.target.value); grow(e.target); }}
          onKeyDown=${(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
          }} />
        <button class="send" onClick=${submit} disabled=${busy || !text.trim()}
                aria-label="Send"><${Icon.send} /></button>
      </div>
      <div class="hint">Enter to send · Shift+Enter for a new line</div>
    </div>`;
}

/* ── app ─────────────────────────────────────────────────────────────────── */

const TABS = [["chat", "Chat"], ["graph", "Graph Visualiser"]];

/* The open tab lives in the URL fragment, and so does what it is focused on:
 * `#graph` is the whole graph, `#graph/IGA60406` is one crew member's ego view.
 * A graph someone wants a colleague to look at is worth a link, and a reload in
 * the middle of reading one should not drop you back into the chat. */
const routeFromHash = () => {
  const [tab, focus = ""] = window.location.hash.slice(1).split("/");
  return TABS.some(([k]) => k === tab)
    ? { tab, focus: decodeURIComponent(focus) }
    : { tab: "chat", focus: "" };
};

function App() {
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [route, setRoute] = useState(routeFromHash);
  const { tab, focus } = route;
  const [sidebar, setSidebar] = useState(window.innerWidth > 860);
  const [overview, setOverview] = useState(null);
  const session = useRef("");
  const bottom = useRef(null);
  const source = useRef(null);

  useEffect(() => {
    fetch("/api/overview").then((r) => r.json()).then(setOverview).catch(() => {});
    return () => source.current?.close();
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  /* One question. Stage events land on the pending message as they arrive, and
   * the result replaces it — the pipeline takes tens of seconds and a spinner
   * for that long says nothing about what is happening. */
  const ask = useCallback((question) => {
    setBusy(true);
    setMessages((m) => [
      ...m,
      { role: "user", text: question },
      { role: "bot", pending: true, stages: [] },
    ]);

    const patch = (fn) => setMessages((m) => {
      const out = m.slice();
      out[out.length - 1] = fn(out[out.length - 1]);
      return out;
    });

    const url = `/api/ask?q=${encodeURIComponent(question)}`
      + (session.current ? `&session=${encodeURIComponent(session.current)}` : "");
    const es = new EventSource(url);
    source.current = es;

    es.addEventListener("stage", (e) => {
      const ev = JSON.parse(e.data);
      patch((last) => ({ ...last, stages: [...(last.stages || []), ev].slice(-6) }));
    });

    es.addEventListener("result", (e) => {
      const payload = JSON.parse(e.data);
      session.current = payload.session || session.current;
      patch(() => ({
        role: "bot",
        pending: false,
        answer: payload.answer,
        crew: payload.crew,
        table: payload.table,
        detail: payload.detail,
      }));
    });

    es.addEventListener("error", (e) => {
      // Two different events arrive on this name: one the server sent with a
      // message, and the browser's own on a dropped connection. Only the first
      // has data, and reporting "undefined" for the second would describe a
      // failure that did not happen.
      let message = "";
      try { message = JSON.parse(e.data).message; } catch { /* transport */ }
      if (message) patch((last) => ({ ...last, pending: false, error: message }));
      es.close(); setBusy(false);
    });

    // The server closes the stream when the result is sent; that surfaces here.
    es.onerror = () => {
      es.close();
      setBusy(false);
      patch((last) => last.pending
        ? { ...last, pending: false, error: last.error || "The connection closed before an answer arrived." }
        : last);
    };
  }, []);

  const reset = () => {
    source.current?.close();
    if (session.current) {
      fetch(`/api/session/${session.current}`, { method: "DELETE" }).catch(() => {});
    }
    session.current = "";
    setMessages([]);
    setBusy(false);
  };

  return html`
    <div class="app">
      <aside class="sidebar" data-open=${String(sidebar)}>
        <div class="side-top">
          <span class="side-label">Conversation</span>
          <button class="side-close" onClick=${() => setSidebar(false)}
                  aria-label="Hide sidebar"><${Icon.close} /></button>
        </div>
        <button class="side-btn" onClick=${reset}>New conversation</button>

        ${overview ? html`
          <${Fragment}>
            <span class="side-label">Data</span>
            <div class="side-facts">
              <div><span>Crew</span><b>${(overview.counts?.EMPLOYEE_INFO ?? 0).toLocaleString()}</b></div>
              <div><span>Assessments</span><b>${(overview.counts?.MENTOR_FEEDBACK ?? 0).toLocaleString()}</b></div>
              <div><span>Tables</span><b>${overview.tables_loaded ?? 0}</b></div>
              <div><span>Sources</span><b>${overview.sources?.length ?? 0}</b></div>
            </div>
          <//>` : null}

        <div class="side-foot">
          Scores are derived from the records themselves — no declared list of
          what matters is read. Duty hours, flight hours and sectors are out of
          scope and are never estimated.
        </div>
      </aside>

      <main class="main">
        <div class="topbar">
          ${!sidebar ? html`
            <button class="icon-btn" onClick=${() => setSidebar(true)}
                    aria-label="Show sidebar"><${Icon.menu} /></button>` : null}
          <h1>Crew Performance</h1>
          <div class="tabs" role="tablist">
            ${TABS.map(([key, label]) => html`
              <button class="tab" key=${key} role="tab" aria-selected=${tab === key}
                      onClick=${() => {
                        window.location.hash = key === "chat" ? "" : key;
                        setRoute({ tab: key, focus: "" });
                      }}>${label}</button>`)}
          </div>
          ${overview ? html`<span class="engine">${overview.engine}</span>` : null}
        </div>

        ${/* The visualiser is mounted only while it is the open tab. It owns a
              requestAnimationFrame loop and a few hundred SVG nodes, and neither
              should be running behind a conversation. Its payload is cached on
              the server, so coming back to it costs a fetch and a relayout. */
          tab === "graph" ? html`
          <div class="pane"><${GraphVisualiser} focus=${focus} /></div>` : html`
          <div class="pane">
            <div class="scroll">
              <div class="column">
                ${messages.length === 0
                  ? html`<${Welcome} onPick=${ask} />`
                  : messages.map((m, i) => html`<${Message} key=${i} msg=${m} />`)}
                <div ref=${bottom}></div>
              </div>
            </div>
            <${Composer} onSend=${ask} busy=${busy} />
          </div>`}
      </main>
    </div>`;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
