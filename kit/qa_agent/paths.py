"""Where things live inside a repository the kit is installed in."""

from __future__ import annotations

import os
from pathlib import Path

from .util import QAError

KIT_DIR = Path(__file__).resolve().parents[1]
QA_DIR = ".qa-agent"


def find_root(explicit=None) -> Path:
    """The host repository: --root, then $QA_AGENT_ROOT, then where this kit is installed."""
    chosen = explicit or os.environ.get("QA_AGENT_ROOT")
    if chosen:
        root = Path(chosen).expanduser().resolve()
        if not root.is_dir():
            raise QAError(f"{root} is not a folder")
        return root
    if KIT_DIR.parent.name == QA_DIR:
        return KIT_DIR.parent.parent
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        if (folder / QA_DIR).is_dir():
            return folder
    raise QAError(
        "No .qa-agent folder found. Run this inside a repository the kit is installed in, "
        "or pass --root PATH."
    )


class Paths:
    def __init__(self, root):
        self.root = Path(root)
        self.qa = self.root / QA_DIR
        installed = self.qa / "kit"
        self.kit = installed if installed.is_dir() else KIT_DIR
        self.kit_lock = self.qa / "kit.lock.json"
        self.config = self.qa / "project.json"
        self.state = self.qa / "state"
        self.catalog = self.state / "catalog.json"
        self.ledger = self.state / "ledger.json"
        self.sync = self.state / "sync.json"
        self.progress = self.state / "progress.json"
        self.consent = self.state / "consent.json"
        self.surface = self.state / "surface"
        self.library = self.qa / "library"
        self.library_lock = self.library / "lock.json"
        self.reports = self.qa / "reports"
        # Not committed: a copy of the team's library the person pasted in, to install from.
        self.library_source = self.qa / "library-source"
        # Not committed: raw run output and worktrees of other repositories.
        self.runs = self.qa / "runs"
        self.run_lock = self.runs / "run.lock"
        self.checkouts = self.runs / "checkouts.json"
        self.worktrees = self.qa / "worktrees"

    def rel(self, path) -> str:
        """A path as the agent should cite it: relative to the host root when inside or beside it."""
        target, root = Path(path).resolve(), self.root.resolve()
        try:
            return target.relative_to(root).as_posix()
        except ValueError:
            pass
        try:
            beside = Path(os.path.relpath(target, root)).as_posix()
        except ValueError:  # another drive
            return str(target)
        return beside if beside.count("../") <= 2 else str(target)
