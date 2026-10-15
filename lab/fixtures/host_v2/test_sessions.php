<?php
/**
 * test_sessions.php — Phase B opaque account-session suite (`--suite sessions`).
 *
 * Runs against the separately provisioned fixture instance only. It never
 * reads application configuration, never touches production, and never prints
 * a credential, a login, a password, a hash, a token or a session identifier:
 * a failure is always one of the fixed FIXTURE_* labels emitted by
 * bootstrap.php, and every assertion message names the RULE, not the data.
 *
 * What this suite proves
 *
 *   1. Positive control FIRST. A desktop login succeeds against a live active
 *      account and yields EXACTLY {token, expires_at, idle_expires_at,
 *      account:{id, display_name, platform_role, must_change_password}}. Every
 *      negative below is only evidence because this one passes first.
 *   2. Token shape. Exactly 64 lowercase hexadecimal characters, validated
 *      BEFORE any lookup. A legacy compact JWS, a bare acting-user value, a
 *      63/65-character hex string, uppercase hex and non-hex all answer
 *      AUTH_REQUIRED without touching a session row.
 *   3. Hash discipline. Only `Tokens::hashOpaque($raw)` is stored; the stored
 *      hash VERIFIES the raw token and DIFFERS from it; no column of
 *      `account_sessions` ever holds the raw token; the raw token appears only
 *      in the successful login response and never in AccountPrincipal, in a
 *      security event, in a login attempt row, in an exception message or in
 *      this suite's own stdout.
 *   4. Generic login failures. Unknown account, disabled account and wrong
 *      password answer with the SAME code AND the SAME message, and that
 *      message carries no login, password or hash. Failed attempts are
 *      recorded in the rate limiter under a `v2:` + SHA-256 namespaced key,
 *      never under the plaintext login.
 *   5. Admission. Locate by token hash, transaction, lock the account THEN the
 *      session, then validate live active status, exact security_version, exact
 *      purpose, revoked_at NULL and BOTH deadlines STRICTLY after now. At
 *      exactly the idle deadline and exactly the absolute deadline the answer
 *      is AUTH_EXPIRED; one second earlier it is admitted.
 *   6. Forced change. A `must_change_password` session is admitted only for
 *      the self-service routes (me / logout / change-password); an ordinary
 *      workspace or business call returns PASSWORD_CHANGE_REQUIRED.
 *   7. withWorkspace runs the SAME admission and then calls
 *      Workspaces::lockAuthorizedWithin INSIDE that transaction. A snapshot
 *      captured in the callback cannot authorize anything after it returns.
 *   8. Idle is never extended: me / workspaces / repeated passive polls leave
 *      `last_activity_at` and `idle_expires_at` byte-identical, and the polls
 *      still fail AUTH_EXPIRED once the idle deadline has passed. The source
 *      of AccountSessions.php is scanned to prove no UPDATE of those columns
 *      exists at all.
 *   9. changePassword verifies the current password, validates and hashes the
 *      new one, rechecks account and session under locks, clears
 *      must_change_password, bumps security_version (thereby invalidating ALL
 *      sessions INCLUDING the caller's) with a BOUNDED per-account sweep, and
 *      returns reauthentication_required = true WITHOUT minting a replacement.
 *  10. Logout revokes exactly the known session and checks purpose: a repeated
 *      known-session logout is an idempotent success, and a wrong-purpose
 *      token cannot touch the other purpose's session.
 *  11. Operator rights come from the LIVE account row, never from the session
 *      purpose: an admin_web session for a member account still reports
 *      member, and a demotion is visible on the very next admission.
 *  12. Two-connection fixtures. A reset committed on a SECOND connection while
 *      an admission is in flight, and a password-change racing a competing
 *      security-version bump on a second connection, both resolve to the
 *      safe answer with no credential ever crossing a process boundary.
 *  13. Login-attempt lock contention is bounded: a held advisory lock returns
 *      RATE_LIMITED with a Retry-After instead of waiting forever, and the
 *      very next attempt succeeds after the lock is released.
 *  14. Lock order is structural, not aspirational: a source scan asserts that
 *      no function of the session service takes a `FOR UPDATE` on
 *      `account_sessions` before the matching `accounts` row.
 *  15. Lock order under real contention. One two-connection fixture holds the
 *      ACCOUNT row and keeps probing the SESSION rows (released through a
 *      SAVEPOINT, account lock kept) while a logout runs on the other: a
 *      logout that took the session first would collide on every probe. A
 *      second fixture races logout against an account-first locker on the
 *      same account; both sides must complete with a defined outcome inside a
 *      bounded lock-wait budget, with no unhandled 1213.
 *
 * Nothing here drops or truncates a table, and nothing re-provisions. Every
 * synthetic row this suite creates is deleted by this suite, in dependency
 * order, in a `finally` block. Direct SQL that clears `must_change_password`
 * on a generated account is confined to this guarded disposable connection and
 * to exactly that account; it is not a production helper.
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
foreach (['Schema', 'Database', 'Http', 'Ids', 'Roles', 'Workspaces',
          'Auth', 'Accounts', 'Tokens', 'RateLimit',
          'AccountSessions'] as $classFile) {
    require_once $repoRoot . '/host/src/' . $classFile . '.php';
}

/**
 * A FIXED server clock, deliberately in the past: `RateLimit::prune()` would
 * otherwise treat every pre-existing row as expired and delete it. Every
 * deadline in this suite is derived from this one value, so no assertion ever
 * depends on wall-clock time, and there is not a single fixed sleep anywhere.
 */
const NOW = 1577836800; // 2020-01-01T00:00:00Z
const IDLE = 1800;
const ABSOLUTE = 28800;

/** The fixed, generic login refusal: identical for every failure mode. */
const GENERIC_LOGIN_MESSAGE = 'login or password is incorrect';

// ---------------------------------------------------------------------------
// Local fixture helpers (ruling 23: the frozen bootstrap cannot be extended).
// ---------------------------------------------------------------------------

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

function uuid(): string
{
    return Ids::v4();
}

/** A synthetic login this suite owns; never a person, never reused blindly. */
function loginFor(string $tag): string
{
    return 'fxs.' . $tag . '.' . substr(str_replace('-', '', uuid()), 0, 16);
}

/** Run `$operation` and return the ApiException code it raised. */
function codeOf(callable $operation, string $what): string
{
    try {
        $operation();
    } catch (ApiException $error) {
        return $error->errorCode;
    }
    throw new RuntimeException('FIXTURE_EXPECTED_REJECTION: ' . $what);
}

/** Same as codeOf(), but also pins the HTTP status the client would see. */
function statusOf(callable $operation, int $expected, string $what): string
{
    try {
        $operation();
    } catch (ApiException $error) {
        t($error->status === $expected,
            $what . ': the refusal must carry the documented HTTP status');
        return $error->errorCode;
    }
    throw new RuntimeException('FIXTURE_EXPECTED_REJECTION: ' . $what);
}

function accepts(callable $operation, string $what): void
{
    try {
        $operation();
    } catch (ApiException $error) {
        throw new RuntimeException('SCENARIO_FAILED: ' . $what
            . ' (refused with ' . $error->errorCode . ')');
    }
}

/** The message an ApiException carried — asserted for leakage, never printed. */
function messageOf(callable $operation, string $what): string
{
    try {
        $operation();
    } catch (ApiException $error) {
        return $error->getMessage();
    }
    throw new RuntimeException('FIXTURE_EXPECTED_REJECTION: ' . $what);
}

/**
 * A SECOND connection to the same fixture schema, opened only after
 * BFixture::db() has already validated the target. Used for the two-connection
 * fixtures; it holds an open transaction the service connection must wait on.
 */
function openConnection(): Database
{
    $path = getenv('IRICA_B_FIXTURE_CONFIG');
    t(is_string($path) && $path !== '', 'the fixture config path must be exported');
    $config = BFixture::readConfig($path);
    $db = Database::fromConfig((array) $config->runtime);
    $expected = 'irica_b_test_' . $config->run_id;
    t((string) $db->queryValue('SELECT DATABASE()') === $expected,
        'an extra connection must target the fixture schema');
    return $db;
}

/** One account, created through the service, recorded for FK-safe cleanup. */
function makeAccount(Accounts $accounts, string $login, string $password,
                     string $platformRole, array &$ids): array
{
    $made = $accounts->create($login, $password, null, $platformRole, NOW);
    $ids['accounts'][] = (string) $made['account_id'];
    $ids['workspaces'][] = (string) $made['workspace_id'];
    return $made;
}

/** Drop the forced-change flag on exactly this generated account. */
function clearMustChange(Database $db, string $accountId): void
{
    $db->execute('UPDATE accounts SET must_change_password = 0 WHERE id = :id',
        ['id' => $accountId]);
}

/** Delete every synthetic row this suite owns, in foreign-key-safe order. */
function clearSynthetic(Database $db, array $ids): void
{
    $accounts = array_values(array_unique(array_filter($ids['accounts'] ?? [])));
    $tenants = array_values(array_unique(array_filter(array_merge(
        $ids['tenants'] ?? [], $ids['workspaces'] ?? []))));
    if ($accounts !== []) {
        $in = implode(',', array_fill(0, count($accounts), '?'));
        $db->execute('DELETE FROM account_sessions WHERE account_id IN (' . $in . ')',
            $accounts);
        $db->execute('DELETE FROM legacy_account_map WHERE account_id IN (' . $in . ')',
            $accounts);
        $db->execute(
            'DELETE FROM account_security_events WHERE subject_account_id IN (' . $in . ')'
            . ' OR actor_account_id IN (' . $in . ')',
            array_merge($accounts, $accounts));
        $db->execute(
            'DELETE FROM account_workspace_memberships WHERE account_id IN (' . $in . ')',
            $accounts);
    }
    if ($tenants !== []) {
        $in = implode(',', array_fill(0, count($tenants), '?'));
        $db->execute('DELETE FROM legacy_account_map WHERE tenant_id IN (' . $in . ')',
            $tenants);
        $db->execute('DELETE FROM account_security_events WHERE tenant_id IN (' . $in . ')',
            $tenants);
        $db->execute(
            'DELETE FROM account_workspace_memberships WHERE tenant_id IN (' . $in . ')',
            $tenants);
        $db->execute('DELETE FROM memberships WHERE tenant_id IN (' . $in . ')', $tenants);
        $db->execute('DELETE FROM users WHERE tenant_id IN (' . $in . ')', $tenants);
        $db->execute('DELETE FROM tenants WHERE id IN (' . $in . ')', $tenants);
    }
    // The rate limiter is keyed by `v2:` + SHA-256 of the canonical login,
    // never by the account id: drop exactly the rows this suite's fixed clock
    // wrote and leave every other row in the shared table alone.
    $db->execute('DELETE FROM login_attempts WHERE account LIKE ?'
        . ' AND attempted_at = ?', ['v2:%', gmdate('Y-m-d H:i:s', NOW)]);
    if ($accounts !== []) {
        $in = implode(',', array_fill(0, count($accounts), '?'));
        $db->execute('DELETE FROM accounts WHERE id IN (' . $in . ')', $accounts);
    }
}

/**
 * The login_attempts rows this suite wrote, keyed by the namespaced `v2:` +
 * SHA-256 form. Used to prove the plaintext login is never the rate-limit key.
 *
 * @return list<array<string, mixed>>
 */
function attemptsFor(Database $db, string $canonicalLogin): array
{
    return $db->query(
        'SELECT account, ip, outcome FROM login_attempts WHERE account = ?',
        ['v2:' . hash('sha256', $canonicalLogin)]);
}

// ---------------------------------------------------------------------------
// The child process.
//
// argv: --child-hold <directory>. The competing security-version bump runs on
// the child's OWN connection, holds the account row lock across an explicit
// handshake, and generates its own password internally — no credential, login,
// token or hash is ever placed on argv, in stdout or in a file. The parent
// observes only fixed labels and an integer version number.
// ---------------------------------------------------------------------------

function childHold(string $directory): int
{
    $accountId = trim((string) file_get_contents($directory . DIRECTORY_SEPARATOR . 'account'));
    if (Ids::isUuid($accountId) !== true) {
        return 2;
    }
    $outcome = ['outcome' => 'FIXTURE_SCENARIO_FAILED', 'version' => 0];
    $db = BFixture::db();
    try {
        $db->pdo()->beginTransaction();
        $locked = $db->queryOne(
            'SELECT id, security_version FROM accounts WHERE id = ? FOR UPDATE',
            [$accountId]);
        if ($locked === null) {
            $db->pdo()->rollBack();
            return 2;
        }
        $version = (int) $locked['security_version'] + 1;
        $stamp = gmdate('Y-m-d H:i:s', NOW);
        $db->execute(
            'UPDATE accounts SET password_hash = ?, must_change_password = 1,'
            . ' security_version = ?, updated_at = ? WHERE id = ?',
            [Auth::hashPassword(BFixture::password()), $version, $stamp, $accountId]);
        $db->execute(
            'UPDATE account_sessions SET revoked_at = ? WHERE account_id = ?'
            . ' AND revoked_at IS NULL', [$stamp, $accountId]);
        // Lock held, bump applied but NOT committed: signal, then wait for the
        // parent to start its own operation before releasing.
        @file_put_contents($directory . DIRECTORY_SEPARATOR . 'ready', 'ready');
        $deadline = microtime(true) + 20.0;
        $go = false;
        while (microtime(true) < $deadline) {
            if (is_file($directory . DIRECTORY_SEPARATOR . 'go')) {
                $go = true;
                break;
            }
            usleep(5000);
        }
        if (!$go) {
            $db->pdo()->rollBack();
            return 2;
        }
        $db->pdo()->commit();
        $outcome = ['outcome' => 'committed', 'version' => $version];
    } catch (Throwable $error) {
        unset($error);
        if ($db->inTransaction()) {
            try {
                $db->pdo()->rollBack();
            } catch (Throwable $rollback) {
                unset($rollback);
            }
        }
        $outcome = ['outcome' => 'FIXTURE_SCENARIO_FAILED', 'version' => 0];
    }
    @file_put_contents($directory . DIRECTORY_SEPARATOR . 'done.part',
        json_encode($outcome));
    @rename($directory . DIRECTORY_SEPARATOR . 'done.part',
        $directory . DIRECTORY_SEPARATOR . 'done');
    return $outcome['outcome'] === 'committed' ? 0 : 1;
}

/**
 * argv: --child-order-probe <directory>.
 *
 * Holds the ACCOUNT row lock — the lock admission and changePassword take
 * FIRST — and then repeatedly probes the SESSION rows of that same account
 * under a one-second lock-wait timeout, releasing each probe through a
 * SAVEPOINT so the account lock is never given up.
 *
 * If the racing logout had locked the session BEFORE the account, every probe
 * would collide with it and the child would report `contention = 1`. If the
 * module keeps the documented account → session order, the logout cannot hold
 * a session row while the child holds the account, so every probe succeeds and
 * the child reports `contention = 0` when its bounded budget is spent.
 *
 * The lock-graph tables (information_schema.INNODB_LOCK_WAITS and friends)
 * are not readable by the fixture principal, so the order is proven by
 * contention instead. Only an account UUID arrives; only labels and integers
 * leave.
 */
function childOrderProbe(string $directory): int
{
    $accountId = trim((string) file_get_contents($directory . DIRECTORY_SEPARATOR . 'account'));
    if (Ids::isUuid($accountId) !== true) {
        return 2;
    }
    $outcome = ['outcome' => 'FIXTURE_SCENARIO_FAILED', 'probes' => 0,
        'contention' => 0];
    $db = BFixture::db();
    try {
        $db->execute('SET SESSION innodb_lock_wait_timeout = 1');
        $db->pdo()->beginTransaction();
        $locked = $db->queryOne(
            'SELECT id FROM accounts WHERE id = ? FOR UPDATE', [$accountId]);
        if ($locked === null) {
            $db->pdo()->rollBack();
            return 2;
        }
        $db->execute('SAVEPOINT irica_gate');
        @file_put_contents($directory . DIRECTORY_SEPARATOR . 'ready', 'ready');
        $deadline = microtime(true) + 3.0;
        $probes = 0;
        $contention = 0;
        while (microtime(true) < $deadline) {
            if (!is_file($directory . DIRECTORY_SEPARATOR . 'go')) {
                usleep(2000);
                continue;
            }
            $probes++;
            try {
                // A column outside the (account_id) secondary index, so
                // FOR UPDATE must lock the clustered session row.
                $db->query('SELECT purpose FROM account_sessions'
                    . ' WHERE account_id = ? FOR UPDATE', [$accountId]);
                $db->execute('ROLLBACK TO SAVEPOINT irica_gate');
                $db->execute('SAVEPOINT irica_gate');
            } catch (Throwable $wait) {
                unset($wait);
                $contention = 1;
                break;
            }
            usleep(2000);
        }
        $outcome = ['outcome' => 'probed', 'probes' => $probes,
            'contention' => $contention];
    } catch (Throwable $error) {
        unset($error);
        $outcome = ['outcome' => 'FIXTURE_SCENARIO_FAILED', 'probes' => 0,
            'contention' => 0];
    }
    if ($db->inTransaction()) {
        try {
            $db->pdo()->rollBack();
        } catch (Throwable $rollback) {
            unset($rollback);
        }
    }
    @file_put_contents($directory . DIRECTORY_SEPARATOR . 'done.part',
        json_encode($outcome));
    @rename($directory . DIRECTORY_SEPARATOR . 'done.part',
        $directory . DIRECTORY_SEPARATOR . 'done');
    return $outcome['outcome'] === 'probed' ? 0 : 1;
}

/**
 * argv: --child-account-gate <directory>.
 *
 * Holds the ACCOUNT row lock — the lock admission and changePassword take
 * FIRST — then, on the `go` handshake, takes the session row lock of the same
 * account, which is exactly the account → session order the module documents.
 * A logout that races it must simply queue; the two orders must never form a
 * cycle. Reports whether the second lock was taken and the transaction
 * committed, or whether the engine refused.
 */
function childAccountGate(string $directory): int
{
    $accountId = trim((string) file_get_contents($directory . DIRECTORY_SEPARATOR . 'account'));
    if (Ids::isUuid($accountId) !== true) {
        return 2;
    }
    $outcome = ['outcome' => 'FIXTURE_SCENARIO_FAILED', 'sessions_locked' => 0];
    $db = BFixture::db();
    try {
        $db->pdo()->beginTransaction();
        $locked = $db->queryOne(
            'SELECT id FROM accounts WHERE id = ? FOR UPDATE', [$accountId]);
        if ($locked === null) {
            $db->pdo()->rollBack();
            return 2;
        }
        @file_put_contents($directory . DIRECTORY_SEPARATOR . 'ready', 'ready');
        $deadline = microtime(true) + 20.0;
        $go = false;
        while (microtime(true) < $deadline) {
            if (is_file($directory . DIRECTORY_SEPARATOR . 'go')) {
                $go = true;
                break;
            }
            usleep(2000);
        }
        if (!$go) {
            $db->pdo()->rollBack();
            return 2;
        }
        $sessions = $db->query(
            'SELECT purpose FROM account_sessions WHERE account_id = ? FOR UPDATE',
            [$accountId]);
        $db->pdo()->commit();
        $outcome = ['outcome' => 'committed', 'sessions_locked' => count($sessions)];
    } catch (Throwable $error) {
        unset($error);
        if ($db->inTransaction()) {
            try {
                $db->pdo()->rollBack();
            } catch (Throwable $rollback) {
                unset($rollback);
            }
        }
        $outcome = ['outcome' => 'FIXTURE_SCENARIO_FAILED', 'sessions_locked' => 0];
    }
    @file_put_contents($directory . DIRECTORY_SEPARATOR . 'done.part',
        json_encode($outcome));
    @rename($directory . DIRECTORY_SEPARATOR . 'done.part',
        $directory . DIRECTORY_SEPARATOR . 'done');
    return $outcome['outcome'] === 'committed' ? 0 : 1;
}

if (isset($argv[1]) && $argv[1] === '--child-hold') {
    exit(childHold((string) ($argv[2] ?? '')));
}
if (isset($argv[1]) && $argv[1] === '--child-order-probe') {
    exit(childOrderProbe((string) ($argv[2] ?? '')));
}
if (isset($argv[1]) && $argv[1] === '--child-account-gate') {
    exit(childAccountGate((string) ($argv[2] ?? '')));
}

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

/** @return array<string, mixed>|null */
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

/** Start a competing child; only an account UUID travels, never a secret. */
function startChild(string $accountId, string $mode = 'hold'): array
{
    t(in_array($mode, ['hold', 'order-probe', 'account-gate'], true),
        'the child mode must be one this suite knows');
    $directory = sys_get_temp_dir() . DIRECTORY_SEPARATOR
        . 'irica-b-sessions-' . bin2hex(random_bytes(8));
    t(mkdir($directory, 0700, true), 'the child directory must be created');
    @file_put_contents($directory . DIRECTORY_SEPARATOR . 'account', $accountId);
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
        '--child-' . $mode, $directory];
    $output = $directory . DIRECTORY_SEPARATOR . 'output';
    $descriptor = [0 => ['pipe', 'r'], 1 => ['file', $output, 'w'],
                   2 => ['file', $output, 'a']];
    $process = proc_open($command, $descriptor, $pipes, null, $environment);
    t(is_resource($process), 'the child must start');
    return ['process' => $process, 'directory' => $directory];
}

/** @return array<string, mixed> */
function finishChild(array $handle): array
{
    $directory = $handle['directory'];
    $ready = waitForFile($directory . DIRECTORY_SEPARATOR . 'ready', 30);
    $result = $ready
        ? waitForJson($directory . DIRECTORY_SEPARATOR . 'done', 30) : null;
    proc_close($handle['process']);
    if (is_dir($directory)) {
        foreach (['account', 'ready', 'go', 'done.part', 'done', 'output'] as $name) {
            $path = $directory . DIRECTORY_SEPARATOR . $name;
            if (is_file($path)) {
                @unlink($path);
            }
        }
        @rmdir($directory);
    }
    t($ready, 'the competing connection must reach its handshake');
    t($result !== null, 'the competing connection must report an outcome');
    return $result;
}

// ---------------------------------------------------------------------------
// Structural checks on the implementation source (no runtime needed).
// ---------------------------------------------------------------------------

/** @return array<int, string> */
function sourceLines(string $relativePath): array
{
    $path = BFixture::repoRoot() . '/' . $relativePath;
    t(is_file($path), 'the implementation file must exist');
    $raw = file_get_contents($path);
    t(is_string($raw) && $raw !== '', 'the implementation file must be readable');
    return preg_split('/\R/', $raw) ?: [];
}

/** Strip block and line comments so a commented word cannot satisfy a scan. */
function codeOnly(array $lines): string
{
    $text = implode("\n", $lines);
    $text = (string) preg_replace('~/\*.*?\*/~s', '', $text);
    $text = (string) preg_replace('~/.*~', '', $text);
    return $text;
}

function constantNames(array $lines): array
{
    $names = [];
    foreach ((preg_split('/\R/', codeOnly($lines)) ?: []) as $line) {
        if (preg_match('/\bpublic\s+const\s+([A-Za-z_][A-Za-z0-9_]*)\b/', $line,
                $match) === 1) {
            $names[] = $match[1];
        }
    }
    return $names;
}

/**
 * The lock order each named function of the session service actually issues.
 *
 * Anonymous closures belong to the enclosing named function (the split only
 * matches `function <identifier>(`), so `changePassword`'s transaction body is
 * scanned as part of `changePassword`. Statements are delimited by `;`, so a
 * table mentioned in one statement can never borrow a later statement's
 * `FOR UPDATE`.
 *
 * @return array<string, array{first: ?string, locked: list<string>}>
 *   keyed by function name: `first` is the table whose row is locked first.
 */
function lockOrders(array $lines): array
{
    $code = codeOnly($lines);
    $parts = preg_split('/\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/', $code,
        -1, PREG_SPLIT_DELIM_CAPTURE) ?: [];
    $orders = [];
    for ($i = 1; $i + 1 < count($parts); $i += 2) {
        $name = $parts[$i];
        $body = $parts[$i + 1];
        $at = [];
        foreach (['accounts', 'account_sessions'] as $table) {
            if (preg_match('/\b' . $table . '\b[^;]*\bFOR\s+UPDATE\b/i', $body,
                    $match, PREG_OFFSET_CAPTURE) === 1) {
                $at[$table] = $match[0][1];
            }
        }
        if ($at !== []) {
            asort($at);
            $orders[$name] = [
                'first' => array_key_first($at),
                'locked' => array_keys($at),
            ];
        }
    }
    return $orders;
}

// ===========================================================================
// The scenario.
// ===========================================================================

$ids = ['accounts' => [], 'workspaces' => [], 'tenants' => []];

try {
    $app = BFixture::db();
    $accounts = new Accounts($app);
    $sessions = new AccountSessions($app);
    $workspaces = new Workspaces($app);

    // --- 0. The code this task owns exists, matches the §15.8 grammar and is
    //         declared exactly once. (SESSION_PURPOSE_MISMATCH belongs to a
    //         later task, so it is deliberately neither asserted nor declared.)
    $httpLines = sourceLines('host/src/Http.php');
    $httpNames = constantNames($httpLines);
    t(in_array('PASSWORD_CHANGE_REQUIRED', $httpNames, true),
        'PASSWORD_CHANGE_REQUIRED must be declared in Codes');
    t(count(array_keys($httpNames, 'PASSWORD_CHANGE_REQUIRED', true)) === 1,
        'PASSWORD_CHANGE_REQUIRED must be declared exactly once (a duplicate'
        . ' public const is a PHP fatal)');
    t(Codes::isValid(Codes::PASSWORD_CHANGE_REQUIRED),
        'PASSWORD_CHANGE_REQUIRED must match the spec §15.8 grammar');

    // =======================================================================
    // POSITIVE CONTROL. Everything after this is only evidence because this
    // login succeeds.
    // =======================================================================
    $loginA = loginFor('control');
    $passwordA = BFixture::password();
    $madeA = makeAccount($accounts, $loginA, $passwordA, 'member', $ids);
    $accountA = (string) $madeA['account_id'];
    $workspaceA = (string) $madeA['workspace_id'];
    clearMustChange($app, $accountA);

    $login = $sessions->loginDesktop($loginA, $passwordA, '127.0.0.1', NOW);
    t(isset($login['token'], $login['expires_at'], $login['idle_expires_at'],
        $login['account']),
        'a successful desktop login must return the documented envelope');
    t(array_keys($login) === ['token', 'expires_at', 'idle_expires_at', 'account'],
        'the login response carries exactly the four documented keys');
    $token = (string) $login['token'];
    t(preg_match('/\A[0-9a-f]{64}\z/D', $token) === 1,
        'the issued token must be exactly 64 lowercase hexadecimal characters');
    t(array_keys($login['account'])
        === ['id', 'display_name', 'platform_role', 'must_change_password'],
        'the account block carries exactly the four documented keys');
    t($login['account']['id'] === $accountA,
        'the login response names the account that authenticated');
    t($login['account']['platform_role'] === 'member',
        'the platform role comes from the live account row');
    t($login['account']['must_change_password'] === false,
        'the forced-change flag mirrors the live account row');
    t($login['expires_at'] === gmdate('Y-m-d\TH:i:s+00:00', NOW + ABSOLUTE),
        'the absolute deadline is now + 28800, on the wire form of spec §4');
    t($login['idle_expires_at'] === gmdate('Y-m-d\TH:i:s+00:00', NOW + IDLE),
        'the idle deadline is now + 1800, on the wire form of spec §4');

    // --- 1. Only the hash is stored, and it verifies but is not the plaintext.
    $stored = (string) scalar($app,
        'SELECT token_hash FROM account_sessions WHERE account_id = ?'
        . ' ORDER BY issued_at DESC, id DESC LIMIT 1', [$accountA]);
    t($stored === Tokens::hashOpaque($token),
        'the stored token_hash must be Tokens::hashOpaque of the raw token');
    t($stored !== $token, 'the stored hash must differ from the raw token');
    t(hash_equals($stored, Tokens::hashOpaque($token)),
        'the stored hash must verify the raw token');
    $columns = $app->query('SELECT * FROM account_sessions WHERE account_id = ?',
        [$accountA]);
    t($columns !== [], 'the session row must exist');
    foreach ($columns as $row) {
        foreach ($row as $value) {
            t(!is_string($value) || !str_contains($value, $token),
                'no session column may ever contain the raw token');
        }
    }
    foreach ([$passwordA, $loginA] as $secret) {
        foreach ($columns as $row) {
            foreach ($row as $value) {
                t(!is_string($value) || !str_contains($value, $secret),
                    'no session column may contain a credential or a login');
            }
        }
    }
    $events = $app->query(
        'SELECT * FROM account_security_events WHERE subject_account_id = ?',
        [$accountA]);
    foreach ($events as $row) {
        foreach ($row as $value) {
            t(!is_string($value)
                || (!str_contains($value, $token) && !str_contains($value, $passwordA)
                    && !str_contains($value, $loginA)),
                'a security event must never carry a token, password or login');
        }
    }

    // --- 2. Malformed and legacy credential shapes never reach a lookup.
    $legacyJws = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl';
    foreach ([
        'a legacy compact JWS' => $legacyJws,
        'a bare acting-user value' => 'admin',
        'an empty value' => '',
        'a 63-character hex string' => substr($token, 0, 63),
        'a 65-character hex string' => $token . 'a',
        'uppercase hex' => strtoupper($token),
        'a non-hex 64-character value' => str_repeat('z', 64),
    ] as $what => $candidate) {
        t(statusOf(fn() => $sessions->withAccount($candidate, 'desktop', NOW, false,
            fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
            401, $what) === 'AUTH_REQUIRED',
            'a malformed or legacy credential must answer AUTH_REQUIRED: ' . $what);
        t(codeOf(fn() => $sessions->logout($candidate, 'desktop', NOW),
            'logout with ' . $what) === 'AUTH_REQUIRED',
            'logout must refuse a malformed credential: ' . $what);
    }

    // --- 3. Positive admission, and every negative login is generic.
    $seen = null;
    $accepted = $sessions->withAccount($token, 'desktop', NOW + IDLE - 1, false,
        function (Database $tx, AccountPrincipal $principal) use (&$seen,
            $accountA): string {
            t($tx->inTransaction(), 'admission must run inside its transaction');
            t($principal->accountId === $accountA,
                'the principal must name the authenticated account');
            t($principal->purpose === 'desktop',
                'the principal must carry the session purpose');
            t($principal->mustChangePassword === false,
                'the principal must mirror the live forced-change flag');
            t($principal->platformRole === 'member',
                'the principal must carry the LIVE platform role');
            $seen = $principal;
            return $principal->accountId;
        });
    t($accepted === $accountA, 'admission must run the callback with the account');
    t($seen instanceof AccountPrincipal, 'the callback must receive a principal');
    t(!property_exists($seen, 'token') && !property_exists($seen, 'rawToken'),
        'AccountPrincipal must never expose the raw token');
    foreach (get_object_vars($seen) as $value) {
        t(!is_string($value) || !str_contains($value, $token),
            'AccountPrincipal must never carry the raw token');
    }

    $unknown = loginFor('unknown');
    $badPassword = BFixture::password();
    $messages = [
        'unknown account' => messageOf(
            fn() => $sessions->loginDesktop($unknown, $badPassword, '10.0.0.9', NOW),
            'unknown account'),
        'wrong password' => messageOf(
            fn() => $sessions->loginDesktop($loginA, $badPassword, '10.0.0.9', NOW),
            'wrong password'),
    ];
    t($messages['unknown account'] === GENERIC_LOGIN_MESSAGE,
        'an unknown account must fail with the generic message');
    t($messages['wrong password'] === $messages['unknown account'],
        'a wrong password must be indistinguishable from an unknown account');
    t(codeOf(fn() => $sessions->loginDesktop($unknown, $badPassword, '10.0.0.9', NOW),
        'unknown account') === 'AUTH_REQUIRED',
        'an unknown account must answer AUTH_REQUIRED');
    t(codeOf(fn() => $sessions->loginDesktop($loginA, $badPassword, '10.0.0.9', NOW),
        'wrong password') === 'AUTH_REQUIRED',
        'a wrong password must answer AUTH_REQUIRED');
    foreach ($messages as $what => $message) {
        t(!str_contains($message, $loginA) && !str_contains($message, $badPassword)
            && !str_contains($message, '$2') && !str_contains($message, '$argon'),
            'a generic login failure must leak nothing: ' . $what);
    }

    // The rate-limit key is namespaced: `v2:` + SHA-256 of the canonical login.
    $attempts = attemptsFor($app, $loginA);
    t($attempts !== [], 'a failed attempt must be recorded');
    foreach ($attempts as $row) {
        t(!str_contains((string) $row['account'], $loginA),
            'the rate-limit key must never be the plaintext login');
        t(!str_contains((string) $row['account'], $badPassword),
            'the rate-limit key must never be a password');
        t((string) $row['account'] !== '', 'the rate-limit key must be namespaced');
    }

    // --- 4. A disabled account fails the same generic way, and is refused.
    $loginD = loginFor('disabled');
    $passwordD = BFixture::password();
    $madeD = makeAccount($accounts, $loginD, $passwordD, 'member', $ids);
    $accountD = (string) $madeD['account_id'];
    clearMustChange($app, $accountD);
    $tokenD = (string) $sessions->loginDesktop($loginD, $passwordD,
        '10.0.0.11', NOW)['token'];
    $accounts->setStatus($accountD, 'disabled', NOW);
    t(messageOf(fn() => $sessions->loginDesktop($loginD, $badPassword, '10.0.0.12', NOW),
        'disabled account') === GENERIC_LOGIN_MESSAGE,
        'a disabled account must fail with the generic message');
    t(codeOf(fn() => $sessions->loginDesktop($loginD, $passwordD, '10.0.0.12', NOW),
        'disabled account with the right password') === 'AUTH_REQUIRED',
        'a disabled account must never be admitted, even with the right password');
    t(statusOf(fn() => $sessions->withAccount($tokenD, 'desktop', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'admission for a disabled account') === 'AUTH_REVOKED',
        'admission of a session whose account was disabled must be AUTH_REVOKED');

    // --- 5. Purpose is immutable and must match exactly.
    $adminSession = $app->transaction(function (Database $tx) use ($sessions,
        $accountA, $login): array {
        $account = $tx->queryOne(
            'SELECT id, login, display_name, status, must_change_password,'
            . ' security_version, platform_role FROM accounts WHERE id = ? FOR UPDATE',
            [$accountA]);
        t($account !== null, 'the account row must exist');
        t(($account['status'] ?? '') === 'active', 'the account must be active');
        return $sessions->issueWithin($tx, $account, 'admin_web', NOW);
    });
    $adminToken = (string) $adminSession['token'];
    t(preg_match('/\A[0-9a-f]{64}\z/D', $adminToken) === 1,
        'an admin_web session is issued as the same opaque shape');
    t($adminToken !== $token, 'two issuances must never share a token');
    t(statusOf(fn() => $sessions->withAccount($token, 'admin_web', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'desktop token on an admin_web route') === 'AUTH_REQUIRED',
        'a desktop session must not satisfy an admin_web route');
    t(statusOf(fn() => $sessions->withAccount($adminToken, 'desktop', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'admin_web token on a desktop route') === 'AUTH_REQUIRED',
        'an admin_web session must not satisfy a desktop route');
    t(codeOf(fn() => $sessions->withWorkspace($token, 'admin_web', NOW, $workspaceA,
        1, 'read', fn(Database $tx, AccountPrincipal $principal,
            array $snapshot): array => $snapshot), 'wrong purpose workspace')
        === 'AUTH_REQUIRED',
        'a wrong-purpose token must never reach the workspace gate');
    t(codeOf(fn() => $sessions->logout($adminToken, 'desktop', NOW),
        'logout with the wrong purpose') === 'AUTH_REQUIRED',
        'logout must refuse a wrong-purpose token');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND purpose = ? AND revoked_at IS NULL', [$accountA, 'admin_web']) === 1,
        'a refused wrong-purpose logout must not touch the other purpose');

    // --- 6. Exact deadlines: strictly after now, both of them.
    t($sessions->withAccount($token, 'desktop', NOW + IDLE - 1, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId)
        === $accountA,
        'one second before the idle deadline the session is still valid');
    t(statusOf(fn() => $sessions->withAccount($token, 'desktop', NOW + IDLE, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'exactly at the idle deadline') === 'AUTH_EXPIRED',
        'at exactly the idle deadline the answer is AUTH_EXPIRED (strict >)');
    t(statusOf(fn() => $sessions->withAccount($token, 'desktop',
        NOW + IDLE + 1, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'past the idle deadline') === 'AUTH_EXPIRED',
        'past the idle deadline the answer is AUTH_EXPIRED');

    // The absolute deadline is a HARD ceiling that outlives any idle-window
    // extension: write the idle window forward by hand — exactly what a future
    // authorized human-command route would do — and one second before the
    // absolute deadline the session still admits, while at the deadline itself
    // it is over. Admission itself never moves either clock.
    $tokenHash = Tokens::hashOpaque($token);
    $app->execute('UPDATE account_sessions SET last_activity_at = ?,'
        . ' idle_expires_at = ? WHERE token_hash = ?',
        [gmdate('Y-m-d H:i:s', NOW + ABSOLUTE - 60),
         gmdate('Y-m-d H:i:s', NOW + ABSOLUTE), $tokenHash]);
    t($sessions->withAccount($token, 'desktop', NOW + ABSOLUTE - 1, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId)
        === $accountA,
        'with the idle window extended, the session still admits one second'
        . ' before the absolute deadline');
    t(statusOf(fn() => $sessions->withAccount($token, 'desktop',
        NOW + ABSOLUTE, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'absolute expiry despite activity') === 'AUTH_EXPIRED',
        'activity can never outlive the absolute deadline');
    t(statusOf(fn() => $sessions->withAccount($token, 'desktop',
        NOW + ABSOLUTE + 1, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'past the absolute deadline') === 'AUTH_EXPIRED',
        'past the absolute deadline the answer is AUTH_EXPIRED');
    // Restore the issued idle window so the polling section starts from it.
    $app->execute('UPDATE account_sessions SET last_activity_at = ?,'
        . ' idle_expires_at = ? WHERE token_hash = ?',
        [gmdate('Y-m-d H:i:s', NOW), gmdate('Y-m-d H:i:s', NOW + IDLE),
         $tokenHash]);

    // --- 7. Repeated passive polls never extend the idle window.
    $before = $app->queryOne(
        'SELECT last_activity_at, idle_expires_at, absolute_expires_at'
        . ' FROM account_sessions WHERE token_hash = ?', [Tokens::hashOpaque($token)]);
    t($before !== null, 'the session row must exist');
    for ($poll = 1; $poll <= 5; $poll++) {
        $sessions->withAccount($token, 'desktop', NOW + $poll, false,
            fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId);
    }
    $after = $app->queryOne(
        'SELECT last_activity_at, idle_expires_at, absolute_expires_at'
        . ' FROM account_sessions WHERE token_hash = ?', [Tokens::hashOpaque($token)]);
    t($after === $before,
        'a passive poll must never move last_activity_at or either deadline');
    t(statusOf(fn() => $sessions->withAccount($token, 'desktop', NOW + IDLE, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'polls past the idle deadline') === 'AUTH_EXPIRED',
        'passive polls must not keep an idle-expired session alive');

    // Structural guard: no update of the idle clock exists at all.
    $sessionSource = codeOnly(sourceLines('host/src/AccountSessions.php'));
    t(substr_count($sessionSource, "'last_activity_at'") === 1,
        'last_activity_at may only be written at issuance — never on admission,'
        . ' a workspace call or a heartbeat');
    t(preg_match('/UPDATE\s+account_sessions[^;]*idle_expires_at/is',
        $sessionSource) !== 1,
        'idle_expires_at may only be written at issuance — no UPDATE of the idle'
        . ' clock exists anywhere in the session service');
    t(preg_match('/UPDATE\s+account_sessions[^;]*last_activity_at/is',
        $sessionSource) !== 1,
        'no UPDATE of last_activity_at exists anywhere in the session service');
    t(stripos($sessionSource, 'error_log') === false
        && stripos($sessionSource, 'Log::') === false
        && stripos($sessionSource, 'var_dump') === false
        && stripos($sessionSource, 'print_r') === false,
        'AccountSessions must never log or dump a value');

    // --- 8. withWorkspace: same admission, then the workspace lock in-tx.
    $snapshot = $sessions->withWorkspace($token, 'desktop', NOW, $workspaceA, 1,
        'read', function (Database $tx, AccountPrincipal $principal,
            array $locked) use ($workspaceA): array {
            t($tx->inTransaction(),
                'the workspace lock must be taken inside the same transaction');
            t($locked['tenant_id'] === $workspaceA,
                'the snapshot must name the locked workspace');
            t($locked['role'] === 'admin', 'the snapshot must carry the real role');
            return $locked;
        });
    t($snapshot['context_version'] === 1,
        'the snapshot carries the context version that was locked');
    t(codeOf(fn() => $sessions->withWorkspace($token, 'desktop', NOW, $workspaceA,
        99, 'read', fn(Database $tx, AccountPrincipal $principal,
            array $locked): array => $locked), 'stale workspace context')
        === 'WORKSPACE_CONTEXT_STALE',
        'a stale context version must answer WORKSPACE_CONTEXT_STALE');
    t(codeOf(fn() => $sessions->withWorkspace($token, 'desktop', NOW, uuid(),
        1, 'read', fn(Database $tx, AccountPrincipal $principal,
            array $locked): array => $locked), 'unknown workspace')
        === 'NOT_FOUND',
        'an unknown workspace must answer the generic denial');

    // A snapshot returned from the callback authorizes NOTHING afterwards.
    accepts(fn() => $workspaces->freeze($accountA, $workspaceA,
        (int) $snapshot['context_version']), 'the workspace may be frozen outside');
    t(codeOf(fn() => $sessions->withWorkspace($token, 'desktop', NOW, $workspaceA,
        (int) $snapshot['context_version'], 'read',
        fn(Database $tx, AccountPrincipal $principal,
            array $locked): array => $locked), 'snapshot reused after the callback')
        === 'WORKSPACE_CONTEXT_STALE',
        'a snapshot captured in the callback must not authorize a later call');
    $app->execute('UPDATE tenants SET status = ?, context_version = ? WHERE id = ?',
        ['active', 1, $workspaceA]);

    // --- 9. Removed membership: the same token loses the workspace.
    $loginB = loginFor('guest');
    $passwordB = BFixture::password();
    $madeB = makeAccount($accounts, $loginB, $passwordB, 'member', $ids);
    $accountB = (string) $madeB['account_id'];
    clearMustChange($app, $accountB);
    $tokenB = (string) $sessions->loginDesktop($loginB, $passwordB,
        '127.0.0.1', NOW)['token'];
    $grant = $workspaces->assign($accountA, $workspaceA, $accountB, 'user', 1);
    t(($grant['role'] ?? '') === 'user', 'the membership must be granted');
    t($sessions->withWorkspace($tokenB, 'desktop', NOW, $workspaceA, 1, 'read',
        function (Database $tx, AccountPrincipal $principal,
            array $locked): string {
            return (string) $locked['role'];
        }) === 'user', 'a live membership admits the workspace');
    t(codeOf(fn() => $sessions->withWorkspace($tokenB, 'desktop', NOW, $workspaceA,
        1, 'delete', fn(Database $tx, AccountPrincipal $principal,
            array $locked): array => $locked), 'capability the role lacks')
        === 'ROLE_FORBIDDEN',
        'a capability the role lacks must answer ROLE_FORBIDDEN');
    $workspaces->remove($accountA, $workspaceA, $accountB, 1);
    t(codeOf(fn() => $sessions->withWorkspace($tokenB, 'desktop', NOW, $workspaceA,
        1, 'read', fn(Database $tx, AccountPrincipal $principal,
            array $locked): array => $locked), 'removed membership')
        === 'NOT_FOUND',
        'a removed membership must be refused after admission');

    // --- 10. Operator rights come from the LIVE account row, not the purpose.
    $loginO = loginFor('operator');
    $passwordO = BFixture::password();
    $madeO = makeAccount($accounts, $loginO, $passwordO, 'operator', $ids);
    $accountO = (string) $madeO['account_id'];
    clearMustChange($app, $accountO);
    // A SECOND active operator, so demoting the first is not the removal of the
    // platform's last one (LAST_OPERATOR_REQUIRED protects that).
    $madeO2 = makeAccount($accounts, loginFor('operator.peer'),
        BFixture::password(), 'operator', $ids);
    $accountO2 = (string) $madeO2['account_id'];
    $tokenO = (string) $sessions->loginDesktop($loginO, $passwordO,
        '127.0.0.1', NOW)['token'];
    $adminO = $app->transaction(function (Database $tx) use ($sessions, $accountO): array {
        $account = $tx->queryOne(
            'SELECT id, login, display_name, status, must_change_password,'
            . ' security_version, platform_role FROM accounts WHERE id = ? FOR UPDATE',
            [$accountO]);
        t($account !== null, 'the operator account must exist');
        return $sessions->issueWithin($tx, $account, 'admin_web', NOW);
    });
    t($sessions->withAccount((string) $adminO['token'], 'admin_web', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->platformRole)
        === 'operator', 'an admin_web session reports the live operator role');
    $accounts->setPlatformRole($accountO, 'member', NOW);
    t($sessions->withAccount($tokenO, 'desktop', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->platformRole)
        === 'member',
        'a demotion is visible on the very next admission of the same session');
    t($sessions->withAccount($tokenO, 'desktop', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->platformRole)
        !== 'operator',
        'the session purpose must never grant an operator right');
    t((string) scalar($app, 'SELECT platform_role FROM accounts WHERE id = ?',
        [$accountO2]) === 'operator',
        'demoting one operator must leave the other operator untouched');

    // --- 11. Forced change: only the self-service routes are admitted.
    $loginF = loginFor('forced');
    $passwordF = BFixture::password();
    $madeF = makeAccount($accounts, $loginF, $passwordF, 'member', $ids);
    $accountF = (string) $madeF['account_id'];
    $workspaceF = (string) $madeF['workspace_id'];
    $forced = $sessions->loginDesktop($loginF, $passwordF, '127.0.0.1', NOW);
    $tokenF = (string) $forced['token'];
    t($forced['account']['must_change_password'] === true,
        'a fresh account is created with the forced-change flag set');
    t(statusOf(fn() => $sessions->withAccount($tokenF, 'desktop', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        403, 'ordinary call on a forced-change session')
        === 'PASSWORD_CHANGE_REQUIRED',
        'an ordinary call on a forced-change session must be'
        . ' PASSWORD_CHANGE_REQUIRED');
    t($sessions->withAccount($tokenF, 'desktop', NOW, true,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId)
        === $accountF,
        'the self-service routes (me / logout / change-password) are admitted');
    t(statusOf(fn() => $sessions->withWorkspace($tokenF, 'desktop', NOW, $workspaceF,
        1, 'read', fn(Database $tx, AccountPrincipal $principal,
            array $locked): array => $locked), 403,
        'workspace call on a forced-change session') === 'PASSWORD_CHANGE_REQUIRED',
        'a workspace call on a forced-change session must be'
        . ' PASSWORD_CHANGE_REQUIRED');

    // --- 12. changePassword: verify, bump, revoke everything, mint NOTHING.
    $sessionsBefore = (int) scalar($app,
        'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?', [$accountF]);
    $passwordF2 = BFixture::password();
    t($passwordF2 !== $passwordF, 'the new password must differ');
    $changed = $sessions->changePassword($tokenF, 'desktop', NOW, $passwordF,
        $passwordF2);
    t(($changed['reauthentication_required'] ?? null) === true,
        'changePassword must demand reauthentication');
    t(($changed['token'] ?? null) === null,
        'changePassword must never mint a replacement session');
    $facts = $app->queryOne(
        'SELECT must_change_password, security_version, password_hash FROM accounts'
        . ' WHERE id = ?', [$accountF]);
    t((int) $facts['must_change_password'] === 0,
        'changePassword must clear the forced-change flag');
    t((int) $facts['security_version'] === 2,
        'changePassword must bump security_version exactly once');
    t(password_verify($passwordF2, (string) $facts['password_hash']),
        'the stored hash must verify the NEW password');
    t(!password_verify($passwordF, (string) $facts['password_hash']),
        'the stored hash must no longer verify the OLD password');
    t((string) $facts['password_hash'] !== $passwordF2
        && (string) $facts['password_hash'] !== $passwordF,
        'the stored value must be a hash, never a plaintext password');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?',
        [$accountF]) === $sessionsBefore,
        'changePassword must not add a session row');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND revoked_at IS NULL', [$accountF]) === 0,
        'every session of the account, the caller\'s included, must be revoked');
    t(statusOf(fn() => $sessions->withAccount($tokenF, 'desktop', NOW, true,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'the caller\'s own session after its own password change')
        === 'AUTH_REVOKED',
        'the caller\'s own session must be invalidated by its own change');
    t(codeOf(fn() => $sessions->changePassword($tokenF, 'desktop', NOW, $passwordF2,
        BFixture::password()), 'change with a dead session')
        === 'AUTH_REVOKED',
        'a revoked session may not change a password again');
    t(codeOf(fn() => $sessions->loginDesktop($loginF, $passwordF, '127.0.0.1', NOW),
        'the old password after a change') === 'AUTH_REQUIRED',
        'the old password must stop working');
    t(codeOf(fn() => $sessions->loginDesktop($loginF, BFixture::password(),
        '127.0.0.1', NOW), 'a password the account never had') === 'AUTH_REQUIRED',
        'an unrelated password must be refused');
    $relit = $sessions->loginDesktop($loginF, $passwordF2, '127.0.0.1', NOW);
    t(preg_match('/\A[0-9a-f]{64}\z/D', (string) $relit['token']) === 1,
        'a fresh login with the NEW password succeeds');
    t($relit['account']['must_change_password'] === false,
        'the new session is no longer forced to change its password');
    $tokenF2 = (string) $relit['token'];

    // Wrong current password and a weak new one are both refused, unchanged.
    $wrongCurrent = BFixture::password();
    t(codeOf(fn() => $sessions->changePassword($tokenF2, 'desktop', NOW,
        $wrongCurrent, BFixture::password()), 'wrong current password')
        === 'AUTH_REQUIRED',
        'changePassword must verify the current password');
    t(!password_verify($wrongCurrent, (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$accountF])),
        'a refused change must leave the credential untouched');
    t(codeOf(fn() => $sessions->changePassword($tokenF2, 'desktop', NOW,
        $passwordF2, 'short'), 'weak new password') === 'VALIDATION_FAILED',
        'the new password must satisfy the password policy first');
    t(password_verify($passwordF2, (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$accountF])),
        'a refused change must leave the credential untouched');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND revoked_at IS NULL', [$accountF]) === 1,
        'no refused change may revoke anything');

    // --- 13. Logout: exact session, purpose-checked, idempotent.
    $sessions->logout($tokenF2, 'desktop', NOW);
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND revoked_at IS NULL', [$accountF]) === 0,
        'logout must revoke the exact known session');
    t(statusOf(fn() => $sessions->withAccount($tokenF2, 'desktop', NOW, true,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'admission after logout') === 'AUTH_REVOKED',
        'a logged-out session must not be admitted again');
    $again = $sessions->logout($tokenF2, 'desktop', NOW);
    t(($again['revoked'] ?? null) === true,
        'a repeated known-session logout is a documented idempotent success');
    t(($again['already_revoked'] ?? null) === true,
        'a repeated logout reports that the session was already revoked');
    t(codeOf(fn() => $sessions->logout($token, 'admin_web', NOW),
        'logout with the wrong purpose') === 'AUTH_REQUIRED',
        'a wrong-purpose logout must be refused');
    t($sessions->withAccount($token, 'desktop', NOW + IDLE - 1, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId)
        === $accountA,
        'a wrong-purpose logout must never touch the other purpose\'s session');

    // --- 14. Forced-change logout still works, then the session is dead.
    $sessions->logout($tokenF, 'desktop', NOW);
    t(statusOf(fn() => $sessions->withAccount($tokenF, 'desktop', NOW, true,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'admission after a forced-change logout') === 'AUTH_REVOKED',
        'logout works on a forced-change session and kills it');

    // --- 15. Source: no exception may ever carry a credential, and the raw
    //         token is only ever handed to the login response.
    $impl = codeOnly(sourceLines('host/src/AccountSessions.php'));
    t(preg_match('/\$error->getMessage\(\)\s*\.\s*\$/', $impl) !== 1,
        'no exception message may be concatenated with a runtime value');
    t(strpos($impl, "'token' =>") !== false,
        'the raw token is returned only in the login response envelope');
    t(substr_count($impl, 'newOpaque') === 1,
        'exactly one site mints an opaque token');

    // --- 15b. Lock order. Every function that takes a session row must take
    //         the account row FIRST, so no path can form session → account
    //         against admitWithin() / changePassword()'s account → session.
    //         The account_security_events foreign keys make a later accounts
    //         lock mandatory, not optional: locking the session first would be
    //         a reachable AB-BA against an admission on the same account.
    $lockOrders = lockOrders(sourceLines('host/src/AccountSessions.php'));
    t($lockOrders !== [],
        'the session service must take row locks somewhere');
    t(isset($lockOrders['logout']),
        'logout must lock rows');
    t(($lockOrders['logout']['first'] ?? null) === 'accounts',
        'logout must lock the account row before the session row');
    foreach ($lockOrders as $functionName => $order) {
        t(!in_array('account_sessions', $order['locked'], true)
            || $order['first'] === 'accounts',
            'no function may lock a session row before its account row: '
            . $functionName);
    }

    // =======================================================================
    // TWO-CONNECTION FIXTURE 1: a reset committed on a SECOND connection while
    // an admission is in flight. The child never learns a credential: it
    // generates its own and bumps security_version under the row lock.
    // =======================================================================
    $loginR = loginFor('race');
    $passwordR = BFixture::password();
    $madeR = makeAccount($accounts, $loginR, $passwordR, 'member', $ids);
    $accountR = (string) $madeR['account_id'];
    clearMustChange($app, $accountR);
    $tokenR = (string) $sessions->loginDesktop($loginR, $passwordR,
        '127.0.0.1', NOW)['token'];
    $versionR = (int) scalar($app,
        'SELECT security_version FROM accounts WHERE id = ?', [$accountR]);
    $child = startChild($accountR);
    $handshake = waitForFile($child['directory'] . DIRECTORY_SEPARATOR . 'ready', 30);
    t($handshake, 'the second connection must reach its locked handshake');
    @file_put_contents($child['directory'] . DIRECTORY_SEPARATOR . 'go', 'go');
    t(statusOf(fn() => $sessions->withAccount($tokenR, 'desktop', NOW, false,
        fn(Database $tx, AccountPrincipal $principal): string => $principal->accountId),
        401, 'admission racing a reset on a second connection') === 'AUTH_REVOKED',
        'a reset committed on another connection must invalidate the session');
    $childResult = finishChild($child);
    t(($childResult['outcome'] ?? '') === 'committed',
        'the competing connection must complete its reset');
    t((int) ($childResult['version'] ?? 0) === $versionR + 1,
        'the competing connection must bump security_version exactly once');
    t((int) scalar($app, 'SELECT security_version FROM accounts WHERE id = ?',
        [$accountR]) === $versionR + 1,
        'the bump must be durable on the shared row');
    t(codeOf(fn() => $sessions->loginDesktop($loginR, $passwordR, '127.0.0.1', NOW),
        'the original password after the racing reset') === 'AUTH_REQUIRED',
        'the racing reset must replace the credential');

    // =======================================================================
    // TWO-CONNECTION FIXTURE 2: a password-change racing a competing
    // security-version bump on a second connection. The change must refuse,
    // leave the competing bump in place, and mint nothing.
    // =======================================================================
    $loginP = loginFor('change');
    $passwordP = BFixture::password();
    $madeP = makeAccount($accounts, $loginP, $passwordP, 'member', $ids);
    $accountP = (string) $madeP['account_id'];
    clearMustChange($app, $accountP);
    $tokenP = (string) $sessions->loginDesktop($loginP, $passwordP,
        '127.0.0.1', NOW)['token'];
    $versionP = (int) scalar($app,
        'SELECT security_version FROM accounts WHERE id = ?', [$accountP]);
    $childP = startChild($accountP);
    $handshakeP = waitForFile($childP['directory'] . DIRECTORY_SEPARATOR . 'ready', 30);
    t($handshakeP, 'the second connection must reach its locked handshake');
    @file_put_contents($childP['directory'] . DIRECTORY_SEPARATOR . 'go', 'go');
    $intended = BFixture::password();
    t(codeOf(fn() => $sessions->changePassword($tokenP, 'desktop', NOW,
        $passwordP, $intended), 'change racing a competing bump')
        === 'AUTH_REVOKED',
        'a change racing a competing security bump must refuse');
    $childPResult = finishChild($childP);
    t(($childPResult['outcome'] ?? '') === 'committed',
        'the competing connection must complete its reset');
    $factsP = $app->queryOne(
        'SELECT security_version, must_change_password, password_hash FROM accounts'
        . ' WHERE id = ?', [$accountP]);
    t((int) $factsP['security_version'] === $versionP + 1,
        'the competing bump must stand; the refused change must not bump again');
    t((int) $factsP['must_change_password'] === 1,
        'the refused change must not clear the competing reset\'s flag');
    t(!password_verify($intended, (string) $factsP['password_hash']),
        'the refused change must never write its password');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?',
        [$accountP]) === 1,
        'the refused change must not mint a replacement session');
    t(codeOf(fn() => $sessions->changePassword($tokenP, 'desktop', NOW,
        $passwordP, BFixture::password()), 'change on a stale session')
        === 'AUTH_REVOKED',
        'a stale session may not change a password');

    // =======================================================================
    // TWO-CONNECTION FIXTURE 3: the LOCK ORDER under real contention. The
    // child holds the ACCOUNT row of the SAME account and keeps probing the
    // SESSION rows under a one-second lock-wait timeout, releasing each probe
    // through a SAVEPOINT. A logout that locked the session first would hold
    // that row for as long as the child holds the account, and every probe
    // would collide. The documented account → session order makes collision
    // impossible, so every probe must succeed.
    // =======================================================================
    $loginS = loginFor('gateorder');
    $passwordS = BFixture::password();
    $madeS = makeAccount($accounts, $loginS, $passwordS, 'member', $ids);
    $accountS = (string) $madeS['account_id'];
    clearMustChange($app, $accountS);
    $tokenS = (string) $sessions->loginDesktop($loginS, $passwordS,
        '127.0.0.1', NOW)['token'];
    $childS = startChild($accountS, 'order-probe');
    $caughtS = null;
    $resultS = null;
    $elapsedS = 0.0;
    $gateS = [];
    try {
        t(waitForFile($childS['directory'] . DIRECTORY_SEPARATOR . 'ready', 30),
            'the order-probe connection must reach its locked handshake');
        @file_put_contents($childS['directory'] . DIRECTORY_SEPARATOR . 'go', 'go');
        $started = microtime(true);
        try {
            $resultS = $sessions->logout($tokenS, 'desktop', NOW);
        } catch (ApiException $error) {
            $caughtS = $error->errorCode;
        } catch (Throwable $error) {
            $caughtS = get_class($error);
        }
        $elapsedS = microtime(true) - $started;
    } finally {
        $gateS = finishChild($childS);
    }
    t(($gateS['outcome'] ?? '') === 'probed',
        'the order probe must complete its bounded budget');
    t((int) ($gateS['probes'] ?? 0) >= 1,
        'the order probe must actually probe the session rows');
    t((int) ($gateS['contention'] ?? 0) === 0,
        'logout must never hold the session row while another transaction'
        . ' holds the account row');
    t($caughtS === null,
        'a contended logout must never surface an unhandled failure');
    t(is_array($resultS) && ($resultS['revoked'] ?? null) === true,
        'the contended logout must still revoke exactly its session');
    t($elapsedS < 10.0,
        'the contended logout must finish inside the bounded lock-wait budget');

    // =======================================================================
    // TWO-CONNECTION FIXTURE 4: logout racing an account-first locker on the
    // SAME account — the child takes the account row (as admission and
    // changePassword do) and then the session rows, the documented order,
    // while the parent logs out. Both sides must complete with a defined
    // outcome, inside a bounded budget, with no unhandled 1213.
    // =======================================================================
    $loginQ = loginFor('gaterace');
    $passwordQ = BFixture::password();
    $madeQ = makeAccount($accounts, $loginQ, $passwordQ, 'member', $ids);
    $accountQ = (string) $madeQ['account_id'];
    clearMustChange($app, $accountQ);
    $tokenQ = (string) $sessions->loginDesktop($loginQ, $passwordQ,
        '127.0.0.1', NOW)['token'];
    $childQ = startChild($accountQ, 'account-gate');
    $caughtQ = null;
    $resultQ = null;
    $elapsedQ = 0.0;
    $gateQ = [];
    try {
        t(waitForFile($childQ['directory'] . DIRECTORY_SEPARATOR . 'ready', 30),
            'the account-gate connection must reach its locked handshake');
        @file_put_contents($childQ['directory'] . DIRECTORY_SEPARATOR . 'go', 'go');
        $started = microtime(true);
        try {
            $resultQ = $sessions->logout($tokenQ, 'desktop', NOW);
        } catch (ApiException $error) {
            $caughtQ = $error->errorCode;
        } catch (Throwable $error) {
            $caughtQ = get_class($error);
        }
        $elapsedQ = microtime(true) - $started;
    } finally {
        $gateQ = finishChild($childQ);
    }
    t(($gateQ['outcome'] ?? '') === 'committed',
        'the competing account-first lock must complete');
    t((int) ($gateQ['sessions_locked'] ?? 0) >= 1,
        'the competing account-first locker must reach the session rows');
    t($caughtQ === null,
        'logout racing an account-first locker must never surface an'
        . ' unhandled failure');
    t(is_array($resultQ) && ($resultQ['revoked'] ?? null) === true,
        'the racing logout must still revoke exactly its session');
    t($elapsedQ < 10.0,
        'the racing logout must finish inside the bounded lock-wait budget');

    // =======================================================================
    // Bounded login-attempt lock: contention is RATE_LIMITED with a
    // Retry-After, never an unbounded wait, and the next attempt succeeds.
    // =======================================================================
    $loginL = loginFor('locked');
    $passwordL = BFixture::password();
    $madeL = makeAccount($accounts, $loginL, $passwordL, 'member', $ids);
    $accountL = (string) $madeL['account_id'];
    clearMustChange($app, $accountL);
    $lockName = AccountSessions::loginLockName($loginL, '10.0.0.77');
    $gate = openConnection();
    $held = (int) $gate->queryValue('SELECT GET_LOCK(?, 5) AS held',
        [$lockName]);
    t($held === 1, 'the fixture must be able to hold the login attempt lock');
    $status = 0;
    $retry = null;
    try {
        $sessions->loginDesktop($loginL, $passwordL, '10.0.0.77', NOW);
        $status = 200;
    } catch (ApiException $error) {
        $status = $error->status;
        t($error->errorCode === 'RATE_LIMITED',
            'lock contention must answer RATE_LIMITED');
        $retry = $error->retryAfter;
    }
    t($status === 429, 'lock contention must answer HTTP 429');
    t($retry !== null && $retry >= 1,
        'lock contention must carry a Retry-After');
    t($gate->queryValue('SELECT RELEASE_LOCK(?) AS released', [$lockName]) !== null,
        'the fixture lock must be releasable');
    $recovered = $sessions->loginDesktop($loginL, $passwordL, '10.0.0.77', NOW);
    t(preg_match('/\A[0-9a-f]{64}\z/D', (string) $recovered['token']) === 1,
        'a login succeeds again once the attempt lock is released');

    // The attempt lock is always released, even when the attempt fails: a
    // failure path must not leave the key held.
    $loginN = loginFor('released');
    t(codeOf(fn() => $sessions->loginDesktop($loginN, BFixture::password(),
        '10.0.0.78', NOW), 'unknown account under the lock') === 'AUTH_REQUIRED',
        'a failed attempt must still answer the generic refusal');
    $gate2 = (int) openConnection()->queryValue(
        'SELECT GET_LOCK(?, 1) AS held',
        [AccountSessions::loginLockName($loginN, '10.0.0.78')]);
    t($gate2 === 1,
        'a failed attempt must release the advisory lock in its finally block');
} finally {
    clearSynthetic($app ?? BFixture::db(), $ids);
}

print("SESSIONS_SCENARIO_OK accounts=" . count($ids['accounts'])
    . " engine=" . BFixture::db()->serverVersion() . "\n");
