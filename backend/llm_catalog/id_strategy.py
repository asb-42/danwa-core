"""Deterministic module-id from a (source, provider, model) triple.

The id pattern matches the existing convention in
``danwa-modules/llm-profiles/llm-<8hex>``.

We use ``sha256(f"{source}:{provider}:{model_normalized}")[:8]`` —
8 hex chars = 32 bits of entropy, which is more than enough for the
~few-thousand models currently in the wild and keeps the directory
names short.

The model name is normalized to lower-case and stripped of
provider-specific prefixes (``openai/``, ``anthropic.``, …) so that
``gpt-4o`` from catwalk and ``gpt-4o`` from llm_db hash to the same
id.
"""

from __future__ import annotations

import hashlib
import re

# Patterns we strip from a model name before hashing.  These are
# vendor-specific prefixes that don't change which physical model
# is being described.
_PROVIDER_PREFIXES = (
    "openai/",
    "anthropic/",
    "google/",
    "meta-llama/",
    "mistralai/",
    "cohere/",
    "ai21/",
    "databricks/",
    "amazon/",
)


def _strip_provider_prefix(name: str) -> str:
    n = name.strip()
    low = n.lower()
    for p in _PROVIDER_PREFIXES:
        if low.startswith(p):
            return n[len(p):]
    # ``models/<name>`` style used by Gemini + a few others
    if low.startswith("models/"):
        return n[len("models/"):]
    # Some catalogs use ``vendor.name`` or ``vendor/name`` — take the
    # tail component only if there is no whitespace/special char that
    # would change the meaning.
    for sep in (".", "/"):
        if sep in n and " " not in n and "-" not in n.split(sep)[0]:
            return n.split(sep)[-1]
    return n


def normalize_model_name(name: str) -> str:
    """Lower-case, strip provider prefix, collapse whitespace."""
    n = _strip_provider_prefix(name)
    n = re.sub(r"\s+", "-", n.strip())
    return n.lower()


def module_id_for(source: str, provider: str, model: str) -> str:
    """Return the deterministic ``llm-<8hex>`` id for this triple.

    Note: the source is part of the digest so that if the same model
    appears in two catalogs with conflicting metadata, we get two
    distinct ids and the studio UI can show them as separate modules.
    Use :func:`module_id_for_provider_model` for the canonical
    cross-source id (recommended for the import workflow).
    """
    norm = normalize_model_name(model)
    digest = hashlib.sha256(f"{source.lower()}:{provider.lower()}:{norm}".encode("utf-8")).hexdigest()
    return f"llm-{digest[:8]}"


def module_id_for_provider_model(provider: str, model: str) -> str:
    """Source-agnostic id: same model in any catalog hashes to the same dir.

    This is what the import workflow uses so a model present in both
    catwalk and llm_db materialises to a single local module.
    """
    norm = normalize_model_name(model)
    digest = hashlib.sha256(f"{provider.lower()}:{norm}".encode("utf-8")).hexdigest()
    return f"llm-{digest[:8]}"


def display_name(source: str, provider: str, model: str, upstream_name: str | None) -> str:
    """Human-readable module name (used for the manifest 'name' field)."""
    if upstream_name and upstream_name.strip():
        return upstream_name.strip()
    return f"{model} ({provider})"
