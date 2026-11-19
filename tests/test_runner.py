import os
import signal
import subprocess
import sys
import time

import pytest

from qa_agent import worktree
from qa_agent.catalog import Catalog
from qa_agent.matching import Case
from qa_agent.runner import Ledger, combine, parse_junit, recent_runs, run_suite, show_run
from qa_agent.util import QAError, write_json
from conftest import REPO, project, sh, write

PYTEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="1" tests="4">
<testcase classname="tests.api.test_orders" name="test_create" time="0.01"/>
<testcase classname="tests.api.test_orders.TestCart" name="test_empty[chromium]" time="0.01">
  <failure message="assert 200 == 422">def test_empty():
&gt;   assert 200 == 422
E   assert 200 == 422</failure></testcase>
<testcase classname="tests.api.test_orders" name="test_later"><skipped type="pytest.skip" message="B-0003"/></testcase>
<testcase classname="tests.api.test_orders" name="test_boom"><error message="fixture 'client' not found"/></testcase>
</testsuite></testsuites>"""

PLAYWRIGHT_XML = """<testsuites id="" name="" tests="2" failures="1" skipped="0" errors="0" time="3.1">
<testsuite name="login.spec.ts" timestamp="2026-01-01T00:00:00.000Z" hostname="chromium" tests="2" failures="1">
<testcase name="Login › signs in" classname="login.spec.ts" time="1.2"></testcase>
<testcase name="Login › rejects a wrong password" classname="login.spec.ts" time="1.9">
<failure message="login.spec.ts:14:5 rejects a wrong password" type="FAILURE"><![CDATA[Error: expect(locator).toBeVisible()]]></failure>
</testcase></testsuite></testsuites>"""

JEST_XML = """<testsuites name="jest tests" tests="1" failures="0">
<testsuite name="cart" tests="1" file="src/cart.test.js">
<testcase classname="cart rejects an empty cart" name="cart rejects an empty cart" time="0.002" file="src/cart.test.js"></testcase>
</testsuite></testsuites>"""

BARE_SUITE_XML = '<testsuite name="go" tests="1"><testcase classname="example.com/shop/orders" name="TestEmptyCart"/></testsuite>'


def test_parse_junit_reads_every_runner(tmp_path):
    cases = parse_junit(write(tmp_path, "pytest.xml", PYTEST_XML))
    assert [c.outcome for c in cases] == ["passed", "failed", "skipped", "error"]
    assert cases[1].message == "assert 200 == 422" and "E   assert 200 == 422" in cases[1].detail
    assert cases[3].message == "fixture 'client' not found"

    cases = parse_junit(write(tmp_path, "pw.xml", PLAYWRIGHT_XML))
    assert [(c.classname, c.name, c.outcome) for c in cases] == [
        ("login.spec.ts", "Login › signs in", "passed"), ("login.spec.ts", "Login › rejects a wrong password", "failed"),
    ]
    assert "toBeVisible" in cases[1].detail

    assert parse_junit(write(tmp_path, "jest.xml", JEST_XML))[0].file == "src/cart.test.js"
    assert parse_junit(write(tmp_path, "go.xml", BARE_SUITE_XML))[0].name == "TestEmptyCart"
    with pytest.raises(QAError, match="not a readable JUnit report"):
        parse_junit(write(tmp_path, "broken.xml", "<testsuite"))


def test_combine_calls_mixed_outcomes_flaky():
    def case(name, outcome, message=""):
        return Case("", "t", name, "", outcome, message, "")

    first = [case("a", "passed"), case("b", "failed", "boom"), case("c", "failed", "always"), case("d", "skipped")]
    second = [case("b", "passed"), case("c", "failed", "always")]
    final, flaky = combine([first, second])
    assert flaky == {"t::b"}
    assert [(c.name, c.outcome) for c in final] == [("a", "passed"), ("b", "passed"), ("c", "failed"), ("d", "skipped")]
    assert final[1].message == "boom"       # the failure is kept, to say what went wrong


def test_ledger_remembers_and_finds_unstable_tests(host):
    ledger = Ledger(host.paths)
    for code in "pfpfp":
        ledger.record("api", {"id": "r", "totals": {}}, {"t::wobbly": code, "t::steady": "p", "t::broken": "f"})
    ledger.record("api", {"id": "r", "totals": {}}, {"t::retried": "x"})
    assert Ledger(host.paths).previous("api", "t::wobbly") == "p"
    assert Ledger(host.paths).previous("api", "t::never") is None
    assert [row["test"] for row in ledger.unstable()] == ["t::retried", "t::wobbly"]
    assert len(ledger.suite("api")["runs"]) == 6


# ---------------------------------------------------------------- running a real suite

TESTS = '''
import os, pathlib

def test_passes():
    assert 1 + 1 == 2

def test_fails():
    assert os.environ.get("SHOP_FIXED") == "1", "the shop is closed"

def test_flaky():
    marker = pathlib.Path(os.environ["QA_AGENT_RUN_DIR"]) / "flaky-ran"
    first = not marker.exists()
    marker.write_text("x")
    assert not first, "fails the first time in each run"

def test_reads_env_file():
    assert os.environ["FROM_ENV_FILE"] == "yes"
'''


@pytest.fixture
def suite_host(host, orders):
    write(host.root, "tests/test_shop.py", TESTS)
    write(host.root, ".env", "FROM_ENV_FILE=yes\n")
    py = sys.executable
    config = host.configure(project(suites=[{
        "name": "api", "levels": ["api"], "stack": "pytest",
        "command": f"{py} -m pytest -q -p no:cacheprovider tests --junitxml={{junit}}",
        "rerun_failed": f"{py} -m pytest -q -p no:cacheprovider tests --junitxml={{junit}} -k 'fails or flaky'",
    }]))
    catalog = Catalog(host.paths, config)
    base = {"service": "orders", "level": "api", "surface": "shop", "expect": "x", "intent": "x"}
    catalog.add([{**base, "case": name} for name in ("passes", "fails", "flaky")])
    for number, name in enumerate(("passes", "fails", "flaky"), 1):
        catalog.link(f"B-000{number}", [f"tests/test_shop.py::test_{name}"])
    return host


def test_run_records_outcomes_and_classifies_failures(suite_host, monkeypatch):
    paths, config = suite_host.paths, suite_host.config
    summary, code = run_suite(paths, config, "api")
    assert code == 1 and summary["runnable"] is True
    assert summary["totals"] == {"tests": 4, "passed": 2, "failed": 1, "skipped": 0, "flaky": 1}
    assert summary["attempts"] == 2
    failed = summary["failed"][0]
    assert failed["test"] == "tests.test_shop::test_fails" and failed["kind"] == "first-run" and failed["behaviours"] == ["B-0002"]
    assert "the shop is closed" in failed["message"]
    assert "the shop is closed" in (paths.root / failed["detail"]).read_text()
    assert summary["flaky"][0]["behaviours"] == ["B-0003"]
    assert summary["tests_without_a_behaviour"] == {"count": 1, "first": ["tests.test_shop::test_reads_env_file"]}
    catalog = Catalog(paths, config)
    assert [catalog.get(f"B-000{n}")["status"] for n in (1, 2, 3)] == ["passing", "failing", "flaky"]
    assert show_run(paths, summary["run"])["totals"]["failed"] == 1
    assert (paths.runs / summary["run"] / "output.log").read_text().count("$ ") == 2

    # The same failure again is known; once fixed it is reported as fixed.
    summary, _ = run_suite(paths, config, "api")
    assert summary["failed"][0]["kind"] == "known"
    monkeypatch.setenv("SHOP_FIXED", "1")
    summary, code = run_suite(paths, config, "api", rerun=False)
    assert summary["fixed"] == ["tests.test_shop::test_fails"]
    assert summary["totals"]["failed"] == 1 and code == 1     # the flaky one fails outright without a re-run
    assert summary["failed"][0]["kind"] == "new"              # it passed (after a retry) last time
    assert Catalog(paths, config).get("B-0002")["status"] == "passing"
    assert [run["totals"]["failed"] for run in recent_runs(paths)] == [1, 1, 1]


def test_run_passes_extra_arguments_and_repeats(suite_host):
    paths, config = suite_host.paths, suite_host.config
    summary, code = run_suite(paths, config, "api", extra=["-k", "passes or reads"])
    assert code == 0 and summary["totals"]["tests"] == 2
    summary, code = run_suite(paths, config, "api", extra=["-k", "flaky"], times=3)
    assert summary["attempts"] == 3 and summary["totals"]["flaky"] == 1 and code == 0


def test_a_selection_takes_the_place_of_the_suites_own(suite_host):
    write(suite_host.root, "other/test_elsewhere.py", "def test_elsewhere():\n    assert True\n")
    data = suite_host.config.data
    py = sys.executable
    data["suites"][0].update(
        command=f"{py} -m pytest -q -p no:cacheprovider --junitxml={{junit}}", select="tests -k 'passes or reads'", rerun_failed=None,
    )
    config = suite_host.configure(data)
    summary, code = run_suite(suite_host.paths, config, "api")
    assert code == 0 and summary["totals"]["tests"] == 2                # the suite's folder and filter
    assert recent_runs(suite_host.paths)[-1]["selection"] == ""         # which is the whole suite
    summary, code = run_suite(suite_host.paths, config, "api", extra=["other/test_elsewhere.py::test_elsewhere"])
    assert code == 0 and summary["totals"]["tests"] == 1                # exactly the test asked for
    assert recent_runs(suite_host.paths)[-1]["selection"] == "other/test_elsewhere.py::test_elsewhere"


def test_a_corrected_defect_shows_as_a_failure_owned_by_the_defect(suite_host, monkeypatch):
    """A strict expected failure fails once the application is corrected; re-linking makes the test live again."""
    paths, config = suite_host.paths, suite_host.config
    catalog = Catalog(paths, config)
    catalog.set_status("B-0002", "defect", note="expected the shop to be open, it is closed", report=".qa-agent/reports/defects/B-0002.md")
    summary, _ = run_suite(paths, config, "api", extra=["-k", "fails"])
    assert summary["failed"][0]["behaviours"] == ["B-0002"]
    assert Catalog(paths, config).get("B-0002")["status"] == "defect"   # a run never overrules a recorded decision
    entry = Catalog(paths, config).link("B-0002", ["tests/test_shop.py::test_fails"])
    assert entry["status"] == "written" and "report" not in entry
    monkeypatch.setenv("SHOP_FIXED", "1")
    run_suite(paths, config, "api", extra=["-k", "fails"])
    assert Catalog(paths, config).get("B-0002")["status"] == "passing"


def test_run_that_writes_no_report_is_not_a_result(suite_host):
    data = suite_host.config.data
    data["suites"][0]["command"] = "echo 'collection error' && exit 3  # {junit}"
    config = suite_host.configure(data)
    summary, code = run_suite(suite_host.paths, config, "api")
    assert code == 2 and summary["runnable"] is False
    assert "without writing a JUnit report" in summary["reason"] and "collection error" in summary["log_tail"]
    assert Catalog(suite_host.paths, config).get("B-0001")["status"] == "written"


def test_run_stops_early_when_a_needed_service_is_down(suite_host):
    data = suite_host.config.data
    data["services"][0].update(url="http://127.0.0.1:9", health="/healthz")
    data["suites"][0]["needs"] = ["orders"]
    config = suite_host.configure(data)
    summary, code = run_suite(suite_host.paths, config, "api")
    assert code == 2 and summary["down"][0]["service"] == "orders"
    assert not suite_host.paths.runs.exists()
    summary, code = run_suite(suite_host.paths, config, "api", health=False, extra=["-k", "passes"])
    assert code == 0


def test_setup_commands_run_first_and_stop_the_run_when_they_fail(suite_host):
    data = suite_host.config.data
    data["suites"][0]["setup"] = ["echo ready > setup-ran.txt"]
    config = suite_host.configure(data)
    run_suite(suite_host.paths, config, "api", extra=["-k", "passes"])
    assert not (suite_host.root / "setup-ran.txt").exists()            # only with --setup
    run_suite(suite_host.paths, config, "api", extra=["-k", "passes"], setup=True)
    assert (suite_host.root / "setup-ran.txt").exists()
    data["suites"][0]["setup"] = ["exit 7"]
    summary, code = run_suite(suite_host.paths, suite_host.configure(data), "api", setup=True)
    assert code == 2 and "setup command failed" in summary["reason"]


def test_a_suite_that_hangs_is_stopped(suite_host):
    data = suite_host.config.data
    data["suites"][0].update(command="sleep 30 # {junit}", timeout_minutes=0.02)
    summary, code = run_suite(suite_host.paths, suite_host.configure(data), "api")
    assert code == 2 and "timed out" in summary["reason"]


def test_a_trial_run_reports_and_records_nothing(suite_host):
    paths, config = suite_host.paths, suite_host.config
    summary, code = run_suite(paths, config, "api", extra=["-k", "fails"], record=False)
    assert code == 1 and summary["recorded"] is False and summary["totals"]["failed"] == 1
    assert "nothing was recorded" in summary["catalog"]["note"]
    assert Catalog(paths, config).get("B-0002")["status"] == "written"                # as it was
    assert recent_runs(paths) == [] and Ledger(paths).previous("api", "tests.test_shop::test_fails") is None
    assert show_run(paths, summary["run"])["totals"]["failed"] == 1                   # the raw result can still be read
    summary, _ = run_suite(paths, config, "api", extra=["-k", "fails"])
    assert "recorded" not in summary and summary["failed"][0]["kind"] == "first-run"  # the trial left no history


def test_a_whole_run_that_does_not_report_a_linked_test_takes_its_status_back(suite_host):
    paths, config = suite_host.paths, suite_host.config
    run_suite(paths, config, "api")
    assert Catalog(paths, config).get("B-0001")["status"] == "passing"
    # The test is renamed and the link is not: the behaviour points at a test no run reports any more.
    source = suite_host.root / "tests/test_shop.py"
    source.write_text(source.read_text().replace("def test_passes", "def test_adds_up"))
    summary, _ = run_suite(paths, config, "api", extra=["-k", "fails"])
    assert "linked_but_not_reported" not in summary["catalog"]                        # a selection proves nothing about the rest
    assert Catalog(paths, config).get("B-0001")["status"] == "passing"
    summary, _ = run_suite(paths, config, "api")
    assert summary["catalog"]["linked_but_not_reported"] == ["B-0001"]
    assert {"id": "B-0001", "from": "passing", "to": "written"} in summary["catalog"]["changed"]
    assert "catalog link <id> --test … --replace" in summary["catalog"]["note"]
    assert Catalog(paths, config).get("B-0001")["status"] == "written"
    assert summary["tests_without_a_behaviour"]["first"] == ["tests.test_shop::test_adds_up", "tests.test_shop::test_reads_env_file"]


def test_levels_shared_by_two_suites_are_never_taken_as_wholly_run(suite_host):
    data = suite_host.config.data
    data["suites"].append({**data["suites"][0], "name": "api-slow", "select": "slow"})
    config = suite_host.configure(data)
    run_suite(suite_host.paths, config, "api", extra=["-k", "passes"])
    source = suite_host.root / "tests/test_shop.py"
    source.write_text(source.read_text().replace("def test_passes", "def test_adds_up"))
    summary, _ = run_suite(suite_host.paths, config, "api")
    assert "linked_but_not_reported" not in summary["catalog"]                        # the other suite may hold that test
    assert Catalog(suite_host.paths, config).get("B-0001")["status"] == "passing"


def test_only_one_run_at_a_time(suite_host):
    paths, config = suite_host.paths, suite_host.config
    paths.runs.mkdir(parents=True)
    write_json(paths.run_lock, {"pid": os.getppid(), "suite": "e2e", "at": "2026-01-01T00:00:00Z"})      # a live process
    with pytest.raises(QAError, match="A run is in progress: suite 'e2e', started 2026-01-01T00:00:00Z"):
        run_suite(paths, config, "api", extra=["-k", "passes"])
    assert recent_runs(paths) == []
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    write_json(paths.run_lock, {"pid": ended.pid, "suite": "e2e", "at": "2026-01-01T00:00:00Z"})         # left by a run that died
    summary, code = run_suite(paths, config, "api", extra=["-k", "passes"])
    assert code == 0 and not paths.run_lock.exists()
    paths.run_lock.write_text("not json")                                                                  # a damaged lock is no lock
    assert run_suite(paths, config, "api", extra=["-k", "passes"])[1] == 0


def test_a_suite_inside_a_service_runs_only_in_its_worktree(host, orders):
    config = host.configure(project(
        services=[{"name": "orders", "repo": "../orders", "branch": "main", "writable": ["tests/**"]}],
        suites=[{"name": "orders-unit", "levels": ["unit"], "repo": "orders", "select": "tests",
                 "command": f"{sys.executable} -m pytest -q -p no:cacheprovider --junitxml={{junit}}"}],
    ))
    with pytest.raises(QAError, match="has no open QA worktree. Open it first: `qa.py worktree open --service orders`"):
        run_suite(host.paths, config, "orders-unit")
    worktree.open_worktree(host.paths, config, config.service("orders"))
    summary, code = run_suite(host.paths, config, "orders-unit")
    assert code == 0 and summary["cwd"] == ".qa-agent/worktrees/orders" and summary["totals"]["passed"] == 1
    assert sh(orders, "git", "status", "--porcelain") == ""                 # the checkout the service runs from saw nothing


@pytest.mark.skipif(os.name == "nt", reason="process groups and signals work differently there")
def test_ending_the_helper_ends_the_tests_it_started(suite_host):
    """The tool that started `qa.py run` gave up on it: the test processes must not live on, nor the lock."""
    marker = suite_host.root / "started.pid"
    data = suite_host.config.data
    data["suites"][0].update(command=f"sh -c 'echo $$ > {marker}; sleep 60' # {{junit}}", rerun_failed=None)
    suite_host.configure(data)
    helper = subprocess.Popen(
        [sys.executable, str(REPO / "kit" / "qa.py"), "--root", str(suite_host.root), "run", "api"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while not (marker.exists() and marker.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(marker.read_text())
        assert suite_host.paths.run_lock.exists()
        helper.send_signal(signal.SIGTERM)
        assert helper.wait(timeout=30) == 130
    finally:
        if helper.poll() is None:
            helper.kill()
        helper.communicate()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the test process outlived the helper")
    assert not suite_host.paths.run_lock.exists()
    assert "the run was interrupted" in next(suite_host.paths.runs.glob("*-api/output.log")).read_text()
