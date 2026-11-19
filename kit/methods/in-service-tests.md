# Tests inside a service's repository

Unit and component tests live with the code they check. They are for behaviour that cannot be seen from outside, or that is cheaper and more exact to check close to the code: calculations, parsing, mapping, state changes, permission rules, how an interface component renders and reacts.

## Where they are written

- **A service in another repository**: never in its checkout. `qa worktree open --service <name>` creates a second working copy at `.qa-agent/worktrees/<name>` on a QA branch; write, run and commit there. The checkout the application runs from stays as it is.
- **A service that lives in this repository** (`placement: service`): in place, on this repository's QA branch. No worktree is opened for it, and its suite has `"repo": "."`.
- **Services that share one repository** share one worktree: that of the service that lists `writable`. Each behaviour's `repo`, shown by `qa catalog next`, names it.
- Everywhere, only in the places the `writable` globs allow. An empty `writable` means nothing is written inside that service: record its unit-level behaviours as `blocked` with that reason.

A worktree holds every file of the repository. Where it holds a copy of an off-limits folder, `status` lists that copy under "Never read" too.

## Follow the house style

Read two or three of the service's existing tests first. Use its runner, its folder layout, its naming, its fixtures and factories. A new convention next to an old one is a cost to its developers.

Add new files rather than editing files the developers wrote: your tests stay reviewable as a unit, and theirs stay theirs.

## Never

- Change application code, configuration, dependency lists, lock files, CI files or `.gitignore`. A behaviour that cannot be tested without a change to the code becomes `blocked`, with a note saying which seam is missing.
- Add a dependency to the service. What the test tooling itself needs (a report writer, for example) is installed in this repository and named by path; `QA_AGENT_ROOT` holds this repository's path during a run.
- Copy the code under test into the test.

## What is already tested

Before writing, look for an existing test of the behaviour. If the developers' own test already asserts it, do not write a second one: `qa catalog status <id> covered --note "the service's own test checks this" --test <path>::<name>`.

## Writing them

- **Unit**: call the function or class directly. Replace only what is outside it and slow or unpredictable: the network, the clock, randomness, the file system. Never replace the thing under test.
- **Component**: one module with its neighbours faked. For an interface component: render it, find elements by role and label, act as a user would, assert what is shown. For a request handler: call it through the framework's test client with a fake store behind it.
- Assert results, not calls. Assert that a collaborator was called only when the call is the behaviour ("an email is queued").
- Cover what `expect` says for the boundaries: zero, one, many; the smallest and largest allowed value and the first one outside; empty and missing.

## Running and committing

Define a suite with `"repo": "<service>"` that runs the service's own runner and writes a JUnit report; the stack file has the commands. Its `setup` installs the service's dependencies inside the worktree. Run it once before writing anything: tests that already fail there are the service's own, and the run history then knows them.

A fresh working copy lacks what git does not carry: local configuration, generated code, built assets, a database. Try the way the service documents for running its tests, once. If that needs something the limits keep you from providing, do not improvise: record the decision (`qa progress --decision "<service>: its own tests need <what>"`) and the service's unit and component behaviours as `blocked` with that note.

Whatever the tools leave behind must be covered by the service's own ignore rules, or be kept out of the worktree (a cache or environment folder under `QA_AGENT_RUN_DIR`, the runner's option to write no cache): the guard counts every other new file there as written outside the allowed places.

Commit inside the worktree: `git -C .qa-agent/worktrees/<service> add <your files>` and `commit`. `qa guard` checks the worktrees along with this repository. Never push; the report names the branch for the person to review.

After merges, `qa worktree update --service <name>` brings the merged code into the QA branch. If it reports a conflict, nothing was changed: the developers edited the same test files. Say so in the report; the person resolves it.
