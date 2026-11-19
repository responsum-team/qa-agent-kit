"""The whole life of the kit in one repository, offline: install, set up, baseline, a merge, the sync, the score.

The steps are the ones the playbooks give an agent, in their order, run through the installed helper exactly as an
agent would run it (`python3 .qa-agent/kit/qa.py …` in the repository). The application is a small HTTP service
started from its own checkout, with a deliberate defect that a later "merge" corrects.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import install
from conftest import commit, git_init, sh, write

TOKEN = "shop-test-token-7f3a91"

PRICING = '''def total(items):
    """The price of all lines together."""
    return sum(item["price"] * item["quantity"] for item in items)
'''

APP = '''import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pricing import total

ORDERS = {}
TOKEN = os.environ["SHOP_TOKEN"]
HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def signed_in(self):
        return self.headers.get("Authorization") == "Bearer " + TOKEN

    def do_GET(self):
        if self.path == "/healthz":
            return self.answer(200, {"status": "ok"})
        if self.path == "/openapi.json":
            with open(os.path.join(HERE, "openapi.json"), encoding="utf-8") as handle:
                return self.answer(200, json.load(handle))
        if not self.signed_in():
            return self.answer(401, {"code": "NOT_SIGNED_IN"})
        if self.path.startswith("/v1/orders/"):
            order = ORDERS.get(self.path.rsplit("/", 1)[1])
            return self.answer(200, order) if order else self.answer(404, {"code": "NOT_FOUND"})
        self.answer(404, {"code": "NOT_FOUND"})

    def do_POST(self):
        if not self.signed_in():
            return self.answer(401, {"code": "NOT_SIGNED_IN"})
        if self.path != "/v1/orders":
            return self.answer(404, {"code": "NOT_FOUND"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        items = body.get("items") or []
        #EMPTY-CART-CHECK
        order = {"id": str(len(ORDERS) + 1), "status": "new", "items": items, "total": total(items)}
        ORDERS[order["id"]] = order
        self.answer(201, order)
    #DELETE-HANDLER

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''

# What the developers merge later: the empty cart is refused, and orders can be cancelled.
EMPTY_CART_CHECK = '''if not items:
            return self.answer(422, {"code": "EMPTY_CART"})'''
DELETE_HANDLER = '''
    def do_DELETE(self):
        if not self.signed_in():
            return self.answer(401, {"code": "NOT_SIGNED_IN"})
        if ORDERS.pop(self.path.rsplit("/", 1)[1], None) is None:
            return self.answer(404, {"code": "NOT_FOUND"})
        self.answer(204)
'''
APP_AFTER_MERGE = APP.replace("#EMPTY-CART-CHECK", EMPTY_CART_CHECK).replace("    #DELETE-HANDLER\n", DELETE_HANDLER)

ORDER = {"type": "object", "required": ["id", "status", "items", "total"], "properties": {
    "id": {"type": "string"}, "status": {"type": "string"}, "total": {"type": "number"},
    "items": {"type": "array", "items": {"type": "object"}},
}}
PROBLEM = {"type": "object", "properties": {"code": {"type": "string"}}}


def body(schema):
    return {"content": {"application/json": {"schema": schema}}}


OPENAPI = {
    "openapi": "3.0.3", "info": {"title": "Orders", "version": "1.0.0"}, "security": [{"bearer": []}],
    "paths": {
        "/healthz": {"get": {"security": [], "responses": {"200": {}}}},
        "/v1/orders": {"post": {
            "requestBody": {"required": True, **body({"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {"type": "object"}}}})},
            "responses": {"201": body(ORDER), "401": body(PROBLEM), "422": body(PROBLEM)},
        }},
        "/v1/orders/{id}": {"get": {"responses": {"200": body(ORDER), "401": body(PROBLEM), "404": body(PROBLEM)}}},
    },
}
OPENAPI_AFTER_MERGE = json.loads(json.dumps(OPENAPI))
OPENAPI_AFTER_MERGE["paths"]["/v1/orders/{id}"]["delete"] = {"responses": {"204": {}, "401": body(PROBLEM), "404": body(PROBLEM)}}

DEVELOPERS_TEST = '''from pricing import total


def test_one_line():
    assert total([{"price": 3, "quantity": 2}]) == 6
'''

# ---- what the agent writes -------------------------------------------------------------------------------------

SUPPORT = '''import json
import os
import urllib.error
import urllib.request

URL = os.environ["ORDERS_URL"]
TOKEN = os.environ["QA_USER_TOKEN"]


def call(method, path, body=None, token=TOKEN):
    """One request to the orders service; returns (status, parsed body)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(URL + path, method=method, data=data, headers={"Content-Type": "application/json"})
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")
'''

# The scaffold's one test: the service the suite needs answers.
SMOKE_TEST = '''from support import call


# B-0006
def test_the_service_answers_its_health_check():
    assert call("GET", "/healthz", token=None) == (200, {"status": "ok"})
'''

API_TESTS = '''from support import call

ITEMS = [{"sku": "qa-apple", "price": 3, "quantity": 2}]


# B-0001
def test_a_valid_order_is_created_and_can_be_read_back():
    status, order = call("POST", "/v1/orders", {"items": ITEMS})

    assert status == 201
    assert order["status"] == "new" and order["items"] == ITEMS and order["total"] == 6
    assert call("GET", "/v1/orders/" + order["id"]) == (200, order)


# B-0002
def test_an_empty_cart_is_rejected():
    status, problem = call("POST", "/v1/orders", {"items": []})

    assert status == 422
    assert problem["code"] == "EMPTY_CART"


# B-0003
def test_an_unknown_order_is_not_found():
    assert call("GET", "/v1/orders/999999") == (404, {"code": "NOT_FOUND"})


# B-0004
def test_orders_cannot_be_created_without_signing_in():
    assert call("POST", "/v1/orders", {"items": ITEMS}, token=None) == (401, {"code": "NOT_SIGNED_IN"})


# B-0006
def test_the_service_answers_its_health_check():
    assert call("GET", "/healthz", token=None) == (200, {"status": "ok"})
'''

UNIT_TEST = '''from pricing import total


# B-0005
def test_total_of_no_lines_is_zero():
    assert total([]) == 0
'''

CANCEL_TEST = '''

# B-0007
def test_a_cancelled_order_is_gone():
    _, order = call("POST", "/v1/orders", {"items": ITEMS})

    assert call("DELETE", "/v1/orders/" + order["id"]) == (204, None)
    assert call("GET", "/v1/orders/" + order["id"])[0] == 404
'''

BEHAVIOURS = [
    {"service": "orders", "level": "api", "area": "orders", "surface": "POST /v1/orders", "priority": 1,
     "case": "a signed-in customer sends one valid item",
     "expect": "201 with status 'new', the items sent and their total; GET /v1/orders/{id} returns the same order",
     "intent": "openapi.json: 201 with Order; app.py do_POST", "sources": ["app.py", "pricing.py"]},
    {"service": "orders", "level": "api", "area": "orders", "surface": "POST /v1/orders",
     "case": "an empty list of items", "expect": "422 with code EMPTY_CART",
     "intent": "openapi.json lists 422 for POST /v1/orders", "sources": ["app.py"]},
    {"service": "orders", "level": "api", "area": "orders", "surface": "GET /v1/orders/{id}",
     "case": "an id that does not exist", "expect": "404 with code NOT_FOUND",
     "intent": "openapi.json: 404; app.py do_GET", "sources": ["app.py"]},
    {"service": "orders", "level": "api", "area": "sign-in", "surface": "POST /v1/orders", "priority": 1,
     "case": "no credentials", "expect": "401 with code NOT_SIGNED_IN",
     "intent": "openapi.json: bearer security on every operation but /healthz; app.py signed_in", "sources": ["app.py"]},
    {"service": "orders", "level": "unit", "area": "pricing", "surface": "func pricing.total",
     "case": "no lines", "expect": "0", "intent": "pricing.py total(): a sum over the lines", "sources": ["pricing.py"]},
]

REFERENCE_TESTS = '''import requests

BASE = "http://shop.test"


def test_create_order():
    response = requests.post(BASE + "/v1/orders", json={"items": [{"sku": "pear", "price": 2, "quantity": 1}]})
    assert response.status_code == 201


def test_empty_cart_gives_422():
    response = requests.post(BASE + "/v1/orders", json={"items": []})
    assert response.status_code == 422


def test_refund_an_order():
    assert requests.post(BASE + "/v1/orders/1/refund").status_code == 200


def test_total_is_zero_without_lines():
    from pricing import total
    assert total([]) == 0
'''

# The team's library, as the person pastes a copy of it into the repository: a QA part and what other teams keep.
TEAM_LIBRARY = {
    "qa/api/rules/api-assertions.mdc": (
        "---\ndescription: Assert on the response body, not only on the status code\nglobs: tests/api/**\n---\n"
        "Every API test checks the body.\n"
    ),
    "qa/shared/skills/write-test-plan/SKILL.md": (
        "---\nname: write-test-plan\ndescription: Write a test plan for one feature\n---\n# Write a test plan\n"
    ),
    "web/rules/components.mdc": "---\ndescription: How components are named\n---\nNames.\n",
    "README.md": "# Team library\n",
}


class Shop:
    """The person's running service: started from its checkout, restarted by them after a merge."""

    def __init__(self, checkout):
        self.checkout = checkout
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, "-B", "app.py", str(self.port)], cwd=str(self.checkout),
            env={**os.environ, "SHOP_TOKEN": TOKEN}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.url + "/healthz", timeout=1):
                    return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("the sample service did not start")

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process = None

    def restart(self):
        self.stop()
        self.start()


def helper(root):
    """`qa` as the documents write it: the installed helper, run in the repository."""

    def qa(*args, code=0):
        proc = subprocess.run(
            [sys.executable, str(root / ".qa-agent" / "kit" / "qa.py"), *args], capture_output=True, text=True, cwd=str(root),
        )
        assert proc.returncode == code, f"qa {' '.join(args)} exited with {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return proc.stdout + proc.stderr

    return qa


def edit_map(root, change):
    path = root / ".qa-agent" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def next_line(qa) -> str:
    """What `qa status` tells the session to do now."""
    return next(line for line in qa("status").splitlines() if line.startswith("Next: "))


def commit_all(root, qa, message, *progress):
    """The manual's sequence, in its order: record where the work is, pass the guard, stage and commit."""
    qa("progress", *progress)
    assert qa("guard")["ok"] is True
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", message)


def test_from_an_empty_repository_to_a_scored_and_synced_suite(tmp_path):
    orders = git_init(tmp_path / "orders")
    commit(orders, "orders service", {
        "app.py": APP, "pricing.py": PRICING, "openapi.json": json.dumps(OPENAPI, indent=1),
        "tests/test_pricing.py": DEVELOPERS_TEST, ".gitignore": "__pycache__/\n.pytest_cache/\n", "README.md": "# orders\n",
    })
    write(tmp_path / "reference", "tests/test_orders.py", REFERENCE_TESTS)     # the suite kept aside for scoring
    shop = Shop(orders)
    shop.start()
    try:
        root = git_init(tmp_path / "shop-qa")
        install.install(root)
        qa = helper(root)
        setup(root, qa, shop)
        baseline(root, qa, orders)
        merge_and_sync(root, qa, orders, shop)
        guard_catches_what_must_not_happen(root, qa, orders)
        score(tmp_path, root)
    finally:
        shop.stop()


# ---------------------------------------------------------------- playbooks/setup.md


def setup(root, qa, shop):
    page = qa("status")
    assert "not set up" in page and "playbooks/setup.md" in page

    # 2. Write the project map
    result = qa(
        "init", "--name", "Shop", "--about", "A small shop: customers place orders.", "--placement", "qa-repo",
        "--service", f"name=orders,repo=../orders,branch=main,url={shop.url},health=/healthz,openapi=/openapi.json",
        "--off-limits", "../reference",
    )
    assert result["env_file"] == ".env" and result["env_lines_added"] == [".env.example: ORDERS_URL", ".env: ORDERS_URL"]
    assert ".gitignore" in result["ignore_lines"]        # a QA repository: its own ignore file is the kit's to write
    assert next_line(qa) == (
        'Next: Continue playbooks/setup.md at "2. Write the project map". '
        "It is the one playbook that asks the person questions."
    )

    def complete(data):
        data["project"]["critical"] = ["A signed-in customer can place an order"]
        data["project"]["notes"] = "The orders service runs from its checkout."
        data["services"][0]["about"] = "Orders API"
        data["auth"] = {"about": "one customer, signing in with an API token", "env": ["QA_USER_TOKEN"], "state_files": []}

    edit_map(root, complete)
    assert qa("config", "validate") == {"ok": True, "errors": []}
    qa("progress", "--phase", "setup", "--step", "env")

    # 3. The env file: names in the example file, values typed in by the person
    with open(root / ".env.example", "a", encoding="utf-8") as handle:
        handle.write("# the test customer's API token\nQA_USER_TOKEN=\n")
    checks = {row["check"]: row for row in qa("doctor", code=1)["checks"]}
    assert checks["sign-in settings"]["detail"] == "not set: QA_USER_TOKEN"
    with open(root / ".env", "a", encoding="utf-8") as handle:           # the person fills in the value
        handle.write(f"QA_USER_TOKEN={TOKEN}\n")
    qa("progress", "--phase", "setup", "--step", "check")

    # 4. Check
    doctor = qa("doctor")
    assert doctor["ok"] is True and TOKEN not in json.dumps(doctor)
    qa("progress", "--phase", "setup", "--step", "rules")

    # 5. Team rules: the person pasted a copy of the team's library into the repository, and wants its QA part
    for rel, text in TEAM_LIBRARY.items():
        write(root, ".qa-agent/library-source/team-library/" + rel, text)
    assert qa("library", "status")["pasted_copy"] == ".qa-agent/library-source/team-library"
    found = qa("library", "scan", "--rules", "qa/**/*.mdc", "--skills", "qa/**/SKILL.md")
    assert found["library"] == ".qa-agent/library-source/team-library" and found["found"] == {"rules": 1, "skills": 1}
    installed = qa("library", "install", "--rules", "qa/**/*.mdc", "--skills", "qa/**/SKILL.md")
    assert installed["installed"] == {"rules": 1, "skills": 1} and installed["index"] == ".qa-agent/library/INDEX.md"
    assert installed["from"] == {"path": ".qa-agent/library-source/team-library", "commit": None, "dirty": None}
    edit_map(root, lambda data: data["bindings"]["level"].update(api=["api-assertions"]))
    reading = qa("rules", "--level", "api", "--task", "write")
    assert reading["then_team_rules"] == [
        {"name": "api-assertions", "kind": "rule", "path": ".qa-agent/library/rules/api-assertions.mdc"},
    ]
    assert "bound_but_not_installed" not in reading and reading["also_installed"]["count"] == 1
    assert {row["check"]: row for row in qa("doctor")["checks"]}["team library"]["ok"] is True
    qa("progress", "--phase", "setup", "--step", "stack")
    assert 'at "6. Look at the services and propose the stack"' in next_line(qa)

    # 6. Look at the services and propose the stack; the person says yes
    edit_map(root, lambda data: data["services"][0].update(sources=["*.py"], tests=["tests/**"], writable=["tests/**"]))
    assert qa("config", "validate")["ok"] is True
    agreed = qa(
        "config", "agree", "--note",
        "pytest for the API suite here and for unit tests inside orders (tests/**, on a qa-agent branch in a "
        "separate working copy); the first round covers the whole service",
    )
    assert agreed["limits"]["services.orders.writable"] == ["tests/**"] and agreed["limits"]["off_limits"] == ["../reference"]
    qa("progress", "--phase", "setup", "--step", "commit")

    # 7. First commit, in a repository without commits
    qa("progress", "--phase", "setup", "--step", "handover")
    assert qa("guard")["ok"] is True                # nothing to branch from yet, so the first commit is allowed here
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Set up the QA agent")
    tracked = sh(root, "git", "ls-files").splitlines()
    assert ".env" not in tracked and ".env.example" in tracked and ".qa-agent/kit/MANUAL.md" in tracked
    assert ".qa-agent/state/consent.json" in tracked and ".qa-agent/state/progress.json" in tracked
    # The rules the team chose travel with the repository; the copy of the library they came from stays on this machine.
    assert ".qa-agent/library/rules/api-assertions.mdc" in tracked and ".qa-agent/library/lock.json" in tracked
    assert not [path for path in tracked if path.startswith(".qa-agent/library-source/")]
    branch = qa("branch", "--name", "baseline")
    assert branch["created"] is True and branch["branch"].startswith("qa-agent/baseline-")

    # 8. Hand over
    page = qa("status")
    assert "Phase: setup, step handover" in page and 'Next: Continue playbooks/setup.md at "8. Hand over".' in page
    assert "Agreed with the person" in page and "pytest for the API suite here" in page
    assert "Never read: ../reference" in page and "orders: ../orders on main" in page and "no sync point" in page


# ---------------------------------------------------------------- playbooks/baseline.md


def baseline(root, qa, orders):
    assert qa("doctor")["ok"] is True
    assert qa("branch", "--name", "baseline")["created"] is False          # setup left the repository on it
    assert qa("progress", "--phase", "baseline") == {"phase": "baseline", "step": "map", "note": ""}
    assert next_line(qa) == (
        'Next: Continue playbooks/baseline.md at "Step 1. Map". So far: 0 behaviour(s) recorded, 0 suite(s) defined.'
    )

    # Step 1. Map. The sync point comes first: what is merged while the baseline is built belongs to the first sync.
    marked = qa("changes", "--mark-synced")
    assert list(marked["synced"]) == ["orders"] and marked["synced"]["orders"]["from"] is None
    reading = qa("rules", "--task", "map")
    assert reading["read_first"] == [".qa-agent/kit/methods/map-the-system.md"] and reading["then_team_rules"] == []
    assert qa("surface", "fetch", "--service", "orders")["operations"] == 3
    assert [op["id"] for op in qa("surface", "show", "--service", "orders")] == ["GET /healthz", "POST /v1/orders", "GET /v1/orders/{id}"]
    assert qa("surface", "show", "--service", "orders", "--op", "POST /v1/orders")[0]["responses"]["422"]["fields"] == {"code": "string"}

    write(root, ".qa-agent/runs/behaviours.json", json.dumps(BEHAVIOURS))
    added = qa("catalog", "add", "--file", ".qa-agent/runs/behaviours.json")
    assert added["added"] == ["B-0001", "B-0002", "B-0003", "B-0004", "B-0005"] and not added["rejected"]
    commit_all(
        root, qa, "Map orders: 5 behaviours",
        "--phase", "baseline", "--step", "map", "--note", "mapped: orders; next: the gaps",
    )
    assert qa("surface", "coverage", "--service", "orders")["without_any"] == ["GET /healthz"]
    qa("catalog", "add", "--json", json.dumps({
        "service": "orders", "level": "api", "area": "health", "surface": "GET /healthz", "case": "the service is up",
        "expect": "200 with status 'ok', without credentials", "intent": "openapi.json: no security on /healthz", "priority": 3,
        "sources": ["app.py"],
    }))
    assert qa("surface", "coverage", "--service", "orders")["without_any"] == []
    stats = qa("catalog", "stats")
    assert stats["total"] == 6 and stats["by_level"] == {"unit": {"planned": 1}, "api": {"planned": 5}}
    decided = qa("progress", "--decision", "integration, e2e and app: nothing to test, because the shop is one service without an interface")
    assert decided["decisions_recorded"] == 1 and decided["step"] == "map"
    commit_all(
        root, qa, "Map the shop: 6 behaviours",
        "--phase", "baseline", "--step", "scaffold", "--note", "6 behaviours; nothing left unmapped",
    )
    assert 'at "Step 2. Scaffold"' in next_line(qa)

    # Step 2. Scaffold: each suite runs from one command and has one passing smoke test.
    assert qa("rules", "--task", "scaffold")["read_first"] == [".qa-agent/kit/methods/scaffold.md"]
    python = sys.executable
    write(root, "tests/api/support.py", SUPPORT)
    write(root, "tests/api/test_orders.py", SMOKE_TEST)

    def suites(data):
        data["suites"] = [
            {"name": "api", "stack": "pytest", "levels": ["api"], "needs": ["orders"],
             "command": f"{python} -m pytest -q -p no:cacheprovider --junitxml={{junit}}", "select": "tests/api"},
            {"name": "orders-unit", "stack": "pytest", "levels": ["unit", "component"], "repo": "orders",
             "command": f"{python} -m pytest -q -p no:cacheprovider --junitxml={{junit}}", "select": "tests"},
        ]

    edit_map(root, suites)
    assert qa("config", "validate")["ok"] is True
    qa("catalog", "link", "B-0006", "--test", "tests/api/test_orders.py::test_the_service_answers_its_health_check")
    smoke = qa("run", "api", "--setup")
    assert smoke["totals"]["passed"] == 1 and smoke["catalog"]["changed"] == [{"id": "B-0006", "from": "written", "to": "passing"}]
    opened = qa("worktree", "open", "--service", "orders")
    assert opened["path"] == ".qa-agent/worktrees/orders" and opened["branch"].startswith("qa-agent/tests-") and opened["new_branch"]
    first = qa("run", "orders-unit", "--setup")          # the developers' own test, before anything is added
    assert first["totals"]["passed"] == 1 and first["cwd"] == ".qa-agent/worktrees/orders"
    assert first["tests_without_a_behaviour"]["count"] == 1
    write(root, "README.md", "# Shop QA\n\n## Running the tests\n\n`python3 .qa-agent/kit/qa.py run api`\n")
    commit_all(
        root, qa, "Scaffold the suites",
        "--phase", "baseline", "--step", "generate", "--note", "suites: api (pytest), orders-unit (pytest, in the service)",
    )

    # Step 3. Generate, in the manual's rhythm: next, rules, write, try, link, run, commit.
    batch = qa("catalog", "next", "--level", "api")
    assert [entry["id"] for entry in batch["batch"]] == ["B-0001", "B-0004", "B-0002", "B-0003"]      # priority 1 first
    assert {entry["suite"] for entry in batch["batch"]} == {"api"} and batch["planned_left"] == 4
    reading = qa("rules", "--level", "api", "--task", "write", "--suite", "api")
    assert reading["read_first"] == [
        ".qa-agent/kit/methods/test-design.md", ".qa-agent/kit/methods/api-tests.md", ".qa-agent/kit/methods/stacks/pytest.md",
    ]
    assert all((root / rel).is_file() for rel in reading["read_first"])

    write(root, "tests/api/test_orders.py", API_TESTS)
    trial = qa("run", "api", "--no-record", "--", "tests/api/test_orders.py::test_a_valid_order_is_created_and_can_be_read_back")
    assert trial["recorded"] is False and trial["totals"] == {"tests": 1, "passed": 1, "failed": 0, "skipped": 0, "flaky": 0}
    assert len(qa("runs", "--suite", "api")) == 1                      # the smoke run: a trial leaves no trace
    names = {
        "B-0001": "test_a_valid_order_is_created_and_can_be_read_back", "B-0002": "test_an_empty_cart_is_rejected",
        "B-0003": "test_an_unknown_order_is_not_found", "B-0004": "test_orders_cannot_be_created_without_signing_in",
    }
    for behaviour, name in names.items():
        assert qa("catalog", "link", behaviour, "--test", f"tests/api/test_orders.py::{name}")["status"] == "written"
    wrong = qa("catalog", "link", "B-0003", "--test", "tests/api/test_orders.py::test_no_such_test", code=2)
    assert "has no test named" in wrong

    run = qa("run", "api", "--", "tests/api/test_orders.py", code=1)   # the files the batch touched, recorded
    assert run["totals"] == {"tests": 5, "passed": 4, "failed": 1, "skipped": 0, "flaky": 0}
    failure = run["failed"][0]
    assert failure["behaviours"] == ["B-0002"] and failure["kind"] == "first-run" and "201 == 422" in failure["message"]
    assert "assert 201 == 422" in (root / failure["detail"]).read_text(encoding="utf-8")
    assert run["tests_without_a_behaviour"]["count"] == 0

    # methods/failures.md: the API document says 422, the service answers 201. The test stays; a defect is recorded.
    reading = qa("rules", "--task", "defect", "--suite", "api")
    assert [rel.rsplit("/", 1)[1] for rel in reading["read_first"]] == ["failures.md", "api-tests.md", "pytest.md"]
    text = (root / "tests/api/test_orders.py").read_text(encoding="utf-8")
    marker = '@pytest.mark.xfail(strict=True, reason="B-0002: answers 201 for an empty cart, the API document says 422")\n'
    text = text.replace("from support import call", "import pytest\n\nfrom support import call")
    text = text.replace("# B-0002\ndef test_an_empty_cart", "# B-0002\n" + marker + "def test_an_empty_cart")
    write(root, "tests/api/test_orders.py", text)
    refused = qa("guard", code=1)
    assert ["hygiene"] == [v["check"] for v in refused["violations"]] and "B-0002 is failing" in refused["violations"][0]["what"]
    write(root, ".qa-agent/reports/defects/B-0002.md", "# B-0002: an empty cart is accepted\n\nExpected 422 (openapi.json), got 201.\n")
    recorded = qa(
        "catalog", "status", "B-0002", "defect", "--note", "expected 422 EMPTY_CART (openapi.json) but the service answers 201",
        "--test", "tests/api/test_orders.py::test_an_empty_cart_is_rejected", "--report", ".qa-agent/reports/defects/B-0002.md",
    )
    assert recorded["status"] == "defect"
    run = qa("run", "api", "--", "tests/api/test_orders.py")
    assert run["totals"] == {"tests": 5, "passed": 4, "failed": 0, "skipped": 1, "flaky": 0}
    commit_all(
        root, qa, "test(api): orders, creating and reading (B-0001..B-0004)",
        "--note", "api: four tests written, B-0002 is a defect; next: the unit level",
    )
    whole = qa("run", "api")                                           # the whole suite, at the end of the level
    assert whole["totals"] == run["totals"] and "linked_but_not_reported" not in whole["catalog"]

    # The unit test goes into the service's QA worktree, never into the checkout the service runs from.
    batch = qa("catalog", "next")
    assert [(entry["id"], entry["repo"], entry["suite"]) for entry in batch["batch"]] == [("B-0005", "orders", "orders-unit")]
    assert qa("rules", "--level", "unit", "--task", "write", "--suite", "orders-unit")["read_first"] == [
        ".qa-agent/kit/methods/test-design.md", ".qa-agent/kit/methods/in-service-tests.md", ".qa-agent/kit/methods/stacks/pytest.md",
    ]
    tree = root / ".qa-agent" / "worktrees" / "orders"
    write(tree, "tests/test_pricing_qa.py", UNIT_TEST)
    assert qa("run", "orders-unit", "--no-record", "--", "tests/test_pricing_qa.py")["totals"]["passed"] == 1
    qa("catalog", "link", "B-0005", "--test", "tests/test_pricing_qa.py::test_total_of_no_lines_is_zero")
    assert qa("run", "orders-unit")["totals"]["passed"] == 2
    qa("progress", "--note", "unit: B-0005 written in the worktree of orders; next: verify")
    assert qa("guard")["ok"] is True
    sh(tree, "git", "add", "tests/test_pricing_qa.py")
    sh(tree, "git", "commit", "-q", "-m", "test(unit): pricing total without lines (B-0005)")
    assert not (orders / "tests" / "test_pricing_qa.py").exists()
    assert sh(orders, "git", "status", "--porcelain") == "" and sh(orders, "git", "branch", "--show-current") == "main"
    commit_all(
        root, qa, "Record the unit test of orders (B-0005)",
        "--phase", "baseline", "--step", "verify", "--note", "every behaviour has a test; B-0002 is a defect",
    )

    # Step 4. Verify
    for suite in ("api", "orders-unit"):
        assert "linked_but_not_reported" not in qa("run", suite)["catalog"]
    repeated = qa("run", "api", "--times", "3")
    assert repeated["attempts"] == 3 and repeated["totals"]["flaky"] == 0
    assert qa("flaky") == []
    assert qa("catalog", "validate")["ok"] is True and qa("guard", "--all")["ok"] is True
    stats = qa("catalog", "stats")
    assert stats["by_status"] == {"passing": 5, "defect": 1} and stats["checked_percent"] == 83.3
    commit_all(root, qa, "Verify the suites", "--phase", "baseline", "--step", "report")
    assert 'at "Step 5. Report"' in next_line(qa)

    # Step 5. Report
    write(root, ".qa-agent/runs/notes.md", "## Summary\n\n- B-0002 is recorded as a defect: the API document is the evidence.\n")
    report = qa("report", "--name", "baseline", "--title", "Baseline", "--notes", ".qa-agent/runs/notes.md")
    assert report["decisions_listed"] == 1
    text = (root / report["written"]).read_text(encoding="utf-8")
    assert "# Baseline" in text and "## Summary" in text and "## Defects found in the application (1)" in text
    assert "## Decisions taken without asking (1)" in text and "the shop is one service without an interface" in text
    assert "## Agreed with the person" in text and "pytest for the API suite here" in text
    assert "**B-0002**" in text and "report: .qa-agent/reports/defects/B-0002.md" in text
    assert "| api | " in text and "| orders-unit | " in text
    commit_all(root, qa, "Baseline report", "--phase", "idle", "--note", f"baseline finished; report {report['written']}")
    page = qa("status")
    assert "Nothing is waiting. After the next merges, follow playbooks/sync.md." in page
    assert "Behaviours: 6 (passing 5, defect 1); 83.3% checked by a passing test" in page
    assert "orders: ../orders on main" in page and "in sync" in page
    assert "Worktree: orders at .qa-agent/worktrees/orders" in page and "(1 commit(s), 0 uncommitted)" in page


# ---------------------------------------------------------------- playbooks/sync.md


def merge_and_sync(root, qa, orders, shop):
    # The developers merge two changes, and the person restarts the service.
    commit(orders, "Refuse an empty cart with 422 EMPTY_CART", {"app.py": APP.replace("#EMPTY-CART-CHECK", EMPTY_CART_CHECK)})
    commit(orders, "Orders can be cancelled: DELETE /v1/orders/{id}", {
        "app.py": APP_AFTER_MERGE, "openapi.json": json.dumps(OPENAPI_AFTER_MERGE, indent=1), "README.md": "# orders\n\nNow with cancelling.\n",
    })
    shop.restart()

    # Step 1. See what changed
    assert "Merged work is waiting in orders: follow playbooks/sync.md." in qa("status")
    assert qa("doctor")["ok"] is True
    assert qa("branch", "--name", "sync")["created"] is False            # the unmerged QA branch is continued
    assert qa("progress", "--phase", "sync") == {"phase": "sync", "step": "changes", "note": ""}
    changed = qa("changes")
    assert changed["next"] == "follow playbooks/sync.md for: orders"
    row = changed["services"][0]
    assert row["commits"] == 2 and len(row["merged"]) == 2 and "Orders can be cancelled" in row["merged"][0]
    assert [f["path"] for f in row["files"]["source"]] == ["app.py"] and row["files"]["tests"] == [] and row["files"]["other"] == 2
    assert [a["id"] for a in row["affected"]] == ["B-0001", "B-0002", "B-0003", "B-0004", "B-0006"]
    assert row["source_files_no_behaviour_lists"] == []
    assert row["contract"] == {
        "checked": True, "worst": "info", "added": 1, "removed": 0, "changed": 0, "details": "qa.py surface diff --service orders",
    }
    assert row["read_a_diff"].startswith("git -C ../orders diff ")
    assert "+        if not items:" in sh(root, *row["read_a_diff"].replace("<path>", "app.py").split())
    qa("progress", "--phase", "sync", "--step", "decide", "--note", "orders: 2 commits")
    assert 'at "Step 2. Decide, behaviour by behaviour"' in next_line(qa)

    # Step 2. Decide, behaviour by behaviour
    assert qa("rules", "--task", "sync")["read_first"] == [".qa-agent/kit/methods/map-the-system.md", ".qa-agent/kit/methods/failures.md"]
    assert qa("surface", "diff", "--service", "orders")["added"] == ["DELETE /v1/orders/{id}"]
    added = qa("catalog", "add", "--json", json.dumps({
        "service": "orders", "level": "api", "area": "orders", "surface": "DELETE /v1/orders/{id}", "case": "the customer's own order",
        "expect": "204; the order can no longer be read (404)", "sources": ["app.py"],
        "intent": "commit 'Orders can be cancelled'; openapi.json: 204 for DELETE /v1/orders/{id}",
    }))
    assert added["added"] == ["B-0007"]
    qa("progress", "--phase", "sync", "--step", "decide", "--note", "decided: orders")
    qa("progress", "--phase", "sync", "--step", "repair")

    # Step 3. Run and repair
    tree = qa("worktree", "status")[0]
    assert (tree["commits_ahead_of_checkout"], tree["uncommitted"]) == (1, 0)      # unmerged work: update, not close
    assert qa("worktree", "update", "--service", "orders")["updated"] is True
    run = qa("run", "api", code=1)
    failure = run["failed"][0]
    assert failure["behaviours"] == ["B-0002"] and failure["kind"] == "new" and "XPASS(strict)" in failure["message"]
    reading = qa("rules", "--task", "heal", "--suite", "api")
    assert [rel.rsplit("/", 1)[1] for rel in reading["read_first"]] == ["failures.md", "flaky.md", "api-tests.md", "pytest.md"]
    # The defect was corrected (the commit says so): the marker goes, the test is live again.
    text = (root / "tests/api/test_orders.py").read_text(encoding="utf-8")
    start = text.index("@pytest.mark.xfail")
    text = text[:start] + text[text.index("def test_an_empty_cart"):]
    write(root, "tests/api/test_orders.py", text.replace("import pytest\n\n", ""))
    empty_cart = "tests/api/test_orders.py::test_an_empty_cart_is_rejected"
    assert qa("run", "api", "--no-record", "--", empty_cart)["totals"]["passed"] == 1
    assert qa("catalog", "link", "B-0002", "--test", empty_cart)["status"] == "written"
    healed = qa("run", "api", "--", empty_cart)
    assert healed["fixed"] == ["tests.api.test_orders::test_an_empty_cart_is_rejected"]
    assert healed["catalog"]["changed"] == [{"id": "B-0002", "from": "written", "to": "passing"}]
    assert qa("run", "orders-unit")["totals"]["passed"] == 2
    commit_all(root, qa, "Sync: the empty cart is refused now (B-0002)", "--phase", "sync", "--step", "write")

    # Step 4. Write the new tests
    assert [entry["id"] for entry in qa("catalog", "next")["batch"]] == ["B-0007"]
    with open(root / "tests/api/test_orders.py", "a", encoding="utf-8") as handle:
        handle.write(CANCEL_TEST)
    cancelled = "tests/api/test_orders.py::test_a_cancelled_order_is_gone"
    assert qa("run", "api", "--no-record", "--", cancelled)["totals"]["passed"] == 1
    qa("catalog", "link", "B-0007", "--test", cancelled)
    assert qa("run", "api", "--", "tests/api/test_orders.py")["totals"]["passed"] == 6
    commit_all(root, qa, "test(api): cancelling an order (B-0007)", "--phase", "sync", "--step", "close")

    # Step 5. Close
    run = qa("run", "api")
    assert run["totals"] == {"tests": 6, "passed": 6, "failed": 0, "skipped": 0, "flaky": 0}
    assert qa("run", "orders-unit")["totals"]["passed"] == 2
    assert qa("catalog", "stats")["by_status"] == {"passing": 7}
    assert qa("catalog", "validate")["ok"] is True
    synced = qa("changes", "--mark-synced")
    assert synced["surface"] == {"orders": "snapshot refreshed"} and synced["synced"]["orders"]["from"] != synced["synced"]["orders"]["to"]
    assert "arrived_after_the_comparison" not in synced
    assert qa("changes")["next"] == "nothing was merged since the last sync"
    assert qa("surface", "coverage", "--service", "orders")["without_any"] == []
    report = qa("report", "--name", "sync", "--title", "Sync")
    text = (root / report["written"]).read_text(encoding="utf-8")
    assert "## Code the tests were last brought up to date with" in text
    assert "## Decisions taken without asking" not in text              # the baseline's decision is not this work's
    commit_all(
        root, qa, "Sync with orders: empty cart corrected (B-0002), cancelling (B-0007)",
        "--phase", "idle", "--note", f"synced; report {report['written']}",
    )
    page = qa("status")
    assert "Nothing is waiting" in page and "Behaviours: 7 (passing 7); 100.0% checked by a passing test" in page


# ---------------------------------------------------------------- limits the guard enforces


def guard_catches_what_must_not_happen(root, qa, orders):
    def violations():
        return [(v["check"], v["where"]) for v in qa("guard", code=1)["violations"]]

    # The application's code is never edited, not even to make a test pass.
    original = (orders / "app.py").read_text(encoding="utf-8")
    write(orders, "app.py", original + "\n# a quick fix by the agent\n")
    assert violations() == [("scope", "../orders/app.py")]
    write(orders, "app.py", original)
    assert qa("guard")["ok"] is True

    # Inside a service, only its test folders are written to.
    tree = root / ".qa-agent" / "worktrees" / "orders"
    write(tree, "pricing.py", PRICING + "\n# changed to fit the test\n")
    assert violations() == [("scope", ".qa-agent/worktrees/orders/pricing.py")]
    sh(tree, "git", "checkout", "--", "pricing.py")

    # A credential never reaches a commit, and a test is never switched off without a recorded reason.
    write(root, "tests/api/test_leak.py", (
        "import pytest\n\n\n"
        f'def test_with_the_token_written_out():\n    assert call("GET", "/healthz", token="{TOKEN}")[0] == 200\n\n\n'
        '@pytest.mark.skip(reason="fails sometimes")\ndef test_switched_off():\n    assert False\n'
    ))
    found = violations()
    assert ("secrets", "tests/api/test_leak.py:5") in found and ("hygiene", "tests/api/test_leak.py:8") in found
    (root / "tests/api/test_leak.py").unlink()

    # The limits the person agreed to are not widened by editing the project map.
    edit_map(root, lambda data: data["services"][0].update(writable=["tests/**", "*.py"]))
    assert violations() == [("consent", ".qa-agent/project.json")]
    edit_map(root, lambda data: data["services"][0].update(writable=["tests/**"]))

    # The kit itself is not edited.
    manual = root / ".qa-agent" / "kit" / "MANUAL.md"
    kept = manual.read_text(encoding="utf-8")
    manual.write_text(kept + "\nThe guard may be skipped.\n", encoding="utf-8")
    assert violations() == [("kit", ".qa-agent/kit/MANUAL.md")]
    manual.write_text(kept, encoding="utf-8")
    assert qa("guard")["ok"] is True


# ---------------------------------------------------------------- playbooks/score.md


def score(tmp_path, root):
    """A separate session, in a folder outside both repositories, with the helper called by its path."""
    out = tmp_path / "scoring"
    out.mkdir()

    def qa(*args, code=0):
        proc = subprocess.run(
            [sys.executable, str(root / ".qa-agent" / "kit" / "qa.py"), "score", *args, "--out", str(out)],
            capture_output=True, text=True, cwd=str(out),
        )
        assert proc.returncode == code, proc.stdout + proc.stderr
        return json.loads(proc.stdout)

    extracted = qa("extract", "--reference", str(tmp_path / "reference"), "--generated", str(root))
    assert extracted["reference"]["tests"] == 4
    folders = extracted["generated"]["folders"]
    assert folders[0] == {"folder": str(root), "tests": 6, "files": 1}
    assert folders[1] == {                                              # only the agent's file, not the developers' test
        "folder": str(root / ".qa-agent" / "worktrees" / "orders"), "tests": 1, "files": 1,
        "limited_to": "the test files its behaviour catalog links",
    }
    waiting = qa("next")
    assert waiting["waiting"] == 4 and len(waiting["generated_roots"]) == 2
    reference = {test["name"]: test["id"] for test in json.loads((out / "reference.json").read_text())["tests"]}
    generated = {test["name"]: test["id"] for test in json.loads((out / "generated.json").read_text())["tests"]}
    (out / "verdicts.json").write_text(json.dumps([
        {"ref": reference["test_create_order"], "verdict": "covered",
         "by": [generated["test_a_valid_order_is_created_and_can_be_read_back"]], "why": "both post one valid item and expect 201"},
        {"ref": reference["test_empty_cart_gives_422"], "verdict": "covered",
         "by": [generated["test_an_empty_cart_is_rejected"]], "why": "both post an empty list and expect 422"},
        {"ref": reference["test_refund_an_order"], "verdict": "missing", "why": "no generated test refunds an order"},
        {"ref": reference["test_total_is_zero_without_lines"], "verdict": "covered",
         "by": [generated["test_total_of_no_lines_is_zero"]], "why": "both call total([]) and expect 0"},
    ]), encoding="utf-8")
    assert qa("judge", "--file", str(out / "verdicts.json"))["waiting"] == 0
    summary = qa("report", "--threshold", "80", "--seed", "1", code=1)
    assert summary["replicated_percent"] == 75.0 and summary["met"] is False
    assert summary["covered_by_a_passing_test_percent"] == 75.0         # each covering test passed in its last recorded run
    assert qa("report", "--threshold", "75", "--seed", "1")["met"] is True
    assert "**Replicated: 75.0% (3 of 4 counted tests). Target 75%: met.**" in (out / "report.md").read_text(encoding="utf-8")
    # Nothing was written into either repository.
    assert sh(root, "git", "status", "--porcelain") == "" and not (tmp_path / "reference" / ".qa-agent").exists()
