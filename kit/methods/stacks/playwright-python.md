# Playwright with pytest

For end-to-end suites in Python (`pytest-playwright`). Everything in `pytest.md` applies; this file adds the browser.

## Files

```
requirements.txt          pytest, pytest-playwright
tests/e2e/conftest.py     base URL, signed-in state
tests/e2e/pages/          page objects
tests/e2e/test_<journey>.py
.auth/                    saved sign-in state (git-ignored, listed in auth.state_files)
```

## Suite

```json
{
  "name": "e2e",
  "stack": "playwright-python",
  "levels": ["e2e"],
  "command": ".venv/bin/python -m pytest --junitxml={junit} --output \"$QA_AGENT_RUN_DIR/artifacts\" --tracing retain-on-failure --screenshot only-on-failure",
  "select": "tests/e2e",
  "rerun_failed": ".venv/bin/python -m pytest tests/e2e --last-failed --last-failed-no-failures none --junitxml={junit} --output \"$QA_AGENT_RUN_DIR/artifacts-rerun\" --tracing retain-on-failure",
  "setup": ["python3 -m venv .venv", ".venv/bin/python -m pip install -q -r requirements.txt", ".venv/bin/python -m playwright install chromium"],
  "needs": ["web", "orders"]
}
```

`playwright install chromium` downloads the browser into Playwright's own cache. If the browser then fails to start because system libraries are missing, installing them is the person's to do: record the journeys as `blocked` with the command Playwright prints, go on with the other suites, and put the request at the top of the report.

The report names a test `test_sign_in_shows_the_orders[chromium]`; link it without the bracket: `tests/e2e/test_sign_in.py::test_sign_in_shows_the_orders`.

## Base URL and sign-in

```python
# tests/e2e/conftest.py
import os
import pytest

STATE = ".auth/user.json"


@pytest.fixture(scope="session")
def base_url():
    return os.environ["WEB_URL"]


@pytest.fixture(scope="session")
def signed_in_state(browser, base_url):
    context = browser.new_context(base_url=base_url)
    page = context.new_page()
    page.goto("/login")
    page.get_by_label("Email").fill(os.environ["QA_USER_EMAIL"])
    page.get_by_label("Password").fill(os.environ["QA_USER_PASSWORD"])
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/orders")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    context.storage_state(path=STATE)
    context.close()
    return STATE


@pytest.fixture
def user_page(browser, base_url, signed_in_state):
    context = browser.new_context(base_url=base_url, storage_state=signed_in_state)
    yield context.new_page()
    context.close()
```

The labels and addresses above are placeholders: take the real ones from the application. Tests of signing in use the plain `page` fixture, not the saved state.

In an application's own repository the state file is under `.qa-agent/.auth/`, not beside the tests: build its path from `QA_AGENT_ROOT`, and from the support code's own location when that variable is not set (a person running the tests directly).

Where a program cannot sign in (`methods/e2e-tests.md`), the person saves the state, and `signed_in_state` only checks it:

```
.venv/bin/python -m playwright codegen --save-storage=.auth/user.json <the application's address>
```

A browser opens; the person signs in and closes it; the state is saved. The fixture then opens a page that needs sign-in with `storage_state=STATE` and fails with `pytest.fail("the saved sign-in state has expired: …the command above…")` when it lands on the sign-in page.

## A test

```python
from playwright.sync_api import expect

# B-0051
def test_placing_an_order_shows_it_in_the_list(user_page, orders_api, product):
    user_page.goto(f"/products/{product.id}")
    user_page.get_by_role("button", name="Add to cart").click()
    user_page.get_by_role("link", name="Cart").click()
    user_page.get_by_role("button", name="Place order").click()

    expect(user_page.get_by_role("heading", name="Order placed")).to_be_visible()
    user_page.reload()
    expect(user_page.get_by_role("row", name=product.name)).to_be_visible()
```

- Find elements with `get_by_role`, `get_by_label`, `get_by_text`, `get_by_test_id`.
- Assert with `expect(...)`: `to_be_visible`, `to_have_text`, `to_have_value`, `to_have_url`, `to_have_count`. They wait by themselves.
- Never `page.wait_for_timeout(...)`. To wait for something that is not an element, use `page.wait_for_url(...)`, `page.expect_response(...)`, or the polling helper from `pytest.md`.
- Markers for defects, quarantine and blocks are those of `pytest.md`.
