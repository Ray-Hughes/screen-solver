#!/usr/bin/env bash
# Desktop app: run, build, or install.
#
#   ./app.sh            run it from source (hot, no packaging)
#   ./app.sh build      package Screen Solver.app (+ .dmg) and ad-hoc sign it
#   ./app.sh install    build, then copy the app into /Applications
#   ./app.sh reset-perms  clear the stale Screen Recording grant and relaunch
set -euo pipefail
cd "$(dirname "$0")/desktop"

APP_DIR="dist/mac-arm64/Screen Solver.app"

need_node_modules() {
  if [ ! -d node_modules ]; then
    echo "installing Electron toolchain (first run)…"
    npm install --silent
  fi
}

adhoc_sign() {
  # macOS only remembers Screen Recording / Automation grants for a signed
  # bundle. An ad-hoc signature is enough for a locally built app.
  echo "ad-hoc signing…"
  codesign --force --deep --sign - "$APP_DIR"
}

BUNDLE_ID="dev.screensolver.app"

case "${1:-run}" in
  reset-perms)
    # An ad-hoc signature pins the app's cdhash, which changes on every
    # rebuild. TCC keeps answering "denied" from the old row without ever
    # re-prompting, even though the toggle still looks on. Drop the row.
    echo "clearing Screen Recording grant for ${BUNDLE_ID}…"
    tccutil reset ScreenCapture "$BUNDLE_ID" || true
    osascript -e 'quit app "Screen Solver"' 2>/dev/null || true
    sleep 1
    if [ -d "/Applications/Screen Solver.app" ]; then
      open -a "/Applications/Screen Solver.app"
      echo "reopened. Take a capture and approve the prompt macOS shows."
    else
      echo "reset. Reopen Screen Solver and take a capture."
    fi
    ;;
  run)
    need_node_modules
    exec npm start
    ;;
  build)
    need_node_modules
    # Order matters. Building `dir dmg` in one pass images the app BEFORE
    # adhoc_sign runs, so the .dmg ships the stock Electron binary — it
    # identifies itself to macOS as "Electron", not dev.screensolver.app, and
    # every Screen Recording grant lands on the wrong identity. Sign the app
    # first, then image the already-signed bundle.
    ./node_modules/.bin/electron-builder --mac dir
    adhoc_sign
    ./node_modules/.bin/electron-builder --mac dmg --prepackaged "$APP_DIR"
    echo
    echo "built:  $(cd .. && pwd)/desktop/$APP_DIR"
    echo "dmg:    $(ls dist/*.dmg 2>/dev/null | head -1)"
    echo "install with:  ./app.sh install"
    ;;
  install)
    need_node_modules
    ./node_modules/.bin/electron-builder --mac dir
    adhoc_sign
    rm -rf "/Applications/Screen Solver.app"
    cp -R "$APP_DIR" /Applications/
    echo "installed to /Applications/Screen Solver.app"
    cat <<'NOTE'

Screen Recording note: this bundle is ad-hoc signed, so macOS sees each
rebuild as a different app and any previous grant stops applying — even
though the toggle still looks on. Clear the stale grant:

    ./app.sh reset-perms

Then take a capture, approve the prompt, turn the toggle on and reopen the
app. The red banner's "Grant access" button does the same thing.

While iterating, prefer `./app.sh` (runs from source). It runs under the
stock, properly signed Electron.app, so you grant "Electron" once and code
changes never invalidate it.
NOTE
    ;;
  *)
    echo "usage: ./app.sh [run|build|install]" >&2
    exit 1
    ;;
esac
