"""Model backends. One of these is chosen from the config at startup."""

from __future__ import annotations

from .base import Backend, TextBlock, ToolUseBlock, Turn

__all__ = ["Backend", "TextBlock", "ToolUseBlock", "Turn", "build_backend"]


def build_backend(cfg) -> Backend:
    if cfg.provider == "openai":
        from .openai_backend import OpenAIBackend

        return OpenAIBackend(cfg)

    from .anthropic_backend import AnthropicBackend

    return AnthropicBackend(cfg)
