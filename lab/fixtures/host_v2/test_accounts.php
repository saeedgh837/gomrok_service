<?php
/**
 * test_accounts.php — Phase B central account suite (`--suite accounts`).
 *
 * Runs against the separately provisioned fixture instance only. It never
 * reads application configuration, never touches production, and never prints
 * a credential, a login, a password, a hash, a token or a session identifier:
 * a failure is always one of the fixed FIXTURE_* labels emitted by
 * bootstrap.php, and every assertion message names the RULE, not the data.
 *
 * What this suite proves
 *
 *   1. Canonical login. trim + lowercase, then the exact ASCII grammar
 *      `[a-z0-9._-]{1,64}`. Blank, interior whitespace, NUL, non-ASCII,
 *      malformed UTF-8 and 65 characters all answer VALIDATION_FAILED — the
 *      legacy tenant-bound username validator is NOT the oracle for a global
 *      account, and nothing is ever silently truncated to fit.
 *   2. Password policy. Valid UTF-8, no NUL, at least 12 characters (mb,
 *      never bytes) and at most 72 BYTES — the documented bcrypt ceiling —
 *      or VALIDATION_FAILED, before any hashing or any write. The string is
 *      checked exactly as supplied: never trimmed, never normalised, and the
 *      suite carries mutation traps that fail if either is ever added.
 *   3. Global uniqueness. `accounts.login` is `ascii_bin`, so canonicalLogin's
 *      strtolower IS the case-insensitive uniqueness rule: the same normalized
 *      login is ONE global account, never an automatic merge, and a second
 *      attempt — which would have received its OWN private workspace — is
 *      ACCOUNT_LOGIN_CONFLICT, never a raw database message.
 *   4. Atomic creation. Account + private workspace + admin membership commit
 *      in ONE transaction through `Workspaces::createPrivateWithin()` inside
 *      the caller's transaction (`Database::transaction()` has no nesting and
 *      no savepoints). A refused or failing workspace step leaves no account;
 *      a refused account leaves no workspace.
 *   5. Hash discipline. The password is hashed OUTSIDE the write transaction;
 *      the stored hash verifies the plaintext, differs from it, and appears
 *      nowhere in the creation receipt. The receipt schema carries no
 *      password, hash or token key.
 *   6. Password reset by account UUID only. Hash outside the transaction, then
 *      lock the account, increment security_version and set
 *      must_change_password=1 in ONE transaction. Every existing session dies
 *      by VERSION COMPARISON at admission — never by scanning session rows —
 *      and physical revocation is bounded housekeeping, not the authorization
 *      dependency. Another account's login, hash, version and memberships are
 *      untouched.
 *   7. Status and operator changes serialize on identity_state BEFORE the
 *      account lock; disabling logically invalidates sessions; the last active
 *      platform operator can never be disabled or demoted
 *      (LAST_OPERATOR_REQUIRED).
 *   8. seedFirstOperator owns its single transaction: identity_state locked,
 *      prior seed refused (VERSION_CONFLICT), and the account is created
 *      through the private account-creation-within-transaction helper, never
 *      by nesting the transactional create().
 *   9. Reviewed legacy linkage. The composite (tenant, legacy user) key, the
 *      account, a valid reviewed UUID and a target already prepared by the
 *      reviewed reset service (must_change_password = 1) are all required.
 *      A wrong-tenant user, a conflicting existing map, an unrestricted
 *      target and an ALREADY-LINKED account (ACCOUNT_ALREADY_LINKED: one
 *      account holds exactly one legacy identity) are all refused WITHOUT
 *      changing the target, no membership is ever granted by inferred
 *      username, and no legacy password hash is copied.
 *  10. Display names are valid UTF-8, bounded to 120 UNICODE code points
 *      (mb, never bytes) and never used as record identity.
 *  11. Structural regression for the two tenant-creation sites (ruling 30/30b):
 *      `host/public/setup.php` and `host/bin/create-admin.php` must both state
 *      `lifecycle = active` and `api_contract = legacy_v1` EXPLICITLY, because
 *      002 flipped the column defaults to staging / authoritative_v2 and a
 *      bootstrap tenant that omits them is refused by the legacy gate. Both
 *      must be REAL array elements of the insert literal: a comment or a
 *      string value that spells the key out does not satisfy the check.
 *
 * Nothing here drops or truncates a table, and nothing re-provisions. Every
 * synthetic row this suite creates is deleted by this suite, in dependency
 * order, in a `finally` block; the identity_state singleton is restored to the
 * values it held before seedFirstOperator ran.
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
          'Auth', 'Accounts'] as $classFile) {
    require_once $repoRoot . '/host/src/' . $classFile . '.php';
}

/** One fixed synthetic timestamp; no clock, no locale, no drift. */
const STAMP = '2026-01-01T00:00:00+00:00';
const NOW = 1893456000; // 2030-01-01T00:00:00Z, a fixed server clock.

/** The parent and the race child both attempt this exact login. */
const RACE_LOGIN = 'fixture.race.parallel.user';

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
    return 'fixture.' . $tag . '.' . substr(str_replace('-', '', uuid()), 0, 16);
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

/**
 * Assert that `$operation` is ACCEPTED: any ApiException becomes a labelled
 * failure carrying the fixed refusal code, never a swallowed pass.
 */
function accepts(callable $operation, string $what): void
{
    try {
        $operation();
    } catch (ApiException $error) {
        throw new RuntimeException('SCENARIO_FAILED: ' . $what
            . ' (refused with ' . $error->errorCode . ')');
    }
}

function tenantCount(Database $db): int
{
    return (int) scalar($db, 'SELECT COUNT(*) FROM tenants');
}

function accountCount(Database $db): int
{
    return (int) scalar($db, 'SELECT COUNT(*) FROM accounts');
}

/** The account row, restricted to non-secret columns. */
function accountFacts(Database $db, string $accountId): array
{
    $row = $db->queryOne(
        'SELECT login, display_name, status, must_change_password,'
        . ' security_version, platform_role FROM accounts WHERE id = ?',
        [$accountId]);
    t($row !== null, 'the account row must exist');
    return $row;
}

/** Membership facts of one account, in tenant order. */
function membershipsOf(Database $db, string $accountId): array
{
    return $db->query(
        'SELECT tenant_id, role, status FROM account_workspace_memberships'
        . ' WHERE account_id = ? ORDER BY tenant_id ASC', [$accountId]);
}

/** A synthetic account row this suite owns, used to force a failing step. */
function insertAccountRow(Database $db, string $accountId, string $status): void
{
    $db->insert('accounts', [
        'id' => $accountId,
        'login' => loginFor('row.' . $status),
        'password_hash' => password_hash(bin2hex(random_bytes(16)), PASSWORD_DEFAULT),
        'display_name' => '',
        'status' => $status,
        'must_change_password' => 1,
        'security_version' => 1,
        'platform_role' => 'member',
        'created_at' => STAMP,
        'updated_at' => STAMP,
    ]);
}

/** Synthetic legacy tenant + user, owned and deleted here. */
function legacyPair(Database $db, array &$ids, string $tag): array
{
    $tenantId = uuid();
    $userId = uuid();
    $ids['tenants'][] = $tenantId;
    $ids['users'][] = $userId;
    $db->insert('tenants', [
        'id' => $tenantId,
        'slug' => 'fx-legacy-' . substr(str_replace('-', '', $tenantId), 0, 18),
        'name' => 'Fixture legacy workspace',
        'status' => 'active',
        // Explicit, never a column default: a bootstrap tenant that relied on
        // the 002 defaults would be staging / authoritative_v2 and the legacy
        // gate would refuse it.
        'lifecycle' => 'active',
        'api_contract' => 'legacy_v1',
        'context_version' => 1,
        'created_at' => STAMP,
        'updated_at' => STAMP,
    ]);
    $db->insert('users', [
        'id' => $userId,
        'tenant_id' => $tenantId,
        'username' => 'fixture_' . $tag . '_'
            . substr(str_replace('-', '', $userId), 0, 12),
        'password_hash' => password_hash(bin2hex(random_bytes(16)), PASSWORD_DEFAULT),
        'display_name' => '',
        'must_change_password' => 0,
        'status' => 'active',
        'token_version' => 1,
        'row_version' => 1,
        'created_at' => STAMP,
        'updated_at' => STAMP,
    ]);
    return ['tenant_id' => $tenantId, 'user_id' => $userId];
}

/**
 * One account created through the service, with every row it owns recorded so
 * the `finally` cleanup can delete them in foreign-key-safe order.
 *
 * @return array<string, mixed>
 */
function makeAccount(Accounts $accounts, string $login, string $password,
                     ?string $displayName, string $platformRole,
                     array &$ids): array
{
    $made = $accounts->create($login, $password, $displayName, $platformRole, NOW);
    $ids['accounts'][] = (string) $made['account_id'];
    $ids['workspaces'][] = (string) $made['workspace_id'];
    return $made;
}

/** Delete every synthetic row this suite owns, in foreign-key-safe order. */
function clearSynthetic(Database $db, array $ids): void
{
    // The identity_state singleton references an account row: release it first.
    if (isset($ids['identity_state'])) {
        $db->execute('UPDATE identity_state SET first_operator_id = NULL,'
            . ' row_version = ? WHERE id = 1', [$ids['identity_state']['row_version']]);
    }
    $accounts = array_values(array_unique(array_filter($ids['accounts'] ?? [])));
    $tenants = array_values(array_unique(array_filter(array_merge(
        $ids['tenants'] ?? [], $ids['workspaces'] ?? []))));
    $users = array_values(array_unique(array_filter($ids['users'] ?? [])));

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
        $db->execute(
            'DELETE FROM account_security_events WHERE tenant_id IN (' . $in . ')',
            $tenants);
        $db->execute(
            'DELETE FROM account_workspace_memberships WHERE tenant_id IN (' . $in . ')',
            $tenants);
        $db->execute('DELETE FROM memberships WHERE tenant_id IN (' . $in . ')', $tenants);
        $db->execute('DELETE FROM users WHERE tenant_id IN (' . $in . ')', $tenants);
        $db->execute('DELETE FROM tenants WHERE id IN (' . $in . ')', $tenants);
    }
    if ($users !== []) {
        $in = implode(',', array_fill(0, count($users), '?'));
        $db->execute('DELETE FROM users WHERE id IN (' . $in . ')', $users);
    }
    if ($accounts !== []) {
        $in = implode(',', array_fill(0, count($accounts), '?'));
        $db->execute('DELETE FROM accounts WHERE id IN (' . $in . ')', $accounts);
    }
}

// ---------------------------------------------------------------------------
// The race child.
//
// argv: --race <directory>. The login is a CONSTANT in this file, never a
// command-line argument and never printed: argv and stdout stay free of any
// identifier. The child signals `ready` immediately before it attempts the
// create, so the parent's own attempt interleaves with it without a sleep.
// ---------------------------------------------------------------------------

/** @param list<string> $arguments */
function raceMain(array $arguments): int
{
    if (count($arguments) !== 2 || $arguments[0] !== '--race') {
        return 2;
    }
    $directory = $arguments[1];
    if (!is_dir($directory)) {
        return 2;
    }
    $outcome = ['ok' => false, 'error' => 'FIXTURE_SCENARIO_FAILED'];
    try {
        $accounts = new Accounts(BFixture::db());
        @file_put_contents($directory . DIRECTORY_SEPARATOR . 'ready', 'ready');
        $made = $accounts->create(RACE_LOGIN, BFixture::password(), null,
            'member', NOW);
        $outcome = ['ok' => true, 'account_id' => (string) $made['account_id'],
                    'workspace_id' => (string) $made['workspace_id']];
    } catch (ApiException $error) {
        $outcome = ['ok' => false, 'error' => $error->errorCode];
    } catch (Throwable $error) {
        unset($error);
        $outcome = ['ok' => false, 'error' => 'FIXTURE_SCENARIO_FAILED'];
    }
    @file_put_contents($directory . DIRECTORY_SEPARATOR . 'done.part',
        json_encode($outcome));
    @rename($directory . DIRECTORY_SEPARATOR . 'done.part',
        $directory . DIRECTORY_SEPARATOR . 'done');
    return ($outcome['ok'] ?? false) === true ? 0 : 1;
}

if (isset($argv[1]) && $argv[1] === '--race') {
    exit(raceMain(array_slice($argv, 1)));
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

/**
 * Start the race child. The parent attempts the SAME login only after the
 * child has written `ready`.
 *
 * @return array<string, mixed>
 */
function startRaceChild(): array
{
    $directory = sys_get_temp_dir() . DIRECTORY_SEPARATOR
        . 'irica-b-accounts-' . bin2hex(random_bytes(8));
    t(mkdir($directory, 0700, true), 'the race directory must be created');
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
        '--race', $directory];
    $output = $directory . DIRECTORY_SEPARATOR . 'output';
    $descriptor = [0 => ['pipe', 'r'], 1 => ['file', $output, 'w'],
                   2 => ['file', $output, 'a']];
    $process = proc_open($command, $descriptor, $pipes, null, $environment);
    t(is_resource($process), 'the race child must start');
    return ['process' => $process, 'directory' => $directory];
}

/** @return array<string, mixed> */
function finishRaceChild(array $handle): array
{
    $parsed = waitForJson($handle['directory'] . DIRECTORY_SEPARATOR . 'done', 60);
    // Never relay child output: it could hold a driver diagnostic.
    $status = proc_close($handle['process']);
    t($parsed !== null, 'the race child must report a result');
    foreach (['ready', 'done', 'output'] as $name) {
        @unlink($handle['directory'] . DIRECTORY_SEPARATOR . $name);
    }
    @rmdir($handle['directory']);
    t($status === 0 || $status === 1, 'the race child must terminate cleanly');
    return $parsed;
}

// ===========================================================================
// The structural regression for ruling 30 / 30b.
// ===========================================================================

/**
 * Read one PHP quoted string starting at `$i`, advancing `$i` past it.
 *
 * Escape pairs are kept verbatim so an escaped quote cannot end the string
 * early. An unterminated string returns null (fail closed).
 *
 * @return string|null the raw contents between the quotes
 */
function phpQuotedAt(string $text, int &$i): ?string
{
    $length = strlen($text);
    $quote = $text[$i] ?? '';
    if ($quote !== "'" && $quote !== '"') {
        return null;
    }
    $i++;
    $raw = '';
    while ($i < $length) {
        $character = $text[$i];
        if ($character === '\\' && $i + 1 < $length) {
            $raw .= $character . $text[$i + 1];
            $i += 2;
            continue;
        }
        if ($character === $quote) {
            $i++;
            return $raw;
        }
        $raw .= $character;
        $i++;
    }
    return null;
}

/**
 * Every `'key' => 'value'` ELEMENT of the literal's own array depth.
 *
 * This is a structural read, not a text search:
 *
 *   * comments (`//`, `#`, `/* … *`​/`) are skipped, never matched;
 *   * a quoted string counts as a KEY only where an element key may legally
 *     appear (after `[` or `,` at depth 1) and only when `=>` follows it;
 *   * a quoted string anywhere else — above all a VALUE such as
 *     `"'api_contract' => 'legacy_v1'"` — is consumed as data and dropped.
 *
 * So neither a comment nor a string value that happens to carry the exact
 * quoted key form can satisfy the structural check (reviewer M-6): only a
 * real array element at the literal's own depth can.
 *
 * @return array<int, array{key: string, value: string}>
 */
function tenantContractElements(string $literal): array
{
    $elements = [];
    $length = strlen($literal);
    $depth = 0;
    $expectKey = false;
    $i = 0;
    while ($i < $length) {
        $character = $literal[$i];

        // Comments: skipped entirely.
        if ($character === '/' && $i + 1 < $length && $literal[$i + 1] === '/') {
            while ($i < $length && $literal[$i] !== "\n") {
                $i++;
            }
            continue;
        }
        if ($character === '#') {
            while ($i < $length && $literal[$i] !== "\n") {
                $i++;
            }
            continue;
        }
        if ($character === '/' && $i + 1 < $length && $literal[$i + 1] === '*') {
            $i += 2;
            while ($i + 1 < $length
                && !($literal[$i] === '*' && $literal[$i + 1] === '/')) {
                $i++;
            }
            $i = min($i + 2, $length);
            continue;
        }

        if ($character === '[') {
            $depth++;
            $expectKey = ($depth === 1);
            $i++;
            continue;
        }
        if ($character === ']') {
            $depth--;
            $expectKey = false;
            $i++;
            continue;
        }
        if ($character === ',' && $depth === 1) {
            $expectKey = true;
            $i++;
            continue;
        }

        if ($character === "'" || $character === '"') {
            $quoted = phpQuotedAt($literal, $i);
            if ($quoted === null) {
                return $elements; // unterminated: fail closed
            }
            if ($expectKey && $depth === 1) {
                $afterKey = $i;
                while ($afterKey < $length
                    && strpos(" \t\r\n", $literal[$afterKey]) !== false) {
                    $afterKey++;
                }
                if (substr($literal, $afterKey, 2) === '=>') {
                    $afterArrow = $afterKey + 2;
                    while ($afterArrow < $length
                        && strpos(" \t\r\n", $literal[$afterArrow]) !== false) {
                        $afterArrow++;
                    }
                    $value = phpQuotedAt($literal, $afterArrow);
                    if ($value !== null) {
                        $elements[] = ['key' => $quoted, 'value' => $value];
                        $i = $afterArrow;
                        $expectKey = false;
                        continue;
                    }
                }
            }
            $expectKey = false;
            continue;
        }

        if (!ctype_space($character)) {
            // A bare token (identifier, number, variable) ends key position.
            $expectKey = false;
        }
        $i++;
    }
    return $elements;
}

/** The values `key` takes as a REAL array element of `$literal`. */
function tenantContractValues(string $literal, string $key): array
{
    $values = [];
    foreach (tenantContractElements($literal) as $element) {
        if ($element['key'] === $key) {
            $values[] = $element['value'];
        }
    }
    return $values;
}

/**
 * Both product tenant-creation sites must STATE lifecycle and api_contract.
 *
 * The check is on a REAL element of the insert literal — not on a comment, not
 * on a string value and not on a column default: after 002 the defaults are
 * staging / authoritative_v2, and a bootstrap tenant that inherits them is
 * refused by the legacy gate. Removing either key from either site fails this
 * assertion, and so does replacing it with a comment that spells it out.
 */
function assertTenantContractStated(string $relativePath): void
{
    global $repoRoot;
    $path = $repoRoot . '/' . $relativePath;
    t(is_file($path), $relativePath . ' must exist to be structurally checked');
    $source = file_get_contents($path);
    t(is_string($source) && $source !== '', $relativePath . ' must be readable');

    $position = strpos($source, "insert('tenants'");
    t($position !== false, $relativePath . ' must contain a tenants insert');
    // Exactly one bootstrap tenant-creation site in each of these two files.
    t(strpos($source, "insert('tenants'", $position + 1) === false,
        $relativePath . ' must contain exactly one tenants insert');
    $block = substr($source, $position, 1200);
    $end = strpos($block, ']);');
    t($end !== false, $relativePath . ' tenants insert must be a closed array literal');
    $literal = substr($block, 0, $end);
    t(in_array('active', tenantContractValues($literal, 'lifecycle'), true),
        $relativePath . " must state 'lifecycle' => 'active' as a real array element");
    t(in_array('legacy_v1', tenantContractValues($literal, 'api_contract'), true),
        $relativePath . " must state 'api_contract' => 'legacy_v1' as a real array element");
}

// ===========================================================================
// The scenario
// ===========================================================================

$ids = ['accounts' => [], 'workspaces' => [], 'tenants' => [], 'users' => []];
try {
    $app = BFixture::db();
    $accounts = new Accounts($app);
    $workspaces = new Workspaces($app);

    // --- 0. The two new codes exist, match §15.8's grammar, and are NOT
    //         declared twice (a duplicate `public const` is a PHP fatal).
    t(Codes::isValid(Codes::ACCOUNT_LOGIN_CONFLICT),
        'ACCOUNT_LOGIN_CONFLICT exists and matches the error-code grammar');
    t(Codes::isValid(Codes::LAST_OPERATOR_REQUIRED),
        'LAST_OPERATOR_REQUIRED exists and matches the error-code grammar');
    t(substr_count(file_get_contents($repoRoot . '/host/src/Http.php'),
        'const ACCOUNT_LOGIN_CONFLICT') === 1,
        'ACCOUNT_LOGIN_CONFLICT is declared exactly once in Codes');
    t(substr_count(file_get_contents($repoRoot . '/host/src/Http.php'),
        'const LAST_OPERATOR_REQUIRED') === 1,
        'LAST_OPERATOR_REQUIRED is declared exactly once in Codes');
    t(Codes::isValid(Codes::ACCOUNT_ALREADY_LINKED),
        'ACCOUNT_ALREADY_LINKED exists and matches the error-code grammar');
    t(substr_count(file_get_contents($repoRoot . '/host/src/Http.php'),
        'const ACCOUNT_ALREADY_LINKED') === 1,
        'ACCOUNT_ALREADY_LINKED is declared exactly once in Codes');

    // --- 1. Canonical login: trim, lowercase, exact ASCII grammar.
    t(Accounts::canonicalLogin('  ALICE.ONE	') === 'alice.one',
        'canonicalLogin trims surrounding whitespace and lowercases');
    t(Accounts::canonicalLogin('alice.one') === 'alice.one',
        'canonicalLogin is the identity on an already-canonical login');
    t(Accounts::canonicalLogin('Fixture.User-1_x') === 'fixture.user-1_x',
        'canonicalLogin accepts the documented ASCII character class');
    t(Accounts::canonicalLogin(str_repeat('a', 64)) === str_repeat('a', 64),
        'a 64-character login is accepted');
    $invalidLogins = [
        'blank' => '',
        'whitespace-only' => '    ',
        'interior-space' => 'user name',
        'interior-tab' => "user\tname",
        'interior-newline' => "user\nname",
        'overlong-65' => str_repeat('a', 65),
        'overlong-200' => str_repeat('a', 200),
        'nul-interior' => "user\0name",
        'nul-prefix' => "\0user",
        'non-ascii' => 'aliسe',
        'malformed-utf8' => "\xC3\x28user",
        'high-byte' => "user\xFF",
        'at-sign' => 'user@name',
        'slash' => 'user/name',
    ];
    foreach ($invalidLogins as $what => $login) {
        $code = codeOf(static fn () => Accounts::canonicalLogin($login), $what);
        t($code === 'VALIDATION_FAILED',
            $what . ': an invalid login is VALIDATION_FAILED, never truncated');
    }
    // No silent truncation: the 65-character input is REJECTED, not stored as
    // a 64-character login anywhere.
    t(strlen(str_repeat('a', 65)) === 65,
        'the overlong fixture input is genuinely overlong');

    // --- 2. Password policy: UTF-8, no NUL, >= 12 characters, <= 72 bytes.
    Accounts::validateNewPassword(str_repeat('x', 12));
    Accounts::validateNewPassword(str_repeat('x', 72));
    Accounts::validateNewPassword(str_repeat('م', 12)); // 12 chars, 24 bytes
    $invalidPasswords = [
        'short-11' => str_repeat('x', 11),
        'empty' => '',
        'long-73-bytes' => str_repeat('x', 73),
        'long-4096-bytes' => str_repeat('x', 4096),
        'nul-interior' => str_repeat('x', 12) . "\0",
        'nul-prefix' => "\0" . str_repeat('x', 12),
        'malformed-utf8' => "\xC3\x28" . str_repeat('x', 12),
        'high-byte' => "\xFF" . str_repeat('x', 12),
        // 40 Arabic digits are 12 characters and 80 BYTES: under the
        // character floor they would pass, over the byte ceiling they must not.
        'multibyte-over-72-bytes' => str_repeat('م', 40),
    ];
    foreach ($invalidPasswords as $what => $password) {
        $code = codeOf(static fn () => Accounts::validateNewPassword($password), $what);
        t($code === 'VALIDATION_FAILED',
            $what . ': an invalid password is VALIDATION_FAILED before hashing');
    }

    // --- 2b. The password is the EXACT string the operator chose: it is
    //         never trimmed and never normalised. Each block below states a
    //         fixture property AND the verdict, so the assertion flips if a
    //         `trim()` or a composition step is ever added to the validator.
    //
    // (a) 12 meaningful code points padded with surrounding whitespace: the
    //     exact string is 73 BYTES, so the byte ceiling refuses it today. A
    //     trimming validator would cut it to 12 code points / 24 bytes and
    //     ACCEPT it — exactly the silent rewrite the brief forbids.
    $paddedMultibyte = ' ' . str_repeat('م', 12) . str_repeat(' ', 48);
    t(strlen($paddedMultibyte) > 72,
        'the padded fixture really does exceed the 72-byte ceiling as supplied');
    t(mb_strlen(trim($paddedMultibyte), 'UTF-8') === 12
        && strlen(trim($paddedMultibyte)) <= 72,
        'the padded fixture would be ACCEPTED if the validator trimmed it');
    t(codeOf(static fn () => Accounts::validateNewPassword($paddedMultibyte),
        '12 meaningful code points padded with whitespace') === 'VALIDATION_FAILED',
        'a password of 12 code points plus surrounding whitespace is still refused (no trim)');

    // (b) The mirror case: 11 visible characters padded to exactly 12 code
    //     points. As supplied it is a valid 12-character password and must be
    //     accepted; trimming it to 11 would refuse it.
    $paddedShort = ' ' . str_repeat('x', 11);
    t(mb_strlen($paddedShort, 'UTF-8') === 12
        && mb_strlen(trim($paddedShort), 'UTF-8') === 11,
        'the short padded fixture would be REFUSED if the validator trimmed it');
    accepts(static fn () => Accounts::validateNewPassword($paddedShort),
        'a whitespace-padded password is validated as the exact string it is');

    // (c) Exactly 12 code points, no whitespace: still accepted.
    $bareTwelve = str_repeat('x', 12);
    t(mb_strlen($bareTwelve, 'UTF-8') === 12
        && strlen($bareTwelve) === 12
        && trim($bareTwelve) === $bareTwelve,
        'the bare 12-character fixture is unaffected by trimming');
    accepts(static fn () => Accounts::validateNewPassword($bareTwelve),
        'a password of exactly 12 code points with no whitespace is accepted');

    // (d) Composition is not normalised away either: six DECOMPOSED letters
    //     are 12 code points as supplied (18 bytes). Their precomposed
    //     spelling is 6 code points, so an NFC step would drop the password
    //     below the 12-character floor and refuse it.
    $decomposed = str_repeat("\u{0065}\u{0301}", 6);
    t(mb_strlen($decomposed, 'UTF-8') === 12,
        'the decomposed fixture really is 12 code points as supplied');
    t(mb_strlen(str_repeat("\u{00E9}", 6), 'UTF-8') === 6,
        'the precomposed spelling of the same text is only 6 code points, so an NFC step would refuse it');
    accepts(static fn () => Accounts::validateNewPassword($decomposed),
        'a decomposed password is counted in code points as supplied (no normalisation)');

    // --- 3. Creation: one transaction, one receipt, no secret in it.
    $loginA = loginFor('create.a');
    $passwordA = BFixture::password();
    $tenantsBefore = tenantCount($app);
    $made = makeAccount($accounts, $loginA, $passwordA, 'Synthetic Account A',
        'member', $ids);
    $accountId = (string) $made['account_id'];
    $workspaceId = (string) $made['workspace_id'];
    t(tenantCount($app) === $tenantsBefore + 1,
        'creating an account creates exactly one private workspace');
    t(Ids::isUuid($accountId), 'the receipt carries an account UUID');
    t(Ids::isUuid($workspaceId), 'the receipt carries a workspace UUID');
    t(($made['must_change_password'] ?? null) === true,
        'a new account is created with must_change_password = 1');
    t(($made['login'] ?? '') === $loginA, 'the receipt reports the canonical login');

    $receiptKeys = array_keys($made);
    t(!in_array('password_hash', $receiptKeys, true)
        && !in_array('password', $receiptKeys, true)
        && !in_array('hash', $receiptKeys, true)
        && !in_array('token', $receiptKeys, true),
        'the receipt schema has no password, hash or token key');
    foreach ($receiptKeys as $key) {
        t($key === 'must_change_password' || !str_contains($key, 'password'),
            'the creation receipt carries no password value');
        t(!str_contains($key, 'hash'), 'the creation receipt carries no hash');
        t(!str_contains($key, 'token'), 'the creation receipt carries no token');
    }
    $encoded = (string) json_encode($made);
    t(!str_contains($encoded, $passwordA),
        'the serialized receipt never contains the plaintext password');
    $storedHash = (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$accountId]);
    t($storedHash !== '' && !str_contains($encoded, $storedHash),
        'the serialized receipt never contains the stored hash');

    // The stored hash verifies the plaintext, differs from it, and is the only
    // durable form of the credential.
    t(password_verify($passwordA, $storedHash) === true,
        'the stored hash verifies the plaintext password');
    t($storedHash !== $passwordA, 'the stored hash is never the plaintext password');
    t(!str_contains($storedHash, $passwordA),
        'the stored hash never embeds the plaintext password');
    t((string) scalar($app, 'SELECT display_name FROM accounts WHERE id = ?',
        [$accountId]) === 'Synthetic Account A',
        'the display name is stored as given');
    t((string) scalar($app, 'SELECT platform_role FROM accounts WHERE id = ?',
        [$accountId]) === 'member',
        'the platform role defaults to the closed member value');
    t((int) scalar($app, 'SELECT security_version FROM accounts WHERE id = ?',
        [$accountId]) === 1, 'a new account starts at security_version 1');

    // Account + private workspace + admin membership, all durable.
    $tenantRow = $app->queryOne(
        'SELECT status, lifecycle, api_contract, context_version FROM tenants WHERE id = ?',
        [$workspaceId]);
    t($tenantRow !== null, 'the private workspace row exists');
    t($tenantRow['status'] === 'active' && $tenantRow['lifecycle'] === 'active'
        && $tenantRow['api_contract'] === 'authoritative_v2'
        && (int) $tenantRow['context_version'] === 1,
        'the private workspace is explicitly active / authoritative_v2');
    $membership = membershipsOf($app, $accountId);
    t(count($membership) === 1, 'a new account holds exactly one membership');
    t($membership[0]['tenant_id'] === $workspaceId
        && $membership[0]['role'] === 'admin' && $membership[0]['status'] === 'active',
        'the owning account is an active admin of its own private workspace');
    t($workspaces->listWithin($accountId, null, 10)['items'] !== [],
        'the new account can select its own private workspace');

    // --- 3b. Validation runs BEFORE any write, so a refused create leaves
    //          neither an account nor a workspace behind.
    foreach (['bad-login' => ['user name', BFixture::password(), 'member'],
              'short-password' => [loginFor('bad.short'), str_repeat('x', 11), 'member'],
              'nul-password' => [loginFor('bad.nul'), str_repeat('x', 12) . "\0", 'member'],
              'bad-role' => [loginFor('bad.role'), BFixture::password(), 'root']] as $what => $triple) {
        $before = [accountCount($app), tenantCount($app)];
        t(codeOf(static fn () => $accounts->create($triple[0], $triple[1], null,
            $triple[2], NOW), $what) === 'VALIDATION_FAILED',
            $what . ' is refused before anything is written');
        t([accountCount($app), tenantCount($app)] === $before,
            $what . ' leaves no account and no workspace behind');
    }
    t(codeOf(static fn () => $accounts->setStatus(uuid(), 'suspended', NOW),
        'an unknown status value') === 'VALIDATION_FAILED',
        'an unknown account status is VALIDATION_FAILED');
    t(codeOf(static fn () => $accounts->setPlatformRole(uuid(), 'root', NOW),
        'an unknown platform role value') === 'VALIDATION_FAILED',
        'an unknown platform role is VALIDATION_FAILED');
    t(codeOf(static fn () => $accounts->resetPassword('not-a-uuid',
        BFixture::password(), NOW), 'a malformed reset target') === 'VALIDATION_FAILED',
        'a reset target that is not an account UUID is VALIDATION_FAILED');

    // --- 4. Atomicity in BOTH directions.
    // (a) A duplicate login inside a caller's transaction is the new conflict
    //     code, and it leaves no workspace behind.
    $before = [accountCount($app), tenantCount($app)];
    try {
        $app->transaction(function (Database $tx) use ($accounts, $loginA): void {
            $accounts->createWithin($tx, $loginA, Auth::hashPassword(
                BFixture::password()), null, 'member', NOW);
        });
        throw new RuntimeException('FIXTURE_EXPECTED_REJECTION: a duplicate login');
    } catch (ApiException $error) {
        t($error->errorCode === 'ACCOUNT_LOGIN_CONFLICT',
            'a duplicate login inside a caller transaction is ACCOUNT_LOGIN_CONFLICT');
        t($error->status === 422,
            'the duplicate-login refusal is a 422 validation-class conflict');
    }
    t([accountCount($app), tenantCount($app)] === $before,
        'a failed account insert leaves no workspace behind');

    // (b) A workspace step that FAILS inside the same transaction as the
    //     account creation leaves no account, no workspace and no membership.
    //     `createPrivateWithin()` refuses a non-active owner, which is a real
    //     workspace-step failure rather than a thrown test sentinel.
    $disabledOwner = uuid();
    $ids['accounts'][] = $disabledOwner;
    insertAccountRow($app, $disabledOwner, 'disabled');
    $atomicReceipt = null;
    try {
        $app->transaction(function (Database $tx) use (
            $accounts, $workspaces, $disabledOwner, &$atomicReceipt
        ): void {
            $atomicReceipt = $accounts->createWithin($tx, loginFor('atomic'),
                Auth::hashPassword(BFixture::password()), null, 'member', NOW);
            // The workspace step of the SAME transaction now fails.
            $workspaces->createPrivateWithin($tx, $disabledOwner, 'never created');
            throw new RuntimeException('unreachable');
        });
        throw new RuntimeException('FIXTURE_EXPECTED_REJECTION: a failing workspace step');
    } catch (ApiException $error) {
        t($error->errorCode === 'NOT_FOUND',
            'the workspace step is what failed inside the transaction');
    }
    t($atomicReceipt !== null && Ids::isUuid((string) $atomicReceipt['account_id']),
        'the account existed inside the transaction before the workspace failed');
    t((int) scalar($app, 'SELECT COUNT(*) FROM accounts WHERE id = ?',
        [(string) $atomicReceipt['account_id']]) === 0,
        'a failed workspace step leaves no account behind');
    t((int) scalar($app, 'SELECT COUNT(*) FROM tenants WHERE id = ?',
        [(string) $atomicReceipt['workspace_id']]) === 0,
        'a failed workspace step leaves no workspace behind');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_workspace_memberships'
        . ' WHERE tenant_id = ?', [(string) $atomicReceipt['workspace_id']]) === 0,
        'a failed workspace step leaves no membership behind');

    // (c) The helper never opens a transaction of its own: Database::
    //     transaction() has no nesting and no savepoints, so the creation
    //     path must be the caller-transaction primitive.
    $source = file_get_contents($repoRoot . '/host/src/Accounts.php');
    t(strpos($source, 'createPrivateWithin') !== false,
        'account creation goes through Workspaces::createPrivateWithin');
    $withinStart = (int) strpos($source, 'function createWithin(');
    t($withinStart > 0, 'createWithin is the caller-transaction primitive');
    $withinBody = substr($source, $withinStart, 3000);
    $withinEnd = strpos($withinBody, "\n    public function ");
    if ($withinEnd !== false) {
        $withinBody = substr($withinBody, 0, $withinEnd);
    }
    t(!str_contains($withinBody, 'transaction('),
        'createWithin never opens a nested transaction or savepoint');
    // The hash is produced BEFORE the write transaction begins, never inside it.
    $createStart = (int) strpos($source, 'public function create(');
    t($createStart > 0, 'create exists in Accounts');
    $createBody = substr($source, $createStart, 3000);
    $createEnd = strpos($createBody, "\n    public function ");
    if ($createEnd !== false) {
        $createBody = substr($createBody, 0, $createEnd);
    }
    $hashAt = strpos($createBody, 'hashPassword');
    $txAt = strpos($createBody, 'transaction(');
    t($hashAt !== false && $txAt !== false && $hashAt < $txAt,
        'create hashes the password outside the write transaction');

    // --- 5. Global uniqueness. The same canonical login is ONE account, even
    //         when a second attempt would have received its own workspace.
    $conflictLogin = loginFor('conflict');
    $first = makeAccount($accounts, $conflictLogin, BFixture::password(), null,
        'member', $ids);
    $beforeAccounts = accountCount($app);
    $beforeTenants = tenantCount($app);
    foreach (['case' => strtoupper($conflictLogin),
              'padded' => '   ' . $conflictLogin . "	",
              'bare' => $conflictLogin] as $what => $variant) {
        $code = codeOf(static fn () => $accounts->create(
            $variant, BFixture::password(), null, 'member', NOW), $what);
        t($code === 'ACCOUNT_LOGIN_CONFLICT',
            $what . ': the same canonical login is one global uniqueness conflict');
    }
    t(accountCount($app) === $beforeAccounts,
        'a refused duplicate creates no second account');
    t(tenantCount($app) === $beforeTenants,
        'a refused duplicate creates no automatic second workspace (no merge)');
    t((int) scalar($app, 'SELECT COUNT(*) FROM accounts WHERE login = ?',
        [$conflictLogin]) === 1,
        'the login column holds exactly one row for the canonical login');
    t(count(membershipsOf($app, (string) $first['account_id'])) === 1,
        'the surviving account keeps exactly its own private workspace');

    // --- 6. The race: two connections attempt the SAME canonical login. One
    //         commits, the other conflicts, and no orphan workspace survives.
    $raceBeforeAccounts = accountCount($app);
    $raceBeforeTenants = tenantCount($app);
    $child = startRaceChild();
    t(waitForFile($child['directory'] . DIRECTORY_SEPARATOR . 'ready', 30),
        'the race child must be ready to attempt the create');
    $parentOutcome = ['ok' => false, 'error' => 'FIXTURE_SCENARIO_FAILED'];
    try {
        $parentMade = $accounts->create(RACE_LOGIN, BFixture::password(), null,
            'member', NOW);
        $parentOutcome = ['ok' => true, 'account_id' => (string) $parentMade['account_id'],
                          'workspace_id' => (string) $parentMade['workspace_id']];
    } catch (ApiException $error) {
        $parentOutcome = ['ok' => false, 'error' => $error->errorCode];
    }
    $childOutcome = finishRaceChild($child);
    foreach ([$parentOutcome, $childOutcome] as $outcome) {
        if (isset($outcome['account_id'])) {
            $ids['accounts'][] = (string) $outcome['account_id'];
        }
        if (isset($outcome['workspace_id'])) {
            $ids['workspaces'][] = (string) $outcome['workspace_id'];
        }
    }
    t(($parentOutcome['ok'] ?? false) === true || ($childOutcome['ok'] ?? false) === true,
        'at least one of the two racing attempts must commit');
    t(!(($parentOutcome['ok'] ?? false) === true
        && ($childOutcome['ok'] ?? false) === true),
        'both racing attempts can never both commit on one canonical login');
    foreach (['parent' => $parentOutcome, 'child' => $childOutcome] as $who => $outcome) {
        if (($outcome['ok'] ?? false) !== true) {
            t(($outcome['error'] ?? '') === 'ACCOUNT_LOGIN_CONFLICT',
                $who . ': the losing attempt is ACCOUNT_LOGIN_CONFLICT,'
                . ' never a raw database message');
        }
    }
    t(accountCount($app) === $raceBeforeAccounts + 1,
        'the race yields exactly one account');
    t(tenantCount($app) === $raceBeforeTenants + 1,
        'the race yields exactly one workspace — the loser leaves no orphan');
    t((int) scalar($app, 'SELECT COUNT(*) FROM accounts WHERE login = ?',
        [RACE_LOGIN]) === 1, 'exactly one row holds the raced canonical login');
    t((int) scalar($app,
        'SELECT COUNT(*) FROM account_workspace_memberships m'
        . ' INNER JOIN accounts a ON a.id = m.account_id WHERE a.login = ?',
        [RACE_LOGIN]) === 1,
        'the raced account holds exactly one membership');

    // --- 7. Display names: valid UTF-8, 120 UNICODE code points, never an
    //         identity column.
    $atLimit = str_repeat('م', 120);
    $overLimit = str_repeat('م', 121);
    $named = makeAccount($accounts, loginFor('name'), BFixture::password(), $atLimit,
        'member', $ids);
    $storedName = (string) scalar($app, 'SELECT display_name FROM accounts WHERE id = ?',
        [(string) $named['account_id']]);
    t($storedName === $atLimit && mb_strlen($storedName, 'UTF-8') === 120,
        'a 120-code-point display name is stored unchanged');
    $truncated = makeAccount($accounts, loginFor('name.over'), BFixture::password(),
        $overLimit, 'member', $ids);
    $boundedName = (string) scalar($app,
        'SELECT display_name FROM accounts WHERE id = ?',
        [(string) $truncated['account_id']]);
    t(mb_strlen($boundedName, 'UTF-8') === 120,
        'a display name over the limit is bounded to 120 UNICODE code points, not bytes');
    t(mb_check_encoding($boundedName, 'UTF-8'),
        'a bounded multibyte display name stays valid UTF-8 (never cut mid-character)');
    $defaulted = makeAccount($accounts, loginFor('name.empty'), BFixture::password(),
        null, 'member', $ids);
    t((string) scalar($app, 'SELECT display_name FROM accounts WHERE id = ?',
        [(string) $defaulted['account_id']]) === '',
        'an omitted display name stores the empty default');
    foreach (['invalid-utf8' => "\xC3\x28name", 'nul' => "na\0me"] as $what => $name) {
        t(codeOf(static fn () => $accounts->create(loginFor('name.bad'),
            BFixture::password(), $name, 'member', NOW), $what) === 'VALIDATION_FAILED',
            $what . ': an invalid display name is VALIDATION_FAILED');
    }
    // The display name is never record identity: two accounts may share one.
    $shared = makeAccount($accounts, loginFor('name.dup'), BFixture::password(),
        $atLimit, 'member', $ids);
    t((string) scalar($app, 'SELECT display_name FROM accounts WHERE id = ?',
        [(string) $shared['account_id']]) === $atLimit,
        'a display name is not unique and never identifies a record');

    // --- 8. Password reset by ACCOUNT UUID only.
    $resetLogin = loginFor('reset');
    $resetPassword = BFixture::password();
    $reset = makeAccount($accounts, $resetLogin, $resetPassword, null, 'member', $ids);
    $resetId = (string) $reset['account_id'];
    $otherPassword = BFixture::password();
    $other = makeAccount($accounts, loginFor('reset.other'), $otherPassword,
        'Other Account', 'member', $ids);
    $otherId = (string) $other['account_id'];

    // A live session row for each account, at the CURRENT security version.
    $sessionIds = [];
    foreach ([$resetId, $otherId] as $accountIdForSession) {
        $sessionId = uuid();
        $sessionIds[$accountIdForSession] = $sessionId;
        $app->insert('account_sessions', [
            'id' => $sessionId,
            'account_id' => $accountIdForSession,
            'token_hash' => bin2hex(random_bytes(32)),
            'purpose' => 'desktop',
            'security_version' => (int) scalar($app,
                'SELECT security_version FROM accounts WHERE id = ?', [$accountIdForSession]),
            'issued_at' => gmdate('Y-m-d H:i:s', NOW),
            'idle_expires_at' => gmdate('Y-m-d H:i:s', NOW + 1800),
            'absolute_expires_at' => gmdate('Y-m-d H:i:s', NOW + 28800),
            'last_activity_at' => gmdate('Y-m-d H:i:s', NOW),
            'revoked_at' => null,
        ]);
    }

    $resetVersionBefore = (int) scalar($app,
        'SELECT security_version FROM accounts WHERE id = ?', [$resetId]);
    $otherBefore = accountFacts($app, $otherId);
    $otherMembershipsBefore = membershipsOf($app, $otherId);
    $newPassword = BFixture::password();
    $oldHash = (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$resetId]);
    $receipt = $accounts->resetPassword($resetId, $newPassword, NOW);
    t(($receipt['account_id'] ?? '') === $resetId,
        'the reset receipt names the account UUID and nothing else');
    t(($receipt['must_change_password'] ?? null) === true,
        'a reset forces a password change on the next sign-in');
    $afterReset = accountFacts($app, $resetId);
    t((int) $afterReset['security_version'] === $resetVersionBefore + 1,
        'a reset increments security_version');
    t((int) $afterReset['must_change_password'] === 1,
        'a reset sets must_change_password = 1');
    t($afterReset['login'] === $resetLogin, 'a reset never renames the account');
    $newHash = (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$resetId]);
    t($newHash !== $oldHash, 'a reset replaces the stored hash');
    t(password_verify($newPassword, $newHash) === true,
        'the new hash verifies the new password');
    t(password_verify($resetPassword, $newHash) === false,
        'the old password no longer verifies after a reset');

    // Authorization is by VERSION COMPARISON, without scanning session rows.
    $sessionVersion = (int) scalar($app,
        'SELECT security_version FROM account_sessions WHERE id = ?',
        [$sessionIds[$resetId]]);
    t($sessionVersion !== (int) $afterReset['security_version'],
        'an old session is stale by version comparison alone');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND revoked_at IS NULL AND security_version = ?',
        [$resetId, (int) $afterReset['security_version']]) === 0,
        'no session still claims the new security version');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND revoked_at IS NOT NULL', [$resetId]) >= 1,
        'bounded housekeeping revokes the physical rows as well');

    // Account B is untouched by every field that identifies or authenticates it.
    t(accountFacts($app, $otherId) === $otherBefore,
        "resetting account A never changes account B's row");
    t(membershipsOf($app, $otherId) === $otherMembershipsBefore,
        "resetting account A never changes account B's memberships");
    t(password_verify($otherPassword, (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$otherId])) === true,
        "resetting account A never changes account B's password");
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND revoked_at IS NULL', [$otherId]) === 1,
        "resetting account A never revokes account B's sessions");

    // The reset writes its security event inside the same transaction and the
    // event carries no credential material.
    t((int) scalar($app,
        'SELECT COUNT(*) FROM account_security_events WHERE subject_account_id = ?'
        . " AND event_type = 'password_reset' AND outcome = 'success'", [$resetId]) === 1,
        'a reset records exactly one password_reset security event');
    t(statusOf(static fn () => $accounts->resetPassword(
        uuid(), BFixture::password(), NOW), 404, 'unknown reset target') === 'NOT_FOUND',
        'a reset of an unknown account UUID is NOT_FOUND');

    // --- 9. setStatus: closed vocabulary and a logical session invalidation.
    $disabled = makeAccount($accounts, loginFor('status'), BFixture::password(),
        null, 'member', $ids);
    $disabledId = (string) $disabled['account_id'];
    $app->insert('account_sessions', [
        'id' => uuid(), 'account_id' => $disabledId,
        'token_hash' => bin2hex(random_bytes(32)), 'purpose' => 'desktop',
        'security_version' => 1, 'issued_at' => gmdate('Y-m-d H:i:s', NOW),
        'idle_expires_at' => gmdate('Y-m-d H:i:s', NOW + 1800),
        'absolute_expires_at' => gmdate('Y-m-d H:i:s', NOW + 28800),
        'last_activity_at' => gmdate('Y-m-d H:i:s', NOW), 'revoked_at' => null,
    ]);
    $disabledReceipt = $accounts->setStatus($disabledId, 'disabled', NOW);
    t(($disabledReceipt['status'] ?? '') === 'disabled', 'disable is durable');
    t((int) scalar($app, 'SELECT security_version FROM accounts WHERE id = ?',
        [$disabledId]) === 2,
        'disabling increments security_version, so live sessions die by comparison');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_sessions WHERE account_id = ?'
        . ' AND security_version = 2', [$disabledId]) === 0,
        'no session row claims the post-disable security version');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_security_events'
        . " WHERE subject_account_id = ? AND event_type = 'account_disabled'",
        [$disabledId]) === 1, 'disabling records an account_disabled security event');
    $accounts->setStatus($disabledId, 'active', NOW);
    t((string) scalar($app, 'SELECT status FROM accounts WHERE id = ?',
        [$disabledId]) === 'active', 'an account can be re-enabled');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_security_events'
        . " WHERE subject_account_id = ? AND event_type = 'account_enabled'",
        [$disabledId]) === 1, 're-enabling records an account_enabled security event');
    t(statusOf(static fn () => $accounts->setStatus(uuid(), 'disabled', NOW), 404,
        'unknown status target') === 'NOT_FOUND',
        'a status change on an unknown account is NOT_FOUND');

    // --- 10. seedFirstOperator and the last-active-operator protection.
    $identityBefore = $app->queryOne(
        'SELECT first_operator_id, row_version FROM identity_state WHERE id = 1');
    t($identityBefore !== null, 'the identity_state singleton row exists');
    $ids['identity_state'] = ['row_version' => (int) $identityBefore['row_version']];
    $seeded = $accounts->seedFirstOperator(loginFor('operator.seed'),
        BFixture::password(), 'Synthetic First Operator', NOW);
    $seedId = (string) $seeded['account_id'];
    $ids['accounts'][] = $seedId;
    $ids['workspaces'][] = (string) $seeded['workspace_id'];
    t((string) scalar($app, 'SELECT platform_role FROM accounts WHERE id = ?',
        [$seedId]) === 'operator', 'seedFirstOperator creates a platform operator');
    t((string) scalar($app, 'SELECT first_operator_id FROM identity_state WHERE id = 1')
        === $seedId, 'seedFirstOperator records the first operator on identity_state');
    t((int) scalar($app, 'SELECT row_version FROM identity_state WHERE id = 1')
        === (int) $identityBefore['row_version'] + 1,
        'seedFirstOperator bumps identity_state.row_version under its lock');
    t(($seeded['must_change_password'] ?? null) === true,
        'the seeded operator must change its password before anything else');
    $seedKeys = array_keys($seeded);
    t(!in_array('password', $seedKeys, true) && !in_array('password_hash', $seedKeys, true)
        && !in_array('hash', $seedKeys, true) && !in_array('token', $seedKeys, true),
        'the seed receipt never carries a password, hash or token');
    t(count(membershipsOf($app, $seedId)) === 1,
        'the seeded operator holds only its own private workspace');

    // Reseeding is refused and creates nothing.
    $before = [accountCount($app), tenantCount($app)];
    t(statusOf(static fn () => $accounts->seedFirstOperator(
        loginFor('operator.reseed'), BFixture::password(), null, NOW), 409,
        'a second first-operator seed') === 'VERSION_CONFLICT',
        'a second first-operator seed is refused');
    t([accountCount($app), tenantCount($app)] === $before,
        'a refused reseed creates no account and no workspace');

    // The single operator may neither be disabled nor demoted.
    t(statusOf(static fn () => $accounts->setStatus($seedId, 'disabled', NOW), 409,
        'disabling the last operator') === 'LAST_OPERATOR_REQUIRED',
        'the last active operator may not be disabled');
    t(statusOf(static fn () => $accounts->setPlatformRole($seedId, 'member', NOW), 409,
        'demoting the last operator') === 'LAST_OPERATOR_REQUIRED',
        'the last active operator may not be demoted');
    t((string) scalar($app, 'SELECT platform_role FROM accounts WHERE id = ?',
        [$seedId]) === 'operator',
        'a refused demotion leaves the platform role unchanged');
    t((string) scalar($app, 'SELECT status FROM accounts WHERE id = ?',
        [$seedId]) === 'active', 'a refused disable leaves the account active');

    // A second operator makes both operations legal, and the survivor is then
    // the protected one.
    $secondOperator = $accounts->create(loginFor('operator.second'),
        BFixture::password(), 'Synthetic Operator Two', 'operator', NOW);
    $secondId = (string) $secondOperator['account_id'];
    $ids['accounts'][] = $secondId;
    $ids['workspaces'][] = (string) $secondOperator['workspace_id'];
    $accounts->setPlatformRole($seedId, 'member', NOW);
    t((string) scalar($app, 'SELECT platform_role FROM accounts WHERE id = ?',
        [$seedId]) === 'member', 'one of two operators may be demoted');
    t(statusOf(static fn () => $accounts->setPlatformRole($secondId, 'member', NOW), 409,
        'demoting the surviving operator') === 'LAST_OPERATOR_REQUIRED',
        'the surviving operator is now the protected one');
    t(statusOf(static fn () => $accounts->setStatus($secondId, 'disabled', NOW), 409,
        'disabling the surviving operator') === 'LAST_OPERATOR_REQUIRED',
        'the surviving operator may not be disabled either');
    $accounts->setPlatformRole($seedId, 'operator', NOW);
    $accounts->setStatus($secondId, 'disabled', NOW);
    t((string) scalar($app, 'SELECT status FROM accounts WHERE id = ?',
        [$secondId]) === 'disabled',
        'with two operators, disabling one is allowed');
    t(statusOf(static fn () => $accounts->setStatus($seedId, 'disabled', NOW), 409,
        'disabling the last remaining operator') === 'LAST_OPERATOR_REQUIRED',
        'the last remaining operator is still protected');

    // The serialization is on identity_state BEFORE the account lock.
    $accountsSource = file_get_contents($repoRoot . '/host/src/Accounts.php');
    foreach (['setStatus', 'setPlatformRole'] as $method) {
        $start = (int) strpos($accountsSource, 'function ' . $method . '(');
        t($start > 0, $method . ' exists in Accounts');
        $body = substr($accountsSource, $start, 4000);
        $stateLock = strpos($body, 'lockIdentityState(');
        $accountLock = strpos($body, 'FROM accounts');
        t($stateLock !== false && $accountLock !== false && $stateLock < $accountLock,
            $method . ' serializes on identity_state BEFORE locking the account');
    }

    // --- 11. Reviewed legacy linkage.
    $legacyA = legacyPair($app, $ids, 'a');
    $legacyB = legacyPair($app, $ids, 'b');
    $legacyC = legacyPair($app, $ids, 'c');

    // A target that is NOT prepared (must_change_password = 0) is refused and
    // left completely unchanged.
    $unrestrictedPassword = BFixture::password();
    $unrestricted = makeAccount($accounts, loginFor('legacy.unrestricted'),
        $unrestrictedPassword, null, 'member', $ids);
    $unrestrictedId = (string) $unrestricted['account_id'];
    $app->execute('UPDATE accounts SET must_change_password = 0 WHERE id = ?',
        [$unrestrictedId]);
    $unrestrictedBefore = accountFacts($app, $unrestrictedId);
    $reviewId = uuid();
    t(statusOf(static fn () => $accounts->linkLegacy($legacyA['tenant_id'],
        $legacyA['user_id'], $unrestrictedId, $reviewId, NOW), 422,
        'an unrestricted linkage target') === 'VALIDATION_FAILED',
        'an unrestricted (must_change_password = 0) target is refused');
    t(accountFacts($app, $unrestrictedId) === $unrestrictedBefore,
        'a refused linkage changes nothing on the target account');
    t(password_verify($unrestrictedPassword, (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$unrestrictedId])) === true,
        'a refused linkage never touches the target credential');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE account_id = ?',
        [$unrestrictedId]) === 0, 'a refused linkage writes no mapping row');
    t(count(membershipsOf($app, $unrestrictedId)) === 1,
        'a refused linkage grants no membership');

    // Explicit reset through the reviewed service, then link.
    $accounts->resetPassword($unrestrictedId, BFixture::password(), NOW);
    t((int) scalar($app, 'SELECT must_change_password FROM accounts WHERE id = ?',
        [$unrestrictedId]) === 1,
        'the reviewed reset prepares the target with must_change_password = 1');
    $linked = $accounts->linkLegacy($legacyA['tenant_id'], $legacyA['user_id'],
        $unrestrictedId, $reviewId, NOW);
    t(($linked['tenant_id'] ?? '') === $legacyA['tenant_id']
        && ($linked['legacy_user_id'] ?? '') === $legacyA['user_id']
        && ($linked['account_id'] ?? '') === $unrestrictedId
        && ($linked['review_id'] ?? '') === $reviewId,
        'a reviewed linkage records the exact composite key, account and review');
    t(($linked['confirmed_at'] ?? '') !== '', 'a reviewed linkage records confirmed_at');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE tenant_id = ?'
        . ' AND legacy_user_id = ? AND account_id = ? AND review_id = ?',
        [$legacyA['tenant_id'], $legacyA['user_id'], $unrestrictedId, $reviewId]) === 1,
        'the mapping row is durable with its review provenance');
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_security_events'
        . " WHERE subject_account_id = ? AND event_type = 'legacy_linked'"
        . ' AND tenant_id = ?', [$unrestrictedId, $legacyA['tenant_id']]) === 1,
        'a reviewed linkage records one legacy_linked security event');
    t(count(membershipsOf($app, $unrestrictedId)) === 1,
        'linkage never grants a membership by inferred username');
    t((int) scalar($app, 'SELECT COUNT(*) FROM memberships WHERE tenant_id = ?'
        . ' AND user_id = ?', [$legacyA['tenant_id'], $legacyA['user_id']]) === 0,
        'the legacy user row itself is not mirrored into a new membership');
    t($unrestrictedBefore['login'] === (string) scalar($app,
        'SELECT login FROM accounts WHERE id = ?', [$unrestrictedId]),
        'linkage never renames the account from the legacy username');
    t((string) scalar($app, 'SELECT password_hash FROM users WHERE id = ?',
        [$legacyA['user_id']]) !== (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$unrestrictedId]),
        'linkage never copies an old password hash');

    // A conflicting existing map is refused and left untouched.
    $conflictTarget = makeAccount($accounts, loginFor('legacy.target'),
        BFixture::password(), null, 'member', $ids);
    $conflictTargetId = (string) $conflictTarget['account_id'];
    $existingMap = $app->queryOne(
        'SELECT account_id, review_id FROM legacy_account_map'
        . ' WHERE tenant_id = ? AND legacy_user_id = ?',
        [$legacyA['tenant_id'], $legacyA['user_id']]);
    t($existingMap !== null && $existingMap['account_id'] === $unrestrictedId,
        'the fixture mapping exists before the conflicting attempt');
    t(statusOf(static fn () => $accounts->linkLegacy($legacyA['tenant_id'],
        $legacyA['user_id'], $conflictTargetId, uuid(), NOW), 409,
        'a conflicting existing map') === 'VERSION_CONFLICT',
        'a conflicting existing mapping is refused');
    t($app->queryOne('SELECT account_id, review_id FROM legacy_account_map'
        . ' WHERE tenant_id = ? AND legacy_user_id = ?',
        [$legacyA['tenant_id'], $legacyA['user_id']]) === $existingMap,
        'a refused conflicting linkage leaves the existing mapping unchanged');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE account_id = ?',
        [$conflictTargetId]) === 0,
        'a refused conflicting linkage writes no row for the other target');

    // ONE account may hold ONE legacy identity. A second, independently
    // reviewed and correctly prepared link — a different legacy user, a fresh
    // review id, a target that still carries must_change_password = 1 — onto
    // an ALREADY-LINKED account is refused, and the first mapping row survives
    // byte for byte. Anything else is the inferred identity merge Phase B
    // exists to prevent: two legacy credentials would then authenticate as one
    // central account.
    $takenRow = $app->queryOne(
        'SELECT tenant_id, legacy_user_id, account_id, review_id, confirmed_at'
        . ' FROM legacy_account_map WHERE account_id = ?', [$unrestrictedId]);
    t($takenRow !== null && $takenRow['legacy_user_id'] === $legacyA['user_id'],
        'the fixture account already holds the first reviewed mapping');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE account_id = ?',
        [$unrestrictedId]) === 1, 'the account starts with exactly one mapping row');
    t((int) scalar($app, 'SELECT must_change_password FROM accounts WHERE id = ?',
        [$unrestrictedId]) === 1,
        'the already-linked target is still a correctly prepared link target');
    t(statusOf(static fn () => $accounts->linkLegacy($legacyC['tenant_id'],
        $legacyC['user_id'], $unrestrictedId, uuid(), NOW), 409,
        'a second legacy identity onto an already-linked account')
        === 'ACCOUNT_ALREADY_LINKED',
        'a second reviewed link onto an already-linked account is refused');
    t($app->queryOne(
        'SELECT tenant_id, legacy_user_id, account_id, review_id, confirmed_at'
        . ' FROM legacy_account_map WHERE account_id = ?', [$unrestrictedId]) === $takenRow,
        'the refused second link leaves the first mapping row byte-identical');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE account_id = ?',
        [$unrestrictedId]) === 1,
        'the refused second link adds no mapping row for the account');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE tenant_id = ?'
        . ' AND legacy_user_id = ?', [$legacyC['tenant_id'], $legacyC['user_id']]) === 0,
        'the refused second link maps nothing for the second legacy user');

    // A wrong-tenant user is refused: the composite key must name a REAL
    // (tenant, legacy user) pair, never a username inference.
    $wrongTarget = makeAccount($accounts, loginFor('legacy.wrong'),
        BFixture::password(), null, 'member', $ids);
    $wrongTargetId = (string) $wrongTarget['account_id'];
    t(statusOf(static fn () => $accounts->linkLegacy($legacyA['tenant_id'],
        $legacyB['user_id'], $wrongTargetId, uuid(), NOW), 404,
        'a user that belongs to another tenant') === 'NOT_FOUND',
        'a legacy user that belongs to another tenant is refused');
    t(statusOf(static fn () => $accounts->linkLegacy($legacyA['tenant_id'],
        uuid(), $wrongTargetId, uuid(), NOW), 404, 'an unknown legacy user')
        === 'NOT_FOUND', 'an unknown legacy user is refused');
    t(statusOf(static fn () => $accounts->linkLegacy(uuid(), $legacyA['user_id'],
        $wrongTargetId, uuid(), NOW), 404, 'an unknown tenant') === 'NOT_FOUND',
        'an unknown tenant is refused');
    t(statusOf(static fn () => $accounts->linkLegacy('not-a-uuid',
        $legacyA['user_id'], $wrongTargetId, uuid(), NOW), 422, 'a malformed tenant id')
        === 'VALIDATION_FAILED', 'a malformed composite key is VALIDATION_FAILED');
    t(statusOf(static fn () => $accounts->linkLegacy($legacyB['tenant_id'],
        $legacyB['user_id'], $wrongTargetId, 'not-a-uuid', NOW), 422,
        'a malformed review id') === 'VALIDATION_FAILED',
        'a review id that is not a UUID is VALIDATION_FAILED');
    t(statusOf(static fn () => $accounts->linkLegacy($legacyB['tenant_id'],
        $legacyB['user_id'], uuid(), uuid(), NOW), 404, 'an unknown account')
        === 'NOT_FOUND', 'an unknown target account is refused');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE account_id = ?',
        [$wrongTargetId]) === 0,
        'every refused linkage writes no mapping row');

    // A valid, prepared target links for the second legacy pair, with no
    // membership granted anywhere and the credential untouched.
    $prepared = makeAccount($accounts, loginFor('legacy.prepared'),
        BFixture::password(), null, 'member', $ids);
    $preparedId = (string) $prepared['account_id'];
    $preparedHash = (string) scalar($app,
        'SELECT password_hash FROM accounts WHERE id = ?', [$preparedId]);
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE account_id = ?',
        [$preparedId]) === 0,
        'the positive control targets an account that has never been linked');
    $secondReview = uuid();
    $secondLink = $accounts->linkLegacy($legacyB['tenant_id'], $legacyB['user_id'],
        $preparedId, $secondReview, NOW);
    t(($secondLink['review_id'] ?? '') === $secondReview,
        'the second reviewed linkage stores its own review id');
    t((int) scalar($app, 'SELECT COUNT(*) FROM legacy_account_map WHERE account_id = ?'
        . ' AND tenant_id = ? AND legacy_user_id = ? AND review_id = ?',
        [$preparedId, $legacyB['tenant_id'], $legacyB['user_id'], $secondReview]) === 1,
        'a genuinely new account still links for a genuinely new legacy identity');
    t((string) scalar($app, 'SELECT password_hash FROM accounts WHERE id = ?',
        [$preparedId]) === $preparedHash,
        'linkage never changes the account credential (no legacy hash copy)');
    t(count(membershipsOf($app, $preparedId)) === 1,
        'linkage leaves the account with only its own private workspace');

    // --- 12. Membership assignment stays a SEPARATE explicit operation.
    $assignee = makeAccount($accounts, loginFor('member.explicit'),
        BFixture::password(), null, 'member', $ids);
    $assigneeId = (string) $assignee['account_id'];
    t(count(membershipsOf($app, $assigneeId)) === 1,
        'account creation grants membership only in its own private workspace');
    $grant = $workspaces->assign($unrestrictedId, (string) $unrestricted['workspace_id'],
        $assigneeId, 'user', 1);
    t(($grant['role'] ?? '') === 'user',
        'workspace membership is granted by an explicit, separate operation');
    t(count(membershipsOf($app, $assigneeId)) === 2,
        'the explicit grant is the only way to add a second membership');

    // --- 13. Structural regression for the two tenant-creation sites.
    assertTenantContractStated('host/public/setup.php');
    assertTenantContractStated('host/bin/create-admin.php');

    // The guard itself must be unsatisfiable by anything that is not a REAL
    // array element: a comment and a string value both spelling out the exact
    // quoted key form must leave the check empty, while a genuine element is
    // still read even with a decoy comment next to it.
    $decoyLiteral = <<<'PHP'
            $db->insert('tenants', [
                'name' => 'IRICA', // was 'lifecycle' => 'active'
                /* and 'api_contract' => 'legacy_v1' spelled out right here */
                'note' => "'api_contract' => 'legacy_v1'",
            PHP;
    t(tenantContractValues($decoyLiteral, 'lifecycle') === [],
        'a comment carrying the lifecycle key form cannot satisfy the structural check');
    t(tenantContractValues($decoyLiteral, 'api_contract') === [],
        'a comment carrying the api_contract key form cannot satisfy the structural check');
    t(tenantContractValues($decoyLiteral, 'note')
        === ["'api_contract' => 'legacy_v1'"],
        'a string VALUE is read as data, never as an element key');
    $realLiteral = <<<'PHP'
            $db->insert('tenants', [
                // a decoy comment: api_contract => legacy_v1, unquoted
                'lifecycle' => 'active',
                'api_contract' => 'legacy_v1',
            PHP;
    t(tenantContractValues($realLiteral, 'lifecycle') === ['active'],
        'a real array element still satisfies the structural check');
    t(tenantContractValues($realLiteral, 'api_contract') === ['legacy_v1'],
        'a real array element still satisfies the structural check');
} finally {
    clearSynthetic($app ?? BFixture::db(), $ids);
}

print("ACCOUNTS_SCENARIO_OK accounts=" . count($ids['accounts'])
    . " workspaces=" . count($ids['workspaces'])
    . " engine=" . BFixture::db()->serverVersion() . "\n");
