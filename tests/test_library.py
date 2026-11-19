import shutil

import pytest

from qa_agent import library
from qa_agent.util import QAError, read_json
from conftest import commit, git_init, project, sh, write

RULE = """---
description: Assert on the response body, not only on the status code
globs: tests/api/**/*.py, tests/api/**/*.ts
alwaysApply: false
---
# API assertions

Every API test checks the body.
"""

ALWAYS_RULE = """---
description: "Tests never share state"
alwaysApply: true
---
Each test creates what it needs.
"""

SKILL = """---
name: write-api-test
description: >-
  Write an API test for one endpoint:
  happy path first, then each error the contract lists.
metadata:
  owner: qa
tags:
  - api
  - testing
---
# Write an API test

Steps go here.
"""


@pytest.fixture
def team_library(workspace):
    root = git_init(workspace / "library")
    commit(root, "rules", {
        "artifacts/qa/api/rules/api-assertions.mdc": RULE,
        "artifacts/qa/shared/rules/test-isolation.mdc": ALWAYS_RULE,
        "artifacts/qa/api/skills/write-api-test/SKILL.md": SKILL,
        "artifacts/qa/api/skills/write-api-test/references/checklist.md": "- status\n- body\n",
        "artifacts/backend/rules/naming.mdc": "---\ndescription: Backend naming\n---\nNames.\n",
        "README.md": "# library\n",
    })
    return root


def test_frontmatter_as_rule_and_skill_files_write_it():
    meta, body = library.parse_frontmatter(SKILL)
    assert meta["name"] == "write-api-test"
    assert meta["description"] == "Write an API test for one endpoint: happy path first, then each error the contract lists."
    assert meta["tags"] == ["api", "testing"]
    assert body.lstrip().startswith("# Write an API test")
    meta, _ = library.parse_frontmatter(RULE)
    assert meta["alwaysApply"] is False and meta["globs"] == "tests/api/**/*.py, tests/api/**/*.ts"
    assert library.parse_frontmatter('---\nglobs: ["a/**", "b/**"]\ndescription: |\n  line one\n  line two\n---\nx')[0] == {
        "globs": ["a/**", "b/**"], "description": "line one\nline two",
    }
    assert library.parse_frontmatter("# no header\n") == ({}, "# no header\n")
    assert library.parse_frontmatter("---\nnever: closed\n") == ({}, "---\nnever: closed\n")


def test_scan_lists_rules_and_skills(team_library):
    items = library.scan(team_library, ["artifacts/qa/**/*.mdc"], ["artifacts/qa/**/SKILL.md"])
    assert [(i["kind"], i["name"]) for i in items] == [
        ("rule", "api-assertions"), ("rule", "test-isolation"), ("skill", "write-api-test"),
    ]
    rule, always, skill = items
    assert rule["globs"] == ["tests/api/**/*.py", "tests/api/**/*.ts"] and rule["always"] is False
    assert always["always"] is True
    assert skill["files"] == [
        "artifacts/qa/api/skills/write-api-test/SKILL.md", "artifacts/qa/api/skills/write-api-test/references/checklist.md",
    ]
    assert len(library.scan(team_library)) == 4          # the default globs take every rule and skill
    with pytest.raises(QAError, match="is not a folder"):
        library.scan(team_library / "missing")


def test_install_copies_locks_and_indexes(host, team_library):
    host.configure(project())
    result = library.install(host.paths, "../library", ["artifacts/qa/**/*.mdc"], ["artifacts/qa/**/SKILL.md"])
    assert result["installed"] == {"rules": 2, "skills": 1}
    assert result["from"]["commit"] and result["from"]["dirty"] is False
    base = host.root / ".qa-agent/library"
    assert (base / "rules/api-assertions.mdc").read_text() == RULE
    assert (base / "skills/write-api-test/references/checklist.md").exists()
    index = (base / "INDEX.md").read_text()
    assert "| api-assertions | rule | tests/api/**/*.py, tests/api/**/*.ts |" in index
    assert "| test-isolation | rule | always |" in index
    assert "`.qa-agent/library/skills/write-api-test/SKILL.md`" in index
    lock = read_json(base / "lock.json")
    assert [item["name"] for item in lock["items"]] == ["api-assertions", "test-isolation", "write-api-test"]
    assert library.status(host.paths)["up_to_date"] is True


def test_install_a_selection_and_reject_unknown_names(host, team_library):
    host.configure(project())
    library.install(host.paths, team_library, select=["write-api-test"])
    assert not (host.root / ".qa-agent/library/rules").exists()
    assert library.status(host.paths)["not_installed"] == ["api-assertions", "naming", "test-isolation"]
    with pytest.raises(QAError, match="Not in the library under these globs: nope"):
        library.install(host.paths, team_library, select=["nope"])
    with pytest.raises(QAError, match="match no rule or skill"):
        library.install(host.paths, team_library, ["none/**"], ["none/**"])


def test_the_same_name_twice_is_refused(host, team_library):
    host.configure(project())
    commit(team_library, "clash", {"artifacts/other/rules/api-assertions.mdc": RULE})
    with pytest.raises(QAError, match="more than once"):
        library.install(host.paths, team_library)
    library.install(host.paths, team_library, exclude=["artifacts/other"])


def test_status_notices_what_changed_in_the_library(host, team_library):
    host.configure(project())
    library.install(host.paths, team_library, ["artifacts/qa/**/*.mdc"], ["artifacts/qa/**/SKILL.md"])
    commit(team_library, "edit", {
        "artifacts/qa/api/rules/api-assertions.mdc": RULE + "\nAnd the headers.\n",
        "artifacts/qa/api/skills/write-api-test/references/checklist.md": "- status\n- body\n- headers\n",
        "artifacts/qa/e2e/rules/selectors.mdc": "---\ndescription: Selectors\n---\nUse roles.\n",
    })
    (team_library / "artifacts/qa/shared/rules/test-isolation.mdc").unlink()
    state = library.status(host.paths)
    assert state["changed_in_library"] == ["api-assertions", "write-api-test"]
    assert state["removed_from_library"] == ["test-isolation"]
    assert state["not_installed"] == ["selectors"]
    assert state["up_to_date"] is False
    (host.root / ".qa-agent/library/rules/api-assertions.mdc").unlink()
    assert library.status(host.paths)["missing_files_here"] == ["api-assertions"]


def test_mirrors_go_to_folders_an_assistant_loads_and_spare_foreign_files(host, team_library):
    host.configure(project())
    write(host.root, ".cursor/rules/api-assertions.mdc", "the team's own, older copy\n")
    write(host.root, ".cursor/rules/unrelated.mdc", "keep me\n")
    args = dict(rules=["artifacts/qa/**/*.mdc"], skills=["artifacts/qa/**/SKILL.md"],
                mirror_rules=[".cursor/rules"], mirror_skills=[".claude/skills"])
    result = library.install(host.paths, team_library, **args)
    assert result["mirror_conflicts_left_untouched"] == [".cursor/rules/api-assertions.mdc"]
    assert (host.root / ".cursor/rules/api-assertions.mdc").read_text() == "the team's own, older copy\n"
    assert (host.root / ".cursor/rules/test-isolation.mdc").exists()
    assert (host.root / ".claude/skills/write-api-test/SKILL.md").read_text() == SKILL
    # Installing again replaces what an earlier install put there, and removes what is no longer selected.
    result = library.install(host.paths, team_library, select=["write-api-test"], **args)
    assert not (host.root / ".cursor/rules/test-isolation.mdc").exists()
    assert (host.root / ".cursor/rules/unrelated.mdc").exists()
    with pytest.raises(QAError, match="must be inside this repository"):
        library.install(host.paths, team_library, mirror_rules=["../elsewhere"])


QA_PART = dict(rules=["artifacts/qa/**/*.mdc"], skills=["artifacts/qa/**/SKILL.md"])


def paste(host, team_library, whole: bool):
    """Copy the library into the repository as a person does: the folder itself, or what it holds without its history."""
    slot = host.paths.library_source
    if slot.exists():
        shutil.rmtree(slot)
    if whole:
        shutil.copytree(team_library, slot / team_library.name)
    else:
        shutil.copytree(team_library, slot, ignore=shutil.ignore_patterns(".git"))


def test_a_pasted_copy_is_found_as_a_folder_or_as_its_contents(host, team_library):
    slot = host.paths.library_source
    assert library.pasted(host.paths) is None
    assert library.status(host.paths) == {"installed": 0, "note": "no team library is installed; the kit's own methods apply"}
    write(host.root, ".qa-agent/library-source/.DS_Store", "left by a file manager\n")
    assert library.pasted(host.paths) is None                         # an empty folder is no library
    with pytest.raises(QAError, match=r"--path is required.*paste a copy of that folder into \.qa-agent/library-source/"):
        library.source(host.paths)

    paste(host, team_library, whole=True)
    assert library.pasted(host.paths) == slot / "library"
    state = library.status(host.paths)
    assert state["installed"] == 0 and state["pasted_copy"] == ".qa-agent/library-source/library"
    assert "library scan" in state["note"]
    paste(host, team_library, whole=False)
    assert library.pasted(host.paths) == slot

    # A path the person gives wins; without one, the pasted copy is the library.
    assert library.source(host.paths, "../library") == "../library"
    assert library.source(host.paths) == str(slot)
    assert [(i["kind"], i["name"]) for i in library.scan(library.source(host.paths), **QA_PART)] == [
        ("rule", "api-assertions"), ("rule", "test-isolation"), ("skill", "write-api-test"),
    ]


def test_install_from_a_pasted_copy_records_its_place_in_this_repository(host, team_library):
    host.configure(project())
    paste(host, team_library, whole=True)
    result = library.install(host.paths, library.source(host.paths), **QA_PART)
    assert result["installed"] == {"rules": 2, "skills": 1}
    # The copy came with its history: the commit is the library's own, not this repository's.
    assert result["from"] == {
        "path": ".qa-agent/library-source/library", "commit": sh(team_library, "git", "rev-parse", "HEAD"), "dirty": False,
    }
    base = host.root / ".qa-agent/library"
    assert (base / "rules/api-assertions.mdc").read_text() == RULE
    assert "Copied from `.qa-agent/library-source/library` at " in (base / "INDEX.md").read_text()
    state = library.status(host.paths)
    assert state["library_reachable"] is True and state["up_to_date"] is True and "pasted_copy" not in state
    # Git sees the installed items and nothing of the copy they came from.
    seen = sh(host.root, "git", "status", "--porcelain", "--untracked-files=all")
    assert ".qa-agent/library/rules/api-assertions.mdc" in seen and "library-source" not in seen


def test_a_newer_copy_pasted_under_another_name_is_what_a_refresh_reads(host, team_library):
    host.configure(project())
    commit(host.root, "set up")                                        # this repository has a commit of its own
    paste(host, team_library, whole=True)
    library.install(host.paths, library.source(host.paths), select=["api-assertions"], **QA_PART)
    locked = read_json(host.paths.library_lock)["source"]["path"]
    assert library.source(host.paths, None, locked) == str(host.paths.library_source / "library")

    write(team_library, "artifacts/qa/api/rules/api-assertions.mdc", RULE + "\nAnd the headers.\n")
    paste(host, team_library, whole=False)                             # the contents this time, without the history
    assert library.status(host.paths)["library_reachable"] is False    # the folder installed from is gone
    again = library.source(host.paths, None, locked)
    assert again == str(host.paths.library_source)
    result = library.install(host.paths, again, select=["api-assertions"], **QA_PART)
    # A copy without history has no commit: this repository's own is not taken for it.
    assert result["from"] == {"path": ".qa-agent/library-source", "commit": None, "dirty": None}
    assert (host.root / ".qa-agent/library/rules/api-assertions.mdc").read_text().endswith("And the headers.\n")
    state = library.status(host.paths)
    assert state["up_to_date"] is True and state["library_commit_now"] is None

    # A library installed from a path outside this repository is refreshed from there, whatever is pasted.
    library.install(host.paths, team_library, **QA_PART)
    locked = read_json(host.paths.library_lock)["source"]["path"]
    assert locked == str(team_library) and library.source(host.paths, None, locked) == locked


def test_reading_list_gives_kit_methods_then_bound_team_rules(host, team_library):
    config = host.configure(project(
        suites=[{"name": "api", "command": "pytest --junitxml={junit}", "levels": ["api", "integration"], "stack": "pytest"}],
        bindings={"always": ["test-isolation"], "level": {"api": ["api-assertions"]}, "task": {"write": ["write-api-test", "gone"]}},
    ))
    library.install(host.paths, team_library)
    result = library.reading_list(host.paths, config, level="api", task="write", suite="api")
    assert [row["name"] for row in result["then_team_rules"]] == ["test-isolation", "api-assertions", "write-api-test"]
    assert result["then_team_rules"][2]["path"] == ".qa-agent/library/skills/write-api-test/SKILL.md"
    assert result["bound_but_not_installed"] == ["gone"]
    assert result["also_installed"]["count"] == 1
    for rel in result["read_first"]:
        assert (host.root / rel).is_file() or rel.startswith("/"), rel
    with pytest.raises(QAError, match="--level must be one of"):
        library.reading_list(host.paths, config, level="smoke")


def test_the_stack_file_is_listed_for_every_task_of_a_suite(host):
    config = host.configure(project(suites=[
        {"name": "api", "command": "pytest --junitxml={junit}", "levels": ["api"], "stack": "pytest"},
        {"name": "contract", "command": "schemathesis run --junit-xml {junit}", "levels": ["api"], "stack": "schemathesis"},
        {"name": "legacy", "command": "make test JUNIT={junit}", "levels": ["integration"]},
    ]))

    def read_first(**kwargs):
        return [rel.split("/methods/")[1] for rel in library.reading_list(host.paths, config, **kwargs)["read_first"]]

    assert read_first(task="heal", suite="api") == ["failures.md", "flaky.md", "api-tests.md", "stacks/pytest.md"]
    assert read_first(task="sync", suite="api")[-1] == "stacks/pytest.md"
    assert read_first(task="defect", suite="api")[-1] == "stacks/pytest.md"
    # A runner the kit has no file for, or a suite that names none, gets the general one.
    assert read_first(task="heal", suite="contract")[-1] == "stacks/other.md"
    assert read_first(suite="legacy") == ["integration-tests.md", "stacks/other.md"]
    assert not [rel for rel in read_first(task="heal") if rel.startswith("stacks/")]      # no suite, no runner to read about
