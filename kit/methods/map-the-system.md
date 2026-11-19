# Mapping the system

Before any test is written, the application is turned into a list of behaviours, each with the evidence that it is intended. Tests written without this list check whatever the code happens to do, including its defects.

## Where behaviours come from

Read in this order; earlier sources are stronger evidence of what was meant.

1. **The API document** (OpenAPI): every operation, its documented responses, required fields, limits, and whether it needs sign-in. `qa surface show --service S` lists the operations; `--op "POST /v1/orders"` gives one in full: each field with its type, `*` when required and its limits in braces (`string* {len 1..60}`), and what the document says about each answer. Every limit and every documented answer is a behaviour.
2. **The code that decides**: routes and handlers, validators, permission checks, error handlers, state changes, scheduled jobs, message consumers, calls to other services. Every branch that answers differently for a different input is a case.
3. **The interface**: pages, forms, navigation, what each role sees and may do.
4. **The service's own tests**: they show what its developers meant, and what is already checked at unit level.
5. **Documents**: READMEs, design notes, changelogs, commit messages.
6. **The running application**: use it to confirm what you read, with requests that need no sign-in and change nothing. What needs a signed-in user is confirmed by the test, which has the credentials; you do not. On its own the application only shows what happens today.

When two sources disagree, record what the stronger one says and name the disagreement in `intent`. The test will show which is true, and `failures.md` says what to do then.

## Without a readable API document

`qa surface` reads a JSON document from the service's URL or from a file, without signing in. Where the project map has `"openapi": null` for a service, one of these holds:

- **There is no document.** The routes in the code are the list. Write each `surface` as method and path all the same. Check for gaps by hand: list the route declarations in the code and compare them with `qa catalog list --service <name> --level api`.
- **The document is YAML.** Make a JSON copy under `.qa-agent/runs/` with a tool installed in this repository (PyYAML in the tests' environment, `npx js-yaml <file>`), then `qa surface fetch --service <name> --source .qa-agent/runs/<name>-openapi.json`. `show` and `coverage` then work as for any other service.
- **Reading it needs sign-in.** If the service's repository holds a JSON copy of the document, name that file as `openapi` in the map and nothing here applies. Otherwise map from the code, and when the API suite exists let its support code fetch the document as a signed-in test user into `.qa-agent/runs/`; then fetch it with `--source` as above and close the gaps `qa surface coverage` shows.

`--source` takes a URL, or a file path from the top of the service's repository or of this one. At a sync, `qa changes` reports contract changes only for services whose `openapi` is set. For the others make the copy again, read `qa surface diff --service <name> --source <copy>`, and store it at the close of the sync with `qa surface fetch --service <name> --source <copy>`.

## What to look for

Apply the list for its kind to every surface. A case the code does not distinguish is not a case.

**An API operation**
- the main case: valid input, the documented success answer, and its effect (the record can be read back)
- each documented error answer
- validation, one case per rule: a required field missing, a wrong type, a value below or above its limit, a malformed or unknown value
- no credentials; credentials of a role that is not allowed; another user's or tenant's resource
- a target that does not exist; a duplicate or conflicting request; the same request sent twice, where the code handles it
- for lists: empty, paging limits, filters, sorting

**A page or journey**
- the main path to its visible outcome, and that the outcome is still there after a reload
- what the user sees for invalid input, for an empty state, and when the server refuses or fails
- what a role that is not allowed sees; opening the page by its address without signing in

**Between services**
- an action in one leads to the expected state in the other
- identity and tenant carry across
- what the calling side does when the other side refuses, where the code handles it

**A background job or pipeline**
- its trigger leads to its effect; running it again does not double the effect
- what happens to an input it cannot process

**What is particular to this application**: see `app-specific-tests.md`.

## Writing an entry

One entry is one case with one expected outcome. `qa catalog add --file <file>` takes a JSON list; `templates/behaviours.example.json` shows the shape.

- `expect` is something a test can assert: a status, a field's value, a visible text, a stored record. "Works", "correctly", "properly" and "as expected" say nothing: write what is seen.
- `intent` names the evidence and where it is, so a reviewer can open it: "OpenAPI: 422 for POST /orders; app/routes.py raises EmptyCart".
- Where the only evidence is what the application does today, start `intent` with `observed:` and add the tag `characterization`. Such a test pins today's behaviour; it is weaker, and the report counts these under "Tagged".
- `sources` lists the one to three files that implement the behaviour, as paths from the top of the service's repository (`services/orders/app/routes.py` where several services share one). `qa changes` uses them to find the behaviours a merge touched, so an entry without sources is invisible to the sync.
- `area` is a short, stable feature name used the same way throughout: `orders`, `sign-in`, `billing`.
- `surface` for an API operation is written as method and path, `POST /v1/orders`, so `qa surface coverage` can count it.

## Choosing the level

Take the lowest level at which the behaviour can be observed truthfully.

- Logic with no input or output of its own (a calculation, a parser, a permission rule): `unit`, inside the service.
- One service's answer to a request: `api`.
- Something that only exists when two services, or a service and its real store or queue, work together: `integration`.
- What a user does and sees: `e2e`. Keep these to the journeys that matter; the many input cases of a form belong at `api` level.

Do not repeat one case at every level. A journey's main path and its most important failure are `e2e`; the edge cases behind it sit lower.

## Priority

- **1**: every line of `project.critical`; signing in and permissions; anything that moves money or can lose or leak data.
- **2**: everything else that users rely on.
- **3**: rare edge cases.

## What is not a behaviour

- What a framework or a third party does by itself, unless the application customises it.
- Implementation details at `api` level and above: names of internal functions, table layouts, log lines.
- Anything without an outcome that can be observed.

## Order of work

Breadth first: the main case and the access cases of every surface, then validation and edge cases. The map is complete when each list above has been applied to every surface, not at a round number. Record what could not be mapped, and why, in the progress note.
