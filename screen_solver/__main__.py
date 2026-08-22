"""CLI entry point: python -m screen_solver [serve|displays|shot|solve]"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from .config import Config


def cmd_serve(cfg: Config, args) -> int:
    import uvicorn

    from .server import create_app

    from . import auth

    st = auth.status()
    if not st.signed_in:
        print(
            "! Not signed in — capture will work, solving will not.\n"
            "  Sign in from the dashboard, or run:  ./run.sh login\n"
            "  (An ANTHROPIC_API_KEY in .env works too.)",
            file=sys.stderr,
        )
    else:
        print(f"auth: {st.detail}")

    url = f"http://{cfg.host}:{cfg.port}/"
    where = f"effort: {cfg.effort}" if cfg.provider == "anthropic" else cfg.base_url
    print(f"screen-solver → {url}   (model: {cfg.model}, {where})")
    if not args.no_open:
        webbrowser.open(url)
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level=args.log_level)
    return 0


def cmd_displays(cfg: Config, args) -> int:
    from . import capture

    try:
        found = capture.list_displays()
    except capture.CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for d in found:
        marker = " ← capture target" if d.index == cfg.capture_display else ""
        print(f"{d.index}: {d.width}×{d.height}{marker}")
    return 0


def cmd_doctor(cfg: Config, args) -> int:
    """Check the three things that actually go wrong on a fresh machine."""
    from . import auth, capture
    from . import inspect as page_inspect

    ok = True

    print("screen capture:")
    try:
        found = capture.list_displays()
        for d in found:
            print(f"  ✓ display {d.index}: {d.width}×{d.height}")
    except capture.CaptureError as exc:
        ok = False
        print(f"  ✗ {exc}")

    print("browser inspection:")
    try:
        data = page_inspect.harvest()
        print(f"  ✓ {data.get('browser')} — {data.get('url', '')[:70]}")
    except page_inspect.InspectError as exc:
        print(f"  – {exc}")
        print("    (optional — the bookmarklet fallback still works)")

    print("credentials:")
    st = auth.status()
    if st.signed_in:
        print(f"  ✓ {st.detail}")
        if st.expires_in is not None:
            print(f"    access token valid for {st.expires_in // 60} min "
                  "(the SDK refreshes it automatically)")
    else:
        ok = False
        print(f"  ✗ {st.detail}")
        print("    run  ./run.sh login  (or set ANTHROPIC_API_KEY in .env)")
    for w in st.warnings:
        print(f"  ! {w}")
    if not st.ant_installed:
        print("    " + auth.INSTALL_HINT.replace("\n", "\n    "))

    if cfg.provider == "anthropic":
        print(f"model: {cfg.model} (effort {cfg.effort})")
    else:
        print(f"model: {cfg.model} via {cfg.base_url}")
    return 0 if ok else 1


def cmd_shot(cfg: Config, args) -> int:
    from . import capture

    png = capture.grab_png(args.display or cfg.capture_display)
    out = Path(args.out)
    out.write_bytes(png)
    print(f"{out} ({len(png):,} bytes)")
    return 0


def cmd_inspect(cfg: Config, args) -> int:
    from . import inspect as page_inspect

    data = page_inspect.harvest(args.browser)
    print(page_inspect.summarize_for_model(data, max_chars=args.max_chars))
    return 0


def cmd_login(cfg: Config, args) -> int:
    """Browser sign-in to an Anthropic account — no API key to manage."""
    from . import auth

    if not auth.ant_bin():
        print(auth.INSTALL_HINT, file=sys.stderr)
        return 1
    print(
        "Opening your browser to sign in to Anthropic…\n"
        "If no browser opens, copy the URL the CLI prints below.\n"
        "Waiting for the callback (Ctrl-C to abort)…\n"
    )
    try:
        st = auth.login_interactive(args.profile)
    except auth.AuthError as exc:
        print(f"sign-in failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nsign-in aborted.", file=sys.stderr)
        return 130
    print(f"✓ {st.detail}")
    print(
        "Note: Messages API usage bills to your Anthropic API organization. "
        "A Claude Pro/Max subscription does not cover it — add credits at "
        "https://console.anthropic.com/settings/billing if requests fail."
    )
    return 0


def cmd_logout(cfg: Config, args) -> int:
    from . import auth

    try:
        st = auth.logout(args.all)
    except auth.AuthError as exc:
        print(f"logout failed: {exc}", file=sys.stderr)
        return 1
    print(st.detail)
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = Config.from_env()

    parser = argparse.ArgumentParser(prog="screen-solver")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="run the dashboard (default)")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    p.add_argument("--log-level", default="warning")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("displays", help="list capturable displays")
    p.set_defaults(fn=cmd_displays)

    p = sub.add_parser("login", help="sign in to your Anthropic account in a browser")
    p.add_argument("--profile", help="named profile to sign in under")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("logout", help="clear the stored sign-in")
    p.add_argument("--all", action="store_true", help="every profile, not just the active one")
    p.set_defaults(fn=cmd_logout)

    p = sub.add_parser("doctor", help="check permissions, browser access and credentials")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("shot", help="take one silent screenshot")
    p.add_argument("-o", "--out", default="shot.png")
    p.add_argument("-D", "--display", type=int)
    p.set_defaults(fn=cmd_shot)

    p = sub.add_parser("inspect", help="dump the frontmost browser tab's DOM text")
    p.add_argument("--browser")
    p.add_argument("--max-chars", type=int, default=24000)
    p.set_defaults(fn=cmd_inspect)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        args = parser.parse_args(["serve", *(argv or [])])
    return args.fn(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
