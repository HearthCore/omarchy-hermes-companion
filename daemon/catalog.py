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

        # main provider first, then alphabetical
        provs.sort(key=lambda p: (p["id"] != main_provider, p["id"]))
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
