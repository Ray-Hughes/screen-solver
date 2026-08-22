"""Turning API failures into something a person can act on.

Every failure that reaches the dashboard goes through :func:`classify`, which
maps it to a title, an explanation, and — where one exists — a concrete next
step. The raw message and request id are always carried along so nothing is
hidden, just demoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anthropic

CONSOLE_BILLING = "https://console.anthropic.com/settings/billing"
CONSOLE_LIMITS = "https://console.anthropic.com/settings/limits"


@dataclass
class FriendlyError:
    kind: str
    title: str
    detail: str
    hint: str = ""
    actions: list[dict[str, str]] = field(default_factory=list)
    raw: str = ""
    request_id: str | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "hint": self.hint,
            "actions": self.actions,
            "raw": self.raw,
            "request_id": self.request_id,
            "retryable": self.retryable,
            # Kept so older UI paths that only read `message` still say
            # something sensible.
            "message": self.title,
        }


def _body_message(exc: anthropic.APIStatusError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(getattr(exc, "message", "") or exc)


def _retry_after(exc: anthropic.APIStatusError) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _local_error(exc: BaseException) -> FriendlyError | None:
    """Failures that only happen against a local or OpenAI-compatible server.

    The openai SDK is an optional dependency, so it is matched by class name
    rather than imported — a local setup should not become a hard import for
    everyone running against Claude.
    """
    chain = type(exc).__mro__
    names = {c.__name__ for c in chain}
    text = str(exc)
    lowered = text.lower()

    if "APIConnectionError" in names or isinstance(exc, ConnectionError):
        return FriendlyError(
            kind="no_server",
            title="Could not reach the model server",
            detail=(
                "Nothing answered at the configured SOLVER_BASE_URL, so the "
                "request was never sent."
            ),
            hint=(
                "Start your runner and confirm the model is pulled:\n"
                "    ollama serve\n"
                "    ollama pull qwen2.5vl:7b"
            ),
            raw=text,
            retryable=True,
        )

    if "NotFoundError" in names or ("404" in lowered and "model" in lowered):
        return FriendlyError(
            kind="model",
            title="The server does not have that model",
            detail=text or "The configured SOLVER_MODEL was not found.",
            hint="List what you have with `ollama list`, then set SOLVER_MODEL to one of them.",
            raw=text,
        )

    if "does not support images" in lowered or "image input" in lowered:
        return FriendlyError(
            kind="model",
            title="That model cannot read images",
            detail=(
                "Screen Solver always sends a screenshot, so the model has to "
                "be a vision model."
            ),
            hint="Try `ollama pull qwen2.5vl:7b` (or gemma3, llava, minicpm-v).",
            raw=text,
        )

    if "BadRequestError" in names or "APIStatusError" in names:
        return FriendlyError(
            kind="api",
            title="The model server rejected the request",
            detail=text,
            hint=(
                "If it is complaining about tools, set SOLVER_TOOLS=off in "
                ".env — many local models have no tool support."
            ),
            raw=text,
        )
    return None


def classify(exc: BaseException) -> FriendlyError:
    # ---- local / OpenAI-compatible servers -------------------------------
    # Checked first: an anthropic.* isinstance below would never match these.
    if not isinstance(exc, anthropic.AnthropicError):
        friendly = _local_error(exc)
        if friendly is not None:
            return friendly

    # ---- connectivity ---------------------------------------------------
    if isinstance(exc, anthropic.APITimeoutError):
        return FriendlyError(
            kind="timeout",
            title="The request timed out",
            detail="Anthropic did not respond in time.",
            hint="Long solves on a busy screen can take a while. Try again, "
                 "or lower the effort with SOLVER_EFFORT=high.",
            raw=str(exc),
            retryable=True,
        )

    if isinstance(exc, anthropic.APIConnectionError):
        return FriendlyError(
            kind="network",
            title="Could not reach Anthropic",
            detail="The request never left this machine, or the connection dropped.",
            hint="Check your internet connection, VPN, or proxy.",
            raw=str(exc),
            retryable=True,
        )

    # ---- HTTP -----------------------------------------------------------
    if isinstance(exc, anthropic.APIStatusError):
        message = _body_message(exc)
        lowered = message.lower()
        request_id = getattr(exc, "request_id", None)
        status = getattr(exc, "status_code", None)

        if "credit balance is too low" in lowered or "purchase credits" in lowered:
            return FriendlyError(
                kind="billing",
                title="Out of API credit",
                detail=(
                    "Your Anthropic API organization has no credit left, so the "
                    "request was rejected before the model ran."
                ),
                hint=(
                    "A Claude Pro or Max subscription does not cover API usage — "
                    "they are billed separately. Add credit, then press Retry."
                ),
                actions=[
                    {"label": "Add credits", "url": CONSOLE_BILLING},
                ],
                raw=message,
                request_id=request_id,
                retryable=True,
            )

        if isinstance(exc, anthropic.AuthenticationError):
            return FriendlyError(
                kind="auth",
                title="Anthropic rejected the credentials",
                detail="The sign-in has expired or the API key is not valid.",
                hint="Sign in again from the top bar, or check ANTHROPIC_API_KEY in .env.",
                actions=[{"label": "Sign in again", "command": "signin"}],
                raw=message,
                request_id=request_id,
            )

        if isinstance(exc, anthropic.PermissionDeniedError):
            return FriendlyError(
                kind="permission",
                title="This account cannot use that model",
                detail=message,
                hint=(
                    "The signed-in workspace may not have access to "
                    "the configured model. Try SOLVER_MODEL=claude-sonnet-5 in .env."
                ),
                actions=[{"label": "Open console", "url": CONSOLE_LIMITS}],
                raw=message,
                request_id=request_id,
            )

        if isinstance(exc, anthropic.NotFoundError):
            return FriendlyError(
                kind="model",
                title="Model not found",
                detail=message,
                hint="Check SOLVER_MODEL in .env — it must be an exact model id.",
                raw=message,
                request_id=request_id,
            )

        if isinstance(exc, anthropic.RateLimitError):
            wait = _retry_after(exc)
            return FriendlyError(
                kind="rate_limit",
                title="Rate limited",
                detail=(
                    f"Anthropic asked us to wait {wait} seconds before retrying."
                    if wait
                    else "Too many requests in a short window."
                ),
                hint="Watch mode with auto-solve on can hit this quickly.",
                actions=[{"label": "Open limits", "url": CONSOLE_LIMITS}],
                raw=message,
                request_id=request_id,
                retryable=True,
            )

        if isinstance(exc, (anthropic.RequestTooLargeError,)) or "too long" in lowered:
            return FriendlyError(
                kind="too_large",
                title="The request was too large",
                detail=message,
                hint=(
                    "Drag a region around just the problem before solving, or "
                    "lower SOLVER_MAX_EDGE. A long page-context pull can also do it."
                ),
                raw=message,
                request_id=request_id,
            )

        if isinstance(
            exc,
            (
                anthropic.OverloadedError,
                anthropic.ServiceUnavailableError,
                anthropic.InternalServerError,
                anthropic.DeadlineExceededError,
            ),
        ):
            return FriendlyError(
                kind="overloaded",
                title="Anthropic is busy",
                detail="The API is temporarily overloaded or unavailable.",
                hint="This usually clears in a few seconds.",
                raw=message,
                request_id=request_id,
                retryable=True,
            )

        return FriendlyError(
            kind="api",
            title=f"API error {status}" if status else "API error",
            detail=message,
            raw=message,
            request_id=request_id,
            retryable=bool(status and status >= 500),
        )

    # ---- credentials that never got as far as a request -----------------
    if isinstance(exc, anthropic.AnthropicError):
        lowered = str(exc).lower()
        if any(
            marker in lowered
            for marker in (
                "config file not found",
                "credentials",
                "api_key",
                "authentication method",
                "could not resolve",
            )
        ):
            return FriendlyError(
                kind="no_auth",
                title="Not signed in",
                detail=(
                    "No Anthropic credentials were found, so the request was "
                    "never sent."
                ),
                hint="Sign in from the top bar, or set ANTHROPIC_API_KEY in .env.",
                actions=[{"label": "Sign in", "command": "signin"}],
                raw=str(exc),
            )

    # ---- everything else -------------------------------------------------
    return FriendlyError(
        kind="internal",
        title=type(exc).__name__,
        detail=str(exc) or "Something went wrong.",
        raw=f"{type(exc).__name__}: {exc}",
    )


def simple(kind: str, title: str, detail: str = "", **kw: Any) -> dict[str, Any]:
    """Build the same payload shape for non-exception failures."""
    return FriendlyError(kind=kind, title=title, detail=detail, **kw).to_dict()
