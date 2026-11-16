# 20 — Release closure report, 15 Nov 2026

**Isolated technical acceptance: successful workflow execution (owner-attributed). Production / customer release: HOLD / NO-GO.** Production and shared staging untouched. Public admission closed. No live Stripe activity. PR #68 and #69 remain on HOLD (#69 auto-deploys shared staging web).

## 1. Provenance of evidence
This session's GitHub API is blocked (renamed repository). Job conclusions below marked **(owner)** were independently confirmed by the owner's separate GitHub connection and are attributed to it. Everything marked **(local)** was run or read in this session from the git history.

## 2. Immutable runtime candidate
- Runtime candidate: `2dda56b18f630d3014650bef959e34132ba8fc96` (PR #74 merge). Migration head: `c7d8e9f0a1b2` (local `alembic heads`: single head).
- Commits after the candidate on `qa/geocore-closure-20261105` (local `git diff --name-status`): `9a9d537`, `cc8a9ff`, `a4ca4a1`, `6ccc861` change only `.github/workflows/geocore-qa-acceptance.yml`, `geocore-qa-public-smoke.yml`, `geocore-qa-stripe.yml`. No file under `app/`, `alembic/`, `apps/`, `tests/`, `Dockerfile` or any dependency manifest changed. **No new product candidate exists.**

## 3. Verified technical acceptance evidence
| Item | Result | Source |
|---|---|---|
| PR #74 CI, run 43639548363 | five jobs success | (owner) |
| Integrated candidate CI, run 43641363751 | backend, frontend, dependency audit, secret scan, E2E success | (owner) |
| Authenticated isolated-QA acceptance, run 43654863264 | success: revision check, two synthetic owners, 8 browser tests, lifecycle/invoice/amendment/PDF/portal, roles + tenant isolation, stone workflow, deployed security, capacity to 30 VUs, configuration restored, redacted evidence kept | (owner); per-check counts not read |
| Stripe sandbox run 43662721622 | success; exact coverage in section 4 | (owner) + script review (local) |
| Email run 43529873478 | success; exact coverage in section 4 | (owner) + script review (local) |
| Backend suite on base head `a4ca4a1`, fresh PostgreSQL | 1880 passed, 31 skipped | (local) |
| Earlier exact-SHA acceptance on `607a51b` (8/8, 73/73, 88/88, 10/10, security 38/38, capacity) | recorded in doc 19; applied to the pre-billing-fix runtime | (local) |
| Isolated-QA PG18 matched database+uploads drill | pass (restore 2.0 s, small data, same server, no PITR) | (local), earlier build |

### 3a. Test-count reconciliation (1880/31 local vs 1883/28 CI)
`pytest -rs` locally lists exactly 31 skips: **3** in `tests/test_staging_scripts.py:296` (reason "PowerShell is unavailable": `pwsh` is not installed in this container) and **28** in `tests/test_writer_barrier_rehearsal.py` (module-level skip: needs root, PostgreSQL server binaries, `setpriv` and `REHEARSAL_PYTHON`). GitHub's runner has `pwsh`, so the 3 PowerShell tests run and pass (1880 + 3 = 1883); the 28 rehearsal tests skip in both places (31 - 3 = 28). The difference is environmental, not a regression. The 28 rehearsal tests are therefore not exercised by CI; they were run on the operator-style local rehearsal earlier.

## 4. Exact scenario coverage of the successful Stripe and email workflows
Method: reading `scripts/qa/qa_stripe_acceptance.py` and `qa_email_provision.py` plus the email workflow. The Stripe script stops at the first failed check, so a success conclusion means every step below passed.
| Scenario | Stripe run 43662721622 / email run 43529873478 | Class |
|---|---|---|
| Eight test-mode prices match approved GBP pricing | executed | VERIFIED (owner) |
| Fresh no-card trial (13-14 days) | executed | VERIFIED (owner) |
| Unsigned webhook refused | executed | VERIFIED (owner) |
| Runner-signed diagnostic webhook accepted | executed; explicitly **not** a Stripe delivery | VERIFIED as signature check only |
| Closed admission refuses first checkout (403) | executed | VERIFIED (owner) |
| Real sandbox subscription + real webhook applied | executed (subscription created via Stripe API, status polled) | VERIFIED (owner) |
| Trial to active through real paid renewal invoice event | executed | VERIFIED (owner) |
| Existing-subscriber hosted Checkout session (price, mode, workspace binding), then expired | executed; session **not completed** | VERIFIED for session creation only |
| **Hosted first Checkout completion** | not executed (admission closed by design) | NOT EXECUTED |
| Customer billing portal | session URL on billing.stripe.com created; not opened or used | PARTIAL |
| Cancel at period end and resume (GeoCore API, confirmed on the Stripe object) | executed | VERIFIED (owner) |
| Plan change starter to team to starter, reflected by webhook | executed through the Stripe API, not through the portal/UI | PARTIAL |
| Subscription cancelled webhook applied | executed | VERIFIED (owner) |
| **Declined payment, past_due, recovery** | not in the script | NOT EXECUTED on deployed QA |
| **Signed webhook replay of a real event; real out-of-order delivery** | not in the script; covered by backend unit tests in CI (stale event, same-second, invoice-before-linkage, retired contract, rollback, concurrency) | PENDING (unit-test evidence only) |
| Tenant isolation of billing on deployed QA | no second-tenant billing check in the script | NOT EXECUTED |
| Provider accepted verification and password-reset e-mails (provider message id) | executed | VERIFIED (owner) as provider acceptance |
| **Message arrived in the inbox** | the workflow cannot read the mailbox; needs the owner's confirmation | UNKNOWN |
| API resend; invalid verification token and invalid reset token refused | executed | VERIFIED (owner) |
| **Verification-link click, forgot-password to reset, expired link, used link** | not executed | NOT EXECUTED |
Note: the email run predates PR #74; the e-mail code paths did not change between `86c4911` and the candidate (only billing, CRUD and the migration), so it still applies, but that is an inference, not a re-run.

## 5. Phase C — Stripe reconciliation under failure (local evidence)
Implementation reviewed: `app/billing/service.py` (`_reconcile_stripe_event`, `_lock_subscription`, `_retrieve_subscription`). Probe run against the candidate code on PostgreSQL (scratch test, deleted; mechanism script kept as `evidence/stripe_lock_pool_probe.py.txt`):
| Condition | Observed behaviour | Verdict |
|---|---|---|
| Stripe timeout, 429 rate limit, 5xx outage while retrieving the subscription | HTTP 500 generic body, transaction rolled back, idempotency marker not kept | Correct: Stripe retries |
| Recovery | same event id: first 500, second 200 (applied), third 200 (no-op) | Correct, idempotent |
| Concurrent events for one tenant | second waits for the first (existing test) | Correct |
| Tenant linkage conflict | HTTP 500 every delivery (a permanent error is retried by Stripe until it gives up); no data written | Acceptable but must be alerted on; see below |
| **Slow Stripe while holding the tenant lock** | **DEFECT (medium, low probability): the Stripe read happens after `SELECT ... FOR UPDATE` on the tenant, so each webhook holds a database connection and the lock for the whole Stripe call.** The default Stripe client waits up to 80 s per attempt with 2 retries (about 240 s). Reproduced at pool size 3: three events for one tenant during a slow Stripe read made an unrelated request fail with a pool `TimeoutError` after 2 s. The production pool is the SQLAlchemy default (5 + 10 = 15 connections), so the same starvation needs about 15 concurrent blocked events, e.g. a Stripe slowdown during bursts across a handful of tenants. Within the first-cohort limit (<=10 workspaces) this is unlikely but its impact is platform-wide latency or 500s | **Smallest reviewed correction (needs a new candidate, not applied):** in `BillingService._get_stripe`, set `stripe.max_network_retries = 1` and use an HTTP client with a 10 s timeout, bounding the lock/connection hold to about 20 s; optionally add `lock_timeout` for the webhook transaction. Add a test with a slow retrieve asserting an unrelated query is not starved at default pool settings |
No other defect found. No product runtime change was made.

## 6. Gate classification (VERIFIED, BLOCKED, PENDING, UNKNOWN, NOT EXECUTED)
Ledger source: `15-release-execution-ledger.json` (33 gates, validator clean). Evidence for isolated gates is owner-attributed unless marked local.
| Gate | Class | Basis |
|---|---|---|
| CI on release candidate | VERIFIED | (owner) runs 43639548363, 43641363751; test counts reconciled (local) |
| Secret scanning, data integrity | VERIFIED | CI |
| Tenant isolation, customer/project, quotes, quote revisions, invoices, supplier settings, PDFs, documents/portal, stone workflow, materials/procurement, approval authority, security, capacity (isolated QA) | VERIFIED | (owner) run 43654863264; per-check counts not read |
| Role permissions | PENDING | owner policy approval outstanding; approval policy implemented and tested |
| Stripe: trial, subscription, renewal, cancel/resume, plan change, cancellation webhook | VERIFIED (isolated, API-driven) | (owner) run 43662721622 |
| Stripe: hosted first Checkout completion | NOT EXECUTED | admission closed |
| Stripe: declined payment and recovery | NOT EXECUTED | |
| Stripe: portal use, UI upgrade/downgrade, proration, refund | NOT EXECUTED | |
| Stripe: real replay/out-of-order delivery on deployed QA | PENDING | unit tests only |
| Billing tenant isolation on deployed QA | NOT EXECUTED | |
| E-mail provider acceptance (verification, reset), resend, invalid links | VERIFIED | (owner) run 43529873478 |
| E-mail inbox receipt | UNKNOWN | needs owner confirmation |
| Verification click, reset flow, expired/used links | NOT EXECUTED | |
| Stripe webhook slow-read defect | PENDING | document 5; correction awaits a reviewed candidate |
| Migration `c7d8e9f0a1b2` on isolated QA | PENDING: `EXPECTED_MIGRATION` is declared in the acceptance workflow but no step asserts it (local grep); the revision is only implied by the API start-command migration gate and `/ready` | add an explicit read-only `alembic_version` assertion; production migration NOT EXECUTED |
| Isolated-QA recovery drill | VERIFIED | earlier build, local |
| Shared-staging backup/recovery, PITR/WAL | BLOCKED | needs window, operator, storage |
| Production recovery point, migration impact, catalogue import, configuration | NOT EXECUTED | read-only inspection not yet authorised |
| Monitoring and alerts, support mailbox and targets | BLOCKED | owner accounts and decisions |
| Legal and commercial acceptance | BLOCKED | no approval evidenced |
| Admission closed on production | UNKNOWN | production not inspected |
| PR #68, #69 | HOLD | unchanged |

## 7. Operational release blockers
Shared-staging recovery rehearsal and PITR verification; production recovery point and rollback; production read-only inspection; monitoring/alerting and incident ownership; support mailbox and response targets; legal/commercial acceptance; owner confirmation of the approval policy; hosted-Checkout, declined-payment and inbox scenarios; the webhook timeout correction (recommended before any paying customer).

## 8. Owner decisions required
1. Approve or amend the approval policy, quote-revision workflow and supplier-settings defaults.
2. Authorise the webhook timeout correction as a new reviewed candidate (my recommendation), or accept the risk in writing for the <=10-workspace cohort.
3. Confirm the Gmail test mailbox received the verification and reset messages; approve a click-through test.
4. Decide whether a synthetic hosted-Checkout completion and declined-card test is permitted while admission stays closed.
5. Name the operator and reviewer, approve a replacement UTC window and notification text (doc 21, section B1).
6. Provide monitoring/support accounts, mailbox ownership and targets (doc 21, B3).
7. Legal and commercial acceptance items (doc 21, B4).

## 9. Ordered execution plan
1. Owner: decisions 1-4 above. 2. Agent: timeout correction + test as a new candidate through the normal PR/CI path; re-run Stripe workflow on it. 3. Agent/owner: declined-card, hosted-Checkout and inbox click-through scenarios on isolated QA. 4. Owner: monitoring and support setup, verified by a deliberate test alert and test e-mail. 5. Operator: read-only production inspection (doc 21, B2). 6. Operator: shared-staging rehearsal in the approved window (B1). 7. Owner: legal/commercial checklist (B4). 8. Owner: explicit production authorisation of one immutable SHA; API first, web second; admission stays closed until production acceptance.

## 10. Verdict
**Production GO/NO-GO: NO-GO (HOLD).** Isolated technical acceptance is verified by successful workflows on the integrated candidate. That is not launch approval: recovery, production readiness, monitoring, support and legal gates are open, and several billing and e-mail scenarios were not executed.


---
## 11. UPDATE — final technical closure, 15 Nov (supersedes sections 5, 6 and 10 where stated)
Evidence classes: **Local** (this session, fresh PostgreSQL / local fake Stripe), **CI**, **Deployed QA**, **Owner-confirmed**, **Production** (none exists).

### 11.1 New finding that supersedes earlier Stripe conclusions — FAILED (fixed in PR #75, not yet merged)
`stripe.Event` (SDK 15.6.1) is not a dict and has no `.get`; `BillingService._event_time()` used `event.get("created")`, so it returned `None` for **every real webhook**. Events without a time take the legacy snapshot handler, so the authoritative reconciliation, stale-event protection and the lock/ordering design merged in PR #74 never ran for real Stripe events; only dict test fixtures reached them. (The original `.get` pattern came from my earlier stale-event commit `db763c3`.) Reproduced locally and fixed with subscript access; regression test uses a real `stripe.Event`.
Consequences: (a) the Stripe sandbox run 43662721622 (owner) passed on the legacy path and therefore does **not** prove reconciliation, ordering or locking; its other conclusions (prices, trial, renewal, cancel/resume, plan change, cancellation) stand as outcome evidence only. (b) The candidate `2dda56b` must **not** be treated as the billing release candidate. (c) Section 5 "Correct, idempotent" rows described the unit-test path, not the real-event path, and are superseded by 11.2.

### 11.2 Webhook reliability correction — Local evidence, PR #75 (draft), CI pending
Base `6ccc86139c609ff9f2072656204820aa93265603`; head `2739f37` (product commit `accf6a7`). Changed: `app/billing/service.py`, `tests/test_billing_bounded_reads.py`, two measurement scripts. No migration.
| Measure | Before | After |
|---|---|---|
| One Stripe read against a hung server (real SDK, local fake) | 241.3 s, 3 attempts (module defaults 80 s x 3) | 20.52 s, 2 attempts (connect 5 s, read 10 s, 1 retry; narrowly scoped client used only for reconciliation reads); 429/5xx retry once, 0.5 s |
| Scaled starvation run (pool 4, burst of 6 same-tenant events, hung Stripe) | max connection hold 53.2 s; unrelated probes failed 4 of 164 | read bound only: hold 12.55 s, 1 of 32 failed; read bound + 5 s lock-wait bound: hold 2.53 s, 0 of 7 failed, max probe latency 0.76 s |
| Recovery when Stripe answers | ok | ok (redelivery applies the event; duplicates never touch Stripe) |
The read bound alone is **not** sufficient (same-tenant bursts queue on the lock holding connections); the lock-wait bound is a separable second change. Not a hard guarantee for a slow-trickle response. 14 new tests: connection failure, read timeout, 429, 5xx, redelivery after failure, duplicate after success, concurrent same-tenant, concurrent different-tenant, pool availability, cross-tenant linkage conflict, out-of-order, lock-wait bound, failure classification logging, real-SDK event time. Local full backend: **1894 passed, 31 skipped** (28 rehearsal tests NOT executed; 3 PowerShell tests unavailable locally). Frontend 424 passed; pip-audit clean; secret scan 0; single Alembic head `c7d8e9f0a1b2`. Browser E2E: CI only.
**Poison events:** a permanent conflict (tenant linkage, ambiguous or duplicate live subscription) is not acknowledged or discarded; it returns 5xx on every redelivery and now logs `billing_webhook_permanent_failure event_id=... event_type=... reason=...`. Operational handling: alert on that log line and on repeated 5xx from `/billing/webhook` for one event id; a human compares the Stripe subscription's tenant metadata with the workspace and decides (correct the metadata or the link). No auto-discard without a reviewed design. Transient failures log `billing_webhook_transient_failure`.

### 11.3 Status of authorised items
| Item | Class | Notes |
|---|---|---|
| 1 Webhook correction implemented and tested | PARTIAL | local VERIFIED; CI PENDING; review PENDING |
| 2 New reviewed candidate | PENDING | after PR #75 review and CI; then pin and deploy |
| 3 Hosted first Checkout (QA) | NOT EXECUTED | needs the deployed new candidate and an isolated admission mechanism; hosted-page automation untested |
| 4 Declined payment and recovery | NOT EXECUTED | same |
| 5 Signed replay / duplicate / out-of-order on QA | NOT EXECUTED | locally signed events only; must be labelled as not Stripe-originated |
| 6 Billing tenant isolation on QA | NOT EXECUTED | |
| 7 Email click-through | BLOCKED / UNKNOWN | no mailbox access in this session; owner-assisted procedure in doc 21 |
| 8 Explicit migration-head assertion | PARTIAL | implemented and tested locally on branch `qa/acceptance-migration-assertion` (`47614c5`); not yet run deployed |
| 9 Documentation and ledger | this update | |
Production/customer release: **HOLD / NO-GO.** PR #68 and #69 HOLD.

## 12. UPDATE — hosted acceptance, writer-barrier and recovery closure, 15 Nov (supersedes sections 3, 4, 6 and 10 where stated)

### 12.1 Separation of revisions
| Class | Revision | Notes |
|---|---|---|
| **Product runtime candidate** | `f503041822923bd4b07a0bebaa6748ba71873740` (PR #75 merge) | Alembic head `c7d8e9f0a1b2`. Required CI success on the exact commit: push run 43725241729, PR run 43725247598 (backend, frontend, e2e, dependency-audit, secret-scan). PR #75 head `b351dca` CI 43724361006 / 43724356433. |
| QA tooling, merged | `2b2f5141289267479d23a77780478982a33ed5f1` (PR #76) | Pin to `f503041`, in-container migration assertion, dispatch-only acceptance/Stripe/email workflows, pinned deploy workflow. Runtime paths byte-identical to the candidate. |
| Recovery tooling, open | PR #77, branch `qa/recovery-integration` (`154fce1`) | Writer-barrier rehearsal workflow and verifier, `writer_barrier.py` stdin fix, hardened matched-recovery drill, exact-SHA restoration. Runtime paths byte-identical to the candidate. Not merged. |
| Acceptance tooling, not approved | `qa/stripe-ext` (`6704576`) | Extended Stripe scenarios and hosted-Checkout driver. Not merged; the hosted Checkout outcomes it targets are BLOCKED. |
| Held | PR #68, PR #69 | HOLD, untouched. |

### 12.2 Deployment identities (isolated QA only, project `geocore-qa-20261110`, environment `acceptance`)
| Event | API deployment | Web deployment | Commit |
|---|---|---|---|
| Before this candidate | `c2921803-b4d9-4eed-8c66-699416f77589` | `aef81f00-6007-4bba-aab4-601d12329cc5` | 2dda56b / 9a9d537 |
| Pinned deploy, workflow 43727031737 (API first, then web) | `e43e5b8b-60f3-43a4-8962-07cdb7187ed4` | `d27b912c-f112-49f2-ada3-7f55f1b28c87` | `f503041` exact |
| Restored after authenticated acceptance 43730458415 | `cdef371a-9f7d-44c3-8863-7e070e4d49bf` | unchanged | `f503041` |
| Restored after Stripe run 43729589715 | `ff0e588a-5e41-4215-91cb-f382ca333f96` | unchanged | `f503041` |
| Restored after hosted-Checkout run 43733515870 | `7597e98b-ca3c-4f91-bd7c-d0a23406fd87` | unchanged | `f503041` |
| First recovery drill 43732786394 (old restoration) | `dc05bbcb-ddff-4840-bc2d-9edfd89f9772` | unchanged | `2b2f514` — runtime-identical branch-head build, NOT the pinned candidate |
| Hardened drill 43733902492 (old restoration) | `ef5cd5b9-0781-4e6e-8d36-3b115e57b4c9` | unchanged | `2b2f514` — same caveat |
| **Hardened drill 43734792050 with exact-SHA restoration (current final)** | **`fbe5df50-72a0-4036-bb99-329b41699860`** | **`d27b912c-f112-49f2-ada3-7f55f1b28c87`** | **`f503041` exact** |

Final restoration line, run 43734792050: `RESTORED_PINNED api=fbe5df50…/SUCCESS/f503041… web=d27b912c…/SUCCESS/f503041… admission=closed start_predeploy=baseline rc=0`, `task_rc=0 restore_rc=0`. Public admission stayed closed throughout.

### 12.3 Migration assertion (actual, in the deployed container)
Deploy workflow 43727031737: migrate gate printed `c7d8e9f0a1b2 (head)`, zero tracebacks. The in-container read-only `alembic_version` query printed `QA_MIGRATION|c7d8e9f0a1b2|PASS` in acceptance 43730458415 and in every Stripe provisioning run. Exactly one revision, equal to the expected head; the assertion fails the deployment otherwise.

### 12.4 Authenticated acceptance — run 43730458415 (SUCCESS, both services exactly `f503041`)
| Suite | Result |
|---|---|
| Hosted browser journeys (stone gate) | 8/8 |
| Lifecycle, invoice numbering, quote amendments, PDF text, portal scope | 73/73 |
| Owner/Staff roles, tenant isolation, editing, tasks, workflow | 88/88 |
| Stone job enquiry → complete (12 stages) | 10/10 |
| Hosted security (headers, CORS, error sanitisation, webhook, throttle, secrets) | 38/38 |
| **Total functional/browser/security checks** | **217 passed, 0 failed** |
| Restoration | PASS (API `cdef371a`), admission assertion passed |

Bounded capacity, isolated QA only (not a production-scale claim): 5 VUs 289 requests, 0 errors, p95 0.170 s, p99 0.197 s; 15 VUs 832 requests, 0 errors, p95 0.321 s, p99 1.271 s; 30 VUs 1,566 requests, 0 errors, p95 0.333 s, **p99 3.825 s**, 39.1 req/s. Total 2,687 requests, 0 errors. Supported claim remains ≤10 workspaces and ≤30 concurrent users on the current instance.
Not covered by automation: this is not evidence that every interactive control was manually inspected.

### 12.5 Stripe sandbox acceptance — evidence classes kept separate
Runs: 43727540053 (base lifecycle PASS; extended blocked by the closed-admission policy), 43728086484 (base PASS; extended blocked by a harness ordering defect: the base script deleted its Stripe customer so Checkout returned HTTP 400), 43728784867 (41 PASS / 1 FAIL / 2 BLOCKED: browser driver imported `playwright` but `apps/web` only depends on `@playwright/test`), **43729589715 (42 PASS, 0 FAIL, 2 BLOCKED, 2 NOTE; restored API `ff0e588a`)**.

| Evidence class | Verified outcomes (run 43729589715 unless stated) |
|---|---|
| **Actual Stripe sandbox API operations + actual Stripe-originated webhooks** | Customer, payment method, subscription creation; subscription-created webhook applied; trial invoice paid; trial → active; renewal **declined with HTTP 402 (card_declined)**; workspace → past_due; past_due restricted from workspace routes (402) but billing reachable; recovery with a good test card; entitlements restored (active, 200); cancel at period end; resume; Team ↔ Starter plan changes; cancellation webhook; secondary owner cannot obtain the primary customer's portal or cancel it; tenant-bound subscription metadata |
| **Hosted browser (Stripe-hosted page)** | **Billing portal page loaded with billing content** (portal opened by the driver). The customer's email was not shown on the page, so customer scoping on that page is NOT verified; scoping is evidenced at the API level only. |
| **Locally-signed deliveries (signed with the sandbox webhook secret, verified by the real Stripe SDK in the deployed API; NOT Stripe-originated)** | Duplicate event id → idempotent 200; stale/hostile `canceled` snapshot ignored in favour of Stripe's authoritative state (proves the real-SDK reconciliation path on the corrected candidate); out-of-order older event cannot regress state; concurrent same-tenant (3×200, 3×503) and different-tenant (3×200, 5×503) deliveries leave canonical state; cross-tenant linkage attempt rejected with HTTP 500 and not acknowledged, neither workspace changed; event for a subscription Stripe does not have fails closed (500); **burst of 540 events over ~12 s: 58 × 200, 482 × 503, every 503 carried Retry-After**; 20 sampled 503-rejected events succeeded on redelivery (NOT all 482); API stayed responsive (13 samples, 0 errors, max 0.51 s) |
| **Operational logging** | Permanent-failure marker `billing_webhook_permanent_failure` and transient marker `billing_webhook_transient_failure` found in the API log for the generated synthetic events, via a verifier that cannot pass on an empty manifest (BLOCKED exit 2) and rejects credential patterns. **This is logging only. Delivery of an alert to a monitored owner is NOT verified.** |
| **Local fault injection / CI only** | Stripe read timeout, 429 and 5xx behaviour (bounded client: 20.5 s hang vs 241 s; one retry), lock-wait bound, connection-pool availability, redelivery then duplicate with no second Stripe read. Real timeout/429/5xx recovery against hosted Stripe was **NOT executed** (the sandbox cannot be made to fail that way) and is not claimed. |
| **BLOCKED** | Hosted Checkout, declined card AND successful card: the driver reached the payment form, entered the test card and clicked pay; Stripe's page then presented a **captcha** (`captcha=true`, 16 frames, no redirect or decline message in 90 s; run 43733515870). This is an external anti-automation control, not GeoCore behaviour; it is not bypassed. Owner-assisted procedure: doc 21 §B7. |

Policy facts established: with admission closed, Checkout is refused (HTTP 403) for a workspace with no Stripe subscription (passed as a policy check, admission not opened). Therefore **literal first-ever Checkout cannot be executed in the existing acceptance environment**; testing an existing-contract workspace would not prove it. It stays BLOCKED until the disposable admission-enabled environment (doc 21 §B8) is approved and used.
One observation (LOW, not a candidate blocker): a workspace whose Stripe customer was deleted in Stripe cannot start Checkout (Stripe error surfaces as HTTP 400); there is no in-product recovery path for a deleted customer.

### 12.6 Writer-barrier rehearsal — local/disposable-runner evidence (separate from hosted recovery)
Disposable GitHub-hosted runner, local PostgreSQL 16 only, application as UID 10001, no Railway/Stripe credentials, no production or staging access, no customer data.
| Run | Head | Result |
|---|---|---|
| 43730903412 | `9078d0e` | 6 passed, 12 failed, 10 setup errors, 0 skipped — API exited at start-up (UID 10001 could not traverse the runner checkout under `/home/runner`) |
| 43731260548 | `2768ebb` | 12 passed, 16 failed — source staged under `/opt`; first failure a stale pre-fix expectation (a rejected upload now leaves no orphan file) which cascaded because the private directory was only handed to the app user afterwards |
| 43731908173 | `6db71f5` | 27 passed, 1 failed — **real recovery-tool defect**: piped to `python3 -` on Python 3.12 (the container image) `__file__` is `"<stdin>"`, the repository root resolved to `/`, so every state path was refused as "inside the repository" |
| **43732293051** | `6a5a3b3` | **28 passed, 0 failed, 0 errors, 0 skipped, 136.69 s; pytest exit 0, verifier exit 0** |
| **43732968698** | `77432c2` | **28 passed, 136.82 s; verifier exact (cases=28 passed=28 failed=0 errors=0 skipped=0); staged runtime identical to f503041; no credentials present; UID 10001 can read, import and run the recovery tools** |
Integrity corrections: the workflow originally read `PIPESTATUS[0]` after an evidence copy (could mask a failing pytest) — fixed, and a fail-closed JUnit verifier (`verify_pytest_junit.py`, 5 tests) now requires exactly 28 passed. Administrator records stay root-only; application upload state lives in a separate UID-10001-owned directory. The 28 tests cover: normal writes before the barrier, in-flight transactions, API mutations blocked, direct DB writes blocked, background jobs blocked, upload writes blocked, reads available, monitoring of violations, emergency release and thaw, capture with matching database/upload fingerprints and restore proof, normal service after release, approval gate and operator preflight.
Scope: this closes the writer-barrier REHEARSAL gate. It is not hosted recovery and not a staging/production barrier run.

### 12.7 Matched hosted recovery drill (logical restoration on the SAME PostgreSQL server)
| Run | Result |
|---|---|
| 43732786394 | Original drill: task_rc 0. PostgreSQL 18 server, 18.7 client (checksums verified). T0 db digest `5a5fde50f5a703b5` (53 tables, 13,072 rows, `c7d8e9f0a1b2`), uploads digest `41ff6b6fa846ceab` (34 files, 479,053 bytes); dump 734,356 bytes; restore 0.9 s; every table, sequence, row total and upload byte-identical; per-tenant counts identical across 57 tenants; disposable DB dropped. **Gaps found: T0==T1 was printed but not asserted; the migration was compared to the source rather than pinned; no database-to-file or restored-application checks.** |
| 43733902492 | Hardened drill, failed closed at the restored-application step: 0 eligible tenants. Cause: my owner query used lowercase `owner`; real owners are stored as `Owner`. All database-side assertions passed. |
| **43734792050** | **Hardened drill PASS** (`task_rc=0 restore_rc=0`): pg_dump major equals server major (18); **T0==T1 mandatory PASS**; source migration exactly `c7d8e9f0a1b2` at T0 and T1; restored migration exactly `c7d8e9f0a1b2`; restored database equals capture (all tables rows + content hash, sequences, alembic); row total equal; **34 uploads byte-identical (SHA-256, size, path)**; per-tenant counts identical across 59 tenants; non-empty document dataset; **every document row has its private file with the recorded size**; a SEPARATE application process on loopback against the RESTORED database and RESTORED uploads: ready; **tenant A and tenant B owners each downloaded their own document with bytes equal to the restored manifest SHA-256**; **cross-tenant reads returned 404 in both directions**; **anonymous refused**; **list scoping holds**; disposable database dropped, scratch files removed. T0 digest `68affc6e45e034d8` (13,086 rows). Dump 735,114 bytes in 0.2 s; restore 1.0 s; total 7.2 s. Recovery point 2026-11-15T16:20:00Z. |
Negative evidence for the hardened assertions (local, PostgreSQL 16, seeded synthetic data): a wrong pinned revision fails both revision checks (exit 1); a document row whose private file is missing fails the database-to-file check and the restored retrieval (exit 1, no traceback).
**NOT proven:** recovery onto replacement database infrastructure (new server, new private-upload location); recovery from the original server being lost; point-in-time recovery (no PITR/WAL on this QA database; RPO = time since the dump); timing including provisioning a new Postgres service. These stay NOT EXECUTED (doc 21 §B9).

### 12.8 Exact-SHA restoration correction
The container-task restoration helper restored configuration but left the service on a branch-head build (`2b2f514`, runtime-identical, not the candidate). `restore_qa_config_pinned` now restores the original start and predeploy commands, removes every temporary variable, redeploys EXACTLY `f503041`, requires that deployment id to be a stable SUCCESS at that exact commit, verifies health/readiness, the web deployment commit, closed admission and no leftover `QA_`/`STRIPE_`/`RESEND_` variables, and prints one identity line. Five stubbed tests prove a branch-head commit, a different deployment id, a Railway rejection and a non-SHA input are refused. First live use: run 43734792050 (line in §12.2).

### 12.9 Gate classification after this update (PASS / FAIL / PARTIAL / BLOCKED / NOT EXECUTED / UNKNOWN)
| Gate | Status | Evidence level |
|---|---|---|
| Product CI on f503041 | **PASS** | CI |
| Migration head assertion | **PASS** (isolated QA); production **UNKNOWN** | deployed isolated QA |
| Authenticated acceptance (217 checks) | **PASS** | deployed isolated QA |
| Tenant and role isolation (88/88 + 8/8) | **PASS** | deployed isolated QA |
| Hosted security (38/38) | **PASS** | deployed isolated QA |
| Bounded capacity (≤30 users) | **PASS** (bounded; no production-scale claim; p99 3.8 s at 30 users) | deployed isolated QA |
| Stripe real lifecycle, failed renewal, recovery, entitlements, cancel/resume | **PASS** | isolated QA, Stripe sandbox |
| Stripe locally-signed resilience (idempotency, reconciliation, stale, concurrency, cross-tenant, saturation) | **PASS** (local-signed, labelled) | isolated QA |
| Billing-failure log markers | **PASS** (logging); alert delivery **NOT EXECUTED** | isolated QA |
| Hosted portal page | **PARTIAL** (page loads; interactive portal actions and customer scoping on the page NOT EXECUTED) | hosted browser |
| Hosted Checkout: successful card | **BLOCKED** (external captcha) | — |
| Hosted Checkout: declined card | **BLOCKED** (external captcha) | — |
| First-ever customer Checkout (never-subscribed workspace) | **BLOCKED** (needs approved admission-enabled disposable environment) | — |
| Real-timeout/429/5xx recovery against hosted Stripe | **NOT EXECUTED** (local/CI only) | local/CI |
| Writer-barrier rehearsal (28 tests) | **PASS** (disposable runner, local PostgreSQL) | CI/disposable runner |
| Matched hosted recovery, logical, same PostgreSQL server | **PASS** | deployed isolated QA |
| Replacement-infrastructure recovery | **NOT EXECUTED** | — |
| PITR / WAL | **UNKNOWN** | — |
| Shared-staging A1 capture and recovery point | **BLOCKED** (needs approved window and operator) | — |
| Email verification and password-reset click-through | **BLOCKED** (needs real mailbox receipt) | — |
| Monitored support, alert delivery | **BLOCKED / NOT EXECUTED** | — |
| Legal policies and operator details | **BLOCKED** (owner/solicitor) | — |
| Catalogue import | **NOT EXECUTED** (no importer exists; gap documented) | — |
| Approval, quote-revision and supplier-setting decisions | **PARTIAL** (implemented and verified; owner decisions outstanding) | deployed isolated QA |

### 12.10 Verdict
**HOLD / NO-GO for customer release stays.** The product candidate has strong deployed evidence for functionality, isolation, security, billing lifecycle and logical recovery. It is not releasable because mandatory gates remain open: hosted Checkout (both outcomes) and first-ever customer Checkout, real email click-through, replacement-infrastructure recovery, shared-staging recovery point, monitoring and alert delivery, support, legal/commercial adoption, catalogue import and owner decisions. Green CI is not treated as release readiness.

## 13. UPDATE — PR #77 review and merge; owner acceptance pack (15 Nov)
- **PR #77** (recovery tooling) reviewed at head `154fce1a05466268990057717bb651130a094433`: both CI runs green on all five jobs, nine tooling files only, runtime paths byte-identical to `f503041`, no production or shared-staging reference. Merged as `631477bbbc55236271d6bbd5d88930055787c4b7` on the closure branch following the owner's instruction "merge pr77 when green". Findings and two LOW follow-ups are in doc 22 section 1. Product candidate unchanged.
- The matched hosted recovery, the writer-barrier rehearsal and exact-SHA restoration evidence in section 12 stand; nothing was repeated.
- **Owner acceptance pack and provisioning specifications: doc 22.** Nothing was provisioned, no paid resource was created, admission was not opened, no owner approval, mailbox receipt, legal acceptance or alert delivery is assumed.
- Design fact recorded: admission is a binary `open|closed` switch with no per-identity allowlist, so the first-customer environment must be restricted by isolation, not by the application (doc 22 section 2.1).
- Verdict unchanged: **HOLD / NO-GO**. Remaining sequence: doc 22 section 6.

## Replacement-infrastructure recovery — acceptance record (16 Nov)

**Recovery gate: restore and application checks PASS (run 43783158019, tooling commit 794c0b1e). Cleanup gate: OPEN. Cost reconciliation: OPEN. Verdict stays HOLD / NO-GO.**

Scope: this proves restoration of a matched synthetic database and uploads bundle onto *replacement infrastructure* (new PostgreSQL 18, new volume, application image built from exactly `f503041`). It does not prove production disaster recovery, PITR, or recovery of the production database.

Verified from the execution log (not summary): bundle size and SHA-256; dump, uploads archive, manifest and integrity record checksums; matched T0 = T1 capture; empty replacement database; `pg_restore`; migration exactly `c7d8e9f0a1b2`; database fingerprint (every table's rows and content hash, alembic) equal to the capture; row total and per-tenant counts equal; all 34 uploads byte-identical (path, size, SHA-256); every document row has its file with the recorded size; restored application ready; tenant A and tenant B authenticated retrieval with bytes equal to the manifest hash; cross-tenant 404 in both directions; anonymous request refused; tenant-scoped document lists; the restored application also healthy under the real start command and `/health`. "sequences present=0" in the log is a count of `pg_sequences`; sequence values (where any exist) are covered by the database fingerprint, so no separate sequence claim is made.

Timings: provisioning to first verified retrieval 41 s; database restore 1.0 s; uploads restore 0.1 s; verification 2.6 s; whole run 75 s (excludes teardown). Recovery point 2026-11-15 19:21 UTC (logical dump; data after that instant is not recoverable).

The workflow run shows `failure` overall because the final emptiness step fails while volumes remain; the recovery step itself concluded `success`.

### Attempts (all recovery-related runs; no further recovery run will be dispatched)
| Run | Purpose | Outcome |
|---|---|---|
| 43751882963 | Capture in isolated QA | success (bundle artifact) |
| 43779884919 | First provision | failed: project token cannot connect a repository |
| 43780335001 | Teardown | volumes cannot be deleted by a project token |
| 43780457231, 43781015450 | Provision from private image | failed: platform rejected the deployment |
| 43781520455 | Read-only deployment inspection | showed runtime pick `unsupported_feature` |
| 43781795834, 43782185672 | Volume-free probes | image accepted; ~1.4 MB of variables rejected |
| 43782663691 | Recovery with bundle in image | restore PASS; uploads refused the protected active-upload path (by design) |
| 43783158019 | Recovery, volume at `/var/lib/recovery-uploads` | **all checks PASS** |

### Cleanup state (read-only inventory, run 43784134269)
Services 0. Staged changes: none returned. Buckets: the read query was not valid for this token (my query error), so **buckets are not yet confirmed zero**; ChatGPT/owner to confirm. No recovery workflow is active. Volumes remaining (11) — a project token is not authorised to delete them; deletion is left to the owner or ChatGPT's connected Railway account:

| Volume ID | Name |
|---|---|
| 0e609a57-ab6f-4774-ae9f-36aa266727e9 | rec-app-volume-HZUm |
| 1024a89c-d735-45de-a8ab-391a158abf65 | rec-pg-volume-asfu |
| 129168ba-ddb3-477d-8013-6783d31604be | rec-app-volume |
| 682b1ed7-a59a-4fc3-95c7-96d1cf903739 | rec-pg-volume |
| 682c70de-33ee-4d09-9498-27b28f920b4b | rec-pg-volume--oDX |
| 7cc2e16e-f938-4b5f-8d68-79a7c8f8409e | probe-volume-volume |
| adfd2c31-f4d3-4c6b-8bf5-bc2710487292 | rec-app-volume-ShzU |
| bc66f5bb-d9ca-4def-83e1-fb6688f0d575 | rec-pg-volume-aKbZ |
| d3015d38-845e-40d2-bb35-4e67e864aa1b | rec-app-volume-uHrU |
| d9be319e-6da0-43a9-b5ab-847ee488cb1c | rec-pg-volume-pAlJ |
| f25458d5-f5fb-442b-9967-74aa168d23bb | rec-app-volume-Xdkt |

### Cost reconciliation — OPEN
A project token cannot read billing. Estimate only: all services together lived about 20 service-minutes across the attempts (negligible compute). Orphaned volumes are the uncertain item: if billed on provisioned size they accrue continuously until deleted. Do not treat the cap as respected until the Railway usage page for the disposable project is read; the owner or ChatGPT should record the actual incremental total. Nothing further is provisioned until that is done.

### Next, in order
1. Delete the 11 volumes above (and confirm no buckets) with the connected Railway account. 2. Dispatch workflow mode `inventory` for the final read-only check: zero services, zero volumes, zero buckets, zero staged changes. 3. Record actual spend. 4. Only then close teardown and prepare the first-customer environment.
