"""The kit's documents are instructions an agent follows to the letter, so they are checked like code:
every command they name exists with those options, every file they point at exists, every example is valid."""

from __future__ import annotations

import argparse
import json
import re

import pytest

from qa_agent import LEVELS, TASKS, library
from qa_agent.catalog import Catalog
from qa_agent.cli import build_parser
from qa_agent.config import validate
from conftest import REPO

KIT = REPO / "kit"
DOCS = sorted(KIT.rglob("*.md"))
# File names the documents mention that belong to the project or to a tool's output, not to the kit.
NOT_KIT_FILES = {"report.md", "INDEX.md", "SKILL.md", "AGENTS.md", "CLAUDE.md", "README.md"}


def doc_id(path):
    return str(path.relative_to(KIT))


# ---------------------------------------------------------------- commands


def _options(parser) -> set:
    return {flag for action in parser._actions for flag in action.option_strings}


def command_tree() -> dict:
    """command -> (its options, its actions or None, options per nested action)."""
    parser = build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    tree = {}
    for name, sub in top.choices.items():
        nested = next((a for a in sub._actions if isinstance(a, argparse._SubParsersAction)), None)
        if nested:
            tree[name] = (_options(sub), set(nested.choices), {n: _options(p) for n, p in nested.choices.items()})
        else:
            positional = next((a for a in sub._actions if not a.option_strings and a.choices), None)
            tree[name] = (_options(sub), set(positional.choices) if positional else None, {})
    return tree


def command_mentions(text: str) -> list:
    """Every `qa …` command written in a document: in code spans and in code blocks."""
    found = []
    in_block, pending = False, ""
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_block, pending = not in_block, ""
            continue
        if in_block:
            joined = pending + line.strip()
            if joined.endswith("\\"):
                pending = joined[:-1].rstrip() + " "
                continue
            pending = ""
            if joined.startswith("qa "):
                found.append(joined)
        else:
            found += re.findall(r"`(qa [^`]+)`", line)
    return found


def check_mention(mention: str, tree: dict) -> list:
    words = mention.split()[1:]
    command = words[0].strip(",.;:")
    if command not in tree:
        return [f"`{mention}`: no command '{command}'"]
    options, actions, per_action = tree[command]
    rest = words[1:]
    problems = []
    allowed = set(options)
    if actions is not None and rest and not rest[0].startswith(("-", "<", "[", "…")):
        for action in re.split(r"\\?\|", rest[0].strip(",.;:")):
            if action not in actions:
                problems.append(f"`{mention}`: '{command}' has no action '{action}'")
            allowed |= per_action.get(action, set())
        rest = rest[1:]
    for word in rest:
        word = word.strip("[],.;:")
        if word == "--":
            break  # what follows goes to the test runner
        if word.startswith("--"):
            flag = word.split("=", 1)[0]
            if flag not in allowed:
                problems.append(f"`{mention}`: '{command}' has no option {flag}")
    return problems


@pytest.mark.parametrize("path", DOCS, ids=doc_id)
def test_every_command_a_document_names_exists(path):
    tree = command_tree()
    problems = [p for mention in command_mentions(path.read_text(encoding="utf-8")) for p in check_mention(mention, tree)]
    assert not problems, "\n".join(problems)


def test_the_check_itself_catches_a_wrong_command():
    tree = command_tree()
    assert check_mention("qa catalog link B-0001 --test a::b", tree) == []
    assert check_mention("qa run api --times 3 -- tests/api -k empty --not-ours", tree) == []
    assert check_mention("qa surface fetch\\|diff\\|show\\|coverage --service S", tree) == []
    assert "no command" in check_mention("qa frobnicate", tree)[0]
    assert "no action" in check_mention("qa catalog publish", tree)[0]
    assert "no option --tests" in check_mention("qa catalog link B-0001 --tests a::b", tree)[0]
    assert "no option --mark-synced" in check_mention("qa guard --mark-synced", tree)[0]
    block = "```\nqa init --name x \\\n  --service a=b \\\n  --bogus\n```\nand `qa status` in a sentence"
    assert command_mentions(block) == ["qa init --name x --service a=b --bogus", "qa status"]


def test_the_manual_lists_every_command():
    manual = (KIT / "MANUAL.md").read_text(encoding="utf-8")
    named = {mention.split()[1].strip(",.;:") for mention in command_mentions(manual)}
    # `score` belongs to the judging session and has its own playbook; `version` needs no explanation.
    assert set(command_tree()) - named == {"score", "version"}


# ---------------------------------------------------------------- files


@pytest.mark.parametrize("path", DOCS, ids=doc_id)
def test_every_kit_file_a_document_points_at_exists(path):
    text = path.read_text(encoding="utf-8")
    missing = []
    for rel in set(re.findall(r"\b((?:playbooks|methods|templates)/[\w./-]+\.(?:md|json))", text)):
        if not (KIT / rel).is_file():
            missing.append(rel)
    names = {p.name for p in KIT.rglob("*.md")}
    for name in set(re.findall(r"`([\w-]+\.md)`", text)):
        if name not in names and name not in NOT_KIT_FILES:
            missing.append(name)
    assert not missing, f"{doc_id(path)} points at files the kit does not have: {sorted(missing)}"


def test_every_method_the_reading_list_can_name_exists():
    for group in ("task", "level"):
        for key, files in library.KIT_METHODS[group].items():
            assert key in (TASKS if group == "task" else LEVELS)
            for rel in files:
                assert (KIT / rel).is_file(), rel
    for level in LEVELS:
        assert library.KIT_METHODS["level"].get(level), f"no method for level {level}"
    stacks = {p.stem for p in (KIT / "methods" / "stacks").glob("*.md")}
    assert stacks == {"pytest", "playwright-python", "playwright-typescript", "vitest-jest", "other"}


def test_playbooks_cover_every_phase_the_status_page_can_send_an_agent_to():
    from qa_agent.status import PHASES

    for phase in PHASES:
        if phase != "idle":
            assert (KIT / "playbooks" / f"{phase}.md").is_file()
    assert (KIT / "playbooks" / "score.md").is_file()


def test_every_step_has_its_heading_and_every_playbook_records_its_steps():
    """`qa status` sends the reader to a heading; the playbook must have it, and must record the step on reaching it."""
    from qa_agent.status import STEPS

    for phase, steps in STEPS.items():
        if not steps:
            continue
        text = (KIT / "playbooks" / f"{phase}.md").read_text(encoding="utf-8")
        headings = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")]
        recorded = set(re.findall(rf"qa progress --phase {phase} --step ([a-z]+)", text))
        for key, heading in steps:
            assert heading in headings, f"{phase}.md has no heading '{heading}'"
            if key != steps[0][0]:           # the first step is where a new phase starts by itself
                assert key in recorded, f"{phase}.md never records `qa progress --phase {phase} --step {key}`"


@pytest.mark.parametrize("path", DOCS, ids=doc_id)
def test_every_step_a_document_records_is_one_the_helper_accepts(path):
    from qa_agent.status import STEPS

    problems = []
    for mention in command_mentions(path.read_text(encoding="utf-8")):
        words = mention.split()
        if words[1] != "progress" or "--step" not in words:
            continue
        step = words[words.index("--step") + 1].strip("`,.;:")
        phase = words[words.index("--phase") + 1].strip("`,.;:") if "--phase" in words else None
        if phase is None:
            problems.append(f"`{mention}`: give --phase with --step, so the line works wherever it is read")
        elif step.startswith("<") or phase.startswith("<"):
            continue
        elif step not in [key for key, _ in STEPS.get(phase, ())]:
            problems.append(f"`{mention}`: phase '{phase}' has no step '{step}'")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------- examples


def example_project() -> dict:
    return json.loads((KIT / "templates" / "project.example.json").read_text(encoding="utf-8"))


def json_blocks(text: str) -> list:
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.S)
    return [("{" + block + "}") if block.lstrip().startswith('"') else block for block in blocks]


def test_the_example_project_map_is_valid():
    data = example_project()
    assert validate(data) == []
    stacks = {p.stem for p in (KIT / "methods" / "stacks").glob("*.md")}
    assert {suite["stack"] for suite in data["suites"]} <= stacks
    covered = {level for suite in data["suites"] for level in suite["levels"]}
    assert covered == set(LEVELS)                      # the example shows a suite for every level


def test_the_example_behaviours_are_accepted(host):
    config = host.configure(example_project())
    items = json.loads((KIT / "templates" / "behaviours.example.json").read_text(encoding="utf-8"))
    result = Catalog(host.paths, config).add(items)
    assert result["rejected"] == [] and result["skipped_duplicates"] == [] and len(result["added"]) == len(items)
    catalog = Catalog(host.paths, config)
    assert {entry["level"] for entry in catalog.entries} >= {"unit", "component", "api", "integration", "e2e", "app"}
    assert catalog.get("B-0004")["repo"] == "orders" and catalog.get("B-0007")["repo"] == "web"    # tests live with the code
    assert catalog.get("B-0001")["repo"] == "."


@pytest.mark.parametrize("path", DOCS, ids=doc_id)
def test_every_json_example_in_a_document_is_valid(path):
    for block in json_blocks(path.read_text(encoding="utf-8")):
        data = json.loads(block)
        suites = data.get("suites") if isinstance(data, dict) else None
        if isinstance(data, dict) and "command" in data:
            suites = [data]
        if suites:
            project = example_project()
            project["suites"] = suites
            assert validate(project) == [], f"{doc_id(path)}: suite example is not valid"
