/* Read the page's other panels in place, without anything appearing to move.
 *
 * Inactive panels are unmounted by most frameworks, so the only way to read
 * one is to activate it. Doing that visibly flips the page through each tab;
 * doing it in a second tab puts a tab in the tab strip, which is no good if
 * the screen is being shared.
 *
 * So: freeze a copy. The widget is cloned, the clone is pinned exactly over
 * the original, and the real tabs are cycled underneath it. What is on screen
 * is a still of the panel that was already showing, so nothing changes — then
 * the original tab is restored and the still is removed.
 *
 * Templated in: __TARGETS__ (labels to open), __RESTORE__ (the label to put
 * back afterwards) and __SETTLE__ (ms to let each panel render).
 */
(function () {
  var TARGETS = __TARGETS__;
  var RESTORE = __RESTORE__;
  var SETTLE = __SETTLE__;
  var OUT = (window.__solverExplore = { status: "running", panels: [], failed: [] });

  function clean(s) {
    return (s || "").replace(/ /g, " ").replace(/[^\S\n]+/g, " ")
      .replace(/\n{3,}/g, "\n\n").trim();
  }

  function labelOf(el) {
    return clean(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
  }

  function findControl(label) {
    var nodes = document.querySelectorAll('button, [role="tab"], summary, a[href="#"], [role="button"], .tab, li[data-tab]');
    for (var i = 0; i < nodes.length; i++) {
      if (labelOf(nodes[i]) === label) return nodes[i];
    }
    for (var j = 0; j < nodes.length; j++) {
      if (labelOf(nodes[j]).indexOf(label) === 0) return nodes[j];
    }
    return null;
  }

  /* The smallest box containing both the tab bar and the panel it switches,
     so the still covers the highlight moving as well as the content. */
  function widgetFor(control) {
    var node = control.parentElement || control;
    var bar = node.getBoundingClientRect().height || 1;
    while (node.parentElement && node.getBoundingClientRect().height < bar * 2.5) {
      node = node.parentElement;
    }
    return node;
  }

  function freeze(node) {
    var r = node.getBoundingClientRect();
    var clone = node.cloneNode(true);
    var shell = document.createElement("div");
    shell.setAttribute("data-solver-freeze", "1");
    shell.style.cssText = [
      "position:fixed",
      "left:" + r.left + "px",
      "top:" + r.top + "px",
      "width:" + r.width + "px",
      "height:" + r.height + "px",
      "overflow:hidden",
      "z-index:2147483647",
      "pointer-events:none",
      "background:" + (getComputedStyle(node).backgroundColor || "transparent"),
    ].join(";");
    clone.style.margin = "0";
    clone.style.width = r.width + "px";
    clone.style.height = r.height + "px";
    shell.appendChild(clone);
    document.body.appendChild(shell);
    return shell;
  }

  var first = TARGETS.length ? findControl(TARGETS[0]) : null;
  var widget = first ? widgetFor(first) : null;
  var still = widget ? freeze(widget) : null;

  /* Which tab to put back. Supplied by the caller, which works it out by
     comparing how the tabs *look* — sites commonly mark the open one with
     nothing but a different background colour, which no attribute reveals. */
  var openTab = RESTORE || "";
  OUT.restored = openTab;

  function readPanel() {
    return clean(document.body.innerText).slice(0, 60000);
  }

  function step(i) {
    if (i >= TARGETS.length) {
      var back = openTab && findControl(openTab);
      if (back) back.click();
      setTimeout(function () {
        if (still && still.parentNode) still.parentNode.removeChild(still);
        OUT.status = "done";
      }, SETTLE);
      return;
    }
    var label = TARGETS[i];
    var control = findControl(label);
    if (!control) {
      OUT.failed.push(label);
      return step(i + 1);
    }
    try {
      control.click();
    } catch (e) {
      OUT.failed.push(label);
      return step(i + 1);
    }
    setTimeout(function () {
      OUT.panels.push({ name: label, text: readPanel() });
      step(i + 1);
    }, SETTLE);
  }

  // Bail out visibly rather than leaving a still stuck on the page.
  setTimeout(function () {
    if (OUT.status === "running") {
      if (still && still.parentNode) still.parentNode.removeChild(still);
      OUT.status = "timeout";
    }
  }, 3000 + TARGETS.length * (SETTLE + 400));

  step(0);
  return "started";
})();
