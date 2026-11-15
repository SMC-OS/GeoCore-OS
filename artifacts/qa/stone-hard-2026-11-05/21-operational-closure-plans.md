# 21 — Operational closure plans and owner acceptance checklists (15 Nov 2026)

Plans only. **Nothing here has been executed.** Production and shared staging are untouched; admission stays closed; no live Stripe. Blank fields are owner/operator inputs. Existing reviewed tooling is reused: `scripts/recovery/operator_preflight.py`, `a1_capture.py`, `writer_barrier.py`, `matched_bundle.py`, `content_fingerprint.py`, `restore_upload_bundle.py`, `pitr_diagnostics.sql`; runbooks `docs/STAGING_RUNBOOK.md`, `docs/PRODUCTION_RUNBOOK.md`; approval template `a1-owner-approval.TEMPLATE.json`; docs 13 and 14.

## B1. Shared-staging backup and recovery rehearsal
**Preconditions (all must be recorded before the window; any missing item cancels it)**
| # | Item | Owner input |
|---|---|---|
| 1 | Operator (hands on keyboard) | name, contact |
| 2 | Independent reviewer (watches, can call abort) | name, contact |
| 3 | Window, exact UTC start/end (the 10 Nov window expired; do not reuse) | |
| 4 | Notification to staging users and stakeholders >= 48 h ahead, with start/end, expected write freeze, contact | text and recipients |
| 5 | PostgreSQL 18-compatible client on the operator machine (server major must match; QA server is 18.6) | `pg_dump --version` evidence |
| 6 | Private encrypted storage (provider, encryption at rest, access list, free space >= 3x database + uploads) | |
| 7 | Signed approval file from the template (exact SHA, window, operator, storage) | |
| 8 | Railway provider backups and point-in-time-recovery status read first (read-only) | screenshot or CLI output |
**Sequence (existing tool, in order)**
1. `operator_preflight.py` (tools, disk, transfer timing, PITR diagnostics via `pitr_diagnostics.sql`); stop on any red item.
2. Barrier v2 engage (write fence). Refuses inside the quiet period and on a dirty engage; record time T0.
3. `a1_capture.py`: content fingerprints (database and uploads) at T0, custom-format `pg_dump`, matched uploads archive, T1 fingerprints; four hashes. Stop if T0 and T1 differ.
4. Safe unfreeze; record the write-block duration. Never leave the fence engaged on failure: use the emergency release in doc 14.
5. Copy to the private store; verify checksums at the destination.
6. Scratch restore **into a disposable database and an empty scratch directory** (never the active database or volume); compare per-table row counts and content hashes, sequences, Alembic revision, every upload file byte for byte, per-tenant counts; apply migration head `c7d8e9f0a1b2` to the scratch copy and run catalogue validation there.
7. Record recovery point (T0), backup duration, restore duration, total RTO for this data size; destroy scratch resources.
**Abort conditions:** any mismatch, free space below threshold, fence cannot be released, reviewer calls abort, window overrun. **Rollback:** release fence; nothing else was changed (no replace, downgrade, delete or restore of shared data is part of this plan). **Escalation:** operator, then reviewer, then owner; incident note filed the same day.
**Acceptance:** evidence pack with all hashes equal, scratch restore verified, measured RPO/RTO written to the ledger (gates RECOVERY-POINT, SCRATCH-RESTORE, PITR-WAL).

## B2. Production read-only inspection checklist
No write, migration, deployment, restore or secret value is read. Record findings only.
1. Identity: Railway project and environment names/IDs; services and their deployed commit SHAs; database server version (`SHOW server_version`), `system_identifier`, database size.
2. Current revision: `SELECT version_num FROM alembic_version` (expect an older head than `c7d8e9f0a1b2`; list the pending chain and read each pending migration; all recent ones are additive and reversible).
3. Backups: provider backup list (dates, retention), whether point-in-time recovery is enabled, WAL archiving state via `pitr_diagnostics.sql`; name the most recent verifiable recovery point and its age.
4. Migration compatibility and impact: table sizes and row counts for tables touched by pending migrations; lock behaviour (all additions are nullable or defaulted columns plus one index); estimated duration; confirm the API start-command migration gate and `/ready` behaviour.
5. Catalogue import readiness: **no dedicated catalogue-import tool was found in `scripts/` (local search). The operator must name the import mechanism and data source, run it first in scratch, and compare counts; status UNKNOWN until then.**
6. API and web configuration: variable NAMES only (`APP_ENV`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_BASE_URL`, `PUBLIC_API_BASE_URL`, `NEW_ACCOUNT_ADMISSION`, billing and mail variables present or absent), `API_PROXY_URL` and `NEXT_PUBLIC_API_URL` set for same-origin sessions, health and readiness responses.
7. Environment isolation: production uses its own database, uploads volume, Stripe account mode and webhook secret, mail sender and JWT secret; none shared with QA or staging; no QA variable names (`QA_*`) present.
8. Admission: `GET /api/v1/auth/admission` returns `signups_open=false`; `POST /api/v1/auth/signup` returns 403.
9. Billing posture: live-mode vs test-mode key presence by prefix only (never the value); webhook endpoint registered for production only; price IDs in production are the intended ones; no active real subscription is touched.
10. Output: a signed read-only inspection record (operator, time, commands used).

## B3. Monitoring and support: owner acceptance checklist
| Control | Required decision or account | Verification (to be run by owner/operator) | Status |
|---|---|---|---|
| API availability and readiness (`/health`, `/ready`) | uptime service account (provider, alert recipients) | deliberately fail a check and receive the alert | NOT EXECUTED |
| Web availability (`/login`) | same service | as above | NOT EXECUTED |
| Database connectivity, CPU, memory, connections, disk/volume | Railway alerts or external agent; thresholds | threshold test or forced alert | NOT EXECUTED |
| API error rate and latency | log/metrics destination; suggested starting alerts: 5xx > 2% over 5 min, p95 > 2 s over 10 min, pool timeout errors | synthetic burst | NOT EXECUTED |
| Stripe webhook failures and reconciliation errors | Stripe dashboard webhook alerts to a named mailbox; alert on repeated 5xx from `/billing/webhook` (a permanent tenant-linkage conflict returns 500 on every retry, so it must page a human) | resend a failing test event | NOT EXECUTED |
| E-mail delivery failures and bounces | Resend webhook and dashboard alerts; sending-domain reputation owner | send to a bounce test address | NOT EXECUTED |
| Backup failure alerts | provider notification plus a scheduled restore-test reminder | simulated failure notice | NOT EXECUTED |
| Error tracking and incident alerts | tool and owner | forced exception | NOT EXECUTED |
| Support mailbox | address, owner, delegate, monitored hours, auto-acknowledgement text | message sent from an outside address and answered within target | NOT EXECUTED |
| Response and escalation targets | owner to adopt (no SLA exists; do not promise 24/7 or any RTO/RPO) | written targets by severity | UNKNOWN |
| Incident severity | S1 outage or data loss/cross-tenant exposure; S2 major feature or billing failure; S3 degraded; S4 cosmetic. Owner to set response times for each | tabletop walkthrough | UNKNOWN |
| Operational ownership and out-of-hours | named primary and backup, contact method, handover rule | contact test | UNKNOWN |

## B4. Legal and commercial acceptance checklist (builds on doc 18, section 3)
Each line needs the owner's own act; **no approval, signature or acceptance is recorded by this document.**
| Item | Facts on record (not re-verified) | Needed | Accepted by / date |
|---|---|---|---|
| Operator identity on all documents | sole trader, no company number; virtual-office address | owner confirms wording and address for notices | |
| Terms of service | draft in app | solicitor review | |
| Privacy notice and ICO registration/fee | undecided | owner and solicitor | |
| DPA, processors and sub-processors, transfer routes | Railway, Stripe, Resend (per runbooks) | confirm list, regions, mechanism | |
| Data retention and deletion | `docs/RETENTION_ARCHITECTURE.md` | owner confirms periods | |
| Commercial pricing and plan terms | eight GBP prices verified against the sandbox | owner confirms prices, trial terms, VAT-inclusive or exclusive | |
| Subscription cancellation and renewal wording | behaviour implemented (cancel at period end, resume) | owner and solicitor wording | |
| Refund policy | no refund scenario tested | owner decision | |
| VAT position | no VAT number evidenced; none displayed | accountant | |
| Customer support terms | none adopted | from B3 | |
| Initial operating limits communicated | <=10 workspaces, <=30 concurrent users (measured, small data) | owner accepts | |


## B5. Release-defaults proposal for owner review (implemented, tested, awaiting approval)
- **Approval policy:** the Owner approves financial commitments (quotes, variations, purchase orders). Staff can prepare and submit but receive 403 on approval. An Owner can delegate or withdraw approval authority per Staff user (Team settings); the server enforces it on every approval route; approver, amount and time are recorded; a Staff user cannot self-delegate and no other workspace can delegate.
- **Quote revision behaviour:** an approved quote is never edited. "Create revision" makes a new draft (revision 2, 3, ...) with the same customer, scope, lines and totals; the original keeps its status, total, approval time and approver; history lists every revision with its approved amount; revising a draft is refused (edit it instead).
- **Supplier settings (per workspace, never prefilled):** legal name, trading name, company number, business address, VAT registered yes/no and VAT number, bank account name/sort code/account number or payment instructions, payment terms (default 30 days). Issuing an invoice is refused with the list of missing fields until these are complete for the workspace's VAT status (a non-VAT-registered workspace cannot charge VAT). On first issue the supplier details are frozen onto the invoice with a calculated due date; later changes never alter an issued invoice.
- **Owner decision needed:** accept, amend or reject each of the three behaviours; evidence is in the ledger (isolated QA) and doc 19.

## B6. Supplier catalogue-import mechanism
Search of `scripts/`, `app/` and `docs/` (local, 15 Nov) found catalogue READ/pricing code (`app/catalogue`, quote and procurement use) but **no supported import tool or documented import procedure**. The production catalogue-import gate therefore has no mechanism to verify. Proposed (separately reviewed, not implemented): an idempotent, dry-run-first importer reading a versioned CSV/JSON source, writing inside one transaction, producing a count/hash reconciliation report, runnable first against the scratch restore (B1 step 6) and only then production in an approved window. Until the owner confirms how the production catalogue is populated today, the gate stays UNKNOWN.

## B7. Owner-assisted hosted Checkout procedure (Stripe TEST MODE only)
Why: automated Stripe-hosted Checkout runs reach the payment form and click pay, then Stripe presents a captcha to the headless datacenter browser (run 43733515870: `captcha=true`, no redirect or decline in 90 s). That is an external anti-automation control; it is not bypassed. A human completes the page in a normal browser.
Preconditions: isolated QA on `f503041` (check `GET /ready`), admission closed, Stripe sandbox keys configured by the Stripe workflow (never live), a synthetic existing-contract workspace (see B8 for the never-subscribed case). No real card, no real customer.
1. The agent provisions two synthetic owners, links workspace P to a sandbox customer, cancels its subscription and PAUSES before restoration (to be built as a `scope=owner-assisted` mode: it prints only the synthetic owner email and a one-time password through the masked channel to the owner, never a Checkout URL in logs).
2. Owner logs in as P in a normal browser, opens Billing → choose plan → Checkout.
3. **Declined case:** enter `4000 0000 0000 0002`, any future expiry, any CVC, any postcode. Expected: Stripe shows a decline message, no subscription is created, workspace stays restricted (HTTP 402 on workspace routes). Screenshot the page (no URL bar).
4. Close, reopen Checkout. **Successful case:** `4242 4242 4242 4242`. Expected: redirect back to `/pricing?billing=success`, workspace becomes active, workspace routes return 200.
5. Owner replies with: declined message shown (Y/N), success redirect (Y/N), workspace active (Y/N), screenshots.
6. The agent then verifies from the API/Stripe: session status, subscription tenant metadata equals workspace P, entitlements, Stripe-delivered events (pending_webhooks 0), then restores QA to the exact candidate and deletes the synthetic Stripe objects.
Record the two outcomes as separate acceptance results. This does NOT verify first-ever Checkout for a never-subscribed workspace (B8).

## B8. Proposal: disposable admission-enabled first-customer acceptance environment (approval required before provisioning)
Purpose: execute literal first-ever hosted Checkout, registration, email verification and onboarding, which the closed-admission acceptance environment correctly refuses.
Proposed safeguards (all required):
- A NEW Railway project/environment named for the run, separate from `geocore-qa-20261110`, shared staging and production; own PostgreSQL service, own volume for private uploads; synthetic data only; never connected to a customer domain.
- Stripe TEST keys only (`sk_test_`/`rk_test_`, refused otherwise); a dedicated sandbox webhook endpoint pointing at the disposable API.
- Admission enabled only inside this environment via its own variable, with a hard allowlist of synthetic owner emails (`@acceptance.invalid` plus the owner's own test alias); no public DNS, no link published; basic network restriction where Railway allows it.
- Resend sandbox sender only; mail goes to the owner's test mailbox (B10).
- Pinned to the exact candidate commit (`f503041`) via the pinned deploy mechanism; migration head asserted in the container; workflow refuses any other project or environment id.
- Teardown procedure written first: delete the Stripe sandbox customers/subscriptions created, delete the environment (database and volume), remove the webhook endpoint, remove temporary secrets, confirm nothing remains with a read-only listing.
- Cost: one small Postgres service and one volume for under a day (owner to confirm acceptable).
Executes: registration → verification email click-through → workspace → first hosted Checkout (success and decline, via B7 by the owner) → entitlement → cancel/resume.
**Nothing is provisioned and admission is not opened until the owner approves this proposal in writing.** Until then literal first-ever Checkout stays BLOCKED.

## B9. Proposal: replacement-infrastructure recovery exercise (approval required before paid resources)
Purpose: prove recovery without relying on the original database server or the original private-upload location (the current drill is logical restoration on the same PostgreSQL server).
Smallest safe exercise (synthetic data only):
1. Capture from isolated QA with the existing hardened drill machinery (dump + matched uploads bundle + manifests + checksums).
2. Provision a disposable replacement: one new PostgreSQL service (same major version, 18) and one new empty volume in a separate disposable Railway environment (this is the paid resource).
3. Restore the dump into the replacement and the uploads bundle into the replacement volume, using only the bundle and recorded checksums — no access to the source database or the source volume.
4. Start a disposable application against the replacement database and volume; repeat the drill's checks: migration `c7d8e9f0a1b2`, fingerprints, per-tenant counts, 34-file manifest, database-to-file consistency, authenticated retrieval for two tenants, cross-tenant 404, anonymous refusal.
5. Measure: time to provision, restore time, first successful authenticated retrieval, and the recovery-point limit (a logical dump gives RPO = time since the dump; there is no PITR/WAL on the QA database, and production PITR/WAL status remains UNKNOWN — doc 21 B2).
6. Tear down the disposable environment and verify nothing remains.
Approval needed: permission to provision the two paid resources for under a day and to create the disposable environment. Not executed; classified NOT EXECUTED.

## B10. Consolidated owner-action checklist
| # | Action | Unblocks | Needs |
|---|---|---|---|
| 1 | Approve or amend the disposable admission-enabled first-customer environment (B8) | first-ever Checkout, registration, verification click-through | written approval; cost acceptance |
| 2 | Real email verification AND password-reset click-through: nominate the test mailbox; the agent sends from the disposable/sandbox sender; owner reports receipt, subject/sender, link click, expired and reused link behaviour | REGISTRATION-EMAIL, PASSWORD-RESET gates | mailbox access by the owner (the agent has none) |
| 3 | Complete the owner-assisted hosted Checkout, declined then successful (B7), and report | hosted Checkout (both outcomes) | ~15 minutes at a browser |
| 4 | Approve or amend the replacement-infrastructure recovery exercise (B9) | replacement-infrastructure recovery | approval for two small paid resources |
| 5 | Approve a shared-staging A1 window with a named operator, or decide the alternative (doc 21 B1) | recovery point on shared staging | window, operator, owner approval file |
| 6 | Read-only production inspection (doc 21 B2): PITR/WAL status, service configuration, admission setting | PITR-WAL, PROD-CONFIG, ADMISSION | operator with read-only Railway |
| 7 | Monitoring and support: name the support mailbox and targets; set alert recipients; send a real test enquiry; confirm a billing-failure alert actually reaches the owner (log marker exists; delivery unverified) | MONITORING, SUPPORT | owner decisions and a monitored inbox |
| 8 | Adopt the legal policies and supply operator/company details (doc 21 B4) | LEGAL-COMMERCIAL | owner and UK solicitor |
| 9 | Catalogue import: decide the import method and approve the proposal (doc 21 B6); no importer exists | catalogue acceptance | owner decision |
| 10 | Approve the release defaults: approval authority, Staff powers, quote-revision workflow, invoice-supplier settings (doc 21 B5) | ROLE-PERMS, QUOTE, INVOICE-SUPPLIER confirmations | owner approval |
| 11 | Review and merge PR #77 (recovery tooling) after required CI is green; decide whether `qa/stripe-ext` is integrated | tooling integration | reviewer |

Smallest sequence to a controlled pilot: items 2 and 1 together (mailbox + disposable environment) → item 3 → item 7 (support and alerts) → item 8 (legal) → item 10 (defaults) → item 9 (catalogue) → items 5 and 6 (staging window and production read-only inspection) → items 4 and 11. Production deployment, shared-staging replacement and public admission stay out of scope until each gate above is explicitly verified.

## B11. Pointer — acceptance pack and detailed specifications
Doc 22 supersedes the outline in B7-B10 where it differs: detailed provisioning specification and teardown for the disposable first-customer environment, the owner-assisted Checkout workflow design and the held instruction sheet, the replacement-infrastructure recovery exercise with cost estimates, the sequential-reuse assessment, and the single thirteen-item owner acceptance table with decision, default, unblocks, mandatory flag and PASS evidence.
