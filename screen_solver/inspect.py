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
import time
from contextlib import contextmanager
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

# Panels that open fine but hold nothing about the problem — or, in the case
# of the site's own worked answer, so much that the model would copy it
# instead of solving anything.
IRRELEVANT = (
    "ask", "chat", "discuss", "comment", "submission", "leaderboard",
    "profile", "settings", "editorial", "video", "premium", "upgrade",
    "solution", "answer",
)


def _rank(label: str) -> int:
    """Lower sorts first. -1 means: do not click this at all."""
    low = label.lower().strip()
    if not low or len(low) > 60:
        return -1
    if any(word in low for word in DESTRUCTIVE):
        return -1
    if any(word in low for word in IRRELEVANT):
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

    Panels whose name promises problem context ("Schema & data", "Test cases")
    are always preferred; unnamed ones are opened only to fill the remaining
    budget, since a tab nobody can identify is as likely to navigate away as
    to help.
    """
    active = ""
    seen: set[str] = set()
    named: list[tuple[int, int, str]] = []
    unnamed: list[tuple[int, int, str]] = []
    generic = len(PANEL_KEYWORDS)

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
        (unnamed if score >= generic else named).append((score, order, label))

    named.sort()
    unnamed.sort()
    chosen = named[:limit]
    if len(chosen) < limit:
        chosen += unnamed[: limit - len(chosen)]

    return {"active": active, "tabs": [label for _, _, label in chosen]}


class ScratchTab:
    """A background copy of the front tab, for looking without touching.

    Panels on modern sites are unmounted while inactive — the schema simply is
    not in the DOM until its tab is clicked — so reading one means activating
    it. Doing that in the tab the user is watching makes the page flicker
    through four panels mid-solve. Doing it in a duplicate opened behind their
    back has the same effect on the DOM and none on what they see.

    Chromium only: Safari's AppleScript cannot run JavaScript in a tab that is
    not frontmost, so callers fall back to the visible pass there.
    """

    def __init__(self, app: str, index: int) -> None:
        self.app = app
        self.index = index

    def run(self, js: str, timeout: float = 25.0) -> str:
        payload = _as_string(js)
        return _osascript(
            f'tell application "{self.app}" to execute tab {self.index} '
            f'of front window javascript "{payload}"',
            timeout=timeout,
        )

    def ready(self, attempts: int = 20) -> bool:
        for _ in range(attempts):
            try:
                if self.run('document.readyState', timeout=8) == "complete":
                    return True
            except InspectError:
                pass
            time.sleep(0.4)
        return False

    def harvest(self) -> dict:
        raw = self.run((JS_DIR / "harvest.js").read_text())
        if not raw:
            raise InspectError("the scratch tab returned nothing")
        return json.loads(raw)

    def click(self, label: str) -> dict:
        js = (JS_DIR / "click.js").read_text().replace(
            "__TARGET__", label.replace("\\", "\\\\").replace('"', '\\"')
        )
        try:
            return json.loads(self.run(js) or "{}")
        except json.JSONDecodeError:
            return {"ok": False}


@contextmanager
def scratch_tab(browser: str | None = None):
    """Open a background duplicate of the front tab; always close it again."""
    app = pick_browser(browser)
    if app not in CHROMIUM:
        raise InspectError(
            f"{app} cannot run JavaScript in a background tab. "
            "Use Chrome, Brave, Edge or Arc for quiet exploring."
        )

    url = _osascript(f'tell application "{app}" to get URL of active tab of front window')
    home = _osascript(f'tell application "{app}" to get active tab index of front window')

    _osascript(
        f'tell application "{app}" to make new tab at end of tabs of front window '
        f'with properties {{URL:"{url}"}}'
    )
    index = _osascript(f'tell application "{app}" to get count of tabs of front window')
    # A new tab steals focus; hand it straight back before anything renders.
    _osascript(f'tell application "{app}" to set active tab index of front window to {home}')

    try:
        yield ScratchTab(app, int(index))
    finally:
        for script in (
            f'tell application "{app}" to close tab {index} of front window',
            f'tell application "{app}" to set active tab index of front window to {home}',
        ):
            try:
                _osascript(script, timeout=8)
            except (InspectError, subprocess.SubprocessError):
                pass


def quiet_explore(browser: str | None = None, limit: int = 4) -> dict:
    """Read every worthwhile panel without disturbing the user's tab.

    Returns the combined page text and which panels it came from.
    """
    with scratch_tab(browser) as tab:
        if not tab.ready():
            raise InspectError("the page did not finish loading in the background tab")

        first = tab.harvest()
        plan = tab_plan(first, limit=limit)
        sections = [(plan.get("active") or "current panel", summarize_for_model(first))]
        opened, failed = [], []

        for label in plan["tabs"]:
            if not tab.click(label).get("ok"):
                failed.append(label)
                continue
            time.sleep(0.7)
            try:
                sections.append((label, summarize_for_model(tab.harvest())))
                opened.append(label)
            except (InspectError, json.JSONDecodeError):
                failed.append(label)

    text = "\n\n".join(
        f"--- PANEL: {name} ---\n{body}" for name, body in sections if body.strip()
    )
    return {
        "text": text,
        "panels": opened,
        "failed": failed,
        "chars": len(text),
        "active": plan.get("active", ""),
    }


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
