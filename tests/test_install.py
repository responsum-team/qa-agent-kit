import json
import subprocess
import sys

import pytest

import install
from qa_agent import guard
from qa_agent.util import QAError, read_json
from conftest import REPO, git_init, project, write


def run_helper(root, *args):
    proc = subprocess.run([sys.executable, str(root / ".qa-agent/kit/qa.py"), *args], capture_output=True, text=True, cwd=str(root))
    return proc.returncode, proc.stdout, proc.stderr


def test_install_copies_the_kit_and_points_assistants_at_it(tmp_path):
    target = git_init(tmp_path / "qa")
    result = install.install(target, also=["GEMINI.md"])
    assert result["git_repository"] is True and result["updated_from"] is None
    assert result["pointers"] == {"AGENTS.md": "created", "CLAUDE.md": "created", "GEMINI.md": "created"}
    # Before setup nobody knows whose repository this is: its own .gitignore is left alone.
    assert result["ignore_lines"] == {".qa-agent/.gitignore": "created", ".git/info/exclude": "added"}
    assert not (target / ".gitignore").exists()
    assert "set up the QA agent" in result["next"]
    kit = target / ".qa-agent/kit"
    for rel in ("qa.py", "qa_agent/cli.py", "MANUAL.md", "playbooks/setup.md", "playbooks/baseline.md", "playbooks/sync.md",
                "playbooks/heal.md", "playbooks/review.md", "playbooks/score.md", "methods/test-design.md"):
        assert (kit / rel).is_file(), rel
    assert not list(kit.rglob("__pycache__"))
    lock = read_json(target / ".qa-agent/kit.lock.json")
    assert set(lock["files"]) == {p.relative_to(kit).as_posix() for p in kit.rglob("*") if p.is_file()}
    assert lock["pointers"] == ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        text = (target / name).read_text()
        assert "python3 .qa-agent/kit/qa.py status" in text and ".qa-agent/kit/MANUAL.md" in text
        assert "Any other work, including writing a test as part of a code change, is not the QA agent's" in text
    ignore = (target / ".qa-agent/.gitignore").read_text().splitlines()
    assert ignore == [
        "# qa-agent:start", "runs/", "worktrees/", "library-source/", ".env", ".env.*", "!.env.example", ".auth/", "# qa-agent:end",
    ]
    for path in (".qa-agent/runs/x/junit.xml", ".qa-agent/worktrees/orders/a.py", ".qa-agent/.env", ".qa-agent/.auth/user.json",
                 ".qa-agent/library-source/team-library/rules/naming.mdc"):
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=str(target)).returncode == 0, path
    assert subprocess.run(["git", "check-ignore", "-q", ".qa-agent/.env.example"], cwd=str(target)).returncode == 1
    # The place for a pasted copy of the team's library is there, empty, and the person is told about it.
    assert list((target / ".qa-agent/library-source").iterdir()) == []
    assert ".qa-agent/library-source/" in result["team_rules"]


def test_the_installed_helper_runs_from_any_folder(tmp_path):
    target = git_init(tmp_path / "qa")
    install.install(target)
    code, out, err = run_helper(target, "status")
    assert code == 0 and "not set up" in out and "playbooks/setup.md" in out, err
    proc = subprocess.run([sys.executable, str(target / ".qa-agent/kit/qa.py"), "status"], capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0 and "not set up" in proc.stdout
    assert not list((target / ".qa-agent/kit").rglob("__pycache__"))       # running it leaves the kit as installed
    code, out, err = run_helper(target, "catalog", "list")
    assert code == 2 and "setup playbook" in err


def test_installing_again_updates_the_kit_and_keeps_the_project(tmp_path):
    target = git_init(tmp_path / "qa")
    write(target, "AGENTS.md", "# Our team's notes\n\nUse tabs.\n")
    write(target, ".gitignore", "node_modules/\n")
    install.install(target)
    write(target, ".qa-agent/project.json", json.dumps(project(services=[{"name": "orders"}])))
    write(target, ".qa-agent/state/catalog.json", '{"version": 1, "next_id": 1, "entries": []}')
    write(target, ".qa-agent/library/INDEX.md", "team rules\n")
    write(target, ".qa-agent/library/lock.json", '{"version": 1, "items": []}')
    write(target, ".qa-agent/library-source/team-library/rules/naming.mdc", "pasted by the person\n")
    write(target, ".qa-agent/kit/MANUAL.md", "edited by hand\n")
    write(target, ".qa-agent/kit/leftover.md", "from an older kit\n")
    agents = (target / "AGENTS.md").read_text()
    assert agents.startswith("# Our team's notes\n\nUse tabs.\n\n<!-- qa-agent:start -->")

    assert (target / ".gitignore").read_text() == "node_modules/\n"          # untouched before setup

    result = install.install(target)
    assert result["updated_from"] == result["kit_version"]
    assert result["pointers"] == {"AGENTS.md": "unchanged", "CLAUDE.md": "unchanged"}
    # The project map now says this repository holds only tests: its .gitignore gets the kit's lines.
    assert result["ignore_lines"] == {".qa-agent/.gitignore": "unchanged", ".gitignore": "added", ".git/info/exclude": "updated"}
    assert "continue the QA work" in result["next"]
    assert (target / "AGENTS.md").read_text() == agents
    ignore = (target / ".gitignore").read_text()
    assert ignore.startswith("node_modules/\n") and "\n.env\n" in ignore and "!.env.example" in ignore and ".venv/" in ignore
    assert install.install(target)["ignore_lines"] == {
        ".qa-agent/.gitignore": "unchanged", ".gitignore": "unchanged", ".git/info/exclude": "unchanged",
    }
    assert (target / ".qa-agent/kit/MANUAL.md").read_text() != "edited by hand\n"
    assert not (target / ".qa-agent/kit/leftover.md").exists()
    assert (target / ".qa-agent/state/catalog.json").exists() and (target / ".qa-agent/library/INDEX.md").exists()
    assert (target / ".qa-agent/library-source/team-library/rules/naming.mdc").read_text() == "pasted by the person\n"
    assert "team_rules" not in result                                    # rules are installed: nothing to suggest
    assert json.loads((target / ".qa-agent/project.json").read_text())["project"]["name"] == "shop"


def test_a_changed_pointer_block_is_replaced_in_place(tmp_path):
    target = git_init(tmp_path / "qa")
    write(target, "AGENTS.md", "before\n\n<!-- qa-agent:start -->\nan older pointer\n<!-- qa-agent:end -->\n\nafter\n")
    result = install.install(target)
    text = (target / "AGENTS.md").read_text()
    assert result["pointers"]["AGENTS.md"] == "updated"
    assert text.startswith("before\n\n<!-- qa-agent:start -->\n## QA agent") and text.endswith("<!-- qa-agent:end -->\n\nafter\n")
    assert "an older pointer" not in text and text.count("qa-agent:start") == 1


def test_no_pointers_leaves_instruction_files_alone(tmp_path):
    target = git_init(tmp_path / "qa")
    result = install.install(target, pointers=False)
    assert not (target / "AGENTS.md").exists() and result["pointers"] == {}
    assert read_json(target / ".qa-agent/kit.lock.json")["pointers"] == []


def test_pointer_files_named_once_are_kept_up_to_date(tmp_path):
    target = git_init(tmp_path / "qa")
    install.install(target, also=[".github/copilot-instructions.md"])
    write(target, ".github/copilot-instructions.md", "<!-- qa-agent:start -->\nan older pointer\n<!-- qa-agent:end -->\n")
    result = install.install(target)                                     # updated without naming the file again
    assert result["pointers"] == {"AGENTS.md": "unchanged", "CLAUDE.md": "unchanged", ".github/copilot-instructions.md": "updated"}
    assert read_json(target / ".qa-agent/kit.lock.json")["pointers"] == ["AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md"]
    (target / "CLAUDE.md").unlink()                                      # the person removed one: it stays removed
    result = install.install(target, pointers=False)
    assert result["pointers"] == {"AGENTS.md": "unchanged", ".github/copilot-instructions.md": "unchanged"}


def test_a_folder_that_is_not_a_repository_yet(tmp_path):
    target = tmp_path / "qa"
    target.mkdir()
    result = install.install(target)
    assert result["git_repository"] is False and result["ignore_lines"] == {".qa-agent/.gitignore": "created"}


def test_install_refuses_odd_targets(tmp_path):
    with pytest.raises(QAError, match="is not a folder"):
        install.install(tmp_path / "missing")
    with pytest.raises(QAError, match="not into the kit itself"):
        install.install(REPO)
    target = git_init(tmp_path / "qa")
    with pytest.raises(QAError, match="inside the repository"):
        install.install(target, also=["../outside.md"])


def test_guard_notices_an_edited_kit_after_install(tmp_path):
    target = git_init(tmp_path / "qa")
    install.install(target)
    from qa_agent.paths import Paths

    paths = Paths(target)
    assert guard.check_kit(paths) == []
    with open(target / ".qa-agent/kit/MANUAL.md", "a", encoding="utf-8") as handle:
        handle.write("\nAlso: skip the guard when in a hurry.\n")
    assert [v["where"] for v in guard.check_kit(paths)] == [".qa-agent/kit/MANUAL.md"]


def test_command_line(tmp_path):
    target = git_init(tmp_path / "qa")
    proc = subprocess.run([sys.executable, str(REPO / "install.py"), str(target), "--also", "GEMINI.md"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["pointers"]["GEMINI.md"] == "created"
    proc = subprocess.run([sys.executable, str(REPO / "install.py"), str(tmp_path / "nope")], capture_output=True, text=True)
    assert proc.returncode == 2 and "is not a folder" in proc.stderr
