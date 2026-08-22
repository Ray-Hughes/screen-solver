"""Live page inspection.

A screenshot only contains what is painted. Practice sites hide the half you
need behind inactive tabs, <details> blocks and virtualised code editors.
These helpers reach into the frontmost browser tab over Apple Events and pull
the real DOM: hidden tab panels, editor buffers, tables and code blocks — and
can click a tab so the screen can be re-captured with it open.

Requires, one time:
  * Chrome/Brave/Edge/Arc: View → Developer → Allow JavaScript from Apple Events
  * Safari: Develop → Allow JavaScript from Apple Events
  * macOS will prompt once for Automation permission.

A browser-free fallback exists: POST harvested JSON to /api/context (the
viewer hands you a bookmarklet that does exactly that).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent / "js"

CHROMIUM = {
    "Google Chrome": "Google Chrome",
    "Google Chrome Canary": "Google Chrome Canary",
    "Brave Browser": "Brave Browser",
    "Microsoft Edge": "Microsoft Edge",
    "Arc": "Arc",
    "Vivaldi": "Vivaldi",
    "Chromium": "Chromium",
    "Dia": "Dia",
}
SAFARI = {"Safari", "Safari Technology Preview", "Webkit"}


class InspectError(RuntimeError):
    pass


@dataclass
class PageContext:
    """Whatever we most recently learned about the live page."""

    source: str = "none"
    url: str = ""
    title: str = ""
    data: dict | None = None

    def is_empty(self) -> bool:
        return not self.data


def _osascript(script: str, timeout: float = 25.0) -> str:
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise InspectError((proc.stderr or "osascript failed").strip())
    return proc.stdout.strip()


def frontmost_app() -> str:
    try:
        return _osascript(
            'tell application "System Events" to get name of first process '
            "whose frontmost is true",
            timeout=8,
        )
    except (InspectError, subprocess.SubprocessError):
        return ""


def _as_string(js: str) -> str:
    """Escape a JS payload so it survives as an AppleScript string literal."""
    return js.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _run_js(app: str, js: str, timeout: float = 25.0) -> str:
    payload = _as_string(js)
    if app in CHROMIUM:
        script = (
            f'tell application "{app}" to execute active tab of front window '
            f'javascript "{payload}"'
        )
    elif app in SAFARI:
        script = (
            f'tell application "{app}" to do JavaScript "{payload}" '
            "in current tab of front window"
        )
    else:
        raise InspectError(
            f"{app or 'The frontmost app'} is not a scriptable browser. "
            "Use the bookmarklet fallback (see the viewer's Inspect panel) "
            "or bring the browser to the front."
        )
    try:
        return _osascript(script, timeout=timeout)
    except InspectError as exc:
        msg = str(exc)
        if "not allowed" in msg.lower() or "-1743" in msg or "1728" in msg:
            raise InspectError(
                f"{app} refused the Apple Event. Enable "
                "'Allow JavaScript from Apple Events' in its Develop/Developer "
                "menu, and approve the Automation prompt."
            ) from exc
        raise


def pick_browser(preferred: str | None = None) -> str:
    """Choose which browser to talk to: explicit > frontmost > first running."""
    if preferred:
        return preferred
    front = frontmost_app()
    if front in CHROMIUM or front in SAFARI:
        return front
    for app in list(CHROMIUM) + sorted(SAFARI):
        try:
            running = _osascript(
                f'tell application "System Events" to (name of processes) '
                f'contains "{app}"',
                timeout=8,
            )
        except (InspectError, subprocess.SubprocessError):
            continue
        if running == "true":
            return app
    raise InspectError("No scriptable browser is running.")


def harvest(browser: str | None = None) -> dict:
    app = pick_browser(browser)
    raw = _run_js(app, (JS_DIR / "harvest.js").read_text())
    if not raw:
        raise InspectError(f"{app} returned nothing — is a page loaded?")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InspectError(f"Could not parse page data from {app}: {raw[:200]}") from exc
    data["browser"] = app
    return data


def click(target: str, browser: str | None = None) -> dict:
    app = pick_browser(browser)
    js = (JS_DIR / "click.js").read_text().replace(
        "__TARGET__", target.replace("\\", "\\\\").replace('"', '\\"')
    )
    raw = _run_js(app, js)
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": raw[:200]}


# Panels that usually hold the half of the problem a screenshot cannot show.
# Ordered: the earlier the word, the more it is worth opening.
PANEL_KEYWORDS = (
    "schema", "table", "data", "sample", "example", "constraint", "input",
    "output", "test", "spec", "detail", "hint", "note", "definition", "column",
)

# Controls that do something rather than reveal something. Clicking these
# during an explore pass would submit an answer or navigate away.
DESTRUCTIVE = (
    "submit", "run", "save", "delete", "sign in", "sign up", "log in", "logout",
    "buy", "checkout", "next question", "skip", "reset", "clear", "close",
    "cancel", "back", "continue", "finish", "give up", "reveal answer",
)


def _rank(label: str) -> int:
    """Lower sorts first. -1 means: do not click this at all."""
    low = label.lower().strip()
    if not low or len(low) > 60:
        return -1
    if any(word in low for word in DESTRUCTIVE):
        return -1
    for i, word in enumerate(PANEL_KEYWORDS):
        if word in low:
            return i
    return len(PANEL_KEYWORDS)


def tab_plan(data: dict, limit: int = 4) -> dict:
    """Which panels an explore pass should open, and what is open right now.

    Only tab-like controls are considered — a plain button is as likely to
    submit the answer as to reveal a schema, and this runs without the model
    in the loop, so it has to be conservative.
    """
    active = ""
    seen: set[str] = set()
    ranked: list[tuple[int, int, str]] = []

    for order, item in enumerate(data.get("clickables") or []):
        label = (item.get("label") or "").strip()
        if not label:
            continue
        key = label.lower()
        if item.get("active") and item.get("tab") and not active:
            active = label
        if key in seen or not item.get("tab") or item.get("active"):
            seen.add(key)
            continue
        seen.add(key)
        score = _rank(label)
        if score < 0:
            continue
        ranked.append((score, order, label))

    ranked.sort()
    return {"active": active, "tabs": [label for _, _, label in ranked[:limit]]}


def summarize_for_model(data: dict, max_chars: int = 24000) -> str:
    """Render harvested page data into something compact and readable."""
    parts: list[str] = []
    if data.get("title") or data.get("url"):
        parts.append(f"PAGE: {data.get('title', '')}\nURL: {data.get('url', '')}")

    visible = (data.get("visible") or "").strip()
    if visible:
        parts.append("--- VISIBLE TEXT ---\n" + visible)

    hidden_panels = [p for p in data.get("panels", []) if p.get("hidden")]
    shown_panels = [p for p in data.get("panels", []) if not p.get("hidden")]
    for label, group in (("HIDDEN / INACTIVE PANELS", hidden_panels),
                         ("OTHER PANELS", shown_panels)):
        if not group:
            continue
        chunk = [f"--- {label} ---"]
        for p in group:
            chunk.append(f"[{p.get('name', '?')}]\n{p.get('text', '')}")
        parts.append("\n\n".join(chunk))

    editors = data.get("editors") or []
    if editors:
        chunk = ["--- EDITOR BUFFERS (what is typed in the code panes) ---"]
        for e in editors:
            chunk.append(f"[{e.get('kind')}]\n{e.get('value', '')}")
        parts.append("\n\n".join(chunk))

    tables = data.get("tables") or []
    if tables:
        chunk = ["--- TABLES ---"]
        for rows in tables:
            chunk.append("\n".join(" | ".join(r) for r in rows))
        parts.append("\n\n".join(chunk))

    blocks = data.get("code_blocks") or []
    if blocks:
        parts.append("--- CODE / PRE BLOCKS ---\n" + "\n\n".join(blocks))

    clickables = data.get("clickables") or []
    if clickables:
        labels = ", ".join(sorted({c["label"] for c in clickables if c.get("label")}))
        parts.append("--- CLICKABLE LABELS (tabs/buttons you can open) ---\n" + labels)

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[...truncated...]"
    return text
