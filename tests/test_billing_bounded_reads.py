"""Bounded Stripe reads during webhook reconciliation (regression for the lock/connection-starvation defect).

The REAL Stripe SDK and the REAL bounded read client talk to a LOCAL fake Stripe HTTP server, so timeouts, 429/5xx and
retries are genuine HTTP behaviour. Webhook events are signed with a test secret by the test (locally signed, not
Stripe-originated). Timeouts are scaled down (connect 1 s / read 1 s) so the suite stays fast; the production values
are asserted separately.
"""
import hashlib
import hmac
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import stripe
from sqlalchemy import create_engine, delete, text

import app.billing.service as svc
from app.billing.service import BillingError, BillingService
from app.core.config import settings
from app.database import crud
from app.database.database import SessionLocal
from app.database.models import ProcessedStripeEvent, Subscription, Tenant
from app.tenants.models import TenantCreate
from app.tenants.service import tenant_service
from tests.test_billing import owner_and_staff  # noqa: F401  (fixture)

SECRET = "whsec_unit_test_secret"


class _Unique(dict):
    """Unique Stripe subscription ids per run so leftovers from an aborted run can never collide."""
    def __missing__(self, key):
        self[key] = f"{key}_{uuid.uuid4().hex[:10]}"
        return self[key]


SUB = _Unique()


class FakeStripe:
    def __init__(self):
        self.subs: dict[str, dict] = {}      # sub_id -> subscription object
        self.mode = {"kind": "ok", "delay": 0.0}
        self.hits: list[tuple[float, str]] = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                sub_id = self.path.rstrip("/").split("/")[-1].split("?")[0]
                with outer.lock:
                    outer.hits.append((time.time(), sub_id))
                kind, delay = outer.mode["kind"], outer.mode["delay"]
                if delay:
                    time.sleep(delay)
                if kind == "429":
                    code, body = 429, {"error": {"type": "invalid_request_error", "code": "rate_limit", "message": "slow"}}
                elif kind == "503":
                    code, body = 503, {"error": {"type": "api_error", "message": "temporary"}}
                else:
                    sub = outer.subs.get(sub_id)
                    code, body = (200, sub) if sub else (404, {"error": {"type": "invalid_request_error", "message": "no such subscription"}})
                data = json.dumps(body).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    if code == 429:
                        self.send_header("Stripe-Should-Retry", "true")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake(monkeypatch):
    f = FakeStripe()
    orig = svc._build_bounded_read_client
    monkeypatch.setattr(svc, "STRIPE_READ_CONNECT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(svc, "STRIPE_READ_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(svc, "_build_bounded_read_client",
                        lambda module, key, **extra: orig(module, key, base_addresses={"api": f.base}, **extra))
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_unit")
    monkeypatch.setattr(settings, "stripe_webhook_secret", SECRET)
    yield f
    f.close()


def _subscription(tenant, sub_id, status, created=100):
    return {"id": sub_id, "object": "subscription", "customer": "cus_unit", "status": status, "created": created,
            "cancel_at_period_end": False, "metadata": {"tenant_id": str(tenant.id), "plan": "pro", "billing_period": "monthly"}}


def _signed(event):
    payload = json.dumps(event, separators=(",", ":")).encode()
    ts = int(time.time())
    sig = hmac.new(SECRET.encode(), str(ts).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return payload, f"t={ts},v1={sig}"


def _event(tenant, sub_id, status, created, event_id=None):
    return {"id": event_id or "evt_bounded_" + uuid.uuid4().hex, "object": "event", "type": "customer.subscription.updated",
            "created": created, "livemode": False,
            "data": {"object": {"id": sub_id, "object": "subscription", "status": status,
                                "metadata": {"tenant_id": str(tenant.id), "plan": "pro", "billing_period": "monthly"}}}}


def _deliver(event, service=None):
    service = service or BillingService(stripe)
    payload, sig = _signed(event)
    db = SessionLocal()
    t0 = time.time()
    try:
        service.handle_webhook(db, payload, sig)
        return time.time() - t0
    finally:
        db.close()


def _status(tenant):
    db = SessionLocal()
    try:
        row = crud.get_subscription_by_tenant_id(db, tenant.id)
        return row.status if row else None
    finally:
        db.close()


def _marker(event_id):
    db = SessionLocal()
    try:
        return db.get(ProcessedStripeEvent, event_id) is not None
    finally:
        db.close()


def test_production_bounds_are_the_reviewed_values():
    # No fixture here, so the module constants are the real production values.
    assert (svc.STRIPE_READ_CONNECT_TIMEOUT_SECONDS, svc.STRIPE_READ_TIMEOUT_SECONDS, svc.STRIPE_READ_MAX_NETWORK_RETRIES) == (5.0, 10.0, 1)
    client = svc._build_bounded_read_client(stripe, "sk_test_unit")
    assert client._requestor._options.max_network_retries == 1


# 1. connection failure
def test_connection_failure_rolls_back_and_a_retry_succeeds(owner_and_staff, fake, monkeypatch):
    tenant, _, _ = owner_and_staff
    event = _event(tenant, SUB["sub_conn"], "active", 1000)
    before = _status(tenant)
    fake.subs[SUB["sub_conn"]] = _subscription(tenant, SUB["sub_conn"], "active")
    good = fake.base
    fake.base = "http://127.0.0.1:1"      # refused: nothing listens
    monkeypatch.setattr(svc, "_build_bounded_read_client",
                        lambda module, key, **extra: stripe.StripeClient(key, http_client=stripe.RequestsClient(timeout=(1, 1)),
                                                                         max_network_retries=1, base_addresses={"api": "http://127.0.0.1:1"}))
    with pytest.raises(stripe.APIConnectionError):
        _deliver(event)
    assert not _marker(event["id"]) and _status(tenant) == before
    # Stripe is reachable again: the same event is redelivered
    orig_fake = svc._build_bounded_read_client
    monkeypatch.setattr(svc, "_build_bounded_read_client",
                        lambda module, key, **extra: stripe.StripeClient(key, http_client=stripe.RequestsClient(timeout=(1, 1)),
                                                                         max_network_retries=1, base_addresses={"api": good}))
    _deliver(event)
    assert _marker(event["id"]) and _status(tenant) == "active"


# 2. read timeout, bounded
def test_read_timeout_is_bounded_and_leaves_no_trace(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    fake.subs[SUB["sub_slow"]] = _subscription(tenant, SUB["sub_slow"], "active")
    fake.mode.update(kind="ok", delay=3.0)
    event = _event(tenant, SUB["sub_slow"], "active", 1000)
    before = _status(tenant)
    t0 = time.time()
    with pytest.raises(stripe.APIConnectionError):
        _deliver(event)
    elapsed = time.time() - t0
    assert elapsed < 4.5, f"read took {elapsed:.1f}s; bound is 2 attempts x 1 s + back-off"
    assert len(fake.hits) == 2                      # one retry, no more
    assert not _marker(event["id"]) and _status(tenant) == before
    # recovery once Stripe answers
    fake.mode.update(delay=0.0)
    _deliver(event)
    assert _marker(event["id"]) and _status(tenant) == "active"


# 3. rate limiting
def test_rate_limit_retries_once_then_fails_then_recovers(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    fake.subs[SUB["sub_rl"]] = _subscription(tenant, SUB["sub_rl"], "active")
    fake.mode.update(kind="429")
    event = _event(tenant, SUB["sub_rl"], "active", 1000)
    t0 = time.time()
    with pytest.raises(stripe.RateLimitError):
        _deliver(event)
    assert time.time() - t0 < 3 and len(fake.hits) == 2
    assert not _marker(event["id"])
    fake.mode.update(kind="ok")
    _deliver(event)
    assert _status(tenant) == "active"


# 4. temporary server failure
def test_server_error_is_not_acknowledged(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    fake.subs[SUB["sub_5xx"]] = _subscription(tenant, SUB["sub_5xx"], "active")
    fake.mode.update(kind="503")
    event = _event(tenant, SUB["sub_5xx"], "active", 1000)
    before = _status(tenant)
    with pytest.raises(stripe.APIError):
        _deliver(event)
    assert not _marker(event["id"]) and _status(tenant) == before


# 5 + 6. redelivery after failure, then duplicate after success (no second Stripe read)
def test_redelivery_then_duplicate_is_idempotent(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    fake.subs[SUB["sub_dup"]] = _subscription(tenant, SUB["sub_dup"], "active")
    event = _event(tenant, SUB["sub_dup"], "active", 1000)
    fake.mode.update(kind="503")
    with pytest.raises(stripe.APIError):
        _deliver(event)
    fake.mode.update(kind="ok")
    _deliver(event)
    reads_after_success = len(fake.hits)
    _deliver(event)
    _deliver(event)
    assert len(fake.hits) == reads_after_success      # duplicates never touch Stripe
    db = SessionLocal()
    try:
        assert db.query(Subscription).filter(Subscription.tenant_id == tenant.id).count() == 1
    finally:
        db.close()
    assert _status(tenant) == "active"


# 7. concurrent events, same tenant: serialised, both applied, authoritative state wins
def test_concurrent_events_same_tenant_serialise(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    fake.subs[SUB["sub_same"]] = _subscription(tenant, SUB["sub_same"], "active")
    fake.mode.update(delay=0.5)
    events = [_event(tenant, SUB["sub_same"], s, 1000 + i) for i, s in enumerate(("trialing", "active", "past_due"))]
    fake.subs[SUB["sub_same"]]["status"] = "active"
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(_deliver, events))
    assert all(_marker(e["id"]) for e in events)
    assert _status(tenant) == "active"                # Stripe's current state, not the last event delivered


def _second_tenant():
    db = SessionLocal()
    try:
        return tenant_service.create(db, TenantCreate(name="Pytest Bounded Reads Second " + uuid.uuid4().hex[:6]))
    finally:
        db.close()


def _drop_tenant(tenant):
    db = SessionLocal()
    try:
        db.execute(text("delete from activity_log where tenant_id = :t"), {"t": tenant.id})
        db.execute(delete(Subscription).where(Subscription.tenant_id == tenant.id))
        db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        db.commit()
    finally:
        db.close()


# 8. concurrent events, different tenants: not serialised against each other
def test_concurrent_events_different_tenants_run_in_parallel(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    other = _second_tenant()
    try:
        fake.subs[SUB["sub_a"]] = _subscription(tenant, SUB["sub_a"], "active")
        fake.subs[SUB["sub_b"]] = _subscription(other, SUB["sub_b"], "active")
        fake.mode.update(delay=0.8)
        events = [_event(tenant, SUB["sub_a"], "active", 1000), _event(other, SUB["sub_b"], "active", 1000)]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(_deliver, events))
        assert time.time() - t0 < 1.5                 # two 0.8 s reads overlapped (serial would be >= 1.6 s)
        assert _status(tenant) == "active" and _status(other) == "active"
    finally:
        _drop_tenant(other)


# 9. unrelated requests keep getting connections while Stripe is slow
def test_slow_stripe_does_not_starve_unrelated_requests(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    fake.subs[SUB["sub_pool"]] = _subscription(tenant, SUB["sub_pool"], "active")
    fake.mode.update(delay=3.0)                        # every read stalls past the 1 s read timeout
    small = create_engine(settings.database_url, pool_size=3, max_overflow=0, pool_timeout=10)
    original = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=small)
    try:
        events = [_event(tenant, SUB["sub_pool"], "active", 1000 + i) for i in range(3)]
        holds: list[float] = []

        def deliver(event):
            try:
                holds.append(_deliver(event))
            except stripe.APIConnectionError:
                pass

        def unrelated():
            t0 = time.time()
            with SessionLocal() as db:
                db.execute(text("select 1"))
            return time.time() - t0

        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(deliver, e) for e in events]
            time.sleep(0.5)
            latency = pool.submit(unrelated).result()
            for f in futs:
                f.result()
        # Before the correction the same scenario held connections for the SDK default (up to ~240 s per read).
        assert latency < 6.0, f"unrelated request waited {latency:.1f}s"
    finally:
        SessionLocal.configure(bind=original)
        small.dispose()


# 10. cross-tenant linkage conflict is refused and writes nothing
def test_cross_tenant_linkage_conflict_is_refused(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    other = _second_tenant()
    try:
        fake.subs[SUB["sub_conf"]] = _subscription(tenant, SUB["sub_conf"], "active")
        _deliver(_event(tenant, SUB["sub_conf"], "active", 1000))
        # Stripe now claims the subscription belongs to another tenant: never relink silently
        fake.subs[SUB["sub_conf"]] = _subscription(other, SUB["sub_conf"], "active")
        event = _event(tenant, SUB["sub_conf"], "active", 2000)
        with pytest.raises(BillingError):
            _deliver(event)
        assert not _marker(event["id"])
        assert _status(other) is None and _status(tenant) == "active"
    finally:
        _drop_tenant(other)


# 11. out-of-order delivery still resolves to Stripe's current state
def test_out_of_order_events_resolve_to_authoritative_state(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    fake.subs[SUB["sub_ooo"]] = _subscription(tenant, SUB["sub_ooo"], "active")
    _deliver(_event(tenant, SUB["sub_ooo"], "active", 3000))
    _deliver(_event(tenant, SUB["sub_ooo"], "trialing", 1000))   # old event arrives late
    assert _status(tenant) == "active"


def test_real_sdk_event_exposes_its_created_time():
    """Regression: stripe.Event is not a dict, so `.get('created')` silently returned None and every real webhook
    skipped authoritative reconciliation (only dict fixtures reached it)."""
    event = stripe.Webhook.construct_event(*_signed(_event(type("T", (), {"id": uuid.uuid4()})(), "sub_t", "active", 1234)), SECRET)
    assert not isinstance(event, dict)
    assert BillingService._event_time(event).timestamp() == 1234
    assert BillingService._event_time({"created": 5}).timestamp() == 5
    assert BillingService._event_time({}) is None
    assert BillingService._event_time({"created": "x"}) is None


# 12. waiting for the tenant lock is bounded too (a waiter holds a connection while it waits)
def test_waiting_for_the_tenant_lock_is_bounded(owner_and_staff, fake, monkeypatch):
    tenant, _, _ = owner_and_staff
    sub = SUB["sub_lockwait"]
    fake.subs[sub] = _subscription(tenant, sub, "active")
    fake.mode.update(delay=3.0)                       # stalled Stripe: the first event holds the lock ~2 s (1 s x 2 attempts)
    monkeypatch.setattr(svc, "STRIPE_WEBHOOK_LOCK_WAIT_SECONDS", 0.5)
    events = [_event(tenant, sub, "active", 1000 + i) for i in range(4)]
    outcomes, durations = [], []

    def go(event):
        t0 = time.time()
        try:
            _deliver(event)
            outcomes.append("ok")
        except Exception as e:
            outcomes.append(type(e).__name__)
        durations.append(time.time() - t0)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(go, events))
    assert max(durations) < 3.5, f"a waiter was held {max(durations):.1f}s"
    assert not any(_marker(e["id"]) for e in events)
    # Stripe recovers: Stripe redelivers each event and every one is applied
    fake.mode.update(delay=0.0)
    for e in events:
        _deliver(e)
    assert all(_marker(e["id"]) for e in events) and _status(tenant) == "active"


# 13. permanent vs transient failures are distinguishable in the logs (alerting) and never acknowledged
def test_permanent_and_transient_failures_are_logged_distinctly(owner_and_staff, fake, caplog):
    import logging
    tenant, _, _ = owner_and_staff
    other = _second_tenant()
    try:
        sub = SUB["sub_log"]
        fake.subs[sub] = _subscription(tenant, sub, "active")
        _deliver(_event(tenant, sub, "active", 1000))
        fake.subs[sub] = _subscription(other, sub, "active")           # conflicting linkage: permanent
        permanent = _event(tenant, sub, "active", 2000)
        with caplog.at_level(logging.WARNING, logger="simo_os"):
            with pytest.raises(BillingError):
                _deliver(permanent)
            fake.mode.update(kind="503")                                 # outage: transient
            transient = _event(tenant, sub, "active", 3000)
            with pytest.raises(stripe.APIError):
                _deliver(transient)
        text_ = caplog.text
        assert f"billing_webhook_permanent_failure event_id={permanent['id']}" in text_
        assert f"billing_webhook_transient_failure event_id={transient['id']}" in text_
        assert not _marker(permanent["id"]) and not _marker(transient["id"])   # neither is acknowledged
        assert "sk_test" not in text_ and SECRET not in text_
    finally:
        _drop_tenant(other)


def test_real_sdk_event_without_created_never_uses_snapshot(owner_and_staff, fake):
    tenant, _, _ = owner_and_staff
    sub = SUB["sub_missing_created"]
    fake.subs[sub] = _subscription(tenant, sub, "active")
    event = _event(tenant, sub, "past_due", 1000)
    del event["created"]
    before = _status(tenant)
    with pytest.raises(BillingError, match="created timestamp"):
        _deliver(event)
    assert not _marker(event["id"]) and _status(tenant) == before
    assert not fake.hits


def test_duplicate_in_flight_has_bounded_marker_wait(owner_and_staff, fake, monkeypatch):
    """The idempotency insert can block before the tenant lock is reached."""
    tenant, _, _ = owner_and_staff
    sub = SUB["sub_duplicate_wait"]
    fake.subs[sub] = _subscription(tenant, sub, "active")
    event = _event(tenant, sub, "active", 1000)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(svc, "STRIPE_WEBHOOK_LOCK_WAIT_SECONDS", 0.3)
    first_service = BillingService(stripe)
    read = first_service._read_subscription

    def hold(stripe_module, sub_id):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test release timeout")
        return read(stripe_module, sub_id)

    monkeypatch.setattr(first_service, "_read_subscription", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_deliver, event, first_service)
        try:
            assert entered.wait(5)
            duplicate = pool.submit(_deliver, event)
            from sqlalchemy.exc import OperationalError
            with pytest.raises(OperationalError):
                duplicate.result(timeout=3)
        finally:
            release.set()
        first.result(timeout=10)
    assert _marker(event["id"]) and _status(tenant) == "active"
    before = len(fake.hits)
    _deliver(event)
    assert len(fake.hits) == before


def test_http_webhook_does_not_block_readiness_and_rejects_overload(client, owner_and_staff, fake, monkeypatch):
    """Exercise the actual ASGI HTTP route, not just threaded service calls."""
    import importlib
    router_module = importlib.import_module("app.billing.router")
    assert router_module.STRIPE_WEBHOOK_MAX_IN_FLIGHT == 3
    # One slot makes saturation deterministic without creating extra tenants.
    monkeypatch.setattr(router_module, "_webhook_slots", threading.BoundedSemaphore(1))
    service = BillingService(stripe)
    monkeypatch.setattr(router_module, "billing_service", service)
    tenant, _, _ = owner_and_staff
    sub = SUB["sub_http_worker"]
    fake.subs[sub] = _subscription(tenant, sub, "active")
    first_event = _event(tenant, sub, "trialing", 1000)
    second_event = _event(tenant, sub, "past_due", 1001)
    entered, release = threading.Event(), threading.Event()
    read = service._read_subscription

    def hold(stripe_module, sub_id):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test release timeout")
        return read(stripe_module, sub_id)

    monkeypatch.setattr(service, "_read_subscription", hold)

    def post(event):
        payload, signature = _signed(event)
        return client.post("/api/v1/billing/webhook", content=payload,
                           headers={"stripe-signature": signature, "Content-Type": "application/json"})

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(post, first_event)
        try:
            assert entered.wait(5)
            health = pool.submit(client.get, "/ready")
            overloaded = pool.submit(post, second_event)
            assert health.result(timeout=3).status_code == 200
            response = overloaded.result(timeout=3)
            assert response.status_code == 503
            assert response.headers.get("retry-after") == "5"
            assert not _marker(second_event["id"])
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200

    # Stripe retry is accepted once capacity is available; the stale payload
    # cannot revert the real current subscription status.
    assert post(second_event).status_code == 200
    assert _marker(first_event["id"]) and _marker(second_event["id"])
    assert _status(tenant) == "active"
