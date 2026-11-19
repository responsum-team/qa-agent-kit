# Working on the QA agent kit

This repository builds a kit that is copied into other repositories, where coding assistants of any make follow it to write and maintain tests. `README.md` says what the kit does; `docs/design.md` says why it is built this way. This file is for sessions that change the kit itself.

## Layout

- `kit/` is the product. `install.py` copies it, unchanged, to `<repository>/.qa-agent/kit/`.
- `kit/MANUAL.md`, `kit/playbooks/`, `kit/methods/` are instructions an assistant reads while it works.
- `kit/qa.py` and `kit/qa_agent/` are the helper those instructions call.
- `tests/` tests the helper, the installer and the documents.

## Rules for `kit/`

- **Standard library only, Python 3.9 syntax.** The kit runs in repositories whose toolchain is unknown. No third-party imports, no `match`, no `tomllib`.
- **Nothing about any project.** No product names, URLs, repository names or team rule text. Examples use the invented "Example Shop". A team's rules are installed from its own library at setup, never copied here.
- **No assistant-specific features.** Instructions must work for an assistant that has only file and shell tools: no sub-agents, no MCP servers, no memory between sessions. Everything that must survive a session is written to disk by the helper.
- **Documents are operative.** Each rule is stated once, with a one-sentence reason where the reason helps apply it. No history, no ticket references, no explanations of how the kit came to be.
- **Documents and helper change together.** `tests/test_docs.py` fails when a document names a command, an option or a kit file that does not exist, or when a JSON example does not validate.
- **A limit the guard can check is checked by `qa guard`**, not only written in the manual. When adding one, add the check and its test. The table in `docs/design.md` says, per limit of the manual, what the guard sees and what stays an instruction: keep it true when a limit or a check changes.
- **Helper output is for an assistant to read**: JSON on standard output, short and complete. Errors are a `QAError` saying what to change; exit code 2.
- **State files are written only by the helper** and carry a `version`. Change a format together with the code that reads it and a test with an old file.

## Checking

```
uv run pytest
uv run --isolated --python 3.9 pytest
```

Run both after any change. `tests/test_end_to_end.py` drives the installed helper through setup, baseline, a merge, sync and scoring against a sample service; extend it when a playbook gains a step that the helper supports.

## Never

- Read a real project's reference test suite, or any scoring output, as a source for kit content.
- Install the kit into this repository.
- Commit anything under `scores/`.
