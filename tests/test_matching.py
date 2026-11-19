import pytest

from qa_agent.matching import Case, case_key, file_agrees, match_cases, name_agrees, path_parts, split_ref


def case(classname, name, file="", suite="", outcome="passed"):
    return Case(suite, classname, name, file, outcome, "", "")


def test_split_ref():
    assert split_ref("tests/api/test_orders.py::TestCart::test_empty") == ("tests/api/test_orders.py", ["TestCart", "test_empty"])
    assert split_ref("./e2e/login.spec.ts::signs in") == ("e2e/login.spec.ts", ["signs in"])
    for bad in ("no-separator", "::name", "file.py::"):
        with pytest.raises(ValueError):
            split_ref(bad)


def test_path_parts_drop_extensions():
    assert path_parts("tests/api/test_orders.py") == ["tests", "api", "test_orders"]
    assert path_parts("e2e/login.spec.ts") == ["e2e", "login"]
    assert path_parts("src/cart.test.tsx") == ["src", "cart"]


# One (reference, reported case) pair per runner, as each names its tests.
RUNNERS = [
    ("pytest function", "tests/api/test_orders.py::test_empty_cart",
     case("tests.api.test_orders", "test_empty_cart")),
    ("pytest class", "tests/api/test_orders.py::TestCart::test_empty_cart",
     case("tests.api.test_orders.TestCart", "test_empty_cart")),
    ("pytest class, short reference", "tests/api/test_orders.py::test_empty_cart",
     case("tests.api.test_orders.TestCart", "test_empty_cart")),
    ("pytest parametrized", "tests/api/test_orders.py::test_bad_quantity",
     case("tests.api.test_orders", "test_bad_quantity[-1]")),
    ("pytest-playwright browser", "tests/e2e/test_login.py::test_sign_in",
     case("tests.e2e.test_login", "test_sign_in[chromium]")),
    ("pytest run from a subfolder", "qa/tests/test_orders.py::test_empty_cart",
     case("tests.test_orders", "test_empty_cart")),
    ("playwright", "e2e/login.spec.ts::signs in with a valid password",
     case("login.spec.ts", "signs in with a valid password", suite="login.spec.ts")),
    ("playwright with describe", "e2e/login.spec.ts::Login::signs in",
     case("e2e/login.spec.ts", "Login › signs in")),
    ("playwright with describe, short reference", "e2e/login.spec.ts::signs in",
     case("e2e/login.spec.ts", "Login › signs in")),
    ("vitest", "src/cart.test.ts::cart::rejects an empty cart",
     case("src/cart.test.ts", "cart > rejects an empty cart")),
    ("jest", "src/cart.test.js::rejects an empty cart",
     case("cart rejects an empty cart", "cart rejects an empty cart", file="src/cart.test.js")),
    ("go", "orders/cart_test.go::TestEmptyCart",
     case("example.com/shop/orders", "TestEmptyCart")),
    ("go subtest", "orders/cart_test.go::TestEmptyCart",
     case("example.com/shop/orders", "TestEmptyCart/guest")),
]


@pytest.mark.parametrize("label, ref, reported", RUNNERS, ids=[r[0] for r in RUNNERS])
def test_reference_matches_the_case_each_runner_reports(label, ref, reported):
    assert match_cases(ref, [reported]) == [reported]


def test_same_name_in_another_file_does_not_match():
    reported = [
        case("tests.api.test_orders", "test_create"),
        case("tests.api.test_users", "test_create"),
        case("tests.unit.test_orders", "test_create"),
    ]
    assert match_cases("tests/api/test_orders.py::test_create", reported) == [reported[0]]
    assert match_cases("tests/api/test_missing.py::test_create", reported) == []


def test_exact_name_wins_over_a_longer_one():
    reported = [case("e2e/cart.spec.ts", "adds an item"), case("e2e/cart.spec.ts", "guest › adds an item")]
    assert match_cases("e2e/cart.spec.ts::adds an item", reported) == [reported[0]]
    assert match_cases("e2e/cart.spec.ts::guest::adds an item", reported) == [reported[1]]


def test_a_name_that_only_contains_the_reference_does_not_match():
    reported = [case("tests.test_a", "test_create_order_twice")]
    assert match_cases("tests/test_a.py::test_create_order", reported) == []


def test_group_names_must_appear():
    reported = case("tests.test_a.TestAdmin", "test_list")
    assert name_agrees(["TestAdmin", "test_list"], reported) == "exact"
    assert name_agrees(["TestGuest", "test_list"], reported) is None


def test_file_agrees_is_unknown_when_the_report_names_no_file():
    assert file_agrees("tests/test_a.py", case("", "test_x")) is None
    assert file_agrees("tests/test_a.py", case("Some Suite Title", "test_x")) is None
    assert file_agrees("tests/test_a.py", case("tests.test_b", "test_x")) is False


def test_case_key():
    assert case_key(case("tests.test_a", "test_x")) == "tests.test_a::test_x"
    assert case_key(case("", "test_x")) == "test_x"
