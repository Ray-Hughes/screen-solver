"""The solving loop: screenshot in, streamed breakdown out.

The model gets the screenshot plus three tools for reaching past the pixels —
reading the live DOM, opening a hidden tab and re-capturing, and taking a
fresh screenshot. Text and reasoning stream to the viewer as they arrive.

Which model that is comes from the backend (see .backends): Claude by default,
or anything OpenAI-compatible — including a local Ollama or LM Studio.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Callable

from . import capture, errors, inspect as page_inspect, prompts, verify as verifier
from .backends import build_backend
from .config import Config
from .events import EventBus
from .store import Shot, ShotStore

MAX_TOOL_ROUNDS = 6
# Two goes at fixing a solution that will not run. Past that the model is
# usually cycling between the same two wrong answers, and saying so is more
# use than a third attempt.
MAX_REPAIRS = 2

TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_page",
        "description": (
            "Read the live DOM of the frontmost browser tab. Returns the "
            "visible text, the contents of HIDDEN and inactive tab panels "
            "(schema tabs, examples, constraints, collapsed details), the "
            "full buffers of any code editor on the page (including lines "
            "scrolled out of view), tables, code blocks, and the labels of "
            "clickable tabs and buttons. Use this whenever the screenshot "
            "does not show you a schema, sample data, examples, constraints "
            "or the complete editor contents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "What you are missing from the screenshot.",
                }
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_and_capture",
        "description": (
            "Click an element on the page by its visible label (a tab such as "
            "'Schema & data', a button, or a <details> summary), then re-read "
            "the DOM and take a fresh silent screenshot. Use when a panel is "
            "rendered only after it is opened."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": (
                        "Visible text of the thing to click, or a CSS selector."
                    ),
                },
                "reason": {"type": "string"},
            },
            "required": ["label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "recapture_screen",
        "description": (
            "Take a fresh silent screenshot of the display. Use for "
            "non-browser applications, or to see the result of a change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
    },
]


def _replace_solution(markdown: str, code: str) -> str:
    """Swap the body of the ## Solution fence, leaving the rest of the answer."""
    section = verifier.SOLUTION_RE.search(markdown)
    if not section:
        return markdown
    body = section.group(1)
    fence = verifier.CODE_FENCE_RE.search(body)
    if not fence:
        return markdown
    replaced = body[: fence.start(2)] + code.rstrip() + "\n" + body[fence.end(2):]
    return markdown[: section.start(1)] + replaced + markdown[section.end(1):]


def image_block(png: bytes, max_edge: int) -> dict[str, Any]:
    data, media_type = capture.prepare_for_api(png, max_edge=max_edge)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


class Solver:
    def __init__(self, cfg: Config, store: ShotStore, bus: EventBus) -> None:
        self.cfg = cfg
        self.store = store
        self.bus = bus
        self.backend = build_backend(cfg)
        self._task: asyncio.Task | None = None

    def reset_client(self) -> None:
        """Drop the cached client — signing in mid-session takes effect at once."""
        self.backend.reset()

    # ------------------------------------------------------------------ #

    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def cancel(self) -> None:
        if self.busy():
            self._task.cancel()

    def start(self, coro) -> None:
        self.cancel()
        self._task = asyncio.create_task(coro)
        self._task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.bus.publish("analysis_error", errors.classify(exc).to_dict())

    # ------------------------------------------------------------------ #

    async def analyze(
        self,
        shot: Shot,
        *,
        mode: str = "auto",
        language: str = "",
        hint: str = "",
        allow_tools: bool = True,
        region: tuple[float, float, float, float] | None = None,
    ) -> None:
        png = capture.crop_normalized(shot.png, region) if region else shot.png

        user_content: list[dict[str, Any]] = [image_block(png, self.cfg.max_edge)]

        # Supporting captures go between the screenshot and the instructions,
        # each announced by name, so the model knows a second image is another
        # panel of the same problem rather than a different problem.
        for i, sup in enumerate(shot.supports, 1):
            user_content.append(
                {
                    "type": "text",
                    "text": f"Supporting capture {i} of {len(shot.supports)} "
                            f"\u2014 the \"{sup.label}\" panel of the same page:",
                }
            )
            user_content.append(image_block(sup.png, self.cfg.support_max_edge))

        user_content.append(
            {
                "type": "text",
                "text": prompts.user_block(
                    mode=mode,
                    language=language,
                    hint=hint,
                    page_context=shot.page_context,
                    supports=[s.label for s in shot.supports],
                ),
            }
        )
        shot.messages = [{"role": "user", "content": user_content}]
        shot.analysis = ""

        self.bus.publish("analysis_start", {"shot_id": shot.id, "mode": mode})
        await self._loop(shot, allow_tools=allow_tools)

    async def followup(self, shot: Shot, question: str) -> None:
        if not shot.messages:
            raise ValueError("Nothing has been analyzed for this shot yet.")
        shot.messages.append({"role": "user", "content": question})
        self.bus.publish("followup_start", {"shot_id": shot.id, "question": question})
        await self._loop(shot, allow_tools=True, followup=True)

    # ------------------------------------------------------------------ #

    async def _loop(self, shot: Shot, *, allow_tools: bool, followup: bool = False) -> None:
        system = prompts.SYSTEM + (prompts.FOLLOWUP_SYSTEM_SUFFIX if followup else "")
        tools = TOOLS if allow_tools else None

        collected: list[str] = []

        def on_text(text: str) -> None:
            collected.append(text)
            self.bus.publish("text_delta", {"text": text})

        def on_thinking(text: str) -> None:
            self.bus.publish("thinking_delta", {"text": text})

        async def finish(turn) -> None:
            shot.analysis = "".join(collected)
            await self._repair(shot, system)
            self.bus.publish(
                "analysis_done",
                {
                    "shot_id": shot.id,
                    "stop_reason": turn.stop_reason,
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                },
            )

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                turn = await self.backend.stream(
                    system=system,
                    messages=shot.messages,
                    tools=tools,
                    on_text=on_text,
                    on_thinking=on_thinking,
                )

                shot.messages.append({"role": "assistant", "content": turn.content})

                if turn.stop_reason == "refusal":
                    self.bus.publish(
                        "analysis_error",
                        errors.simple(
                            "refusal",
                            "The model declined this request",
                            turn.refusal or "No explanation was given.",
                            hint="Try a narrower region, or rephrase the note to the model.",
                        ),
                    )
                    return

                if turn.stop_reason != "tool_use":
                    await finish(turn)
                    return

                results = []
                for block in turn.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    results.append(await self._run_tool(shot, block))

                if not results:
                    # A tool_use stop with no usable call — some local models
                    # do this. Take what was said rather than looping on it.
                    await finish(turn)
                    return

                shot.messages.append({"role": "user", "content": results})

            self.bus.publish(
                "analysis_error",
                errors.simple(
                    "tool_loop",
                    "Gave up looking for more context",
                    f"The model used {MAX_TOOL_ROUNDS} rounds of tools without "
                    "settling on an answer.",
                    hint="Try cropping to just the problem, or use Inspect front "
                         "tab first so the page context is already attached.",
                    retryable=True,
                ),
            )
        except asyncio.CancelledError:
            self.bus.publish("analysis_cancelled", {"shot_id": shot.id})
            raise
        except Exception as exc:
            # One path for every failure, so the dashboard always gets a
            # title, an explanation and (where one exists) a next step —
            # never a raw traceback.
            self.bus.publish("analysis_error", errors.classify(exc).to_dict())

    # ------------------------------------------------------------------ #

    async def _repair(self, shot: Shot, system: str) -> None:
        """Run the answer, and if it fails, make the model fix it.

        The point is that a solution which does not execute never reaches the
        screen looking finished. Verification needs no model — the schema came
        off the page — so a wrong answer is caught for free; only the fix costs
        a round trip.
        """
        for attempt in range(1, MAX_REPAIRS + 1):
            result = await asyncio.to_thread(
                verifier.verify, shot.analysis, shot.page_context
            )
            self.bus.publish(
                "verify",
                {**result.to_dict(), "shot_id": shot.id, "attempt": attempt},
            )
            if result.ok or not result.ran:
                return

            language, code = verifier.solution_code(shot.analysis)
            self.bus.publish(
                "repair", {"shot_id": shot.id, "attempt": attempt, "error": result.error}
            )

            fixed: list[str] = []
            shot.messages.append(
                {
                    "role": "user",
                    "content": prompts.REPAIR.format(
                        error=result.error, language=language or "", code=code
                    ),
                }
            )
            turn = await self.backend.stream(
                system=system,
                messages=shot.messages,
                tools=None,
                on_text=fixed.append,
                on_thinking=lambda t: self.bus.publish("thinking_delta", {"text": t}),
            )
            shot.messages.append({"role": "assistant", "content": turn.content})

            _, new_code = verifier.solution_code("## Solution\n" + "".join(fixed))
            if not new_code.strip():
                new_code = verifier.CODE_FENCE_RE.search("".join(fixed))
                new_code = new_code.group(2) if new_code else ""
            if not new_code.strip():
                return  # nothing usable came back; leave the original in place

            shot.analysis = _replace_solution(shot.analysis, new_code)
            self.bus.publish(
                "solution_fixed",
                {"shot_id": shot.id, "language": language, "code": new_code},
            )

        final = await asyncio.to_thread(verifier.verify, shot.analysis, shot.page_context)
        self.bus.publish(
            "verify", {**final.to_dict(), "shot_id": shot.id, "attempt": MAX_REPAIRS + 1}
        )

    async def _run_tool(self, shot: Shot, block: Any) -> dict[str, Any]:
        name = block.name
        args = block.input or {}
        self.bus.publish("tool_start", {"name": name, "input": args})

        try:
            content = await self._dispatch(shot, name, args)
            ok = True
        except Exception as exc:  # surfaced to the model, not fatal
            content = [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}]
            ok = False

        preview = next(
            (c["text"][:400] for c in content if c.get("type") == "text"), "(image)"
        )
        self.bus.publish("tool_end", {"name": name, "ok": ok, "preview": preview})

        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": content,
            **({"is_error": True} if not ok else {}),
        }

    async def _dispatch(self, shot: Shot, name: str, args: dict) -> list[dict[str, Any]]:
        if name == "read_page":
            data = await asyncio.to_thread(page_inspect.harvest)
            text = page_inspect.summarize_for_model(data)
            shot.page_context = text
            self.bus.publish("page_context", {"shot_id": shot.id, "chars": len(text)})
            return [{"type": "text", "text": text}]

        if name == "open_and_capture":
            label = args.get("label", "")
            result = await asyncio.to_thread(page_inspect.click, label)
            if not result.get("ok"):
                labels = ""
                try:
                    data = await asyncio.to_thread(page_inspect.harvest)
                    labels = ", ".join(
                        sorted({c["label"] for c in data.get("clickables", [])})
                    )
                except page_inspect.InspectError:
                    pass
                return [
                    {
                        "type": "text",
                        "text": (
                            f"Could not click {label!r}: {result.get('error')}. "
                            f"Available labels: {labels or 'unknown'}"
                        ),
                    }
                ]
            await asyncio.sleep(0.7)
            data = await asyncio.to_thread(page_inspect.harvest)
            text = page_inspect.summarize_for_model(data)
            shot.page_context = text

            png = await asyncio.to_thread(capture.grab_png, self.cfg.capture_display)
            new_shot = self.store.add(png, self.cfg.capture_display)
            self.bus.publish("shot", new_shot.meta())
            self.bus.publish("page_context", {"shot_id": shot.id, "chars": len(text)})

            return [
                {"type": "text", "text": f"Clicked {result.get('clicked')!r}. Page now reads:\n\n{text}"},
                image_block(png, self.cfg.max_edge),
                {"type": "text", "text": "Fresh screenshot after the click is above."},
            ]

        if name == "recapture_screen":
            png = await asyncio.to_thread(capture.grab_png, self.cfg.capture_display)
            new_shot = self.store.add(png, self.cfg.capture_display)
            self.bus.publish("shot", new_shot.meta())
            return [
                image_block(png, self.cfg.max_edge),
                {"type": "text", "text": "Fresh screenshot above."},
            ]

        raise ValueError(f"Unknown tool {name!r}")
