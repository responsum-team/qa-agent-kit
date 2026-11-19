import pytest

from qa_agent import worktree
from qa_agent.util import QAError, today
from conftest import commit, project, sh, write


@pytest.fixture
def configured(host, orders):
    host.configure(project(services=[{"name": "orders", "repo": "../orders", "branch": "main", "writable": ["tests/**"]}]))
    return host


def branch(repo) -> str:
    return sh(repo, "git", "branch", "--show-current")


def test_host_branch_needs_a_first_commit(configured):
    with pytest.raises(QAError, match="no commit yet"):
        worktree.ensure_host_branch(configured.paths, configured.config, "baseline")


def test_host_branch_is_created_once_and_reused(configured):
    root = configured.root
    commit(root, "install the kit")
    result = worktree.ensure_host_branch(configured.paths, configured.config, "Baseline run")
    assert result == {"branch": f"qa-agent/baseline-run-{today()}", "created": True, "from": "main"}
    assert branch(root) == result["branch"]
    assert worktree.ensure_host_branch(configured.paths, configured.config, "sync") == {"branch": result["branch"], "created": False}
    assert sh(root, "git", "status", "--porcelain") == ""              # asking for the branch changes no file


def test_unmerged_qa_work_is_continued_not_forked(configured):
    root, paths, config = configured.root, configured.paths, configured.config
    commit(root, "install the kit")
    first = worktree.ensure_host_branch(paths, config, "baseline")["branch"]
    commit(root, "tests", {"tests/test_a.py": "def test_a(): pass\n", ".qa-agent/state/catalog.json": "{}\n"})
    sh(root, "git", "checkout", "-q", "main")                          # the person went back without merging
    assert worktree.waiting_branch(root, "qa-agent/", ".qa-agent/state") == first
    again = worktree.ensure_host_branch(paths, config, "sync")
    assert (again["branch"], again["created"], again["continued"], again["from"]) == (first, False, True, "main")
    assert branch(root) == first and "--fresh" in again["note"]
    # Only when the person asked for a new start.
    sh(root, "git", "checkout", "-q", "main")
    fresh = worktree.ensure_host_branch(paths, config, "sync", fresh=True)
    assert fresh == {"branch": f"qa-agent/sync-{today()}", "created": True, "from": "main", "left_unmerged": first}


def at(monkeypatch, day: int) -> None:
    """Commits made from here on carry this date: git orders branches by it, to the second."""
    for name in ("GIT_COMMITTER_DATE", "GIT_AUTHOR_DATE"):
        monkeypatch.setenv(name, f"2026-03-{day:02d}T10:00:00+00:00")


def needs_merge_tree():
    version = tuple(int(part) for part in sh(".", "git", "version").split()[2].split(".")[:2])
    if version < (2, 38):
        pytest.skip("git older than 2.38 cannot tell a squash-merged branch by its content")


def test_a_branch_merged_in_another_form_is_not_waiting(configured, monkeypatch):
    root, paths, config = configured.root, configured.paths, configured.config
    at(monkeypatch, 1)
    commit(root, "install the kit")
    first = worktree.ensure_host_branch(paths, config, "baseline")["branch"]
    at(monkeypatch, 2)
    commit(root, "tests", {"tests/test_a.py": "def test_a(): pass\n", ".qa-agent/state/catalog.json": "{}\n"})
    sh(root, "git", "checkout", "-q", "main")
    at(monkeypatch, 3)
    sh(root, "git", "merge", "--squash", "-q", first)
    sh(root, "git", "commit", "-q", "-m", "QA baseline, squashed")
    # Later work changes the same records: neither the histories nor the content match that branch any more,
    # yet the records here are newer than its last commit.
    commit(root, "a later sync, squashed", {".qa-agent/state/catalog.json": "{\"entries\": []}\n"})
    assert worktree.waiting_branch(root, "qa-agent/", ".qa-agent/state") is None
    result = worktree.ensure_host_branch(paths, config, "sync")
    assert result == {"branch": f"qa-agent/sync-{today()}", "created": True, "from": "main"}


def test_a_squash_merged_branch_is_recognised_by_its_content(configured, monkeypatch):
    needs_merge_tree()
    root = configured.root
    at(monkeypatch, 1)
    commit(root, "install the kit")
    first = worktree.ensure_host_branch(configured.paths, configured.config, "baseline")["branch"]
    at(monkeypatch, 2)
    commit(root, "tests", {"tests/test_a.py": "def test_a(): pass\n"})
    sh(root, "git", "checkout", "-q", "main")
    assert worktree.merged_into_head(root, first) is False
    sh(root, "git", "merge", "--squash", "-q", first)
    sh(root, "git", "commit", "-q", "-m", "QA baseline, squashed")
    assert worktree.merged_into_head(root, first) is True and worktree.waiting_branch(root, "qa-agent/") is None


def test_in_an_application_repository_an_older_qa_branch_is_not_switched_to(host):
    config = host.configure(project(
        services=[{"name": "app", "repo": ".", "branch": "main", "writable": ["tests/**"]}], placement="service",
    ))
    root, paths = host.root, host.paths
    commit(root, "the application", {"src/app.py": "VERSION = 1\n"})
    first = worktree.ensure_host_branch(paths, config, "baseline")["branch"]
    commit(root, "tests", {"tests/test_a.py": "def test_a(): pass\n"})
    sh(root, "git", "checkout", "-q", "main")
    assert worktree.ensure_host_branch(paths, config, "sync")["continued"] is True     # main has not moved: nothing is rewound
    sh(root, "git", "checkout", "-q", "main")
    commit(root, "the application moved on", {"src/app.py": "VERSION = 2\n"})
    with pytest.raises(QAError, match="would put older application code in place"):
        worktree.ensure_host_branch(paths, config, "sync")
    assert branch(root) == "main" and (root / "src/app.py").read_text() == "VERSION = 2\n"
    assert worktree.ensure_host_branch(paths, config, "sync", fresh=True)["left_unmerged"] == first


def test_an_empty_prefix_means_the_current_branch(configured):
    data = configured.config.data
    data["git"]["branch_prefix"] = ""
    config = configured.configure(data)
    commit(configured.root, "install")
    result = worktree.ensure_host_branch(configured.paths, config, "baseline")
    assert result["branch"] == "main" and result["created"] is False


def test_open_write_update_close(configured, orders):
    paths, config = configured.paths, configured.config
    service = config.service("orders")
    opened = worktree.open_worktree(paths, config, service)
    target = paths.worktrees / "orders"
    assert opened["open"] is True and opened["created"] is True and opened["new_branch"] is True
    assert opened["branch"] == f"qa-agent/tests-{today()}" and opened["path"] == ".qa-agent/worktrees/orders"
    assert (target / "app/routes.py").exists()
    assert branch(orders) == "main"                                   # the service's own checkout did not move
    assert worktree.open_worktree(paths, config, service)["created"] is False

    commit(target, "unit tests for the cart", {"tests/test_cart.py": "def test_total(): pass\n"})
    assert not (orders / "tests/test_cart.py").exists()
    state = worktree.describe(paths, config, service)
    assert state["commits_ahead_of_checkout"] == 1 and state["commits_behind_checkout"] == 0 and state["uncommitted"] == 0

    commit(orders, "merged work", {"app/routes.py": "def create_order(cart):\n    return {'id': 2}\n"})
    assert worktree.describe(paths, config, service)["commits_behind_checkout"] == 1
    updated = worktree.update_worktree(paths, config, service)
    assert updated["updated"] is True and updated["commits_behind_checkout"] == 0
    assert "'id': 2" in (target / "app/routes.py").read_text()
    assert worktree.update_worktree(paths, config, service)["updated"] is False

    write(target, "tests/test_draft.py", "def test_draft(): pass\n")
    with pytest.raises(QAError, match="uncommitted"):
        worktree.close_worktree(paths, config, service)
    with pytest.raises(QAError, match="uncommitted changes"):
        worktree.update_worktree(paths, config, service)
    closed = worktree.close_worktree(paths, config, service, force=True)
    assert closed["open"] is False and closed["branch"] == opened["branch"] and not target.exists()
    assert opened["branch"] in sh(orders, "git", "branch", "--list", "qa-agent/*")

    # Opening again continues the QA branch that is still waiting to be merged.
    again = worktree.open_worktree(paths, config, service)
    assert again["branch"] == opened["branch"] and again["new_branch"] is False
    assert (target / "tests/test_cart.py").exists()
    assert [row["service"] for row in worktree.status(paths, config)] == ["orders"]


def test_only_the_newest_qa_branch_of_a_service_can_be_waiting(configured, orders, monkeypatch):
    needs_merge_tree()
    paths, config = configured.paths, configured.config
    service = config.service("orders")
    target = paths.worktrees / "orders"

    def squash(branch_name: str) -> None:
        worktree.close_worktree(paths, config, service)
        sh(orders, "git", "merge", "--squash", "-q", branch_name)
        sh(orders, "git", "commit", "-q", "-m", "QA tests, squashed")

    at(monkeypatch, 1)
    first = worktree.open_worktree(paths, config, service, label="first")["branch"]
    commit(target, "tests", {"tests/test_cart.py": "def test_total():\n    assert 1\n"})
    at(monkeypatch, 2)
    squash(first)
    second = worktree.open_worktree(paths, config, service, label="second")
    assert second["branch"] == f"qa-agent/second-{today()}" and second["new_branch"] is True   # merged: a new branch
    at(monkeypatch, 3)
    commit(target, "more tests", {"tests/test_cart.py": "def test_total():\n    assert 2\n"})
    at(monkeypatch, 4)
    squash(second["branch"])
    # The first branch no longer matches what is merged, and it is not taken up again: only the newest counts.
    assert worktree.merged_into_head(orders, first) is False
    third = worktree.open_worktree(paths, config, service, label="third")
    assert third["branch"] == f"qa-agent/third-{today()}" and third["new_branch"] is True


def test_a_merge_conflict_changes_nothing_and_is_reported(configured, orders):
    paths, config = configured.paths, configured.config
    service = config.service("orders")
    worktree.open_worktree(paths, config, service)
    target = paths.worktrees / "orders"
    commit(target, "qa edit", {"tests/test_models.py": "def test_order():\n    assert 1\n"})
    commit(orders, "dev edit", {"tests/test_models.py": "def test_order():\n    assert 2\n"})
    with pytest.raises(QAError, match="conflicts"):
        worktree.update_worktree(paths, config, service)
    assert sh(target, "git", "status", "--porcelain") == ""


def test_worktrees_need_writable_places_and_a_separate_repository(host, orders):
    config = host.configure(project(
        services=[{"name": "app", "repo": "."}, {"name": "orders", "repo": "../orders"}], placement="service",
    ))
    with pytest.raises(QAError, match="no 'writable' globs"):
        worktree.open_worktree(host.paths, config, config.service("orders"))
    with pytest.raises(QAError, match="is this repository"):
        worktree.open_worktree(host.paths, config, config.service("app"))
    assert worktree.status(host.paths, config) == [{"service": "orders", "open": False}]
