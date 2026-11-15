import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("verify_pytest_junit", Path(__file__).resolve().parents[1] / "scripts/qa/verify_pytest_junit.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def xml(passed=0, failed=0, errors=0, skipped=0):
    body = "".join(f'<testcase classname="t" name="p{i}"/>' for i in range(passed))
    body += "".join(f'<testcase classname="t" name="f{i}"><failure message="x"/></testcase>' for i in range(failed))
    body += "".join(f'<testcase classname="t" name="e{i}"><error message="x"/></testcase>' for i in range(errors))
    body += "".join(f'<testcase classname="t" name="s{i}"><skipped message="x"/></testcase>' for i in range(skipped))
    return f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


def test_exact_all_pass():
    assert mod.verify(xml(passed=28), 28)[0] == 0


def test_any_failure_error_or_skip_fails():
    assert mod.verify(xml(passed=27, failed=1), 28)[0] == 1
    assert mod.verify(xml(passed=27, errors=1), 28)[0] == 1
    assert mod.verify(xml(passed=27, skipped=1), 28)[0] == 1


def test_wrong_count_fails_even_when_all_pass():
    assert mod.verify(xml(passed=27), 28)[0] == 1
    assert mod.verify(xml(passed=29), 28)[0] == 1


def test_all_skipped_cannot_pass():
    assert mod.verify(xml(skipped=28), 28)[0] == 1


def test_invalid_or_empty_xml_fails():
    assert mod.verify("not xml", 28)[0] == 1
    assert mod.verify("<testsuites/>", 28)[0] == 1
