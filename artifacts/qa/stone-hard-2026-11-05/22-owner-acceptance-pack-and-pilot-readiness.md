# 22 — Owner acceptance pack and pilot-readiness preparation (15 Nov)

Status: **PREPARED, NOTHING PROVISIONED, NOTHING OPENED.** Production, shared staging, live Stripe and customer admission are untouched; PR #68 and #69 remain on HOLD. No owner approval, mailbox receipt, legal acceptance or alert delivery is assumed anywhere in this document: each is an open item until its stated evidence exists. Verdict: **HOLD / NO-GO**.

Revision separation (unchanged): product runtime candidate `f503041822923bd4b07a0bebaa6748ba71873740`; merged QA tooling `2b2f514` (PR #76) and `631477bbbc55236271d6bbd5d88930055787c4b7` (PR #77); acceptance tooling `qa/stripe-ext` (not merged).

## 0. Owner approval record

| Item | Recorded decision | Source | Scope and limits |
|---|---|---|---|
| Cost caps (acceptance pack item 3) | **Approved: both caps** — disposable first-customer environment up to **$10**, replacement-infrastructure recovery exercise up to **$3** | Owner chat message "approve both caps", 15 Nov 2026, this session | Approves spending up to those caps only, for the specifications in sections 2 and 4, with the recovery exercise first. It is not approval to open admission anywhere other than the disposable environment, not approval of the mailbox, not approval of any other pack item, and not authorisation to touch production, shared staging or live Stripe. Teardown must be verified and the final dashboard cost recorded. If projected spend would exceed a cap, work stops and the owner is asked. |

Still required from the owner before any resource can be created (the agent holds only the isolated-QA project token and cannot create Railway projects or tokens):
1. Create the disposable Railway project with an unguessable name, and a separate environment for each exercise (recovery first, first-customer second).
2. Create one project token per environment and add the repository secrets `GEOCORE_DISPOSABLE_RECOVERY_RAILWAY_TOKEN` and `GEOCORE_DISPOSABLE_FIRSTCUSTOMER_RAILWAY_TOKEN` (Railway dashboard → project → Settings → Tokens → create token for the environment; GitHub → repository → Settings → Secrets and variables → Actions).
3. Set a Railway usage alert at $5 and a hard limit if the plan offers one.
4. Confirm the test mailbox (pack item 1) and that the Resend account's unverified sender delivers only to its owner (section 2.2 item 3).
5. Confirm the Stripe sandbox keys and a dedicated sandbox webhook endpoint are the test ones (names only; the agent never needs to see them).
When these exist the agent proceeds in this order: recovery exercise (section 4) → teardown → first-customer environment (section 2) → owner session → teardown.

## 1. PR #77 review record (recovery tooling) and merge

Reviewed at the exact head `154fce1a05466268990057717bb651130a094433`; merged as `631477bbbc55236271d6bbd5d88930055787c4b7` after the review, following the owner's instruction "merge pr77 when green".

| Check | Finding |
|---|---|
| Required CI | PASS. Both runs for this head (push 43734784943, PR 43735275937): backend, frontend, e2e, dependency-audit, secret-scan all success. PR mergeable state `clean`. |
| Scope | 9 files: 2 workflows, 3 scripts, 1 recovery tool, 3 test files. Nothing under app, alembic, apps, packages, deploy, Dockerfile or lockfiles: `git diff f503041..154fce1` over the runtime paths is empty. The product candidate is unchanged. |
| Python 3.12 piped-script fix | Correct. `_real_file()` accepts only an existing file whose `__file__` does not start with `<` and resolves it; piped stdin gives no real file, so the repository root is the non-existent sentinel and state files outside the repository are accepted. A file run still refuses a state file inside the repository (verified: exit 2). `db-engage` still refuses to run from stdin, now via the same helper. |
| Rehearsal fail-closed | Correct. Pytest status is captured immediately after the pipeline; a separate verifier requires exactly 28 cases, 28 passed, zero failed/errors/skipped; a missing or invalid JUnit file fails; five unit tests include all-skipped and wrong-count cases. Limitation (LOW): the verifier checks the count, not the 28 specific test names. |
| Hardened drill assertions | Correct. T0==T1, pinned migration on source and restored, non-empty documents, database-to-file existence and size, restored application start, two-tenant retrieval with bytes compared to the restored manifest SHA-256, cross-tenant 404 in both directions, anonymous refusal, list scoping. Negative runs fail the drill. The query bug found in the first hosted run (role stored as `Owner`) is fixed. |
| Restored application | The restored process is loopback-only, uses the restored database and restored uploads, and is terminated afterwards. Follow-up (LOW): the drill does not refuse to run while temporary `STRIPE_`/`RESEND_` variables are set, in which case the restored process would inherit them; the container-task workflow runs it only from the baseline configuration, so this did not occur. Recommended guard in a later tooling change. |
| Exact-SHA restoration | Correct and proven. `pin_candidate_api` requires the 40-hex SHA, the requested deployment id, a stable SUCCESS and the exact commit; `restore_qa_config_pinned` also checks baseline commands, no leftover variables, health, ready, web commit and closed admission. Five stubbed tests; live proof run 43734792050. |
| Production / shared staging | No mutation. The changed workflows contain fixed isolated-QA ids only; the rehearsal workflow has no secrets and asserts no credential-like variable. |
| Evidence preserved | Rehearsal runs 43732293051 and 43732968698 (28/28), hosted drill 43734792050. Not repeated. |

## 2. Disposable first-customer acceptance environment (design from doc 21 §B8; NOT provisioned)

Purpose: literal first-ever customer journey — registration, verification email click-through, workspace, first hosted Checkout (decline and success, completed by the owner in a normal browser), entitlement. The existing acceptance environment must not be opened.

### 2.1 Fact that shapes the design
Admission in the product is a single switch `NEW_ACCOUNT_ADMISSION=open|closed` (app/core/admission.py). **There is no per-identity allowlist.** "Restricted to authorised test identities" therefore cannot be implemented in the application without a product change (which would create a new candidate and invalidate evidence). It is achieved by environment isolation instead.

### 2.2 Isolation safeguards (all required)
1. A NEW Railway project (owner-created) with its own environment; random, unguessable project/service names so the generated `*.up.railway.app` hostnames cannot be guessed; no custom domain, no DNS record, nothing linked from any public page.
2. Admission is `closed` by default; it is set to `open` only for the owner's session window (target ≤ 2 hours) and set back to `closed` before verification and teardown. A hard timer in the workflow closes it regardless.
3. Email: the Resend **unverified-domain sender**, which delivers only to the Resend account owner's verified address. Any stranger who found the URL could not receive a verification email and so could not verify an account. (To be confirmed against the owner's actual Resend account settings before relying on it.) The mailbox in doc 22 item 1 receives all mail.
4. Stripe TEST keys only: `sk_test_`/`rk_test_` and `whsec_` prefixes enforced; refusal otherwise; a dedicated sandbox webhook endpoint pointing only at this environment; the live-mode flag is asserted false on every object read.
5. Synthetic data only; no import from any other environment; its own PostgreSQL service and its own volume for private uploads; secrets generated fresh (JWT, owner password) and never printed.
6. Exact product candidate: deployed pinned to `f503041822923bd4b07a0bebaa6748ba71873740` (API first, then web), migration head `c7d8e9f0a1b2` asserted inside the container (the existing assertion), no traceback.
7. The workflows refuse any project or environment id other than the disposable ones, and refuse the isolated-QA, shared-staging and production ids by name. The disposable token is scoped to the disposable environment only.
8. A written teardown runs before provisioning is declared complete (see 2.5).

### 2.3 Resources and cost estimate (ESTIMATE — confirm in the Railway dashboard before approving)
Assumed Railway usage rates (verify, they change): memory about $10 per GB-month, CPU about $20 per vCPU-month, volume about $0.15 per GB-month, egress about $0.05 per GB.
| Resource | Size assumed | Approx. per day |
|---|---|---|
| API service | 0.5 GB, 0.25 vCPU | $0.33 |
| Web service | 0.5 GB, 0.25 vCPU | $0.33 |
| PostgreSQL 18 service | 0.5 GB, 0.25 vCPU | $0.33 |
| Volumes (database 1 GB + uploads 1 GB) | 2 GB | $0.01 |
| Egress and builds | negligible | < $0.05 |
| **Total** | | **about $1.05 per day** |
Maximum proposed: **at most 3 days = about $3.2; owner cap proposed at $10** with a Railway usage alert set at $5. Intended lifetime: one working day. Stripe test mode, Resend sandbox sender and GitHub-hosted runners add no charge. The Railway plan's base subscription is unchanged (if the account is on a plan with included usage credit, the real incremental cost may be lower or zero — to be read from the account).

### 2.4 Provisioning steps (owner and agent)
Owner: create the new Railway project/environment with unguessable names; create the project tokens (the agent cannot create them); add the two environment-scoped repository secrets `GEOCORE_DISPOSABLE_RECOVERY_RAILWAY_TOKEN` (environment `recovery`) and `GEOCORE_DISPOSABLE_FIRSTCUSTOMER_RAILWAY_TOKEN` (environment `first-customer`), and confirm `GEOCORE_QA_STRIPE_*` test keys and the Resend key are the sandbox ones; nominate the mailbox; approve the cost cap in writing.
Agent (after the owner step): create PostgreSQL 18 + volume + API + web from the pinned SHA; run migrations through the existing migrate gate; assert `QA_MIGRATION|c7d8e9f0a1b2|PASS`; set test-mode Stripe variables and the webhook secret; verify `/ready`, `/health`, admission closed, web login; hand the owner the URL through the secure channel; open admission only for the window; after the owner completes the journey, close admission and verify independently (API, Stripe, database read-only).

### 2.5 Teardown (written first)
Close admission → verify closed. Delete the synthetic Stripe test customers/subscriptions created by the run and expire open Checkout sessions. Delete the disposable webhook endpoint. Delete the environment (services, PostgreSQL and both volumes). Remove the repository secrets that were specific to it. Read-only listing of the project proves nothing remains. Record the final cost line from the dashboard.

## 3. Owner-assisted hosted Checkout (doc 21 §B7 made concrete; NOT READY)

Stripe's hosted page presents a captcha to the automated browser (run 43733515870). It is not bypassed: a person completes the page.
**Recommended placement:** run both journeys inside the disposable environment of section 2 with a brand-new workspace, so the same session proves **first-ever** Checkout (not only an existing subscriber). The existing-contract variant in the current QA environment remains an optional, lesser proof.
Workflow design (to be implemented only after the prerequisites exist, to avoid shipping an untested mutating path to QA):
- Credentials: the owner sets the test workspace password themselves (a repository secret read only by the workflow); the workflow never prints it, a Checkout URL, a session token or any Stripe object that carries a secret. Only pass/fail counts, ids with no secrets, and statuses are logged.
- Phase 1 (agent): prepare environment and synthetic workspace; verify ready; write "READY" status only.
- Phase 2 (owner, normal browser): journey A declined card, journey B successful card (sheet below).
- Phase 3 (agent, after the owner reports): independent verification through the API and Stripe — session status, subscription tenant metadata equals the workspace, no subscription after the decline, active entitlement after the success, Stripe events delivered; then restoration/teardown and cleanup of Stripe objects.
**Prerequisites not yet met:** the disposable environment is not provisioned; the owner password secret does not exist; the workflow mode is not built. Therefore the instruction sheet below is a DRAFT and must not be sent yet.

### Owner instruction sheet — DRAFT, HELD until the environment is genuinely ready
1. You will receive one link by a private message (not in any log). Open it in your normal browser.
2. Create your workspace with the test email the pack names; open the verification email in your test mailbox and click the link.
3. Go to Billing and choose Starter monthly → Checkout.
4. First attempt: card `4000 0000 0000 0002`, any future expiry, any CVC, any postcode. Expect a decline message. Take a screenshot (hide the address bar).
5. Second attempt: card `4242 4242 4242 4242`, same other details. Expect a redirect back and an active workspace. Take a screenshot.
6. Reply with: decline shown (yes/no), success redirect (yes/no), workspace active (yes/no), and the two screenshots. Do not send any password or link.
Only test cards. Nothing real is charged. About 15 minutes.

## 4. Replacement-infrastructure recovery (doc 21 §B9 made concrete) — EXECUTED 16 Nov: restore PASS, teardown INCOMPLETE

Smallest safe exercise proving recovery onto independent infrastructure, synthetic data only:
1. Capture from isolated QA with the hardened drill machinery: custom-format dump, matched uploads bundle, manifests, SHA-256 of every file and of the bundle, T0/T1 fingerprints, recovery point timestamp, expected revision `c7d8e9f0a1b2`. Store the bundle outside the source environment (runner artifact, retention 1 day) together with its integrity record.
2. In a NEW disposable environment (section 2.2 rules apply): PostgreSQL 18 (new service) and an empty volume (new). The restore job receives only the bundle and the integrity record — it is given no credentials, hostnames or paths of the source database or the source upload location, and the source is not reachable from it.
3. Restore: verify bundle and dump checksums first; `pg_restore` into the new database; extract uploads to the new volume; compare per-table row counts and content hashes, sequences and the single alembic revision; compare every restored file (path, size, SHA-256) with the manifest; check document row ↔ file relationships.
4. Start a disposable application against the new database and volume: two tenants authenticate and download their own documents (bytes equal the manifest hash), cross-tenant reads return 404 in both directions, anonymous is refused, document lists are tenant-scoped.
5. Record timing: environment provisioning, restore, first successful authenticated retrieval; state the recovery-point limit (a logical dump gives RPO = time since the dump; no PITR/WAL exists on the QA database; production PITR/WAL status is UNKNOWN).
6. Teardown and read-only verification that nothing remains.
Estimated cost: PostgreSQL 18 + volume + API + volume for under 4 hours ≈ **$0.30**; with a 24-hour margin ≈ **$1.40**; proposed cap **$3**.
**Evidence class:** PASS here proves restoration onto replacement infrastructure; it does not prove production recovery, PITR or recovery of the production database.

### 4.1 Can the disposable resources be reused sequentially?
| Item | Reuse? | Reason |
|---|---|---|
| Railway project shell, naming, workflows, token handling, cost cap | Yes | no data, no independence concern |
| Services, PostgreSQL and volumes from the first-customer environment | **No** | The recovery proof must show restoration into an EMPTY database and an EMPTY private-upload location that never held other data. A database that has served admission-open registrations and Stripe webhooks, and a volume with unrelated uploads, would contaminate the comparison and weaken the independence claim. It would also risk overwriting the first-customer evidence. |
| Order | Run the recovery exercise first, tear it down completely, then create the first-customer environment fresh (or in parallel in a separate environment of the same project). | Cost saved by reusing stores is under about $1; independence is worth more. |
Conclusion: reuse the shell and tooling, not the data stores. Two separate short-lived environments; combined worst-case ≈ $13 against the two proposed caps ($10 + $3).

## 5. Owner acceptance pack — one list, thirteen items

"Mandatory" = the gate cannot close without this owner action. Nothing below is assumed done.

| # | Item | Exact decision required | Recommended default | Unblocks | Owner action mandatory? | Evidence required for PASS |
|---|---|---|---|---|---|---|
| 1 | Test mailbox | Nominate a mailbox you can read. A QA mailbox variable already exists on the QA web service (`GEOCORE_QA_TEST_MAILBOX`): confirm it is yours or replace it | Use that mailbox with plus-aliases for every test | items 2 and 4; REGISTRATION-EMAIL, PASSWORD-RESET | Yes | You state the address is yours and you receive a test message sent by the agent |
| 2 | Email verification and password-reset click-through | Perform the procedure: register, receive the verification email, click; request a reset, receive, click, set a password; try the old session, an expired link and a reused link | Run in the disposable environment (item 3) | REGISTRATION-EMAIL, PASSWORD-RESET | Yes | You report subject, sender, delivery time, that each link worked once, that the expired/reused link was refused and that the old session stopped working; the agent confirms the database state (verified, token version changed). Screenshots without secrets |
| 3 | Infrastructure cost and approval | Approve (a) the disposable first-customer environment, cap $10 (about $1 a day) and (b) the replacement-infrastructure recovery exercise, cap $3; create the disposable Railway project and tokens | Approve both, run recovery first | FIRST-CUSTOMER-ACCEPTANCE, REPLACEMENT-INFRA-RECOVERY | Yes | Written approval naming the caps and the project; a read-only listing after teardown shows nothing left; final cost read from the dashboard |
| 4 | Owner-assisted Stripe Checkout | Complete the declined and the successful journey (sheet in section 3) inside the disposable environment | Do both in the disposable environment with a brand-new workspace | STRIPE-CHECKOUT, STRIPE-FAILURE (hosted decline) | Yes | Your yes/no replies and screenshots, AND the agent's independent API/Stripe verification (session status, tenant metadata, no subscription after decline, active entitlement after success) |
| 5 | Support mailbox and response targets | Name the mailbox shown to customers, hours and response targets | A shared mailbox on your operator domain; acknowledge within 1 working day; urgent billing/access issues within 4 working hours | SUPPORT | Yes | A real test enquiry is received and answered within the stated target; the mailbox appears in the product and legal pages |
| 6 | Operational alert recipients and actual delivery | Choose who receives billing-failure and outage alerts and through which channel; choose the monitoring tool | Your email plus one more person; a log/uptime alert tool on `billing_webhook_permanent_failure`, `/ready` and 5xx rate | MONITORING; alert-delivery gate | Yes | The agent generates a synthetic permanent-failure event; the alert reaches the named recipient within 10 minutes; you confirm receipt with a screenshot. (Log markers already exist; delivery to a person is NOT yet verified) |
| 7 | Legal adoption and operator details | Supply the real operator legal name, trading name, registered address, contact details and registrations; adopt terms, privacy and cookie policies after solicitor review | Use the doc 18 packet; the solicitor signs off | LEGAL-COMMERCIAL | Yes | Signed approval; the published pages contain the real details and no placeholders (agent scans); date of adoption |
| 8 | Approval authority and Staff permission defaults | Approve: only Owner approves quotes, variations and purchase orders unless authority is delegated; Staff may prepare; decide whether Staff may write the cost ledger | Approve as implemented; keep the Staff cost-ledger write allowed but flagged | ROLE-PERMS, APPROVAL-AUTHORITY | Yes | Written approval of the policy |
| 9 | Quote-revision workflow | Confirm approved quotes stay immutable and changes happen as numbered revisions that keep the original | Approve as implemented | QUOTE, QUOTE-REVISIONS | Yes (confirmation) | Written confirmation |
| 10 | Invoice and supplier settings | Enter the real supplier details per workspace (VAT registration, bank details, payment terms, prefix and starting number) | Enter before the first real invoice; keep the default `Invoice` prefix | INVOICE, INVOICE-SUPPLIER | Yes (data entry) | You review the first generated invoice PDF and confirm the details, reference and total |
| 11 | Catalogue-import decision | Choose: (a) no import for the pilot, manual entry; (b) a reviewed importer built as a separate change with its own acceptance | (a) for a controlled pilot of up to 10 workspaces; plan (b) afterwards | PROD-CONFIG (catalogue review) | Yes (decision) | Decision recorded; if (b), a new reviewed candidate and its own evidence |
| 12 | Shared-staging recovery window and named operator | Name the operator with Railway access and an exact window (about 2 hours, outside business hours); sign the approval file from the template | An early weekend morning | RECOVERY-POINT, SCRATCH-RESTORE (staging) | Yes | Completed approval file; A1 capture and restore proof from the window; barrier released and verified |
| 13 | Read-only production inspection | Name the operator who runs the doc 21 §B2 checklist read-only: PITR/WAL status, service configuration, admission setting, previous deployments, catalogue import review | Run before any production deployment, in the same sitting as item 12 | PITR-WAL, PROD-CONFIG, ADMISSION, MIGRATION (production) | Yes | The operator's redacted output attached to the evidence record |

## 6. Remaining sequence toward a controlled pilot (smallest)
1. Items 1 and 3: nominate the mailbox and approve the two caps; create the disposable Railway project and tokens.
2. Agent: replacement-infrastructure recovery exercise (section 4), then teardown.
3. Agent: disposable first-customer environment (section 2); item 2 (email click-through) and item 4 (declined then successful Checkout) in one owner session; agent verification; teardown.
4. Items 5 and 6: support mailbox, alert recipients, real test enquiry and a delivered test alert.
5. Item 7: legal adoption and operator details.
6. Items 8, 9, 10: approve the defaults and enter supplier details.
7. Item 11: catalogue decision.
8. Items 12 and 13: shared-staging recovery window and read-only production inspection (the production-readiness inputs).
9. Re-run the full gate classification; production deployment remains out of scope until every mandatory gate has explicit evidence.

## 7. Evidence separation (for the reader)
| Evidence | Where it stands |
|---|---|
| Hosted application acceptance | PASS, isolated QA, `f503041`, run 43730458415 |
| Real Stripe sandbox evidence | PASS for lifecycle, failed renewal, recovery, cancellation, resume; hosted Checkout BLOCKED (captcha) |
| Locally signed webhook resilience | PASS, labelled as not Stripe-originated |
| Local writer-barrier rehearsal | PASS 28/28 on a disposable runner (runs 43732293051, 43732968698) |
| Hosted logical same-server recovery | PASS, run 43734792050 |
| Replacement-infrastructure recovery | NOT EXECUTED (section 4) |
| First-customer acceptance | NOT EXECUTED (section 2) |
| Production readiness | NOT VERIFIED; HOLD / NO-GO |

### 4.2 Result of the execution (16 Nov, disposable project geocore-disposable-qa-7f3c9a2e81, environment `recovery`, run 43783158019)
| Check | Result |
|---|---|
| Token scope = disposable project + `recovery` environment only | PASS (run 43750894509 and every later run) |
| Bundle SHA-256, dump, uploads archive and manifest vs integrity record; matched capture T0 = T1 | PASS |
| New PostgreSQL 18, independent volume, starts empty; `pg_restore` | PASS |
| Migration revision exactly `c7d8e9f0a1b2`; database fingerprint (rows, content hash, alembic) equals capture; per-tenant counts equal | PASS |
| 34 uploads byte-identical (SHA-256, size, path); every document row has its file with the recorded size | PASS |
| Restored application ready; two tenants download own documents (hash = manifest); cross-tenant 404 both ways; anonymous refused; list isolation | PASS |
| Restored application under the real start command (migrate_gate) and `/health`, candidate image | PASS |
| Timing | provision to verified 41 s; DB restore 1.0 s; uploads 0.1 s; verification 2.6 s; whole run 75 s (excludes teardown) |
| Recovery point | 15 Nov 19:21 UTC (logical dump; no PITR/WAL) |
| Teardown: services | deleted |
| Teardown: volumes | **NOT deleted: a Railway project token is not authorised to delete volumes. 11 volumes remain (synthetic/empty).** |
| Actual spend | not yet measured; runs lasted minutes with tiny resources; owner to confirm in the Railway usage page (cap $3) |

Findings that change the plan: (1) a project token cannot connect a repository, so the application ran from a private image built in Actions from a checkout of exactly `f503041` (identity: image built from that commit); (2) Railway rejected ~1.4 MB of variables, so the bundle travels in a data-layer image; (3) the restore tool correctly refuses a target under `/var/lib/simo-os/uploads`, so the independent volume is mounted elsewhere; (4) volumes need owner deletion — **owner action: delete every volume in the disposable project, then dispatch workflow mode `teardown` to verify emptiness.** Until then REPLACEMENT-INFRA-RECOVERY stays BLOCKED in the ledger and the first-customer environment must not be provisioned.

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

### Recovery closure (owner-supplied evidence, 16 Nov)
Technical recovery PASS (run 43783158019). Infrastructure cleanup PASS: ChatGPT's connected Railway account deleted all 11 volumes and verified zero services, volumes, buckets, shared variables and staged changes. Cost reconciliation PASS: Railway Usage dashboard shows $0.0010 for project geocore-disposable-qa-7f3c9a2e81 against the $3.00 cap. `REPLACEMENT-INFRA-RECOVERY` is VERIFIED in the ledger. Scope limit unchanged: replacement-infrastructure recovery of a synthetic bundle is verified; production disaster recovery and PITR are not. The recovery exercise is not to be repeated.

### First-customer acceptance — status 16 Nov (BLOCKED at the first prerequisite)
Read-only scope check (workflow `geocore-disposable-firstcustomer.yml`, run 43785042244, job declares GitHub environment `first-customer`): **`GEOCORE_DISPOSABLE_FIRSTCUSTOMER_RAILWAY_TOKEN` resolved empty.** Nothing was provisioned and nothing was spent. Either the secret is not yet stored under a GitHub environment named exactly `first-customer`, or it is stored as a repository secret/under a differently named GitHub environment. Owner action: create (or rename) the GitHub environment `first-customer` and store the Railway project token for the Railway environment `first-customer` of project `geocore-disposable-qa-7f3c9a2e81` there; then re-dispatch the scope check. Expected PASS lines: project id equals 482d0eff-ecd3-4457-8834-e0b49e897e42; environment is named `first-customer`; it is not the recovery or any QA identifier; zero services.
Not yet started: provisioning, email acceptance, Stripe sandbox webhook, admission protection. HOLD / NO-GO unchanged.

### Discrepancy to reconcile before closing the cleanup result (16 Nov, 19:31 UTC)
A read-only inventory (workflow run 43940077675, disposable recovery token) and the first-customer scope check (run 43786043868, project-wide query) both still list the same 11 volume IDs recorded above, with zero services. This conflicts with the owner/ChatGPT report that all 11 volumes were deleted. Possible explanations: deletion not yet applied to this project, deletion performed in a different project/workspace, or the project-level volume listing returning entries that no longer have instances. The recovery gate remains VERIFIED on the restore PASS and the $0.0010 dashboard cost; the *cleanup* statement is re-opened until a dashboard screenshot or an inventory run shows zero volumes. No new volumes are being created until this is reconciled.
