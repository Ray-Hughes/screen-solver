"""FastAPI app: capture endpoints, SSE event stream, and the viewer."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import auth, capture, inspect as page_inspect, settings as settings_store
from .analyze import Solver
from .backends import build_backend
from .config import Config
from .events import EventBus
from .store import ShotStore

STATIC = Path(__file__).resolve().parent / "static"
JS_DIR = Path(__file__).resolve().parent / "js"


@dataclass
class WatchState:
    enabled: bool = False
    interval: float = 2.0
    threshold: int = 6
    auto_analyze: bool = False
    mode: str = "auto"
    language: str = ""
    task: asyncio.Task | None = None
    last_committed: int | None = None
    pending: int | None = None


def create_app(cfg: Config) -> FastAPI:
    bus = EventBus()
    store = ShotStore(cfg.shots_dir, keep=cfg.keep_shots)
    solver = Solver(cfg, store, bus)
    watch = WatchState(interval=cfg.watch_interval)
    displays: list[dict[str, Any]] = []
    settings = {"capture_display": cfg.capture_display}
    login: dict[str, Any] = {"task": None, "session": None}
    capture_error: dict[str, str | None] = {"message": None}

    async def probe_displays() -> None:
        displays.clear()
        capture_error["message"] = None
        if cfg.desktop:
            # The Electron shell enumerates and captures displays itself, so
            # that macOS attributes Screen Recording to the app bundle rather
            # than to the Python child process. It pushes the list to us.
            return
        try:
            found = await asyncio.to_thread(capture.list_displays)
            displays.extend(
                {"index": d.index, "width": d.width, "height": d.height, "label": d.label}
                for d in found
            )
        except capture.CaptureError as exc:
            capture_error["message"] = str(exc)
            displays.append(
                {"index": 1, "width": 0, "height": 0, "label": "Display 1 — unavailable"}
            )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await probe_displays()
        yield
        if watch.task:
            watch.task.cancel()

    app = FastAPI(title="screen-solver", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---------------------------------------------------------------- #

    def _shot_or_404(shot_id: str | None):
        shot = store.get(shot_id) if shot_id else store.latest()
        if shot is None:
            raise HTTPException(404, "No such shot. Capture one first.")
        return shot

    async def _do_capture(display: int | None = None):
        idx = display or settings["capture_display"]
        png = await asyncio.to_thread(capture.grab_png, idx)
        shot = store.add(png, idx)
        watch.last_committed = shot.ahash
        bus.publish("shot", shot.meta())
        return shot

    # ---------------------------------------------------------------- #

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC / "index.html").read_text()

    @app.get("/api/health")
    async def health():
        """Readiness probe — the desktop shell polls this before showing UI."""
        return {"ok": True, "displays": len(displays), "desktop": cfg.desktop}

    @app.get("/api/state")
    async def state():
        return {
            "displays": displays,
            "capture_display": settings["capture_display"],
            "capture_error": capture_error["message"],
            "model": solver.cfg.model,
            "effort": solver.cfg.effort,
            "provider": solver.backend.describe(),
            "busy": solver.busy(),
            "auth": auth.status().to_dict(),
            "shots": store.list(),
            "watch": {
                "enabled": watch.enabled,
                "interval": watch.interval,
                "threshold": watch.threshold,
                "auto_analyze": watch.auto_analyze,
            },
            "port": cfg.port,
            "desktop": cfg.desktop,
        }

    @app.post("/api/displays/push")
    async def push_displays(body: dict = Body(...)):
        """The desktop shell reports the displays it can see."""
        displays.clear()
        displays.extend(body.get("displays") or [])
        capture_error["message"] = body.get("error") or None
        bus.publish(
            "displays",
            {"displays": displays, "capture_error": capture_error["message"]},
        )
        return {"ok": True}

    @app.post("/api/capture/push")
    async def push_capture(request: Request):
        """Accept a PNG captured by the desktop shell and treat it as a shot."""
        png = await request.body()
        if not png:
            raise HTTPException(400, "empty image")
        q = request.query_params
        shot = store.add(png, int(q.get("display") or settings["capture_display"]))
        watch.last_committed = shot.ahash
        bus.publish("shot", shot.meta())

        if q.get("analyze") == "1":
            solver.start(
                solver.analyze(
                    shot,
                    mode=q.get("mode", "auto"),
                    language=q.get("language", ""),
                    hint=q.get("hint", ""),
                    region=_region(_json_or_none(q.get("region"))),
                )
            )
        return shot.meta()

    @app.post("/api/displays/refresh")
    async def refresh_displays():
        """Re-probe after the user grants Screen Recording.

        In desktop mode the shell re-probes and pushes; here we only report
        what we currently hold.
        """
        await probe_displays()
        return {"displays": displays, "capture_error": capture_error["message"]}

    @app.post("/api/settings")
    async def update_settings(body: dict = Body(...)):
        if "capture_display" in body:
            settings["capture_display"] = int(body["capture_display"])
        return {"ok": True, "capture_display": settings["capture_display"]}

    @app.post("/api/capture")
    async def do_capture(body: dict = Body(default={})):
        if cfg.desktop:
            raise HTTPException(
                409,
                "Running under the desktop shell — capture goes through it so "
                "macOS attributes Screen Recording to the app.",
            )
        try:
            shot = await _do_capture(body.get("display"))
        except capture.CaptureError as exc:
            raise HTTPException(500, str(exc))

        if body.get("analyze"):
            solver.start(
                solver.analyze(
                    shot,
                    mode=body.get("mode", "auto"),
                    language=body.get("language", ""),
                    hint=body.get("hint", ""),
                    region=_region(body.get("region")),
                )
            )
        return shot.meta()

    @app.get("/api/shots/{shot_id}.png")
    async def shot_png(shot_id: str):
        return Response(_shot_or_404(shot_id).png, media_type="image/png")

    @app.get("/api/shots/{shot_id}/thumb.jpg")
    async def shot_thumb(shot_id: str):
        return Response(_shot_or_404(shot_id).thumb, media_type="image/jpeg")

    @app.post("/api/analyze")
    async def analyze(body: dict = Body(default={})):
        shot = _shot_or_404(body.get("shot_id"))
        solver.start(
            solver.analyze(
                shot,
                mode=body.get("mode", "auto"),
                language=body.get("language", ""),
                hint=body.get("hint", ""),
                allow_tools=body.get("tools", True),
                region=_region(body.get("region")),
            )
        )
        return {"ok": True, "shot_id": shot.id}

    @app.post("/api/followup")
    async def followup(body: dict = Body(...)):
        shot = _shot_or_404(body.get("shot_id"))
        question = (body.get("question") or "").strip()
        if not question:
            raise HTTPException(400, "question is required")
        if not shot.messages:
            raise HTTPException(400, "Analyze this shot before asking follow-ups.")
        solver.start(solver.followup(shot, question))
        return {"ok": True}

    @app.post("/api/cancel")
    async def cancel():
        solver.cancel()
        return {"ok": True}

    # ------------------------------ auth ------------------------------ #

    @app.get("/api/auth")
    async def auth_status():
        return auth.status().to_dict()

    @app.post("/api/auth/login")
    async def auth_login(body: dict = Body(default={})):
        """Kick off `ant auth login`; the browser does the rest.

        Returns immediately — the CLI blocks until the OAuth callback lands (or
        until a code is pasted back), so progress arrives on the event stream.
        """
        task = login["task"]
        if task and not task.done():
            raise HTTPException(409, "A sign-in is already in progress.")
        if not auth.ant_bin():
            raise HTTPException(400, auth.INSTALL_HINT)

        loop = asyncio.get_running_loop()

        def on_event(kind: str, payload: dict) -> None:
            loop.call_soon_threadsafe(
                bus.publish,
                "auth_pending",
                {
                    "message": payload.get("message") or payload.get("text", ""),
                    "url": payload.get("url"),
                    "needs_code": kind == "needs_code",
                },
            )

        session = auth.LoginSession(profile=body.get("profile"), on_event=on_event)
        login["session"] = session

        async def run():
            bus.publish(
                "auth_pending",
                {"message": "Opening your browser — finish signing in there."},
            )
            try:
                st = await asyncio.to_thread(session.run)
            except auth.AuthError as exc:
                bus.publish("auth_error", {"message": str(exc)})
                return
            finally:
                login["session"] = None
            solver.reset_client()
            bus.publish("auth", st.to_dict())

        login["task"] = asyncio.create_task(run())
        return {"ok": True, "pending": True}

    @app.post("/api/auth/code")
    async def auth_code(body: dict = Body(...)):
        """Hand a pasted authorization code to the waiting sign-in."""
        session = login["session"]
        if session is None:
            raise HTTPException(409, "No sign-in is waiting for a code.")
        code = (body.get("code") or "").strip()
        if not code:
            raise HTTPException(400, "code is required")
        try:
            await asyncio.to_thread(session.submit_code, code)
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc))
        bus.publish("auth_pending", {"message": "Code submitted — finishing…"})
        return {"ok": True}

    @app.post("/api/auth/cancel")
    async def auth_cancel():
        session = login["session"]
        if session is not None:
            session.cancel()
        return {"ok": True}

    @app.post("/api/auth/logout")
    async def auth_logout(body: dict = Body(default={})):
        try:
            st = await asyncio.to_thread(auth.logout, bool(body.get("all")))
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc))
        solver.reset_client()
        bus.publish("auth", st.to_dict())
        return st.to_dict()

    # ----------------------------- settings ---------------------------- #

    @app.get("/api/config")
    async def get_config():
        return settings_store.describe(solver.cfg)

    @app.get("/api/models")
    async def get_models(provider: str | None = None, base_url: str | None = None):
        """What a server can run — populates the model picker.

        `provider`/`base_url` let the settings form preview a provider it has
        not saved yet; without them this answers for the running config.
        """
        try:
            return await settings_store.list_models(solver.cfg, provider, base_url)
        except settings_store.SettingsError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            _, url = settings_store.resolve_target(solver.cfg, provider, base_url)
            raise HTTPException(502, f"Could not reach {url or 'the provider'}: {exc}")

    @app.post("/api/config")
    async def set_config(body: dict = Body(...)):
        """Persist a settings change and adopt it without a restart."""
        nonlocal cfg
        try:
            new_cfg, stale = await asyncio.to_thread(settings_store.apply, body)
        except settings_store.SettingsError as exc:
            raise HTTPException(400, str(exc))

        cfg = new_cfg
        # The solver owns the model, so swapping its config and backend is the
        # whole of a live provider change.
        solver.cfg = new_cfg
        solver.backend = build_backend(new_cfg)
        watch.interval = new_cfg.watch_interval
        store.keep = new_cfg.keep_shots

        payload = {
            "config": settings_store.describe(new_cfg),
            "provider": solver.backend.describe(),
            "model": new_cfg.model,
            "effort": new_cfg.effort,
            "restart_required": stale,
        }
        bus.publish("config", payload)
        return payload

    # ------------------------- page inspection ------------------------ #

    @app.post("/api/inspect")
    async def inspect_now(body: dict = Body(default={})):
        """Manually pull the live DOM and attach it to a shot."""
        shot = _shot_or_404(body.get("shot_id"))
        try:
            data = await asyncio.to_thread(page_inspect.harvest, body.get("browser"))
        except page_inspect.InspectError as exc:
            raise HTTPException(400, str(exc))
        text = page_inspect.summarize_for_model(data)
        shot.page_context = text
        bus.publish("page_context", {"shot_id": shot.id, "chars": len(text)})
        return {"ok": True, "chars": len(text), "url": data.get("url"), "browser": data.get("browser")}

    @app.post("/api/context")
    async def push_context(request: Request):
        """Bookmarklet fallback for browsers that cannot be scripted."""
        raw = (await request.body()).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"visible": raw}
        shot = store.latest()
        if shot is None:
            shot = await _do_capture()
        text = page_inspect.summarize_for_model(data)
        shot.page_context = text
        bus.publish("page_context", {"shot_id": shot.id, "chars": len(text)})
        return JSONResponse(
            {"ok": True, "chars": len(text)},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.get("/bookmarklet.js", response_class=PlainTextResponse)
    async def bookmarklet():
        """The harvest script as a javascript: URL that POSTs back to us.

        mode:'no-cors' with a text/plain body dodges the CORS preflight, and
        http://127.0.0.1 counts as a trustworthy origin, so this works from
        https pages too.
        """
        harvest = (JS_DIR / "harvest.js").read_text().rstrip().rstrip(";")
        code = (
            "(function(){var payload=" + harvest + ";"
            f"fetch('http://127.0.0.1:{cfg.port}/api/context',"
            "{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain'},"
            "body:payload}).then(function(){},function(){});})()"
        )
        # Collapse newlines and escape the characters a URL would eat.
        code = " ".join(line.strip() for line in code.splitlines())
        code = code.replace("%", "%25").replace("#", "%23")
        return "javascript:" + code

    # ----------------------------- watch ------------------------------ #

    async def watch_loop():
        while watch.enabled:
            await asyncio.sleep(watch.interval)
            if not watch.enabled or solver.busy():
                continue
            try:
                png = await asyncio.to_thread(capture.grab_png, settings["capture_display"])
            except capture.CaptureError as exc:
                bus.publish("watch_error", {"message": str(exc)})
                continue

            h = capture.ahash(png)
            if watch.last_committed is not None and \
                    capture.hamming(h, watch.last_committed) <= watch.threshold:
                watch.pending = None
                continue

            # Require two consecutive similar frames so we do not fire
            # mid-scroll or mid-animation.
            if watch.pending is None or capture.hamming(h, watch.pending) > 2:
                watch.pending = h
                continue

            watch.pending = None
            watch.last_committed = h
            shot = store.add(png, settings["capture_display"])
            bus.publish("shot", shot.meta())
            if watch.auto_analyze:
                solver.start(
                    solver.analyze(shot, mode=watch.mode, language=watch.language)
                )

    @app.post("/api/watch")
    async def set_watch(body: dict = Body(...)):
        watch.interval = float(body.get("interval", watch.interval))
        watch.threshold = int(body.get("threshold", watch.threshold))
        watch.auto_analyze = bool(body.get("auto_analyze", watch.auto_analyze))
        watch.mode = body.get("mode", watch.mode)
        watch.language = body.get("language", watch.language)
        enabled = bool(body.get("enabled", watch.enabled))

        if enabled and not watch.enabled:
            watch.enabled = True
            latest = store.latest()
            watch.last_committed = latest.ahash if latest else None
            # Under Electron the shell polls and pushes frames instead.
            if not cfg.desktop:
                watch.task = asyncio.create_task(watch_loop())
        elif not enabled and watch.enabled:
            watch.enabled = False
            if watch.task:
                watch.task.cancel()
                watch.task = None

        bus.publish("watch", {"enabled": watch.enabled, "auto_analyze": watch.auto_analyze})
        return {"ok": True, "enabled": watch.enabled}

    # ----------------------------- events ----------------------------- #

    @app.get("/api/events")
    async def events():
        queue = bus.subscribe()

        async def gen():
            yield "retry: 2000\n\n"
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield msg
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _json_or_none(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _region(value: Any) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    try:
        x, y, w, h = (float(value[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0.01 or h <= 0.01:
        return None
    return (x, y, w, h)
