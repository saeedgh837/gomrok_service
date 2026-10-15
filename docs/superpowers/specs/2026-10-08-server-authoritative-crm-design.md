# Context

IRICA v1.14.0's online mode is still local-first: `crm_store.json`, `profiles.json`, and `estelam_store.json` are read and rewritten, with an outbox and frequent synchronization added afterward. The requested replacement is a simpler product: the operator creates accounts and assigns/uploads datasets on the host; colleagues log into the Windows app using those accounts; all durable business state is server-owned. Only image files may remain in the application's local data directory.

This is an architectural design and staged execution proposal for review, not authorization to edit production, delete existing data, publish a release, or install an update. No new behavior has been implemented or tested. The repository is clean on `main` at `08c3445`. Inspection covers the checked-in schema and code, not the production database's actual deployed schema or data.

## 1. Scope and product decisions proposed for approval

- Replace, rather than extend, the Cloud/Server tab, device enrollment, transfer/merge button, local/online selector, offline lease, local conflict queue, and durable synchronization worker.
- Keep the existing CRM screens, periods, financial semantics, stages, inquiry/registration/payment capabilities, and photo viewing. Do not introduce a second CRM UI or redesign unrelated screens.
- Add a private host administrator page for account creation/reset/disable, workspace assignment, dataset upload/preview/activation, and import/backup status.
- A workspace is one isolated CRM dataset, represented by the existing `tenants` concept. One physical MySQL database contains many workspaces; this does not require a new MySQL database/user for every application account.
- A newly created user gets a private workspace by default. The operator can instead assign multiple users to the same workspace, or give one user access to several workspaces. One workspace is active per desktop session; switch only after stopping active local jobs and handling unsaved forms.
- Give each colleague a separate login, even when sharing data. The platform administrator manages accounts/imports; an ordinary workspace editor cannot manage other accounts or access another workspace.
- Additional uploaded datasets create new workspaces by default. Updating a populated workspace uses explicit reviewed replacement, never automatic merge. A period is a grouping inside a workspace, not a database.
- The normal app cannot upload/import a database. Import is an operator action on the host page.
- Images have an authoritative private host copy and optional local image-only cache. This makes receipts available to colleagues without sharing laptop filesystem paths.
- Offline business operation is intentionally removed. If the host is unreachable, no saved login, local dataset fallback, local write queue, or success toast is allowed.
- Existing local non-image files are not deleted implicitly. A fresh online installation must persist images only; strict removal of existing files requires a separately confirmed cleanup after migration and restore verification.
- All new source code, tests, UI strings, logs, and checked-in documentation are English per the repository contract. Persian explanations in the conversation are separate from product copy.

## 2. Evidence and reuse boundaries

| Evidence | Consequence |
| --- | --- |
| `crm.py` `_store_read/_store_write/store_read/store_write`: file-backed whole-store reads/writes | Do not make `store_write(document)` a remote endpoint; move mutations to server business commands. |
| `panel.py` `crm_state_payload()`: CRM is joined to inquiry and profile files | Port profiles, inquiry results, payment links, and output references too; CRM table migration alone is incomplete. |
| `panel.py` login handler and `main()`: local password verification, seeded accounts, startup stores/workers | Replace both authentication systems with a single host account login; prevent startup from creating/migrating old stores. |
| `host/src/Auth.php` `tenant()/login()` and `host/bin/create-user.php`: one configured tenant and tenant-bound usernames | Current login cannot implement username-only multi-workspace access without a central account identity. |
| `host/migrations/001_initial.sql`: 18 tables, tenant-scoped business data, versions and idempotency | Preserve useful business tables; evolve schema additively rather than reset the database. |
| Existing business foreign keys point to `id` alone | Add same-workspace composite references and validate existing rows before enabling constraints. |
| `panel.py:803-829`: job ownership depends on a local PID/file lock | Replace with host claim/lease/fencing, never check a colleague's PID as if it were local. |
| `robot_launcher.py:22-40`: existing `runpy` launcher; `gomrok.py` imports panel helpers but also writes text/HTML/logs | Preserve `gomrok.py` byte-for-byte; verify launcher/broker compatibility before the main rewrite. |
| `host/bin/backup-to-telegram.php`: all-table raw dump, partial-table errors, plaintext gzip delivery | Do not activate this route as the new backup solution or send database archives to Telegram. |

Reuse `Database.php` prepared statements/identifier allowlists/UTC settings/bounded DB retries, `Auth::hashPassword()` and secure randomness, applicable schema validation/normalization, the migration checksum runner, and redaction primitives. Reuse row-version/idempotency concepts, not the old device-plus-acting-user synchronization topology. Use existing domain tests as behavioral oracles for financial/stage semantics. Existing transport code is reference material; do not instantiate a client that writes cloud config, leases, tokens, or an outbox.

## 3. Recommended architecture

**Windows UI -> loopback panel/session broker -> HTTPS versioned API -> PHP domain services -> MySQL**

**Host administrator browser -> HTTPS admin controller -> the same domain/auth/import services**

**Local robot -> narrow loopback job capability -> host job/result endpoints**

The PHP host remains framework-free and cPanel-compatible. No direct MySQL exposure, new Python host requirement, WebSocket server, Redis, or background service daemon is introduced. A new `/v2` route family separates the new contract from `/v1` during migration; old synchronization writes are disabled at cutover. Never run old synchronization and new authoritative writes against the same active workspace simultaneously.

- Host services own input validation, workspace permission, stage transitions, ledger changes, period opening/closing, owner binding, job transitions, and audit history.
- The desktop broker owns local UI sessions, network calls, robot lifecycle, in-memory buffers, and safe image caching. It owns no durable business database. Package the approved HTTPS host origin as non-secret application code rather than creating a per-user config file; allowlist the origin, validate TLS, and never forward credentials through cross-origin redirects.
- Bind the broker to loopback only, retain CSRF/Host/Origin protections, require per-launch capabilities for robot endpoints, and allowlist their operations. The UI session cannot access a different user's client/context; direct requests from another website or untrusted local process must not retrieve data or launch a job. A loopback address alone is not an authorization control.
- Keep compatible existing local route names where useful, but route them to typed commands rather than local read-modify-write functions. Both generic profile routes and CRM-specific routes must use the same host state.
- No process-global cloud actor/client reused across logged-in users. Session/workspace context is explicit, per-session, and immutable for each in-flight request/job.
- Server timestamps are UTC. Import requires a documented legacy timestamp interpretation and preserves integer-Toman amounts; no guessed time zone or floating-point currency.

## 4. Schema and account migration

Do not edit applied `001_initial.sql`. Add forward migrations, update `Schema.php`, and rehearse on a restored staging copy. MySQL DDL is not transactional: preflight and per-step recovery/checksum behavior must be implemented and tested before production migration.

### Central identity, separate from legacy tenant-bound users

- `accounts`: UUID, globally unique canonical ASCII login (`a-z`, digits, `.`, `_`, `-`, 1-64 characters; trim/lowercase before lookup), password hash, display name, active/disabled state, password-change requirement, security version, platform role (`operator` or `member`), timestamps. Display names may be Unicode; no locale-dependent username comparison. Preserve approved legacy names where compatible; collision/rename is an explicit operator decision. Reuse the existing 12-character password minimum and password hashing helper.
- `account_workspace_memberships`: account, tenant/workspace, workspace role using the existing admin/user capability matrix where applicable, status; unique `(account_id, tenant_id)`.
- `account_sessions`: hashes of cryptographically random session tokens, purpose (`admin_web` or `desktop`), account, expiry/revocation, security version. Prefer opaque DB-validated sessions to another persistent device JWT/refresh subsystem. Proposed idle timeout 30 minutes, absolute lifetime 8 hours; passive polling must not keep an unused session alive indefinitely. Tokens remain inside the broker; the local browser receives only a RAM-backed, session-only HttpOnly handle. No token in URLs, logs, browser storage, process arguments or environment. Authentication is rechecked on every call.
- `legacy_account_map`: legacy `(tenant_id, user_id)` to central account mapping, confirmed by the operator. Default to newly set credentials and forced first-login password change for migrated accounts. Do not import seeded local `panel_users.json` identities or infer identity from equal usernames. Retain historical actor references without knowing or revealing old passwords; duplicate/ambiguous usernames block migration until resolved.
- Legacy `users`, memberships, sessions, and actor references remain intact until reconciliation. Add account actor columns to new/updated audit paths rather than breaking historical FKs. Retire legacy authentication after cutover, not via destructive migration.

### Complete online business state

Reuse `tenants`, customers, owners, identities, periods, settings, cases (`crm_devices`), and customer transactions. Add:

- `registration_profiles`: typed registration inputs, linked case, operator settings, status; no saved browser cookies.
- `inquiry_results`: case/service, status, sanitized result data, timestamps, image attachment references.
- `automation_jobs` and bounded job events: requesting actor/workspace/case, job kind, state, active lease, fencing epoch, terminal/uncertain outcome. Local PID is diagnostic metadata only.
- `command_intents`: recoverable actor/workspace/business-action identity, validated proposed payload, relevant preconditions, state and committed-result linkage for financial/job actions; created durably before execution and discoverable after fresh login. A committed intent cannot execute a second time under a new HTTP operation ID.
- `attachments`: workspace/entity/job linkage, random storage key, verified MIME/size/hash, retention/access policy; private host files outside the document root.
- `dataset_imports`, staging rows, and `legacy_record_map`: source IDs/namespaces, digest, status, counts, validation summary, activation status. No secret-bearing config import.
- Account preferences and validated workspace settings for business/UI preferences previously persisted locally. Telegram/proxy service secrets remain in host-side private configuration, never in CRM exports or ordinary API responses.

### Integrity and indexing

- Add unique `(tenant_id, id)` keys and composite same-workspace FKs for case/customer/owner/period, transaction/customer/case, profile/case, result/case, attachments, and settings/current period. Test cross-workspace references at both API and database layers.
- Keep stable UUIDs as the only new API record identity; array indices and display sequence numbers cannot identify transactions.
- Allocate transaction ordering and period numbers server-side under a lock; add scoped uniqueness. `reversal_of_id` and unique reversal identity prevent double void/refund. Preserve existing balance semantics exactly; both original and reversal exclusion rules need explicit parity tests.
- Enforce serial uniqueness across both serial slots for active cases using a workspace-scoped serial reservation table, and owner binding with transactional scoped reservation/constraints. Soft-deleted cases release only the appropriate active reservations.
- Add indexes driven by actual filters: workspace + active/stage/period/customer, inquiry service/status, due jobs, session expiry, account memberships. Existing duplicate/orphan rows stop constraint activation and produce a sanitized repair report, not auto-deletion.
- Do not rely only on CHECK constraints across all supported MySQL/MariaDB versions; domain validation and verified unique/FK behavior remain mandatory.

## 5. API, concurrency, and honest outcomes

Representative `/v2` endpoints:

- `POST /auth/login`, `GET /auth/me`, `POST /auth/logout`, `POST /auth/change-password`, `GET /workspaces`.
- Workspace-scoped list/detail/create/edit endpoints for customers, owners, identities, cases and profiles; separate commands for stage action, owner binding, period open/close, transaction add/void, report generation, job start/claim/heartbeat/result.
- Platform-operator endpoints for account management, memberships, import preview/status/activation, and backup status. They are not exposed by merely hiding UI buttons.

Every request verifies a live account and current workspace membership. A client-provided workspace ID selects a workspace but grants no authority. Record lookup is scoped before reading or changing it. The platform-admin permission is separate from a workspace admin.

- Changes send `expected_version` and a unique `operation_id`; operation replay returns the original committed outcome. Scope operation keys to workspace + actor + command, store the request digest and committed result atomically, and reject reuse with a changed payload. Version/lock every record participating in an invariant, not only the visible case row.
- Stage/ledger/profile actions that belong together commit in one database transaction. Network calls, Telegram uploads, and external robot work never run inside that transaction.
- HTTP timeout after a possible commit is **unknown**, not failed or successful. Query operation status and retry only the same idempotent operation ID when permitted. For finance and job execution, first obtain a durable **server-owned intent**, then execute that intent with a unique business/intent constraint and locked preconditions. After restart and fresh login, retrieve the user's intents/results and uncertain jobs; unresolved intent execution blocks starting the same action again. Intents and results live on the host; losing a RAM draft is not a recoverable offline queue.
- An identical actor/payload hash alone cannot distinguish an accidental duplicate from a legitimately repeated payment. Reuse the recovered intent unless the operator explicitly selects a genuinely new transaction. Derive domain-specific uniqueness from existing behavior (fee/refund/reversal per case, job ownership), without inventing invoice/installment entities. Do not claim exactly-once behavior for newly created independent intents or third-party website side effects.
- Stale edits return a structured conflict with the current row/version. Preserve the user's RAM-only draft and offer reload/reapply; never blindly overwrite or auto-merge financial/state data.
- Reads are paginated/filterable; do not fetch every tenant's entire database every two seconds. Poll a workspace revision/invalidation endpoint about every 3 seconds while the UI is visible; refetch only affected views. This is refresh notification, not durable local synchronization. Bound concurrent requests, back off on transient failures, honor `Retry-After`, and slow polling while hidden.
- Do not rerender dirty forms on background refresh. Reject late responses from a previous login/workspace. Success UI requires host commit acknowledgment; stale/unsaved data is visibly distinguished.

## 6. Host administrator page and uploads

One private admin page with three simple sections: accounts, workspaces/imports, and operational status. A guided upload flow is: **select/create workspace -> choose supported export -> validate/preview -> confirm activation -> verify report**.

- Create named accounts, reset passwords without revealing an old password, disable accounts/revoke sessions, and assign/remove workspace access. Generated passwords are displayed once, never logged or retained plaintext. Seed the first platform operator through CLI or a one-time keyed setup operation that is disabled afterward.
- Protect login and changes using HTTPS, Secure/HttpOnly/SameSite cookies, server-side expiry/revocation, CSRF, origin validation, escaping/CSP, bounded login throttling, no third-party scripts, and sanitized audit events. Confirm passwords without logging request bodies.
- Support the existing CRM JSON and a versioned migration package containing CRM/profiles/inquiry data plus images and a manifest. Snapshot/export is an explicit operator migration action, not an automatic startup upload. CRM-only uploads state their limited coverage; missing required profile/result references block activation instead of silently discarding links. The full package is the recommended migration format.
- Collect legacy local-only edits and current host-only edits into an operator-reviewed migration candidate under a final writer freeze. This is one-time reconciliation, not a hidden ongoing synchronization feature. Compare conflicting versions, finances, and pending jobs before selecting the candidate.
- Never execute uploaded SQL, PHP, or arbitrary configuration. If an old source is SQL-only, transform it in isolated migration tooling with an explicit table/column allowlist; do not add an SQL console to the admin page.
- Cap compressed/uncompressed bytes, record/image count, nesting depth, and processing time. Validate image bytes; reject path traversal, symlinks, archive bombs, unexpected file types, duplicate IDs, cross-workspace references, invalid stages/currency/dates and dangling links. Stage outside the web root with short retention.
- Preserve source installation/namespace and stable mappings. Equal short legacy IDs from different installations are not automatically the same person/case. Repeated upload of the same source/digest is idempotent. Reject ambiguous duplicates for operator review.
- Preview counts, errors, reference coverage, missing photos, balances per customer/period and overall. Never report an upload as imported while it is only queued/staged.
- Default activation creates a new workspace. Implement replacement by building an **inaccessible new staging tenant**, not by deleting/updating the live tenant in an unbounded transaction. Add workspace lifecycle/API-contract fields and replacement linkage. Remint UUIDs for copied/imported rows with an explicit source-to-target map, because existing table primary keys are global; rewrite every relationship consistently. Load and validate bounded chunks; staged rows/images are invisible to ordinary accounts and old sync routes.
- At reviewed replacement activation, require no active/uncertain jobs and a verified private snapshot. Freeze old writes **before producing the final migration candidate**, or reject a stale source revision and redo reconciliation/preview; never silently discard writes made while staging. Seal the validated staged manifest. In one short transaction, lock old/new tenant state in deterministic order, verify source revision and staged completeness, copy the approved membership set within an enforced supported cap, archive the old tenant, activate the new one, and record replacement linkage/audit. All mutations and job authorization take the relevant tenant-state lock through commit and reject stale/frozen/archived context. Invalidate sessions/workspace contexts **logically through state**, not by scanning an unbounded session list. Warn clients and require selecting the replacement before new editing. Retain the old dataset privately for rollback, not as a second writable authority. No need to add generation columns to every business row.
- The migration package covers BOTH current host-only edits and local-only records using explicit reconciliation. Never designate a stale laptop export as authoritative just because it uploaded last.

## 7. Storage contract, images, and legacy files

**Durable on host:** business records, financial ledger, profiles, inquiry results, job/scheduler history, payment links, settings/preferences, account/password hashes, audit/import/operation state, attachment metadata and canonical image files.

**RAM only on desktop:** login/session handles, host token, active workspace, rendered records, unsaved form drafts, bounded pending response/log buffers, per-job capabilities. Application close loses unsaved drafts and requires login again. No browser localStorage/sessionStorage/IndexedDB, service-worker CRM caching, remembered passwords, local `.bak`, outbox, cursor or account/config files in the online runtime.

**Local persistence allowed:** image bytes in workspace/account-isolated photo directories, using opaque IDs rather than customer names/national IDs. Access still requires the active session/workspace. Hash and MIME checks, safe path containment and invalidation on deletion apply. A missing local image is fetched through an authorized host attachment endpoint. Missing legacy images are reported, not falsely marked available. The exception does not make local photos private from the Windows account owner: logout blocks application access, not filesystem access; disclose this and enforce appropriate folder ACLs.

Attachment upload is a separate bounded idempotent operation. For receipt+ledger workflows, only reference an acknowledged image attachment; do not leave a false photo-present flag after failed upload. A produced but not-yet-uploaded photo can remain locally under its opaque filename, while the host job reports `attachment_pending`; a reauthenticated authorized worker can request the scoped pending-upload descriptor from the host. Never store a JSON sidecar or an unbounded local retry worker.

Program binaries/static assets and driver/runtime tooling are not a local business database. Reports should be streamed from the host for printing/viewing rather than implicitly saved. OS paging/crash dumps and third-party browser/website temporary files cannot be eliminated by an application promise; the enforceable target is no application-owned durable non-image business state, with no-store responses, ephemeral browser profiles where supported, and explicit disk-write verification. Any unavoidable browser/runtime persistence is documented before release.

Upgrade is non-destructive: disable legacy startup/bootstrap/import paths, do not silently reuse old files, and report legacy data awaiting controlled migration. After comparison and restore proof, request explicit approval for the exact old non-image files to remove. Until cleanup is performed, an upgraded machine still contains historical local data; do not claim otherwise.

## 8. Robots and scheduling: early feasibility gate

`gomrok.py` must remain byte-identical. Preserving its behavior while removing its local file I/O is an engineering gate, not yet a proven fact.

- Reuse `robot_launcher.py` as the compatibility boundary. Panel helpers imported in each subprocess become calls to the loopback broker, not another local store or a separately logged-in process.
- Give a job narrowly scoped, expiring, revocable capability via protected IPC; no panel password, host bearer token, bot secret or proxy credential in process arguments or environment. Limit it to the assigned job/case/workspace. Do not globally monkeypatch `builtins.open` or unrelated modules.
- Prototype targeted launcher/module I/O adapters for legacy text/HTML/log output and helper dependencies; image writes remain allowed. Pipe process stdout/stderr to sanitized bounded in-memory handling rather than `gomrok_worker.log`. Ensure crash/debug/Telegram helpers cannot recreate JSON/log files. Prove every relevant branch on fixtures, including direct text/HTML writes inside `gomrok.py`.
- Claim and transition jobs atomically on the host. Proposed job lease: heartbeat every 10 seconds, expiry 60 seconds, server-clock authority and monotonically increasing fencing epoch. Reject stale-owner results. Run authorization and workspace membership checks for jobs too.
- No two workers may run the same case concurrently. An expired job during a potentially irreversible website operation becomes `needs_reconciliation`, not automatically free for another laptop. External website execution cannot be guaranteed exactly-once by a database lock alone.
- Prove a lease/permission check before irreversible external submission and a stop/cancellation mechanism despite the unchanged robot. If the launcher cannot enforce that without changing `gomrok.py`, do not silently weaken either requirement or publish a partial solution; present the specific incompatibility for a separate decision.
- Preserve scheduler cadence/settings on the host. cPanel cron creates bounded due jobs; only an explicitly enabled, logged-in desktop worker consumes its workspace's jobs. If every laptop is closed, external browser robots do not run; the host still owns the schedule/history.
- Session logout/workspace switch/revocation stops accepting new jobs and cancels the associated local workers. If an irreversible request may already have happened, preserve uncertain server state for reconciliation. Never advance the CRM stage just because a process exited.
- Telegram compatibility must move persistent config/secrets to the host. Use authenticated host delivery with fixed authorized destinations, not a public bot relay accepting arbitrary chat IDs/tokens. Preserve notification behavior only within existing data-handling authorization; test all sends against mocks with sample data.

## 9. Backups, rollout, and rollback

Replace the unsafe raw-dump-to-Telegram route. Retain the previous four-hour backup cadence as a host responsibility: consistent snapshot, encrypted private archive, bounded resource use, lock, failure propagation, retention, checksum, and tested restoration. A small cron worker handles bounded import/job/backup tasks; no synchronous 60-second Telegram upload inside an API request. Telegram receives only sanitized backup success/failure status by default. Database documents containing customers, password hashes, sessions or secrets are not sent under the current privacy constraints.

Rollout order after approvals:

1. Reproduce the supported test baseline in a disposable environment. Inventory all application-owned non-image writes and domain endpoints. Inspect the actual host schema/version/grants via a separately authorized sanitized metadata preflight.
2. Prove robot/session/storage compatibility with synthetic data before expensive implementation. Stop at any unresolved strict-storage or irreversible-action gate.
3. Implement additive schema/auth and `/v2` scoped business-command services. Write migration, domain-parity, permission and concurrency tests as each service becomes available.
4. Implement host admin/import preview/atomic activation and image metadata/private storage.
5. Convert desktop login, routes, startup, scheduler/robot broker and UI to server authority; remove the old server/sync UI and old persistence from the online distribution.
6. Independent review of authentication/tenant isolation, finance/migration, and robot/storage failure behavior. Do not count an implementer's own review as independent.
7. Staging trial with two clean installations/accounts sharing one workspace plus a separate workspace; network interruption, session revocation, concurrent edits, duplicate requests, and disk-write audit.
8. Authorized production maintenance window: stop old writes/jobs; private verified backup; migrate/reconcile; activate `/v2`; reject old client writes with an upgrade-required response; verify; distribute a major-version online-only build. Archive old binaries/data for controlled rollback, never as an online fallback.
9. Confirmed cleanup of legacy non-image local data only after acceptance; verified rollback rehearsal; bundle/installer/install verification in a separate explicitly authorized release phase.

Rollback before new writes can switch back to the verified old snapshot. After new writes, freeze activity and export/reconcile post-cutover operations first; restoring an old dump or reenabling `/v1` blindly would discard new work and is forbidden. Destructive cleanup and production cutover require separate confirmation.

## 10. Verification and completion gates

No checks below have run for this new design. Commands are derived from the existing repository runner; extend it for new online suites, and update obsolete local-storage expectations rather than retaining misleading source-string tests.

Existing focused baseline:

```bash
"C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py test_crm_validation test_crm_stage_transitions test_crm_case_integrity test_crm_ledger_integrity test_crm_scheduler test_auth_hardening test_runtime_api test_runtime_heartbeat test_token_privacy
```

Host baseline/schema contract (dedicated local DB credentials only from `%TEMP%/irica_test_db.json`; never print them):

```bash
"C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py test_host_schema test_host_api
```

Complete supported regression run after updating intentional contract changes:

```bash
"C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py
```

New gates:

- Login created on the host works on two clean laptops, with no local seeded account and no persisted bearer/refresh/password. Disabled/reset accounts fail on their next request.
- Shared-workspace users see committed edits within the polling interval under normal network conditions; separate workspaces cannot read/write/reference/download each other's data. Test through the API and SQL constraints, not only through UI visibility.
- Concurrent stale edit gives a conflict without overwriting; timeout-after-commit plus replay and restart/recovered-intent execution do not duplicate cases, payments, periods, transactions or jobs. Verify distinct legitimate repeated ledger entries are still possible through an explicitly new intent. Finance/void/refund/stage semantics match existing validated fixtures.
- Invalid/repeated/interrupted imports are safe; a populated-workspace replacement is atomic. Compare per-entity counts, relations, stage distributions, per-customer balances, period totals, image coverage and restored snapshots. No arbitrary SQL execution.
- Concurrent robot start has one owner; stale results fail; lease/network/revocation loss does not auto-repeat irreversible work. Unknown outcomes remain visible for reconciliation.
- Fresh installation disk audit covers login, edits, inquiries, registration/payment fixtures, Telegram mock delivery, image caching, offline failure, crash paths, restart and logout. Scan data dir, app dir and temporary locations for JSON/JSONL/text/HTML/SQLite/auth state. `gomrok.py` SHA-256 is unchanged. Observe subprocess file I/O, not just directory contents after cleanup.
- Authenticated browser checks use `preview_*` against sandbox configurations only: admin create/assign/upload/activate; desktop login/list/edit/conflict; unsaved form preservation; error states; image authorization; responsive layouts; logs/network; screenshots with synthetic records. Do not launch the installed live-data preview configuration for tests.
- Python compile checks and PHP lint on changed files; runtime host integration against fixture DB; supported full suite must pass or genuine pre-existing failures must be reproduced/identified and explicitly resolved as a release decision. Syntax checks alone are not business verification.
- Encrypted backup restore passes, no secret/PII leak in logs/reports, no old sync worker/store/account generation in the online runtime, and final diff has no unrelated changes or generated/live-data artifacts.
- If release is authorized later: use existing Windows build/verifier/installer scripts, and verify installed hashes for **every changed distributed file**, including new API/broker modules, not only a fixed legacy list. Local install does not prove production host migration or cron activation.

## 11. Critical implementation areas

- Existing: `host/src/Auth.php`, `Roles.php`, `Schema.php`, `Database.php`, `Http.php`, `RateLimit.php`, `Tokens.php`; `host/public/index.php`, `.htaccess`; `host/bin/migrate.php`, backup CLI; new migrations; `host/INSTALL.md`, `SECURITY.md`.
- New host modules: online account/session/workspace services, CRM command/query services, admin controller/view, dataset import/staging service, job service/cron processor, attachment service and private-backup service. Keep each responsibility bounded; do not add it all to `index.php`.
- Desktop: `panel.py`, `panel_auth.py`, `crm.py`, `app_paths.py`, `panel_gui.pyw`, `robot_launcher.py`, `telegram_notify.py`, `system_debug.py`, inquiry/profile helper modules and release packaging rules. New memory-only `online_api.py`/session broker and narrowly scoped robot I/O adapter.
- `cloud_sync/` is excluded from the new online runtime/distribution after migration tools stop depending on it; audited mapping helpers may remain in operator-only migration tooling. Retire `/v1` routes/tables only after cutover and retention requirements, not by deleting evidence/history.
- Tests: `lab/tests/test_host_api.py`, `test_host_schema.py`, `test_auth_hardening.py`, `test_crm_stage_transitions.py`, `test_crm_ledger_integrity.py`, runtime/Telegram/receipt suites, new central-auth/workspace/import/job/storage suites; sandbox browser launch configuration.

## 12. Design review status and approval boundary

Two Advisor responses were received and incorporated: the initial architecture critique and a focused follow-up on tenant replacement and lost operation identities. A longer intermediate follow-up timed out; its shortened, focused replacement completed. These are design critiques against supplied evidence, not independent repository code review, implementation approval, or test results.

The refinements are explicit: reviewed identity mapping/reset rather than merging equal usernames; immutable-robot/storage/irreversible-action release gates; tenant-scoped locks and composite references; sealed staging tenants as replacement generations; a final writer freeze/stale-revision rejection; logically invalidated contexts with bounded membership activation; and recoverable server-owned business intents instead of claiming transport idempotency prevents every repeated financial action.

Repository inspection is complete for the principal data/auth/robot/schema paths. No product code, live records, accounts, host files, database schema, installed application, or release has changed. No tests or production checks ran for this planning request. Production schema drift, representative dataset sizes, PHP limits/extensions, cPanel cron access, actual legacy identity collisions, and robot I/O/cancellation feasibility remain implementation preflight gates, not invented facts.

This artifact is the architecture/specification and staged delivery proposal requested for review. Approval of it establishes the design direction; before coding, produce and review the detailed phase-level implementation plan/execution method as required by the architectural workflow. Destructive cleanup, production migration/cutover, live integration tests, and build/release/install each require their own applicable authorization; neither this document nor earlier v1.14.0 approvals authorize them.
