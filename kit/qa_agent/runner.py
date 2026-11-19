"""Run a suite, read its JUnit report, and remember the outcome of every test.

A run never stops at the first failure and is never the end of the work: each
failure is classified against what the same test did before (new, known, first
time, flaky), and the catalog's passing/failing/flaky statuses come from here.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from .catalog import HAS_TEST, Catalog
from .matching import Case, case_key, match_cases
from .util import QAError, clip, http_get, join_url, now_iso, read_json, stamp, write_json

HISTORY = 20  # outcomes kept per test
SHOWN = 40  # failures listed in the printed summary; the rest are in result.json


# ---------------------------------------------------------------- JUnit


def parse_junit(path) -> list:
    """Every test case in a JUnit report, whatever runner wrote it."""
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        raise QAError(f"{path} is not a readable JUnit report: {exc}") from exc
    suites = [root] if root.tag == "testsuite" else []
    suites += [node for node in root.iter("testsuite") if node is not root]
    cases = []
    for suite in suites:
        for node in suite.findall("testcase"):
            outcome, message, detail = "passed", "", ""
            for tag, name in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
                child = node.find(tag)
                if child is not None:
                    outcome = name
                    detail = (child.text or "").strip()
                    message = (child.get("message") or "").strip() or (detail.splitlines()[0] if detail else "")
                    break
            cases.append(
                Case(
                    suite=suite.get("name") or "",
                    classname=node.get("classname") or "",
                    name=node.get("name") or "",
                    file=node.get("file") or suite.get("file") or "",
                    outcome=outcome,
                    message=message,
                    detail=detail,
                )
            )
    return cases


def combine(attempts: list):
    """Fold several attempts into one outcome per test. Mixed pass and fail means flaky."""
    order, seen = [], {}
    for cases in attempts:
        for case in cases:
            key = case_key(case)
            if key not in seen:
                seen[key] = []
                order.append(key)
            seen[key].append(case)
    final, flaky = [], set()
    for key in order:
        history = seen[key]
        ran = [c for c in history if c.outcome != "skipped"]
        failed = [c for c in ran if c.outcome in ("failed", "error")]
        passed = [c for c in ran if c.outcome == "passed"]
        if failed and passed:
            flaky.add(key)
            final.append(passed[-1]._replace(message=failed[0].message, detail=failed[0].detail))
        elif failed:
            final.append(failed[-1])
        elif passed:
            final.append(passed[-1])
        else:
            final.append(history[-1])
    return final, flaky


# ---------------------------------------------------------------- ledger


class Ledger:
    """Per suite: recent runs, and a short outcome history per test (p, f, s, x = flaky)."""

    def __init__(self, paths):
        self.paths = paths
        self.data = read_json(paths.ledger, None) or {"version": 1, "suites": {}}

    def suite(self, name: str) -> dict:
        return self.data["suites"].setdefault(name, {"runs": [], "tests": {}})

    def previous(self, suite: str, key: str):
        history = self.suite(suite)["tests"].get(key, "")
        return history[-1] if history else None

    def record(self, suite: str, run: dict, outcomes: dict) -> None:
        entry = self.suite(suite)
        entry["runs"] = (entry["runs"] + [run])[-HISTORY:]
        for key, code in outcomes.items():
            entry["tests"][key] = (entry["tests"].get(key, "") + code)[-HISTORY:]
        write_json(self.paths.ledger, self.data)

    def unstable(self, suite=None) -> list:
        found = []
        for name, entry in sorted(self.data["suites"].items()):
            if suite and name != suite:
                continue
            for key, history in sorted(entry["tests"].items()):
                recent = history[-10:].replace("s", "")
                flips = sum(1 for a, b in zip(recent, recent[1:]) if {a, b} == {"p", "f"})
                if "x" in recent or flips >= 2:
                    found.append({"suite": name, "test": key, "history": history[-10:], "flips": flips})
        return found


# ---------------------------------------------------------------- running


def suite_base(paths, config, suite: dict) -> Path:
    """Where a suite runs: this repository, or the QA worktree of the service whose repository holds its tests."""
    if suite["repo"] == ".":
        return paths.root
    worktree = paths.worktrees / suite["repo"]
    if worktree.is_dir():
        return worktree
    # Never the checkout the service runs from: a test run leaves caches and reports behind, and setup installs.
    raise QAError(
        f"Suite '{suite['name']}' runs inside service '{suite['repo']}', which has no open QA worktree. "
        f"Open it first: `qa.py worktree open --service {suite['repo']}`."
    )


def whole_levels(config, suite: dict, extra=()) -> list:
    """The levels a run covers completely: the whole suite ran, and no other suite there shares the level."""
    if extra:
        return []
    others = [other for other in config.suites if other is not suite and other["repo"] == suite["repo"]]
    return [level for level in suite["levels"] if not any(level in other["levels"] for other in others)]


def check_needs(config, suite: dict) -> list:
    """The services a suite needs that are not answering."""
    down = []
    for name in suite.get("needs") or []:
        service = config.service(name)
        url = config.url(service)
        if not url:
            down.append({"service": name, "reason": "no URL: set its url_env in the env file, or url in the project map"})
            continue
        target = join_url(url, service.get("health") or "")
        status, _, error = http_get(target)
        if status is None or status >= 500:
            down.append({"service": name, "url": target, "reason": error or f"HTTP {status}"})
    return down


def _execute(command: str, cwd: Path, env: dict, log_path: Path, timeout_s: float):
    """Run a shell command with its output in a log. Returns (exit code, timed out)."""
    with open(log_path, "ab") as log:
        log.write(f"\n$ {command}\n".encode("utf-8"))
        log.flush()
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            _kill(proc)
            log.write(f"\n[qa-agent] stopped after {int(timeout_s)}s\n".encode("utf-8"))
            return proc.returncode if proc.returncode is not None else -1, True
        except BaseException:  # interrupted or ended from outside: the test run must not live on by itself
            _kill(proc)
            log.write(b"\n[qa-agent] the run was interrupted\n")
            raise


def _kill(proc) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # Windows
            proc.kill()
        proc.wait(timeout=10)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass


def _tail(path: Path, lines: int = 30) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(text[-lines:])


def _child_env(config, run_dir: Path) -> dict:
    env = dict(os.environ)
    for key, value in config.file_env().items():
        env.setdefault(key, value)
    env["QA_AGENT_RUN_DIR"] = str(run_dir)  # a place for traces, screenshots and other raw output
    env["QA_AGENT_ROOT"] = str(config.paths.root.resolve())
    return env


def _new_run_dir(paths, suite_name: str) -> Path:
    base = f"{stamp()}-{suite_name}"
    candidate, index = paths.runs / base, 1
    while candidate.exists():
        index += 1
        candidate = paths.runs / f"{base}-{index}"
    candidate.mkdir(parents=True)
    return candidate


def _alive(pid) -> bool:
    if os.name == "nt":  # no harmless way to ask there
        return False
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def _take_lock(paths, suite_name: str) -> None:
    """One run at a time in a repository: two would write the same records and collide on the tests' data."""
    paths.runs.mkdir(parents=True, exist_ok=True)
    try:
        held = read_json(paths.run_lock)
    except QAError:
        held = None
    if held and held.get("pid") != os.getpid() and _alive(held.get("pid")):
        raise QAError(
            f"A run is in progress: suite '{held.get('suite')}', started {held.get('at')} (process {held.get('pid')}). "
            f"Wait for it, then read its result with `qa.py runs`. If no run is going on, delete {paths.rel(paths.run_lock)}."
        )
    write_json(paths.run_lock, {"pid": os.getpid(), "suite": suite_name, "at": now_iso()})


def _interrupted(signum, frame):
    raise KeyboardInterrupt()


def run_suite(paths, config, suite_name, extra=(), times=1, rerun=True, setup=False, health=True, record=True):
    """Run a suite and record it. Returns (summary, exit code): 0 green, 1 failures, 2 could not run.

    With `record=False` the outcome is reported and nothing is remembered: for a test still being written, and for
    a run that is meant to fail (proving that an assertion can).
    """
    suite = config.suite(suite_name)
    base = suite_base(paths, config, suite)
    cwd = (base / suite["workdir"]).resolve()
    if not cwd.is_dir():
        raise QAError(f"Suite '{suite_name}': folder {cwd} does not exist")

    if health:
        down = check_needs(config, suite)
        if down:
            return {
                "suite": suite_name, "runnable": False,
                "reason": "services this suite needs are not answering; nothing was run",
                "down": down,
            }, 2

    _take_lock(paths, suite_name)
    handlers = {}
    for name in ("SIGTERM", "SIGHUP"):
        try:  # ended by the tool that started it: stop the tests too
            handlers[name] = signal.signal(getattr(signal, name), _interrupted)
        except (AttributeError, ValueError, OSError):  # not on this platform, or not the main thread
            pass
    try:
        return _run(paths, config, suite, cwd, extra, times, rerun, setup, record)
    finally:
        for name, handler in handlers.items():
            signal.signal(getattr(signal, name), handler)
        try:
            paths.run_lock.unlink()
        except OSError:
            pass


def _run(paths, config, suite, cwd, extra, times, rerun, setup, record):
    suite_name = suite["name"]
    run_dir = _new_run_dir(paths, suite_name)
    log_path = run_dir / "output.log"
    env = _child_env(config, run_dir)
    timeout_s = float(suite["timeout_minutes"]) * 60
    summary = {"suite": suite_name, "run": run_dir.name, "runnable": True, "cwd": paths.rel(cwd)}
    if not record:
        summary["recorded"] = False

    if setup:
        for command in suite["setup"]:
            code, timed_out = _execute(command, cwd, env, log_path, timeout_s)
            if code != 0:
                summary.update(
                    runnable=False,
                    reason=f"setup command {'timed out' if timed_out else 'failed'}: {command}",
                    log=paths.rel(log_path), log_tail=_tail(log_path),
                )
                write_json(run_dir / "result.json", summary)
                return summary, 2

    # What follows `--` takes the place of the suite's own selection, so one test can be run alone.
    if extra:
        suffix = " " + " ".join(shlex.quote(arg) for arg in extra)
    else:
        suffix = (" " + suite["select"].strip()) if (suite.get("select") or "").strip() else ""
    attempts, exit_codes = [], []
    for index in range(max(1, times)):
        junit = run_dir / f"junit-{index + 1}.xml"
        command = suite["command"].replace("{junit}", shlex.quote(str(junit))) + suffix
        code, timed_out = _execute(command, cwd, env, log_path, timeout_s)
        exit_codes.append(code)
        if not junit.exists():
            why = "timed out" if timed_out else f"exited with {code}"
            summary.update(
                runnable=False,
                reason=f"the suite command {why} without writing a JUnit report to {{junit}}",
                command=command, log=paths.rel(log_path), log_tail=_tail(log_path),
            )
            write_json(run_dir / "result.json", summary)
            return summary, 2
        attempts.append(parse_junit(junit))

    first_failed = {case_key(c) for c in attempts[0] if c.outcome in ("failed", "error")}
    if times <= 1 and rerun and suite.get("rerun_failed") and first_failed:
        junit = run_dir / "junit-rerun.xml"
        command = suite["rerun_failed"].replace("{junit}", shlex.quote(str(junit)))
        code, _ = _execute(command, cwd, env, log_path, timeout_s)
        exit_codes.append(code)
        if junit.exists():
            # Only the tests that failed in this run count; a runner's "last failed" list can hold older ones.
            attempts.append([c for c in parse_junit(junit) if case_key(c) in first_failed])

    cases, flaky = combine(attempts)
    summary.update(_record(paths, config, suite, run_dir, cases, flaky, extra, record))
    summary["attempts"] = len(attempts)
    summary["exit_codes"] = exit_codes
    summary["log"] = paths.rel(log_path)
    write_json(run_dir / "result.json", summary)
    return summary, (1 if summary["totals"]["failed"] else 0)


def _record(paths, config, suite, run_dir, cases, flaky, extra, record=True) -> dict:
    ledger = Ledger(paths)
    suite_name = suite["name"]
    catalog = Catalog(paths, config)

    # Which behaviours each reported case belongs to.
    owners: dict = {}
    for entry in catalog.entries:
        if entry.get("repo", ".") != suite["repo"] or entry.get("status") not in HAS_TEST:
            continue
        for ref in entry.get("tests") or []:
            for case in match_cases(ref, cases):
                owners.setdefault(case_key(case), [])
                if entry["id"] not in owners[case_key(case)]:
                    owners[case_key(case)].append(entry["id"])

    totals = {"tests": len(cases), "passed": 0, "failed": 0, "skipped": 0, "flaky": 0}
    outcomes, failed, flaky_list, fixed = {}, [], [], []
    failures_dir = run_dir / "failures"
    for case in cases:
        key = case_key(case)
        previous = ledger.previous(suite_name, key)
        if key in flaky:
            totals["flaky"] += 1
            outcomes[key] = "x"
            flaky_list.append({"test": key, "behaviours": owners.get(key, []), "message": clip(case.message, 200)})
        elif case.outcome in ("failed", "error"):
            totals["failed"] += 1
            outcomes[key] = "f"
            # Known: it failed last time too. New: it ran or was switched off before, and now fails.
            kind = "known" if previous == "f" else ("first-run" if previous is None else "new")
            failures_dir.mkdir(exist_ok=True)
            detail_path = failures_dir / f"{len(failed) + 1:03d}.txt"
            detail_path.write_text(f"{key}\n\n{case.message}\n\n{case.detail}\n", encoding="utf-8")
            failed.append({
                "test": key, "kind": kind, "behaviours": owners.get(key, []),
                "message": clip(case.message or case.detail, 240), "detail": paths.rel(detail_path),
            })
        elif case.outcome == "skipped":
            totals["skipped"] += 1
            outcomes[key] = "s"
        else:
            totals["passed"] += 1
            outcomes[key] = "p"
            if previous == "f":
                fixed.append(key)

    if record:
        ledger.record(
            suite_name,
            {"id": run_dir.name, "at": now_iso(), "totals": totals, "selection": " ".join(extra)},
            outcomes,
        )
        applied = catalog.apply_results(suite["repo"], cases, flaky, whole_levels(config, suite, extra))
    else:
        applied = {"note": "nothing was recorded: the run history and the behaviours' statuses are as they were"}
    unlinked = [case_key(c) for c in cases if case_key(c) not in owners]
    order = {"new": 0, "first-run": 1, "known": 2}
    failed.sort(key=lambda item: order[item["kind"]])
    result = {
        "totals": totals,
        "failed": failed[:SHOWN],
        "flaky": flaky_list[:SHOWN],
        "fixed": fixed[:SHOWN],
        "catalog": applied,
        "tests_without_a_behaviour": {"count": len(unlinked), "first": unlinked[:15]},
    }
    if len(failed) > SHOWN:
        result["failed_not_shown"] = len(failed) - SHOWN
        (run_dir / "failed.json").write_text(json.dumps(failed, indent=1), encoding="utf-8")
        result["all_failed"] = paths.rel(run_dir / "failed.json")
    return result


# ---------------------------------------------------------------- reading back


def recent_runs(paths, suite=None, last: int = 5) -> list:
    ledger = Ledger(paths)
    rows = []
    for name, entry in sorted(ledger.data["suites"].items()):
        if suite and name != suite:
            continue
        for run in entry["runs"][-last:]:
            rows.append({"suite": name, **run})
    return rows


def last_run(paths, suite: str):
    runs = Ledger(paths).data["suites"].get(suite, {}).get("runs") or []
    return runs[-1] if runs else None


def show_run(paths, run_id: str) -> dict:
    path = paths.runs / run_id / "result.json"
    data = read_json(path)
    if data is None:
        raise QAError(f"No recorded run '{run_id}' (raw runs are kept only on the machine that ran them)")
    return data
