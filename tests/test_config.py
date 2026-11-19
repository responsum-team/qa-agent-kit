import json

import pytest

from qa_agent.config import (
    Config, agree, agreed, consent_changes, consent_fields, init_project, parse_service_spec, url_env_name, validate,
)
from qa_agent.util import QAError
from conftest import git_init, project, write


def test_a_minimal_map_is_valid_and_gets_defaults(host):
    config = host.configure(project())
    assert config.name == "shop"
    assert config.suites == []
    assert config["bindings"] == {"always": [], "level": {}, "task": {}}
    assert config["git"]["branch_prefix"] == "qa-agent/"
    assert config["off_limits"] == []
    assert config.service("orders")["writable"] == []


@pytest.mark.parametrize("change, expected", [
    ({"version": 2}, "version: must be 1"),
    ({"placement": "elsewhere"}, "placement: must be one of"),
    ({"services": []}, "services: list at least one service"),
    ({"services": [{"name": "Orders API"}]}, "services[0].name"),
    ({"services": [{"name": "a"}, {"name": "a"}]}, "'a' is used twice"),
    ({"services": [{"name": "a", "url": "localhost:8000"}]}, "services[0].url: must start with http"),
    ({"services": [{"name": "a", "health": "/healthz"}]}, "needs url_env or url"),
    ({"services": [{"name": "a", "calls": ["b"]}]}, "'b' is not a service"),
    ({"suites": [{"name": "api", "command": "pytest"}]}, "must be a command that writes a JUnit report to {junit}"),
    ({"suites": [{"name": "api", "command": "pytest {junit}", "levels": ["smoke"]}]}, "suites[0].levels"),
    ({"suites": [{"name": "api", "command": "pytest {junit}", "repo": "nope"}]}, "suites[0].repo"),
    ({"suites": [{"name": "api", "command": "pytest {junit}", "needs": ["nope"]}]}, "'nope' is not a service"),
    ({"auth": {"env": ["not a name"]}}, "auth.env"),
    ({"bindings": {"level": {"smoke": []}}}, "bindings.level.smoke"),
    ({"limits": {"batch": 0}}, "limits.batch"),
    ({"off_limits": "../secret"}, "off_limits"),
    ({"environment": {"writes": "anything"}}, "environment.writes"),
    ({"suites": [{"name": "api", "command": "pytest {junit}", "on_request": "yes"}]}, "suites[0].on_request: must be true or false"),
    ({"services": [{"name": "a", "repo": "."}]}, "that is placement 'service'"),
    ({"services": [{"name": "a", "repo": "./"}]}, "that is placement 'service'"),
    ({"services": [{"name": "api", "repo": "../mono", "writable": ["api/tests/**"]},
                   {"name": "web", "repo": "../mono/", "writable": ["web/tests/**"]}]},
     "api, web share the checkout ../mono: list 'writable' on one of them"),
])
def test_validate_names_each_problem(change, expected):
    data = project()
    data.update(change)
    assert any(expected in error for error in validate(data)), validate(data)


def test_placement_service_needs_a_service_in_this_repository():
    data = project(services=[{"name": "orders", "repo": "../orders"}], placement="service")
    assert any("one service must have repo '.'" in e for e in validate(data))
    data["services"][0]["repo"] = "."
    assert validate(data) == []


def test_load_reports_every_problem(host):
    host.paths.config.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(QAError) as error:
        Config.load(host.paths)
    assert "project.name" in str(error.value) and "services" in str(error.value)
    host.paths.config.unlink()
    with pytest.raises(QAError, match="setup playbook"):
        Config.load(host.paths)
    assert Config.load(host.paths, required=False) is None


def test_url_comes_from_the_env_file_then_the_map(host, monkeypatch):
    config = host.configure(project(services=[
        {"name": "orders", "url_env": "ORDERS_URL", "url": "http://127.0.0.1:8000/"},
        {"name": "web", "url": "http://127.0.0.1:3000"},
        {"name": "worker"},
    ]))
    assert config.url(config.service("orders")) == "http://127.0.0.1:8000"
    write(host.root, ".env", "ORDERS_URL=http://127.0.0.1:9000/\n")
    config = host.config
    assert config.url(config.service("orders")) == "http://127.0.0.1:9000"
    monkeypatch.setenv("ORDERS_URL", "http://10.0.0.1")
    assert config.url(config.service("orders")) == "http://10.0.0.1"
    assert config.url(config.service("web")) == "http://127.0.0.1:3000"
    assert config.url(config.service("worker")) is None


def test_host_service_and_off_limits(host):
    config = host.configure(project(
        services=[{"name": "app", "repo": "."}, {"name": "orders", "repo": "../orders"}],
        placement="service", off_limits=["../reference-tests"],
    ))
    assert config.host_service()["name"] == "app"
    assert config.is_host(config.service("orders")) is False
    assert config.off_limits() == [(host.root.parent / "reference-tests").resolve()]


def test_parse_service_spec():
    assert parse_service_spec("name=orders, repo=../orders,url=http://x:1,health=/healthz") == {
        "name": "orders", "repo": "../orders", "url": "http://x:1", "health": "/healthz",
    }
    with pytest.raises(QAError, match="unknown key"):
        parse_service_spec("name=a,port=1")
    with pytest.raises(QAError, match="name is required"):
        parse_service_spec("repo=../a")
    assert url_env_name("white-tiger.be") == "WHITE_TIGER_BE_URL"


def test_init_project_writes_the_map_and_url_settings(host, workspace):
    service = git_init(workspace / "orders", branch="dev")
    result = init_project(
        host.paths, "shop", "A shop.", "qa-repo",
        ["name=orders,repo=../orders,url=http://127.0.0.1:8000,health=/healthz,openapi=/openapi.json", "name=web,url=http://127.0.0.1:3000"],
        off_limits=["../old-tests"],
    )
    assert result["services"] == ["orders", "web"]
    config = host.config
    orders = config.service("orders")
    assert orders["url_env"] == "ORDERS_URL" and orders["branch"] == "dev" and orders["openapi"] == "/openapi.json"
    assert config.repo_path(orders) == service.resolve()
    assert config["off_limits"] == ["../old-tests"]
    assert "ORDERS_URL=http://127.0.0.1:8000" in (host.root / ".env.example").read_text()
    assert "WEB_URL=http://127.0.0.1:3000" in (host.root / ".env").read_text()
    with pytest.raises(QAError, match="already exists"):
        init_project(host.paths, "shop", "", "qa-repo", ["name=orders"])
    # Running it again with --force keeps settings the person already changed.
    write(host.root, ".env", "ORDERS_URL=http://127.0.0.1:9999\n")
    init_project(host.paths, "shop", "", "qa-repo", ["name=orders,url=http://127.0.0.1:8000"], force=True)
    assert (host.root / ".env").read_text() == "ORDERS_URL=http://127.0.0.1:9999\n"


def test_services_that_share_a_checkout_keep_their_tests_in_one_worktree(host):
    from qa_agent.catalog import Catalog

    config = host.configure(project(
        services=[
            {"name": "api", "repo": "../mono", "writable": ["api/tests/**", "web/src/**/*.test.ts"]},
            {"name": "web", "repo": "../mono"},
            {"name": "mail", "repo": "../mail", "writable": ["tests/**"]},
        ],
        suites=[{"name": "web-unit", "levels": ["unit"], "repo": "api", "command": "npx vitest run --outputFile={junit}"}],
    ))
    assert config.tests_owner(config.service("web"))["name"] == "api"
    assert config.tests_owner(config.service("api"))["name"] == "api"
    assert config.tests_owner(config.service("mail"))["name"] == "mail"
    catalog = Catalog(host.paths, config)
    assert catalog.default_repo("web", "unit") == "api" and catalog.default_repo("web", "e2e") == "."
    added = catalog.add([
        {"service": "web", "level": "unit", "surface": "func total", "case": "no lines", "expect": "0", "intent": "x"},
        {"service": "web", "level": "component", "surface": "Cart", "case": "empty", "expect": "a hint", "intent": "x", "repo": "web"},
    ])
    assert added["rejected"] == [] and [catalog.get(i)["repo"] for i in added["added"]] == ["api", "api"]
    assert catalog.update("B-0001", {"repo": "web"})["repo"] == "api"


def test_the_service_that_lives_here_is_tested_here(host):
    from qa_agent.catalog import Catalog

    config = host.configure(project(
        services=[{"name": "app", "repo": ".", "writable": ["tests/**"]}, {"name": "worker", "repo": "./", "writable": ["worker/tests/**"]},
                  {"name": "orders", "repo": "../orders", "writable": ["tests/**"]}],
        suites=[{"name": "app-unit", "levels": ["unit"], "repo": "app", "command": "pytest --junitxml={junit}"},
                {"name": "orders-unit", "levels": ["unit"], "repo": "orders", "command": "pytest --junitxml={junit}"}],
        placement="service",
    ))
    assert [service["name"] for service in config.host_services()] == ["app", "worker"]
    assert [suite["repo"] for suite in config.suites] == [".", "orders"]      # no worktree of this repository itself
    catalog = Catalog(host.paths, config)
    assert catalog.default_repo("app", "unit") == "." and catalog.default_repo("worker", "component") == "."
    assert catalog.default_repo("orders", "unit") == "orders"


def test_inside_an_application_repository_the_tests_get_an_env_file_of_their_own(host):
    write(host.root, ".env", "DATABASE_URL=postgres://the-application\n")
    write(host.root, ".env.example", "DATABASE_URL=\n")
    result = init_project(host.paths, "shop", "", "service", ["name=app,repo=.,url=http://127.0.0.1:8000"])
    assert result["env_file"] == ".qa-agent/.env"
    assert result["env_lines_added"] == [".qa-agent/.env.example: APP_URL", ".qa-agent/.env: APP_URL"]
    assert (host.root / ".env").read_text() == "DATABASE_URL=postgres://the-application\n"
    assert (host.root / ".env.example").read_text() == "DATABASE_URL=\n"
    config = host.config
    assert config.env_file() == host.root / ".qa-agent/.env" and config.env_example() == host.root / ".qa-agent/.env.example"
    assert config.env_value("APP_URL") == "http://127.0.0.1:8000" and config.env_value("DATABASE_URL") is None


def test_what_the_person_agreed_to_is_pinned(host):
    config = host.configure(project(
        services=[{"name": "orders", "repo": "../orders", "writable": ["tests/qa/**", "tests/**"]}], off_limits=["../reference"],
    ))
    assert agreed(host.paths) is None and consent_changes(host.paths, config) == []
    assert consent_fields(config) == {
        "placement": "qa-repo", "environment.writes": "own-data", "git.branch_prefix": "qa-agent/", "off_limits": ["../reference"],
        "services.orders.repo": "../orders", "services.orders.writable": ["tests/**", "tests/qa/**"],
    }
    with pytest.raises(QAError, match="--note is required"):
        agree(host.paths, config, "ok")
    agree(host.paths, config, "pytest and Playwright; tests for orders under tests/ only")
    assert consent_changes(host.paths, config) == []
    data = config.data
    data["off_limits"] = []
    data["git"]["branch_prefix"] = ""
    data["services"].append({"name": "web", "repo": "../web", "writable": ["src/**"]})
    data["suites"] = [{"name": "api", "command": "pytest --junitxml={junit}"}]         # not a limit: free to change
    changed = consent_changes(host.paths, host.configure(data))
    assert changed == [
        ("git.branch_prefix", "qa-agent/", ""), ("off_limits", ["../reference"], []),
        ("services.web.repo", None, "../web"), ("services.web.writable", None, ["src/**"]),
    ]
    record = agree(host.paths, host.config, "the person added the web service and asked for commits on the current branch")
    assert consent_changes(host.paths, host.config) == [] and record["fields"]["git.branch_prefix"] == ""
    assert [row["note"] for row in agreed(host.paths)["history"]] == ["pytest and Playwright; tests for orders under tests/ only"]
