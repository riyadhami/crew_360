"""Configuration-level tests for the shared Azure OpenAI clients."""

from __future__ import annotations

from crew_perf import config
from crew_perf.llm import _embedding_azure_endpoint


def test_embedding_resource_url_is_used_directly(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "https://embedding.openai.azure.com")
    monkeypatch.setattr(config, "EMBEDDING_ENDPOINT", "https://ignored.example")

    assert _embedding_azure_endpoint() == "https://embedding.openai.azure.com"


def test_full_embedding_rest_url_is_normalised_for_sdk(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "")
    monkeypatch.setattr(
        config,
        "EMBEDDING_ENDPOINT",
        "https://embedding.openai.azure.com/openai/deployments/"
        "text-embedding-3-small/embeddings?api-version=2023-05-15",
    )

    assert _embedding_azure_endpoint() == "https://embedding.openai.azure.com"
