"""Silent screen capture on macOS.

Everything here shells out to /usr/sbin/screencapture with -x, which
suppresses the shutter sound. There is no flash, no thumbnail animation and
no UI: the only observable effect is a file appearing on disk.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

SCREENCAPTURE = "/usr/sbin/screencapture"
MAX_PROBE_DISPLAYS = 6


def permission_hint() -> str:
    """Name the app macOS will actually list, which differs by how we run."""
    if os.environ.get("SOLVER_DESKTOP") == "1":
        subject = "Screen Solver"
        reopen = "quit Screen Solver from the menu bar and reopen it"
    else:
        subject = (
            "the app that launches the server (Terminal, iTerm, VS Code, ...)"
        )
        reopen = "fully quit and reopen that app"
    return (
        f"macOS denied the screen capture. Grant Screen Recording to {subject} "
        "under System Settings \u2192 Privacy & Security \u2192 Screen Recording, "
        f"then {reopen} \u2014 the permission is only picked up on a fresh launch."
    )


class CaptureError(RuntimeError):
    pass


class PermissionDenied(CaptureError):
    """screencapture ran but macOS refused to hand over the pixels."""


@dataclass(frozen=True)
class Display:
    index: int
    width: int
    height: int

    @property
    def label(self) -> str:
        return f"Display {self.index} — {self.width}×{self.height}"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def grab_png(display: int = 1, region: tuple[int, int, int, int] | None = None) -> bytes:
    """Capture one display (or a pixel region of it) and return PNG bytes.

    -x  silent, no shutter sound
    -r  omit screenshot metadata
    -t  output format
    -D  which display (1-based)
    Cursor is excluded because -C is not passed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "shot.png"
        args = [SCREENCAPTURE, "-x", "-r", "-t", "png"]
        if region:
            x, y, w, h = region
            args += ["-R", f"{x},{y},{w},{h}"]
        else:
            args += ["-D", str(display)]
        args.append(str(out))

        proc = _run(args)
        if not out.exists() or out.stat().st_size == 0:
            stderr = proc.stderr.strip()
            if "could not create image" in stderr.lower():
                raise PermissionDenied(permission_hint())
            raise CaptureError(
                f"screencapture failed for display {display}: "
                f"{stderr or 'no output produced'}"
            )
        return out.read_bytes()


def list_displays() -> list[Display]:
    """Probe for attached displays by capturing a 1px region from each index.

    Cheaper and dependency-free compared to pulling in PyObjC just to call
    CGGetActiveDisplayList. A missing display produces no file, which is how
    we know where to stop.
    """
    found: list[Display] = []
    for idx in range(1, MAX_PROBE_DISPLAYS + 1):
        try:
            data = grab_png(display=idx)
        except PermissionDenied:
            raise
        except (CaptureError, subprocess.SubprocessError):
            break
        with Image.open(io.BytesIO(data)) as im:
            found.append(Display(index=idx, width=im.width, height=im.height))
    if not found:
        raise CaptureError("No displays reported any pixels.")
    return found


def crop_normalized(png: bytes, box: tuple[float, float, float, float]) -> bytes:
    """Crop using fractional coordinates (x, y, w, h) in 0..1 of the image."""
    fx, fy, fw, fh = box
    with Image.open(io.BytesIO(png)) as im:
        W, H = im.size
        left = max(0, int(fx * W))
        top = max(0, int(fy * H))
        right = min(W, int((fx + fw) * W))
        bottom = min(H, int((fy + fh) * H))
        if right - left < 8 or bottom - top < 8:
            return png
        out = io.BytesIO()
        im.crop((left, top, right, bottom)).save(out, format="PNG")
        return out.getvalue()


def prepare_for_api(png: bytes, max_edge: int = 1568) -> tuple[bytes, str]:
    """Downscale to the API's effective resolution ceiling.

    Returns (bytes, media_type). Falls back to JPEG if the PNG is still large
    enough to bump the 5 MB per-image limit.
    """
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        longest = max(im.size)
        if longest > max_edge:
            scale = max_edge / longest
            im = im.resize(
                (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                Image.LANCZOS,
            )
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= 4_500_000:
            return data, "image/png"
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90, subsampling=0)
        return buf.getvalue(), "image/jpeg"


def thumbnail(png: bytes, max_edge: int = 320) -> bytes:
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


def ahash(png: bytes) -> int:
    """64-bit average hash, used to tell whether the screen actually changed."""
    with Image.open(io.BytesIO(png)) as im:
        small = im.convert("L").resize((8, 8), Image.LANCZOS)
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for i, px in enumerate(pixels):
        if px > mean:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
