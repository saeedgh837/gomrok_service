<?php
/**
 * create-admin.php — create (or rotate) the first administrator.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §8.1 and §10.1. There is no seeded
 * user, no seeded hash and no default password anywhere in this package: the
 * password is either supplied on this command line, read from the environment,
 * or generated here and printed exactly once.
 *
 *   php bin/create-admin.php                        # generated password
 *   php bin/create-admin.php --username=admin \
 *        --password='…'                             # operator-chosen
 *   php bin/create-admin.php --username=admin --reset-password
 *
 * Single use: once an administrator exists this script refuses to create a
 * second one and says so. `--reset-password` is the supported way to rotate a
 * password later — it also bumps token_version, which invalidates every token
 * that account still holds (spec §5).
 *
 * The role goes on memberships, never on users (spec §15 / the schema's own
 * comment): a user-tenant pair has a role, a user does not.
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
    'role::', 'reset-password', 'quiet', 'help',
]);
if ($options === false) {
    fwrite(STDERR, "usage: php bin/create-admin.php [--username=NAME] "
        . "[--password=SECRET] [--display-name=NAME] [--tenant-slug=SLUG] "
        . "[--role=admin|user] [--reset-password] [--quiet]\n");
    exit(2);
}
if (isset($options['help'])) {
    fwrite(STDOUT, "create-admin.php — create or rotate the first administrator\n"
        . "  --username=NAME       login name (default: admin)\n"
        . "  --password=SECRET     password; omit to generate and print once\n"
        . "  --display-name=NAME   shown in audit rows (no password, no PII)\n"
        . "  --tenant-slug=SLUG    tenant to create/use (default from config)\n"
        . "  --role=admin|user     role of the membership (default: admin)\n"
        . "  --reset-password      rotate an EXISTING account instead of failing\n"
        . "  --quiet               print only the outcome\n"
        . "Environment: IRICA_ADMIN_PASSWORD is used when --password is absent.\n");
    exit(0);
}

$quiet = isset($options['quiet']);
function say(string $line, bool $quiet): void
{
    if (!$quiet) {
        fwrite(STDOUT, $line . PHP_EOL);
    }
}
function done(string $line): void
{
    fwrite(STDOUT, $line . PHP_EOL);
    exit(0);
}
function fail(string $line): never
{
    fwrite(STDERR, 'FAIL  ' . $line . PHP_EOL);
    exit(1);
}

try {
    $config = Config::load();
} catch (RuntimeException $error) {
    fail($error->getMessage());
}

$username = isset($options['username']) && is_string($options['username'])
    ? trim($options['username']) : 'admin';
if ($username === '' || strlen($username) > 64
    || preg_match('/[\x00-\x1f\x7f]/', $username) === 1) {
    fail('username must be 1-64 printable characters');
}
$role = isset($options['role']) && is_string($options['role'])
    ? $options['role'] : Roles::ADMIN;
if (!in_array($role, Roles::ALL, true)) {
    fail('--role must be one of: ' . implode(', ', Roles::ALL));
}
$slug = isset($options['tenant-slug']) && is_string($options['tenant-slug'])
    ? trim($options['tenant-slug'])
    : (is_string($config['tenant_slug'] ?? null)
        && $config['tenant_slug'] !== '' ? $config['tenant_slug'] : 'default');
$displayName = isset($options['display-name'])
    && is_string($options['display-name'])
    ? trim($options['display-name']) : $username;

try {
    $db = Database::fromConfig(Config::db());
} catch (Throwable $error) {
    fail('cannot connect to the database (' . get_class($error) . ')');
}

$now = gmdate('Y-m-d H:i:s');

// ---- the tenant ---------------------------------------------------------
$tenant = $db->queryOne('SELECT id, slug FROM tenants WHERE slug = ?', [$slug]);
if ($tenant === null) {
    $tenantId = Ids::v4();
    $db->insert('tenants', [
        'id' => $tenantId,
        'slug' => $slug,
        'name' => 'IRICA',
        'status' => 'active',
        'created_at' => $now,
        'updated_at' => $now,
    ]);
    say('created tenant ' . $slug, $quiet);
} else {
    $tenantId = (string) $tenant['id'];
}

$existing = $db->queryOne(
    'SELECT u.id AS user_id, u.username, u.status, m.role'
    . ' FROM memberships m JOIN users u ON u.id = m.user_id'
    . ' WHERE m.tenant_id = ? AND m.role = ? LIMIT 5',
    [$tenantId, Roles::ADMIN]
);
$reset = isset($options['reset-password']);

if ($existing !== null && !$reset) {
    fail('an administrator already exists in this tenant. This script is '
        . 'single-use (spec §8.1: no seeded account, no second seed). '
        . 'To rotate a password use: php bin/create-admin.php --username='
        . $existing['username'] . ' --reset-password');
}

// ---- the password -------------------------------------------------------
$password = null;
$generated = false;
if (isset($options['password']) && is_string($options['password'])) {
    $password = $options['password'];
} else {
    $fromEnv = getenv('IRICA_ADMIN_PASSWORD');
    if (is_string($fromEnv) && $fromEnv !== '') {
        $password = $fromEnv;
    }
}
if ($password === null) {
    // 24 URL-safe characters from the CSPRNG: no alphabet to misread, no
    // default that could ever ship in a file.
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

try {
    if ($existing !== null) {
        $userId = (string) $existing['user_id'];
        $db->transaction(function (Database $tx) use (
            $userId, $hash, $now, $role, $tenantId, $username
        ): void {
            $tx->update('users', [
                'username' => $username,
                'password_hash' => $hash,
                'must_change_password' => 1,
                'status' => 'active',
                // Every outstanding token of this account dies with the bump
                // (spec §5).
                'token_version' => (int) $tx->queryValue(
                    'SELECT token_version FROM users WHERE id = ? FOR UPDATE',
                    [$userId]
                ) + 1,
                'updated_at' => $now,
            ], 'id = ?', [$userId]);
            $tx->execute(
                'UPDATE auth_sessions SET revoked_at = ?,'
                . ' revoked_reason = ? WHERE user_id = ? AND revoked_at IS NULL',
                [$now, 'password_rotated', $userId]
            );
            // The membership is normally already there (it is how this user
            // was found); make the requested role stick either way.
            $updated = $tx->update('memberships', [
                'role' => $role, 'updated_at' => $now,
            ], 'tenant_id = ? AND user_id = ?', [$tenantId, $userId]);
            if ($updated === 0) {
                $tx->insert('memberships', [
                    'id' => Ids::v4(),
                    'tenant_id' => $tenantId,
                    'user_id' => $userId,
                    'role' => $role,
                    'created_at' => $now,
                    'updated_at' => $now,
                ]);
            }
        });
        done('OK    rotated the password of an existing administrator and '
            . 'revoked every token it still held. Username: ' . $username
            . ($generated ? PHP_EOL . 'PASSWORD (shown once): ' . $password
                : ''));
    }

    $userId = Ids::v4();
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

fwrite(STDOUT, 'OK    administrator created' . PHP_EOL);
fwrite(STDOUT, '      username:      ' . $username . PHP_EOL);
fwrite(STDOUT, '      role:          ' . $role . PHP_EOL);
fwrite(STDOUT, '      user id:       ' . $userId . PHP_EOL);
fwrite(STDOUT, '      tenant:        ' . $slug . ' (' . $tenantId . ')' . PHP_EOL);
fwrite(STDOUT, '      must change:   yes' . PHP_EOL);
if ($generated) {
    fwrite(STDOUT, PHP_EOL
        . 'PASSWORD (shown once, it is not stored anywhere else):' . PHP_EOL
        . '  ' . $password . PHP_EOL . PHP_EOL);
}
fwrite(STDOUT, 'Store the password in your password manager now.' . PHP_EOL);
fwrite(STDOUT, 'NEXT: remove this script — it has served its purpose:' . PHP_EOL
    . '  Windows:  del host\\bin\\create-admin.php' . PHP_EOL
    . '  Unix:     rm host/bin/create-admin.php' . PHP_EOL);
exit(0);
