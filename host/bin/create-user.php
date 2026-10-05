<?php
/**
 * create-user.php — create one additional account (spec §15 ruling 17).
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §7 (two roles, one shared tenant),
 * §6 (no user-management endpoint — accounts are made here, never over
 * HTTP) and §10.1 (no seeded/default credential anywhere in this package).
 *
 *   php bin/create-user.php --username=amir               # generated password
 *   php bin/create-user.php --username=amir --role=user \
 *        --password='…'                                   # operator-chosen
 *   IRICA_USER_PASSWORD='…' php bin/create-user.php --username=amir
 *
 * `create-admin.php` stays single-use for the FIRST administrator: it
 * bootstraps the tenant and refuses a second run. This script is the
 * supported way to add every account after it — one per colleague, each
 * with their own username, so several people can log in from any machine.
 * Password rotation stays with
 * `create-admin.php --username=… --reset-password` (spec §15 ruling 17).
 *
 * The role lives on the membership row, never on `users` (spec §7 and the
 * schema's own comment): a user-tenant pair has a role, a user does not.
 *
 * Only the one-time plaintext password is ever printed; a hash never
 * leaves this process and nothing is written inside the repository.
 *
 * Exit codes: 0 success · 1 refusal or failure · 2 usage error.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
error_reporting(E_ALL);

define('HOST_ROOT', dirname(__DIR__));
foreach (['Schema', 'Database', 'Http', 'Ids', 'Config', 'Auth', 'Roles',
    'Tokens'] as $classFile) {
    require_once HOST_ROOT . '/src/' . $classFile . '.php';
}

$options = getopt('', [
    'username::', 'password::', 'display-name::', 'tenant-slug::',
    'role::', 'quiet', 'help',
]);
if ($options === false) {
    fwrite(STDERR, "usage: php bin/create-user.php --username=NAME "
        . "[--password=SECRET] [--display-name=NAME] [--tenant-slug=SLUG] "
        . "[--role=admin|user] [--quiet]\n");
    exit(2);
}
if (isset($options['help'])) {
    fwrite(STDOUT, "create-user.php — create one additional account\n"
        . "  --username=NAME       login name (required, unique per tenant)\n"
        . "  --password=SECRET     password; omit to generate and print once\n"
        . "  --display-name=NAME   shown in audit rows (no password, no PII)\n"
        . "  --tenant-slug=SLUG    tenant to join (default from config)\n"
        . "  --role=admin|user     role of the membership (default: user)\n"
        . "  --quiet               print only the outcome\n"
        . "Environment: IRICA_USER_PASSWORD is used when --password is absent.\n"
        . "The password is printed exactly once and never stored anywhere\n"
        . "else; record it now. The role is stored on the membership, so one\n"
        . "username may hold a different role in another tenant.\n"
        . "bin/create-admin.php stays single-use for the first admin and is\n"
        . "the rotation path (--reset-password) for an existing account.\n");
    exit(0);
}

$quiet = isset($options['quiet']);
function say(string $line, bool $quiet): void
{
    if (!$quiet) {
        fwrite(STDOUT, $line . PHP_EOL);
    }
}
function fail(string $line): never
{
    fwrite(STDERR, 'FAIL  ' . $line . PHP_EOL);
    exit(1);
}

// ---- arguments ---------------------------------------------------------
// Validated before the configuration is loaded: a usage error is a usage
// error, and it must not depend on a working database or a valid config.
$username = isset($options['username']) && is_string($options['username'])
    ? trim($options['username']) : '';
if ($username === '') {
    // No default is possible: a username is the identity of this command.
    fwrite(STDERR, "usage: php bin/create-user.php --username=NAME "
        . "[--password=SECRET] [--display-name=NAME] [--tenant-slug=SLUG] "
        . "[--role=admin|user] [--quiet]\n");
    exit(2);
}
if (strlen($username) > 64
    || preg_match('/[\x00-\x1f\x7f]/', $username) === 1) {
    fail('username must be 1-64 printable characters');
}
$role = isset($options['role']) && is_string($options['role'])
    ? $options['role'] : Roles::USER;
if (!in_array($role, Roles::ALL, true)) {
    fail('--role must be one of: ' . implode(', ', Roles::ALL));
}
$displayName = isset($options['display-name'])
    && is_string($options['display-name'])
    ? trim($options['display-name']) : $username;

try {
    $config = Config::load();
} catch (RuntimeException $error) {
    fail($error->getMessage());
}

$slug = isset($options['tenant-slug']) && is_string($options['tenant-slug'])
    ? trim($options['tenant-slug'])
    : (is_string($config['tenant_slug'] ?? null)
        && $config['tenant_slug'] !== '' ? $config['tenant_slug'] : 'default');

try {
    $db = Database::fromConfig(Config::db());
} catch (Throwable $error) {
    fail('cannot connect to the database (' . get_class($error) . ')');
}

$now = gmdate('Y-m-d H:i:s');

// ---- the tenant --------------------------------------------------------
// Unlike create-admin.php this script never creates a tenant: it adds an
// account to one that already exists. A mistyped --tenant-slug would
// otherwise seed a tenant the API cannot reach (Auth resolves the tenant
// from config), which is worse than a refusal.
$tenant = $db->queryOne('SELECT id FROM tenants WHERE slug = ?', [$slug]);
if ($tenant === null) {
    fail('no tenant with slug "' . $slug . '" exists. Run '
        . 'bin/create-admin.php first, or pass the right --tenant-slug.');
}
$tenantId = (string) $tenant['id'];

// ---- refuse a duplicate before touching the database -------------------
// uq_users_tenant_username covers soft-deleted rows, so this lookup
// deliberately does not filter on deleted_at: a retired username stays
// taken rather than silently resurrecting an old identity.
$existing = $db->queryOne(
    'SELECT id FROM users WHERE tenant_id = ? AND username = ? LIMIT 1',
    [$tenantId, $username]
);
if ($existing !== null) {
    fail('the username "' . $username . '" already exists in this tenant. '
        . 'Every colleague needs a username of their own — pick another '
        . 'one. To rotate this account\'s password use: php '
        . 'bin/create-admin.php --username=' . $username
        . ' --reset-password');
}

// ---- the password -------------------------------------------------------
$password = null;
$generated = false;
if (isset($options['password']) && is_string($options['password'])) {
    $password = $options['password'];
} else {
    $fromEnv = getenv('IRICA_USER_PASSWORD');
    if (is_string($fromEnv) && $fromEnv !== '') {
        $password = $fromEnv;
    }
}
if ($password === null) {
    // 24 URL-safe characters from the CSPRNG: no alphabet to misread, no
    // default that could ever ship in a file (spec §10.1).
    $password = rtrim(strtr(base64_encode(random_bytes(24)), '+/', '-_'), '=');
    $generated = true;
}
if (strlen($password) < 12) {
    fail('the password must be at least 12 characters');
}
if (strlen($password) > 4096) {
    fail('the password is unreasonably long');
}

$hash = '';
try {
    $hash = Auth::hashPassword($password);
} catch (Throwable $error) {
    fail('this PHP build cannot hash a password (' . get_class($error) . ')');
}

// ---- the account and its membership ------------------------------------
$userId = Ids::v4();
try {
    $db->transaction(function (Database $tx) use (
        $userId, $tenantId, $username, $hash, $displayName, $now, $role
    ): void {
        $tx->insert('users', [
            'id' => $userId,
            'tenant_id' => $tenantId,
            'username' => $username,
            'password_hash' => $hash,
            'display_name' => substr($displayName, 0, 120),
            'must_change_password' => 1,
            'status' => 'active',
            'token_version' => 1,
            'last_login_at' => null,
            'row_version' => 1,
            'created_at' => $now,
            'updated_at' => $now,
            'deleted_at' => null,
            'deleted_by' => null,
        ]);
        // The role belongs to this row, not to `users` (spec §7).
        $tx->insert('memberships', [
            'id' => Ids::v4(),
            'tenant_id' => $tenantId,
            'user_id' => $userId,
            'role' => $role,
            'created_at' => $now,
            'updated_at' => $now,
        ]);
    });
} catch (PDOException $error) {
    $info = $error->errorInfo ?? null;
    $errno = is_array($info) && isset($info[1]) ? (int) $info[1] : 0;
    if ($errno === 1062) {
        fail('that username already exists in this tenant');
    }
    fail('database error while creating the account (errno ' . $errno . ')');
}

fwrite(STDOUT, 'OK    user created' . PHP_EOL);
say('      username:      ' . $username, $quiet);
say('      display name:  ' . substr($displayName, 0, 120), $quiet);
say('      role:          ' . $role, $quiet);
say('      user id:       ' . $userId, $quiet);
say('      tenant:        ' . $slug . ' (' . $tenantId . ')', $quiet);
say('      must change:   yes', $quiet);
// Never suppressed: this is the only place the generated password exists.
if ($generated) {
    fwrite(STDOUT, PHP_EOL
        . 'PASSWORD (shown once, it is not stored anywhere else):' . PHP_EOL
        . '  ' . $password . PHP_EOL . PHP_EOL);
}
say('Store the password in your password manager now, then tell your '
    . 'colleague to sign in with this username.', $quiet);
exit(0);
