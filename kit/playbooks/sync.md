# Sync

Brings the tests up to date with what was merged since the last sync: new tests for new behaviour, updated tests where behaviour changed on purpose, retired tests where it was removed, and a finding where it changed by accident. It runs without questions.

Tests check the running application, so the sync covers what each service's checkout is on now. The person updates and restarts a service; this playbook never does. In an application's own repository the merged code is there once the person has merged the earlier QA branch and updated their own branch; the sync starts from that branch.

## Step 1. See what changed

1. `qa doctor` must pass. `qa branch --name sync`. `qa progress --phase sync`.
2. `qa changes --fetch` (drop `--fetch` when there is no network). For each service it gives the merged commits, the changed files by kind, the behaviours whose `sources` were touched, the source files no behaviour lists, and a summary of contract changes. It also remembers the commit it compared up to; step 5 marks the sync there.
3. If it reports commits on the remote that are not in a checkout, the running service is behind the merged code. Sync to what is running, and say so in the report.
4. `qa progress --phase sync --step decide --note "<services with changes>"`. If nothing changed anywhere: `qa progress --phase sync --step close`, and go to step 5.

## Step 2. Decide, behaviour by behaviour

`qa rules --task sync`, and read what it lists.

For each service with changes, read the merged commit subjects first: they say what was meant. Then:

- **Each affected behaviour**: read the diff of the files named (`read_a_diff` gives the command; add the path).
- **Each source file no behaviour lists**: read its diff. It is either new behaviour, or behaviour whose entry never recorded this file.
- **Contract changes**: `qa surface diff --service <name>` gives every added, removed and changed operation with a level. For a service whose `openapi` is `null`, see "Without a readable API document" in `methods/map-the-system.md`.

| The diff shows | Do |
| --- | --- |
| The behaviour is unchanged (refactoring, renaming, comments) | Nothing. If the code moved, update `sources` with `qa catalog update`. |
| It changed, and the change was meant | `qa catalog update <id>` with the new `expect` and an `intent` that cites the commit; then change the test to match. |
| It changed, and nothing says it was meant | Leave the entry and the test. If the test now fails, that is a finding: step 3. |
| It was removed on purpose | Delete the test; `qa catalog status <id> retired --note "<commit>: <what was removed>"`. |
| New behaviour | `qa catalog add`, with the commit in `intent`. |
| An operation no longer requires sign-in (level `security`) | Never adapt silently. Unless the commit says it was meant, keep the test that expects sign-in and record a `defect`. |

A change was meant when at least one of these holds: the commit or merge message describes it; the API document or the docs changed with it; the service's developers changed their own tests for it. The code merely doing something different is not evidence.

After each service: `qa progress --phase sync --step decide --note "decided: <services>; next: <service>"`. When all are decided: `qa progress --phase sync --step repair`.

## Step 3. Run and repair

1. Bring the merged code into each service's worktree (`qa worktree status` lists them):
   - its work was merged (no commits ahead of the checkout, nothing uncommitted): `qa worktree close --service <name>`, then `qa worktree open --service <name>`. That starts a new QA branch from the merged code and leaves the old one for the person to delete;
   - it holds unmerged work: `qa worktree update --service <name>`;
   - it is not open: `qa worktree open --service <name>`.
2. Run every suite: `qa run <suite>`. Each failure comes back as `new`, `known` or `first-run`. Run an `on_request` suite only when the code behind its behaviours changed.
3. Work through the failures by `heal.md`, steps 2 and 3. A `new` failure in a behaviour whose sources changed is most likely the change itself: apply the table above.
4. Close: `qa progress --phase sync --step write`; `qa guard`; commit.

## Step 4. Write the new tests

For the behaviours added in step 2, work in the manual's rhythm: `qa catalog next`, rules for the level, write, try, link, run, commit. Then `qa progress --phase sync --step close`.

## Step 5. Close

1. Run every suite once more in full. `qa catalog validate` passes.
2. `qa changes --mark-synced` moves each sync point to the commit step 1 compared, and refreshes the stored API snapshots. If it answers `arrived_after_the_comparison`, more was merged while you worked: those commits wait for the next sync, and the report says so.
3. Notes for the report, in a file under `.qa-agent/runs/`: per service, what was merged; behaviours added, updated, retired; defects and unclear changes found; what is blocked.
4. `qa report --name sync --title "Sync <date>" --notes <file>`; `qa progress --phase idle --note "synced; report <path>"`; `qa guard`; commit.
5. Tell the person what changed in the tests, what was found, and which branches to review.
