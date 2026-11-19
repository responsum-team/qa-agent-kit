# End-to-end tests

An end-to-end test follows a user through the interface, from a starting point to a result the user can see. These tests are the slowest and the most fragile, so there are few of them and each one matters.

## What gets one

- Every journey behind a line of `project.critical`.
- Each role's main tasks, start to finish.
- For each of those, the failure a user is most likely to meet: wrong input, no permission, nothing found.

The many input cases of a form belong at `api` level. An end-to-end test proves the journey holds together, not every rule along it.

## How

- **One journey per test**, starting from a known state: a fresh browser context, the page opened by its address.
- **Find elements the way a user does**: by role and name, by label, by visible text; by a test id where the application provides one. Never by a chain of CSS classes or by position: they change with every restyle.
- **Wait through assertions.** Use the runner's assertions that wait for an element's state. Never pause for a fixed time and do not wait for the network to be quiet: wait for what the user would see.
- **Prepare through the API, check through the interface.** Create the data a journey needs with API calls: faster and more stable than clicking it together. The journey under test itself is done by hand of the test, in the interface.
- **Sign in once per role** and reuse the saved state, kept at the paths in `auth.state_files`. Tests of signing in and out do not use it, and one test does the full sign-in through the form.
- **Assert what the user gets**: the text, the state of a control, the address, and that it is still there after a reload. Where the journey changes data, also check the effect through the API.

## When a program cannot sign in

Single sign-on with a second factor, a captcha, a code sent to a phone: the tests cannot type their way through these, and must not try to get round them. `auth.about` in the project map says when this is the case. Then the person signs in and the tests reuse the browser state they saved:

1. Give the person the command that opens a browser and saves its state when they close it, with the path from `auth.state_files`. The stack file has it.
2. The sign-in support loads that file instead of filling in a form. Before the first journey it opens a page that needs sign-in; when the application sends it to its sign-in page, it fails with one clear message: the saved sign-in state has expired, and the command that renews it.
3. Record one behaviour for it (`a saved sign-in state opens the application`), so the first test of every run says whether the rest can be trusted.
4. Journeys that test signing in itself are `blocked`, with "signing in needs a second factor" as the note.

An expired state is not a fault of the tests or of the application, and renewing it is the person's. Change nothing in the tests or in the catalog for it: go on with what does not need the browser state, and put the request, with the command, at the top of the report and of what you tell the person. The first recorded run after they renewed it puts the statuses right.

If a fresh state does not sign the browser in, the application keeps its session where the saved state does not reach (the browser's session storage). The runner's documentation on authentication shows how the sign-in support carries that over.

## Stability

- One browser, a fixed window size, a fixed locale and time zone.
- No reliance on the time of day or on data from earlier runs.
- Animations and toasts: assert the final state, not the transition.
- Keep handles fresh: look an element up when acting on it, not once at the start.

## Structure

- Steps that repeat (opening a page, filling a form) go into page objects or fixtures. Assertions about the behaviour stay in the test.
- Traces, screenshots and videos are kept for failures only, in `QA_AGENT_RUN_DIR`.
- Run headless. A test that passes only in a visible browser has a timing problem; see `flaky.md`.
