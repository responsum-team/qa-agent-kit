"""Small shared helpers: JSON, git, globs, env files, HTTP."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class QAError(Exception):
    """A problem the person or the agent can fix. Printed without a traceback."""


# ---------------------------------------------------------------- time


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return now().strftime("%Y%m%dT%H%M%SZ")


def today() -> str:
    return now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- json


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QAError(f"{path} is not valid JSON: {exc}") from exc


def write_json(path, data) -> None:
    """Write atomically, so an interrupted command never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dumps(data))
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _flat(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))


def dumps(obj, level: int = 0, width: int = 150) -> str:
    """JSON that stays readable and short: one line per record where it fits."""
    pad = "  " * level
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        nested = any(isinstance(v, (dict, list)) and v for v in obj.values())
        flat = _flat(obj)
        if level > 0 and not nested and len(flat) + len(pad) <= width:
            return flat
        rows = [
            f"{pad}  {json.dumps(str(k), ensure_ascii=False)}: {dumps(v, level + 1, width)}"
            for k, v in obj.items()
        ]
        return "{\n" + ",\n".join(rows) + "\n" + pad + "}"
    if isinstance(obj, (list, tuple)):
        if not obj:
            return "[]"
        flat = _flat(obj)
        if all(not isinstance(v, (dict, list)) for v in obj) and len(flat) + len(pad) <= width:
            return flat
        rows = []
        for item in obj:
            line = _flat(item)
            if len(line) + len(pad) + 2 <= width:
                rows.append(pad + "  " + line)
            else:
                rows.append(pad + "  " + dumps(item, level + 1, width))
        return "[\n" + ",\n".join(rows) + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=False)


def emit(obj) -> None:
    print(dumps(obj))


# ---------------------------------------------------------------- hashing


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------- git


def git(repo, *args, check: bool = True) -> str:
    """Run git in a repository and return its output without the final newline."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise QAError("git is not installed or not on PATH") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise QAError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return proc.stdout.rstrip("\n")


def git_ok(repo, *args) -> bool:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def git_try(repo, *args):
    """Run git where a failure is an answer, not an error. Returns (succeeded, output)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        return False, ""
    return proc.returncode == 0, proc.stdout.rstrip("\n")


def is_git_repo(path) -> bool:
    return Path(path).is_dir() and git_ok(path, "rev-parse", "--git-dir")


def git_head(repo):
    """The commit a checkout is at, or None before its first commit."""
    if not git_ok(repo, "rev-parse", "--verify", "-q", "HEAD"):
        return None
    return git(repo, "rev-parse", "HEAD")


def git_branch(repo):
    name = git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
    return name or None


def git_dirty(repo) -> bool:
    return bool(git(repo, "status", "--porcelain", check=False).strip())


# ---------------------------------------------------------------- globs


def _glob_body(pattern: str) -> str:
    out = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                i += 2
                if pattern[i : i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "{":
            end = pattern.find("}", i)
            if end == -1:
                out.append(re.escape(char))
            else:
                parts = pattern[i + 1 : end].split(",")
                out.append("(?:" + "|".join(_glob_body(p) for p in parts) + ")")
                i = end
        elif char == "[":
            end = pattern.find("]", i + 2)
            if end == -1:
                out.append(re.escape(char))
            else:
                group = pattern[i + 1 : end]
                if group.startswith("!"):
                    group = "^" + group[1:]
                out.append("[" + group + "]")
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


_GLOB_CACHE: dict = {}


def posix(path: str) -> str:
    """A path with forward slashes and no leading `./`."""
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def glob_regex(pattern: str):
    """Compile a glob. `**` crosses folders, `*` and `?` do not, `{a,b}` alternates."""
    compiled = _GLOB_CACHE.get(pattern)
    if compiled is None:
        text = posix(pattern).rstrip("/")
        if text.startswith("/"):
            text = text[1:]  # a leading slash means: at the top of the repository only, as in .gitignore
        elif "/" not in text:
            # A bare name or `*.ext` matches at any depth, as in .gitignore.
            text = "**/" + text
        compiled = re.compile("^" + _glob_body(text) + "$")
        _GLOB_CACHE[pattern] = compiled
    return compiled


def glob_match(path: str, pattern: str) -> bool:
    """True when the pattern matches the path or any folder that contains it."""
    path = posix(path)
    regex = glob_regex(pattern)
    if regex.match(path):
        return True
    parts = path.split("/")
    for depth in range(len(parts) - 1, 0, -1):
        if regex.match("/".join(parts[:depth])):
            return True
    return False


def match_any(path: str, patterns) -> bool:
    return any(glob_match(path, p) for p in patterns or ())


SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", "dist", "build", ".next", ".nuxt", "coverage", "htmlcov", ".idea",
    ".vscode", "target", "vendor", ".gradle", "playwright-report", "test-results", "worktrees",
}


def walk_files(root, include=("**",), exclude=(), skip_dirs=SKIP_DIRS):
    """Repo-relative paths under root, sorted, without dependency and cache folders."""
    root = Path(root)
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
        base = Path(folder).relative_to(root).as_posix()
        for name in sorted(files):
            rel = name if base == "." else f"{base}/{name}"
            if match_any(rel, include) and not match_any(rel, exclude):
                found.append(rel)
    return found


# ---------------------------------------------------------------- env files


def parse_env_file(path) -> dict:
    """KEY=VALUE lines. Comments, blank lines and `export` are ignored."""
    values: dict = {}
    path = Path(path)
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


# ---------------------------------------------------------------- http


def http_get(url: str, timeout: float = 5.0, headers=None):
    """GET a URL. Returns (status, body, error); status is None when nothing answered."""
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "qa-agent-kit"})
    context = None
    if url.lower().startswith("https") and os.environ.get("QA_AGENT_INSECURE_TLS") == "1":
        context = ssl._create_unverified_context()  # local environments with self-signed certificates
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 - the status is what matters
            body = b""
        return exc.code, body, None
    except Exception as exc:  # noqa: BLE001 - any failure means "did not answer"
        reason = getattr(exc, "reason", None) or exc
        return None, b"", str(reason)


def join_url(base: str, path: str) -> str:
    if not path:
        return base
    return base.rstrip("/") + "/" + path.lstrip("/")


# ---------------------------------------------------------------- text


_WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|[0-9]+")

STOP_WORDS = {
    "test", "tests", "should", "the", "a", "an", "it", "is", "are", "be", "to", "of", "and", "or", "in",
    "on", "for", "with", "when", "then", "that", "this", "can", "does", "do", "via", "by", "as", "at",
    "from", "spec", "check", "checks", "verify", "verifies", "given", "if", "its", "into", "after",
}


def words(text: str) -> list:
    """Lower-case words of a name or sentence: snake_case, camelCase and prose alike."""
    return [w.lower() for w in _WORD.findall(text or "")]


def key_words(text: str) -> set:
    return {w for w in words(text) if w not in STOP_WORDS and len(w) > 1}


def clip(text: str, limit: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
