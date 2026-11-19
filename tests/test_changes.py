import json

import pytest

from qa_agent import changes
from qa_agent.catalog import Catalog
from qa_agent.util import read_json
from conftest import commit, git_init, project, sh, write
from test_surface import OPENAPI

BASE = {"service": "orders", "level": "api", "surface": "POST /orders", "expect": "x", "intent": "x"}


@pytest.fixture
def synced(host, orders):
    config = host.configure(project(services=[
        {"name": "orders", "repo": "../orders", "branch": "main"},
        {"name": "mail"},
    ]))
    catalog = Catalog(host.paths, config)
    catalog.add([
        {**BASE, "case": "creates an order", "sources": ["app/routes.py"]},
        {**BASE, "case": "stores the order", "sources": ["app/models.py:12", "app/db/**"]},
        {**BASE, "case": "sends a mail", "service": "system", "level": "integration", "sources": ["orders:app/mailer.py"]},
        {**BASE, "case": "gone", "sources": ["app/routes.py"]},
    ])
    catalog.set_status("B-0004", "retired", note="the endpoint was removed")
    return host


def test_classify():
    service = {"tests": [], "sources": []}
    assert changes.classify("app/routes.py", service) == "source"
    assert changes.classify("tests/test_routes.py", service) == "tests"
    assert changes.classify("src/cart.test.ts", service) == "tests"
    assert changes.classify("docs/api.md", service) == "other"
    assert changes.classify("package-lock.json", service) == "other"
    strict = {"tests": ["checks/**"], "sources": ["app/**"]}
    assert changes.classify("checks/a.py", strict) == "tests"
    assert changes.classify("scripts/deploy.py", strict) == "other"
    assert changes.classify("app/x.py", strict) == "source"


def test_without_a_sync_point_it_says_so(synced):
    result = changes.report(synced.paths, synced.config)
    row = result["services"][0]
    assert row["synced"] is None and len(row["recent"]) == 1 and "no sync point" in row["notes"][0]
    assert result["services"][1] == {"service": "mail", "available": False, "reason": "no checkout in the project map"}
    assert result["next"] == "no sync point for: orders"


def test_mark_then_report_what_was_merged(synced, orders):
    paths, config = synced.paths, synced.config
    marked = changes.mark(paths, config)
    assert marked["synced"]["orders"]["from"] is None and marked["skipped"] == {"mail": "no git checkout"}
    assert changes.report(paths, config)["next"] == "nothing was merged since the last sync"

    sh(orders, "git", "checkout", "-q", "-b", "feature")
    commit(orders, "validate the cart", {"app/routes.py": "def create_order(cart):\n    assert cart\n"})
    commit(orders, "mailer", {"app/mailer.py": "def send(): ...\n", "app/pricing.py": "def price(): ...\n"})
    (orders / "app/db").mkdir()
    sh(orders, "git", "mv", "app/models.py", "app/db/models.py")
    commit(orders, "move models, docs, tests", {"README.md": "# orders v2\n", "tests/test_routes.py": "def test_x(): pass\n"})
    sh(orders, "git", "checkout", "-q", "main")
    sh(orders, "git", "merge", "-q", "--no-ff", "feature", "-m", "Merge feature: cart validation (#12)")

    result = changes.report(paths, config)
    row = result["services"][0]
    assert result["next"] == "follow playbooks/sync.md for: orders"
    assert row["commits"] == 4
    assert len(row["merged"]) == 1 and "Merge feature: cart validation (#12)" in row["merged"][0]
    source = {r["path"]: r for r in row["files"]["source"]}
    assert set(source) == {"app/routes.py", "app/mailer.py", "app/pricing.py", "app/db/models.py"}
    assert source["app/db/models.py"]["change"] == "R" and source["app/db/models.py"]["was"] == "app/models.py"
    assert [r["path"] for r in row["files"]["tests"]] == ["tests/test_routes.py"]
    assert row["files"]["other"] == 1
    affected = {a["id"]: a["files"] for a in row["affected"]}
    assert affected == {
        "B-0001": ["app/routes.py"],                          # its source changed
        "B-0002": ["app/models.py", "app/db/models.py"],      # by the path it was renamed from, and by glob
        "B-0003": ["app/mailer.py"],                          # a system behaviour naming this service
    }
    assert row["source_files_no_behaviour_lists"] == ["app/pricing.py"]
    assert row["read_a_diff"].startswith("git -C ../orders diff ")

    # Marking again moves the sync point and keeps a history.
    marked = changes.mark(paths, config, names=["orders"])
    assert marked["synced"]["orders"]["from"] and changes.report(paths, config)["services"][0]["changed"] is False
    assert len(read_json(paths.sync)["history"]) == 2


def test_a_sync_point_that_left_history_is_reported(synced, orders):
    paths, config = synced.paths, synced.config
    changes.mark(paths, config)
    data = read_json(paths.sync)
    data["services"]["orders"]["commit"] = "0" * 40
    paths.sync.write_text(json.dumps(data))
    row = changes.report(paths, config)["services"][0]
    assert row["changed"] is True and "history was rewritten" in row["notes"][0]


def test_checkout_on_another_branch_is_noted(synced, orders):
    changes.mark(synced.paths, synced.config)
    sh(orders, "git", "checkout", "-q", "-b", "experiment")
    row = changes.report(synced.paths, synced.config)["services"][0]
    assert "merged work is expected on 'main'" in row["notes"][0]


def test_fetch_reports_what_the_remote_has_that_the_checkout_lacks(host, workspace):
    upstream = git_init(workspace / "upstream")
    commit(upstream, "first", {"app/a.py": "a = 1\n"})
    sh(workspace, "git", "clone", "-q", str(upstream), "orders")
    config = host.configure(project())
    changes.mark(host.paths, config)
    commit(upstream, "merged later", {"app/a.py": "a = 2\n"})
    row = changes.report(host.paths, config, fetch=True)["services"][0]
    assert row["changed"] is False and row["not_yet_in_checkout"] == 1
    assert "are not in the checkout the service runs from" in row["notes"][0]
    assert (workspace / "orders/app/a.py").read_text() == "a = 1\n"       # fetching changed no file


def test_contract_changes_are_summarised_and_snapshots_refreshed(host, orders):
    write(orders, "openapi.json", json.dumps(OPENAPI))
    commit(orders, "openapi")
    config = host.configure(project(services=[{"name": "orders", "repo": "../orders", "openapi": "openapi.json"}]))
    marked = changes.mark(host.paths, config)
    assert marked["surface"] == {"orders": "snapshot refreshed"}
    doc = json.loads(json.dumps(OPENAPI))
    doc["paths"].pop("/healthz")
    commit(orders, "drop health", {"openapi.json": json.dumps(doc)})
    row = changes.report(host.paths, config)["services"][0]
    assert row["contract"] == {
        "checked": True, "worst": "breaking", "added": 0, "removed": 1, "changed": 0,
        "details": "qa.py surface diff --service orders",
    }


def test_marking_stops_at_the_commit_that_was_compared(synced, orders):
    paths, config = synced.paths, synced.config
    changes.mark(paths, config)
    first = commit(orders, "merged: validate the cart", {"app/routes.py": "def create_order(cart):\n    assert cart\n"})
    assert changes.report(paths, config)["services"][0]["commits"] == 1
    assert read_json(paths.sync)["examined"]["orders"]["commit"] == first
    # While the sync is being worked on, more is merged and the person updates the checkout.
    second = commit(orders, "merged later: a mailer", {"app/mailer.py": "def send(): ...\n"})
    marked = changes.mark(paths, config, names=["orders"])
    assert marked["synced"]["orders"]["to"] == first[:12]
    assert marked["arrived_after_the_comparison"] == {"orders": 1} and "wait for the next sync" in marked["note"]
    assert read_json(paths.sync)["examined"] == {}
    row = changes.report(paths, config)["services"][0]                  # the later commit is still to be synced
    assert row["commits"] == 1 and "merged later: a mailer" in row["merged"][0]
    marked = changes.mark(paths, config, names=["orders"])
    assert marked["synced"]["orders"]["to"] == second[:12] and "arrived_after_the_comparison" not in marked


def test_marking_without_a_comparison_takes_what_the_checkout_is_on(synced, orders):
    paths, config = synced.paths, synced.config
    changes.mark(paths, config)
    head = commit(orders, "merged", {"app/routes.py": "def create_order(cart):\n    return {}\n"})
    assert changes.mark(paths, config, names=["orders"])["synced"]["orders"]["to"] == head[:12]
    # A comparison that history no longer holds (the branch was rewritten) is not a point to mark.
    changes.report(paths, config)
    data = read_json(paths.sync)
    data["examined"] = {"orders": {"commit": "0" * 40, "at": "2026-01-01T00:00:00Z"}}
    paths.sync.write_text(json.dumps(data))
    assert changes.mark(paths, config, names=["orders"])["synced"]["orders"]["to"] == head[:12]


def test_the_api_snapshot_waits_when_later_commits_arrived(host, orders):
    write(orders, "openapi.json", json.dumps(OPENAPI))
    commit(orders, "openapi")
    config = host.configure(project(services=[{"name": "orders", "repo": "../orders", "openapi": "openapi.json"}]))
    changes.mark(host.paths, config)
    commit(orders, "merged", {"app/routes.py": "def create_order(cart):\n    return {}\n"})
    changes.report(host.paths, config)
    doc = json.loads(json.dumps(OPENAPI))
    doc["paths"].pop("/healthz")
    commit(orders, "merged later: drop health", {"openapi.json": json.dumps(doc)})
    marked = changes.mark(host.paths, config)
    assert marked["surface"] == {"orders": "not refreshed: the service's document may already describe the later commits"}
    # So the next comparison still sees the contract change that came with the later commit.
    assert changes.report(host.paths, config)["services"][0]["contract"]["removed"] == 1
