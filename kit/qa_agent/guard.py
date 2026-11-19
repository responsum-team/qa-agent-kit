"""The check that passes before every commit.

Limits that matter are checked here rather than left to instructions: where the
agent wrote, whether the application's code was left alone, whether a secret is
about to be committed, and whether a test was quietly switched off.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import testscan
from .catalog import Catalog
from .config import agreed, consent_changes
from .util import (
    QAError, clip, git, git_branch, git_head, git_ok, is_git_repo, match_any, now, now_iso, read_json, sha256_bytes,
    write_json,
)

SHOWN = 60
MAX_BYTES = 1_000_000
HOST_ALWAYS = ["/.qa-agent"]
POINTERS = ["AGENTS.md", "CLAUDE.md"]  # the files install.py adds its pointer to, unless the kit lock lists others
HOST = "."  # this repository, among the places whose files must stay as the person left them
SKIP_SCAN = [".qa-agent/kit", ".qa-agent/library", ".qa-agent/library-source", ".qa-agent/runs", ".qa-agent/worktrees"]

SECRET_NAME = re.compile(r"PASS|SECRET|TOKEN|KEY|CREDENTIAL|COOKIE|SESSION", re.I)
PLACEHOLDER = re.compile(r"^(change[-_ ]?me|example|placeholder|todo|xxx+|your[-_ ].*|<.*>|\*+|none|null|true|false|\d+)$", re.I)
TOKEN_SHAPES = [
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("an API key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
    ("a Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("a signed token (JWT)", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]

FOCUSED = re.compile(r"\b(?:describe|it|test|context|suite)(?:\.\w+)*\.only\s*\(|\b(?:fit|fdescribe)\s*\(")
SLEEP = re.compile(r"\btime\.sleep\s*\(|\.waitForTimeout\s*\(|\.wait_for_timeout\s*\(|\bcy\.wait\s*\(\s*\d")
SKIP = re.compile(
    r"pytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.(?:skip|xfail)\s*\("
    r"|unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b"
    r"|\b(?:describe|it|test|context)(?:\.\w+)*\.(?:skip|fixme|fail|fails|failing|todo|skipIf)\s*\("
    r"|\b(?:xit|xdescribe|xtest)\s*\("
)
BEHAVIOUR_ID = re.compile(r"\bB-\d{4,}\b")
ALLOW = re.compile(r"qa-agent:\s*allow-([a-z-]+)\s+\S")
SKIP_STATUSES = ("defect", "quarantined", "blocked")
# A runner that retries reports a flaky test as passed. `qa.py run` re-runs failures itself and records them as flaky.
RETRY_IN_SETTINGS = re.compile(
    r"""\bretries["']?\s*[:=]\s*(?!0\b)[\w(\[{]|--retries(?:=|\s+)[1-9]|--reruns\b|\breruns\s*=\s*[1-9]"""
)
RETRY_IN_TESTS = re.compile(
    r"\bmark\.flaky\b|@flaky\b|\bretryTimes\s*\(\s*[1-9]|\.retries\s*\(\s*[1-9]"
    r"|\bconfigure\s*\(\s*\{[^}]*\bretries\s*:\s*[1-9]|--reruns\b"
)
RUNNER_SETTINGS = [
    "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "package.json", ".mocharc.*", "*.config.*", "conftest.py",
]
CHANGED_FIX = (
    "If you changed it, put it back by editing it yourself (delete a file you created): application code is never "
    "edited, and tests for a service are written in its QA worktree. If you did not, it is the person's work: leave "
    "it, and record that with `qa.py guard --accept-checkouts --note \"<which files, and how you know they are not "
    "yours>\"`. Never discard changes with git (checkout, restore, reset, stash, clean): they may be the person's "
    "unsaved work."
)


def _violation(check: str, where: str, what: str, fix: str) -> dict:
    return {"check": check, "where": where, "what": what, "fix": fix}


# ---------------------------------------------------------------- which files


def uncommitted(repo) -> list:
    """(status code, path) for everything staged, changed or untracked."""
    out = git(repo, "status", "--porcelain", "-z", "--untracked-files=all")
    entries = out.split("\0")
    files, i = [], 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1  # the path it was renamed or copied from
        files.append((code, path))
    return files


def committed_since(repo, base: str, removed: bool = False) -> list:
    """Paths the commits since `base` add or change; with `removed`, also those they delete or rename away."""
    args = ["--diff-filter=ACMRD", "--no-renames"] if removed else ["--diff-filter=ACMR"]
    out = git(repo, "diff", "--name-only", *args, f"{base}..HEAD", check=False)
    return [line for line in out.splitlines() if line.strip()]


def changed_files(repo, base=None, everything=False) -> list:
    """Files the commit (or the branch since `base`) adds or changes: what there is to read. Deleted ones are left out."""
    if everything:
        out = git(repo, "ls-files", "-co", "--exclude-standard", "-z")
        return sorted(p for p in out.split("\0") if p and (Path(repo) / p).is_file())
    paths = {path for code, path in uncommitted(repo) if "D" not in code}
    if base:
        paths.update(committed_since(repo, base))
    return sorted(p for p in paths if (Path(repo) / p).is_file())


def touched_paths(repo, base=None) -> list:
    """Every path the working tree, or the branch since `base`, adds, changes or removes: what scope applies to."""
    paths = {path for _, path in uncommitted(repo)}
    if base:
        paths.update(committed_since(repo, base, removed=True))
    return sorted(paths)


def _text(path: Path):
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- checks


def check_branch(config, repo, label: str) -> list:
    branch_prefix = config["git"]["branch_prefix"]
    if not branch_prefix or git_head(repo) is None:
        return []
    current = git_branch(repo)
    if current and current.startswith(branch_prefix):
        return []
    return [_violation(
        "branch", label, f"on '{current or 'a detached commit'}', not on a QA branch ({branch_prefix}…)",
        "run `qa.py branch --name <what this work is>` before committing",
    )]


def check_kit(paths) -> list:
    lock = read_json(paths.kit_lock)
    if not lock or not (paths.qa / "kit").is_dir():
        return []
    found = []
    expected = lock.get("files") or {}
    for rel, digest in expected.items():
        target = paths.qa / "kit" / rel
        if not target.is_file():
            found.append(_violation("kit", f".qa-agent/kit/{rel}", "a kit file is missing", "re-run the kit's install.py"))
        elif sha256_bytes(target.read_bytes()) != digest:
            found.append(_violation(
                "kit", f".qa-agent/kit/{rel}", "a kit file was edited",
                "re-run the kit's install.py, which restores it; project-specific guidance goes in project.json "
                "'notes' or in the team library, not in the kit",
            ))
    return found


def _private(config) -> list:
    """Files that hold credentials: never committed, never scanned, never anyone's to judge."""
    return [config["environment"]["env_file"]] + list(config["auth"]["state_files"])


def _allowed_here(paths, config) -> list:
    """Globs the agent may write in this repository when it is an application's own repository."""
    allowed = list(HOST_ALWAYS) + [config["environment"]["env_file"] + ".example"]
    for service in config.host_services():
        allowed += list(service["writable"])
    pointers = (read_json(paths.kit_lock, {}) or {}).get("pointers")
    allowed += ["/" + name for name in (POINTERS if pointers is None else pointers)]
    lock = read_json(paths.library_lock, {}) or {}
    for kind in ("rules", "skills"):
        allowed += list((lock.get("mirrors") or {}).get(kind) or [])
    return allowed


def check_scope(files: list, allowed: list, label: str, what: str, fix: str) -> list:
    return [_violation("scope", f"{label}{path}", what, fix) for path in files if not match_any(path, allowed)]


def check_ignored(paths, config) -> list:
    """Files that hold secrets, raw output or a pasted copy of the team's library must exist only on this machine."""
    root = paths.root
    found = []
    private = _private(config)
    if config.host_services():
        where = "keep it under .qa-agent/ (.qa-agent/.env, .qa-agent/.auth/), which is ignored, or list it in a .gitignore inside the test folder"
    else:
        where = "list it in .gitignore"
    for rel in private:
        if not (root / rel).exists():
            continue
        if git_ok(root, "ls-files", "--error-unmatch", rel):
            found.append(_violation("secrets", rel, "is tracked by git", f"`git rm --cached {rel}`, then {where}"))
        elif not git_ok(root, "check-ignore", "-q", rel):
            found.append(_violation("secrets", rel, "is not git-ignored", where))
    for folder in (paths.runs, paths.worktrees, paths.library_source):
        rel = paths.rel(folder)
        if folder.exists() and not git_ok(root, "check-ignore", "-q", rel):
            found.append(_violation(
                "scope", rel, "is not git-ignored",
                "run `qa.py branch --name <what this work is>`: it writes the ignore lines (.qa-agent/.gitignore) again",
            ))
    return found


def secret_values(config) -> dict:
    """Secret value -> the setting it belongs to. Only values worth searching for."""
    env = config.file_env()
    names = set(config["auth"]["env"]) | {name for name in env if SECRET_NAME.search(name)}
    values = {}
    for name in sorted(names):
        value = env.get(name) or os.environ.get(name) or ""
        if len(value) >= 6 and not PLACEHOLDER.match(value) and not re.match(r"^https?://[^@]*$", value):
            values[value] = name
    return values


def check_secrets(repo: Path, files: list, values: dict, label: str, private=()) -> list:
    found = []
    for rel in files:
        if match_any(rel, SKIP_SCAN) or rel in private:
            continue
        text = _text(repo / rel)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if ALLOW.search(line) and "allow-secret" in line:
                continue
            for value, name in values.items():
                if value in line:
                    found.append(_violation(
                        "secrets", f"{label}{rel}:{number}", f"contains the value of {name}",
                        f"read it from the environment ({name}) instead of writing it in the file",
                    ))
            for what, shape in TOKEN_SHAPES:
                if shape.search(line):
                    found.append(_violation(
                        "secrets", f"{label}{rel}:{number}", f"looks like {what}",
                        "remove it; if it is a made-up sample, say so on the line: `qa-agent: allow-secret <why>`",
                    ))
    return found


def _allowed(lines: list, index: int, kind: str, before: int = 1, after: int = 0) -> bool:
    for line in lines[max(0, index - before) : index + after + 1]:
        match = ALLOW.search(line)
        if match and match.group(1) == kind:
            return True
    return False


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(("#", "//", "*", "/*"))


def _statement(lines: list, index: int) -> list:
    """The lines of the statement starting at `index`, with the comment lines directly above it."""
    window = []
    above = index - 1
    while above >= 0 and index - above <= 3 and _is_comment(lines[above]):
        window.insert(0, lines[above])
        above -= 1
    depth = 0
    for line in lines[index : index + 6]:
        window.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            break
    return window


def check_hygiene(repo: Path, files: list, statuses: dict, label: str) -> list:
    """Focused tests, fixed waits, tests switched off without a recorded reason, tests that assert nothing."""
    found = []
    for rel in files:
        if testscan.language(rel) is None or match_any(rel, SKIP_SCAN):
            continue
        text = _text(repo / rel)
        if text is None:
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if _is_comment(line):
                continue
            where = f"{label}{rel}:{index + 1}"
            if FOCUSED.search(line):
                found.append(_violation(
                    "hygiene", where, "a focused test (.only) would hide every other test",
                    "remove the focus",
                ))
            if SLEEP.search(line) and not _allowed(lines, index, "sleep"):
                found.append(_violation(
                    "hygiene", where, "a fixed wait",
                    "wait for the condition instead; inside a polling helper with a deadline, mark the line "
                    "`qa-agent: allow-sleep <why>`",
                ))
            statement = _statement(lines, index) if SKIP.search(line) else None
            if statement and not _allowed(statement, 0, "skip", before=0, after=len(statement)):
                ids = BEHAVIOUR_ID.findall("\n".join(statement))
                if not ids:
                    found.append(_violation(
                        "hygiene", where, "a test is switched off or expected to fail without a behaviour id",
                        "put the behaviour id (B-0000) in the reason and record its status with `qa.py catalog status`; "
                        "for a skip that depends on the environment, mark it `qa-agent: allow-skip <why>`",
                    ))
                elif not any(statuses.get(i) in SKIP_STATUSES for i in ids):
                    shown = ", ".join(f"{i} is {statuses.get(i, 'not in the catalog')}" for i in ids)
                    found.append(_violation(
                        "hygiene", where, f"a test is switched off, but {shown}",
                        "record why with `qa.py catalog status <id> defect|quarantined|blocked --note …`, or remove the marker",
                    ))
        if testscan.is_test_file(rel):
            for test in testscan.scan_file(repo / rel, rel):
                if test["assertions"] or test["skipped"]:
                    continue  # a switched-off test already needs a recorded reason
                if _allowed(lines, test["line"] - 1, "no-assert", before=1, after=test["end_line"] - test["line"]):
                    continue
                found.append(_violation(
                    "hygiene", f"{label}{rel}:{test['line']}", f"test '{test['name']}' asserts nothing",
                    "assert the expected result; if a helper it calls does the asserting, mark the test "
                    "`qa-agent: allow-no-assert <which helper>`",
                ))
    return found


def check_retries(repo: Path, files: list, label: str) -> list:
    """Retry settings and markers: they turn a flaky test into a passing one in the report."""
    found = []
    fix = (
        "remove it or set it to 0: `qa.py run` re-runs failures itself and records them as flaky. Where the team's "
        "pipeline needs the setting, say so on the line: `qa-agent: allow-retry <why>`"
    )
    for rel in files:
        settings, code = match_any(rel, RUNNER_SETTINGS), testscan.language(rel) is not None
        if not (settings or code) or match_any(rel, SKIP_SCAN):
            continue
        text = _text(repo / rel)
        if text is None:
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if _is_comment(line) or _allowed(lines, index, "retry"):
                continue
            if (settings and RETRY_IN_SETTINGS.search(line)) or (code and RETRY_IN_TESTS.search(line)):
                found.append(_violation("hygiene", f"{label}{rel}:{index + 1}", "the test runner is told to retry", fix))
    return found


def check_suite_commands(config) -> list:
    found = []
    for suite in config.suites:
        for key in ("command", "rerun_failed"):
            if RETRY_IN_SETTINGS.search(suite.get(key) or ""):
                found.append(_violation(
                    "hygiene", f".qa-agent/project.json suites[{suite['name']}].{key}", "the test runner is told to retry",
                    "remove the option: `qa.py run` re-runs failures itself and records them as flaky",
                ))
    return found


# ---------------------------------------------------------------- what the person left stays as it was


def _watched(paths, config) -> list:
    """(name, folder, which paths count) for every place whose files must stay as the person left them.

    A service's checkout counts whole. This repository counts where it is an application's own: everything in it
    outside the places tests may be written.
    """
    rows = []
    if config.host_services():
        allowed, private = _allowed_here(paths, config), _private(config)
        rows.append((HOST, paths.root, lambda path: not match_any(path, allowed) and not match_any(path, private)))
    seen = set()
    for service in config.services:
        checkout = config.repo_path(service)
        if config.is_host(service) or checkout is None or checkout in seen or not is_git_repo(checkout):
            continue
        seen.add(checkout)
        rows.append((service["name"], checkout, None))
    return rows


def _checkout_state(checkout: Path, keep=None) -> dict:
    entries = [(code, path) for code, path in uncommitted(checkout) if keep is None or keep(path)]
    state = {}
    hashed = len(entries) <= 400
    for code, path in entries:
        target = checkout / path
        if "D" in code or not target.exists():
            state[path] = "deleted"
        elif hashed and target.is_file():
            try:
                state[path] = sha256_bytes(target.read_bytes()) if target.stat().st_size <= MAX_BYTES * 5 else "large"
            except OSError:
                state[path] = "unreadable"
        else:
            state[path] = "present"
    return state


def snapshot_checkouts(paths, config, only=None) -> dict:
    """Remember the uncommitted state of the watched places, to notice a write made after this moment.

    With `only`, the named places are recorded anew and the others keep what was recorded for them.
    """
    stored = ((read_json(paths.checkouts) or {}).get("services") or {}) if only is not None else {}
    services = {}
    for name, folder, keep in _watched(paths, config):
        if name in stored and name not in only:
            services[name] = stored[name]
        else:
            services[name] = {"at": now_iso(), "head": git_head(folder), "files": _checkout_state(folder, keep)}
    data = {"version": 2, "services": services}
    paths.runs.mkdir(parents=True, exist_ok=True)
    write_json(paths.checkouts, data)
    return data


def _ago(stamp) -> str:
    try:
        then = datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "at an unknown time"
    minutes = int((now() - then).total_seconds() // 60)
    if minutes < 1:
        return "under a minute ago"
    if minutes < 120:
        return f"{minutes} minute(s) ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} hour(s) ago"
    return f"{minutes // (24 * 60)} day(s) ago"


def recorded_when(stamp) -> str:
    return f"{stamp}, {_ago(stamp)}"


def checkout_changes(paths, config):
    """What differs from the recorded state. Nothing is written.

    Returns (the record or None, changed files, places on another commit or not recorded, checkouts on a QA branch):
    changed rows are (name, folder, path, recorded at); moved rows (name, folder, commit then, commit now, recorded
    at); switched rows (name, folder, branch).
    """
    stored = read_json(paths.checkouts)
    if not stored:
        return None, [], [], []
    changed, moved, switched = [], [], []
    branch_prefix = config["git"]["branch_prefix"]
    for name, folder, keep in _watched(paths, config):
        before = (stored.get("services") or {}).get(name)
        head = git_head(folder)
        if before is None:
            moved.append((name, folder, None, head, None))
            continue
        if name != HOST:  # this repository's own commit moves with every piece of QA work
            branch = git_branch(folder)
            if branch_prefix and branch and branch.startswith(branch_prefix):
                switched.append((name, folder, branch))
                continue
            if before.get("head") != head:
                moved.append((name, folder, before.get("head"), head, before.get("at")))
                continue
        state = _checkout_state(folder, keep)
        for path in sorted(set(state) | set(before["files"])):
            if state.get(path) != before["files"].get(path):
                changed.append((name, folder, path, before.get("at")))
    return stored, changed, moved, switched


def check_checkouts(paths, config):
    """Violations for files that changed, since their state was recorded, where the agent must write nothing."""
    stored, changed, moved, switched = checkout_changes(paths, config)
    if stored is None:
        snapshot_checkouts(paths, config)
        return [], [
            "the state of the files outside the tests was not recorded yet; it is now, and later changes are compared with it"
        ]
    found, warnings = [], []
    for name, folder, branch in switched:
        found.append(_violation(
            "scope", paths.rel(folder), f"the checkout of '{name}' is on the QA branch '{branch}'",
            "a service's checkout is never switched or committed to: tests for a service are written in its QA "
            "worktree (`qa.py worktree open`). Tell the person; putting the checkout back on its branch is theirs to do.",
        ))
    for name, folder, path, at in changed:
        if name == HOST:
            where, what = path, "a file outside the places tests may be written"
        else:
            where, what = f"{paths.rel(folder)}/{path}", "a file in a service's checkout"
        found.append(_violation(
            "scope", where, f"{what} changed since its state was recorded ({recorded_when(at)})", CHANGED_FIX,
        ))
    for name, folder, then, head, at in moved:
        if then is None or name == HOST:
            continue  # a place added to the project map: recorded below, nothing to compare it with
        new = int(git(folder, "rev-list", "--count", f"{then}..{head}", check=False) or 0) if head else 0
        newest = clip(git(folder, "log", "-1", "--format=%s", check=False), 70) if head else ""
        warnings.append(
            f"{name}: the checkout went from {then[:12]} to {(head or 'no commit')[:12]} since its state was recorded "
            f"({recorded_when(at)}): {new} new commit(s), the newest \"{newest}\". Its state was recorded again. "
            "Committing there is the person's to do: if one of these commits is yours, stop and tell them."
        )
    if moved:
        snapshot_checkouts(paths, config, only=[row[0] for row in moved])
    return found, warnings


# ---------------------------------------------------------------- the whole check


def run(paths, config, since=None, everything=False, accept_checkouts=False):
    """Returns (result, exit code). Exit code 1 means: do not commit."""
    root = paths.root
    if not is_git_repo(root):
        raise QAError("This folder is not a git repository. Run `git init` here first.")
    catalog = Catalog(paths, config)
    statuses = {entry["id"]: entry["status"] for entry in catalog.entries}
    values = secret_values(config)
    private = _private(config)
    violations, warnings, checked = [], [], {}

    # This repository.
    if since and not git_ok(root, "rev-parse", "--verify", "-q", f"{since}^{{commit}}"):
        raise QAError(f"--since: '{since}' is not a commit here")
    hosts = config.host_services()
    base, base_name = since, since
    merged = next((service["branch"] for service in hosts if service.get("branch")), None)
    if base is None and merged and git_branch(root) != merged and git_ok(root, "rev-parse", "--verify", "-q", merged):
        base, base_name = git(root, "merge-base", "HEAD", merged, check=False) or None, merged
    allowed = _allowed_here(paths, config) if hosts else None
    files = changed_files(root, base=base)
    if everything:
        files = sorted(set(files) | set(changed_files(root, everything=True)))  # every file, changed or not
    if hosts:
        # An application's own repository: only the places tests are written in are read. The application's files
        # are not the agent's to judge; that they were left alone is checked separately, below.
        files = [path for path in files if match_any(path, allowed)]
    checked["."] = len(files)
    violations += check_branch(config, root, ".")
    violations += check_kit(paths)
    violations += check_ignored(paths, config)
    if hosts and base:
        # What this branch commits. Uncommitted changes are compared with the state recorded earlier, below.
        violations += check_scope(
            [path for path in committed_since(root, base, removed=True) if not match_any(path, private)], allowed, "",
            "outside the places tests may be written in this repository, and committed on this branch",
            f"commit the file as it is on {base_name} again (`git show {base_name}:<path>` prints it); history is "
            "not rewritten. Application code is never edited, and a place for tests is added to 'writable' only "
            "when the person agrees to it",
        )
    violations += check_secrets(root, files, values, "", private)
    violations += check_hygiene(root, files, statuses, "")
    violations += check_retries(root, files, "")
    violations += check_suite_commands(config)

    # Tests written inside other services' repositories.
    for service in config.services:
        target = paths.worktrees / service["name"]
        if config.is_host(service) or not (target / ".git").exists():
            continue
        label = paths.rel(target) + "/"
        checkout = config.repo_path(service)
        fork = git(target, "merge-base", "HEAD", git(checkout, "rev-parse", "HEAD"), check=False) or None
        tree_files = changed_files(target, base=fork)
        checked[paths.rel(target)] = len(tree_files)
        violations += check_branch(config, target, label.rstrip("/"))
        violations += check_scope(
            touched_paths(target, fork), service["writable"], label,
            f"outside the places tests may be written in '{service['name']}'",
            "undo the change in the worktree, which is your own working copy: only test files and test support go "
            "into a service's repository",
        )
        violations += check_secrets(target, tree_files, values, label)
        violations += check_hygiene(target, tree_files, statuses, label)
        violations += check_retries(target, tree_files, label)

    if accept_checkouts:
        snapshot_checkouts(paths, config)
        warnings.append("the files outside the tests were recorded as they are now, as the person's own work")
    found, notes = check_checkouts(paths, config)  # after an acceptance, what is left is what no note can accept
    violations += found
    warnings += notes

    for field, was, current in consent_changes(paths, config):
        violations.append(_violation(
            "consent", ".qa-agent/project.json",
            f"'{field}' is {json.dumps(current)}; the person agreed to {json.dumps(was)}",
            "put the agreed value back. If the person has since agreed to the new one, record it: "
            "`qa.py config agree --note \"<what they agreed to>\"`",
        ))
    if agreed(paths) is None and catalog.entries:
        warnings.append("no agreement with the person is recorded: `qa.py config agree --note …` (setup, step 6)")

    report = catalog.validate()
    violations += [
        _violation("catalog", ".qa-agent/state/catalog.json", error, "fix the entry with `qa.py catalog …`") for error in report["errors"]
    ]
    warnings += report["warnings"]

    result = {"ok": not violations, "files_checked": checked, "violations": violations[:SHOWN]}
    if len(violations) > SHOWN:
        result["violations_not_shown"] = len(violations) - SHOWN
    if warnings:
        result["warnings"] = warnings[:SHOWN]
    if violations:
        result["next"] = "fix every violation, run the guard again, and commit only when it passes"
    return result, (1 if violations else 0)
