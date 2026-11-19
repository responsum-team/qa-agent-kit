#!/usr/bin/env python3
"""Install or update the QA agent kit in a repository.

    python3 install.py /path/to/repository [--also FILE ...]

Copies the kit to <repository>/.qa-agent/kit/, records a hash of every file,
and adds a short pointer for coding assistants to AGENTS.md and CLAUDE.md.
Running it again updates the kit; the project map, the state, the installed
team rules, a pasted copy of the team's library and the reports are never touched.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "kit"))

from qa_agent import __version__  # noqa: E402
from qa_agent.ignore import keep_local, put_block  # noqa: E402
from qa_agent.util import QAError, emit, git_head, is_git_repo, now_iso, read_json, sha256_bytes, write_json  # noqa: E402

START, END = "<!-- qa-agent:start -->", "<!-- qa-agent:end -->"
POINTER = f"""{START}
## QA agent

This repository carries an automated QA agent kit in `.qa-agent/`. It is for one kind of request: the person asks
for the QA agent's work (set it up, build the baseline of tests, bring the tests up to date with merged code, heal
failing or flaky tests, review the tests, report). Then:

1. Run `python3 .qa-agent/kit/qa.py status` and read what it prints.
2. Read `.qa-agent/kit/MANUAL.md` in full and follow it. It names the playbook for the request and the limits that always hold.

Until you have done both, open nothing outside this repository: `status` lists folders that must never be read.

Any other work, including writing a test as part of a code change, is not the QA agent's: do it as you normally
would and leave `.qa-agent/` alone. Whatever the request, never edit anything under `.qa-agent/kit/`.
{END}"""

SKIP = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def payload(source: Path) -> list:
    files = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_file() and not (set(rel.parts) & SKIP) and path.suffix != ".pyc":
            files.append(rel.as_posix())
    return files


def _qa_repo(target: Path) -> bool:
    """True when the project map says this repository holds only tests. Unknown before setup: then False."""
    try:
        data = read_json(target / ".qa-agent" / "project.json")
    except QAError:
        return False
    return isinstance(data, dict) and data.get("placement") == "qa-repo"


def install(target: Path, also=(), pointers: bool = True) -> dict:
    target = target.expanduser().resolve()
    source = HERE / "kit"
    if not target.is_dir():
        raise QAError(f"{target} is not a folder. Create the repository first (mkdir, git init).")
    if target == HERE or HERE in target.parents:
        raise QAError("Install the kit into the repository that will hold the tests, not into the kit itself.")
    for name in also:
        if not (target / name).resolve().is_relative_to(target):
            raise QAError(f"--also {name}: must be a file inside the repository")

    destination = target / ".qa-agent" / "kit"
    lock_path = target / ".qa-agent" / "kit.lock.json"
    before = (read_json(lock_path, {}) or {}).get("kit_version")
    if destination.exists():
        shutil.rmtree(destination)
    files = payload(source)
    hashes = {}
    for rel in files:
        data = (source / rel).read_bytes()
        out = destination / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        hashes[rel] = sha256_bytes(data)
    (destination / "qa.py").chmod(0o755)

    earlier = (read_json(lock_path, {}) or {}).get("pointers") or []
    named = list(dict.fromkeys((["AGENTS.md", "CLAUDE.md", *also] if pointers else []) + [
        name for name in earlier if (target / name).is_file() and START in (target / name).read_text(encoding="utf-8")
    ]))
    touched = {name: put_block(target / name, POINTER, START, END) for name in named}
    write_json(lock_path, {
        "version": 1,
        "kit_version": __version__,
        "installed_at": now_iso(),
        "source": {"path": str(HERE), "commit": git_head(HERE) if is_git_repo(HERE) else None},
        "pointers": named,  # the guard lets the QA branch carry these files in an application's own repository
        "files": hashes,
    })
    # Before setup the repository's own .gitignore is left alone: it may be an application's. `qa.py init` writes it.
    ignored = keep_local(target, qa_repo=_qa_repo(target))
    # Where the person may paste a copy of the team's rule library. Git ignores it; what it holds is never touched.
    (target / ".qa-agent" / "library-source").mkdir(exist_ok=True)

    configured = (target / ".qa-agent" / "project.json").exists()
    result = {
        "installed": str(destination),
        "kit_version": __version__,
        "updated_from": before,
        "files": len(files),
        "pointers": touched,
        "ignore_lines": ignored,
        "git_repository": is_git_repo(target),
        "next": (
            "The project map is kept. Open a coding assistant in the repository and say: continue the QA work."
            if configured else
            "Open a coding assistant (Cursor, Claude Code, Codex, Grok, …) in the repository and say: set up the QA agent."
        ),
    }
    if not (target / ".qa-agent" / "library" / "lock.json").exists():
        result["team_rules"] = (
            "To have the agent follow your team's rules and skills, paste a copy of the team's library into "
            ".qa-agent/library-source/ before setup, or give setup its path."
        )
    if not result["git_repository"]:
        result["note"] = "this folder is not a git repository yet; the setup playbook starts one"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install or update the QA agent kit in a repository.")
    parser.add_argument("target", help="the repository that will hold the tests")
    parser.add_argument("--also", nargs="+", default=[], metavar="FILE",
                        help="more instruction files to carry the pointer, e.g. GEMINI.md .github/copilot-instructions.md")
    parser.add_argument("--no-pointers", action="store_true", help="do not touch AGENTS.md or CLAUDE.md")
    args = parser.parse_args(argv)
    try:
        emit(install(Path(args.target), also=args.also, pointers=not args.no_pointers))
    except QAError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
