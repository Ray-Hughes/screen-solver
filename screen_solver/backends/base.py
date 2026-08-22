"""The shape every model backend presents to the solver.

The solver keeps its conversation in Anthropic's message format — content is a
list of blocks, tool calls are `tool_use` blocks, tool output goes back as
`tool_result` blocks. That format is the lingua franca here; a backend that
talks to something else translates on the way in and on the way out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class Turn:
    """One assistant turn, however the backend produced it."""

    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens | refusal
    input_tokens: int = 0
    output_tokens: int = 0
    refusal: str = ""


class Backend:
    """Interface only — see anthropic_backend / openai_backend."""

    name = "backend"
    label = "model"
    #: False once the provider has proved it cannot handle tool definitions.
    supports_tools = True

    def reset(self) -> None:
        """Drop the cached client, e.g. after signing in mid-session."""

    def describe(self) -> dict[str, Any]:
        """Small dict for /api/state, so the dashboard can label itself."""
        return {"provider": self.name, "label": self.label}

    async def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
        on_thinking: Callable[[str], None],
    ) -> Turn:
        raise NotImplementedError
