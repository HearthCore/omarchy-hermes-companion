"""Model catalog for the companion: every provider the user has credentials for,
grouped by provider, with a vision-capability flag per model.

Sources (all from Hermes, no core changes):
  * hermes_cli.models.list_available_providers  -> which providers are authenticated
  * hermes_cli.models.provider_model_ids         -> model ids per provider
  * agent.models_dev.get_model_capabilities      -> vision flag (native providers)
  * Nous /v1/models catalog                      -> vision flag for the Nous aggregator
  * models.dev by bare model id                  -> best-effort flag for other aggregators
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Optional

HERMES_ROOT = Path(os.environ.get("HERMES_AGENT_DIR", Path.home() / ".hermes/hermes-agent"))
sys.path.insert(0, str(HERMES_ROOT))

log = logging.getLogger("companion.catalog")

# Providers that speak the Anthropic wire or are pure Claude fronts: all vision.
_ALL_VISION_PROVIDERS = {"anthropic"}


def _nous_vision_ids() -> Optional[set[str]]:
    try:
        from hermes_cli.models_reasoning_caps import nous_catalog_url

        with urllib.request.urlopen(nous_catalog_url(), timeout=15) as r:
            d = json.load(r)
        d = d.get("data", d) if isinstance(d, dict) else d
        return {m["id"] for m in d if "image" in ((m.get("architecture") or {}).get("input_modalities") or [])}
    except Exception:
        log.warning("nous catalog unavailable", exc_info=True)
        return None


def _bare(model: str) -> str:
    """'openai/gpt-6-astra:free' -> 'gpt-6-astra'."""
    m = model.split("/")[-1]
    m = re.sub(r":(free|batch|us|eu|thinking|fast|flex)$", "", m, flags=re.I)
    return m


def _named_custom_providers() -> list[dict]:
    """User-configured named custom endpoints (config.yaml ``providers:`` / legacy
    ``custom_providers:``) as their routable runtime identities ``custom:<slug>``.

    The bare ``custom`` id is deliberately not offered: the runtime resolver refuses it
    (it owns no endpoint) and the request falls through to an unrelated provider.
    """
    try:
        from hermes_cli.config import load_config
        from hermes_cli.providers import custom_provider_slug

        cfg = load_config()
    except Exception:
        return []
    entries = []
    providers = cfg.get("providers")
    for key, entry in (providers.items() if isinstance(providers, dict) else []):
        if isinstance(entry, dict):
            entries.append((str(entry.get("name") or key), str(key), entry))
    legacy = cfg.get("custom_providers")
    for entry in (legacy if isinstance(legacy, list) else []):
        if isinstance(entry, dict):
            entries.append((str(entry.get("name") or ""), str(entry.get("provider_key") or ""), entry))
    out, seen = [], set()
    for name, key, entry in entries:
        base_url = str(entry.get("base_url") or entry.get("url") or entry.get("api") or "").strip()
        slug = custom_provider_slug(name, key) if (name or key) else ""
        pid = slug if slug.startswith("custom:") else (f"custom:{slug}" if slug else "")
        if not base_url or not pid or pid in seen:
            continue
        seen.add(pid)
        api_key = str(entry.get("api_key") or "").strip()
        key_env = str(entry.get("key_env") or "").strip()
        m = re.match(r"^\$\{([A-Za-z0-9_]+)\}$", api_key)  # api_key is usually a ${ENV_NAME} template
        if m:
            key_env, api_key = m.group(1), ""
        elif key_env:
            api_key = ""
        out.append({"id": pid, "slug": pid.partition(":")[2], "label": name or key,
                    "base_url": base_url, "key_env": key_env, "api_key": api_key})
    return out


def _endpoint_model_ids(base_url: str, key_env: str, api_key: str = "") -> list[str]:
    """OpenAI-wire ``GET <base_url>/models`` for one named custom endpoint."""
    req = urllib.request.Request(base_url.rstrip("/") + "/models")
    key = api_key
    if not key and key_env:
        try:
            from agent.credential_pool import get_env_prefer_dotenv

            key = get_env_prefer_dotenv(key_env).strip()
        except Exception:
            key = os.environ.get(key_env, "").strip()
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    data = d.get("data", d) if isinstance(d, dict) else d
    return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})


class Catalog:
    def __init__(self):
        self.providers: list[dict] = []   # [{id, label, models:[{id, vision, note}]}]
        self._vision_cache: dict[str, Optional[bool]] = {}
        self._mdev_by_bare: dict[str, bool] = {}

    # ---------------------------------------------------------------- build
    def refresh(self, main_provider: str = "") -> "Catalog":
        from hermes_cli.models import list_available_providers, provider_model_ids

        try:
            from agent.models_dev import fetch_models_dev

            reg = fetch_models_dev() or {}
            for prov in reg.values():
                for mid, raw in (prov.get("models") or {}).items():
                    mods = (raw.get("modalities") or {}).get("input") or []
                    vis = "image" in mods or bool(raw.get("attachment"))
                    self._mdev_by_bare[_bare(mid)] = self._mdev_by_bare.get(_bare(mid), False) or vis
        except Exception:
            log.warning("models.dev registry unavailable", exc_info=True)

        nous_vis = None
        nous_free_tier = False
        provs = []
        for p in list_available_providers():
            if not (p.get("authenticated") or self._extra_auth(p["id"])):
                continue
            pid = p["id"]
            if pid == "custom":
                continue  # bare "custom" owns no endpoint; named endpoints are appended below
            try:
                ids = provider_model_ids(pid) or []
            except Exception:
                log.warning("model list failed for %s", pid, exc_info=True)
                provs.append({"id": pid, "label": p.get("label", pid), "models": [], "error": "unavailable"})
                continue
            if pid == "nous":
                nous_vis = _nous_vision_ids()
                try:
                    from hermes_cli.models import check_nous_free_tier

                    nous_free_tier = bool(check_nous_free_tier())
                except Exception:
                    pass
            models = []
            for mid in ids:
                vis = self._vision_for(pid, mid, nous_vis)
                note = ""
                if pid == "nous":
                    if mid.endswith(":free"):
                        note = "free"
                    elif nous_free_tier:
                        note = "⚠ no credits"
                models.append({"id": mid, "vision": vis, "note": note})
            # vision-capable first, then the rest (each keeps catalog order)
            models.sort(key=lambda m: (m["vision"] is not True,))
            provs.append({"id": pid, "label": p.get("label", pid), "models": models})

        # named custom endpoints (skipping ones that are real registry providers), then
        # main provider first, then alphabetical
        canonical_ids = {p["id"] for p in provs}
        for np in _named_custom_providers():
            if np["slug"] in canonical_ids or np["id"] in canonical_ids:
                continue
            try:
                models = [{"id": mid, "vision": self._vision_for(np["id"], mid, None), "note": ""}
                          for mid in _endpoint_model_ids(np["base_url"], np["key_env"], np["api_key"])]
                models.sort(key=lambda m: (m["vision"] is not True,))
                provs.append({"id": np["id"], "label": np["label"], "models": models})
            except Exception:
                log.warning("model list failed for %s", np["id"], exc_info=True)
                provs.append({"id": np["id"], "label": np["label"], "models": [], "error": "unavailable"})
        main_ids = {main_provider, f"custom:{main_provider}"}
        provs.sort(key=lambda p: (p["id"] not in main_ids, p["id"]))
        self.providers = provs
        return self

    @staticmethod
    def _extra_auth(pid: str) -> bool:
        """Credentials Hermes can use at call time but that list_available_providers misses
        (e.g. Anthropic via Claude Code's ~/.claude/.credentials.json)."""
        if pid == "anthropic":
            try:
                from agent.anthropic_credentials import resolve_anthropic_token

                return bool(resolve_anthropic_token())
            except Exception:
                return False
        return False

    def _vision_for(self, pid: str, mid: str, nous_vis: Optional[set[str]]) -> Optional[bool]:
        key = f"{pid}:{mid}"
        if key in self._vision_cache:
            return self._vision_cache[key]
        v: Optional[bool] = None
        if pid in _ALL_VISION_PROVIDERS:
            v = True
        elif pid == "nous" and nous_vis is not None:
            v = mid in nous_vis
        else:
            try:
                from agent.models_dev import get_model_capabilities

                caps = get_model_capabilities(pid, mid, allow_network=False)
                if caps is not None:
                    v = bool(caps.supports_vision)
            except Exception:
                pass
            if v is None:
                v = self._mdev_by_bare.get(_bare(mid))  # None if unknown
        self._vision_cache[key] = v
        return v

    # ---------------------------------------------------------------- queries
    def supports_vision(self, key: str) -> Optional[bool]:
        """key = 'provider:model'. True/False, or None if unknown."""
        for p in self.providers:
            for m in p["models"]:
                if f'{p["id"]}:{m["id"]}' == key:
                    return m["vision"]
        return None

    def exists(self, key: str) -> bool:
        return any(f'{p["id"]}:{m["id"]}' == key for p in self.providers for m in p["models"])

    def default_vision_key(self, prefer: str = "") -> str:
        if prefer and self.supports_vision(prefer):
            return prefer
        for p in self.providers:
            for m in p["models"]:
                if m["vision"]:
                    return f'{p["id"]}:{m["id"]}'
        return prefer

    def to_state(self) -> list[dict]:
        """Flat list for the widget: provider header rows + model rows."""
        out = []
        for p in self.providers:
            out.append({"header": True, "label": p["label"], "provider": p["id"], "error": p.get("error", "")})
            for m in p["models"]:
                out.append({
                    "value": f'{p["id"]}:{m["id"]}',
                    "label": m["id"] + (f'  ·  {m["note"]}' if m["note"] else ""),
                    "vision": m["vision"],
                    "provider": p["id"],
                })
        return out
