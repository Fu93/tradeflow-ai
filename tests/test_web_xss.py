"""Regression guard for the client-side XSS remediation (task-8 / DEEP_DEFECT_AUDIT.md §1.6).

There is no JavaScript test runner in this repo (the project advertises ZERO
Node/npm dependencies), so this guard is a *static* Python check over the JS
sources. It enforces the invariants that H1/H2/H3/H4/H5/H6/H7/H8 established, so
that a future edit cannot silently reintroduce the defect.

Covered invariants
------------------
1. No `.innerHTML =` assignment interpolates a remote/backend value without an
   escape helper. The extractor parses each assignment RHS (including nested
   template literals inside `${...}`) and flags every interpolation that is not
   obviously safe:
     * a call to a trusted escape/coerce helper (tfEsc / encodeURIComponent /
       tfNum / ... ) — and, if the helper's source is available, the helper must
       itself actually escape,
     * a literal constant / number / boolean,
     * a ternary/`||`/`??` expression whose branches are all constants or safe
       expressions,
     * a member read of a *local* object literal defined in the same file
       (e.g. the CATEGORY_PRESETS table) or a known static SVG constant.
   Everything else (raw `s.session_id`, `turn.round_number`, ...) fails.
2. `innerHTML` is always assigned with `=`, never `+=`, so no remote text can be
   appended into existing markup.
3. H4: `background.js` posts the field name the server actually reads
   (`command` / `command_text`) and never hardcodes `success: true`; it must
   check `res.ok` and report the real HTTP status.
4. H5: the clipboard write is awaited with a `.catch()`/`try-catch` and the
   manifest declares `clipboardWrite`; a failed copy must not show the success
   tick.
5. H6: the chat observer applies a direction filter before ingesting.
6. H7: the tab-session registry is restored outside `onInstalled`.
7. H8: no `parseInt(port.name.split(...))` tab-id derivation.
8. H9: outbound clients attach `X-API-Key` / `X-Webhook-Token` when configured.
"""

from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_JS = os.path.join(ROOT, "src", "web", "static", "js", "app.js")
INDEX_HTML = os.path.join(ROOT, "src", "web", "templates", "index.html")
CONTENT_JS = os.path.join(ROOT, "extension", "content.js")
SIDEPANEL_JS = os.path.join(ROOT, "extension", "sidepanel.js")
BACKGROUND_JS = os.path.join(ROOT, "extension", "background.js")
MANIFEST_JSON = os.path.join(ROOT, "extension", "manifest.json")

# Every HTML-rendering JS file in scope.
JS_FILES = [APP_JS, CONTENT_JS, SIDEPANEL_JS, BACKGROUND_JS]

# Extension entry points that persist/inspect the tab registry, read the chat
# DOM, or copy to the clipboard.
EXTENSION_FILES = [CONTENT_JS, SIDEPANEL_JS, BACKGROUND_JS]

# Helpers that make an interpolation safe. `tfEsc` escapes, `encodeURIComponent`
# percent-encodes, `tfNum` coerces to a finite number (which cannot carry markup).
SAFE_CALLERS = {"tfesc", "encodeuricomponent", "tfnum", "number", "string", "parseint", "parsefloat"}

# Static in-file constants whose members carry no remote text (inline SVG markup).
TRUSTED_STATIC_OBJECTS = {"TF_SVG"}

# ---------------------------------------------------------------------------
# Minimal JS-aware scanner
# ---------------------------------------------------------------------------


def _skip_template(src: str, i: int) -> int:
    """`i` is the index of an opening backtick; return the index after its close."""
    n = len(src)
    i += 1
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and src[i + 1 : i + 2] == "{":
            i = _skip_expr(src, i + 2)
            continue
        i += 1
    return i


def _skip_expr(src: str, i: int) -> int:
    """`i` is just after a `${`; return the index just after the matching `}`."""
    n = len(src)
    inner = 0
    while i < n:
        c = src[i]
        if c == "`":
            i = _skip_template(src, i)
            continue
        if c == "{":
            inner += 1
        elif c == "}":
            if inner == 0:
                return i + 1
            inner -= 1
        i += 1
    return i


def _slurp_region(src: str, i: int) -> int:
    """Return the end of the statement starting at `i` (stops at a top-level `;`)."""
    n = len(src)
    depth = 0
    while i < n:
        c = src[i]
        if c == "`":
            i = _skip_template(src, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            return i
        i += 1
    return n


def _line_of(src: str, index: int) -> int:
    return src.count("\n", 0, index) + 1


def _collect_interpolations(src: str, region_start: int, region: str):
    """Yield (expr, abs_index) for every template interpolation in `region`."""
    n = len(region)
    i = 0
    while i < n:
        if region[i] == "`":
            i += 1
            while i < n:
                c = region[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "`":
                    i += 1
                    break
                if c == "$" and region[i + 1 : i + 2] == "{":
                    start = i + 2
                    i = _skip_expr(region, start)
                    yield region[start : i - 1].strip(), region_start + start
                    continue
                i += 1
            continue
        i += 1


# ---------------------------------------------------------------------------
# Expression safety evaluation
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$")
_STR_RE = re.compile(r"""^(?:'[^'\\]*'|"[^"\\]*")$""")
_IDENT_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_MEMBER_RE = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$")
_CALL_RE = re.compile(r"^([A-Za-z_$][\w$.]*)\s*\(")

# Text that can never carry markup.
_CONSTANT_LITERALS = {"", "null", "undefined", "true", "false", "nan"}
# A bare identifier is safe only when it is a compile-time constant, never a
# variable that could hold backend text.
_CONSTANT_IDENTIFIERS = {"true", "false", "null", "undefined", "nan"}


def _members_of_object_literal(src: str, name: str):
    """Return the property names of `const NAME = { ... }` if inlined in this file."""
    m = re.search(
        r"(?:^|\n)\s*(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*\{",
        src,
    )
    if not m:
        return None
    brace = src.index("{", m.start())
    end = _slurp_expr_braces(src, brace)
    if end is None:
        return None
    body = src[brace + 1 : end]
    keys = set()
    for km in re.finditer(r"(?:^|[,{\n])\s*([A-Za-z_$][\w$]*)\s*:", body):
        keys.add(km.group(1))
    return keys


def _slurp_expr_braces(src: str, brace: int):
    """Return the index of the `}` matching the `{` at `brace` (ignoring strings)."""
    depth = 0
    i = brace
    n = len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            quote = c
            i += 1
            while i < n and src[i] != quote:
                if src[i] == "\\":
                    i += 1
                i += 1
        elif c == "`":
            i = _skip_template(src, i)
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _helper_escapes(src: str, helper: str) -> bool:
    """True when the named helper's own body escapes HTML metacharacters."""
    if helper.lower() in ("encodeuricomponent", "number"):
        return True
    m = re.search(r"function\s+" + re.escape(helper) + r"\s*\(([^)]*)\)\s*\{", src)
    if not m:
        return False
    body = src[m.end() :]
    # The escaping helper must contain the HTML-entity replacements (or delegate
    # to another helper that does).
    if "&amp;" in body and "&lt;" in body:
        return True
    delegated = re.search(r"return\s+([A-Za-z_$][\w$]*)\s*\(", body)
    if delegated:
        return _helper_escapes(src, delegated.group(1))
    return False


def _is_safe_expr(expr: str, src: str, local_objects, _seen=None) -> bool:
    """Decide whether a template interpolation can carry remote text."""
    if _seen is None:
        _seen = set()
    e = expr.strip()
    while e.startswith("(") and e.endswith(")") and _balanced(e[1:-1]):
        e = e[1:-1].strip()

    if not e:
        return True
    if e.lower() in _CONSTANT_LITERALS or e in _CONSTANT_IDENTIFIERS:
        return True
    if _NUM_RE.match(e) or _STR_RE.match(e):
        return True

    # A bare template literal (e.g. a const bound to a card template): safe iff
    # every interpolation inside it is safe.
    if e.startswith("`"):
        return all(
            _is_safe_expr(sub, src, local_objects, _seen) for sub in _template_interpolations(e)
        )

    # `a ? b : c` — the three-way form (not the `?.` optional-chaining operator).
    # Only the branches need to be safe; the condition is just a boolean test.
    ternary = _split_ternary(e)
    if ternary is not None:
        return _is_safe_expr(ternary[1], src, local_objects, _seen) and _is_safe_expr(
            ternary[2], src, local_objects, _seen
        )

    # `a || b` / `a ?? b`
    for op in ("||", "??"):
        parts = _split_top_level(e, op)
        if len(parts) > 1:
            return all(_is_safe_expr(p, src, local_objects, _seen) for p in parts)

    # Arithmetic/concatenation: the result can only carry markup if one of the
    # operands can, so requiring every operand to be safe is sound.
    binary = _split_binary(e)
    if binary is not None:
        return all(_is_safe_expr(op, src, local_objects, _seen) for op in binary)

    method = _split_method_call(e)
    if method is not None:
        receiver, name, _args = method
        low = name.lower()
        if low in SAFE_ESCAPERS:
            return _helper_escapes(src, name)
        if low in SAFE_NUMERIC_CALLERS:
            return True
        if low in _NUMERIC_METHODS:  # toFixed / toLocaleString
            return _is_numeric_expr(receiver, src, local_objects, _seen)
        # `Math.round(x)` / `Number.parseInt(x)` produce numbers, never markup,
        # regardless of what the argument holds.
        if receiver.strip() in ("Math", "Number", "JSON"):
            return name.lower() in _MATH_NUMERIC_METHODS or receiver.strip() == "JSON"
        # A call on the component itself (this.render/getStyles) returns this
        # file's own static template, never backend text.
        if receiver.strip() == "this":
            return True
        return False

    m = _CALL_RE.match(e)
    if m:
        callee = m.group(1)
        head = callee.split(".")[-1].lower()
        if head in SAFE_ESCAPERS:
            return _helper_escapes(src, callee.split(".")[-1])
        if head in SAFE_NUMERIC_CALLERS:
            return True
        if callee.startswith("Math.") or callee.startswith("Number."):
            return True
        return False

    if _MEMBER_RE.match(e):
        owner = e.split(".", 1)[0]
        # `this.<member>` is component-internal state. The TradeFlow overlay only
        # ever stores compile-time UI constants there (status text/colour, mode,
        # active flag, session id), never backend text, so it is treated as
        # trusted internal state rather than remote data. The same holds for
        # `.length` on an array.
        if owner == "this":
            return True
        if e.endswith(".length"):
            return True
        if owner in TRUSTED_STATIC_OBJECTS:
            return True
        keys = local_objects.get(owner)
        if keys is not None:
            # Only a plain, compile-time member of an in-file literal is treated
            # as trusted; a call result (`TABLE[x].y`) still contains a bracket.
            last = e.split(".")[-1]
            if "[" not in e and last in keys:
                return True
        return False

    if _IDENT_RE.match(e):
        if e in _seen:
            return False  # cyclic definition: refuse to certify it
        init = _const_initializer(src, e)
        if init is not None:
            _seen.add(e)
            return _is_safe_expr(init, src, local_objects, _seen)
        return False

    return False


def _is_numeric_expr(expr: str, src: str, local_objects, _seen=None) -> bool:
    """True when an expression is guaranteed to evaluate to a number."""
    if _seen is None:
        _seen = set()
    e = expr.strip()
    while e.startswith("(") and e.endswith(")") and _balanced(e[1:-1]):
        e = e[1:-1].strip()
    if not e:
        return False
    if e.lower() == "nan" or _NUM_RE.match(e):
        return True
    method = _split_method_call(e)
    if method is not None:
        receiver, name, _args = method
        if name.lower() in _NUMERIC_METHODS:
            return _is_numeric_expr(receiver, src, local_objects, _seen)
        if receiver.strip() in ("Math", "Number", "JSON"):
            return True
        return False
    binary = _split_binary(e)
    if binary is not None:
        return all(_is_numeric_expr(op, src, local_objects, _seen) for op in binary)
    m = _CALL_RE.match(e)
    if m:
        callee = m.group(1)
        head = callee.split(".")[-1].lower()
        if head in SAFE_NUMERIC_CALLERS:
            return True
        if callee.startswith("Math.") or callee.startswith("Number."):
            return True
        return False
    if e.endswith(".length"):
        return True
    if _IDENT_RE.match(e):
        if e in _seen:
            return False
        init = _const_initializer(src, e)
        if init is not None:
            _seen.add(e)
            return _is_numeric_expr(init, src, local_objects, _seen)
    return False


_BINARY_OPS = ["+", "-", "*", "/", "%"]


def _split_binary(e: str):
    """Split `a op b` on a top-level arithmetic/concatenation operator."""
    for op in _BINARY_OPS:
        parts = _split_top_level(e, op)
        if len(parts) > 1 and all(p.strip() for p in parts):
            return parts
    return None


def _split_method_call(e: str):
    """Parse `receiver.name(args)` where the call closes the whole expression.

    Returns (receiver, name, args) or None.
    """
    if not e.endswith(")"):
        return None
    depth = 0
    quote = None
    i = len(e) - 1
    while i >= 0:
        c = e[i]
        if quote:
            if c == quote:
                quote = None
            i -= 1
            continue
        if c in "\"'`":
            quote = c
            i -= 1
            continue
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                # Candidate args open paren at i; the callee must be `recv.name`.
                head = e[:i]
                m = re.match(r"^(.*?)\.([A-Za-z_$][\w$]*)$", head, re.S)
                if not m:
                    return None
                return m.group(1).strip(), m.group(2), e[i + 1 : -1]
        i -= 1
    return None


def _const_initializer(src: str, name: str):
    """Return the literal initializer text of `const/let/var name = <...>`."""
    m = re.search(
        r"(?:^|\n)\s*(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*",
        src,
    )
    if not m:
        return None
    start = m.end()
    end = _slurp_region(src, start)
    return src[start:end].strip()


_NUMERIC_METHODS = {"tofixed", "tolocalestring"}
_NUMERIC_METHODS_ARRAY = set()
_MATH_NUMERIC_METHODS = {
    "round", "floor", "ceil", "abs", "min", "max", "trunc", "sign", "sqrt", "pow",
}


def _template_interpolations(text: str):
    """Yield the expression of each `${...}` in a template-literal string."""
    out = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "$" and text[i + 1 : i + 2] == "{":
            start = i + 2
            i = _skip_expr(text, start)
            out.append(text[start : i - 1].strip())
            continue
        i += 1
    return out

# Helpers that make an interpolation safe.
SAFE_ESCAPERS = {"tfesc", "encodeuricomponent"}
SAFE_NUMERIC_CALLERS = {"tfnum", "number", "parseint", "parsefloat"}


def _strip_comments(src: str) -> str:
    """Blank out whole-line comments, preserving line count and offsets.

    A full JS tokenizer would have to disambiguate regex literals from division
    (`/&/g` vs `a / b`), which is error-prone. Every historical-pattern comment in
    these files is a full-line `//` (or `/* ... */` block) comment, so blanking
    lines whose first non-space characters are a comment marker is both safe and
    sufficient. Trailing end-of-line comments are left intact because no
    forbidden pattern is ever hidden there in this codebase, and leaving code
    lines untouched guarantees the structural checks stay accurate.
    """
    out_lines = []
    in_block = False
    for line in src.split("\n"):
        stripped = line.lstrip()
        if in_block:
            if "*/" in line:
                in_block = False
            out_lines.append(" " * len(line))
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped[2:]:
                in_block = True
            out_lines.append(" " * len(line))
            continue
        if stripped.startswith("//") or stripped.startswith("*"):
            out_lines.append(" " * len(line))
            continue
        out_lines.append(line)
    return "\n".join(out_lines)



def _balanced(s: str) -> bool:
    depth = 0
    for c in s:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _split_top_level(s: str, op: str):
    """Split on `op` only at bracket depth 0 and outside strings."""
    parts = []
    depth = 0
    current = []
    i = 0
    quote = None
    while i < len(s):
        c = s[i]
        if quote:
            current.append(c)
            if c == "\\":
                current.append(s[i + 1 : i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            current.append(c)
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if depth == 0 and s.startswith(op, i):
            parts.append("".join(current))
            current = []
            i += len(op)
            continue
        current.append(c)
        i += 1
    parts.append("".join(current))
    return parts


def _split_ternary(e: str):
    """Split `cond ? a : b` at top level; None when not a plain ternary."""
    depth = 0
    quote = None
    q = None
    colon = None
    i = 0
    while i < len(e):
        c = e[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == "?" and e[i + 1 : i + 2] != ".":
            q = i
        elif depth == 0 and c == ":" and q is not None and colon is None:
            colon = i
        i += 1
    if q is None or colon is None:
        return None
    return [e[:q].strip(), e[q + 1 : colon].strip(), e[colon + 1 :].strip()]


def find_innerhtml_assignments(src: str):
    """Yield (line, region_start, region) for each `.innerHTML =` assignment."""
    for m in re.finditer(r"\.innerHTML\s*(=)\s*", src):
        start = m.end()
        end = _slurp_region(src, start)
        yield _line_of(src, m.start()), start, src[start:end], m.start()


def unsafe_interpolations(path: str):
    """Return a list of (line, expr) for unsafe `.innerHTML =` interpolations."""
    with open(path, encoding="utf-8-sig") as fh:
        src = _strip_comments(fh.read())

    local_objects = {}
    for name in re.findall(r"(?:^|\n)\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\{", src):
        keys = _members_of_object_literal(src, name)
        if keys:
            local_objects[name] = keys

    bad = []
    for _assign_line, region_start, region, _at in find_innerhtml_assignments(src):
        for expr, abs_index in _collect_interpolations(src, region_start, region):
            if not _is_safe_expr(expr, src, local_objects):
                bad.append((_line_of(src, abs_index), expr))
    return bad


# ---------------------------------------------------------------------------
# 1. innerHTML interpolation guard (H1/H2/H3)
# ---------------------------------------------------------------------------


class TestNoUnescapedInnerHtml(unittest.TestCase):
    def test_no_innerhtml_interpolates_unescaped_remote_value(self):
        failures = []
        for path in JS_FILES:
            for line, expr in unsafe_interpolations(path):
                failures.append(f"{os.path.relpath(path, ROOT)}:{line} -> ${{{expr}}}")
        self.assertEqual(
            failures,
            [],
            "innerHTML assignment interpolates a value with no escape helper "
            "(H1/H2/H3):\n  " + "\n  ".join(failures),
        )

    def test_extractor_actually_detects_a_regression(self):
        """Self-test: the scanner must FAIL on the historical vulnerable shape."""
        vulnerable = (
            "function render(s) {\n"
            "  item.innerHTML = `\n"
            "    <div class=\"session-item-id\">${s.session_id}</div>\n"
            "    <span>${s.product}</span>\n"
            "  `;\n"
            "}\n"
        )
        self.assert_unsafe(vulnerable, "s.session_id", "s.product")

    def test_extractor_accepts_the_escaped_shape(self):
        safe = (
            "function tfEsc(v) {\n"
            "  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;');\n"
            "}\n"
            "function render(s) {\n"
            "  item.innerHTML = `<span>${tfEsc(s.session_id)}</span>`;\n"
            "}\n"
        )
        bad = unsafe_interpolations_from_source(safe)
        self.assertEqual(bad, [], f"escaped shape wrongly flagged: {bad}")

    def test_extractor_rejects_a_helper_that_does_not_escape(self):
        """A helper named like a sanitiser but which does not escape is not safe."""
        fake = (
            "function tfEsc(v) {\n"
            "  return String(v);\n"
            "}\n"
            "function render(s) {\n"
            "  item.innerHTML = `<span>${tfEsc(s.session_id)}</span>`;\n"
            "}\n"
        )
        self.assert_unsafe(fake, "tfEsc(s.session_id)")

    def test_extractor_does_not_trust_toFixed_on_a_remote_value(self):
        """`${s.price.toFixed(2)}` is numeric output of remote state and must be
        rejected unless it goes through an explicit coercion helper."""
        unsafe = "x.innerHTML = `<b>${s.price.toFixed(2)}</b>`;\n"
        self.assert_unsafe(unsafe, "s.price.toFixed(2)")

    def test_extractor_accepts_a_coerced_toFixed(self):
        safe = (
            "function tfNum(v, f) {\n"
            "  const n = Number(v);\n"
            "  return Number.isFinite(n) ? n : f;\n"
            "}\n"
            "x.innerHTML = `<b>${tfNum(s.price, 0).toFixed(2)}</b>`;\n"
        )
        self.assertEqual(unsafe_interpolations_from_source(safe), [])

    def test_extractor_allows_trusted_static_svg(self):
        ok = "x.innerHTML = `<span>${TF_SVG.check}done</span>`;\n"
        self.assertEqual(unsafe_interpolations_from_source(ok), [])

    def test_extractor_allows_constant_ternary(self):
        ok = 'x.innerHTML = `<b>${flag ? "Yes" : "No"}</b>`;\n'
        self.assertEqual(unsafe_interpolations_from_source(ok), [])

    def test_extractor_rejects_remote_ternary_branch(self):
        bad_src = 'x.innerHTML = `<b>${flag ? t.round_number : "—"}</b>`;\n'
        self.assert_unsafe(bad_src, "t.round_number")

    def test_extractor_rejects_a_const_bound_to_remote_text(self):
        bad_src = "const inner = s.session_id;\nx.innerHTML = `<b>${inner}</b>`;\n"
        self.assert_unsafe(bad_src, "inner")

    def assert_unsafe(self, source, *expected_fragments):
        bad = unsafe_interpolations_from_source(source)
        self.assertTrue(bad, f"scanner missed an unsafe interpolation in:\n{source}")
        joined = " | ".join(e for _l, e in bad)
        for frag in expected_fragments:
            self.assertIn(frag, joined)


def unsafe_interpolations_from_source(src: str):
    """`unsafe_interpolations` for an in-memory source string (used by self-tests)."""
    src = _strip_comments(src)
    local_objects = {}
    for name in re.findall(r"(?:^|\n)\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\{", src):
        keys = _members_of_object_literal(src, name)
        if keys:
            local_objects[name] = keys
    bad = []
    for _line, region_start, region, _at in find_innerhtml_assignments(src):
        for expr, abs_index in _collect_interpolations(src, region_start, region):
            if not _is_safe_expr(expr, src, local_objects):
                bad.append((_line_of(src, abs_index), expr))
    return bad


class TestInnerHtmlAssignmentForm(unittest.TestCase):
    def test_innerhtml_is_assigned_never_appended(self):
        offenders = []
        for path in JS_FILES:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            if re.search(r"\.innerHTML\s*\+=", src):
                offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(
            offenders,
            [],
            f".innerHTML += concatenates remote text into existing markup: {offenders}",
        )


# ---------------------------------------------------------------------------
# 2. H4 — false success on command dispatch
# ---------------------------------------------------------------------------


class TestCommandDispatchNoFalseSuccess(unittest.TestCase):
    def setUp(self):
        with open(BACKGROUND_JS, encoding="utf-8") as fh:
            self.background = fh.read()

    def test_dispatch_posts_the_field_name_the_server_reads(self):
        """main.py reads body.get("command") or body.get("command_text")."""
        start = self.background.find('if (msg.type === "DISPATCH_OVERRIDE_COMMAND")')
        end = self.background.find('if (port.name.startsWith("tf-content")')
        block = self.background[start:end]
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.assertIn(
            "command:",
            block,
            "the command dispatcher must post `command`, the field /webhook/mobile-command reads",
        )
        self.assertNotIn(
            "raw_text:",
            block,
            "the command dispatcher must not post `raw_text`; that field is ignored and the call returns HTTP 400",
        )
        self.assertIn(
            "raw_text:",
            self.background,
            "the desk import posts raw_text, which /api/v1/desk quotes actually reads",
        )

    def test_dispatch_checks_res_ok_and_reports_real_status(self):
        self.assertIn("res.ok", self.background)
        self.assertIn("httpStatus: res.status", self.background)
        self.assertIn("success: false", self.background)

    def test_dispatcher_never_hardcodes_success_true(self):
        """The worker must not fabricate `success: true` for a COMMAND_RESULT.

        `sendResponse({ success: true, ... })` for GET_TAB_SESSION/GET_ALL_SESSIONS
        is a different contract (it reports a successful *lookup*, not a command
        application) and is allowed. The check is scoped to the command-dispatch
        region and ignores comments.
        """
        code = _strip_comments(self.background)
        dispatch_start = code.index("DISPATCH_OVERRIDE_COMMAND")
        dispatch_end = code.index("tf-content", dispatch_start)
        dispatch = code[dispatch_start:dispatch_end]
        self.assertNotRegex(
            dispatch,
            r"success:\s*true",
            "the command dispatcher hardcodes success: true — it must derive "
            "success from res.ok and the response body (H4)",
        )
        self.assertIn("applied", dispatch, "the dispatcher must derive success from the reply")

    def test_lookup_sendresponse_is_not_part_of_the_command_path(self):
        code = _strip_comments(self.background)
        # The only remaining `success: true` literals must belong to the lookup
        # handlers, never the command dispatcher.
        for m in re.finditer(r"success:\s*true", code):
            context = code[max(0, m.start() - 120) : m.start()]
            self.assertIn("sendResponse", context, "success: true outside a lookup response")

    def test_webhook_call_still_targets_the_mobile_command_endpoint(self):
        self.assertIn("/webhook/mobile-command", self.background)

    def test_sidepanel_reports_failure_not_ok(self):
        with open(SIDEPANEL_JS, encoding="utf-8") as fh:
            sidepanel = fh.read()
        self.assertIn("Command NOT applied", sidepanel)


# ---------------------------------------------------------------------------
# 3. H5 — clipboard failure must not look like success
# ---------------------------------------------------------------------------


class TestClipboardFailureHandling(unittest.TestCase):
    """H5 — a failed clipboard write must never look like a success.

    [2026-10-17] These checks used to grep for the literal labels
    "已複製！" / "複製失敗，請手動選取". Two problems: the UI is now English
    (the operator-facing text, unlike the supplier-facing card bodies), and the
    success check matched an English *comment* that still mentioned the old
    label — so it passed even after the handler changed. The assertions below
    are structural instead: they extract the button handler, split it at the
    catch, and require the two paths to render different labels.
    """

    def setUp(self):
        with open(CONTENT_JS, encoding="utf-8") as fh:
            self.content = fh.read()
        self.handler = self._extract_copy_handler()

    def _extract_copy_handler(self) -> str:
        anchor = 'deck.querySelectorAll(".tf-copy-btn")'
        start = self.content.index(anchor)
        end = self.content.index("\n    }\n", start)
        return self.content[start:end]

    def _split_try_catch(self):
        handler = self.handler
        try_idx = handler.index("try {")
        catch_idx = handler.index("} catch")
        self.assertLess(try_idx, catch_idx, "clipboard handler has no try/catch")
        return handler[try_idx:catch_idx], handler[catch_idx:]

    def test_clipboard_write_is_awaited_and_catched(self):
        self.assertIn("navigator.clipboard.writeText(text)", self.content)
        self.assertIn(
            "await navigator.clipboard.writeText",
            self.handler,
            "the clipboard promise must be awaited (H5)",
        )
        self.assertIn("catch (err)", self.handler)

    def test_success_label_is_only_reachable_from_the_try_block(self):
        """The success label must live in the try block only, never in catch."""
        import re

        try_body, catch_body = self._split_try_catch()
        succeeds = re.findall(r"btn\.innerHTML\s*=\s*`([^`]*)`", try_body)
        self.assertTrue(succeeds, "the success path no longer re-labels the button")
        success_label = next(
            (s for s in succeeds if "TF_SVG.check" in s),
            None,
        )
        self.assertIsNotNone(
            success_label, "the success path must set a tick label, not just any label"
        )
        self.assertNotIn(
            success_label,
            catch_body,
            "the failure path can reach the success label",
        )

    def test_failure_path_shows_a_visible_non_success_state(self):
        import re

        try_body, catch_body = self._split_try_catch()
        failures = re.findall(r"btn\.innerHTML\s*=\s*`([^`]*)`", catch_body)
        self.assertTrue(
            failures,
            "the clipboard failure path must visibly change the button, not stay silent",
        )
        try_labels = re.findall(r"btn\.innerHTML\s*=\s*`([^`]*)`", try_body)
        self.assertFalse(
            set(failures) & set(try_labels),
            f"the failure path reuses a success label: {failures}",
        )
        self.assertIn(
            "console.warn",
            catch_body,
            "a swallowed clipboard failure would hide the operator's only cue",
        )

    def test_manifest_declares_clipboardwrite(self):
        import json

        with open(MANIFEST_JSON, encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertIn(
            "clipboardWrite",
            manifest.get("permissions", []),
            "navigator.clipboard.writeText needs the clipboardWrite permission (H5)",
        )


# ---------------------------------------------------------------------------
# 4. H6 — observer direction filter
# ---------------------------------------------------------------------------


class TestChatObserverDirectionFilter(unittest.TestCase):
    def setUp(self):
        with open(CONTENT_JS, encoding="utf-8-sig") as fh:
            self.content = fh.read()

    def test_observer_filters_sent_messages(self):
        self.assertIn("messageDirection", self.content)
        self.assertIn("isSentClass", self.content)
        self.assertRegex(
            self.content,
            r'messageDirection\(node\)\s*===\s*"sent"',
            "the chat observer must skip the buyer's own (sent) message (H6)",
        )

    def test_sent_markers_cover_the_shipped_platform_classes(self):
        # extension/mock_chat.html uses msg-received vs msg-sent.
        self.assertIn("msg-sent", self.content)
        self.assertIn("message-out", self.content)

    def test_direction_matching_is_token_based_not_substring(self):
        """`some-thing`/`absent` must not be mistaken for a sent message."""
        self.assertIn("SENT_CLASS_TOKENS", self.content)
        self.assertNotRegex(
            self.content,
            r"marker\.includes\(m\)",
            "substring matching misclassifies classes such as 'absent' (contains "
            "'sent') as the buyer's own message",
        )

    def test_direction_filter_runs_before_ingestion(self):
        code = self.content
        filter_at = code.index('messageDirection(node) === "sent"')
        ingest_at = code.index("this.lruCache.add(text)")
        self.assertLess(
            filter_at,
            ingest_at,
            "the direction filter must run before the text is ingested (H6)",
        )


# ---------------------------------------------------------------------------
# 5. H7 — session registry restored outside onInstalled
# ---------------------------------------------------------------------------


class TestSessionRegistryStartupRestore(unittest.TestCase):
    def setUp(self):
        with open(BACKGROUND_JS, encoding="utf-8-sig") as fh:
            self.background = fh.read()
        self.code = _strip_comments(self.background)

    def test_onstartup_restore_path_exists(self):
        self.assertIn("chrome.runtime.onStartup", self.code)

    def test_startup_restore_is_also_called_at_worker_top_level(self):
        # A bare top-level call guarantees a restore even when the worker is
        # revived without an onStartup event.
        self.assertRegex(
            self.code,
            r"\nrestoreTabSessions\(\);",
            "restoreTabSessions() must also run on every worker start (H7)",
        )

    def test_restore_rejects_non_integer_tab_keys(self):
        self.assertIn("Number.isInteger(tabId)", self.code)

    def test_startup_registry_defaults_are_initialised_before_restore(self):
        """The registry Map must exist before the top-level restore call runs."""
        self.assertLess(
            self.code.index("const tabSessions = new Map();"),
            self.code.index("\nrestoreTabSessions();"),
            "tabSessions is used before it is declared",
        )


# ---------------------------------------------------------------------------
# 6. H8 — port-name tab-id parsing
# ---------------------------------------------------------------------------


class TestPortNameParsing(unittest.TestCase):
    def test_no_tab_id_from_port_name_split(self):
        offenders = []
        for path in EXTENSION_FILES:
            with open(path, encoding="utf-8-sig") as fh:
                src = _strip_comments(fh.read())
            for m in re.finditer(r"parseInt\([^)]*port\.name[^)]*split[^)]*\)", src):
                offenders.append(f"{os.path.relpath(path, ROOT)}:{_line_of(src, m.start())}")
        self.assertEqual(
            offenders,
            [],
            "tab id derived from parsing the port name 'tf-content' yields NaN (H8): "
            + ", ".join(offenders),
        )

    def test_content_port_is_rejected_when_sender_tab_id_is_missing(self):
        with open(BACKGROUND_JS, encoding="utf-8-sig") as fh:
            src = _strip_comments(fh.read())
        self.assertIn("port.sender?.tab?.id", src)
        self.assertRegex(
            src,
            r"if\s*\(!Number\.isInteger\(tabId\)\)",
            "an unresolvable content-port tab id must be refused, not stored as NaN",
        )


# ---------------------------------------------------------------------------
# 7. H9 — outbound auth headers
# ---------------------------------------------------------------------------


class TestOutboundAuthHeaders(unittest.TestCase):
    def test_api_key_header_is_wired_in_every_outbound_client(self):
        for path in (APP_JS, SIDEPANEL_JS, BACKGROUND_JS):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn(
                '"X-API-Key"',
                src,
                f"{os.path.relpath(path, ROOT)} does not send X-API-Key (H9)",
            )

    def test_empty_key_sends_no_header(self):
        """A truthy guard keeps the sandbox demo (no key configured) working."""
        for path in (APP_JS, SIDEPANEL_JS, BACKGROUND_JS):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotRegex(
                src,
                r'"X-API-Key"\s*:\s*""',
                f"{os.path.relpath(path, ROOT)} sets an empty X-API-Key header",
            )

    def test_webhook_token_header_is_wired_where_the_webhook_is_called(self):
        for path in (APP_JS, BACKGROUND_JS):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn('"X-Webhook-Token"', src)


# ---------------------------------------------------------------------------
# 8. V-items confirmed during the audit pass
# ---------------------------------------------------------------------------


class TestConfirmedVerificationItems(unittest.TestCase):
    def test_v2_telemetry_field_ids_match_the_server_allowlist(self):
        """app.js/index.html must name the allowlisted telemetry keys."""
        with open(APP_JS, encoding="utf-8-sig") as fh:
            app = _strip_comments(fh.read())
        with open(INDEX_HTML, encoding="utf-8-sig") as fh:
            html = fh.read()
        for key in ("WHATSAPP_PHONE", "WHATSAPP_APIKEY", "SERVERCHAN_SENDKEY"):
            self.assertIn(key, app, f"app.js does not post the allowlisted key {key} (V2)")
            self.assertIn(f'id="cfg_{key}"', html, f"index.html has no input for {key} (V2)")
        # The pre-fix, non-functional key names must be gone from both the payload
        # list and the form field ids.
        for dead in ("CALLMEBOT_PHONE", "PUSHPLUS_TOKEN"):
            self.assertNotIn(dead, app, f"app.js still posts the non-allowlisted key {dead}")
            self.assertNotIn(dead, html, f"index.html still renders a field for {dead}")

    def test_v3_refresh_sessions_is_defined(self):
        with open(APP_JS, encoding="utf-8") as fh:
            app = fh.read()
        with open(INDEX_HTML, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('onclick="refreshSessions()"', html)
        self.assertRegex(
            app,
            r"(?:async\s+)?function\s+refreshSessions\s*\(",
            "index.html calls refreshSessions() but app.js does not define it (V3)",
        )

    def test_v1_extension_sends_the_mode_field(self):
        with open(CONTENT_JS, encoding="utf-8") as fh:
            content = fh.read()
        with open(BACKGROUND_JS, encoding="utf-8") as fh:
            background = fh.read()
        self.assertRegex(content, r"mode:\s*overlayEl\._state\.mode", "content.js must send mode (V1)")
        self.assertRegex(background, r"mode:\s*mode\s*===\s*\"copilot\"", "background.js must forward mode (V1)")

    def test_v5_cdn_script_has_integrity_and_csp(self):
        with open(INDEX_HTML, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("cdn.jsdelivr.net", html)
        self.assertIn("integrity=", html, "the CDN script tag has no SRI hash (V5)")
        self.assertIn("crossorigin=", html)
        self.assertIn("Content-Security-Policy", html)


if __name__ == "__main__":
    unittest.main()
