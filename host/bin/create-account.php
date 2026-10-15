<?php
/**
 * create-account.php — private CLI: create an account, never exposed to HTTP.
 *
 * Usage:
 *
 *     echo '{"kind":"member","login":"…","password":"…"}' \
 *         | php bin/create-account.php
 *
 * Kinds
 *   member           a member account plus its private workspace
 *   operator         a platform operator plus its private workspace
 *   first_operator   the FIRST platform operator, through the locked
 *                    `identity_state` bootstrap; refused once one exists
 *
 * Contract
 *   * CLI only. There is no SAPI in which this file serves a request.
 *   * Account parameters and the password arrive as ONE JSON object on STDIN,
 *     bounded at 16 KiB — never in argv, never in the environment. Passing any
 *     command-line argument at all is a usage error, so no shell history, no
 *     process listing and no error message can ever carry a credential.
 *   * Unknown fields are refused rather than ignored: a typo must never be
 *     silently dropped, and `role`/`purpose`/`workspace` have no meaning here.
 *   * The receipt on STDOUT names the identifiers and the forced-change flag,
 *     and nothing else: no login, no password, no hash, no role, no token.
 *   * It never delegates to create-admin.php and exposes no reset command.
 *
 * Exit codes: 0 success · 1 refusal or failure · 2 usage error.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
error_reporting(E_ALL);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "FAIL  this command runs only from the command line\n");
    exit(2);
}
if ($argc !== 1) {
    // Nothing about an account ever comes from argv (see the contract above).
    fwrite(STDERR, "FAIL  unexpected command-line arguments; supply one JSON"
        . " object on stdin\n");
    exit(2);
}

const MAX_STDIN_BYTES = 16384;

define('HOST_ROOT', dirname(__DIR__));
foreach (['Schema', 'Database', 'Http', 'Ids', 'Config', 'Log', 'Tokens',
    'Roles', 'RateLimit', 'Auth', 'Accounts', 'Workspaces'] as $classFile) {
    require_once HOST_ROOT . '/src/' . $classFile . '.php';
}

/** The fields each kind accepts. Anything else is a refusal, not a no-op. */
const FIELDS = [
    'member' => ['kind' => true, 'login' => true, 'password' => true,
        'display_name' => true],
    'operator' => ['kind' => true, 'login' => true, 'password' => true,
        'display_name' => true],
    'first_operator' => ['kind' => true, 'login' => true, 'password' => true,
        'display_name' => true],
];
const REQUIRED = ['kind', 'login', 'password'];

function fail(string $line, int $code = 1): never
{
    fwrite(STDERR, 'FAIL  ' . $line . PHP_EOL);
    exit($code);
}

/**
 * The missing `identity_state` singleton is post-install data loss, not a
 * caller mistake: say so, name the remedy, and never surface a stack trace.
 */
function diagnoseMissingIdentityState(): never
{
    fwrite(STDERR, "FAIL  SERVER_ERROR the identity state row is missing\n");
    fwrite(STDERR, "  This installation has no identity state row. Only data loss\n");
    fwrite(STDERR, "  after a successful install can cause that. Restore the\n");
    fwrite(STDERR, "  database from a backup taken after setup, confirm the row is\n");
    fwrite(STDERR, "  present, then run this command again. Do not re-run setup and\n");
    fwrite(STDERR, "  do not seed an operator by hand.\n");
    exit(1);
}

// ---------------------------------------------------------------------------
// 1. One JSON object on stdin, bounded. Nothing else is read.
// ---------------------------------------------------------------------------

$raw = file_get_contents('php://stdin', false, null, 0, MAX_STDIN_BYTES + 1);
if ($raw === false) {
    fail('the request could not be read from stdin', 2);
}
if (strlen($raw) > MAX_STDIN_BYTES) {
    fail('the request exceeds the ' . MAX_STDIN_BYTES . ' byte limit', 2);
}
try {
    $decoded = json_decode($raw, false, 32, JSON_BIGINT_AS_STRING
        | JSON_THROW_ON_ERROR);
} catch (JsonException $error) {
    unset($error);
    fail('the request is not valid JSON', 2);
}
if (!$decoded instanceof stdClass) {
    fail('the request must be a JSON object', 2);
}
/** @var array<string, mixed> $input */
$input = json_decode($raw, true, 32, JSON_BIGINT_AS_STRING);
if (!is_array($input)) {
    fail('the request must be a JSON object', 2);
}

$kind = isset($input['kind']) && is_string($input['kind']) ? $input['kind'] : '';
if ($kind === '' || !isset(FIELDS[$kind])) {
    fail('the request must name a supported kind', 1);
}
foreach (REQUIRED as $key) {
    if (!array_key_exists($key, $input)) {
        fail('the request is missing a required field', 1);
    }
}
foreach (array_keys($input) as $key) {
    if (!isset(FIELDS[$kind][$key])) {
        fail('the request carries a field this command does not accept', 1);
    }
}
$login = $input['login'];
$password = $input['password'];
$displayName = $input['display_name'] ?? null;
if (!is_string($login) || !is_string($password)
    || ($displayName !== null && !is_string($displayName))) {
    fail('the request fields must be strings', 1);
}

// ---------------------------------------------------------------------------
// 2. One connection, one receipt.
// ---------------------------------------------------------------------------

try {
    Config::load();
} catch (\RuntimeException $error) {
    fail('CONFIG_INVALID ' . $error->getMessage(), 1);
}

try {
    $db = Database::fromConfig(Config::db());
    $accounts = new Accounts($db);
    $now = time();
    $receipt = $kind === 'first_operator'
        ? $accounts->seedFirstOperator($login, $password, $displayName, $now)
        : $accounts->create($login, $password, $displayName,
            $kind === 'operator' ? Accounts::ROLE_OPERATOR : Accounts::ROLE_MEMBER,
            $now);
} catch (ApiException $error) {
    if ($error->status === 500
        && $error->errorCode === Codes::SERVER_ERROR
        && $error->getMessage() === 'the identity state row is missing') {
        diagnoseMissingIdentityState();
    }
    fail($error->errorCode . ' ' . $error->getMessage(), 1);
} catch (\PDOException $error) {
    unset($error);
    fail('DB_UNAVAILABLE the database is unavailable', 1);
} catch (\Throwable $error) {
    // A class name is safe and actionable; a message or a trace is not.
    fail('SERVER_ERROR ' . get_class($error), 1);
}

// Identifiers and the forced-change flag. Nothing else leaves this process.
fwrite(STDOUT, json_encode([
    'account_id' => (string) $receipt['account_id'],
    'workspace_id' => (string) $receipt['workspace_id'],
    'must_change_password' => (bool) $receipt['must_change_password'],
], JSON_UNESCAPED_SLASHES) . PHP_EOL);
exit(0);
