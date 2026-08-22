"""Configuration, loaded from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Where the usual local runners listen. Naming one as SOLVER_PROVIDER is a
# shorthand for "openai-compatible, at this address".
LOCAL_PRESETS = {
    "ollama": "http://127.0.0.1:11434/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "llamacpp": "http://127.0.0.1:8080/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "jan": "http://127.0.0.1:1337/v1",
}
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
DEFAULT_LOCAL_MODEL = "qwen2.5vl:7b"
DEFAULT_CLOUD_MODEL = "claude-opus-5"


# A packaged app has its own PROJECT_ROOT inside the .app bundle, so a .env
# sitting in the checkout is invisible to it. The user-level file is the one
# both builds can see; the project file stays first so a checkout can override.
USER_ENV_FILE = Path.home() / ".config" / "screen-solver" / ".env"


def env_files() -> list[Path]:
    explicit = os.environ.get("SOLVER_ENV_FILE")
    paths = [Path(explicit)] if explicit else []
    paths += [PROJECT_ROOT / ".env", USER_ENV_FILE]
    return paths


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader. Existing environment variables always win.

    Several files are read in order; because each key is only ever set with
    setdefault, the first file to mention a key is the one that decides it.
    """
    for candidate in [path] if path else env_files():
        _load_env_file(candidate)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # "anthropic" or "openai". Every local runner speaks the latter.
    provider: str
    base_url: str
    api_key: str
    # True when base_url points at this machine — only affects wording and
    # which failures are worth explaining.
    local: bool
    temperature: float
    max_tokens: int
    timeout: float
    # "auto" lets a model that rejects tool definitions fall back to none.
    tools_mode: str
    model: str
    effort: str
    host: str
    port: int
    capture_display: int
    watch_interval: float
    shots_dir: Path
    keep_shots: int
    # Longest edge sent to the API. 1568px is the point above which the
    # API downscales anyway, so going higher only costs upload time.
    max_edge: int
    # True when launched by the Electron shell, which adds global hotkeys
    # and a menu-bar item the browser build cannot offer.
    desktop: bool

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        # A leftover "sk-ant-..." from .env.example outranks an OAuth profile
        # in the SDK's credential chain, which looks exactly like a broken
        # login. Drop it before anything reads it.
        from .auth import drop_placeholder_key

        drop_placeholder_key()
        provider, base_url, model = _resolve_provider()
        cloud = provider == "anthropic"
        return cls(
            provider=provider,
            base_url=base_url,
            api_key=os.environ.get("SOLVER_API_KEY") or "",
            local=bool(base_url) and any(h in base_url for h in LOCAL_HOSTS),
            temperature=_float("SOLVER_TEMPERATURE", 0.2),
            max_tokens=_int("SOLVER_MAX_TOKENS", 32000 if cloud else 4096),
            timeout=_float("SOLVER_TIMEOUT", 600.0),
            tools_mode=(os.environ.get("SOLVER_TOOLS") or "auto").strip().lower(),
            model=model,
            effort=os.environ.get("SOLVER_EFFORT") or "xhigh",
            host=os.environ.get("SOLVER_HOST") or "127.0.0.1",
            port=_int("SOLVER_PORT", 8787),
            capture_display=_int("SOLVER_CAPTURE_DISPLAY", 1),
            watch_interval=_float("SOLVER_WATCH_INTERVAL", 2.0),
            shots_dir=Path(os.environ.get("SOLVER_SHOTS_DIR") or (PROJECT_ROOT / "shots")),
            keep_shots=_int("SOLVER_KEEP_SHOTS", 40),
            max_edge=_int("SOLVER_MAX_EDGE", 1568),
            desktop=os.environ.get("SOLVER_DESKTOP") == "1",
        )


def _resolve_provider() -> tuple[str, str, str]:
    """Work out which API to talk to, where, and with which model.

    Setting any one of SOLVER_PROVIDER, SOLVER_BASE_URL or a non-Claude
    SOLVER_MODEL is enough — the rest is inferred, so a local setup needs one
    line in .env rather than four.
    """
    provider = (os.environ.get("SOLVER_PROVIDER") or "").strip().lower()
    base_url = (os.environ.get("SOLVER_BASE_URL") or "").strip()
    model = (os.environ.get("SOLVER_MODEL") or "").strip()

    if provider in LOCAL_PRESETS:
        base_url = base_url or LOCAL_PRESETS[provider]
        provider = "openai"
    elif provider in ("local", "openai-compatible"):
        provider = "openai"
    elif not provider:
        if base_url:
            provider = "openai"
        elif model and not model.startswith("claude"):
            provider = "openai"
        else:
            provider = "anthropic"

    if provider != "anthropic":
        provider = "openai"
        base_url = base_url or LOCAL_PRESETS["ollama"]
        if not model or model.startswith("claude"):
            # A leftover SOLVER_MODEL=claude-… from .env.example would
            # otherwise be sent to Ollama, which fails with a bare 404.
            if model:
                print(
                    f"[solver] SOLVER_MODEL={model!r} is a Claude id but the "
                    f"provider is local — using {DEFAULT_LOCAL_MODEL} instead. "
                    "Set SOLVER_MODEL to a model your runner has."
                )
            model = DEFAULT_LOCAL_MODEL
    else:
        model = model or DEFAULT_CLOUD_MODEL

    return provider, base_url, model
