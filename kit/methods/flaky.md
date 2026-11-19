# Flaky tests

A flaky test passes and fails without anything changing. It is worse than a failing one: it teaches people to ignore red. A flaky test is cured by removing its cause, and until then it is switched off with a date, never left to flicker.

## Measure first

- `qa flaky` lists tests whose recorded outcomes keep changing.
- `qa run <suite> --times 5 -- <selection>` runs one test five times and reports it as flaky when the outcomes differ.
- Run it alone and with its neighbours (its whole file, then the suite). Where it fails says a lot: only with others means shared state; also alone means timing or the environment.

## Causes and cures

| What is seen | Cause | Cure |
| --- | --- | --- |
| "not found", "not visible", an old value, right after an action | the test looked before the application had finished | wait for that exact condition with a deadline: the runner's waiting assertion, or the polling helper |
| passes alone, fails in the suite, or the other way round | tests share data, an account or a setting; or one relies on another having run | data of its own per test, created and removed by the test; no reliance on order |
| fails on a rerun against the same environment | leftovers of the earlier run | unique names; cleanup that also runs after a failure |
| fails with parallel workers only | workers collide on one resource | data per worker; mark the few tests that cannot share to run serially |
| fails at certain hours or dates | the test depends on the clock, the time zone, the end of a month | fixed inputs, or assert with the tolerance the behaviour really has |
| fails for some generated inputs | random data hits a case the test did not mean | fix the seed or narrow the data, and add the case that was hit as a behaviour of its own |
| first test of a run fails, the rest pass | the service or a cache was not warm | a readiness check in the suite's setup or a session fixture |
| click lands on the wrong element or none; "element detached" | the page re-rendered between finding and acting; an overlay or animation | look the element up when acting on it; assert the final state; wait for the overlay to go |
| a third party answered slowly or not at all | the test depends on something outside the application | fake it only where the project allows; otherwise `blocked` |
| the same request gives different answers | the application itself is unreliable | that is a defect: `failures.md`, the intermittent case |

Not cures: a longer fixed pause, a retry around the assertion, a retry setting in the runner, a larger timeout without a measurement. A deadline may be raised when measuring shows the operation legitimately takes that long; write the measured time in a comment.

## Prove it

After the change: the same five runs all pass, then the whole suite passes. Say in the commit message what the cause was.

## Quarantine, when the cause is not found

1. Switch the test off, with the behaviour id and the date in the reason; the stack file shows the syntax.
2. `qa catalog status <id> quarantined --note "<what was seen, what was tried>" --test <path>::<name> --until <a date within two weeks>`
3. Put it in the report. `qa status` raises it again when the date has passed; then either cure it or renew the date with what was learned since.

A quarantined test checks nothing, so its behaviour counts as unchecked until it is back.
