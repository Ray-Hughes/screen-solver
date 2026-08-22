"""In-memory (plus on-disk) store of captured shots and their conversations."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import capture


@dataclass
class Shot:
    id: str
    ts: float
    display: int
    png: bytes
    thumb: bytes
    width: int
    height: int
    ahash: int
    path: Path | None = None
    # Conversation history for this shot, so follow-ups keep the image context.
    messages: list[dict[str, Any]] = field(default_factory=list)
    analysis: str = ""
    page_context: str = ""

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "display": self.display,
            "width": self.width,
            "height": self.height,
            "has_analysis": bool(self.analysis),
            "has_page_context": bool(self.page_context),
        }


class ShotStore:
    def __init__(self, directory: Path, keep: int = 40) -> None:
        self.dir = directory
        self.keep = keep
        self.dir.mkdir(parents=True, exist_ok=True)
        self._shots: OrderedDict[str, Shot] = OrderedDict()

    def add(self, png: bytes, display: int) -> Shot:
        from PIL import Image
        import io

        with Image.open(io.BytesIO(png)) as im:
            width, height = im.size

        shot_id = uuid.uuid4().hex[:12]
        path = self.dir / f"{int(time.time())}-{shot_id}.png"
        path.write_bytes(png)

        shot = Shot(
            id=shot_id,
            ts=time.time(),
            display=display,
            png=png,
            thumb=capture.thumbnail(png),
            width=width,
            height=height,
            ahash=capture.ahash(png),
            path=path,
        )
        self._shots[shot_id] = shot
        self._evict()
        return shot

    def _evict(self) -> None:
        while len(self._shots) > self.keep:
            _, old = self._shots.popitem(last=False)
            if old.path and old.path.exists():
                old.path.unlink(missing_ok=True)

    def get(self, shot_id: str) -> Shot | None:
        return self._shots.get(shot_id)

    def latest(self) -> Shot | None:
        if not self._shots:
            return None
        return next(reversed(self._shots.values()))

    def list(self) -> list[dict[str, Any]]:
        return [s.meta() for s in reversed(self._shots.values())]
