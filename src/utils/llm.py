"""
llm.py — Shared LLM & Embedding clients (Azure OpenAI)

All layers import from here to avoid duplicating client setup.
"""

import json
import os
import re as _re

import json_repair
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, AzureCliCredential, get_bearer_token_provider

load_dotenv()

# ─── Constants (loaded from .env — see .env.example) ──────────────────────
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT", "https://abpatra-7946-resource.openai.azure.com/"))
EMBEDDING_ENDPOINT = os.getenv("EMBEDDING_ENDPOINT", os.getenv("AZURE_EMBEDDING_ENDPOINT", "https://abpatra-7946-resource.cognitiveservices.azure.com/"))
API_VERSION = os.getenv("API_VERSION", os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"))
API_KEY = os.getenv("AZURE_OPENAI_API_KEY")  # Use API key if available
EMBEDDING_API_KEY = os.getenv("AZURE_EMBEDDING_API_KEY", API_KEY)  # Use separate key or fall back to main API key
TOKEN_SCOPE = os.getenv("TOKEN_SCOPE", "https://cognitiveservices.azure.com/.default")
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4.1"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", os.getenv("AZURE_EMBEDDING_DEPLOYMENT_NAME", "text-embedding-3-small"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))


# ─── LLM (Chat Completions) ────────────────────────────────────────────────

def get_llm_client() -> AzureOpenAI:
    """Create an Azure OpenAI client for chat completions.

    Prefers API-key auth when AZURE_OPENAI_API_KEY is set; otherwise falls back
    to Azure CLI (az login) token auth.
    """
    if API_KEY:
        return AzureOpenAI(
            azure_endpoint=LLM_ENDPOINT,
            api_key=API_KEY,
            api_version=API_VERSION,
        )
    credential = AzureCliCredential()
    token_provider = get_bearer_token_provider(credential, TOKEN_SCOPE)
    return AzureOpenAI(
        azure_endpoint=LLM_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version=API_VERSION,
    )


def call_llm(client: AzureOpenAI, prompt: str, temperature: float = 0.3) -> str:
    """Send a prompt to GPT-4.1 and return the response text."""
    completion = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return completion.choices[0].message.content


def parse_llm_json(response: str) -> dict | list | None:
    """
    Parse JSON from LLM response, handling markdown fences, comments,
    trailing commas, and other common LLM quirks.
    Uses json_repair for robust parsing.
    Returns parsed object or None on failure.
    """
    if not isinstance(response, str):
        return response  # already parsed (dict/list)

    cleaned = response.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]  # remove first line
        cleaned = cleaned.rsplit("```", 1)[0]  # remove last fence
    cleaned = cleaned.strip()

    if not cleaned:
        return None

    # Pre-clean: strip backslash-newline line continuations that LLMs produce.
    # json_repair handles most quirks, but \<newline> inside large JSON causes
    # it to silently drop trailing keys (e.g. "edges" lost after "nodes" array).
    cleaned = _re.sub(r'\\\s*\n', '\n', cleaned)

    result = json_repair.loads(cleaned)
    # json_repair returns "" for completely unparseable input
    if result == "" or result is None:
        return None
    return result


# ─── Embeddings ─────────────────────────────────────────────────────────────

def get_embedding_client() -> AzureOpenAI:
    """Create an Azure OpenAI client for embeddings.

    Prefers API-key auth when a key is available; otherwise falls back to
    Azure CLI (az login) token auth.
    """
    if EMBEDDING_API_KEY:
        return AzureOpenAI(
            azure_endpoint=EMBEDDING_ENDPOINT,
            api_key=EMBEDDING_API_KEY,
            api_version=API_VERSION,
        )
    credential = AzureCliCredential()
    token_provider = get_bearer_token_provider(credential, TOKEN_SCOPE)
    return AzureOpenAI(
        azure_endpoint=EMBEDDING_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version=API_VERSION,
    )


def embed_texts(client: AzureOpenAI, texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using text-embedding-3-small.
    Batches in groups of 16 to stay within token limits.
    """
    all_embeddings = []
    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i : i + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_embeddings.extend([item.embedding for item in response.data])
    return all_embeddings
