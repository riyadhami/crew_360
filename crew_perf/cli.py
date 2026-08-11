"""crewperf — command line entry point."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from crew_perf import config

app = typer.Typer(
    add_completion=False,
    help="Multi-agent crew performance scoring over a Snowflake-backed knowledge graph.",
)
console = Console()


@app.callback()
def _main() -> None:
    """Keeps typer in multi-command mode (it collapses a lone command into root)."""


@app.command()
def doctor() -> None:
    """Check that every external dependency is reachable and configured."""
    results: list[tuple[str, bool, str]] = []

    # ── Config ──
    results.append((
        "config: Azure OpenAI",
        bool(config.LLM_ENDPOINT and config.API_KEY),
        f"endpoint set, model={config.LLM_MODEL}" if config.LLM_ENDPOINT else "LLM_ENDPOINT/API key missing",
    ))
    results.append((
        "config: Cosmos DB",
        bool(config.COSMOS_ENDPOINT and config.COSMOS_KEY),
        f"{config.COSMOS_DATABASE}/{config.COSMOS_GRAPH}" if config.COSMOS_ENDPOINT else "not configured",
    ))

    # ── Guard: never write the donor project's graph ──
    try:
        config.assert_graph_writable()
        results.append(("guard: graph not protected", True, f"target={config.COSMOS_GRAPH}"))
    except RuntimeError as e:
        results.append(("guard: graph not protected", False, str(e)))

    # ── LLM ──
    try:
        from crew_perf.llm import call_llm

        reply = call_llm("Reply with exactly: OK", temperature=0.0)
        ok = "OK" in reply.upper()
        results.append(("llm: chat completion", ok, f"{config.LLM_MODEL} -> {reply.strip()[:40]!r}"))
    except Exception as e:  # noqa: BLE001
        results.append(("llm: chat completion", False, f"{type(e).__name__}: {e}"))

    # ── Embeddings ──
    try:
        from crew_perf.llm import embed_texts

        vecs = embed_texts(["crew performance assessment"])
        ok = len(vecs) == 1 and len(vecs[0]) > 100
        results.append(("llm: embeddings", ok, f"{config.EMBEDDING_MODEL}, dim={len(vecs[0])}"))
    except Exception as e:  # noqa: BLE001
        results.append(("llm: embeddings", False, f"{type(e).__name__}: {e}"))

    # ── Cosmos ──
    try:
        from crew_perf.graph.cosmos import close_cosmos_client, get_cosmos_client, graph_counts

        client = get_cosmos_client()
        counts = graph_counts(client)
        close_cosmos_client(client)
        results.append((
            "cosmos: connectivity",
            True,
            f"{counts['vertices']} vertices, {counts['edges']} edges in {config.COSMOS_GRAPH}",
        ))
    except Exception as e:  # noqa: BLE001
        detail = f"{type(e).__name__}: {e}"
        hint = ""
        if "NotFound" in detail or "Resource" in detail:
            hint = f"  (create the '{config.COSMOS_GRAPH}' graph container in Azure first)"
        results.append(("cosmos: connectivity", False, detail[:160] + hint))

    # ── Data layer ──
    try:
        from crew_perf.data.executor import get_executor

        ex = get_executor()
        tables = ex.list_tables()
        results.append((
            f"data: {config.SNOWFLAKE_MODE}",
            True,
            f"{len(tables)} tables" if tables else "connected, no tables yet (run `crewperf synth`)",
        ))
    except Exception as e:  # noqa: BLE001
        results.append((f"data: {config.SNOWFLAKE_MODE}", False, f"{type(e).__name__}: {e}"))

    # ── Snowflake schemas ──
    # Reported per schema, not as one verdict. The failure this catches is
    # partial: a role granted on PEP and not on CLMS connects cleanly, answers
    # every PEP question, and reports every leave signal as missing data.
    if config.SNOWFLAKE_MODE == "snowflake":
        try:
            from crew_perf.data.executor import get_executor

            health = get_executor().health()
            summary = (f"{health['database']}, session schema "
                       f"{health['session_schema']}, auth via {health['auth']}")
            results.append(("snowflake: database", True, summary))

            for s in health["schemas"]:
                detail = f"{s['tables_found']} of {s['tables_expected']} scoped tables visible"
                if s["missing"]:
                    detail += f" — missing {', '.join(s['missing'])}"
                results.append((f"snowflake: {s['schema']}", s["ok"], detail))

            # ServiceNow is a file extract, not a warehouse schema. Reported
            # beside the schemas because to everything above the executor it is
            # just another source, and "no ServiceNow data" and "no CLMS grant"
            # look identical from a scorecard.
            local = health.get("local_extract")
            if local:
                detail = (
                    f"{local['tables_found']} of {local['tables_expected']} tables in "
                    f"{local['path']}"
                    if local["ingested"]
                    else f"not ingested — run `crewperf ingest-servicenow` ({local['path']})"
                )
                results.append(("local: ServiceNow extract", local["ok"], detail))
        except Exception as e:  # noqa: BLE001
            results.append(("snowflake: schemas", False, f"{type(e).__name__}: {e}"))

    # ── Registry & in-scope schemas ──
    try:
        from crew_perf.data.scope import get_scope
        from crew_perf.sources import load_registry

        registry = load_registry()
        for src in registry.sources.values():
            where = (f"schema={src.warehouse_schema}" if src.is_warehouse
                     else "local extract")
            detail = (f"{len(src)} tables, detail={src.detail}, join={src.join_key}, "
                      f"{where}" if src.available else "schema file missing")
            results.append((f"source: {src.name}", src.available, detail))

        # The allow-list itself, because it is what bounds every warehouse read:
        # a production account holds far more than this, and a table absent here
        # is one no query can reach however well-formed it is.
        scope = get_scope()
        in_scope = (f"{len(scope.warehouse_tables)} warehouse tables in "
                    f"{', '.join(scope.schemas)}; {len(scope.local)} local  (COPS descoped)")
        results.append(("registry: scope", bool(scope.layout), in_scope))
    except Exception as e:  # noqa: BLE001
        results.append(("registry: load", False, f"{type(e).__name__}: {e}"))

    # ── Render ──
    table = Table(title="crewperf doctor", show_lines=False)
    table.add_column("check", style="cyan", no_wrap=True)
    table.add_column("", width=3)
    table.add_column("detail", style="dim")
    for name, ok, detail in results:
        table.add_row(name, "[green]OK[/]" if ok else "[red]XX[/]", detail)
    console.print(table)

    hard_failures = [n for n, ok, _ in results if not ok]
    if hard_failures:
        console.print(f"\n[red]{len(hard_failures)} check(s) failed:[/] {', '.join(hard_failures)}")
        raise typer.Exit(1)
    console.print("\n[green]All checks passed.[/]")


@app.command()
def synth(
    crew: int = typer.Option(None, help="Number of crew members (default: SYNTH_CREW_COUNT)"),
    months: int = typer.Option(None, help="Months of history (default: SYNTH_MONTHS)"),
    seed: int = typer.Option(None, help="RNG seed (default: SYNTH_SEED)"),
    clean: bool = typer.Option(False, "--clean", help="Generate without injected data-quality mess"),
) -> None:
    """Generate the synthetic PEP dataset, load DuckDB, and verify the chain."""
    from crew_perf.data.synth import fleet
    from crew_perf.data.synth.generate import GenConfig
    from crew_perf.data.synth.load import build

    cfg = GenConfig(
        seed=seed if seed is not None else config.SYNTH_SEED,
        # The window ends where the extracts do. They are real files and cannot
        # be moved, so a fixed date lets the generated history and the reported
        # flights drift past each other — see `fleet.anchor_end_date`.
        end_date=fleet.anchor_end_date(GenConfig.end_date),
        n_crew=crew if crew is not None else config.SYNTH_CREW_COUNT,
        months=months if months is not None else config.SYNTH_MONTHS,
        inject_mess=not clean,
    )
    console.print(f"[cyan]Generating[/] {cfg.n_crew} crew over {cfg.months} months (seed={cfg.seed})…")
    summary = build(cfg)

    # Where the fleet came from. Printed before the row counts because it is the
    # thing that decides whether ServiceNow and CAC can be attributed at all.
    prov = summary.get("fleet") or {}
    if prov.get("source") == "extracts":
        full = prov["all_five_sources"]
        console.print(
            f"[bold]{prov['generated']:,}[/] crew drawn from the extracts — "
            f"[bold]{full:,}[/] carry all five sources"
            f" ({full / prov['generated']:.0%} of the fleet)"
        )
        console.print(
            f"  [dim]pool {prov['pool']:,}: {prov['in_both']:,} in both extracts, "
            f"{prov['servicenow_only']:,} ServiceNow only, {prov['cac_only']:,} CAC only; "
            f"{prov['rejected_not_five_digit']:,} identifiers rejected as malformed[/]"
        )
    elif prov:
        console.print(
            f"[yellow]No extracts found[/] — generated {prov['generated']:,} crew on the "
            f"fallback identifier block. ServiceNow and CAC will not join to this fleet."
        )

    t = Table(title="tables loaded", show_lines=False)
    t.add_column("table", style="cyan")
    t.add_column("rows", justify="right")
    for name, n in sorted(summary["tables"].items(), key=lambda kv: -kv[1]):
        t.add_row(name, f"{n:,}")
    console.print(t)

    rec = summary["reconciliation"]
    d = summary["distribution"]["stats"]
    console.print("\n[bold]Reconciliation[/] (recomputed from question answers -> deviation matrix)")
    console.print(f"  assessments      : {rec['assessments']:,} "
                  f"({rec['uncastable']} uncastable MARK, {rec['orphan_affected']} orphan-affected "
                  f"— both excluded by design)")
    console.print(f"  reconciled on    : {rec['intact']:,} intact assessments")
    console.print(f"  mark match       : {rec['mark_match_pct']:.2f}%  "
                  f"({rec['mark_mismatches']} mismatches)")
    console.print(f"  grade match      : {rec['grade_match_pct']:.2f}%  "
                  f"({rec['grade_mismatches']} mismatches)")

    console.print("\n[bold]Mark distribution[/] (target: clustered 92-100, G8)")
    console.print(f"  mean {d['mean_mark']}  sd {d['sd_mark']}  "
                  f"range {d['min_mark']}-{d['max_mark']}  p05 {d['p05']}  p95 {d['p95']}")
    g = Table(show_header=True)
    g.add_column("grade"); g.add_column("n", justify="right")
    g.add_column("share", justify="right"); g.add_column("mark range")
    total = sum(r["n"] for r in summary["distribution"]["by_grade"])
    for r in summary["distribution"]["by_grade"]:
        g.add_row(r["GRADE"], f"{r['n']:,}", f"{100 * r['n'] / total:.1f}%",
                  f"{r['min_mark']:.1f}-{r['max_mark']:.1f}")
    console.print(g)

    disc = summary["discrimination"]
    non_disc = [q for q in disc if q["p_pass"] >= 0.95]
    console.print(f"\n[bold]Discrimination[/] — {len(non_disc)}/{len(disc)} questions pass at >=95% "
                  f"(these carry ~no information; the mechanism must exclude them)")
    for q in non_disc[:5]:
        console.print(f"  p_pass {q['p_pass']:.3f}  marks {q['MARKS']:.0f}  {q['QUESTION'][:62]}")

    console.print(f"\n[dim]ground truth -> {config.LATENT_TRAITS_PATH}[/]")
    if rec["grade_match_pct"] < 95.0:
        console.print("[red]Grade reconciliation below 95% — the assessment chain is broken.[/]")
        raise typer.Exit(1)
    console.print("[green]Chain verified.[/]")


@app.command("ingest-servicenow")
def ingest_servicenow(
    path: str = typer.Option(None, help="CSV extract (default: servicenow/servicenow_response.csv)"),
) -> None:
    """Load the ServiceNow crew-feedback extract and publish its schema.

    Splits the wide export into the four grains it contains, writes them into the
    working database, and emits `schemas/servicenow_schema.csv` so the registry,
    the KG builder and the SQL validator treat it like any other source.
    """
    from pathlib import Path

    from crew_perf.data import servicenow

    src = Path(path) if path else servicenow.CSV_PATH
    if not src.exists():
        console.print(f"[red]No extract at {src}[/]")
        raise typer.Exit(1)

    console.print(f"[cyan]Ingesting[/] {src.name}…")
    result = servicenow.load(src)

    t = Table(title="ServiceNow extract", show_lines=False)
    t.add_column("table", style="cyan")
    t.add_column("rows", justify="right")
    t.add_column("grain", style="dim")
    for name, n in result.tables.items():
        t.add_row(name, f"{n:,}", servicenow.TABLES_BY_NAME[name].grain)
    console.print(t)

    span = f"{result.date_range[0]} to {result.date_range[1]}" if result.date_range else "—"
    console.print(f"\n[bold]{result.rows_read:,}[/] rows read · "
                  f"[bold]{result.crew:,}[/] distinct crew "
                  f"({result.crew_cabin:,} seen in a cabin seat) · flights {span}")
    console.print("  polarity: " + ", ".join(
        f"{k} {v:,}" for k, v in sorted(result.polarity.items(), key=lambda kv: -kv[1])))
    filled = ", ".join(f"{k} {v:,}" for k, v in sorted(result.check_fill.items(),
                                                       key=lambda kv: -kv[1]))
    console.print(f"  service checks answered: {filled or 'none'}")

    for f in result.findings:
        console.print(f"\n[yellow]![/] {f}")
    console.print(f"\n[dim]schema -> {config.SCHEMAS_DIR / servicenow.SCHEMA_FILE}[/]")
    console.print("[dim]next: crewperf build-graph ServiceNow && crewperf build-bridge[/]")


@app.command("ingest-cac")
def ingest_cac(
    path: str = typer.Option(None, help="Spreadsheet export (default: newest cac/*.xlsx)"),
) -> None:
    """Load the CAC appreciation-letter extract and publish its schema.

    Three of its eleven columns are loaded — the crew identifier, the flight and
    the comment. The export also names the person appreciated, who raised it and
    who approved it; those are not read, because personal data is hashed behind
    the identifier in every other source and a file extract must not be the way
    it comes back.
    """
    from pathlib import Path

    from crew_perf.data import cac

    src = Path(path) if path else cac.default_path()
    if src is None or not src.exists():
        console.print(f"[red]No CAC export in {cac.EXTRACT_DIR}[/]")
        raise typer.Exit(1)

    console.print(f"[cyan]Ingesting[/] {src.name}…")
    result = cac.load(src)

    t = Table(title="CAC extract", show_lines=False)
    t.add_column("table", style="cyan")
    t.add_column("rows", justify="right")
    t.add_column("grain", style="dim")
    for name, n in result.tables.items():
        t.add_row(name, f"{n:,}", cac.TABLES_BY_NAME[name].grain)
    console.print(t)

    console.print(f"\n[bold]{result.rows_read:,}[/] rows read · "
                  f"[bold]{result.crew:,}[/] distinct crew appreciated")
    console.print(f"  columns not loaded: {', '.join(map(str, result.dropped_columns)) or 'none'}")

    for f in result.findings:
        console.print(f"\n[yellow]![/] {f}")
    console.print(f"\n[dim]schema -> {config.SCHEMAS_DIR / cac.SCHEMA_FILE}[/]")
    console.print("[dim]next: crewperf build-graph CAC && crewperf build-bridge[/]")


@app.command("build-graph")
def build_graph(
    source: str = typer.Argument("PEP", help="Source name from schemas/registry.yaml"),
    push: bool = typer.Option(False, "--push", help="Upsert the result into Cosmos"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip enrichment/concepts (joins only)"),
) -> None:
    """Agent 1 — build the concept graph for a source."""
    from crew_perf.agents.kg_builder import build, push_to_cosmos, save_graph
    from crew_perf.data.executor import get_executor
    from crew_perf.sources import load_registry

    registry = load_registry()
    if source not in registry.sources:
        console.print(f"[red]Unknown source {source!r}.[/] "
                      f"Known: {', '.join(registry.sources)}")
        raise typer.Exit(1)
    src = registry[source]
    if not src.available:
        console.print(f"[red]{source} schema is not available[/] "
                      f"— see PLAN.md O4a if this is COPS.")
        raise typer.Exit(1)

    try:
        executor = get_executor()
    except Exception:  # noqa: BLE001 - join verification is optional
        executor = None
        console.print("[yellow]No data layer — joins will not be verified against data.[/]")

    console.print(f"[cyan]Building[/] {source}: {len(src)} tables, detail={src.detail}"
                  f"{' (LLM disabled)' if no_llm else ''}…")

    def progress(stage: str, detail: str) -> None:
        console.print(f"  [dim]{stage:18s}[/] {detail}")

    result = build(src, executor=executor, use_llm=not no_llm, progress=progress)
    json_path, md_path = save_graph(result)
    s = result.to_graph()["stats"]

    t = Table(title=f"{source} concept graph")
    t.add_column("", style="cyan"); t.add_column("", justify="right")
    t.add_row("tables", str(s["tables"]))
    t.add_row("concepts", str(s["concepts"]))
    t.add_row("edges", str(s["edges"]))
    t.add_row("data-verified joins", str(s["verified_joins"]))
    t.add_row("excluded (relevance gate)", str(s["excluded"]))
    console.print(t)

    if result.concepts:
        console.print("\n[bold]Concepts[/]")
        for c in result.concepts:
            console.print(f"  [cyan]{c['display_label']:34s}[/] "
                          f"{len(c['source_tables'])} tables  [dim]{c['description'][:70]}[/]")

    for w in result.warnings:
        console.print(f"[yellow]warning:[/] {w}")
    console.print(f"\n[dim]{json_path}\n{md_path}[/]")

    if push:
        console.print(f"\n[cyan]Pushing[/] to {config.COSMOS_DATABASE}/{config.COSMOS_GRAPH}…")
        counts = push_to_cosmos(result)
        console.print(f"[green]Pushed[/] {counts['vertices']} vertices, {counts['edges']} edges.")


@app.command()
def retrieve(
    question: str = typer.Argument(..., help="Natural-language question about the data"),
    trace: bool = typer.Option(False, "--trace", help="Show every tool call"),
    show_sql: bool = typer.Option(True, "--sql/--no-sql", help="Show the generated SQL"),
) -> None:
    """Agent 3 — explore the graph, generate validated SQL, return rows."""
    from crew_perf.agents.retrieval import RetrievalAgent

    agent = RetrievalAgent()
    console.print(f"[cyan]Q:[/] {question}\n")

    result = None
    for event in agent.stream(question, verbose=trace):
        if event["type"] == "tool":
            payload = event["result"]
            note = ""
            if isinstance(payload, dict):
                if payload.get("rejected"):
                    note = f"[red]rejected[/] {'; '.join(payload.get('errors', []))[:90]}"
                elif payload.get("ok"):
                    note = f"[green]{payload.get('row_count', 0)} rows[/]"
                elif payload.get("error"):
                    note = f"[yellow]{str(payload['error'])[:70]}[/]"
            console.print(f"  [dim]{event['name']:16s}[/] "
                          f"{json.dumps(event['args'], default=str)[:70]:70s} {note}")
        else:
            result = event["result"]

    if result is None:
        console.print("[red]No result.[/]")
        raise typer.Exit(1)

    if show_sql and result.sql:
        console.print("\n[bold]SQL[/]")
        for line in result.sql.strip().splitlines():
            console.print(f"  [dim]{line}[/]")

    if result.rows:
        t = Table(title=f"{len(result.rows)} row(s)")
        for col in result.columns:
            t.add_column(str(col), overflow="fold")
        for row in result.rows[:25]:
            t.add_row(*[("" if v is None else str(v))[:40] for v in row])
        console.print()
        console.print(t)
        if len(result.rows) > 25:
            console.print(f"[dim]… {len(result.rows) - 25} more[/]")
    else:
        console.print("\n[yellow]No rows returned.[/]")

    if result.validation_failures:
        console.print(f"[yellow]{len(result.validation_failures)} query rejection(s) "
                      f"before success:[/]")
        for f in result.validation_failures:
            console.print(f"  [dim]{f[:130]}[/]")
    if result.answer:
        console.print(f"\n{result.answer}")


@app.command()
def golden(
    only: str = typer.Option(None, help="Run a single case by id"),
    show_sql: bool = typer.Option(False, "--sql", help="Print the SQL for failures"),
) -> None:
    """Run the golden retrieval prompts (Phase 4 acceptance)."""
    import sys

    sys.path.insert(0, str(config.EVAL_DIR))
    from golden_runner import run_all

    console.print("[cyan]Running golden retrieval prompts…[/]\n")
    results = run_all(only)

    t = Table(title="golden retrieval")
    t.add_column("", width=4)
    t.add_column("case", style="cyan")
    t.add_column("rows", justify="right")
    t.add_column("retries", justify="right")
    t.add_column("notes", style="dim", overflow="fold")
    for r in results:
        t.add_row(
            "[green]PASS[/]" if r.passed else "[red]FAIL[/]",
            r.id, str(r.row_count), str(r.rejections),
            "; ".join(r.failures)[:90] if r.failures else "",
        )
    console.print(t)

    if show_sql:
        for r in results:
            if not r.passed and r.sql:
                console.print(f"\n[yellow]{r.id}[/]")
                for line in r.sql.strip().splitlines():
                    console.print(f"  [dim]{line}[/]")

    passed = sum(r.passed for r in results)
    retries = sum(r.rejections for r in results)
    console.print(f"\n[bold]{passed}/{len(results)} passed[/]  "
                  f"({retries} validator rejections absorbed across all cases)")
    if passed < len(results):
        raise typer.Exit(1)


@app.command()
def score(
    iga: str = typer.Argument(..., help="Crew identifier, e.g. IGA60406"),
    refit: bool = typer.Option(False, "--refit", help="Rebuild the mechanism before scoring"),
    explain: bool = typer.Option(
        True, "--explain/--no-explain", help="Write the reasoning behind the score"),
) -> None:
    """Agent 4 — scorecard for one crew member, and why it reads that way."""
    from crew_perf.agents import conversation, evaluator, scoring
    from crew_perf.data.executor import get_executor
    from crew_perf.graph.store import get_store

    store, ex = get_store(), get_executor()
    ws = _weightset(store, ex, refit=refit)

    card = scoring.score(iga, ws, store=store, executor=ex)
    if not card.native.get("assessments"):
        console.print(f"[yellow]No submitted assessments for {iga}.[/]")
        for f in card.findings:
            console.print(f"  [dim]{f}[/]")
        raise typer.Exit(1)

    console.print(f"\n[bold]{card.iga}[/]  [dim]{card.base} · "
                  f"{card.designation}[/]")
    console.print(f"[bold cyan]Composite {card.composite_score}/10[/]   "
                  f"native mark {card.native.get('mean_mark'):.1f} "
                  f"(grade {card.native.get('modal_grade')})   "
                  f"coverage {card.coverage:.0%}   confidence [bold]{card.confidence}[/]")

    t = Table(title="components", show_lines=False)
    t.add_column("attribute", style="cyan")
    t.add_column("raw", justify="right")
    t.add_column("z", justify="right")
    t.add_column("pctile", justify="right")
    t.add_column("weight", justify="right")
    t.add_column("contribution", justify="right")
    for c in card.components:
        t.add_row(c.attribute, f"{c.raw:.3f}", f"{c.normalized:+.2f}",
                  f"{c.percentile:.0f}", f"{c.weight:.3f}", f"{c.contribution:+.3f}")
    console.print(t)

    if card.positives:
        console.print("\n[green]Positives[/]")
        for p in card.positives[:5]:
            console.print(f"  + {p}")
    if card.negatives:
        console.print("\n[red]Negatives[/]")
        for n in card.negatives[:5]:
            console.print(f"  - {n}")
    if card.findings:
        console.print("\n[bold]Findings[/]")
        for f in card.findings:
            style = "yellow" if "DIVERGENCE" in f or "NOT offset" in f else "dim"
            console.print(f"  [{style}]* {f}[/]")
    if card.unavailable:
        console.print(f"\n[dim]Unavailable ({len(card.unavailable)}): "
                      f"{'; '.join(card.unavailable[:4])}[/]")

    # Reasoning over the audited card, not the raw one: an audit can downgrade
    # confidence, and prose explaining the pre-audit card would describe a
    # scorecard that is not the one printed above.
    if explain:
        audit = evaluator.audit_score(card, ws, store=store)
        if audit.confidence_downgrade:
            card.confidence = audit.confidence_downgrade
        card.narrative = conversation.explain(
            f"Score {iga}.", [card], [audit])
        if card.narrative:
            console.print(f"\n{card.narrative}")

    console.print(f"\n[dim]weightset {card.weightset_version} · "
                  f"{scoring.save(card)}[/]")


def _weightset(store, executor, refit: bool = False):
    """The weightset a scoring command should use, built or loaded.

    One mechanism: Agent 7 derives weights from the records and the graph, reading
    no declared statement of what matters. Built on first use and cached on disk,
    because designing it scans the whole population and a scorecard should not
    pay for that every time.
    """
    from crew_perf.agents import dynamic as dynamic_agent
    from crew_perf.agents import weighting

    path = config.GRAPHS_DIR / "dynamic__v1.json"
    if refit or not path.exists():
        console.print("[cyan]Designing the scoring mechanism from the data…[/]")
        ws, mech = dynamic_agent.design(store=store, executor=executor)
        dynamic_agent.save(mech, ws)
        return ws
    return weighting.load(path)


@app.command()
def dynamic(
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip the semantic pass over the graph"),
    show_signals: bool = typer.Option(False, "--signals", help="List every signal, not just constructs"),
) -> None:
    """Agent 7 — design a scoring mechanism from the data and the graph alone.

    Reads no declared statement of importance at all. Weight comes from
    measured coverage, discrimination, reliability, redundancy and attribution;
    the LLM only says what each number means and which way is good.
    """
    from crew_perf.agents import dynamic as agent
    from crew_perf.agents.attributes import build_frame, discover
    from crew_perf.data.executor import get_executor
    from crew_perf.graph.store import get_store

    store, ex = get_store(), get_executor()

    def progress(stage: str, detail: str) -> None:
        console.print(f"  [dim]{stage:12s}[/] {detail}")

    ws, mech = agent.design(store=store, executor=ex, use_llm=not no_llm, progress=progress)
    if not ws.attributes:
        console.print("[red]No signal passed admission — no mechanism could be designed.[/]")
        for f in mech.findings:
            console.print(f"  [dim]{f}[/]")
        raise typer.Exit(1)

    c = Table(title=f"constructs discovered ({len(mech.constructs)} independent)")
    c.add_column("construct", style="cyan")
    c.add_column("weight", justify="right")
    c.add_column("signals", style="dim", overflow="fold")
    for con in sorted(mech.constructs, key=lambda k: -k["weight"]):
        c.add_row(con["label"], f"{con['weight']:.1%}", ", ".join(con["signals"]))
    console.print(); console.print(c)

    if show_signals:
        t = Table(title="signals")
        t.add_column("signal", style="cyan"); t.add_column("weight", justify="right")
        t.add_column("dir"); t.add_column("attribution"); t.add_column("cov", justify="right")
        t.add_column("n", justify="right"); t.add_column("rho vs mark", justify="right")
        by_name = {s.name: s for s in mech.signals}
        for a in ws.attributes:
            s = by_name[a.attribute]
            t.add_row(a.attribute, f"{a.weight:.1%}",
                      "up" if a.direction == "higher_is_better" else "down",
                      s.attribution, f"{a.coverage:.0%}", str(a.n),
                      "—" if s.rho_native is None else f"{s.rho_native:+.2f}")
        console.print(); console.print(t)

    frame = build_frame(ex, discover(store, ex))
    ev = agent.evaluate(ws, frame)
    console.print(f"\n[bold]Self-evaluation[/] — {ev['crew_scored']} crew scored, "
                  f"{ev['distinct_scores']} distinct scores")
    if ev.get("stability"):
        console.print(f"  rank stability: top-{ev['stability'].get('top_n')} membership "
                      f"survives weight perturbation "
                      f"{ev['stability'].get('agreement', 0):.0%} of the time")
    if ev.get("rho_native") is not None:
        console.print(f"  agreement with the recorded assessment mark: rho "
                      f"{ev['rho_native']:+.3f} [dim](reported, never fitted to — a "
                      f"mechanism that reproduced the mark would add nothing)[/]")

    audit = mech.coverage_audit
    a = Table(title="data-point coverage")
    a.add_column("source", style="cyan"); a.add_column("tables", justify="right")
    a.add_column("contributing a signal", justify="right")
    for source, stats in sorted(audit.get("by_source", {}).items()):
        a.add_row(source, str(stats["tables"]), str(stats["used"]))
    console.print(); console.print(a)

    if mech.rejected:
        console.print(f"\n[bold]Rejected[/] ({len(mech.rejected)})")
        for r in mech.rejected:
            console.print(f"  [dim]{r['signal']:26s}[/] {r['reason'][:88]}")
    for f in mech.findings:
        console.print(f"\n[yellow]![/] {f}")

    mech_path, ws_path = agent.save(mech, ws)
    console.print(f"\n[dim]{mech_path}\n{ws_path}[/]")
    console.print("[dim]use it: crewperf score IGA60024[/]")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Natural-language question"),
    top: int = typer.Option(5, help="How many crew for a ranking question"),
    chat: bool = typer.Option(
        False, "--chat", help="Keep the thread open for follow-up questions"),
) -> None:
    """Full pipeline — routes to scoring, retrieval or a follow-up, and audits it."""
    from crew_perf.agents.conversation import Conversation
    from crew_perf.agents.orchestrator import stream

    # Only in --chat mode: a one-shot `ask` that carried a thread would keep a
    # conversation nothing can ever add a second turn to, and the routing would
    # start guessing "follow-up" against a history of exactly one question.
    conversation = Conversation(id="cli") if chat else None

    while True:
        console.print(f"[cyan]Q:[/] {question}\n")
        result = None
        for ev in stream(question, top_n=top, conversation=conversation):
            if ev.get("stage") == "done":
                result = ev["result"]
            else:
                console.print(f"  [dim]{ev['stage']:10s}[/] {ev['detail']}")

        if result is None:
            console.print("[red]No result.[/]")
            raise typer.Exit(1)

        _render_answer(result)

        if not chat:
            return
        console.print()
        try:
            question = typer.prompt("follow up (blank to quit)", default="", show_default=False)
        except (EOFError, KeyboardInterrupt):
            return
        if not question.strip():
            return
        console.print()


def _render_answer(result) -> None:
    """Print whichever shape of answer the pipeline produced."""
    if result.route.kind == "followup":
        f = result.followup
        if f and f.rows:
            _print_rows(f.columns, f.rows)
        console.print(f"\n{result.narrative}")
        return

    if result.route.kind == "retrieval":
        r = result.retrieval
        if r and r.unavailable:
            console.print("\n[yellow]Not answerable from the onboarded schema.[/]")
            console.print(f"  missing: {r.unavailable['missing']}")
            console.print(f"  reason : {r.unavailable['reason']}")
            return
        if r and r.rows:
            _print_rows(r.columns, r.rows)
        if r and r.answer:
            console.print(f"\n{r.answer}")
        return

    for note in result.notes:
        console.print(f"[dim]{note}[/]")
    for card, audit in zip(result.scorecards, result.audits):
        console.print(f"\n[bold]{card.iga}[/] [dim]{card.base} · "
                      f"{card.designation}[/]")
        console.print(f"  [bold cyan]{card.composite_score}/10[/]  "
                      f"native {card.native.get('mean_mark', 0):.1f} "
                      f"({card.native.get('modal_grade')})  "
                      f"coverage {card.coverage:.0%}  confidence [bold]{card.confidence}[/]")
        for p in card.positives[:2]:
            console.print(f"  [green]+[/] {p[:96]}")
        for n in card.negatives[:3]:
            console.print(f"  [red]-[/] {n[:96]}")
        for f in audit.findings:
            if f.severity != "info":
                console.print(f"  [yellow]audit {f.severity}:[/] {f.message[:92]}")
        if not audit.passed:
            console.print("  [red]AUDIT FAILED — score not trustworthy[/]")

    # Last, and once for the whole answer: the reasoning reads as a conclusion
    # about the cards above it, which is what it is.
    if result.narrative:
        console.print(f"\n{result.narrative}")


def _print_rows(columns, rows) -> None:
    t = Table(title=f"{len(rows)} row(s)")
    for c in columns:
        t.add_column(str(c), overflow="fold")
    for row in rows[:20]:
        t.add_row(*[("" if v is None else str(v))[:38] for v in row])
    console.print()
    console.print(t)


@app.command("business-qa")
def business_qa(
    difficulty: str = typer.Option(None, help="easy | medium | hard"),
    only: str = typer.Option(None, help="Run a single pair by id"),
    show: bool = typer.Option(False, "--show", help="Print the question and answer text"),
) -> None:
    """Re-verify the business Q&A pairs against the live dataset.

    Every answer in `eval/business_qa.yaml` was computed from the data and
    carries the computation that produced it. This reruns them: a pair that no
    longer reproduces is a stale number that reads exactly like a fresh one.
    """
    import sys

    sys.path.insert(0, str(config.EVAL_DIR))
    from business_qa_runner import load_pairs, run_all

    console.print("[cyan]Verifying business Q&A pairs…[/]\n")
    results = run_all(difficulty=difficulty, only=only)
    if not results:
        console.print("[yellow]No pairs matched.[/]")
        raise typer.Exit(1)

    t = Table(title="business Q&A")
    t.add_column("", width=4)
    t.add_column("id", style="cyan")
    t.add_column("level")
    t.add_column("checks", justify="right")
    t.add_column("detail", style="dim", overflow="fold")
    for r in results:
        mark = ("[yellow]SKIP[/]" if r.skipped
                else "[green]PASS[/]" if r.passed else "[red]FAIL[/]")
        t.add_row(mark, r.id, r.difficulty, str(len(r.checked)),
                  r.skipped or "; ".join(r.failures)[:80])
    console.print(t)

    if show:
        pairs = {p["id"]: p for p in load_pairs()}
        for r in results:
            p = pairs[r.id]
            console.print(f"\n[bold cyan]{r.difficulty.upper()}[/] {r.id}")
            console.print(f"  [bold]Q:[/] {p['question']}")
            console.print(f"  [bold]A:[/] {' '.join(p['answer'].split())}")
            console.print(f"  [dim]sources: {', '.join(p.get('sources', []))}[/]")

    passed = sum(r.passed for r in results)
    skipped = sum(bool(r.skipped) for r in results)
    console.print(f"\n[bold]{passed}/{len(results) - skipped} verified[/]"
                  + (f", {skipped} skipped" if skipped else ""))
    if passed < len(results) - skipped:
        raise typer.Exit(1)


@app.command()
def benchmark() -> None:
    """Show the ground-truth reference points an estimator is judged against."""
    import sys

    sys.path.insert(0, str(config.EVAL_DIR))
    from harness import benchmark as run_benchmark

    b = run_benchmark()
    t = Table(title=f"recovery benchmark ({b['n_crew']} crew)")
    t.add_column("reference", style="cyan")
    t.add_column("Spearman rho", justify="right")
    t.add_column("meaning", style="dim")
    t.add_row("native", f"{b['native']:.3f}", "mean MENTOR_FEEDBACK.MARK — what production reports")
    t.add_row("equal-weight", f"{b['equal_weight']:.3f}", "categories averaged, no weighting intelligence")
    t.add_row("oracle", f"{b['oracle']:.3f}", "true importance weights — the best achievable here")
    console.print(t)
    console.print(f"\nHeadroom for the mechanism: [bold]{b['headroom']:+.3f}[/] Spearman.")
    console.print("[dim]A mechanism is scored on `recovery` = fraction of that gap closed, "
                  "not on absolute rho (which is capped by data volume).[/]")


@app.command("refresh-values")
def refresh_values() -> None:
    """Re-read enum values and code decodes from data into the built graphs.

    Values drift with the data, descriptions do not, so this patches the graph
    JSONs in place without re-running enrichment — no LLM calls, safe to repeat.
    """
    from crew_perf.agents.kg_builder import capture_enum_values, capture_lookup_decodes
    from crew_perf.data.executor import get_executor
    from crew_perf.sources import load_registry

    ex = get_executor()
    registry = load_registry()
    touched = 0
    for src in registry.available():
        path = config.GRAPHS_DIR / f"{src.name}_concept_graph.json"
        if not path.exists():
            console.print(f"[dim]{src.name}: no graph built, skipped[/]")
            continue
        graph = json.loads(path.read_text())
        enums = capture_enum_values(src, ex)
        decodes = capture_lookup_decodes(src, ex)
        n_dec = 0
        for node in graph.get("nodes", []):
            name = node["name"]
            if name in enums:
                node["enum_values"] = enums[name]
            if name in decodes:
                node["decodes"] = decodes[name]
                n_dec += len(decodes[name])
        path.write_text(json.dumps(graph, indent=2))
        touched += 1
        console.print(f"  {src.name}: {len(enums)} tables with values, "
                      f"[green]{n_dec}[/] code decode(s)")
    console.print(f"\n[green]{touched}[/] graph(s) refreshed.")


@app.command("build-bridge")
def build_bridge() -> None:
    """Build and verify the identity joins that span sources.

    Per-source graphs cannot contain these, so without this step the merged
    graph is a set of islands and no question can reach across them.
    """
    import json
    from datetime import datetime, timezone

    from crew_perf.agents.joins import bridge_edges, verify_edges
    from crew_perf.data.executor import get_executor
    from crew_perf.graph.schema import EdgeLabel
    from crew_perf.graph.store import CROSS_SOURCE_FILE
    from crew_perf.sources import load_registry

    registry = load_registry()
    edges = bridge_edges(registry)
    if not edges:
        console.print("[yellow]No bridge declared in schemas/registry.yaml[/] "
                      "(identity.bridge) — nothing to build.")
        raise typer.Exit(1)

    ex = get_executor()
    available = set(ex.list_tables())
    scd2 = {t for t in available}          # every delivered table is SCD-2
    verified = verify_edges(edges, ex, available, scd2_tables=scd2)

    warnings = []
    for e in verified:
        mark = "[green]verified[/]" if e.verified else "[red]NOT verified[/]"
        cov = f"{e.coverage:.1%}" if e.coverage is not None else "—"
        console.print(f"  {e.source_table}.{e.source_column} = "
                      f"{e.target_table}.{e.target_column}  {mark}  coverage {cov}")
        if not e.verified:
            # Three different failures wear the same "NOT verified" label, and
            # conflating them is how a usable source gets written off. A bridge
            # that resolves nothing is broken; one that resolves a subset is a
            # population gap, and the honest report of it is a coverage number,
            # not a warning that the answers are wrong. A bridge whose two ends
            # sit in different engines is neither — it is a deployment fact, and
            # no amount of re-running will verify it.
            edge_desc = (f"bridge {e.source_table}.{e.source_column} -> "
                         f"{e.target_table}.{e.target_column}")
            if _spans_engines(e):
                warnings.append(
                    f"{edge_desc} spans two engines — {e.source_table} and "
                    f"{e.target_table} are not in the same database, so the join "
                    f"cannot be probed or executed. Query each side separately"
                )
            elif e.confidence >= 0.8 and (e.coverage or 0) > 0.01:
                warnings.append(
                    f"{edge_desc} resolves for only {cov} of rows — the join is sound, "
                    f"the populations differ, and answers through it cover that share only"
                )
            else:
                warnings.append(
                    f"{edge_desc} did not verify against data (coverage "
                    f"{cov}) — cross-source answers through it would be wrong"
                )

    usable = [e for e in verified if e.confidence >= 0.8]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [s.name for s in registry.available()],
        "warnings": warnings,
        "edges": [
            {"label": EdgeLabel.JOINS_TO,
             "from": f"{_source_of(registry, e.source_table)}__table__{e.source_table}",
             "to": f"{_source_of(registry, e.target_table)}__table__{e.target_table}",
             "source_column": e.source_column, "target_column": e.target_column,
             "confidence": e.confidence, "join_source": e.source, "evidence": e.evidence,
             "verified": e.verified, "coverage": e.coverage,
             "cardinality": e.cardinality, "cross_source": True}
            for e in usable
        ],
    }
    path = config.GRAPHS_DIR / CROSS_SOURCE_FILE
    path.write_text(json.dumps(payload, indent=2))
    console.print(f"\n[green]{len(usable)}[/] cross-source join(s) -> {path}")
    if warnings:
        console.print(f"[yellow]{len(warnings)} unverified[/] — see the file's warnings")


def _spans_engines(edge) -> bool:
    """True when a join's two tables are held by different engines.

    Only possible in Snowflake mode, where ServiceNow is a local extract and the
    other three sources are warehouse schemas.
    """
    if config.SNOWFLAKE_MODE != "snowflake":
        return False
    from crew_perf.data.scope import get_scope

    scope = get_scope()
    return scope.engine_of(edge.source_table) != scope.engine_of(edge.target_table)


def _source_of(registry, table: str) -> str:
    for s in registry.available():
        if table in s.tables:
            return s.name
    return "?"


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8000, help="Port"),
) -> None:
    """Serve the web UI over the same agents this CLI drives."""
    try:
        from crew_perf.api.app import main as run_server
    except ImportError:
        console.print("[red]The API extra is not installed.[/] "
                      "Install it with:  pip install -e '.[api]'")
        raise typer.Exit(1) from None

    if not config.DUCKDB_PATH.exists() and config.SNOWFLAKE_MODE == "duckdb":
        console.print(f"[red]No database at {config.DUCKDB_PATH}.[/] "
                      "Generate and load data first, or point SNOWFLAKE_MODE at a warehouse.")
        raise typer.Exit(1)

    console.print(f"[cyan]Crew Performance[/] on http://{host}:{port}  "
                  f"[dim](engine: {config.SNOWFLAKE_MODE})[/]")
    run_server(host=host, port=port)


if __name__ == "__main__":
    app()
