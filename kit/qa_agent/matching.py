"""Match a test reference kept in the catalog against the test cases a JUnit report names.

A reference is `path/to/file::name`, optionally `path::Group::name`. Runners name
the same test differently, so matching looks at the file and the name separately:

    pytest       classname="tests.api.test_orders.TestCart"  name="test_empty[chromium]"
    Playwright   classname="api/orders.spec.ts"              name="cart › rejects an empty cart"
    Vitest       classname="src/cart.test.ts"                name="cart > rejects an empty cart"
    Go           classname="example.com/shop/orders"         name="TestEmptyCart/guest"
"""

from __future__ import annotations

import re
from collections import namedtuple

from .util import posix

Case = namedtuple("Case", "suite classname name file outcome message detail")

CODE_EXT = re.compile(r"\.(py|ts|tsx|js|jsx|mjs|cjs|mts|cts|go|java|kt|rb|cs|php|rs|swift|dart)$", re.I)
ROLE_EXT = re.compile(r"\.(spec|test|tests|api|e2e|cy|it)$", re.I)
PARAMS = re.compile(r"\[.*\]$", re.S)
SEPARATORS = (" › ", " > ", " ", "::")


def case_key(case: Case) -> str:
    """How a reported test is named in the run history and in summaries."""
    return f"{case.classname}::{case.name}" if case.classname else case.name


def split_ref(ref: str):
    """`a/b.py::Group::name` as ("a/b.py", ["Group", "name"])."""
    path, sep, rest = ref.partition("::")
    if not sep or not path.strip() or not rest.strip():
        raise ValueError(f"'{ref}' is not a test reference; write it as path/to/file::test name")
    return posix(path.strip()), [part.strip() for part in rest.split("::") if part.strip()]


def path_parts(path: str) -> list:
    """Folder and file names of a test file, without its extensions."""
    text = posix(path)
    text = CODE_EXT.sub("", text)
    text = ROLE_EXT.sub("", text)
    return [part for part in text.split("/") if part]


def _looks_like_file(text: str) -> bool:
    if "/" in text or "\\" in text or CODE_EXT.search(text):
        return True
    return bool(re.match(r"^[\w$]+(\.[\w$]+)+$", text))  # a dotted module or package name


def _source_parts(text: str) -> list:
    if "/" in text or "\\" in text or CODE_EXT.search(text):
        return path_parts(text)
    return [part for part in text.split(".") if part]


def _tail_agrees(left: list, right: list) -> bool:
    """Compare two component lists from the end; true when the shorter one is a tail of the other."""
    compared = 0
    for a, b in zip(reversed(left), reversed(right)):
        if a != b:
            return False
        compared += 1
    return compared > 0


def file_agrees(ref_path: str, case: Case):
    """True or False when the report says which file a case is in, None when it does not say."""
    ref = path_parts(ref_path)
    if not ref:
        return None
    stem, folder = ref[-1], ref[:-1]
    informative = False
    for source in (case.file, case.classname, case.suite):
        if not source or not _looks_like_file(source):
            continue
        informative = True
        parts = _source_parts(source)
        if stem in parts:
            # Align on the file name; whatever follows it is a class or group name.
            index = len(parts) - 1 - parts[::-1].index(stem)
            if _tail_agrees(parts[: index + 1], ref):
                return True
        elif folder and _tail_agrees(parts, folder):
            return True  # runners that report the package (folder), not the file
    return False if informative else None


def _name_forms(names: list) -> set:
    forms = {names[-1]}
    if len(names) > 1:
        forms.update(sep.join(names) for sep in SEPARATORS)
    return forms


def name_agrees(names: list, case: Case):
    """'exact', 'suffix' or None: how well a reference's name fits a reported case."""
    reported = case.name or ""
    bare = PARAMS.sub("", reported).strip()
    head = bare.split("/")[0]  # Go subtests: TestName/subtest
    forms = _name_forms(names)
    exact = reported in forms or bare in forms or head in forms
    context = f"{case.classname or ''} {reported}"
    if not all(group in context for group in names[:-1]):
        return None
    if exact:
        return "exact"
    last = names[-1]
    if any(bare.endswith(sep + last) for sep in (" › ", " > ", " ")):
        return "suffix"
    return None


def match_cases(ref: str, cases) -> list:
    """The reported cases a reference points at. Exact name matches win over suffix matches."""
    try:
        ref_path, names = split_ref(ref)
    except ValueError:
        return []
    exact, suffix = [], []
    for case in cases:
        kind = name_agrees(names, case)  # the cheap check first
        if kind is None or file_agrees(ref_path, case) is False:
            continue
        if kind == "exact":
            exact.append(case)
        else:
            suffix.append(case)
    return exact or suffix
