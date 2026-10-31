# Host Identity and Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an additive, fixture-verified PHP identity and tenant-integrity foundation for the server-authoritative product, without cutting over the deployed service or changing the Windows runtime.

**Architecture:** Keep legacy `/v1` identities and business rows intact. Introduce separate central accounts, opaque purpose-bound account sessions, explicit workspace memberships and tenant lifecycle/contract gates behind an exact `/v2` boundary. Every authenticated tenant/domain mutation locks live identity/session/workspace state through its SQL commit. Login, bootstrap, account creation and migration/system writes instead have explicit credential/CLI/fixture authorization and their own invariants; they cannot require a session/membership that does not exist yet. Migration steps are journaled and verified because MySQL DDL is not transactional.

**Tech Stack:** Framework-free PHP 8.1+, PDO MySQL, MySQL 5.7+ / MariaDB 10.3+, existing UUID/password/token helpers, Python 3.10 standard-library test orchestration. No Composer, Redis, Python application server or new production daemon.

**Spec:** [Server-authoritative CRM design](../specs/2026-10-08-server-authoritative-crm-design.md), sections 1, 3, 4, 5, 6 and 10. [Delivery roadmap](2026-10-08-online-delivery-roadmap.md), subsystem B.

**Planning snapshot:** 2026-10-11; branch `lab/online-feasibility-phase-a`, HEAD `d0932a65b3ecb74d43807732d93185ea82fe17bd`. The architecture's older source snapshot is not the current checkout identity. Only `001_initial.sql` exists; `002`/`003` below are available at this snapshot and must be rechecked before execution.

## Authorization and evidence boundary

The user explicitly selected **drafting this plan only**. Neither this document nor its code recipes authorize implementation, DB provisioning, migration, account resets, production inspection, network authentication tests, commit/push, release or installation. Implementation needs plan approval; fixture provisioning and any actual-host action need their own applicable authorization.

Phase A's offline Chrome/panel continuation and 88 harness tests establish bounded laboratory behavior, not full-product acceptance. Runtime image/mapping measurements, complete robot storage/cancellation proof, full business baselines and live migration remain release gates. Do not dispatch broad product implementation or claim production PASS from this plan. A fixture-only B foundation can be prepared after approval without claiming that C-F or the complete product can ship.

## Global Constraints

- "Do not edit applied `001_initial.sql`." Preserve its bytes, existing migration checksum and historical identity/actor rows. This Windows checkout has CRLF while the Git blob has LF; that pre-existing difference is not permission to normalize an applied migration or reinterpret a deployed checksum. Actual deployed bytes/checksum remain unknown and require an authorized host preflight.
- "No direct MySQL exposure, new Python host requirement, WebSocket server, Redis, or background service daemon is introduced."
- "A new `/v2` route family separates the new contract from `/v1` during migration; old synchronization writes are disabled at cutover."
- "Never run old synchronization and new authoritative writes against the same active workspace simultaneously."
- "A client-provided workspace ID selects a workspace but grants no authority."
- "The platform-admin permission is separate from a workspace admin."
- "All new source code, tests, UI strings, logs, and checked-in documentation are English per the repository contract."
- "Existing duplicate/orphan rows stop constraint activation and produce a sanitized repair report, not auto-deletion."
- "Do not rely only on CHECK constraints across all supported MySQL/MariaDB versions; domain validation and verified unique/FK behavior remain mandatory."
- Keep `gomrok.py` byte-identical: SHA-256 `5d00ac1516728986d2fcc314d7f6c862a7f9d14142b4e9307f812807b9986d5e`.
- Do not read `host/config/config.php`, existing DB credential files, customer data or local panel data while planning/testing this change.
- No production fallback, unbounded retry, external effect in a retried transaction, raw password/token logging, token in URL/process arguments/environment, automatic legacy identity merge or destructive repair.
- No commit/push steps are authorized. At task boundaries review the scoped diff and record verification; commit only after separate explicit authorization.
- Existing v1 regression fixtures must remain compatible; their destructive test harness cannot be run against an arbitrary localhost DB.
- Do not reopen the denied Phase A Advisor final evidence gate or replace `.claude/TASK_STATE.md`. The separate planning ledger is `.superpowers/sdd/2026-10-11-host-identity-integrity/progress.md`.

## Review Focus

1. A valid legacy token or malformed versioned URL must not enter a v2 handler; Task 7 tests exact routing and credential separation.
2. A reset, membership removal or freeze concurrent with an admitted write must have a defined linearization point; Tasks 4-6 test two-connection lock ordering and revocation.
3. DDL completed immediately before a crash must not be reapplied blindly or falsely marked complete; Task 2 interrupts before/after every named step and before final tracking.
4. A nullable relationship or tombstoned parent must not weaken cross-tenant FK enforcement; Task 3 tests INSERT and UPDATE directly for every relationship.
5. Passive polling, incorrect session purpose or a forced-change account must not obtain normal business authority; Task 6 uses a server-controlled clock and negative controls.

## Checked-in facts and reuse limits

| Evidence | Consequence |
| --- | --- |
| `Auth.php:95-140,190-294` resolves one configured tenant and tenant-bound usernames | New accounts cannot be an alias over legacy `users` |
| `Auth.php:308-437,645-823` implements rotating JWT/device refresh families | Preserve it for v1; do not reuse its identity/session semantics in v2 |
| `Tokens.php:121-129` generates 32 random bytes encoded as 64 hex characters and hashes opaque values with SHA-256 | Reuse these exact helpers; a v2 token is not a JWT |
| `Auth::hashPassword()` prefers Argon2id and falls back to `PASSWORD_DEFAULT` | Reuse hashing, but explicitly reject bcrypt-truncated new password inputs |
| Legacy force-change is only a returned flag | New service must enforce a restricted-session allowlist |
| `Roles.php:24-109` separates workspace admin/user capabilities | Reuse those meanings, never infer platform operator status |
| `RateLimit.php:46-115` separates check from recording | Namespace v2 buckets; serialize an account/IP attempt around check/verify/record |
| `index.php:86-139` strips configured `base_path` and accepts unprefixed route keys | Exact v2 dispatch must precede legacy route normalization |
| `Database.php:376-385` retries whole transaction callbacks | Callbacks contain SQL only, no passwords printed, HTTP, files, email or Telegram |
| Current SQL has 18 tables and ID-only relationship FKs | Add same-tenant keys/FKs rather than assuming current constraints provide isolation |
| Current migration tracking is per file, after all statements | Add step journal, lock, drift detection and verified recovery |
| Current schema tests hardcode initial-only 18 tables/237 columns and flatten FK pairs | Add complete-chain and grouped ordered-constraint oracles; retain initial-only compatibility checks separately |
| Existing host tests drop fixed DBs and can skip missing prerequisites | New isolated runner must reject missing prerequisites and never count skipped DB/API tests as PASS |

`create-admin.php:128-133,175-192` has a separate confirmed named-reset targeting defect: it chooses an admin by tenant/role and can reset/rename a different requested admin. It was flagged as a separate task. New account tools must not delegate to that reset path; this plan does not silently fix or endorse legacy reset behavior.

## Scope allocation

**B includes:** fixture safety, additive migration/preflight/recovery, central identity, reviewed mapping service, session revocation/expiry/purpose, memberships, tenant lifecycle and API-contract gates, existing scoped FK integrity, exact v2 auth/workspace-list endpoints and private CLI bootstrap.

**C owns:** business command parity, balances/stages, reservations, durable intents, mutation operation IDs, new profiles/results/jobs and their scoped constraints.

**D owns:** browser administrator page/cookies/CSRF, package imports, sealed staging validation, bounded replacement activation, canonical images and backup prerequisite enforcement.

**E owns:** RAM-only desktop broker, active workspace switching, local UI/worker capabilities, immutable robot compatibility and cancellation/storage gates.

**F owns:** actual-host rehearsal, backups/restore, cutover, old-auth retirement, deployment and release.

B must expose safe tenant-state locks for C/D, but must not implement an unsafe early activation endpoint or promise that setting a tenant field completes cutover.

## Proposed policies for plan approval

These proposed defaults require plan approval. The Advisor design critique checked internal consistency; it did not authorize implementation or select production settings:

- Session idle lifetime 1,800 seconds; absolute lifetime 28,800 seconds. A deadline equal to server `now` is expired. No refresh-token subsystem for v2.
- Public v2 login issues only `desktop` sessions. `admin_web` issuance remains an internal service interface until D implements cookie/CSRF protections. Purpose never confers operator role.
- Passive reads, polling, heartbeat and worker traffic do not renew idle time. Future C human-command handlers opt in by a server-owned route allowlist; the client cannot send `active=true` to extend expiry. This is an activity heuristic, not proof of human presence; absolute expiry remains binding.
- New passwords: valid UTF-8, no NUL, at least 12 Unicode code points and at most 72 UTF-8 bytes, no normalization/trimming. The upper bound avoids bcrypt truncation on PHP 8.1 fallback runtimes. Legacy hashes/policy are unchanged. This explicit new-account limit needs approval with the plan.
- Existing tenants retain `status` and start with `lifecycle='active'`, `api_contract='legacy_v1'`. New central-account workspaces are explicitly created `active/authoritative_v2`; operator import staging is `staging/authoritative_v2` and inaccessible. SQL defaults for future unspecified inserts are `staging/authoritative_v2`, never accidentally live.
- Freeze/archive, membership removal and account disable are logical gates checked on each call. They do not cancel an operation already admitted while holding the required transaction locks; they serialize with it.

New password validation uses the exact same unnormalized byte sequence for strict UTF-8 validation, codepoint count, byte bound and hashing. Canonically equivalent Unicode spellings remain different passwords; never silently normalize them.

## File structure and responsibility

| File | Responsibility |
| --- | --- |
| `host/src/Migrations.php` | Existing SQL splitter extraction plus named-step execution, lock, checksum and postcondition recovery |
| `host/src/MigrationPreflight.php` | Counts-only schema/data blockers and expected ordered metadata; no repair |
| `host/migrations/002_identity_foundation.sql` | Central tables, tenant lifecycle metadata and supporting legacy user key |
| `host/migrations/003_scoped_business_keys.sql` | Same-tenant business parent keys and child FKs |
| `host/src/Schema.php` | New tables/columns, grouped composite FK/index metadata and identifier allowlists |
| `host/src/Workspaces.php` | Creation/membership/lifecycle services and lock-required authorization helpers |
| `host/src/Accounts.php` | Canonical accounts, password policy/reset/disable, private-workspace default and explicit legacy mapping |
| `host/src/AccountSessions.php` | AccountPrincipal, opaque sessions, locked account/workspace contexts and purpose/expiry checks |
| `host/src/V2Api.php` | Exact typed v2 route dispatch without growing the legacy switch |
| `host/public/index.php` | Narrow pre-legacy v2 dispatch integration and existing response/log envelope |
| `host/src/Sync.php`, `Import.php`, `Auth.php` | Legacy tenant visibility and transaction-time lifecycle/contract backstops |
| `host/bin/migrate.php` | Thin existing CLI adapter to the shared safe runner; original config selection retained |
| `host/bin/create-account.php` | CLI-only first operator/member creation through new services, passwords on stdin |
| `host/INSTALL.md`, `SECURITY.md` | Versioned contract, fixture/live boundaries, migration recovery and credential handling |
| `lab/fixtures/host_v2/bootstrap.php` | Fixture config/instance marker guard and shared PHP assertions |
| `lab/fixtures/host_v2/*.php` | CLI-only named PHP integration suites, not deployed |
| `lab/fixtures/host_v2_front.php` | Loopback-only v2 fixture HTTP adapter, never legacy Config/setup/Telegram routing |
| `lab/scripts/run_host_v2_tests.py` | Standard-library fixture runner; fail on prerequisites, failures or skipped integration suites |
| `lab/tests/test_host_v2_fixture.py`, `test_host_v2_api.py` | Pure fixture guards and bounded loopback HTTP contracts |

## Interface contract (defined here for all tasks)

New service constructors receive `Database`; they do not load config or credentials themselves. All `$now` values below come from a server clock, never an HTTP body. All callbacks below are SQL-only and run within one existing `Database::transaction()`; nested transaction calls are forbidden.

```php
<?php
declare(strict_types=1);

interface MigrationPreflightContract {
    public function inspect(): array;
}
interface MigrationsContract {
    public function status(): array;
    public function apply(): array;
}
interface WorkspacesContract {
    public function createPrivateWithin(Database $tx, string $accountId, string $displayName, int $now): string;
    public function listWithin(Database $tx, string $accountId, ?string $afterId = null, int $limit = 50): array;
    public function lockAuthorizedWithin(Database $tx, string $accountId, string $tenantId,
                                         int $expectedVersion, string $capability): array;
    public function lockLegacyWithin(Database $tx, string $tenantId, bool $write): array;
}
interface AccountsContract {
    public static function canonicalLogin(string $input): string;
    public static function validateNewPassword(string $password): void;
    public function create(string $login, string $password, string $displayName,
                           string $platformRole, int $now): array;
    public function seedFirstOperator(string $login, string $password, string $displayName, int $now): array;
    public function resetPassword(string $accountId, string $newPassword, int $now): void;
    public function setStatus(string $accountId, string $status, int $now): void;
    public function linkLegacy(string $tenantId, string $legacyUserId, string $accountId,
                               string $reviewId, int $now): void;
}
interface AccountSessionsContract {
    public function loginDesktop(string $login, string $password, string $ip, int $now): array;
    public function issueWithin(Database $tx, array $account, string $purpose, int $now): array;
    public function withAccount(string $token, string $purpose, int $now,
                                bool $allowPasswordChange, callable $sqlOnly): mixed;
    public function withWorkspace(string $token, string $purpose, string $tenantId,
                                  int $expectedVersion, string $capability, int $now,
                                  callable $sqlOnly): mixed;
    public function logout(string $token, string $purpose, int $now): void;
    public function changePassword(string $token, string $oldPassword, string $newPassword, int $now): void;
}
```

Concrete constructors: `Migrations(Database $db, string $directory)`, `MigrationPreflight(Database $db)`, `Workspaces(Database $db)`, `Accounts(Database $db)`, `AccountSessions(Database $db, array $rateLimitConfig = [])`, `V2Api(Database $db, Log $log, array $config)`. Defaults for session deadlines are the proposed constants above, not client inputs. Concrete classes use the contract names without the `Contract` suffix; the interface blocks define signatures, not a request to create unnecessary interface files. `AccountPrincipal` is a new final PHP 8.1-compatible class in `AccountSessions.php`, with public readonly properties: strings `accountId`, `sessionId`, `purpose`, `platformRole`; integer `securityVersion`; boolean `mustChangePassword`. Do not use PHP 8.2's readonly-class syntax. It never contains the raw token/password. Workspace context is an explicit array with `account_id`, `tenant_id`, `workspace_role`, `context_version`, `api_contract` and no implicit platform privilege. Legacy `Principal` remains unchanged.

Callbacks have exact shapes: `withAccount` invokes `fn(Database $tx, AccountPrincipal $principal): mixed`; `withWorkspace` invokes `fn(Database $tx, AccountPrincipal $principal, array $context): mixed`. They must not escape a context and reuse it as later mutation authority.

Additional mutation service contracts in `Workspaces`: `assign(string $accountId, string $tenantId, string $role, int $now): void`, `remove(string $accountId, string $tenantId, int $now): void`, `freeze(string $tenantId, int $expectedVersion, int $now): void`. They are internal/CLI services in B, not unauthenticated public endpoints. D must add explicit operator authentication before exposing them.

### Lock order

0. Operations changing platform-operator lifecycle acquire the singleton `identity_state` lock first. D must authenticate/recheck the actor within this same ordered transaction; it cannot call an operator-changing service from inside a previously entered `withAccount()` transaction.
1. For multi-account operations, declare all affected account IDs before entering the transaction and lock them in UUID order.
2. Discover identity IDs without locks only as locators, then lock/revalidate account rows and the affected session set by stable primary key. Password reset/disable uses bounded logical invalidation by security_version, not an unbounded session scan. Any operation that locks more than one session does so in UUID order.
3. Lock all affected tenant rows in UUID order, then membership rows.
4. Lock domain rows only after those gates. Existing `tenant_state` locks follow the tenant row.

New-account insertion has no existing account to lock; it creates account and workspace in one transaction. First-operator bootstrap serializes on the singleton `identity_state` row. Password hashing/verifying is outside prolonged SQL locks; lock/recheck account hash/security version before issuing/changing sessions. Failure after concurrent reset never authenticates a stale verified hash. Reset/disable uses account then sessions; freeze needs only its tenant row. Future D operations must not acquire a target account after already locking an actor account out of order.

---

### Task 1: Establish a non-destructive disposable fixture gate

**Files:** Create `lab/fixtures/host_v2/bootstrap.php`, `lab/scripts/run_host_v2_tests.py`, `lab/tests/test_host_v2_fixture.py`. No `host/config/config.php` access.

**Consumes:** explicit fixture-only PHP/PDO runtime and a separately authorized disposable DB instance. **Produces:** `BFixture::db(bool $migration = false): Database`, `BFixture::repoRoot(): string`, `BFixture::same(mixed $expected, mixed $actual): void`, `BFixture::code(string $expected, callable $operation): void`, `BFixture::password(): string`; Python runner's exact suite allowlist and exit behavior.

**Fixture prerequisite, not performed now:** owner provisions a separate loopback instance, port 3307, fresh **empty** schema `irica_b_test_<16 lowercase hex>`, distinct DDL/runtime users, and an instance marker in the separate control table `irica_b_fixture_control.runs(run_id, instance_id, dbname)`. Both fixture users receive SELECT on that control table; the runtime user otherwise has only SELECT/INSERT/UPDATE/DELETE on its target fixture schema. Keeping the marker outside the target preserves the pristine-database migration check. A private file outside the repo, `%TEMP%/irica_b_fixture.json`, contains `fixture=true`, `run_id`, `instance_id`, `php_path`, and `migrate`/`runtime` connection objects (`host`, `port`, `dbname`, `user`, `password`). Both objects must name the same approved schema. Never create this file by copying production config. No automatic installation or grant changes are part of current planning authorization.

- [ ] **Step 1: Write guard tests using only synthetic dictionaries.** The pure validator is shared by the runner before any process/socket and by PHP before PDO. It requires host exactly `127.0.0.1`, port exactly integer 3307, matching generated schema suffix/run ID, fixture=true, two nonempty distinct usernames and matching instance marker. Unknown fields fail; credential values never appear in exception text.

```python
import unittest
from run_host_v2_tests import validate_fixture

class FixtureGuardTests(unittest.TestCase):
    def test_rejects_a_production_shaped_target_before_connecting(self):
        for host, port, name in (("example.invalid", 3307, "irica_b_test_0123456789abcdef"),
                                 ("127.0.0.1", 3306, "irica_b_test_0123456789abcdef"),
                                 ("127.0.0.1", 3307, "irica")):
            with self.subTest(host=host, port=port, name=name):
                with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                    validate_fixture({"fixture": True, "run_id": "0123456789abcdef",
                                      "instance_id": "synthetic-instance", "php_path": "php",
                                      "migrate": {"host": host, "port": port, "dbname": name, "user": "fixture_ddl", "password": "synthetic"},
                                      "runtime": {"host": host, "port": port, "dbname": name, "user": "fixture_dml", "password": "synthetic"}})
```

- [ ] **Step 2: Run the pure guard suite; watch the missing validator fail.** Future command: `"C:/Program Files/Python310/python.exe" -I -B lab/scripts/run_host_v2_tests.py --suite fixture-guard`. The runner explicitly loads new tests from known paths with `importlib.util` and, before loading them, registers `sys.modules['run_host_v2_tests'] = sys.modules[__name__]`; this makes the shown guard-test import work under `-I` without adding repository paths or importing product modules. Missing script/function is RED only at this first task, not a successful test.
- [ ] **Step 3: Implement strict config validation and runner.** Read at most 16 KiB, reject JSON non-object/unknown keys, validate before connecting. PHP verifies `SELECT DATABASE()` and the exact marker after connection, UTC, driver/server version, `pdo_mysql`/`mbstring`, and denied DDL under runtime credentials. The marker is not sufficient by itself: all static target restrictions must also pass. Do not open a socket for a rejected configuration. Exit 2 for missing/unsafe prerequisites; never `skipTest` an integration gate. Whitelist suites `fixture-guard`, `migrations`, `integrity`, `workspaces`, `accounts`, `sessions`, `api`, `all`; refuse arbitrary file/SQL/URL arguments. Only the fixture adapter, not the application, receives test time/fault injection. Its CLI scenario files invoke concrete service methods and never expose SQL over HTTP.

```php
<?php
function same(mixed $expected, mixed $actual): void {
    if ($expected !== $actual) {
        throw new RuntimeException('FIXTURE_ASSERTION_FAILED');
    }
}
function expectApiCode(string $expected, callable $operation): void {
    try { $operation(); }
    catch (ApiException $error) {
        same($expected, $error->errorCode);
        return;
    }
    throw new RuntimeException('FIXTURE_EXPECTED_REJECTION');
}
```

`BFixture::same/code` delegate to these concrete helpers. `BFixture::password()` returns `bin2hex(random_bytes(16))` at runtime; it is not printed or retained in reports. PDO configs stay in memory; only a credential-file path, never credentials, is passed as a process argument/environment setting. The runner gives each PHP scenario a maximum 120-second timeout and sanitized exit/result labels. It serializes migration/integrity suites on the same approved fixture instance.

- [ ] **Step 4: Verify pure guards, then fixture prerequisite checks.** `--suite fixture-guard` must pass without a DB. `--suite all` must fail nonzero if the private config, instance, marker or required runtime is absent. Do not misreport that failure as a product regression or skip-as-success.
- [ ] **Step 5: Review diff and record the safe fixture binding.** Do not run existing `test_host_schema/test_host_api` against a shared DB. Their fixed names/destructive setup require a separately isolated legacy-compatibility instance later.

### Task 2: Recoverable additive migration runner and central schema

**Files:** Create `host/src/Migrations.php`, `MigrationPreflight.php`, `host/migrations/002_identity_foundation.sql`, `lab/fixtures/host_v2/test_migrations.php`. Modify `host/bin/migrate.php`, `host/src/Schema.php`, `host/INSTALL.md`. Preserve `001_initial.sql`.

**Consumes:** Task 1 guarded DDL/runtime connections; existing `Database::query/queryOne/queryValue/execute/transaction`, `Schema` allowlist. **Produces:** shared `Migrations::status/apply`, `MigrationPreflight::inspect`, central tables and tenant gates, counts-only report `{safe, blockers:[{code, table, count}], migration_status}`. Any future populated-host run requires a separately authorized maintenance writer freeze; do not attempt a concurrent online schema/backfill upgrade merely because an advisory migration lock exists.

- [ ] **Step 1: Write populated-upgrade and fault tests.** Initialize 001 only in an empty approved fixture, insert synthetic legacy tenant/user/member/customer/case/transaction rows and historical actor links, hash sorted rows, then apply 002. Record the initial column projections before upgrade and hash the 17 non-tracker legacy tables in stable primary-key order, excluding only newly introduced columns from the after-projection. Row counts alone, or hashes of `SELECT *` across a changing schema, are not preservation proof. Assert all original values and actor links unchanged; new metadata and migration tracker/journal changes are checked separately. Test crash before DDL, after DDL, before journal completion and before `schema_migrations` insertion for each step; start two independent PDO connections and assert exactly one migration runner owns the lock. Add checksum-drift and missing-recorded-file cases.

```php
<?php
require __DIR__ . '/bootstrap.php';
$db = BFixture::db(true);
$runner = new Migrations($db, BFixture::repoRoot() . '/host/migrations');
$before = $db->queryValue('SELECT COUNT(*) FROM users');
$runner->apply();
BFixture::same($before, $db->queryValue('SELECT COUNT(*) FROM users'));
BFixture::same('COMPLETE', $runner->status()['state']);
$runner->apply();
BFixture::same('COMPLETE', $runner->status()['state']);
```

The fixture bootstrap publishes repository root as `BFixture::repoRoot(): string`; it returns
a verified directory containing `host/migrations/001_initial.sql`, not cwd. This accessor is
part of Task 1's fixture contract.

- [ ] **Step 2: Run `--suite migrations`; require RED because the shared runner/002 is missing.** Do not use a missing DB prerequisite as RED for migration behavior; first finish Task 1's actual DB gate.
- [ ] **Step 3: Extract current parser and implement the recovery protocol.** Preserve legacy full filename-stem versions/checksums and 001 pristine-install safeguards. Inventory a first-run empty target before bootstrapping the journal; the fixture control marker lives outside it. On resume, only an exact compatible journal and its verified known-step history may explain partial schema objects; arbitrary untracked tables still block. Add an advisory lock keyed by current schema hash, `GET_LOCK(name, 0)`, released in `finally`. Unknown recorded files/checksum changes fail before DDL. Create the step journal only when absent; an existing incompatible journal blocks. Every new SQL statement has a stable `-- irica-step: <id>` marker and SHA-256; `Schema::migrationSteps()` provides exact expected pre/post metadata. Do not add stored procedures or a new general SQL parser.

```sql
CREATE TABLE migration_steps (
  migration_version VARCHAR(64) NOT NULL,
  step_id VARCHAR(64) NOT NULL,
  checksum CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  cursor_value VARCHAR(64) NULL,
  started_at DATETIME NOT NULL,
  completed_at DATETIME NULL,
  PRIMARY KEY (migration_version, step_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

Protocol per step models `started`, `applied`, `verified`, `failed` and `ambiguous`: verify complete expected engine-normalized metadata -> mark reconciled verified; verify complete pre-state and matching journal checksum -> record started, execute one statement, record applied if control returns, verify post-state, record verified/completed_at; an execution error records failed only when the journal connection remains usable; mixed/incompatible state -> `MIGRATION_STATE_AMBIGUOUS`, stop without repair. A crash can leave a started row even though DDL committed, so recovery always checks metadata rather than trusting status. The final file tracker is written only after all step postconditions pass. `status()` never mutates. DDL is never wrapped in a claimed atomic rollback. Normalize information_schema types/defaults/collations/index order separately for MySQL and MariaDB; do not compare raw SHOW CREATE strings across engines. Fault injection belongs in the fixture PDO/runner adapter, not an HTTP/config flag shipped enabled in production.

**002 tables and exact logical columns:**

| Table | Columns and constraints |
| --- | --- |
| `accounts` | UUID `id`; `login VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin UNIQUE`; `password_hash VARCHAR(255)`; Unicode `display_name VARCHAR(120)`; `status active/disabled`; `must_change_password` boolean; positive `security_version` default 1; `platform_role operator/member`; UTC created/updated |
| `account_workspace_memberships` | UUID `id`, account FK, tenant FK, `role admin/user`, `status active/removed`, UTC created/updated; UNIQUE(account_id,tenant_id); index(tenant_id,status,account_id) |
| `account_sessions` | UUID `id`, account FK, UNIQUE `token_hash CHAR(64) ascii_bin`; `purpose desktop/admin_web`; positive issued `security_version`; UTC issued/idle_expires/absolute_expires/last_activity/revoked; index(account_id,revoked_at,absolute_expires_at) |
| `legacy_account_map` | `(tenant_id,legacy_user_id)` primary key, account FK, `review_id CHAR(36)`, confirmed_at UTC; composite FK(tenant_id,legacy_user_id)->users(tenant_id,id); no credential copy or username-derived mapping |
| `identity_state` | singleton `id=1`, nullable first_operator_id account FK, positive row_version; serializes first bootstrap and last-active-operator protection |
| `account_security_events` | UUID id; nullable actor_account_id/subject_account_id account FKs; nullable tenant_id FK; actor_kind private_cli/account/system; closed event_type VARCHAR(32); outcome VARCHAR(16); created_at UTC; indexes(subject_account_id,created_at) and (tenant_id,created_at); no request payload, login, password/hash or token columns |

Security lifecycle events are inserted directly in the corresponding SQL transaction; do not route them through legacy `Log::audit()`'s swallowed-write behavior or legacy actor_user_id FKs. B's private CLI operations record actor_kind=private_cli with no invented account actor; self-service password/logout records the authenticated account. D must supply an authenticated operator actor when exposing account administration. Add rollback-on-audit-failure and forbidden-field tests, plus membership/freeze event assertions. Accepted event names are account_created, password_reset, account_disabled, account_enabled, legacy_linked, membership_assigned, membership_removed, workspace_frozen, login_success, logout and password_changed; outcome is success. Failed login attempts remain in the existing bounded rate-limit mechanism, not raw credential audit payloads.

Add UNIQUE users(tenant_id,id) **before** creating `legacy_account_map`. New account UUID/FK columns must have matching charset/collation; tenant and legacy-user FK columns match existing UTF-8 columns, not a silently changed ASCII parent. Add `tenants.lifecycle VARCHAR(16)`, `api_contract VARCHAR(24)`, `context_version INT`, nullable `replacement_tenant_id` self-FK. Existing rows are backfilled active/legacy_v1/context_version=1. Only after that backfill change column defaults to staging/authoritative_v2/1. `status='suspended'` remains an independent denial gate. Record each ALTER/backfill as a named bounded step; bound backfill by keyset chunks and persist its cursor rather than claim arbitrarily large data fits one transaction.

Extend `Schema` with grouped `foreignKeysFor(string $table): array`, `indexesFor(string $table): array`, and `migrationSteps(string $version): array`; preserve current scalar column reference API for legacy consumers. New status/role/purpose/version ranges are validated in PHP even on engines ignoring CHECK. `accounts` are not added to synchronization entity mappings.

- [ ] **Step 4: Run migration fixtures with no skipped cases.** Verify initial install plus full chain, populated upgrade, idempotent rerun, every interruption point, lock contention, missing file, checksum drift, unknown journal and counts-only blocker output. Verify runtime DDL refusal.
- [ ] **Step 5: Review recovery evidence and record engine/version.** No drop/reset advice for populated databases. Production preflight/restore rehearsal remains unrun and separately authorized.

### Task 3: Enforce existing same-tenant relationships with direct SQL

**Files:** Create `host/migrations/003_scoped_business_keys.sql`, `lab/fixtures/host_v2/test_integrity.php`; modify `MigrationPreflight.php`, `Schema.php`, targeted legacy schema test expectations only after their fixture isolation is established.

**Consumes:** Task 2 step engine and central schema. **Produces:** verified grouped composite FKs and indexes, with a read-only blocker report instead of destructive repair.

Required existing relationships:

| Child columns | Parent columns |
| --- | --- |
| settings(tenant_id,current_period_id) | periods(tenant_id,id) |
| crm_devices(tenant_id,customer_id) | customers(tenant_id,id) |
| crm_devices(tenant_id,owner_id) | owners(tenant_id,id) |
| crm_devices(tenant_id,period_id) | periods(tenant_id,id) |
| customer_transactions(tenant_id,customer_id) | customers(tenant_id,id) |
| customer_transactions(tenant_id,device_id) | crm_devices(tenant_id,id) |

- [ ] **Step 1: Write an INSERT and an UPDATE cross-tenant rejection test for every row of this matrix.** Include same-tenant success, permitted nullable foreign key, tombstoned parent and pre-existing cross-tenant row blocking activation. Test ordered constraint columns, not flattened pairs.

```sql
SELECT COUNT(*) AS blockers
FROM crm_devices c JOIN customers p ON p.id = c.customer_id
WHERE c.customer_id IS NOT NULL AND c.tenant_id <> p.tenant_id;
```

```php
<?php
require __DIR__ . '/bootstrap.php';
$db = BFixture::db();
$bad = BFixture::crossTenantCaseRow();
try {
    $db->insert('crm_devices', $bad);
    throw new RuntimeException('CROSS_TENANT_REFERENCE_ACCEPTED');
} catch (PDOException $error) {
    BFixture::same('23000', $error->getCode());
    BFixture::same(1452, (int) ($error->errorInfo[1] ?? 0));
}
```

`BFixture::crossTenantCaseRow()` is defined in this task's fixture file, returning all required valid case columns from two synthetic tenants; apart from a fresh per-attempt case UUID, it differs from its same-tenant positive control only in `customer_id`. Never reuse an already-inserted UUID for a negative INSERT. Assert driver error 1452, not duplicate-key 1062 or merely generic integrity SQLSTATE. Query the exact named constraint in `information_schema.KEY_COLUMN_USAGE` and ordered `ORDINAL_POSITION` to distinguish FK failure from unrelated validation/NOT NULL failures.

- [ ] **Step 2: Run `--suite integrity`; observe that the old ID-only FK admits the cross-tenant row.** A duplicate UUID or missing required field is not valid RED evidence.
- [ ] **Step 3: Add unique parent keys first, then additive composite FKs.** Keep ID-only FKs and historical keys until a later separately reviewed retirement. Name constraints deterministically below and use these names in metadata expectations.

```sql
ALTER TABLE customers ADD UNIQUE KEY uq_customers_tenant_id (tenant_id, id);
ALTER TABLE owners ADD UNIQUE KEY uq_owners_tenant_id (tenant_id, id);
ALTER TABLE periods ADD UNIQUE KEY uq_periods_tenant_id (tenant_id, id);
ALTER TABLE crm_devices ADD UNIQUE KEY uq_crm_devices_tenant_id (tenant_id, id);
ALTER TABLE settings ADD CONSTRAINT fk_settings_period_scoped
  FOREIGN KEY (tenant_id, current_period_id) REFERENCES periods (tenant_id, id);
ALTER TABLE crm_devices ADD CONSTRAINT fk_crm_devices_customer_scoped
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id);
ALTER TABLE crm_devices ADD CONSTRAINT fk_crm_devices_owner_scoped
  FOREIGN KEY (tenant_id, owner_id) REFERENCES owners (tenant_id, id);
ALTER TABLE crm_devices ADD CONSTRAINT fk_crm_devices_period_scoped
  FOREIGN KEY (tenant_id, period_id) REFERENCES periods (tenant_id, id);
ALTER TABLE customer_transactions ADD CONSTRAINT fk_customer_tx_customer_scoped
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id);
ALTER TABLE customer_transactions ADD CONSTRAINT fk_customer_tx_device_scoped
  FOREIGN KEY (tenant_id, device_id) REFERENCES crm_devices (tenant_id, id);
```

Each statement gets its own named/checksummed migration step. Preflight checks orphan and cross-tenant references for all six relationships, positive versions and existing supporting-index definitions. It emits table/constraint/count/code only, never names, national IDs, customer rows or raw logs. Blocked constraint installation changes no business records. New profile/result/attachment constraints belong to C/D when those tables actually exist.

- [ ] **Step 4: Run all six INSERT/UPDATE cases and both supported engine fixtures.** MySQL 5.7 CHECK non-enforcement must not make the application validator accept invalid status/version values. Preserve nullable semantics; a tombstone is not a deleted SQL parent.
- [ ] **Step 5: Review grouped metadata and unchanged historical rows.** Record exact engine matrix results; absence of an engine is a support-verification gap, not PASS.

### Task 4: Workspace lifecycle, membership and legacy contract locks

**Files:** Create `host/src/Workspaces.php`, `lab/fixtures/host_v2/test_workspaces.php`; modify narrow transaction boundaries in `Sync.php:269,473`, `Import.php:75`, and legacy tenant resolution/authentication in `Auth.php`.

**Consumes:** tenant lifecycle/context fields, account memberships, existing admin/user capability meanings. **Produces:** Workspaces contracts above and mandatory lock-held context for C/D.

- [ ] **Step 1: Write active/shared/isolated membership and state tests.** A workspace admin remains a platform member. A platform operator without membership cannot use ordinary workspace business endpoints. Staging, archived, suspended, removed membership and wrong contract return generic scoped denial; a stale expected context version returns `WORKSPACE_CONTEXT_STALE`. A frozen workspace is absent from selectable normal workspaces in B. Add two-connection write-versus-freeze and remove-versus-use barriers, not fixed sleeps.

```php
<?php
require __DIR__ . '/bootstrap.php';
$db = BFixture::db();
$workspaces = new Workspaces($db);
$ids = BFixture::twoAccountTwoWorkspaceFixture();
$db->transaction(function (Database $tx) use ($workspaces, $ids): void {
    BFixture::code('NOT_FOUND', fn() => $workspaces->lockAuthorizedWithin(
        $tx, $ids['account_a'], $ids['tenant_b'], 1, 'read'));
});
```

Define `twoAccountTwoWorkspaceFixture()` in this task's scenario helpers using valid central rows and explicit memberships. Tests include positive controls on each account's own tenant so a broken helper cannot satisfy all denials.

- [ ] **Step 2: Run `--suite workspaces`; observe missing service/state gate failures.** Existing scoped reads are not sufficient evidence for transaction-time lifecycle enforcement.
- [ ] **Step 3: Implement lock-held lookup and capability checks.** Start with the exact tenant-row query below inside the caller's transaction. Membership lookup is scoped to account+tenant and must return active role. Validate positive expected version and compare after locking. Increment context_version when changing lifecycle/contract. `assign/remove` lock account then tenant then membership; `freeze` locks tenant before returning. Do not expose public activate/archive/contract-switch endpoints in B.

```sql
SELECT id, status, lifecycle, api_contract, context_version
FROM tenants WHERE id = ? FOR UPDATE;
```

Capabilities: read/edit => admin or user; delete/settings/period_admin => admin only. Unknown capabilities/roles fail closed. `listWithin` accepts a validated nullable last-tenant UUID and integer limit 1-100, queries active memberships and active authoritative_v2 tenants in tenant-ID order with at most limit+1 rows, and returns `{items, next_id}`. The API encodes/decodes the UUID cursor without using it as authority; it never loads the whole membership set then slices in PHP. A new private workspace uses random UUID and non-personal slug, explicit active/authoritative_v2 state and admin membership for its owning account; account role remains member by default.

For legacy traffic, `lockLegacyWithin($tx,$tenantId,$write)` requires active, not suspended, lifecycle active and contract legacy_v1. It is invoked **inside** real Sync/Import mutation transactions, before `tenant_state` and business locks, not only once in the front controller. Legacy bootstrap/changes/auth must not reveal staging/v2/archived tenants. Legacy token refresh and bearer validation recheck the tenant gate. New tenant defaults must not silently break legacy create-user/admin: their existing tenant-creation paths explicitly create active/legacy_v1 only, with no access to new account tables. Preserve existing legacy credential behavior and do not fix unrelated reset targeting here.

- [ ] **Step 4: Verify state matrix and concurrent barriers.** An already admitted write holding the tenant lock may finish; freeze waits, then all later writes reject. Do not claim immediate cancellation. Verify v1 cannot read/write an authoritative_v2 or staging tenant even with valid old credentials.
- [ ] **Step 5: Record new lock-order integration points.** No data activation or cutover was performed; C/D must use these exact helpers.

### Task 5: Central account creation, passwords and explicit legacy mapping

**Files:** Create `host/src/Accounts.php`, `lab/fixtures/host_v2/test_accounts.php`; use `Workspaces.php`, `Tokens.php`, `Auth::hashPassword`, `Ids::v4`, new schema metadata.

**Consumes:** Task 4 private workspace transaction helper and Task 2 central schema. **Produces:** account contracts above; `{account_id, workspace_id, must_change_password}` creation receipt with no password/hash/token.

- [ ] **Step 1: Write canonicalization, global collision, private/shared workspace and named reset tests.** Invalid Unicode login, overlong input, whitespace interior, NUL, blank and malformed UTF-8 fail; no silent truncation. Same normalized login in different workspaces is still one global uniqueness conflict, not an automatic merge. New account+private workspace+membership must commit atomically. Failed workspace creation leaves no account. Legacy mapping requires exact tenant/user/account IDs plus a reviewed UUID, rejects wrong-tenant users, conflicting existing maps and targets without must_change_password=1, and never copies old password hashes. Test explicit reset-then-link success and rejection of an unrestricted existing target without changing that target.

```php
<?php
require __DIR__ . '/bootstrap.php';
BFixture::same('alice.one', Accounts::canonicalLogin("  ALICE.ONE\t"));
BFixture::code('VALIDATION_FAILED', fn() => Accounts::canonicalLogin(str_repeat('a', 65)));
BFixture::code('VALIDATION_FAILED', fn() => Accounts::canonicalLogin('user name'));
BFixture::code('VALIDATION_FAILED', fn() => Accounts::validateNewPassword(str_repeat('x', 11)));
BFixture::code('VALIDATION_FAILED', fn() => Accounts::validateNewPassword(str_repeat('x', 73)));
```

- [ ] **Step 2: Run `--suite accounts`; observe missing service or invalid canonicalization policy.** The legacy tenant-bound username validator is not the oracle for global accounts.
- [ ] **Step 3: Implement canonical login and password policy with the following actual validation core.** Use binary ASCII collation and catch duplicate-key SQLSTATE 23000 as `ACCOUNT_LOGIN_CONFLICT`, not a raw database message. Hash outside the write transaction; transactional creation calls `createPrivateWithin`. `platformRole` is a closed member/operator enum accessible only to private internal/CLI creation in B.

```php
<?php
function canonicalLogin(string $input): string {
    $login = strtolower(trim($input, " \t\r\n"));
    if (preg_match('/\A[a-z0-9._-]{1,64}\z/D', $login) !== 1) {
        throw new ApiException(422, Codes::VALIDATION_FAILED, 'login is invalid');
    }
    return $login;
}
function validateNewPassword(string $password): void {
    if (!mb_check_encoding($password, 'UTF-8') || str_contains($password, "\0")
        || mb_strlen($password, 'UTF-8') < 12 || strlen($password) > 72) {
        throw new ApiException(422, Codes::VALIDATION_FAILED, 'password is invalid');
    }
}
```

Place these bodies on the corresponding static methods in `Accounts`. `resetPassword` targets only account UUID, hashes the new password, then locks the account, increments security_version and sets must_change_password=1 in one transaction; every existing session is immediately invalid by version comparison without scanning all session rows. Physical expiry/revocation cleanup is bounded housekeeping, not an authorization dependency. It never accepts an arbitrary tenant administrator as the target. `setStatus` validates active/disabled and likewise logically invalidates sessions when disabling; serialize status/operator changes on identity_state before account locks and reject removal of the last active operator with `LAST_OPERATOR_REQUIRED`. `seedFirstOperator` owns its single transaction, locks identity_state, verifies no prior seed and uses a private account-creation-within-transaction helper; neither the CLI nor this helper nests a call to the transactional `create()` method. `linkLegacy` verifies the composite legacy user key and account existence, requires a valid reviewed UUID and a target already prepared with newly set credentials/must_change_password=1, stores review_id/confirmed_at, and never grants membership by inferred username. A pre-existing unrestricted target must be explicitly reset through the reviewed service before linkage; the mapper cannot silently copy or bless a legacy hash. Membership assignment is a separate explicit operation. Display names are valid UTF-8 and bounded to 120 Unicode code points, without using display names as record identity.

- [ ] **Step 4: Run account creation/reset/disable/concurrency/map fixtures.** Assert stored hashes verify but differ from plaintext; response/log schema contains no password hash/password. Race same canonical login: one commits, one conflict, no orphan workspaces. Reset of account A does not change B's username/password/memberships.
- [ ] **Step 5: Record password-policy proposal and compatibility.** No actual legacy identity mapping or reset occurs in this phase's tests outside synthetic fixtures.

### Task 6: Purpose-bound opaque sessions and live authorization

**Files:** Create `host/src/AccountSessions.php`, `lab/fixtures/host_v2/test_sessions.php`; extend necessary `Http.php` error constants without changing legacy error semantics.

**Consumes:** accounts/workspaces, Tokens helpers and server `now`. **Produces:** opaque login response `{token, expires_at, idle_expires_at, account:{id,display_name,platform_role,must_change_password}}`, `AccountPrincipal`, locked account/workspace callback interfaces, reset/logout/expiry rejection.

- [ ] **Step 1: Write deterministic server-clock tests.** Cover unknown/disabled account, bad password, purpose mismatch, legacy JWT/device token rejection, security-version mismatch, removed membership, reset/logout, forced change, exactly-equal deadlines, absolute expiry despite activity, and repeated passive polls beyond idle expiry. No fixed sleeps or external auth endpoints. Add two-connection reset-versus-session-admission and password-change race fixtures.

```php
<?php
require __DIR__ . '/bootstrap.php';
$db = BFixture::db();
$accounts = new Accounts($db);
$sessions = new AccountSessions($db);
$password = BFixture::password();
$made = $accounts->create('idle.fixture', $password, 'Synthetic account', 'member', 1800000000);
$db->execute('UPDATE accounts SET must_change_password = 0 WHERE id = :id',
    ['id' => $made['account_id']]);
$login = $sessions->loginDesktop('idle.fixture', $password, '127.0.0.1', 1800000000);
$sessions->withAccount($login['token'], 'desktop', 1800001799, false,
    fn(Database $tx, AccountPrincipal $principal) => $principal->accountId);
BFixture::code('AUTH_EXPIRED', fn() => $sessions->withAccount(
    $login['token'], 'desktop', 1800001800, false,
    fn(Database $tx, AccountPrincipal $principal) => $principal->accountId));
```

The direct SQL fixture preparation above modifies only the exact generated account on the already guarded disposable connection. It is not a production helper or test-only production bypass; forced-password-change behavior has its own separate positive and negative cases.

- [ ] **Step 2: Run `--suite sessions`; require a genuine missing/incorrect session behavior failure.** Login succeeds in a positive control before negative expiry/purpose tests.
- [ ] **Step 3: Implement opaque issuance and admission.** Validate token as exactly 64 lowercase hexadecimal characters before lookup; store only `Tokens::hashOpaque($raw)`. Legacy JWT/bare acting-user formats fail. `issueWithin` permits only desktop/admin_web; purposes are immutable. The raw token leaves only the successful login response, never AccountPrincipal, logs/audit, URLs or durable client storage.

```php
<?php
$raw = Tokens::newOpaque();
$sessionRow = [
    'id' => Ids::v4(), 'account_id' => $account['id'],
    'token_hash' => Tokens::hashOpaque($raw), 'purpose' => $purpose,
    'security_version' => (int) $account['security_version'],
    'issued_at' => gmdate('Y-m-d H:i:s', $now),
    'last_activity_at' => gmdate('Y-m-d H:i:s', $now),
    'idle_expires_at' => gmdate('Y-m-d H:i:s', $now + 1800),
    'absolute_expires_at' => gmdate('Y-m-d H:i:s', $now + 28800),
    'revoked_at' => null,
];
$tx->insert('account_sessions', $sessionRow);
```

Admission locates account by token hash, starts a transaction, locks/rechecks account then session, validates live active status, exact version/purpose, revoked_at null and both deadlines strictly after now. Forced-change sessions allow only me/logout/change-password; normal workspaces/business calls return `PASSWORD_CHANGE_REQUIRED`. `withWorkspace` performs the same admission and then calls `lockAuthorizedWithin` inside that transaction. Returned snapshots do not authorize later mutations outside that callback.

`loginDesktop` namespaces RateLimit account keys as `v2:` plus SHA-256(canonical login), serializes check/credential verification/record on a bounded account+IP advisory lock, uses a decoy password hash for missing/disabled credentials, then locks/rechecks current password hash/security_version before issuing. Failed/password-reset races record a generic failure. Release the attempt lock in `finally`; contention returns RATE_LIMITED with Retry-After, not unbounded waiting. Never extend idle on me/workspaces/worker heartbeat; future authorized human-command route names alone may update last_activity and idle_expires=min(now+1800,absolute_expires).

`changePassword` requires current password verification, validates/hashes the new password, rechecks the account/session under locks, clears must_change_password, bumps security_version and thereby invalidates **all** sessions, including the caller, without an unbounded row update; return reauthentication_required=true without minting a replacement automatically. Logout revokes the exact known session and checks purpose; a repeated known-session logout is a documented idempotent success, but a wrong-purpose token cannot affect another purpose. Operator rights are always from the live account row, never session purpose or a client role field.

- [ ] **Step 4: Run all session/forced-change/revocation/polling and two-connection tests.** Hash/version/password comparisons, generic error bodies, no raw credential logs and no server-side token plaintext must be asserted.
- [ ] **Step 5: Record admission semantics.** Logout/revocation cannot erase already delivered data or cancel externally dispatched work; C/E own those separate gates.

### Task 7: Exact v2 auth routes, private bootstrap and isolated HTTP proof

**Files:** Create `host/src/V2Api.php`, `host/bin/create-account.php`, `lab/fixtures/host_v2_front.php`, `lab/tests/test_host_v2_api.py`; modify `host/public/index.php`, `Http.php`, `INSTALL.md`, `SECURITY.md`. Append a fixture-only launch entry only after inspecting the existing `.claude/launch.json`; never replace the installed live-data preview entry.

**Consumes:** Tasks 4-6 services, existing Request/Response/Log envelope. **Produces:** the route/response contract below; no admin UI, imports, business mutation route or activation route.

| Exact route | Method | Contract |
| --- | --- | --- |
| `/v2/auth/login` | POST | JSON login/password only; unknown purpose/role/workspace body fields reject; desktop token and minimal account receipt |
| `/v2/auth/me` | GET | desktop token; live minimal account; passive/no idle touch; forced-change allowed |
| `/v2/auth/logout` | POST | desktop token; revoke exact session; no token echoed |
| `/v2/auth/change-password` | POST | old_password/new_password; forced-change allowed; all sessions revoked; reauthentication_required=true |
| `/v2/workspaces` | GET | live desktop session and no forced change; paginated active memberships/active authoritative_v2 tenants only, max 100 rows/page, opaque cursor, context_version |

- [ ] **Step 1: Write HTTP contracts with synthetic accounts only.** Test 404 for `/v2/auth/refresh`, 405 known wrong methods, rejected `/v2x`, `/v2/../v1`, encoded separators, repeated separators and configured-prefix confusion. Test invalid JSON/nonobject/unknown fields, missing bearer, legacy token, purpose mismatch, forced-change, disabled member and cross-tenant selection. Verify no Set-Cookie/CORS, no-store responses, generic failure envelopes and no credential/query values in logs. Operator-only internal services are not public just because a user is a workspace admin.

```python
import json
import urllib.error
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise ValueError("FIXTURE_REDIRECT_REJECTED")

def request_json(origin, path, method="GET", body=None, token=None):
    if (origin != "http://127.0.0.1:8766" or not path.startswith("/") or path.startswith("//")
            or len(path) > 2048 or any(ord(char) < 32 for char in path)):
        raise ValueError("FIXTURE_HTTP_TARGET_REJECTED")
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(origin + path, data=payload, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("FIXTURE_RESPONSE_TOO_LARGE")
        return response.status, response.headers, json.loads(raw)
```

The no-redirect handler rejects every redirect rather than carrying credentials to another host. The helper accepts relative malformed/version-negative paths for HTTP boundary tests, while the origin stays exactly the approved loopback fixture and ambient proxies stay disabled. Tokens/passwords remain in transient test request memory, not assertion dumps or argv.

- [ ] **Step 2: Start only the fixture preview with `preview_start`, then run `--suite api`; missing v2 route is RED.** Tests do not spawn servers themselves. Missing preview is a prerequisite failure, not a skipped suite.
- [ ] **Step 3: Implement exact version dispatch before legacy normalization.** `V2Api::dispatch(Request $request, int $now): array` returns `{status,body,headers}`; front controller emits it with existing Response::emit and sanitized labels. `V2Api::assertTransport(string $remoteAddr, bool $serverTls, bool $fixture = false): void` requires the server's trusted TLS indication in production; it ignores arbitrary `X-Forwarded-Proto`. Only the guarded CLI-server fixture adapter may pass fixture=true, and only for an exact loopback peer. Production index always passes fixture=false. Deployments needing a trusted TLS-terminating proxy require a separately verified server configuration; do not silently trust user headers. Add a pure test for forged forwarded HTTPS and a positive trusted-server TLS control. Constructor receives Database/Log/config services explicitly; no duplicate config read, no raw request body logging. Reject ambiguous/encoded version path forms; do not fall through an apparent v2 path into v1. Preserve configured v1 behavior for legitimate old URLs. Common JSON body cap is 16 KiB on v2 auth calls: classify the raw version path before `Request::capture`, and pass the smaller cap into capture so an oversized v2 body is never buffered up to the legacy import limit. Legacy import limits remain unchanged. A configured legacy base_path colliding with `/v2` is CONFIG_INVALID, not permission to route v1 handlers under v2. Add error constants PASSWORD_CHANGE_REQUIRED, WORKSPACE_CONTEXT_STALE, ACCOUNT_LOGIN_CONFLICT, LAST_OPERATOR_REQUIRED and SESSION_PURPOSE_MISMATCH to the existing grammar/list.

Private `create-account.php`: require PHP_SAPI=cli; take account parameters/password through stdin JSON read up to 16 KiB, never argv/environment, reject unknown fields; use new Accounts/Workspaces services. First operator uses locked identity_state and refuses reseeding. Subsequent private member creation defaults to a private workspace. Output IDs/forced-change flag only. It never delegates to legacy `create-admin.php --reset-password`; a new reset command is not exposed until explicitly reviewed. Do not run this CLI against any real config during B fixtures.

Fixture preview entry (append, do not replace existing entries):

```json
{
  "name": "host-v2-fixture",
  "runtimeExecutable": "php",
  "runtimeArgs": ["-S", "127.0.0.1:8766", "lab/fixtures/host_v2_front.php"],
  "port": 8766
}
```

`host_v2_front.php` requires PHP_SAPI=cli-server and loopback, loads only the approved fixture file/marker/runtime connection, then calls the same V2Api. It refuses non-v2 paths and never includes `Config::load()`, setup.php, Telegram relay, installed panel or a live host config. Use preview snapshot/network/console/log tools for the synthetic API check; never start a server via Bash or point preview at production. Browser-admin UI proof is intentionally deferred to D.

- [ ] **Step 4: Run API suite plus service/SQL suites, then narrow legacy compatibility in a separately bound disposable legacy fixture.** Capture only sanitized request counts/status/code/no-store/header evidence. Use `preview_network` for proof; no tokens or complete response bodies in screenshots/reports. Do not run network credential guessing or authorization probing on an external host.
- [ ] **Step 5: Review the integration diff and update security/install notes.** `/v1` remains unretired for legacy tenants; `/v2` is not a deployment or cutover approval.

### Task 8: Phase B acceptance, compatibility and handoff

**Files:** Review all scoped changes; update `host/INSTALL.md`, `SECURITY.md`; create `lab/docs/HOST_IDENTITY_PHASE_B.md` with counts-only acceptance and declared blockers.

**Consumes:** all previous fixture evidence. **Produces:** a fixture foundation acceptance report and explicit C/D/E contracts, not whole-product release PASS.

- [ ] **Step 1: Verify every Review Focus class has both a positive and targeted negative test.** Missing prerequisites/skips are not green acceptance. Confirm legacy model tests still describe the original schema while new full-chain/grouped metadata tests cover 002/003; do not merely replace hardcoded 18/237 with new magic counts.
- [ ] **Step 2: Run focused suites, then complete B suites on each declared engine.**

```bash
"C:/Program Files/Python310/python.exe" -I -B lab/scripts/run_host_v2_tests.py --suite all
```

Expected future result: all fixture-guard/migrations/integrity/workspaces/accounts/sessions/api cases executed, zero failures and zero prerequisite skips. This command does not exist at this planning snapshot; Task 1 creates it. Record each engine/version separately. MySQL 5.7 and MariaDB 10.3 compatibility is not inferred from MariaDB 11.4 or syntax checks.

- [ ] **Step 3: Verify PHP syntax and Python source compilation on the exact changed files.** Use `php -l` per file and isolated Python compile/AST without importing product state. Broad panel/host runners are permitted only after their destructive fixture binding is independently established; otherwise record the unrun suites and remaining gate by name.
- [ ] **Step 4: Independently review security/DB concurrency changes before integration.** Read-heavy mapping reports and Advisor critique are not implementation review or runtime evidence. Require independent review of the implemented diff and targeted regressions for concrete high-risk findings. Never reopen a previously denied external evidence gate by renaming it.
- [ ] **Step 5: Verify unchanged 001/raw robot hashes, no secrets/debug/generated/live data, and no unsupported activation/UI/worker work.** New account IDs cannot be written into legacy actor_user_id FKs. C adds separate account actor columns when its mutation audit paths are introduced. Existing actor history is retained, not remapped silently.
- [ ] **Step 6: Deliver fixture acceptance, unresolved risks and C/D service interfaces.** Real schema/collision inspection, actual account mapping, host installation, maintenance freeze, migration rehearsal, data activation and release remain separately authorized. No automatic commit/push/deploy or old-data deletion.

## Self-review and execution gates

Coverage: central login/password/status/roles -> Tasks 2/5; purpose/revocation/idle/force-change -> Task 6; memberships/lifecycle/API contract -> Task 4; migration recovery/scoped SQL integrity -> Tasks 2/3; versioned transport/CLI -> Task 7; full fixture/review evidence -> Task 8. Future profiles/results/images/imports/jobs/finance/desktop/backup requirements remain allocated to C-F, not omitted from the product roadmap.

Before implementation approval, review the explicit password bound, TTL/activity policy, engine matrix, fixture provisioning and logical admission semantics. No alternative production behavior is silently selected. Runtime provisioning and engine support cannot be verified by writing this plan.

Planning verification is limited to repository mapping, document/link/interface/snippet checks and unchanged source hashes. No migration, DB/API/business suite, preview, account creation, host inspection or production check was run for this document.

The first two Advisor design consultations timed out; the third completed successfully. Its corrections on lifecycle-write authorization, global lock order, nontransactional DDL recovery, strict unnormalized password bytes, purpose enforcement and legacy/TLS boundaries are incorporated above. That critique was not a complete implementation review, runtime proof, execution approval or the previously denied Phase A evidence gate.

Implementation method remains the user's earlier managed-agent preference, with disjoint file ownership and serial shared-schema integration; do not ask the user to reselect it. Await plan review and applicable fixture authorization before starting Task 1.

## Local checkpoint exception — Phase A and Phase B

Added 2026-10-15 by Advisor ruling (Saeed delegated the decision).

This amendment permits two ordered, local-only checkpoint commits on `lab/host-identity-phase-b`, based on the
recorded starting HEAD `d0932a65b3ec`:

1. A Phase A baseline containing only the reviewed Phase A work and its directly supporting documentation and tests.
2. A Phase B delta against that baseline containing only the completed identity/integrity implementation and its
   directly supporting documentation, tests, and this amendment.

Each checkpoint must have an explicit file-and-hunk scope, a reviewed staged diff, and verification of its exact
committed tree. Shared files must be separated by their actual Phase A and Phase B changes, not assigned wholesale
by filename. Unrelated work remains uncommitted.

Preserve the verified recovery snapshot at
`.superpowers/sdd/2026-10-11-host-identity-integrity/recovery-snapshot-2026-10-15/` and current work before
reconstructing these boundaries. Do not commit recovery-snapshot contents, credentials, runtime data, or unrelated
artifacts. Do not use destructive cleanup or history rewriting to create the checkpoints.

These commits establish recovery and review boundaries only. They do not authorize a push, tag, release, package
publication, deployment, production migration, or Phase C implementation. All other safety and release restrictions
remain unchanged. This exception does not authorize additional checkpoint commits or committing a subsequently
authored Phase C plan.

If a valid, independently verifiable Phase A baseline cannot be separated from Phase B, stop and obtain an amended
checkpoint boundary rather than creating a misleading intermediate commit.

### Amendment extension — Phase C (added 2026-10-16, Advisor ruling, Saeed delegated the decision)

Two separate authorizations, both narrow.

**1. Retrospective ratification of `b596172`.** The sentence "does not authorize … committing a subsequently
authored Phase C plan" is hereby relaxed for exactly one named commit: the docs-only Phase C plan
`docs/superpowers/plans/2026-10-15-phase-c-queries-domain.md`, commit `b596172`. It is ratified as a third
post-baseline commit on `lab/host-identity-phase-b` and no other. The two implementation-checkpoint slots above
remain **exhausted**: this ratification does not create a reusable slot, and no further Phase C content may be
committed to `lab/host-identity-phase-b` until the Phase C integration gate is reviewed and approved.

**2. Phase C isolated-branch checkpoints.** Phase C implementation work is checkpointed **only** on an isolated
worktree branch, never on `lab/host-identity-phase-b`. Permitted: local-only, task-scoped commits containing
reviewed task scope — the task's implementation, its approved contract/document changes and its test fixtures —
staged as an explicit file allowlist. Explicitly **not** permitted without the applicable later approval: a push,
a merge, a cherry-pick onto `lab/host-identity-phase-b`, a tag, a release, or any deployment. An isolated-branch
commit is a recovery and review boundary, not integration; control happens at the gate.

Excluded from every such commit: generated databases, credentials, fixture secrets, runtime logs and state,
the `.superpowers/` SDD workspace, and recovery-snapshot contents. Each commit carries an explicit file allowlist
and a reviewed staged diff, and its exact committed tree is verified afterwards.

Pending hunk: this extension is itself recorded uncommitted in the working tree and must ride with the next
authorized commit on `lab/host-identity-phase-b` (the Phase C integration gate).
