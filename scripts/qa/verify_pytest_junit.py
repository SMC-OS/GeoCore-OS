"""Fail-closed check of a pytest JUnit XML file: exact expected test count, all passed, zero failures/errors/skips.

Usage: verify_pytest_junit.py <junit.xml> --expect-passed N
Exit 0 only when the file parses, contains exactly N test cases, every one passed (no failure, error or skipped child).
"""
import sys
import xml.etree.ElementTree as ET


def verify(xml_text: str, expect_passed: int):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return 1, ["FAIL junit file is not valid XML"]
    cases = list(root.iter("testcase"))
    failed = [c for c in cases if c.find("failure") is not None]
    errored = [c for c in cases if c.find("error") is not None]
    skipped = [c for c in cases if c.find("skipped") is not None]
    passed = len(cases) - len(failed) - len(errored) - len(skipped)
    lines = [f"cases={len(cases)} passed={passed} failed={len(failed)} errors={len(errored)} skipped={len(skipped)} expected_passed={expect_passed}"]
    for c in failed + errored + skipped:
        lines.append("NOT PASSED " + (c.get("classname") or "") + "::" + (c.get("name") or ""))
    ok = len(cases) == expect_passed and passed == expect_passed and not failed and not errored and not skipped
    lines.append("PASS exact expected result" if ok else "FAIL result is not exactly the expected all-pass")
    return (0 if ok else 1), lines


def main(argv):
    if len(argv) < 4 or argv[2] != "--expect-passed":
        print("usage: verify_pytest_junit.py <junit.xml> --expect-passed N")
        return 1
    try:
        text = open(argv[1], encoding="utf-8").read()
    except OSError:
        print("FAIL junit file missing")
        return 1
    code, lines = verify(text, int(argv[3]))
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
