"""The helper's command line: `python3 .qa-agent/kit/qa.py <command>`.

Commands print JSON, except `status` (a short page to read). Exit codes:
0 done, 1 the check or run found problems, 2 the command could not do its work.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import LEVELS, TASKS, __version__, changes, doctor, guard, library, score, status, surface, worktree
from .catalog import AGENT_STATUSES, STATUSES, Catalog
from .config import PLACEMENTS, WRITES, Config, agree, agreed, consent_changes, init_project, validate
from .paths import Paths, find_root
from .runner import Ledger, recent_runs, run_suite, show_run
from .util import QAError, clip, emit, read_json, write_json


def _input_file(name: str, root=None) -> Path:
    """A file named on the command line: as given, or relative to the repository when the command runs elsewhere."""
    path = Path(name).expanduser()
    if not path.is_file() and root is not None and not path.is_absolute() and (Path(root) / path).is_file():
        path = Path(root) / path
    if not path.is_file():
        raise QAError(f"{name} does not exist")
    return path


def _json_input(args, root=None):
    if getattr(args, "json", None):
        text = args.json
    elif getattr(args, "file", None) and args.file != "-":
        text = _input_file(args.file, root).read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            raise QAError("Give the data with --json '…', --file PATH, or on standard input")
        text = sys.stdin.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise QAError(f"The input is not valid JSON: {exc}") from exc


def _context(args, need_config: bool = True):
    paths = Paths(find_root(args.root))
    config = Config.load(paths) if need_config else None
    return paths, config


def _suite_for(config, entry: dict):
    for suite in config.suites:
        if suite["repo"] == entry.get("repo", ".") and entry["level"] in suite["levels"]:
            return suite["name"]
    return None


def _brief(entry: dict) -> dict:
    return {
        "id": entry["id"], "status": entry["status"], "level": entry["level"], "service": entry["service"],
        "area": entry.get("area", ""), "case": clip(f"{entry['surface']}: {entry['case']}", 130),
        "tests": len(entry.get("tests") or []),
    }


# ---------------------------------------------------------------- commands


def cmd_status(args) -> int:
    paths, _ = _context(args, need_config=False)
    state = status.build(paths)
    if args.json:
        emit(state)
    else:
        print(status.render(state))
    return 0


def cmd_doctor(args) -> int:
    paths, _ = _context(args, need_config=False)
    result, code = doctor.run(paths)
    emit(result)
    return code


def cmd_init(args) -> int:
    paths, _ = _context(args, need_config=False)
    result = init_project(
        paths, args.name, args.about, args.placement, args.service, env_name=args.env_name, writes=args.writes,
        force=args.force, off_limits=args.off_limits or [],
    )
    if status.load_progress(paths).get("phase") is None:
        status.set_progress(paths, phase="setup")  # from here on, `qa.py status` says which setup step is next
    emit(result)
    return 0


def cmd_config(args) -> int:
    paths, _ = _context(args, need_config=False)
    if args.action == "validate":
        data = read_json(paths.config)
        if data is None:
            raise QAError(f"{paths.rel(paths.config)} does not exist")
        errors = validate(data)
        emit({"ok": not errors, "errors": errors})
        return 1 if errors else 0
    config = Config.load(paths)
    if args.action == "agree":
        if not (args.note or "").strip():
            raise QAError("--note is required: what the person agreed to, in a sentence or two")
        changed = [field for field, _, _ in consent_changes(paths, config)]
        record = agree(paths, config, args.note)
        result = {"agreed_at": record["agreed_at"], "note": record["note"], "limits": record["fields"]}
        if changed:
            result["changed_since_the_last_agreement"] = changed
        emit(result)
        return 0
    if args.action == "agreed":
        record = agreed(paths)
        if record is None:
            emit({"agreed": False, "note": "nothing is recorded yet: setup records it once the person agrees to the plan"})
            return 1
        differences = [
            {"field": field, "agreed": was, "now": current} for field, was, current in consent_changes(paths, config)
        ]
        emit({"agreed": True, "agreed_at": record["agreed_at"], "note": record["note"], "limits": record["fields"],
              "changed_since": differences})
        return 1 if differences else 0
    emit(config.data)
    return 0


def cmd_branch(args) -> int:
    paths, config = _context(args)
    emit(worktree.ensure_host_branch(paths, config, args.name, fresh=args.fresh))
    return 0


def cmd_library(args) -> int:
    paths, _ = _context(args, need_config=False)
    if args.action == "status":
        emit(library.status(paths))
        return 0
    if args.action == "scan":
        path = library.source(paths, args.path)
        items = library.scan(path, args.rules, args.skills, args.exclude)
        rows = [{"kind": i["kind"], "name": i["name"], "about": clip(i["description"], 150), "source": i["source"]} for i in items]
        result = {
            "library": paths.rel(Path(path).expanduser()),
            "found": {"rules": sum(1 for i in items if i["kind"] == "rule"), "skills": sum(1 for i in items if i["kind"] == "skill")},
            "items": rows,
        }
        clashes = library.duplicates(items)
        if clashes:
            result["same_name_more_than_once"] = clashes
        emit(result)
        return 0
    # install
    lock = read_json(paths.library_lock, {}) or {}
    rules, skills, exclude = args.rules, args.skills, args.exclude
    select, mirror_rules, mirror_skills = args.select, args.mirror_rules, args.mirror_skills
    if args.refresh:
        if not lock:
            raise QAError("Nothing is installed yet, so there is nothing to refresh")
        path = library.source(paths, args.path, lock["source"]["path"])
        rules = rules or lock["globs"]["rules"]
        skills = skills or lock["globs"]["skills"]
        exclude = exclude or lock["globs"]["exclude"]
        select = select or [item["name"] for item in lock["items"]]
        mirror_rules = mirror_rules or lock["mirrors"]["rules"]
        mirror_skills = mirror_skills or lock["mirrors"]["skills"]
    else:
        path = library.source(paths, args.path)
    result = library.install(
        paths, path, rules, skills, exclude, select, mirror_rules or (), mirror_skills or (), force=args.force,
    )
    # The map names the library as the person gave it; a pasted copy by its place in this repository.
    named = args.path or result["from"]["path"]
    data = read_json(paths.config)
    if data is not None and data.get("library") != named:
        data["library"] = named
        write_json(paths.config, data)
    emit(result)
    return 0


def cmd_rules(args) -> int:
    paths, config = _context(args)
    emit(library.reading_list(paths, config, level=args.level, task=args.task, suite=args.suite))
    return 0


def cmd_surface(args) -> int:
    paths, config = _context(args)
    if args.action == "show":
        emit(surface.show(paths, args.service, text=args.text, op=args.op, full=args.full))
        return 0
    if args.action == "coverage":
        config.service(args.service)
        emit(surface.coverage(paths, args.service, Catalog(paths, config).entries))
        return 0
    service = config.service(args.service)
    emit(surface.fetch(paths, config, service, source=args.source, save=args.action == "fetch"))
    return 0


def cmd_catalog(args) -> int:
    paths, config = _context(args)
    catalog = Catalog(paths, config)
    action = args.action
    if action == "add":
        result = catalog.add(_json_input(args, paths.root))
        emit(result)
        return 1 if result["rejected"] else 0
    if action == "list":
        entries = catalog.select(
            status=args.status, level=args.level, service=args.service, area=args.area, priority=args.priority,
            tag=args.tag,
        )
        shown = entries[: args.limit]
        result = {"count": len(entries), "entries": shown if args.full else [_brief(e) for e in shown]}
        if len(entries) > len(shown):
            result["not_shown"] = len(entries) - len(shown)
        emit(result)
        return 0
    if action == "show":
        emit([catalog.get(entry_id) for entry_id in args.ids])
        return 0
    if action == "next":
        batch = args.batch or config["limits"]["batch"]
        filters = {"level": args.level, "service": args.service, "area": args.area, "priority": args.priority, "tag": args.tag}
        entries = catalog.next(batch, offset=args.offset, **filters)
        result = {
            "planned_left": len(catalog.select(status=["planned"], **filters)),
            "batch": [{**entry, "suite": _suite_for(config, entry)} for entry in entries],
            "then": "write each test and try it (`qa.py run <suite> --no-record -- <the test>`), "
                    "`qa.py catalog link <id> --test path::name`, run the suite, `qa.py guard`, commit; "
                    "then ask for the next batch",
        }
        if args.offset:
            result["skipped"] = args.offset
        emit(result)
        return 0
    if action == "update":
        emit(catalog.update(args.id, _json_input(args, paths.root)))
        return 0
    if action == "link":
        emit(_brief(catalog.link(args.id, args.test, replace=args.replace, verify=not args.no_verify)))
        return 0
    if action == "status":
        entry = catalog.set_status(
            args.id, args.status, note=args.note, tests=args.test, until=args.until, report=args.report,
            verify=not args.no_verify, tags=args.tag,
        )
        emit(_brief(entry))
        return 0
    if action == "remove":
        for entry_id in args.ids:
            catalog.remove(entry_id)
        emit({"removed": args.ids})
        return 0
    if action == "stats":
        emit(catalog.stats())
        return 0
    result = catalog.validate()
    emit(result)
    return 0 if result["ok"] else 1


def cmd_run(args) -> int:
    paths, config = _context(args)
    summary, code = run_suite(
        paths, config, args.suite, extra=list(args.extra or []), times=args.times, rerun=not args.no_rerun,
        setup=args.setup, health=not args.no_health, record=not args.no_record,
    )
    emit(summary)
    return code


def cmd_runs(args) -> int:
    paths, _ = _context(args, need_config=False)
    if args.run:
        emit(show_run(paths, args.run))
    else:
        emit(recent_runs(paths, suite=args.suite, last=args.last))
    return 0


def cmd_flaky(args) -> int:
    paths, _ = _context(args, need_config=False)
    emit(Ledger(paths).unstable(args.suite))
    return 0


def cmd_changes(args) -> int:
    paths, config = _context(args)
    if args.mark_synced:
        emit(changes.mark(paths, config, names=args.service, to=args.to))
    else:
        emit(changes.report(paths, config, names=args.service, fetch=args.fetch, to=args.to, max_files=args.max_files))
    return 0


def cmd_worktree(args) -> int:
    paths, config = _context(args)
    if args.action == "status":
        emit(worktree.status(paths, config))
        return 0
    if not args.service:
        raise QAError("--service is required")
    service = config.service(args.service)
    if args.action == "open":
        emit(worktree.open_worktree(paths, config, service, label=args.name))
    elif args.action == "update":
        emit(worktree.update_worktree(paths, config, service))
    else:
        emit(worktree.close_worktree(paths, config, service, force=args.force))
    return 0


def cmd_guard(args) -> int:
    paths, config = _context(args)
    if args.accept_checkouts:
        if len((args.note or "").strip()) < 12:
            raise QAError("--accept-checkouts needs --note: which files changed, whose changes they are and how you know")
        # The report lists it: the person sees which changes were taken to be theirs.
        status.set_progress(paths, decision="changes outside the tests were taken to be the person's own: " + args.note.strip())
    result, code = guard.run(paths, config, since=args.since, everything=args.all, accept_checkouts=args.accept_checkouts)
    emit(result)
    return code


def cmd_progress(args) -> int:
    paths, _ = _context(args, need_config=False)
    if args.phase is None and args.step is None and args.note is None and args.decision is None:
        progress = status.load_progress(paths)
        emit({
            **{key: progress.get(key) for key in ("phase", "step", "note", "updated")},
            "unfinished_before_this": progress["interrupted"],
            "decisions_for_the_next_report": status.fresh_decisions(progress),
            "earlier_steps": progress["log"][-10:],
        })
    else:
        emit(status.set_progress(paths, phase=args.phase, step=args.step, note=args.note, decision=args.decision))
    return 0


def cmd_report(args) -> int:
    paths, config = _context(args)
    notes = _input_file(args.notes, paths.root) if args.notes else None
    emit(status.write_report(paths, config, args.name, title=args.title, notes_file=notes))
    return 0


def cmd_score(args) -> int:
    action = args.action
    if action == "extract":
        emit(score.extract(args.reference, args.generated, args.out, include=args.include or (), exclude=args.exclude or (), top=args.top))
        return 0
    if action == "next":
        emit(score.next_batch(args.out, args.batch))
        return 0
    if action == "judge":
        result = score.judge(args.out, _json_input(args))
        emit(result)
        return 1 if result["rejected"] else 0
    if action == "status":
        result = score.tally(args.out, allow_incomplete=True)
        emit({key: value for key, value in result.items() if key not in ("rows", "areas")})
        return 0
    summary, code = score.report(
        args.out, threshold=args.threshold, sample=args.sample, seed=args.seed, allow_incomplete=args.allow_incomplete,
    )
    emit(summary)
    return code


def cmd_version(args) -> int:
    print(__version__)
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qa.py", description="Helper for the QA agent kit.")
    parser.add_argument("--root", help="the repository the kit is installed in (default: found from this file or the current folder)")
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    p = sub.add_parser("status", help="where the work stands and what to do next; read it at the start of a session")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="check the project map, the checkouts and the running services")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("init", help="write the first project map (.qa-agent/project.json)")
    p.add_argument("--name", required=True)
    p.add_argument("--about", default="")
    p.add_argument("--placement", required=True, choices=PLACEMENTS)
    p.add_argument("--service", action="append", required=True, metavar="name=…,repo=…,url=…,health=…,openapi=…,branch=…")
    p.add_argument("--env-name", default="local")
    p.add_argument("--writes", default="own-data", choices=WRITES)
    p.add_argument("--off-limits", action="append", metavar="PATH", help="a folder the agent must never read")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("config", help="validate or print the project map; record what the person agreed to")
    p.add_argument("action", choices=("validate", "show", "agree", "agreed"))
    p.add_argument("--note", help="agree: what the person agreed to (the plan, the stack, the limits)")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("branch", help="put this repository on a QA branch: the unmerged one, or a new one")
    p.add_argument("--name", required=True, help="what the work is: baseline, sync, heal")
    p.add_argument("--fresh", action="store_true", help="start a new branch from here although earlier QA work was never merged")
    p.set_defaults(func=cmd_branch)

    p = sub.add_parser("library", help="the team's own rules and skills")
    p.add_argument("action", choices=("scan", "install", "status"))
    p.add_argument("--path")
    p.add_argument("--rules", nargs="+", metavar="GLOB")
    p.add_argument("--skills", nargs="+", metavar="GLOB")
    p.add_argument("--exclude", nargs="+", metavar="GLOB")
    p.add_argument("--select", nargs="+", metavar="NAME", help="install only these")
    p.add_argument("--mirror-rules", nargs="+", metavar="DIR", help="also copy rules to a folder an assistant loads by itself")
    p.add_argument("--mirror-skills", nargs="+", metavar="DIR")
    p.add_argument("--refresh", action="store_true", help="install again what is installed, from the library as it is now")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_library)

    p = sub.add_parser("rules", help="what to read before a piece of work")
    p.add_argument("--level", choices=LEVELS)
    p.add_argument("--task", choices=TASKS)
    p.add_argument("--suite")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("surface", help="a service's API as its OpenAPI document describes it")
    p.add_argument("action", choices=("fetch", "diff", "show", "coverage"))
    p.add_argument("--service", required=True)
    p.add_argument("--source", help="read the document from this URL or file instead")
    p.add_argument("--text", help="show: only operations mentioning this")
    p.add_argument("--op", help="show: one operation in full, e.g. 'POST /orders'")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_surface)

    p = sub.add_parser("catalog", help="the behaviours to check, and the tests that check them")
    cat = p.add_subparsers(dest="action", metavar="action")
    cat.required = True
    c = cat.add_parser("add", help="add planned behaviours from JSON")
    c.add_argument("--json")
    c.add_argument("--file")
    c = cat.add_parser("list")
    c.add_argument("--status", nargs="+", choices=STATUSES)
    c.add_argument("--level", nargs="+", choices=LEVELS)
    c.add_argument("--service", nargs="+")
    c.add_argument("--area", nargs="+")
    c.add_argument("--priority", nargs="+", type=int, choices=(1, 2, 3))
    c.add_argument("--tag", nargs="+")
    c.add_argument("--full", action="store_true")
    c.add_argument("--limit", type=int, default=200)
    c = cat.add_parser("show")
    c.add_argument("ids", nargs="+", metavar="ID")
    c = cat.add_parser("next", help="the next planned behaviours to write tests for")
    c.add_argument("--batch", type=int)
    c.add_argument("--level", nargs="+", choices=LEVELS)
    c.add_argument("--service", nargs="+")
    c.add_argument("--area", nargs="+")
    c.add_argument("--priority", nargs="+", type=int, choices=(1, 2, 3))
    c.add_argument("--tag", nargs="+")
    c.add_argument("--offset", type=int, default=0, help="skip this many: a batch for a second writer")
    c = cat.add_parser("update")
    c.add_argument("id")
    c.add_argument("--json")
    c.add_argument("--file")
    c = cat.add_parser("link", help="record the test that checks a behaviour")
    c.add_argument("id")
    c.add_argument("--test", nargs="+", required=True, metavar="path::name")
    c.add_argument("--replace", action="store_true")
    c.add_argument("--no-verify", action="store_true")
    c = cat.add_parser("status", help="record a decision: " + ", ".join(AGENT_STATUSES))
    c.add_argument("id")
    c.add_argument("status", choices=AGENT_STATUSES)
    c.add_argument("--note")
    c.add_argument("--test", nargs="+", metavar="path::name")
    c.add_argument("--until", metavar="YYYY-MM-DD")
    c.add_argument("--report", help="defect: path of the written defect report")
    c.add_argument("--tag", nargs="+", help="add these tags, e.g. needs-decision")
    c.add_argument("--no-verify", action="store_true")
    c = cat.add_parser("remove")
    c.add_argument("ids", nargs="+", metavar="ID")
    cat.add_parser("stats")
    cat.add_parser("validate")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("run", help="run a suite and record every test's outcome")
    p.add_argument("suite")
    p.add_argument("--times", type=int, default=1, help="run the selection this many times, to look for flakiness")
    p.add_argument("--no-rerun", action="store_true", help="do not re-run failures to tell flaky from failing")
    p.add_argument("--setup", action="store_true", help="run the suite's setup commands first")
    p.add_argument("--no-health", action="store_true", help="do not check that the services it needs answer")
    p.add_argument("--no-record", action="store_true", help="a trial while writing: report the outcome, record nothing")
    p.epilog = "Everything after `--` goes to the test runner: qa.py run api -- tests/api/test_orders.py -k empty"
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("runs", help="recent runs, or one run in full")
    p.add_argument("run", nargs="?")
    p.add_argument("--suite")
    p.add_argument("--last", type=int, default=5)
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("flaky", help="tests whose outcome keeps changing")
    p.add_argument("--suite")
    p.set_defaults(func=cmd_flaky)

    p = sub.add_parser("changes", help="what was merged since the last sync, and the behaviours it touches")
    p.add_argument("--service", nargs="+")
    p.add_argument("--fetch", action="store_true", help="git fetch first, to report what the remote has that the checkout lacks")
    p.add_argument("--to", metavar="REF", help="compare up to this commit instead of what is checked out")
    p.add_argument("--mark-synced", action="store_true", help="record that the tests are up to date with the code as it is now")
    p.add_argument("--max-files", type=int, default=150)
    p.set_defaults(func=cmd_changes)

    p = sub.add_parser("worktree", help="where tests for another service's own repository are written")
    p.add_argument("action", choices=("open", "update", "close", "status"))
    p.add_argument("--service")
    p.add_argument("--name", default="tests")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_worktree)

    p = sub.add_parser("guard", help="the check that must pass before every commit")
    p.add_argument("--since", metavar="REF", help="also check what was committed since this commit")
    p.add_argument("--all", action="store_true", help="also read every test and state file, changed or not")
    p.add_argument("--accept-checkouts", action="store_true",
                   help="files outside the tests changed and the change is the person's: record their state anew")
    p.add_argument("--note", help="with --accept-checkouts: which files, whose changes, how you know")
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser("progress", help="record where the work is, for the next session; alone, print it")
    p.add_argument("--phase", choices=status.PHASES)
    p.add_argument("--step", help="the playbook step that is next: " + "; ".join(
        f"{phase}: {', '.join(key for key, _ in steps)}" for phase, steps in status.STEPS.items() if steps))
    p.add_argument("--note", help="one line for whoever continues")
    p.add_argument("--decision", help="something you decided without asking, and why; the next report lists it")
    p.set_defaults(func=cmd_progress)

    p = sub.add_parser("report", help="write a dated report to .qa-agent/reports/")
    p.add_argument("--name", required=True)
    p.add_argument("--title")
    p.add_argument("--notes", metavar="FILE", help="markdown to place at the top")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("score", help="how much of a reference test suite the generated tests replicate")
    sc = p.add_subparsers(dest="action", metavar="action")
    sc.required = True
    c = sc.add_parser("extract", help="read the tests of both sides and propose candidates")
    c.add_argument("--reference", nargs="+", required=True, metavar="DIR")
    c.add_argument("--generated", nargs="+", required=True, metavar="DIR")
    c.add_argument("--out", required=True, metavar="DIR", help="a folder outside both")
    c.add_argument("--include", nargs="+", metavar="GLOB")
    c.add_argument("--exclude", nargs="+", metavar="GLOB")
    c.add_argument("--top", type=int, default=5)
    c = sc.add_parser("next", help="the next reference tests to judge, with their candidates")
    c.add_argument("--out", required=True)
    c.add_argument("--batch", type=int, default=15)
    c = sc.add_parser("judge", help="record verdicts")
    c.add_argument("--out", required=True)
    c.add_argument("--json")
    c.add_argument("--file")
    c = sc.add_parser("status")
    c.add_argument("--out", required=True)
    c = sc.add_parser("report", help="count, and write report.md")
    c.add_argument("--out", required=True)
    c.add_argument("--threshold", type=float, default=80.0)
    c.add_argument("--sample", type=int, default=15)
    c.add_argument("--seed", type=int)
    c.add_argument("--allow-incomplete", action="store_true")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("version")
    p.set_defaults(func=cmd_version)
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:  # what follows belongs to the test runner
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1 :]
    parser = build_parser()
    args = parser.parse_args(argv)
    args.extra = extra
    try:
        if extra and args.command != "run":
            raise QAError("Only `run` takes arguments after `--`")
        return args.func(args)
    except QAError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
