"""The behaviour catalog: what the application should do, and which test checks each thing.

It is the plan, the progress record and the traceability matrix in one file, so
work can stop and resume in any session, with any assistant. Identifiers are
assigned here, and whether a test passes is recorded only from a test run.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import IN_SERVICE_LEVELS, LEVELS
from .matching import case_key, match_cases, split_ref
from .util import QAError, now_iso, read_json, today, write_json

# Set by a test run, never by hand.
RUN_STATUSES = ("passing", "failing", "flaky")
# Set by the agent, each with a reason.
AGENT_STATUSES = ("planned", "defect", "blocked", "quarantined", "retired", "covered")
STATUSES = ("planned", "written") + RUN_STATUSES + AGENT_STATUSES[1:]
# A test exists and is expected to be collected by a run.
LIVE = ("written", "passing", "failing", "flaky")
HAS_TEST = LIVE + ("defect", "quarantined", "covered")

REQUIRED = ("service", "level", "surface", "case", "expect", "intent")
EDITABLE = ("service", "level", "area", "surface", "case", "expect", "intent", "priority", "sources", "tags", "note", "repo")
LIMITS = {"surface": 160, "case": 200, "expect": 300, "intent": 300, "note": 600, "area": 60}
SYSTEM = "system"  # a behaviour that belongs to several services together


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def entry_key(entry: dict) -> tuple:
    return (entry.get("service"), entry.get("level"), _norm(entry.get("surface")), _norm(entry.get("case")))


def _id_number(entry_id) -> int:
    match = re.match(r"^B-(\d+)$", str(entry_id or ""))
    return int(match.group(1)) if match else 0


class Catalog:
    def __init__(self, paths, config):
        self.paths = paths
        self.config = config
        self.data = read_json(paths.catalog, None) or {"version": 1, "next_id": 1, "entries": []}
        if not isinstance(self.data.get("entries"), list):
            raise QAError(f"{paths.rel(paths.catalog)}: 'entries' must be a list")

    # -- storage

    @property
    def entries(self) -> list:
        return self.data["entries"]

    def save(self) -> None:
        self.entries.sort(key=lambda e: _id_number(e.get("id")))
        write_json(self.paths.catalog, self.data)

    def get(self, entry_id: str) -> dict:
        for entry in self.entries:
            if entry.get("id") == entry_id:
                return entry
        raise QAError(f"No behaviour {entry_id} in the catalog")

    def select(self, status=None, level=None, service=None, area=None, repo=None, priority=None, tag=None) -> list:
        areas = [_norm(a) for a in area] if area else None
        tags = {_norm(t) for t in tag} if tag else None

        def keep(entry):
            return (
                (not status or entry.get("status") in status)
                and (not level or entry.get("level") in level)
                and (not service or entry.get("service") in service)
                and (not areas or _norm(entry.get("area")) in areas)
                and (repo is None or entry.get("repo") == repo)
                and (not priority or entry.get("priority", 2) in priority)
                and (not tags or tags & {_norm(t) for t in entry.get("tags") or []})
            )

        return [entry for entry in self.entries if keep(entry)]

    # -- checks on one entry

    def _service_names(self) -> list:
        return [s["name"] for s in self.config.services]

    def default_repo(self, service: str, level: str) -> str:
        """Unit and component tests live with the service's code; everything else lives here."""
        if service == SYSTEM or level not in IN_SERVICE_LEVELS:
            return "."
        return self.repo_name(service)

    def repo_name(self, service: str) -> str:
        """How the repository holding a service's own tests is named in an entry: '.' when it is this one."""
        owner = self.config.tests_owner(self.config.service(service))
        return "." if self.config.is_host(owner) else owner["name"]

    def problems(self, entry: dict) -> list:
        found = []
        for field in REQUIRED:
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                found.append(f"'{field}' is required")
        services = self._service_names()
        if entry.get("service") and entry["service"] != SYSTEM and entry["service"] not in services:
            found.append(
                f"service '{entry['service']}' is not in the project map "
                f"(use one of {', '.join(services)}, or '{SYSTEM}')"
            )
        if entry.get("level") and entry["level"] not in LEVELS:
            found.append(f"level must be one of {', '.join(LEVELS)}")
        for field, limit in LIMITS.items():
            if isinstance(entry.get(field), str) and len(entry[field]) > limit:
                found.append(f"'{field}' is {len(entry[field])} characters; keep it under {limit}")
        if entry.get("priority", 2) not in (1, 2, 3) or isinstance(entry.get("priority", 2), bool):
            found.append("priority must be 1 (highest), 2 or 3")
        for field in ("sources", "tags", "tests"):
            value = entry.get(field, [])
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                found.append(f"'{field}' must be a list of text values")
        repo = entry.get("repo", ".")
        if repo != "." and repo not in services:
            found.append("repo must be '.' or the name of a service")
        if entry.get("status", "planned") not in STATUSES:
            found.append(f"status must be one of {', '.join(STATUSES)}")
        return found

    # -- test references

    def test_base(self, repo: str):
        """The folder a behaviour's test paths are relative to."""
        if repo == ".":
            return self.paths.root
        worktree = self.paths.worktrees / repo
        if worktree.is_dir():
            return worktree
        checkout = self.config.repo_path(self.config.service(repo))
        if checkout is None:
            raise QAError(f"Service '{repo}' has no checkout in the project map, so it cannot hold tests")
        return checkout

    def ref_problem(self, repo: str, ref: str):
        """Why a test reference cannot be trusted, or None when the file names that test."""
        try:
            path, names = split_ref(ref)
        except ValueError as exc:
            return str(exc)
        base = self.test_base(repo)
        target = base / path
        if not target.is_file():
            return f"{path} does not exist in {self.paths.rel(base) or '.'}"
        text = target.read_text(encoding="utf-8", errors="replace")
        last = names[-1]
        if last not in text and re.sub(r"\[.*\]$", "", last) not in text:
            return f"{path} has no test named '{last}'"
        return None

    # -- changes

    def add(self, items) -> dict:
        """Add planned behaviours. Returns what was added and what was skipped, and why."""
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise QAError("Give one behaviour (an object) or a list of them")
        seen = {entry_key(e): e["id"] for e in self.entries}
        added, skipped, rejected = [], [], []
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                rejected.append({"index": index, "problems": ["must be an object"]})
                continue
            entry = {
                "id": None,
                "service": raw.get("service"),
                "level": raw.get("level"),
                "area": str(raw.get("area") or "").strip(),
                "surface": str(raw.get("surface") or "").strip(),
                "case": str(raw.get("case") or "").strip(),
                "expect": str(raw.get("expect") or "").strip(),
                "intent": str(raw.get("intent") or "").strip(),
                "priority": raw.get("priority", 2),
                "sources": raw.get("sources") or [],
                "tags": raw.get("tags") or [],
                "repo": raw.get("repo"),
                "tests": [],
                "status": "planned",
                "note": str(raw.get("note") or "").strip(),
            }
            unknown = sorted(set(raw) - set(entry) - {"created", "updated"})
            problems = self.problems({**entry, "repo": entry["repo"] or "."})
            if unknown:
                problems.append(f"unknown field(s): {', '.join(unknown)}")
            if raw.get("status") not in (None, "planned") or raw.get("tests"):
                problems.append("new behaviours start as 'planned' without tests; link the test after writing it")
            if problems:
                rejected.append({"index": index, "case": entry["case"], "problems": problems})
                continue
            if entry["repo"] is None:
                entry["repo"] = self.default_repo(entry["service"], entry["level"])
            elif entry["repo"] != ".":
                entry["repo"] = self.repo_name(entry["repo"])
            key = entry_key(entry)
            if key in seen:
                skipped.append({"index": index, "case": entry["case"], "same_as": seen[key]})
                continue
            entry["id"] = f"B-{self.data['next_id']:04d}"
            self.data["next_id"] += 1
            entry["created"] = entry["updated"] = now_iso()
            self.entries.append(entry)
            seen[key] = entry["id"]
            added.append(entry["id"])
        if added:
            self.save()
        return {"added": added, "skipped_duplicates": skipped, "rejected": rejected}

    def update(self, entry_id: str, fields: dict) -> dict:
        entry = self.get(entry_id)
        if not isinstance(fields, dict) or not fields:
            raise QAError("Give the fields to change as a JSON object")
        unknown = sorted(set(fields) - set(EDITABLE))
        if unknown:
            raise QAError(f"Cannot change {', '.join(unknown)} this way. Editable: {', '.join(EDITABLE)}")
        candidate = {**entry, **fields}
        problems = self.problems(candidate)
        if problems:
            raise QAError(f"{entry_id}: " + "; ".join(problems))
        if fields.get("repo") not in (None, "."):
            fields = {**fields, "repo": self.repo_name(fields["repo"])}
            candidate["repo"] = fields["repo"]
        key = entry_key(candidate)
        for other in self.entries:
            if other is not entry and entry_key(other) == key:
                raise QAError(f"{entry_id} would duplicate {other['id']}")
        entry.update(fields)
        entry["updated"] = now_iso()
        self.save()
        return entry

    def link(self, entry_id: str, refs: list, replace: bool = False, verify: bool = True) -> dict:
        """Record the test(s) that check a behaviour. Its status becomes 'written' until a run says more."""
        entry = self.get(entry_id)
        if entry["status"] in ("retired", "covered"):
            raise QAError(f"{entry_id} is '{entry['status']}'; set it back to planned before linking a new test")
        if verify:
            for ref in refs:
                problem = self.ref_problem(entry["repo"], ref)
                if problem:
                    raise QAError(f"{entry_id}: {problem}")
        tests = [] if replace else list(entry.get("tests") or [])
        for ref in refs:
            if ref not in tests:
                tests.append(ref)
        entry["tests"] = tests
        if entry["status"] in ("planned", "blocked", "defect", "quarantined"):
            # Linking says the test is live (again): the next run decides whether it passes.
            entry["status"] = "written"
            entry.pop("until", None)
            entry.pop("report", None)
        entry["updated"] = now_iso()
        self.save()
        return entry

    def set_status(self, entry_id, status, note=None, tests=None, until=None, report=None, verify=True, tags=None) -> dict:
        if status in RUN_STATUSES or status == "written":
            raise QAError(
                f"'{status}' is recorded by `qa.py run` from the test report (or by `catalog link`), not set by hand"
            )
        if status not in AGENT_STATUSES:
            raise QAError(f"status must be one of {', '.join(AGENT_STATUSES)}")
        entry = self.get(entry_id)
        note = (note or "").strip()
        if status != "planned" and not note:
            raise QAError(f"--note is required for '{status}': say why, with the evidence")
        if len(note) > LIMITS["note"]:
            raise QAError(f"--note is {len(note)} characters; keep it under {LIMITS['note']}")
        if tests:
            if verify:
                for ref in tests:
                    problem = self.ref_problem(entry["repo"], ref)
                    if problem:
                        raise QAError(f"{entry_id}: {problem}")
            entry["tests"] = list(dict.fromkeys(tests))
        if status in ("defect", "quarantined", "covered") and not entry.get("tests"):
            raise QAError(f"'{status}' needs the test it refers to: pass --test path::name")
        if status == "quarantined":
            if not until:
                raise QAError("--until YYYY-MM-DD is required for 'quarantined': the day it must be looked at again")
            try:
                datetime.strptime(until, "%Y-%m-%d")
            except ValueError as exc:
                raise QAError("--until must be a date written as YYYY-MM-DD") from exc
            entry["until"] = until
        else:
            entry.pop("until", None)
        if status == "defect" and report:
            entry["report"] = report
        elif status != "defect":
            entry.pop("report", None)
        if status == "planned":
            entry["tests"] = []
        entry["status"] = status
        if note:
            entry["note"] = note
        if tags:
            entry["tags"] = list(dict.fromkeys(list(entry.get("tags") or []) + [str(t).strip() for t in tags if str(t).strip()]))
        entry["updated"] = now_iso()
        self.save()
        return entry

    def remove(self, entry_id: str) -> None:
        entry = self.get(entry_id)
        if entry["status"] != "planned":
            raise QAError(f"{entry_id} is '{entry['status']}'. Only planned behaviours can be removed; retire the others.")
        self.entries.remove(entry)
        self.save()

    # -- reading

    def next(self, batch: int, offset: int = 0, **filters) -> list:
        planned = self.select(status=["planned"], **filters)
        planned.sort(key=lambda e: (e.get("priority", 2), _id_number(e["id"])))
        return planned[max(0, offset) : max(0, offset) + batch]

    def stats(self) -> dict:
        by_status: dict = {}
        by_level: dict = {}
        by_service: dict = {}
        by_tag: dict = {}
        for entry in self.entries:
            status = entry.get("status", "planned")
            by_status[status] = by_status.get(status, 0) + 1
            level = by_level.setdefault(entry.get("level"), {})
            level[status] = level.get(status, 0) + 1
            service = by_service.setdefault(entry.get("service"), {})
            service[status] = service.get(status, 0) + 1
            if status != "retired":
                for tag in entry.get("tags") or []:
                    by_tag[tag] = by_tag.get(tag, 0) + 1
        total = len(self.entries)
        in_scope = total - by_status.get("retired", 0)
        checked = by_status.get("passing", 0) + by_status.get("covered", 0)
        result = {
            "total": total,
            "by_status": {s: by_status[s] for s in STATUSES if s in by_status},
            "by_level": {lv: by_level[lv] for lv in LEVELS if lv in by_level},
            "by_service": by_service,
            "checked_percent": round(100 * checked / in_scope, 1) if in_scope else 0.0,
        }
        if by_tag:
            result["by_tag"] = dict(sorted(by_tag.items()))
        return result

    def validate(self) -> dict:
        errors, warnings = [], []
        seen: dict = {}
        ids = set()
        for entry in self.entries:
            entry_id = str(entry.get("id") or "?")
            if not re.match(r"^B-\d{4,}$", entry_id):
                errors.append(f"{entry_id}: id must look like B-0001")
            if entry_id in ids:
                errors.append(f"{entry_id}: id is used twice")
            ids.add(entry_id)
            for problem in self.problems(entry):
                errors.append(f"{entry_id}: {problem}")
            key = entry_key(entry)
            if key in seen:
                errors.append(f"{entry_id}: same behaviour as {seen[key]}")
            seen[key] = entry_id
            status = entry.get("status")
            tests = entry.get("tests") or []
            if status in HAS_TEST and not tests:
                errors.append(f"{entry_id}: status '{status}' but no test is linked")
            if status == "planned" and tests:
                warnings.append(f"{entry_id}: planned, yet a test is linked")
            if status != "retired" and isinstance(tests, list):
                for ref in tests:
                    try:
                        problem = self.ref_problem(entry.get("repo", "."), ref)
                    except QAError as exc:
                        problem = str(exc)
                    if problem:
                        errors.append(f"{entry_id}: {problem}")
            if status == "quarantined":
                until = entry.get("until")
                if not until:
                    errors.append(f"{entry_id}: quarantined without an 'until' date")
                elif until < today():
                    warnings.append(f"{entry_id}: quarantine ended {until}; fix the test or renew it with a reason")
            if status in ("defect", "blocked", "quarantined", "retired") and not str(entry.get("note") or "").strip():
                errors.append(f"{entry_id}: status '{status}' needs a note saying why")
        highest = max((_id_number(e.get("id")) for e in self.entries), default=0)
        if self.data.get("next_id", 1) <= highest:
            errors.append(f"next_id must be above {highest}")
        return {"ok": not errors, "entries": len(self.entries), "errors": errors, "warnings": warnings}

    # -- results from a run

    def apply_results(self, repo: str, cases: list, flaky_keys=(), whole_levels=()) -> dict:
        """Move live behaviours to passing, failing or flaky according to a run's test cases.

        `whole_levels` names the levels this run covered completely. A behaviour at one of them whose linked test
        the run did not report goes back to 'written': no run has seen that test, whatever an earlier one saw.
        """
        flaky_keys = set(flaky_keys)
        changes, skipped, unreported, counts = [], [], [], {"passing": 0, "failing": 0, "flaky": 0}
        touched = False
        for entry in self.entries:
            if entry.get("repo", ".") != repo or entry.get("status") not in LIVE or not entry.get("tests"):
                continue
            matched = []
            for ref in entry["tests"]:
                matched.extend(match_cases(ref, cases))
            ran = [case for case in matched if case.outcome != "skipped"]
            if not ran:
                if matched:
                    skipped.append(entry["id"])
                elif entry.get("level") in whole_levels:
                    unreported.append(entry["id"])
                    if entry["status"] != "written":
                        changes.append({"id": entry["id"], "from": entry["status"], "to": "written"})
                        entry["status"] = "written"
                        entry["updated"] = now_iso()
                        touched = True
                continue
            if any(case.outcome in ("failed", "error") for case in ran):
                status = "failing"
            elif any(case_key(case) in flaky_keys for case in ran):
                status = "flaky"
            else:
                status = "passing"
            counts[status] += 1
            if status != entry["status"]:
                changes.append({"id": entry["id"], "from": entry["status"], "to": status})
                entry["status"] = status
                entry["updated"] = now_iso()
                touched = True
        if touched:
            self.save()
        result = {"seen": counts, "changed": changes, "linked_but_skipped": skipped}
        if unreported:
            result["linked_but_not_reported"] = unreported
            result["note"] = (
                "the whole suite ran and did not report the tests these behaviours link, so they are 'written' again: "
                "correct the link (`qa.py catalog link <id> --test … --replace`) or what the suite selects"
            )
        return result
