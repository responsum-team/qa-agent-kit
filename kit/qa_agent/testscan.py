"""Find the tests in source files without running them.

Python files are read through their syntax tree; JavaScript and TypeScript
through a small lexer that knows strings, templates, comments and brackets.
Each test comes back with what it visibly does: the HTTP calls and pages it
names, the status codes it compares with, and whether it asserts anything.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .util import key_words, match_any, walk_files

PY_TEST_FILES = ["test_*.py", "*_test.py"]
JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
JS_TEST_FILES = ["*.spec.*", "*.test.*", "*.cy.*", "*.e2e.*", "__tests__"]
OTHER_TEST_FILES = ["*_test.go", "*Test.java", "*Tests.java", "*Test.kt", "*_spec.rb", "*_test.rb", "*Tests.cs", "*.robot", "*.feature"]

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
ASSERT_PREFIXES = ("expect", "assert", "verify", "check", "should", "validate")
ASSERT_NAMES = {"raises", "warns", "fail", "raise_for_status", "deprecated_call"}
WAIT_NAMES = {"waitfortimeout", "wait_for_timeout", "settimeout", "sleep", "wait", "delay", "timeout", "set_default_timeout"}
MAX_WORDS = 120


# ---------------------------------------------------------------- shared


_ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f-]{20,}|[0-9a-f]{16,}|:\w+|<[^>]+>|\{[^}]*\}|%[sd])$", re.I)


def norm_path(text):
    """A URL or path literal as a comparable route: no host, no query, identifiers as `{}`."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > 300 or "\n" in text or " " in text:
        return None
    text = re.sub(r"^https?://[^/]+", "", text, flags=re.I) or "/"
    if text.startswith("{}"):  # an interpolated base URL
        text = "/" + text[2:].lstrip("/")
    if not text.startswith("/") or text.startswith("//"):
        return None
    text = re.split(r"[?#]", text, maxsplit=1)[0]
    parts = []
    for segment in text.split("/"):
        if not segment:
            continue
        parts.append("{}" if _ID_SEGMENT.match(segment) else re.sub(r"\{[^}]*\}", "{}", segment).lower())
    return "/" + "/".join(parts)


def _is_assert_name(name: str) -> bool:
    lower = name.lower()
    return lower in ASSERT_NAMES or lower.startswith(ASSERT_PREFIXES)


def _record(rel, line, end_line, names, lang) -> dict:
    return {
        "file": rel, "line": line, "end_line": end_line, "name": " › ".join(names),
        "ref": rel + "::" + "::".join(names), "lang": lang,
        "skipped": False, "expected_failure": False, "only": False, "params": None,
        "assertions": 0, "calls": [], "visits": [], "statuses": [],
        "title_words": sorted(key_words(" ".join(names))), "body_words": [],
    }


def _finish(record: dict, calls, visits, statuses, words) -> dict:
    record["calls"] = sorted(calls)
    record["visits"] = sorted(visits)
    record["statuses"] = sorted(statuses)
    record["body_words"] = sorted(words)[:MAX_WORDS]
    return record


# ---------------------------------------------------------------- Python


def _py_text(node):
    """The text of a string expression, with `{}` where a value is filled in at run time."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}"
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _py_text(node.left), _py_text(node.right)
        if left is None and right is None:
            return None
        return (left if left is not None else "{}") + (right if right is not None else "{}")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = _py_text(node.left)
        return re.sub(r"%[sdri]", "{}", left) if left else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _py_text(node.func.value)
    return None


def _py_call_name(func) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _py_dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        return _py_dotted(node.func)
    return ".".join(reversed(parts))


class _Signals:
    def __init__(self):
        self.calls, self.visits, self.statuses, self.words = set(), set(), set(), set()
        self.assertions = 0
        self.called = set()

    def merge(self, other) -> None:
        self.calls |= other.calls
        self.visits |= other.visits
        self.statuses |= other.statuses
        self.words |= other.words
        self.assertions += other.assertions


def _status_ints(node) -> set:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and type(child.value) is int and 100 <= child.value <= 599:
            found.add(child.value)
    return found


def _py_signals(function) -> _Signals:
    signals = _Signals()
    for node in ast.walk(function):
        if isinstance(node, ast.Assert):
            signals.assertions += 1
            signals.statuses |= _status_ints(node)
        elif isinstance(node, ast.Compare):
            signals.statuses |= _status_ints(node)
        elif isinstance(node, ast.Call):
            name = _py_call_name(node.func)
            lower = name.lower()
            if isinstance(node.func, ast.Name):
                signals.called.add(name)
            first = _py_text(node.args[0]) if node.args else None
            url = next((_py_text(k.value) for k in node.keywords if k.arg == "url"), None)
            if lower in HTTP_METHODS and isinstance(node.func, ast.Attribute):
                path = norm_path(first if first is not None else url)
                if path:
                    signals.calls.add(f"{lower.upper()} {path}")
            elif lower == "request" and len(node.args) >= 2 and first and first.lower() in HTTP_METHODS:
                path = norm_path(_py_text(node.args[1]))
                if path:
                    signals.calls.add(f"{first.upper()} {path}")
            elif lower in ("goto", "visit", "navigate"):
                path = norm_path(first if first is not None else url)
                if path:
                    signals.visits.add(path)
            if _is_assert_name(name):
                signals.assertions += 1
                if lower not in WAIT_NAMES:
                    for arg in node.args:
                        signals.statuses |= _status_ints(arg)
            elif "status" in lower:
                for arg in node.args:
                    signals.statuses |= _status_ints(arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) <= 200:
            signals.words |= key_words(node.value)
    return signals


def _py_marks(decorators) -> dict:
    marks = {"skipped": False, "expected_failure": False, "params": None}
    for decorator in decorators:
        dotted = _py_dotted(decorator)
        last = dotted.rsplit(".", 1)[-1]
        if last in ("skip", "skipif", "skipIf", "skipUnless"):
            marks["skipped"] = True
        elif last in ("xfail", "expectedFailure"):
            marks["expected_failure"] = True
        elif last == "parametrize" and isinstance(decorator, ast.Call) and len(decorator.args) >= 2:
            values = decorator.args[1]
            if isinstance(values, (ast.List, ast.Tuple)):
                marks["params"] = (marks["params"] or 1) * max(1, len(values.elts))
    return marks


def scan_python(text: str, rel: str) -> list:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    helpers = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test")
    }
    helper_signals: dict = {}

    def helper(name: str):
        if name not in helper_signals:
            helper_signals[name] = _py_signals(helpers[name])
        return helper_signals[name]

    module_marks = {"skipped": False, "expected_failure": False}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            marks = _py_marks(values)
            module_marks = {"skipped": marks["skipped"], "expected_failure": marks["expected_failure"]}

    tests = []

    def visit(body, groups, inherited):
        for node in body:
            if isinstance(node, ast.ClassDef):
                marks = _py_marks(node.decorator_list)
                visit(node.body, groups + [node.name], {
                    "skipped": inherited["skipped"] or marks["skipped"],
                    "expected_failure": inherited["expected_failure"] or marks["expected_failure"],
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                marks = _py_marks(node.decorator_list)
                signals = _py_signals(node)
                for name in sorted(signals.called):
                    if name in helpers:  # one level of helpers defined in the same file
                        signals.merge(helper(name))
                record = _record(rel, node.lineno, getattr(node, "end_lineno", node.lineno), groups + [node.name], "python")
                record["skipped"] = inherited["skipped"] or marks["skipped"]
                record["expected_failure"] = inherited["expected_failure"] or marks["expected_failure"]
                record["params"] = marks["params"]
                record["assertions"] = signals.assertions
                doc = ast.get_docstring(node)
                if doc:
                    signals.words |= key_words(doc[:300])
                tests.append(_finish(record, signals.calls, signals.visits, signals.statuses, signals.words))

    visit(tree.body, [], module_marks)
    return tests


# ---------------------------------------------------------------- JavaScript and TypeScript


_REGEX_AFTER_PUNCT = set("(,=:[!&|?{};+-*%~^")
_REGEX_AFTER_WORD = {"return", "typeof", "case", "do", "else", "in", "of", "void", "delete", "throw", "new", "await", "yield"}
_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def _js_string(text: str, i: int):
    quote, n = text[i], len(text)
    out = []
    i += 1
    while i < n:
        char = text[i]
        if char == "\\":
            out.append(text[i + 1 : i + 2])
            i += 2
            continue
        if char == quote or char == "\n":
            return "".join(out), i + 1
        out.append(char)
        i += 1
    return "".join(out), n


def _js_template(text: str, i: int):
    n = len(text)
    out = []
    i += 1
    while i < n:
        char = text[i]
        if char == "\\":
            out.append(text[i + 1 : i + 2])
            i += 2
            continue
        if char == "`":
            return "".join(out), i + 1
        if char == "$" and text[i + 1 : i + 2] == "{":
            i = _js_skip_expression(text, i + 2)
            out.append("{}")
            continue
        out.append(char)
        i += 1
    return "".join(out), n


def _js_skip_expression(text: str, i: int) -> int:
    depth, n = 1, len(text)
    while i < n:
        char = text[i]
        if char in "'\"":
            _, i = _js_string(text, i)
            continue
        if char == "`":
            _, i = _js_template(text, i)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _js_regex_end(text: str, i: int):
    """Index after a regex literal starting at i, or None when this slash is not one."""
    n = len(text)
    j, in_class = i + 1, False
    while j < n:
        char = text[j]
        if char == "\n":
            return None
        if char == "\\":
            j += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            j += 1
            while j < n and text[j].isalpha():
                j += 1
            return j
        j += 1
    return None


def js_tokens(text: str) -> list:
    """(kind, value, line) for identifiers, strings, numbers and punctuation; comments are dropped."""
    tokens = []
    i, n, line = 0, len(text), 1
    while i < n:
        char = text[i]
        if char == "\n":
            line += 1
            i += 1
        elif char.isspace() or char == "\ufeff":
            i += 1
        elif char == "/" and text[i + 1 : i + 2] == "/":
            end = text.find("\n", i)
            i = n if end == -1 else end
        elif char == "/" and text[i + 1 : i + 2] == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            line += text.count("\n", i, end)
            i = end
        elif char in "'\"":
            value, end = _js_string(text, i)
            tokens.append(("str", value, line))
            line += text.count("\n", i, end)
            i = end
        elif char == "`":
            value, end = _js_template(text, i)
            tokens.append(("str", value, line))
            line += text.count("\n", i, end)
            i = end
        elif char == "/":
            previous = tokens[-1] if tokens else None
            allowed = (
                previous is None
                or (previous[0] == "p" and previous[1] in _REGEX_AFTER_PUNCT)
                or (previous[0] == "id" and previous[1] in _REGEX_AFTER_WORD)
            )
            end = _js_regex_end(text, i) if allowed else None
            if end is None:
                tokens.append(("p", "/", line))
                i += 1
            else:
                tokens.append(("regex", text[i:end], line))
                i = end
        elif char.isalpha() or char in "_$":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            tokens.append(("id", text[i:j], line))
            i = j
        elif char.isdigit():
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "._"):
                j += 1
            tokens.append(("num", text[i:j], line))
            i = j
        else:
            tokens.append(("p", char, line))
            i += 1
    return tokens


def _js_pairs(tokens: list) -> dict:
    match, stack = {}, []
    for index, (kind, value, _line) in enumerate(tokens):
        if kind != "p":
            continue
        if value in _CLOSERS:
            stack.append(index)
        elif value in ")]}":
            while stack:
                opener = stack.pop()
                if _CLOSERS[tokens[opener][1]] == value:
                    match[opener] = index
                    break
    return match


JS_TESTS = {"test", "it", "specify", "xit", "fit", "xtest"}
JS_GROUPS = {"describe", "context", "suite", "xdescribe", "fdescribe"}
JS_MODIFIERS = {
    "only", "skip", "fixme", "fail", "fails", "failing", "slow", "concurrent", "sequential", "todo", "each", "runIf",
    "skipIf", "serial", "parallel", "describe",
}
JS_EXPECTED_FAILURE = ("fail", "fails", "failing")  # Playwright, Vitest, Jest
_JS_DECLARATION = {"function", "const", "let", "var", "class", "import", "interface", "type", "enum"}


def _is(token, kind, value=None) -> bool:
    return token is not None and token[0] == kind and (value is None or token[1] == value)


def _js_argument(body: list, start: int):
    """The text of the argument starting at `start`, and the index after it.

    A string, or strings joined with `+` to other values, which become `{}`.
    None when the argument holds no string literal at all.
    """
    parts, current, depth, i = [], [], 0, start
    while i < len(body):
        kind, value, _line = body[i]
        if kind == "p":
            if value in "([{":
                depth += 1
            elif value in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and value == ",":
                break
            elif depth == 0 and value == "+":
                parts.append(current)
                current = []
                i += 1
                continue
        current.append(body[i])
        i += 1
    parts.append(current)
    if not any(len(part) == 1 and part[0][0] == "str" for part in parts):
        return None, i
    return "".join(part[0][1] if len(part) == 1 and part[0][0] == "str" else "{}" for part in parts), i


def _js_signals(body: list):
    calls, visits, statuses, words = set(), set(), set(), set()
    assertions = 0
    for k, token in enumerate(body):
        kind, value, _line = token
        before = body[k - 1] if k else None
        after = body[k + 1] if k + 1 < len(body) else None
        if kind == "id" and _is(after, "p", "("):
            lower = value.lower()
            dotted = _is(before, "p", ".")
            text, end = _js_argument(body, k + 2)
            if dotted and lower in HTTP_METHODS:
                path = norm_path(text)
                if path:
                    calls.add(f"{lower.upper()} {path}")
            elif lower in ("goto", "visit") and dotted:
                path = norm_path(text)
                if path:
                    visits.add(path)
            elif lower in ("request", "fetch", "axios") and text is not None:
                second = _js_argument(body, end + 1)[0] if end < len(body) and _is(body[end], "p", ",") else None
                if text.lower() in HTTP_METHODS and second is not None:
                    path = norm_path(second)
                    if path:
                        calls.add(f"{text.upper()} {path}")
                else:
                    path = norm_path(text)
                    if path:
                        calls.add(f"* {path}")
            if _is_assert_name(value):
                assertions += 1
        elif kind == "id" and value in ("expect", "assert") and _is(after, "p", "."):
            assertions += 1  # expect.soft(...), assert.equal(...)
        elif kind == "num" and value.isdigit() and 100 <= int(value) <= 599:
            if _is(before, "p") and before[1] in "(,=":
                caller = body[k - 2] if k >= 2 else None
                if not (_is(caller, "id") and caller[1].lower() in WAIT_NAMES):
                    statuses.add(int(value))
        elif kind == "str" and len(value) <= 200:
            words |= key_words(value)
    return calls, visits, statuses, words, assertions


def scan_js(text: str, rel: str) -> list:
    tokens = js_tokens(text)
    pairs = _js_pairs(tokens)
    tests, groups = [], []  # groups: (title, closing index, skipped, only)
    i, n = 0, len(tokens)
    while i < n:
        while groups and i > groups[-1][1]:
            groups.pop()
        kind, value, line = tokens[i]
        base_ok = kind == "id" and (value in JS_TESTS or value in JS_GROUPS)
        before = tokens[i - 1] if i else None
        if not base_ok or _is(before, "p", ".") or (_is(before, "id") and before[1] in _JS_DECLARATION):
            i += 1
            continue
        j, chain = i + 1, []
        while j + 1 < n and _is(tokens[j], "p", ".") and _is(tokens[j + 1], "id"):
            chain.append(tokens[j + 1][1])
            j += 2
        if any(name not in JS_MODIFIERS for name in chain):
            i += 1
            continue
        if chain and chain[-1] in ("each", "skipIf", "runIf") and j < n:
            if _is(tokens[j], "p", "(") and j in pairs:
                j = pairs[j] + 1  # the table or the condition
            elif _is(tokens[j], "str"):
                j += 1  # a tagged template table
        if j >= n or not _is(tokens[j], "p", "(") or j not in pairs:
            i += 1
            continue
        opening, closing = j, pairs[j]
        title = tokens[opening + 1] if opening + 1 < closing else None
        if not _is(title, "str"):
            i += 1
            continue
        is_group = value in JS_GROUPS or "describe" in chain
        skipped = value.startswith("x") or any(name in chain for name in ("skip", "fixme", "todo", "skipIf"))
        only = value in ("fit", "fdescribe") or "only" in chain
        has_body = _is(tokens[opening + 2] if opening + 2 < closing else None, "p", ",")
        if is_group:
            if has_body:
                groups.append((title[1], closing, skipped, only))
            i = opening + 1
            continue
        if not has_body and "todo" not in chain:
            i += 1
            continue
        names = [group[0] for group in groups] + [title[1]]
        record = _record(rel, line, tokens[closing][2], names, "js")
        record["skipped"] = skipped or any(group[2] for group in groups)
        record["only"] = only or any(group[3] for group in groups)
        record["expected_failure"] = any(name in chain for name in JS_EXPECTED_FAILURE)
        if "each" in chain:
            record["params"] = 0  # a table of cases; how many is not read
        calls, visits, statuses, words, assertions = _js_signals(tokens[opening + 2 : closing])
        record["assertions"] = assertions
        tests.append(_finish(record, calls, visits, statuses, words))
        i = closing + 1
    return tests


# ---------------------------------------------------------------- files and trees


def language(rel: str):
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(".py"):
        return "python"
    if name.endswith(JS_EXTENSIONS) and not name.endswith(".d.ts"):
        return "js"
    return None


def is_test_file(rel: str, extra=()) -> bool:
    lang = language(rel)
    if lang == "python":
        return match_any(rel, PY_TEST_FILES) or match_any(rel, extra)
    if lang == "js":
        return match_any(rel, JS_TEST_FILES) or match_any(rel, extra)
    return False


def scan_file(path, rel: str) -> list:
    lang = language(rel)
    if lang is None:
        return []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return scan_python(text, rel) if lang == "python" else scan_js(text, rel)


def scan_tree(root, include=(), exclude=()) -> dict:
    """Every test under a folder. `include` adds file globs beyond the usual test file names."""
    root = Path(root)
    tests, files, unsupported = [], 0, []
    for rel in walk_files(root, ("**",), exclude):
        if is_test_file(rel, include):
            found = scan_file(root / rel, rel)
            if found:
                files += 1
                tests.extend(found)
        elif match_any(rel, OTHER_TEST_FILES):
            unsupported.append(rel)
    return {"tests": tests, "files": files, "unsupported": unsupported}
