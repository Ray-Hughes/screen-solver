"""Running the answer before it is trusted.

A solution that does not execute is worse than no solution — it looks right,
gets typed out, and fails on submit. Both of the failures that prompted this
(`no such column: account_name`, `no such function: DATEDIFF`) are caught by
simply running the thing.

SQL is checked against a throwaway SQLite database rebuilt from the CREATE
TABLE statements the explore pass already harvested. Python is compiled and
executed in a subprocess. Neither needs the model.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

CODE_FENCE_RE = re.compile(r"```([\w+-]*)\s*\n(.*?)```", re.S)
SOLUTION_RE = re.compile(r"^##\s*Solution\s*$(.*?)(?=^##\s|\Z)", re.S | re.M)
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(\w+)[\"`\]]?\s*\((.*?)\n\s*\)\s*;",
    re.S | re.I,
)

SQL_LANGS = {"sql", "sqlite", "postgres", "postgresql", "mysql", "tsql", "plsql"}
PY_LANGS = {"python", "py", "python3"}

# Long enough for a real answer, short enough that a runaway loop is not a hang.
RUN_TIMEOUT = 12.0


@dataclass
class VerifyResult:
    ok: bool
    language: str = ""
    error: str = ""
    ran: bool = False  # False when there was nothing we could execute

    def to_dict(self) -> dict:
        return {"ok": self.ok, "language": self.language, "error": self.error,
                "ran": self.ran}


def solution_code(markdown: str) -> tuple[str, str]:
    """The language and body of the answer's ## Solution block."""
    section = SOLUTION_RE.search(markdown or "")
    if not section:
        return "", ""
    fence = CODE_FENCE_RE.search(section.group(1))
    if not fence:
        return "", ""
    return (fence.group(1) or "").strip().lower(), fence.group(2)


def _statements(sql: str) -> list[str]:
    """Split on semicolons that are not inside a string literal."""
    out, buf, quote = [], [], ""
    for ch in sql:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == ";":
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s.strip() for s in out if s.strip()]


def verify_sql(sql: str, page_context: str) -> VerifyResult:
    """Run the query against a rebuild of the page's own schema."""
    ddl = CREATE_TABLE_RE.findall(page_context or "")
    if not ddl:
        return VerifyResult(ok=True, language="sql", ran=False,
                            error="no CREATE TABLE statements to rebuild from")

    db = sqlite3.connect(":memory:")
    try:
        for name, body in ddl:
            try:
                db.execute(f"CREATE TABLE {name} ({body}\n)")
            except sqlite3.Error:
                # A statement we cannot rebuild is not the answer's fault.
                continue

        statements = _statements(sql)
        if not statements:
            return VerifyResult(ok=True, language="sql", ran=False)
        try:
            for stmt in statements:
                db.execute(stmt)
        except sqlite3.Error as exc:
            return VerifyResult(ok=False, language="sql", ran=True, error=str(exc))
        return VerifyResult(ok=True, language="sql", ran=True)
    finally:
        db.close()


def verify_python(code: str) -> VerifyResult:
    """Compile, then execute, in a subprocess that cannot outlive the check."""
    try:
        compile(code, "<solution>", "exec")
    except SyntaxError as exc:
        where = f" (line {exc.lineno})" if exc.lineno else ""
        return VerifyResult(ok=False, language="python", ran=True,
                            error=f"{type(exc).__name__}: {exc.msg}{where}")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "solution.py"
        path.write_text(code)
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True, text=True, timeout=RUN_TIMEOUT, cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(ok=False, language="python", ran=True,
                                error=f"it did not finish within {RUN_TIMEOUT:.0f}s "
                                      "— check for an unbounded loop")
        if proc.returncode != 0:
            return VerifyResult(ok=False, language="python", ran=True,
                                error=_python_error(proc.stderr, str(path)))
    return VerifyResult(ok=True, language="python", ran=True)


def _python_error(stderr: str, path: str) -> str:
    """The exception and where it happened, without the temp-directory noise."""
    lines = [ln.rstrip() for ln in (stderr or "").splitlines() if ln.strip()]
    if not lines:
        return "it exited non-zero without saying why"
    # The last line is the exception; keep the nearest frame in our own file
    # for context and drop everything inside the interpreter.
    where = ""
    for line in lines:
        if path in line:
            where = line.replace(path, "solution.py").strip()
    tail = lines[-1]
    return f"{tail}\n  at {where}" if where else tail


def verify(markdown: str, page_context: str = "") -> VerifyResult:
    """Check the answer's solution block, if it is a language we can run."""
    lang, code = solution_code(markdown)
    if not code.strip():
        return VerifyResult(ok=True, ran=False, error="no solution block")
    if lang in SQL_LANGS or (not lang and re.search(r"\bSELECT\b", code, re.I)):
        return verify_sql(code, page_context)
    if lang in PY_LANGS:
        return verify_python(code)
    return VerifyResult(ok=True, language=lang, ran=False,
                        error=f"nothing to run {lang or 'this'} with")
