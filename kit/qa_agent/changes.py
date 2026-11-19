"""What was merged into each service since the last sync, and which recorded behaviours it touches.

The sync point of a service is the commit its tests were last brought up to
date with. Tests check the running application, so the comparison ends at the
commit the service's checkout is on, not at whatever the remote has since.
"""

from __future__ import annotations

import re

from . import surface
from .catalog import SYSTEM, Catalog
from .util import (
    QAError, clip, git, git_branch, git_head, git_ok, glob_match, is_git_repo, match_any, now_iso, read_json,
    write_json,
)

TEST_HINTS = [
    "tests", "test", "__tests__", "e2e", "cypress", "specs", "*.spec.*", "*.test.*", "test_*.py", "*_test.py",
    "*_test.go", "conftest.py",
]
OTHER_HINTS = [
    "*.md", "*.rst", "*.txt", "docs", ".github", ".gitlab", "*.lock", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "LICENSE*", ".gitignore", ".editorconfig", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.ico",
    ".qa-agent",
]
HISTORY = 20


def load(paths) -> dict:
    data = read_json(paths.sync, None) or {"version": 1, "services": {}, "history": []}
    data.setdefault("services", {})
    data.setdefault("history", [])
    data.setdefault("examined", {})  # per service: the commit the last comparison went up to
    return data


def classify(path: str, service: dict) -> str:
    """'tests', 'source' or 'other' for a changed file of a service."""
    if match_any(path, service.get("tests")) or match_any(path, TEST_HINTS):
        return "tests"
    if service.get("sources"):
        return "source" if match_any(path, service["sources"]) else "other"
    return "other" if match_any(path, OTHER_HINTS) else "source"


def entry_sources(entry: dict, service_names) -> list:
    """The (service, path pattern) pairs a behaviour says it was derived from."""
    pairs = []
    for source in entry.get("sources") or []:
        text = re.sub(r"(::.*|#.*|:\d+(-\d+)?)$", "", source.strip())
        head, sep, rest = text.partition(":")
        if sep and head in service_names and rest:
            pairs.append((head, rest))
        elif entry.get("service") != SYSTEM:
            pairs.append((entry.get("service"), text))
    return pairs


def _changed_files(checkout, since: str, to: str) -> list:
    out = git(checkout, "diff", "--name-status", "-M", f"{since}..{to}")
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0][0]
        record = {"path": parts[-1], "change": code}
        if code == "R" and len(parts) == 3:
            record["was"] = parts[1]
        files.append(record)
    return files


def _log(checkout, since: str, to: str, limit: int) -> list:
    out = git(
        checkout, "log", "--first-parent", f"--max-count={limit}", "--date=short",
        "--pretty=format:%h %ad %s", f"{since}..{to}",
    )
    return [clip(line, 160) for line in out.splitlines() if line.strip()]


def _surface_summary(paths, config, service: dict):
    if not service.get("openapi"):
        return None
    try:
        result = surface.fetch(paths, config, service, save=False)
    except QAError as exc:
        return {"checked": False, "reason": clip(str(exc), 200)}
    if "worst" not in result:
        return {"checked": False, "reason": "no stored snapshot to compare with"}
    return {
        "checked": True, "worst": result["worst"], "added": len(result["added"]),
        "removed": len(result["removed"]), "changed": len(result["changed"]),
        "details": f"qa.py surface diff --service {service['name']}",
    }


def service_changes(paths, config, catalog, service: dict, fetch=False, to=None, max_files=150, max_commits=40):
    name = service["name"]
    checkout = config.repo_path(service)
    if checkout is None:
        return {"service": name, "available": False, "reason": "no checkout in the project map"}
    if not is_git_repo(checkout):
        return {"service": name, "available": False, "reason": f"{checkout} is not a git repository"}
    head = git_head(checkout)
    if head is None:
        return {"service": name, "available": False, "reason": "the repository has no commits"}
    branch = git_branch(checkout)
    target = to or "HEAD"
    if to and not git_ok(checkout, "rev-parse", "--verify", "-q", f"{to}^{{commit}}"):
        raise QAError(f"{name}: '{to}' is not a commit in {checkout}")
    target_sha = git(checkout, "rev-parse", f"{target}^{{commit}}")
    result = {"service": name, "available": True, "branch": branch, "at": target_sha[:12]}
    notes = []
    if service.get("branch") and branch != service["branch"] and not config.is_host(service):
        notes.append(
            f"the checkout is on '{branch or 'a detached commit'}', while merged work is expected on "
            f"'{service['branch']}': the running service may not be the merged code"
        )
    if fetch and service.get("branch"):
        remote = f"origin/{service['branch']}"
        if git_ok(checkout, "fetch", "--quiet", "origin", service["branch"]):
            if git_ok(checkout, "rev-parse", "--verify", "-q", remote):
                behind = int(git(checkout, "rev-list", "--count", f"{target_sha}..{remote}") or 0)
                result["not_yet_in_checkout"] = behind
                if behind:
                    notes.append(
                        f"{behind} commit(s) on {remote} are not in the checkout the service runs from; "
                        "the person updates and restarts it, then sync again"
                    )
        else:
            notes.append("git fetch failed; the comparison uses what is already in the checkout")

    point = load(paths)["services"].get(name)
    if not point:
        result["synced"] = None
        result["recent"] = _recent(checkout, target_sha, 10)
        notes.append("no sync point yet: record one with `qa.py changes --mark-synced` once the map is drawn")
        if notes:
            result["notes"] = notes
        return result

    since = point["commit"]
    result["synced"] = {"commit": since[:12], "at": point.get("at")}
    if not git_ok(checkout, "cat-file", "-e", f"{since}^{{commit}}"):
        notes.append(
            "the sync point is not in this repository any more (history was rewritten); "
            "treat every behaviour of this service as possibly affected"
        )
        result["changed"] = True
        result["notes"] = notes
        return result
    if since == target_sha:
        result["changed"] = False
        if notes:
            result["notes"] = notes
        return result
    if not git_ok(checkout, "merge-base", "--is-ancestor", since, target_sha):
        notes.append("the checkout is not ahead of the sync point (it moved back or to another line of history)")

    files = _changed_files(checkout, since, target_sha)
    grouped = {"source": [], "tests": [], "other": []}
    for record in files:
        grouped[classify(record["path"], service)].append(record)
    total = int(git(checkout, "rev-list", "--count", f"{since}..{target_sha}") or 0)
    result["changed"] = True
    result["commits"] = total
    result["merged"] = _log(checkout, since, target_sha, max_commits)
    if total > len(result["merged"]):
        result["merged_note"] = "first-parent history; merged branches bring the other commits"
    result["files"] = {
        "source": grouped["source"][:max_files],
        "tests": grouped["tests"][:max_files],
        "other": len(grouped["other"]),
    }
    for kind in ("source", "tests"):
        if len(grouped[kind]) > max_files:
            result["files"][f"{kind}_not_shown"] = len(grouped[kind]) - max_files

    # Behaviours whose recorded sources were touched.
    changed_paths = [r["path"] for r in files] + [r["was"] for r in files if r.get("was")]
    names = [s["name"] for s in config.services]
    affected, listed = [], set()
    for entry in catalog.entries:
        if entry.get("status") == "retired":
            continue
        hits = []
        for owner, pattern in entry_sources(entry, names):
            if owner != name:
                continue
            for path in changed_paths:
                if glob_match(path, pattern) and path not in hits:
                    hits.append(path)
        if hits:
            listed.update(hits)
            affected.append({
                "id": entry["id"], "status": entry["status"], "level": entry["level"],
                "case": clip(f"{entry['surface']}: {entry['case']}", 120), "files": hits[:5],
            })
    result["affected"] = affected
    result["source_files_no_behaviour_lists"] = [
        r["path"] for r in grouped["source"] if r["path"] not in listed and r["change"] != "D"
    ][:max_files]
    contract = _surface_summary(paths, config, service)
    if contract:
        result["contract"] = contract
    result["read_a_diff"] = f"git -C {paths.rel(checkout)} diff {since[:12]}..{target_sha[:12]} -- <path>"
    if notes:
        result["notes"] = notes
    return result


def _recent(checkout, sha: str, limit: int) -> list:
    out = git(
        checkout, "log", "--first-parent", f"--max-count={limit}", "--date=short", "--pretty=format:%h %ad %s", sha,
        check=False,
    )
    return [clip(line, 160) for line in out.splitlines() if line.strip()]


def report(paths, config, names=None, fetch=False, to=None, max_files=150) -> dict:
    catalog = Catalog(paths, config)
    services = [config.service(n) for n in names] if names else config.services
    rows = [service_changes(paths, config, catalog, s, fetch=fetch, to=to, max_files=max_files) for s in services]
    # Remember how far this comparison went: the sync is later marked up to here, not to whatever arrived meanwhile.
    data = load(paths)
    before = dict(data["examined"])
    for service, row in zip(services, rows):
        if row.get("available") and row.get("synced") is not None:
            sha = git(config.repo_path(service), "rev-parse", f"{to or 'HEAD'}^{{commit}}")
            data["examined"][service["name"]] = {"commit": sha, "at": now_iso()}
    if data["examined"] != before:
        write_json(paths.sync, data)
    changed = [r["service"] for r in rows if r.get("changed")]
    unsynced = [r["service"] for r in rows if r.get("available") and r.get("synced") is None]
    if changed:
        advice = "follow playbooks/sync.md for: " + ", ".join(changed)
    elif unsynced:
        advice = "no sync point for: " + ", ".join(unsynced)
    else:
        advice = "nothing was merged since the last sync"
    return {"services": rows, "next": advice}


def mark(paths, config, names=None, to=None, refresh_surface=True) -> dict:
    """Record that the tests are up to date with each service's code.

    The point is the commit the last comparison (`report`) went up to, so commits that reached the checkout while
    the sync was being worked on are not skipped; without an earlier comparison it is what the checkout is on now.
    """
    data = load(paths)
    services = [config.service(n) for n in names] if names else config.services
    moved, skipped, surfaces, later = {}, {}, {}, {}
    for service in services:
        name = service["name"]
        checkout = config.repo_path(service)
        if checkout is None or not is_git_repo(checkout) or git_head(checkout) is None:
            skipped[name] = "no git checkout"
            continue
        head = git(checkout, "rev-parse", "HEAD")
        examined = (data["examined"].pop(name, None) or {}).get("commit")
        if to:
            sha = git(checkout, "rev-parse", f"{to}^{{commit}}")
        elif examined and git_ok(checkout, "merge-base", "--is-ancestor", examined, head):
            sha = examined
        else:
            sha = head
        before = data["services"].get(name, {}).get("commit")
        data["services"][name] = {"commit": sha, "branch": git_branch(checkout), "at": now_iso()}
        moved[name] = {"from": before[:12] if before else None, "to": sha[:12]}
        behind = 0 if sha == head else int(git(checkout, "rev-list", "--count", f"{sha}..{head}", check=False) or 0)
        if behind:
            later[name] = behind
        if refresh_surface and service.get("openapi"):
            if behind:
                surfaces[name] = "not refreshed: the service's document may already describe the later commits"
                continue
            try:
                surface.fetch(paths, config, service, save=True)
                surfaces[name] = "snapshot refreshed"
            except QAError as exc:
                surfaces[name] = "not refreshed: " + clip(str(exc), 160)
    if moved:
        data["history"] = (data["history"] + [{"at": now_iso(), "services": moved}])[-HISTORY:]
        write_json(paths.sync, data)
    result = {"synced": moved}
    if later:
        result["arrived_after_the_comparison"] = later
        result["note"] = (
            "these commits reached the checkout after `qa.py changes` last compared it; they are not marked as "
            "synced and wait for the next sync"
        )
    if skipped:
        result["skipped"] = skipped
    if surfaces:
        result["surface"] = surfaces
    return result
