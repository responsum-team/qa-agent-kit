# Playwright Test (TypeScript)

One runner for API, integration, end-to-end and application-specific suites: each level is a project in `playwright.config.ts`.

## Files

```
package.json              devDependencies: @playwright/test, dotenv, typescript
playwright.config.ts
tests/support/            config.ts, clients.ts, data.ts
tests/api/<service>.<area>.spec.ts
tests/e2e/auth.setup.ts   signs in once and saves the state
tests/e2e/<journey>.spec.ts
.auth/                    saved sign-in state (git-ignored, listed in auth.state_files)
```

```ts
// playwright.config.ts
import 'dotenv/config';
import { defineConfig } from '@playwright/test';

const runDir = process.env.QA_AGENT_RUN_DIR;

export default defineConfig({
  retries: 0,          // the kit re-runs failures itself, so a flaky test is recorded as flaky
  forbidOnly: true,
  workers: 1,
  outputDir: runDir ? `${runDir}/artifacts` : 'test-results',
  reporter: [['line'], ['junit', { outputFile: process.env.QA_JUNIT_FILE ?? 'test-results/junit.xml' }]],
  use: { trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'api', testDir: 'tests/api' },
    { name: 'integration', testDir: 'tests/integration' },
    { name: 'app', testDir: 'tests/app' },
    { name: 'e2e-setup', testDir: 'tests/e2e', testMatch: /.*\.setup\.ts/ },
    {
      name: 'e2e',
      testDir: 'tests/e2e',
      dependencies: ['e2e-setup'],
      use: { baseURL: process.env.WEB_URL, storageState: '.auth/user.json' },
    },
  ],
});
```

## Suites

```json
{
  "name": "api",
  "stack": "playwright-typescript",
  "levels": ["api"],
  "command": "QA_JUNIT_FILE={junit} npx playwright test --project=api",
  "rerun_failed": "QA_JUNIT_FILE={junit} npx playwright test --project=api --last-failed",
  "setup": ["npm ci", "npx playwright install chromium"],
  "needs": ["orders"]
}
```

One suite per project; the project selects the tests, so no `select` is needed. Use `npm install` in `setup` until a lock file exists. If the browser fails to start because system libraries are missing, installing them is the person's to do: record the journeys as `blocked` with the command Playwright prints, go on with the other suites, and put the request at the top of the report.

`retries` stays 0 and no test gets a retry: the guard reports both.

| To | Write |
| --- | --- |
| run one file | `qa run api -- tests/api/orders.cart.spec.ts` |
| run one test | `qa run api -- tests/api/orders.cart.spec.ts -g "rejects an empty cart"` |
| link a test | `qa catalog link B-0012 --test "tests/api/orders.cart.spec.ts::rejects an empty cart"` (inside a `describe`: `…spec.ts::POST /v1/orders::rejects an empty cart`) |

Give tests fixed titles: no values filled into the title, or the catalog cannot find the test.

## An API test

```ts
import { test, expect } from '@playwright/test';
import { ordersUrl, userHeaders } from '../support/config';

test.describe('POST /v1/orders', () => {
  // B-0012
  test('rejects an empty cart', async ({ request }) => {
    const response = await request.post(`${ordersUrl}/v1/orders`, { headers: await userHeaders(), data: { items: [] } });

    expect(response.status()).toBe(422);
    expect((await response.json()).code).toBe('EMPTY_CART');
  });
});
```

## An end-to-end test

```ts
// tests/e2e/auth.setup.ts
import { test as setup, expect } from '@playwright/test';

setup('sign in as the test user', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill(process.env.QA_USER_EMAIL!);
  await page.getByLabel('Password').fill(process.env.QA_USER_PASSWORD!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/orders/);
  await page.context().storageState({ path: '.auth/user.json' });
});
```

The labels and addresses are placeholders: take the real ones from the application. Tests of signing in start without the saved state: `test.use({ storageState: { cookies: [], origins: [] } })`.

In an application's own repository the state file is under `.qa-agent/.auth/`, not beside the tests: build its path in `tests/support/config.ts` from `QA_AGENT_ROOT`, and from the file's own location when that variable is not set (a person running the tests directly).

Where a program cannot sign in (`methods/e2e-tests.md`), the person saves the state, and the setup project only checks it:

```
npx playwright codegen --save-storage=.auth/user.json <the application's address>
```

A browser opens; the person signs in and closes it; the state is saved. `auth.setup.ts` then opens a page that needs sign-in with that state and, when it lands on the sign-in page, throws an error saying that the saved sign-in state has expired, with the command above.

- Find elements with `getByRole`, `getByLabel`, `getByText`, `getByTestId`.
- Assert with `await expect(locator)…`: `toBeVisible`, `toHaveText`, `toHaveValue`, `toHaveCount`, and `expect(page).toHaveURL`. They wait by themselves.
- Never `page.waitForTimeout(...)`. For a condition that is not an element: `await expect.poll(async () => …, { timeout: 10_000 }).toBe(…)`.
- Clean up in `test.afterEach`, or in a fixture after `await use(...)`.

## Markers the catalog requires

```ts
// B-0012: answers 200 for an empty cart, expected 422
test.fail('rejects an empty cart', async ({ request }) => { /* the test, unchanged */ });

// B-0031 quarantined until 2026-01-15: list is sometimes empty right after creation
test.fixme('shows a new order in the list', async ({ page }) => { /* … */ });

// B-0040 blocked: no admin test account
test.skip('an admin can refund an order', async ({ page }) => { /* … */ });
```

`test.fail` is strict by itself: the test fails the run when it passes. Record the status in the catalog in the same step (`qa catalog status …`); the guard refuses a marker whose id has no such status.
