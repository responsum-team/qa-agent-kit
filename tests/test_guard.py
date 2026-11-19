import pytest

from qa_agent import guard, worktree
from qa_agent.catalog import Catalog
from qa_agent.util import QAError, write_json
from conftest import commit, project, sh, write

BASE = {"service": "orders", "level": "api", "surface": "POST /orders", "expect": "x", "intent": "x"}
GOOD_TEST = "def test_ok(client):\n    assert client.get('/orders').status_code == 200\n"


@pytest.fixture
def ready(host, orders):
    """A QA repository on a QA branch with a clean first commit."""
    host.configure(project(
        services=[{"name": "orders", "repo": "../orders", "branch": "main", "writable": ["tests/**"]}],
        auth={"env": ["QA_USER", "QA_PASSWORD"]},
    ))
    commit(host.root, "set up")
    worktree.ensure_host_branch(host.paths, host.config, "baseline")
    return host


def check(host, **kwargs):
    result, code = guard.run(host.paths, host.config, **kwargs)
    assert (code == 0) == result["ok"]
    return result


def whats(result, name=None):
    return [f"{v['where']}: {v['what']}" for v in result["violations"] if name is None or v["check"] == name]


def test_a_clean_change_passes(ready):
    write(ready.root, "tests/api/test_orders.py", GOOD_TEST)
    result = check(ready)
    assert result == {
        "ok": True, "files_checked": {".": 1}, "violations": [],
        "warnings": ["the state of the files outside the tests was not recorded yet; it is now, and later changes are compared with it"],
    }
    assert "warnings" not in check(ready)


def test_work_must_be_on_a_qa_branch(ready):
    sh(ready.root, "git", "checkout", "-q", "main")
    assert whats(check(ready), "branch") == [".: on 'main', not on a QA branch (qa-agent/…)"]


def test_the_first_commit_of_a_new_repository_needs_no_branch(host, orders):
    host.configure(project())
    assert check(host)["ok"] is True


def test_a_folder_that_is_not_a_repository(tmp_path):
    from conftest import Host

    host = Host(tmp_path / "plain")
    (tmp_path / "plain" / ".qa-agent").mkdir(parents=True)
    host.configure(project(services=[{"name": "orders"}]))
    with pytest.raises(QAError, match="not a git repository"):
        guard.run(host.paths, host.config)


def test_the_env_file_and_sign_in_state_stay_out_of_git(ready):
    data = ready.config.data
    data["auth"]["state_files"] = [".auth/user.json", "state/admin.json"]
    ready.configure(data)
    write(ready.root, ".env", "QA_PASSWORD=correct-horse\n")           # ignored by the kit's lines in .gitignore: fine
    write(ready.root, ".auth/user.json", "{}")                          # so is this
    write(ready.root, "state/admin.json", "{}")                         # not ignored
    assert whats(check(ready), "secrets") == ["state/admin.json: is not git-ignored"]
    sh(ready.root, "git", "add", "-f", ".env")
    assert ".env: is tracked by git" in whats(check(ready), "secrets")


def test_raw_output_worktrees_and_a_pasted_library_stay_out_of_git(ready):
    write(ready.root, ".qa-agent/runs/20260101T000000Z-api/junit.xml", "<testsuite/>")
    # A pasted copy of the team's library holds what the team wrote, samples of tokens and tests of its own included.
    write(ready.root, ".qa-agent/library-source/skills/sign-in/SKILL.md", "Use a token like ghp_" + "a" * 36 + "\n")
    write(ready.root, ".qa-agent/library-source/skills/sign-in/tests/test_skill.py", "def test_nothing():\n    pass\n")
    result = check(ready)
    assert result["ok"] is True and result["files_checked"] == {".": 0}
    (ready.root / ".qa-agent/.gitignore").unlink()
    (ready.root / ".git/info/exclude").write_text("")
    found = check(ready)["violations"]
    assert [v["where"] for v in found if v["what"] == "is not git-ignored"] == [".qa-agent/runs", ".qa-agent/library-source"]
    # What the copy holds is the team's and is not judged: the one finding is that it would be committed.
    assert [v["where"] for v in found if "library-source/" in v["where"]] == []
    worktree.ensure_host_branch(ready.paths, ready.config, "baseline")   # writes the ignore lines again
    assert [v for v in check(ready)["violations"] if v["what"] == "is not git-ignored"] == []


def test_secret_values_and_token_shapes_are_found(ready, monkeypatch):
    write(ready.root, ".env", "QA_USER=ada@example.test\nQA_PASSWORD=correct-horse\nORDERS_URL=http://127.0.0.1:8000\nAPI_TOKEN=changeme\n")
    monkeypatch.setenv("QA_PASSWORD", "ignored-because-the-file-wins")
    write(ready.root, "tests/conftest.py", "\n".join([
        "import os",
        "USER = 'ada@example.test'",
        "PASSWORD = os.environ['QA_PASSWORD']",
        "FALLBACK = 'correct-horse'",
        "URL = 'http://127.0.0.1:8000'",
        "TOKEN = 'changeme'",
        "KEY = 'ghp_" + "a" * 36 + "'",
        "SAMPLE = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXNhbXBsZQ'  # qa-agent: allow-secret a made-up token for the bad-token test",
    ]))
    assert whats(check(ready), "secrets") == [
        "tests/conftest.py:2: contains the value of QA_USER",
        "tests/conftest.py:4: contains the value of QA_PASSWORD",
        "tests/conftest.py:7: looks like a GitHub token",
    ]
    report = str(check(ready))
    assert "correct-horse" not in report and "ada@example.test" not in report      # the guard never prints a secret


def test_focused_tests_fixed_waits_and_silent_skips(ready):
    catalog = Catalog(ready.paths, ready.config)
    catalog.add([{**BASE, "case": name} for name in ("a defect", "still planned")])
    write(ready.root, "tests/test_marks.py", "\n".join([
        "import pytest, time",
        "",
        "@pytest.mark.xfail(reason='B-0001: an empty cart is accepted', strict=True)",
        "def test_defect(client):",
        "    assert client.post('/orders').status_code == 422",
        "",
        "@pytest.mark.skip(reason='B-0002 later')",
        "def test_planned_but_skipped(): assert True",
        "",
        "@pytest.mark.skip(reason='no reason')",
        "def test_no_id(): assert True",
        "",
        "@pytest.mark.skipif(not HAS_BROWSER, reason='needs a browser')  # qa-agent: allow-skip runs only where a browser is installed",
        "def test_environment(): assert True",
        "",
        "def test_waits(client):",
        "    time.sleep(2)",
        "    assert client.get('/orders').status_code == 200",
        "",
        "def poll(check):",
        "    for _ in range(50):",
        "        if check(): return",
        "        time.sleep(0.1)  # qa-agent: allow-sleep polling with a five second deadline",
        "    # time.sleep(1) in a comment is not code",
    ]))
    write(ready.root, "e2e/cart.spec.ts", "\n".join([
        "test.only('focused', async ({ page }) => { await expect(page).toHaveURL('/'); });",
        "test('waits', async ({ page }) => { await page.waitForTimeout(500); await expect(page).toHaveTitle('x'); });",
        "test.fixme('broken', async () => {});",
        "test('conditional', async ({ page, browserName }) => {",
        "  // qa-agent: allow-skip the upload dialog cannot be driven in webkit",
        "  test.skip(browserName === 'webkit', 'no upload dialog');",
        "  await expect(page).toHaveTitle('x');",
        "});",
    ]))
    write(ready.root, "tests/test_marks.py", (ready.root / "tests/test_marks.py").read_text())
    catalog.set_status("B-0001", "defect", note="an empty cart is accepted", tests=["tests/test_marks.py::test_defect"])
    found = whats(check(ready), "hygiene")
    assert found == [
        "e2e/cart.spec.ts:1: a focused test (.only) would hide every other test",
        "e2e/cart.spec.ts:2: a fixed wait",
        "e2e/cart.spec.ts:3: a test is switched off or expected to fail without a behaviour id",
        "tests/test_marks.py:7: a test is switched off, but B-0002 is planned",
        "tests/test_marks.py:10: a test is switched off or expected to fail without a behaviour id",
        "tests/test_marks.py:17: a fixed wait",
    ]


def test_tests_that_assert_nothing(ready):
    write(ready.root, "tests/test_empty.py", "\n".join([
        "def test_calls_only(client):",
        "    client.get('/orders')",
        "",
        "def test_helper_asserts(client):",
        "    # qa-agent: allow-no-assert expect_order_listed raises when the order is absent",
        "    order_listed(client)",
        "",
        "def test_real(client):",
        "    assert client.get('/orders').json() == []",
    ]))
    write(ready.root, "e2e/a.spec.ts", "test('opens', async ({ page }) => { await page.goto('/'); });\n")
    write(ready.root, "e2e/pages/cart.ts", "export async function open(page) { await page.goto('/cart'); }\n")
    assert whats(check(ready), "hygiene") == [
        "e2e/a.spec.ts:1: test 'opens' asserts nothing",
        "tests/test_empty.py:1: test 'test_calls_only' asserts nothing",
    ]


def test_the_installed_kit_may_not_be_edited(ready):
    kit = ready.root / ".qa-agent/kit"
    write(kit, "MANUAL.md", "the rules\n")
    write(kit, "playbooks/sync.md", "sync\n")
    write_json(ready.paths.kit_lock, {"files": {
        "MANUAL.md": guard.sha256_bytes(b"the rules\n"), "playbooks/sync.md": guard.sha256_bytes(b"sync\n"),
    }})
    assert check(ready)["ok"] is True
    write(kit, "MANUAL.md", "the rules, relaxed\n")
    (kit / "playbooks/sync.md").unlink()
    assert whats(check(ready), "kit") == [
        ".qa-agent/kit/MANUAL.md: a kit file was edited", ".qa-agent/kit/playbooks/sync.md: a kit file is missing",
    ]


def test_a_broken_catalog_blocks_the_commit(ready):
    catalog = Catalog(ready.paths, ready.config)
    write(ready.root, "tests/test_a.py", GOOD_TEST)
    catalog.add({**BASE, "case": "lists orders"})
    catalog.link("B-0001", ["tests/test_a.py::test_ok"])
    assert check(ready)["ok"] is True
    (ready.root / "tests/test_a.py").unlink()         # deleting a linked test is not a way to get green
    assert whats(check(ready), "catalog") == [".qa-agent/state/catalog.json: B-0001: tests/test_a.py does not exist in ."]


def test_a_service_checkout_must_not_change_once_its_state_is_recorded(ready, orders):
    write(orders, "notes.txt", "the developer's own uncommitted work\n")
    assert check(ready)["ok"] is True                            # recorded as it was found
    write(orders, "app/routes.py", "def create_order(cart):\n    return {'id': 99}\n")
    write(orders, "tests/test_new.py", "def test_x(): assert True\n")
    result = check(ready)
    found = [v for v in result["violations"] if v["check"] == "scope"]
    assert [v["where"] for v in found] == ["../orders/app/routes.py", "../orders/tests/test_new.py"]
    assert all(v["what"].startswith("a file in a service's checkout changed since its state was recorded (20") for v in found)
    # The way out never discards anyone's work: put your own change back by hand, or record that it is the person's.
    assert "Never discard changes with git" in found[0]["fix"] and "--accept-checkouts" in found[0]["fix"]
    assert "git checkout --" not in str(result) and "git -C" not in str(result)
    assert check(ready)["ok"] is False                           # asking again does not make it go away
    result = check(ready, accept_checkouts=True)
    assert result["ok"] is True and "as the person's own work" in result["warnings"][0]
    assert check(ready)["ok"] is True
    # A new commit in the checkout (the person pulled) is reported, with what arrived, and recorded.
    commit(orders, "the person's commit")
    result = check(ready)
    assert result["ok"] is True
    assert "1 new commit(s), the newest \"the person's commit\"" in result["warnings"][0]
    assert "warnings" not in check(ready)


def test_a_service_checkout_on_a_qa_branch_is_a_violation(ready, orders):
    assert check(ready)["ok"] is True
    sh(orders, "git", "checkout", "-q", "-b", "qa-agent/tests-in-the-wrong-place")
    result = check(ready)
    assert whats(result, "scope") == ["../orders: the checkout of 'orders' is on the QA branch 'qa-agent/tests-in-the-wrong-place'"]
    assert "theirs to do" in result["violations"][0]["fix"]
    assert check(ready, accept_checkouts=True)["ok"] is False    # accepting file changes does not accept this
    sh(orders, "git", "checkout", "-q", "main")
    assert check(ready)["ok"] is True


def test_services_in_one_checkout_are_watched_once(host, workspace):
    mono = workspace / "mono"
    from conftest import git_init

    git_init(mono)
    commit(mono, "first", {"api/app.py": "x = 1\n", "web/app.ts": "export const x = 1;\n"})
    host.configure(project(services=[
        {"name": "api", "repo": "../mono", "branch": "main", "writable": ["api/tests/**", "web/tests/**"]},
        {"name": "web", "repo": "../mono", "branch": "main"},
    ]))
    commit(host.root, "set up")
    worktree.ensure_host_branch(host.paths, host.config, "baseline")
    assert check(host)["ok"] is True
    write(mono, "web/app.ts", "export const x = 2;\n")
    assert [v["where"] for v in check(host)["violations"]] == ["../mono/web/app.ts"]     # once, though two services live there


def test_in_a_service_worktree_only_test_files_may_change(ready, orders):
    paths, config = ready.paths, ready.config
    worktree.open_worktree(paths, config, config.service("orders"))
    tree = paths.worktrees / "orders"
    commit(tree, "tests", {"tests/test_cart.py": "def test_total():\n    assert 2 + 2 == 4\n"})
    assert check(ready)["files_checked"] == {".": 0, ".qa-agent/worktrees/orders": 1}
    commit(tree, "sneaky", {"app/routes.py": "def create_order(cart):\n    return {'id': 5}\n"})   # committed app code
    write(tree, "app/new_module.py", "x = 1\n")                                                   # and uncommitted
    write(tree, "tests/test_wait.py", "import time\ndef test_w():\n    time.sleep(1)\n    assert True\n")
    result = check(ready)
    assert whats(result, "scope") == [
        ".qa-agent/worktrees/orders/app/new_module.py: outside the places tests may be written in 'orders'",
        ".qa-agent/worktrees/orders/app/routes.py: outside the places tests may be written in 'orders'",
    ]
    assert whats(result, "hygiene") == [".qa-agent/worktrees/orders/tests/test_wait.py:3: a fixed wait"]
    # Merged work brought into the QA branch is not the agent's change.
    sh(tree, "git", "reset", "-q", "--hard", "HEAD~1")
    sh(tree, "git", "clean", "-fdq")
    commit(orders, "merged", {"app/pricing.py": "def price(): ...\n"})
    worktree.update_worktree(paths, config, config.service("orders"))
    assert check(ready)["ok"] is True


def only(repo, message, *paths):
    """Commit the named paths and nothing else, as the agent does: other files may be the person's unsaved work."""
    sh(repo, "git", "add", "--", *paths)
    sh(repo, "git", "commit", "-q", "-m", message)


@pytest.fixture
def inside(host):
    """The kit inside an application's own repository, on a QA branch, with the person's unsaved work around."""
    root = host.root
    host.configure(project(
        services=[{"name": "app", "repo": ".", "branch": "main", "writable": ["tests/**", "playwright.config.ts"]}],
        placement="service", environment={"name": "local", "writes": "own-data", "env_file": ".qa-agent/.env"},
    ))
    commit(root, "the application", {
        "src/app.py": "def run(): ...\n", "src/legacy.py": "OLD = 1\n",
        "tests/test_app.py": "def test_run():\n    assert True\n",
    })
    worktree.ensure_host_branch(host.paths, host.config, "baseline")
    write(root, "src/wip.py", "TOKEN = 'ghp_" + "b" * 36 + "'  # the developer's unsaved work\n")
    return host


def test_inside_an_application_repository_only_test_places_may_change(inside):
    root = inside.root
    write(root, "tests/api/test_orders.py", GOOD_TEST)
    write(root, "playwright.config.ts", "export default { retries: 0 };\n")
    write(root, "AGENTS.md", "pointer\n")
    write(root, ".qa-agent/state/progress.json", "{}")
    write(root, ".qa-agent/.env", "QA_PASSWORD=correct-horse\n")
    result = check(inside)
    # The person's unsaved application file is neither a scope problem nor read for secrets: it is not the agent's.
    assert result["ok"] is True and result["files_checked"] == {".": 4}
    only(root, "tests", "tests", "playwright.config.ts", "AGENTS.md", ".qa-agent")
    assert ".qa-agent/.env" not in sh(root, "git", "ls-files")           # ignored by the QA folder's own .gitignore

    write(root, "src/app.py", "def run(): return 1\n")                  # application code, edited after the record
    write(root, ".gitignore", "dist/\n")                                # the application's file as well
    found = [v for v in check(inside)["violations"] if v["check"] == "scope"]
    assert [v["where"] for v in found] == [".gitignore", "src/app.py"]
    assert all(v["what"].startswith("a file outside the places tests may be written changed since its state was recorded (20") for v in found)
    assert "Never discard changes with git" in found[0]["fix"]
    (root / ".gitignore").unlink()

    only(root, "committed anyway", "src/app.py")                         # found after the commit too, against main
    result = check(inside)
    assert whats(result, "scope") == [
        "src/app.py: outside the places tests may be written in this repository, and committed on this branch",
    ]
    fix = result["violations"][0]["fix"]
    assert "git show main:<path>" in fix and "history is not rewritten" in fix
    sh(root, "git", "rm", "-q", "src/legacy.py")                         # removing application code is a change too
    sh(root, "git", "commit", "-q", "-m", "removed")
    assert [v["where"] for v in check(inside)["violations"]] == ["src/app.py", "src/legacy.py"]
    # The way back: commit the files as they are on main again.
    write(root, "src/app.py", sh(root, "git", "show", "main:src/app.py") + "\n")
    write(root, "src/legacy.py", sh(root, "git", "show", "main:src/legacy.py") + "\n")
    only(root, "put back", "src/app.py", "src/legacy.py")
    assert check(inside)["ok"] is True


def test_the_persons_later_changes_in_an_application_repository_are_accepted_with_a_reason(inside):
    root = inside.root
    assert check(inside)["ok"] is True
    write(root, "src/app.py", "def run(): return 'the developer kept working'\n")
    assert [v["where"] for v in check(inside)["violations"]] == ["src/app.py"]
    assert check(inside, accept_checkouts=True)["ok"] is True
    assert check(inside)["ok"] is True


def test_all_reads_every_test_file_but_never_judges_the_application(host):
    root = host.root
    host.configure(project(
        services=[{"name": "app", "repo": ".", "branch": "main", "writable": ["tests/**"]}], placement="service",
        environment={"name": "local", "writes": "own-data", "env_file": ".qa-agent/.env"},
    ))
    commit(root, "the application", {
        "src/client.py": "TOKEN = 'ghp_" + "b" * 36 + "'\n",                 # the application's own business
        "package.json": "{\"jest\": {\"retries\": 3}}\n",
        "tests/test_old.py": "import time\ndef test_old():\n    time.sleep(1)\n    assert True\n",   # merged long ago
    })
    worktree.ensure_host_branch(host.paths, host.config, "review")
    assert check(host)["ok"] is True                                    # nothing changed: nothing to read
    result = check(host, everything=True)
    assert whats(result) == ["tests/test_old.py:3: a fixed wait"]
    assert result["files_checked"] == {".": 3}                           # the QA folder's two files and the test


def test_retry_settings_are_not_a_cure_for_flaky_tests(ready):
    root = ready.root
    write(root, "playwright.config.ts", "export default defineConfig({\n  retries: process.env.CI ? 2 : 0,\n  workers: 1,\n});\n")
    write(root, "pytest.ini", "[pytest]\naddopts = -q --reruns 2\n")
    write(root, "tests/test_marks.py", "\n".join([
        "import pytest",
        "",
        "@pytest.mark.flaky(reruns=3)",
        "def test_sometimes(client):",
        "    assert client.get('/orders').status_code == 200",
    ]))
    write(root, "e2e/cart.spec.ts", "\n".join([
        "test.describe.configure({ retries: 2 });",
        "test('cart', async ({ page }) => { await expect(page).toHaveTitle('x'); });",
    ]))
    result = check(ready)
    assert whats(result, "hygiene") == [
        "e2e/cart.spec.ts:1: the test runner is told to retry",
        "playwright.config.ts:2: the test runner is told to retry",
        "pytest.ini:2: the test runner is told to retry",
        "tests/test_marks.py:3: the test runner is told to retry",
    ]
    assert "records them as flaky" in result["violations"][0]["fix"]
    # Zero is no retry; a setting the team's pipeline needs is said so on the line.
    write(root, "playwright.config.ts", "\n".join([
        "export default defineConfig({",
        "  // qa-agent: allow-retry the team's pipeline retries once by policy; `qa.py run` passes --retries=0",
        "  retries: process.env.CI ? 1 : 0,",
        "  timeout: 30_000,",
        "});",
    ]))
    write(root, "pytest.ini", "[pytest]\naddopts = -q\n")
    write(root, "e2e/cart.spec.ts", "test.describe.configure({ retries: 0 });\ntest('cart', async ({ page }) => { await expect(page).toHaveTitle('x'); });\n")
    (root / "tests/test_marks.py").unlink()
    assert check(ready)["ok"] is True
    # The suite's own command line counts as well.
    data = ready.config.data
    data["suites"] = [{"name": "e2e", "levels": ["e2e"], "command": "npx playwright test --retries=2 --reporter=junit > {junit}"}]
    ready.configure(data)
    assert whats(check(ready), "hygiene") == [".qa-agent/project.json suites[e2e].command: the test runner is told to retry"]


def test_the_limits_the_person_agreed_to_do_not_change_quietly(ready):
    from qa_agent.config import agree

    Catalog(ready.paths, ready.config).add({**BASE, "case": "lists orders"})
    result = check(ready)
    assert result["ok"] is True and any("no agreement with the person is recorded" in w for w in result["warnings"])
    agree(ready.paths, ready.config, "tests for orders go under tests/ only; the agent creates its own data")
    assert "warnings" not in check(ready)
    data = ready.config.data
    data["services"][0]["writable"] = ["tests/**", "app/**"]
    data["off_limits"] = []
    data["environment"]["writes"] = "none"
    ready.configure(data)
    result = check(ready)
    assert whats(result, "consent") == [
        ".qa-agent/project.json: 'environment.writes' is \"none\"; the person agreed to \"own-data\"",
        ".qa-agent/project.json: 'services.orders.writable' is [\"app/**\", \"tests/**\"]; the person agreed to [\"tests/**\"]",
    ]
    assert "qa.py config agree" in result["violations"][0]["fix"]
    agree(ready.paths, ready.config, "the person widened it: fixtures under app/ may be written; nothing is written to the environment")
    assert check(ready)["ok"] is True


def test_since_and_all_widen_what_is_checked(ready):
    start = sh(ready.root, "git", "rev-parse", "HEAD")
    commit(ready.root, "a wait slipped in", {"tests/test_w.py": "import time\ndef test_w():\n    time.sleep(1)\n    assert True\n"})
    assert check(ready)["ok"] is True                       # nothing uncommitted
    assert whats(check(ready, since=start), "hygiene") == ["tests/test_w.py:3: a fixed wait"]
    assert whats(check(ready, everything=True), "hygiene") == ["tests/test_w.py:3: a fixed wait"]
    with pytest.raises(QAError, match="is not a commit here"):
        check(ready, since="nope")
