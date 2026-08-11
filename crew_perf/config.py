"""Central configuration. Everything env-driven; no secrets in code.

Loads `.env` from the repo root regardless of the current working directory, so
the CLI behaves the same whether invoked from the repo root or elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# ─── Paths ──────────────────────────────────────────────────────────────────
SCHEMAS_DIR = REPO_ROOT / "schemas"
GRAPHS_DIR = REPO_ROOT / "graphs"
DOCS_DIR = REPO_ROOT / "docs"
DATA_DIR = REPO_ROOT / "data"
EVAL_DIR = REPO_ROOT / "eval"

# ─── Azure OpenAI ───────────────────────────────────────────────────────────
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT", ""))
EMBEDDING_ENDPOINT = os.getenv("EMBEDDING_ENDPOINT", LLM_ENDPOINT)
API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
EMBEDDING_API_KEY = os.getenv("AZURE_EMBEDDING_API_KEY", API_KEY)
API_VERSION = os.getenv("API_VERSION", "2024-02-15-preview")
TOKEN_SCOPE = os.getenv("TOKEN_SCOPE", "https://cognitiveservices.azure.com/.default")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))

# ─── Cosmos DB (Gremlin) ────────────────────────────────────────────────────
# Same account/database as the existing Indigo knowledge layer, but a distinct
# graph — the existing IndigoKG graph must never be written to by this project.
COSMOS_ENDPOINT = os.getenv("COSMOS_DB_ENDPOINT", "")
COSMOS_KEY = os.getenv("COSMOS_DB_KEY", "")
COSMOS_DATABASE = os.getenv("COSMOS_DB_DATABASE", "IndigoKG")
COSMOS_GRAPH = os.getenv("COSMOS_DB_GRAPH", "CrewPerfKG")

# Hard guard: refuse to write to the donor project's graph.
PROTECTED_GRAPHS = {"knowledgeGraph", "Unified_Knowledge_graph"}

# ─── Data layer ─────────────────────────────────────────────────────────────
# snowflake (default, production warehouse) | duckdb (local synthetic dataset).
# Production is the default because it is what the deployed system reads; a
# local run that wants the synthetic data has to say so, rather than a
# misconfigured production run silently answering from 800 invented crew.
SNOWFLAKE_MODE = os.getenv("SNOWFLAKE_MODE", "snowflake").lower()

# Still needed in Snowflake mode: ServiceNow is a file extract, not a warehouse
# schema, and its tables are served from here while PEP, CLMS and CrewPortal are
# read from Snowflake. See data/scope.py.
DUCKDB_PATH = REPO_ROOT / os.getenv("DUCKDB_PATH", "data/crew_perf.duckdb")

SNOWFLAKE = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT", ""),
    "user": os.getenv("SNOWFLAKE_USER", ""),
    # A programmatic access token. Preferred for anything unattended: it is
    # scoped to a role, expires on a date you set, and is revocable on its own
    # without touching the account's password. Supplied as `token=` with
    # `authenticator=PROGRAMMATIC_ACCESS_TOKEN` — NOT as a password, which is a
    # different login flow that a PAT fails against MFA-enforced accounts.
    "pat": os.getenv("SNOWFLAKE_PAT", os.getenv("SNOWFLAKE_TOKEN", "")),
    "password": os.getenv("SNOWFLAKE_PASSWORD", ""),
    # Key-pair and SSO, because a service account running scheduled scoring
    # should not be holding a password that expires.
    "private_key_file": os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE", ""),
    "private_key_passphrase": os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", ""),
    "authenticator": os.getenv("SNOWFLAKE_AUTHENTICATOR", ""),
    "role": os.getenv("SNOWFLAKE_ROLE", ""),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", ""),
    "database": os.getenv("SNOWFLAKE_DATABASE", ""),
    # The session default only. The source schemas are read from the
    # column-level exports in schemas/ — see data/scope.py — so a query crossing
    # sources resolves each table in the schema it lives in rather than failing
    # on the first one outside this default.
    "schema": os.getenv("SNOWFLAKE_SCHEMA", "PEP"),
    # Network timeouts. A warehouse that is resuming can take tens of seconds;
    # an unreachable one should not hang a CLI command indefinitely.
    "login_timeout": int(os.getenv("SNOWFLAKE_LOGIN_TIMEOUT", "60")),
    "query_timeout": int(os.getenv("SNOWFLAKE_QUERY_TIMEOUT", "300")),
}


def snowflake_auth() -> dict:
    """The credential kwargs for `snowflake.connector.connect`, one method only.

    Passing several at once is not "belt and braces" — the connector picks by
    precedence and the one that wins is not obviously the one you set, so a stale
    password in the environment can quietly beat the PAT you just issued. This
    resolves to exactly one method and names it, so a failure says which
    credential was actually tried.
    """
    cfg = SNOWFLAKE
    if cfg["pat"]:
        return {"authenticator": "PROGRAMMATIC_ACCESS_TOKEN", "token": cfg["pat"],
                "_method": "programmatic access token"}
    if cfg["private_key_file"]:
        return {
            "private_key_file": cfg["private_key_file"],
            "private_key_file_pwd": (cfg["private_key_passphrase"].encode()
                                     if cfg["private_key_passphrase"] else None),
            "_method": "key pair",
        }
    if cfg["authenticator"]:
        return {"authenticator": cfg["authenticator"],
                "password": cfg["password"] or None,
                "_method": cfg["authenticator"]}
    if cfg["password"]:
        return {"password": cfg["password"], "_method": "password"}
    return {"_method": ""}


# Per-source schema overrides, keyed by the registry's source name upper-cased.
# Empty by default: the export's own TABLE_SCHEMA is authoritative, and this is
# only for a warehouse whose schemas were renamed on the way in.
SNOWFLAKE_SCHEMA_OVERRIDES = {
    name: value
    for name in ("PEP", "CLMS", "CREWPORTAL")
    if (value := os.getenv(f"SNOWFLAKE_SCHEMA_{name}", ""))
}

# ─── Synthetic data ─────────────────────────────────────────────────────────
SYNTH_SEED = int(os.getenv("SYNTH_SEED", "20260731"))
SYNTH_CREW_COUNT = int(os.getenv("SYNTH_CREW_COUNT", "800"))
SYNTH_MONTHS = int(os.getenv("SYNTH_MONTHS", "6"))

# Ground truth lives outside the queryable database — agents must never see it.
LATENT_TRAITS_PATH = DATA_DIR / "ground_truth" / "latent_traits.parquet"


def assert_graph_writable() -> None:
    """Guard against writing into the donor project's live graph."""
    if COSMOS_GRAPH in PROTECTED_GRAPHS:
        raise RuntimeError(
            f"Refusing to write to protected graph {COSMOS_GRAPH!r}. "
            f"Set COSMOS_DB_GRAPH to a project-owned graph (e.g. CrewPerfKG)."
        )
