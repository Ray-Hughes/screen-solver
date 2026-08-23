(function () {
  var LIMIT = 60000;
  var out = { url: location.href, title: document.title };

  function clean(s) {
    if (!s) return "";
    return s.replace(/\u00a0/g, " ").replace(/[^\S\n]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  }

  function isHidden(el) {
    var cs = window.getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return true;
    if (el.hasAttribute("hidden")) return true;
    if (el.getAttribute("aria-hidden") === "true") return true;
    if (el.offsetParent === null && cs.position !== "fixed") return true;
    return false;
  }

  out.visible = clean(document.body ? document.body.innerText : "").slice(0, LIMIT);

  var panels = [];
  var seen = [];
  var candidates = document.querySelectorAll(
    '[role="tabpanel"], [hidden], details, .tab-pane, .tabpanel, [aria-hidden="true"], [data-state="inactive"], [data-headlessui-state]'
  );
  for (var i = 0; i < candidates.length && panels.length < 25; i++) {
    var el = candidates[i];
    var txt = clean(el.textContent || "");
    if (txt.length < 30) continue;
    var nested = false;
    for (var s = 0; s < seen.length; s++) {
      if (seen[s].contains(el)) { nested = true; break; }
    }
    if (nested) continue;
    seen.push(el);
    var name =
      el.getAttribute("aria-label") ||
      el.getAttribute("data-tab") ||
      el.id ||
      (el.tagName === "DETAILS" && el.querySelector("summary")
        ? clean(el.querySelector("summary").textContent)
        : "") ||
      el.className ||
      el.tagName;
    panels.push({
      name: String(name).slice(0, 120),
      hidden: isHidden(el),
      text: txt.slice(0, 12000)
    });
  }
  out.panels = panels;

  var editors = [];
  function pushEditor(kind, value) {
    if (value && value.trim().length) {
      editors.push({ kind: kind, value: String(value).slice(0, 12000) });
    }
  }
  try {
    if (window.monaco && window.monaco.editor) {
      var models = window.monaco.editor.getModels();
      for (var m = 0; m < models.length; m++) pushEditor("monaco", models[m].getValue());
    }
  } catch (e) {}
  var cms = document.querySelectorAll(".cm-editor, .CodeMirror, .cm-content");
  for (var c = 0; c < cms.length; c++) {
    var node = cms[c];
    try {
      if (node.CodeMirror) { pushEditor("codemirror5", node.CodeMirror.getValue()); continue; }
      var view = node.cmView && node.cmView.view;
      if (!view && node.querySelector(".cm-content") && node.querySelector(".cm-content").cmView) {
        view = node.querySelector(".cm-content").cmView.view;
      }
      if (view && view.state) { pushEditor("codemirror6", view.state.doc.toString()); continue; }
      pushEditor("dom-editor", node.innerText);
    } catch (e2) {}
  }
  var tas = document.querySelectorAll("textarea");
  for (var t = 0; t < tas.length; t++) pushEditor("textarea", tas[t].value);
  out.editors = editors;

  var tables = [];
  var tbls = document.querySelectorAll("table");
  for (var q = 0; q < tbls.length && tables.length < 12; q++) {
    var rows = [];
    var trs = tbls[q].querySelectorAll("tr");
    for (var r = 0; r < trs.length && r < 60; r++) {
      var cells = trs[r].querySelectorAll("th,td");
      var row = [];
      for (var k = 0; k < cells.length; k++) row.push(clean(cells[k].textContent).slice(0, 200));
      if (row.length) rows.push(row);
    }
    if (rows.length) tables.push(rows);
  }
  out.tables = tables;

  var pre = [];
  var codes = document.querySelectorAll("pre, code");
  for (var p = 0; p < codes.length && pre.length < 25; p++) {
    var ct = clean(codes[p].textContent);
    if (ct.length > 20) pre.push(ct.slice(0, 4000));
  }
  out.code_blocks = pre;

  /* Finding the tab bar.
     Sites almost never mark tabs up as tabs. On the page this was built
     against, "Description / Schema & data / Hints / Ask / Solution" are five
     plain <button>s with utility classes and no role, so matching on
     role="tab" or a class name finds nothing.
     What is reliable is the shape: a tab bar is several sibling controls in
     one parent, and the open one is the only one that looks different. */

  function labelOf(el) {
    return clean(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
  }

  /* A visual fingerprint. The active tab is the odd one out among its
     siblings — highlighted, underlined, bolder — whatever the framework. */
  function styleSig(el) {
    var cs = window.getComputedStyle(el);
    return [
      cs.backgroundColor, cs.color, cs.fontWeight,
      cs.borderBottomColor, cs.borderBottomWidth, cs.opacity
    ].join("|");
  }

  function explicitState(el) {
    if (el.getAttribute("aria-selected") === "true") return true;
    if (el.getAttribute("data-state") === "active") return true;
    if (el.tagName.toLowerCase() === "summary") {
      return !!(el.parentElement && el.parentElement.hasAttribute("open"));
    }
    var cls = " " + (el.className && el.className.baseVal !== undefined
      ? el.className.baseVal : (el.className || "")) + " ";
    return / (active|selected|is-active|is-selected|current) /.test(cls);
  }

  var controls = document.querySelectorAll(
    'button, [role="tab"], summary, a[href="#"], [role="button"], .tab, li[data-tab]'
  );

  /* Bucket by parent, so siblings can be compared with each other. */
  var parents = [], buckets = [];
  for (var c = 0; c < controls.length; c++) {
    var el = controls[c];
    var lbl = labelOf(el);
    if (!lbl || lbl.length > 80 || isHidden(el)) continue;
    var parent = el.parentElement;
    var at = -1;
    for (var q = 0; q < parents.length; q++) if (parents[q] === parent) { at = q; break; }
    if (at < 0) { parents.push(parent); buckets.push([]); at = parents.length - 1; }
    buckets[at].push({ el: el, label: lbl });
  }

  var clickables = [];
  for (var g = 0; g < buckets.length && clickables.length < 60; g++) {
    var group = buckets[g];
    var isSummary = group.length === 1 && group[0].el.tagName.toLowerCase() === "summary";
    var hasRole = group[0].el.getAttribute("role") === "tab";
    // Two or more siblings behaving alike is the tab-bar signal; a lone
    // <summary> is a disclosure, which is the same idea with one control.
    var groupIsTabs = isSummary || hasRole || group.length >= 2;

    /* Which sibling is open: an explicit flag if the site sets one,
       otherwise the one whose look is unique within the group. */
    var activeIndex = -1;
    for (var k = 0; k < group.length; k++) {
      if (explicitState(group[k].el)) { activeIndex = k; break; }
    }
    if (activeIndex < 0 && group.length >= 2) {
      var sigs = [], counts = [];
      for (var m = 0; m < group.length; m++) {
        var sig = styleSig(group[m].el);
        sigs.push(sig);
        var found = false;
        for (var n = 0; n < counts.length; n++) {
          if (counts[n].sig === sig) { counts[n].n++; found = true; break; }
        }
        if (!found) counts.push({ sig: sig, n: 1 });
      }
      // Exactly one sibling looking different is a highlighted tab. Two or
      // more differing means it is just a row of unlike buttons.
      if (counts.length >= 2) {
        var singles = [];
        for (var t = 0; t < counts.length; t++) if (counts[t].n === 1) singles.push(counts[t].sig);
        if (singles.length === 1) {
          for (var u = 0; u < sigs.length; u++) if (sigs[u] === singles[0]) activeIndex = u;
        }
      }
    }

    for (var b = 0; b < group.length && clickables.length < 60; b++) {
      clickables.push({
        label: group[b].label,
        tag: group[b].el.tagName.toLowerCase(),
        hidden: false,
        tab: groupIsTabs,
        group: g,
        siblings: group.length,
        active: b === activeIndex
      });
    }
  }
  out.clickables = clickables;

  return JSON.stringify(out);
})();
