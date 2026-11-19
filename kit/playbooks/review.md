# Review

Checks the tests themselves: does each one check what its behaviour says, would it fail if the application broke, and what is missing. It is worth most in a session other than the one that wrote the tests, and with a different assistant if one is at hand: the writer shares its own blind spots. It runs without questions.

Keep the findings in a file under `.qa-agent/runs/` as you go: it becomes the report's notes, and a session that continues the review reads it first.

## Step 1. Mechanical checks

1. `qa doctor`; `qa branch --name review`; `qa progress --phase review`.
2. `qa guard --all`: every test and state file, not only what changed.
3. `qa catalog validate`: every linked test exists; nothing is quarantined past its date.
4. `qa flaky`: tests whose outcome changed in recent runs.
5. `qa surface coverage --service <name>` for each service with an API document: operations no behaviour names, and operations with only one.
6. `qa catalog stats`: a level or a service with few behaviours next to what the application does there is a gap.

Note what these report; they go into the findings. Then `qa progress --phase review --step read`.

## Step 2. Read tests against their behaviours

`qa rules --task review`, and read what it lists; with `--suite <suite>` it adds the runner's stack file.

Take every priority 1 behaviour (`qa catalog list --priority 1`), and a sample of the others spread over levels, services and areas (at least one in five). For each: `qa catalog show <id>`, then open the test.

| Ask | A finding when |
| --- | --- |
| Does it check the whole `expect`? | Part of the expected result is never asserted: the status but not the body, the message but not that nothing was stored. |
| Would it fail if the behaviour broke? | The assertion holds whatever the application answers: a value compared with itself, "is not empty", a list of accepted statuses, an assertion inside a branch or loop that may never run, an error caught and ignored. |
| Does it check the application? | It asserts on its own test data, a mock or a stub instead of what the application returned or did. |
| Does it stand alone? | It needs another test to have run first, fixed identifiers, data it did not create, or a particular order. |
| Does it clean up? | It leaves data behind that changes what the next run sees. |
| Is the evidence real? | The place named in `intent` does not say what the entry claims. |
| Is `covered` true? | The linked test does not check this behaviour's `expect`. |
| Is the `defect` real? | The evidence does not support the expectation; the application is right and the entry is wrong. |
| Is `blocked` still blocked? | What it waited for is now there. |

**Is the assertion live?** For each priority 1 test and a few others, prove it: change the expected value in the test to something wrong, run that test alone as a trial (`qa run <suite> --no-record -- <selection>`), see it fail, and put the value back. A test that still passes checks nothing. A trial records nothing, so the probe leaves no failure behind; `git diff` on the file must be empty afterwards.

After every twenty behaviours: `qa progress --phase review --step read --note "<read up to which id; where the findings are>"`. When the reading is done: `qa progress --phase review --step gaps`.

## Step 3. Look for what is missing

- Each line of `project.critical` (`qa config show`) has priority 1 behaviours, at the level where a failure would show.
- Each operation that changes data has, beyond its main case: invalid input, a caller who is not signed in, a caller who is signed in but not allowed, and a target that does not exist.
- Each journey has what the user sees when something goes wrong, not only when all goes well.
- Each call between services has the case of the other side failing or answering late, where the code handles it.

Add what is missing with `qa catalog add`. Then `qa progress --phase review --step act`.

## Step 4. Act on the findings

- A test weaker than its behaviour: strengthen it, run it. If it now fails, handle it by `methods/failures.md`.
- A wrong entry: correct it with `qa catalog update`, then the test.
- A wrong `defect`: remove the marker, correct the entry, `qa catalog link` the test again, run it.
- A `covered` that is not true, or a `blocked` whose reason is gone: `qa catalog status <id> planned`, then write its test.
- Missing behaviours: write their tests in the manual's rhythm, or leave them `planned` when there are many, and say so.
- Everything else: describe it in the report.

Then `qa progress --phase review --step close`; `qa guard`; commit.

## Step 5. Close

1. Run the suites that changed, in full. `qa catalog validate` and `qa guard --all` pass.
2. Notes for the report: what was checked and how many; findings, most serious first, each with the behaviour id and what was done; what remains for the person.
3. `qa report --name review --notes <file>`; `qa progress --phase idle --note "reviewed; report <path>"`; `qa guard`; commit.
4. Tell the person what was found, what was corrected, and what is left to them.
