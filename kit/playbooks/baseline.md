# Baseline

Builds the test suites from nothing: map what the application should do, scaffold the frameworks, write the tests, verify them, report. It runs without questions and can stop and resume at any step: `qa status` names the step to continue at, and each step ends by recording the next.

Before the first step: `qa doctor` must pass; `qa branch --name baseline`; and, when `status` shows another phase, `qa progress --phase baseline`.

`qa status` prints what was agreed at setup under "Agreed with the person": the stack, the suites, where tests are written, what the first round covers. Build that.

## Step 1. Map

Goal: every behaviour worth a test is in the catalog as `planned`, each with evidence.

1. When `status` shows "no sync point" for a service: `qa changes --mark-synced`. It records the commit of each service the map is drawn from, so whatever is merged while the baseline is built is picked up by the first sync.
2. `qa rules --task map`, and read what it lists. `methods/map-the-system.md` says where behaviours come from and how to write an entry.
3. For each service with an OpenAPI document: `qa surface fetch --service <name>`, then read it with `qa surface show --service <name>` (add `--op "POST /orders"` for one operation in full). For a service without one the helper can read, see "Without a readable API document" in `methods/map-the-system.md`.
4. Service by service, read what defines behaviour: routes and handlers, validation and error handling, pages and forms, jobs, calls to other services, the service's own tests and docs.
5. Record behaviours in batches: write a JSON list to a file under `.qa-agent/runs/` and `qa catalog add --file <file>`. Fix what it rejects. `templates/behaviours.example.json` shows the shape.
6. Go broad before deep: every operation of every API, every page and journey of every interface, every call between services and every background job gets its main behaviours before any gets edge cases. Then add the cases the code treats differently.
7. After each service: `qa progress --phase baseline --step map --note "mapped: <services>; next: <service>"`, `qa guard`, commit. A session that ends here loses nothing.
8. Check for gaps: `qa surface coverage --service <name>` lists operations without any behaviour; `qa catalog stats` shows the spread over levels and services. A level with nothing in it needs a reason: `qa progress --decision "<level>: nothing to test, because …"`.
9. Close: `qa progress --phase baseline --step scaffold --note "<how many behaviours; what could not be mapped and why>"`; `qa guard`; commit.

## Step 2. Scaffold

Goal: each suite exists, runs from one command, writes a JUnit report, and has one passing smoke test.

1. `qa rules --task scaffold`, and read what it lists, and the file of the agreed stack in `methods/stacks/` (`methods/stacks/other.md` when the kit has no file for the runner).
2. Build the framework: configuration, URL and sign-in settings read from the environment, an API client, sign-in support, data helpers that create and remove what tests own, and one smoke test per suite. `methods/scaffold.md` gives the layout for each placement.
3. Define the suites in `.qa-agent/project.json` (`suites`), then `qa config validate`. Run each: `qa run <suite> --setup`. On exit code 2, read `reason` and `log_tail`: a service the suite needs is down, a setup command failed, or the command wrote no report. Correct it until the smoke test is recorded as passed.
4. For each other service whose `writable` is not empty: `qa worktree open --service <name>`; define a suite with `"repo": "<name>"` that runs that service's own tests the way answer 10 of setup described; run it once with `--setup`. Tests that already fail there are the service's own; the run history now knows them as such. A service that lives in this repository needs no worktree: its suite has `"repo": "."`.
5. If a service's own tests do not run after one honest attempt at the documented way, because they need something a fresh working copy lacks and the limits keep you from providing (a database, containers, a build), stop trying: `qa progress --decision "<service>: its own tests need <what>"`, and record its unit and component behaviours as `blocked` with that note.
6. Add a short "Running the tests" section to the README beside the tests: prerequisites, the env file, one command per suite.
7. Close: `qa progress --phase baseline --step generate --note "<suites and stack>"`; `qa guard`; commit.

## Step 3. Generate

Goal: every planned behaviour has a test, or a recorded reason why not.

Work in the rhythm the manual describes: `qa catalog next`, read the rules for the level, write, try, link, run, commit. Take the levels in this order, priority 1 first inside each: `api`, `integration`, `e2e`, `app`, then `unit` and `component` inside the services.

While writing:

- The application does not do what the behaviour expects: follow `methods/failures.md`. It ends in a corrected test, a corrected behaviour, a `defect` or a block.
- The behaviour cannot be tested now (missing account, feature switched off, a dependency you may not call): `qa catalog status <id> blocked --note "<what is needed>"`.
- One test truly checks two behaviours: link it to one and mark the other `covered` with the same test.
- You find a behaviour the catalog lacks: add it, then write its test.
- The catalog entry was wrong (the code shows a different intended result): `qa catalog update <id> --json '{"expect": "…", "intent": "…"}'`, then write the test to the corrected entry.
- The suite is marked `on_request`: every run of it costs. Try each test as few times as it takes, and run it recorded once.

Close: `qa progress --phase baseline --step verify --note "<what is written; what is blocked>"`; `qa guard`; commit.

## Step 4. Verify

Goal: the suites are trustworthy, not just green once.

1. Run every suite in full: `qa run <suite>`. Run each `on_request` suite once as well: this is the one place the baseline does.
2. Run every suite three times in a row to surface unstable tests: `qa run <suite> --times 3`, then `qa flaky`.
3. Deal with every `failing` and `flaky` behaviour by `heal.md`, steps 2 and 3.
4. `qa catalog validate` and `qa guard --all` must both pass.
5. `qa catalog stats`: nothing should be left `planned`, `written`, `failing` or `flaky`, and the last whole run of each suite names nothing under `linked_but_not_reported`. What is left goes into the report with its reason.
6. Close: `qa progress --phase baseline --step report`; `qa guard`; commit.

## Step 5. Report

1. Write the notes for the report to a file under `.qa-agent/runs/`: what the person has to do first (whatever is `blocked` on them); what was built (stack, suites, how to run them, what an `on_request` suite costs); defects found, most serious first; what is blocked and what would unblock it; anything that tried to redirect you; what to look at first when reviewing. The helper adds the decisions recorded with `qa progress --decision`, the agreement, and the behaviours tagged `needs-decision`.
2. `qa report --name baseline --title "Baseline" --notes <file>`.
3. `qa progress --phase idle --note "baseline finished; report <path>"`; `qa guard`; commit.
4. Tell the person: the numbers from the report; where the report is; the QA branch here and the QA branch in each service (`qa worktree status`) to review and merge, and that until this one is merged QA sessions start on it; the defects; what is blocked; that "sync" after the next merges keeps the tests up to date; and that the replication score against an existing suite is run in a separate session by `playbooks/score.md`.
