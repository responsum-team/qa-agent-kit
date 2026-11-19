"""Team rules and skills: read from where the team maintains them, installed into this repository.

The kit ships only its own generic methods. A team's rules stay the team's: they
are copied from its library on request, with the library's commit and a hash of
every item recorded, so a later check can tell what has drifted. The library is
read at a path the person names, or from a copy they pasted into
.qa-agent/library-source/.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import LEVELS, TASKS
from .util import (
    QAError, clip, git_dirty, git_head, git_ok, is_git_repo, match_any, now_iso, read_json, sha256_bytes,
    walk_files, write_json,
)

DEFAULT_RULES = ["**/*.mdc"]
DEFAULT_SKILLS = ["**/SKILL.md"]
# Files a file manager leaves in a folder: they do not make an empty folder a library.
CLUTTER = {".DS_Store", "Thumbs.db", "desktop.ini"}

# The kit's own methods, by what the agent is doing and at which test level.
KIT_METHODS = {
    "task": {
        "map": ["methods/map-the-system.md"],
        "scaffold": ["methods/scaffold.md"],
        "write": ["methods/test-design.md"],
        "heal": ["methods/failures.md", "methods/flaky.md"],
        "review": ["methods/test-design.md"],
        "sync": ["methods/map-the-system.md", "methods/failures.md"],
        "defect": ["methods/failures.md"],
        "score": [],
    },
    "level": {
        "unit": ["methods/in-service-tests.md"],
        "component": ["methods/in-service-tests.md"],
        "api": ["methods/api-tests.md"],
        "integration": ["methods/integration-tests.md"],
        "e2e": ["methods/e2e-tests.md"],
        "app": ["methods/app-specific-tests.md"],
    },
}


# ---------------------------------------------------------------- front matter


def _scalar(text: str):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def _value(text: str):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(part) for part in text[1:-1].split(",") if part.strip()]
    return _scalar(text)


def parse_frontmatter(text: str):
    """The `---` header of a rule or skill file as a dict, and the text after it.

    Reads what such headers use in practice: `key: value`, folded (`>`) and
    literal (`|`) blocks, inline lists and dash lists. It is not a YAML parser.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict = {}
    i = 1
    while i < end:
        match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", lines[i])
        if not match:
            i += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        i += 1
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            block = []
            while i < end and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            joiner = " " if value.startswith(">") else "\n"
            meta[key] = joiner.join(part for part in block if part or joiner == "\n").strip()
        elif value == "":
            items = []
            while i < end and re.match(r"^\s*-\s+\S", lines[i]):
                items.append(_scalar(re.sub(r"^\s*-\s+", "", lines[i])))
                i += 1
            while i < end and lines[i].startswith((" ", "\t")):
                i += 1  # a nested mapping: not needed here
            meta[key] = items if items else ""
        else:
            meta[key] = _value(value)
    return meta, "\n".join(lines[end + 1 :])


def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


# ---------------------------------------------------------------- scanning


def _hash_files(root: Path, files: list) -> str:
    parts = []
    for rel in sorted(files):
        parts.append(rel + ":" + sha256_bytes((root / rel).read_bytes()))
    return sha256_bytes("\n".join(parts).encode("utf-8"))


def scan(library, rules=None, skills=None, exclude=None) -> list:
    """Every rule and skill a library holds under the given globs."""
    root = Path(library).expanduser().resolve()
    if not root.is_dir():
        raise QAError(f"Rules library {root} is not a folder")
    rules = rules or DEFAULT_RULES
    skills = skills or DEFAULT_SKILLS
    exclude = list(exclude or [])
    items = []
    for rel in walk_files(root, rules, exclude):
        meta, _ = parse_frontmatter((root / rel).read_text(encoding="utf-8", errors="replace"))
        name = Path(rel).name
        name = name[: name.index(".")] if "." in name else name
        items.append({
            "kind": "rule",
            "name": name,
            "description": clip(str(meta.get("description") or ""), 400),
            "always": meta.get("alwaysApply") is True,
            "globs": _as_list(meta.get("globs")),
            "source": rel,
            "files": [rel],
            "sha256": _hash_files(root, [rel]),
        })
    for rel in walk_files(root, skills, exclude):
        meta, _ = parse_frontmatter((root / rel).read_text(encoding="utf-8", errors="replace"))
        folder = Path(rel).parent
        prefix = "" if folder.as_posix() == "." else folder.as_posix() + "/"
        files = [f for f in walk_files(root, [prefix + "**"] if prefix else ["**"], exclude)]
        items.append({
            "kind": "skill",
            "name": str(meta.get("name") or folder.name),
            "description": clip(str(meta.get("description") or ""), 400),
            "always": False,
            "globs": [],
            "source": rel,
            "files": files,
            "sha256": _hash_files(root, files),
        })
    items.sort(key=lambda item: (item["kind"], item["name"], item["source"]))
    return items


def duplicates(items: list) -> dict:
    seen: dict = {}
    for item in items:
        seen.setdefault((item["kind"], item["name"]), []).append(item["source"])
    return {f"{kind} {name}": sources for (kind, name), sources in seen.items() if len(sources) > 1}


# ---------------------------------------------------------------- where the library is


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def pasted(paths):
    """The copy of a library the person pasted into this repository, or None.

    A folder pasted whole lands one level down, its contents land at the top: both are the library.
    """
    slot = paths.library_source
    if not slot.is_dir():
        return None
    entries = [entry for entry in slot.iterdir() if entry.name not in CLUTTER]
    if not entries:
        return None
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return slot


def source(paths, given=None, locked=None) -> str:
    """The library to read: the path given, else the one installed from, else the pasted copy."""
    if given:
        return given
    found = pasted(paths)
    if locked:
        # A copy pasted again may sit in a folder of another name: it is still the pasted copy.
        again = found is not None and _inside(paths.library_source, paths.root / locked)
        return str(found) if again else locked
    if found is not None:
        return str(found)
    raise QAError(
        "--path is required: the folder the team keeps its rules and skills in. "
        f"Or paste a copy of that folder into {paths.rel(paths.library_source)}/ and leave --path out"
    )


def _versioned(root: Path) -> bool:
    """True when git tracks the folder. A copy pasted without its history, into a folder git ignores, has no commit."""
    return is_git_repo(root) and not git_ok(root, "check-ignore", "-q", ".")


def _source_info(paths, root: Path) -> dict:
    # A library inside this repository is recorded by its place here, so the record holds on every machine.
    path = root.relative_to(paths.root.resolve()).as_posix() if _inside(paths.root, root) else str(root)
    info = {"path": path, "commit": None, "dirty": None}
    if _versioned(root):
        info["commit"] = git_head(root)
        info["dirty"] = git_dirty(root)
    return info


# ---------------------------------------------------------------- installing


def install(paths, library, rules=None, skills=None, exclude=None, select=None,
            mirror_rules=(), mirror_skills=(), force: bool = False) -> dict:
    """Copy the chosen rules and skills into .qa-agent/library, and into any native folders asked for."""
    root = Path(library).expanduser()
    if not root.is_absolute():
        root = paths.root / root
    root = root.resolve()
    items = scan(root, rules, skills, exclude)
    if select is not None:
        wanted = set(select)
        known = {item["name"] for item in items}
        missing = sorted(wanted - known)
        if missing:
            raise QAError(f"Not in the library under these globs: {', '.join(missing)}")
        items = [item for item in items if item["name"] in wanted]
    clashes = duplicates(items)
    if clashes:
        lines = [f"{name}: {', '.join(sources)}" for name, sources in clashes.items()]
        raise QAError("The same name appears more than once; narrow the globs or exclude one:\n  " + "\n  ".join(lines))
    if not items:
        raise QAError("Nothing to install: the globs match no rule or skill")

    for folder in list(mirror_rules) + list(mirror_skills):
        if not _inside(paths.root, paths.root / folder):
            raise QAError(f"Mirror folder '{folder}' must be inside this repository")

    previous = read_json(paths.library_lock, {}) or {}
    ours = set(previous.get("mirror_files") or [])
    for rel in ours:
        target = paths.root / rel
        if target.is_file():
            target.unlink()
    for folder in (paths.library / "rules", paths.library / "skills"):
        if folder.exists():
            shutil.rmtree(folder)

    mirror_files, conflicts, locked = [], [], []

    def copy(source: Path, target: Path, mirror: bool) -> None:
        rel = target.relative_to(paths.root).as_posix()
        if mirror:
            if target.exists() and rel not in ours and not force and target.read_bytes() != source.read_bytes():
                conflicts.append(rel)
                return
            mirror_files.append(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    for item in items:
        installed = []
        if item["kind"] == "rule":
            suffix = "".join(Path(item["source"]).suffixes) or ".md"
            filename = item["name"] + suffix
            target = paths.library / "rules" / filename
            copy(root / item["source"], target, False)
            installed.append(target.relative_to(paths.root).as_posix())
            for folder in mirror_rules:
                copy(root / item["source"], paths.root / folder / filename, True)
        else:
            base = Path(item["source"]).parent
            for rel in item["files"]:
                inner = Path(rel).relative_to(base)
                target = paths.library / "skills" / item["name"] / inner
                copy(root / rel, target, False)
                installed.append(target.relative_to(paths.root).as_posix())
                for folder in mirror_skills:
                    copy(root / rel, paths.root / folder / item["name"] / inner, True)
        locked.append({
            "kind": item["kind"], "name": item["name"], "description": item["description"],
            "always": item["always"], "globs": item["globs"], "source": item["source"],
            "sha256": item["sha256"], "files": installed,
        })

    lock = {
        "version": 1,
        "source": _source_info(paths, root),
        "globs": {"rules": rules or DEFAULT_RULES, "skills": skills or DEFAULT_SKILLS, "exclude": list(exclude or [])},
        "installed_at": now_iso(),
        "items": locked,
        "mirrors": {"rules": list(mirror_rules), "skills": list(mirror_skills)},
        "mirror_files": sorted(mirror_files),
    }
    write_json(paths.library_lock, lock)
    _write_index(paths, lock)
    return {
        "installed": {"rules": sum(1 for i in locked if i["kind"] == "rule"),
                      "skills": sum(1 for i in locked if i["kind"] == "skill")},
        "from": lock["source"],
        "index": paths.rel(paths.library / "INDEX.md"),
        "mirrored_files": len(mirror_files),
        "mirror_conflicts_left_untouched": conflicts,
    }


def _write_index(paths, lock: dict) -> None:
    source = lock["source"]
    commit = (source.get("commit") or "no commit")[:12] + (" plus local edits" if source.get("dirty") else "")
    lines = [
        "# Team rules and skills installed here",
        "",
        f"Copied from `{source['path']}` at {commit} on {lock['installed_at']}.",
        "They are maintained in that library: change them there, then run `qa.py library install --refresh`.",
        "",
        "| Name | Kind | Applies | What it is for | File |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in lock["items"]:
        if item["kind"] == "rule":
            applies = "always" if item["always"] else (", ".join(item["globs"]) or "when relevant")
            main = item["files"][0]
        else:
            applies = "when the task needs it"
            main = next((f for f in item["files"] if f.endswith("SKILL.md")), item["files"][0])
        description = clip(item["description"], 160).replace("|", "\\|")
        lines.append(f"| {item['name']} | {item['kind']} | {applies.replace('|', '/')} | {description} | `{main}` |")
    (paths.library / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- status


def status(paths) -> dict:
    lock = read_json(paths.library_lock)
    if not lock:
        found = pasted(paths)
        if found is None:
            return {"installed": 0, "note": "no team library is installed; the kit's own methods apply"}
        return {
            "installed": 0,
            "pasted_copy": paths.rel(found),
            "note": "a copy of a library is pasted here and nothing is installed from it yet: "
                    "`qa.py library scan` lists what it holds",
        }
    modified_here = []
    for item in lock["items"]:
        present = [f for f in item["files"] if (paths.root / f).is_file()]
        if len(present) != len(item["files"]):
            modified_here.append(item["name"])
    result = {
        "installed": len(lock["items"]),
        "source": lock["source"],
        "installed_at": lock["installed_at"],
        "missing_files_here": modified_here,
    }
    root = Path(lock["source"]["path"])
    if not root.is_absolute():
        root = paths.root / root
    if not root.is_dir():
        result["library_reachable"] = False
        return result
    result["library_reachable"] = True
    globs = lock["globs"]
    current = {(i["kind"], i["name"]): i for i in scan(root, globs["rules"], globs["skills"], globs["exclude"])}
    locked = {(i["kind"], i["name"]): i for i in lock["items"]}
    result["changed_in_library"] = sorted(
        name for (kind, name), item in locked.items()
        if (kind, name) in current and current[(kind, name)]["sha256"] != item["sha256"]
    )
    result["removed_from_library"] = sorted(name for (kind, name) in locked if (kind, name) not in current)
    result["not_installed"] = sorted(name for (kind, name) in current if (kind, name) not in locked)
    result["library_commit_now"] = git_head(root) if _versioned(root) else None
    result["up_to_date"] = not (result["changed_in_library"] or result["removed_from_library"] or modified_here)
    return result


# ---------------------------------------------------------------- what applies to a task


def reading_list(paths, config, level=None, task=None, suite=None) -> dict:
    """The files an agent reads before a piece of work: the kit's methods, then the team's rules."""
    if level and level not in LEVELS:
        raise QAError(f"--level must be one of {', '.join(LEVELS)}")
    if task and task not in TASKS:
        raise QAError(f"--task must be one of {', '.join(TASKS)}")
    levels = [level] if level else []
    stack = None
    if suite:
        entry = config.suite(suite)
        stack = entry.get("stack")
        if not levels:
            levels = list(entry.get("levels") or [])

    kit: list = []

    def add_kit(rel: str) -> None:
        if rel not in kit and (paths.kit / rel).is_file():
            kit.append(rel)

    for rel in KIT_METHODS["task"].get(task or "", []):
        add_kit(rel)
    for name in levels:
        for rel in KIT_METHODS["level"].get(name, []):
            add_kit(rel)
    if suite:
        # How this suite's runner behaves: needed whatever the task. A stack the kit has no file for gets the
        # general one, which says what to find out about a runner before trusting it.
        own = f"methods/stacks/{stack}.md" if stack else None
        add_kit(own if own and (paths.kit / own).is_file() else "methods/stacks/other.md")

    bindings = config["bindings"]
    names = list(bindings.get("always") or [])
    for name in levels:
        names += bindings.get("level", {}).get(name, [])
    if task:
        names += bindings.get("task", {}).get(task, [])
    names = list(dict.fromkeys(names))

    lock = read_json(paths.library_lock, {}) or {}
    by_name: dict = {}
    for item in lock.get("items", []):
        by_name.setdefault(item["name"], []).append(item)
    team, missing = [], []
    for name in names:
        matches = by_name.get(name)
        if not matches:
            missing.append(name)
            continue
        for item in matches:
            main = next((f for f in item["files"] if f.endswith("SKILL.md")), item["files"][0])
            team.append({"name": name, "kind": item["kind"], "path": main})
    bound = set(names)
    others = [item["name"] for item in lock.get("items", []) if item["name"] not in bound]
    result = {
        "read_first": [paths.rel(paths.kit / rel) for rel in kit],
        "then_team_rules": team,
    }
    if missing:
        result["bound_but_not_installed"] = missing
    if others:
        result["also_installed"] = {
            "count": len(others),
            "index": paths.rel(paths.library / "INDEX.md"),
            "note": "open one when the task touches its subject",
        }
    return result
