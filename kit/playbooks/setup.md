# Setup

The one playbook that asks the person questions. It ends with a project map (`.qa-agent/project.json`), an env file the person has filled in, the team's rules installed, a `qa doctor` that passes, a recorded agreement and a first commit.

Until the person has answered question 8, open nothing outside this repository.

Once the project map exists, `qa status` names the step to continue at by its heading here. Each step ends by recording the next.

## 1. Ask

Put all questions in one message and say that short answers are enough. Offer what you can already see as a default (this repository's name, its README).

1. **The application.** Its name; two or three sentences on what it does and for whom; the few things that must never break; and whether the first round of tests covers all of it or only some services or features.
2. **Placement.** Is this repository a QA repository for one or more application repositories, or is it the application's own repository?
3. **Services.** For each part that runs (API, web front end, worker): a short name; the path of its code on this machine; the branch merged work lands on; the URL it runs at now; a health path; the path of its OpenAPI document (JSON), if it has one, and whether reading it needs sign-in; which other services it calls. Where several services share one repository, say so.
4. **Running.** Is every service up now, with data that may be tested against? Is the environment the person's own, or shared? Does each service run from the checkout named in 3 as it is now, or from something built earlier or elsewhere (an image, a deployed environment)?
5. **Data.** May tests create data (accounts, orders, uploads) as long as they clean up after themselves, or may they only read? What must tests never touch or trigger: payments, messages to real people, paid third-party calls, other people's records?
6. **Signing in.** Which test users exist, and with which roles. How they sign in: a form with a password, an API token, single sign-on, a second factor. The *names* of the environment variables that will hold their credentials and any keys the tests need (propose `QA_USER_EMAIL`, `QA_USER_PASSWORD`, `QA_ADMIN_EMAIL`, `QA_ADMIN_PASSWORD`). Never ask for the values: the person types them into the env file.
7. **Team rules.** Does the team keep QA rules and skills in a library? Its path, or a copy of it pasted into `.qa-agent/library-source/`; and whether to install its whole QA part or to pick from a list. Run `qa library status` before asking: when it names a `pasted_copy`, the library is already here, so ask only whether to install its whole QA part or to pick.
8. **Off limits.** Any folder that must never be read, such as an existing test suite kept aside to measure this agent against.
9. **Stack.** A preferred language and test runner for the suites in this repository, or "propose one".
10. **Tests inside services.** May unit and component tests be added inside each service's repository? Where do its tests live, which command runs them, and what do they need beyond a fresh clone (a database, containers, a build, generated code)?

If an answer is still missing after one follow-up, take the cautious default (tests only read, nothing written inside services, nothing installed from a library) and tell the person which defaults you took.

## 2. Write the project map

```
qa init --name "<name>" --about "<what it does>" --placement qa-repo|service \
  --service "name=orders,repo=../orders,branch=main,url=http://127.0.0.1:8000,health=/healthz,openapi=/openapi.json" \
  --service "name=web,repo=../web,branch=main,url=http://127.0.0.1:3000" \
  --off-limits ../existing-tests --writes own-data|none
```

For the application's own repository use `--placement service` and give the service that lives here `repo=.`. `qa init` answers with the env file it chose (`.env` in a QA repository, `.qa-agent/.env` in an application's own) and writes each service's URL there and into the `.example` file beside it, under a name like `ORDERS_URL`; tests read URLs from there.

Then complete `.qa-agent/project.json` by hand and run `qa config validate`. `templates/project.example.json` shows a full map.

| Field | Fill in |
| --- | --- |
| `project.critical` | the things that must never break, one line each; behaviours for them get priority 1 |
| `project.notes` | what tests must never touch or trigger; how a service runs when it is not from its checkout (answer 4); anything else the next session must know. `status` prints it |
| `services[].about` | one line on what the service is |
| `services[].calls` | names of the services it calls |
| `services[].openapi` | the document's URL path, or a JSON file in a repository; `null` when there is none, or when it is YAML or needs sign-in |
| `auth.about` | which test users exist, what each may do, and how they sign in |
| `auth.env` | the names of the variables holding credentials and keys; the guard searches committed files for their values |
| `auth.state_files` | files that will hold signed-in browser state: `.auth/user.json` in a QA repository, `.qa-agent/.auth/user.json` in an application's own. They stay git-ignored |
| `environment.writes` | `own-data` or `none`, from answer 5 |
| `off_limits` | from answer 8 |

Services that share one repository get the same `repo`, and their globs (`sources`, `tests`, `writable`) are written from the top of that repository: `services/orders/app/**`.

Leave `suites`, `sources`, `tests`, `writable` and `bindings` for the steps below. Then `qa progress --phase setup --step env`.

## 3. The env file

Add every name from `auth.env` to the `.example` file beside the env file, each with an empty value and a comment saying what it is. Ask the person to put the real values into the env file (`qa init` named it; it is git-ignored) and to say when it is done. Never open the env file, before or after: `qa doctor` reports which settings are still empty.

Where signing in cannot be done by a program (single sign-on, a second factor), say so in `auth.about`, with how long a session lasts. The person will sign in once and save the browser's state when the end-to-end suite exists; `methods/e2e-tests.md` says how.

When the person has answered: `qa progress --phase setup --step check`.

## 4. Check

Run `qa doctor`. Correct what is yours to correct (a wrong path, health path or URL in the map). A service that does not answer is the person's to start. For a local HTTPS service with a certificate of its own making, run the helper with `QA_AGENT_INSECURE_TLS=1` in its environment.

Read the `<service>: code` rows. One that says merged work is expected on another branch, or that the checkout has uncommitted files, means the running service may not be the code of its last commit: tell the person now, and write what they answer into `project.notes`.

Do not go on until `ok` is true. Then `qa progress --phase setup --step rules`.

## 5. Team rules

When the person named no library and `qa library status` names no `pasted_copy`, skip to the last line of this step.

For a copy pasted into `.qa-agent/library-source/`, leave `--path` out in 1 and 3: the helper reads the copy, whether the person pasted the library's folder or its contents. The copy is the person's and is not committed: never edit it.

1. `qa library scan --path <library> --rules "<glob>" --skills "<glob>"` lists what the globs find. Narrow the globs to the library's QA part (for example `"artifacts/qa/**/*.mdc"` and `"artifacts/qa/**/SKILL.md"`).
2. Show the person the names with their one-line descriptions, unless they said to take everything.
3. `qa library install --path <library> --rules … --skills … [--select name …]` copies the chosen items to `.qa-agent/library/` and writes `INDEX.md` there. Add `--mirror-rules .cursor/rules` or `--mirror-skills .claude/skills` only if the person wants their own assistant to load them by itself.
4. Bind each installed item in `project.json` under `bindings`, by reading its description in `INDEX.md`:
   - `always`: applies to every test (naming, isolation, data, reporting);
   - `level.<level>`: applies to one level (`api`, `e2e`, …);
   - `task.<task>`: applies to one kind of work (`map`, `scaffold`, `write`, `heal`, `review`, `sync`, `defect`).
   An item bound nowhere is still listed in `INDEX.md` and can be opened when its subject comes up.
5. `qa rules --level api --task write` must list the files you expect and nothing under `bound_but_not_installed`.

Team rules are written for people and for particular assistants. Where one says to ask or to wait for confirmation, the unattended work after setup takes the cautious choice and records it instead. Read the items bound under `always` now. Where one conflicts with the limits in the manual, the limits win; tell the person which ones do.

Then `qa progress --phase setup --step stack`.

## 6. Look at the services and propose the stack

Now open the services' checkouts. For each, find its language and framework, how its own tests run and where they are, and fill in:

- `sources`: globs of its application code (`["app/**"]`, `["src/**"]`);
- `tests`: globs of its existing tests;
- `writable`: where this agent may add tests inside it, only if answer 10 allows it: the test folders or file patterns the service already uses (`["tests/**"]`, `["src/**/*.test.ts"]`). Leave it empty to write nothing there. Where services share a repository, list `writable` on one of them, with the test places of all.

When this repository is the application's own (`placement: service`), the `writable` of the service that lives here is also the only place the new suites can go. Add a folder of their own to it (for example `qa/**`), holding the API, integration, end-to-end and application-specific tests with their own dependency list, so the application's manifests stay untouched.

Propose the stack for the suites in this repository. Follow the team's rules where they name one. Otherwise use the language of most of the services' own tests: Python gives pytest, with pytest-playwright for the interface; TypeScript or JavaScript gives Playwright Test for both API and interface. One stack for all suites here unless there is a reason. Tests inside a service always use what that service already uses.

Tell the person the proposal in a few lines and get a yes:

- the stack and the suites, and what the first round covers;
- where tests will live, and what will be written inside which service. Say how: on a `qa-agent/…` branch of that service's repository, in a separate working copy, so its checked-out files and branch are never touched and nothing is pushed;
- in an application's own repository: that QA sessions put this working copy on a `qa-agent/…` branch, and that the person's uncommitted work stays as it is and is never committed by the agent;
- what the work needs on this machine: network access for the package manager and the browser download, and a shell the assistant may use for hours of test runs and git commands without a prompt for each.

Record the yes: `qa config agree --note "<the stack; the suites; where tests live; what is written inside which service; what the first round covers>"`. Every `qa status` prints the note, so the sessions that follow build what was agreed. This is the last question of setup.

Then `qa progress --phase setup --step commit`.

## 7. First commit

First `qa progress --phase setup --step handover`, so the commit carries it. Then:

- A repository without commits: `git init` if needed; `qa guard`; `git add -A`; `git commit -m "Set up the QA agent"`; then `qa branch --name baseline`.
- A repository with history: `qa branch --name setup`; `qa guard`; stage the kit's own files by name and commit. They are `.qa-agent`, the instruction files the installer added its block to (`AGENTS.md`, `CLAUDE.md`), and, in a repository that holds only tests, `.gitignore` and `.env.example`. Whatever else `git status` shows is the person's: leave it unstaged.

## 8. Hand over

Tell the person, briefly:

- what is recorded in the map, and which defaults or rule conflicts you noted;
- where the work lands: on the local QA branch that `qa branch` named. Until they merge it, QA sessions start on that branch, because their own branch does not hold the kit's records yet;
- that from here on the work runs without questions, and that the baseline will map the application, build the suites, write the tests, verify them and report.

Ask whether to start now. On a yes, follow `baseline.md`.
