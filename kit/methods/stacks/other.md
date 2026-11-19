# Any other runner

For a suite whose runner has no file of its own here: Go, Java, .NET, Ruby, Cypress, Robot Framework and the rest. Give the suite `"stack": "other"`, or the runner's name: `qa rules --suite` lists this file for both.

The kit needs six things from a runner. Find how this one provides each, in its documentation and in the configuration the service already has, and prove each with the smoke test before writing more.

| The kit needs | How to prove it |
| --- | --- |
| A JUnit XML report, written to the path the kit puts in place of `{junit}` | `qa run <suite>` counts the smoke test under `totals`. Exit code 2 with "without writing a JUnit report" means nothing arrived at that path |
| Every test runs, whatever fails before it | no fail-fast option in the command; break the smoke test in a trial and see the tests after it reported |
| One file, and one test, can be run alone | `qa run <suite> --no-record -- <the runner's own selection>` reports only that |
| No retries | none in the command, none in the runner's configuration, no retry marker on a test |
| A test can be marked as expected to fail, strictly | the marked test fails the run on the day it passes; see below where the runner has no such marker |
| A test can be switched off with a reason | the report lists it as skipped |

Write what you found into the README beside the tests: the command for one test, and how the report names a test.

## The report

`{junit}` is replaced by the report's path, already quoted for the shell: write it outside quotes. Known ways, to be checked against the runner's version:

| Runner | Report |
| --- | --- |
| Go | `go test` writes none; `gotestsum --junitfile {junit} -- ./...` does |
| Maven, Gradle | one XML file per class, under `target/surefire-reports/` or `build/test-results/test/`: join them |
| .NET | `dotnet test --logger junit\;LogFilePath={junit}`, with the JunitXml.TestLogger package in the test project |
| Cypress | `--reporter junit --reporter-options mochaFile=<folder>/[hash].xml`: one file per spec, join them |
| RSpec | `--format RspecJunitFormatter --out {junit}`, with the rspec_junit_formatter gem |
| Robot Framework | `--xunit {junit}` |

What follows `--` in `qa run` is added at the end of the command. Where the command is more than one program (clear old reports, run, join), put it in a script that takes the report's path first and hands the rest to the runner. Keep the script in this repository, also for a suite inside a service:

```sh
#!/bin/sh
# run-maven.sh <report path> [the runner's own selection, e.g. -Dtest=CartTest#rejectsAnEmptyCart]
report="$1"; shift
rm -rf target/surefire-reports          # reports of an earlier run would be counted again
mvn -q test "$@"
code=$?
{ echo '<testsuites>'; cat target/surefire-reports/TEST-*.xml | grep -v '^<?xml'; echo '</testsuites>'; } > "$report"
exit $code
```

```json
{
  "name": "orders-unit",
  "stack": "other",
  "levels": ["unit", "component"],
  "repo": "orders",
  "command": "sh \"$QA_AGENT_ROOT/tests/support/run-maven.sh\" {junit}",
  "setup": ["mvn -q -DskipTests package"]
}
```

## Linking a test

A reference is the test file's path from the top of the repository the suite runs in, `::`, and the test's name as the report gives it: `orders/cart_test.go::TestEmptyCart`, `src/test/java/shop/CartTest.java::rejectsAnEmptyCart`. A group or class can go between: `path::Group::name`.

After the first recorded run, look at `tests_without_a_behaviour` in its result: it prints each unlinked test as the report names it. A linked test that still appears there, or a behaviour that stays `written` after a run, means the reference does not fit the report's name: link it again with `--replace`. Where the report's name is not written in the file (a display name built at run time), give tests fixed names.

## Expected failures and switched-off tests

Use the runner's own markers where it has them, with the behaviour id and the reason in the marker's text.

Where the runner cannot mark a test as expected to fail, write it into the test. The assertions stay as the evidence demands; the test passes while they fail, and fails on the day they pass:

```java
// B-0012: answers 200 for an empty cart, expected 422
@Test
void rejectsAnEmptyCart() {
    try {
        assertEquals(422, postOrder(emptyCart()).statusCode());   // the test, unchanged
    } catch (AssertionError stillWrong) {
        return;                                                   // the defect is still there
    }
    fail("B-0012 is corrected: remove the expected failure and link the test again");
}
```

Record the status in the catalog in the same step (`qa catalog status …`).

## What the guard does not see here

`qa guard` reads test code only in Python, JavaScript and TypeScript. In any other language it still checks where files are written, secrets, retry settings in the suite's command and the kit's own files, but not the tests themselves. Before each commit, check by hand what it would have checked:

- no test is focused, so that the others would not run;
- no fixed wait outside a polling helper with a deadline;
- every test that is switched off or expected to fail carries its behaviour id, and the catalog says `defect`, `quarantined` or `blocked` for that id;
- every test asserts something.

Say in the report that these suites were checked by hand.
