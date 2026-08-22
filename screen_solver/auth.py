"""Signing in to Anthropic without managing an API key.

`ant auth login` opens a browser, and writes an OAuth profile under
~/.config/anthropic/. The Anthropic SDK's credential chain picks that profile
up on its own — a bare `AsyncAnthropic()` just works, and the SDK refreshes
the short-lived access token itself. So all this module has to do is install
awareness of the profile, drive login/logout, and keep a stale API key from
silently shadowing it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Values people leave behind after copying .env.example. An API key — even an
# empty or placeholder one — outranks an OAuth profile in the SDK's credential
# chain, so a forgotten placeholder would silently break a working login.
PLACEHOLDER_KEYS = {"", "sk-ant-...", "sk-ant-xxx", "your-api-key", "changeme", "todo"}

# Homebrew and Go install locations, for when the server inherits a thin PATH.
EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "~/go/bin", "~/.local/bin")

INSTALL_HINT = (
    "The Anthropic CLI is not installed. On macOS:\n"
    "    brew install anthropics/tap/ant\n"
    '    xattr -d com.apple.quarantine "$(brew --prefix)/bin/ant"'
)


def looks_like_placeholder(value: str | None) -> bool:
    if value is None:
        return False
    v = value.strip()
    return v.lower() in PLACEHOLDER_KEYS or v.endswith("...")


def drop_placeholder_key() -> str | None:
    """Remove a placeholder ANTHROPIC_API_KEY so it cannot shadow a profile.

    Returns the dropped value's variable name, or None.
    """
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if var in os.environ and looks_like_placeholder(os.environ[var]):
            del os.environ[var]
            return var
    return None


def ant_bin() -> str | None:
    found = shutil.which("ant")
    if found:
        return found
    for d in EXTRA_BIN_DIRS:
        candidate = Path(d).expanduser() / "ant"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# --------------------------------------------------------------------------- #
# Profile location — mirrors the SDK's own resolution, and defers to it when
# the private helpers are importable so the two can never disagree.
# --------------------------------------------------------------------------- #

def config_dir() -> Path:
    try:
        from anthropic.lib.credentials._constants import _config_dir

        return _config_dir()
    except Exception:
        env = os.environ.get("ANTHROPIC_CONFIG_DIR")
        return Path(env) if env else Path.home() / ".config" / "anthropic"


def active_profile() -> str:
    try:
        from anthropic.lib.credentials._constants import _active_profile

        return _active_profile()
    except Exception:
        env = os.environ.get("ANTHROPIC_PROFILE")
        if env:
            return env
        pointer = config_dir() / "active_config"
        if pointer.is_file():
            name = pointer.read_text(encoding="utf-8").strip()
            if name:
                return name
        return "default"


def _credentials_path(profile: str) -> Path:
    return config_dir() / "credentials" / f"{profile}.json"


def _config_path(profile: str) -> Path:
    return config_dir() / "configs" / f"{profile}.json"


@dataclass
class AuthStatus:
    source: str = "none"          # api_key | auth_token | profile | none
    signed_in: bool = False
    profile: str = "default"
    detail: str = ""
    expires_at: int | None = None
    ant_installed: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def expires_in(self) -> int | None:
        if self.expires_at is None:
            return None
        return max(0, int(self.expires_at - time.time()))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "signed_in": self.signed_in,
            "profile": self.profile,
            "detail": self.detail,
            "expires_in": self.expires_in,
            "ant_installed": self.ant_installed,
            "warnings": self.warnings,
        }


def status() -> AuthStatus:
    st = AuthStatus(ant_installed=ant_bin() is not None)
    profile = active_profile()
    st.profile = profile

    creds = _credentials_path(profile)
    have_profile = creds.is_file()

    key = os.environ.get("ANTHROPIC_API_KEY")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")

    if key and not looks_like_placeholder(key):
        st.source = "api_key"
        st.signed_in = True
        st.detail = f"API key …{key[-4:]}"
        if have_profile:
            st.warnings.append(
                "ANTHROPIC_API_KEY is set, so it wins over your signed-in "
                "profile. Remove it from .env to use the login instead."
            )
        return st

    if token and not looks_like_placeholder(token):
        st.source = "auth_token"
        st.signed_in = True
        st.detail = "ANTHROPIC_AUTH_TOKEN from the environment"
        return st

    if have_profile:
        st.source = "profile"
        st.signed_in = True
        st.detail = f"signed in as profile “{profile}”"
        try:
            data = json.loads(creds.read_text(encoding="utf-8"))
            exp = data.get("expires_at")
            st.expires_at = int(exp) if exp is not None else None
            if not data.get("refresh_token") and st.expires_in == 0:
                st.warnings.append(
                    "The access token has expired and the profile has no "
                    "refresh token — run sign-in again."
                )
        except (OSError, ValueError, TypeError):
            pass
        cfg = _config_path(profile)
        if cfg.is_file():
            try:
                conf = json.loads(cfg.read_text(encoding="utf-8"))
                ws = conf.get("workspace_id") or conf.get("authentication", {}).get("workspace_id")
                if ws:
                    st.detail += f" · workspace {ws}"
            except (OSError, ValueError, TypeError):
                pass
        return st

    st.detail = (
        "not signed in"
        if st.ant_installed
        else "not signed in — the Anthropic CLI is not installed"
    )
    return st


# --------------------------------------------------------------------------- #
# Login / logout
# --------------------------------------------------------------------------- #

class AuthError(RuntimeError):
    pass


def _login_env() -> dict:
    env = dict(os.environ)
    # A key in the environment would make the fresh profile pointless.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def _login_args(profile: str | None, no_browser: bool = False) -> list[str]:
    binary = ant_bin()
    if not binary:
        raise AuthError(INSTALL_HINT)
    args = [binary, "auth", "login"]
    if profile:
        args += ["--profile", profile]
    if no_browser:
        args.append("--no-browser")
    return args


def login_interactive(profile: str | None = None) -> AuthStatus:
    """Run `ant auth login` attached to the terminal.

    Deliberately does NOT capture output: the CLI prints the authorize URL and
    may prompt for a pasted code, and swallowing either makes a multi-minute
    wait look like a hang.
    """
    proc = subprocess.run(_login_args(profile), env=_login_env())
    if proc.returncode != 0:
        raise AuthError(f"ant auth login exited with status {proc.returncode}.")
    return status()


def extract_url(text: str) -> str | None:
    """Pull the authorize URL out of the CLI's output, for a clickable link."""
    match = re.search(r"https://\S+", text)
    return match.group(0).rstrip(".,)") if match else None


# The CLI asks for a pasted code when the browser cannot reach the local
# callback listener. The prompt has no trailing newline, so it never arrives
# through a line iterator — the reader below works in chunks for that reason.
_CODE_PROMPT = re.compile(r"code\s*:\s*$", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class LoginSession:
    """One `ant auth login` run, driven from a GUI.

    Output is streamed out through ``on_event`` so the caller can show the
    authorize URL and progress, and a code prompt can be answered later with
    :meth:`submit_code` rather than from a terminal.
    """

    def __init__(
        self,
        profile: str | None = None,
        timeout: float = 300.0,
        on_event: "Callable[[str, dict], None] | None" = None,
    ) -> None:
        self.profile = profile
        self.timeout = timeout
        self._on_event = on_event
        self.lines: list[str] = []
        self.needs_code = False
        self._proc: subprocess.Popen | None = None
        self._timed_out = threading.Event()
        self._cancelled = threading.Event()

    # -- plumbing -------------------------------------------------------- #

    def _emit(self, kind: str, payload: dict) -> None:
        if self._on_event:
            self._on_event(kind, payload)

    def _line(self, text: str) -> None:
        text = _ANSI.sub("", text).rstrip()
        if not text:
            return
        self.lines.append(text)
        self._emit("line", {"text": text, "url": extract_url(text)})

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        fd = self._proc.stdout.fileno()
        buf = ""
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                self._line(line.rstrip("\r"))
            # A trailing fragment with no newline is either a prompt or a
            # partial line; only the prompt shape is actionable.
            tail = _ANSI.sub("", buf).strip()
            if tail and _CODE_PROMPT.search(tail):
                self._line(tail)
                self.needs_code = True
                self._emit(
                    "needs_code",
                    {"message": "Paste the code shown in your browser."},
                )
                buf = ""

    # -- public ---------------------------------------------------------- #

    def run(self) -> AuthStatus:
        """Blocking. Call from a worker thread."""
        self._proc = subprocess.Popen(
            _login_args(self.profile),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            env=_login_env(),
        )

        # A watchdog rather than a deadline in the read loop: `ant` sits
        # silently on the OAuth callback for minutes, so a loop-based check
        # would never be reached while the read blocks.
        watchdog = threading.Timer(self.timeout, self._expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            self._pump()
            self._proc.wait()
        finally:
            watchdog.cancel()
            self._close_stdin()

        if self._cancelled.is_set():
            raise AuthError("Sign-in cancelled.")
        if self._timed_out.is_set():
            raise AuthError("Timed out waiting for the sign-in to finish.")
        if self._proc.returncode != 0:
            raise AuthError(self.tail() or "ant auth login failed.")

        st = status()
        if not st.signed_in:
            raise AuthError("Sign-in finished but no profile was written.\n" + self.tail())
        return st

    def submit_code(self, code: str) -> None:
        if self._proc is None or self._proc.poll() is not None:
            raise AuthError("The sign-in is no longer running.")
        if not self._proc.stdin:
            raise AuthError("The sign-in cannot accept a code.")
        self._proc.stdin.write((code.strip() + "\n").encode())
        self._proc.stdin.flush()
        self.needs_code = False

    def cancel(self) -> None:
        self._cancelled.set()
        self._kill()

    def tail(self, n: int = 8) -> str:
        return "\n".join(self.lines[-n:])

    def _expire(self) -> None:
        self._timed_out.set()
        self._kill()

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def _close_stdin(self) -> None:
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass


def logout(all_profiles: bool = False) -> AuthStatus:
    binary = ant_bin()
    if not binary:
        raise AuthError(INSTALL_HINT)
    args = [binary, "auth", "logout"] + (["--all"] if all_profiles else [])
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise AuthError((proc.stderr or proc.stdout or "logout failed").strip())
    return status()


def cli_status_text() -> str:
    """The CLI's own human-readable report, for `doctor`."""
    binary = ant_bin()
    if not binary:
        return INSTALL_HINT
    proc = subprocess.run(
        [binary, "auth", "status"], capture_output=True, text=True, timeout=30
    )
    return (proc.stdout or proc.stderr or "").strip()
