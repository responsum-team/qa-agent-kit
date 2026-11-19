"""Is everything the agent depends on in place: the project map, the checkouts, the running services."""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

from . import guard, library
from .config import Config, agreed, consent_changes
from .runner import suite_base
from .util import QAError, git, git_branch, git_head, git_ok, http_get, is_git_repo, join_url


def _row(check: str, ok, detail: str, fix: str = "") -> dict:
    row = {"check": check, "ok": ok, "detail": detail}
    if fix and ok is not True:
        row["fix"] = fix
    return row


def _program(command: str):
    """The program a shell command starts with, skipping VAR=value settings."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    for word in words:
        if "=" in word and not word.startswith(("-", "/", ".")) and word.split("=", 1)[0].isidentifier():
            continue
        return word
    return None


def run(paths):
    """Returns (result, exit code). `ok` is False when something stops the agent from working."""
    rows = []
    version = sys.version_info
    rows.append(_row(
        "python", version >= (3, 9), f"{version.major}.{version.minor}.{version.micro}",
        "the helper needs Python 3.9 or newer",
    ))
    rows.append(_row("git", shutil.which("git") is not None, "found" if shutil.which("git") else "not on PATH", "install git"))
    repo = is_git_repo(paths.root)
    top = Path(git(paths.root, "rev-parse", "--show-toplevel", check=False) or paths.root).resolve() if repo else None
    if repo and top != paths.root.resolve():
        rows.append(_row(
            "repository", False, f"{paths.root} is a folder inside the repository {top}",
            "install the kit at the top of the repository: the helper reads git's paths from there",
        ))
    else:
        rows.append(_row("repository", repo, str(paths.root), "run `git init` in this folder"))

    try:
        config = Config.load(paths)
    except QAError as exc:
        rows.append(_row("project map", False, str(exc), "follow .qa-agent/kit/playbooks/setup.md"))
        return {"ok": False, "checks": rows}, 1
    rows.append(_row("project map", True, f"{config.name}: {len(config.services)} service(s), {len(config.suites)} suite(s)"))

    edited = guard.check_kit(paths)
    if paths.kit_lock.exists():
        rows.append(_row(
            "kit", not edited, "unchanged since install" if not edited else f"{len(edited)} file(s) differ from the installed kit",
            "re-run the kit's install.py",
        ))

    env_file = config.env_file()
    if env_file.exists():
        ignored = repo and git_ok(paths.root, "check-ignore", "-q", config["environment"]["env_file"])
        rows.append(_row(
            "env file", bool(ignored), f"{config['environment']['env_file']} exists" + ("" if ignored else ", but is not git-ignored"),
            "list it in a .gitignore; the kit's own lines cover .env in a QA repository and .qa-agent/.env in an application's",
        ))
    else:
        rows.append(_row("env file", None, f"{config['environment']['env_file']} does not exist; settings come from the process environment"))

    for service in config.services:
        name = service["name"]
        checkout = config.repo_path(service)
        if checkout is None:
            rows.append(_row(f"{name}: code", None, "no checkout in the project map: tests are derived from the running service only"))
        elif not is_git_repo(checkout):
            rows.append(_row(f"{name}: code", False, f"{checkout} is not a git checkout", "correct services[].repo in the project map"))
        else:
            branch = git_branch(checkout)
            head = (git_head(checkout) or "no commits")[:12]
            expected = service.get("branch")
            same = not expected or branch == expected or config.is_host(service)
            detail = f"{paths.rel(checkout) or '.'} on {branch or 'a detached commit'} at {head}"
            if not same:
                detail += f" (merged work is expected on {expected})"
            if not config.is_host(service):
                unsaved = len(guard.uncommitted(checkout))
                if unsaved:
                    # Worth knowing before reading the code: the running service may differ from the last commit.
                    detail += f"; {unsaved} uncommitted file(s), the person's work in progress"
            rows.append(_row(f"{name}: code", True if same else None, detail))
        url = config.url(service)
        if not url:
            rows.append(_row(f"{name}: running", None, "no URL in the project map"))
            continue
        target = join_url(url, service.get("health") or "")
        status, _, error = http_get(target)
        answering = status is not None and status < 500
        rows.append(_row(
            f"{name}: running", answering, f"{target} -> {'HTTP ' + str(status) if status is not None else error}",
            "the person starts the service (the agent never starts or stops one), or corrects its URL in the env file",
        ))
        spec = service.get("openapi")
        if spec and spec.startswith("/") and answering:
            status, _, error = http_get(join_url(url, spec), timeout=15)
            rows.append(_row(
                f"{name}: openapi", status == 200, f"{spec} -> {'HTTP ' + str(status) if status is not None else error}",
                "correct services[].openapi, or set it to null when the service publishes no document",
            ))

    missing = [name for name in config["auth"]["env"] if not config.env_value(name)]
    if config["auth"]["env"]:
        rows.append(_row(
            "sign-in settings", not missing,
            "all set" if not missing else "not set: " + ", ".join(missing),
            f"the person puts them in {config['environment']['env_file']} (values are never written anywhere else)",
        ))

    for suite in config.suites:
        program = _program(suite["command"])
        check = f"suite {suite['name']}"
        try:
            base = suite_base(paths, config, suite)
        except QAError:
            # Not a fault: a worktree is opened when work in that repository begins, and closed after a merge.
            rows.append(_row(
                check, None, f"its tests live in the repository of '{suite['repo']}', whose QA worktree is not open",
                f"`qa.py worktree open --service {suite['repo']}` before running it",
            ))
            continue
        folder = base / suite["workdir"]
        if not folder.is_dir():
            rows.append(_row(
                check, False, f"{paths.rel(folder)} does not exist", "create the folder, or correct suites[].workdir",
            ))
            continue
        found = program is None or shutil.which(program) is not None or (folder / program).exists()
        if found:
            rows.append(_row(check, True, f"runs in {paths.rel(folder) or '.'}"))
        elif suite["setup"]:
            rows.append(_row(
                check, None, f"runs in {paths.rel(folder) or '.'}; '{program}' is not on PATH yet",
                f"`qa.py run {suite['name']} --setup` runs the suite's setup commands, which install what it needs",
            ))
        else:
            rows.append(_row(
                check, False, f"runs in {paths.rel(folder) or '.'}; '{program}' is not on PATH",
                "install the test runner, and list the commands that do it under suites[].setup",
            ))

    state = library.status(paths)
    if state.get("installed"):
        fine = state.get("up_to_date", True) and state.get("library_reachable", True)
        detail = f"{state['installed']} item(s) installed"
        if state.get("library_reachable") is False:
            detail += "; the library folder is not reachable, so changes there cannot be seen"
        elif not state.get("up_to_date", True):
            changed = len(state.get("changed_in_library", [])) + len(state.get("removed_from_library", []))
            detail += f"; {changed} changed in the library since install"
        rows.append(_row("team library", True if fine else None, detail, "run `qa.py library install --refresh`"))
    elif state.get("pasted_copy"):
        rows.append(_row(
            "team library", None, f"none installed; a copy of a library is pasted at {state['pasted_copy']}",
            "`qa.py library scan`, then `qa.py library install` (setup, step 5)",
        ))
    else:
        rows.append(_row("team library", None, "none installed; the kit's own methods apply"))

    if config["off_limits"]:
        rows.append(_row("off limits", True, "never read: " + ", ".join(config["off_limits"])))
    if agreed(paths):
        differences = [field for field, _, _ in consent_changes(paths, config)]
        rows.append(_row(
            "agreed limits", not differences,
            "as the person agreed" if not differences else "changed since the person agreed: " + ", ".join(differences),
            "`qa.py config agreed` shows the differences: put the agreed values back, or record the new agreement",
        ))

    ok = all(row["ok"] is not False for row in rows)
    return {"ok": ok, "checks": rows}, (0 if ok else 1)
