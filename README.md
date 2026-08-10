# Crew Performance

Multi-agent crew performance scoring over a Snowflake-backed knowledge graph.
Five data sources (PEP, CLMS, CrewPortal, ServiceNow, CAC), one scoring
mechanism derived from the records themselves, a chat UI and a CLI over the
same agent pipeline.

This file is the practical "how do I run it" reference. Source comments cite
`PLAN.md` sections (G4, G7, §6.2 …) for the design rationale behind individual
decisions; that document is not in this checkout, so those are pointers to the
design record rather than to a file you can open here.

## Prerequisites

Already satisfied in this checkout:

- `.venv/` — Python 3.11+ virtualenv with everything installed
- `.env` — Azure OpenAI + Cosmos DB credentials, and Snowflake credentials if
  you are reading the production warehouse (copy from `.env.example` if starting
  fresh)
- `data/crew_perf.duckdb` — the synthetic PEP/CLMS/CrewPortal dataset plus the
  loaded ServiceNow and CAC extracts

**Which data you get is `SNOWFLAKE_MODE`.** It defaults to `snowflake` — the
production warehouse — so a local run that wants the synthetic dataset has to say
`SNOWFLAKE_MODE=duckdb`. The DuckDB file is still read in either mode: ServiceNow
is a file extract rather than a warehouse schema. See [Running against production
Snowflake](#running-against-production-snowflake).

If any of that is missing, see [Setting up from scratch](#setting-up-from-scratch)
below.

**Windows.** Every command in this file is written for a POSIX shell
(macOS/Linux, or WSL). On native Windows the venv layout is `.venv\Scripts\`
instead of `.venv/bin/` — everywhere below that reads `.venv/bin/<tool>`,
use `.venv\Scripts\<tool>` in PowerShell or Command Prompt (`copy` in place
of `cp`). Setup step 1 has the full Windows venv-creation/activation commands
since those differ by more than the path separator.

## Run the web UI

macOS/Linux:

```bash
.venv/bin/crewperf serve --port 8000        # needs the [api] extra installed
```

Windows:

```powershell
.venv\Scripts\crewperf serve --port 8000    # needs the [api] extra installed
```

Open **http://127.0.0.1:8000**. Ctrl+C stops it.

Two tabs: **Chat**, which is the question box and the conversation, and **Graph
Visualiser**, which draws the knowledge graph the answers are grounded in. The
open tab is in the URL fragment (`#graph`), so a view is linkable and survives a
reload.

The chat is one question box and a conversation — no mode switch, no second way
to reach a score. It streams which agent is running while the pipeline works,
then answers.

**What an answer shows, and what it hides.** The visible half is plain English:
who, a score out of ten, and the reasons in the words the mentors and the reports
actually used. The technical half — the SQL, the signal identifiers
(`sn_service_response_rate` and friends), the weights, the audit — sits behind
**Why this answer**, **SQL** and **Signals**, and opens on a click. A score
nobody can read is not an answer; a score nobody can check is not trustworthy.
The split is made in `_result_payload`, not in the stylesheet, so it holds for
every consumer of the API.

Follow-ups stay in context — ask "why is that low?" or "how does she compare to
DEL?" and Agent 8 answers from what was already computed, running a fresh query
only when it needs one. **New conversation** clears the thread.

The front end is React with **no build step**: `htm` supplies the JSX-equivalent
syntax and React is vendored under `crew_perf/api/static/vendor/`, so there is no
node, no `package.json` and nothing to compile before a deploy. Vendored rather
than loaded from a CDN so the page works on a network with no route to the public
internet.

## The Graph Visualiser tab

Everything the retrieval agent reasons over, drawn: **5 schemas, 48 tables, 52
concepts and 352 edges** — every vertex and every edge, nothing sampled and
nothing capped. A picture that quietly drops the tail is worse than none, because
the reason to look is to find the table with no edges or the source with no
bridge into it, and that is never the popular node.

Three shapes and one colour rule. Colour is the **source**, because *which system
is this from* is the first question anyone asks; shape is the **kind** — a filled
circle is a schema, a hollow circle a table, a diamond a concept — so the two
questions never compete for the same channel. Red edges cross sources, dashed
ones are unverified joins.

- **Hover** a node to isolate its neighbourhood; everything else recedes. This is
  what the tab is for — "what does this table join to" is unanswerable by eye on
  350 edges and immediate once the rest fades.
- **Click** for the inspector: the grain, the description, and every column
  grouped by role. The role is the point — `measure` is the only role a scoring
  signal can be built from, `temporal_control` is the one that forces a currency
  filter. Joins are listed with the column pair they were verified on and are
  clickable, so the identity bridge can be walked.
- **Search** matches table names, concept labels *and* column names, and marks
  rather than filters: deleting the non-matches would answer "where is
  MENTOR_FEEDBACK" by removing everything that gives the answer meaning.
- **Filter** by source, by node kind, by edge kind, by join confidence, or down
  to verified/cross-source joins only.
- **Labels** are adaptive — schema captions always, the other hundred as you zoom
  past where they stop colliding, plus whatever is hovered or matched. `Labels:
  all` / `none` overrides it.

### One crew member: type an IGA into the search box

Type `IGA60406` (or a `CREW_ID` like `C60406`) and the person joins the graph as
a node, with an edge to every table that **actually holds a row about them**,
labelled with the count. Tables that were checked and hold nothing stay on the
page as dashed outlines. Deep-linkable as `#graph/IGA60406`.

The half that matters is the dashed half. `IGA60406` has 30 rows across 9 of the
17 crew-bearing tables and **nothing at all in ServiceNow or CAC** — which is
precisely why their scorecard carries a *"computed from 59% of the weight"*
audit warning. That warning is a footnote nobody acts on; the same fact drawn as
nine lit tables and eight dashed ones is the reason the number is what it is. A
source with no rows is **missing evidence, not a low score**, and the panel says
so in those words.

Three things this required getting right:

- **CLMS is keyed on `CREW_ID`, everything else on `IGA`.** The translation is a
  lookup through `M_CREW_DETAILS`, never string surgery — the synthetic data
  makes `IGA60406` → `C60406` look derivable and the real warehouse promises no
  such thing. Deriving it returns a clean, confident, empty answer.
- **One `COUNT(*)` per table, never a join.** In Snowflake mode PEP/CLMS/
  CrewPortal are in the warehouse and ServiceNow/CAC are local, and one query
  cannot span both engines. Per-table counting works in both modes, and a
  permission gap on one schema greys one row instead of failing the view —
  *unreadable* renders differently from *zero*, because "nobody could look" and
  "nothing was recorded" are different answers.
- **The route is `async` and offloaded onto the pipeline worker.** As a plain
  `def` it drove the shared DuckDB connection from a second thread while the page
  loaded `/api/overview`, and the failure was not an exception — it returned the
  *other* query's rows, so a crew member with 30 rows reported zero everywhere.
  There is a regression test.

Crew vertices exist only for the length of the request. `VertexLabel.IDENTITY`
and `EdgeLabel.IDENTIFIES` are in the vocabulary and nothing has ever built them
into Cosmos, deliberately: a graph with 800 crew vertices is one nobody can draw
and every agent has to filter past.

**Origin.** `graphs/ (built)` draws the JSON artifacts, which are what
`build-graph` writes and `push_to_cosmos` uploads — the files are the origin and
Cosmos is a load target, so the picture is current whether or not anyone has
pushed. `Cosmos (live)` reads the Gremlin graph instead. The two disagreeing is
not a bug to hide: this checkout draws 105 nodes from disk and 79 from Cosmos,
because ServiceNow and CAC have never been pushed. Being able to see that is the
reason the switch is there.

There is still **no graph library**. The force-directed layout is integrated in
`graph_visualiser/static/visualiser.js` against ~105 nodes, a size where the
naive O(n²) repulsion is free and every quadtree and WebGL renderer in a real
library answers a question this page does not ask. Adding one would have been the
first exception to the no-CDN, no-build rule.

The data route is `/api/graph-view`, deliberately **not** `/api/graph` — that
path belonged to the removed graph explorer, and `tests/test_api.py` still
asserts it 404s. This tab reaches no score at all; reusing the path would have
made that guarantee unassertable.

## Run the CLI

Every CLI command drives the same agents as the UI — there is one implementation
of scoring and one of retrieval, not two paths that can disagree.

macOS/Linux:

```bash
# health check — Azure OpenAI, Cosmos, the data layer, and (in Snowflake mode)
# each schema's visibility one at a time, plus which credential authenticated
.venv/bin/crewperf doctor

# full pipeline: routes to scoring, retrieval or a follow-up automatically
.venv/bin/crewperf ask "How is IGA60406 performing?"
.venv/bin/crewperf ask "Rank the weakest 3 crew at DEL"
.venv/bin/crewperf ask "Which questions fail most often?"

# keep the thread open and ask follow-ups against what was just computed
.venv/bin/crewperf ask "How is IGA60406 performing?" --chat

# one crew member's full scorecard
.venv/bin/crewperf score IGA60024

# see what Agent 7 derived — constructs, weights, rejections, source coverage
.venv/bin/crewperf dynamic --signals

# ad hoc data question, showing the generated SQL
.venv/bin/crewperf retrieve "What is the average mark at BOM?" --trace

# re-verify the 15 business Q&A pairs against the live data
.venv/bin/crewperf business-qa --show

# run the golden retrieval eval suite
.venv/bin/crewperf golden
```

Windows (PowerShell or Command Prompt — same commands, `.venv\Scripts\` instead
of `.venv/bin/`):

```powershell
.venv\Scripts\crewperf doctor

.venv\Scripts\crewperf ask "How is IGA60406 performing?"
.venv\Scripts\crewperf ask "Rank the weakest 3 crew at DEL"
.venv\Scripts\crewperf ask "Which questions fail most often?"

.venv\Scripts\crewperf ask "How is IGA60406 performing?" --chat

.venv\Scripts\crewperf score IGA60024

.venv\Scripts\crewperf dynamic --signals

.venv\Scripts\crewperf retrieve "What is the average mark at BOM?" --trace

.venv\Scripts\crewperf business-qa --show

.venv\Scripts\crewperf golden
```

Run any command with `--help` for its full option list.

## Setting up from scratch

### 1. Environment

macOS/Linux:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[api]"
cp .env.example .env
```

Windows (PowerShell):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1     # optional; lets you drop the .venv\Scripts\ prefix below
                                # if this errors with "running scripts is disabled", run:
                                # Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.venv\Scripts\pip install -e ".[api]"
copy .env.example .env
```

Windows (Command Prompt):

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat     REM optional; lets you drop the .venv\Scripts\ prefix below
.venv\Scripts\pip install -e ".[api]"
copy .env.example .env
```

`py -3.11` needs the Python Launcher for Windows (installed by default with
python.org's installer). If it's not available, `python -m venv .venv` works
as long as `python` on your PATH is already 3.11+.

Edit `.env` with your Azure OpenAI endpoint/key and Cosmos DB credentials.
`SNOWFLAKE_MODE=snowflake` is the default and reads the production warehouse —
see [Running against production
Snowflake](#running-against-production-snowflake) for the credentials and grants
that needs. Set `SNOWFLAKE_MODE=duckdb` to work against the local synthetic
dataset instead, and install the warehouse driver with
`.venv/bin/pip install -e ".[snowflake]"` (`.venv\Scripts\pip install -e ".[snowflake]"`
on Windows).

### 2. Generate the synthetic dataset

```bash
.venv/bin/crewperf synth
```

Windows: `.venv\Scripts\crewperf synth`

Builds PEP + CLMS + CrewPortal synthetic data into `data/crew_perf.duckdb` and
verifies the assessment chain reconciles (marks → deviation matrix → grade).

Reference tables — the assessment form, grades, leave types, issue categories —
are transcribed from the dev warehouse into
[crew_perf/data/synth/reference_data.py](crew_perf/data/synth/reference_data.py)
rather than read from an extract directory. A code mapping is only useful if it
is *right*: `LEAVE_TYPE_ID = 10` means nothing until the master table says it is
LWP, and a missing extract used to leave the generator running happily against
invented mappings. Keeping the values in code makes them reviewable in a diff
and impossible to lose. Only reference data is real; assessments, leave requests
and flights are still generated.

### 3. Ingest ServiceNow crew feedback and CAC appreciation letters

```bash
.venv/bin/crewperf ingest-servicenow
.venv/bin/crewperf ingest-cac
```

Windows:

```powershell
.venv\Scripts\crewperf ingest-servicenow
.venv\Scripts\crewperf ingest-cac
```

`ingest-servicenow` reads `servicenow/servicenow_response.csv`, splits it into
its four grains (flight reports, per-crew involvement, service checks, crew
master), loads them into the same DuckDB, and publishes
`schemas/servicenow_schema.csv` so the rest of the pipeline treats it like any
other source.

`ingest-cac` reads the newest `cac/*.xlsx` spreadsheet export (appreciation
letters), loads three of its eleven columns — crew identifier, flight, comment
— into the same DuckDB, and publishes `schemas/cac_schema.csv`. The columns
naming who raised or approved the letter are not read: personal data is hashed
behind the crew identifier in every other source, and a file extract must not
be the way it comes back.

### 4. Build the knowledge graph

```bash
.venv/bin/crewperf build-graph PEP
.venv/bin/crewperf build-graph CLMS
.venv/bin/crewperf build-graph CrewPortal
.venv/bin/crewperf build-graph ServiceNow
.venv/bin/crewperf build-graph CAC
.venv/bin/crewperf build-bridge      # cross-source identity joins
.venv/bin/crewperf refresh-values    # patch in current enum values/decodes
```

Windows:

```powershell
.venv\Scripts\crewperf build-graph PEP
.venv\Scripts\crewperf build-graph CLMS
.venv\Scripts\crewperf build-graph CrewPortal
.venv\Scripts\crewperf build-graph ServiceNow
.venv\Scripts\crewperf build-graph CAC
.venv\Scripts\crewperf build-bridge
.venv\Scripts\crewperf refresh-values
```

Each `build-graph` call runs Agent 1: relevance gating, join resolution
(asserted → inferred → data-verified), and LLM enrichment into concepts. Takes
tens of seconds to a couple of minutes per source. `--no-llm` skips enrichment
if you just need joins.

### 5. Derive the scoring mechanism

```bash
.venv/bin/crewperf dynamic                   # designs and caches dynamic__v1.json
.venv/bin/crewperf score IGA60024 --refit    # rebuilds it, then scores
```

Windows:

```powershell
.venv\Scripts\crewperf dynamic
.venv\Scripts\crewperf score IGA60024 --refit
```

It writes to `graphs/`; subsequent commands load the cached version unless
`--refit` or a fresh `crewperf dynamic` run is requested.

### 6. Verify

```bash
.venv/bin/pip install -e ".[dev]"      # pytest, ruff — not installed by step 1
.venv/bin/pytest -q                    # 422 tests
.venv/bin/crewperf golden              # retrieval eval
.venv/bin/crewperf business-qa         # Q&A pairs re-verified against live data
```

Windows:

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q
.venv\Scripts\crewperf golden
.venv\Scripts\crewperf business-qa
```

## Architecture at a glance

```
schemas/*.csv          → column-level source definitions (registry.yaml declares scope)
crew_perf/agents/
  kg_builder.py           Agent 1 — schema CSVs → concept graph (joins, concepts)
  weighting.py            WeightSet structure, persistence, question-scoped refocus
  retrieval.py             Agent 3 — graph-grounded NL → validated SQL → rows
  scoring.py               Agent 4 — two-track scorecard (native mark + composite)
  evaluator.py              Agent 5 — deterministic audit (arithmetic, rank stability)
  dynamic.py                  Agent 7 — THE scoring mechanism, derived from data + graph
  conversation.py              Agent 8 — reasoning over computed cards, and follow-ups
  tools.py                      the graph/SQL tool surface Agents 3 and 8 share
  orchestrator.py               routes a question to scoring, retrieval or a follow-up
crew_perf/graph/store.py   in-memory view the agents query
crew_perf/data/
  executor.py               backend factory; DuckDB executor
  snowflake.py              production warehouse — PAT auth, schema qualification
  scope.py                  the allow-list: which tables exist, and on which engine
  routing.py                one executor over two engines (warehouse + local extract)
  validator.py              graph-grounded SQL linting before anything runs
  dialect.py                Snowflake↔DuckDB rewrites, schema qualification
  servicenow.py             the file-extract ingest
  cac.py                    the CAC appreciation-letter ingest
crew_perf/api/
  app.py                    FastAPI; splits each answer into visible vs. detail
  sessions.py               bounded in-memory conversation threads
  static/index.html         page shell, styles, tab bar
  static/app.js             the React chat UI
  static/vendor/            React + htm, vendored (no build step, no CDN)
crew_perf/graph_visualiser/  the Graph Visualiser tab, whole
  payload.py                graph -> drawable nodes/edges; synthesises schema vertices
  cosmos_source.py          the live Gremlin graph, read back into the same shape
  crew_ego.py               one crew member: where they appear, and where they don't
  routes.py                 /api/graph-view (+ /crew/{id}), cached on the artifacts' mtime
  static/visualiser.js      force layout + renderer + inspector (no graph library)
  static/visualiser.css     its styles, on the page shell's palette
graphs/                    built concept graphs, weightsets, scorecards (generated)
eval/                      golden_queries.yaml (retrieval), business_qa.yaml (performance Q&A)
```

**One scoring mechanism.** Agent 7 derives weight purely from measured coverage,
discrimination, reliability, redundancy and attribution — no declared statement
of what matters is read at all. There used to be a second one that started from a
business's written importance and let the data adjust it; it is gone, along with
`policy.CATEGORY_IMPORTANCE` and `policy.ATTRIBUTE_IMPORTANCE`. Two mechanisms
meant every score had to say which produced it, every comparison had to check the
two were on the same scale, and a reader had to hold both in their head to act on
a number.

`weighting.py` keeps what surrounds a mechanism — the `WeightSet` structure,
persistence, and question-scoped `refocus` — because those were never about which
mechanism produced the weights.

## What a question is judged on

A question that names an aspect is ranked on that aspect alone, never on the
standing weighting wearing the question's label. `weighting.ASPECTS` maps the
words a manager actually uses onto the measures that carry them, and the answer
says which systems it read.

**Feedback is the case worth understanding**, because it is the one that spans
everything. "Top performing crew in terms of feedback" is not a PEP question:

| System | What it contributes |
|---|---|
| PEP | `MENTOR_FEEDBACK` — the assessor's mark out of 100, and their written strengths and improvement areas |
| ServiceNow | the `Crew Feedback` category — inflight feedback naming a crew member for a wow moment or for a conduct concern — plus star-performer reports and customer-experience nominations |
| CLMS | `T_SPL_APPRECIATION` — appreciations recorded against them |
| CAC | appreciation letters — crew identifier, flight and comment only |

The mentor's mark is deliberately excluded from the *standing* weighting: the
composite is built from the question answers the mark is computed from, so
scoring it there is circular. It is held in `WeightSet.reportable` at zero weight,
with its evidence measured the same way as everything else, and
`weighting.refocus` promotes it only for a question that asks to be judged on the
assessment. Nothing else can reach it.

The share it gets when promoted is `dynamic.VERDICT_SHARE_WHEN_ASKED_FOR` —
**half** the ranking, split between the two verdict signals by how reliably each
is measured. Capped rather than left to renormalise freely, because feedback is
not one system's word: letting the mark take whatever share the arithmetic gave
it drove it to 91% in testing, which is the recorded mark with rounding errors
attached rather than a ranking that spans the sources.

## Running against production Snowflake

`SNOWFLAKE_MODE=snowflake` is the default. No generated SQL changes between
modes: the agents write Snowflake dialect against bare table names either way.

### Credentials

Set in `.env`: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_DATABASE`,
`SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, and **`SNOWFLAKE_PAT`** — a programmatic
access token, which is the preferred credential here: scoped to a role, expiring
on a date you set, revocable without touching the account password. It is sent
as `authenticator=PROGRAMMATIC_ACCESS_TOKEN` with the token in `token=`, not as a
password; the two are different login flows and a PAT sent as a password fails on
an MFA-enforced account. Needs `snowflake-connector-python>=3.14`.

Key-pair, SSO and password still work. Exactly one method is used —
PAT, then key pair, then `SNOWFLAKE_AUTHENTICATOR`, then password — so a stale
password left in the environment cannot quietly win over the PAT you just
issued. `crewperf doctor` reports which one was used.

### Only three schemas, and only the scoped tables

The production account is much larger than this project, so the warehouse is not
browsed — it is declared, by the column-level exports in `schemas/`:

| export | schema |
| --- | --- |
| `schemas/pep_schema.csv` | `PEP` |
| `schemas/clms_schema.csv` | `CLMS` |
| `schemas/crewportal_schema.csv` | `CREWPORTAL` |

Those three files are the allow-list, and `crew_perf/data/scope.py` enforces it
in every direction. `list_tables()` returns the intersection of what the account
holds and what the exports declared, so attribute discovery never samples a table
nobody scoped. `describe()` is bounded to the declared columns. And a query
naming anything outside the exports is refused before the warehouse sees it —
a second line behind the graph validator, checking the delivered extract rather
than the graph, since a table can pass one and fail the other.

**The role needs USAGE on all three schemas and SELECT on their tables.** One
granted `PEP` alone connects cleanly, answers every assessment question, and
reports every leave and flight-report signal as missing data. `crewperf doctor`
checks the schemas one at a time, counting *scoped* tables and naming the missing
ones, so exactly that gap shows up as a failed row rather than as a quietly
narrower score.

Only override `SNOWFLAKE_SCHEMA_<SOURCE>` if your warehouse renamed a schema on
the way in.

### Schema qualification

A connection defaults to exactly one schema, so a query reaching across sources —
most of the interesting ones — fails on the first name outside it. The connector
resolves each table into its own schema on the way out:

```sql
-- as written by the agent            -- as executed
FROM MENTOR_FEEDBACK                  FROM <db>.PEP.MENTOR_FEEDBACK
JOIN M_CREW_DETAILS                   JOIN <db>.CREWPORTAL.M_CREW_DETAILS
JOIN M_CLMS_CREW                      JOIN <db>.CLMS.M_CLMS_CREW
```

The table → schema map is the `TABLE_SCHEMA` column of those same exports, so it
cannot drift from the warehouse it describes. Qualification runs *after*
validation and only changes how a name resolves, never which table is named.
CTEs and already-qualified names are left alone.

### ServiceNow and CAC are not in the warehouse

ServiceNow and CAC are the two sources that are not a Snowflake schema. Each
arrives as a file export — ServiceNow a CSV, CAC a spreadsheet — and is
ingested into the local DuckDB file by `crewperf ingest-servicenow` /
`crewperf ingest-cac`, so in production the system runs on **two engines at
once**: PEP / CLMS / CrewPortal from Snowflake, ServiceNow and CAC from
`DUCKDB_PATH`. `crew_perf/data/routing.py` sends each query to whichever
engine holds the tables it names; nothing above the executor knows there are
two. The local side is intersected with the scope too — the same DuckDB file
may also hold the full synthetic dataset from `crewperf synth`, and a
synthetic `MENTOR_FEEDBACK` beside the real one is how half an answer gets
invented.

**One query cannot span both engines.** There is no shared catalogue between a
Snowflake session and a local DuckDB file, so a query joining, say,
`SN_FLIGHT_REPORT` to `M_CREW_DETAILS` is refused by name, with the split spelled
out, rather than executed against one engine and returning rows from half the
join condition. Ask the two questions separately and report each population on
its own. This is the practical cost of keeping ServiceNow and CAC local;
loading the extracts into a Snowflake schema of their own would remove it.

## Troubleshooting

**Data layer**

- **`Snowflake mode requires: SNOWFLAKE_ACCOUNT, …`** — those three have no
  sensible default. Set them, or set `SNOWFLAKE_MODE=duckdb` to work locally.
- **`Snowflake mode requires a credential`** — set `SNOWFLAKE_PAT` (or a key
  pair / authenticator / password). `crewperf doctor` reports which one was used,
  so check there first if you think you set one.
- **`Unknown authenticator: PROGRAMMATIC_ACCESS_TOKEN`** — the connector predates
  PAT support. Upgrade: `pip install 'snowflake-connector-python>=3.14'`.
- **`table(s) not in the scoped schemas: …`** — the table is not in any of the
  three exports in `schemas/`, so nothing can query it. Refresh the export if the
  table is genuinely in scope; otherwise this is the bound working.
- **A schema shows `0 of 12 scoped tables visible` in `doctor`** — the role has no
  USAGE on it. This is the failure that answers PEP questions perfectly and calls
  every leave signal missing data, which is why it is reported per schema.
- **`one query cannot span both engines`** — the question needs a ServiceNow or
  CAC table and a warehouse table in one join. Ask the two halves separately;
  see [ServiceNow and CAC are not in the warehouse](#servicenow-and-cac-are-not-in-the-warehouse).
- **`No database at data/crew_perf.duckdb`** — run `crewperf synth` (for the
  synthetic dataset) or `crewperf ingest-servicenow` / `crewperf ingest-cac`
  (for ServiceNow/CAC in Snowflake mode) first.
- **DuckDB lock errors** — DuckDB allows one writer or many readers. Don't run
  `synth`/`ingest-servicenow`/`ingest-cac`/`build-graph` at the same time as
  `serve` or another command against the same file.

**Graph and UI**

- **`No concept graph in graphs/`** — run `crewperf build-graph <source>` for
  each source, then `crewperf build-bridge`. The Graph Visualiser reports the
  same thing on its canvas rather than drawing an empty page.
- **The Graph Visualiser draws fewer nodes on `Cosmos (live)` than on
  `graphs/ (built)`** — working as intended. Cosmos holds what was last pushed
  with `crewperf build-graph --push`; the files are the origin. Push the missing
  sources, or read the built origin.
- **An IGA in the search box finds nothing** — the identifier is not in
  `M_CREW_DETAILS`, the bridge that carries both `IGA` and `CREW_ID`. The panel
  says so and names which sources it could still check. It is not a 500 and not
  an empty picture presented as fact.
- **A crew member shows dashed outlines for a whole source** — they have no rows
  there. That is missing evidence, not a low score, and it is the reason their
  scorecard reports partial coverage.
- **The graph draws as separate islands** — no cross-source joins, so
  `graphs/cross_source_joins.json` is missing. Run `crewperf build-bridge`. The
  build notes in the side panel say so too.
- **Blank page, assets returning 200** — a React error killed the tree. Open the
  browser console; if it is a minified error code, swap the two `vendor/react*`
  script tags in `index.html` for the `.development.js` builds from unpkg to get
  the readable message, then swap back. Production React *throws* on some things
  development only warns about — a string `style="…"` where React wants an object
  is the one that has bitten here.
- **`serve` says the API extra is not installed** — `pip install -e ".[api]"`
  (`.venv\Scripts\pip install -e ".[api]"` on Windows).
- **Port already in use after a backgrounded `serve`** — macOS/Linux: check
  `lsof -ti:8000`. Windows: `netstat -ano | findstr :8000` to find the PID, then
  `taskkill /PID <pid> /F`. Then re-run `crewperf serve --port 8000`.
