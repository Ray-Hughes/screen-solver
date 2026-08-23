/* screen-solver dashboard */
(function () {
  const $ = (id) => document.getElementById(id);

  const el = {
    connDot: $("conn-dot"), status: $("status"), modelChip: $("model-chip"),
    capture: $("btn-capture"), solve: $("btn-solve"), cancel: $("btn-cancel"),
    captureSolve: $("btn-capture-solve"),
    display: $("display"),
    watchEnabled: $("watch-enabled"), watchAuto: $("watch-auto"),
    watchAutoHint: $("watch-auto-hint"),
    watchInterval: $("watch-interval"), watchThreshold: $("watch-threshold"),
    mode: $("mode"), language: $("language"), hint: $("hint"),
    inspect: $("btn-inspect"), ctxState: $("ctx-state"), bookmarklet: $("bookmarklet"),
    explore: $("btn-explore"), exploreFirst: $("explore-first"), supports: $("supports"),
    filmstrip: $("filmstrip"),
    viewport: $("viewport"), img: $("shot-img"), viewerEmpty: $("viewer-empty"),
    selection: $("selection"), shotMeta: $("shot-meta"),
    region: $("btn-region"), fit: $("btn-fit"),
    solution: $("solution"), breakdown: $("breakdown"),
    sectionNav: $("section-nav"), usage: $("usage"),
    answerTabs: $("answer-tabs"), copySolution: $("btn-copy-solution"),
    thinking: $("thinking"), thinkingText: $("thinking-text"), thinkingBtn: $("btn-thinking"),
    transcript: $("transcript"), composer: $("composer"), chatInput: $("chat-input"),
    clearChat: $("btn-clear-chat"),
    toast: $("toast"),
    authPill: $("auth-pill"), authbar: $("authbar"), authbarText: $("authbar-text"),
    authSignin: $("authbar-signin"), authDismiss: $("authbar-dismiss"),
    authUrl: $("authbar-url"), authCodeForm: $("authbar-code"),
    authCode: $("auth-code"), authCancel: $("authbar-cancel"),
    permbar: $("permbar"), permbarText: $("permbar-text"),
    permOpen: $("permbar-open"), permRecheck: $("permbar-recheck"),
    permGrant: $("permbar-grant"),
    settingsBtn: $("btn-settings"), settings: $("settings"),
    settingsSave: $("settings-save"), settingsFile: $("settings-file"),
    settingsStatus: $("settings-status"),
    setProvider: $("set-provider"), setBaseUrl: $("set-base-url"),
    setApiKey: $("set-api-key"), setModel: $("set-model"),
    modelOptions: $("model-options"), modelHint: $("model-hint"),
    setEffort: $("set-effort"), setTools: $("set-tools"),
    setMaxTokens: $("set-max-tokens"), setTemperature: $("set-temperature"),
    setMaxEdge: $("set-max-edge"), setWatchIntervalCfg: $("set-watch-interval"),
    setKeepShots: $("set-keep-shots"),
    rowBaseUrl: $("row-base-url"), rowApiKey: $("row-api-key"),
    rowEffort: $("row-effort"), rowTools: $("row-tools"),
    refreshModels: $("btn-refresh-models"),
  };

  const state = {
    shotId: null,
    mode: "auto",
    region: null,        // {x,y,w,h} normalised
    selecting: false,
    stream: null,        // "analysis" | "followup" | null
    buffer: "",
    chatBubble: null,
    chatBuffer: "",
    fit: true,
    busy: false,
    auth: null,
    authDismissed: false,
    lastSolve: null,
  };

  /* ── helpers ────────────────────────────────────────────────────── */

  let toastTimer;
  function toast(msg, isErr) {
    el.toast.textContent = msg;
    el.toast.className = "toast" + (isErr ? " err" : "");
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.toast.hidden = true), 4200);
  }

  function setStatus(text, kind) {
    el.status.textContent = text;
    el.connDot.className = "dot " + (kind || "live");
  }

  function setBusy(b) {
    state.busy = b;
    el.cancel.hidden = !b;
    el.solve.disabled = b;
    el.captureSolve.disabled = b;
  }

  async function api(path, body, method) {
    const res = await fetch(path, {
      method: method || (body ? "POST" : "GET"),
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { detail: text }; }
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  function opts() {
    return {
      mode: state.mode,
      language: el.language.value.trim(),
      hint: el.hint.value.trim(),
      region: state.region,
      shot_id: state.shotId,
    };
  }

  /* ── solution pane ──────────────────────────────────────────────── */

  /* Which of the two panes is showing. Remembered, because whether you want
     the working or just the answer is a habit, not a per-solve decision. */
  state.view = localStorage.getItem("answer-view") === "solution" ? "solution" : "breakdown";

  function showView(name) {
    state.view = name;
    localStorage.setItem("answer-view", name);
    el.solution.hidden = name !== "solution";
    el.breakdown.hidden = name !== "breakdown";
    // The section jumps belong to the breakdown; on the Solution tab there is
    // only ever one section, so the strip would be noise.
    el.sectionNav.hidden = name !== "breakdown" || !el.sectionNav.children.length;
    el.copySolution.hidden = name !== "solution" || !state.solutionCode;
    el.answerTabs.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("on", b.dataset.view === name);
    });
  }

  showView(state.view); // honour the remembered tab before anything renders

  let renderQueued = false;
  function renderSolution() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;

      const near =
        el.breakdown.scrollHeight - el.breakdown.scrollTop - el.breakdown.clientHeight < 120;
      el.breakdown.innerHTML = MD.render(state.buffer);
      wireCopy(el.breakdown);
      if (near) el.breakdown.scrollTop = el.breakdown.scrollHeight;

      const hs = MD.headings(state.buffer);
      el.sectionNav.innerHTML = hs
        .map((h) => `<button data-target="${h.id}">${h.text}</button>`)
        .join("");

      // The clean pane is the ## Solution section on its own.
      const body = MD.section(state.buffer, "Solution");
      state.solutionCode = MD.firstCode(body);
      el.solution.innerHTML = body
        ? MD.render(body)
        : '<div class="empty small"><p>' +
          (state.stream ? "Still working\u2026" : "No solution section in this answer.") +
          "</p></div>";
      wireCopy(el.solution);

      // A quiet mark on the tab you are not looking at, so a finished answer
      // is discoverable without switching.
      const tab = el.answerTabs.querySelector('[data-view="solution"]');
      tab.innerHTML = "Solution" + (body && state.view !== "solution" ? '<i class="dot-badge"></i>' : "");

      showView(state.view);
    });
  }

  el.answerTabs.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (btn) showView(btn.dataset.view);
  });

  el.copySolution.onclick = async () => {
    if (!state.solutionCode) return;
    await navigator.clipboard.writeText(state.solutionCode);
    toast("Solution copied.");
  };

  el.sectionNav.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const node = el.breakdown.querySelector("#" + CSS.escape(btn.dataset.target));
    if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  function wireCopy(root) {
    root.querySelectorAll("pre .copy").forEach((b) => {
      b.onclick = () => {
        navigator.clipboard.writeText(b.parentElement.querySelector("code").textContent);
        b.textContent = "copied";
        setTimeout(() => (b.textContent = "copy"), 1200);
      };
    });
  }

  /* ── errors ─────────────────────────────────────────────────────── */

  const esc = (t) =>
    String(t == null ? "" : t)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function errorCard(d) {
    const wrap = document.createElement("div");
    wrap.className = "errorcard";

    const actions = (d.actions || []).map((a) =>
      a.url
        ? `<a class="btn accent" href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.label)}</a>`
        : `<button class="btn" data-command="${esc(a.command)}">${esc(a.label)}</button>`
    );
    if (d.retryable && state.lastSolve) {
      actions.push('<button class="btn" data-retry>Retry</button>');
    }

    const technical = [d.raw, d.request_id ? `request_id: ${d.request_id}` : ""]
      .filter(Boolean)
      .join("\n\n");

    wrap.innerHTML = `
      <div class="errorcard-head">
        <span class="errorcard-icon">!</span>
        <h3>${esc(d.title || d.message || "Something went wrong")}</h3>
      </div>
      ${d.detail ? `<p>${esc(d.detail)}</p>` : ""}
      ${d.hint ? `<p class="hint">${esc(d.hint)}</p>` : ""}
      ${actions.length ? `<div class="errorcard-actions">${actions.join("")}</div>` : ""}
      ${technical ? `<details><summary>Technical details</summary><pre>${esc(technical)}</pre></details>` : ""}
    `;

    wrap.querySelectorAll("[data-command]").forEach((b) => {
      b.onclick = () => {
        if (b.dataset.command === "signin") signIn();
        else if (b.dataset.command === "privacy") el.permOpen.click();
      };
    });
    const retry = wrap.querySelector("[data-retry]");
    if (retry) retry.onclick = () => retrySolve();
    return wrap;
  }

  async function retrySolve() {
    if (!state.lastSolve) return;
    try {
      startAnalysisPane();
      await api("/api/analyze", state.lastSolve);
    } catch (e) {
      setBusy(false);
      toast(e.message, true);
    }
  }

  function showError(d) {
    const card = errorCard(d);
    if (state.stream === "followup") {
      clearPlaceholder();
      el.transcript.appendChild(card);
      el.transcript.scrollTop = el.transcript.scrollHeight;
    } else {
      el.breakdown.innerHTML = "";
      el.breakdown.appendChild(card);
      showView("breakdown");
    }
    addEvent(d.title || d.message, "err");
  }

  /* ── chat pane ──────────────────────────────────────────────────── */

  function clearPlaceholder() {
    const ph = el.transcript.querySelector(".empty");
    if (ph) ph.remove();
  }

  function addMsg(role, text) {
    clearPlaceholder();
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = '<div class="markdown">' + MD.render(text) + "</div>";
    el.transcript.appendChild(div);
    el.transcript.scrollTop = el.transcript.scrollHeight;
    return div;
  }

  function addEvent(text, kind) {
    clearPlaceholder();
    const div = document.createElement("div");
    div.className = "event " + (kind || "");
    div.textContent = text;
    el.transcript.appendChild(div);
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  /* ── viewer ─────────────────────────────────────────────────────── */

  function showShot(meta) {
    state.shotId = meta.id;
    el.img.src = `/api/shots/${meta.id}.png?ts=${meta.ts}`;
    el.img.hidden = false;
    el.viewerEmpty.hidden = true;
    const when = new Date(meta.ts * 1000).toLocaleTimeString();
    el.shotMeta.textContent = `${meta.width}×${meta.height} · display ${meta.display} · ${when}`;
    clearRegion();
    markFilmstrip();
    el.ctxState.textContent = meta.has_page_context ? "page context attached" : "no page context";
    el.ctxState.className = "ctx-state" + (meta.has_page_context ? " ok" : "");
    renderSupports(meta);
  }

  /* The other panels captured for this shot. Clicking one swaps it into the
     viewer, so you can check what the model was actually given. */
  function renderSupports(meta) {
    const list = (meta && meta.supports) || [];
    state.supports = list;
    el.supports.hidden = !list.length;
    if (!list.length) return;
    el.supports.innerHTML =
      '<span class="supports-label">also sent</span>' +
      `<button class="support on" data-index="-1">
         <img src="/api/shots/${meta.id}/thumb.jpg" alt="" /><span>main capture</span>
       </button>` +
      list
        .map(
          (sup) => `<button class="support" data-index="${sup.index}">
             <img src="/api/shots/${meta.id}/support/${sup.index}/thumb.jpg" alt="" />
             <span>${sup.label}</span>
           </button>`
        )
        .join("");
  }

  /** Put one of this shot's captures in the viewer. -1 is the main one. */
  function showSupport(index) {
    if (!state.shotId) return;
    el.img.src =
      index < 0
        ? `/api/shots/${state.shotId}.png`
        : `/api/shots/${state.shotId}/support/${index}.png`;
    el.supports.querySelectorAll(".support").forEach((n) => {
      n.classList.toggle("on", Number(n.dataset.index) === index);
    });
    clearRegion();
  }

  el.supports.addEventListener("click", (e) => {
    const btn = e.target.closest(".support");
    if (btn) showSupport(Number(btn.dataset.index));
  });

  function markFilmstrip() {
    el.filmstrip.querySelectorAll("img").forEach((n) =>
      n.classList.toggle("on", n.dataset.id === state.shotId)
    );
  }

  function addToFilmstrip(meta, prepend) {
    const img = document.createElement("img");
    img.src = `/api/shots/${meta.id}/thumb.jpg`;
    img.dataset.id = meta.id;
    img.title = new Date(meta.ts * 1000).toLocaleTimeString();
    img.onclick = () => showShot(meta);
    if (prepend) el.filmstrip.prepend(img);
    else el.filmstrip.appendChild(img);
    while (el.filmstrip.children.length > 40) el.filmstrip.lastChild.remove();
  }

  function clearRegion() {
    state.region = null;
    el.selection.hidden = true;
    el.region.classList.remove("on");
    state.selecting = false;
    el.viewport.classList.remove("selecting");
  }

  el.region.onclick = () => {
    if (state.region) { clearRegion(); return; }
    state.selecting = !state.selecting;
    el.region.classList.toggle("on", state.selecting);
    el.viewport.classList.toggle("selecting", state.selecting);
  };

  el.fit.onclick = () => {
    state.fit = !state.fit;
    el.img.classList.toggle("actual", !state.fit);
    el.fit.textContent = state.fit ? "Fit" : "100%";
  };

  (function regionDrag() {
    let start = null;
    el.viewport.addEventListener("mousedown", (e) => {
      if (!state.selecting || el.img.hidden) return;
      const r = el.img.getBoundingClientRect();
      start = { x: e.clientX, y: e.clientY, r };
      el.selection.hidden = false;
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!start) return;
      const vp = el.viewport.getBoundingClientRect();
      const x1 = Math.min(start.x, e.clientX), x2 = Math.max(start.x, e.clientX);
      const y1 = Math.min(start.y, e.clientY), y2 = Math.max(start.y, e.clientY);
      Object.assign(el.selection.style, {
        left: x1 - vp.left + el.viewport.scrollLeft + "px",
        top: y1 - vp.top + el.viewport.scrollTop + "px",
        width: x2 - x1 + "px",
        height: y2 - y1 + "px",
      });
    });
    window.addEventListener("mouseup", (e) => {
      if (!start) return;
      const r = start.r;
      const x1 = Math.min(start.x, e.clientX), x2 = Math.max(start.x, e.clientX);
      const y1 = Math.min(start.y, e.clientY), y2 = Math.max(start.y, e.clientY);
      start = null;
      const w = (x2 - x1) / r.width, h = (y2 - y1) / r.height;
      if (w < 0.02 || h < 0.02) { clearRegion(); return; }
      state.region = {
        x: Math.max(0, (x1 - r.left) / r.width),
        y: Math.max(0, (y1 - r.top) / r.height),
        w, h,
      };
      state.selecting = false;
      el.viewport.classList.remove("selecting");
      el.region.classList.add("on");
      toast("Region set — only this area will be sent.");
    });
  })();

  /* ── desktop shell ──────────────────────────────────────────────── */

  function applyDesktop() {
    const d = window.solverDesktop;
    if (!d) return;
    document.body.classList.add("desktop");

    const labels = {
      captureSolve: "Capture & solve",
      capture: "Capture",
      toggleWatch: "Toggle watch",
      toggleWindow: "Show / hide this window",
    };
    const list = document.getElementById("hotkeys-list");
    list.innerHTML = Object.keys(labels)
      .filter((k) => d.hotkeys[k])
      .map((k) => `<dt>${d.hotkeys[k]}</dt><dd>${labels[k]}</dd>`)
      .join("");
    document.getElementById("hotkeys").hidden = false;

    // A failed explore pass is reported but never blocks the solve that
    // follows it — the pixels are still there.
    if (d.onExploreError) {
      d.onExploreError(({ message }) => {
        addEvent(`explore failed: ${message}`, "err");
        toast(`Could not explore the page: ${message}`, true);
      });
    }

    // Narrate the pass. Opening tabs on another display is invisible from
    // here, so without this it just looks like a long pause.
    if (d.onExploreProgress) {
      d.onExploreProgress((p) => {
        switch (p.phase) {
          case "planning":
            setStatus("reading the page…", "busy");
            break;
          case "start":
            if (!p.total) {
              addEvent("explore: no other panels to open", "tool");
              break;
            }
            addEvent(`explore: opening ${p.total} panel(s) — ${p.tabs.join(", ")}`, "tool");
            break;
          case "opening":
            setStatus(`exploring ${p.index}/${p.total} — ${p.label}…`, "busy");
            break;
          case "captured":
            addEvent(`explore: captured “${p.label}” (${p.index}/${p.total})`, "tool");
            break;
          case "failed":
            addEvent(`explore: could not open “${p.label}” — ${p.message}`, "err");
            break;
          case "restoring":
            setStatus(`restoring “${p.label}”…`, "busy");
            break;
          case "done":
            setStatus(
              p.panels.length ? `explored ${p.panels.length} panel(s)` : "nothing to explore",
              "live"
            );
            break;
        }
      });
    }

    d.onCaptureError(({ message }) => {
      setStatus("capture failed", "err");
      toast(message, true);
    });

    d.onWatch(({ enabled }) => {
      // Keeps the checkbox honest when the tray or a hotkey toggles watch.
      el.watchEnabled.checked = !!enabled;
    });

    // Open every external link in the real browser, not in the app window.
    document.addEventListener("click", (e) => {
      const a = e.target.closest('a[href^="http"]');
      if (!a) return;
      e.preventDefault();
      d.openExternal(a.href);
    });
  }

  /* ── screen recording permission ────────────────────────────────── */

  const PRIVACY_PANE =
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";

  function applyCaptureError(message) {
    if (!message) {
      el.permbar.hidden = true;
      document.body.classList.remove("has-permbar");
      return;
    }
    el.permbarText.textContent = message;
    el.permbar.hidden = false;
    document.body.classList.add("has-permbar");
  }

  function renderDisplays(list, selected) {
    el.display.innerHTML = list
      .map((d) => `<option value="${d.index}">${d.label}</option>`)
      .join("");
    if (selected) el.display.value = String(selected);
  }

  el.permOpen.onclick = () => {
    if (window.solverDesktop) window.solverDesktop.openExternal(PRIVACY_PANE);
    else window.location.href = PRIVACY_PANE;
  };

  // Only the Electron shell can ask macOS; in a plain browser tab there is
  // no bundle to grant anything to.
  el.permGrant.hidden = !window.solverDesktop;
  el.permGrant.onclick = async () => {
    el.permGrant.disabled = true;
    try {
      const { status } = await window.solverDesktop.requestScreenAccess();
      toast(
        status === "granted"
          ? "Screen Recording is granted."
          : "Turn the Screen Solver toggle on in Privacy Settings, then reopen the app.",
        status !== "granted"
      );
    } catch (e) {
      toast(e.message, true);
    } finally {
      el.permGrant.disabled = false;
    }
  };

  el.permRecheck.onclick = async () => {
    el.permRecheck.disabled = true;
    try {
      if (window.solverDesktop) {
        const { status } = await window.solverDesktop.screenPermission();
        // The shell pushes the fresh list to the backend, which broadcasts it.
        toast(
          status === "granted"
            ? "Screen Recording is granted."
            : `Still ${status}. Quit and reopen the app after enabling it.`,
          status !== "granted"
        );
      } else {
        const r = await api("/api/displays/refresh", {});
        renderDisplays(r.displays, Number(el.display.value));
        applyCaptureError(r.capture_error);
        toast(r.capture_error ? "Still blocked." : "Screen capture is available.");
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      el.permRecheck.disabled = false;
    }
  };

  /* ── auth ───────────────────────────────────────────────────────── */

  /* A local model has no account to sign in to, so the Anthropic chip and
     the sign-in bar are meaningless — hide them and name the model instead. */
  function applyProvider(p, s) {
    state.provider = p || { provider: "anthropic", needs_signin: true };
    el.modelChip.textContent =
      (p && p.label) || `${s.model}${s.effort ? ` · ${s.effort}` : ""}`;
    if (p && p.base_url) el.modelChip.title = p.base_url;

    if (state.provider.needs_signin === false) {
      el.authPill.hidden = true;
      hideAuthbar();
      if (p && p.tools === false) {
        addEvent("This model has no tool support — page inspection is off.");
      }
    }
  }

  function applyAuth(a) {
    // Nothing to authenticate against when the model is local.
    if (state.provider && state.provider.needs_signin === false) return;

    state.auth = a;
    const pill = el.authPill;

    if (a.pending) {
      authPending({ message: "Starting sign-in…" });
      return;
    }

    resetAuthbarControls();

    if (a.signed_in) {
      pill.className = "chip auth in";
      pill.textContent =
        a.source === "profile" ? `signed in · ${a.profile}` : a.detail;
      pill.title =
        a.source === "profile"
          ? "Click to sign out"
          : "Authenticated from the environment";
      hideAuthbar();
      if (a.warnings && a.warnings.length) toast(a.warnings[0], true);
      return;
    }

    pill.className = "chip auth out";
    pill.textContent = "sign in";
    pill.title = "Sign in to your Anthropic account";
    showAuthbar(
      a.ant_installed
        ? "Not signed in — capture works, solving does not."
        : "The Anthropic CLI is not installed. Run: brew install anthropics/tap/ant"
    );
    el.authSignin.disabled = !a.ant_installed;
  }

  function showAuthbar(text) {
    if (state.authDismissed) return;
    el.authbarText.textContent = text;
    el.authbar.hidden = false;
    document.body.classList.add("has-authbar");
  }

  function authPending(d) {
    // The CLI blocks for minutes; forward whatever it says so the wait is legible.
    state.authDismissed = false;
    el.authPill.className = "chip auth pending";
    el.authPill.textContent = "signing in…";
    showAuthbar(d.message || "Waiting for the browser…");
    el.authSignin.hidden = true;
    el.authCancel.hidden = false;

    if (d.url) {
      el.authUrl.href = d.url;
      el.authUrl.hidden = false;
    }
    if (d.needs_code) {
      el.authCodeForm.hidden = false;
      el.authCode.focus();
    }
  }

  function resetAuthbarControls() {
    el.authSignin.hidden = false;
    el.authCancel.hidden = true;
    el.authCodeForm.hidden = true;
    el.authUrl.hidden = true;
    el.authCode.value = "";
  }

  function hideAuthbar() {
    el.authbar.hidden = true;
    document.body.classList.remove("has-authbar");
  }

  async function signIn() {
    try {
      state.authDismissed = false;
      applyAuth({ ...(state.auth || {}), pending: true });
      await api("/api/auth/login", {});
      toast("Complete the sign-in in your browser.");
    } catch (e) {
      applyAuth(state.auth || { signed_in: false, ant_installed: true });
      toast(e.message, true);
    }
  }

  async function signOut() {
    try {
      applyAuth(await api("/api/auth/logout", {}));
      toast("Signed out.");
    } catch (e) {
      toast(e.message, true);
    }
  }

  el.authPill.onclick = () => {
    const a = state.auth;
    if (a && a.signed_in && a.source === "profile") signOut();
    else if (!a || !a.signed_in) signIn();
    else toast(a.detail);
  };
  el.authSignin.onclick = signIn;
  el.authDismiss.onclick = () => {
    state.authDismissed = true;
    hideAuthbar();
  };
  el.authCancel.onclick = async () => {
    await api("/api/auth/cancel", {}).catch(() => {});
    applyAuth(state.auth || { signed_in: false, ant_installed: true });
  };
  el.authCodeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = el.authCode.value.trim();
    if (!code) return;
    try {
      await api("/api/auth/code", { code });
      el.authCodeForm.hidden = true;
      el.authCode.value = "";
      showAuthbar("Code submitted — finishing sign-in…");
    } catch (err) {
      toast(err.message, true);
    }
  });

  /* ── settings ───────────────────────────────────────────────────── */

  (function settingsPanel() {
    let schema = null;

    const options = (sel, values, current) => {
      sel.innerHTML = values
        .map((v) => {
          const value = typeof v === "string" ? v : v.value;
          const label = typeof v === "string" ? v : v.label;
          return `<option value="${value}">${label}</option>`;
        })
        .join("");
      if (current != null) sel.value = String(current);
    };

    const isLocalProvider = (name) =>
      !!(schema && schema.providers.find((p) => p.value === name && p.local));
    const isAnthropic = (name) => name === "anthropic";

    /* Which rows make sense depends on the provider, so re-derive on change. */
    function syncRows() {
      const provider = el.setProvider.value;
      const anthropic = isAnthropic(provider);
      el.rowBaseUrl.hidden = anthropic;
      el.rowApiKey.hidden = anthropic || isLocalProvider(provider);
      el.rowEffort.hidden = !anthropic;
      el.rowTools.hidden = anthropic;
      el.modelHint.textContent = anthropic
        ? "Every Claude model here reads images."
        : "Every solve sends a screenshot, so this must be a vision model.";
    }

    function fill(cfg) {
      schema = cfg;
      const v = cfg.values;
      options(el.setProvider, cfg.providers, v.SOLVER_PROVIDER);
      options(el.setEffort, cfg.efforts, v.SOLVER_EFFORT);
      options(el.setTools, cfg.tool_modes, v.SOLVER_TOOLS);
      el.setBaseUrl.value = v.SOLVER_BASE_URL || "";
      el.setApiKey.value = v.SOLVER_API_KEY || "";
      el.setModel.value = v.SOLVER_MODEL || "";
      el.setMaxTokens.value = v.SOLVER_MAX_TOKENS;
      el.setTemperature.value = v.SOLVER_TEMPERATURE;
      el.setMaxEdge.value = v.SOLVER_MAX_EDGE;
      el.setWatchIntervalCfg.value = v.SOLVER_WATCH_INTERVAL;
      el.setKeepShots.value = v.SOLVER_KEEP_SHOTS;
      el.settingsFile.textContent = `Saved to ${cfg.file}`;
      syncRows();
    }

    async function loadModels(announce) {
      el.refreshModels.disabled = true;
      el.settingsStatus.textContent = "Looking for models…";
      try {
        // Ask about the provider the form is currently showing, which may
        // not be the one that is saved yet.
        const q = new URLSearchParams({ provider: el.setProvider.value });
        if (!isAnthropic(el.setProvider.value)) q.set("base_url", el.setBaseUrl.value.trim());
        const r = await api(`/api/models?${q}`);
        const bytes = (n) => (n ? `${(n / 1e9).toFixed(1)} GB` : "");
        const usable = r.models.filter((m) => m.id && m.usable !== false);
        el.modelOptions.innerHTML = usable
          .map((m) => {
            const tags = [m.vision === true ? "vision" : "", bytes(m.size)]
              .filter(Boolean)
              .join(" · ");
            return `<option value="${m.id}">${tags}</option>`;
          })
          .join("");
        const blind = usable.filter((m) => m.vision === false).length;
        el.settingsStatus.textContent = !usable.length
          ? "No usable models — pull one, e.g. `ollama pull qwen2.5vl:7b`."
          : blind
          ? `${usable.length} available — ${blind} cannot read images.`
          : `${usable.length} available.`;
      } catch (e) {
        el.settingsStatus.textContent = e.message;
        if (announce) toast(e.message, true);
      } finally {
        el.refreshModels.disabled = false;
      }
    }

    async function open() {
      try {
        fill(await api("/api/config"));
      } catch (e) {
        toast(e.message, true);
        return;
      }
      el.settings.hidden = false;
      el.settingsStatus.textContent = "";
      loadModels(false);
      el.setModel.focus();
    }

    const close = () => {
      el.settings.hidden = true;
    };

    async function save() {
      el.settingsSave.disabled = true;
      el.settingsStatus.textContent = "Saving…";
      const body = {
        SOLVER_PROVIDER: el.setProvider.value,
        SOLVER_MODEL: el.setModel.value.trim(),
        SOLVER_BASE_URL: isAnthropic(el.setProvider.value) ? "" : el.setBaseUrl.value.trim(),
        SOLVER_API_KEY: el.setApiKey.value.trim(),
        SOLVER_EFFORT: el.setEffort.value,
        SOLVER_TOOLS: el.setTools.value,
        SOLVER_MAX_TOKENS: el.setMaxTokens.value,
        SOLVER_TEMPERATURE: el.setTemperature.value,
        SOLVER_MAX_EDGE: el.setMaxEdge.value,
        SOLVER_WATCH_INTERVAL: el.setWatchIntervalCfg.value,
        SOLVER_KEEP_SHOTS: el.setKeepShots.value,
      };
      try {
        const r = await api("/api/config", body);
        applyProvider(r.provider, r);
        el.watchInterval.value = body.SOLVER_WATCH_INTERVAL;
        toast(
          r.restart_required.length
            ? `Saved. Reopen the app to apply: ${r.restart_required.join(", ")}.`
            : `Now using ${r.provider.label}.`
        );
        close();
      } catch (e) {
        el.settingsStatus.textContent = e.message;
        toast(e.message, true);
      } finally {
        el.settingsSave.disabled = false;
      }
    }

    el.settingsBtn.onclick = open;
    el.settingsSave.onclick = save;
    el.refreshModels.onclick = () => loadModels(true);
    el.setProvider.onchange = () => {
      // Picking a named runner means picking its usual port; "Custom" leaves
      // whatever address is already there alone.
      const preset = schema && schema.presets[el.setProvider.value];
      if (preset) el.setBaseUrl.value = preset;
      // A ceiling saved for one provider is usually wrong for the other:
      // a local model's 4096 would quietly truncate Claude's answers.
      if (schema) {
        el.setMaxTokens.value =
          schema.max_token_defaults[
            isAnthropic(el.setProvider.value) ? "anthropic" : "local"
          ];
      }
      syncRows();
      loadModels(false);
    };
    el.settings.querySelectorAll("[data-close]").forEach((n) => (n.onclick = close));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !el.settings.hidden) close();
    });
  })();

  /* ── column resizers ────────────────────────────────────────────── */

  (function columnSplitters() {
    const grid = document.querySelector(".grid");
    if (!grid) return;
    const PAD = 12; // .grid padding, so the pointer lands on the panel edge
    const BARS = [
      { id: "split-left", prop: "--rail-w", from: "left", min: 200, max: 520 },
      { id: "split-right", prop: "--chat-w", from: "right", min: 300, max: 760 },
    ];

    for (const cfg of BARS) {
      const saved = localStorage.getItem(cfg.prop);
      if (saved) grid.style.setProperty(cfg.prop, saved);

      const bar = document.getElementById(cfg.id);
      if (!bar) continue;

      bar.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        bar.setPointerCapture(e.pointerId);
        document.body.classList.add("resizing-x");
        const rect = grid.getBoundingClientRect();

        const move = (ev) => {
          const raw =
            cfg.from === "left"
              ? ev.clientX - rect.left - PAD
              : rect.right - ev.clientX - PAD;
          const px = Math.max(cfg.min, Math.min(cfg.max, raw));
          grid.style.setProperty(cfg.prop, `${Math.round(px)}px`);
        };
        const up = () => {
          document.body.classList.remove("resizing-x");
          localStorage.setItem(cfg.prop, grid.style.getPropertyValue(cfg.prop));
          bar.removeEventListener("pointermove", move);
          bar.removeEventListener("pointerup", up);
          bar.removeEventListener("pointercancel", up);
        };
        bar.addEventListener("pointermove", move);
        bar.addEventListener("pointerup", up);
        bar.addEventListener("pointercancel", up);
      });

      bar.addEventListener("dblclick", () => {
        grid.style.removeProperty(cfg.prop);
        localStorage.removeItem(cfg.prop);
      });
    }
  })();

  /* ── splitter ───────────────────────────────────────────────────── */

  (function splitter() {
    const bar = document.getElementById("splitter");
    const stage = bar.parentElement;
    const saved = localStorage.getItem("split");
    if (saved) stage.style.setProperty("--split", saved);
    let dragging = false;
    bar.addEventListener("mousedown", () => {
      dragging = true;
      document.body.classList.add("resizing");
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const r = stage.getBoundingClientRect();
      const pct = Math.min(80, Math.max(14, ((e.clientY - r.top) / r.height) * 100));
      stage.style.setProperty("--split", pct.toFixed(1) + "%");
    });
    window.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove("resizing");
      localStorage.setItem("split", stage.style.getPropertyValue("--split"));
    });
  })();

  /* ── actions ────────────────────────────────────────────────────── */

  async function doCapture(andSolve) {
    const body = {
      display: Number(el.display.value) || undefined,
      analyze: !!andSolve,
      ...opts(),
    };
    try {
      setStatus("capturing…", "busy");
      // In the desktop app the frame is grabbed by the app bundle itself, so
      // macOS attributes Screen Recording to Screen Solver. In a browser the
      // backend shells out instead.
      const meta = window.solverDesktop
        ? await window.solverDesktop.capture(body)
        : await api("/api/capture", body);
      if (andSolve) {
        state.lastSolve = { ...opts(), shot_id: meta.id };
        startAnalysisPane();
      }
      setStatus("captured", "live");
      return meta;
    } catch (e) {
      setStatus("capture failed", "err");
      showError({
        kind: "capture",
        title: "Could not capture the screen",
        detail: e.message,
        hint: "If macOS has not granted Screen Recording to Screen Solver, "
            + "enable it in Privacy Settings, then quit and reopen the app.",
        actions: [{ label: "Open Privacy Settings", command: "privacy" }],
      });
    }
  }

  function startAnalysisPane() {
    state.buffer = "";
    state.stream = "analysis";
    el.usage.textContent = "";
    el.thinkingText.textContent = "";
    el.breakdown.innerHTML = '<div class="empty small"><p>thinking…</p></div>';
    setBusy(true);
  }

  async function doSolve() {
    if (!state.shotId) { await doCapture(false); }
    if (!state.shotId) return;
    try {
      startAnalysisPane();
      state.lastSolve = opts();
      await api("/api/analyze", state.lastSolve);
    } catch (e) {
      setBusy(false);
      toast(e.message, true);
    }
  }

  el.capture.onclick = () => doCapture(false);
  el.solve.onclick = doSolve;
  el.captureSolve.onclick = () => doCapture(true);
  el.cancel.onclick = () => api("/api/cancel", {});


  el.exploreFirst.onchange = async () => {
    try {
      await api("/api/settings", { explore: el.exploreFirst.checked });
    } catch (e) {
      toast(e.message, true);
    }
  };

  el.explore.onclick = async () => {
    if (!state.shotId) {
      toast("Capture something first.", true);
      return;
    }
    el.explore.disabled = true;
    const label = el.explore.textContent;
    el.explore.textContent = "Exploring…";
    try {
      const r = await api("/api/explore", { shot_id: state.shotId });
      const n = (r.panels || []).length;
      toast(n ? `Read ${n} more panel(s).` : "No other panels to read.");
    } catch (e) {
      toast(e.message, true);
      addEvent(e.message, "err");
    } finally {
      el.explore.textContent = label;
      el.explore.disabled = false;
    }
  };

  el.inspect.onclick = async () => {
    el.inspect.disabled = true;
    el.ctxState.textContent = "inspecting…";
    try {
      const r = await api("/api/inspect", { shot_id: state.shotId });
      el.ctxState.textContent = `${r.chars.toLocaleString()} chars from ${r.browser}`;
      el.ctxState.className = "ctx-state ok";
      addEvent(`inspected ${r.url || "page"} — ${r.chars} chars`, "tool");
    } catch (e) {
      el.ctxState.textContent = e.message;
      el.ctxState.className = "ctx-state";
      toast(e.message, true);
    } finally {
      el.inspect.disabled = false;
    }
  };

  el.mode.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.mode = b.dataset.mode;
    el.mode.querySelectorAll("button").forEach((n) => n.classList.toggle("on", n === b));
  });

  function pushWatch() {
    const cfg = {
      enabled: el.watchEnabled.checked,
      auto_analyze: el.watchAuto.checked,
      interval: Number(el.watchInterval.value),
      threshold: Number(el.watchThreshold.value),
      mode: state.mode,
      language: el.language.value.trim(),
    };
    if (window.solverDesktop) window.solverDesktop.setWatch(cfg);
    api("/api/watch", cfg).catch((e) => toast(e.message, true));
  }
  /* "Solve each auto-capture" does nothing unless watch mode is on, which
     is not obvious from a checkbox sitting right under it. */
  function syncWatchControls() {
    const on = el.watchEnabled.checked;
    el.watchAuto.disabled = !on;
    el.watchAuto.parentElement.style.opacity = on ? "" : ".5";
    el.watchAuto.parentElement.title = on
      ? ""
      : "Turn on Auto-capture on screen change first.";
  }
  el.watchEnabled.addEventListener("change", syncWatchControls);
  syncWatchControls();

  [el.watchEnabled, el.watchAuto, el.watchInterval, el.watchThreshold].forEach((n) =>
    n.addEventListener("change", pushWatch)
  );

  el.display.addEventListener("change", () => {
    const index = Number(el.display.value);
    if (window.solverDesktop) window.solverDesktop.setDisplay(index);
    api("/api/settings", { capture_display: index });
  });

  el.thinkingBtn.onclick = () => {
    el.thinking.hidden = !el.thinking.hidden;
    el.thinkingBtn.classList.toggle("on", !el.thinking.hidden);
  };

  el.clearChat.onclick = () => {
    el.transcript.innerHTML = '<div class="empty small"><p>Cleared.</p></div>';
  };

  el.composer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = el.chatInput.value.trim();
    if (!q) return;
    if (!state.shotId) { toast("Capture and solve something first.", true); return; }
    el.chatInput.value = "";
    addMsg("user", q);
    state.chatBuffer = "";
    state.chatBubble = null;
    state.stream = "followup";
    setBusy(true);
    try {
      await api("/api/followup", { shot_id: state.shotId, question: q });
    } catch (err) {
      setBusy(false);
      addEvent(err.message, "err");
    }
  });

  el.chatInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    // Enter sends; Shift+Enter is a newline. ⌘/Ctrl+Enter still sends too,
    // since that was the binding before.
    if (e.shiftKey && !(e.metaKey || e.ctrlKey)) return;
    // Don't send half a word while an IME candidate window is open.
    if (e.isComposing) return;
    e.preventDefault();
    el.composer.requestSubmit();
  });

  document.addEventListener("keydown", (e) => {
    // The settings panel owns the keyboard while it is up, or Space on a
    // focused button in it would fire a capture behind the modal.
    if (!el.settings.hidden) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (typing) return;
    if (e.code === "Space") { e.preventDefault(); doCapture(false); }
    else if (e.key === "s" || e.key === "S") { e.preventDefault(); doSolve(); }
    else if (e.key === "r" || e.key === "R") { el.region.click(); }
    else if (e.key === "Escape") { clearRegion(); }
  });

  // Test hooks: let a harness drive the panes without a live API call.
  document.addEventListener("__inject", (e) => showError(e.detail));
  document.addEventListener("__render", (e) => {
    state.buffer = e.detail;
    renderSolution();
  });

  /* ── event stream ───────────────────────────────────────────────── */

  function connect() {
    const es = new EventSource("/api/events");

    es.addEventListener("open", () => setStatus("connected", "live"));
    es.addEventListener("error", () => setStatus("reconnecting…", "err"));

    es.addEventListener("shot", (e) => {
      const meta = JSON.parse(e.data);
      addToFilmstrip(meta, true);
      showShot(meta);
    });

    es.addEventListener("analysis_start", () => {
      state.stream = "analysis";
      state.buffer = "";
      setBusy(true);
      setStatus("reading the screen…", "busy");
      addEvent("analysing capture");
    });

    es.addEventListener("followup_start", () => {
      state.stream = "followup";
      state.chatBuffer = "";
      state.chatBubble = null;
      setBusy(true);
      setStatus("answering…", "busy");
    });

    es.addEventListener("thinking_delta", (e) => {
      const { text } = JSON.parse(e.data);
      el.thinkingText.textContent += text;
      el.thinking.scrollTop = el.thinking.scrollHeight;
    });

    es.addEventListener("text_delta", (e) => {
      const { text } = JSON.parse(e.data);
      if (state.stream === "followup") {
        state.chatBuffer += text;
        if (!state.chatBubble) state.chatBubble = addMsg("bot", "");
        state.chatBubble.querySelector(".markdown").innerHTML = MD.render(state.chatBuffer);
        wireCopy(state.chatBubble);
        el.transcript.scrollTop = el.transcript.scrollHeight;
      } else {
        state.buffer += text;
        setStatus("writing the breakdown…", "busy");
        renderSolution();
      }
    });

    es.addEventListener("tool_start", (e) => {
      const d = JSON.parse(e.data);
      const label = d.input && (d.input.label || d.input.reason) || "";
      addEvent(`→ ${d.name}${label ? " · " + label : ""}`, "tool");
      setStatus(`tool: ${d.name}`, "busy");
    });

    es.addEventListener("tool_end", (e) => {
      const d = JSON.parse(e.data);
      addEvent(`✓ ${d.name}${d.ok ? "" : " (failed)"} — ${(d.preview || "").slice(0, 90)}`,
        d.ok ? "tool" : "err");
    });

    es.addEventListener("page_context", (e) => {
      const d = JSON.parse(e.data);
      el.ctxState.textContent = `${d.chars.toLocaleString()} chars of page context`;
      el.ctxState.className = "ctx-state ok";
    });

    es.addEventListener("analysis_done", (e) => {
      const d = JSON.parse(e.data);
      setBusy(false);
      state.stream = null;
      setStatus("done", "live");
      el.usage.textContent = `${d.input_tokens}→${d.output_tokens} tok`;
      renderSolution();
    });

    es.addEventListener("analysis_error", (e) => {
      const d = JSON.parse(e.data);
      setBusy(false);
      setStatus(d.title || "error", "err");
      showError(d);
      state.stream = null;
      // The card carries the detail; the toast is just the nudge.
      toast(d.title || d.message, true);
    });

    es.addEventListener("analysis_cancelled", () => {
      setBusy(false);
      state.stream = null;
      setStatus("stopped", "live");
      addEvent("stopped");
    });

    es.addEventListener("watch_error", (e) => addEvent(JSON.parse(e.data).message, "err"));

    es.addEventListener("displays", (e) => {
      const d = JSON.parse(e.data);
      renderDisplays(d.displays, Number(el.display.value) || undefined);
      applyCaptureError(d.capture_error);
    });

    es.addEventListener("auth", (e) => {
      const a = JSON.parse(e.data);
      applyAuth(a);
      if (a.signed_in) {
        addEvent(`signed in — ${a.detail}`, "tool");
        toast("Signed in to Anthropic.");
      }
    });

    es.addEventListener("auth_pending", (e) => {
      const d = JSON.parse(e.data);
      authPending(d);
      if (d.message) addEvent(d.message);
    });

    es.addEventListener("explore", (e) => {
      const d = JSON.parse(e.data);
      if (d.phase === "start") {
        setStatus("reading the page…", "busy");
        addEvent("explore: reading the other panels…", "tool");
      } else if (d.phase === "done") {
        const n = (d.panels || []).length;
        addEvent(
          n
            ? `explore: read ${d.panels.join(", ")} — ${d.chars.toLocaleString()} chars`
            : "explore: no other panels to read",
          "tool"
        );
        if ((d.failed || []).length) {
          addEvent(`explore: could not open ${d.failed.join(", ")}`, "err");
        }
        el.ctxState.textContent = `${d.chars.toLocaleString()} chars from ${n + 1} panel(s)`;
        el.ctxState.className = "ctx-state ok";
      } else if (d.phase === "failed") {
        addEvent(`explore failed: ${d.message}`, "err");
      }
    });

    // A supporting capture landed on the shot we are looking at.
    es.addEventListener("shot_updated", (e) => {
      const meta = JSON.parse(e.data);
      if (meta.id !== state.shotId) return;
      const grew = (meta.supports || []).length > (state.supports || []).length;
      renderSupports(meta);
      // Follow the newest one into the viewer, so you watch the panels being
      // read rather than staring at the first screenshot the whole time.
      if (grew) showSupport(meta.supports[meta.supports.length - 1].index);
    });

    // Another window (or the settings panel) changed the model.
    es.addEventListener("config", (e) => {
      const d = JSON.parse(e.data);
      applyProvider(d.provider, d);
    });

    es.addEventListener("auth_error", (e) => {
      const m = JSON.parse(e.data).message;
      applyAuth(state.auth || { signed_in: false, ant_installed: true });
      addEvent(m, "err");
      toast(m, true);
    });
  }

  /* ── boot ───────────────────────────────────────────────────────── */

  (async function boot() {
    applyDesktop();
    try {
      const s = await api("/api/state");
      applyProvider(s.provider, s);
      applyAuth(s.auth);
      renderDisplays(s.displays, s.capture_display);
      applyCaptureError(s.capture_error);
      el.watchEnabled.checked = s.watch.enabled;
      el.exploreFirst.checked = !!s.explore;
      el.watchAuto.checked = s.watch.auto_analyze;
      syncWatchControls();
      el.watchInterval.value = s.watch.interval;
      el.watchThreshold.value = s.watch.threshold;
      s.shots.slice().reverse().forEach((m) => addToFilmstrip(m, true));
      if (s.shots.length) showShot(s.shots[0]);

      const src = await (await fetch("/bookmarklet.js")).text();
      el.bookmarklet.href = src;
      setStatus("ready", "live");
    } catch (e) {
      setStatus("failed to load state", "err");
      toast(e.message, true);
    }
    connect();
  })();
})();
