"""Shared LLM & embedding clients (Azure OpenAI).

Ported from Indigo_Knowledge_Layer-main/src/utils/llm.py with config centralised
in crew_perf.config and retry/backoff added (the donor re-implemented retries in
each agent; here it lives in one place).
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit, urlunsplit

import json_repair
from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import AzureOpenAI

from crew_perf import config

_llm_client: AzureOpenAI | None = None
_embed_client: AzureOpenAI | None = None


# ─── Clients ────────────────────────────────────────────────────────────────


def _embedding_azure_endpoint() -> str:
    """Return the Azure resource URL expected by ``AzureOpenAI``.

    Azure's portal often supplies a complete embeddings REST URL. The SDK takes
    the resource base URL instead and adds the deployment path and API version
    itself, so passing the portal URL unchanged produces an invalid doubled URL.
    """
    endpoint = config.EMBEDDING_BASE_URL or config.EMBEDDING_ENDPOINT
    parsed = urlsplit(endpoint)
    if "/openai/deployments/" in parsed.path:
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return endpoint


def get_llm_client() -> AzureOpenAI:
    """Azure OpenAI client for chat completions (cached).

    Prefers API-key auth; falls back to Azure CLI token auth (`az login`).
    """
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    if config.API_KEY:
        _llm_client = AzureOpenAI(
            azure_endpoint=config.LLM_ENDPOINT,
            api_key=config.API_KEY,
            api_version=config.API_VERSION,
        )
    else:
        token_provider = get_bearer_token_provider(AzureCliCredential(), config.TOKEN_SCOPE)
        _llm_client = AzureOpenAI(
            azure_endpoint=config.LLM_ENDPOINT,
            azure_ad_token_provider=token_provider,
            api_version=config.API_VERSION,
        )
    return _llm_client


def get_embedding_client() -> AzureOpenAI:
    """Azure OpenAI client for embeddings (cached)."""
    global _embed_client
    if _embed_client is not None:
        return _embed_client

    if config.EMBEDDING_API_KEY:
        _embed_client = AzureOpenAI(
            azure_endpoint=_embedding_azure_endpoint(),
            api_key=config.EMBEDDING_API_KEY,
            api_version=config.EMBEDDING_API_VERSION,
        )
    else:
        token_provider = get_bearer_token_provider(AzureCliCredential(), config.TOKEN_SCOPE)
        _embed_client = AzureOpenAI(
            azure_endpoint=_embedding_azure_endpoint(),
            azure_ad_token_provider=token_provider,
            api_version=config.EMBEDDING_API_VERSION,
        )
    return _embed_client


# ─── Chat ───────────────────────────────────────────────────────────────────


def call_llm(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.3,
    max_retries: int = 4,
    client: AzureOpenAI | None = None,
) -> str:
    """Send a prompt and return the response text, retrying on transient errors."""
    client = client or get_llm_client()
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                temperature=temperature,
            )
            return completion.choices[0].message.content or ""
        except Exception as e:
            last_err = e
            if _is_content_policy_error(e) or attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)
    raise last_err  # pragma: no cover


def call_llm_messages(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.3,
    max_retries: int = 4,
    client: AzureOpenAI | None = None,
):
    """Multi-turn call returning the raw message object — for ReAct/tool loops."""
    client = client or get_llm_client()
    kwargs: dict = {"model": config.LLM_MODEL, "messages": messages, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**kwargs).choices[0].message
        except Exception as e:
            last_err = e
            if _is_content_policy_error(e) or attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)
    raise last_err  # pragma: no cover


def _is_content_policy_error(e: Exception) -> bool:
    """Content-policy rejections are not worth retrying — the prompt must change."""
    text = str(e).lower()
    return "content_filter" in text or "content policy" in text or "responsibleai" in text


# ─── JSON parsing ───────────────────────────────────────────────────────────


def parse_llm_json(response: str) -> dict | list | None:
    """Parse JSON from an LLM response, tolerating markdown fences and LLM quirks.

    Returns None when nothing parseable is present.
    """
    if not isinstance(response, str):
        return response  # already parsed

    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    if not cleaned:
        return None

    # Backslash-newline continuations make json_repair silently drop trailing keys
    # (e.g. "edges" lost after a long "nodes" array). Strip them first.
    cleaned = re.sub(r"\\\s*\n", "\n", cleaned)

    result = json_repair.loads(cleaned)
    return None if result == "" or result is None else result


# ─── Embeddings ─────────────────────────────────────────────────────────────


def embed_texts(texts: list[str], client: AzureOpenAI | None = None) -> list[list[float]]:
    """Embed texts in batches, preserving input order."""
    client = client or get_embedding_client()
    out: list[list[float]] = []
    for i in range(0, len(texts), config.EMBEDDING_BATCH_SIZE):
        batch = texts[i : i + config.EMBEDDING_BATCH_SIZE]
        resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=batch)
        out.extend(item.embedding for item in resp.data)
    return out
