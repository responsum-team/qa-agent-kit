"""The project map, .qa-agent/project.json: services, where they run, where their code is, how tests run."""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import LEVELS, TASKS
from .ignore import keep_local
from .util import QAError, git_branch, is_git_repo, now_iso, parse_env_file, posix, read_json, write_json

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEMENTS = ("qa-repo", "service")
WRITES = ("own-data", "none")


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def validate(data) -> list:
    """Every problem in a project map, each as `where: what`. Empty when it is usable."""
    errors: list = []

    def err(where, what):
        errors.append(f"{where}: {what}")

    if not isinstance(data, dict):
        return ["project.json: must be a JSON object"]
    if data.get("version") != 1:
        err("version", "must be 1")

    project = data.get("project")
    if not isinstance(project, dict) or not str(project.get("name") or "").strip():
        err("project.name", "is required")
    elif not isinstance(project.get("about", ""), str):
        err("project.about", "must be text")

    if data.get("placement") not in PLACEMENTS:
        err("placement", f"must be one of {', '.join(PLACEMENTS)}")

    environment = data.get("environment", {})
    if not isinstance(environment, dict):
        err("environment", "must be an object")
    else:
        if environment.get("writes", "own-data") not in WRITES:
            err("environment.writes", f"must be one of {', '.join(WRITES)}")
        if not isinstance(environment.get("env_file", ".env"), str):
            err("environment.env_file", "must be a path")

    services = data.get("services")
    names: list = []
    if not isinstance(services, list) or not services:
        err("services", "list at least one service")
        services = []
    for index, service in enumerate(services):
        where = f"services[{index}]"
        if not isinstance(service, dict):
            err(where, "must be an object")
            continue
        name = service.get("name")
        if not isinstance(name, str) or not SLUG.match(name):
            err(f"{where}.name", "use lowercase letters, digits, '-', '_' or '.'")
        elif name in names:
            err(f"{where}.name", f"'{name}' is used twice")
        else:
            names.append(name)
        if service.get("repo") is not None and not isinstance(service.get("repo"), str):
            err(f"{where}.repo", "must be a path, or null when there is no checkout")
        url_env = service.get("url_env")
        if url_env is not None and (not isinstance(url_env, str) or not ENV_NAME.match(url_env)):
            err(f"{where}.url_env", "must be an environment variable name")
        if service.get("url") is not None and not re.match(r"^https?://", str(service.get("url"))):
            err(f"{where}.url", "must start with http:// or https://")
        if not url_env and not service.get("url") and service.get("health"):
            err(f"{where}.health", "needs url_env or url to be checked against")
        for key in ("health",):
            value = service.get(key)
            if value is not None and (not isinstance(value, str) or not value.startswith("/")):
                err(f"{where}.{key}", "must be a path starting with /")
        if service.get("openapi") is not None and not isinstance(service.get("openapi"), str):
            err(f"{where}.openapi", "must be a URL path such as /openapi.json, or a JSON file in the repository")
        for key in ("sources", "tests", "writable", "calls"):
            if key in service and not _is_str_list(service[key]) and service[key] != []:
                err(f"{where}.{key}", "must be a list of text values")
    for index, service in enumerate(services):
        if isinstance(service, dict):
            for called in service.get("calls") or []:
                if called not in names:
                    err(f"services[{index}].calls", f"'{called}' is not a service")

    here = [s for s in services if isinstance(s, dict) and isinstance(s.get("repo"), str)
            and posix(s["repo"]).rstrip("/") in ("", ".")]
    if data.get("placement") == "service" and services and not here:
        err("services", "with placement 'service', one service must have repo '.'")
    if data.get("placement") == "qa-repo" and here:
        err("services", "a service with repo '.' means the kit sits in that service's own repository: "
                        "that is placement 'service'")
    shared: dict = {}
    for service in services:
        if isinstance(service, dict) and isinstance(service.get("repo"), str) and service.get("writable"):
            checkout = posix(service["repo"]).rstrip("/") or "."
            if checkout != ".":
                shared.setdefault(checkout, []).append(str(service.get("name")))
    for checkout, owners in shared.items():
        if len(owners) > 1:
            err("services", f"{', '.join(owners)} share the checkout {checkout}: list 'writable' on one of them; "
                            "its QA worktree holds the tests of all")

    suite_names: list = []
    suites = data.get("suites", [])
    if not isinstance(suites, list):
        err("suites", "must be a list")
        suites = []
    for index, suite in enumerate(suites):
        where = f"suites[{index}]"
        if not isinstance(suite, dict):
            err(where, "must be an object")
            continue
        name = suite.get("name")
        if not isinstance(name, str) or not SLUG.match(name):
            err(f"{where}.name", "use lowercase letters, digits, '-', '_' or '.'")
        elif name in suite_names:
            err(f"{where}.name", f"'{name}' is used twice")
        else:
            suite_names.append(name)
        repo = suite.get("repo", ".")
        if repo != "." and repo not in names:
            err(f"{where}.repo", "must be '.' for this repository, or the name of a service")
        command = suite.get("command")
        if not isinstance(command, str) or "{junit}" not in command:
            err(f"{where}.command", "must be a command that writes a JUnit report to {junit}")
        rerun = suite.get("rerun_failed")
        if rerun is not None and (not isinstance(rerun, str) or "{junit}" not in rerun):
            err(f"{where}.rerun_failed", "must be a command that writes a JUnit report to {junit}")
        if suite.get("select") is not None and not isinstance(suite.get("select"), str):
            err(f"{where}.select", "must be text: the arguments that select the whole suite, such as a folder")
        for key in ("workdir", "stack"):
            if suite.get(key) is not None and not isinstance(suite.get(key), str):
                err(f"{where}.{key}", "must be text")
        levels = suite.get("levels", [])
        if not isinstance(levels, list) or any(level not in LEVELS for level in levels):
            err(f"{where}.levels", f"each must be one of {', '.join(LEVELS)}")
        for needed in suite.get("needs") or []:
            if needed not in names:
                err(f"{where}.needs", f"'{needed}' is not a service")
        if "setup" in suite and not _is_str_list(suite["setup"]) and suite["setup"] != []:
            err(f"{where}.setup", "must be a list of commands")
        timeout = suite.get("timeout_minutes", 30)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            err(f"{where}.timeout_minutes", "must be a positive number")
        if not isinstance(suite.get("on_request", False), bool):
            err(f"{where}.on_request", "must be true or false")

    bindings = data.get("bindings", {})
    if not isinstance(bindings, dict):
        err("bindings", "must be an object")
    else:
        if "always" in bindings and not _is_str_list(bindings["always"]) and bindings["always"] != []:
            err("bindings.always", "must be a list of rule or skill names")
        for group, allowed in (("level", LEVELS), ("task", TASKS)):
            section = bindings.get(group, {})
            if not isinstance(section, dict):
                err(f"bindings.{group}", "must be an object")
                continue
            for key, value in section.items():
                if key not in allowed:
                    err(f"bindings.{group}.{key}", f"must be one of {', '.join(allowed)}")
                elif not _is_str_list(value) and value != []:
                    err(f"bindings.{group}.{key}", "must be a list of rule or skill names")

    auth = data.get("auth", {})
    if not isinstance(auth, dict):
        err("auth", "must be an object")
    else:
        for name in auth.get("env") or []:
            if not isinstance(name, str) or not ENV_NAME.match(name):
                err("auth.env", f"'{name}' is not an environment variable name; list names here, never values")

    limits = data.get("limits", {})
    if not isinstance(limits, dict):
        err("limits", "must be an object")
    else:
        batch = limits.get("batch", 10)
        if not isinstance(batch, int) or isinstance(batch, bool) or batch < 1:
            err("limits.batch", "must be a whole number above 0")

    if "off_limits" in data and not _is_str_list(data["off_limits"]) and data["off_limits"] != []:
        err("off_limits", "must be a list of paths the agent never reads")
    git_section = data.get("git", {})
    if not isinstance(git_section, dict) or not isinstance(git_section.get("branch_prefix", ""), str):
        err("git.branch_prefix", "must be text; leave it empty to commit on the current branch")
    return errors


def _normalise(data: dict) -> dict:
    data.setdefault("project", {})
    data["project"].setdefault("about", "")
    data["project"].setdefault("critical", [])
    data["project"].setdefault("notes", "")
    environment = data.setdefault("environment", {})
    environment.setdefault("name", "local")
    environment.setdefault("writes", "own-data")
    environment.setdefault("env_file", ".env")
    for service in data.get("services", []):
        for key, default in (
            ("about", ""), ("repo", None), ("branch", None), ("url_env", None), ("url", None),
            ("health", None), ("openapi", None),
        ):
            service.setdefault(key, default)
        for key in ("calls", "sources", "tests", "writable"):
            service.setdefault(key, [])
    data.setdefault("suites", [])
    for suite in data["suites"]:
        suite.setdefault("repo", ".")
        suite.setdefault("workdir", ".")
        suite.setdefault("levels", [])
        suite.setdefault("setup", [])
        suite.setdefault("needs", [])
        suite.setdefault("rerun_failed", None)
        suite.setdefault("select", None)
        suite.setdefault("timeout_minutes", 30)
        suite.setdefault("stack", None)
        suite.setdefault("on_request", False)
    auth = data.setdefault("auth", {})
    auth.setdefault("about", "")
    auth.setdefault("env", [])
    auth.setdefault("state_files", [])
    bindings = data.setdefault("bindings", {})
    bindings.setdefault("always", [])
    bindings.setdefault("level", {})
    bindings.setdefault("task", {})
    data.setdefault("library", None)
    data.setdefault("off_limits", [])
    data.setdefault("git", {}).setdefault("branch_prefix", "qa-agent/")
    data.setdefault("limits", {}).setdefault("batch", 10)
    return data


class Config:
    def __init__(self, paths, data: dict):
        self.paths = paths
        self.data = _normalise(data)
        self._env = None
        here = {service["name"] for service in self.host_services()}
        for suite in self.suites:
            if suite["repo"] in here:
                suite["repo"] = "."  # the service that lives in this repository is tested here, not in a worktree

    # -- loading

    @classmethod
    def load(cls, paths, required: bool = True):
        data = read_json(paths.config)
        if data is None:
            if required:
                raise QAError(
                    f"{paths.rel(paths.config)} does not exist yet. Follow the setup playbook "
                    "(.qa-agent/kit/playbooks/setup.md) to create it."
                )
            return None
        errors = validate(data)
        if errors:
            raise QAError("project.json has problems:\n  - " + "\n  - ".join(errors))
        return cls(paths, data)

    # -- lookups

    def __getitem__(self, key):
        return self.data[key]

    @property
    def name(self) -> str:
        return self.data["project"]["name"]

    @property
    def placement(self) -> str:
        return self.data["placement"]

    @property
    def services(self) -> list:
        return self.data["services"]

    @property
    def suites(self) -> list:
        return self.data["suites"]

    def service(self, name: str) -> dict:
        for service in self.services:
            if service["name"] == name:
                return service
        known = ", ".join(s["name"] for s in self.services)
        raise QAError(f"No service named '{name}'. Services: {known}")

    def suite(self, name: str) -> dict:
        for suite in self.suites:
            if suite["name"] == name:
                return suite
        known = ", ".join(s["name"] for s in self.suites) or "none defined"
        raise QAError(f"No suite named '{name}'. Suites: {known}")

    def repo_path(self, service: dict):
        """The service's checkout, or None when the project map gives no code location."""
        if not service.get("repo"):
            return None
        return (self.paths.root / service["repo"]).resolve()

    def is_host(self, service: dict) -> bool:
        path = self.repo_path(service)
        return path is not None and path == self.paths.root.resolve()

    def host_services(self) -> list:
        """The services whose code lives in this repository, when the kit is installed inside one."""
        return [service for service in self.services if self.is_host(service)]

    def host_service(self):
        """The first of them, or None in a repository that holds only tests."""
        return next(iter(self.host_services()), None)

    def tests_owner(self, service: dict) -> dict:
        """The service entry whose QA worktree holds the tests written inside this service's repository.

        It is the service itself, except where several services share one repository: then the one listing `writable`.
        """
        checkout = self.repo_path(service)
        if checkout is None or service.get("writable"):
            return service
        for other in self.services:
            if other.get("writable") and self.repo_path(other) == checkout:
                return other
        return service

    def off_limits(self) -> list:
        """Folders the agent never opens, as absolute paths."""
        return [(self.paths.root / Path(item).expanduser()).resolve() for item in self.data["off_limits"]]

    # -- environment

    def env_file(self) -> Path:
        return self.paths.root / self.data["environment"]["env_file"]

    def env_example(self) -> Path:
        """The committed list of settings, without values, that sits beside the env file."""
        return self.paths.root / (self.data["environment"]["env_file"] + ".example")

    def file_env(self) -> dict:
        if self._env is None:
            self._env = parse_env_file(self.env_file())
        return self._env

    def env_value(self, name: str):
        """A setting from the process environment, then from the env file."""
        if name in os.environ and os.environ[name] != "":
            return os.environ[name]
        value = self.file_env().get(name)
        return value if value not in (None, "") else None

    def url(self, service: dict):
        """Where the service answers right now: its url_env if set, else the map's default."""
        if service.get("url_env"):
            value = self.env_value(service["url_env"])
            if value:
                return value.rstrip("/")
        return service["url"].rstrip("/") if service.get("url") else None


# ---------------------------------------------------------------- init


def url_env_name(service_name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", service_name.upper()).strip("_") + "_URL"


def parse_service_spec(spec: str) -> dict:
    """`name=orders,repo=../orders,url=http://127.0.0.1:8000,health=/healthz` as a service entry."""
    fields: dict = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise QAError(f"--service '{spec}': expected key=value pairs separated by commas")
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    allowed = {"name", "repo", "url", "health", "openapi", "branch", "url_env", "about"}
    unknown = set(fields) - allowed
    if unknown:
        raise QAError(f"--service: unknown key(s) {', '.join(sorted(unknown))}; allowed: {', '.join(sorted(allowed))}")
    if not fields.get("name"):
        raise QAError(f"--service '{spec}': name is required")
    return fields


def _ensure_env_line(path: Path, name: str, value: str) -> bool:
    existing = parse_env_file(path)
    if name in existing:
        return False
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + f"{name}={value}\n", encoding="utf-8")
    return True


def init_project(paths, name, about, placement, service_specs, env_name="local", writes="own-data", force=False,
                 off_limits=()):
    """Write a first project map from what the person said, and the URL settings beside it."""
    if paths.config.exists() and not force:
        raise QAError(f"{paths.rel(paths.config)} already exists. Edit it, or pass --force to replace it.")
    services = []
    for spec in service_specs:
        fields = parse_service_spec(spec)
        service = {
            "name": fields["name"],
            "about": fields.get("about", ""),
            "repo": fields.get("repo") or None,
            "branch": fields.get("branch") or None,
            "url_env": fields.get("url_env") or (url_env_name(fields["name"]) if fields.get("url") else None),
            "url": fields.get("url") or None,
            "health": fields.get("health") or None,
            "openapi": fields.get("openapi") or None,
            "calls": [],
            "sources": [],
            "tests": [],
            "writable": [],
        }
        if service["repo"] and not service["branch"]:
            checkout = (paths.root / service["repo"]).resolve()
            if is_git_repo(checkout):
                service["branch"] = git_branch(checkout)
        services.append(service)
    # In an application's own repository a top-level .env is the application's: the tests get one of their own.
    env_file = paths.rel(paths.qa / ".env") if placement == "service" else ".env"
    data = {
        "version": 1,
        "project": {"name": name, "about": about or "", "critical": [], "notes": ""},
        "placement": placement,
        "environment": {"name": env_name, "writes": writes, "env_file": env_file},
        "services": services,
        "suites": [],
        "auth": {"about": "", "env": [], "state_files": []},
        "library": None,
        "bindings": {"always": [], "level": {}, "task": {}},
        "off_limits": list(off_limits),
        "git": {"branch_prefix": "qa-agent/"},
        "limits": {"batch": 10},
    }
    errors = validate(data)
    if errors:
        raise QAError("Cannot write the project map:\n  - " + "\n  - ".join(errors))
    write_json(paths.config, data)
    ignored = keep_local(paths.root, qa_repo=placement == "qa-repo")  # before the env file exists
    added = []
    for service in services:
        if service["url_env"] and service["url"]:
            for target in (paths.root / (env_file + ".example"), paths.root / env_file):
                if _ensure_env_line(target, service["url_env"], service["url"]):
                    added.append(f"{paths.rel(target)}: {service['url_env']}")
    return {
        "written": paths.rel(paths.config), "services": [s["name"] for s in services], "env_file": env_file,
        "env_lines_added": added, "ignore_lines": ignored,
    }


# ---------------------------------------------------------------- what the person agreed to


def consent_fields(config) -> dict:
    """The parts of the project map that set the agent's limits. They change only with the person's agreement."""
    fields = {
        "placement": config.placement,
        "environment.writes": config["environment"]["writes"],
        "git.branch_prefix": config["git"]["branch_prefix"],
        "off_limits": sorted(config["off_limits"]),
    }
    for service in config.services:
        fields[f"services.{service['name']}.repo"] = service.get("repo")
        fields[f"services.{service['name']}.writable"] = sorted(service.get("writable") or [])
    return fields


def agree(paths, config, note: str) -> dict:
    """Record that the person agreed to the limits as the project map states them now."""
    note = " ".join((note or "").split())
    if len(note) < 12:
        raise QAError("--note is required: what the person agreed to, in a sentence or two")
    earlier = read_json(paths.consent, {}) or {}
    history = list(earlier.get("history") or [])
    if earlier.get("agreed_at"):
        history.append({"agreed_at": earlier["agreed_at"], "note": earlier.get("note", "")})
    record = {
        "version": 1, "agreed_at": now_iso(), "note": note[:1200], "fields": consent_fields(config),
        "history": history[-20:],
    }
    write_json(paths.consent, record)
    return {"agreed_at": record["agreed_at"], "note": record["note"], "fields": record["fields"]}


def agreed(paths):
    """The recorded agreement, or None before setup has reached it."""
    return read_json(paths.consent)


def consent_changes(paths, config) -> list:
    """Limits that differ from what the person agreed to: (field, agreed value, value now)."""
    record = agreed(paths)
    if not record:
        return []
    was, now = record.get("fields") or {}, consent_fields(config)
    return [(field, was.get(field), now.get(field)) for field in sorted(set(was) | set(now)) if was.get(field) != now.get(field)]
