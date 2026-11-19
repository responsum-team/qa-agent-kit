"""The command line itself: how arguments arrive, what is printed, which exit code says what."""

from __future__ import annotations

import io
import json
import shutil

import pytest

from qa_agent import __version__
from qa_agent.cli import main
from conftest import project, write


@pytest.fixture
def cli(host, orders, capsys):
    host.configure(project(services=[{"name": "orders", "repo": "../orders", "branch": "main", "sources": ["app/**"]}]))

    def run(*args, stdin=None, monkeypatch=None):
        if stdin is not None:
            monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        code = main(["--root", str(host.root), *args])
        captured = capsys.readouterr()
        try:
            out = json.loads(captured.out)
        except ValueError:
            out = captured.out
        return code, out, captured.err

    run.host = host
    return run


BEHAVIOUR = {"service": "orders", "level": "api", "surface": "POST /orders", "case": "an empty cart",
             "expect": "422", "intent": "app/routes.py", "sources": ["app/routes.py"]}


def test_exit_codes_tell_done_from_problems_from_cannot(cli):
    code, out, err = cli("catalog", "validate")
    assert code == 0 and out["ok"] is True and err == ""
    code, out, err = cli("catalog", "show", "B-0404")
    assert code == 2 and out == "" and err == "error: No behaviour B-0404 in the catalog\n"
    code, out, _ = cli("catalog", "add", "--json", json.dumps({"service": "nowhere", "level": "api"}))
    assert code == 1 and out["added"] == [] and "'surface' is required" in out["rejected"][0]["problems"]
    code, _, err = cli("catalog", "add", "--json", "{not json")
    assert code == 2 and "not valid JSON" in err


def test_json_arrives_inline_from_a_file_or_on_standard_input(cli, monkeypatch):
    code, out, _ = cli("catalog", "add", "--json", json.dumps(BEHAVIOUR))
    assert code == 0 and out["added"] == ["B-0001"]
    write(cli.host.root, "more.json", json.dumps([{**BEHAVIOUR, "case": "a full cart", "expect": "201"}]))
    assert cli("catalog", "add", "--file", "more.json")[1]["added"] == ["B-0002"]
    piped = json.dumps({**BEHAVIOUR, "case": "no credentials", "expect": "401", "priority": 1})
    assert cli("catalog", "add", stdin=piped, monkeypatch=monkeypatch)[1]["added"] == ["B-0003"]
    code, _, err = cli("catalog", "add", "--file", "missing.json")
    assert code == 2 and "missing.json does not exist" in err

    code, out, _ = cli("catalog", "update", "B-0002", "--json", '{"expect": "201 with the order", "priority": 3}')
    assert code == 0 and out["expect"] == "201 with the order"
    code, _, err = cli("catalog", "update", "B-0002", "--json", '{"status": "passing"}')
    assert code == 2 and "Cannot change status" in err


def test_catalog_reading_commands(cli):
    cli("catalog", "add", "--json", json.dumps([
        BEHAVIOUR, {**BEHAVIOUR, "case": "no credentials", "expect": "401", "priority": 1, "area": "sign-in"},
        {**BEHAVIOUR, "level": "unit", "surface": "func total", "case": "no lines", "expect": "0"},
    ]))
    code, out, _ = cli("catalog", "list")
    assert code == 0 and out["count"] == 3 and out["entries"][0] == {
        "id": "B-0001", "status": "planned", "level": "api", "service": "orders", "area": "", "case": "POST /orders: an empty cart", "tests": 0,
    }
    assert cli("catalog", "list", "--level", "unit", "--full")[1]["entries"][0]["repo"] == "orders"
    assert cli("catalog", "list", "--area", "Sign-In")[1]["count"] == 1
    assert cli("catalog", "list", "--limit", "1")[1]["not_shown"] == 2
    out = cli("catalog", "next", "--batch", "2")[1]
    assert [entry["id"] for entry in out["batch"]] == ["B-0002", "B-0001"] and out["planned_left"] == 3
    assert out["batch"][0]["suite"] is None                       # no suite is defined for that level yet
    assert [entry["id"] for entry in cli("catalog", "show", "B-0003", "B-0001")[1]] == ["B-0003", "B-0001"]
    assert cli("catalog", "remove", "B-0003")[1] == {"removed": ["B-0003"]}
    assert cli("catalog", "stats")[1]["total"] == 2


def test_statuses_need_their_reasons(cli):
    cli("catalog", "add", "--json", json.dumps(BEHAVIOUR))
    code, _, err = cli("catalog", "status", "B-0001", "blocked")
    assert code == 2 and "--note is required" in err
    code, out, _ = cli("catalog", "status", "B-0001", "blocked", "--note", "no test account for this role")
    assert code == 0 and out["status"] == "blocked"
    code, _, err = cli("catalog", "status", "B-0001", "quarantined", "--note", "fails now and then")
    assert code == 2 and "needs the test it refers to" in err
    write(cli.host.root, "tests/test_cart.py", "def test_empty_cart():\n    assert True\n")
    code, _, err = cli("catalog", "status", "B-0001", "quarantined", "--note", "fails now and then", "--test", "tests/test_cart.py::test_empty_cart")
    assert code == 2 and "--until YYYY-MM-DD is required" in err
    code, out, _ = cli("catalog", "status", "B-0001", "quarantined", "--note", "fails now and then",
                       "--test", "tests/test_cart.py::test_empty_cart", "--until", "2031-01-15")
    assert code == 0 and out["status"] == "quarantined" and out["tests"] == 1
    with pytest.raises(SystemExit):                                # 'passing' is never set by hand
        cli("catalog", "status", "B-0001", "passing", "--note", "trust me")
    code, out, _ = cli("catalog", "link", "B-0001", "--test", "tests/test_cart.py::test_empty_cart")
    assert code == 0 and out["status"] == "written"                # linking makes the test live again


def test_only_run_takes_runner_arguments(cli):
    code, _, err = cli("catalog", "list", "--", "-k", "x")
    assert code == 2 and "Only `run` takes arguments after `--`" in err
    code, _, err = cli("run", "nowhere", "--", "-k", "x")
    assert code == 2 and "No suite named 'nowhere'" in err


def test_progress_is_kept_and_shown(cli):
    assert cli("progress")[1]["phase"] is None
    assert cli("progress", "--phase", "baseline", "--step", "map", "--note", "setup finished")[1] == {
        "phase": "baseline", "step": "map", "note": "setup finished",
    }
    assert cli("progress", "--step", "scaffold")[1] == {"phase": "baseline", "step": "scaffold", "note": ""}
    code, _, err = cli("progress", "--step", "polish")
    assert code == 2 and "must be one of: map, scaffold, generate, verify, report" in err
    assert cli("progress", "--note", "two suites defined")[1] == {"phase": "baseline", "step": "scaffold", "note": "two suites defined"}
    # A decision is added to the list for the report; where the work stands is left as it was.
    assert cli("progress", "--decision", "pytest for the API suite: the service's own tests use it")[1] == {
        "phase": "baseline", "step": "scaffold", "note": "two suites defined", "decisions_recorded": 1,
    }
    assert cli("progress", "--phase", "sync")[1] == {"phase": "sync", "step": "changes", "note": ""}
    shown = cli("progress")[1]
    assert shown["phase"] == "sync" and [row["step"] for row in shown["earlier_steps"]] == ["map", "scaffold", "scaffold", "changes"]
    assert [d["text"] for d in shown["decisions_for_the_next_report"]] == ["pytest for the API suite: the service's own tests use it"]
    with pytest.raises(SystemExit):
        cli("progress", "--phase", "holiday")


def test_init_starts_setup_and_keeps_secrets_out_of_git(host, orders, capsys):
    from conftest import sh

    def run(*args):
        code = main(["--root", str(host.root), *args])
        return code, json.loads(capsys.readouterr().out)

    code, out = run("init", "--name", "Shop", "--placement", "qa-repo",
                    "--service", "name=orders,repo=../orders,url=http://127.0.0.1:8000,health=/healthz")
    assert code == 0 and out["env_file"] == ".env" and out["env_lines_added"] == [".env.example: ORDERS_URL", ".env: ORDERS_URL"]
    assert out["ignore_lines"][".gitignore"] == "created"
    assert sh(host.root, "git", "check-ignore", ".env") == ".env"
    assert sh(host.root, "git", "check-ignore", ".env.example", check=False) == ""
    # From here on the status page says which setup step is next.
    code, state = run("status", "--json")
    assert state["progress"]["phase"] == "setup" and state["progress"]["step"] == "map"
    assert state["next"].startswith('Continue playbooks/setup.md at "2. Write the project map".')


def test_init_inside_an_application_repository_leaves_its_files_alone(host, capsys):
    from conftest import commit, sh

    commit(host.root, "the application", {".gitignore": "dist/\n", ".env": "DATABASE_URL=postgres://app\n", "src/app.py": "x = 1\n"})
    code = main(["--root", str(host.root), "init", "--name", "Shop", "--placement", "service",
                 "--service", "name=app,repo=.,url=http://127.0.0.1:8000"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["env_file"] == ".qa-agent/.env"
    assert out["env_lines_added"] == [".qa-agent/.env.example: APP_URL", ".qa-agent/.env: APP_URL"]
    assert ".gitignore" not in out["ignore_lines"]
    assert (host.root / ".gitignore").read_text() == "dist/\n" and (host.root / ".env").read_text() == "DATABASE_URL=postgres://app\n"
    changed = sh(host.root, "git", "status", "--porcelain").splitlines()
    assert changed and all(line.startswith("?? .qa-agent/") for line in changed)   # nothing of the application's was touched
    assert sh(host.root, "git", "check-ignore", ".qa-agent/.env") == ".qa-agent/.env"
    # On a branch that does not have the QA folder's own .gitignore, the clone's exclude file still covers it.
    (host.root / ".qa-agent/.gitignore").unlink()
    assert sh(host.root, "git", "check-ignore", ".qa-agent/.env", ".qa-agent/runs/x.xml").splitlines() == [
        ".qa-agent/.env", ".qa-agent/runs/x.xml",
    ]


def test_the_agreement_is_recorded_and_shown(cli):
    code, out, _ = cli("config", "agreed")
    assert code == 1 and out["agreed"] is False
    code, _, err = cli("config", "agree")
    assert code == 2 and "--note is required" in err
    code, out, _ = cli("config", "agree", "--note", "pytest for the API; tests for orders go under tests/ only")
    assert code == 0 and out["limits"]["placement"] == "qa-repo" and out["limits"]["services.orders.writable"] == []
    assert cli("config", "agreed")[0] == 0
    data = json.loads((cli.host.root / ".qa-agent/project.json").read_text())
    data["services"][0]["writable"] = ["tests/**"]
    write(cli.host.root, ".qa-agent/project.json", json.dumps(data))
    code, out, _ = cli("config", "agreed")
    assert code == 1 and out["changed_since"] == [{"field": "services.orders.writable", "agreed": [], "now": ["tests/**"]}]
    code, out, _ = cli("config", "agree", "--note", "the person named tests/ as the place for the service's own tests")
    assert code == 0 and out["changed_since_the_last_agreement"] == ["services.orders.writable"]
    assert cli("config", "agreed")[1]["changed_since"] == []


def test_batches_by_priority_tag_and_offset(cli):
    cli("catalog", "add", "--json", json.dumps([
        {**BEHAVIOUR, "case": f"case {number}", "priority": priority, "tags": tags}
        for number, priority, tags in ((1, 2, []), (2, 1, ["smoke"]), (3, 3, []), (4, 1, []), (5, 2, ["smoke"]))
    ]))
    ids = lambda out: [entry["id"] for entry in out["batch"]]  # noqa: E731
    assert ids(cli("catalog", "next", "--batch", "2")[1]) == ["B-0002", "B-0004"]
    out = cli("catalog", "next", "--batch", "2", "--offset", "2")[1]            # a second writer takes the next two
    assert ids(out) == ["B-0001", "B-0005"] and out["skipped"] == 2 and out["planned_left"] == 5
    out = cli("catalog", "next", "--priority", "1")[1]
    assert ids(out) == ["B-0002", "B-0004"] and out["planned_left"] == 2
    assert ids(cli("catalog", "next", "--tag", "smoke")[1]) == ["B-0002", "B-0005"]
    assert cli("catalog", "list", "--priority", "3")[1]["count"] == 1
    assert cli("catalog", "list", "--tag", "Smoke")[1]["count"] == 2
    assert "--no-record" in cli("catalog", "next")[1]["then"]
    # A status can carry a tag, added to the ones the behaviour has.
    cli("catalog", "status", "B-0005", "blocked", "--note", "the spec and the code disagree", "--tag", "needs-decision")
    assert cli("catalog", "show", "B-0005")[1][0]["tags"] == ["smoke", "needs-decision"]
    assert cli("catalog", "stats")[1]["by_tag"] == {"needs-decision": 1, "smoke": 2}


def test_status_is_a_page_and_also_json(cli):
    code, page, _ = cli("status")
    assert code == 0 and page.startswith("# QA agent: shop (qa-repo), kit " + __version__)
    assert "Next: " in page and "Behaviours: none recorded" in page and "Suites: none defined" in page
    code, state, _ = cli("status", "--json")
    assert code == 0 and state["configured"] is True and state["behaviours"]["total"] == 0


def test_config_and_version(cli, capsys):
    assert cli("config", "validate") == (0, {"ok": True, "errors": []}, "")
    assert cli("config", "show")[1]["services"][0]["writable"] == []        # defaults are filled in
    assert cli("version")[1].strip() == __version__
    write(cli.host.root, ".qa-agent/project.json", json.dumps({"version": 1, "project": {"name": "x"}, "placement": "cloud", "services": []}))
    code, out, _ = cli("config", "validate")
    assert code == 1 and out["errors"] == ["placement: must be one of qa-repo, service", "services: list at least one service"]
    code, _, err = cli("catalog", "list")
    assert code == 2 and "project.json has problems" in err
    code, page, _ = cli("status")
    assert code == 0 and "not set up" in page and "config validate" in page


def test_a_library_pasted_into_the_repository_needs_no_path(cli):
    root = cli.host.root
    rule = "---\ndescription: Assert on the response body\n---\nEvery API test checks the body.\n"
    assert "pasted_copy" not in cli("library", "status")[1]
    write(root, ".qa-agent/library-source/team-library/qa/rules/api-assertions.mdc", rule)
    write(root, ".qa-agent/library-source/team-library/web/rules/components.mdc", "---\ndescription: Component names\n---\nNames.\n")
    assert cli("library", "status")[1]["pasted_copy"] == ".qa-agent/library-source/team-library"

    code, out, _ = cli("library", "scan", "--rules", "qa/**/*.mdc")
    assert code == 0 and out["library"] == ".qa-agent/library-source/team-library"
    assert out["found"] == {"rules": 1, "skills": 0} and out["items"][0]["source"] == "qa/rules/api-assertions.mdc"
    code, out, _ = cli("library", "install", "--rules", "qa/**/*.mdc")
    assert code == 0 and out["installed"] == {"rules": 1, "skills": 0}
    assert out["from"]["path"] == ".qa-agent/library-source/team-library"
    assert cli("config", "show")[1]["library"] == ".qa-agent/library-source/team-library"       # no machine's own path

    # The person replaces the copy with a newer one, in a folder of another name.
    shutil.rmtree(root / ".qa-agent/library-source/team-library")
    write(root, ".qa-agent/library-source/team-library-main/qa/rules/api-assertions.mdc", rule + "And the headers.\n")
    code, out, _ = cli("library", "install", "--refresh")
    assert code == 0 and out["from"]["path"] == ".qa-agent/library-source/team-library-main"
    assert (root / ".qa-agent/library/rules/api-assertions.mdc").read_text().endswith("And the headers.\n")
    assert cli("config", "show")[1]["library"] == ".qa-agent/library-source/team-library-main"
    assert cli("library", "status")[1]["up_to_date"] is True


def test_rules_and_guard_and_branch_through_the_command_line(cli):
    code, out, _ = cli("rules", "--level", "e2e", "--task", "write")
    assert code == 0 and out["then_team_rules"] == []
    code, _, err = cli("guard", "--accept-checkouts")
    assert code == 2 and "needs --note" in err
    code, _, err = cli("guard", "--accept-checkouts", "--note", "theirs")
    assert code == 2 and "needs --note" in err                        # a reason, not a word
    code, _, err = cli("branch", "--name", "baseline")
    assert code == 2 and "has no commit yet" in err
    code, _, err = cli("worktree", "open")
    assert code == 2 and "--service is required" in err
    code, _, err = cli("worktree", "open", "--service", "orders")
    assert code == 2 and "has no 'writable' globs" in err
    code, _, err = cli("surface", "show", "--service", "billing")
    assert code == 2 and "No stored surface for 'billing'" in err
    code, _, err = cli("surface", "coverage", "--service", "billing")
    assert code == 2 and "No service named 'billing'" in err
    code, _, err = cli("library", "scan")
    assert code == 2 and "--path is required" in err and "paste a copy of that folder into .qa-agent/library-source/" in err
    code, _, err = cli("library", "install")
    assert code == 2 and "--path is required" in err
    code, _, err = cli("library", "install", "--refresh")
    assert code == 2 and "nothing to refresh" in err
    code, _, err = cli("report", "--name", "x", "--notes", "missing.md")
    assert code == 2 and "missing.md does not exist" in err
    code, out, _ = cli("changes")
    assert code == 0 and out["next"] == "no sync point for: orders"
    assert cli("runs")[1] == [] and cli("flaky")[1] == []
