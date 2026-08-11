"""Which crew the synthetic fleet is *about*.

The generator used to invent its own identifiers — `IGA60000 + i` for a
contiguous block — while ServiceNow and CAC arrived as real extracts keyed on a
completely different IGA universe. Nothing connected them, so the only crew who
appeared in both were the ones whose invented identifier happened to collide
with a real one: 146 of 800 against ServiceNow, 106 against CAC. Measured across
rows rather than crew, roughly **1% of either extract** could be attributed to
anybody, and every signal drawn from them covered a sliver of the fleet.

So the dependency is inverted here. The extracts are fixed — they are real files
and no crew can be added to them — while PEP, CLMS and CrewPortal are generated
and can be about anyone. The fleet is therefore *drawn from* the extracts, and
the warehouse sources are generated for those crew.

Two filters, both load-bearing:

**Cabin crew only.** ServiceNow names crew by seat, and `FO`/`CAPTAIN` are the
flight deck. 4,906 of its 13,996 crew appear in no other seat, and generating
cabin-service assessments, grooming marks and cabin-management scores for pilots
would be inventing a population that does not exist.

**Five-digit identifiers only.** The extracts carry about a hundred values that
are not employee numbers — two- and three-digit fragments, and one `IGA70000252`
— which are delimiter damage rather than crew.

CLMS needs no equivalent: it keys on its own `CREW_ID`, generated positionally
and bridged through `M_CREW_DETAILS`, so it follows the fleet automatically.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from crew_perf import config

CABIN_SEATS = {"L1", "L2", "L3", "L4", "R1", "R2", "R3", "R4"}

# An employee number, as spelled once normalised. The extracts write it bare
# (`94736`, CAC) or with the prefix and stray spacing (`IGA 35231`, ServiceNow).
_FIVE_DIGIT = re.compile(r"^IGA\d{5}$")


@lru_cache(maxsize=1)
def _scan_servicenow() -> tuple[frozenset[str], object]:
    """One pass over the extract: who flew a cabin seat, and when it last flew.

    Cached because both the fleet and the observation window are read from it and
    the file is 23 MB — scanning it twice per `synth` is a third of the run.
    """
    from crew_perf.data.servicenow import POSITION_COLUMNS, parse_crew, parse_timestamp

    path = _servicenow_path()
    igas: set[str] = set()
    latest = None
    if not path.exists():
        return frozenset(), None
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            for position, column in POSITION_COLUMNS.items():
                if position not in CABIN_SEATS:
                    continue
                iga, _ = parse_crew(row.get(column, ""))
                if iga:
                    igas.add(iga)
            when = parse_timestamp(row.get("Flight Date Time", ""))
            if when and (latest is None or when > latest):
                latest = when
    return frozenset(igas), latest


def _servicenow_path() -> Path:
    from crew_perf.data import servicenow as sn_mod

    return sn_mod.CSV_PATH


def anchor_end_date(default):
    """The day the observation window should end.

    The extracts cannot be moved — ServiceNow's reports are real and stop on a
    fixed date — so it is the generated window that has to meet them. Anchored to
    the last reported flight, a six-month window contains the extract; anchored
    to a constant, the two can miss each other entirely, which is what happened:
    generated history ran to 2026-07-30 while every ServiceNow report was from
    autumn 2025, so no assessment and no inflight report ever described the same
    period, and any question comparing them was answering across a year-wide gap.
    """
    _, latest = _scan_servicenow()
    if latest is None:
        return default
    # One day past the last flight, so the window closes on a whole day rather
    # than mid-afternoon on the busiest one.
    return datetime(latest.year, latest.month, latest.day) + timedelta(days=1)


def _cac_igas(path: Path) -> set[str]:
    """Crew a CAC appreciation letter was raised for."""
    if not path.exists():
        return set()
    import pandas as pd

    from crew_perf.data.cac import normalise_iga

    column = pd.read_excel(path, usecols=["IGA"])["IGA"].dropna()
    return {iga for iga in (normalise_iga(v) for v in column) if iga}


def candidate_igas(seed: int = 0) -> tuple[list[str], dict]:
    """The fleet the extracts can actually support, and what it was drawn from.

    Ordered deterministically — sorted, then shuffled on the generator's own seed
    — so two runs of `synth` produce the same fleet. Sorting first matters: set
    iteration order is not stable across processes, and a fleet that changed
    between runs would make every scorecard unrepeatable.
    """
    from crew_perf.data import cac as cac_mod

    sn = set(_scan_servicenow()[0])
    cac_path = cac_mod.default_path()
    cac = _cac_igas(cac_path) if cac_path else set()

    sn_clean = {i for i in sn if _FIVE_DIGIT.match(i)}
    cac_clean = {i for i in cac if _FIVE_DIGIT.match(i)}
    union = sn_clean | cac_clean

    provenance = {
        "servicenow_cabin_crew": len(sn_clean),
        "cac_crew": len(cac_clean),
        "in_both": len(sn_clean & cac_clean),
        "servicenow_only": len(sn_clean - cac_clean),
        "cac_only": len(cac_clean - sn_clean),
        "rejected_not_five_digit": len((sn | cac) - (sn_clean | cac_clean)),
        "pool": len(union),
    }

    # Ordered so that a fleet smaller than the pool is drawn from the crew who
    # appear in BOTH extracts first, then ServiceNow-only, then CAC-only.
    #
    # This is what makes a reduced fleet worth querying. Sampling the union at
    # random gives 800 crew of whom roughly a quarter have no ServiceNow row and
    # a further sixth have no appreciation letter, so half the questions about
    # those sources come back thin for reasons that are an artefact of the
    # sample. Taking the intersection first means every crew member in a small
    # fleet carries all five sources — the median one has 11 ServiceNow rows and
    # 3 letters — while the full pool is unchanged, because at `n = len(pool)`
    # the order does not matter.
    #
    # Shuffled within each band rather than ranked by volume: ordering by
    # evidence would hand a small fleet the busiest crew in the airline and
    # quietly shift every distribution the mechanism fits to.
    import numpy as np

    rng = np.random.default_rng(seed)
    bands = []
    for band in (sn_clean & cac_clean, sn_clean - cac_clean, cac_clean - sn_clean):
        members = sorted(band)
        rng.shuffle(members)
        bands.extend(members)
    return bands, provenance


def fallback_igas(n: int) -> list[str]:
    """The old contiguous block, for a checkout with no extracts.

    Kept so the generator still runs against a bare repository — the extracts are
    large binary files and not everyone who clones this has them. Such a run is
    self-consistent and simply has no ServiceNow or CAC signal, which is the
    honest outcome rather than a crash.
    """
    return [f"IGA{60000 + i}" for i in range(n)]


def resolve(n_crew: int | None, seed: int = 0) -> tuple[list[str], dict]:
    """The fleet to generate, and a note of where it came from.

    `n_crew=None` means "as many as the extracts support" — the whole pool. A
    number caps it, and because the pool is shuffled, a smaller fleet is a random
    sample rather than the alphabetical head of the identifier range.
    """
    pool, provenance = candidate_igas(seed)
    if not pool:
        n = n_crew or config.SYNTH_CREW_COUNT
        provenance.update(source="fallback", requested=n, generated=n)
        return fallback_igas(n), provenance

    n = len(pool) if n_crew is None else min(n_crew, len(pool))
    chosen = pool[:n]
    provenance.update(source="extracts", requested=n_crew, generated=n,
                      # How many of the fleet carry both extracts. Worth stating
                      # rather than inferring: it is the difference between a
                      # reduced fleet that can answer a ServiceNow question and
                      # one that reports missing data for a quarter of it.
                      all_five_sources=min(n, provenance["in_both"]))
    return chosen, provenance
