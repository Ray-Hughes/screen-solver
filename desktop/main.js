"use strict";

/**
 * Screen Solver — Electron shell.
 *
 * Owns three things a browser tab cannot:
 *   1. global hotkeys, so a capture never needs the dashboard focused,
 *   2. a menu-bar item, so the app can keep working with no window open,
 *   3. its own bundle identity, so macOS grants Screen Recording and
 *      Automation to *this app* rather than to whichever terminal launched it.
 *
 * The dashboard itself is the same FastAPI app as the browser build; this
 * process just supervises it on a private port.
 */

const {
  app, BrowserWindow, Tray, Menu, globalShortcut, shell, dialog, ipcMain,
  nativeImage, desktopCapturer, screen, systemPreferences,
} = require("electron");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const net = require("net");
const crypto = require("crypto");

const IS_DEV = !app.isPackaged;
const REPO_ROOT = path.resolve(__dirname, "..");
const PYTHON_ROOT = IS_DEV ? REPO_ROOT : path.join(process.resourcesPath, "python");

// A packaged app inherits a bare PATH, so interpreters are probed by full path.
// /usr/bin/python3 is deliberately last — it is 3.9 on current macOS and the
// Anthropic SDK needs 3.10+.
const PYTHON_CANDIDATES = [
  "/opt/homebrew/bin/python3.13",
  "/opt/homebrew/bin/python3.12",
  "/opt/homebrew/bin/python3.11",
  "/opt/homebrew/bin/python3",
  "/usr/local/bin/python3.13",
  "/usr/local/bin/python3.12",
  "/usr/local/bin/python3.11",
  "/usr/local/bin/python3",
  path.join(app.getPath("home"), ".pyenv/shims/python3"),
  "/usr/bin/python3",
];

const HOTKEYS = {
  "Alt+Command+C": "capture",
  "Alt+Command+S": "captureSolve",
  "Alt+Command+W": "toggleWatch",
  "Alt+Command+E": "exploreNow",
  "Alt+Command+A": "addSupport",
  "Alt+Command+D": "toggleWindow",
};

let win = null;
let loader = null;
let tray = null;
let backend = null;
let port = 0;
let quitting = false;
let watchOn = false;
let watchTimer = null;
let watchCfg = { interval: 2000, threshold: 6, autoAnalyze: false, mode: "auto", language: "" };
let lastHash = null;
let pendingHash = null;
let captureDisplayIndex = 1;

/* ── small helpers ─────────────────────────────────────────────────── */

const boundsFile = () => path.join(app.getPath("userData"), "window.json");
const flagsFile = () => path.join(app.getPath("userData"), "flags.json");

function readFlags() {
  try {
    return JSON.parse(fs.readFileSync(flagsFile(), "utf8"));
  } catch {
    return {};
  }
}

function writeFlag(key, value) {
  try {
    fs.writeFileSync(flagsFile(), JSON.stringify({ ...readFlags(), [key]: value }));
  } catch (err) {
    console.error(`[solver] could not persist ${key}: ${err.message}`);
  }
}

function readBounds() {
  try {
    return JSON.parse(fs.readFileSync(boundsFile(), "utf8"));
  } catch {
    return { width: 1500, height: 940 };
  }
}

function saveBounds() {
  if (!win || win.isDestroyed() || win.isMinimized()) return;
  try {
    fs.mkdirSync(path.dirname(boundsFile()), { recursive: true });
    fs.writeFileSync(boundsFile(), JSON.stringify(win.getBounds()));
  } catch {
    /* a lost window position is not worth surfacing */
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

const run = (file, args, opts = {}) =>
  new Promise((resolve) =>
    execFile(file, args, { timeout: 20000, ...opts }, (err, stdout, stderr) =>
      resolve({ ok: !err, stdout: String(stdout || ""), stderr: String(stderr || "") })
    )
  );

function status(text) {
  console.log(`[solver] ${text}`);
  if (loader && !loader.isDestroyed()) loader.webContents.send("status", text);
}

function logLine(text) {
  if (loader && !loader.isDestroyed()) loader.webContents.send("log", text);
}

/* ── python environment ────────────────────────────────────────────── */

const venvDir = () =>
  IS_DEV ? path.join(REPO_ROOT, ".venv") : path.join(app.getPath("userData"), "venv");

const venvPython = () => path.join(venvDir(), "bin", "python");
const requirementsFile = () => path.join(PYTHON_ROOT, "requirements.txt");
// The venv outlives upgrades, so remember what was installed into it. Without
// this a new dependency (the openai client, say) never reaches an existing
// install and the feature that needs it fails at runtime.
const stampFile = () => path.join(venvDir(), ".requirements.sha256");

function requirementsHash() {
  try {
    return crypto
      .createHash("sha256")
      .update(fs.readFileSync(requirementsFile()))
      .digest("hex");
  } catch {
    return "";
  }
}

function venvUsable() {
  return fs.existsSync(venvPython());
}

function venvCurrent() {
  try {
    return fs.readFileSync(stampFile(), "utf8").trim() === requirementsHash();
  } catch {
    return false;
  }
}

/** Find an interpreter new enough to build the venv from. */
async function findBootstrapPython() {
  const explicit = process.env.SOLVER_PYTHON;
  const list = explicit ? [explicit, ...PYTHON_CANDIDATES] : PYTHON_CANDIDATES;
  for (const candidate of list) {
    if (!fs.existsSync(candidate)) continue;
    const { ok, stdout } = await run(candidate, [
      "-c",
      "import sys; print('%d.%d' % sys.version_info[:2])",
    ]);
    if (!ok) continue;
    const [major, minor] = stdout.trim().split(".").map(Number);
    if (major === 3 && minor >= 10) return candidate;
  }
  return null;
}

/** Create the virtualenv and install requirements, streaming progress. */
function createVenv(python) {
  return new Promise((resolve, reject) => {
    status("Setting up the Python environment (one time, ~30s)…");
    const proc = spawn(python, ["-m", "venv", venvDir()], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    proc.stdout.on("data", (d) => logLine(String(d).trim()));
    proc.stderr.on("data", (d) => logLine(String(d).trim()));
    proc.on("error", reject);
    proc.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`python -m venv exited ${code}`))
    );
  });
}

function installRequirements() {
  return new Promise((resolve, reject) => {
    const pip = spawn(
      venvPython(),
      ["-m", "pip", "install", "--disable-pip-version-check", "-r", requirementsFile()],
      { stdio: ["ignore", "pipe", "pipe"] }
    );
    pip.stdout.on("data", (d) => logLine(String(d).trim()));
    pip.stderr.on("data", (d) => logLine(String(d).trim()));
    pip.on("error", reject);
    pip.on("exit", (code) => {
      if (code !== 0) return reject(new Error(`pip install exited ${code}`));
      try {
        fs.writeFileSync(stampFile(), requirementsHash());
      } catch (err) {
        // Only costs a redundant pip run next launch.
        console.error(`[solver] could not stamp the venv: ${err.message}`);
      }
      resolve();
    });
  });
}

/* ── backend ───────────────────────────────────────────────────────── */

async function startBackend() {
  port = await freePort();
  backend = spawn(
    venvPython(),
    ["-m", "screen_solver", "serve", "--no-open"],
    {
      cwd: PYTHON_ROOT,
      env: {
        ...process.env,
        SOLVER_PORT: String(port),
        SOLVER_HOST: "127.0.0.1",
        SOLVER_DESKTOP: "1",
        PYTHONPATH: PYTHON_ROOT,
        PYTHONUNBUFFERED: "1",
        // Keep the app's own PATH usable for `ant` and `screencapture`.
        PATH: `/opt/homebrew/bin:/usr/local/bin:${process.env.PATH || "/usr/bin:/bin:/usr/sbin:/sbin"}`,
      },
      stdio: ["ignore", "pipe", "pipe"],
    }
  );

  backend.stdout.on("data", (d) => logLine(String(d).trimEnd()));
  backend.stderr.on("data", (d) => logLine(String(d).trimEnd()));
  backend.on("exit", (code) => {
    backend = null;
    if (quitting) return;
    fail(`The backend stopped unexpectedly (exit ${code}).`);
  });
}

async function waitForBackend(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  status("Starting the solver…");
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

const api = (route, body) =>
  fetch(`http://127.0.0.1:${port}${route}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
    .catch(() => null);

/* ── screen capture ────────────────────────────────────────────────── *
 * Capture happens HERE, in the app bundle, rather than by shelling out to
 * /usr/sbin/screencapture from the Python child. macOS attributes Screen
 * Recording to the process that asks; a child Python.app is its own bundle
 * (org.python.python) and would need its own grant, which is why granting
 * "Screen Solver" appeared to do nothing.
 * ------------------------------------------------------------------------ */

const BUNDLE_ID = "dev.screensolver.app";
const PRIVACY_PANE =
  "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";

/**
 * What macOS currently thinks.
 *
 * IMPORTANT: for 'screen' this is a thin wrapper over
 * CGPreflightScreenCaptureAccess(), which returns a plain boolean and so
 * cannot tell "the user refused" apart from "nobody has asked yet". Electron
 * maps false to 'denied' and never returns 'not-determined' for 'screen'.
 *
 * So 'denied' is ALSO what a freshly installed - or freshly reset - app looks
 * like. Refusing to capture on 'denied' means macOS is never asked, the
 * consent prompt never appears, and the app can never leave that state.
 */
function screenPermission() {
  return systemPreferences.getMediaAccessStatus("screen");
}

/**
 * Make macOS actually consider the request.
 *
 * Electron exposes no request API for screen capture - askForMediaAccess()
 * covers camera and microphone only - so the sole way to raise the system
 * prompt (and to get the app listed under Privacy & Security > Screen
 * Recording at all) is to genuinely attempt a capture. A 1x1 thumbnail is
 * enough to trip it and costs nothing.
 */
async function requestScreenAccess() {
  if (process.platform !== "darwin") return "granted";
  if (screenPermission() === "granted") return "granted";
  try {
    await desktopCapturer.getSources({
      types: ["screen"],
      thumbnailSize: { width: 1, height: 1 },
      fetchWindowIcons: false,
    });
  } catch (err) {
    // Failing is expected while access is refused; the point of the call is
    // the side effect, not the result.
    console.error(`[solver] permission probe: ${(err && err.message) || err}`);
  }
  return screenPermission();
}

/**
 * Forget the existing TCC decision for this bundle.
 *
 * The app is ad-hoc signed, so every rebuild changes its cdhash. TCC pinned
 * the old hash, and the stale row makes macOS answer "denied" without ever
 * re-prompting - the toggle in System Settings still looks on. Dropping the
 * row is what lets the prompt come back.
 */
function resetScreenPermission() {
  return new Promise((resolve) => {
    execFile("/usr/bin/tccutil", ["reset", "ScreenCapture", BUNDLE_ID], (err) => {
      if (err) console.error(`[solver] tccutil reset failed: ${err.message}`);
      resolve(!err);
    });
  });
}

/** SIGTERM the Python child, with SIGKILL as a backstop. */
function stopBackend() {
  if (!backend) return;
  const child = backend;
  backend = null;
  try {
    child.kill("SIGTERM");
  } catch (err) {
    console.error(`[solver] could not stop the backend: ${err.message}`);
  }
  setTimeout(() => {
    try {
      child.kill("SIGKILL");
    } catch {
      /* already gone */
    }
  }, 2000);
}

function relaunchApp() {
  // app.exit() skips `before-quit`, so the backend has to be stopped by hand
  // or it is orphaned and keeps holding its port.
  quitting = true;
  stopBackend();
  app.relaunch();
  app.exit(0);
}

/** The whole recovery flow in one dialog. */
async function promptForScreenAccess({ afterReset = false } = {}) {
  if (process.platform !== "darwin") return "granted";

  const status = await requestScreenAccess();
  if (status === "granted") return "granted";
  if (status === "restricted") {
    dialog.showErrorBox("Screen Solver", PERMISSION_MESSAGE.restricted);
    return status;
  }

  const buttons = afterReset
    ? ["Open Privacy Settings", "Quit & Reopen", "Cancel"]
    : ["Open Privacy Settings", "Reset & Ask Again", "Quit & Reopen", "Cancel"];

  const { response } = await dialog.showMessageBox({
    type: "warning",
    buttons,
    defaultId: 0,
    cancelId: buttons.length - 1,
    message: "Screen Solver needs Screen Recording access",
    detail:
      "macOS has now been asked, so Screen Solver appears under\n" +
      "Privacy & Security > Screen Recording.\n\n" +
      "1. Turn the Screen Solver toggle ON.\n" +
      "2. Come back and choose Quit & Reopen - macOS only hands the " +
      "permission to a process that starts after it is granted.\n\n" +
      "If the toggle already looks ON it is stale: this build is ad-hoc " +
      "signed, so rebuilding changes its signature and the old grant stops " +
      "matching. Use Reset & Ask Again to clear it.",
  });

  const choice = buttons[response];
  if (choice === "Open Privacy Settings") {
    shell.openExternal(PRIVACY_PANE);
  } else if (choice === "Reset & Ask Again") {
    await resetScreenPermission();
    return promptForScreenAccess({ afterReset: true });
  } else if (choice === "Quit & Reopen") {
    relaunchApp();
  }
  return screenPermission();
}

function listDisplays() {
  return screen.getAllDisplays().map((d, i) => {
    const w = Math.round(d.bounds.width * d.scaleFactor);
    const h = Math.round(d.bounds.height * d.scaleFactor);
    return {
      index: i + 1,
      id: String(d.id),
      width: w,
      height: h,
      label: `Display ${i + 1} - ${w}x${h}${d.internal ? " (built-in)" : ""}`,
    };
  });
}

/** Full-resolution PNG of one display, as a Buffer. */
async function grabDisplay(index) {
  // Do not pre-judge 'denied' - see screenPermission(). Ask macOS for real
  // first, which is also what raises the consent prompt on a fresh install.
  if (process.platform === "darwin" && screenPermission() !== "granted") {
    const status = await requestScreenAccess();
    if (status !== "granted") {
      throw new Error(PERMISSION_MESSAGE[status] || `Screen Recording is ${status}.`);
    }
  }

  const displays = listDisplays();
  const target = displays.find((d) => d.index === index) || displays[0];
  if (!target) throw new Error("No displays found.");

  const sources = await desktopCapturer.getSources({
    types: ["screen"],
    thumbnailSize: { width: target.width, height: target.height },
    fetchWindowIcons: false,
  });
  const source =
    sources.find((s) => s.display_id === target.id) || sources[index - 1] || sources[0];
  // When access is refused getSources does not throw - it hands back black or
  // empty thumbnails, so an empty one means the grant is missing or stale.
  if (!source || source.thumbnail.isEmpty()) {
    throw new Error(PERMISSION_MESSAGE.denied);
  }
  return { png: source.thumbnail.toPNG(), display: target.index, image: source.thumbnail };
}

/** 64-bit average hash, so watch mode can tell whether the screen moved. */
function averageHash(image) {
  const small = image.resize({ width: 8, height: 8, quality: "good" });
  const bmp = small.toBitmap(); // BGRA
  const lum = [];
  for (let i = 0; i < 64; i++) {
    const o = i * 4;
    lum.push(0.114 * bmp[o] + 0.587 * bmp[o + 1] + 0.299 * bmp[o + 2]);
  }
  const mean = lum.reduce((a, b) => a + b, 0) / lum.length;
  let bits = 0n;
  lum.forEach((v, i) => {
    if (v > mean) bits |= 1n << BigInt(i);
  });
  return bits;
}

const hamming = (a, b) => {
  let x = a ^ b;
  let n = 0;
  while (x) {
    n += Number(x & 1n);
    x >>= 1n;
  }
  return n;
};

const PERMISSION_MESSAGE = {
  denied:
    "Screen Solver does not have Screen Recording access. Click \u201cGrant " +
    "access\u201d - macOS will list Screen Solver under Privacy & Security > " +
    "Screen Recording. Turn the toggle on, then reopen the app. If the toggle " +
    "already looks on it is stale (this build is ad-hoc signed, so rebuilding " +
    "changes its signature); Grant access clears it for you.",
  restricted:
    "Screen Recording is restricted on this Mac, probably by a device " +
    "management profile.",
  "not-determined":
    "Screen Recording has not been granted yet. Click \u201cGrant access\u201d " +
    "and approve the prompt macOS shows.",
};

async function pushDisplays() {
  const perm = screenPermission();
  const error = perm === "granted" ? null : PERMISSION_MESSAGE[perm] || String(perm);
  await fetch(`http://127.0.0.1:${port}/api/displays/push`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ displays: listDisplays(), error }),
  }).catch(() => {});
}

/** Capture and hand the frame to the backend. Returns the shot metadata. */
async function captureAndPush(opts = {}) {
  const { png, display, image } = await grabDisplay(opts.display || captureDisplayIndex);
  lastHash = averageHash(image);

  const q = new URLSearchParams({ display: String(display) });
  if (opts.analyze) {
    q.set("analyze", "1");
    if (opts.mode) q.set("mode", opts.mode);
    if (opts.language) q.set("language", opts.language);
    if (opts.hint) q.set("hint", opts.hint);
    if (opts.region) q.set("region", JSON.stringify(opts.region));
  }

  const res = await fetch(`http://127.0.0.1:${port}/api/capture/push?${q}`, {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: png,
  });
  if (!res.ok) throw new Error(`backend rejected the capture (${res.status})`);
  const shot = await res.json();

  return shot;
}

/** Pin whatever is on screen right now to the latest shot as an extra view. */
async function addSupportCapture(label) {
  const { png } = await grabDisplay(captureDisplayIndex);
  const q = new URLSearchParams({ label: label || "extra capture" });
  const res = await fetch(`http://127.0.0.1:${port}/api/capture/support?${q}`, {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: png,
  });
  if (!res.ok) throw new Error("There is no capture to attach this to yet.");
  return res.json();
}

/* ── exploring the page ────────────────────────────────────────────── *
 *
 * A screenshot only ever shows the tab that happened to be open, which is why
 * a schema sitting behind "Schema & data" ends up invented rather than read.
 *
 * This walks the other panels: the backend nominates which are worth opening
 * and clicks them over Apple Events, and THIS process takes each picture —
 * capture has to happen in the app bundle, because a screenshot taken by the
 * Python child is attributed to org.python.python and refused.
 *
 * It is deliberately not driven by the model: most local models cannot call
 * tools at all, and this has to work for them too.
 * ------------------------------------------------------------------------ */

const post = async (path, body) => {
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail ? JSON.parse(detail).detail || detail : `HTTP ${res.status}`);
  }
  return res.json();
};

async function pushSupport(shotId, label, note, png) {
  const q = new URLSearchParams({ shot_id: shotId, label, note: note || "" });
  await fetch(`http://127.0.0.1:${port}/api/capture/support?${q}`, {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: png,
  });
}

/** Narrate the pass to the dashboard, so it is visibly doing something. */
function exploreProgress(payload) {
  if (win && !win.isDestroyed()) win.webContents.send("explore-progress", payload);
}

async function explorePage(shotId) {
  exploreProgress({ phase: "planning" });
  const plan = await post("/api/explore/plan");
  const tabs = plan.tabs || [];
  const total = tabs.length;
  const opened = [];

  exploreProgress({ phase: "start", total, tabs, active: plan.active || "" });
  if (!total) {
    exploreProgress({ phase: "done", panels: [], total: 0 });
    return opened;
  }

  for (let i = 0; i < tabs.length; i++) {
    const label = tabs[i];
    const step = { label, index: i + 1, total };

    exploreProgress({ phase: "opening", ...step });
    let result;
    try {
      result = await post("/api/explore/open", { label });
    } catch (err) {
      exploreProgress({ phase: "failed", ...step, message: err.message });
      continue;
    }
    if (!result.ok) {
      exploreProgress({ phase: "failed", ...step, message: result.error || "could not open it" });
      continue;
    }

    try {
      const { png } = await grabDisplay(captureDisplayIndex);
      await pushSupport(shotId, result.label, result.note, png);
      opened.push(result.label);
      exploreProgress({ phase: "captured", ...step, label: result.label });
    } catch (err) {
      exploreProgress({ phase: "failed", ...step, message: err.message });
    }
  }

  // Put the page back the way it was found — the user is looking at it.
  if (plan.active) {
    exploreProgress({ phase: "restoring", label: plan.active });
    await post("/api/explore/open", { label: plan.active, settle: 0.2 }).catch(() => {});
  }

  exploreProgress({ phase: "done", panels: opened, total });
  return opened;
}

/* ── watch mode (desktop) ──────────────────────────────────────────── */

async function watchTick() {
  if (!watchOn) return;
  try {
    const { image } = await grabDisplay(captureDisplayIndex);
    const h = averageHash(image);

    if (lastHash !== null && hamming(h, lastHash) <= watchCfg.threshold) {
      pendingHash = null;
      return;
    }
    // Require two similar frames in a row so we never fire mid-scroll.
    if (pendingHash === null || hamming(h, pendingHash) > 2) {
      pendingHash = h;
      return;
    }
    pendingHash = null;
    await captureAndPush({
      analyze: watchCfg.autoAnalyze,
      mode: watchCfg.mode,
      language: watchCfg.language,
    });
  } catch {
    /* a denied or transient frame should not kill the loop */
  }
}

function setWatch(on, cfg = {}) {
  watchCfg = { ...watchCfg, ...cfg };
  watchOn = on;
  if (watchTimer) clearInterval(watchTimer);
  watchTimer = null;
  if (on) watchTimer = setInterval(watchTick, Math.max(1000, watchCfg.interval));
  refreshTray();
  if (win && !win.isDestroyed()) win.webContents.send("watch", { enabled: watchOn, ...watchCfg });
}

/* ── windows ───────────────────────────────────────────────────────── */

function showLoader() {
  loader = new BrowserWindow({
    width: 720,
    height: 420,
    resizable: false,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#0a0c10",
    show: true,
    webPreferences: { nodeIntegration: true, contextIsolation: false },
  });
  loader.loadFile(path.join(__dirname, "loading.html"));
}

function fail(message) {
  console.error(`[solver] FAILED: ${message}`);
  if (loader && !loader.isDestroyed()) {
    loader.webContents.send("failed", message);
    return;
  }
  dialog.showErrorBox("Screen Solver", message);
}

function createWindow() {
  const saved = readBounds();
  win = new BrowserWindow({
    ...saved,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    backgroundColor: "#0a0c10",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 14, y: 18 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadURL(`http://127.0.0.1:${port}/`);

  win.once("ready-to-show", () => {
    win.show();
    if (loader && !loader.isDestroyed()) loader.close();
    loader = null;
  });

  // Links open in the real browser, never in a chrome-less app window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  win.on("resize", saveBounds);
  win.on("move", saveBounds);

  // Closing the window leaves the app running in the menu bar.
  win.on("close", (e) => {
    if (quitting) return;
    e.preventDefault();
    saveBounds();
    hideWindow();
    explainMenuBarOnce();
  });
}

/**
 * Hide without leaving a black rectangle behind.
 *
 * A fullscreen window on macOS owns its own Space, and hiding it leaves that
 * Space up — empty and painted with the window's background colour, which is
 * the black screen you cannot click away. Leave fullscreen first and hide
 * only once the transition has actually finished.
 */
function hideWindow() {
  if (!win || win.isDestroyed()) return;
  if (win.isFullScreen()) {
    win.once("leave-full-screen", () => {
      // The event fires while AppKit is still animating; yield one turn.
      setTimeout(() => {
        if (win && !win.isDestroyed()) win.hide();
      }, 0);
    });
    win.setFullScreen(false);
    return;
  }
  win.hide();
}

/** Closing the window is not quitting — say so once, then never again. */
function explainMenuBarOnce() {
  if (readFlags().menuBarExplained) return;
  writeFlag("menuBarExplained", true);
  dialog
    .showMessageBox({
      type: "info",
      buttons: ["Keep it running", "Quit Screen Solver"],
      defaultId: 0,
      cancelId: 0,
      message: "Screen Solver is still running",
      detail:
        "Closing the window leaves it in the menu bar, so the global hotkeys " +
        "keep working. Use the menu-bar icon or \u2318Q to quit for real.",
    })
    .then(({ response }) => {
      if (response === 1) app.quit();
    });
}

function toggleWindow() {
  if (!win) return;
  if (win.isVisible() && win.isFocused()) hideWindow();
  else {
    win.show();
    win.focus();
  }
}

/* ── tray ──────────────────────────────────────────────────────────── */

function buildTrayMenu() {
  return Menu.buildFromTemplate([
    { label: "Capture", accelerator: "Alt+Command+C", click: () => command("capture") },
    { label: "Capture & solve", accelerator: "Alt+Command+S", click: () => command("captureSolve") },
    { type: "separator" },
    {
      label: "Watch for screen changes",
      type: "checkbox",
      checked: watchOn,
      accelerator: "Alt+Command+W",
      click: () => command("toggleWatch"),
    },
    { type: "separator" },
    { label: "Show dashboard", accelerator: "Alt+Command+D", click: () => toggleWindow() },
    {
      label: "Open in browser",
      click: () => shell.openExternal(`http://127.0.0.1:${port}/`),
    },
    { type: "separator" },
    {
      label: "Launch at login",
      type: "checkbox",
      checked: app.getLoginItemSettings().openAtLogin,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }),
    },
    { type: "separator" },
    { type: "separator" },
    {
      label: "Screen Recording access\u2026",
      visible: process.platform === "darwin",
      click: () => promptForScreenAccess(),
    },
    { type: "separator" },
    { label: "Quit Screen Solver", accelerator: "Command+Q", click: () => app.quit() },
  ]);
}

function refreshTray() {
  if (tray) tray.setContextMenu(buildTrayMenu());
}

function createTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, "assets", "trayTemplate.png"));
  icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.setToolTip("Screen Solver");
  refreshTray();
  tray.on("click", () => tray.popUpContextMenu());
}

/* ── commands (hotkeys + tray + menu) ──────────────────────────────── */

async function command(name) {
  switch (name) {
    case "capture":
      await captureAndPush({}).catch((e) => reportCaptureError(e));
      break;
    case "exploreNow":
      await post("/api/explore", {}).catch((e) => reportCaptureError(e));
      break;
    case "addSupport":
      await addSupportCapture().catch((e) => reportCaptureError(e));
      break;
    case "captureSolve":
      await captureAndPush({ analyze: true, mode: watchCfg.mode, language: watchCfg.language })
        .catch((e) => reportCaptureError(e));
      if (win && !win.isVisible()) win.show();
      break;
    case "toggleWatch":
      setWatch(!watchOn);
      break;
    case "toggleWindow":
      toggleWindow();
      break;
  }
}

function reportCaptureError(err) {
  const message = String((err && err.message) || err);
  console.error(`[solver] capture failed: ${message}`);
  if (win && !win.isDestroyed()) win.webContents.send("capture-error", { message });
  pushDisplays();
}

function registerHotkeys() {
  const failed = [];
  for (const [accel, name] of Object.entries(HOTKEYS)) {
    if (!globalShortcut.register(accel, () => command(name))) failed.push(accel);
  }
  if (failed.length) {
    // Another app owns the combination — worth saying, not worth blocking on.
    console.warn(`could not register global hotkeys: ${failed.join(", ")}`);
  }
}

function buildAppMenu() {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: app.name,
        submenu: [
          { role: "about" },
          { type: "separator" },
          { role: "hide" },
          { role: "hideOthers" },
          { type: "separator" },
          { role: "quit" },
        ],
      },
      {
        label: "Capture",
        submenu: [
          { label: "Capture", accelerator: "Alt+Command+C", click: () => command("capture") },
          { label: "Capture & solve", accelerator: "Alt+Command+S", click: () => command("captureSolve") },
          { label: "Toggle watch", accelerator: "Alt+Command+W", click: () => command("toggleWatch") },
        ],
      },
      { role: "editMenu" },
      {
        label: "View",
        submenu: [
          { role: "reload" },
          { role: "toggleDevTools" },
          { type: "separator" },
          { role: "resetZoom" },
          { role: "zoomIn" },
          { role: "zoomOut" },
          { type: "separator" },
          { role: "togglefullscreen" },
        ],
      },
      { role: "windowMenu" },
    ])
  );
}

/* ── lifecycle ─────────────────────────────────────────────────────── */

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => toggleWindow());

  ipcMain.handle("open-external", (_e, url) => shell.openExternal(url));
  ipcMain.handle("capture", async (_e, opts) => {
    if (opts && opts.display) captureDisplayIndex = Number(opts.display);
    return captureAndPush(opts || {});
  });
  ipcMain.handle("set-watch", (_e, cfg) => {
    setWatch(!!cfg.enabled, {
      interval: (cfg.interval || 2) * 1000,
      threshold: cfg.threshold,
      autoAnalyze: cfg.auto_analyze,
      mode: cfg.mode,
      language: cfg.language,
    });
    return { enabled: watchOn };
  });
  ipcMain.handle("set-display", (_e, index) => {
    captureDisplayIndex = Number(index) || 1;
    return { display: captureDisplayIndex };
  });
  ipcMain.handle("screen-permission", async () => {
    await pushDisplays();
    return { status: screenPermission(), displays: listDisplays() };
  });
  // Kept for the screenshot-based pass; the default route is the backend's
  // quiet explore, which never touches the user's tab.
  ipcMain.handle("explore-visual", async (_e, opts) => ({
    panels: await explorePage((opts && opts.shot_id) || ""),
  }));
  ipcMain.handle("add-support", (_e, opts) => addSupportCapture(opts && opts.label));
  ipcMain.handle("request-screen-access", async () => {
    const status = await promptForScreenAccess();
    await pushDisplays();
    return { status, displays: listDisplays() };
  });

  app.whenReady().then(async () => {
    showLoader();
    buildAppMenu();

    try {
      if (!venvUsable()) {
        status("Looking for Python 3.10+…");
        const python = await findBootstrapPython();
        if (!python) {
          fail(
            "No Python 3.10 or newer was found.\n\n" +
              "Install one and reopen Screen Solver:\n    brew install python@3.12"
          );
          return;
        }
        logLine(`bootstrap interpreter: ${python}`);
        await createVenv(python);
        status("Installing dependencies…");
        await installRequirements();
      } else if (!venvCurrent()) {
        status("Updating dependencies…");
        await installRequirements();
      }

      await startBackend();
      if (!(await waitForBackend())) {
        fail("The backend did not come up in time. See the log above.");
        return;
      }

      console.log(`[solver] backend ready on http://127.0.0.1:${port}/`);
      await pushDisplays();
      screen.on("display-added", pushDisplays);
      screen.on("display-removed", pushDisplays);
      createWindow();
      createTray();
      registerHotkeys();
      console.log("[solver] window, tray and hotkeys up");
    } catch (err) {
      fail(String((err && err.message) || err));
    }
  });

  app.on("activate", () => {
    if (win) {
      win.show();
      win.focus();
    }
  });

  // The menu-bar item keeps the app alive with every window closed.
  app.on("window-all-closed", () => {});

  app.on("before-quit", () => {
    quitting = true;
    if (watchTimer) clearInterval(watchTimer);
    saveBounds();
    globalShortcut.unregisterAll();
    stopBackend();
  });

  // Last line of defence: a crash or a force-quit still gets the child killed
  // rather than leaving it listening on its port forever.
  process.on("exit", () => {
    if (backend) {
      try {
        backend.kill("SIGKILL");
      } catch {
        /* nothing left to do while exiting */
      }
    }
  });
}
