# Server-Authoritative CRM Delivery Roadmap

**Date:** 2026-10-08

**Design:** [Reviewed architecture](../specs/2026-10-08-server-authoritative-crm-design.md)

**First executable plan:** [Phase A: baseline and compatibility evidence](2026-10-08-online-feasibility.md)

## Intent and authorization

Replace the current local-first synchronization product, not merely its login screen. The delivered system consists of central host accounts, explicitly isolated/shared workspaces, transactional server-owned CRM/finance/profile/inquiry/job state, controlled operator imports, private canonical images, an online-only Windows panel, and recoverable private backups.

This document sequences the complete design. Only Phase A has a task-level implementation plan in this delivery. The remaining rows are scope and acceptance contracts, not incomplete executable plans. Each subsystem receives its own concrete plan after its dependencies and measured Phase A findings exist. This prevents distributing unproven robot adapters or inventing production schema facts.

No product code, baseline, compatibility probe, migration, preview, build, release, or installation ran while writing these documents. Design approval does not authorize destructive cleanup or production side effects. No commit or push is authorized. The architecture snapshot records repository state at design time, not the state of a future executor's branch.

## Execution order

| Phase | Independently testable output | Dependency / stop condition |
| --- | --- | --- |
| A. Baseline and compatibility evidence | Disposable baseline results; exact-source write/action inventory; lab-only unchanged-module probes; sanitized CONDITIONAL/BLOCKED report | First phase. Mocked dependencies, unresolved async actions, or browser persistence cannot receive production PASS. |
| B. Host identity and integrity foundation | Additive migration/preflight; central accounts, memberships and purpose-bound sessions; scoped authorization; tenant lifecycle gate | A evidence reviewed. No modification to applied `001_initial.sql`; actual-host preflight requires authorization. |
| C. Authoritative business services | Paginated `/v2` reads and transactional CRM, period, owner, stage, ledger, profile and inquiry commands; durable intents and operation recovery | B fixture migrations pass. Existing domain fixtures are the behavioral oracle; stale edits and uncertain outcomes cannot become success. |
| D. Operator administration and imports | Private account/workspace/import/status page; bounded manifest packages; private image storage; validated staging and atomic activation | B and C interfaces stable. Stale source revision, active/uncertain jobs, incomplete profile/result links, or failed backup block replacement. |
| E. Online desktop and jobs | Memory-only login/broker; preserved CRM screens; explicit context fencing; host-owned jobs/schedule; narrow protected worker IPC; removed sync/server UI | C and D fixture API exists. Every supported immutable-robot path needs realistic dependency/storage/control proof; unsupported critical paths block full release. |
| F. Backups, rehearsal and delivery | Encrypted private four-hour backups; restore proof; two-client staging trial; maintenance/rollback runbook; reviewed online distribution | A-E acceptance passes. Live tests, migration, cutover, cleanup, release and installation each need their applicable authorization. |

Phases remain ordered at integration boundaries. Read-heavy independent reviews can overlap; two implementers must never edit a shared critical file concurrently. Do not dispatch a broad implementation workflow while Phase A is conditional or blocked. Technical artifacts may be prepared, but a conditional laboratory result is not permission to claim that the complete product can ship.

## Subsystem file and interface boundaries

### B. Identity and database integrity

**Existing integration points:** `host/src/Auth.php`, `Database.php`, `Schema.php`, `Roles.php`, `Http.php`, `RateLimit.php`, `host/public/index.php`, `host/bin/migrate.php`, `host/migrations/001_initial.sql` (read-only).

**New responsibilities:** `host/src/Accounts.php` (canonical ASCII login and password lifecycle), `AccountSessions.php` (hashed opaque sessions and revocation), `Workspaces.php` (membership and tenant lifecycle), `MigrationPreflight.php` (sanitized duplicates/orphans/DDL state report), and additive numbered migrations. Migration filenames are chosen from the repository's current next migration number at execution planning, not assumed to be unused here.

**Required contract:** canonical login is trimmed/lowercase ASCII `[a-z0-9._-]`, length 1-64; existing 12-character password policy; `admin_web` and `desktop` purposes; live account/security version, membership and tenant state on every request. Passive polls do not prolong idle sessions indefinitely. Platform operator and workspace admin are separate permissions. Reviewed legacy mapping with newly set credentials is the default; matching usernames never merge identities. Composite same-tenant foreign keys are tested with direct fixture SQL as well as API requests. DDL interruption is recoverable and the runtime fixture DB account cannot perform DDL.

### C. Queries and transactional domain commands

**Existing behavioral references:** `crm.py`, `panel.py::crm_state_payload`, `lab/tests/test_crm_stage_transitions.py`, `test_crm_case_integrity.py`, `test_crm_ledger_integrity.py`, `test_v1_6_accounting.py`, `test_v1_7.py`.

**New responsibilities:** `host/src/CrmQueries.php`, `CrmCommands.php`, `LedgerCommands.php`, `Profiles.php`, `InquiryResults.php`, `CommandIntents.php`, `Operations.php`. File boundaries follow domain responsibilities; do not accumulate them in the front controller.

**Required contract:** explicit workspace/actor/command context, stable UUID identity, expected row versions, invariant-wide deterministic locks, and atomic operation digest/result. Finance and job actions first have durable recoverable intents. A committed intent cannot execute twice under new transport IDs; explicitly new legitimate repeat ledger entries remain possible. Preserve integer-Toman signed/void/refund rules. Cover both serial slots, owner bindings, period numbering, linked profile creation and historical attribution. Do not create invoice/installment models absent from the current domain. Timeout-after-commit is unknown until operation/intent recovery resolves it.

### D. Administrator, package import and canonical attachments

**Existing integration points:** `host/public/index.php`, `.htaccess`, `host/src/Http.php`, schema/migration interfaces from B, command services from C.

**New responsibilities:** `host/src/AdminController.php`, `DatasetImports.php`, `Attachments.php`, `host/views/admin.php`, and bounded operator/cron CLI entry points. Canonical images/config/staging/archives stay outside the public document root.

**Required contract:** secure cookies, CSRF/origin/CSP/escaping and real operator authorization; create/reset/disable/assign actions; no arbitrary SQL console; bounded archive/JSON/image validation; stable source namespace and ID remapping; repeated digest idempotency; per-entity, relation, balance and photo-coverage preview. CRM-only uploads disclose incomplete coverage and cannot silently discard linked profiles/results. New workspace is the default. Replacement freezes final legacy writers before candidate creation or rejects source-revision drift, seals an inaccessible staging tenant, then activates with old/new lifecycle locks and a bounded membership set. Invalidate old context logically; retain the archive privately. An upload is not an import until acknowledged activation completes.

### E. Online broker, desktop and controlled workers

**Existing integration points:** `panel.py`, `panel_auth.py`, `app_paths.py`, `panel_gui.pyw`, `robot_launcher.py`, `system_debug.py`, `telegram_notify.py`, inquiry helper modules. `gomrok.py` is immutable.

**New responsibilities:** `online_api.py` (typed bounded HTTPS calls and honest outcome classification), `online_session.py` (RAM-only per-session context), `online_broker.py` (loopback protected UI/job routes), and a narrowly scoped robot adapter only if Phase A and realistic follow-up prove it. Preserve the existing UI rather than adding a second CRM. Resolve packaging/startup import paths so unused local-first modules do not create stores before online mode initializes.

**Required contract:** fixed approved HTTPS origin; no cross-origin credential forwarding; no host token in browser storage, worker args/environment or logs; RAM-only local handle and scoped IPC capabilities; authenticated image-only cache with opaque names. Remove durable sync/outbox/lease/device-enrollment/server configuration, startup seeded local accounts, JSON store/bootstrap and local business fallback. Poll lightweight invalidation about every three seconds while visible with bounded backoff; retain dirty drafts in RAM and reject late old-context responses. Host job leases use server time/fencing, proposed 10-second heartbeat/60-second lease. Uncertain external actions require reconciliation, never automatic repeat. Host cron schedules; only enabled logged-in laptops perform browser jobs. Mock Telegram sends only, preserving receipt/payment notification ordering without sending customer data or accepting arbitrary destinations.

### F. Operations, migration and release

**Existing references:** `host/bin/backup-to-telegram.php` (replace unsafe behavior, do not activate it), `host/INSTALL.md`, `host/SECURITY.md`, existing build/verifier/installer scripts.

**Required contract:** private encrypted consistent backups every four hours; lock, limits, failure propagation, checksums, retention and restore proof. Telegram receives sanitized status only. Staging uses two clean clients/accounts sharing one workspace and another isolated workspace, tests concurrency/revocation/network failure/import crash/disk writes and observes image authorization. Independent review covers auth/tenant isolation, finance/import and robot/storage behavior. Preserve both local-only and host-only changes under a reviewed final writer freeze. Reject old `/v1` clients/writes at cutover. Rollback after new writes must first preserve/reconcile those writes. Exact legacy-file deletion remains separately authorized. A later authorized release verifies installed hashes for every changed/new distributed module; version strings and local installation do not prove host deployment or cron activation.

## Architecture-to-phase coverage

| Architecture section | Owning phase / acceptance evidence |
| --- | --- |
| 1. Product scope and removal of old server/sync model | B, D, E; account/import behavior and distribution/startup tests |
| 2. Evidence and selective reuse | A inventory; C domain parity; E startup/write-path audit |
| 3. PHP/broker topology and trust boundaries | B and E; real fixture API plus loopback origin/capability tests |
| 4. Schema, identities and same-workspace integrity | B and C; additive migration/direct-SQL/API tests |
| 5. Commands, conflicts, intents, refresh and honest outcomes | C and E; concurrency/replay/restart/dirty-form tests |
| 6. Admin, imports, freeze, sealed staging and activation | D and F; invalid/interrupted/repeated import and replacement rehearsal |
| 7. Host/RAM/image-only storage and controlled legacy cleanup | A, D, E, F; attempted-write audit and clean-install/upgrade trials |
| 8. Immutable robot, fencing, uncertainty and schedule | A and E; branch-level lab evidence followed by realistic authorized proof |
| 9. Backup, cutover and rollback | F; encrypted restore plus post-cutover-write preservation rehearsal |
| 10. Verification/release gates | Every phase's focused tests; F complete supported regression and authorization |
| 11-12. Files, review and approval boundaries | This roadmap and each subsystem plan/review gate |

## Execution method

For Phase A, recommend native sequential execution in the current harness followed by one fresh independent whole-change reviewer and an Advisor evidence check. The tasks share inventory, storage and report interfaces, and their deliverable is diagnostic software rather than a production rollout; serial implementation avoids paying repeated implementation contexts or parallel changes to the same harness. That recommendation is not an assumed user method selection or implementation-plan approval.

For high-risk host/auth/finance/import phases, preserve independent review by someone other than the implementer. Do not promise a calendar estimate before the baseline and source coverage are measured. No production work is bundled into a generic “continue” or laboratory-plan approval.
