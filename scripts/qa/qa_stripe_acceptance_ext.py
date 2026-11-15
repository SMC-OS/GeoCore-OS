"""Extended GeoCore Stripe SANDBOX acceptance for the isolated QA environment (test mode only).

Runs after qa_stripe_acceptance.py in the same workflow, against two synthetic owners (A = STAGING_OWNER_*, B = STAGING_OWNER_B_*).
Every scenario is recorded as PASS, FAIL, BLOCKED (could not be executed, reason given) or NOTE (observation, not a verdict).
Evidence class is stated per check: "stripe" = originated by real Stripe (hosted Checkout, API-created objects delivered by
Stripe to the QA webhook); "local-signed" = a runner-generated event signed with the sandbox webhook secret and verified by the
real Stripe SDK in the deployed API. Local-signed events are NEVER Stripe-originated deliveries and are labelled as such.
Never prints secrets, checkout/portal URLs, emails or raw upstream payloads.
"""
import concurrent.futures as cf
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import qa_stripe_acceptance as q  # same directory; module import only defines helpers (preflight/main are not run)

API = q.API
SECRET = q.SECRET
KEY = q.KEY
results: list[dict] = []
event_log: list[dict] = []


def rec(label, status, evidence="", note=""):
    results.append({"check": label, "status": status, "evidence": evidence, "note": note})
    print(f"{status:7} [{evidence or '-'}] {label}" + (f" -- {note}" if note else ""), flush=True)


def ok(label, cond, evidence, note=""):
    rec(label, "PASS" if cond else "FAIL", evidence, note)
    return bool(cond)


class Owner:
    def __init__(self, email, password):
        self.email, self.password, self.token = email, password, None
        self.login()

    def login(self):
        code, obj = q.request("POST", API + "/api/v1", "/auth/login", body={"email": self.email, "password": self.password})
        if code != 200:
            raise RuntimeError("owner login failed")
        self.token = obj["access_token"]

    def call(self, method, path, body=None):
        code, obj = q.request(method, API + "/api/v1", path, self.token, body=body)
        if code == 401:
            self.login()
            code, obj = q.request(method, API + "/api/v1", path, self.token, body=body)
        return code, obj

    def sub(self):
        return self.call("GET", "/billing/subscription")[1]

    def poll(self, predicate, seconds=240):
        end = time.monotonic() + seconds
        last = {}
        while time.monotonic() < end:
            last = self.sub()
            if predicate(last):
                return last
            time.sleep(5)
        return None

    def workspace_code(self):
        return self.call("GET", "/customers")[0]


def stripe_req(method, path, form=None):
    return q.request(method, q.STRIPE, path, KEY, form=form)


def post_event(event_id, event_type, obj, created):
    """Locally signed event (NOT a Stripe delivery); verified by the deployed API with the real SDK."""
    payload = json.dumps({"id": event_id, "object": "event", "type": event_type, "created": created, "livemode": False,
                          "api_version": "2025-01-01", "data": {"object": obj}}, separators=(",", ":")).encode()
    ts = int(time.time())
    sig = hmac.new(SECRET.encode(), str(ts).encode() + b"." + payload, hashlib.sha256).hexdigest()
    req = urllib.request.Request(API + "/api/v1/billing/webhook", data=payload, method="POST",
                                 headers={"Content-Type": "application/json", "Stripe-Signature": f"t={ts},v1={sig}"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get("Retry-After"), time.monotonic() - start
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Retry-After"), time.monotonic() - start
    except Exception:
        return 0, None, time.monotonic() - start


def sub_obj(sub_id, customer, tenant, status="active", plan="starter"):
    return {"id": sub_id, "object": "subscription", "customer": customer, "status": status,
            "metadata": {"tenant_id": tenant, "plan": plan, "billing_period": "monthly"}, "items": {"data": []}}


def evt():
    return "evt_geocore_qa_" + uuid.uuid4().hex


def browser(mode, url, email, card=""):
    js = Path("scripts/qa/qa_stripe_checkout.mjs").read_text()
    env = dict(os.environ, MODE=mode, TARGET_URL=url, EXPECT_EMAIL=email, TEST_CARD=card)
    proc = subprocess.run(["node", "--input-type=module"], input=js, cwd="apps/web", env=env, capture_output=True, text=True, timeout=240)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"outcome": "blocked", "detail": "no browser result"}


def make_api_sub(owner, tenant, customer=None, days=10):
    """A genuine sandbox customer + trialing subscription created through the Stripe API (Stripe delivers its webhooks to QA).
    Independent of hosted Checkout: used wherever a scenario only needs a real live subscription."""
    if customer is None:
        customer = stripe_req("POST", "/customers", {"email": owner.email, "description": "GeoCore isolated QA ext " + os.environ.get("GITHUB_RUN_ID", ""),
                                                      "metadata[geocore_qa]": "true", "metadata[tenant_id]": tenant})[1].get("id")
    if not customer:
        return None, None
    pm = stripe_req("POST", "/payment_methods/pm_card_visa/attach", {"customer": customer})[1].get("id")
    sub = stripe_req("POST", "/subscriptions", {"customer": customer, "items[0][price]": q.PRICES["STARTER_MONTHLY"][0], "default_payment_method": pm,
                                                "trial_end": str(int(time.time()) + days * 86400), "metadata[tenant_id]": tenant,
                                                "metadata[plan]": "starter", "metadata[billing_period]": "monthly"})[1].get("id")
    return customer, sub


def checkout_for(owner):
    code, obj = owner.call("POST", "/billing/checkout", {"plan": "starter", "billing_period": "monthly"})
    url = obj.get("checkout_url", "") if code == 200 else ""
    sid = ""
    parts = urllib.parse.urlsplit(url).path.split("/")
    for p in parts:
        if p.startswith("cs_test_"):
            sid = p
    return code, url, sid


def run():
    # PRIMARY (variable B below) = the owner the base script just took through a real subscription and cancellation: it holds a
    # (terminal) Stripe contract, so closed admission still lets it use hosted Checkout to resubscribe. SECONDARY (variable A below)
    # = the other synthetic owner. A workspace with NO Stripe subscription is refused Checkout while admission is closed (policy;
    # admission stays closed here), so a literal never-subscribed "first Checkout" cannot be executed in this environment.
    A = Owner(os.environ["STAGING_OWNER_B_EMAIL"], os.environ["STAGING_OWNER_B_PASSWORD"])
    B = Owner(os.environ["STAGING_OWNER_EMAIL"], os.environ["STAGING_OWNER_PASSWORD"])
    bsub0, asub0 = B.sub(), A.sub()
    TB, TA = bsub0["tenant_id"], asub0["tenant_id"]
    ok("two distinct synthetic workspaces", TA != TB, "stripe")
    created_customers, created_subs, open_sessions = [], [], []
    try:
        # ---- Prepare the primary workspace as an existing-contract workspace with a VALID Stripe customer --------------------
        # (the base script deletes its customer at the end, and a deleted customer makes Checkout fail with HTTP 400; that was
        # the cause of the second extended failure: a harness-ordering defect, not candidate behaviour).
        cust_p, sub_p0 = make_api_sub(B, TB)
        prepared = False
        if cust_p and sub_p0:
            created_customers.append(cust_p)
            created_subs.append(sub_p0)
            linked0 = B.poll(lambda x: x.get("plan") == "starter" and x.get("status") in ("trialing", "active") and x.get("trial_state") is None, 240)
            ok("primary workspace linked to a fresh genuine sandbox subscription by actual Stripe webhook", linked0 is not None, "stripe")
            stripe_req("DELETE", "/subscriptions/" + sub_p0)
            created_subs.remove(sub_p0)
            cancelled0 = B.poll(lambda x: x.get("status") in ("cancelled", "canceled"), 240)
            prepared = ok("actual Stripe cancellation webhook restricts the primary workspace (existing contract, valid customer kept)", cancelled0 is not None and B.workspace_code() == 402, "stripe")
        else:
            rec("primary workspace preparation for hosted Checkout", "BLOCKED", "stripe", "could not create a sandbox customer and subscription")
        # ---- Hosted Checkout: declined card, then successful Checkout (primary owner) ---------------------------------------
        before = B.sub()
        ok("primary workspace is cancelled after the base run and restricted (402)", before.get("status") in ("cancelled", "canceled") and B.workspace_code() == 402, "stripe")
        ok("secondary workspace is a no-card trial with workspace access", A.sub().get("status") == "trialing" and A.workspace_code() == 200, "stripe")
        nc, _ = A.call("POST", "/billing/checkout", {"plan": "starter", "billing_period": "monthly"})
        ok("closed admission refuses Checkout for a workspace with no Stripe subscription (policy; admission not opened)", nc == 403, "stripe", f"HTTP {nc}")
        code, url, sid = checkout_for(B)
        if code != 200 or not sid:
            rec("hosted Checkout session created (decline path)", "BLOCKED", "stripe", f"POST /billing/checkout HTTP {code}; workspace prepared={prepared}")
        else:
            open_sessions.append(sid)
            r = browser("pay", url, B.email, "4000000000000002")
            if r["outcome"] == "declined":
                s = stripe_req("GET", "/checkout/sessions/" + sid)[1]
                after = B.sub()
                ok("hosted Checkout with a declined test card is refused by Stripe and creates no subscription",
                   s.get("status") == "open" and s.get("payment_status") != "paid" and not s.get("subscription"), "stripe")
                ok("declined payment leaves entitlements and plan unchanged (still cancelled and restricted)", after.get("status") == before.get("status") and after.get("plan") == before.get("plan") and B.workspace_code() == 402, "stripe")
            else:
                rec("hosted Checkout declined-card page interaction", "BLOCKED", "stripe", f"browser outcome={r['outcome']} {r.get('detail','')}")
            stripe_req("POST", "/checkout/sessions/" + sid + "/expire", {})
            open_sessions.remove(sid)

        code, url, sid = checkout_for(B)
        completed = False
        if code != 200 or not sid:
            rec("hosted first Checkout completed", "BLOCKED", "stripe", f"POST /billing/checkout HTTP {code}")
        else:
            open_sessions.append(sid)
            r = browser("pay", url, B.email, "4242424242424242")
            if r["outcome"] == "complete":
                open_sessions.remove(sid)
                s = stripe_req("GET", "/checkout/sessions/" + sid)[1]
                ok("hosted Checkout (resubscribe by an existing-contract workspace) completed with the Stripe test card", s.get("status") == "complete" and s.get("livemode") is False and s.get("client_reference_id") == TB, "stripe")
                cur = B.poll(lambda x: x.get("status") in ("active", "trialing") and x.get("plan") == "starter" and x.get("trial_state") is None, 240)
                completed = ok("actual Stripe webhooks linked the Checkout subscription to primary workspace and access continues", cur is not None and B.workspace_code() == 200, "stripe")
                stripe_sub_id = s.get("subscription")
                if stripe_sub_id:
                    created_subs.append(stripe_sub_id)
                    ss = stripe_req("GET", "/subscriptions/" + stripe_sub_id)[1]
                    ok("Stripe subscription is tenant-bound to primary workspace only", ss.get("metadata", {}).get("tenant_id") == TB and ss.get("livemode") is False, "stripe")
                    # Stripe delivered the events to the QA endpoint (pending_webhooks == 0 means no outstanding delivery).
                    evs = stripe_req("GET", "/events?limit=40&types[]=checkout.session.completed&types[]=customer.subscription.created")[1].get("data", [])
                    mine = [e for e in evs if e.get("data", {}).get("object", {}).get("id") in (sid, stripe_sub_id)]
                    ok("Stripe reports the Checkout events delivered to the QA webhook (no pending deliveries)", len(mine) >= 2 and all(e.get("pending_webhooks") == 0 for e in mine), "stripe")
            else:
                rec("hosted first Checkout completed", "BLOCKED", "stripe", f"browser outcome={r['outcome']} {r.get('detail','')}")

        # Everything below needs ONE genuine live sandbox subscription for the primary workspace. Hosted Checkout is only one way to
        # obtain it; when it did not complete, an API-created genuine subscription is used and the evidence note says so.
        if completed and created_subs and created_subs[-1]:
            sub_id, cust_id, source = created_subs[-1], cust_p, "hosted Checkout subscription"
        else:
            cust_id, sub_id = (make_api_sub(B, TB, customer=cust_p) if cust_p else (None, None))
            source = "API-created genuine sandbox subscription (hosted Checkout not completed)"
            if sub_id:
                created_subs.append(sub_id)
                ok("primary workspace linked to the independent genuine subscription by actual Stripe webhook",
                   B.poll(lambda x: x.get("plan") == "starter" and x.get("status") in ("trialing", "active") and x.get("trial_state") is None, 240) is not None, "stripe")
        if not (sub_id and cust_id):
            for label in ("failed renewal and recovery", "billing portal", "cancellation and resume", "duplicate/out-of-order/concurrent/cross-tenant/saturation events"):
                rec(label, "BLOCKED", "stripe", "no genuine live sandbox subscription could be established")
            return
        rec("subscription under test", "NOTE", "stripe", source)

        # ---- Failed renewal and recovery (real Stripe objects and real webhook deliveries) -------------------------
        bad = stripe_req("POST", "/payment_methods/pm_card_chargeCustomerFail/attach", {"customer": cust_id})[1].get("id")
        good = stripe_req("POST", "/payment_methods/pm_card_visa/attach", {"customer": cust_id})[1].get("id")
        if not (bad and good):
            rec("failed renewal and recovery", "BLOCKED", "stripe", "could not attach Stripe test payment methods")
        else:
            cur_status = stripe_req("GET", "/subscriptions/" + sub_id)[1].get("status")
            prev_invoice = stripe_req("GET", "/subscriptions/" + sub_id)[1].get("latest_invoice")
            if cur_status == "trialing":
                stripe_req("POST", "/subscriptions/" + sub_id, {"default_payment_method": bad, "trial_end": "now", "proration_behavior": "none"})
            else:
                # Active contract: force an immediate renewal-style invoice that the failing test card cannot pay.
                stripe_req("POST", "/subscriptions/" + sub_id, {"default_payment_method": bad, "billing_cycle_anchor": "now",
                                                                 "proration_behavior": "always_invoice", "payment_behavior": "allow_incomplete"})
            inv = None
            end = time.monotonic() + 240
            while time.monotonic() < end and not inv:
                latest = stripe_req("GET", "/subscriptions/" + sub_id)[1].get("latest_invoice")
                if isinstance(latest, str) and latest.startswith("in_") and latest != prev_invoice:
                    i = stripe_req("GET", "/invoices/" + latest)[1]
                    if i.get("total", 0) > 0:
                        inv = i
                time.sleep(5)
            if not inv:
                rec("failed renewal and recovery", "BLOCKED", "stripe", "no new non-zero invoice appeared")
            else:
                if inv.get("status") == "draft":
                    inv = stripe_req("POST", "/invoices/" + inv["id"] + "/finalize", {"auto_advance": "false"})[1]
                pay_code = stripe_req("POST", "/invoices/" + inv["id"] + "/pay", {"payment_method": bad})[0]
                ok("Stripe declines the renewal payment (card_declined)", pay_code == 402, "stripe", f"HTTP {pay_code}")
                pd = B.poll(lambda x: x.get("status") == "past_due", 240)
                ok("failed renewal moves the workspace to past_due via actual Stripe webhooks", pd is not None, "stripe")
                ok("past_due workspace is restricted from workspace routes (402) but can still reach billing", B.workspace_code() == 402 and B.call("GET", "/billing/subscription")[0] == 200, "stripe")
                stripe_req("POST", "/customers/" + cust_id, {"invoice_settings[default_payment_method]": good})
                stripe_req("POST", "/subscriptions/" + sub_id, {"default_payment_method": good})
                pay2 = stripe_req("POST", "/invoices/" + inv["id"] + "/pay", {"payment_method": good})[0]
                ok("payment recovery with a good test card succeeds", pay2 in (200,), "stripe", f"HTTP {pay2}")
                rc = B.poll(lambda x: x.get("status") == "active", 240)
                ok("recovery restores active status and workspace access via actual Stripe webhooks", rc is not None and B.workspace_code() == 200, "stripe")

        # ---- Portal and tenant scope ---------------------------------------------------------------------------------
        code, portal = B.call("POST", "/billing/portal")
        purl = portal.get("portal_url", "") if code == 200 else ""
        if urllib.parse.urlsplit(purl).hostname != "billing.stripe.com":
            rec("billing portal for the subscriber", "BLOCKED", "stripe", f"POST /billing/portal HTTP {code}")
        else:
            r = browser("portal", purl, B.email)
            ok("hosted billing portal opens for primary workspace", r["outcome"] == "portal_loaded", "stripe", r.get("detail", ""))
        pa_code, pa = A.call("POST", "/billing/portal")
        a_url = pa.get("portal_url", "") if pa_code == 200 else ""
        ok("secondary owner cannot obtain a portal for primary workspace's customer", pa_code != 200 or a_url != purl, "stripe", f"HTTP {pa_code}")
        ca_code, _ = A.call("POST", "/billing/cancel")
        ok("secondary owner's cancel request cannot affect primary workspace", B.sub().get("cancel_at_period_end") is False, "stripe", f"A cancel HTTP {ca_code}")
        ok("secondary workspace billing view never exposes primary workspace", A.sub().get("tenant_id") == TA, "stripe")

        # ---- Cancellation and resume (webhook-confirmed) ---------------------------------------------------------------
        ok("cancel at period end accepted", B.call("POST", "/billing/cancel")[1].get("cancel_at_period_end") is True, "stripe")
        ok("actual Stripe webhook reflects cancellation flag", B.poll(lambda x: x.get("cancel_at_period_end") is True, 180) is not None, "stripe")
        ok("resume accepted", B.call("POST", "/billing/resume")[1].get("cancel_at_period_end") is False, "stripe")
        ok("actual Stripe webhook reflects resume", B.poll(lambda x: x.get("cancel_at_period_end") is False and x.get("status") == "active", 180) is not None, "stripe")

        # ---- Local-signed event scenarios (verified by the deployed SDK; NOT Stripe deliveries) -------------------------
        now = int(time.time())
        canon = B.sub()
        e1 = evt()
        ok("duplicate delivery: first signed event accepted", post_event(e1, "customer.subscription.updated", sub_obj(sub_id, cust_id, TB, "active"), now)[0] == 200, "local-signed")
        ok("duplicate delivery: identical event id redelivered is an idempotent 200", post_event(e1, "customer.subscription.updated", sub_obj(sub_id, cust_id, TB, "active"), now)[0] == 200, "local-signed")
        ok("duplicate delivery leaves state equal to Stripe's canonical state", B.sub().get("status") == "active" and B.sub().get("plan") == canon.get("plan"), "local-signed")

        # Reconciliation discriminator: a stale/hostile snapshot claiming 'canceled' must not override Stripe's current state.
        e2 = evt()
        c2 = post_event(e2, "customer.subscription.updated", sub_obj(sub_id, cust_id, TB, "canceled", "team"), now + 5)[0]
        s2 = B.sub()
        ok("real-SDK event reconciles from authoritative Stripe state, not from the event snapshot", c2 == 200 and s2.get("status") == "active" and s2.get("plan") == "starter" and B.workspace_code() == 200, "local-signed", f"HTTP {c2}")
        e3 = evt()
        c3 = post_event(e3, "customer.subscription.updated", sub_obj(sub_id, cust_id, TB, "canceled"), now - 7200)[0]
        ok("out-of-order (older) event delivered after newer ones cannot regress state", c3 == 200 and B.sub().get("status") == "active", "local-signed", f"HTTP {c3}")

        # Concurrency: same tenant
        def burst(n, factory):
            with cf.ThreadPoolExecutor(max_workers=n) as ex:
                return list(ex.map(lambda i: factory(i), range(n)))
        same = burst(6, lambda i: post_event(evt(), "customer.subscription.updated", sub_obj(sub_id, cust_id, TB, "active"), int(time.time())))
        ok("concurrent same-tenant events: every response is 200 or a retryable 503", all(c in (200, 503) for c, _, _ in same), "local-signed", str(sorted(c for c, _, _ in same)))
        ok("concurrent same-tenant events leave the canonical state", B.sub().get("status") == "active" and B.workspace_code() == 200, "local-signed")

        # Second live subscription for tenant A (real Stripe objects) for different-tenant concurrency and linkage checks
        acust = stripe_req("POST", "/customers", {"email": A.email, "description": "GeoCore isolated QA ext " + os.environ.get("GITHUB_RUN_ID", ""), "metadata[geocore_qa]": "true", "metadata[tenant_id]": TA})[1].get("id")
        a_sub = None
        if acust:
            created_customers.append(acust)
            apm = stripe_req("POST", "/payment_methods/pm_card_visa/attach", {"customer": acust})[1].get("id")
            a_trial_end = int(time.time()) + 10 * 86400
            a_sub = stripe_req("POST", "/subscriptions", {"customer": acust, "items[0][price]": q.PRICES["STARTER_MONTHLY"][0], "default_payment_method": apm,
                                                          "trial_end": str(a_trial_end), "metadata[tenant_id]": TA, "metadata[plan]": "starter", "metadata[billing_period]": "monthly"})[1].get("id")
        if a_sub:
            created_subs.append(a_sub)
            linked = A.poll(lambda x: x.get("plan") == "starter" and x.get("status") in ("trialing", "active"), 240)
            ok("secondary workspace linked to its own new Stripe subscription by actual webhook", linked is not None, "stripe")
            mixed = burst(8, lambda i: post_event(evt(), "customer.subscription.updated",
                                                   sub_obj(sub_id if i % 2 == 0 else a_sub, cust_id if i % 2 == 0 else acust, TB if i % 2 == 0 else TA, "active"), int(time.time())))
            ok("concurrent different-tenant events: every response is 200 or a retryable 503", all(c in (200, 503) for c, _, _ in mixed), "local-signed", str(sorted(c for c, _, _ in mixed)))
            ok("different-tenant events keep each workspace on its own subscription", A.sub().get("tenant_id") == TA and B.sub().get("tenant_id") == TB and B.sub().get("status") == "active", "local-signed")

            # Cross-tenant linkage rejection: event for A's subscription claiming primary workspace
            before_a, before_b = A.sub(), B.sub()
            xt = evt()
            xc = post_event(xt, "customer.subscription.updated", sub_obj(a_sub, acust, TB, "active"), int(time.time()))[0]
            ok("cross-tenant linkage attempt is rejected and not acknowledged", xc >= 500, "local-signed", f"HTTP {xc}")
            ok("cross-tenant attempt changed neither workspace", A.sub().get("status") == before_a.get("status") and B.sub().get("status") == before_b.get("status") and B.sub().get("plan") == before_b.get("plan"), "local-signed")
            event_log.append({"kind": "permanent", "event_id": xt})
        else:
            rec("different-tenant concurrency and cross-tenant linkage", "BLOCKED", "stripe", "could not create the second synthetic Stripe subscription")

        # Transient-class failure: subscription that does not exist in Stripe
        tr = evt()
        tc = post_event(tr, "customer.subscription.updated", sub_obj("sub_geocore_qa_missing_" + uuid.uuid4().hex[:10], cust_id, TB, "active"), int(time.time()))[0]
        ok("event for a subscription Stripe does not have fails closed (not acknowledged)", tc >= 500, "local-signed", f"HTTP {tc}")
        event_log.append({"kind": "transient", "event_id": tr})

        # Saturation + responsiveness: 30 concurrent signed events while sampling health and an authenticated read
        lat, errors, stop = [], [], False

        def sampler():
            while not stop:
                t0 = time.monotonic()
                c1 = q.request("GET", API, "/health")[0]
                c2 = B.call("GET", "/billing/subscription")[0]
                lat.append(time.monotonic() - t0)
                if c1 != 200 or c2 != 200:
                    errors.append((c1, c2))
                time.sleep(0.5)
        with cf.ThreadPoolExecutor(max_workers=1) as sx:
            fut = sx.submit(sampler)
            sat = burst(30, lambda i: (lambda ev: (ev, post_event(ev, "customer.subscription.updated", sub_obj(sub_id, cust_id, TB, "active"), int(time.time()))))(evt()))
            stop = True
            fut.result()
        codes = [r[1][0] for r in sat]
        n503 = [r for r in sat if r[1][0] == 503]
        rec("webhook burst distribution", "NOTE", "local-signed", f"30 concurrent: 200={codes.count(200)} 503={codes.count(503)} other={len([c for c in codes if c not in (200, 503)])}")
        ok("burst: every response is 200 or 503", all(c in (200, 503) for c in codes), "local-signed")
        if n503:
            ok("burst: every 503 carries Retry-After", all(r[1][1] for r in n503), "local-signed")
            redeliver = [post_event(ev, "customer.subscription.updated", sub_obj(sub_id, cust_id, TB, "active"), int(time.time()))[0] for ev, _ in n503]
            ok("503-rejected events succeed on redelivery", all(c == 200 for c in redeliver), "local-signed", str(sorted(set(redeliver))))
        else:
            rec("webhook concurrency saturation (HTTP 503) observed", "BLOCKED", "local-signed", "30-way burst did not reach the in-flight cap; 503 recovery not proven on the deployed service (CI/local tests cover it)")
        ok("API stays responsive during the webhook burst (health + authenticated read)", not errors and bool(lat) and max(lat) < 15, "local-signed",
           f"samples={len(lat)} max_s={max(lat) if lat else 'n/a'} errors={len(errors)}")
        ok("state canonical after the burst", B.sub().get("status") == "active" and B.workspace_code() == 200, "local-signed")
    finally:
        # Only objects created by this run.
        for sid in open_sessions:
            stripe_req("POST", "/checkout/sessions/" + sid + "/expire", {})
        for s in created_subs:
            code = stripe_req("DELETE", "/subscriptions/" + s)[0]
            rec("cleanup synthetic subscription", "PASS" if code in (200, 404) else "FAIL", "stripe")
        for c in [x for x in created_customers if x]:
            code = stripe_req("DELETE", "/customers/" + c)[0]
            rec("cleanup synthetic customer", "PASS" if code in (200, 404) else "FAIL", "stripe")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        rec("extended Stripe acceptance run", "FAIL", "-", type(exc).__name__)
    finally:
        Path("qa-stripe-ext-results.json").write_text(json.dumps({"environment": "isolated_qa", "checks": results, "events": event_log}, indent=2))
        Path("qa-stripe-ext-events.json").write_text(json.dumps(event_log))
    counts = {s: len([r for r in results if r["status"] == s]) for s in ("PASS", "FAIL", "BLOCKED", "NOTE")}
    print("SUMMARY", json.dumps(counts))
    sys.exit(0 if counts["FAIL"] == 0 and counts["BLOCKED"] == 0 else 1)
