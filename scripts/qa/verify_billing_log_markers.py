"""Verify that billing-failure log markers exist for the synthetic events a QA run generated.

Usage: verify_billing_log_markers.py <events-manifest.json> <api-log.txt> [--require permanent,transient]

Exit codes: 0 = every required class had >=1 expected event and every expected event has its log marker;
            1 = FAIL (invalid manifest, missing marker, credential pattern in the log);
            2 = BLOCKED (a required class had no generated event, so the gate cannot be claimed).
Verified LOGGING is not operational ALERT DELIVERY: this proves the marker reached the API log only.
"""
import json
import re
import sys

MARKERS = {"permanent": "billing_webhook_permanent_failure", "transient": "billing_webhook_transient_failure"}
CREDENTIALS = re.compile(r"(sk|rk)_(test|live)_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,}")
EVENT_ID = re.compile(r"^evt_geocore_qa_[0-9a-f]{32}$")


def verify(manifest_text: str, log_text: str, required=("permanent", "transient")):
    """Return (exit_code, lines)."""
    lines = []
    try:
        manifest = json.loads(manifest_text)
    except ValueError:
        return 1, ["FAIL manifest is not valid JSON"]
    if not isinstance(manifest, list):
        return 1, ["FAIL manifest is not a list"]
    for item in manifest:
        if not (isinstance(item, dict) and item.get("kind") in MARKERS and EVENT_ID.match(str(item.get("event_id", "")))):
            return 1, ["FAIL manifest contains an invalid entry"]
    if CREDENTIALS.search(log_text):
        return 1, ["FAIL credential pattern present in the retained API log"]
    code = 0
    for kind in required:
        events = [i["event_id"] for i in manifest if i["kind"] == kind]
        if not events:
            lines.append(f"BLOCKED no {kind}-failure event was generated; the {kind} logging gate is not verified")
            code = max(code, 2) if code != 1 else 1
            continue
        for eid in events:
            if f"{MARKERS[kind]} event_id={eid}" in log_text:
                lines.append(f"PASS {kind} marker {MARKERS[kind]} logged for {eid}")
            else:
                lines.append(f"FAIL {kind} marker {MARKERS[kind]} not found for {eid}")
                code = 1
    return code, lines


def main(argv):
    if len(argv) < 3:
        print("usage: verify_billing_log_markers.py <manifest> <log> [--require a,b]")
        return 1
    required = ("permanent", "transient")
    if "--require" in argv:
        required = tuple(argv[argv.index("--require") + 1].split(","))
    try:
        manifest_text = open(argv[1]).read()
        log_text = open(argv[2], errors="replace").read()
    except OSError:
        print("FAIL manifest or log file missing")
        return 1
    code, lines = verify(manifest_text, log_text, required)
    print("\n".join(lines))
    print("NOTE logging verified here is NOT operational alert delivery to a monitored owner")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
