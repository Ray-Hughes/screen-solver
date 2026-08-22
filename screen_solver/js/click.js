(function () {
  var want = "__TARGET__".toLowerCase().trim();
  function clean(s) { return (s || "").replace(/\s+/g, " ").trim(); }
  var nodes = document.querySelectorAll(
    'button, [role="tab"], summary, a, [role="button"], .tab, li, div[tabindex], span[tabindex]'
  );
  var exact = null, partial = null;
  for (var i = 0; i < nodes.length; i++) {
    var lbl = clean(nodes[i].innerText || nodes[i].textContent || nodes[i].getAttribute("aria-label") || "").toLowerCase();
    if (!lbl || lbl.length > 120) continue;
    if (lbl === want) { exact = nodes[i]; break; }
    if (!partial && lbl.indexOf(want) !== -1 && lbl.length < want.length + 40) partial = nodes[i];
  }
  var target = exact || partial;
  if (!target) {
    try { target = document.querySelector("__TARGET__"); } catch (e) { target = null; }
  }
  if (!target) return JSON.stringify({ ok: false, error: "no element matched" });
  if (target.tagName === "SUMMARY" && target.parentElement && target.parentElement.tagName === "DETAILS") {
    target.parentElement.open = true;
  }
  target.scrollIntoView({ block: "center" });
  target.click();
  return JSON.stringify({ ok: true, clicked: clean(target.innerText || target.textContent).slice(0, 120) });
})();
