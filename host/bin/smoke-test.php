<?php
/**
 * smoke-test.php — the first thing to run on the host after installation.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §2 (least privilege, loopback-only
 * MySQL), §10.6 (config outside public_html), §10.8 (CORS closed, HTTPS),
 * §13.1 (the same checks the local verification environment runs).
 *
 * It prints a plain PASS / WARN / FAIL report and exits non-zero when
 * anything FAILs. It never prints a password, a token or the signing key:
 * connection details are used, not echoed, and the only identifier shown is
 * whether a check passed.
 *
 *   php bin/smoke-test.php            # full report
 *   php bin/smoke-test.php --quiet    # failures and warnings only
 *
 * Exit codes: 0 everything passed (warnings allowed) · 1 at least one FAIL.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
error_reporting(E_ALL);

define('HOST_ROOT', dirname(__DIR__));
foreach (['Schema', 'Database', 'Http', 'Ids', 'Config'] as $classFile) {
    require_once HOST_ROOT . '/src/' . $classFile . '.php';
}

$quiet = in_array('--quiet', $argv, true);
$failures = 0;
$warnings = 0;

function report(string $status, string $text): void
{
    global $failures, $warnings, $quiet;
    if ($status === 'FAIL') {
        $failures++;
    } elseif ($status === 'WARN') {
        $warnings++;
    }
    if ($quiet && $status === 'PASS') {
        return;
    }
    fwrite(STDOUT, sprintf("%-4s  %s%s", $status, $text, PHP_EOL));
}

function finish(): never
{
    global $failures, $warnings;
    fwrite(STDOUT, str_repeat('-', 64) . PHP_EOL);
    if ($failures > 0) {
        fwrite(STDOUT, sprintf('Result: FAIL — %d failure(s), %d warning(s)%s',
            $failures, $warnings, PHP_EOL));
        exit(1);
    }
    fwrite(STDOUT, sprintf('Result: PASS — 0 failures, %d warning(s)%s',
        $warnings, PHP_EOL));
    exit(0);
}

fwrite(STDOUT, 'IRICA host smoke test' . PHP_EOL);
fwrite(STDOUT, str_repeat('-', 64) . PHP_EOL);

// ---------------------------------------------------------------------------
// 1. PHP itself
// ---------------------------------------------------------------------------
$version = PHP_VERSION;
if (version_compare($version, '8.1.0', '>=')) {
    report('PASS', 'PHP ' . $version . ' meets the 8.1 minimum');
} else {
    report('FAIL', 'PHP ' . $version . ' is older than 8.1 — the API will '
        . 'not run');
    finish();
}

$requiredExtensions = ['pdo_mysql', 'mbstring', 'json', 'hash'];
foreach ($requiredExtensions as $extension) {
    if (extension_loaded($extension)) {
        report('PASS', 'extension ' . $extension);
    } else {
        report('FAIL', 'extension ' . $extension . ' is missing (required)');
    }
}
foreach (['openssl', 'sodium', 'fileinfo'] as $extension) {
    if (extension_loaded($extension)) {
        report('PASS', 'extension ' . $extension);
    } else {
        report('WARN', 'extension ' . $extension . ' is missing (recommended)');
    }
}
if (in_array('argon2id', password_algos(), true)) {
    report('PASS', 'password hashing: argon2id available (spec §10.4)');
} else {
    report('WARN', 'argon2id is unavailable; passwords will fall back to '
        . 'PASSWORD_DEFAULT');
}

// ---------------------------------------------------------------------------
// 2. Configuration (must exist, must not still be the example)
// ---------------------------------------------------------------------------
$configPath = Config::locate();
if (!is_file($configPath)) {
    report('FAIL', 'configuration file is missing — copy '
        . 'config/config.example.php to config/config.php and fill it in');
    finish();
}
report('PASS', 'configuration file present (outside public_html)');

try {
    $config = Config::load();
    report('PASS', 'configuration is complete and holds no placeholder');
} catch (Throwable $error) {
    report('FAIL', 'configuration is not usable: ' . $error->getMessage());
    finish();
}

$examplePath = HOST_ROOT . '/config/config.example.php';
if (is_file($examplePath)
    && hash_file('sha256', $configPath) === hash_file('sha256', $examplePath)) {
    report('FAIL', 'configuration is still an unmodified copy of the example');
} else {
    report('PASS', 'configuration differs from the example');
}
if (Config::isUsableSecret((string) $config['jwt_secret'])) {
    report('PASS', 'token signing key is a real random value');
} else {
    report('FAIL', 'jwt_secret is the example placeholder — generate one with '
        . 'php -r "echo bin2hex(random_bytes(32));"');
}

// ---------------------------------------------------------------------------
// 3. The request log
// ---------------------------------------------------------------------------
$logPath = is_string($config['log_path'] ?? null) ? $config['log_path'] : '';
if ($logPath === '') {
    report('WARN', 'log_path is empty — request logs are disabled');
} else {
    $directory = dirname($logPath);
    if (!is_dir($directory)) {
        @mkdir($directory, 0750, true);
    }
    if (!is_dir($directory) || !is_writable($directory)) {
        report('FAIL', 'the log directory is not writable: '
            . basename($directory));
    } else {
        report('PASS', 'request log directory is writable (outside public_html)');
    }
}

// ---------------------------------------------------------------------------
// 4. The database, as the application account
// ---------------------------------------------------------------------------
try {
    $db = Database::fromConfig(Config::db());
} catch (Throwable $error) {
    report('FAIL', 'cannot connect with the application account ('
        . get_class($error) . ')');
    finish();
}
report('PASS', 'database reachable with the application account');

$serverVersion = $db->serverVersion();
// A version banner is operational data, not a secret; bound it so a hostile
// server string cannot flood the report.
report('PASS', 'server: ' . substr(preg_replace('/[^\w.\-]/', '',
    $serverVersion), 0, 48));

// ---------------------------------------------------------------------------
// 5. Schema version
// ---------------------------------------------------------------------------
$tracker = 'schema_migrations';
$hasTracker = (int) ($db->queryValue(
    'SELECT COUNT(*) FROM information_schema.TABLES'
    . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?', [$tracker]
) ?? 0) === 1;
if (!$hasTracker) {
    report('FAIL', 'the migration tracker does not exist — run '
        . 'php bin/migrate.php first');
} else {
    $row = $db->queryOne(
        'SELECT version, checksum FROM schema_migrations ORDER BY version'
    );
    if ($row === null) {
        report('FAIL', 'no migration has been recorded — run '
            . 'php bin/migrate.php first');
    } else {
        $version = (string) $row['version'];
        $file = HOST_ROOT . '/migrations/' . $version . '.sql';
        if (!is_file($file)) {
            report('FAIL', 'recorded migration ' . $version . ' has no file');
        } elseif (!hash_equals((string) $row['checksum'],
            (string) hash_file('sha256', $file))) {
            report('FAIL', 'recorded checksum for ' . $version
                . ' does not match the file');
        } else {
            report('PASS', 'schema version ' . $version
                . ' applied and checksum verified');
        }
    }
}

// ---------------------------------------------------------------------------
// 6. Least privilege: the app account must be DML-only
// ---------------------------------------------------------------------------
$privileges = [];
try {
    foreach ($db->query('SHOW GRANTS FOR CURRENT_USER()') as $grantRow) {
        foreach ($grantRow as $text) {
            if (!is_string($text)
                || preg_match('/^GRANT\s+(.+?)\s+ON\s+/is', $text, $m) !== 1) {
                continue;
            }
            foreach (explode(',', $m[1]) as $item) {
                $privileges[strtoupper(trim($item))] = true;
            }
        }
    }
} catch (Throwable $error) {
    report('WARN', 'grants could not be enumerated ('
        . get_class($error) . ')');
}
unset($privileges['USAGE']);

$required = ['SELECT', 'INSERT', 'UPDATE', 'DELETE'];
$missing = [];
foreach ($required as $privilege) {
    if (!isset($privileges[$privilege])) {
        $missing[] = $privilege;
    }
}
if ($missing === []) {
    report('PASS', 'application account holds SELECT, INSERT, UPDATE, DELETE');
} else {
    report('FAIL', 'application account is missing: ' . implode(', ', $missing));
}

$forbidden = [
    'CREATE', 'ALTER', 'DROP', 'INDEX', 'REFERENCES', 'CREATE VIEW',
    'CREATE ROUTINE', 'ALTER ROUTINE', 'TRIGGER', 'EVENT',
    'CREATE TEMPORARY TABLES', 'LOCK TABLES', 'EXECUTE', 'FILE', 'SUPER',
    'PROCESS', 'RELOAD', 'SHUTDOWN', 'GRANT OPTION', 'ALL PRIVILEGES',
    'CREATE USER', 'REPLICATION CLIENT', 'REPLICATION SLAVE',
];
$found = array_values(array_intersect(array_keys($privileges), $forbidden));
if ($found === []) {
    report('PASS', 'application account holds no DDL or admin privilege');
} else {
    report('FAIL', 'application account holds elevated privileges: '
        . implode(', ', $found));
}

// Empirical proof beats parsing: try to create something and expect refusal.
$probe = 'zz_smoke_ddl_probe';
$created = false;
try {
    $db->execute('CREATE TABLE ' . $probe . ' (id INT NOT NULL)');
    $created = true;
} catch (Throwable $error) {
    unset($error);
}
if ($created) {
    try {
        $db->execute('DROP TABLE ' . $probe);
    } catch (Throwable $error) {
        report('FAIL', 'the DDL probe table could not be removed');
        finish();
    }
    report('FAIL', 'the application account WAS able to create a table '
        . '(spec §2 requires DML only)');
} else {
    report('PASS', 'the application account is refused DDL (CREATE denied)');
}

// ---------------------------------------------------------------------------
// 7. The tenant exists (create-admin has been run)
// ---------------------------------------------------------------------------
$tenantSlug = is_string($config['tenant_slug'] ?? null)
    && $config['tenant_slug'] !== '' ? $config['tenant_slug'] : 'default';
$tenants = (int) ($db->queryValue('SELECT COUNT(*) FROM tenants') ?? 0);
$slugs = $db->query('SELECT slug FROM tenants');
$known = false;
foreach ($slugs as $slugRow) {
    if ((string) $slugRow['slug'] === $tenantSlug) {
        $known = true;
        break;
    }
}
if ($tenants === 0) {
    report('FAIL', 'no tenant exists — run php bin/create-admin.php');
} elseif (!$known) {
    report('WARN', 'no tenant with slug "' . $tenantSlug
        . '" — check tenant_slug in the configuration');
} else {
    report('PASS', 'tenant "' . $tenantSlug . '" exists');
}

// ---------------------------------------------------------------------------
// 8. Static security posture of the public directory
// ---------------------------------------------------------------------------
$indexSource = @file_get_contents(HOST_ROOT . '/public/index.php');
if (!is_string($indexSource)) {
    report('FAIL', 'public/index.php is missing');
} else {
    if (stripos($indexSource, 'Access-Control-Allow-Origin') === false) {
        report('PASS', 'the API never emits Access-Control-Allow-Origin '
            . '(spec §10.7)');
    } else {
        report('FAIL', 'the API references Access-Control-Allow-Origin');
    }
    if (strpos($indexSource, 'isSecureTransport') !== false) {
        report('PASS', 'the HTTPS gate is present in the front controller');
    } else {
        report('FAIL', 'the HTTPS gate is missing from the front controller');
    }
}

$htaccess = @file_get_contents(HOST_ROOT . '/public/.htaccess');
if (!is_string($htaccess)) {
    report('FAIL', 'public/.htaccess is missing');
} else {
    $needed = [
        'migrations' => 'migrations is denied',
        'config' => 'config is denied',
        'bin' => 'bin is denied',
        'X-Forwarded-Proto' => 'the HTTPS redirect is present',
    ];
    foreach ($needed as $needle => $label) {
        if (stripos($htaccess, $needle) !== false) {
            report('PASS', '.htaccess: ' . $label);
        } else {
            report('FAIL', '.htaccess: ' . $label . ' — not found');
        }
    }
}

// ---------------------------------------------------------------------------
// 9. Optional live probe of the deployed endpoint
// ---------------------------------------------------------------------------
$probeUrl = is_string($config['smoke_probe_url'] ?? null)
    ? trim((string) $config['smoke_probe_url']) : '';
if ($probeUrl === '') {
    report('WARN', 'live header probe skipped — set smoke_probe_url to '
        . 'https://<api-host>/v1/health/live to enable it');
} else {
    $context = stream_context_create(['http' => [
        'method' => 'GET',
        'timeout' => 10,
        'ignore_errors' => true,
        'header' => "Accept: application/json\r\n",
    ]]);
    $body = @file_get_contents($probeUrl, false, $context);
    $responseHeaders = $http_response_header ?? [];
    if ($body === false) {
        report('FAIL', 'the health endpoint could not be reached');
    } else {
        $status = 0;
        foreach ($responseHeaders as $header) {
            if (preg_match('#^HTTP/\S+\s+(\d{3})#', $header, $m) === 1) {
                $status = (int) $m[1];
            }
        }
        $decoded = json_decode($body, true);
        if ($status === 200 && is_array($decoded)
            && ($decoded['ok'] ?? null) === true
            && isset($decoded['time']) && count($decoded) === 2) {
            report('PASS', 'health endpoint answers {ok, time} and nothing else');
        } else {
            report('FAIL', 'health endpoint did not answer the liveness '
                . 'envelope (status ' . $status . ')');
        }
        $hasCors = false;
        $hasNosniff = false;
        foreach ($responseHeaders as $header) {
            if (stripos($header, 'access-control-allow-origin:') === 0) {
                $hasCors = true;
            }
            if (stripos($header, 'x-content-type-options:') === 0) {
                $hasNosniff = true;
            }
        }
        if ($hasCors) {
            report('FAIL', 'the response carries Access-Control-Allow-Origin');
        } else {
            report('PASS', 'no Access-Control-Allow-Origin header is sent');
        }
        if ($hasNosniff) {
            report('PASS', 'X-Content-Type-Options: nosniff is present');
        } else {
            report('WARN', 'X-Content-Type-Options is missing from the response');
        }
        $scheme = strtolower((string) parse_url($probeUrl, PHP_URL_SCHEME));
        if ($scheme === 'https') {
            report('PASS', 'the endpoint was probed over HTTPS');
        } else {
            report('WARN', 'smoke_probe_url is not https:// — production must '
                . 'be TLS-terminated (spec §2)');
        }
    }
}

finish();
