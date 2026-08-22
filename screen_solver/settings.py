"""The editable slice of the configuration, and how it is persisted.

Everything here is written to the user-level env file — the one both the
packaged app and ./run.sh read (see config.USER_ENV_FILE) — so a change made
in the dashboard survives a restart and applies to either way of launching.

Writes are line-oriented rather than a rewrite of the whole file, so comments
and any keys this page does not know about are left exactly as they were.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .config import LOCAL_PRESETS, USER_ENV_FILE, Config

# Anthropic ids worth offering; the field stays free-text for anything else.
CLAUDE_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]

PROVIDERS = [
    {"value": "anthropic", "label": "Anthropic (Claude)", "local": False},
    {"value": "ollama", "label": "Ollama", "local": True},
    {"value": "lmstudio", "label": "LM Studio", "local": True},
    {"value": "llamacpp", "label": "llama.cpp", "local": True},
    {"value": "vllm", "label": "vLLM", "local": True},
    {"value": "jan", "label": "Jan", "local": True},
    {"value": "openai", "label": "Custom (OpenAI-compatible)", "local": False},
]

# key -> (kind, applies live). Anything not live needs the app reopened.
FIELDS: dict[str, tuple[str, bool]] = {
    "SOLVER_PROVIDER": ("choice", True),
    "SOLVER_MODEL": ("text", True),
    "SOLVER_BASE_URL": ("text", True),
    "SOLVER_API_KEY": ("secret", True),
    "SOLVER_EFFORT": ("choice", True),
    "SOLVER_TOOLS": ("choice", True),
    "SOLVER_MAX_TOKENS": ("int", True),
    "SOLVER_TEMPERATURE": ("float", True),
    "SOLVER_MAX_EDGE": ("int", True),
    "SOLVER_WATCH_INTERVAL": ("float", True),
    "SOLVER_KEEP_SHOTS": ("int", False),
}

# Sensible ceilings per provider. A value saved for one is usually wrong for
# the other — 4096 would quietly truncate Claude — so the form re-seeds this
# field whenever the provider changes.
MAX_TOKEN_DEFAULTS = {"anthropic": 32000, "local": 4096}

EFFORTS = ["low", "medium", "high", "xhigh", "max"]
TOOL_MODES = ["auto", "on", "off"]


class SettingsError(ValueError):
    """A value the form should not have sent."""


def _validate(key: str, value: str) -> str:
    kind, _ = FIELDS[key]
    value = value.strip()
    if not value:
        return ""

    if key == "SOLVER_PROVIDER" and value not in {p["value"] for p in PROVIDERS}:
        raise SettingsError(f"Unknown provider {value!r}.")
    if key == "SOLVER_EFFORT" and value not in EFFORTS:
        raise SettingsError(f"Effort must be one of {', '.join(EFFORTS)}.")
    if key == "SOLVER_TOOLS" and value not in TOOL_MODES:
        raise SettingsError(f"Tools must be one of {', '.join(TOOL_MODES)}.")
    if key == "SOLVER_BASE_URL" and not re.match(r"^https?://", value):
        raise SettingsError("The base URL must start with http:// or https://.")
    if kind == "int":
        try:
            if int(value) <= 0:
                raise SettingsError(f"{key} must be greater than zero.")
        except ValueError as exc:
            raise SettingsError(f"{key} must be a whole number.") from exc
    if kind == "float":
        try:
            if float(value) < 0:
                raise SettingsError(f"{key} cannot be negative.")
        except ValueError as exc:
            raise SettingsError(f"{key} must be a number.") from exc
    return value


def write(updates: dict[str, str], path: Path | None = None) -> Path:
    """Merge `updates` into the env file, preserving everything else in it.

    A key set to the empty string is removed, so "unset" and "set to blank"
    stay distinguishable — the latter would otherwise shadow a default.
    """
    path = path or USER_ENV_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if stripped.startswith("#") or key not in remaining:
            out.append(line)
            continue
        value = remaining.pop(key)
        if value != "":
            out.append(f"{key}={value}")
        # An empty value drops the line entirely.

    added = [f"{k}={v}" for k, v in remaining.items() if v != ""]
    if added:
        if out and out[-1].strip():
            out.append("")
        out.append("# Written by the Settings panel.")
        out.extend(added)

    path.write_text("\n".join(out).rstrip("\n") + "\n")
    return path


def apply(updates: dict[str, str]) -> tuple[Config, list[str]]:
    """Validate, persist, and rebuild the config. Returns it plus stale keys.

    "Stale" means the value was saved but the running process cannot adopt it
    until it is restarted.
    """
    clean: dict[str, str] = {}
    for key, raw in updates.items():
        if key not in FIELDS:
            raise SettingsError(f"{key} is not an editable setting.")
        clean[key] = _validate(key, "" if raw is None else str(raw))

    write(clean)

    # load_dotenv only ever uses setdefault, so the live process has to be
    # told about the change explicitly.
    for key, value in clean.items():
        if value == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    stale = [k for k, v in clean.items() if not FIELDS[k][1] and v != ""]
    return Config.from_env(), stale


def describe(cfg: Config) -> dict[str, Any]:
    """Current values plus everything the form needs to render itself."""
    # SOLVER_PROVIDER is stored as the runner's name; cfg.provider has already
    # been collapsed to the wire protocol, so report what was actually set.
    stored = (os.environ.get("SOLVER_PROVIDER") or "").strip().lower()
    if stored not in {p["value"] for p in PROVIDERS}:
        stored = "anthropic" if cfg.provider == "anthropic" else "openai"

    return {
        "file": str(USER_ENV_FILE),
        "providers": PROVIDERS,
        "presets": LOCAL_PRESETS,
        "efforts": EFFORTS,
        "tool_modes": TOOL_MODES,
        "claude_models": CLAUDE_MODELS,
        "max_token_defaults": MAX_TOKEN_DEFAULTS,
        "values": {
            "SOLVER_PROVIDER": stored,
            "SOLVER_MODEL": cfg.model,
            "SOLVER_BASE_URL": cfg.base_url,
            "SOLVER_API_KEY": os.environ.get("SOLVER_API_KEY") or "",
            "SOLVER_EFFORT": cfg.effort,
            "SOLVER_TOOLS": cfg.tools_mode,
            "SOLVER_MAX_TOKENS": cfg.max_tokens,
            "SOLVER_TEMPERATURE": cfg.temperature,
            "SOLVER_MAX_EDGE": cfg.max_edge,
            "SOLVER_WATCH_INTERVAL": cfg.watch_interval,
            "SOLVER_KEEP_SHOTS": cfg.keep_shots,
        },
    }


def _get_json(url: str, timeout: float = 8.0) -> Any:
    """Plain stdlib fetch.

    Deliberately not httpx: which HTTP library the Anthropic and OpenAI SDKs
    vendor is their business and it changes between releases (this venv has
    httpx2, not httpx). urllib is always there.
    """
    import json as _json
    from urllib.request import Request, urlopen

    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as res:
        return _json.loads(res.read().decode("utf-8"))


def resolve_target(cfg: Config, provider: str | None, base_url: str | None) -> tuple[str, str]:
    """Where to ask about models — the saved config, or a pending form choice.

    The settings form needs to list what a provider offers *before* the user
    commits to it, so it passes its current selection here rather than making
    the server answer from what is already saved.
    """
    if not provider:
        return cfg.provider, cfg.base_url
    if provider == "anthropic":
        return "anthropic", ""
    return "openai", (base_url or LOCAL_PRESETS.get(provider) or cfg.base_url)


def _list_models_sync(provider: str, base_url: str) -> dict[str, Any]:
    if provider == "anthropic":
        return {"models": [{"id": m} for m in CLAUDE_MODELS], "source": "static"}

    if not base_url:
        raise SettingsError("No server address is set for this provider.")

    root = base_url.rstrip("/")

    # Ollama's native endpoint reports per-model capabilities, which is the
    # only reliable way to tell a vision model from a text one — and this app
    # is useless without vision.
    if root.endswith("/v1"):
        try:
            data = _get_json(f"{root[:-3]}/api/tags")
            return {
                "models": [
                    {
                        "id": m.get("name", ""),
                        "size": m.get("size"),
                        "vision": "vision" in (m.get("capabilities") or []),
                        "usable": "embedding" not in (m.get("capabilities") or []),
                    }
                    for m in data.get("models", [])
                ],
                "source": "ollama",
            }
        except SettingsError:
            raise
        except Exception:
            pass  # not Ollama, or not up — fall through to the generic endpoint

    data = _get_json(f"{root}/models")
    return {
        "models": [{"id": m.get("id", "")} for m in data.get("data", [])],
        "source": "openai",
    }


async def list_models(
    cfg: Config, provider: str | None = None, base_url: str | None = None
) -> dict[str, Any]:
    """What the given (or configured) server can actually run."""
    import asyncio

    target, url = resolve_target(cfg, provider, base_url)
    return await asyncio.to_thread(_list_models_sync, target, url)
