import pytest

from qa_agent.catalog import Catalog
from qa_agent.matching import Case
from qa_agent.util import QAError
from conftest import project, write

BEHAVIOUR = {
    "service": "orders", "level": "api", "area": "cart", "surface": "POST /orders",
    "case": "an empty cart", "expect": "422 with code EMPTY_CART; no order is created",
    "intent": "app/routes.py create_order raises EmptyCart; OpenAPI lists 422",
    "sources": ["app/routes.py"], "priority": 1,
}


@pytest.fixture
def catalog(host, orders):
    return Catalog(host.paths, host.configure(project()))


def passed(classname, name, outcome="passed"):
    return Case("", classname, name, "", outcome, "", "")


def test_add_assigns_identifiers_and_defaults(catalog):
    result = catalog.add([BEHAVIOUR, {**BEHAVIOUR, "case": "an unknown product", "level": "unit"}])
    assert result == {"added": ["B-0001", "B-0002"], "skipped_duplicates": [], "rejected": []}
    first, second = catalog.get("B-0001"), catalog.get("B-0002")
    assert first["status"] == "planned" and first["tests"] == [] and first["repo"] == "."
    assert second["repo"] == "orders"      # unit tests live with the service's code
    assert Catalog(catalog.paths, catalog.config).get("B-0002")["case"] == "an unknown product"


def test_add_skips_duplicates_and_rejects_bad_entries(catalog):
    catalog.add(BEHAVIOUR)
    result = catalog.add([
        {**BEHAVIOUR, "case": "An  empty CART"},                # the same behaviour, written differently
        {**BEHAVIOUR, "case": "x", "service": "billing"},       # not a service of this project
        {**BEHAVIOUR, "case": "y", "level": "smoke"},
        {**BEHAVIOUR, "case": "z", "intent": ""},
        {**BEHAVIOUR, "case": "w", "status": "passing"},
        {**BEHAVIOUR, "case": "v", "colour": "blue"},
        "not an object",
    ])
    assert result["added"] == []
    assert result["skipped_duplicates"] == [{"index": 0, "case": "An  empty CART", "same_as": "B-0001"}]
    problems = {r["index"]: " ".join(r["problems"]) for r in result["rejected"]}
    assert "not in the project map" in problems[1]
    assert "level must be one of" in problems[2]
    assert "'intent' is required" in problems[3]
    assert "start as 'planned'" in problems[4]
    assert "unknown field(s): colour" in problems[5]
    assert "must be an object" in problems[6]


def test_system_behaviours_belong_to_no_single_service(catalog):
    result = catalog.add({**BEHAVIOUR, "service": "system", "level": "integration", "case": "order reaches billing"})
    assert result["added"] == ["B-0001"]
    assert catalog.get("B-0001")["repo"] == "."


def test_link_checks_that_the_test_exists(catalog):
    catalog.add(BEHAVIOUR)
    with pytest.raises(QAError, match="does not exist"):
        catalog.link("B-0001", ["tests/api/test_orders.py::test_empty_cart"])
    write(catalog.paths.root, "tests/api/test_orders.py", "def test_other():\n    assert True\n")
    with pytest.raises(QAError, match="no test named 'test_empty_cart'"):
        catalog.link("B-0001", ["tests/api/test_orders.py::test_empty_cart"])
    with pytest.raises(QAError, match="not a test reference"):
        catalog.link("B-0001", ["tests/api/test_orders.py"])
    write(catalog.paths.root, "tests/api/test_orders.py", "def test_empty_cart():\n    assert True\n")
    entry = catalog.link("B-0001", ["tests/api/test_orders.py::test_empty_cart"])
    assert entry["status"] == "written"
    assert catalog.link("B-0001", ["tests/api/test_orders.py::test_empty_cart"])["tests"] == ["tests/api/test_orders.py::test_empty_cart"]


def test_run_statuses_cannot_be_set_by_hand(catalog):
    catalog.add(BEHAVIOUR)
    for status in ("passing", "failing", "flaky", "written"):
        with pytest.raises(QAError, match="recorded by"):
            catalog.set_status("B-0001", status, note="it passes, trust me")


def test_decisions_need_a_reason_and_a_test(catalog):
    catalog.add(BEHAVIOUR)
    write(catalog.paths.root, "tests/test_a.py", "def test_empty_cart():\n    assert False\n")
    ref = "tests/test_a.py::test_empty_cart"
    with pytest.raises(QAError, match="--note is required"):
        catalog.set_status("B-0001", "blocked")
    with pytest.raises(QAError, match="needs the test it refers to"):
        catalog.set_status("B-0001", "defect", note="the API answers 200")
    with pytest.raises(QAError, match="--until"):
        catalog.set_status("B-0001", "quarantined", note="fails one run in five", tests=[ref])
    with pytest.raises(QAError, match="YYYY-MM-DD"):
        catalog.set_status("B-0001", "quarantined", note="fails one run in five", tests=[ref], until="next week")
    entry = catalog.set_status("B-0001", "quarantined", note="fails one run in five", tests=[ref], until="2099-01-01")
    assert entry["until"] == "2099-01-01"
    entry = catalog.set_status("B-0001", "defect", note="the API answers 200 for an empty cart", report="reports/defects/B-0001.md")
    assert entry["status"] == "defect" and "until" not in entry and entry["report"].endswith("B-0001.md")
    entry = catalog.set_status("B-0001", "planned")
    assert entry["tests"] == [] and "report" not in entry


def test_only_planned_behaviours_can_be_removed(catalog):
    catalog.add([BEHAVIOUR, {**BEHAVIOUR, "case": "another"}])
    catalog.set_status("B-0002", "blocked", note="needs a paid account")
    catalog.remove("B-0001")
    with pytest.raises(QAError, match="Only planned"):
        catalog.remove("B-0002")
    assert [e["id"] for e in catalog.entries] == ["B-0002"]
    assert catalog.add({**BEHAVIOUR, "case": "third"})["added"] == ["B-0003"]   # identifiers are never reused


def test_update_keeps_entries_distinct(catalog):
    catalog.add([BEHAVIOUR, {**BEHAVIOUR, "case": "another"}])
    assert catalog.update("B-0002", {"expect": "404", "priority": 3})["priority"] == 3
    with pytest.raises(QAError, match="would duplicate B-0001"):
        catalog.update("B-0002", {"case": "an empty cart"})
    with pytest.raises(QAError, match="Cannot change status"):
        catalog.update("B-0002", {"status": "passing"})


def test_next_orders_by_priority(catalog):
    catalog.add([
        {**BEHAVIOUR, "case": "c", "priority": 3}, {**BEHAVIOUR, "case": "a", "priority": 1},
        {**BEHAVIOUR, "case": "b", "priority": 2}, {**BEHAVIOUR, "case": "d", "priority": 1, "level": "e2e"},
    ])
    assert [e["case"] for e in catalog.next(3)] == ["a", "d", "b"]
    assert [e["case"] for e in catalog.next(3, level=["e2e"])] == ["d"]


def test_results_of_a_run_move_statuses(catalog):
    root = catalog.paths.root
    write(root, "tests/test_a.py", "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\ndef test_four(): pass\n")
    catalog.add([{**BEHAVIOUR, "case": name} for name in ("one", "two", "three", "four", "five")])
    for number, name in enumerate(("one", "two", "three", "four"), 1):
        catalog.link(f"B-000{number}", [f"tests/test_a.py::test_{name}"])
    cases = [
        passed("tests.test_a", "test_one"), passed("tests.test_a", "test_two", "failed"),
        passed("tests.test_a", "test_three"), passed("tests.test_a", "test_four", "skipped"),
    ]
    result = catalog.apply_results(".", cases, flaky_keys={"tests.test_a::test_three"})
    assert result["seen"] == {"passing": 1, "failing": 1, "flaky": 1}
    assert result["linked_but_skipped"] == ["B-0004"]
    assert [catalog.get(f"B-000{n}")["status"] for n in range(1, 6)] == ["passing", "failing", "flaky", "written", "planned"]
    # A decision the agent recorded is not overwritten by a run.
    catalog.set_status("B-0002", "defect", note="the API answers 200 for an empty cart")
    catalog.apply_results(".", cases)
    assert catalog.get("B-0002")["status"] == "defect"
    assert catalog.stats()["checked_percent"] == 40.0


def test_validate_finds_what_a_hand_edit_breaks(catalog):
    root = catalog.paths.root
    write(root, "tests/test_a.py", "def test_one(): pass\n")
    catalog.add([BEHAVIOUR, {**BEHAVIOUR, "case": "two"}, {**BEHAVIOUR, "case": "three"}])
    catalog.link("B-0001", ["tests/test_a.py::test_one"])
    assert catalog.validate()["ok"] is True
    (root / "tests/test_a.py").unlink()                       # a linked test was deleted
    catalog.entries[1]["status"] = "passing"                  # a status without a test
    catalog.entries[2].update(status="quarantined", note="", tests=[])
    catalog.data["next_id"] = 2
    report = catalog.validate()
    errors = " | ".join(report["errors"])
    assert report["ok"] is False
    assert "B-0001: tests/test_a.py does not exist" in errors
    assert "B-0002: status 'passing' but no test is linked" in errors
    assert "B-0003: quarantined without an 'until' date" in errors
    assert "next_id must be above 3" in errors


def test_an_ended_quarantine_is_a_warning(catalog):
    write(catalog.paths.root, "tests/test_a.py", "def test_one(): pass\n")
    catalog.add(BEHAVIOUR)
    catalog.set_status("B-0001", "quarantined", note="times out now and then", tests=["tests/test_a.py::test_one"], until="2020-01-01")
    report = catalog.validate()
    assert report["ok"] is True and "quarantine ended 2020-01-01" in report["warnings"][0]


def test_tests_of_a_service_are_looked_up_in_its_worktree_first(catalog, orders):
    catalog.add({**BEHAVIOUR, "level": "unit", "case": "total of an empty cart"})
    assert catalog.test_base("orders") == orders.resolve()
    with pytest.raises(QAError, match="does not exist"):
        catalog.link("B-0001", ["tests/test_cart.py::test_empty_total"])
    worktree = catalog.paths.worktrees / "orders"
    write(worktree, "tests/test_cart.py", "def test_empty_total(): pass\n")
    assert catalog.test_base("orders") == worktree
    assert catalog.link("B-0001", ["tests/test_cart.py::test_empty_total"])["status"] == "written"
