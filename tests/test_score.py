import json

import pytest

from qa_agent import score
from qa_agent.util import QAError, read_json
from conftest import write

REFERENCE = {
    "tests/api/test_orders.py": '''
def test_create_order_returns_201(client):
    response = client.post("/v1/orders", json={"items": [{"sku": "apple", "quantity": 2}]})
    assert response.status_code == 201

def test_empty_cart_is_rejected(client):
    response = client.post("/v1/orders", json={"items": []})
    assert response.status_code == 422
    assert response.json()["code"] == "EMPTY_CART"

def test_unknown_order_is_404(client):
    assert client.get("/v1/orders/999999").status_code == 404

def test_orders_need_a_token(anonymous):
    assert anonymous.get("/v1/orders").status_code == 401
''',
    "tests/api/test_refunds.py": '''
import pytest

def test_refund_a_paid_order(client, paid_order):
    response = client.post(f"/v1/orders/{paid_order}/refund")
    assert response.status_code == 200
    assert response.json()["status"] == "refunded"

@pytest.mark.skip(reason="refund emails are not sent in this environment")
def test_refund_sends_an_email(client, mailbox):
    assert mailbox.last()["subject"] == "Your refund"
''',
    "tests/e2e/test_login.py": '''
def test_sign_in_shows_the_order_list(page):
    page.goto("/login")
    page.get_by_label("Email").fill("ada@example.test")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Your orders")).to_be_visible()

def test_wrong_password_shows_an_error(page):
    page.goto("/login")
    page.get_by_label("Password").fill("wrong")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("Wrong email or password")).to_be_visible()
''',
    "pkg/cart_test.go": "package pkg\n",
}

GENERATED = {
    "tests/api/orders.spec.ts": '''
import { test, expect } from '@playwright/test';
const API = process.env.ORDERS_URL;
test.describe('POST /v1/orders', () => {
  test('creates an order with one item', async ({ request }) => {
    const response = await request.post(`${API}/v1/orders`, { data: { items: [{ sku: 'apple', quantity: 2 }] } });
    expect(response.status()).toBe(201);
    expect((await response.json()).items).toHaveLength(1);
  });
  test('rejects an empty cart with EMPTY_CART', async ({ request }) => {
    const response = await request.post(`${API}/v1/orders`, { data: { items: [] } });
    expect(response.status()).toBe(422);
    expect((await response.json()).code).toBe('EMPTY_CART');
  });
  test.skip('answers 401 without a token', async ({ request }) => {
    expect((await request.get(`${API}/v1/orders`)).status()).toBe(401);
  });
});
test('GET /v1/orders/{id} answers 404 for an unknown order', async ({ request }) => {
  expect((await request.get(`${API}/v1/orders/123456`)).status()).toBe(404);
});
''',
    "tests/e2e/login.spec.ts": '''
import { test, expect } from '@playwright/test';
test('signing in opens the order list', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill(process.env.QA_USER);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Your orders' })).toBeVisible();
});
''',
    "tests/api/products.spec.ts": '''
import { test, expect } from '@playwright/test';
test('lists products', async ({ request }) => {
  expect((await request.get(`${process.env.ORDERS_URL}/v1/products`)).status()).toBe(200);
});
''',
    ".qa-agent/kit/tests/test_kit.py": "def test_kit_file_is_not_a_generated_test():\n    assert True\n",
}


@pytest.fixture
def sides(tmp_path):
    for root, files in (("reference", REFERENCE), ("generated", GENERATED)):
        for rel, text in files.items():
            write(tmp_path / root, rel, text)
    out = tmp_path / "scores" / "run1"
    result = score.extract([tmp_path / "reference"], [tmp_path / "generated"], out)
    return tmp_path, out, result


def ids(out):
    ref = {t["name"]: t["id"] for t in read_json(out / "reference.json")["tests"]}
    gen = {t["name"]: t["id"] for t in read_json(out / "generated.json")["tests"]}
    return ref, gen


def test_extract_reads_both_sides_and_proposes_candidates(sides):
    _, out, result = sides
    root = sides[0]
    assert result["reference"] == {"tests": 8, "files": 3, "unsupported_files": 1}
    assert result["generated"] == {                                   # the kit's own files are left out
        "tests": 6, "files": 3, "unsupported_files": 0,
        "folders": [{"folder": str(root / "generated"), "tests": 6, "files": 3}],
    }
    ref, gen = ids(out)
    candidates = read_json(out / "candidates.json")

    def best(name):
        found = candidates[ref[name]]
        return found[0]["id"] if found else None

    assert best("test_create_order_returns_201") == gen["POST /v1/orders › creates an order with one item"]
    assert best("test_empty_cart_is_rejected") == gen["POST /v1/orders › rejects an empty cart with EMPTY_CART"]
    assert best("test_unknown_order_is_404") == gen["GET /v1/orders/{id} answers 404 for an unknown order"]
    assert best("test_orders_need_a_token") == gen["POST /v1/orders › answers 401 without a token"]
    assert best("test_sign_in_shows_the_order_list") == gen["signing in opens the order list"]
    top = candidates[ref["test_empty_cart_is_rejected"]][0]
    assert top["score"] > 0.6 and "POST /v1/orders" in top["same"] and "status 422" in top["same"]
    refund = candidates[ref["test_refund_a_paid_order"]]
    assert not refund or refund[0]["score"] < score.POSSIBLE         # nothing like it was generated
    assert 0 < result["similarity_estimate"]["likely_percent"] < 100


def test_outputs_must_stay_outside_both_repositories(sides, tmp_path):
    root, _, _ = sides
    with pytest.raises(QAError, match="is inside"):
        score.extract([root / "reference"], [root / "generated"], root / "generated" / "scores")
    with pytest.raises(QAError, match="is inside"):
        score.extract([root / "reference"], [root / "generated"], root / "reference" / "out")
    with pytest.raises(QAError, match="overlap"):
        score.extract([root / "reference"], [root / "reference" / "tests"], root / "out")
    write(root / "empty", "README.md", "no tests here\n")
    with pytest.raises(QAError, match="No tests were found in the reference"):
        score.extract([root / "empty"], [root / "generated"], root / "out2")


def test_next_gives_the_judge_what_to_open(sides):
    _, out, _ = sides
    batch = score.next_batch(out, batch=3)
    assert batch["judged"] == 0 and batch["waiting"] == 8 and len(batch["tests"]) == 3
    first = batch["tests"][0]
    assert first["at"] == "tests/api/test_orders.py:2" and first["calls"] == ["POST /v1/orders"] and first["statuses"] == [201]
    assert first["candidates"][0]["at"].startswith("tests/api/orders.spec.ts:") and "same" in first["candidates"][0]


def test_judging_is_validated(sides):
    _, out, _ = sides
    ref, gen = ids(out)
    create, empty = ref["test_create_order_returns_201"], ref["test_empty_cart_is_rejected"]
    token = ref["test_orders_need_a_token"]
    g_create = gen["POST /v1/orders › creates an order with one item"]
    g_token = gen["POST /v1/orders › answers 401 without a token"]
    result = score.judge(out, [
        {"ref": create, "verdict": "covered", "by": [g_create], "why": "both POST one item and expect 201"},
        {"ref": empty, "verdict": "covered", "why": "same check"},                                   # no 'by'
        {"ref": "R-9999", "verdict": "covered", "by": [g_create], "why": "does not exist"},
        {"ref": token, "verdict": "covered", "by": [g_token], "why": "both expect 401 without a token"},   # switched off
        {"ref": token, "verdict": "maybe", "by": [], "why": "not a verdict"},
        {"ref": token, "verdict": "missing", "by": [g_create], "why": "missing takes no test"},
        {"ref": token, "verdict": "missing", "why": ""},
        {"ref": token, "verdict": "covered", "by": ["G-9999"], "why": "no such generated test"},
    ])
    assert result["accepted"] == 1 and result["judged"] == 1 and result["waiting"] == 7
    problems = [" ".join(r["problems"]) for r in result["rejected"]]
    assert "needs 'by'" in problems[0]
    assert "is not a reference test" in problems[1]
    assert "a switched-off test checks nothing" in problems[2]
    assert "verdict must be one of" in problems[3]
    assert "takes no 'by'" in problems[4]
    assert "'why' is required" in problems[5]
    assert "must list generated test ids" in problems[6]
    again = score.judge(out, {"ref": create, "verdict": "partial", "by": [g_create], "why": "the body is not compared"})
    assert again["replaced"] == 1 and again["judged"] == 1


def judge_all(out):
    ref, gen = ids(out)
    verdicts = [
        ("test_create_order_returns_201", "covered", ["POST /v1/orders › creates an order with one item"], "both POST one item and expect 201"),
        ("test_empty_cart_is_rejected", "covered", ["POST /v1/orders › rejects an empty cart with EMPTY_CART"], "same 422 and the same code"),
        ("test_unknown_order_is_404", "covered", ["GET /v1/orders/{id} answers 404 for an unknown order"], "same 404 for an id that does not exist"),
        ("test_orders_need_a_token", "partial", ["POST /v1/orders › answers 401 without a token"], "the generated test is switched off"),
        ("test_refund_a_paid_order", "missing", [], "no generated test calls the refund endpoint"),
        ("test_refund_sends_an_email", "excluded", [], "skipped in the reference suite itself"),
        ("test_sign_in_shows_the_order_list", "covered", ["signing in opens the order list"], "same journey and the same heading"),
        ("test_wrong_password_shows_an_error", "missing", [], "no generated test enters a wrong password"),
    ]
    return score.judge(out, [
        {"ref": ref[name], "verdict": verdict, "by": [gen[g] for g in by], "why": why} for name, verdict, by, why in verdicts
    ])


def test_report_counts_and_writes_what_to_check_by_hand(sides):
    _, out, _ = sides
    with pytest.raises(QAError, match="have no verdict yet"):
        score.report(out)
    summary, code = score.report(out, allow_incomplete=True)
    assert code == 1 and summary["replicated_percent"] == 0.0 and summary["not_judged"] == 8

    assert judge_all(out)["waiting"] == 0
    summary, code = score.report(out, threshold=80, sample=2, seed=7)
    assert code == 1 and summary["met"] is False
    assert summary["counted"] == 7 and summary["covered"] == 4 and summary["partial"] == 1 and summary["missing"] == 2
    assert summary["replicated_percent"] == 57.1                      # 4 of 7; the excluded test is not counted
    assert summary["with_partial_as_half_percent"] == 64.3
    assert summary["generated_tests_matching_no_reference_test"] == 1  # 'lists products' is extra coverage
    text = (out / "report.md").read_text()
    assert "**Replicated: 57.1% (4 of 7 counted tests). Target 80%: NOT met.**" in text
    assert "| tests/api | 6 | 3 | 1 | 1 | 1 | 60% |" in text
    assert "## Check these by hand (2 covered tests, picked at random, seed 7)" in text
    assert "## Missing (2)" in text and "no generated test calls the refund endpoint" in text
    assert "1 reference test file(s) are in a language this scorer does not read" in text
    result = read_json(out / "result.json")
    assert result["seed"] == 7 and len(result["rows"]) == 8

    _, code = score.report(out, threshold=55)
    assert code == 0


def test_generated_tests_named_like_reference_tests_are_pointed_out(tmp_path):
    """Suites written apart rarely share test names; when they do, the reference suite may have been read."""
    for rel, text in REFERENCE.items():
        write(tmp_path / "reference", rel, text)
    write(tmp_path / "generated", "tests/api/test_orders_copy.py", (
        "def test_empty_cart_is_rejected(api):\n    assert api.post('/v1/orders', json={'items': []}).status_code == 422\n\n"
        "def testUnknownOrderIs404(api):\n    assert api.get('/v1/orders/1').status_code == 404\n\n"       # same words, other casing
        "def test_an_order_of_two_apples_is_stored(api):\n    assert api.post('/v1/orders', json={}).status_code == 201\n"
    ))
    write(tmp_path / "reference", "tests/test_health.py", "def test_health(api):\n    assert api.get('/healthz').status_code == 200\n")
    write(tmp_path / "generated", "tests/test_health.py", "def test_health(api):\n    assert api.get('/healthz').status_code == 200\n")
    out = tmp_path / "scores"
    score.extract([tmp_path / "reference"], [tmp_path / "generated"], out)
    summary, _ = score.report(out, allow_incomplete=True)
    assert summary["generated_tests_named_like_a_reference_test"] == 2     # `test_health` is too short to mean anything
    assert "same_names" not in summary
    assert [pair["name"] for pair in read_json(out / "result.json")["same_names"]] == ["test_empty_cart_is_rejected", "testUnknownOrderIs404"]
    text = (out / "report.md").read_text()
    section = text.split("## Named exactly like a reference test (2)")[1].split("\n## ")[0]
    assert "`tests/api/test_orders_copy.py:1` = " in section and "`tests/api/test_orders.py:" in section


def test_suites_written_apart_have_no_names_in_common(sides):
    _, out, _ = sides
    judge_all(out)
    summary, _ = score.report(out, seed=1)
    assert summary["generated_tests_named_like_a_reference_test"] == 0
    assert "Named exactly like a reference test" not in (out / "report.md").read_text()


def test_disagreements_between_verdict_and_similarity_are_listed(sides):
    _, out, _ = sides
    ref, gen = ids(out)
    judge_all(out)
    score.judge(out, [
        {"ref": ref["test_empty_cart_is_rejected"], "verdict": "missing", "why": "judged carelessly"},
        {"ref": ref["test_refund_a_paid_order"], "verdict": "covered", "by": [gen["lists products"]], "why": "judged too generously"},
    ])
    score.report(out, seed=1)
    text = (out / "report.md").read_text()
    section = text.split("## Where the verdict and the similarity disagree")[1].split("## Missing")[0]
    assert "test_refund_a_paid_order" in section.split("Judged missing although")[0]
    assert "test_empty_cart_is_rejected" in section.split("Judged missing although")[1]


SERVICE_TESTS = {
    "tests/test_pricing.py": "def test_total_adds_tax():\n    assert total(100) == 120\n",          # written by the agent
    "tests/test_legacy.py": "def test_written_by_the_developers():\n    assert legacy() is None\n",
}


def kit_state(root, placement, services, entries):
    write(root, ".qa-agent/project.json", json.dumps({"version": 1, "placement": placement, "services": services}))
    write(root, ".qa-agent/state/catalog.json", json.dumps({"version": 1, "next_id": 9, "entries": entries}))


def test_tests_written_inside_a_service_are_found_through_the_catalog(sides):
    root, out, _ = sides
    for rel, text in SERVICE_TESTS.items():
        write(root / "orders", rel, text)
    kit_state(root / "generated", "qa-repo", [{"name": "orders", "repo": "../orders"}, {"name": "web", "repo": "../web"}], [
        {"id": "B-0001", "repo": "orders", "status": "passing", "tests": ["tests/test_pricing.py::test_total_adds_tax"]},
        # The developers' own test already checks this one: it is not a generated test.
        {"id": "B-0002", "repo": "orders", "status": "covered", "tests": ["tests/test_legacy.py::test_written_by_the_developers"]},
        {"id": "B-0003", "repo": ".", "status": "passing", "tests": ["tests/api/products.spec.ts::lists products"]},
    ])
    result = score.extract([root / "reference"], [root / "generated"], out)
    folders = result["generated"]["folders"]
    assert folders[0] == {"folder": str(root / "generated"), "tests": 6, "files": 3}       # a QA repository counts whole
    assert folders[1] == {
        "folder": str(root / "orders"), "tests": 1, "files": 1,
        "limited_to": "the test files its behaviour catalog links",
    }
    names = [t["name"] for t in read_json(out / "generated.json")["tests"]]
    assert "test_total_adds_tax" in names and "test_written_by_the_developers" not in names
    assert score.next_batch(out, 1)["generated_roots"] == [str(root / "generated"), str(root / "orders")]

    # An open QA worktree is read instead of the checkout, and a linked file that is nowhere is reported.
    write(root / "generated" / ".qa-agent" / "worktrees" / "orders", "tests/test_pricing.py", SERVICE_TESTS["tests/test_pricing.py"])
    kit_state(root / "generated", "qa-repo", [{"name": "orders", "repo": "../orders"}], [
        {"id": "B-0001", "repo": "orders", "status": "passing", "tests": ["tests/test_pricing.py::test_total_adds_tax"]},
        {"id": "B-0004", "repo": "orders", "status": "written", "tests": ["tests/test_gone.py::test_gone"]},
    ])
    folders = score.extract([root / "reference"], [root / "generated"], out)["generated"]["folders"]
    assert folders[1]["folder"] == str(root / "generated" / ".qa-agent" / "worktrees" / "orders")
    assert folders[1]["linked_files_not_found"] == 1 and folders[1]["first_not_found"] == ["tests/test_gone.py"]

    # The scoring folder may not sit inside a service's repository either.
    with pytest.raises(QAError, match="is inside"):
        score.extract([root / "reference"], [root / "generated"], root / "generated" / ".qa-agent" / "worktrees" / "orders" / "out")


def test_inside_an_application_repository_only_linked_tests_count(tmp_path):
    for rel, text in REFERENCE.items():
        write(tmp_path / "reference", rel, text)
    app = tmp_path / "app"
    for rel, text in SERVICE_TESTS.items():
        write(app, rel, text)
    kit_state(app, "service", [{"name": "orders", "repo": "."}], [
        {"id": "B-0001", "repo": ".", "status": "passing", "tests": ["tests/test_pricing.py::test_total_adds_tax"]},
    ])
    result = score.extract([tmp_path / "reference"], [app], tmp_path / "out")
    assert result["generated"]["tests"] == 1
    assert result["generated"]["folders"] == [{
        "folder": str(app), "tests": 1, "files": 1, "limited_to": "the test files its behaviour catalog links",
    }]


def test_run_history_of_the_generated_repository_is_used(sides):
    root, out, _ = sides
    write(root / "generated", ".qa-agent/state/ledger.json", json.dumps({"version": 1, "suites": {"api": {"runs": [], "tests": {
        "tests/api/orders.spec.ts::POST /v1/orders › creates an order with one item": "pp",
        "tests/api/orders.spec.ts::POST /v1/orders › rejects an empty cart with EMPTY_CART": "pf",
        "tests/api/orders.spec.ts::GET /v1/orders/{id} answers 404 for an unknown order": "fx",
    }}}}))
    score.extract([root / "reference"], [root / "generated"], out)
    assert list(out.glob("mapping-*.json"))                # the earlier (empty) verdicts were set aside, not lost
    gen = {t["name"]: t.get("last_run") for t in read_json(out / "generated.json")["tests"]}
    assert gen["POST /v1/orders › creates an order with one item"] == "passed"
    assert gen["POST /v1/orders › rejects an empty cart with EMPTY_CART"] == "failed"
    assert gen["GET /v1/orders/{id} answers 404 for an unknown order"] == "flaky"
    assert gen["signing in opens the order list"] == "not run"
    judge_all(out)
    summary, _ = score.report(out, seed=1)
    assert summary["covered_by_a_passing_test_percent"] == 28.6      # 2 of 7: created, and the 404 that passed on a retry
    assert "passed in its last recorded run: 28.6%" in (out / "report.md").read_text()
