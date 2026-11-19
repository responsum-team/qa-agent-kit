import json

import pytest

from qa_agent import util
from qa_agent.util import QAError, dumps, glob_match, key_words, match_any, parse_env_file, walk_files, words
from conftest import write


@pytest.mark.parametrize("path, pattern, expected", [
    ("tests/api/test_orders.py", "tests/**", True),
    ("tests/api/test_orders.py", "tests/**/*.py", True),
    ("tests/test_orders.py", "tests/**/*.py", True),
    ("src/app.py", "tests/**", False),
    ("a/b/c.spec.ts", "*.spec.ts", True),          # a bare pattern matches at any depth
    ("a/b/c.spec.ts", "*.spec.*", True),
    ("a/b/c.ts", "*.spec.ts", False),
    ("pkg/tests/unit/x.py", "tests", True),         # a folder name matches what it contains
    ("pkg/tests/unit/x.py", "pkg/tests", True),
    ("pkg/latests/x.py", "tests", False),
    ("src/a.test.tsx", "src/**/*.test.{ts,tsx}", True),
    ("src/a.test.js", "src/**/*.test.{ts,tsx}", False),
    (".qa-agent/kit/qa.py", ".qa-agent", True),     # a leading dot is part of the name
    ("./tests/x.py", "tests/**", True),
    ("qa-agent/kit/qa.py", ".qa-agent", False),
    ("src/x.py", "src/[!_]*.py", True),
    ("src/_x.py", "src/[!_]*.py", False),
    ("src/deep/x.py", "src/*.py", False),           # one star stays inside a folder
    ("AGENTS.md", "/AGENTS.md", True),              # a leading slash: at the top of the repository only
    ("docs/AGENTS.md", "/AGENTS.md", False),
    ("docs/AGENTS.md", "AGENTS.md", True),
    (".qa-agent/state/catalog.json", "/.qa-agent", True),
    ("vendor/.qa-agent/x.json", "/.qa-agent", False),
])
def test_glob_match(path, pattern, expected):
    assert glob_match(path, pattern) is expected


def test_match_any_with_nothing_to_match():
    assert match_any("a.py", []) is False
    assert match_any("a.py", None) is False


def test_walk_files_skips_dependency_folders(tmp_path):
    write(tmp_path, "src/a.py", "")
    write(tmp_path, "node_modules/x/index.js", "")
    write(tmp_path, ".git/config", "")
    write(tmp_path, "tests/test_a.py", "")
    assert walk_files(tmp_path) == ["src/a.py", "tests/test_a.py"]
    assert walk_files(tmp_path, ["*.py"], ["tests"]) == ["src/a.py"]


def test_parse_env_file(tmp_path):
    path = write(tmp_path, ".env", "\n".join([
        "# a comment",
        "ORDERS_URL=http://127.0.0.1:8000",
        "export TOKEN='abc def'",
        'QUOTED="x # not a comment"',
        "INLINE=value # a comment",
        "EMPTY=",
        "not a setting",
        "1BAD=x",
    ]))
    assert parse_env_file(path) == {
        "ORDERS_URL": "http://127.0.0.1:8000", "TOKEN": "abc def", "QUOTED": "x # not a comment",
        "INLINE": "value", "EMPTY": "",
    }
    assert parse_env_file(tmp_path / "missing") == {}


def test_dumps_is_valid_json_and_keeps_records_on_one_line():
    data = {"entries": [{"id": "B-0001", "tests": ["a::b"], "note": "é"}, {"id": "B-0002", "tests": []}], "n": 1, "empty": {}}
    text = dumps(data)
    assert json.loads(text) == data
    assert '{"id": "B-0002", "tests": []}' in text
    assert "é" in text


def test_write_json_is_atomic_and_readable(tmp_path):
    target = tmp_path / "deep" / "state.json"
    util.write_json(target, {"a": [1, 2]})
    assert util.read_json(target) == {"a": [1, 2]}
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(QAError, match="not valid JSON"):
        util.read_json(target)


def test_words_split_every_naming_style():
    assert words("test_emptyCart_isRejected HTTP404") == ["test", "empty", "cart", "is", "rejected", "http", "404"]
    assert key_words("should reject an empty cart") == {"reject", "empty", "cart"}


def test_join_url():
    assert util.join_url("http://x/", "/health") == "http://x/health"
    assert util.join_url("http://x", "") == "http://x"


def test_http_get_reports_no_answer():
    status, body, error = util.http_get("http://127.0.0.1:9/nothing", timeout=1)
    assert status is None and body == b"" and error


def test_git_try_tells_an_answer_from_an_error(tmp_path):
    from conftest import commit, git_init

    repo = git_init(tmp_path / "repo")
    commit(repo, "first", {"a.txt": "a\n"})
    assert util.git_try(repo, "rev-parse", "--verify", "-q", "HEAD")[0] is True
    assert util.git_try(repo, "rev-parse", "--verify", "-q", "no-such-branch") == (False, "")
    assert util.git_try(repo, "no-such-command")[0] is False


def test_the_ignore_lines_keep_secrets_and_raw_output_local(tmp_path):
    from conftest import git_init, sh
    from qa_agent import ignore

    repo = git_init(tmp_path / "repo")
    write(repo, ".gitignore", "dist/\n")
    done = ignore.keep_local(repo)
    assert done == {".qa-agent/.gitignore": "created", ".git/info/exclude": "added"}
    assert (repo / ".gitignore").read_text() == "dist/\n"                       # an application's file: not touched

    def ignored(path):
        return sh(repo, "git", "check-ignore", path, check=False) == path

    assert ignored(".qa-agent/.env") and ignored(".qa-agent/runs/x") and ignored(".qa-agent/.auth/user.json")
    assert ignored("tests/.venv/bin/python") and ignored("e2e/node_modules/x/index.js")
    assert not ignored(".qa-agent/.env.example") and not ignored(".env") and not ignored(".qa-agent/project.json")
    done = ignore.keep_local(repo, qa_repo=True)                                 # a repository that holds only tests
    assert done == {".qa-agent/.gitignore": "unchanged", ".gitignore": "added", ".git/info/exclude": "updated"}
    assert ignored(".env") and ignored(".env.local") and ignored(".auth/user.json") and not ignored(".env.example")
    assert (repo / ".gitignore").read_text().startswith("dist/\n\n# qa-agent:start\n")
    assert set(ignore.keep_local(repo, qa_repo=True).values()) == {"unchanged"}
    # Lines the person added around the kit's block stay.
    write(repo, ".qa-agent/.gitignore", "scratch/\n" + (repo / ".qa-agent/.gitignore").read_text())
    ignore.keep_local(repo, qa_repo=True)
    assert (repo / ".qa-agent/.gitignore").read_text().startswith("scratch/\n# qa-agent:start\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ignore.keep_local(plain) == {".qa-agent/.gitignore": "created"}       # not a repository yet
