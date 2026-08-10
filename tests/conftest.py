"""Shared fixtures.

DuckDB allows a single writer OR multiple readers on one file, so any concurrent
`crewperf synth` / `build-graph` holds an exclusive lock and even read-only opens
fail. Tests copy the database to a temp file so they never contend with a running
build — and so a test can never mutate the working dataset.
"""

from __future__ import annotations

import shutil

import pytest

from crew_perf import config
from crew_perf.data.executor import DuckDBExecutor


@pytest.fixture(scope="session")
def duckdb_snapshot(tmp_path_factory):
    """A private copy of the synthetic dataset."""
    if not config.DUCKDB_PATH.exists():
        pytest.skip("no DuckDB dataset — run `crewperf synth` first")
    dest = tmp_path_factory.mktemp("duckdb") / "snapshot.duckdb"
    shutil.copy2(config.DUCKDB_PATH, dest)
    return dest


@pytest.fixture(scope="session")
def executor(duckdb_snapshot):
    ex = DuckDBExecutor(duckdb_snapshot, read_only=True)
    yield ex
    ex.close()


@pytest.fixture(scope="session")
def weights(executor):
    """The one weighting mechanism, built once for the whole session.

    There used to be two — a declared business prior and this one — and the tests
    each built their own. There is now one, `agents/dynamic.py`, and designing it
    scans the whole population, so it is built once here and shared rather than
    per module.

    `use_llm=False` deliberately: the semantic pass only names and describes what
    the numbers already decided, so skipping it keeps the suite offline and
    deterministic without changing a single weight.
    """
    from crew_perf.agents import dynamic
    from crew_perf.graph.store import get_store

    weightset, _ = dynamic.design(store=get_store(), executor=executor, use_llm=False)
    return weightset
