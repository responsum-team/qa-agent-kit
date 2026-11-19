# Vitest and Jest

For unit and component tests inside JavaScript and TypeScript services. Use whichever the service already uses, with its existing configuration; never add a package to the service.

## Vitest

```json
{
  "name": "web-unit",
  "stack": "vitest-jest",
  "levels": ["unit", "component"],
  "repo": "web",
  "command": "npx vitest run --reporter=default --reporter=junit --outputFile.junit={junit}",
  "setup": ["npm ci"]
}
```

| To | Write |
| --- | --- |
| run one file | `qa run web-unit -- src/cart/total.test.ts` |
| run one test | `qa run web-unit -- src/cart/total.test.ts -t "adds tax"` |
| link a test | `qa catalog link B-0070 --test "src/cart/total.test.ts::total::adds tax"` |

Vitest has no "only what failed last time", so the suite has no `rerun_failed`. To tell a flaky test from a failing one, run it again: `qa run web-unit --times 3 -- <file>`.

Markers: `test.fails('…', …)` for a defect (it fails the run when the test passes), `test.skip('…', …)` for quarantine and blocks; the behaviour id goes in a comment on the line above.

## Jest

Jest needs the `jest-junit` package for the report. If the service does not have it, install it in this repository and name it by path, so the service's own files stay as they are:

```json
{
  "name": "web-unit",
  "stack": "vitest-jest",
  "levels": ["unit", "component"],
  "repo": "web",
  "command": "JEST_JUNIT_OUTPUT_FILE={junit} JEST_JUNIT_CLASSNAME='{filepath}' JEST_JUNIT_ANCESTOR_SEPARATOR=' › ' npx jest --reporters=default --reporters=\"$QA_AGENT_ROOT/node_modules/jest-junit\"",
  "rerun_failed": "JEST_JUNIT_OUTPUT_FILE={junit} JEST_JUNIT_CLASSNAME='{filepath}' JEST_JUNIT_ANCESTOR_SEPARATOR=' › ' npx jest --onlyFailures --reporters=default --reporters=\"$QA_AGENT_ROOT/node_modules/jest-junit\"",
  "setup": ["npm ci"]
}
```

`JEST_JUNIT_CLASSNAME='{filepath}'` puts the file into the report, which is what lets two tests with the same name in different files be told apart.

| To | Write |
| --- | --- |
| run one test | `qa run web-unit -- src/cart/total.test.js -t "adds tax"` |
| link a test | `qa catalog link B-0070 --test "src/cart/total.test.js::total::adds tax"` |

Markers: `test.failing('…', …)` for a defect, `test.skip('…', …)` for quarantine and blocks.

## A unit test

```ts
import { describe, expect, test } from 'vitest';
import { total } from './total';

describe('total', () => {
  // B-0070
  test('adds tax to the sum of the lines', () => {
    expect(total([{ price: 100, quantity: 2 }], { taxRate: 0.2 })).toBe(240);
  });
});
```

## A component test

Use the testing library the service already has (Testing Library in most).

```tsx
// B-0081
test('shows the validation message for an empty quantity', async () => {
  render(<QuantityField onChange={() => {}} />);
  await userEvent.clear(screen.getByLabelText('Quantity'));
  await userEvent.tab();

  expect(screen.getByRole('alert')).toHaveTextContent('Enter a quantity');
});
```

- Find elements by role and label, as a user does; not by class or test-internal structure.
- Act through `userEvent`, not by calling the component's handlers.
- Replace the network at its edge (the service's own request mocking, if it has one). Never mock the module under test.
- Where time matters, use the runner's fake timers and restore them after the test.
- No snapshot of a whole rendered tree as the only assertion: it fails on every restyle and says nothing about the behaviour.
