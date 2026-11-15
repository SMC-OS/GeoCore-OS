"""Before/after measurement of webhook-driven database-connection pinning during a Stripe stall (LOCAL, scaled).

A local fake Stripe server never answers. A burst of webhook events for ONE tenant (Stripe sends several per change)
is delivered against a deliberately small connection pool, while an unrelated query is probed every 0.25 s.
Reports: max Stripe call duration, max connection hold per webhook, max tenant-lock hold, probe failures/latency,
and recovery once Stripe answers. All times are scaled down (about 1/10 of production) so the run is short.
Variants: before = long read timeout and 2 retries (stand-in for the SDK default of 80 s x 3), no lock wait bound;
read-bound = short timeouts, 1 retry; read+lock = also a bounded lock wait.
Usage: DATABASE_URL=... python scripts/qa/measure_webhook_starvation.py [before|read-bound|read-lock|all]
"""
import os, sys, threading, time, uuid
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from concurrent.futures import ThreadPoolExecutor

import stripe
from sqlalchemy import create_engine, delete, text

import app.billing.service as svc
from app.billing.service import BillingService
from app.core.config import settings
from app.database.database import SessionLocal
from app.database.models import Subscription, Tenant
from app.tenants.models import TenantCreate
from app.tenants.service import tenant_service
from tests.test_billing_bounded_reads import FakeStripe, SECRET, _deliver, _event, _subscription

VARIANTS = {  # connect s, read s, retries, lock wait s
    "before": (4.0, 4.0, 2, None),
    "read-bound": (0.5, 1.0, 1, None),
    "read-lock": (0.5, 1.0, 1, 0.5),
}


def run(name, burst=6, pool=4, pool_timeout=3.0):
    connect, read, retries, lock_wait = VARIANTS[name]
    settings.stripe_secret_key, settings.stripe_webhook_secret = "sk_test_unit", SECRET
    svc.STRIPE_WEBHOOK_LOCK_WAIT_SECONDS = lock_wait
    fake = FakeStripe()
    sub_id = "sub_meas_" + uuid.uuid4().hex[:8]
    db = SessionLocal()
    tenant = tenant_service.create(db, TenantCreate(name="Measure " + uuid.uuid4().hex[:6]))
    db.close()
    fake.subs[sub_id] = _subscription(tenant, sub_id, "active")
    fake.mode.update(kind="ok", delay=60.0)  # stalled Stripe
    svc._build_bounded_read_client = lambda m, k, **e: m.StripeClient(
        k, http_client=m.RequestsClient(timeout=(connect, read)), max_network_retries=retries, base_addresses={"api": fake.base})
    engine = create_engine(settings.database_url, pool_size=pool, max_overflow=0, pool_timeout=pool_timeout)
    original = SessionLocal.kw["bind"]; SessionLocal.configure(bind=engine)
    stripe_calls, holds, lock_holds, probes = [], [], [], []
    orig_read, orig_lock = BillingService._read_subscription, BillingService._lock_subscription
    state = threading.local()

    def timed_read(self, s, sid):
        t = time.time()
        try:
            return orig_read(self, s, sid)
        finally:
            stripe_calls.append(time.time() - t)

    def timed_lock(db, tid, **kw):
        r = orig_lock(db, tid, **kw); state.lock_at = time.time(); return r

    BillingService._read_subscription = timed_read
    BillingService._lock_subscription = staticmethod(timed_lock)
    stop = threading.Event()

    def probe():
        while not stop.is_set():
            t = time.time()
            try:
                with SessionLocal() as d:
                    d.execute(text("select 1"))
                probes.append(("ok", time.time() - t))
            except Exception as e:
                probes.append((type(e).__name__, time.time() - t))
            time.sleep(0.25)

    def deliver(i):
        state.lock_at = None
        t0 = time.time()
        try:
            _deliver(_event(tenant, sub_id, "active", 1000 + i))
            outcome = "ok"
        except Exception as e:
            outcome = type(e).__name__
        end = time.time()
        holds.append((end - t0, outcome))
        if state.lock_at:
            lock_holds.append(end - state.lock_at)

    t_start = time.time()
    pt = threading.Thread(target=probe, daemon=True); pt.start()
    with ThreadPoolExecutor(max_workers=burst) as pool_:
        list(pool_.map(deliver, range(burst)))
    drain = time.time() - t_start
    stop.set(); pt.join()
    fake.mode.update(delay=0.0)                       # Stripe recovers
    rt = time.time()
    try:
        _deliver(_event(tenant, sub_id, "active", 5000)); recovered = f"ok in {time.time() - rt:.2f}s"
    except Exception as e:
        recovered = f"FAILED {type(e).__name__}"
    bad = [p for p in probes if p[0] != "ok"]
    print(f"{name:11s} burst={burst} pool={pool}/{pool_timeout:.0f}s | max Stripe call {max(stripe_calls, default=0):5.2f}s | "
          f"max connection hold {max((h[0] for h in holds), default=0):6.2f}s | max tenant-lock hold {max(lock_holds, default=0):6.2f}s | "
          f"drain {drain:6.2f}s | unrelated probes {len(probes)}: failed {len(bad)} max-latency {max((p[1] for p in probes), default=0):5.2f}s | "
          f"webhook outcomes {sorted(set(h[1] for h in holds))} | recovery {recovered}", flush=True)
    BillingService._read_subscription, BillingService._lock_subscription = orig_read, orig_lock
    SessionLocal.configure(bind=original); engine.dispose(); fake.close()
    d = SessionLocal()
    d.execute(text("delete from activity_log where tenant_id = :t"), {"t": tenant.id})
    d.execute(delete(Subscription).where(Subscription.tenant_id == tenant.id)); d.execute(delete(Tenant).where(Tenant.id == tenant.id)); d.commit(); d.close()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for v in (VARIANTS if which == "all" else [which]):
        run(v)
