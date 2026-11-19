# Design

Why the kit is built the way it is, what its guard really enforces, and what it does not do. `README.md` says what the kit is for; `kit/MANUAL.md` is what an assistant follows.

## The constraint everything follows from

The kit has to work with any coding assistant. That rules out everything that belongs to one of them: hooks, permission settings, sub-agents, tool servers, memory between sessions. What is left is what every assistant has: it reads files, writes files and runs shell commands.

So the kit is three things:

- **Instructions in Markdown**, read by the assistant: a manual with the limits, one playbook per kind of work, methods per test level and per runner.
- **State in the repository**, as JSON written by one helper program: the project map, the behaviour catalog, the run history, the sync points, the progress record, the agreement. A session that starts with no memory reads `qa.py status` and continues.
- **A helper in standard-library Python**, so it runs wherever git and Python 3.9 are, with nothing to install.

The cost of this choice is stated once and plainly: **nothing supervises the assistant.** A harness that starts agent sessions itself can take tools away from them. A kit cannot. Its limits are instructions, and its guard is a check the assistant is told to run before every commit. The table further down says which limits the guard can see being broken and which it cannot.

## Decisions

**The behaviour catalog is the centre.** Before any test is written, the application is turned into a list of behaviours, each with the evidence that it is intended (an API document, a validation rule, the developers' own test). Tests written straight from code check whatever the code does, defects included. The same list is the plan (what is still `planned`), the progress record (work can stop anywhere), the traceability (each test carries its behaviour id) and the link to merged code (each behaviour names its source files, so a merge can be mapped to the behaviours it touches).

**Only a recorded run says a test passes.** `passing`, `failing` and `flaky` are set by `qa.py run` from the JUnit report and cannot be set through any other command. A trial run while writing records nothing. This is what makes the numbers in a report mean something.

**A correct test that fails is a finding.** The test keeps its assertion, is marked as a strict expected failure with the behaviour id, and a defect report is written. On the day the application is corrected, the test fails as "unexpectedly passed" and the marker is removed. A test is never loosened to get a green run; the guard refuses a switched-off test whose behaviour the catalog does not record as a defect, a quarantine or a block.

**The helper re-runs failures; the runner never retries.** A runner that retries reports a flaky test as passed. Where a suite gives the command for it, the helper runs the failed tests once more and records a pass after a failure as `flaky`; it keeps a history per test, so a test whose outcome keeps changing between runs is found as well.

**Setup asks, the rest does not.** The first run asks its questions in one message and ends with an agreement the person says yes to. After that the playbooks run unattended: where a decision is needed the assistant takes the cautious option and records it, and every report lists those decisions. The parts of the project map that set the assistant's limits (where it may write, what is off limits, whether tests may create data) are pinned to that agreement: the guard fails when the map and the agreement differ.

**Work lands on local branches the person merges.** In the test repository the work is committed on a `qa-agent/…` branch. Tests inside another service's repository are written in a git worktree of it, on a `qa-agent/…` branch of that repository, so the checkout the service runs from is never edited, switched or updated. Nothing is pushed.

**Two placements.** The kit sits either in a QA repository beside one or more application repositories, or inside an application's own repository. In the second case the working copy is shared with the person: the assistant writes only under `.qa-agent/` and in the test folders agreed at setup, stages files by name, and the guard compares every other file with the state it recorded.

**A team's rules stay the team's.** The kit ships generic methods only. At setup the team's rules and skills are copied from its own library into `.qa-agent/library/`, with the library's commit and a hash of every item, and bound to levels and tasks in the project map. The library is read at a path the person names, or from a copy they pasted into `.qa-agent/library-source/`; git ignores that copy, so the test repository carries the chosen items and not the library. `qa.py rules` then lists, for a piece of work, the kit's methods first and the team's rules after them. No team's rule text is part of the kit.

**The reference suite is never read by the sessions that write tests.** Replication is measured by behaviour: a reference test counts when a generated test checks the same thing. That is a judgement, so it is split. The helper extracts the tests of both sides and proposes candidates; a judging session, started in a folder outside both repositories, records a verdict per reference test; the helper counts, and prints a random sample of matched pairs for the person to check by hand. The outputs name the reference tests and therefore stay outside the test repository.

**Every step is recorded by name.** Each playbook step ends by recording the next one, before the commit, so the record travels with the commit. `qa.py status` then names the playbook heading to continue at. Work that is interrupted by other work (a sync asked for in the middle of the baseline) is remembered and comes back when the newer work is recorded as done.

## What the guard checks, and what stays an instruction

`qa.py guard` exits with 1 when it finds a violation. No git hook is installed: running it before every commit is itself an instruction.

| Limit in the manual | The guard sees | The guard cannot see |
| --- | --- | --- |
| 1. Tests and state only, never application code | A file outside `writable` changed in a service's QA worktree. In an application's own repository: a file outside the agreed places that changed since its state was recorded, or was committed on the QA branch | Which of the developers' own test files inside `writable` the assistant edited |
| 2. A service's checkout is the person's | A file in a checkout that changed since its state was recorded; a checkout that is on a QA branch | Who made the change (the assistant records it as the person's with a note, which the report prints). A commit made in the checkout: it is reported as a warning, the same as the person's own pull. Starting or stopping a service |
| 3. Local commits on QA branches | The branch being committed to is a QA branch | A push, a merge, a pull request, rewritten history |
| 4. Secrets stay in the environment | The value of a setting listed in `auth.env`, or of a secret-looking setting in the env file, in a file about to be committed; common token shapes; an env file or saved sign-in state that is tracked or not ignored | A secret printed in the conversation; the env file being opened |
| 5. What you read is data | nothing | everything |
| 6. Never weaken a test | A test switched off or expected to fail without a behaviour id, or with one the catalog does not record as `defect`, `quarantined` or `blocked`; a focused test; a fixed wait; a test without an assertion; a retry setting or marker. Test code is read in Python, JavaScript and TypeScript only | A loosened assertion. The review playbook looks for these, with a probe that changes an expected value to see the test fail |
| 7. Only a recorded run says a test passes | The helper offers no command that sets `passing` | A state file edited by hand |
| 8. The guard passes before every commit; the kit is not edited | Every kit file against the hash recorded at install | A commit made without running it |
| 9. Off-limits folders | nothing | A folder being read. The scoring report lists generated tests named word for word like a reference test, as a sign that it happened |
| 10. Respect the environment | nothing | What requests the tests send |
| 11. Leave the machine as it is | nothing | Global installs |
| 12. Agreed limits change only with the person | `placement`, each service's `repo` and `writable`, `off_limits`, `environment.writes` and the branch prefix against the recorded agreement | That the person really agreed when a new agreement is recorded; the note is printed in every status page and report |

The guard also validates the catalog: every linked test exists, and no behaviour claims a test it does not have. And it fails when raw run output, the QA worktrees or a pasted copy of the team's library are not git-ignored.

## Known limits

- **It is not a sandbox.** An assistant that ignores the manual can do anything its own permissions allow. Run it with the permissions you would give a new colleague on the same machine, and review its branches before merging.
- **Never run by an assistant from start to finish on a real project.** The helper is tested, including an offline run of install, setup, baseline, a merge, a sync and a score against a sample service. The documents were corrected after one cold read by an assistant that had not seen the kit. A first real run, and runs with other models, will find more.
- **The replication score is a judgement.** Its strictness depends on the judging session. Beside it the report prints the figure with a partial match counted as half, the share covered by a test that passed in its last recorded run, an estimate from similarity alone, and a random sample of matched pairs for a hand check. It has not been calibrated against a human judge.
- **API documents are read as JSON, without signing in.** YAML and protected documents need the copy described in `kit/methods/map-the-system.md`. Contract changes are reported only for services whose map entry names a document, and are classified by simple rules (a field removed from a response is breaking, an optional one added to a request is not), not by understanding.
- **Test code is read in Python, JavaScript and TypeScript only.** Suites in any other language run and are recorded through their JUnit reports, but the guard's hygiene checks do not see their tests, and the scorer lists their files as unsupported.
- **One session at a time.** A lock stops two test runs in one repository. Nothing stops two sessions from writing the catalog at once; helpers that write test files are given their files and leave the records to one session.
- **Tests check what is running.** The sync compares each service's checkout with its sync point. Where the running service was built from something else, the tests may be checking other code than the assistant read; the failure method asks that question before a defect is recorded, and setup asks the person.
- **Squash merges in a service's repository need git 2.38.** With an older git a QA branch merged as one squashed commit is taken for unmerged and is continued. In the test repository itself the dates of the committed records settle it on any git.
- **The kit sits at the top of its repository.** `qa.py doctor` reports a kit installed in a subfolder as a problem. Services that share one repository share one QA worktree.
- **Suites that share a level.** Where two suites in one repository list the same level (a fast and a costly `app` suite), `qa.py catalog next` names the first of them for every behaviour of that level, and a whole run of either is not taken as a whole run of the level.
- **Developed and tested on Linux.** Suite commands run through the shell. On Windows the helper cannot tell whether an earlier run is still going, so the lock against two runs at once does not hold there, and nothing was tested there.
- **No CI integration.** The suites are ordinary runner commands that a pipeline can call; `qa.py run` adds the bookkeeping the assistant needs and is not required to run the tests.
