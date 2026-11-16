"""Replacement-infrastructure restore + verification (runs INSIDE the disposable application container; synthetic data only).

Inputs (environment, set by the disposable provisioning workflow; no source credential and no source address is ever present):
  RECOVERY_BUNDLE_PART_000.. : base64 chunks of the captured tar.gz bundle      RECOVERY_BUNDLE_SHA256 / RECOVERY_BUNDLE_BYTES : outer integrity
  DATABASE_URL               : the NEW independent PostgreSQL 18 (private network)  UPLOAD_DIR : the NEW independent private-upload volume
  RESTORE_SRC                : unpacked recovery tooling (scripts/qa, scripts/recovery, scripts/disposable)
Everything printed starts with "DRILL|". Exit code 0 only when every check passed.
"""
import base64, hashlib, io, json, os, re, shutil, sys, tarfile, time
from pathlib import Path

SRC = Path(os.environ["RESTORE_SRC"])
os.environ.setdefault("RECOVERY_DIR", str(SRC / "scripts" / "recovery"))
sys.path.insert(0, str(SRC / "scripts" / "qa"))
import qa_recovery_drill as d  # noqa: E402

say, check, cf, rub = d.say, d.check, d.cf, d.rub


def assemble():
    parts = sorted(k for k in os.environ if re.fullmatch(r"RECOVERY_BUNDLE_PART_\d{3}", k))
    blob = base64.b64decode("".join(os.environ[k] for k in parts)) if parts else b""
    check("bundle parts present", len(parts) > 0, f"{len(parts)} parts")
    check("bundle byte count equals the captured size", len(blob) == int(os.environ.get("RECOVERY_BUNDLE_BYTES", "-1")), f"{len(blob)}")
    check("bundle SHA-256 equals the captured digest", hashlib.sha256(blob).hexdigest() == os.environ.get("RECOVERY_BUNDLE_SHA256", ""))
    return blob


def main():
    t_start = time.time()
    out = Path("/tmp/recovery-work")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    blob = assemble()
    if not d.ok:
        say("FAILED bundle integrity")
        return 1
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = {m.name for m in tf.getmembers()}
        check("bundle holds exactly dump, uploads archive, manifest and integrity record", names == {"qa.dump", "uploads.tar", "uploads.manifest.json", "integrity.json"}, str(sorted(names)))
        tf.extractall(out, filter="data")
    integ = json.loads((out / "integrity.json").read_text())
    for key in ("dump", "uploads_archive", "uploads_manifest"):
        rec = integ[key]
        check(f"{rec['name']} matches the integrity record (size + SHA-256)", (out / rec["name"]).stat().st_size == rec["bytes"] and hashlib.sha256((out / rec["name"]).read_bytes()).hexdigest() == rec["sha256"])
    check("integrity record is a matched capture (T0 == T1)", integ["t0_equals_t1"] is True)
    expected = integ["expected_migration"]
    check(f"captured migration revision is {d.EXPECTED_MIGRATION}", integ["db_fingerprint_t1"]["alembic_version"] == expected == d.EXPECTED_MIGRATION)
    say(f"recovery point {integ['recovery_point']} captured_at {integ['captured_at_utc']}")

    import psycopg
    dsn = d.dsn_plain()
    major = d.server_major()
    say(f"replacement server major version {major}")
    with psycopg.connect(dsn) as c:
        pre_tables = c.execute("select count(*) from information_schema.tables where table_schema='public'").fetchone()[0]
    check("replacement database starts empty (no tables)", pre_tables == 0, f"{pre_tables} tables")
    bindir, env_extra = d.fetch_pg_client(major)
    from psycopg.conninfo import conninfo_to_dict
    p = conninfo_to_dict(dsn)
    penv = {**env_extra, "PGHOST": p["host"], "PGPORT": str(p.get("port", 5432)), "PGUSER": p["user"], "PGPASSWORD": p.get("password", ""), "PGDATABASE": p["dbname"]}
    r0 = time.time()
    rr = d.run([str(bindir / "pg_restore"), "--no-owner", "--exit-on-error", f"--dbname={p['dbname']}", str(out / "qa.dump")], penv)
    check("pg_restore into the independent PostgreSQL succeeded", rr.returncode == 0, rr.stderr.strip()[:200])
    db_s = time.time() - r0

    uploads_root = Path(os.environ["UPLOAD_DIR"])
    scratch = uploads_root / "restored-private-uploads"
    scratch.mkdir(parents=True, exist_ok=False)
    u0 = time.time()
    count, _digest, rp = rub.restore_bundle(out / "uploads.tar", out / "uploads.manifest.json", scratch)
    up_s = time.time() - u0
    say(f"uploads restored files={count} recovery_point={rp}")

    rest = cf.db_fingerprint(dsn)
    problems = cf.diff_fingerprints(integ["db_fingerprint_t1"], rest)
    check("restored database equals the captured fingerprint: every table (rows + content hash), sequences, alembic revision", not problems, "; ".join(problems[:3]))
    check(f"restored migration revision is exactly {expected}", rest["alembic_version"] == expected, rest["alembic_version"])
    check("restored row total equals the capture", rest["row_total"] == integ["db_fingerprint_t1"]["row_total"], f"{rest['row_total']}")
    upr = cf.uploads_manifest(scratch)
    uprob = cf.diff_manifests(integ["uploads_manifest_t1"], upr)
    check("every restored upload is byte-identical to the captured manifest (SHA-256, size, path)", not uprob and upr["file_count"] == integ["uploads_manifest_t1"]["file_count"], "; ".join(uprob[:3]))
    tc = d.tenant_counts(dsn)
    check("per-tenant row counts equal the capture", tc == integ["tenant_counts"] and len(tc) > 0, f"{len(tc)} vs {len(integ['tenant_counts'])} keys")
    with psycopg.connect(dsn) as c:
        seqs = c.execute("select count(*) from pg_sequences where schemaname='public'").fetchone()[0]
    say(f"sequences present={seqs} (values compared inside the database fingerprint)")
    recovered_at = time.time()
    d.restored_app_checks(dsn, scratch, upr)
    say(f"TIMINGS database_restore_seconds={db_s:.1f} uploads_restore_seconds={up_s:.1f} verification_seconds={time.time() - recovered_at:.1f} task_total_seconds={time.time() - t_start:.1f}")
    say(f"RECOVERY_POINT captured_at={integ['captured_at_utc']} (logical dump: data written after this instant is not recoverable)")
    shutil.rmtree(out, ignore_errors=True)
    say("scratch files removed")
    say("DONE" if d.ok else "FAILED")
    return 0 if d.ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        say(f"FAILED unexpected {type(exc).__name__}: {str(exc)[:200]}")
        sys.exit(1)
