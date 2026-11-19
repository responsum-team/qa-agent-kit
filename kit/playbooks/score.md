# Score

Measures how much of an existing (reference) test suite the generated tests replicate. A reference test counts as replicated when a generated test checks the same behaviour, whatever its name, language or framework.

This playbook is for a judging session, never for the session that writes the tests.

## Rules of the judging session

- **A session of its own.** Start it in an empty scoring folder outside both repositories. The generated tests' repository lists the reference suite as off limits; that holds for its sessions, not for this one.
- **Read only.** Change nothing in the reference repository or in the generated one.
- **The outputs stay in the scoring folder.** They name the reference tests. Never copy them, or anything taken from them, into the generated tests' repository, and never show them to a session that writes tests: tests written from the answer sheet would make the next score meaningless.
- **Judge what the tests do.** Open both tests and read them. Names and the similarity figure are hints.

`qa` here is the kit's script, called by its path: `python3 <generated repo>/.qa-agent/kit/qa.py`.

## Step 1. Extract

```
qa score extract --reference <reference repo> --generated <generated repo> --out <scoring folder>
```

Naming the repository the kit works in is enough. Tests it wrote inside the services' repositories are found through its behaviour catalog, in each service's QA worktree or, once the QA branch is merged, its checkout; there only the test files the catalog links are counted, so tests the service's developers wrote are not taken for generated ones. The result lists every folder that was read under `generated.folders`. If one reports `linked_files_not_found`, its QA branch is neither open as a worktree nor merged: have that put right and extract again, or the score will be too low.

The command reads Python and JavaScript/TypeScript test files, lists files in other languages as unsupported, and prints a similarity estimate. Add `--include GLOB` when test files have unusual names.

## Step 2. Judge

Repeat until nothing is waiting:

1. `qa score next --out <scoring folder>` gives the next reference tests, each with the generated tests most like it.
2. For each reference test, open it at the place given and read what it sets up, what it does and what it asserts. Open the candidates. If none fits, search the generated tests for the endpoint, page or term (`rg`) before deciding nothing checks it.
3. Write the verdicts to a file in the scoring folder and record them: `qa score judge --out <scoring folder> --file <file>`.

```json
[
  {"ref": "R-0001", "verdict": "covered", "by": ["G-0042"], "why": "both post an empty cart and expect 422 with EMPTY_CART"},
  {"ref": "R-0002", "verdict": "partial", "by": ["G-0043"], "why": "same request; the reference also checks that no order is stored"},
  {"ref": "R-0003", "verdict": "missing", "why": "no generated test cancels an order that was already shipped"},
  {"ref": "R-0004", "verdict": "excluded", "why": "checks the reference suite's own retry helper, not the application"}
]
```

| Verdict | When |
| --- | --- |
| `covered` | The generated test(s) in `by` exercise the same operation or journey, under the same condition, and assert the same outcome. Other data values, names, structure, language or framework do not matter. Several generated tests may together cover one reference test; then they must together assert everything it asserts. |
| `partial` | Same operation and condition, but the generated test asserts less, or only some of what the reference test checks is checked anywhere. Also when the only matching generated test is switched off. |
| `missing` | No generated test checks it. |
| `excluded` | The reference test is not a check of the application: it is switched off, asserts nothing, or tests the reference suite's own helpers. Nothing else is excluded. |

When in doubt between two verdicts, give the lower one. A reference test over a table of cases is `covered` only when the generated tests check each kind of case in the table. A generated test marked as an expected failure for a recorded defect still counts: it runs and asserts the behaviour.

`qa score status --out <scoring folder>` shows the count so far.

## Step 3. Report

```
qa score report --out <scoring folder> --threshold 80
```

It writes `report.md` and `result.json` there and exits with 0 when the threshold is met, 1 when not. The report gives:

- **Replicated**: covered tests as a share of the counted (not excluded) reference tests. This is the figure the threshold applies to.
- The same with a partial match counted as half; the share covered by a generated test that passed in its last recorded run; and the similarity estimate, which involves no judgement.
- A table by area of the reference suite.
- **Check these by hand**: covered pairs picked at random. The person opens both tests of each pair; if more than one or two are not the same check, the judging was too generous and is repeated more strictly.
- **Named exactly like a reference test**: generated tests whose name is word for word a reference test's. Suites written apart rarely share names; more than one or two mean the generated tests were written with the reference suite in view, and the score then says nothing.
- Where the verdict and the similarity disagree, and the full lists of missing, partial and excluded tests.

## Step 4. Tell the person

The replicated figure and whether the threshold is met; the stricter and looser figures beside it; how many tests were excluded and why; how many reference files could not be read; how many generated tests carry a reference test's name; where the report is; and that the hand check is theirs to do.

If the person wants the generated tests improved after a score, they say which area is thin in their own words. The lists in the report are not handed to the session that writes tests.
