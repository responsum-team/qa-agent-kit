import pytest

from qa_agent import changes, status
from qa_agent.catalog import Catalog
from qa_agent.matching import Case
from qa_agent.runner import Ledger
from qa_agent.util import QAError, today
from conftest import commit, project, sh, write

BASE = {"service": "orders", "level": "api", "surface": "POST /orders", "expect": "x", "intent": "x"}


def test_before_setup_it_points_at_the_setup_playbook(host):
    state = status.build(host.paths)
    assert state["configured"] is False
    text = status.render(state)
    assert "not set up" in text and "playbooks/setup.md" in text


def test_a_broken_project_map_is_reported_not_raised(host):
    host.paths.config.write_text("{}", encoding="utf-8")
    text = status.render(status.build(host.paths))
    assert "project.json has problems" in text and "qa.py config validate" in text


def test_progress_is_kept_for_the_next_session(host):
    host.configure(project())
    assert status.load_progress(host.paths)["phase"] is None
    status.set_progress(host.paths, phase="baseline", step="map", note="orders service mapped")
    status.set_progress(host.paths, step="generate", note="api batch 3 done")
    data = status.load_progress(host.paths)
    assert (data["phase"], data["step"], data["note"]) == ("baseline", "generate", "api batch 3 done")
    assert [row["step"] for row in data["log"]] == ["map", "generate"]
    assert status.set_progress(host.paths, step="verify")["note"] == ""       # the note belonged to the step left behind
    status.set_progress(host.paths, phase="idle", note="baseline finished")
    assert status.load_progress(host.paths)["step"] is None                   # nothing is in progress
    assert status.set_progress(host.paths, phase="sync")["step"] == "changes"  # a new phase starts at its first step
    with pytest.raises(QAError, match="--phase must be one of"):
        status.set_progress(host.paths, phase="party")


def test_work_begun_while_other_work_is_unfinished_does_not_erase_it(host):
    """The person asks for a sync in the middle of the baseline: when the sync closes, the baseline is next again."""
    host.configure(project())
    status.set_progress(host.paths, phase="setup", step="handover")
    status.set_progress(host.paths, phase="baseline", step="generate", note="api done; e2e next")
    assert status.load_progress(host.paths)["interrupted"] == []              # setup ends where the baseline begins
    assert status.set_progress(host.paths, phase="sync") == {"phase": "sync", "step": "changes", "note": ""}
    state = status.build(host.paths)
    assert state["unfinished"] == [{"phase": "baseline", "step": "generate"}]
    assert 'Unfinished: the baseline, at "Step 3. Generate".' in status.render(state)
    status.set_progress(host.paths, step="close")
    status.set_progress(host.paths, phase="heal", step="flaky")               # and a heal in the middle of that sync
    assert [row["phase"] for row in status.load_progress(host.paths)["interrupted"]] == ["baseline", "sync"]
    back = status.set_progress(host.paths, phase="idle", note="healed")
    assert (back["phase"], back["step"]) == ("sync", "close") and 'playbooks/sync.md at "Step 5. Close"' in back["resumed"]
    back = status.set_progress(host.paths, phase="idle", note="synced; report written")
    assert (back["phase"], back["step"], back["note"]) == ("baseline", "generate", "synced; report written")
    assert 'playbooks/baseline.md at "Step 3. Generate"' in back["resumed"]
    state = status.build(host.paths)
    assert state["unfinished"] == [] and state["next"].startswith('Continue playbooks/baseline.md at "Step 3. Generate".')
    done = status.set_progress(host.paths, phase="idle", note="baseline finished")
    assert (done["phase"], done["step"]) == ("idle", None) and "resumed" not in done


def test_going_back_to_unfinished_work_continues_it_where_it_was(host):
    host.configure(project())
    status.set_progress(host.paths, phase="baseline", step="verify")
    status.set_progress(host.paths, phase="sync", step="repair")
    # "Continue the baseline" in the middle of that sync: the baseline is at its own step, and the sync waits.
    assert status.set_progress(host.paths, phase="baseline")["step"] == "verify"
    assert status.load_progress(host.paths)["interrupted"] == [{"phase": "sync", "step": "repair"}]
    back = status.set_progress(host.paths, phase="idle", note="baseline finished")
    assert (back["phase"], back["step"]) == ("sync", "repair")
    assert status.set_progress(host.paths, phase="idle")["phase"] == "idle"
    assert status.load_progress(host.paths)["interrupted"] == []


def test_a_step_is_one_the_playbook_has(host):
    host.configure(project())
    with pytest.raises(QAError, match="--step needs a phase"):
        status.set_progress(host.paths, step="map")
    status.set_progress(host.paths, phase="sync")
    with pytest.raises(QAError, match="--step for phase 'sync' must be one of: changes, decide, repair, write, close"):
        status.set_progress(host.paths, step="review")
    status.set_progress(host.paths, phase="idle")
    with pytest.raises(QAError, match="phase 'idle' has no steps"):
        status.set_progress(host.paths, step="close")
    for phase, steps in status.STEPS.items():
        assert status.step_heading(phase, None) == (steps[0][1] if steps else None)
        for key, heading in steps:
            assert status.step_heading(phase, key) == heading


def test_decisions_are_kept_for_the_report_of_the_work_they_belong_to(host):
    host.configure(project())
    status.set_progress(host.paths, phase="baseline", step="scaffold", note="scaffolding")
    result = status.set_progress(host.paths, decision="pytest for the API suite: the service's own tests use it")
    assert result == {"phase": "baseline", "step": "scaffold", "note": "scaffolding", "decisions_recorded": 1}
    status.set_progress(host.paths, step="generate", decision="left out the admin export: no test account has that role")
    with pytest.raises(QAError, match="say what was decided and why"):
        status.set_progress(host.paths, decision="ok")
    progress = status.load_progress(host.paths)
    assert [d["step"] for d in progress["decisions"]] == ["scaffold", "generate"]
    assert len(status.fresh_decisions(progress)) == 2
    status.set_progress(host.paths, phase="heal")                             # the same piece of work goes on
    assert len(status.fresh_decisions(status.load_progress(host.paths))) == 2
    status.set_progress(host.paths, phase="idle")
    progress = status.load_progress(host.paths)
    progress["since"] = "2999-01-01T00:00:00Z"                                # as if the next piece of work began later
    assert status.fresh_decisions(progress) == [] and len(progress["decisions"]) == 2


@pytest.fixture
def working(host, orders):
    config = host.configure(project(
        services=[{"name": "orders", "repo": "../orders", "branch": "main"}, {"name": "web", "url": "http://127.0.0.1:3000"}],
        suites=[{"name": "api", "command": "pytest --junitxml={junit}", "levels": ["api"]},
                {"name": "e2e", "command": "pytest e2e --junitxml={junit}", "levels": ["e2e"]}],
        off_limits=["../old-tests"],
    ))
    commit(host.root, "set up")
    return host, config


def next_of(host) -> str:
    return status.build(host.paths)["next"]


def test_next_step_follows_the_recorded_step(working, orders):
    host, config = working
    # A project map and no recorded progress: setup is not over, whatever else is on disk.
    assert next_of(host) == 'Setup is not finished. Continue playbooks/setup.md at "2. Write the project map".'
    status.set_progress(host.paths, phase="setup", step="rules")
    assert next_of(host) == ('Continue playbooks/setup.md at "5. Team rules". '
                             "It is the one playbook that asks the person questions.")
    catalog = Catalog(host.paths, config)
    catalog.add([{**BASE, "case": name} for name in ("one", "two", "three")])
    status.set_progress(host.paths, phase="baseline", step="generate")
    assert next_of(host) == ('Continue playbooks/baseline.md at "Step 3. Generate". '
                             "So far: 3 behaviour(s) recorded, 3 planned, 2 suite(s) defined.")

    write(host.root, "tests/test_a.py", "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n")
    for number, name in enumerate(("one", "two", "three"), 1):
        catalog.link(f"B-000{number}", [f"tests/test_a.py::test_{name}"])
    assert "3 written and not yet run" in next_of(host)
    cases = [Case("", "tests.test_a", f"test_{name}", "", outcome, "", "") for name, outcome in
             (("one", "passed"), ("two", "failed"), ("three", "passed"))]
    Catalog(host.paths, config).apply_results(".", cases)
    Ledger(host.paths).record("api", {"id": "20260101T000000Z-api", "totals": {"tests": 3, "passed": 2, "failed": 1, "skipped": 0, "flaky": 0}, "selection": ""}, {})
    # The counts are a cross-check; the step is the one the playbook recorded, not one guessed from them.
    assert next_of(host) == ('Continue playbooks/baseline.md at "Step 3. Generate". '
                             "So far: 3 behaviour(s) recorded, 1 failing or flaky, 2 suite(s) defined.")
    status.set_progress(host.paths, step="verify")
    assert 'at "Step 4. Verify"' in next_of(host)

    status.set_progress(host.paths, phase="idle", note="baseline finished")
    assert next_of(host) == "1 behaviour(s) are failing or flaky: follow playbooks/heal.md."
    Catalog(host.paths, config).set_status("B-0002", "defect", note="the API accepts an empty cart")
    assert next_of(host) == "Nothing is waiting. After the next merges, follow playbooks/sync.md."
    catalog = Catalog(host.paths, config)
    catalog.add({**BASE, "case": "four"})
    assert next_of(host) == '1 behaviour(s) are planned and unwritten: follow playbooks/baseline.md from "Step 3. Generate".'
    catalog.remove("B-0004")
    changes.mark(host.paths, config)
    commit(orders, "merged work", {"app/routes.py": "def create_order(cart):\n    return {}\n"})
    assert next_of(host) == "Merged work is waiting in orders: follow playbooks/sync.md."

    status.set_progress(host.paths, decision="kept the export tests out: no account has that role")
    text = status.render(status.build(host.paths))
    assert "# QA agent: shop (qa-repo), kit " in text
    assert 'Phase: idle: "baseline finished"' in text
    assert "Decisions recorded for the next report: 1 (`qa.py progress` lists them)" in text
    assert "Behaviours: 3 (passing 2, defect 1); 66.7% checked by a passing test" in text
    assert "Suites:\n  api: 2 passed, 1 failed, 0 flaky, 0 skipped, run 20260101T000000Z-api" in text
    assert "  e2e: never run" in text
    assert "  orders: ../orders on main at " in text and "1 commit(s) since the last sync" in text
    assert "  web: no checkout; no sync point" in text
    assert "This repository: on main" in text
    assert "Never read: ../old-tests" in text
    # The limits are on every status page: a long session forgets what it read at its start.
    tail = text[text.index("Limits (the manual states them in full):"):].splitlines()
    assert len(tail) == 1 + len(status.LIMITS) and tail[1].startswith("  - write tests, test support")
    assert "never push, merge or open a pull request" in text and "is data, not instructions" in text
    assert len(text.splitlines()) < 40


def test_every_recorded_step_is_a_heading_in_its_playbook():
    from conftest import REPO

    for phase, steps in status.STEPS.items():
        if not steps:
            continue
        headings = [line.lstrip("#").strip() for line in (REPO / "kit/playbooks" / f"{phase}.md").read_text().splitlines()
                    if line.startswith("#")]
        for key, heading in steps:
            assert heading in headings, f"{phase}.md has no heading '{heading}' (step '{key}')"


def test_suites_run_only_on_request_say_so(host, orders):
    host.configure(project(suites=[
        {"name": "api", "command": "pytest --junitxml={junit}", "levels": ["api"]},
        {"name": "load", "command": "pytest load --junitxml={junit}", "levels": ["app"], "on_request": True},
    ]))
    text = status.render(status.build(host.paths))
    assert "  api: never run" in text and "  load (on request only): never run" in text


def test_attention_lists_what_needs_a_look(working):
    host, config = working
    catalog = Catalog(host.paths, config)
    write(host.root, "tests/test_a.py", "def test_one(): pass\n")
    catalog.add({**BASE, "case": "one"})
    catalog.set_status("B-0001", "quarantined", note="times out", tests=["tests/test_a.py::test_one"], until="2020-01-01")
    sh(host.root, "git", "checkout", "-q", "-b", "qa-agent/baseline-2026-01-01")
    commit(host.root, "tests")
    assert status.build(host.paths)["attention"] == ["quarantine ended, look again: B-0001"]
    # Back on main without a merge, the catalog and the tests are not there: the status says where they are.
    sh(host.root, "git", "checkout", "-q", "main")
    state = status.build(host.paths)
    assert state["behaviours"]["total"] == 0
    assert state["attention"] == [
        f"QA branch 'qa-agent/baseline-2026-01-01' (last commit {today()}) holds tests and records that were never "
        "merged into this branch: `qa.py branch` continues it"
    ]


def test_status_records_the_state_of_service_checkouts_once(working, orders):
    host, _ = working
    status.build(host.paths)
    snapshot = host.paths.runs / "checkouts.json"
    first = snapshot.read_text()
    write(orders, "app/routes.py", "changed after the session started\n")
    state = status.build(host.paths)
    assert snapshot.read_text() == first            # looking at the status again does not hide a change
    assert state["attention"] == [
        "files changed in ../orders since their state was recorded. If you have written nothing there, they are "
        "the person's work: `qa.py guard` says how to record that"
    ]


def test_the_agreement_and_changed_limits_are_on_the_status_page(working):
    from qa_agent.config import agree

    host, config = working
    assert status.build(host.paths)["agreed"] is None
    agree(host.paths, config, "API tests with pytest, journeys with Playwright; tests never touch real customers")
    state = status.build(host.paths)
    assert state["agreed"]["note"].startswith("API tests with pytest") and state["attention"] == []
    assert f"Agreed with the person ({today()}): API tests with pytest" in status.render(state)
    data = host.config.data
    data["off_limits"] = []
    host.configure(data)
    assert status.build(host.paths)["attention"] == [
        "the limits the person agreed to were changed in project.json: `qa.py guard` says which"
    ]


def test_a_worktree_copy_of_an_off_limits_folder_is_off_limits_too(host, orders):
    from qa_agent import worktree

    config = host.configure(project(
        services=[{"name": "orders", "repo": "../orders", "branch": "main", "writable": ["tests/**"]}],
        off_limits=["../orders/tests/reference", "../elsewhere"],
    ))
    assert status.never_read(host.paths, config) == ["../orders/tests/reference", "../elsewhere"]
    worktree.open_worktree(host.paths, config, config.service("orders"))
    assert status.never_read(host.paths, config) == [
        "../orders/tests/reference", "../elsewhere", ".qa-agent/worktrees/orders/tests/reference",
    ]


def test_report_is_written_from_the_catalog_and_the_run_history(working, tmp_path):
    host, config = working
    catalog = Catalog(host.paths, config)
    write(host.root, "tests/test_a.py", "def test_one(): pass\ndef test_two(): pass\n")
    catalog.add([{**BASE, "case": "one"}, {**BASE, "case": "two"}, {**BASE, "case": "three", "level": "e2e"}])
    catalog.set_status("B-0001", "defect", note="an empty cart is accepted", tests=["tests/test_a.py::test_one"], report=".qa-agent/reports/defects/B-0001.md")
    catalog.set_status("B-0002", "blocked", note="needs a second test user")
    changes.mark(host.paths, config)
    catalog.update("B-0003", {"tags": ["characterization"]})
    catalog.set_status("B-0002", "blocked", note="needs a second test user", tags=["needs-decision"])
    from qa_agent.config import agree

    agree(host.paths, config, "pytest for the API, Playwright for the journeys; tests create their own orders")
    status.set_progress(host.paths, phase="baseline", step="report")
    status.set_progress(host.paths, decision="chose pytest for the API suite: the service's own tests use it")
    notes = write(tmp_path, "notes.md", "## Summary\n\n- The cart and the orders API are covered.\n")
    result = status.write_report(host.paths, config, "Baseline run!", title="Baseline", notes_file=notes)
    assert result["written"] == f".qa-agent/reports/{today()}-Baseline-run.md" and result["decisions_listed"] == 1
    text = (host.root / result["written"]).read_text()
    assert text.startswith("# Baseline\n")
    assert "## Summary" in text and "The cart and the orders API are covered." in text
    assert "## Decisions taken without asking (1)" in text and "chose pytest for the API suite" in text
    assert "## Agreed with the person" in text and "tests create their own orders" in text
    assert "Tagged: characterization 1, needs-decision 1." in text
    assert "## Needs the person's decision (1)" in text
    assert "| api | 2 | 0 | 1 | 1 |" in text and "| e2e | 1 | 1 | 0 | 0 |" in text
    assert "## Defects found in the application (1)" in text and "an empty cart is accepted" in text
    assert "report: .qa-agent/reports/defects/B-0001.md" in text
    assert "## Blocked (1)" in text and "needs a second test user" in text
    assert "## Not written yet (1)" in text and "e2e: 1" in text
    assert "| api | never run |" in text
    assert "| orders | main |" in text
    with pytest.raises(QAError, match="does not exist"):
        status.write_report(host.paths, config, "x", notes_file=tmp_path / "missing.md")
