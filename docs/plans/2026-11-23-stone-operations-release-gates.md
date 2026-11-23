# GeoCore — stone operations and release-gate execution plan (23 Nov 2026)

Status: DRAFT / PLANNING ONLY. No runtime changes, deployment, merge or release approval.

## Verified existing implementation
- Stone slab calculation: `app/quotes/slab_calculator.py` uses quantity × length × width × 15% wastage, divided by slab area. This is a quoting estimate, **not** nesting, seam or grain matching.
- `app/workflows/gates.py` implements `materials_ready`, blocking fabrication if a project's requirements are planned/required/ordered/partially received. Zero requirements do not block.
- `docs/SPRINTS/sprint-044.md` documents procurement PO lifecycle, receipts, project allocations, tenant scoping and tests. It explicitly records a V1 limitation: partial receipts keep the full PO item amount committed until full receipt.
- Existing quote-to-project browser coverage: `apps/web/e2e/quote-handoff.spec.ts`.

These are repository-source observations, **not** current production acceptance evidence.

## Workstream A — stone estimating precision (proposed; separate PR)
1. Define attached-piece geometry: waterfall, return, backsplash, upstand, cut-out and edge/joint treatment. Support nonrectangular geometry without inventing dimensions.
2. Preserve drawing/quote revision lineage and approval snapshots. Changes to accepted quantities/prices require a new reviewed revision.
3. Calculate prices from explicit measured components and tenant price-book rules. Distinguish slab-yield *estimate* from cutting/nesting optimisation; never label area-only math as fabrication-ready.
4. Add deterministic unit tests for L/U shapes, partial edge attachments, mitred returns, cut-outs, zero/invalid dimensions, material/thickness changes, VAT and discounts.
5. Add a browser journey: create → price → revise → compare → approve → hand off, checking customer-facing PDF consistency.

## Workstream B — permissions, labour and material/financial reconciliation (proposed; separate PR)
1. Audit current tenant and role permissions before designing within-tenant sales visibility. Deny by default; do not broaden customer/project access.
2. Verify full, partial, duplicate, over- and cancelled receipts; allocated material must be traceable to the source PO/requirement.
3. Evaluate V1 committed-versus-actual partial-receipt accounting: preserve reconciliation, avoid double counting, and propose a backward-compatible change only with migration and ledger tests.
4. Define crew time entries linked to project, user, date, approval and cost category; prevent double billing and unauthorised edits.
5. AI/API actions must use identical server-side authorisation and audit rules as the UI; no autonomous purchasing or financial posting.

## Workstream C — release acceptance (must precede deployment)
- Stone and general-construction quote-to-cash journeys with real backend and browser.
- Materials gate: no requirements; planned; ordered; partially received; fully received; allocated; cancelled; repeated requests; cross-tenant references.
- Stripe checkout, workspace activation, cancellation, payment failures and portal access: use test mode only, verify actual webhook object types, idempotency and stale-event handling.
- Database migration/recovery and upload restoration with documented evidence; no destructive drill without separate approval.
- Security: tenant isolation, roles, audit events, secret scanning, PDF/document access and failed integration retries.
- Record evidence links, exact commit SHA, test environment, results, skips, known limitations and owner approval.

## Execution order / stop conditions
1. Read current PRs and branch heads; reconcile existing changes before touching code.
2. Start with regression tests around the existing materials gate and partial-receipt accounting. Do not modify existing ledger semantics without an approved reconciliation design.
3. Implement stone geometry in a separately reviewable feature branch, with a stable pricing contract.
4. Implement within-tenant permissions and labour capture only after authorization model review.
5. Run full CI and controlled staging acceptance. Keep HOLD until all required release gates have evidence.

## Non-goals
No production deployment, merge, DNS or Stripe configuration changes. No invented CI pass, no assumed production parity, no unsupported claims of AI accuracy.
