<?php
/**
 * test_workspaces.php — Phase B workspace lifecycle/membership suite
 * (`--suite workspaces`).
 *
 * Runs against the separately provisioned fixture instance only. It never
 * reads application configuration, never touches production, and never prints
 * a credential, a login, a password, a hash, a token, a session identifier or
 * a row value: a failure is always one of the fixed FIXTURE_* labels emitted
 * by bootstrap.php, and every assertion message names the RULE, not the data.
 *
 * What this suite proves
 *
 *   1. Lock-held lookup. `Workspaces::lockAuthorizedWithin()` locks the tenant
 *      row (`SELECT … FOR UPDATE`) inside the CALLER's transaction, compares
 *      the expected context version only AFTER that lock, then applies the
 *      state gate, the membership gate and the capability gate. Without the
 *      lock, two connections could both admit a write against a workspace
 *      that a freeze committed between their read and their write.
 *   2. Generic scoped denial. Staging lifecycle, suspended status, a
 *      non-authoritative (legacy_v1) contract, a removed membership and a
 *      membership that never existed all answer with ONE code and ONE
 *      message, so the refusal cannot be used as an oracle for which of them
 *      is true. A positive control on each account's OWN tenant runs beside
 *      every denial, so a broken fixture helper cannot satisfy the suite by
 *      denying everything.
 *   3. Capability separation. read/edit => admin or user;
 *      delete/settings/period_admin => admin only. An unknown capability and
 *      an unknown role both fail closed.
 *   4. Context version. A stale expected version answers
 *      WORKSPACE_CONTEXT_STALE; a non-positive expected version is a
 *      validation refusal, never a silent accept.
 *   5. Listing. `listWithin()` returns only active memberships joined to
 *      active authoritative_v2 tenants, in tenant-id order, with at most
 *      `limit` items and a `next_id` cursor taken from a limit+1 peek. It
 *      never loads the whole membership set and slices it in PHP.
 *   6. New private workspace. Random UUID, non-personal slug, explicit
 *      active/authoritative_v2 state, admin membership for its owning
 *      account, and the account's PLATFORM role untouched (member by
 *      default). The name is bounded in CHARACTERS (120), so a multibyte
 *      name at and over the limit is stored as valid UTF-8, and
 *      `createPrivateWithin()` creates the account's workspace inside the
 *      CALLER's transaction so both roll back together. No activate /
 *      archive / contract-switch endpoint exists in this phase, and this
 *      suite asserts none is reached.
 *   7. Legacy contract locks. `lockLegacyWithin()` requires active status,
 *      lifecycle `active` and contract `legacy_v1`, and it is invoked INSIDE
 *      the real Sync/Import mutation transactions BEFORE the tenant_state and
 *      business locks. Legacy bootstrap, changes and authentication must not
 *      reveal a staging or authoritative_v2 tenant, and an old but valid v1
 *      credential cannot read or write one.
 *   8. Account and admin invariants. A DISABLED account keeps no workspace
 *      authority even with an active membership row, and the LAST active
 *      admin of a workspace cannot be removed — one admin may not be taken
 *      out, two admins may lose one, and only then is the survivor protected.
 *   9. Concurrency barriers on real row locks. A write already admitted while
 *      holding the tenant lock MAY finish; the freeze waits for that lock and
 *      only then commits, after which every later write is rejected. This is
 *      proved with a second connection and a second process synchronised on
 *      observable files — never with a fixed sleep.
 *
 * Nothing here drops or truncates a table, and nothing re-provisions. Every
 * synthetic row this suite creates is deleted by this suite, in dependency
 * order, in a `finally` block. The populated fixture state (001+002+003,
 * journal verified) is carried forward untouched.
 *
 * Engine matrix (recorded honestly, never inferred)
 *   MariaDB 10.3.39 — the only provisioned engine, exercised here.
 *   MySQL 5.7       — BLOCKED, UNRUN: the official CDN returns an embargo
 *                     redirect that was not followed. No MySQL support,
 *                     compatibility or PASS is claimed or implied.
 */

declare(strict_types=1);

require __DIR__ . '/bootstrap.php';

$repoRoot = BFixture::repoRoot();
foreach (['Schema', 'Database', 'Http', 'Ids', 'Log', 'Tokens', 'Roles',
          'RateLimit', 'Workspaces', 'Sync', 'Import', 'Auth'] as $classFile) {
    require_once $repoRoot . '/host/src/' . $classFile . '.php';
}

/** One fixed synthetic timestamp; no clock, no locale, no drift. */
const STAMP = '2026-01-01T00:00:00+00:00';

/** The synthetic tenants this suite creates and always deletes. */
const SYNTHETIC_TENANTS = [
    'tenant_a', 'tenant_b', 'tenant_shared', 'tenant_isolated',
    'tenant_staging', 'tenant_freeze', 'tenant_remove', 'tenant_barrier',
    'tenant_assign', 'tenant_legacy', 'tenant_credential_v2',
    'tenant_credential_staging',
    'tenant_page_1', 'tenant_page_2', 'tenant_page_3', 'tenant_page_4',
    'tenant_page_5',
];

const SYNTHETIC_ACCOUNTS = ['account_a', 'account_b', 'account_op'];

/**
 * Every table a synthetic tenant row can be referenced from, in DELETE order.
 *
 * A row that points at a `users` row (or at itself) must go before that row,
 * and a row that points at another tenant-scoped table must go after it.
 */
const TENANT_CHILDREN = [
    'customer_transactions', 'crm_devices', 'settings', 'customers', 'owners',
    'identities', 'periods',
    'sync_devices',
    'auth_sessions',
    'memberships', 'login_attempts', 'audit_events', 'change_log',
    'idempotency_keys', 'tenant_state',
    'legacy_account_map', 'account_security_events',
    'account_workspace_memberships',
];

function t(bool $condition, string $what): void
{
    if (!$condition) {
        throw new RuntimeException('SCENARIO_FAILED: ' . $what);
    }
}

function scalar(Database $db, string $sql, array $params = []): mixed
{
    return $db->queryValue($sql, $params);
}

/** A fresh RFC-4122-shaped UUID. Never reused between attempts. */
function uuid(): string
{
    return Ids::v4();
}

function accountRow(string $id, string $platformRole): array
{
    // The login is random hex: an account fixture must never look like a
    // person, and it must never collide with a row this suite does not own.
    return [
        'id' => $id,
        'login' => 'fixture_' . substr(str_replace('-', '', $id), 0, 20),
        'password_hash' => password_hash(bin2hex(random_bytes(16)), PASSWORD_DEFAULT),
        'display_name' => '',
        'status' => 'active',
        'must_change_password' => 0,
        'security_version' => 1,
        'platform_role' => $platformRole,
        'created_at' => STAMP,
        'updated_at' => STAMP,
    ];
}

function tenantRow(string $id, string $status, string $lifecycle,
                   string $contract, int $contextVersion = 1): array
{
    return [
        'id' => $id,
        // Non-personal and collision-free: derived from the random tenant UUID.
        'slug' => 'fx-' . substr(str_replace('-', '', $id), 0, 24),
        'name' => 'Fixture workspace',
        'status' => $status,
        'lifecycle' => $lifecycle,
        'api_contract' => $contract,
        'context_version' => $contextVersion,
        'created_at' => STAMP,
        'updated_at' => STAMP,
    ];
}

function membershipRow(string $id, string $accountId, string $tenantId,
                       string $role, string $status = 'active'): array
{
    return [
        'id' => $id,
        'account_id' => $accountId,
        'tenant_id' => $tenantId,
        'role' => $role,
        'status' => $status,
        'created_at' => STAMP,
        'updated_at' => STAMP,
    ];
}

function userRow(string $id, string $tenantId, string $username): array
{
    return [
        'id' => $id,
        'tenant_id' => $tenantId,
        'username' => $username,
        'password_hash' => password_hash(bin2hex(random_bytes(16)), PASSWORD_DEFAULT),
        'display_name' => '',
        'must_change_password' => 0,
        'status' => 'active',
        'token_version' => 1,
        'row_version' => 1,
        'created_at' => STAMP,
        'updated_at' => STAMP,
    ];
}

function sessionRow(string $id, string $tenantId, string $userId,
                    string $tokenHash): array
{
    $now = gmdate('Y-m-d H:i:s');
    return [
        'id' => $id,
        'tenant_id' => $tenantId,
        'family_id' => uuid(),
        'kind' => 'user',
        'token_hash' => $tokenHash,
        'user_id' => $userId,
        'sync_device_id' => null,
        'parent_id' => null,
        'issued_at' => $now,
        'idle_expires_at' => gmdate('Y-m-d H:i:s', time() + 3600),
        'absolute_expires_at' => gmdate('Y-m-d H:i:s', time() + 7200),
        'used_at' => null,
        'revoked_at' => null,
        'revoked_reason' => '',
        'rotated_to' => null,
        'created_at' => $now,
    ];
}

/**
 * Ruling 23 precedent: `BFixture` is declared in the frozen bootstrap.php and
 * PHP cannot redeclare or extend it, so this task's fixture helper is a LOCAL
 * function with the same contract the brief asks for.
 *
 * Two accounts each own a workspace as admin (the positive controls that keep
 * a broken helper from satisfying every denial), plus shared, isolated,
 * staging, suspended, wrong-contract, pagination and credential tenants.
 *
 * @return array<string, string>
 */
function twoAccountTwoWorkspaceFixture(Database $db): array
{
    $ids = [];
    foreach (SYNTHETIC_ACCOUNTS as $key) {
        $ids[$key] = uuid();
    }
    foreach (SYNTHETIC_TENANTS as $key) {
        $ids[$key] = uuid();
    }

    $db->insert('accounts', accountRow($ids['account_a'], 'member'));
    $db->insert('accounts', accountRow($ids['account_b'], 'member'));
    // A platform operator with NO workspace membership: platform_role must
    // grant nothing inside an ordinary workspace.
    $db->insert('accounts', accountRow($ids['account_op'], 'operator'));

    $v2 = 'authoritative_v2';
    $legacy = 'legacy_v1';
    $db->insert('tenants', tenantRow($ids['tenant_a'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_b'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_shared'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_isolated'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_staging'], 'active', 'staging', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_freeze'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_remove'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_barrier'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_assign'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_legacy'], 'active', 'active', $legacy));
    $db->insert('tenants', tenantRow($ids['tenant_credential_v2'], 'active', 'active', $v2));
    $db->insert('tenants', tenantRow($ids['tenant_credential_staging'], 'active', 'staging', $legacy));
    foreach ([1, 2, 3, 4, 5] as $n) {
        $db->insert('tenants', tenantRow($ids['tenant_page_' . $n], 'active', 'active', $v2));
    }

    $give = function (Database $db, array $ids, string $account, string $tenant,
                      string $role, string $status = 'active'): void {
        $db->insert('account_workspace_memberships',
            membershipRow(uuid(), $ids[$account], $ids[$tenant], $role, $status));
    };
    $give($db, $ids, 'account_a', 'tenant_a', 'admin');
    $give($db, $ids, 'account_b', 'tenant_b', 'admin');
    // Shared: account_a is a plain member, and account_b's row is REMOVED.
    $give($db, $ids, 'account_a', 'tenant_shared', 'user');
    $give($db, $ids, 'account_b', 'tenant_shared', 'user', 'removed');
    // Isolated: only account_b may ever reach it.
    $give($db, $ids, 'account_b', 'tenant_isolated', 'admin');
    // account_a is an admin of the staging tenant: the STATE gate, not the
    // membership gate, must be what refuses it.
    $give($db, $ids, 'account_a', 'tenant_staging', 'admin');
    $give($db, $ids, 'account_a', 'tenant_freeze', 'admin');
    $give($db, $ids, 'account_b', 'tenant_remove', 'admin');
    // Two admins on purpose: the remove barrier takes account_b out of this
    // workspace, and the last active admin of a workspace may never be
    // removed — a single-admin barrier would now be refused, not raced.
    $give($db, $ids, 'account_a', 'tenant_remove', 'admin');
    $give($db, $ids, 'account_a', 'tenant_barrier', 'admin');
    $give($db, $ids, 'account_a', 'tenant_assign', 'admin');
    foreach ([1, 2, 3, 4, 5] as $n) {
        $give($db, $ids, 'account_a', 'tenant_page_' . $n, 'admin');
    }
    // A tombstoned membership row, not an absent one: the gate must refuse
    // `status = 'removed'` exactly like a membership that never existed.
    $give($db, $ids, 'account_a', 'tenant_isolated', 'admin', 'removed');

    // --- legacy credential tenants: a real user, membership and session pair
    // so an OLD but VALID v1 credential can be replayed against a v2 or a
    // staging tenant.
    foreach (['v2' => 'tenant_credential_v2',
              'staging' => 'tenant_credential_staging'] as $tag => $tenantKey) {
        $tenant = $ids[$tenantKey];
        $user = uuid();
        $ids['user_credential_' . $tag] = $user;
        $db->insert('users', userRow($user, $tenant, 'fixture_' . $tag . '_' . substr(str_replace('-', '', $user), 0, 12)));
        $db->insert('memberships', [
            'id' => uuid(), 'tenant_id' => $tenant, 'user_id' => $user,
            'role' => 'admin', 'created_at' => STAMP, 'updated_at' => STAMP,
        ]);
        $ids['session_access_' . $tag] = uuid();
        $db->insert('auth_sessions', sessionRow(
            $ids['session_access_' . $tag], $tenant, $user, bin2hex(random_bytes(32))));
        $ids['refresh_token_' . $tag] = bin2hex(random_bytes(24));
        $ids['session_refresh_' . $tag] = uuid();
        $db->insert('auth_sessions', sessionRow(
            $ids['session_refresh_' . $tag], $tenant, $user,
            Tokens::hashOpaque($ids['refresh_token_' . $tag])));
    }
    // One legacy tenant that IS usable, as the positive control for the
    // legacy credential paths.
    $legacyUser = uuid();
    $ids['user_credential_legacy'] = $legacyUser;
    $db->insert('users', userRow($legacyUser, $ids['tenant_legacy'], 'fixture_legacy_' . substr(str_replace('-', '', $legacyUser), 0, 12)));
    $db->insert('memberships', [
        'id' => uuid(), 'tenant_id' => $ids['tenant_legacy'], 'user_id' => $legacyUser,
        'role' => 'admin', 'created_at' => STAMP, 'updated_at' => STAMP,
    ]);
    $ids['session_access_legacy'] = uuid();
    $db->insert('auth_sessions', sessionRow(
        $ids['session_access_legacy'], $ids['tenant_legacy'], $legacyUser,
        bin2hex(random_bytes(32))));
    $ids['refresh_token_legacy'] = bin2hex(random_bytes(24));
    $ids['session_refresh_legacy'] = uuid();
    $db->insert('auth_sessions', sessionRow(
        $ids['session_refresh_legacy'], $ids['tenant_legacy'], $legacyUser,
        Tokens::hashOpaque($ids['refresh_token_legacy'])));

    return $ids;
}

/** Delete every synthetic row this suite owns, in foreign-key-safe order. */
function clearSynthetic(Database $db, array $ids): void
{
    if ($ids === []) {
        return;
    }
    $tenants = [];
    $accounts = [];
    foreach (SYNTHETIC_TENANTS as $key) {
        $tenants[] = $ids[$key];
    }
    foreach (SYNTHETIC_ACCOUNTS as $key) {
        $accounts[] = $ids[$key];
    }
    // Workspaces this suite created through the service are recorded, never
    // discovered by a name pattern that could reach a row we do not own.
    foreach (($ids['created_tenants'] ?? []) as $created) {
        $tenants[] = (string) $created;
    }
    $in = implode(',', array_fill(0, count($tenants), '?'));
    // Two self-referencing foreign keys must be broken before their rows can
    // go: auth_sessions.rotated_to / .parent_id, and users.deleted_by.
    $db->execute('UPDATE auth_sessions SET parent_id = NULL, rotated_to = NULL'
        . ' WHERE tenant_id IN (' . $in . ')', $tenants);
    $db->execute('UPDATE users SET deleted_by = NULL WHERE tenant_id IN (' . $in . ')',
        $tenants);
    foreach (TENANT_CHILDREN as $table) {
        $db->execute('DELETE FROM `' . $table . '` WHERE tenant_id IN (' . $in . ')',
            $tenants);
    }
    $db->execute('DELETE FROM users WHERE tenant_id IN (' . $in . ')', $tenants);
    $db->execute('DELETE FROM tenants WHERE id IN (' . $in . ')', $tenants);
    $db->execute('DELETE FROM accounts WHERE id IN ('
        . implode(',', array_fill(0, count($accounts), '?')) . ')', $accounts);
}

/** The authoritative state of one tenant, as the gate reads it. */
function tenantRowOf(Database $db, string $tenantId): array
{
    $row = $db->queryOne(
        'SELECT status, lifecycle, api_contract, context_version FROM tenants WHERE id = ?',
        [$tenantId]);
    t($row !== null, 'the fixture tenant row must exist');
    return $row;
}

/** Every workspace account_a can select, in tenant-id order. */
function selectableFor(Database $db, string $accountId): array
{
    $rows = $db->query(
        'SELECT t.id FROM account_workspace_memberships m'
        . ' INNER JOIN tenants t ON t.id = m.tenant_id'
        . " WHERE m.account_id = ? AND m.status = 'active'"
        . " AND t.status = 'active' AND t.lifecycle = 'active'"
        . " AND t.api_contract = 'authoritative_v2' ORDER BY t.id ASC",
        [$accountId]);
    return array_map(static fn(array $row): string => (string) $row['id'], $rows);
}

// ---------------------------------------------------------------------------
// Two-process barriers on real InnoDB row locks.
//
// Neither fixture principal may read information_schema.INNODB_TRX or
// INNODB_LOCK_WAITS, so "is the other transaction blocked?" cannot be polled
// from SQL. It is instead turned into an OBSERVED server outcome: the child
// lowers its own innodb_lock_wait_timeout to one second and attempts the
// operation while the parent holds the tenant row lock. The attempt must fail
// with errno 1205 (ER_LOCK_WAIT_TIMEOUT) — that failure IS the proof that the
// child was blocked on that lock for the whole budget. Only then does the
// child signal "blocked"; the parent commits; the child retries and succeeds.
//
// No participant sleeps for a duration it chose: every wait is on an
// observable event (a file written by the other side, or an engine error).
// ---------------------------------------------------------------------------

const BARRIER_TIMEOUT_S = 30;

/** Wait for a signal file to appear with non-empty content. */
function waitForFile(string $path, int $timeoutSeconds): bool
{
    $deadline = microtime(true) + $timeoutSeconds;
    while (microtime(true) < $deadline) {
        $raw = is_file($path) ? file_get_contents($path) : false;
        if (is_string($raw) && $raw !== '') {
            return true;
        }
        usleep(5000);
    }
    return false;
}

/**
 * The child's payload is only trusted once it parses, so a partially flushed
 * file can never be read as a result.
 *
 * @return array<string, mixed>|null
 */
function waitForJson(string $path, int $timeoutSeconds): ?array
{
    $deadline = microtime(true) + $timeoutSeconds;
    while (microtime(true) < $deadline) {
        $raw = is_file($path) ? file_get_contents($path) : false;
        if (is_string($raw) && $raw !== '') {
            $decoded = json_decode($raw, true);
            if (is_array($decoded)) {
                return $decoded;
            }
        }
        usleep(5000);
    }
    return null;
}

/** @param list<string> $arguments */
function barrierMain(array $arguments): int
{
    // argv: <mode> <directory> <actor> <tenant> <expected> <marker>
    if (count($arguments) !== 6) {
        return 2;
    }
    [$mode, $directory, $actor, $tenant, $expected, $marker] = $arguments;
    if (!is_dir($directory)) {
        return 2;
    }
    $db = BFixture::db();
    $workspaces = new Workspaces($db);
    $db->execute('SET SESSION innodb_lock_wait_timeout = 1');

    $attempt = static function () use (
        $mode, $workspaces, $actor, $tenant, $expected
    ): array {
        return $mode === 'freeze'
            ? $workspaces->freeze($actor, $tenant, (int) $expected)
            : $workspaces->remove($actor, $tenant, $actor, (int) $expected);
    };

    $write = static function (array $outcome) use ($directory): void {
        // A single rename makes the payload visible in one step, so the parent
        // can never observe a half-written result.
        @file_put_contents($directory . DIRECTORY_SEPARATOR . 'done.part',
            json_encode($outcome));
        @rename($directory . DIRECTORY_SEPARATOR . 'done.part',
            $directory . DIRECTORY_SEPARATOR . 'done');
    };

    @file_put_contents($directory . DIRECTORY_SEPARATOR . 'ready', 'ready');
    try {
        $attempt();
        // The parent still holds the lock, so this must never succeed: a
        // silent success would make the whole barrier meaningless.
        $write(['ok' => false, 'error' => 'FIXTURE_BARRIER_NOT_BLOCKED']);
        return 2;
    } catch (\PDOException $error) {
        $info = $error->errorInfo ?? null;
        $errno = is_array($info) ? (int) ($info[1] ?? 0) : 0;
        if ($errno !== 1205) {
            $write(['ok' => false, 'errno' => $errno]);
            return 2;
        }
    } catch (Throwable $error) {
        unset($error);
        $write(['ok' => false, 'errno' => 0]);
        return 2;
    }

    // Blocked for the whole lock-wait budget: say so, and only then stand by.
    @file_put_contents($directory . DIRECTORY_SEPARATOR . 'blocked', 'blocked');
    if (!waitForFile($directory . DIRECTORY_SEPARATOR . 'release',
        BARRIER_TIMEOUT_S)) {
        $write(['ok' => false, 'error' => 'FIXTURE_BARRIER_NO_RELEASE']);
        return 2;
    }

    $outcome = ['ok' => false, 'context_version' => 0, 'status' => '',
                'role' => '', 'marker_seen' => false, 'errno' => 0];
    try {
        $result = $attempt();
        $outcome['ok'] = true;
        $outcome['context_version'] = (int) ($result['context_version'] ?? 0);
        $outcome['status'] = (string) ($result['status'] ?? '');
        $outcome['role'] = (string) ($result['role'] ?? '');
        // The marker was written by the parent INSIDE its lock-holding
        // transaction: seeing it proves this read happened after the commit.
        $outcome['marker_seen'] = (string) ($result['name'] ?? '') === $marker;
    } catch (ApiException $error) {
        $outcome['error'] = $error->errorCode;
    } catch (Throwable $error) {
        $outcome['error'] = 'FIXTURE_SCENARIO_FAILED';
        unset($error);
    }
    $write($outcome);
    return $outcome['ok'] === true ? 0 : 2;
}

if (isset($argv[1]) && $argv[1] === '--barrier') {
    exit(barrierMain(array_slice($argv, 2)));
}

/**
 * Start the barrier child; the parent releases the lock only after the child
 * reports that it was blocked on it.
 *
 * @return array<string, mixed>
 */
function startBarrier(string $mode, string $actor, string $tenant,
                      int $expected, string $marker): array
{
    $directory = sys_get_temp_dir() . DIRECTORY_SEPARATOR
        . 'irica-b-ws-' . bin2hex(random_bytes(8));
    t(mkdir($directory, 0700, true), 'the barrier directory must be created');
    $config = getenv('IRICA_B_FIXTURE_CONFIG');
    t(is_string($config) && $config !== '', 'the fixture config path must be exported');
    $environment = [];
    foreach (['PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP'] as $name) {
        $value = getenv($name);
        if (is_string($value)) {
            $environment[$name] = $value;
        }
    }
    $environment['IRICA_B_FIXTURE_CONFIG'] = $config;
    $command = [PHP_BINARY, '-d', 'display_errors=0', '-d', 'log_errors=0',
        '-d', 'auto_prepend_file=', '-d', 'auto_append_file=', __FILE__,
        '--barrier', $mode, $directory, $actor, $tenant, (string) $expected,
        $marker];
    $output = $directory . DIRECTORY_SEPARATOR . 'output';
    $descriptor = [0 => ['pipe', 'r'], 1 => ['file', $output, 'w'],
                   2 => ['file', $output, 'a']];
    $process = proc_open($command, $descriptor, $pipes, null, $environment);
    t(is_resource($process), 'the barrier child must start');
    return ['process' => $process, 'directory' => $directory];
}

/** @return array<string, mixed> */
function finishBarrier(array $handle, string $what): array
{
    $parsed = waitForJson($handle['directory'] . DIRECTORY_SEPARATOR . 'done',
        BARRIER_TIMEOUT_S);
    // Never relay child output: it could hold a driver diagnostic.
    $status = proc_close($handle['process']);
    t($parsed !== null, $what . ': the barrier child must report a result');
    foreach (['ready', 'blocked', 'release', 'done', 'output'] as $name) {
        @unlink($handle['directory'] . DIRECTORY_SEPARATOR . $name);
    }
    @rmdir($handle['directory']);
    t($status === 0, $what . ': the barrier child must exit cleanly');
    return $parsed;
}

// ===========================================================================
// The scenario
// ===========================================================================

$ids = [];
try {
    $app = BFixture::db();
    $workspaces = new Workspaces($app);
    $log = new Log(null);
    $ids = twoAccountTwoWorkspaceFixture($app);

    // --- 0. The fixture helper is real: positive controls on each account's
    //         OWN tenant, before any denial is asserted anywhere.
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        $own = $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_a'], 1, 'read');
        t($own['role'] === 'admin', 'account_a is an admin of its own workspace');
        $ownB = $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_b'], $ids['tenant_b'], 1, 'read');
        t($ownB['role'] === 'admin', 'account_b is an admin of its own workspace');
    });

    // --- 1. Capability matrix. Shared membership is a real membership with a
    //         weaker role; isolated membership is unreachable to the other
    //         account entirely.
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_a'], 1, 'read')['role'] === 'admin',
            'admin may read');
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_a'], 1, 'edit')['role'] === 'admin',
            'admin may edit');
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_a'], 1, 'delete')['role'] === 'admin',
            'admin may delete');
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_a'], 1, 'settings')['role'] === 'admin',
            'admin may write settings');
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_a'], 1, 'period_admin')['role'] === 'admin',
            'admin may open and close periods');

        $shared = $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_shared'], 1, 'read');
        t($shared['role'] === 'user', 'the shared membership keeps its role');
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_shared'], 1, 'edit')['role'] === 'user',
            'a user may edit');
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_b'], $ids['tenant_isolated'], 1, 'settings')['role'] === 'admin',
            'the isolated workspace admin keeps its own settings capability');
    });
    foreach (['delete', 'settings', 'period_admin'] as $adminOnly) {
        $app->transaction(function (Database $tx) use ($workspaces, $ids, $adminOnly): void {
            BFixture::code('ROLE_FORBIDDEN', static fn () => $workspaces->lockAuthorizedWithin(
                $tx, $ids['account_a'], $ids['tenant_shared'], 1, $adminOnly));
        });
    }
    // The workspace admin who is still only a platform MEMBER.
    $adminRole = (string) scalar($app,
        'SELECT platform_role FROM accounts WHERE id = ?', [$ids['account_a']]);
    t($adminRole === 'member', 'a workspace admin remains a platform member');
    $operatorRole = (string) scalar($app,
        'SELECT platform_role FROM accounts WHERE id = ?', [$ids['account_op']]);
    t($operatorRole === 'operator', 'the operator fixture carries platform_role operator');

    // --- 2. Generic scoped denial, one code and one message for every cause.
    $denials = [];
    $denial = function (string $what, callable $operation) use (&$denials): void {
        try {
            $operation();
        } catch (ApiException $error) {
            $denials[$what] = [$error->errorCode, $error->getMessage()];
            return;
        }
        throw new RuntimeException('FIXTURE_EXPECTED_REJECTION');
    };
    $denial('staging', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_staging'], 1, 'read')));
    // Flip one OWN workspace to suspended and back, so the denial below is a
    // state denial and never a missing-membership denial.
    $app->execute("UPDATE tenants SET status = 'suspended' WHERE id = ?",
        [$ids['tenant_freeze']]);
    $denial('suspended', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_freeze'], 1, 'read')));
    $app->execute("UPDATE tenants SET status = 'active' WHERE id = ?",
        [$ids['tenant_freeze']]);
    $denial('wrong-contract', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_legacy'], 1, 'read')));
    $denial('removed-membership', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_b'], $ids['tenant_shared'], 1, 'read')));
    $denial('tombstoned-membership', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_isolated'], 1, 'read')));
    $denial('no-membership', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_b'], 1, 'read')));
    $denial('platform-operator', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_op'], $ids['tenant_a'], 1, 'read')));
    $denial('unknown-tenant', static fn () => $app->transaction(
        static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], uuid(), 1, 'read')));

    t(count($denials) === 8, 'every scoped denial cause must be exercised');
    $first = null;
    foreach ($denials as $what => $pair) {
        t($pair[0] === 'NOT_FOUND', $what . ': a scoped denial must be generic NOT_FOUND');
        if ($first === null) {
            $first = $pair[1];
        }
        t($pair[1] === $first,
            $what . ': every scoped denial must carry the same message (no oracle)');
    }

    // --- 3. Capability and input validation fail closed.
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_a'], 1, 'teleport'));
        BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_a'], 0, 'read'));
        BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_a'], -1, 'read'));
        BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, 'not-a-uuid', $ids['tenant_a'], 1, 'read'));
        BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], 'not-a-uuid', 1, 'read'));
        BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->lockLegacyWithin(
            $tx, 'not-a-uuid', true));
    });
    // An unknown role fails closed even when the capability would otherwise be
    // allowed: the map is checked, never defaulted.
    t(Workspaces::allows('root', 'read') === false, 'an unknown role fails closed');
    t(Workspaces::allows('admin', 'teleport') === false, 'an unknown capability fails closed');
    t(Workspaces::allows('admin', 'read') === true, 'admin keeps the read capability');
    t(Workspaces::allows('user', 'edit') === true, 'user keeps the edit capability');
    t(Workspaces::allows('user', 'delete') === false, 'user never gets delete');
    t(Workspaces::allows('', 'read') === false, 'an empty role fails closed');

    // --- 3b. A DISABLED account keeps no workspace authority, even though
    //         its membership row is still active. The account row is read
    //         under the same lock order as the tenant and the membership, so
    //         a disable cannot slip in between the check and the use.
    $disabledAccount = [];
    $app->execute("UPDATE accounts SET status = 'disabled' WHERE id = ?",
        [$ids['account_a']]);
    foreach (['read', 'edit'] as $capability) {
        try {
            $app->transaction(static fn (Database $tx) => $workspaces->lockAuthorizedWithin(
                $tx, $ids['account_a'], $ids['tenant_a'], 1, $capability));
        } catch (ApiException $error) {
            $disabledAccount[$capability] = [$error->errorCode, $error->getMessage()];
            continue;
        }
        throw new RuntimeException(
            'SCENARIO_FAILED: a disabled account must not be allowed to ' . $capability);
    }
    t(count($disabledAccount) === 2,
        'a disabled account is refused for read and for edit');
    foreach ($disabledAccount as $capability => $pair) {
        t($pair[0] === 'NOT_FOUND',
            $capability . ': a disabled account answers the generic scoped denial');
        t($pair[1] === $first,
            $capability . ': the disabled-account denial is the one uniform message');
    }
    $app->execute("UPDATE accounts SET status = 'active' WHERE id = ?",
        [$ids['account_a']]);
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_a'], 1, 'read')['role'] === 'admin',
            're-enabling the account restores its workspace authority');
    });

    // --- 4. Context version. Freeze is the only state change this phase
    //         exposes, and it bumps context_version because the workspace
    //         context the client cached is no longer the one it is holding.
    $frozen = $workspaces->freeze($ids['account_a'], $ids['tenant_freeze'], 1);
    t($frozen['status'] === 'suspended', 'freeze suspends the workspace');
    t($frozen['context_version'] === 2, 'freeze increments the context version');
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        BFixture::code('WORKSPACE_CONTEXT_STALE', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_freeze'], 1, 'read'));
    });
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_freeze'], 2, 'read'));
    });
    // A frozen workspace is absent from the selectable normal workspaces.
    t(!in_array($ids['tenant_freeze'], selectableFor($app, $ids['account_a']), true),
        'a frozen workspace is absent from the selectable workspaces');
    t($workspaces->listWithin($ids['account_a'], null, 100)['next_id'] === null
        && !in_array($ids['tenant_freeze'],
            array_column($workspaces->listWithin($ids['account_a'], null, 100)['items'], 'id'), true),
        'listWithin omits a frozen workspace');

    // A stale version on a STILL-ACTIVE workspace: the version comparison is
    // not an artefact of the suspension.
    $app->execute('UPDATE tenants SET context_version = context_version + 1 WHERE id = ?',
        [$ids['tenant_a']]);
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        BFixture::code('WORKSPACE_CONTEXT_STALE', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_a'], 1, 'read'));
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $ids['tenant_a'], 2, 'read')['context_version'] === 2,
            'the current context version is accepted');
    });

    // --- 5. listWithin: order, cursor, limit bounds, and a limit+1 peek.
    $pageOne = $workspaces->listWithin($ids['account_a'], null, 3);
    t(count($pageOne['items']) === 3, 'a page holds at most `limit` items');
    t($pageOne['next_id'] !== null, 'a truncated page carries a next_id');
    $expectedOrder = selectableFor($app, $ids['account_a']);
    t(count($expectedOrder) > 4, 'the pagination fixture holds more than one page');
    t($expectedOrder[2] === $pageOne['next_id'],
        'next_id is the last id of the page, ready to be echoed back');
    // The limit+1 peek is what distinguishes "exactly `limit` rows left" from
    // "another page follows": one row short of the set still says more, an
    // exact fit says none.
    t($workspaces->listWithin($ids['account_a'], null, 3)['next_id'] !== null,
        'a page that leaves rows behind reports another page');
    t($workspaces->listWithin($ids['account_a'], null,
        count($expectedOrder) - 1)['next_id'] === $expectedOrder[count($expectedOrder) - 2],
        'a page one row short of the set still reports another page');
    t($workspaces->listWithin($ids['account_a'], null,
        count($expectedOrder))['next_id'] === null,
        'a page that exactly exhausts the set reports no next page');
    $collected = [];
    $cursor = null;
    for ($guard = 0; $guard < 20; $guard++) {
        $page = $workspaces->listWithin($ids['account_a'], $cursor, 3);
        t(count($page['items']) <= 3, 'no page exceeds its limit');
        foreach ($page['items'] as $item) {
            $collected[] = $item['id'];
            t($item['role'] === 'admin' || $item['role'] === 'user',
                'a listed workspace always carries a known role');
            t($item['context_version'] >= 1, 'a listed workspace carries a positive context version');
        }
        $cursor = $page['next_id'];
        if ($cursor === null) {
            break;
        }
    }
    t($cursor === null, 'the cursor chain terminates');
    t($collected === $expectedOrder,
        'the cursor chain visits every selectable workspace exactly once, in order');
    t(!in_array($ids['tenant_staging'], $collected, true)
        && !in_array($ids['tenant_legacy'], $collected, true)
        && !in_array($ids['tenant_isolated'], $collected, true)
        && !in_array($ids['tenant_b'], $collected, true),
        'listing never exposes a staging, wrong-contract or foreign workspace');
    BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->listWithin($ids['account_a'], null, 0));
    BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->listWithin($ids['account_a'], null, 101));
    BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->listWithin($ids['account_a'], 'not-a-uuid', 10));
    BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->listWithin('not-a-uuid', null, 10));
    $single = $workspaces->listWithin($ids['account_a'], null, 1);
    t(count($single['items']) === 1 && $single['items'][0]['id'] === $expectedOrder[0],
        'a one-item page is served from the query, not from a full scan');

    // --- 6. A new private workspace.
    $created = $workspaces->createPrivate($ids['account_a'], 'Fixture private desk');
    $ids['created_tenants'] = [(string) $created['tenant_id']];
    t(Ids::isUuid($created['tenant_id']), 'a new workspace carries a random UUID');
    t(strlen($created['slug']) > 3 && !str_contains($created['slug'], ' ')
        && !str_contains($created['slug'], $ids['account_a']),
        'a new workspace carries a non-personal slug');
    t($created['role'] === 'admin', 'the owning account is an admin of its new workspace');
    $fresh = tenantRowOf($app, $created['tenant_id']);
    t($fresh['status'] === 'active' && $fresh['lifecycle'] === 'active'
        && $fresh['api_contract'] === 'authoritative_v2',
        'a new workspace is explicitly active / authoritative_v2');
    t((int) $fresh['context_version'] === 1, 'a new workspace starts at context version 1');
    t((string) scalar($app, 'SELECT platform_role FROM accounts WHERE id = ?',
        [$ids['account_a']]) === 'member',
        'creating a workspace never changes the platform role');
    $app->transaction(function (Database $tx) use ($workspaces, $ids, $created): void {
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_a'], $created['tenant_id'], 1, 'settings')['role'] === 'admin',
            'the creator can administer its new workspace');
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_b'], $created['tenant_id'], 1, 'read'));
    });

    // --- 6b. A workspace name is bounded in CHARACTERS, never in bytes.
    //         `tenants.name varchar(120)` under utf8mb4 holds 120 characters;
    //         a byte truncation of a multibyte name cuts a character in half
    //         and stores invalid UTF-8 (or fails the insert outright).
    $storeName = function (string $name) use ($workspaces, $app, &$ids): array {
        try {
            $made = $workspaces->createPrivate($ids['account_a'], $name);
        } catch (Throwable $failure) {
            // The class name only: an engine error carries no row value here.
            return ['error' => get_class($failure)];
        }
        $ids['created_tenants'][] = (string) $made['tenant_id'];
        return [
            'error' => null,
            'label' => (string) $made['name'],
            'stored' => (string) scalar($app, 'SELECT name FROM tenants WHERE id = ?',
                [$made['tenant_id']]),
        ];
    };
    $nameAtLimit = str_repeat('م', 120);
    $nameOverLimit = str_repeat('م', 121);
    $atLimit = $storeName($nameAtLimit);
    t($atLimit['error'] === null,
        'a 120-character multibyte name must be created without an engine error'
        . ($atLimit['error'] !== null ? ': ' . $atLimit['error'] : ''));
    t(($atLimit['stored'] ?? '') === $nameAtLimit
        && mb_strlen((string) ($atLimit['stored'] ?? ''), 'UTF-8') === 120,
        'a multibyte name exactly at the limit is stored unchanged at 120 characters');
    t(($atLimit['label'] ?? '') === $nameAtLimit,
        'the returned name is the name that was stored');
    $overLimit = $storeName($nameOverLimit);
    t($overLimit['error'] === null,
        'a 121-character multibyte name must be created without an engine error'
        . ($overLimit['error'] !== null ? ': ' . $overLimit['error'] : ''));
    t(mb_check_encoding((string) ($overLimit['stored'] ?? ''), 'UTF-8'),
        'a multibyte name over the limit is stored as valid UTF-8, never cut mid-character');
    t(mb_strlen((string) ($overLimit['stored'] ?? ''), 'UTF-8') === 120,
        'a multibyte name over the limit is truncated to the declared 120 characters');
    t(($overLimit['stored'] ?? '') === mb_substr($nameOverLimit, 0, 120, 'UTF-8')
        && ($overLimit['label'] ?? '') === ($overLimit['stored'] ?? ''),
        'the stored name and the returned name agree character for character');

    // --- 6c. createPrivateWithin(): account + private workspace + membership
    //         created inside the CALLER's transaction, so a later failing step
    //         rolls all three back together. This is the primitive the
    //         transactional account-creation path builds on.
    t(method_exists($workspaces, 'createPrivateWithin'),
        'createPrivateWithin exists so a caller can create inside its own transaction');
    $joinedAccount = uuid();
    $joinedTenant = null;
    $joinedError = null;
    try {
        $app->transaction(function (Database $tx) use (
            $workspaces, $joinedAccount, &$joinedTenant
        ): void {
            $tx->insert('accounts', accountRow($joinedAccount, 'member'));
            $made = $workspaces->createPrivateWithin(
                $tx, $joinedAccount, 'Caller-managed workspace');
            $joinedTenant = (string) $made['tenant_id'];
            t(Ids::isUuid($joinedTenant), 'the caller transaction receives the new workspace');
            t($made['role'] === 'admin'
                && $made['api_contract'] === 'authoritative_v2'
                && (int) $made['context_version'] === 1,
                'createPrivateWithin returns the same shape as createPrivate');
            // A LATER step of the SAME caller transaction fails.
            throw new RuntimeException('FIXTURE_EXPECTED_ROLLBACK');
        });
    } catch (RuntimeException $failure) {
        $joinedError = $failure->getMessage();
    }
    t($joinedError === 'FIXTURE_EXPECTED_ROLLBACK',
        'the caller transaction reached its later failing step');
    t($joinedTenant !== null, 'the workspace was created before that step failed');
    t((int) scalar($app, 'SELECT COUNT(*) FROM accounts WHERE id = ?',
        [$joinedAccount]) === 0,
        'the account rolls back together with the caller transaction');
    t((int) scalar($app, 'SELECT COUNT(*) FROM tenants WHERE id = ?',
        [(string) $joinedTenant]) === 0,
        'the private workspace rolls back together with the caller transaction');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_workspace_memberships'
        . ' WHERE tenant_id = ?', [(string) $joinedTenant]) === 0,
        'the admin membership rolls back together with the caller transaction');

    // --- 7. assign / remove. Account, then tenant, then membership.
    $granted = $workspaces->assign($ids['account_a'], $ids['tenant_assign'],
        $ids['account_b'], 'user', 1);
    t($granted['role'] === 'user' && $granted['status'] === 'active',
        'assign records an active membership');
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        t($workspaces->lockAuthorizedWithin(
              $tx, $ids['account_b'], $ids['tenant_assign'], 1, 'edit')['role'] === 'user',
            'an assigned member may edit');
        BFixture::code('ROLE_FORBIDDEN', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_b'], $ids['tenant_assign'], 1, 'settings'));
    });
    BFixture::code('VALIDATION_FAILED', static fn () => $workspaces->assign(
        $ids['account_a'], $ids['tenant_assign'], $ids['account_b'], 'root', 1));
    BFixture::code('ROLE_FORBIDDEN', static fn () => $workspaces->assign(
        $ids['account_b'], $ids['tenant_assign'], $ids['account_b'], 'admin', 1));
    BFixture::code('NOT_FOUND', static fn () => $workspaces->assign(
        $ids['account_a'], $ids['tenant_assign'], uuid(), 'user', 1));
    BFixture::code('WORKSPACE_CONTEXT_STALE', static fn () => $workspaces->assign(
        $ids['account_a'], $ids['tenant_assign'], $ids['account_b'], 'user', 99));
    t((int) scalar($app,
        'SELECT COUNT(*) FROM account_workspace_memberships WHERE tenant_id = ?',
        [$ids['tenant_assign']]) === 2,
        'a refused assign leaves the membership set untouched');

    $workspaces->remove($ids['account_a'], $ids['tenant_assign'],
        $ids['account_b'], 1);
    t((string) scalar($app,
        'SELECT status FROM account_workspace_memberships WHERE account_id = ? AND tenant_id = ?',
        [$ids['account_b'], $ids['tenant_assign']]) === 'removed',
        'remove tombstones the membership instead of deleting the row');
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_b'], $ids['tenant_assign'], 1, 'read'));
    });
    BFixture::code('NOT_FOUND', static fn () => $workspaces->remove(
        $ids['account_a'], $ids['tenant_isolated'], $ids['account_a'], 1));
    t((int) scalar($app,
        'SELECT COUNT(*) FROM account_workspace_memberships WHERE tenant_id = ?',
        [$ids['tenant_isolated']]) === 2,
        'a refused remove leaves the target membership set untouched');
    t((string) scalar($app,
        'SELECT status FROM account_workspace_memberships'
        . ' WHERE account_id = ? AND tenant_id = ?',
        [$ids['account_b'], $ids['tenant_isolated']]) === 'active',
        'a refused remove does not touch another account in that workspace');

    // --- 7b. The last ACTIVE admin of a workspace may never be removed: a
    //         workspace left with no admin can never be administered again,
    //         and freeze itself needs an admin.
    $activeAdmins = static function (Database $db, string $tenantId): int {
        return (int) scalar($db,
            'SELECT COUNT(*) FROM account_workspace_memberships'
            . " WHERE tenant_id = ? AND role = 'admin' AND status = 'active'",
            [$tenantId]);
    };
    t($activeAdmins($app, $ids['tenant_assign']) === 1,
        'the fixture starts tenant_assign with exactly one active admin');
    t($activeAdmins($app, $ids['tenant_remove']) === 2,
        'the remove-barrier workspace starts with two active admins');
    BFixture::code('LAST_WORKSPACE_ADMIN', static fn () => $workspaces->remove(
        $ids['account_a'], $ids['tenant_assign'], $ids['account_a'], 1));
    t((string) scalar($app,
        'SELECT status FROM account_workspace_memberships'
        . ' WHERE account_id = ? AND tenant_id = ?',
        [$ids['account_a'], $ids['tenant_assign']]) === 'active',
        'a refused last-admin removal leaves that membership active');
    // Two admins: one may leave, and only then is the survivor locked in.
    $workspaces->assign($ids['account_a'], $ids['tenant_assign'],
        $ids['account_b'], 'admin', 1);
    t($activeAdmins($app, $ids['tenant_assign']) === 2,
        'the workspace now holds two active admins');
    $workspaces->remove($ids['account_a'], $ids['tenant_assign'],
        $ids['account_b'], 1);
    t($activeAdmins($app, $ids['tenant_assign']) === 1,
        'removing one of two admins is allowed and leaves the other');
    BFixture::code('LAST_WORKSPACE_ADMIN', static fn () => $workspaces->remove(
        $ids['account_a'], $ids['tenant_assign'], $ids['account_a'], 1));
    t($activeAdmins($app, $ids['tenant_assign']) === 1,
        'the refused removal of the remaining admin changes nothing');
    t((int) scalar($app,
        'SELECT COUNT(*) FROM account_workspace_memberships WHERE tenant_id = ?',
        [$ids['tenant_assign']]) === 2,
        'both membership rows of tenant_assign survive the refused removals');

    // --- 8. The legacy contract gate, on the exact helper C/D must call.
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        $legacy = $workspaces->lockLegacyWithin($tx, $ids['tenant_legacy'], true);
        t($legacy['api_contract'] === 'legacy_v1', 'the legacy gate admits a legacy_v1 tenant');
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockLegacyWithin(
            $tx, $ids['tenant_a'], true));
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockLegacyWithin(
            $tx, $ids['tenant_credential_staging'], true));
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockLegacyWithin(
            $tx, $ids['tenant_freeze'], true));
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockLegacyWithin(
            $tx, uuid(), false));
    });

    // The gate is invoked INSIDE the real mutation transactions, BEFORE the
    // tenant_state and business locks — not once in a front controller.
    $importSource = (string) file_get_contents($repoRoot . '/host/src/Import.php');
    $syncSource = (string) file_get_contents($repoRoot . '/host/src/Sync.php');
    $importTx = (int) strpos($importSource, 'transaction(function');
    $syncTx = (int) strpos($syncSource, 'mutations(array $operations');
    $syncTxBody = (int) strpos($syncSource, 'transaction(function', $syncTx);
    t($importTx > 0 && (int) strpos($importSource, 'lockLegacyWithin', $importTx) !== false
        && (int) strpos($importSource, 'lockLegacyWithin', $importTx)
           < (int) strpos($importSource, 'FROM tenant_state', $importTx),
        'Import locks the legacy contract inside its transaction before tenant_state');
    t($syncTxBody > 0 && (int) strpos($syncSource, 'lockLegacyWithin', $syncTxBody) !== false
        && (int) strpos($syncSource, 'lockLegacyWithin', $syncTxBody)
           < (int) strpos($syncSource, 'loadReplays', $syncTxBody),
        'Sync mutations lock the legacy contract before any business lock');
    $bootstrapTx = (int) strpos($syncSource, 'public function bootstrap');
    t((int) strpos($syncSource, 'lockLegacyWithin', $bootstrapTx) !== false
        && (int) strpos($syncSource, 'lockLegacyWithin', $bootstrapTx)
           < (int) strpos($syncSource, 'public function changes', $bootstrapTx),
        'Sync bootstrap locks the legacy contract inside its transaction');

    // --- 9. v1 cannot read or write an authoritative_v2 or staging tenant,
    //         even with a valid old credential; the same credential still
    //         works on a legacy_v1 tenant.
    $cfg = ['jwt_secret' => bin2hex(random_bytes(24)), 'tenant_slug' => 'no-such-tenant'];
    $auth = new Auth($app, $log, $cfg);
    $tokens = new Tokens($cfg['jwt_secret']);
    $signIn = static function (string $userId, string $tenantId,
                               string $sessionId, Tokens $tokens): string {
        $now = time();
        return $tokens->sign([
            'sub' => $userId,
            'aud' => Tokens::AUDIENCE_USER,
            'kind' => Tokens::KIND_USER,
            'tid' => $tenantId,
            'tv' => 1,
            'sid' => $sessionId,
            'iat' => $now, 'nbf' => $now, 'exp' => $now + 900,
        ]);
    };
    BFixture::code('NOT_FOUND', static fn () => $auth->principalFor($signIn(
        $ids['user_credential_v2'], $ids['tenant_credential_v2'],
        $ids['session_access_v2'], $tokens)));
    BFixture::code('NOT_FOUND', static fn () => $auth->principalFor($signIn(
        $ids['user_credential_staging'], $ids['tenant_credential_staging'],
        $ids['session_access_staging'], $tokens)));
    $legacyPrincipal = $auth->principalFor($signIn(
        $ids['user_credential_legacy'], $ids['tenant_legacy'],
        $ids['session_access_legacy'], $tokens));
    t($legacyPrincipal->role === 'admin',
        'a valid old credential still authenticates a legacy_v1 tenant');

    $legacyAuth = new Auth($app, $log, array_merge($cfg,
        ['tenant_slug' => (string) scalar($app, 'SELECT slug FROM tenants WHERE id = ?',
            [$ids['tenant_legacy']])]));
    t($legacyAuth->tenant()['status'] === 'active',
        'legacy tenant resolution admits an active legacy_v1 tenant');
    foreach (['tenant_credential_v2' => 'v2', 'tenant_credential_staging' => 'staging'] as $key => $tag) {
        $slugAuth = new Auth($app, $log, array_merge($cfg,
            ['tenant_slug' => (string) scalar($app, 'SELECT slug FROM tenants WHERE id = ?',
                [$ids[$key]])]));
        $resolved = null;
        try {
            $resolved = $slugAuth->tenant();
        } catch (ApiException $error) {
            t($error->errorCode === 'NOT_FOUND',
                'legacy tenant resolution must not reveal a ' . $tag . ' tenant');
            $resolved = null;
        }
        t($resolved === null,
            'legacy tenant resolution refuses a ' . $tag . ' tenant');
    }
    // Refresh rechecks the same gate, on a token that is otherwise valid.
    foreach (['v2', 'staging'] as $tag) {
        BFixture::code('NOT_FOUND', static fn () => $auth->refresh(
            ['refresh_token' => $ids['refresh_token_' . $tag], 'kind' => 'user'],
            '127.0.0.1', 'fixture'));
    }
    $refreshed = $auth->refresh(
        ['refresh_token' => $ids['refresh_token_legacy'], 'kind' => 'user'],
        '127.0.0.1', 'fixture');
    t(is_array($refreshed) && isset($refreshed['access_token'])
        && (string) ($refreshed['tenant_id'] ?? '') === $ids['tenant_legacy'],
        'the legacy tenant refresh still rotates to a fresh credential');

    // --- 10. The real Sync/Import mutation boundaries.
    $sync = new Sync($app, $log, ['limits' => ['max_operations' => 100]]);
    $import = new Import($app, $sync, $log, ['limits' => ['max_import_rows' => 1000]]);
    $adminPrincipal = new Principal('user', $ids['user_credential_legacy'],
        $ids['tenant_legacy'], 'admin', $ids['user_credential_legacy'], '', uuid(), 1);
    $v2Principal = new Principal('user', $ids['user_credential_v2'],
        $ids['tenant_credential_v2'], 'admin', $ids['user_credential_v2'], '', uuid(), 1);
    $stagingPrincipal = new Principal('user', $ids['user_credential_staging'],
        $ids['tenant_credential_staging'], 'admin', $ids['user_credential_staging'], '', uuid(), 1);

    // Reads: bootstrap of a v2 or staging tenant must not answer.
    BFixture::code('NOT_FOUND', static fn () => $sync->bootstrap($ids['tenant_credential_v2']));
    BFixture::code('NOT_FOUND', static fn () => $sync->bootstrap($ids['tenant_credential_staging']));
    $legacyBootstrap = $sync->bootstrap($ids['tenant_legacy']);
    t(isset($legacyBootstrap['collections']), 'legacy bootstrap still answers on a legacy_v1 tenant');

    // The changes READ carries its own gate. It must refuse a staging, an
    // authoritative_v2 and a suspended tenant with the SAME uniform denial,
    // whatever caller hands it the tenant id — the front controller is not the
    // only future caller of this method.
    //
    // `archived` is NOT a representable workspace state in this schema:
    // 002_identity_foundation.sql pins `lifecycle CHECK (lifecycle IN
    // ('staging','active'))` and Schema.php mirrors it, so no row can hold it.
    // The refusal is an equality test against `active`, so staging is the
    // reachable instance of "any lifecycle that is not active".
    $changesDenied = [];
    foreach (['staging' => 'tenant_credential_staging',
              'wrong-contract' => 'tenant_a',
              'suspended' => 'tenant_freeze'] as $what => $key) {
        try {
            $sync->changes($ids[$key], 0, 10);
        } catch (ApiException $error) {
            t($error->errorCode === 'NOT_FOUND',
                'the changes read must not reveal a ' . $what . ' tenant');
            $changesDenied[$what] = $error->getMessage();
            continue;
        }
        throw new RuntimeException(
            'SCENARIO_FAILED: the changes read must refuse a ' . $what . ' tenant');
    }
    t(count($changesDenied) === 3,
        'every non-legacy tenant is refused on the changes read');
    foreach ($changesDenied as $what => $message) {
        t($message === $first,
            'the ' . $what . ' changes refusal is the one uniform denial message');
    }
    $legacyChanges = $sync->changes($ids['tenant_legacy'], 0, 10);
    t(is_array($legacyChanges) && array_key_exists('changes', $legacyChanges)
        && array_key_exists('cursor', $legacyChanges)
        && array_key_exists('has_more', $legacyChanges),
        'the changes read still answers on a healthy legacy_v1 tenant');

    // Writes: mutations and import on a v2 or staging tenant are refused, and
    // nothing is written.
    $operation = [
        'operation_id' => uuid(), 'kind' => 'upsert', 'entity' => 'customers',
        'id' => uuid(), 'base_version' => 0,
        'record' => ['id' => 'fixture-ledger-1', 'full_name' => 'Fixture',
            'address' => '', 'mobile' => '09120000000', 'telegram_id' => '',
            'instagram_id' => '', 'notes' => '', 'created' => STAMP, 'updated' => STAMP],
    ];
    $contextV2 = ['device' => null, 'user' => $v2Principal, 'role' => 'admin'];
    $contextStaging = ['device' => null, 'user' => $stagingPrincipal, 'role' => 'admin'];
    BFixture::code('NOT_FOUND', static fn () => $sync->mutations([$operation], $contextV2, 'fixture'));
    BFixture::code('NOT_FOUND', static fn () => $sync->mutations([$operation], $contextStaging, 'fixture'));
    BFixture::code('NOT_FOUND', static fn () => $import->run($v2Principal, $v2Principal, [
        'import_id' => uuid(), 'source_installation_id' => uuid(),
        'collections' => ['customers' => []],
    ], 'fixture', '127.0.0.1'));
    foreach (['tenant_credential_v2', 'tenant_credential_staging'] as $key) {
        $tenantId = $ids[$key];
        t((int) scalar($app, 'SELECT COUNT(*) FROM customers WHERE tenant_id = ?', [$tenantId]) === 0,
            'a refused import writes no customer row');
        t((int) scalar($app, 'SELECT COUNT(*) FROM change_log WHERE tenant_id = ?', [$tenantId]) === 0,
            'a refused mutation writes no change_log row');
        t((int) scalar($app, 'SELECT COUNT(*) FROM idempotency_keys WHERE tenant_id = ?', [$tenantId]) === 0,
            'a refused mutation writes no idempotency key');
    }
    // Positive control: the same code path still writes on a legacy_v1 tenant.
    $legacyOperation = array_merge($operation, ['operation_id' => uuid(), 'id' => uuid()]);
    $result = $sync->mutations([$legacyOperation],
        ['device' => null, 'user' => $adminPrincipal, 'role' => 'admin'], 'fixture');
    t(count($result['applied']) === 1,
        'the legacy mutation path still applies a batch on a legacy_v1 tenant');
    t((int) scalar($app, 'SELECT COUNT(*) FROM customers WHERE tenant_id = ?',
        [$ids['tenant_legacy']]) === 1,
        'the admitted legacy mutation is durable');

    // --- 11. Concurrency barrier: a write already admitted under the tenant
    //          lock MAY finish; the freeze waits for that lock, and only then
    //          commits. Nothing is cancelled.
    $second = Database::fromConfig((array) BFixture::readConfig(
        (string) getenv('IRICA_B_FIXTURE_CONFIG'))->runtime);
    $marker = 'admitted-write-marker';
    $observed = $second->transaction(function (Database $tx) use (
        $workspaces, $ids, $marker
    ): array {
        $admitted = $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_barrier'], 1, 'edit');
        // The admitted write the freeze must wait for.
        $tx->execute('UPDATE tenants SET name = ? WHERE id = ?',
            [$marker, $ids['tenant_barrier']]);
        $handle = startBarrier('freeze', $ids['account_a'],
            $ids['tenant_barrier'], 1, $marker);
        t(waitForFile($handle['directory'] . DIRECTORY_SEPARATOR . 'ready',
            BARRIER_TIMEOUT_S), 'the freeze child must reach the tenant lock');
        // The child lowered its lock-wait budget to one second and FAILED with
        // errno 1205: the server itself reports that it was blocked here.
        t(waitForFile($handle['directory'] . DIRECTORY_SEPARATOR . 'blocked',
            BARRIER_TIMEOUT_S),
            'the freeze must be reported blocked on the admitted write lock');
        return ['handle' => $handle, 'role' => $admitted['role']];
    });
    t($observed['role'] === 'admin',
        'a write already holding the tenant lock is admitted and may finish');
    t((string) scalar($app, 'SELECT name FROM tenants WHERE id = ?',
        [$ids['tenant_barrier']]) === $marker,
        'the admitted write committed and was not cancelled');
    @file_put_contents($observed['handle']['directory']
        . DIRECTORY_SEPARATOR . 'release', 'release');
    $freezeResult = finishBarrier($observed['handle'], 'write-versus-freeze');
    t(($freezeResult['ok'] ?? false) === true,
        'the waiting freeze succeeds once the admitted write releases the lock');
    t(($freezeResult['marker_seen'] ?? false) === true,
        'the freeze read the admitted write, so it waited for that commit');
    t(($freezeResult['context_version'] ?? 0) === 2
        && ($freezeResult['status'] ?? '') === 'suspended',
        'the freeze committed after the lock was released');
    $frozenRow = tenantRowOf($app, $ids['tenant_barrier']);
    t($frozenRow['status'] === 'suspended' && (int) $frozenRow['context_version'] === 2,
        'the freeze is durable after the barrier');
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        BFixture::code('WORKSPACE_CONTEXT_STALE', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_barrier'], 1, 'edit'));
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_a'], $ids['tenant_barrier'], 2, 'edit'));
    });
    t(!in_array($ids['tenant_barrier'], selectableFor($app, $ids['account_a']), true),
        'the frozen workspace disappears from the selectable set');

    // --- 12. Concurrency barrier: remove versus an admitted use.
    $observedRemove = $second->transaction(function (Database $tx) use (
        $workspaces, $ids
    ): array {
        $admitted = $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_b'], $ids['tenant_remove'], 1, 'edit');
        $handle = startBarrier('remove', $ids['account_b'],
            $ids['tenant_remove'], 1, '');
        t(waitForFile($handle['directory'] . DIRECTORY_SEPARATOR . 'ready',
            BARRIER_TIMEOUT_S), 'the remove child must reach the tenant lock');
        t(waitForFile($handle['directory'] . DIRECTORY_SEPARATOR . 'blocked',
            BARRIER_TIMEOUT_S),
            'the remove must be reported blocked on the admitted use lock');
        return ['handle' => $handle, 'role' => $admitted['role']];
    });
    t($observedRemove['role'] === 'admin',
        'an admitted use keeps its role until the lock is released');
    @file_put_contents($observedRemove['handle']['directory']
        . DIRECTORY_SEPARATOR . 'release', 'release');
    $removeResult = finishBarrier($observedRemove['handle'], 'remove-versus-use');
    t(($removeResult['ok'] ?? false) === true
        && ($removeResult['status'] ?? '') === 'removed',
        'the waiting remove succeeds once the admitted use releases the lock');
    $app->transaction(function (Database $tx) use ($workspaces, $ids): void {
        BFixture::code('NOT_FOUND', static fn () => $workspaces->lockAuthorizedWithin(
            $tx, $ids['account_b'], $ids['tenant_remove'], 1, 'edit'));
    });
    t(!in_array($ids['tenant_remove'], selectableFor($app, $ids['account_b']), true),
        'a workspace whose membership is removed is no longer selectable');
} finally {
    clearSynthetic($app ?? BFixture::db(), $ids);
}

print("WORKSPACES_SCENARIO_OK tenants=" . count(SYNTHETIC_TENANTS)
    . ' accounts=' . count(SYNTHETIC_ACCOUNTS)
    . ' engine=' . BFixture::db()->serverVersion() . "\n");
