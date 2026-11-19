# Heal

Repairs failing and flaky tests without making them say less. Each one ends as a corrected test, an updated behaviour, a recorded defect, a block, or a dated quarantine. It runs without questions.

The baseline and the sync send their failures to steps 2 and 3 here. Coming from one of them, do those two steps and go back to the step that sent you, leaving out the `qa progress` lines of this playbook: `status` then keeps naming that step.

## Step 1. Take stock

1. `qa doctor`: a service that is down explains many failures at once; nothing below applies until it is back.
2. `qa branch --name heal`. `qa progress --phase heal`.
3. `qa run <suite>` for every suite, so the picture is current; an `on_request` suite only when one of its behaviours is failing or flaky. Then `qa catalog list --status failing flaky` and `qa flaky`.
4. `qa progress --phase heal --step failing --note "<how many failing, how many flaky>"`.

## Step 2. Repair each failing test

Once per suite in a session: `qa rules --task heal --suite <suite>`, and read what it lists: the methods for failures and flaky tests, the method for the level, the runner's stack file, and the team's rules for this work.

Take `new` failures first: something changed. Then `first-run`, then `known`.

Follow `methods/failures.md` for each one. It has the questions to answer in order, what is never done to get a pass, and how a defect is recorded. Run the test again, recorded, after every repair: `qa run <suite> -- <selection>`.

After every tenth test, and at the end: commit by the manual's sequence, with `qa progress --phase heal --step failing --note "<done; left>"`. When none is left: `qa progress --phase heal --step flaky`.

## Step 3. Cure each flaky test

Follow `methods/flaky.md` for each one: measure, find the cause, remove it, prove it. Where the cause cannot be found now, it gives the quarantine: switched off, with a date.

When none is left: `qa progress --phase heal --step close`; `qa guard`; commit.

## Step 4. Close

1. Run every suite in full. `qa catalog validate` and `qa guard --all` pass.
2. Notes for the report, in a file under `.qa-agent/runs/`: what was corrected and why it had failed; defects recorded; quarantines with their dates; what is blocked.
3. `qa report --name heal --notes <file>`; `qa progress --phase idle --note "healed; report <path>"`; `qa guard`; commit.
4. Tell the person what was repaired, what turned out to be a defect, and what waits for them.
