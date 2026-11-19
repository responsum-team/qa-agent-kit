# Scaffolding the suites

A suite is a set of tests that runs from one command and writes one JUnit report. The scaffold is done when every suite runs through `qa run`, a smoke test passes in each, and a new clone can be prepared with `qa run <suite> --setup`.

## Layout

In a QA repository (`placement: qa-repo`), one folder per level and shared support beside them:

```
tests/
  api/            one file per service and area: test_orders_cart.py, orders.cart.spec.ts
  integration/
  e2e/
  app/
  support/        configuration, API clients, sign-in, data helpers, waiting helper
.env.example      every setting the tests read, without values
README.md         how to run the tests
```

In an application's own repository (`placement: service`), the same layout sits inside the one folder its `writable` globs give to these suites (here `qa/`), and every suite there has `"workdir": "qa"`:

```
qa/
  tests/          as above
  .gitignore      the tests' own: .venv/, node_modules/, test-results/
  README.md       how to run the tests
  (the tests' own dependency list: requirements.txt or package.json)
.qa-agent/.env.example    every setting the tests read, without values
```

The application's manifests, its `.gitignore` and its README stay untouched. Unit and component tests go where the repository already keeps them.

## Support code every suite needs

- **Configuration**: one module that reads the service URLs (`ORDERS_URL`, …) and the credentials named in `auth.env` from the environment, and stops with a clear message when one is missing. `qa run` puts the env file's values into the environment. So that a person can run the tests directly as well, the module also loads the env file itself: `.env` at the top of a QA repository, `.qa-agent/.env` in an application's own. It loads it; you never open it.
- **API client** per service: base URL, sign-in, a timeout on every request.
- **Sign-in**: done once per role and reused. Saved browser state goes to the paths listed in `auth.state_files`, which are git-ignored: under `.auth/` in a QA repository, under `.qa-agent/.auth/` in an application's own (`QA_AGENT_ROOT` holds the repository's path during a run).
- **Data helpers**: create what a test needs through the application's own interface, with unique `qa-` names, and remove it afterwards.
- **A waiting helper**: polls a condition until a deadline and fails with what it was waiting for.

Put dependencies in the tests' own manifest and install them into a local, git-ignored folder (`.venv`, `node_modules`).

## Defining a suite

Suites are listed in `.qa-agent/project.json`; run `qa config validate` after editing.

```json
"suites": [
  {
    "name": "api",
    "stack": "pytest",
    "levels": ["api"],
    "command": ".venv/bin/python -m pytest --junitxml={junit}",
    "select": "tests/api",
    "rerun_failed": ".venv/bin/python -m pytest tests/api --last-failed --last-failed-no-failures none --junitxml={junit}",
    "setup": ["python3 -m venv .venv", ".venv/bin/python -m pip install -q -r requirements.txt"],
    "needs": ["orders"],
    "timeout_minutes": 20
  }
]
```

| Field | Meaning |
| --- | --- |
| `name` | what `qa run <name>` runs |
| `stack` | a file name from `methods/stacks/` (`pytest`, `playwright-python`, `playwright-typescript`, `vitest-jest`); `qa rules --suite` then lists that file, and `methods/stacks/other.md` for any other runner |
| `levels` | the behaviour levels its tests check; `qa catalog next` uses it to say which suite a behaviour belongs to |
| `repo` | `.` (the default) for this repository, or a service's name for tests inside that service: the suite then runs in its QA worktree |
| `workdir` | the folder inside that repository to run in, when it is not the top |
| `command` | runs the tests and writes a JUnit report to `{junit}`. No option that stops at the first failure, none that retries |
| `select` | what selects the whole suite: a folder, a marker. `qa run <name> -- <arguments>` puts the arguments in its place, which is how one test is run alone |
| `rerun_failed` | optional: runs again only what failed in the run before it, also writing `{junit}`. A test that fails and then passes is recorded as flaky |
| `setup` | every command that prepares a fresh clone for this suite, in order; they run only with `qa run <name> --setup` |
| `needs` | services that must answer their health check before the suite runs |
| `timeout_minutes` | after this the run is stopped; default 30 |
| `on_request` | `true` for a suite that costs money or takes very long: "every suite" in the playbooks then leaves it out |

Every command runs through the shell in the suite's folder, with the env file's values and two more variables: `QA_AGENT_RUN_DIR`, a folder for this run's traces, screenshots and logs (never committed), and `QA_AGENT_ROOT`, this repository's path.

`qa run` exits with 2 and a `reason` when it could not run the suite: read `reason` and `log_tail`, correct what they name, run again.

## Rules for the runner's configuration

- **No retries in the runner.** The kit re-runs failures itself; a runner that retries hides flaky tests as passing. The guard reports retry settings and retry markers.
- **Start serial.** Turn on parallel workers only after the suite has passed three times in a row, and check again afterwards.
- **Expected failures are strict**: a test marked as expected to fail must fail the run when it passes.
- **Artifacts go to `QA_AGENT_RUN_DIR`**, not into the repository.

## The first tests

Write one smoke test per suite: the services it needs answer, and a test user can sign in. Record these as behaviours like any other, so no test in a suite is without one (`qa run` lists `tests_without_a_behaviour`).

## README

Add a "Running the tests" section to the README beside the tests (the top one in a QA repository, the tests' own in an application's): what must be installed, which settings go into the env file (names only), one command per suite, what a suite marked `on_request` costs, and where reports are.
