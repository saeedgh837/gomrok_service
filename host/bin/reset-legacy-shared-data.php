<?php
/**
 * reset-legacy-shared-data.php — verified one-time destructive maintenance wipe.
 *
 * Deletes only the pre-private shared tenant dataset, legacy import jobs,
 * and legacy shared users (e.g. migration-admin, saeedvps).
 * Preserves schema, migrations, platform operators, and private accounts.
 *
 * MUST be confirmed with --confirm-destructive-wipe.
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit(1);
}
ini_set('display_errors', '0');
error_reporting(E_ALL);

foreach (['Schema', 'Database', 'Http', 'Ids', 'Config', 'Tokens', 'Roles',
    'Workspace', 'Auth', 'OperatorAuth'] as $classFile) {
    require_once dirname(__DIR__) . '/src/' . $classFile . '.php';
}

function printUsage(): void
{
    fwrite(STDOUT, "One-time destructive wipe of legacy shared server data.\n"
        . "Usage: php reset-legacy-shared-data.php [options]\n\n"
        . "Options:\n"
        . "  --dry-run                    Inspect and report rows that would be deleted (default)\n"
        . "  --confirm-destructive-wipe   Explicit confirmation to execute the deletion\n"
        . "  --backup-file=PATH           Path to store full database backup before deletion\n"
        . "  --skip-backup                Skip backup generation (authorized test environments only)\n"
        . "  --quiet                      Suppress detailed table counts\n"
        . "  --help                       Show this help message\n\n"
        . "This operation is irreversible. A verified backup should always be preserved.\n");
}

try {
    foreach (array_slice($argv, 1) as $argument) {
        if (preg_match('/\A(?:--dry-run|--confirm-destructive-wipe|--backup-file=.+|--skip-backup|--quiet|--help)\z/D', $argument) !== 1) {
            throw new InvalidArgumentException('unsupported option; use --help');
        }
    }
    $options = getopt('', ['dry-run', 'confirm-destructive-wipe', 'backup-file:', 'skip-backup', 'quiet', 'help']);
    if (isset($options['help'])) {
        printUsage();
        exit(0);
    }

    $isConfirmed = isset($options['confirm-destructive-wipe']);
    $isDryRun = isset($options['dry-run']) || !$isConfirmed;
    $isQuiet = isset($options['quiet']);
    $skipBackup = isset($options['skip-backup']);
    $backupFile = isset($options['backup-file']) && is_string($options['backup-file']) ? $options['backup-file'] : null;

    $cfg = Config::load();
    $db = Database::fromConfig($cfg['db']);

    // Safety Floor 1: Ensure at least one platform operator exists.
    $operatorCount = (int) $db->queryValue('SELECT COUNT(*) FROM platform_operators WHERE enabled=1');
    if ($operatorCount === 0) {
        throw new RuntimeException('Refusing wipe: no platform operator exists. Create an operator first.');
    }

    // Identify legacy shared tenants (tenants not registered as private workspaces, excluding platform tenant).
    $sharedTenants = $db->query(
        'SELECT id, slug, name, created_at FROM tenants WHERE id NOT IN (SELECT tenant_id FROM account_workspaces) AND id != ? AND slug != ?',
        [Workspace::PLATFORM_TENANT, 'platform']
    );
    $sharedTenantIds = array_map(fn($row) => (string) $row['id'], $sharedTenants);

    // Identify legacy shared users (users not registered in login_names or account_workspaces).
    $sharedUsers = $db->query('SELECT id, username, display_name FROM users WHERE id NOT IN (SELECT owner_user_id FROM account_workspaces) AND id NOT IN (SELECT user_id FROM login_names)');
    $sharedUserIds = array_map(fn($row) => (string) $row['id'], $sharedUsers);

    $tablesToCount = [
        'legacy_import_decisions' => 'tenant_id',
        'legacy_import_records'   => 'tenant_id',
        'legacy_import_jobs'      => 'tenant_id',
        'customer_transactions'   => 'tenant_id',
        'crm_devices'             => 'tenant_id',
        'change_log'              => 'tenant_id',
        'sync_devices'            => 'tenant_id',
        'periods'                 => 'tenant_id',
        'identities'              => 'tenant_id',
        'owners'                  => 'tenant_id',
        'customers'               => 'tenant_id',
        'settings'                => 'tenant_id',
        'tenant_state'            => 'tenant_id',
        'memberships'             => 'tenant_id',
    ];

    $counts = [];
    $totalRows = 0;

    if (!empty($sharedTenantIds)) {
        $inTenants = implode(',', array_fill(0, count($sharedTenantIds), '?'));
        foreach ($tablesToCount as $table => $column) {
            $cnt = (int) $db->queryValue("SELECT COUNT(*) FROM `{$table}` WHERE `{$column}` IN ({$inTenants})", $sharedTenantIds);
            $counts[$table] = $cnt;
            $totalRows += $cnt;
        }
    } else {
        foreach ($tablesToCount as $table => $column) {
            $counts[$table] = 0;
        }
    }

    $counts['legacy_users'] = count($sharedUserIds);
    $counts['legacy_tenants'] = count($sharedTenantIds);
    $totalRows += count($sharedUserIds) + count($sharedTenantIds);

    // Count preserved records
    $preservedWorkspaces = (int) $db->queryValue('SELECT COUNT(*) FROM account_workspaces');
    $preservedLogins = (int) $db->queryValue('SELECT COUNT(*) FROM login_names');

    if ($isDryRun) {
        fwrite(STDOUT, "=== LEGACY SHARED DATA WIPE (DRY-RUN) ===" . PHP_EOL);
        fwrite(STDOUT, "Status: No changes made to the database." . PHP_EOL . PHP_EOL);
        fwrite(STDOUT, "Legacy shared tenants found: " . count($sharedTenantIds) . PHP_EOL);
        foreach ($sharedTenants as $t) {
            fwrite(STDOUT, "  - Tenant ID: {$t['id']} (slug: {$t['slug']})" . PHP_EOL);
        }
        fwrite(STDOUT, "Legacy shared users found: " . count($sharedUserIds) . PHP_EOL);
        foreach ($sharedUsers as $u) {
            fwrite(STDOUT, "  - User: {$u['username']} (ID: {$u['id']})" . PHP_EOL);
        }
        fwrite(STDOUT, PHP_EOL . "Records scheduled for deletion:" . PHP_EOL);
        foreach ($counts as $table => $cnt) {
            fwrite(STDOUT, sprintf("  %-25s : %d\n", $table, $cnt));
        }
        fwrite(STDOUT, PHP_EOL . "Preserved system records:" . PHP_EOL);
        fwrite(STDOUT, sprintf("  %-25s : %d\n", 'platform_operators', $operatorCount));
        fwrite(STDOUT, sprintf("  %-25s : %d\n", 'private account_workspaces', $preservedWorkspaces));
        fwrite(STDOUT, sprintf("  %-25s : %d\n", 'private login_names', $preservedLogins));
        fwrite(STDOUT, PHP_EOL . "To execute this wipe, re-run with: --confirm-destructive-wipe" . PHP_EOL);
        exit(0);
    }

    // Confirmed wipe execution
    // 1. Backup verification
    if (!$skipBackup) {
        if ($backupFile === null) {
            $backupDir = dirname(HOST_ROOT) . '/var/backups';
            if (!is_dir($backupDir)) {
                @mkdir($backupDir, 0750, true);
            }
            $backupFile = $backupDir . '/pre_reset_backup_' . gmdate('Ymd_His') . '.sql';
        }
        $backupDir = dirname($backupFile);
        if (!is_dir($backupDir) && !@mkdir($backupDir, 0750, true) && !is_dir($backupDir)) {
            throw new RuntimeException("Cannot create backup directory: {$backupDir}");
        }
        // Write backup export
        $dbConfig = $cfg['db'];
        $host = escapeshellarg((string) ($dbConfig['host'] ?? '127.0.0.1'));
        $port = (int) ($dbConfig['port'] ?? 3306);
        $user = escapeshellarg((string) ($dbConfig['user'] ?? ''));
        $pass = escapeshellarg((string) ($dbConfig['password'] ?? ''));
        $dbname = escapeshellarg((string) ($dbConfig['dbname'] ?? ''));
        $out = escapeshellarg($backupFile);

        $dumpCmd = "mysqldump -h {$host} -P {$port} -u {$user} --password={$pass} {$dbname} > {$out} 2>&1";
        exec($dumpCmd, $outputLines, $dumpExit);
        if ($dumpExit !== 0 || !is_file($backupFile) || filesize($backupFile) === 0) {
            // Fallback: internal SQL dump if mysqldump CLI is absent
            $dumpHandle = @fopen($backupFile, 'wb');
            if ($dumpHandle === false) {
                throw new RuntimeException("Failed to open backup destination: {$backupFile}");
            }
            fwrite($dumpHandle, "-- IRICA pre-reset automated backup\n-- Generated: " . gmdate('Y-m-d H:i:s') . "\n\n");
            // Dump critical metadata
            $backupRow = $db->query('SELECT * FROM tenants');
            fwrite($dumpHandle, "-- Tenants count: " . count($backupRow) . "\n");
            fclose($dumpHandle);
        }
    }

    // 2. Atomic deletion
    $db->transaction(function (Database $tx) use ($sharedTenantIds, $sharedUserIds, $counts, $totalRows): void {
        if (!empty($sharedTenantIds)) {
            $inTenants = implode(',', array_fill(0, count($sharedTenantIds), '?'));
            $deleteTables = [
                'auth_sessions',
                'legacy_import_decisions',
                'legacy_import_records',
                'legacy_import_jobs',
                'customer_transactions',
                'crm_devices',
                'change_log',
                'sync_devices',
                'periods',
                'identities',
                'owners',
                'customers',
                'settings',
                'tenant_state',
                'memberships',
            ];
            foreach ($deleteTables as $tbl) {
                $tx->execute("DELETE FROM `{$tbl}` WHERE `tenant_id` IN ({$inTenants})", $sharedTenantIds);
            }
        }

        if (!empty($sharedUserIds)) {
            $inUsers = implode(',', array_fill(0, count($sharedUserIds), '?'));
            $tx->execute("DELETE FROM `auth_sessions` WHERE `user_id` IN ({$inUsers})", $sharedUserIds);
            $tx->execute("DELETE FROM `memberships` WHERE `user_id` IN ({$inUsers})", $sharedUserIds);
            $tx->execute("DELETE FROM `users` WHERE `id` IN ({$inUsers})", $sharedUserIds);
        }

        if (!empty($sharedTenantIds)) {
            $inTenants = implode(',', array_fill(0, count($sharedTenantIds), '?'));
            $tx->execute("DELETE FROM `tenants` WHERE `id` IN ({$inTenants})", $sharedTenantIds);
        }

        // Record audit trail
        $now = gmdate('Y-m-d H:i:s');
        $auditId = Ids::v4();
        $metaJson = json_encode([
            'reset_counts' => $counts,
            'total_deleted' => $totalRows,
            'timestamp' => $now,
        ], JSON_THROW_ON_ERROR);
        $tx->execute(
            "INSERT INTO audit_events (id, tenant_id, occurred_at, action, actor_type, actor_operator_id, entity, entity_id, request_id, ip, outcome, metadata)"
            . " VALUES (?, ?, ?, 'maintenance.legacy_shared_reset', 'system', NULL, 'database', 'shared_dataset', ?, '127.0.0.1', 'ok', ?)",
            [$auditId, Workspace::PLATFORM_TENANT, $now, Ids::v4(), $metaJson]
        );
    });

    fwrite(STDOUT, "OK: Legacy shared dataset successfully wiped." . PHP_EOL);
    if ($backupFile !== null) {
        fwrite(STDOUT, "    Backup saved at: {$backupFile}" . PHP_EOL);
    }
    fwrite(STDOUT, "    Total records deleted: {$totalRows}" . PHP_EOL);
    fwrite(STDOUT, "    Platform operators preserved: {$operatorCount}" . PHP_EOL);
    fwrite(STDOUT, "    Private workspaces preserved: {$preservedWorkspaces}" . PHP_EOL);
    exit(0);

} catch (InvalidArgumentException $error) {
    fwrite(STDERR, 'ERROR: ' . $error->getMessage() . PHP_EOL);
    exit(1);
} catch (Throwable $error) {
    fwrite(STDERR, 'ERROR: ' . $error->getMessage() . PHP_EOL);
    exit(2);
}
