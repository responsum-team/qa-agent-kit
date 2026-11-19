"""Shared helpers for the kit's own tests. Everything runs offline, in temporary folders."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "kit"))
sys.path.insert(0, str(REPO))

from qa_agent.config import Config  # noqa: E402
from qa_agent.ignore import keep_local  # noqa: E402
from qa_agent.paths import Paths  # noqa: E402


def sh(cwd, *args, check=True) -> str:
    proc = subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed in {cwd}:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def git_init(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "git", "init", "-q", "-b", branch)
    sh(path, "git", "config", "user.email", "qa@example.test")
    sh(path, "git", "config", "user.name", "QA Test")
    sh(path, "git", "config", "commit.gpgsign", "false")
    return path


def write(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def commit(repo: Path, message: str = "change", files=None) -> str:
    for rel, text in (files or {}).items():
        write(repo, rel, text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", message, "--allow-empty")
    return sh(repo, "git", "rev-parse", "HEAD")


def project(services=None, suites=None, placement="qa-repo", **extra) -> dict:
    data = {
        "version": 1,
        "project": {"name": "shop", "about": "A small shop."},
        "placement": placement,
        "environment": {"name": "local", "writes": "own-data", "env_file": ".env"},
        "services": services if services is not None else [{"name": "orders", "repo": "../orders", "branch": "main"}],
        "suites": suites or [],
    }
    data.update(extra)
    return data


class Host:
    """A temporary repository with the kit's state folder, as it looks after setup."""

    def __init__(self, root: Path):
        self.root = root
        self.paths = Paths(root)

    def configure(self, data: dict) -> Config:
        """Write the project map and the ignore lines, as `qa.py init` does."""
        self.paths.qa.mkdir(exist_ok=True)
        self.paths.config.write_text(json.dumps(data), encoding="utf-8")
        keep_local(self.root, qa_repo=data.get("placement") == "qa-repo")
        return Config.load(self.paths)

    @property
    def config(self) -> Config:
        return Config.load(self.paths)


@pytest.fixture
def answering():
    """Start small HTTP services on this machine: answering({"/healthz": (200, {"ok": True})}) returns the URL."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    servers = []

    def start(routes: dict) -> str:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                status, body = routes.get(self.path.split("?")[0], (404, {"code": "NOT_FOUND"}))
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def workspace(tmp_path):
    """A folder holding a QA repository (`qa`) and room for service repositories beside it."""
    return tmp_path


@pytest.fixture
def host(workspace) -> Host:
    """A repository the kit was installed in, before setup."""
    root = git_init(workspace / "qa")
    (root / ".qa-agent").mkdir()
    keep_local(root)
    return Host(root)


@pytest.fixture
def orders(workspace) -> Path:
    """A service repository with a little code and one commit."""
    repo = git_init(workspace / "orders")
    commit(repo, "first", {
        "app/routes.py": "def create_order(cart):\n    return {'id': 1}\n",
        "app/models.py": "class Order: ...\n",
        "tests/test_models.py": "def test_order():\n    assert True\n",
        "README.md": "# orders\n",
    })
    return repo
