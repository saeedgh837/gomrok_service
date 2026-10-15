<?php
/**
 * migrate.php — thin CLI adapter over host/src/Migrations.php.
 *
 * Run as the PRIVILEGED database account (config `db_migrate`), because the
 * application account holds no DDL (spec §2):
 *
 *     php bin/migrate.php            # apply everything not yet applied
 *     php bin/migrate.php --status   # report and change nothing
 *
 * Configuration selection is unchanged: `db_migrate` when it is set, otherwise
 * `db`. All migration behaviour — the step journal, the advisory lock, the
 * recovery protocol and the counts-only report — lives in Migrations.php and
 * MigrationPreflight.php so the CLI, the fixture suite and any future entry
 * point share one implementation.
 *
 * Rules, exactly as host/migrations/001_initial.sql and
 * host/migrations/002_identity_foundation.sql document them:
 *     version present + checksum == sha256(file)  -> skip   (no-op)
 *     version present + checksum != sha256(file)  -> refuse (hard error)
 *     recorded version with no file               -> refuse (hard error)
 *     version absent                              -> apply step by step
 *
 * Exit codes: 0 success · 1 refusal or failure · 2 usage error.
 * Output is counts and stable codes only: never a row value, a name, a
 * national id, a credential or a raw server log line.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
error_reporting(E_ALL);

define('HOST_ROOT', dirname(__DIR__));
foreach (['Schema', 'Database', 'Http', 'Ids', 'Config', 'MigrationPreflight', 'Migrations'] as $classFile) {
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

$runner = new Migrations($db, HOST_ROOT . '/migrations');

try {
    $report = $onlyStatus ? $runner->status() : $runner->apply();
} catch (RuntimeException $error) {
    fail($error->getMessage());
} catch (Throwable $error) {
    fail('migration failed (' . get_class($error) . ')');
}

$status = $report['migration_status'];
out('state: ' . $report['state'], $quiet);
out('applied versions: ' . $status['applied_versions'], $quiet);
out('pending versions: ' . $status['pending_versions'], $quiet);
out('verified steps: ' . $status['verified_steps'], $quiet);
out('pending steps: ' . $status['pending_steps'], $quiet);
out('blockers: ' . count($report['blockers']), $quiet);
foreach ($report['blockers'] as $blocker) {
    out('  ' . $blocker['code'] . ' table=' . $blocker['table'] . ' count=' . $blocker['count'], $quiet);
}
if ($onlyStatus) {
    exit(0);
}
if ($report['state'] !== 'COMPLETE') {
    fail('migration did not reach COMPLETE; see the blocker codes above and host/INSTALL.md');
}
out('nothing else to apply (account: ' . $account . ')', $quiet);
exit(0);
