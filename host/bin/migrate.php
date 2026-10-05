<?php
/**
 * migrate.php — the forward-only migration runner (spec §10.10).
 *
 * Run as the PRIVILEGED database account (config `db_migrate`), because the
 * application account holds no DDL (spec §2):
 *
 *     php bin/migrate.php            # apply everything not yet applied
 *     php bin/migrate.php --status   # report and change nothing
 *
 * Rules, exactly as host/migrations/001_initial.sql documents them:
 *     version present + checksum == sha256(file)  -> skip   (no-op)
 *     version present + checksum != sha256(file)  -> refuse (hard error)
 *     version absent                              -> apply, then record
 *
 * The tracker is read FIRST, so a database that already carries the schema
 * never sees the file again. The file itself is split into statements without
 * relying on multi-statement execution, and a failure names the statement and
 * stops: MySQL DDL is not transactional, so the recovery for a half-applied
 * file is always "drop the empty scratch database and start again", which is
 * why the runner refuses to touch a database that already has tables it has
 * not applied.
 *
 * Exit codes: 0 success · 1 refusal or failure · 2 usage error.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
error_reporting(E_ALL);

define('HOST_ROOT', dirname(__DIR__));
foreach (['Schema', 'Database', 'Http', 'Ids', 'Config'] as $classFile) {
    require_once HOST_ROOT . '/src/' . $classFile . '.php';
}

$onlyStatus = in_array('--status', $argv, true);
$quiet = in_array('--quiet', $argv, true);

function out(string $line, bool $quiet): void
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

try {
    $config = Config::load();
} catch (RuntimeException $error) {
    fail($error->getMessage());
}

$settings = is_array($config['db_migrate'] ?? null)
    ? $config['db_migrate'] : $config['db'];
$account = is_array($config['db_migrate'] ?? null) ? 'privileged' : 'app';

try {
    $db = Database::fromConfig($settings);
} catch (Throwable $error) {
    // Never echo the connection string: Database::fromConfig never puts a
    // password in a message, but a driver message could name the account.
    fail('cannot connect to the database (' . get_class($error) . ')');
}

$directory = HOST_ROOT . '/migrations';
$files = glob($directory . '/*.sql');
if ($files === false || $files === []) {
    fail('no migration files found in host/migrations');
}
sort($files, SORT_STRING);

$applied = [];
if (tableExists($db, 'schema_migrations')) {
    foreach ($db->query(
        'SELECT version, checksum FROM schema_migrations ORDER BY version'
    ) as $row) {
        $applied[(string) $row['version']] = (string) $row['checksum'];
    }
}

$pending = [];
foreach ($files as $file) {
    $version = basename($file, '.sql');
    $checksum = hash_file('sha256', $file);
    if ($checksum === false) {
        fail('cannot read ' . basename($file));
    }
    if (isset($applied[$version])) {
        if (!hash_equals($applied[$version], $checksum)) {
            fail('version ' . $version . ' is recorded with a different '
                . 'checksum — the file changed after it was applied. '
                . 'Migrations are forward-only: add a NEW file instead of '
                . 'editing this one.');
        }
        out('SKIP  ' . $version . ' (already applied)', $quiet);
        continue;
    }
    $pending[] = ['version' => $version, 'file' => $file, 'checksum' => $checksum];
}

if ($onlyStatus) {
    out('schema version: ' . (array_key_first($applied) !== null
        ? implode(', ', array_keys($applied)) : 'none'), $quiet);
    out('pending: ' . ($pending === []
        ? 'none' : implode(', ', array_column($pending, 'version'))), $quiet);
    exit(0);
}

if ($pending === []) {
    out('nothing to apply (account: ' . $account . ')', $quiet);
    exit(0);
}

if ($account === 'app') {
    out('NOTE  db_migrate is not configured; using the application account. '
        . 'That account is expected to hold CREATE only for this run '
        . '(host/INSTALL.md).', $quiet);
}

if ($applied === [] && !databaseIsPristine($db)) {
    fail('the database already holds tables this runner has not applied. '
        . 'Refusing to write into a non-empty schema — drop the scratch '
        . 'database or restore from backup and try again.');
}

foreach ($pending as $entry) {
    out('APPLY ' . $entry['version'], $quiet);
    $statements = splitStatements((string) file_get_contents($entry['file']));
    if ($statements === []) {
        fail($entry['version'] . ' contains no statements');
    }
    $index = 0;
    foreach ($statements as $statement) {
        $index++;
        try {
            $db->pdo()->exec($statement);
        } catch (PDOException $error) {
            $info = $error->errorInfo ?? null;
            $errno = is_array($info) && isset($info[1]) ? (int) $info[1] : 0;
            $snippet = trim(preg_replace('/\s+/', ' ', $statement) ?? '');
            fail($entry['version'] . ' failed at statement ' . $index
                . ' (errno ' . $errno . '): ' . substr($snippet, 0, 120)
                . PHP_EOL . 'The schema may be partially applied. MySQL DDL '
                . 'cannot be rolled back: drop the database and re-run.');
        }
    }
    $db->insert('schema_migrations', [
        'version' => $entry['version'],
        'description' => basename($entry['file']),
        'checksum' => $entry['checksum'],
        'applied_at' => gmdate('Y-m-d H:i:s'),
        'applied_by' => substr((string) $db->queryValue('SELECT CURRENT_USER()'),
            0, 64),
    ]);
    out('OK    ' . $entry['version'] . ' (' . count($statements)
        . ' statements)', $quiet);
}

out('done — schema version ' . implode(', ', array_column($pending, 'version')),
    $quiet);
exit(0);

// ---------------------------------------------------------------------------

function tableExists(Database $db, string $table): bool
{
    $value = $db->queryValue(
        'SELECT COUNT(*) FROM information_schema.TABLES'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?',
        [$table]
    );
    return $value !== null && (int) $value === 1;
}

/**
 * True when the only table present is the tracker itself — the state a
 * forward-only runner is allowed to write into.
 */
function databaseIsPristine(Database $db): bool
{
    $rows = $db->query(
        'SELECT TABLE_NAME FROM information_schema.TABLES'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = ?',
        ['BASE TABLE']
    );
    foreach ($rows as $row) {
        if ((string) $row['TABLE_NAME'] !== 'schema_migrations') {
            return false;
        }
    }
    return true;
}

/**
 * Split a migration file into executable statements.
 *
 * A naive explode on the semicolon would cut one inside a string literal, and
 * relying on multi-statement execution would hide which statement failed.
 * Single quotes, double quotes, backticks, line comments and block comments
 * are all tracked while scanning.
 *
 * @return list<string>
 */
function splitStatements(string $sql): array
{
    $statements = [];
    $buffer = '';
    $length = strlen($sql);
    $state = 'normal';
    for ($i = 0; $i < $length; $i++) {
        $char = $sql[$i];
        $next = $i + 1 < $length ? $sql[$i + 1] : '';

        if ($state === 'line_comment') {
            $buffer .= $char;
            if ($char === "\n") {
                $state = 'normal';
            }
            continue;
        }
        if ($state === 'block_comment') {
            $buffer .= $char;
            if ($char === '*' && $next === '/') {
                $buffer .= '/';
                $i++;
                $state = 'normal';
            }
            continue;
        }
        if ($state === 'single' || $state === 'double' || $state === 'backtick') {
            $buffer .= $char;
            if ($char === '\\' && $state !== 'backtick') {
                if ($i + 1 < $length) {
                    $buffer .= $sql[$i + 1];
                    $i++;
                }
                continue;
            }
            $close = $state === 'single' ? "'"
                : ($state === 'double' ? '"' : '`');
            if ($char === $close) {
                // '' or "" inside a literal is an escaped quote, not a close.
                if ($next === $close && $state !== 'backtick') {
                    $buffer .= $next;
                    $i++;
                    continue;
                }
                if ($state === 'backtick' && $next === '`') {
                    $buffer .= $next;
                    $i++;
                    continue;
                }
                $state = 'normal';
            }
            continue;
        }

        // normal
        if ($char === '-' && $next === '-') {
            $state = 'line_comment';
            $buffer .= $char;
            continue;
        }
        if ($char === '#' && ($next === ' ' || $next === "\t")) {
            $state = 'line_comment';
            $buffer .= $char;
            continue;
        }
        if ($char === '/' && $next === '*') {
            $state = 'block_comment';
            $buffer .= $char . $next;
            $i++;
            continue;
        }
        if ($char === "'") {
            $state = 'single';
            $buffer .= $char;
            continue;
        }
        if ($char === '"') {
            $state = 'double';
            $buffer .= $char;
            continue;
        }
        if ($char === '`') {
            $state = 'backtick';
            $buffer .= $char;
            continue;
        }
        if ($char === ';') {
            $trimmed = trim($buffer);
            if ($trimmed !== '') {
                $statements[] = $trimmed;
            }
            $buffer = '';
            continue;
        }
        $buffer .= $char;
    }
    $trimmed = trim($buffer);
    if ($trimmed !== '') {
        $statements[] = $trimmed;
    }
    return $statements;
}
