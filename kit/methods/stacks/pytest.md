# pytest

For API, integration and application-specific suites in Python, and for unit tests inside Python services.

## Files

```
requirements.txt        pytest, an HTTP client (httpx or requests), nothing unpinned
pytest.ini
tests/conftest.py       fixtures shared by all levels
tests/support/          config.py, clients.py, data.py, waiting.py
tests/api/test_<service>_<area>.py
```

`pytest.ini`:

```ini
[pytest]
testpaths = tests
addopts = -ra --strict-markers
junit_family = xunit2
xfail_strict = true
```

`xfail_strict` makes every expected failure fail the run when it passes. Do not install `pytest-rerunfailures`, and do not put `-x` or `--maxfail` in a suite command.

## Suite

```json
{
  "name": "api",
  "stack": "pytest",
  "levels": ["api"],
  "command": ".venv/bin/python -m pytest --junitxml={junit}",
  "select": "tests/api",
  "rerun_failed": ".venv/bin/python -m pytest tests/api --last-failed --last-failed-no-failures none --junitxml={junit}",
  "setup": ["python3 -m venv .venv", ".venv/bin/python -m pip install -q -r requirements.txt"],
  "needs": ["orders"]
}
```

Use the project's own tool where it has one (`uv run pytest …`, `poetry run pytest …`).

| To | Write |
| --- | --- |
| run one test | `qa run api -- tests/api/test_orders_cart.py::test_empty_cart_is_rejected` |
| run a file, or by name | `qa run api -- tests/api/test_orders_cart.py`, `qa run api -- tests/api -k "empty_cart"` |
| link a test | `qa catalog link B-0012 --test tests/api/test_orders_cart.py::test_empty_cart_is_rejected` (inside a class: `…py::TestCart::test_empty`) |

## A test

```python
# B-0012
def test_empty_cart_is_rejected(orders_api, user):
    response = orders_api.post("/v1/orders", json={"items": []}, headers=user.headers)

    assert response.status_code == 422
    assert response.json()["code"] == "EMPTY_CART"
    assert orders_api.get("/v1/orders", headers=user.headers).json()["items"] == []
```

- Fixtures: clients and sign-in with `scope="session"`; data with function scope, removed after `yield`, so cleanup runs when the test fails too.
- Cases of one behaviour: `@pytest.mark.parametrize("quantity", [0, -1, 1001], ids=["zero", "negative", "above the limit"])`. Always give `ids`; they appear in the report.
- An expected exception: `with pytest.raises(ValueError, match="quantity"):`.

## Waiting

```python
def wait_until(check, what, timeout=10.0, interval=0.25):
    """Poll `check` until it returns something true; fail saying what was waited for."""
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what} did not happen within {timeout}s")
        time.sleep(interval)  # qa-agent: allow-sleep polling with a deadline
```

## Markers the catalog requires

```python
# A defect: the test is right, the application is wrong.
@pytest.mark.xfail(strict=True, reason="B-0012: answers 200 for an empty cart, expected 422")

# Quarantined: unreliable, cause not found yet.
@pytest.mark.skip(reason="B-0031 quarantined until 2026-01-15: list is sometimes empty right after creation")

# Blocked: something outside the tests is missing.
@pytest.mark.skipif(not os.environ.get("QA_ADMIN_EMAIL"), reason="B-0040 blocked: no admin test account")
```

Record the status in the catalog in the same step (`qa catalog status …`); the guard refuses a marker whose id has no such status.

## Inside a service

Run the service's tests the way its developers do, plus the report:

```json
{
  "name": "orders-unit",
  "stack": "pytest",
  "levels": ["unit", "component"],
  "repo": "orders",
  "command": ".venv/bin/python -m pytest --junitxml={junit}",
  "select": "tests",
  "setup": ["python3 -m venv .venv", ".venv/bin/python -m pip install -q -e '.[dev]'"]
}
```

The virtual environment is created inside the worktree. If the service's own ignore rules do not cover it, create it under `$QA_AGENT_ROOT/.qa-agent/runs/` instead and name that path in the commands.
