# When a test fails

A failing test is information. The work is to find out what it says, never to make it stop saying it.

## First, look wide

When many tests fail at once, look for one cause before touching any test: a service that is down (`qa doctor`), sign-in that stopped working, one fixture, one helper, one setting that is missing. Where the cause is the person's to remove (a saved sign-in state that has expired), leave the tests and the catalog as they are: `e2e-tests.md` says what to do. `qa run` marks each failure `new` (it passed before), `known` (it failed before) or `first-run`.

## Then, test by test

Read the failure's `detail` file, and run the test alone as a trial: `qa run <suite> --no-record -- <selection>`. Answer these in order and stop at the first that applies.

1. **Did the test get as far as the application?** An import error, a missing fixture, a typo in the test's own code: correct the test.
2. **Was everything it needs there?** The service, the account, the setting, the data. If not, it is the environment: correct what is yours (a setting's name, waiting until the service is ready), otherwise record the behaviour as `blocked` with what is needed.
3. **Does it pass alone but fail with the others, or only sometimes?** It is unreliable: `flaky.md`.
4. **What did the application really answer?** Look at the actual status, body, page or trace next to `expect`.
5. **Does the evidence say what the entry claims?** Open the place named in `intent`. If the entry misread it, the entry is wrong: correct it with `qa catalog update`, then the test.
6. **Was the behaviour changed on purpose?** A commit message, a changed document or the developers' own changed test says so. Then update the behaviour and the test (`playbooks/sync.md` has the table).
7. **Is the running service the code you read?** `qa doctor` shows each checkout's branch, commit and uncommitted files, and the project notes say when a service runs from something built elsewhere. Where the service may be running other code than your evidence comes from, it is not a defect yet: record the behaviour as `blocked` with what would settle it ("the running build may be older than commit 3f2a91c"), and put the question in the report.
8. **Otherwise the application is wrong.** Record a defect.

When it stays unclear whether a change was meant, record a defect, say in the note that the intent is unclear, and add `--tag needs-decision`: the report lists these apart for the person. The test keeps saying what it saw.

After a repair, run the test recorded (`qa run <suite> -- <selection>`): only a recorded run changes its behaviour's status.

## What is never done to get a pass

- Loosening the assertion: a range or list instead of the value, "contains" instead of "equals", a deleted assertion.
- Changing the expected value to what the application returned, without evidence that this is intended.
- Wrapping the step in a retry or a `try`, or raising a timeout without a measured reason.
- Skipping, deleting or commenting out the test.
- Changing the application.

## Recording a defect

1. Once per suite in a session: `qa rules --task defect --suite <suite>`, and read what it lists. It names the team's own rules for defect reports, where it has any.
2. Leave the assertion as the evidence demands.
3. Mark the test as an expected failure that is strict, with the behaviour id in the reason; the stack file shows the syntax. It then counts as "known" in every run, and fails the run on the day the application is corrected.
4. Write `.qa-agent/reports/defects/<id>.md` from `templates/defect-report.md`: what was done, what was expected and why, what happened instead. Requests are shown without credentials.
5. `qa catalog status <id> defect --note "<expected> but <actual>" --test <path>::<name> --report .qa-agent/reports/defects/<id>.md`

A defect that shows only sometimes cannot be an expected failure, because it would pass now and then. Switch that test off with the behaviour id in the reason, record it as `defect` with the measured failure rate in the note, and add `--tag intermittent`.

Severity, for the report: **critical** when data is lost or exposed, or a line of `project.critical` is broken; **major** when a feature does not do its job; **minor** otherwise.

## When a defect is corrected

The expected failure now fails as "unexpectedly passed", and the run names the `defect` behaviour as its owner. Remove the marker, run the test, and link it again (`qa catalog link <id> --test <path>::<name>`): the behaviour returns to `written` and the next run records it as passing.

## Which status

| Situation | Status |
| --- | --- |
| the application does not do what the evidence says | `defect` |
| the test cannot run for a reason outside the tests (no account, feature switched off, forbidden to trigger, a read-only environment) | `blocked` |
| the test is unreliable and the cause is not found yet | `quarantined`, with a date |
| the application no longer has the behaviour, on purpose | `retired` |
