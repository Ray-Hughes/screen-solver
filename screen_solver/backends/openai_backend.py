"""OpenAI-compatible backend — Ollama, LM Studio, llama.cpp, vLLM, and friends.

Every local runner worth using speaks the OpenAI chat-completions dialect, so
one translation layer covers all of them. The solver's conversation stays in
Anthropic's block format; this module converts it on the way out and converts
the reply back on the way in.

Two things local models do that the hosted API does not:

  * reasoning models leak their scratchpad into `content` as a <think>…</think>
    span rather than a separate field, so it is split out here and routed to
    the reasoning pane instead of the answer;
  * plenty of vision models have no tool support at all, and say so with a
    plain 400. That downgrades the session to tool-free rather than failing.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .base import Backend, TextBlock, ToolUseBlock, Turn

MISSING_SDK = (
    "The `openai` package is required for local models.\n"
    "    ./run.sh --help  # creates .venv, then:\n"
    "    .venv/bin/pip install 'openai>=1.50'"
)

# Substrings a server uses when it simply does not implement tool calling.
NO_TOOL_MARKERS = (
    "does not support tools",
    "does not support function",
    "tools are not supported",
    "tool calls are not supported",
    "unsupported parameter: 'tools'",
    "unknown parameter: 'tools'",
    "registry does not support tools",
)

# Ollama and some llama.cpp builds 400 on stream_options instead of ignoring it.
NO_USAGE_MARKERS = ("stream_options", "include_usage")

# A text-only model asked to look at a picture.
NO_VISION_MARKERS = (
    "does not support images", "image input", "not support image",
    "vision", "multimodal", "unsupported content type",
)


def _data_url(block: dict[str, Any]) -> dict[str, Any]:
    src = block["source"]
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"},
    }


def _blocks(content: Any) -> list[Any]:
    return content if isinstance(content, list) else []


def _get(block: Any, key: str, default: Any = None) -> Any:
    """Blocks are dicts on the way in and dataclasses on the way back out."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


class ThinkSplitter:
    """Route <think>…</think> spans to the reasoning pane, the rest to the answer.

    Tags can straddle chunk boundaries, so the tail of the buffer that could
    still turn into a tag is held back until the next chunk proves otherwise.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self, on_text: Callable[[str], None], on_thinking: Callable[[str], None]):
        self._on_text = on_text
        self._on_thinking = on_thinking
        self._buf = ""
        self._thinking = False
        self.text: list[str] = []

    def thinking(self, chunk: str) -> None:
        """Reasoning the server already separated out for us."""
        self._on_thinking(chunk)

    def _emit(self, chunk: str) -> None:
        if not chunk:
            return
        if self._thinking:
            self._on_thinking(chunk)
        else:
            self.text.append(chunk)
            self._on_text(chunk)

    @staticmethod
    def _held_back(buf: str, tag: str) -> int:
        for k in range(min(len(tag) - 1, len(buf)), 0, -1):
            if buf.endswith(tag[:k]):
                return k
        return 0

    def feed(self, chunk: str) -> None:
        self._buf += chunk
        while True:
            tag = self.CLOSE if self._thinking else self.OPEN
            at = self._buf.find(tag)
            if at >= 0:
                self._emit(self._buf[:at])
                self._buf = self._buf[at + len(tag) :]
                self._thinking = not self._thinking
                continue
            keep = self._held_back(self._buf, tag)
            if keep < len(self._buf):
                self._emit(self._buf[: len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep :]
            return

    def flush(self) -> None:
        self._emit(self._buf)
        self._buf = ""


class OpenAIBackend(Backend):
    name = "openai"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._client = None
        self.supports_tools = cfg.tools_mode != "off"
        # Usage only arrives on the stream if the server implements this, and
        # not all of them do — see _run's fallback.
        self._ask_for_usage = True
        # Once a page has been explored the text carries the problem, so a
        # text-only model is a perfectly good choice — and usually a stronger
        # one at the same size. Drop the images rather than failing.
        self.sends_images = cfg.vision != "off"

    @property
    def label(self) -> str:
        return f"{self.cfg.model} · local" if self.cfg.local else self.cfg.model

    def reset(self) -> None:
        self._client = None

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "label": self.label,
            "model": self.cfg.model,
            "base_url": self.cfg.base_url,
            "local": self.cfg.local,
            "tools": self.supports_tools,
            "vision": self.sends_images,
            "needs_signin": False,
        }

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - environment issue
                raise RuntimeError(MISSING_SDK) from exc
            self._client = AsyncOpenAI(
                base_url=self.cfg.base_url,
                # Local servers ignore the key but the SDK insists on one.
                api_key=self.cfg.api_key or "local",
                timeout=self.cfg.timeout,
                max_retries=1,
            )
        return self._client

    # ---------------- format translation ---------------- #

    def to_openai(self, system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue

            if role == "assistant":
                texts, calls = [], []
                for b in _blocks(content):
                    kind = _get(b, "type")
                    if kind == "text":
                        texts.append(_get(b, "text", ""))
                    elif kind == "tool_use":
                        calls.append(
                            {
                                "id": _get(b, "id"),
                                "type": "function",
                                "function": {
                                    "name": _get(b, "name"),
                                    "arguments": json.dumps(_get(b, "input") or {}),
                                },
                            }
                        )
                entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
                if calls:
                    entry["tool_calls"] = calls
                out.append(entry)
                continue

            # A user turn: plain parts, plus any tool results, which have to
            # become their own `tool` messages right after the assistant turn
            # that asked for them.
            parts: list[dict[str, Any]] = []
            hoisted: list[dict[str, Any]] = []
            for b in _blocks(content):
                kind = _get(b, "type")
                if kind == "text":
                    parts.append({"type": "text", "text": _get(b, "text", "")})
                elif kind == "image":
                    if self.sends_images:
                        parts.append(_data_url(b))
                elif kind == "tool_result":
                    chunks, images = [], []
                    for piece in _get(b, "content") or []:
                        if _get(piece, "type") == "text":
                            chunks.append(_get(piece, "text", ""))
                        elif _get(piece, "type") == "image":
                            images.append(piece)
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": _get(b, "tool_use_id"),
                            "content": "\n".join(chunks) or "(no textual output)",
                        }
                    )
                    # A `tool` message cannot carry an image, so the screenshot
                    # a tool produced rides along in the next user turn.
                    hoisted.extend(images)

            for image in hoisted:
                if self.sends_images:
                    parts.append(_data_url(image))
            if parts:
                out.append({"role": "user", "content": parts})

        return out

    @staticmethod
    def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    # ---------------- the call ---------------- #

    async def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
        on_thinking: Callable[[str], None],
    ) -> Turn:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": self.to_openai(system, messages),
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
            "stream": True,
        }
        if tools and self.supports_tools:
            payload["tools"] = self.to_openai_tools(tools)
        if self._ask_for_usage:
            payload["stream_options"] = {"include_usage": True}

        # Two optional features, either of which a given server may reject with
        # a flat 400. Drop whichever it complained about and retry — once each,
        # then remember for the rest of the session.
        for _attempt in range(3):
            try:
                return await self._run(payload, on_text, on_thinking)
            except Exception as exc:
                lowered = str(exc).lower()
                if "tools" in payload and any(m in lowered for m in NO_TOOL_MARKERS):
                    self.supports_tools = False
                    payload.pop("tools", None)
                    continue
                if "stream_options" in payload and any(
                    m in lowered for m in NO_USAGE_MARKERS
                ):
                    self._ask_for_usage = False
                    payload.pop("stream_options", None)
                    continue
                if self.sends_images and any(m in lowered for m in NO_VISION_MARKERS):
                    # A text-only model. Resend without the screenshot and
                    # stay that way for the session.
                    self.sends_images = False
                    payload["messages"] = self.to_openai(system, messages)
                    continue
                raise
        return await self._run(payload, on_text, on_thinking)

    async def _consume(self, stream, split, calls) -> tuple[str | None, Any]:
        """Drain one SSE stream. Returns (finish_reason, usage)."""
        finish = None
        usage = None

        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish = choice.finish_reason

            delta = choice.delta
            if delta is None:
                continue

            # vLLM, Ollama and LM Studio all expose reasoning under one of
            # these when the model separates it properly.
            reasoning = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if reasoning:
                split.thinking(reasoning)
            if delta.content:
                split.feed(delta.content)

            for call in delta.tool_calls or []:
                slot = calls.setdefault(call.index, {"id": "", "name": "", "args": ""})
                if call.id:
                    slot["id"] = call.id
                fn = getattr(call, "function", None)
                if fn is None:
                    continue
                if fn.name:
                    slot["name"] = fn.name
                if fn.arguments:
                    slot["args"] += fn.arguments

        return finish, usage

    async def _run(self, payload, on_text, on_thinking) -> Turn:
        split = ThinkSplitter(on_text, on_thinking)
        calls: dict[int, dict[str, str]] = {}

        stream = await self.client.chat.completions.create(**payload)
        try:
            finish, usage = await self._consume(stream, split, calls)
        finally:
            # Without this the SDK's underlying byte stream is torn down by the
            # garbage collector, which logs a noisy GeneratorExit traceback.
            await stream.close()

        split.flush()

        content: list[Any] = []
        text = "".join(split.text)
        if text.strip():
            content.append(TextBlock(text=text))

        for index in sorted(calls):
            slot = calls[index]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                # A truncated or malformed argument blob is worth surfacing to
                # the model rather than crashing the round.
                args = {"_malformed_arguments": slot["args"]}
            content.append(
                ToolUseBlock(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    input=args if isinstance(args, dict) else {"value": args},
                )
            )

        stop = {
            "tool_calls": "tool_use",
            "stop": "end_turn",
            "length": "max_tokens",
            "content_filter": "refusal",
        }.get(finish or "stop", "end_turn")
        if any(isinstance(b, ToolUseBlock) for b in content):
            stop = "tool_use"

        return Turn(
            content=content,
            stop_reason=stop,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
