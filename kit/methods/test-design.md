# Test design

Rules for every test at every level. Team rules listed by `qa rules` add the team's own conventions on top; they do not replace these.

## One test, one behaviour

- A test checks one catalog entry. Put its id in a comment on the line directly above the test (`# B-0012`, `// B-0012`), so anyone can go from a test to its reason and back.
- Name the test after the behaviour, not after the code: `test_empty_cart_is_rejected`, `'rejects an empty cart'`.
- Several assertions are fine when together they describe the one outcome in `expect`.
- The same behaviour over equivalent inputs is one parametrised test. Different outcomes are different tests.

## Assert what `expect` says, all of it

- Assert on what the application returned or did: the status, the fields that matter, the visible text, and the effect ("no order is stored" needs a second request that looks).
- Be exact where the behaviour is exact. One status code, never a list of acceptable ones. For values that differ every time (identifiers, timestamps), assert their form and their relation to what was sent.
- A test for a refusal asserts the specific refusal (status and error code or message) and that nothing changed.
- Never compare a value with itself, with what the test just computed the same way, or with a mock's canned answer.
- No assertion inside an `if`, a `try` that swallows the error, or a loop that may run zero times. Assert the count first.
- Give an assertion a message when the plain failure output would not say what was expected.

## Stand alone

- Each test creates what it needs and removes it afterwards, also when it fails: use the runner's fixtures or hooks for cleanup.
- Data is unique per test and recognisably the tests' own: a `qa-` prefix and a random suffix. Never use, change or delete data the test did not create.
- No test relies on another having run, on order, or on what an earlier run left behind.
- Nothing is shared and mutable between tests except through fixtures that reset it.

## Wait for conditions, not for time

- Never pause for a fixed time. Wait for the condition the test is about, with a deadline, through the runner's own waiting assertions or a polling helper.
- A deadline that had to be raised gets a comment with the time that was measured.

## Keep the environment out of the test

- URLs, accounts and keys come from the environment variables the project map names. Nothing about one machine or one person is written into a test.
- At `api` level and above, use only what a client or a user can reach. No imports of the application's internals, no direct database access unless the project map provides for it.
- Control time and randomness where the behaviour depends on them; do not let a test depend on today's date, the time zone or the order of an unordered list.

## Support code

- Repeated steps go into helpers, fixtures or page objects. The assertions of a behaviour stay in the test, where a reviewer sees them.
- Helpers fail loudly: a helper that cannot do its step raises, it does not return `None`.

## Switched-off tests

A test is switched off or marked as an expected failure only with its behaviour id in the reason, and only while the catalog says `defect`, `quarantined` or `blocked` for that id. `failures.md` and `flaky.md` say when; the stack file shows the syntax.

## What the guard checks, and how to answer it

`qa guard` reads every changed test file. Where it is wrong about a line, say why on that line or the line above it; the reason is part of the code and gets reviewed.

| The guard reports | Do | Only where it is truly needed |
| --- | --- | --- |
| a focused test (`.only`) | remove the focus | |
| a fixed wait | wait for the condition | `qa-agent: allow-sleep <why>`, inside a polling helper that has a deadline |
| a test switched off without a behaviour id | add the id and record the status in the catalog | `qa-agent: allow-skip <why>`, for a skip that depends on the platform |
| a test that asserts nothing | assert the expected result | `qa-agent: allow-no-assert <which helper asserts>` |
| something that looks like a secret | read it from the environment | `qa-agent: allow-secret <why>`, for a made-up sample value |

## Before linking a test

1. Does it assert every part of `expect`?
2. Would it fail if the application did the opposite? If unsure, change the expected value once, watch it fail, and change it back.
3. Does it pass when run alone, and twice in a row?
4. Does it leave nothing behind?
5. Is there anything in it that belongs to this machine only?
