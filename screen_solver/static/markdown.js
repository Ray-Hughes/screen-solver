/* Small GitHub-flavoured Markdown renderer.
   Local and dependency-free so the dashboard works offline. */
(function (global) {
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function inline(s) {
    let t = esc(s);
    t = t.replace(/`([^`]+)`/g, (_, c) => "<code>" + c + "</code>");
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return t;
  }

  function splitRow(line) {
    return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(c => c.trim());
  }

  function render(src) {
    const lines = (src || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // fenced code
      const fence = line.match(/^\s*```([\w+-]*)\s*$/);
      if (fence) {
        const lang = fence[1] || "";
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++;
        out.push(
          '<pre data-lang="' + esc(lang) + '"><button class="copy">copy</button><code>' +
          esc(body.join("\n")) + "</code></pre>"
        );
        continue;
      }

      // heading
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        const lvl = Math.min(6, h[1].length);
        const text = h[2].trim();
        const id = "s-" + text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        out.push("<h" + lvl + ' id="' + id + '">' + inline(text) + "</h" + lvl + ">");
        i++;
        continue;
      }

      if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { out.push("<hr />"); i++; continue; }

      // table
      if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:-]*-[-\s|:]*\|?\s*$/.test(lines[i + 1])) {
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim()) rows.push(splitRow(lines[i++]));
        out.push(
          "<table><thead><tr>" + head.map(c => "<th>" + inline(c) + "</th>").join("") +
          "</tr></thead><tbody>" +
          rows.map(r => "<tr>" + r.map(c => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") +
          "</tbody></table>"
        );
        continue;
      }

      // lists
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        const ordered = /^\s*\d+[.)]\s+/.test(line);
        const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          let text = lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
          i++;
          // continuation lines belonging to this bullet
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
            text += " " + lines[i].trim();
            i++;
          }
          items.push("<li>" + inline(text) + "</li>");
        }
        out.push((ordered ? "<ol>" : "<ul>") + items.join("") + (ordered ? "</ol>" : "</ul>"));
        continue;
      }

      // blockquote
      if (/^\s*>\s?/.test(line)) {
        const body = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
        out.push("<blockquote>" + render(body.join("\n")) + "</blockquote>");
        continue;
      }

      if (!line.trim()) { i++; continue; }

      // paragraph
      const para = [];
      while (
        i < lines.length && lines[i].trim() &&
        !/^\s*```/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i]) &&
        !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) && !/^\s*>/.test(lines[i])
      ) para.push(lines[i++]);
      out.push("<p>" + inline(para.join(" ")) + "</p>");
    }

    return out.join("\n");
  }

  function headings(src) {
    const found = [];
    (src || "").split("\n").forEach(l => {
      const m = l.match(/^##\s+(.*)$/);
      if (m) {
        const text = m[1].trim();
        found.push({
          text,
          id: "s-" + text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
        });
      }
    });
    return found;
  }

  /* Body of one `## Section`, without its heading. Fenced blocks are skipped
     over so a `##` comment inside code cannot end the section early. */
  function section(src, name) {
    const lines = (src || "").replace(/\r\n/g, "\n").split("\n");
    const want = name.trim().toLowerCase();
    let body = null;
    let fenced = false;

    for (const line of lines) {
      if (/^\s*```/.test(line)) {
        fenced = !fenced;
      } else if (!fenced) {
        const h = line.match(/^##\s+(.*)$/);
        if (h) {
          if (body !== null) break; // the next section starts here
          if (h[1].trim().toLowerCase() === want) body = [];
          continue;
        }
      }
      if (body !== null) body.push(line);
    }
    return body ? body.join("\n").trim() : "";
  }

  /* The first fenced code block, for the copy button. */
  function firstCode(src) {
    const m = (src || "").match(/```[\w+-]*\n([\s\S]*?)```/);
    return m ? m[1].replace(/\n$/, "") : "";
  }

  global.MD = { render, headings, section, firstCode };
})(window);
