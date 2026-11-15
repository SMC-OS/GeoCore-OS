"""pin_candidate_api must succeed only for the exact requested commit and deployment id (stubbed Railway; no network)."""
import subprocess
from pathlib import Path

OPS = Path(__file__).resolve().parents[1] / "scripts/qa/qa_railway_ops.sh"
SHA = "f503041822923bd4b07a0bebaa6748ba71873740"


def run(latest_after, request_reply):
    script = f"""
set -u
source {OPS}
C=$(mktemp); echo 0 > "$C"
latest_deployment() {{ n=$(cat "$C"); echo $((n+1)) > "$C"; if [ "$n" -eq 0 ]; then echo "prev-id SUCCESS 0000"; else echo "{latest_after}"; fi; }}
gql() {{ echo '{request_reply}'; }}
wait_for_new_deployment() {{ return 0; }}
wait_for_stable_success() {{ return 0; }}
pin_candidate_api {SHA}
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_exact_commit_and_id_passes():
    r = run(f"new-id SUCCESS {SHA}", '{"data":{"serviceInstanceDeployV2":"new-id"}}')
    assert r.returncode == 0 and "PINNED API deployment new-id SUCCESS" in r.stdout


def test_branch_head_commit_is_refused():
    r = run("new-id SUCCESS 2b2f5141289267479d23a77780478982a33ed5f1", '{"data":{"serviceInstanceDeployV2":"new-id"}}')
    assert r.returncode != 0 and "PIN FAILED" in r.stdout


def test_a_different_deployment_id_is_refused():
    r = run(f"other-id SUCCESS {SHA}", '{"data":{"serviceInstanceDeployV2":"new-id"}}')
    assert r.returncode != 0


def test_railway_rejection_is_refused():
    r = run(f"new-id SUCCESS {SHA}", '{"errors":[{"message":"no"}]}')
    assert r.returncode != 0 and "rejected" in r.stdout


def test_non_sha_input_is_refused():
    r = subprocess.run(["bash", "-c", f"source {OPS}; pin_candidate_api main"], capture_output=True, text=True)
    assert r.returncode != 0 and "not a full commit sha" in r.stdout
