"""Anthropic backend — the default, and the only one with adaptive thinking."""

from __future__ import annotations

from typing import Any, Callable

import anthropic

from .base import Backend, Turn


class AnthropicBackend(Backend):
    name = "anthropic"
    supports_tools = True

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def label(self) -> str:
        return f"{self.cfg.model} · {self.cfg.effort}"

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Built lazily so signing in mid-session takes effect immediately.

        A bare client runs the SDK's credential chain: ANTHROPIC_API_KEY, then
        ANTHROPIC_AUTH_TOKEN, then the OAuth profile written by `ant auth
        login` (which the SDK also refreshes on its own).
        """
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    def reset(self) -> None:
        self._client = None

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "label": self.label,
            "model": self.cfg.model,
            "local": False,
            "needs_signin": True,
        }

    async def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
        on_thinking: Callable[[str], None],
    ) -> Turn:
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.cfg.effort},
        }
        if tools:
            kwargs["tools"] = tools

        async with self.client.messages.stream(messages=messages, **kwargs) as stream:
            async for event in stream:
                if event.type != "content_block_delta":
                    continue
                if event.delta.type == "text_delta":
                    on_text(event.delta.text)
                elif event.delta.type == "thinking_delta":
                    on_thinking(event.delta.thinking)
            final = await stream.get_final_message()

        details = getattr(final, "stop_details", None)
        return Turn(
            content=final.content,
            stop_reason=final.stop_reason or "end_turn",
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            refusal=getattr(details, "explanation", "") or "",
        )
