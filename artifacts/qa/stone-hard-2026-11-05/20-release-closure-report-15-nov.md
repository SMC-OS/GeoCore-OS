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
