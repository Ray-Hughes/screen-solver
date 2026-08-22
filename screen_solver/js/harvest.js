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

  var clickables = [];
  var cand2 = document.querySelectorAll(
    'button, [role="tab"], summary, a[href="#"], [role="button"], .tab, li[data-tab]'
  );
  for (var b = 0; b < cand2.length && clickables.length < 60; b++) {
    var lbl = clean(cand2[b].innerText || cand2[b].textContent || cand2[b].getAttribute("aria-label") || "");
    if (lbl && lbl.length < 80) {
      clickables.push({ label: lbl, tag: cand2[b].tagName.toLowerCase(), hidden: isHidden(cand2[b]) });
    }
  }
  out.clickables = clickables;

  return JSON.stringify(out);
})();
