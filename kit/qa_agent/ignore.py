"""What must stay on this machine only: the ignore lines the kit keeps in place.

`.qa-agent/.gitignore` travels with the QA folder and covers what is inside it. The clone's own exclude file
(`.git/info/exclude`) says the same on every branch, including those that do not have the QA folder yet, where the
env file and the raw run output would otherwise show up as new files to commit. The repository's own `.gitignore`
is written only in a QA repository: in an application's own repository that file is the application's.
"""

from __future__ import annotations

import re
from pathlib import Path

from .util import git_try, is_git_repo

START, END = "# qa-agent:start", "# qa-agent:end"
INSIDE = ["runs/", "worktrees/", "library-source/", ".env", ".env.*", "!.env.example", ".auth/"]
ROOT = [".env", ".env.*", "!.env.example", ".auth/", "__pycache__/", ".venv/", "node_modules/"]
LOCAL = [("!.qa-agent/" + line[1:]) if line.startswith("!") else ".qa-agent/" + line for line in INSIDE] + [
    "__pycache__/", ".venv/", "node_modules/",
]


def put_block(path: Path, block: str, start: str, end: str) -> str:
    """Insert or replace a marked block. Returns what happened: created, updated, added, unchanged."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block + "\n", encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pattern.search(text):
        updated = pattern.sub(lambda _: block, text, count=1)
        if updated == text:
            return "unchanged"
        path.write_text(updated, encoding="utf-8")
        return "updated"
    joiner = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + joiner + block + "\n", encoding="utf-8")
    return "added"


def _block(lines: list) -> str:
    return "\n".join([START, *lines, END])


def exclude_file(root):
    """The clone's own exclude file, or None when the folder is not a git repository."""
    if not is_git_repo(root):
        return None
    found, rel = git_try(root, "rev-parse", "--git-path", "info/exclude")
    if not found or not rel.strip():
        return None
    path = Path(rel.strip())
    return path if path.is_absolute() else Path(root) / path


def keep_local(root, qa_repo: bool = False) -> dict:
    """Write the ignore lines. `qa_repo`: the repository is the tests' own, so its `.gitignore` is written too."""
    root = Path(root)
    done = {".qa-agent/.gitignore": put_block(root / ".qa-agent" / ".gitignore", _block(INSIDE), START, END)}
    if qa_repo:
        done[".gitignore"] = put_block(root / ".gitignore", _block(ROOT), START, END)
    target = exclude_file(root)
    if target is not None:
        try:
            done[".git/info/exclude"] = put_block(target, _block(LOCAL + (ROOT[:4] if qa_repo else [])), START, END)
        except OSError as exc:  # a read-only git folder is no reason to stop
            done[".git/info/exclude"] = f"not written: {exc}"
    return done
