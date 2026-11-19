"""How much of an existing (reference) test suite the generated tests replicate.

A reference test counts as replicated when a generated test checks the same
behaviour: the same endpoint and case, or the same journey and outcome. That is
a judgement, so the work is split: this module extracts the tests of both
sides, proposes candidates by similarity, keeps the verdicts a judge records,
and does the counting. The judge is a separate session from the one that
generated the tests, and everything written here stays outside the generated
repository, because the outputs name the reference tests.
"""

from __future__ import annotations

import math
import random
import time
from collections import Counter
from pathlib import Path

from . import testscan
from .matching import Case, match_cases
from .util import QAError, clip, now_iso, read_json, words, write_json

VERDICTS = ("covered", "partial", "missing", "excluded")
LIKELY, POSSIBLE = 0.55, 0.35
NAME_WORDS = 4  # shorter names (`test_health`) coincide by themselves
DEFAULT_EXCLUDE = [".qa-agent", "fixtures", "__snapshots__", "*.d.ts"]


# ---------------------------------------------------------------- where things are


def _inside(inner: Path, outer: Path) -> bool:
    try:
        inner.resolve().relative_to(outer.resolve())
        return True
    except ValueError:
        return False


def check_layout(references: list, generated: list, out: Path) -> None:
    for root in references + generated:
        if not root.is_dir():
            raise QAError(f"{root} is not a folder")
    for ref in references:
        for gen in generated:
            if _inside(ref, gen) or _inside(gen, ref):
                raise QAError(f"{ref} and {gen} overlap: the reference and the generated tests must be separate folders")
    for root in references + generated:
        if _inside(out, root):
            raise QAError(
                f"--out {out} is inside {root}. The outputs name the reference tests, so they are kept outside "
                "both repositories: the agent that generates tests must never read them."
            )


def _load(out: Path, name: str):
    data = read_json(out / name)
    if data is None:
        raise QAError(f"{out / name} does not exist. Run `qa.py score extract` first.")
    return data


# ---------------------------------------------------------------- extraction


def _last_outcomes(root: Path) -> list:
    """Pseudo test cases from the generated repository's run history, when it has one."""
    ledger = read_json(root / ".qa-agent" / "state" / "ledger.json")
    if not ledger:
        return []
    cases = []
    for suite, entry in (ledger.get("suites") or {}).items():
        for key, history in (entry.get("tests") or {}).items():
            classname, sep, name = key.partition("::")
            if not sep:
                classname, name = "", key
            last = history[-1:] if history else ""
            outcome = {"p": "passed", "x": "flaky", "f": "failed", "s": "skipped"}.get(last)
            if outcome:
                cases.append(Case(suite, classname, name, "", outcome, "", ""))
    return cases


def _outcome(test: dict, cases: list):
    if not cases:
        return None
    matched = {case.outcome for case in match_cases(test["ref"], cases)}
    for outcome in ("failed", "flaky", "passed", "skipped"):
        if outcome in matched:
            return outcome
    return "not run"


def generated_plan(roots: list) -> list:
    """(folder, the test files that count there, or None for all of them) for the generated side.

    A repository the kit works in records in its behaviour catalog which tests it wrote. Where those sit among
    tests other people wrote (inside a service's repository), only the recorded files count as generated, and
    the folders they are in are found from the project map, so naming the kit's repository is enough.
    """
    plan: dict = {}
    for root in roots:
        plan.setdefault(root, None)
        catalog = read_json(root / ".qa-agent" / "state" / "catalog.json")
        project = read_json(root / ".qa-agent" / "project.json")
        if not isinstance(catalog, dict) or not isinstance(project, dict):
            continue
        linked: dict = {}
        for entry in catalog.get("entries") or []:
            # 'covered' points at a test written for something else, which may be the service's developers' own.
            if entry.get("status") in ("retired", "covered"):
                continue
            for ref in entry.get("tests") or []:
                linked.setdefault(entry.get("repo") or ".", set()).add(str(ref).split("::", 1)[0])
        if project.get("placement") == "service":
            plan[root] = linked.get(".", set())
        for service in project.get("services") or []:
            name = service.get("name")
            if name not in linked:
                continue
            folder = root / ".qa-agent" / "worktrees" / name
            if not folder.is_dir():
                if not service.get("repo"):
                    continue
                folder = (root / service["repo"]).resolve()
            if folder.is_dir() and folder != root:
                plan[folder] = linked[name]
    return list(plan.items())


def _extract_side(plan: list, prefix: str, include, exclude, cases=()) -> dict:
    tests, files, unsupported, folders = [], 0, [], []
    for index, (root, only) in enumerate(plan):
        found = testscan.scan_tree(root, include, list(exclude) + DEFAULT_EXCLUDE)
        kept = [test for test in found["tests"] if only is None or test["file"] in only]
        seen = {test["file"] for test in kept}
        files += len(seen)
        row = {"folder": str(root), "tests": len(kept), "files": len(seen)}
        if only is not None:
            row["limited_to"] = "the test files its behaviour catalog links"
            absent = sorted(only - {test["file"] for test in found["tests"]})
            if absent:
                row["linked_files_not_found"] = len(absent)
                row["first_not_found"] = absent[:5]
        else:
            unsupported += [f"{root}/{rel}" for rel in found["unsupported"]]
        folders.append(row)
        for test in kept:
            test["root"] = index
            if prefix == "G":
                test["last_run"] = _outcome(test, cases)
            tests.append(test)
    for number, test in enumerate(tests, 1):
        test["id"] = f"{prefix}-{number:04d}"
    return {"roots": [str(root) for root, _ in plan], "folders": folders, "extracted_at": now_iso(), "files": files,
            "unsupported": unsupported, "tests": tests}


def extract(references, generated, out, include=(), exclude=(), top: int = 5) -> dict:
    references = [Path(p).expanduser().resolve() for p in references]
    generated = [Path(p).expanduser().resolve() for p in generated]
    out = Path(out).expanduser().resolve()
    check_layout(references, generated, out)
    plan = generated_plan(generated)
    check_layout(references, [root for root, _ in plan], out)
    out.mkdir(parents=True, exist_ok=True)
    cases = [case for root in generated for case in _last_outcomes(root)]
    ref = _extract_side([(root, None) for root in references], "R", include, exclude)
    gen = _extract_side(plan, "G", include, exclude, cases)
    if not ref["tests"]:
        raise QAError(
            "No tests were found in the reference. This scorer reads Python (test_*.py) and JavaScript/TypeScript "
            "(*.spec.*, *.test.*, *.cy.*) test files; pass --include GLOB for other file names."
        )
    write_json(out / "reference.json", ref)
    write_json(out / "generated.json", gen)
    ranked = rank(ref["tests"], gen["tests"], top)
    write_json(out / "candidates.json", ranked)
    mapping_path = out / "mapping.json"
    kept = None
    if mapping_path.exists():
        # Identifiers are positions in the extraction, so earlier verdicts no longer point at the same tests.
        kept = out / f"mapping-{time.strftime('%Y%m%dT%H%M%S')}.json"
        mapping_path.rename(kept)
    write_json(mapping_path, {"version": 1, "verdicts": {}})
    estimate = _estimate(ref["tests"], ranked)
    result = {
        "reference": {"tests": len(ref["tests"]), "files": ref["files"], "unsupported_files": len(ref["unsupported"])},
        "generated": {"tests": len(gen["tests"]), "files": gen["files"], "unsupported_files": len(gen["unsupported"]),
                      "folders": gen["folders"]},
        "similarity_estimate": estimate,
        "out": str(out),
        "next": "judge every reference test: `qa.py score next --out DIR`, then `qa.py score judge`",
    }
    if kept:
        result["earlier_verdicts_set_aside"] = str(kept)
    if ref["unsupported"] or gen["unsupported"]:
        result["note"] = "test files in languages this scorer does not read are listed under 'unsupported' in the json files"
    return result


# ---------------------------------------------------------------- similarity


def _all_words(test: dict) -> set:
    return set(test["title_words"]) | set(test["body_words"])


def _signals(test: dict) -> set:
    return set(test["calls"]) | {f"VISIT {path}" for path in test["visits"]}


def _idf(tests: list) -> dict:
    df = Counter()
    for test in tests:
        df.update(_all_words(test))
    total = max(1, len(tests))
    return {word: math.log(1 + total / count) for word, count in df.items()}


def _weighted_jaccard(a: set, b: set, idf: dict) -> float:
    if not a or not b:
        return 0.0
    union = sum(idf.get(word, 1.0) for word in a | b)
    return sum(idf.get(word, 1.0) for word in a & b) / union if union else 0.0


def _signal_score(ref_signals: set, gen_signals: set):
    if not ref_signals:
        return None, []
    gen_paths = {signal.split(" ", 1)[1] for signal in gen_signals}
    total, same = 0.0, []
    for signal in sorted(ref_signals):
        method, path = signal.split(" ", 1)
        if signal in gen_signals or f"* {path}" in gen_signals or (method == "*" and path in gen_paths):
            total += 1
            same.append(signal)
        elif path in gen_paths:
            total += 0.4
            same.append(f"{path} (other method)")
    return total / len(ref_signals), same


def similarity(ref: dict, gen: dict, idf: dict):
    """A 0..1 likeness of two tests and the reasons for it. A ranking aid, not a verdict."""
    title = _weighted_jaccard(set(ref["title_words"]), set(gen["title_words"]), idf)
    body = _weighted_jaccard(_all_words(ref), _all_words(gen), idf)
    parts = [(title, 0.30), (body, 0.25)]
    signal, same = _signal_score(_signals(ref), _signals(gen))
    if signal is not None:
        parts.append((signal, 0.35))
    ref_status, gen_status = set(ref["statuses"]), set(gen["statuses"])
    if ref_status and gen_status:
        parts.append((len(ref_status & gen_status) / len(ref_status | gen_status), 0.10))
        same += [f"status {code}" for code in sorted(ref_status & gen_status)]
    score = sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts)
    shared = sorted(set(ref["title_words"]) & set(gen["title_words"]), key=lambda w: -idf.get(w, 0))[:4]
    if shared:
        same.append("words: " + " ".join(shared))
    return round(score, 3), same


def rank(ref_tests: list, gen_tests: list, top: int = 5) -> dict:
    """For each reference test, the generated tests most like it."""
    idf = _idf(ref_tests + gen_tests)
    postings: dict = {}
    for index, test in enumerate(gen_tests):
        for key in _all_words(test) | _signals(test):
            postings.setdefault(key, []).append(index)
    busy = max(25, int(0.3 * len(gen_tests)))  # words nearly every test has say nothing
    ranked = {}
    for ref in ref_tests:
        weight: Counter = Counter()
        for key in _all_words(ref) | _signals(ref):
            hits = postings.get(key)
            if not hits or len(hits) > busy:
                continue
            gain = 3.0 if " " in key else idf.get(key, 1.0)
            for index in hits:
                weight[index] += gain
        scored = []
        for index, _ in weight.most_common(40):
            gen = gen_tests[index]
            score, same = similarity(ref, gen, idf)
            if score > 0.05:
                scored.append({"id": gen["id"], "score": score, "same": same})
        scored.sort(key=lambda item: (-item["score"], item["id"]))
        ranked[ref["id"]] = scored[:top]
    return ranked


def _estimate(ref_tests: list, ranked: dict) -> dict:
    total = len(ref_tests)
    best = []
    for test in ref_tests:
        found = ranked.get(test["id"]) or []
        best.append(found[0]["score"] if found else 0.0)
    likely = sum(1 for score in best if score >= LIKELY)
    possible = sum(1 for score in best if score >= POSSIBLE)
    return {
        "likely_percent": round(100 * likely / total, 1) if total else 0.0,
        "possible_percent": round(100 * possible / total, 1) if total else 0.0,
        "note": "from name and call similarity alone; the judged figure is the one that counts",
    }


# ---------------------------------------------------------------- judging


def _brief(test: dict, roots: list) -> dict:
    row = {"id": test["id"], "at": f"{test['file']}:{test['line']}", "name": clip(test["name"], 160)}
    if len(roots) > 1:
        row["root"] = roots[test["root"]]
    for key in ("calls", "visits", "statuses"):
        if test[key]:
            row[key] = test[key]
    if test["skipped"]:
        row["skipped"] = True
    if test.get("last_run") in ("failed", "not run", "skipped"):
        row["last_run"] = test["last_run"]
    return row


def next_batch(out, batch: int = 15) -> dict:
    out = Path(out).expanduser().resolve()
    ref, gen = _load(out, "reference.json"), _load(out, "generated.json")
    ranked, mapping = _load(out, "candidates.json"), _load(out, "mapping.json")
    by_id = {test["id"]: test for test in gen["tests"]}
    waiting = [test for test in ref["tests"] if test["id"] not in mapping["verdicts"]]
    rows = []
    for test in waiting[:batch]:
        row = _brief(test, ref["roots"])
        row["candidates"] = [
            {**_brief(by_id[c["id"]], gen["roots"]), "score": c["score"], "same": c["same"]}
            for c in ranked.get(test["id"], []) if c["id"] in by_id
        ]
        rows.append(row)
    return {
        "judged": len(mapping["verdicts"]), "waiting": len(waiting),
        "reference_roots": ref["roots"], "generated_roots": gen["roots"],
        "tests": rows,
    }


def judge(out, verdicts) -> dict:
    out = Path(out).expanduser().resolve()
    ref, gen = _load(out, "reference.json"), _load(out, "generated.json")
    mapping = _load(out, "mapping.json")
    if isinstance(verdicts, dict):
        verdicts = [verdicts]
    if not isinstance(verdicts, list):
        raise QAError("Give one verdict (an object) or a list of them")
    ref_ids = {test["id"] for test in ref["tests"]}
    gen_by_id = {test["id"]: test for test in gen["tests"]}
    accepted, replaced, rejected = 0, 0, []
    for index, item in enumerate(verdicts):
        problems = []
        if not isinstance(item, dict):
            rejected.append({"index": index, "problems": ["must be an object"]})
            continue
        ref_id, verdict = item.get("ref"), item.get("verdict")
        by = item.get("by") or []
        why = str(item.get("why") or "").strip()
        if ref_id not in ref_ids:
            problems.append(f"'{ref_id}' is not a reference test")
        if verdict not in VERDICTS:
            problems.append(f"verdict must be one of {', '.join(VERDICTS)}")
        if not isinstance(by, list) or any(g not in gen_by_id for g in by):
            problems.append("'by' must list generated test ids (G-0001)")
            by = []
        if verdict in ("covered", "partial") and not by:
            problems.append(f"'{verdict}' needs 'by': the generated test(s) that check it")
        if verdict in ("missing", "excluded") and by:
            problems.append(f"'{verdict}' takes no 'by'")
        if verdict == "covered" and by and all(gen_by_id[g]["skipped"] for g in by):
            problems.append("a switched-off test checks nothing: at most 'partial'")
        if len(why) < 8:
            problems.append("'why' is required: what both tests check, or what is absent")
        if problems:
            rejected.append({"index": index, "ref": ref_id, "problems": problems})
            continue
        if ref_id in mapping["verdicts"]:
            replaced += 1
        mapping["verdicts"][ref_id] = {"verdict": verdict, "by": by, "why": clip(why, 300)}
        accepted += 1
    if accepted:
        write_json(out / "mapping.json", mapping)
    return {
        "accepted": accepted, "replaced": replaced, "rejected": rejected,
        "judged": len(mapping["verdicts"]), "waiting": len(ref_ids) - len(mapping["verdicts"]),
    }


# ---------------------------------------------------------------- counting


def _area(path: str) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:3]) if parts else "(top folder)"


def tally(out, threshold: float = 80.0, allow_incomplete: bool = False) -> dict:
    out = Path(out).expanduser().resolve()
    ref, gen = _load(out, "reference.json"), _load(out, "generated.json")
    ranked, mapping = _load(out, "candidates.json"), _load(out, "mapping.json")
    verdicts = mapping["verdicts"]
    gen_by_id = {test["id"]: test for test in gen["tests"]}
    counts = Counter()
    areas: dict = {}
    rows = []
    for test in ref["tests"]:
        entry = verdicts.get(test["id"])
        verdict = entry["verdict"] if entry else "not judged"
        counts[verdict] += 1
        area = areas.setdefault(_area(test["file"]), Counter())
        area[verdict] += 1
        best = ranked.get(test["id"]) or []
        rows.append({
            "ref": test["id"], "at": f"{test['file']}:{test['line']}", "name": test["name"], "verdict": verdict,
            "by": entry["by"] if entry else [], "why": entry["why"] if entry else "",
            "best_score": best[0]["score"] if best else 0.0,
        })
    total = len(ref["tests"])
    counted = total - counts["excluded"]
    waiting = counts["not judged"]
    if waiting and not allow_incomplete:
        raise QAError(
            f"{waiting} reference test(s) have no verdict yet. Judge them (`qa.py score next`), "
            "or pass --allow-incomplete to count them as missing."
        )

    def percent(value: float) -> float:
        return round(100 * value / counted, 1) if counted else 0.0

    passing = 0
    have_runs = any(test.get("last_run") for test in gen["tests"])
    for row in rows:
        if row["verdict"] == "covered" and any(gen_by_id[g].get("last_run") in ("passed", "flaky") for g in row["by"]):
            passing += 1
    used = {g for row in rows for g in row["by"]}
    # Suites written apart seldom give a test the same name, so names in common hint that one was read for the other.
    named: dict = {}
    for test in ref["tests"]:
        key = tuple(words(test["name"]))
        if len(key) >= NAME_WORDS:
            named.setdefault(key, test)
    twins = [(named[key], test) for test in gen["tests"] for key in [tuple(words(test["name"]))] if key in named]
    strict = percent(counts["covered"])
    result = {
        "reference_tests": total,
        "counted": counted,
        "covered": counts["covered"], "partial": counts["partial"], "missing": counts["missing"],
        "excluded": counts["excluded"], "not_judged": waiting,
        "replicated_percent": strict,
        "with_partial_as_half_percent": percent(counts["covered"] + 0.5 * counts["partial"]),
        "threshold": threshold,
        "met": strict >= threshold,
        "similarity_estimate": _estimate(ref["tests"], ranked),
        "generated_tests": len(gen["tests"]),
        "generated_tests_matching_no_reference_test": len(gen["tests"]) - len(used),
        "generated_tests_named_like_a_reference_test": len(twins),
        "same_names": [
            {"ref": r["id"], "ref_at": f"{r['file']}:{r['line']}", "gen": g["id"], "gen_at": f"{g['file']}:{g['line']}",
             "name": clip(g["name"], 120)}
            for r, g in twins[:50]
        ],
        "areas": {
            name: {"tests": sum(area.values()), "covered": area["covered"], "partial": area["partial"],
                   "missing": area["missing"] + area["not judged"], "excluded": area["excluded"]}
            for name, area in sorted(areas.items())
        },
        "rows": rows,
    }
    if have_runs:
        result["covered_by_a_passing_test_percent"] = percent(passing)
    return result


def _line(row: dict, gen_by_id: dict) -> str:
    text = f"- {row['ref']} `{row['at']}` {clip(row['name'], 110)}"
    for gen_id in row["by"][:3]:
        gen = gen_by_id[gen_id]
        text += f"\n  - {gen_id} `{gen['file']}:{gen['line']}` {clip(gen['name'], 110)}"
    if row["why"]:
        text += f"\n  - why: {row['why']}"
    return text


def report(out, threshold: float = 80.0, sample: int = 15, seed=None, allow_incomplete: bool = False):
    """Write report.md and result.json. Returns (summary, exit code): 0 when the threshold is met."""
    out = Path(out).expanduser().resolve()
    result = tally(out, threshold, allow_incomplete)
    ref, gen = _load(out, "reference.json"), _load(out, "generated.json")
    gen_by_id = {test["id"]: test for test in gen["tests"]}
    rows = result["rows"]
    seed = int(time.time()) if seed is None else seed
    covered = [row for row in rows if row["verdict"] == "covered"]
    chosen = random.Random(seed).sample(covered, min(sample, len(covered)))
    doubtful_covered = sorted((r for r in covered if r["best_score"] < 0.2), key=lambda r: r["best_score"])[:20]
    doubtful_missing = sorted(
        (r for r in rows if r["verdict"] in ("missing", "not judged") and r["best_score"] >= LIKELY),
        key=lambda r: -r["best_score"],
    )[:20]

    verdict_word = "met" if result["met"] else "NOT met"
    lines = [
        f"# Replication score, {now_iso()}",
        "",
        f"- Reference: {', '.join(ref['roots'])} ({result['reference_tests']} tests in {ref['files']} files)",
        f"- Generated: {', '.join(gen['roots'])} ({result['generated_tests']} tests in {gen['files']} files)",
        "",
        f"**Replicated: {result['replicated_percent']}% ({result['covered']} of {result['counted']} counted tests). "
        f"Target {threshold:g}%: {verdict_word}.**",
        "",
        f"- Counting a partial match as half: {result['with_partial_as_half_percent']}%",
    ]
    if "covered_by_a_passing_test_percent" in result:
        lines.append(
            f"- Covered by a generated test that passed in its last recorded run: {result['covered_by_a_passing_test_percent']}%"
        )
    estimate = result["similarity_estimate"]
    lines += [
        f"- Similarity estimate, no judgement involved: likely {estimate['likely_percent']}%, "
        f"possible {estimate['possible_percent']}%",
        f"- Generated tests that match no reference test: {result['generated_tests_matching_no_reference_test']}",
        "",
        "| Verdict | Tests |",
        "| --- | --- |",
        f"| covered | {result['covered']} |",
        f"| partial | {result['partial']} |",
        f"| missing | {result['missing']} |",
        f"| excluded from the count | {result['excluded']} |",
    ]
    if result["not_judged"]:
        lines.append(f"| not judged (counted as missing) | {result['not_judged']} |")
    if ref["unsupported"]:
        lines += ["", f"{len(ref['unsupported'])} reference test file(s) are in a language this scorer does not read; "
                  "they are not in any figure above (listed in reference.json)."]
    lines += ["", "## By area of the reference suite", "", "| Area | Tests | Covered | Partial | Missing | Excluded | Replicated |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, area in result["areas"].items():
        counted = area["tests"] - area["excluded"]
        share = f"{round(100 * area['covered'] / counted)}%" if counted else "n/a"
        lines.append(f"| {name} | {area['tests']} | {area['covered']} | {area['partial']} | {area['missing']} | {area['excluded']} | {share} |")
    lines += ["", f"## Check these by hand ({len(chosen)} covered tests, picked at random, seed {seed})", "",
              "Open both tests of each pair. If more than one or two are not the same check, the judging was too generous.", ""]
    lines += [_line(row, gen_by_id) for row in chosen] or ["Nothing was judged covered."]
    if result["same_names"]:
        count = result["generated_tests_named_like_a_reference_test"]
        lines += ["", f"## Named exactly like a reference test ({count})", "",
                  "Two suites written apart seldom give a test the same name. One or two can coincide. Many mean the "
                  "generated tests were written with the reference suite in view, and then this score says nothing.", ""]
        lines += [f"- {pair['gen']} `{pair['gen_at']}` = {pair['ref']} `{pair['ref_at']}`: {pair['name']}" for pair in result["same_names"]]
        if count > len(result["same_names"]):
            lines.append(f"- … and {count - len(result['same_names'])} more")
    if doubtful_covered or doubtful_missing:
        lines += ["", "## Where the verdict and the similarity disagree", ""]
        if doubtful_covered:
            lines += ["Judged covered although the tests look unlike each other:", ""]
            lines += [_line(row, gen_by_id) for row in doubtful_covered]
        if doubtful_missing:
            lines += ["", "Judged missing although a generated test looks alike:", ""]
            lines += [_line(row, gen_by_id) for row in doubtful_missing]
    for title, name in (("Missing", "missing"), ("Partial", "partial"), ("Excluded from the count", "excluded"),
                        ("Not judged", "not judged")):
        chosen_rows = [row for row in rows if row["verdict"] == name]
        if chosen_rows:
            lines += ["", f"## {title} ({len(chosen_rows)})", ""]
            lines += [_line(row, gen_by_id) for row in chosen_rows[:300]]
            if len(chosen_rows) > 300:
                lines.append(f"- … and {len(chosen_rows) - 300} more, in result.json")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(out / "result.json", {**result, "seed": seed, "written_at": now_iso()})
    summary = {key: value for key, value in result.items() if key not in ("rows", "areas", "same_names")}
    summary["report"] = str(out / "report.md")
    return summary, (0 if result["met"] else 1)
