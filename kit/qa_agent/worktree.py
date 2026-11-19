"""Branches the agent works on.

In this repository, work goes on a QA branch the person merges when satisfied.
Tests that live inside another service's repository are written in a git
worktree of it, on a QA branch of that repository: the checkout the service
runs from is never switched, edited or updated.
"""

from __future__ import annotations

import re

from .ignore import keep_local
from .util import QAError, git, git_branch, git_dirty, git_head, git_ok, git_try, is_git_repo, today


def prefix(config) -> str:
    return config["git"]["branch_prefix"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-") or "work"


def _branch_exists(repo, name: str) -> bool:
    return git_ok(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{name}")


def _count(repo, spec: str) -> int:
    return int(git(repo, "rev-list", "--count", spec, check=False) or 0)


def _qa_branches(repo, branch_prefix: str) -> list:
    out = git(
        repo, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
        f"refs/heads/{branch_prefix}*", check=False,
    )
    return [line for line in out.splitlines() if line.strip()]


def _committed_at(repo, *spec) -> int:
    """When the newest commit the arguments select was made, in seconds; 0 when there is none."""
    out = git(repo, "log", "-1", "--format=%ct", *spec, check=False).strip()
    return int(out) if out.isdigit() else 0


def last_commit_day(repo, name: str) -> str:
    return git(repo, "log", "-1", "--date=short", "--format=%cd", name, check=False).strip() or "unknown"


def merged_into_head(repo, name: str) -> bool:
    """True when what is checked out already holds what the named branch would bring."""
    if git_ok(repo, "merge-base", "--is-ancestor", name, "HEAD"):
        return True
    # Merged as one squashed commit, or rebased: merging it now would change nothing.
    # `merge-tree --write-tree` came with git 2.38; an older git fails here, which reads as "not merged".
    clean, out = git_try(repo, "merge-tree", "--write-tree", "HEAD", name)
    return clean and out.splitlines()[:1] == [git(repo, "rev-parse", "HEAD^{tree}", check=False).strip()]


def waiting_branch(repo, branch_prefix: str, records=None):
    """The QA branch whose work is not in what is checked out, or None.

    Only the newest QA branch can be it: a later one is begun only once the earlier ones were merged or given up.
    `records` is a path every piece of QA work commits to (this repository's state folder). When what is checked
    out has a newer commit there than the branch's last commit, the branch was merged in another form, however
    the two histories look.
    """
    names = _qa_branches(repo, branch_prefix) if branch_prefix else []
    if not names or names[0] == git_branch(repo) or merged_into_head(repo, names[0]):
        return None
    if records and _committed_at(repo, names[0]) < _committed_at(repo, "HEAD", "--", records):
        return None
    return names[0]


# ---------------------------------------------------------------- this repository


def ensure_host_branch(paths, config, label: str, fresh: bool = False) -> dict:
    """Put this repository on a QA branch: the one whose work waits to be merged, or a new one from here."""
    result = _host_branch(paths, config, label, fresh)
    # Once the branch is settled: the env file and the raw output stay ignored, on this branch and on every other.
    keep_local(paths.root, qa_repo=not config.host_services())
    return result


def _host_branch(paths, config, label: str, fresh: bool) -> dict:
    root = paths.root
    if not is_git_repo(root):
        raise QAError("This folder is not a git repository. Run `git init` here first.")
    branch_prefix = prefix(config)
    current = git_branch(root)
    if not branch_prefix:
        return {"branch": current, "created": False, "note": "git.branch_prefix is empty: work is committed on the current branch"}
    if git_head(root) is None:
        raise QAError(
            "This repository has no commit yet, so there is nothing to branch from. "
            "Commit the installed kit and the project map first, then run this again."
        )
    if current and current.startswith(branch_prefix):
        return {"branch": current, "created": False}
    here = current or git_head(root)[:12]
    waiting = waiting_branch(root, branch_prefix, paths.rel(paths.state))
    if waiting and not fresh:
        # A new branch from here would start without the tests and records that are on the waiting one.
        new_start = f"`git checkout {here}`, then `qa.py branch --name {label} --fresh`"
        if config.host_services() and not git_ok(root, "merge-base", "--is-ancestor", "HEAD", waiting):
            raise QAError(
                f"QA branch '{waiting}' (last commit {last_commit_day(root, waiting)}) holds tests and records that "
                f"were never merged into '{here}', so they are not in what is checked out. This repository is the "
                "application's own, and switching to that branch would put older application code in place, so "
                f"nothing was switched. Tell the person: either they merge '{waiting}' first, or they confirm a new "
                f"start from '{here}', which is `qa.py branch --name {label} --fresh`."
            )
        git(root, "checkout", "--quiet", waiting)
        return {
            "branch": waiting, "created": False, "continued": True, "from": current,
            "note": (
                f"the QA work on '{waiting}' was never merged into '{here}', so it is continued: its tests and "
                f"records are the ones to build on. Only when the person asked for a new start from '{here}': {new_start}."
            ),
        }
    name = f"{branch_prefix}{_slug(label)}-{today()}"
    if _branch_exists(root, name):
        git(root, "checkout", "--quiet", name)
        created = False
    else:
        git(root, "checkout", "--quiet", "-b", name)
        created = True
    result = {"branch": name, "created": created, "from": current}
    if waiting:
        result["left_unmerged"] = waiting
    return result


# ---------------------------------------------------------------- service worktrees


def _checkout(config, service: dict):
    if config.is_host(service):
        raise QAError(f"'{service['name']}' is this repository: its tests are written here, no worktree is needed")
    checkout = config.repo_path(service)
    if checkout is None or not is_git_repo(checkout):
        raise QAError(f"Service '{service['name']}' has no git checkout in the project map")
    if git_head(checkout) is None:
        raise QAError(f"{checkout} has no commits")
    return checkout


def _is_open(target) -> bool:
    return target.is_dir() and (target / ".git").exists()


def describe(paths, config, service: dict) -> dict:
    checkout = _checkout(config, service)
    target = paths.worktrees / service["name"]
    if not _is_open(target):
        return {"service": service["name"], "open": False}
    head = git(checkout, "rev-parse", "HEAD")
    dirty = [line[3:] for line in git(target, "status", "--porcelain", check=False).splitlines() if line.strip()]
    return {
        "service": service["name"],
        "open": True,
        "path": paths.rel(target),
        "branch": git_branch(target),
        "commits_ahead_of_checkout": _count(target, f"{head}..HEAD"),
        "commits_behind_checkout": _count(target, f"HEAD..{head}"),
        "uncommitted": len(dirty),
    }


def open_worktree(paths, config, service: dict, label: str = "tests") -> dict:
    """Open (or re-open) the service's QA worktree at .qa-agent/worktrees/<service>."""
    checkout = _checkout(config, service)
    if not service.get("writable"):
        raise QAError(
            f"Service '{service['name']}' has no 'writable' globs in the project map. List where its tests may be "
            "written (for example [\"tests/**\"]) before opening a worktree: the guard allows nothing else."
        )
    target = paths.worktrees / service["name"]
    if _is_open(target):
        return {**describe(paths, config, service), "created": False}
    if target.exists() and any(target.iterdir()):
        raise QAError(f"{paths.rel(target)} exists and is not a git worktree; move it away first")
    paths.worktrees.mkdir(parents=True, exist_ok=True)
    git(checkout, "worktree", "prune")
    branch_prefix = prefix(config) or "qa-agent/"
    waiting = waiting_branch(checkout, branch_prefix)
    if waiting:
        branch, created = waiting, False  # continue the QA work that is waiting to be merged
        git(checkout, "worktree", "add", "--quiet", str(target), branch)
    else:
        branch = f"{branch_prefix}{_slug(label)}-{today()}"
        created = not _branch_exists(checkout, branch)
        if created:
            git(checkout, "worktree", "add", "--quiet", "-b", branch, str(target), "HEAD")
        else:
            git(checkout, "worktree", "add", "--quiet", str(target), branch)
    result = {**describe(paths, config, service), "created": True, "new_branch": created}
    if result["commits_behind_checkout"]:
        result["note"] = (
            "this QA branch is behind the service's checkout; bring the merged work in with "
            f"`qa.py worktree update --service {service['name']}`"
        )
    return result


def update_worktree(paths, config, service: dict) -> dict:
    """Merge what the service's checkout is on into the QA branch, so tests see the merged code."""
    checkout = _checkout(config, service)
    target = paths.worktrees / service["name"]
    if not _is_open(target):
        raise QAError(f"No open worktree for '{service['name']}'. Open one with `qa.py worktree open`.")
    if git_dirty(target):
        raise QAError(f"{paths.rel(target)} has uncommitted changes; commit them before updating")
    head = git(checkout, "rev-parse", "HEAD")
    if git_ok(target, "merge-base", "--is-ancestor", head, "HEAD"):
        return {**describe(paths, config, service), "updated": False}
    if not git_ok(target, "merge", "--no-edit", "--quiet", head):
        git(target, "merge", "--abort", check=False)
        raise QAError(
            f"Merging the service's code into the QA branch in {paths.rel(target)} conflicts. Nothing was changed. "
            "The conflicting files are tests that the service's own developers also edited: report it to the person."
        )
    return {**describe(paths, config, service), "updated": True}


def close_worktree(paths, config, service: dict, force: bool = False) -> dict:
    """Remove the worktree folder. The QA branch and its commits stay in the service's repository."""
    checkout = _checkout(config, service)
    target = paths.worktrees / service["name"]
    if not _is_open(target):
        return {"service": service["name"], "open": False}
    before = describe(paths, config, service)
    if before["uncommitted"] and not force:
        raise QAError(
            f"{paths.rel(target)} has {before['uncommitted']} uncommitted file(s). Commit them, or pass --force to discard them."
        )
    args = ["worktree", "remove", str(target)] + (["--force"] if force else [])
    git(checkout, *args)
    return {
        "service": service["name"], "open": False, "branch": before["branch"],
        "commits_on_branch": before["commits_ahead_of_checkout"],
        "note": f"branch '{before['branch']}' stays in {paths.rel(checkout)} for the person to review and merge",
    }


def status(paths, config) -> list:
    rows = []
    for service in config.services:
        if config.is_host(service) or not service.get("repo"):
            continue
        try:
            rows.append(describe(paths, config, service))
        except QAError as exc:
            rows.append({"service": service["name"], "open": False, "problem": str(exc)})
    return rows
