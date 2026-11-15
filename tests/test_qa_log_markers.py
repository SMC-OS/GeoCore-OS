import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("verify_billing_log_markers", Path(__file__).resolve().parents[1] / "scripts/qa/verify_billing_log_markers.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

P = "evt_geocore_qa_" + "a" * 32
T = "evt_geocore_qa_" + "b" * 32
LOG = f"x billing_webhook_permanent_failure event_id={P} y\nbilling_webhook_transient_failure event_id={T}\n"
FULL = json.dumps([{"kind": "permanent", "event_id": P}, {"kind": "transient", "event_id": T}])


def test_both_classes_with_markers_pass():
    code, lines = mod.verify(FULL, LOG)
    assert code == 0 and all(line.startswith("PASS") for line in lines)


def test_empty_manifest_never_passes():
    code, lines = mod.verify("[]", LOG)
    assert code == 2 and all(line.startswith("BLOCKED") for line in lines)


def test_missing_class_is_blocked_not_pass():
    code, _ = mod.verify(json.dumps([{"kind": "permanent", "event_id": P}]), LOG)
    assert code == 2


def test_missing_marker_fails():
    code, lines = mod.verify(FULL, "nothing relevant\n")
    assert code == 1 and any(line.startswith("FAIL") for line in lines)


def test_invalid_manifest_fails():
    assert mod.verify("not json", LOG)[0] == 1
    assert mod.verify('{"a":1}', LOG)[0] == 1
    assert mod.verify(json.dumps([{"kind": "permanent", "event_id": "evt_real"}]), LOG)[0] == 1


def test_credential_pattern_fails_even_with_markers():
    assert mod.verify(FULL, LOG + "key sk_test_" + "A" * 20)[0] == 1
    assert mod.verify(FULL, LOG + "whsec_" + "B" * 20)[0] == 1


def test_failure_outranks_blocked():
    code, _ = mod.verify(json.dumps([{"kind": "permanent", "event_id": P}]), "no marker")
    assert code == 1
