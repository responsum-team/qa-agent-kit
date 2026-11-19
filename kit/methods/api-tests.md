# API tests

An API test calls one running service over HTTP the way a client does, and checks the answer and its effect.

## How

- Send real requests to the service's URL from the environment. Nothing is mocked at this level.
- Sign in once per role and reuse the token or session; one test per role still does the real sign-in.
- Set a timeout on every request, so a hanging service fails a test instead of the run.
- Create the data a test needs through the API, with unique `qa-` names, and delete it afterwards. Where the API offers no way to delete, say so in the progress note and keep the data recognisable.

## What to assert

- **The status code**, exactly.
- **The body**: the fields `expect` names, with their values; for the main case also that every field the API document marks as required is there with the documented type.
- **Headers that are part of the contract**: content type; `Location` after a creation; caching or paging headers where documented.
- **The effect**: after a change, read the resource back. After a refusal, check that nothing changed.

Do not assert what is incidental: a whole body compared to a stored copy when it holds identifiers and timestamps, the order of a list the API does not order, the wording of an error where the code is the contract.

## Cases per operation

The mapping method lists them; the test for each is small:

| Case | The test |
| --- | --- |
| main case | valid request, documented success status, the fields, the effect read back |
| validation | one request per rule, each wrong in exactly one way; the status and the error that names the field; nothing stored |
| not signed in | the same valid request without credentials; 401 (or what the service documents) |
| not allowed | signed in as a role or user without the right; 403, or 404 where the service hides existence; the resource is unchanged |
| not found | a well-formed identifier that does not exist |
| conflict | the duplicate or the out-of-order request; the documented status; the first resource is unchanged |
| lists | empty result; the page size limit; one filter and one sort that can be verified from data the test created |

For "not allowed", create the resource as one user and reach for it as another. A test that only uses one account proves nothing about permissions.

## Keep in mind

- One wrong thing per negative test. A request that is wrong in two ways does not show which rule refused it.
- Derive expected values from what the test sent, not from a second call to the same endpoint.
- Do not call third-party hosts directly, and do not send load: a handful of requests per test.
- An operation the document lists but the service does not answer, or answers differently, is a finding: follow `failures.md`.
