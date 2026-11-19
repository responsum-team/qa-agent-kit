import pytest

from qa_agent import testscan
from qa_agent.testscan import norm_path, scan_js, scan_python
from conftest import write


@pytest.mark.parametrize("text, expected", [
    ("/orders", "/orders"),
    ("/orders/", "/orders"),
    ("/", "/"),
    ("http://127.0.0.1:8000/v1/Orders?limit=5", "/v1/orders"),
    ("https://shop.example/", "/"),
    ("{}/v1/orders/{}", "/v1/orders/{}"),                 # an interpolated base URL and identifier
    ("{}v1/orders", "/v1/orders"),
    ("/orders/123/items/9", "/orders/{}/items/{}"),
    ("/orders/3fa85f64-5717-4562-b3fc-2c963f66afa6", "/orders/{}"),
    ("/orders/:id", "/orders/{}"),
    ("/orders/<order_id>/pay", "/orders/{}/pay"),
    ("/orders/%s", "/orders/{}"),
    ("/files/{name}.pdf", "/files/{}.pdf"),
    ("orders", None),
    ("a sentence, not a path", None),
    ("//cdn.example/x.js", None),
    ("", None),
    (None, None),
])
def test_norm_path(text, expected):
    assert norm_path(text) == expected


PYTHON = '''
import pytest
import requests

BASE = "http://127.0.0.1:8000"

def _create(client, items):
    response = client.post(f"{BASE}/v1/orders", json={"items": items})
    assert response.status_code == 201
    return response.json()

def helper_without_test_prefix():
    return 1

def test_create_order(client):
    """An order with one item is stored."""
    order = _create(client, [{"sku": "apple", "quantity": 2}])
    again = client.get(BASE + "/v1/orders/" + order["id"])
    assert again.json()["items"][0]["sku"] == "apple"

class TestCart:
    @pytest.mark.parametrize("quantity", [0, -1, 1000])
    def test_bad_quantity(self, client, quantity):
        response = client.post("/v1/orders", json={"items": [{"sku": "apple", "quantity": quantity}]})
        assert response.status_code == 422
        assert response.json()["code"] == "BAD_QUANTITY"

    @pytest.mark.xfail(reason="B-0012: an empty cart is accepted", strict=True)
    def test_empty_cart(self, client):
        assert client.post("/v1/orders", json={"items": []}).status_code == 422

    @pytest.mark.skip(reason="B-0013")
    def test_later(self):
        pass

async def test_unknown_order(client):
    with pytest.raises(requests.HTTPError):
        client.request("DELETE", "/v1/orders/%s" % 42).raise_for_status()

def test_sign_in(page):
    page.goto("/login")
    page.get_by_label("Email").fill("ada@example.test")
    expect(page.get_by_role("heading", name="Your orders")).to_be_visible()

def test_nothing_checked(client):
    client.get("/v1/orders")
    data = {"a": 1}.get("a")
'''


def by_name(tests):
    return {test["name"]: test for test in tests}


def test_python_tests_and_what_they_do():
    tests = by_name(scan_python(PYTHON, "tests/api/test_orders.py"))
    assert list(tests) == [
        "test_create_order", "TestCart › test_bad_quantity", "TestCart › test_empty_cart", "TestCart › test_later",
        "test_unknown_order", "test_sign_in", "test_nothing_checked",
    ]
    create = tests["test_create_order"]
    assert create["ref"] == "tests/api/test_orders.py::test_create_order" and create["line"] == 15
    assert create["calls"] == ["GET /v1/orders/{}", "POST /v1/orders"]      # the helper's call counts too
    assert create["statuses"] == [201] and create["assertions"] == 2
    assert {"apple", "stored", "sku"} <= set(create["body_words"])
    bad = tests["TestCart › test_bad_quantity"]
    assert bad["ref"] == "tests/api/test_orders.py::TestCart::test_bad_quantity"
    assert bad["params"] == 3 and bad["statuses"] == [422] and bad["calls"] == ["POST /v1/orders"]
    assert tests["TestCart › test_empty_cart"]["expected_failure"] is True
    assert tests["TestCart › test_later"]["skipped"] is True and tests["TestCart › test_later"]["assertions"] == 0
    unknown = tests["test_unknown_order"]
    assert unknown["calls"] == ["DELETE /v1/orders/{}"] and unknown["assertions"] == 2
    sign_in = tests["test_sign_in"]
    assert sign_in["visits"] == ["/login"] and sign_in["assertions"] == 1 and sign_in["calls"] == []
    nothing = tests["test_nothing_checked"]
    assert nothing["assertions"] == 0 and nothing["calls"] == ["GET /v1/orders"]     # {}.get("a") is not a request


def test_python_module_and_class_marks_reach_every_test():
    text = "import pytest\npytestmark = pytest.mark.skip(reason='x')\ndef test_a():\n    assert 1\n"
    assert scan_python(text, "t.py")[0]["skipped"] is True
    text = "import pytest\n@pytest.mark.xfail\nclass TestX:\n    def test_a(self):\n        assert 1\n"
    assert scan_python(text, "t.py")[0]["expected_failure"] is True
    assert scan_python("def broken(:\n", "t.py") == []


JS = r'''
import { test, expect } from '@playwright/test';
// test('commented out', async () => { expect(1).toBe(1); });
/* describe('also commented', () => { it('nope', () => {}) }) */
const API = process.env.ORDERS_URL;
const pattern = /\/orders\/(\d+)/;     // a regex with a quote ' and a paren (
function test_helper() { return 1; }

test.describe('Orders API', () => {
  test.beforeEach(async ({ page }) => { await page.goto('/setup-is-not-a-test'); });

  test('creates an order', async ({ request }) => {
    const response = await request.post(`${API}/v1/orders`, { data: { items: [{ sku: 'apple', quantity: 2 }] } });
    expect(response.status()).toBe(201);
    const body = await response.json();
    expect(body.items[0].sku).toBe('apple');
    await test.step('reads it back', async () => {
      const again = await request.get(`${API}/v1/orders/${body.id}`);
      expect(again.ok()).toBeTruthy();
    });
  });

  test.describe('validation', () => {
    test('rejects an empty cart', async ({ request }) => {
      const response = await request.post(`${API}/v1/orders`, { data: { items: [] }, timeout: 500 });
      expect(response.status()).toBe(422);
    });

    test.skip('rejects a huge quantity (B-0031)', async ({ request }) => {
      expect((await request.post(`${API}/v1/orders`, { data: {} })).status()).toBe(422);
    });

    test.fixme('handles "quotes" and `ticks`', async () => {});
  });

  for (const role of ['admin', 'guest']) {
    test(`lists orders as ${role}`, async ({ page }) => {
      test.skip(role === 'guest', 'guests see nothing');
      await page.goto('/orders');
      await page.waitForTimeout(300);
      await expect(page.getByRole('heading', { name: 'Your orders' })).toBeVisible();
    });
  }
});

test.only('focused', async ({ page }) => { await page.goto("/"); });

test('does nothing useful', async ({ request }) => {
  await request.delete(API + '/v1/orders/7');
});
'''


def test_js_tests_groups_and_what_they_do():
    tests = by_name(scan_js(JS, "tests/api/orders.spec.ts"))
    assert list(tests) == [
        "Orders API › creates an order",
        "Orders API › validation › rejects an empty cart",
        "Orders API › validation › rejects a huge quantity (B-0031)",
        'Orders API › validation › handles "quotes" and `ticks`',
        "Orders API › lists orders as {}",
        "focused",
        "does nothing useful",
    ]
    create = tests["Orders API › creates an order"]
    assert create["ref"] == "tests/api/orders.spec.ts::Orders API::creates an order"
    assert create["calls"] == ["GET /v1/orders/{}", "POST /v1/orders"] and create["statuses"] == [201]
    assert create["assertions"] == 3 and create["line"] == 12 and create["end_line"] == 21
    empty = tests["Orders API › validation › rejects an empty cart"]
    assert empty["statuses"] == [422]                       # the timeout of 500 is not a status
    assert tests["Orders API › validation › rejects a huge quantity (B-0031)"]["skipped"] is True
    assert tests['Orders API › validation › handles "quotes" and `ticks`']["skipped"] is True
    listing = tests["Orders API › lists orders as {}"]
    assert listing["visits"] == ["/orders"] and listing["skipped"] is False and listing["statuses"] == []
    assert tests["focused"]["only"] is True and tests["focused"]["visits"] == ["/"]
    nothing = tests["does nothing useful"]
    assert nothing["assertions"] == 0 and nothing["calls"] == ["DELETE /v1/orders/{}"]


VITEST = r'''
import { describe, it, expect } from 'vitest';
describe('cart', () => {
  it('totals the items', () => { expect(total([{ price: 2, quantity: 3 }])).toBe(6); });
  it.each([[0], [-1]])('rejects quantity %i', (quantity) => { expect(() => add(quantity)).toThrow(); });
  it.todo('applies a coupon');
  xit('old style skip', () => {});
  it.skipIf(process.env.CI)('needs a display', () => { expect(render()).toBeTruthy(); });
});
describe.skip('legacy', () => { it('is skipped with its group', () => { expect(1).toBe(1); }); });
const result = regexTest.test('not a test');
const obj = { it: 1, test: 2 };
'''


def test_vitest_and_jest_forms():
    tests = by_name(scan_js(VITEST, "src/cart.test.ts"))
    assert list(tests) == [
        "cart › totals the items", "cart › rejects quantity %i", "cart › applies a coupon", "cart › old style skip",
        "cart › needs a display", "legacy › is skipped with its group",
    ]
    assert tests["cart › rejects quantity %i"]["params"] == 0 and tests["cart › rejects quantity %i"]["assertions"] == 1
    assert tests["cart › applies a coupon"]["skipped"] is True
    assert tests["cart › old style skip"]["skipped"] is True
    assert tests["cart › needs a display"]["skipped"] is True
    assert tests["legacy › is skipped with its group"]["skipped"] is True


def test_cypress_and_supertest_calls():
    text = """
    describe('checkout', () => {
      it('pays', () => {
        cy.visit('/checkout');
        cy.request('POST', '/api/pay', { amount: 5 }).its('status').should('eq', 200);
        cy.get('.total').should('contain', '5');
      });
      it('lists', async () => {
        await request(app).get('/api/orders').expect(200);
        const res = await fetch('http://localhost:3000/api/health');
        assert.equal(res.status, 200);
      });
    });
    """
    tests = by_name(scan_js(text, "cypress/e2e/checkout.cy.js"))
    pays = tests["checkout › pays"]
    assert pays["visits"] == ["/checkout"] and pays["calls"] == ["POST /api/pay"] and pays["statuses"] == [200]
    lists = tests["checkout › lists"]
    assert lists["calls"] == ["* /api/health", "GET /api/orders"] and lists["assertions"] >= 1


def test_broken_or_odd_js_does_not_crash():
    assert scan_js("test('unclosed', async () => {", "a.spec.ts") == []
    assert scan_js("const a = `${`nested ${1}`}`; test('ok', () => { expect(1).toBe(1) })", "a.spec.ts")[0]["name"] == "ok"
    assert scan_js("", "a.spec.ts") == []


def test_scan_tree_finds_test_files_and_lists_other_languages(tmp_path):
    write(tmp_path, "tests/api/test_orders.py", "def test_a():\n    assert 1\n")
    write(tmp_path, "tests/api/helpers.py", "def test_not_collected():\n    assert 1\n")
    write(tmp_path, "tests/conftest.py", "import pytest\n")
    write(tmp_path, "e2e/login.spec.ts", "test('signs in', async () => { expect(1).toBe(1) })")
    write(tmp_path, "e2e/pages/login.ts", "export class LoginPage {}")
    write(tmp_path, "src/__tests__/cart.js", "it('adds', () => { expect(1).toBe(1) })")
    write(tmp_path, "pkg/cart_test.go", "package pkg")
    write(tmp_path, "node_modules/x/x.test.js", "it('dep', () => {})")
    write(tmp_path, "types/api.d.ts", "declare const x: number")
    found = testscan.scan_tree(tmp_path)
    assert sorted(test["ref"] for test in found["tests"]) == [
        "e2e/login.spec.ts::signs in", "src/__tests__/cart.js::adds", "tests/api/test_orders.py::test_a",
    ]
    assert found["files"] == 3 and found["unsupported"] == ["pkg/cart_test.go"]
    more = testscan.scan_tree(tmp_path, include=["tests/api/helpers.py"], exclude=["e2e"])
    assert sorted(test["file"] for test in more["tests"]) == ["src/__tests__/cart.js", "tests/api/helpers.py", "tests/api/test_orders.py"]
