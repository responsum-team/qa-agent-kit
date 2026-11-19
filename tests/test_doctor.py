import sys

from qa_agent import doctor
from conftest import project, write


def rows(result):
    return {row["check"]: row for row in result["checks"]}


def test_doctor_without_a_project_map_points_at_setup(host):
    result, code = doctor.run(host.paths)
    assert code == 1 and result["ok"] is False
    found = rows(result)
    assert found["python"]["ok"] and found["git"]["ok"] and found["repository"]["ok"]
    assert found["project map"]["ok"] is False and "playbooks/setup.md" in found["project map"]["fix"]


def test_doctor_checks_the_checkouts_the_services_and_the_settings(host, orders, answering, monkeypatch):
    monkeypatch.delenv("QA_USER_PASSWORD", raising=False)
    monkeypatch.delenv("QA_USER_EMAIL", raising=False)
    url = answering({"/healthz": (200, {"ok": True}), "/openapi.json": (200, {"openapi": "3.0.0", "paths": {}})})
    write(host.root, ".env", f"ORDERS_URL={url}\nQA_USER_EMAIL=ada@example.test\nQA_USER_PASSWORD=\n")
    write(host.root, "tests/api/test_smoke.py", "def test_smoke():\n    assert True\n")
    host.configure(project(
        services=[
            {"name": "orders", "repo": "../orders", "branch": "release", "url_env": "ORDERS_URL", "url": "http://127.0.0.1:9",
             "health": "/healthz", "openapi": "/openapi.json"},
            {"name": "web", "repo": "../web", "url": "http://127.0.0.1:9", "health": "/login"},
            {"name": "mailer"},
        ],
        suites=[
            {"name": "api", "levels": ["api"], "command": f"{sys.executable} -m pytest --junitxml={{junit}}", "select": "tests/api"},
            {"name": "e2e", "levels": ["e2e"], "workdir": "e2e", "command": "QA_JUNIT_FILE={junit} no-such-runner test"},
        ],
        auth={"env": ["QA_USER_EMAIL", "QA_USER_PASSWORD"]},
        off_limits=["../reference"],
    ))
    result, code = doctor.run(host.paths)
    found = rows(result)
    assert code == 1 and result["ok"] is False

    # The URL in the env file wins over the default in the map; the service answers there.
    assert found["orders: running"]["ok"] is True and url in found["orders: running"]["detail"]
    assert found["orders: openapi"]["ok"] is True
    # Being on another branch than the one merged work lands on is worth knowing, and no reason to stop.
    assert found["orders: code"]["ok"] is None and "merged work is expected on release" in found["orders: code"]["detail"]
    assert found["web: code"]["ok"] is False and "not a git checkout" in found["web: code"]["detail"]
    assert found["web: running"]["ok"] is False and "never starts or stops" in found["web: running"]["fix"]
    assert found["mailer: code"]["ok"] is None and found["mailer: running"]["ok"] is None
    # Settings are reported by name, never by value.
    assert found["sign-in settings"]["ok"] is False and found["sign-in settings"]["detail"] == "not set: QA_USER_PASSWORD"
    assert "ada@example.test" not in str(result)
    assert found["env file"]["ok"] is True
    assert found["suite api"]["ok"] is True
    assert found["suite e2e"]["ok"] is False
    assert found["team library"]["ok"] is None and "fix" not in found["team library"]
    assert found["off limits"]["detail"] == "never read: ../reference"

    # A copy of the team's library that was pasted in and not installed from is worth a line.
    write(host.root, ".qa-agent/library-source/team-library/rules/naming.mdc", "---\ndescription: Names\n---\nNames.\n")
    row = rows(doctor.run(host.paths)[0])["team library"]
    assert row["ok"] is None and row["detail"] == "none installed; a copy of a library is pasted at .qa-agent/library-source/team-library"
    assert "library install" in row["fix"]


def test_doctor_passes_when_everything_is_in_place(host, orders, answering):
    url = answering({"/healthz": (200, {"ok": True})})
    write(host.root, ".env", "QA_USER_PASSWORD=correct-horse\n")
    host.configure(project(
        services=[{"name": "orders", "repo": "../orders", "branch": "main", "url": url, "health": "/healthz"}],
        auth={"env": ["QA_USER_PASSWORD"]},
    ))
    result, code = doctor.run(host.paths)
    assert code == 0 and result["ok"] is True
    assert all("fix" not in row for row in result["checks"] if row["ok"] is True)
    assert "correct-horse" not in str(result)


def test_an_env_file_git_would_commit_is_a_problem(host, orders):
    write(host.root, "settings/test.env", "QA_USER_PASSWORD=correct-horse\n")
    host.configure(project(environment={"name": "local", "writes": "own-data", "env_file": "settings/test.env"}))
    result, code = doctor.run(host.paths)
    row = rows(result)["env file"]
    assert code == 1 and row["ok"] is False and "is not git-ignored" in row["detail"] and ".gitignore" in row["fix"]


def test_the_kit_sits_at_the_top_of_its_repository(tmp_path):
    from conftest import Host, git_init

    repo = git_init(tmp_path / "mono")
    host = Host(repo / "services" / "api")
    (host.root / ".qa-agent").mkdir(parents=True)
    host.configure(project(services=[{"name": "api"}]))
    result, code = doctor.run(host.paths)
    row = rows(result)["repository"]
    assert code == 1 and row["ok"] is False and "is a folder inside the repository" in row["detail"]
    assert "at the top of the repository" in row["fix"]


def test_a_suite_that_is_not_ready_says_what_makes_it_ready(host, orders):
    write(orders, "notes.txt", "the developer's unsaved work\n")
    host.configure(project(
        services=[{"name": "orders", "repo": "../orders", "branch": "main", "writable": ["tests/**"]}],
        suites=[
            {"name": "orders-unit", "levels": ["unit"], "repo": "orders", "command": f"{sys.executable} -m pytest --junitxml={{junit}}"},
            {"name": "e2e", "levels": ["e2e"], "command": "QA_JUNIT_FILE={junit} no-such-runner test", "setup": ["npm ci"]},
            {"name": "load", "levels": ["app"], "command": "no-such-tool --junit {junit}"},
        ],
    ))
    result, code = doctor.run(host.paths)
    found = rows(result)
    # A closed worktree and a runner the suite's own setup installs are not faults; a missing runner without setup is.
    assert found["suite orders-unit"]["ok"] is None and "whose QA worktree is not open" in found["suite orders-unit"]["detail"]
    assert found["suite orders-unit"]["fix"] == "`qa.py worktree open --service orders` before running it"
    assert found["suite e2e"]["ok"] is None and "`qa.py run e2e --setup`" in found["suite e2e"]["fix"]
    assert found["suite load"]["ok"] is False and "'no-such-tool' is not on PATH" in found["suite load"]["detail"]
    assert code == 1
    assert "1 uncommitted file(s), the person's work in progress" in found["orders: code"]["detail"]


def test_the_agreed_limits_are_checked(host, orders):
    from qa_agent.config import agree

    config = host.configure(project(services=[{"name": "orders", "repo": "../orders", "branch": "main", "writable": ["tests/**"]}]))
    assert "agreed limits" not in rows(doctor.run(host.paths)[0])                 # before setup reaches the agreement
    agree(host.paths, config, "tests for orders go under tests/ only")
    assert rows(doctor.run(host.paths)[0])["agreed limits"]["ok"] is True
    data = config.data
    data["services"][0]["writable"] = ["**"]
    host.configure(data)
    result, code = doctor.run(host.paths)
    row = rows(result)["agreed limits"]
    assert code == 1 and row["ok"] is False and row["detail"] == "changed since the person agreed: services.orders.writable"
