# Phase B acceptance, compatibility and handoff

Fixture-foundation acceptance for the host identity/integrity work (Tasks 1-7).
Counts, exit codes and declared blockers only. This is not a whole-product
release PASS, not an activation, not a cutover and not a deployment approval.

## 1. What was accepted

The Phase B foundation on branch `lab/host-identity-phase-b`:

- additive recoverable migrations (`001_initial`, `002_identity_foundation`,
  `003_scoped_business_keys`) with a per-step journal, advisory lock and
  fail-closed adoption;
- scoped business keys with cross-tenant reference gates enforced on every
  apply path and surfaced by preflight status;
- central accounts with strict password policy, first-operator bootstrap and
  legacy identity mapping guards;
- live revocable sessions with purpose binding, forced-change gating and a
  server-controlled clock;
- tenant lifecycle/api-contract gates keeping legacy `/v1` serving exactly
  its own tenants;
- five `/v2` routes (login, me, logout, change-password, workspaces) plus the
  private `create-account.php` CLI, additive alongside the unretired `/v1`.

`/v1` stays the served API. `/v2` is not a cutover.

## 2. Evidence produced by Task 8 (commands run, output read)

Engine: MariaDB 10.3.39 (`FIXTURE_READY driver=mysql
server=10.3.39-MariaDB`), PHP 8.1.34, Python 3.10.11.
Fixture state at run time: 25 tables (from the `FIXTURE_READY ... tables=25`
line of every suite).

| Command | Result |
|---|---|
| `python -I -B lab/scripts/run_host_v2_tests.py --suite fixture-guard` (no `--fixture-config`) | exit 0, 43/43 OK |
| `python -I -B lab/scripts/run_host_v2_tests.py --suite all --fixture-config <fixture.json>` | exit 0; one `FIXTURE_READY` each for migrations, integrity, workspaces, accounts, sessions, api; `FIXTURE_MIGRATIONS_PASS`, `FIXTURE_INTEGRITY_PASS`, `FIXTURE_WORKSPACES_PASS`, `FIXTURE_ACCOUNTS_PASS`, `FIXTURE_SESSIONS_PASS`, `FIXTURE_API_PASS`; zero failures, zero prerequisite skips |
| per-suite runs (migrations, integrity, workspaces, accounts, sessions, api) | each exit 0 with exactly one `FIXTURE_READY` |
| `api` suite front | disposable `php -S 127.0.0.1:8766 lab/fixtures/host_v2_front.php`, loopback only, started and killed inside one run, port 8766 verified closed afterwards; origin from `IRICA_V2_ORIGIN` (default `http://127.0.0.1:8766`) |

Assertion volumes (static counts from the delivered files, all green above):
migrations 180 `t()` assertions, integrity 64, workspaces 118, accounts 182,
sessions 165 (709 PHP assertions total); api 35 tests (31 HTTP/CLI
`ApiFixture` cases a01-a04, b01-b07, c01-c14, e01-e06 plus 4 `PurePhpGates`
cases d01-d04); fixture-guard 43/43.

## 3. Coverage audit against the plan Review Focus list

1. Legacy token / malformed versioned URL must not enter a v2 handler.
   Positive: api a04 (known v2 routes survive a raw request line), c01
   (login receipt shape). Negative: api a03 (version-path confusion never
   served), b05/b06 (missing, malformed and legacy credentials answer
   `AUTH_REQUIRED`). Covered.
2. Reset / removal / freeze concurrent with an admitted write has a defined
   linearization point. Positive: workspaces two-process InnoDB barriers
   (admitted write finishes, freeze waits on the tenant lock);
   sessions two-connection lock-ordering fixtures; accounts reset+link
   pre-checks. Negative: lock-wait-timeout proof (child blocked on the held
   lock), logout lock-order structural test. Gap G6 below (race test is
   opportunistic).
3. DDL interrupted before/after a crash is never reapplied blindly or falsely
   marked complete. Positive: migrations `driveStepThroughCrashes` before and
   after every named step and before final tracking. Negative: section K real
   ambiguity refusal with zero DDL/repair, plus the negative control proving
   adoption still refuses a genuinely wrong schema after the `rows.*`
   exclusion. Covered.
4. Nullable relationship / tombstoned parent must not weaken cross-tenant FK
   enforcement. Positive: integrity INSERT and UPDATE directly for every
   relationship, NULL FK still inserts, tombstoned same-tenant parent still
   resolves. Negative: cross-tenant and orphan writes refused with
   `C_CROSS_TENANT_REFERENCE` / `C_ORPHAN_REFERENCE`, plus the degenerate-gate
   negative control on an already-applied 003. Covered.
5. Passive polling / wrong purpose / forced-change must not obtain normal
   business authority. Positive: sessions fixed server clock, idle deadlines
   exact, forced-change admits only self-service; api c05/c06/c08.
   Negative: polls leave activity byte-identical yet still expire, wrong
   purpose answers `SESSION_PURPOSE_MISMATCH`, forced-change blocked from
   workspaces. Covered.

Task review close-out (from the workspace ledger and rulings 33-63): Task 1
COMPLETE (2 reviews + 2 re-reviews); Task 2 CLEAN after fix round 2;
Task 3 CLEAN after fix round 1; Task 4 CLEAN after fix round 1;
Task 5 CLEAN after fix round 1; Task 6 fix round CLEAN for its own finding
with two pre-existing Task 2/3 findings routed to the integrity fix round 1,
which closed CLEAN; Task 7 ACCEPT WITH MINOR DEFERRALS (0C/0I/7M).

## 4. Coverage gaps (written down, not passed over)

- G1: no test binds `index.php`'s v2 branch; flipping its fixture flag or
  deleting the early return ships green (Task 7 M-2).
- G2: the front's SAPI and exact-loopback guards — the two preconditions
  ruling 43 rests on — are exercised by no test (Task 7 M-3).
- G3: `issuedForAnotherPurpose()` has no liveness predicate; a revoked or
  expired other-purpose session is labelled purpose-mismatch instead of
  revoked/expired (401 either way; Task 7 M-5).
- G4: api `test_c14` hardcodes the log path and ignores `IRICA_V2_LOG`
  (Task 7 M-7).
- G5: the `Codes::ALL` agreement guard cannot run without a listening front
  (Task 7 M-1).
- G6: the accounts race test is opportunistic, not forced (Task 5 M-5).
- G7: the PHP duplicate-key tokenizer and surrogate rejection have no
  dedicated test (Task 1 deferred minor).
- G8: latent uncaught `JsonException` in the PHP JSON reader (Task 1
  deferred minor).
- G9: the `outcome <> 'success'` assertion in `test_migrations.php` is still
  globally unscoped (rulings 53/175).
- G10: `AccountPrincipal` carries no `sessionId` (extra `displayName`
  instead); a caller holding only a principal cannot name its session row
  (ruling 46).
- G11: `Workspaces::assign()`, `remove()` and `freeze()` do not check
  `accounts.status`; only `lockAuthorizedWithin()` does (Task 4 hand-off).
- G12: `lab/tests/test_host_api.py::test_migrate_applies_once_then_skips_and_refuses_drift`
  is scoped out (section 7): it describes the pre-B single-file CLI contract
  and cannot pass against the step-journal CLI.

## 5. Engine matrix

| Engine | Result |
|---|---|
| MariaDB 10.3.39 (Windows x64 ZIP, SHA-256 verified) | All suites green (section 2) |
| MySQL 5.7 | BLOCKED and UNRUN. The official CDN returns an embargo redirect that was never followed. No support, compatibility or PASS is claimed or inferred from MariaDB results or syntax checks. |

## 6. Unrun and blocked gates (by name, never counted as green)

- MySQL 5.7 compatibility run (blocked, section 5).
- `lab/scripts/run_panel_tests.py` (broad panel runner; fixture binding not
  established for this task).
- `lab/tests/test_host_api.py` and `lab/tests/test_host_schema.py`
  (destructive legacy harnesses bound to real credentials; schema suite
  frozen at the initial 18 tables / 237 columns by ruling 13).
- `lab/tests/test_create_admin.py` (parallel session's file).
- `lab/scripts/run_online_feasibility.py` and `lab/tests/test_online_*`
  (online suites; out of Phase B scope).
- `php -l` / Python compile checks on changed files: Task 8 changed no PHP
  and no Python file (ruling-19 option scoped-out, section 7), so there was
  nothing to lint; recorded as not applicable rather than passed.

## 7. Ruling 19 decision: scope out, with evidence

`lab/tests/test_host_api.py` asserts three strings against `migrate.php`
output: `"APPLY 001_initial"`, `"SKIP  001_initial"` and `"non-empty"`.
The CLI today (`host/bin/migrate.php`) prints only counts and stable codes —
`state:`, `applied versions:`, `pending versions:`, `verified steps:`,
`pending steps:`, `blockers:` and `nothing else to apply (account: ...)` —
with failures as `FAIL <code>` on stderr. A grep over `host/bin/migrate.php`
and `host/src/Migrations.php` finds none of the three strings outside a
docblock reference to the file name. The surrounding test function further
assumes the pre-B single-file model (one `schema_migrations` row, no journal,
no 002/003, no adoption), so correcting three strings would leave a test that
still cannot pass, and the harness is destructive and forbidden against the
shared fixture. The file was therefore left unedited and the test is scoped
out here instead of corrected.

## 8. Integrity sweep (whole branch)

- `host/migrations/001_initial.sql` and `gomrok.py` are byte-identical to
  their pins (`a5d48d9a…`, `5d00ac15…`).
- No secret, credential, token, session id, password hash, username or IP in
  source, tests, docs or reports. Two scan hits reviewed and cleared:
  doc-comment password placeholders in `bin/create-admin.php` /
  `bin/create-user.php` usage text (both pre-existing, unmodified files).
- `DROP`/`TRUNCATE` scan: hits only in `bin/backup-to-telegram.php`
  (dump writer) and `bin/smoke-test.php` (DDL probe cleanup) — both
  pre-existing, unmodified, outside Phase B. Nothing in the Phase B diff
  drops, truncates or recreates a schema object.
- `lab/tests/test_host_schema.py` still asserts the initial 18 tables /
  237 columns (ruling 13 intact).
- No new account id is written into a legacy `actor_user_id` foreign key:
  every `actor_user_id` write is in the legacy `Auth`/`Import`/`Log` paths
  using legacy user ids; `Accounts`, `Workspaces`, `AccountSessions`,
  `V2Api`, `create-account.php`, `setup.php` and `create-admin.php` never
  write that column.
- No activation, admin-UI, worker or import work in the Phase B surface;
  no debug output; no generated files; no live or customer data.

## 9. Service contracts handed to C/D/E

Foundation only — each consumer adds its own authentication, authorization
and review before exposure:

- `Accounts`: transactional creation (`createWithin`), first-operator
  bootstrap (`seedFirstOperator`, missing-row is an actionable CLI error),
  password policy (12 codepoints / 72 UTF-8 bytes, no trim/normalise),
  legacy mapping with uniqueness on `account_id`.
- `AccountSessions`: purpose-bound admission, passive reads never renew
  idle, forced-change admits only me/logout/change-password,
  account-then-session lock order on every path including logout.
- `Workspaces`: lock-held lookup with expected-version compare after the
  row lock; `assign` / `remove` / `freeze` are internal/CLI services —
  D must add explicit operator authentication before exposing them, and
  should add the `accounts.status` check of G11.
- `V2Api`: five routes with exact 404/405/415/400 behaviour, 16 KiB body
  cap from `Content-Length`, unknown-field refusal, `no-store` closed-CORS
  responses, purpose-mismatch relabelling only on a live differing-purpose
  row (G3 notes the liveness edge).
- `Migrations` / `MigrationPreflight`: step journal, advisory lock,
  crash reconciliation, fail-closed adoption; C adds separate account actor
  columns when its mutation audit paths are introduced (existing actor
  history retained, never remapped).

## 10. NOT done (separately authorized work)

Real schema/collision inspection, actual account mapping, host installation,
maintenance freeze, migration rehearsal, data activation and release. The
deferred minors of Tasks 1-7 and the integrity fix round (including G1-G11)
ride to the final whole-branch review.
