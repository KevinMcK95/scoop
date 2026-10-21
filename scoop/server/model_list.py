"""The models an OpenAI-compatible endpoint offers (its ``GET /models`` list), for the
Settings tab's model search."""
from __future__ import annotations

MAX_MODELS = 2000


def parse_models(payload) -> list[dict]:
    """``{"data": [{"id", "name"?, "context_length"?}, ...]}`` -> [{id, name, context_length}], sorted by id."""
    entries = payload.get("data") if isinstance(payload, dict) else None
    models = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
            name, length = entry.get("name"), entry.get("context_length")
            models[entry["id"]] = {"id": entry["id"], "name": name if isinstance(name, str) and name else None,
                                   "context_length": length if isinstance(length, int) and not isinstance(length, bool) else None}
    return sorted(models.values(), key=lambda m: m["id"].lower())[:MAX_MODELS]


def model_lister(client, headers_for=lambda base_url: {}):
    """An async ``(base_url, api_key) -> models`` using an active httpx client."""

    async def list_models(base_url: str, api_key: str | None) -> list[dict]:
        headers = {**headers_for(base_url), **({"Authorization": f"Bearer {api_key}"} if api_key else {})}
        response = await client.get(base_url.rstrip("/") + "/models", headers=headers, timeout=20.0)
        response.raise_for_status()
        return parse_models(response.json())

    return list_models


__all__ = ["MAX_MODELS", "model_lister", "parse_models"]
