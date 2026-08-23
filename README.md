# Screen Solver

**Capture your screen silently, and have an AI read the coding or SQL problem
on it and solve it — with a full teaching-quality breakdown.**

Built for the moment you are looking at a problem on one display and want a
worked answer on another. Runs on Claude, or entirely offline on a local model.

<!-- Add a screenshot here: docs/dashboard.png -->

---

## What it does

- **Native macOS app** — global hotkeys, a menu-bar item, launch at login.
  Closing the window leaves it running quietly in the menu bar.
- **Silent capture** — no shutter sound, no flash, no overlay, no thumbnail
  animation. Nothing on screen changes when you capture.
- **Reads a real screen, not a clean prompt** — it finds the problem among
  nav bars, timers, hint counters, editor panes and test panels, and obeys the
  output contract it finds there (column names, ordering, dialect, signature).
- **Reaches past the pixels** — when the schema is behind a "Schema & data"
  tab, it pulls the live DOM: hidden tab panels, collapsed `<details>`, editor
  buffers, tables. It can click a tab and re-capture.
- **Reads the tabs you are not looking at** — *Explore page* pulls the schema,
  examples and test-case panels out of a background copy of the page, so your
  own tab never changes and nothing is missed below the fold. Driven by the
  app, so it works with models that have no tool support.
- **Two views of the answer** — a **Solution** tab with just the finished
  code, and a **Breakdown** tab with the full reasoning: problem restatement,
  inputs/schema, assumptions, approach, numbered steps, clause-by-clause
  walkthrough, a traced worked example, complexity, edge cases and
  alternatives.
- **Your choice of model** — Claude by default, or any local vision model
  through Ollama / LM Studio / llama.cpp / vLLM. No API bill required.

---

## Install (macOS)

Requires **macOS 12 or newer**. Apple Silicon and Intel both work.

### Option A — download the app

1. Grab the latest `.dmg` from
   [Releases](https://github.com/Ray-Hughes/screen-solver/releases/latest).
   Apple Silicon only for now — on an Intel Mac, build from source below.
2. Open the `.dmg` and drag **Screen Solver** into **Applications**.
3. **Clear the quarantine flag.** The app is ad-hoc signed rather than
   notarized (it costs $99/yr to notarize), so macOS will otherwise refuse it
   with *"Screen Solver is damaged and can't be opened"*. In Terminal:

   ```bash
   xattr -dr com.apple.quarantine "/Applications/Screen Solver.app"
   ```

4. Open it from Spotlight.

You also need **Python 3.10+** on the machine — the app builds its own
isolated environment on first launch and never touches your system packages:

```bash
brew install python@3.12
```

First launch takes ~40 seconds while it does that. After that it starts in a
couple of seconds.

### Option B — build from source

Needs [Node.js](https://nodejs.org) as well as Python 3.10+.

```bash
git clone https://github.com/Ray-Hughes/screen-solver.git
cd screen-solver
./app.sh install       # builds Screen Solver.app into /Applications
```

Nothing is downloaded from an untrusted source, so there is no quarantine flag
to clear. Other commands:

| Command | What it does |
|---|---|
| `./app.sh` | run from source, no packaging — best while editing the code |
| `./app.sh build` | produce `.app` and `.dmg` under `desktop/dist/` |
| `./app.sh install` | build, then copy into `/Applications` |
| `./app.sh reset-perms` | clear a stale Screen Recording grant (see below) |

### Option C — browser only

No app bundle, no hotkeys, no menu bar — but nothing to install either.

```bash
./run.sh               # creates .venv on first run, serves on :8787
```

Then open <http://127.0.0.1:8787/>.

> Whichever you pick: put the dashboard on the display you are **not**
> capturing.

---

## First run: macOS permissions

| What | Where | Needed for |
|---|---|---|
| **Screen Recording** | System Settings → Privacy & Security → Screen Recording → add **Screen Solver** (or, for `./run.sh`, the terminal app that runs it) | any capture at all |
| **Automation** | prompted on first use | reading the live DOM |
| **Allow JavaScript from Apple Events** | Chrome/Brave/Edge/Arc: View → Developer. Safari: Develop menu. | reading the live DOM |

While the permission is missing the dashboard shows a red bar with three
buttons. **Grant access** is the one that does the work: macOS only raises the
Screen Recording prompt in response to a real capture attempt, so the app makes
one. Then turn the toggle on in Privacy Settings and reopen the app — macOS
hands the permission only to a process that *starts* after it is granted, so a
fresh launch is always required.

The same flow lives in the menu-bar menu under **Screen Recording access…**.

### If the toggle is on and it still says denied

That is a stale grant, and it is expected after a rebuild. The packaged app is
ad-hoc signed, so its code hash changes every time `./app.sh build` runs. TCC
pinned the old hash, so macOS answers "denied" from a row that no longer
matches — without ever re-prompting, while the toggle still looks on.

```bash
./app.sh reset-perms     # drops the stale grant and reopens the app
```

**Grant access** in the banner offers the same reset. While iterating on the
code, prefer `./app.sh` (runs from source) — that runs under the stock, properly
signed `Electron.app`, so you grant *Electron* once and rebuilds never
invalidate it.

```bash
./run.sh doctor     # checks all three plus your sign-in
```

---

## Choose your model

Two options, switchable at any time from **⚙ Settings** in the top bar:

| | Claude (hosted) | Local model |
|---|---|---|
| Cost | per token, needs API credit | free |
| Setup | `./run.sh login` — browser sign-in, no API key | `ollama pull qwen2.5vl:7b` |
| Quality | markedly better at multi-step reasoning | good enough for most problems |
| Page inspection | yes | usually not — most local models cannot call tools |
| Privacy | image + text go to the Anthropic API | nothing leaves the machine |

Jump to [Running a local model](#running-a-local-model-no-api-bill) or
[Signing in](#signing-in).

---

## Running a local model (no API bill)

Screen Solver talks to anything that speaks the OpenAI chat API, so it can run
entirely on your own machine — no key, no credit, no network.

```bash
brew install ollama
ollama serve                  # leave running
ollama pull qwen2.5vl:7b      # ~6 GB
```

### From the dashboard

The **⚙** button in the top bar opens Settings, which is the easiest way to do
all of this: pick the provider, pick the model from a list of what your runner
actually has (vision models are labelled, embedding-only ones are hidden), and
save. The change applies immediately — no restart, no editing files.

Everything it writes goes to `~/.config/screen-solver/.env`, so the same choice
applies to `./run.sh` too.

### Or by hand

Two lines of config:

```
SOLVER_PROVIDER=ollama
SOLVER_MODEL=qwen2.5vl:7b
```

Put them in `~/.config/screen-solver/.env` — that file is read by both the
packaged app and `./run.sh`. A `.env` in the checkout also works and takes
precedence, but the packaged app cannot see it: it runs from inside the `.app`
bundle and has no idea where your checkout is.

That is all. The dashboard drops the sign-in chip and labels itself
`qwen2.5vl:7b · local`.

### Picking a model

It **must** be a vision model — every solve sends a screenshot.

| Model | Size | Good at |
|---|---|---|
| `minicpm-v` | ~5 GB | pure OCR; text-heavy screenshots |
| `qwen2.5vl:7b` | ~6 GB | the sensible default — reads code, tables and SQL |
| `gemma3:12b` | ~8 GB | better prose when you want the *explanation* |
| `qwen2.5vl:32b` | ~21 GB | closest to hosted quality; wants ~32 GB of RAM |

### What you give up

Local vision models are meaningfully weaker than Claude at multi-step
reasoning, and most of them cannot call tools at all. When the server rejects
the tool definitions, Screen Solver notices, says so in the chat pane, and
retries without them for the rest of the session — you lose **page inspection**
(reading hidden tabs and editor buffers straight from the DOM) but everything
else still works. Force it either way with `SOLVER_TOOLS=off` / `on`.

Reasoning models that emit `<think>…</think>` inline have that split out and
routed to the Reasoning pane rather than pasted into the answer.

### Other runners

`SOLVER_PROVIDER` also accepts `lmstudio`, `llamacpp`, `vllm` and `jan`, each of
which just picks the usual port. For anything else — including a paid
OpenAI-compatible gateway — set `SOLVER_BASE_URL` (and `SOLVER_API_KEY`)
directly.

---

## When something goes wrong

Failures render as a card in the Breakdown pane with a plain-language title, an
explanation, and a button where one helps — never a raw traceback. The original
message and `request_id` are one click away under **Technical details**.

The one you are most likely to hit first:

> **Out of API credit** — your Anthropic API organization has no credit left, so
> the request is rejected before the model runs. The card links straight to
> [Plans & Billing](https://console.anthropic.com/settings/billing). Buy credits
> there (minimum $5), then press **Retry** on the card. Make sure it is the same
> organization your profile is bound to — `ant auth status` prints the active
> workspace.

Others are classified too: not signed in, expired credentials, rate limits
(with the retry-after), request too large (crop a region), model not found,
Anthropic overloaded, and network failures. Anything retryable gets a **Retry**
button that replays the last solve.

---

## Signing in

You do **not** need an API key. Click **sign in** in the dashboard's top bar,
or run:

```bash
./run.sh login      # opens a browser, then stores an OAuth profile
./run.sh logout
```

This shells out to the official Anthropic CLI, which writes a profile to
`~/.config/anthropic/`. The Anthropic SDK's credential chain finds it on its
own and refreshes the short-lived access token itself — nothing to paste,
nothing to rotate.

Install the CLI once if you have not already:

```bash
brew install anthropics/tap/ant
xattr -d com.apple.quarantine "$(brew --prefix)/bin/ant"
```

**Billing.** Signing in authenticates your Anthropic *account*, but Messages
API calls bill to your Anthropic **API organization** — a Claude Pro or Max
subscription does not cover them. If requests come back with a credit error,
add credits at
[console.anthropic.com](https://console.anthropic.com/settings/billing).

**A static key still works** if you prefer one: uncomment `ANTHROPIC_API_KEY`
in `.env`. Be aware it *overrides* the browser sign-in — including an empty or
placeholder value, which is the single most confusing way to break auth. The
app drops obvious placeholders (`sk-ant-...`, `your-api-key`, blank) for
exactly that reason, and `doctor` warns when a real key is shadowing a
profile.

---

## Using it

### Global hotkeys (desktop app only)

These work from any app — the dashboard never needs focus.

| Hotkey | Action |
|---|---|
| `⌥⌘S` | Capture & solve |
| `⌥⌘C` | Capture |
| `⌥⌘E` | Read the page's other panels (schema, examples, tests) |
| `⌥⌘A` | Pin the current screen as a supporting capture |
| `⌥⌘W` | Toggle watch mode |
| `⌥⌘D` | Show / hide the dashboard |

The menu-bar icon offers the same actions plus **Launch at login** and
**Quit**. Closing the window leaves the app running there; capture and watch
keep working.

### In the dashboard

| Action | How |
|---|---|
| Capture | `Space`, the **Capture** button, or `bin/solve-capture` |
| Capture and solve in one go | **Capture & solve** |
| Solve the current capture | `S` |
| Crop to just the problem | `R`, then drag a box. `Esc` clears it. |
| Pull the live page | **Inspect front tab** |
| Follow-up question | the chat column, `⌘⏎` to send |
| Resize viewer vs. breakdown | drag the bar between them |

**Region cropping matters on big displays.** Everything is downscaled to
1568px on the long edge before it is sent, so a 5K screenshot loses small
text. Dragging a box around the problem keeps it legible.

### Watch mode

Polls the display, compares an average hash, and captures when the screen
settles on something new — so it does not fire mid-scroll. Optionally solves
each new capture automatically. Sensitivity is the Hamming distance that
counts as "different".

### Triggering from elsewhere

The desktop app's hotkeys cover most of this, but `bin/solve-capture` POSTs to
a **browser-mode** server on the fixed port, which is handy for Raycast,
Alfred, BetterTouchTool, or Shortcuts.app → *Run Shell Script*:

```
/Users/you/va/screen-solver/bin/solve-capture --solve
```

(The desktop app picks a random free port, so this only targets `./run.sh`.)

---

## Reading the tabs you are not looking at

A screenshot only shows the tab that happened to be open. When the schema is
behind **Schema & data**, a model working from pixels alone invents the column
names — and the query fails with *no such column*.

**Explore page** (left rail, or `⌥⌘E`) fixes it, and does so **without
touching your browser**. It opens a background duplicate of the page, clicks
through the panels *there*, reads each one, and closes it. Your own tab never
changes — same URL, same tab, same scroll position.

That matters for a second reason: it reads the DOM rather than photographing
it, so it picks up everything in a panel, including the rows below the fold
that a screenshot would cut off. On the site this was built against it returns
all eight tables with their full `CREATE TABLE` statements and foreign keys —
about 21,000 characters that no screenshot could have shown.

Tick **Explore before solving** to make it part of every solve. The setting
lives on the backend, so the global hotkeys and watch mode honour it too.

Panels are chosen by name: ones promising problem context (*Schema & data*,
*Test cases*, *Constraints*) are always preferred, action buttons (*Submit*,
*Run*, *Reset*) are never clicked, and panels holding nothing useful — or in
the case of a site's own *Solution* tab, so much that the model would copy the
answer instead of working it out — are skipped.

> Chromium only (Chrome, Brave, Edge, Arc). Safari cannot run JavaScript in a
> tab that is not frontmost, so there is no way to do this invisibly there.

### Supporting screenshots

For anything that is not a browser tab — a PDF, another window, a second
monitor — press `⌥⌘A` to pin whatever is on screen as an extra view of the
current problem. The strip under the viewer shows everything that was sent,
and follows each capture in as it arrives; click any of them to see exactly
what the model got.

---

## How it decides what to solve

The system prompt (`screen_solver/prompts.py`) tells the model to:

1. Find the prose that states the task, and read the title/difficulty/topic
   chips — `hard · recursive CTE · 18 min` means write a recursive CTE.
2. Extract the **output contract** exactly: column names and order, sort
   order, rounding, tie-breaks, return type. This is where most answers fail.
3. Treat a partially-filled editor as a hard constraint — complete *that*
   skeleton in *that* dialect.
4. Ignore chrome: logos, gems, streaks, timers, ads, other windows.
5. Never invent a schema, signature or constraint it could have looked up.

### Tools it can call

| Tool | What it does |
|---|---|
| `read_page` | Live DOM of the frontmost browser tab — visible text, **hidden/inactive tab panels**, editor buffers (Monaco, CodeMirror 5/6, textareas — including lines scrolled out of view), tables, code blocks, and the labels of every clickable tab |
| `open_and_capture` | Clicks a tab/button/`<summary>` by its visible label, re-reads the DOM, and takes a fresh silent screenshot |
| `recapture_screen` | Fresh screenshot — for non-browser apps, or to confirm a change |

Tool activity streams into the chat column as it happens.

### Bookmarklet fallback

For browsers that cannot be scripted (Firefox, a locked-down profile, a
Chrome without the Apple Events flag), drag **Inspect bookmarklet** from the
dashboard to your bookmarks bar. Clicking it on any page harvests the same
data and POSTs it to the local server, attaching it to the latest capture.

---

## CLI

```bash
./run.sh                       # serve (default)
./run.sh serve --no-open       # do not open a browser
./run.sh login                 # browser sign-in to your Anthropic account
./run.sh logout                # clear the stored sign-in
./run.sh doctor                # permissions, browser access, credentials
./run.sh displays              # list capturable displays
./run.sh shot -o out.png -D 2  # one silent screenshot
./run.sh inspect               # dump the frontmost tab's DOM text
```

## Configuration

All optional, via `.env` (see `.env.example`):

Most of this is editable from **⚙ Settings** in the top bar, which writes to
`~/.config/screen-solver/.env` and applies without a restart.

Files are read in order: `.env` in the checkout, then
`~/.config/screen-solver/.env`. The first file to mention a key wins; real
environment variables beat both. Only the packaged app's `SOLVER_PORT` and
`SOLVER_HOST` need a relaunch to take effect.

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | optional — `./run.sh login` is the easier path, and a key set here overrides it |
| `SOLVER_MODEL` | `claude-opus-5` | or a local model id, e.g. `qwen2.5vl:7b` |
| `SOLVER_EFFORT` | `xhigh` | `low`…`max` — Anthropic only |
| `SOLVER_PROVIDER` | inferred | `anthropic`, or `ollama` / `lmstudio` / `llamacpp` / `vllm` / `jan` |
| `SOLVER_BASE_URL` | per provider | any OpenAI-compatible endpoint |
| `SOLVER_API_KEY` | — | only for gateways that want one |
| `SOLVER_TOOLS` | `auto` | `off` disables page inspection outright |
| `SOLVER_MAX_TOKENS` | `32000` / `4096` | cloud / local |
| `SOLVER_TEMPERATURE` | `0.2` | local models only |
| `SOLVER_PORT` | `8787` | |
| `SOLVER_CAPTURE_DISPLAY` | `1` | which display to capture |
| `SOLVER_WATCH_INTERVAL` | `2.0` | watch-mode poll seconds |
| `SOLVER_MAX_EDGE` | `1568` | long edge sent to the API |
| `SOLVER_SUPPORT_MAX_EDGE` | `1024` | long edge for supporting captures |
| `SOLVER_EXPLORE` | `0` | read the other panels before every solve |
| `SOLVER_KEEP_SHOTS` | `40` | shots kept on disk |

## Layout

```
app.sh          desktop app: run / build / install
run.sh          browser mode
desktop/
  main.js       Electron shell — supervises the backend, hotkeys, tray, window
  preload.js    the narrow bridge the dashboard sees as window.solverDesktop
  loading.html  first-run progress while the Python env is built
screen_solver/
  auth.py       browser sign-in, profile detection, placeholder-key guard
  capture.py    screencapture -x, display probing, crop/downscale, ahash
  inspect.py    Apple Events → browser DOM; harvest + click
  js/           the JS injected into the page (also the bookmarklet body)
  analyze.py    the streaming tool-use loop, provider-agnostic
  backends/     one adapter per API dialect (Anthropic, OpenAI-compatible)
  prompts.py    the system prompt and message construction
  settings.py   the editable config and how it is persisted
  server.py     FastAPI routes + SSE
  store.py      shots and their conversations
  static/       the dashboard (no CDN — works offline)
```

In the desktop app the backend runs on a random free port supervised by the
Electron process, and its Python environment lives in
`~/Library/Application Support/Screen Solver/venv`. In browser mode it uses
the repo's `.venv` and `SOLVER_PORT`.

Captures are written to `shots/` and pruned to the newest `SOLVER_KEEP_SHOTS`.
On a local model nothing leaves the machine at all; on Claude, only the image
and text of the solve you asked for.

---

## Cutting a release

```bash
./app.sh build                       # -> desktop/dist/Screen Solver-<ver>-arm64.dmg
gh release create v0.1.0 "desktop/dist/Screen Solver-0.1.0-arm64.dmg" \
  --title "v0.1.0" --notes "First release."
```

Bump `version` in `desktop/package.json` first. The build is ad-hoc signed, so
tell people to clear the quarantine flag — see [Install](#option-a--download-the-app).

---

## License

MIT. See [LICENSE](LICENSE).
