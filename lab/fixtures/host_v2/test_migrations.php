<?php
/**
 * test_migrations.php — Phase B migration suite (`--suite migrations`).
 *
 * Runs against the separately provisioned fixture instance only. It never
 * reads application configuration, never touches production, and never prints
 * a credential, a row value, a name or a national id: a failure is always one
 * of the fixed FIXTURE_* labels emitted by bootstrap.php.
 *
 * Target-state contract (Ruling 4)
 *   The shared prerequisite gate accepts a populated target, so this scenario
 *   classifies the target itself and distinguishes two legal states from one
 *   illegal one:
 *     EMPTY    - no objects at all: the initial-install path runs in full,
 *                including the fault matrix for 001 and for 002.
 *     APPLIED  - every recorded file applied, every step verified, no
 *                unexpected object: the legitimate "already applied, no work"
 *                path. apply() must be a no-op and status() must stay COMPLETE.
 *     anything else -> scenario failure, never a silent green.
 *   EMPTY and APPLIED are NOT the same code path: EMPTY asserts objects are
 *   created and data preserved; APPLIED asserts nothing is created and nothing
 *   is written. The pristine assertion (no object before 001) is only
 *   meaningful on EMPTY, and 001's own first-run inventory inside the runner
 *   enforces it.
 *
 * Nothing here drops or truncates a table. The controller re-provisions the
 * synthetic schema between waves; this suite leaves it populated on purpose so
 * the following integrity suite can consume it.
 */

declare(strict_types=1);

require __DIR__ . '/bootstrap.php';

$repoRoot = BFixture::repoRoot();
foreach (['Schema', 'Database', 'MigrationPreflight', 'Migrations'] as $classFile) {
    require_once $repoRoot . '/host/src/' . $classFile . '.php';
}

const MIG_DIR_NAME = 'host/migrations';
const TENANT = 'a1000000-0000-4000-8000-000000000001';
const USER_ADMIN = 'b1000000-0000-4000-8000-000000000001';
const USER_MEMBER = 'b1000000-0000-4000-8000-000000000002';
const DEVICE = 'c1000000-0000-4000-8000-000000000001';
const PERIOD = '91000000-0000-4000-8000-000000000001';
const CUSTOMER_1 = 'd1000000-0000-4000-8000-000000000001';
const CUSTOMER_2 = 'd1000000-0000-4000-8000-000000000002';
const OWNER_1 = 'e1000000-0000-4000-8000-000000000001';
const IDENTITY_1 = 'f1000000-0000-4000-8000-000000000001';
const CASE_1 = '71000000-0000-4000-8000-000000000001';
const TX_1 = '61000000-0000-4000-8000-000000000001';
const TX_2 = '61000000-0000-4000-8000-000000000002';
const SETTINGS = '81000000-0000-4000-8000-000000000001';
const AUTH_SESSION = 'c2000000-0000-4000-8000-000000000001';
const MEMBERSHIP_1 = 'b2000000-0000-4000-8000-000000000001';
const MEMBERSHIP_2 = 'b2000000-0000-4000-8000-000000000002';
const AUDIT_1 = 'a2000000-0000-4000-8000-000000000001';
const AUDIT_2 = 'a2000000-0000-4000-8000-000000000002';
const IDEMPOTENCY = 'a3000000-0000-4000-8000-000000000001';

/** Synthetic markers that must NEVER appear in a structured report. */
const PII_CUSTOMER_NAME = 'PII_MARKER_CUSTOMER_ALPHA';
const PII_NATIONAL_ID = '9876543210';

/** Not a credential: a fixed, obviously synthetic placeholder string. */
const SYNTHETIC_HASH = '$argon2id$synthetic-not-a-credential-00000000000000000000000';

$directory = $repoRoot . '/' . MIG_DIR_NAME;
$fixture = BFixture::readConfig((string) getenv('IRICA_B_FIXTURE_CONFIG'));
$ddl = BFixture::db(true);
$app = BFixture::db(false);
/** A second, independent PDO connection, for the lock-contention proof. */
$second = Database::fromConfig((array) $fixture->migrate);

function t(bool $condition, string $what): void
{
    if (!$condition) {
        throw new RuntimeException('SCENARIO_FAILED: ' . $what);
    }
}

function tableCount(Database $db): int
{
    return (int) $db->queryValue(
        'SELECT COUNT(*) FROM information_schema.TABLES'
        . " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'");
}

function projectionColumns(Database $db, string $table): array
{
    $rows = $db->query(
        'SELECT COLUMN_NAME FROM information_schema.COLUMNS'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
        . ' ORDER BY ORDINAL_POSITION', ['table' => $table]);
    return array_map(static fn (array $row): string => (string) $row['COLUMN_NAME'], $rows);
}

function primaryKeyOf(Database $db, string $table): array
{
    $rows = $db->query(
        'SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE'
        . " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        . " AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION",
        ['table' => $table]);
    return array_map(static fn (array $row): string => (string) $row['COLUMN_NAME'], $rows);
}

/**
 * Hash of the rows of one table, over an explicit column projection, in stable
 * primary-key order. Row counts alone prove nothing, and hashing SELECT *
 * across a schema that is about to grow new columns is not a preservation
 * proof either — so the projection is recorded before the upgrade and reused.
 */
function rowHash(Database $db, string $table, array $columns): string
{
    $quoted = implode(', ', array_map(
        static fn (string $c): string => '`' . str_replace('`', '``', $c) . '`', $columns));
    $order = primaryKeyOf($db, $table);
    $orderBy = $order === [] ? '' : ' ORDER BY ' . implode(', ', array_map(
        static fn (string $c): string => '`' . str_replace('`', '``', $c) . '`', $order));
    $hash = hash_init('sha256');
    foreach ($db->query('SELECT ' . $quoted . ' FROM `' . str_replace('`', '``', $table) . '`' . $orderBy) as $row) {
        foreach ($columns as $column) {
            $value = $row[$column] ?? null;
            hash_update($hash, $value === null ? "\x00N" : (string) $value);
            hash_update($hash, "\x1f");
        }
        hash_update($hash, "\x1e");
    }
    return hash_final($hash);
}

function scalar(Database $db, string $sql, array $params = []): mixed
{
    return $db->queryValue($sql, $params);
}

/** One injected crash at exactly one (event, version, step). */
function crashOnce(Database $db, string $directory, array $versions, string $event,
                   string $version, string $step): void
{
    $fired = false;
    $hook = static function (string $observed, string $observedVersion,
                             string $observedStep) use (&$fired, $event, $version, $step): void {
        if ($observed === $event && $observedVersion === $version && $observedStep === $step) {
            $fired = true;
            throw new MigrationSimulatedCrash('injected ' . $event);
        }
    };
    $runner = new Migrations($db, $directory, $hook);
    try {
        $runner->apply($versions);
    } catch (MigrationSimulatedCrash $crash) {
        unset($crash);
    }
    t($fired, 'fault did not fire: ' . $event . ' ' . $version . '/' . $step);
}

/**
 * Drive one step through every interruption point:
 *   before_ddl                 -> journal `started`, DDL not executed
 *   after_ddl                  -> DDL committed, journal still `started`
 *   before_journal_complete    -> journal `applied`, not yet `verified`
 * Each crash leaves exactly the durable state a killed process would leave,
 * because the journal writes are individually committed and MySQL DDL
 * auto-commits. The following run reconciles from engine metadata, which is
 * the whole point of the protocol.
 */
function driveStepThroughCrashes(Database $db, string $directory, array $versions,
                                 array $plan): void
{
    foreach ($plan as [$version, $stepId]) {
        crashOnce($db, $directory, $versions, Migrations::EVENT_BEFORE_DDL, $version, $stepId);
        crashOnce($db, $directory, $versions, Migrations::EVENT_AFTER_DDL, $version, $stepId);
        crashOnce($db, $directory, $versions, Migrations::EVENT_BEFORE_JOURNAL_COMPLETE,
            $version, $stepId);
    }
}

function journalStatus(Database $db, string $version, string $step): ?string
{
    $value = scalar($db, 'SELECT status FROM migration_steps'
        . ' WHERE migration_version = :version AND step_id = :step',
        ['version' => $version, 'step' => $step]);
    return $value === null ? null : (string) $value;
}

function journalCursor(Database $db, string $version, string $step): ?string
{
    $value = scalar($db, 'SELECT cursor_value FROM migration_steps'
        . ' WHERE migration_version = :version AND step_id = :step',
        ['version' => $version, 'step' => $step]);
    return $value === null ? null : (string) $value;
}

/**
 * Every declared table's normalised metadata, as one hash. Two identical
 * hashes before and after an operation prove that operation executed no DDL:
 * no table, column, index or foreign key was created, dropped or altered.
 */
function schemaFingerprint(MigrationPreflight $preflight): string
{
    $snapshot = [];
    foreach ($preflight->tables() as $table) {
        $snapshot[$table] = $preflight->tableState($table);
    }
    ksort($snapshot, SORT_STRING);
    return hash('sha256', json_encode($snapshot));
}

/** The whole step ledger: `version/step` => status, sorted. */
function journalLedger(Database $db): array
{
    $out = [];
    foreach ($db->query('SELECT migration_version, step_id, status FROM migration_steps'
        . ' ORDER BY migration_version, step_id') as $row) {
        $out[$row['migration_version'] . '/' . $row['step_id']] = (string) $row['status'];
    }
    ksort($out, SORT_STRING);
    return $out;
}

/**
 * Every journal row, byte-for-byte, so a section that empties the ledger can
 * put the exact original back without depending on the code under test.
 *
 * @return list<array<string, mixed>>
 */
function journalRowsFull(Database $db): array
{
    return $db->query('SELECT migration_version, step_id, checksum, status, cursor_value,'
        . ' started_at, completed_at FROM migration_steps'
        . ' ORDER BY migration_version, step_id');
}

/** Replace the whole step ledger with a snapshot taken earlier. */
function restoreJournal(Database $db, array $rows): void
{
    $db->execute('DELETE FROM migration_steps');
    foreach ($rows as $row) {
        $db->execute(
            'INSERT INTO migration_steps (migration_version, step_id, checksum, status,'
            . ' cursor_value, started_at, completed_at) VALUES'
            . ' (:version, :step, :checksum, :status, :cursor, :started, :completed)',
            ['version' => (string) $row['migration_version'],
             'step' => (string) $row['step_id'],
             'checksum' => (string) $row['checksum'],
             'status' => (string) $row['status'],
             'cursor' => $row['cursor_value'] === null ? null : (string) $row['cursor_value'],
             'started' => (string) $row['started_at'],
             'completed' => $row['completed_at'] === null ? null : (string) $row['completed_at']]);
    }
}

/** The whole file tracker: version => [checksum, applied_at]. */
function trackerLedger(Database $db, array $columns): array
{
    $selected = [];
    foreach (['version', 'checksum', 'applied_at'] as $name) {
        if (in_array($name, $columns, true)) {
            $selected[] = $name;
        }
    }
    sort($selected);
    $out = [];
    foreach ($db->query('SELECT ' . implode(', ', $selected)
        . ' FROM schema_migrations ORDER BY version') as $row) {
        $out[(string) $row['version']] = $row;
    }
    ksort($out, SORT_STRING);
    return $out;
}

/** The distinct `code` values a validation returned, as a sorted list. */
function codes(array $failures): array
{
    $out = [];
    foreach ($failures as $failure) {
        $out[$failure['column'] . '=' . $failure['code']] = true;
    }
    $keys = array_keys($out);
    sort($keys, SORT_STRING);
    return $keys;
}

// ---------------------------------------------------------------------------
// Target classification: EMPTY, APPLIED, or a hard failure.
// ---------------------------------------------------------------------------
$probe = new MigrationPreflight($ddl, $directory);
$existing = $probe->tables();
$state = $existing === [] ? 'EMPTY' : 'UNKNOWN';

$runner = new Migrations($ddl, $directory);
if ($state === 'UNKNOWN') {
    $report = $runner->status();
    $expectedVersions = count($probe->files());
    if ($report['state'] === 'COMPLETE'
        && $report['migration_status']['applied_versions'] === $expectedVersions
        && $report['migration_status']['pending_steps'] === 0) {
        $state = 'APPLIED';
    } else {
        // Not empty, not a fully recorded chain: unexplained pre-existing
        // objects. Refusing is the only honest outcome — the scenario must
        // never pass by ignoring what it found.
        throw new RuntimeException('SCENARIO_FAILED: unexpected pre-existing objects');
    }
}

// ---------------------------------------------------------------------------
// A. The application account can never run DDL.
// ---------------------------------------------------------------------------
try {
    $app->execute('CREATE TABLE `__irica_b_runtime_refusal` (`id` INT NOT NULL)');
    throw new RuntimeException('SCENARIO_FAILED: runtime DDL was allowed');
} catch (PDOException $error) {
    BFixture::assertDdlDenied($error->errorInfo);
}
if ($state === 'EMPTY') {
    $before = tableCount($ddl);
    $refused = false;
    try {
        (new Migrations($app, $directory))->apply(['001_initial']);
    } catch (Throwable $error) {
        $refused = true;
    }
    t($refused, 'the runtime runner must be refused');
    t(tableCount($ddl) === $before, 'the refused runtime runner mutated the schema');
}

// ---------------------------------------------------------------------------
// B. Initial install, populated upgrade, full chain (EMPTY target only).
// ---------------------------------------------------------------------------
if ($state === 'EMPTY') {
    $plan001 = [['001_initial', Migrations::LEGACY_STEP_ID]];

    // --- B1. 001 through every interruption point, then completed. ---------
    driveStepThroughCrashes($ddl, $directory, ['001_initial'], $plan001);
    crashOnce($ddl, $directory, ['001_initial'], Migrations::EVENT_BEFORE_TRACKER_INSERT,
        '001_initial', Migrations::VERSION_STEP);
    t(journalStatus($ddl, '001_initial', Migrations::LEGACY_STEP_ID) === 'verified',
        '001 must be verified before its file tracker row is written');
    t((int) scalar($ddl, 'SELECT COUNT(*) FROM schema_migrations') === 0,
        'the file tracker row must not exist before the final run');
    $report = $runner->apply(['001_initial']);
    t($report['state'] === 'PENDING', '001 alone must leave the chain PENDING');
    // The runner owns one extra object of its own: the step journal.
    t(tableCount($ddl) === count(Schema::tablesForVersion('001_initial')) + 1,
        '001 must create exactly its declared tables plus the journal');
    foreach (Schema::tablesForVersion('001_initial') as $ownedTable) {
        t(in_array($ownedTable, $probe->tables(), true), '001 must create ' . $ownedTable);
    }
    foreach (Schema::tablesForVersion('002_identity_foundation') as $futureTable) {
        t(!in_array($futureTable, $probe->tables(), true),
            '002 objects must not exist yet: ' . $futureTable);
    }
    t((int) scalar($ddl, 'SELECT COUNT(*) FROM schema_migrations') === 1, 'one tracker row');
    t((string) scalar($ddl, 'SELECT version FROM schema_migrations') === '001_initial',
        'the tracker records the filename stem');
    t(journalStatus($ddl, '001_initial', Migrations::LEGACY_STEP_ID) === 'verified',
        '001 must end verified');
    t((int) scalar($ddl, 'SELECT COUNT(*) FROM users') === 0, '001 seeds no user');

    // --- B2. Synthetic legacy data across all 17 non-tracker tables. -------
    $stamp = '2026-01-01T00:00:00+00:00';
    $ddl->insert('tenants', ['id' => TENANT, 'slug' => 'synthetic-tenant',
        'name' => 'Synthetic Tenant', 'status' => 'active',
        'created_at' => $stamp, 'updated_at' => $stamp]);
    $ddl->insert('users', ['id' => USER_ADMIN, 'tenant_id' => TENANT, 'username' => 'legacy_admin',
        'password_hash' => SYNTHETIC_HASH, 'display_name' => 'Legacy Admin',
        'must_change_password' => 0, 'status' => 'active', 'token_version' => 3,
        'last_login_at' => $stamp, 'row_version' => 4,
        'created_at' => $stamp, 'updated_at' => $stamp]);
    $ddl->insert('users', ['id' => USER_MEMBER, 'tenant_id' => TENANT, 'username' => 'legacy_member',
        'password_hash' => SYNTHETIC_HASH, 'display_name' => 'Legacy Member',
        'must_change_password' => 1, 'status' => 'disabled', 'token_version' => 2,
        'last_login_at' => null, 'row_version' => 5,
        'created_at' => $stamp, 'updated_at' => $stamp]);
    $ddl->insert('memberships', ['id' => MEMBERSHIP_1, 'tenant_id' => TENANT,
        'user_id' => USER_ADMIN, 'role' => 'admin', 'created_at' => $stamp, 'updated_at' => $stamp]);
    $ddl->insert('memberships', ['id' => MEMBERSHIP_2, 'tenant_id' => TENANT,
        'user_id' => USER_MEMBER, 'role' => 'user', 'created_at' => $stamp, 'updated_at' => $stamp]);
    $ddl->insert('sync_devices', ['id' => DEVICE, 'tenant_id' => TENANT, 'name' => 'legacy-laptop',
        'status' => 'active', 'token_version' => 2, 'last_auth_at' => $stamp,
        'last_seen_at' => $stamp, 'enrolled_by' => USER_ADMIN, 'row_version' => 2,
        'created_at' => $stamp, 'updated_at' => $stamp]);
    $ddl->insert('auth_sessions', ['id' => AUTH_SESSION, 'tenant_id' => TENANT,
        'family_id' => AUTH_SESSION, 'kind' => 'user', 'token_hash' => str_repeat('a', 64),
        'user_id' => USER_ADMIN, 'sync_device_id' => null, 'parent_id' => null,
        'issued_at' => $stamp, 'idle_expires_at' => $stamp, 'absolute_expires_at' => $stamp,
        'used_at' => $stamp, 'revoked_at' => null, 'revoked_reason' => '',
        'rotated_to' => null, 'created_at' => $stamp]);
    $ddl->insert('login_attempts', ['tenant_id' => TENANT, 'account' => 'legacy_admin',
        'ip' => '127.0.0.1', 'attempted_at' => $stamp, 'outcome' => 'success']);
    $ddl->insert('audit_events', ['id' => AUDIT_1, 'tenant_id' => TENANT, 'occurred_at' => $stamp,
        'action' => 'user.login', 'actor_type' => 'user', 'actor_user_id' => USER_ADMIN,
        'actor_device_id' => DEVICE, 'entity' => 'users', 'entity_id' => USER_ADMIN,
        'request_id' => null, 'ip' => null, 'outcome' => 'ok', 'metadata' => null]);
    $ddl->insert('audit_events', ['id' => AUDIT_2, 'tenant_id' => TENANT, 'occurred_at' => $stamp,
        'action' => 'device.enroll', 'actor_type' => 'device', 'actor_user_id' => null,
        'actor_device_id' => DEVICE, 'entity' => 'sync_devices', 'entity_id' => DEVICE,
        'request_id' => null, 'ip' => null, 'outcome' => 'ok', 'metadata' => '{"count":1}']);
    $ddl->insert('idempotency_keys', ['operation_id' => IDEMPOTENCY, 'tenant_id' => TENANT,
        'request_hash' => str_repeat('b', 64), 'status' => 'applied', 'response' => '{"ok":true}',
        'created_at' => $stamp, 'expires_at' => $stamp]);
    $ddl->insert('change_log', ['tenant_id' => TENANT, 'entity' => 'customers', 'id' => CUSTOMER_1,
        'row_version' => 2, 'action' => 'upsert', 'actor_type' => 'user',
        'actor_user_id' => USER_ADMIN, 'actor_device_id' => DEVICE,
        'operation_id' => IDEMPOTENCY, 'changed_at' => $stamp]);
    $ddl->insert('tenant_state', ['tenant_id' => TENANT, 'bootstrap_status' => 'done',
        'dataset_epoch' => 4, 'last_import_id' => IDEMPOTENCY,
        'last_import_hash' => str_repeat('c', 64), 'source_installation_id' => null,
        'created_at' => $stamp, 'updated_at' => $stamp]);
    $ddl->insert('periods', ['id' => PERIOD, 'tenant_id' => TENANT, 'legacy_id' => 'p0001',
        'row_version' => 2, 'created_at' => $stamp, 'updated_at' => $stamp,
        'number' => 7, 'label' => 'Period Seven', 'opened_at' => $stamp,
        'opened_by' => 'legacy_admin', 'closed_at' => null, 'closed_by' => '',
        'closed_reason' => '']);
    $ddl->insert('settings', ['id' => SETTINGS, 'tenant_id' => TENANT, 'legacy_id' => 'settings/main',
        'row_version' => 3, 'created_at' => $stamp, 'updated_at' => $stamp,
        'business_name' => 'Synthetic Business', 'business_phone' => '000',
        'watch_interval_hours' => 5, 'scheduler_enabled' => 1, 'auto_advance_default' => 0,
        'telegram_report_full_data' => 1, 'registry_fee_amount' => 12345678901,
        'registration_chat_id' => '11', 'report_chat_id' => '22', 'sound_enabled' => 0,
        'current_period_id' => PERIOD, 'links' => '[{"name":"x","url":"y"}]']);
    $ddl->insert('customers', ['id' => CUSTOMER_1, 'tenant_id' => TENANT, 'legacy_id' => 'c0001',
        'row_version' => 6, 'created_at' => $stamp, 'updated_at' => $stamp,
        'full_name' => PII_CUSTOMER_NAME, 'address' => 'Synthetic Street 1',
        'mobile' => '09120000001', 'telegram_id' => '', 'instagram_id' => '',
        'notes' => 'synthetic', 'created' => $stamp, 'updated' => $stamp]);
    $ddl->insert('customers', ['id' => CUSTOMER_2, 'tenant_id' => TENANT, 'legacy_id' => 'c0002',
        'row_version' => 2, 'created_at' => $stamp, 'updated_at' => $stamp,
        'full_name' => 'PII_MARKER_CUSTOMER_BETA', 'address' => '', 'mobile' => '09120000002',
        'telegram_id' => '', 'instagram_id' => '', 'notes' => '',
        'created' => $stamp, 'updated' => $stamp]);
    $ddl->insert('owners', ['id' => OWNER_1, 'tenant_id' => TENANT, 'legacy_id' => 'o0001',
        'row_version' => 3, 'created_at' => $stamp, 'updated_at' => $stamp,
        'first_name' => 'Synthetic', 'last_name' => 'Passenger', 'national' => PII_NATIONAL_ID,
        'birth' => '13700101', 'mobile' => '09120000003', 'card_no' => '', 'passport' => '',
        'created' => $stamp, 'updated' => $stamp]);
    $ddl->insert('identities', ['id' => IDENTITY_1, 'tenant_id' => TENANT, 'legacy_id' => 'i0001',
        'row_version' => 2, 'created_at' => $stamp, 'updated_at' => $stamp,
        'label' => 'pool-1', 'national' => '1112223334', 'birth' => '13690101',
        'mobile' => '09120000004', 'created' => $stamp, 'updated' => $stamp]);
    $ddl->insert('crm_devices', ['id' => CASE_1, 'tenant_id' => TENANT, 'legacy_id' => 'd0001',
        'row_version' => 9, 'created_at' => $stamp, 'updated_at' => $stamp,
        'name' => 'Case One', 'serial1' => 'SN0000001', 'serial2' => 'SN0000002',
        'customer_id' => CUSTOMER_1, 'owner_id' => OWNER_1, 'stage' => 'payment_done',
        'auto_advance' => 1, 'profile_id' => 'prof-1', 'handed_at' => $stamp,
        'registered_at' => $stamp, 'payment_confirmed_at' => $stamp, 'code_delivered_at' => null,
        'done_at' => null, 'last_inquiry_at' => $stamp, 'notes' => 'synthetic case',
        'advance_attempts' => 2, 'period_id' => PERIOD, 'created' => $stamp, 'updated' => $stamp,
        'skipped_stages' => '["problem"]', 'closed_at' => null, 'closed_reason' => '',
        'periodic_opt_out' => 0, 'payment_started_at' => $stamp]);
    $ddl->insert('customer_transactions', ['id' => TX_1, 'tenant_id' => TENANT,
        'legacy_id' => 'c0001/1', 'row_version' => 2, 'created_at' => $stamp, 'updated_at' => $stamp,
        'seq' => 1, 'customer_id' => CUSTOMER_1, 'at' => $stamp, 'amount' => 2500000,
        'note' => 'synthetic payment', 'kind' => 'payment', 'image' => '',
        'device_id' => CASE_1, 'voided_at' => null, 'void_reason' => '']);
    $ddl->insert('customer_transactions', ['id' => TX_2, 'tenant_id' => TENANT,
        'legacy_id' => 'c0001/2', 'row_version' => 4, 'created_at' => $stamp, 'updated_at' => $stamp,
        'seq' => 2, 'customer_id' => CUSTOMER_1, 'at' => $stamp, 'amount' => -500000,
        'note' => 'synthetic debt', 'kind' => 'debt', 'image' => '',
        'device_id' => null, 'voided_at' => $stamp, 'void_reason' => 'synthetic void']);

    // --- B3. Record the pre-upgrade projection and row hashes. --------------
    $legacyTables = Schema::tablesForVersion('001_initial');
    $before = [];
    foreach ($legacyTables as $table) {
        if ($table === 'schema_migrations') {
            continue;
        }
        $columns = projectionColumns($ddl, $table);
        $before[$table] = ['columns' => $columns, 'hash' => rowHash($ddl, $table, $columns)];
    }
    t(count($before) === 17, 'the 17 non-tracker legacy tables must be projected');
    $usersBefore = (int) scalar($ddl, 'SELECT COUNT(*) FROM users');
    $actorLinksBefore = $ddl->query(
        'SELECT id, actor_user_id, actor_device_id FROM audit_events ORDER BY id');

    // --- B4. 002 through every interruption point, step by step. ------------
    $version002 = '002_identity_foundation';
    $declared002 = Schema::migrationSteps($version002);
    t(count($declared002) === 16, '002 must declare sixteen steps');
    $ids = array_column($declared002, 'step_id');
    $firstHalf = array_slice($ids, 0, 5);
    $secondHalf = array_slice($ids, 5);
    driveStepThroughCrashes($ddl, $directory, [$version002],
        array_map(static fn (string $id): array => [$version002, $id], $firstHalf));

    // The tenants contract columns now exist. Put one row into a real
    // mismatching state so the bounded keyset backfill has actual work to do,
    // with a cursor that has to be persisted.
    $ddl->execute("UPDATE tenants SET api_contract = 'authoritative_v2' WHERE id = :id",
        ['id' => TENANT]);

    driveStepThroughCrashes($ddl, $directory, [$version002],
        array_map(static fn (string $id): array => [$version002, $id], $secondHalf));
    crashOnce($ddl, $directory, [$version002], Migrations::EVENT_BEFORE_TRACKER_INSERT,
        $version002, Migrations::VERSION_STEP);
    t((int) scalar($ddl, 'SELECT COUNT(*) FROM schema_migrations') === 1,
        '002 must not be tracked before the final run');
    $report = $runner->apply(null);
    t($report['state'] === 'COMPLETE', 'the full chain must reach COMPLETE');

    // --- B5. Preservation proof. -------------------------------------------
    // The projection recorded before the upgrade is reused verbatim; only the
    // columns 002 introduces are excluded from the after-projection. Row
    // counts would not prove preservation, and hashing SELECT * across a
    // schema that just gained columns would not either.
    $introduced = ['tenants' => ['lifecycle', 'api_contract', 'context_version',
                                 'replacement_tenant_id']];
    foreach ($before as $table => $snapshot) {
        $now = projectionColumns($ddl, $table);
        $expected = array_values(array_diff($now, $introduced[$table] ?? []));
        t($expected === $snapshot['columns'], 'column projection changed for ' . $table);
        t(rowHash($ddl, $table, $snapshot['columns']) === $snapshot['hash'],
            'row values changed for ' . $table);
    }
    t($ddl->query('SELECT id, actor_user_id, actor_device_id FROM audit_events ORDER BY id')
        === $actorLinksBefore, 'historical actor links must be unchanged');
    t((int) scalar($ddl, 'SELECT COUNT(*) FROM users') === $usersBefore,
        'the user count must be unchanged by 002');

    // --- B6. The new metadata, checked separately from the preservation. ---
    $tenantRow = $ddl->queryOne('SELECT lifecycle, api_contract, context_version,'
        . ' replacement_tenant_id, status FROM tenants WHERE id = :id', ['id' => TENANT]);
    t($tenantRow['lifecycle'] === 'active', 'existing tenants backfill to active');
    t($tenantRow['api_contract'] === 'legacy_v1', 'existing tenants backfill to legacy_v1');
    t((int) $tenantRow['context_version'] === 1, 'existing tenants backfill to context_version 1');
    t($tenantRow['replacement_tenant_id'] === null, 'replacement_tenant_id starts NULL');
    t($tenantRow['status'] === 'active', 'status is untouched and stays a separate gate');
    t((int) scalar($ddl, "SELECT COUNT(*) FROM tenants WHERE lifecycle = 'staging'") === 0,
        'no legacy tenant becomes staging');
    $normalized = new MigrationPreflight($ddl, $directory);
    // Compare through the production normaliser, not the raw engine spelling:
    // MariaDB quotes a string default and MySQL 8.0.19+ does not.
    t($normalized->normalizeDefault(scalar($ddl, 'SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS'
        . " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tenants'"
        . " AND COLUMN_NAME = 'lifecycle'")) === 'staging',
        'the lifecycle default flips only after the backfill');
    t($normalized->normalizeDefault(scalar($ddl, 'SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS'
        . " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tenants'"
        . " AND COLUMN_NAME = 'api_contract'")) === 'authoritative_v2',
        'the api_contract default flips only after the backfill');
    t((int) scalar($ddl, 'SELECT COUNT(*) FROM identity_state WHERE id = 1') === 1,
        'identity_state holds exactly its singleton row');
    t(journalStatus($ddl, $version002, 's06_tenants_backfill_legacy_contract') === 'verified',
        'the keyset backfill must end verified');
    t(journalCursor($ddl, $version002, 's06_tenants_backfill_legacy_contract') !== null,
        'the keyset backfill must persist its cursor');
    $verifiedSteps = (int) scalar($ddl,
        "SELECT COUNT(*) FROM migration_steps WHERE status = 'verified'");
    // Inventory (Ruling 24): 001 is one unit step, 002 declares sixteen and 003
    // declares ten, so a fully applied chain of three files holds twenty-seven
    // verified step rows. The per-file breakdown below keeps a single missing
    // or extra row diagnosable instead of collapsing into one total.
    t($verifiedSteps === 27, 'every step of all three files must be verified');
    foreach (['001_initial' => 1, '002_identity_foundation' => 16,
              '003_scoped_business_keys' => 10] as $inventoryVersion => $inventorySteps) {
        t(count(Schema::migrationSteps($inventoryVersion)) === $inventorySteps,
            $inventoryVersion . ' must declare its expected number of steps');
        t((int) scalar($ddl, "SELECT COUNT(*) FROM migration_steps"
            . ' WHERE migration_version = :version AND status = \'verified\'',
            ['version' => $inventoryVersion]) === $inventorySteps,
            $inventoryVersion . ' must hold one verified ledger row per declared step');
    }
    t(tableCount($ddl) === count(Schema::tables()),
        'the target must hold exactly the declared schema');
}

// ---------------------------------------------------------------------------
// C. Idempotent rerun — the documented runner contract, verbatim in shape.
// ---------------------------------------------------------------------------
$before = (int) scalar($ddl, 'SELECT COUNT(*) FROM users');
$tablesBefore = tableCount($ddl);
$runner->apply();
t($before === (int) scalar($ddl, 'SELECT COUNT(*) FROM users'),
    'applying an installed schema must not touch the users table');
t($runner->status()['state'] === 'COMPLETE', 'an installed schema reports COMPLETE');
$runner->apply();
t($runner->status()['state'] === 'COMPLETE', 'a second rerun still reports COMPLETE');
t(tableCount($ddl) === $tablesBefore, 'a rerun must not create objects');

// status() is documented never to mutate.
$journalBefore = (int) scalar($ddl, 'SELECT COUNT(*) FROM migration_steps');
for ($i = 0; $i < 3; $i++) {
    $runner->status();
}
t((int) scalar($ddl, 'SELECT COUNT(*) FROM migration_steps') === $journalBefore,
    'status() must never write to the journal');
t(tableCount($ddl) === $tablesBefore, 'status() must never create objects');

// ---------------------------------------------------------------------------
// D. Two independent connections: exactly one runner owns the lock.
// ---------------------------------------------------------------------------
$name = $runner->lockName();
t(strlen($name) <= 64, 'the advisory lock name must fit the engine limit');
t((int) scalar($second, 'SELECT GET_LOCK(:name, 0)', ['name' => $name]) === 1,
    'the second connection must be able to take the lock');
$held = false;
try {
    $runner->apply();
} catch (RuntimeException $error) {
    $held = str_starts_with($error->getMessage(), Migrations::E_LOCK_HELD);
}
t($held, 'a second runner must be refused while the lock is held');
t(tableCount($ddl) === $tablesBefore, 'a refused runner must not mutate the schema');
$second->queryValue('SELECT RELEASE_LOCK(:name)', ['name' => $name]);
$runner->apply();
t(scalar($second, 'SELECT IS_USED_LOCK(:name)', ['name' => $name]) === null,
    'the advisory lock must be released in finally');

// ---------------------------------------------------------------------------
// E. Checksum drift blocks before any DDL. Both ledgers, both restored.
// ---------------------------------------------------------------------------
$restored = [];
try {
    $restored['file'] = (string) scalar($ddl,
        'SELECT checksum FROM schema_migrations WHERE version = :version',
        ['version' => '001_initial']);
    $ddl->execute('UPDATE schema_migrations SET checksum = :checksum WHERE version = :version',
        ['checksum' => str_repeat('0', 64), 'version' => '001_initial']);
    $drifted = false;
    try {
        $runner->apply();
    } catch (RuntimeException $error) {
        $drifted = str_starts_with($error->getMessage(), Migrations::E_CHECKSUM_DRIFT);
    }
    t($drifted, 'a drifted file checksum must refuse');

    $restored['step'] = (string) scalar($ddl,
        'SELECT checksum FROM migration_steps WHERE migration_version = :version'
        . ' AND step_id = :step', ['version' => '002_identity_foundation', 'step' => 's01_users_tenant_id_unique']);
    $ddl->execute('UPDATE migration_steps SET checksum = :checksum'
        . ' WHERE migration_version = :version AND step_id = :step',
        ['checksum' => str_repeat('f', 64), 'version' => '002_identity_foundation',
         'step' => 's01_users_tenant_id_unique']);
    $drifted = false;
    try {
        $runner->apply();
    } catch (RuntimeException $error) {
        $drifted = str_starts_with($error->getMessage(), Migrations::E_CHECKSUM_DRIFT);
    }
    t($drifted, 'a drifted step checksum must refuse');
} finally {
    if (isset($restored['file'])) {
        $ddl->execute('UPDATE schema_migrations SET checksum = :checksum WHERE version = :version',
            ['checksum' => $restored['file'], 'version' => '001_initial']);
    }
    if (isset($restored['step'])) {
        $ddl->execute('UPDATE migration_steps SET checksum = :checksum'
            . ' WHERE migration_version = :version AND step_id = :step',
            ['checksum' => $restored['step'], 'version' => '002_identity_foundation',
             'step' => 's01_users_tenant_id_unique']);
    }
}
t($runner->status()['state'] === 'COMPLETE', 'the ledger must be restored');

// ---------------------------------------------------------------------------
// F. A recorded version with no file blocks; an unknown journal blocks.
// ---------------------------------------------------------------------------
$ghost = '999_phantom_recording';
try {
    $ddl->insert('schema_migrations', ['version' => $ghost, 'description' => $ghost . '.sql',
        'checksum' => str_repeat('a', 64), 'applied_at' => '2026-01-01 00:00:00',
        'applied_by' => 'fixture']);
    $missing = false;
    try {
        $runner->apply();
    } catch (RuntimeException $error) {
        $missing = str_starts_with($error->getMessage(), Migrations::E_MISSING_FILE);
    }
    t($missing, 'a recorded version with no file must refuse');
} finally {
    $ddl->execute('DELETE FROM schema_migrations WHERE version = :version', ['version' => $ghost]);
}
t($runner->status()['state'] === 'COMPLETE', 'the tracker must be restored');

t((new MigrationPreflight($ddl, $directory))->journalCompatible(
    (new MigrationPreflight($ddl, $directory))->journalColumns()), 'the live journal is compatible');
t(!(new MigrationPreflight($ddl, $directory))->journalCompatible(
    array_merge(MigrationPreflight::JOURNAL_COLUMNS, ['zz_unknown'])),
    'an unknown journal column must be recognised as incompatible');
t(!(new MigrationPreflight($ddl, $directory))->journalCompatible(
    ['migration_version', 'step_id']), 'an incomplete journal must be recognised as incompatible');
try {
    $ddl->execute('ALTER TABLE migration_steps ADD COLUMN zz_unknown_probe INT NULL');
    $unknown = false;
    try {
        $runner->apply();
    } catch (RuntimeException $error) {
        $unknown = str_starts_with($error->getMessage(), Migrations::E_UNKNOWN_JOURNAL);
    }
    t($unknown, 'an incompatible journal must refuse before any DDL');
} finally {
    $ddl->execute('ALTER TABLE migration_steps DROP COLUMN zz_unknown_probe');
}
t($runner->status()['state'] === 'COMPLETE', 'the journal must be restored');

// ---------------------------------------------------------------------------
// G. Counts-only reports: no name, no national id, no row, no raw log.
// ---------------------------------------------------------------------------
$ghostReport = null;
try {
    $ddl->insert('schema_migrations', ['version' => $ghost, 'description' => $ghost . '.sql',
        'checksum' => str_repeat('a', 64), 'applied_at' => '2026-01-01 00:00:00',
        'applied_by' => 'fixture']);
    $ghostReport = (new MigrationPreflight($ddl, $directory))->inspect();
    t($ghostReport['safe'] === false, 'a blocker must make the report unsafe');
    $codes = array_column($ghostReport['blockers'], 'code');
    t(in_array(MigrationPreflight::C_MISSING_FILE, $codes, true),
        'the missing recorded file must be reported by code');
} finally {
    $ddl->execute('DELETE FROM schema_migrations WHERE version = :version', ['version' => $ghost]);
}
t($ghostReport !== null, 'the blocker report must be produced');
$encoded = json_encode([$ghostReport, $runner->status(),
    (new MigrationPreflight($ddl, $directory))->inspect()]);
t($encoded !== false, 'the reports must be encodable');
t(strpos($encoded, PII_CUSTOMER_NAME) === false, 'a report must never carry a customer name');
t(strpos($encoded, PII_NATIONAL_ID) === false, 'a report must never carry a national id');
t(strpos($encoded, SYNTHETIC_HASH) === false, 'a report must never carry a credential');
foreach (array_merge($ghostReport['blockers'], $runner->status()['blockers']) as $blocker) {
    t(array_keys($blocker) === ['code', 'table', 'count'], 'a blocker has exactly three keys');
    t(is_int($blocker['count']) && $blocker['count'] >= 0, 'a blocker carries a count');
    t(is_string($blocker['code']) && $blocker['code'] !== '', 'a blocker carries a stable code');
}

// ---------------------------------------------------------------------------
// H. Security lifecycle events: closed set, no forbidden field, same
//    transaction as the state change, rollback when the audit write fails.
// ---------------------------------------------------------------------------
$columns = Schema::columnsFor('account_security_events');
t($columns === ['id', 'actor_account_id', 'subject_account_id', 'tenant_id',
    'actor_kind', 'event_type', 'outcome', 'created_at'],
    'the security event table carries metadata only');
foreach (['login', 'username', 'password', 'password_hash', 'token', 'token_hash',
          'request', 'payload', 'metadata', 'ip', 'national', 'mobile', 'display_name'] as $forbidden) {
    t(!in_array($forbidden, $columns, true), 'forbidden field present: ' . $forbidden);
}
t(projectionColumns($app, 'account_security_events') === $columns,
    'the physical security event table matches the declaration');
t(Schema::securityEventTypes() === ['account_created', 'password_reset', 'account_disabled',
    'account_enabled', 'legacy_linked', 'membership_assigned', 'membership_removed',
    'workspace_frozen', 'login_success', 'logout', 'password_changed'],
    'the accepted event names are the closed set from the brief');
t(Schema::column('account_security_events', 'event_type')['enum']
    === Schema::securityEventTypes(), 'the column enum is the same closed set');
t(Schema::validate('account_security_events', ['id' => AUTH_SESSION, 'actor_kind' => 'account',
    'event_type' => 'not_an_accepted_event', 'outcome' => 'success',
    'created_at' => '2026-01-01T00:00:00+00:00']) !== [],
    'an unknown event name must be rejected in PHP, not only by a CHECK');
t(Schema::validate('account_security_events', ['id' => AUTH_SESSION, 'actor_kind' => 'account',
    'event_type' => 'account_created', 'outcome' => 'success',
    'created_at' => '2026-01-01T00:00:00+00:00']) === [],
    'an accepted event name must validate');

$versionBefore = (int) scalar($app, 'SELECT context_version FROM tenants WHERE id = :id',
    ['id' => TENANT]);
$rolledBack = false;
try {
    $app->transaction(static function (Database $db) use ($versionBefore): void {
        // The state change and its audit write share ONE transaction.
        $db->execute('UPDATE tenants SET context_version = :next WHERE id = :id',
            ['next' => $versionBefore + 1, 'id' => TENANT]);
        $db->execute('INSERT INTO account_security_events'
            . ' (id, actor_account_id, subject_account_id, tenant_id, actor_kind, event_type,'
            . ' outcome, created_at)'
            . ' VALUES (:id, NULL, :subject, :tenant, :kind, :event, :outcome, :at)',
            ['id' => AUTH_SESSION, 'subject' => '00000000-0000-4000-8000-000000000099',
             'tenant' => TENANT, 'kind' => 'account', 'event' => 'account_created',
             'outcome' => 'success', 'at' => '2026-01-01 00:00:00']);
    });
} catch (PDOException $error) {
    $rolledBack = true;
    t((int) ($error->errorInfo[1] ?? 0) === 1452, 'the audit write must fail on the foreign key');
}
t($rolledBack, 'the audit write must be refused');
t((int) scalar($app, 'SELECT context_version FROM tenants WHERE id = :id', ['id' => TENANT]) === $versionBefore,
    'a failed audit write must roll back the state change');
t((int) scalar($app, 'SELECT COUNT(*) FROM account_security_events WHERE id = :id',
    ['id' => AUTH_SESSION]) === 0, 'no partial security event may survive');

$eventIds = [
    '93000000-0000-4000-8000-000000000001' => ['membership_assigned', 'private_cli', null, TENANT],
    '93000000-0000-4000-8000-000000000002' => ['workspace_frozen', 'private_cli', null, TENANT],
    '93000000-0000-4000-8000-000000000003' => ['login_success', 'account', null, TENANT],
    '93000000-0000-4000-8000-000000000004' => ['logout', 'account', null, TENANT],
];
// A synthetic account so the account-kind events have a real foreign key to
// point at; removed again below, because account creation belongs to Task 5.
const SYNTHETIC_ACCOUNT = '94000000-0000-4000-8000-000000000001';
$app->execute('INSERT INTO accounts (id, login, password_hash, display_name, status,'
    . ' must_change_password, security_version, platform_role, created_at, updated_at)'
    . ' VALUES (:id, :login, :hash, :name, :status, :must, :version, :role, :created, :updated)',
    ['id' => SYNTHETIC_ACCOUNT, 'login' => 'fixture_account', 'hash' => SYNTHETIC_HASH,
     'name' => 'Synthetic Account', 'status' => 'active', 'must' => 0, 'version' => 1,
     'role' => 'member', 'created' => '2026-01-01 00:00:00',
     'updated' => '2026-01-01 00:00:00']);
$eventIds['93000000-0000-4000-8000-000000000003'][2] = SYNTHETIC_ACCOUNT;
$eventIds['93000000-0000-4000-8000-000000000004'][2] = SYNTHETIC_ACCOUNT;

// M-FR1-1: another suite's leftovers, present while this suite's own
// assertions run. The shared fixture is consumed by five suites in one
// instance, so a row that belongs to sessions/accounts/workspaces must not
// decide whether MIGRATIONS is green. Removed again in the finally below —
// this suite leaks nothing, it only tolerates what it did not write.
$foreignAccountM = '94000000-0000-4000-8000-0000000000fe';
$foreignEventM = '93000000-0000-4000-8000-0000000000fe';
try {
    $app->execute('INSERT INTO accounts (id, login, password_hash, display_name, status,'
        . ' must_change_password, security_version, platform_role, created_at, updated_at)'
        . ' VALUES (:id, :login, :hash, :name, :status, :must, :version, :role,'
        . ' :created, :updated)',
        ['id' => $foreignAccountM, 'login' => 'fixture_foreign_account',
         'hash' => SYNTHETIC_HASH, 'name' => 'Foreign Suite Account', 'status' => 'active',
         'must' => 0, 'version' => 1, 'role' => 'member',
         'created' => '2026-01-01 00:00:00', 'updated' => '2026-01-01 00:00:00']);
    $app->execute('INSERT INTO account_security_events'
        . ' (id, actor_account_id, subject_account_id, tenant_id, actor_kind, event_type,'
        . ' outcome, created_at)'
        . ' VALUES (:id, NULL, :subject, :tenant, :kind, :event, :outcome, :at)',
        ['id' => $foreignEventM, 'subject' => $foreignAccountM, 'tenant' => TENANT,
         'kind' => 'account', 'event' => 'password_changed', 'outcome' => 'success',
         'at' => '2026-01-01 00:00:00']);
    try {
        foreach ($eventIds as $id => [$event, $kind, $subject, $tenant]) {
            $app->execute('INSERT INTO account_security_events'
                . ' (id, actor_account_id, subject_account_id, tenant_id, actor_kind,'
                . ' event_type, outcome, created_at)'
                . ' VALUES (:id, NULL, :subject, :tenant, :kind, :event, :outcome, :at)',
                ['id' => $id, 'subject' => $subject, 'tenant' => $tenant, 'kind' => $kind,
                 'event' => $event, 'outcome' => 'success', 'at' => '2026-01-01 00:00:00']);
        }
        t((int) scalar($app,
            'SELECT COUNT(*) FROM account_security_events WHERE outcome <> :outcome',
            ['outcome' => 'success']) === 0, 'every stored outcome is success');
        $ownParams = [];
        $ownList = [];
        foreach (array_keys($eventIds) as $n => $id) {
            $ownList[] = ':own' . $n;
            $ownParams['own' . $n] = (string) $id;
        }
        // Counted over THIS SUITE'S rows only: another suite's event is not
        // one of the four this block just wrote, and must not be counted here.
        t((int) scalar($app,
            'SELECT COUNT(DISTINCT event_type) FROM account_security_events WHERE id IN ('
            . implode(', ', $ownList) . ')', $ownParams) === 4,
            'all four lifecycle events are storable');
    } finally {
        foreach (array_keys($eventIds) as $id) {
            $app->execute('DELETE FROM account_security_events WHERE id = :id',
                ['id' => $id]);
        }
        $app->execute('DELETE FROM accounts WHERE id = :id', ['id' => SYNTHETIC_ACCOUNT]);
    }
    // Scoped to this suite's own ids: what these two lines prove is "this
    // suite cleaned up after ITSELF", never "the shared fixture is empty".
    t((int) scalar($app,
        'SELECT COUNT(*) FROM account_security_events WHERE id IN ('
        . implode(', ', $ownList) . ')', $ownParams) === 0,
        'the synthetic security events are removed again');
    t((int) scalar($app, 'SELECT COUNT(*) FROM accounts WHERE id = :id',
        ['id' => SYNTHETIC_ACCOUNT]) === 0,
        'the synthetic account is removed again');
    // Non-vacuous: both foreign rows are still here, so the two assertions
    // above really did pass with another suite's leftovers in the tables.
    t((int) scalar($app, 'SELECT COUNT(*) FROM account_security_events WHERE id = :id',
        ['id' => $foreignEventM]) === 1,
        'the foreign security event is still present and still tolerated');
    t((int) scalar($app, 'SELECT COUNT(*) FROM accounts WHERE id = :id',
        ['id' => $foreignAccountM]) === 1,
        'the foreign account is still present and still tolerated');
} finally {
    $app->execute('DELETE FROM account_security_events WHERE id = :id',
        ['id' => $foreignEventM]);
    $app->execute('DELETE FROM accounts WHERE id = :id', ['id' => $foreignAccountM]);
}

// The private CLI records actor_kind=private_cli with no invented account actor.
t(Schema::column('account_security_events', 'actor_kind')['enum'] ===
    ['private_cli', 'account', 'system'], 'the actor kinds are the closed set');

// ---------------------------------------------------------------------------
// I. Version domains are enforced in PHP, independent of any SQL CHECK.
//
//    002 declares CHECK (context_version >= 1) and CHECK (security_version > 0),
//    but MySQL 5.7 parses CHECK and ignores it, so on the documented support
//    floor the CHECK is not an integrity oracle and the PHP API must refuse
//    out-of-range values itself (Ruling 16 / C-1).
// ---------------------------------------------------------------------------
$versionDomains = [
    'tenants'          => 'context_version',
    'accounts'         => 'security_version',
    'account_sessions' => 'security_version',
    'identity_state'   => 'row_version',
];
foreach ($versionDomains as $table => $column) {
    $spec = Schema::column($table, $column);
    t($spec !== null, $table . '.' . $column . ' must be declared');
    t(($spec['type'] ?? null) === 'int', $table . '.' . $column . ' stays an int');
    t(($spec['min'] ?? null) === 1, $table . '.' . $column . ' must declare min=1');

    t(in_array($column . '=OUT_OF_RANGE', codes(Schema::validate($table, [$column => 0])), true),
        $table . '.' . $column . ' = 0 must be refused as OUT_OF_RANGE');
    t(in_array($column . '=OUT_OF_RANGE', codes(Schema::validate($table, [$column => -1])), true),
        $table . '.' . $column . ' = -1 must be refused as OUT_OF_RANGE');
    t(!in_array($column . '=OUT_OF_RANGE', codes(Schema::validate($table, [$column => 1])), true),
        $table . '.' . $column . ' = 1 is inside the domain');
    t(!in_array($column . '=OUT_OF_RANGE', codes(Schema::validate($table, [$column => 7])), true),
        $table . '.' . $column . ' = 7 is inside the domain');

    // A new code, not a reused one: "you sent a string" and "you sent 0 where
    // a positive version is required" are different client errors.
    t(in_array($column . '=TYPE_MISMATCH', codes(Schema::validate($table, [$column => '1'])), true),
        $table . '.' . $column . ' as a string is still TYPE_MISMATCH, not OUT_OF_RANGE');
}

// Legacy `row_version` columns must behave exactly as before. Adding a domain
// to the four new columns must not tighten any write that already worked, or
// every existing v1 writer would start failing.
$legacyRowVersions = 0;
foreach (Schema::tables() as $legacyTable) {
    $legacy = Schema::column($legacyTable, 'row_version');
    if ($legacy === null) {
        continue;
    }
    if (in_array($legacyTable, array_keys($versionDomains), true)) {
        continue;
    }
    $legacyRowVersions++;
    t(($legacy['min'] ?? null) === null && ($legacy['max'] ?? null) === null,
        $legacyTable . '.row_version must keep an unrestricted domain');
    t(!in_array('row_version=OUT_OF_RANGE',
        codes(Schema::validate($legacyTable, ['row_version' => 0])), true),
        $legacyTable . '.row_version = 0 must still validate as before');
    t(!in_array('row_version=OUT_OF_RANGE',
        codes(Schema::validate($legacyTable, ['row_version' => -5])), true),
        $legacyTable . '.row_version = -5 must still validate as before');
}
t($legacyRowVersions >= 10,
    'the regression covers every legacy row_version column, not one sample');
t(Schema::column('change_log', 'row_version')['default'] === null,
    'the legacy change_log row_version declaration is untouched');

// ---------------------------------------------------------------------------
// J. Adoption (Ruling 17 / I-1).
//
//    `host/public/setup.php` applies every migrations/*.sql through its own
//    splitter and writes only `schema_migrations`, so a database installed by
//    the documented Option A path is complete but has NO step journal. It must
//    be adopted — ledger recorded retroactively, no DDL — not refused as
//    MIGRATION_STATE_AMBIGUOUS with advice to restore a backup over a healthy
//    database. Every gate that used to block still blocks.
//
//    It needs a complete file tracker, so it only runs on an APPLIED target.
// ---------------------------------------------------------------------------
if ($state === 'APPLIED') {
    $probeJ = new MigrationPreflight($ddl, $directory);
    $fileCountJ = count($probeJ->files());
    $journalBeforeJ = journalLedger($ddl);
    $journalRowsBeforeJ = journalRowsFull($ddl);
    $trackerBeforeJ = trackerLedger($ddl, $probeJ->trackerColumns());
    $fingerprintJ = schemaFingerprint($probeJ);
    $driftVersionJ = (string) array_key_first($trackerBeforeJ);
    t($journalBeforeJ !== [], 'the populated target has a journal that can be re-recorded');
    t(count($trackerBeforeJ) === $fileCountJ, 'every known file is recorded');

    try {
        // --- J1. The 001 tracker shape (version, description, checksum,
        //         applied_at, applied_by) with an EMPTY journal: adopt.
        $ddl->execute('DELETE FROM migration_steps');
        $adoptedJ1 = (new Migrations($ddl, $directory))->apply();
        t(($adoptedJ1['migration_status']['adopted_versions'] ?? 0) === $fileCountJ,
            'a complete tracker with an empty journal adopts every version');
        t($adoptedJ1['state'] === 'COMPLETE', 'an adopted database reports COMPLETE');
        t($adoptedJ1['safe'] === true, 'an adopted database has no blockers');
        t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintJ,
            'adoption must execute no DDL on any declared table');
        t(trackerLedger($ddl, $probeJ->trackerColumns()) === $trackerBeforeJ,
            'adoption must not rewrite the file tracker');
        t(journalLedger($ddl) === $journalBeforeJ,
            'adoption must reconstitute the identical step ledger, all verified');

        // --- J5a. Adoption is excused by SUPERSESSION, not by "the last step
        //          of the chain holds" (Ruling 22). 002 s02 adds
        //          tenants.lifecycle with the legacy default and s07
        //          deliberately replaces that default, so on a correct target
        //          s02's own postcondition no longer holds. That one claim is
        //          excusable precisely because s07 re-states the same object
        //          and its own claim passes. This is the pair the adoption rule
        //          has to admit; without it a healthy setup.php target would be
        //          refused forever.
        $supersessionJ = new MigrationPreflight($ddl, $directory);
        $declaredStepsJ = [];
        foreach (Schema::migrationSteps('002_identity_foundation') as $stepJ) {
            $declaredStepsJ[(string) $stepJ['step_id']] = $stepJ;
        }
        $supersededStepJ = $declaredStepsJ['s02_tenants_add_lifecycle'] ?? null;
        $supersedingStepJ = $declaredStepsJ['s07_tenants_lifecycle_default'] ?? null;
        t($supersededStepJ !== null && $supersedingStepJ !== null,
            'the supersession pair is declared by 002');
        t($supersessionJ->evaluate($supersededStepJ['post']) === ['columns.tenants.lifecycle'],
            's02 fails on exactly one key, the column s07 re-states');
        t(!$supersessionJ->satisfies($supersededStepJ['post']),
            'the superseded s02 postcondition genuinely no longer holds');
        t($supersessionJ->satisfies($supersedingStepJ['post']),
            'the later declarer s07 holds against current state');
        t(in_array('columns.tenants.lifecycle',
            MigrationPreflight::declaredKeys($supersedingStepJ['post']), true),
            's07 re-declares the superseded key, which is what excuses s02');
        t(journalStatus($ddl, '002_identity_foundation', 's02_tenants_add_lifecycle')
            === 'verified',
            'adoption recorded the superseded step as verified');
        t(journalStatus($ddl, '002_identity_foundation', 's16_identity_state_seed')
            === 'verified',
            'adoption recorded the final step as verified');
        t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintJ,
            'the superseded chain is adopted with zero DDL');

        // --- J2. The setup.php tracker shape: only version, checksum and
        //         applied_at exist. The reader must discover the columns.
        $restoredColumns = [];
        foreach (['description', 'applied_by'] as $narrowed) {
            $restoredColumns[$narrowed] = (array) $ddl->queryOne(
                'SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA'
                . ' FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()'
                . ' AND TABLE_NAME = :table AND COLUMN_NAME = :column',
                ['table' => MigrationPreflight::TRACKER_TABLE, 'column' => $narrowed]);
        }
        try {
            $ddl->execute('ALTER TABLE schema_migrations DROP COLUMN description,'
                . ' DROP COLUMN applied_by');
            t((new MigrationPreflight($ddl, $directory))->trackerColumns()
                === ['applied_at', 'checksum', 'version'],
                'the narrowed tracker exposes exactly three columns');

            // Non-vacuous: the three-column shape really does fail 001's
            // declared tracker columns, and no later step re-states them, so
            // nothing but the deliberate Ruling 17 tracker-shape exception can
            // be carrying the adoption asserted below.
            $shapeProbeJ2 = new MigrationPreflight($ddl, $directory);
            $shapeKeysJ2 = array_values(array_filter(
                $shapeProbeJ2->evaluate(Schema::migrationSteps('001_initial')[0]['post']),
                static fn (string $keyJ2): bool => str_starts_with($keyJ2,
                    'columns.' . MigrationPreflight::TRACKER_TABLE . '.')));
            sort($shapeKeysJ2, SORT_STRING);
            t($shapeKeysJ2 === ['columns.schema_migrations.applied_by',
                'columns.schema_migrations.description'],
                'the three-column tracker really fails the declared 001 column shape');

            $ddl->execute('DELETE FROM migration_steps');
            // The baseline is taken AFTER the narrowing ALTER, so it already
            // contains the three-column tracker: what follows must not change
            // a single object, including the tracker itself.
            $fingerprintJ2 = schemaFingerprint(new MigrationPreflight($ddl, $directory));
            $adoptedJ2 = (new Migrations($ddl, $directory))->apply();
            t(($adoptedJ2['migration_status']['adopted_versions'] ?? 0) === $fileCountJ,
                'a three-column tracker is adopted exactly like the five-column one');
            t($adoptedJ2['state'] === 'COMPLETE', 'the three-column adoption reports COMPLETE');
            t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintJ2,
                'the three-column adoption executes no DDL either');
            t(journalLedger($ddl) === $journalBeforeJ,
                'the three-column adoption reconstitutes the identical ledger');
        } finally {
            foreach ($restoredColumns as $name => $fact) {
                if ($fact === [] || $fact === null) {
                    continue;
                }
                $exists = (int) scalar($ddl,
                    'SELECT COUNT(*) FROM information_schema.COLUMNS'
                    . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
                    . ' AND COLUMN_NAME = :column',
                    ['table' => MigrationPreflight::TRACKER_TABLE, 'column' => $name]);
                if ($exists === 0) {
                    $ddl->execute('ALTER TABLE schema_migrations ADD COLUMN ' . $name . ' '
                        . $fact['COLUMN_TYPE'] . ' NOT NULL DEFAULT \'\'');
                }
            }
            foreach ($trackerBeforeJ as $version => $row) {
                $ddl->execute('UPDATE schema_migrations SET description = :description,'
                    . ' applied_by = :applied_by WHERE version = :version',
                    ['description' => (string) ($row['description'] ?? ''),
                     'applied_by' => (string) ($row['applied_by'] ?? ''),
                     'version' => $version]);
            }
        }
        t(trackerLedger($ddl, $probeJ->trackerColumns()) === $trackerBeforeJ,
            'the tracker is restored column-for-column and value-for-value');

        // --- J3. Checksum drift must still refuse, and must refuse BEFORE the
        //         ledger is written: adoption is not a bypass of the gates.
        $ddl->execute('DELETE FROM migration_steps');
        $ddl->execute('UPDATE schema_migrations SET checksum = :checksum WHERE version = :version',
            ['checksum' => str_repeat('0', 64), 'version' => $driftVersionJ]);
        $driftJ = '';
        try {
            (new Migrations($ddl, $directory))->apply();
        } catch (RuntimeException $errorJ) {
            $driftJ = $errorJ->getMessage();
        }
        t(str_starts_with($driftJ, Migrations::E_CHECKSUM_DRIFT),
            'a drifted tracker row must never be adopted');
        t((int) scalar($ddl, 'SELECT COUNT(*) FROM migration_steps') === 0,
            'the drift refusal must write no journal rows');
        t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintJ,
            'the drift refusal must execute no DDL');
        $ddl->execute('UPDATE schema_migrations SET checksum = :checksum WHERE version = :version',
            ['checksum' => (string) $trackerBeforeJ[$driftVersionJ]['checksum'],
             'version' => $driftVersionJ]);

        // --- J4. An unexplained object must still refuse adoption, and must
        //         refuse before the ledger is written.
        $ddl->execute('DELETE FROM migration_steps');
        $strayJ = '__irica_b_ddl_probe_adoption';
        $untrackedJ = '';
        try {
            $ddl->execute('CREATE TABLE `' . $strayJ . '` (`id` INT NOT NULL)');
            try {
                (new Migrations($ddl, $directory))->apply();
            } catch (RuntimeException $errorJ) {
                $untrackedJ = $errorJ->getMessage();
            }
            t(str_starts_with($untrackedJ, Migrations::E_UNTRACKED_OBJECTS),
                'an object no known step explains must block adoption');
            t((int) scalar($ddl, 'SELECT COUNT(*) FROM migration_steps') === 0,
                'the untracked-object refusal must write no journal rows');
        } finally {
            $ddl->execute('DROP TABLE IF EXISTS `' . $strayJ . '`');
        }

        // --- J5b. The same tracker-complete, journal-less shape, but with a
        //          MIDDLE step's effect removed and no later step re-stating
        //          that object. Adoption must refuse (Ruling 22 / N-1).
        //
        //          Ruling 17's bound is full postcondition verification, so a
        //          partially applied database can never be adopted into a false
        //          COMPLETE. MigrationPreflight::inspect() cannot catch this
        //          afterwards either: its blockers are unexpected/missing
        //          tables, file and checksum, journal shape and step-ledger
        //          state — never a missing column, index or foreign key. If
        //          adoption waves s04 through, every step is permanently
        //          recorded verified and nothing will ever re-run the DDL.
        $ddl->execute('DELETE FROM migration_steps');
        $probeJ5 = new MigrationPreflight($ddl, $directory);
        $contextVersionJ5 = (array) $ddl->queryOne(
            'SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS'
            . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
            . ' AND COLUMN_NAME = :column',
            ['table' => 'tenants', 'column' => 'context_version']);
        $originalDefaultJ5 = (string) ($contextVersionJ5['COLUMN_DEFAULT'] ?? '');
        t(preg_match('/^-?\d+$/', $originalDefaultJ5) === 1,
            'the mid-chain column carries an integer default that can be damaged');
        $laterKeysJ5 = [];
        $reachedJ5 = false;
        foreach (Schema::migrationSteps('002_identity_foundation') as $afterJ5) {
            if ($reachedJ5) {
                foreach (MigrationPreflight::declaredKeys($afterJ5['post']) as $keyJ5) {
                    $laterKeysJ5[$keyJ5] = true;
                }
            }
            if ((string) $afterJ5['step_id'] === 's04_tenants_add_context_version') {
                $reachedJ5 = true;
            }
        }
        t($reachedJ5, 's04_tenants_add_context_version is part of the declared chain');
        t(!isset($laterKeysJ5['columns.tenants.context_version']),
            'no later step re-states the probe key, so s04 cannot be excused');
        t($probeJ5->satisfies($declaredStepsJ['s04_tenants_add_context_version']['post']),
            'the target is healthy before the damage');

        // Damage only the column DEFAULT: no object is dropped, no row is
        // rewritten, and the original value is restored in the finally below.
        $ddl->execute('ALTER TABLE `tenants` ALTER COLUMN `context_version` SET DEFAULT '
            . (string) ((int) $originalDefaultJ5 + 1));
        $probeJ5->invalidate();
        t(!$probeJ5->satisfies($declaredStepsJ['s04_tenants_add_context_version']['post']),
            'the damage genuinely unsatisfies the middle step postcondition');
        $damagedFingerprintJ5 = schemaFingerprint(new MigrationPreflight($ddl, $directory));
        try {
            $refusedJ5 = (new Migrations($ddl, $directory))->apply();
            t(($refusedJ5['migration_status']['adopted_versions'] ?? 0) === 0,
                'a mid-chain effect no later step re-states must never be adopted');
            t($refusedJ5['state'] !== 'COMPLETE',
                'the damaged target must not report COMPLETE');
            t($refusedJ5['safe'] === false, 'the damaged target must not report safe');
            t(in_array(MigrationPreflight::C_AMBIGUOUS_STEP,
                array_column($refusedJ5['blockers'], 'code'), true),
                'the damaged target must be refused as an unreconcilable chain');
            t((int) scalar($ddl, 'SELECT COUNT(*) FROM migration_steps') === 0,
                'the refusal must record no step as verified');
            t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $damagedFingerprintJ5,
                'the refusal must execute no DDL');
        } finally {
            $ddl->execute('ALTER TABLE `tenants` ALTER COLUMN `context_version` SET DEFAULT '
                . $originalDefaultJ5);
            $probeJ5->invalidate();
        }
        t($probeJ5->satisfies($declaredStepsJ['s04_tenants_add_context_version']['post']),
            'the probe column default is restored exactly');
        t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintJ,
            'the restoration leaves the schema byte-identical');

        // --- J6. A ROW-CONTENT postcondition is not an adoption gate (I-FR1-1).
        //
        //     002 s06's `rows.tenants` predicate is declared by exactly one
        //     step in the whole ledger, so object-scoped supersession has no
        //     later declarer to excuse it with — yet the product's own
        //     Workspaces::createWithin() writes precisely the row it refuses
        //     (api_contract = authoritative_v2). On the ruling-17 adoption
        //     path — complete file tracker, empty journal, i.e. exactly what
        //     host/public/setup.php Option A produces — that one legitimate
        //     workspace must not turn a correct database into
        //     MIGRATION_STATE_AMBIGUOUS with advice to restore a backup.
        //     DDL postconditions stay in the gate; only row-content facts
        //     leave it, and only for adoption.
        $ddl->execute('DELETE FROM migration_steps');
        $probeJ6 = new MigrationPreflight($ddl, $directory);
        $declaredStepsJ6 = [];
        foreach (Schema::migrationSteps('002_identity_foundation') as $stepJ6) {
            $declaredStepsJ6[(string) $stepJ6['step_id']] = $stepJ6;
        }
        $backfillStepJ6 = $declaredStepsJ6['s06_tenants_backfill_legacy_contract'] ?? null;
        t($backfillStepJ6 !== null, '002 s06 declares the tenant row predicate');
        t(MigrationPreflight::declaredKeys($backfillStepJ6['post']) === ['rows.tenants'],
            'the tenant row predicate is declared by that one step alone');
        $specJ6 = $backfillStepJ6['post']['rows']['tenants'] ?? null;
        t(is_array($specJ6) && isset($specJ6['mismatch']),
            '002 s06 declares a row-content mismatch predicate');
        // The exact row Workspaces::createWithin() inserts — never a copy of
        // an existing tenant, so no fixture value is read or echoed.
        $workspaceJ6 = 'f0000000-0000-4000-8000-000000000001';
        try {
            $ddl->execute('INSERT INTO tenants (id, slug, name, status, lifecycle,'
                . ' api_contract, context_version, created_at, updated_at)'
                . ' VALUES (:id, :slug, :name, :status, :lifecycle, :contract, :version,'
                . ' :created, :updated)',
                ['id' => $workspaceJ6, 'slug' => 'irica-integrity-fix1-workspace',
                 'name' => 'Integrity Fix 1 Workspace', 'status' => 'active',
                 'lifecycle' => 'active', 'contract' => 'authoritative_v2', 'version' => 1,
                 'created' => '2026-01-01 00:00:00', 'updated' => '2026-01-01 00:00:00']);
            $probeJ6->invalidate();
            t(!$probeJ6->satisfies($backfillStepJ6['post']),
                'the workspace tenant genuinely unsatisfies the row predicate');
            // Non-vacuous and leak-proof: read the declared tuple and show that
            // THIS row is one of the violators, so the assertion above is not
            // riding on a leaked tenant some other suite happened to leave.
            $violatingJ6 = 0;
            foreach ($specJ6['mismatch'] as $columnJ6 => $valueJ6) {
                $quotedJ6 = '`' . str_replace('`', '``', (string) $columnJ6) . '`';
                $rowJ6 = scalar($ddl, 'SELECT ' . $quotedJ6 . ' FROM tenants WHERE id = :id',
                    ['id' => $workspaceJ6]);
                if ((string) $rowJ6 !== (string) $valueJ6) {
                    $violatingJ6++;
                }
            }
            t($violatingJ6 > 0,
                'the workspace row itself violates the declared row predicate');
            t((int) scalar($ddl, 'SELECT COUNT(*) FROM migration_steps') === 0,
                'the adoption target has an emptied journal');
            $adoptedJ6 = (new Migrations($ddl, $directory))->apply();
            t(($adoptedJ6['migration_status']['adopted_versions'] ?? 0) === $fileCountJ,
                'a tenant the product itself created must not block adoption');
            t($adoptedJ6['state'] === 'COMPLETE',
                'an adopted database with a product workspace still reports COMPLETE');
            t($adoptedJ6['safe'] === true,
                'an adopted database with a product workspace has no blockers');
            t(journalLedger($ddl) === $journalBeforeJ,
                'the adoption reconstitutes the identical step ledger');
            t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintJ,
                'the adoption still executes no DDL');
            t(trackerLedger($ddl, $probeJ->trackerColumns()) === $trackerBeforeJ,
                'the adoption still leaves the file tracker untouched');
        } finally {
            $ddl->execute('DELETE FROM tenants WHERE id = :id', ['id' => $workspaceJ6]);
        }
        $probeJ6->invalidate();
        t((int) scalar($ddl, 'SELECT COUNT(*) FROM tenants WHERE id = :id',
            ['id' => $workspaceJ6]) === 0,
            'the workspace tenant is removed again');
        // Deliberately NOT asserted here: "every tenant is legacy-shaped
        // again". The shared fixture may legitimately hold another suite's
        // workspace, and re-imposing a global content invariant would put the
        // cross-suite brittleness this section exists to remove straight back.

        // --- J7. NEGATIVE CONTROL (ruling 49): taking row-content keys out
        //     of the adoption gate must NOT take the gate out. Adoption still
        //     has to refuse a genuinely wrong schema, so this mistypes a
        //     DDL postcondition — the declared TYPE of a column no later step
        //     re-states — and expects the refusal, with nothing recorded and
        //     no DDL executed beyond the damage itself.
        $ddl->execute('DELETE FROM migration_steps');
        $probeJ7 = new MigrationPreflight($ddl, $directory);
        $chainJ7Steps = [];
        foreach (array_keys($probeJ->files()) as $versionJ7) {
            foreach (Schema::migrationSteps((string) $versionJ7) as $stepJ7) {
                $chainJ7Steps[] = $stepJ7;
            }
        }
        $probeKeyJ7 = 'columns.accounts.created_at';
        $createAccountsJ7 = null;
        foreach ($chainJ7Steps as $stepJ7) {
            if ((string) $stepJ7['step_id'] === 's10_accounts_create') {
                $createAccountsJ7 = $stepJ7;
                break;
            }
        }
        t($createAccountsJ7 !== null, '002 s10 declares the accounts column shape');
        t(in_array($probeKeyJ7, MigrationPreflight::declaredKeys($createAccountsJ7['post']),
            true), 'the probe key is a DDL postcondition of s10');
        $laterKeysJ7 = [];
        $reachedJ7 = false;
        foreach ($chainJ7Steps as $afterJ7) {
            if ($reachedJ7) {
                foreach (MigrationPreflight::declaredKeys($afterJ7['post']) as $keyJ7) {
                    $laterKeysJ7[$keyJ7] = true;
                }
            }
            if ((string) $afterJ7['step_id'] === 's10_accounts_create') {
                $reachedJ7 = true;
            }
        }
        t($reachedJ7, 's10_accounts_create is part of the declared chain');
        t(!isset($laterKeysJ7[$probeKeyJ7]),
            'no later step re-states the probe key, so s10 cannot be excused');
        t($probeJ7->satisfies($createAccountsJ7['post']),
            'the target is healthy before the damage');
        $columnJ7 = (array) $ddl->queryOne(
            'SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS'
            . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column',
            ['table' => 'accounts', 'column' => 'created_at']);
        t($columnJ7 !== [] && array_key_exists('COLUMN_TYPE', $columnJ7),
            'the probe column can be snapshotted');
        try {
            // Mistype the column: still present, still NOT NULL, wrong type.
            // `accounts` is empty on this target, so the conversion is
            // lossless and the original definition restores exactly.
            $ddl->execute('ALTER TABLE `accounts` MODIFY COLUMN `created_at`'
                . ' DATETIME(6) NOT NULL');
            $probeJ7->invalidate();
            t(!$probeJ7->satisfies($createAccountsJ7['post']),
                'the mistyped column genuinely unsatisfies the DDL postcondition');
            t(in_array($probeKeyJ7, $probeJ7->evaluate($createAccountsJ7['post']), true),
                'the failing key is the mistyped column, a DDL fact');
            $refusedJ7 = (new Migrations($ddl, $directory))->apply();
            t(($refusedJ7['migration_status']['adopted_versions'] ?? 0) === 0,
                'a mistyped column must still refuse adoption');
            t($refusedJ7['state'] !== 'COMPLETE',
                'the mistyped target must not report COMPLETE');
            t($refusedJ7['safe'] === false, 'the mistyped target must not report safe');
            t(in_array(MigrationPreflight::C_AMBIGUOUS_STEP,
                array_column($refusedJ7['blockers'], 'code'), true),
                'the mistyped target must be refused as an unreconcilable chain');
            t((int) scalar($ddl, 'SELECT COUNT(*) FROM migration_steps') === 0,
                'the refusal must record no step as verified');
        } finally {
            $ddl->execute('ALTER TABLE `accounts` MODIFY COLUMN `created_at` '
                . strtoupper((string) $columnJ7['COLUMN_TYPE'])
                . ((string) $columnJ7['IS_NULLABLE'] === 'NO' ? ' NOT NULL' : ' NULL')
                . ($columnJ7['COLUMN_DEFAULT'] === null
                    ? ''
                    : ' DEFAULT ' . (string) $columnJ7['COLUMN_DEFAULT']));
            $probeJ7->invalidate();
        }
        $restoredJ7 = (array) $ddl->queryOne(
            'SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS'
            . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column',
            ['table' => 'accounts', 'column' => 'created_at']);
        t($restoredJ7 === $columnJ7, 'the probe column is restored exactly');
        t($probeJ7->satisfies($createAccountsJ7['post']),
            'the target is healthy again after the restoration');
        t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintJ,
            'the restoration leaves the schema byte-identical');
    } finally {
        // Whatever happened above — including a failing assertion — the
        // populated target ends exactly as it started: Task 3's integrity suite
        // consumes this state. Both ledgers are restored from the snapshots,
        // never by asking the code under test to fix itself.
        $ddl->execute('UPDATE schema_migrations SET checksum = :checksum WHERE version = :version',
            ['checksum' => (string) $trackerBeforeJ[$driftVersionJ]['checksum'],
             'version' => $driftVersionJ]);
        restoreJournal($ddl, $journalRowsBeforeJ);
        foreach ($trackerBeforeJ as $version => $row) {
            $ddl->execute('UPDATE schema_migrations SET description = :description,'
                . ' applied_by = :applied_by WHERE version = :version',
                ['description' => (string) ($row['description'] ?? ''),
                 'applied_by' => (string) ($row['applied_by'] ?? ''),
                 'version' => $version]);
        }
        t($runner->status()['state'] === 'COMPLETE', 'section J leaves the ledger COMPLETE');
        t(journalLedger($ddl) === $journalBeforeJ, 'section J leaves the identical ledger');
    }
}

// ---------------------------------------------------------------------------
// K. MIGRATION_STATE_AMBIGUOUS, the protocol's central refusal (Ruling 18).
//
//    Ambiguity is a ledger that cannot be reconciled with metadata: the step
//    says `applied` but neither its pre- nor its post-state holds. Constructing
//    it as "tables present and no journal" would now be the adoption path, so
//    this case is built from a real incompatible step history instead.
// ---------------------------------------------------------------------------
if ($state === 'APPLIED') {
    $probeK = new MigrationPreflight($ddl, $directory);
    $ambiguousStepK = null;
    foreach (Schema::migrationSteps('002_identity_foundation') as $stepK) {
        if (!$probeK->satisfies($stepK['post']) && !$probeK->satisfies($stepK['pre'])) {
            $ambiguousStepK = (string) $stepK['step_id'];
            break;
        }
    }
    t($ambiguousStepK !== null,
        'the migrated chain contains a step superseded by a later one');
    $journalBeforeK = journalLedger($ddl);
    $fingerprintK = schemaFingerprint($probeK);
    $statusBeforeK = (string) $journalBeforeK['002_identity_foundation/' . $ambiguousStepK];
    $trackerRowK = (array) $ddl->queryOne(
        'SELECT description, checksum, applied_at, applied_by FROM schema_migrations'
        . ' WHERE version = :version', ['version' => '002_identity_foundation']);
    t(isset($trackerRowK['checksum'], $trackerRowK['applied_at']),
        'the 002 tracker row can be snapshotted before it is removed');
    try {
        $ddl->execute('UPDATE migration_steps SET status = \'applied\''
            . ' WHERE migration_version = :version AND step_id = :step',
            ['version' => '002_identity_foundation', 'step' => $ambiguousStepK]);
        $ddl->execute('DELETE FROM schema_migrations WHERE version = :version',
            ['version' => '002_identity_foundation']);
        // The ledger as the runner must find it: one step claiming `applied`
        // that neither pre- nor post-state can support, and a file no longer
        // in the tracker. Everything below is judged against THIS state, so
        // "repaired nothing" means "still exactly this", not "back to before".
        $journalIntendedK = journalLedger($ddl);
        t(($journalIntendedK['002_identity_foundation/' . $ambiguousStepK] ?? '') === 'applied',
            'the ambiguous construction actually took hold');

        $refusalsK = [];
        for ($attemptK = 0; $attemptK < 2; $attemptK++) {
            try {
                (new Migrations($ddl, $directory))->apply();
                $refusalsK[] = '';
            } catch (RuntimeException $errorK) {
                $refusalsK[] = $errorK->getMessage();
            }
        }
        t(str_starts_with($refusalsK[0], Migrations::E_AMBIGUOUS),
            'an unreconcilable step history must refuse as MIGRATION_STATE_AMBIGUOUS');
        t(str_starts_with($refusalsK[1], Migrations::E_AMBIGUOUS),
            'a second run must still refuse, not repair');
        t(journalLedger($ddl) === $journalIntendedK,
            'the refusal must repair nothing in the ledger');
        t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintK,
            'the refusal must execute no DDL');
        t((int) scalar($ddl, 'SELECT COUNT(*) FROM schema_migrations WHERE version = :version',
            ['version' => '002_identity_foundation']) === 0,
            'the refusal must not re-record the file tracker either');
    } finally {
        $ddl->execute('DELETE FROM schema_migrations WHERE version = :version',
            ['version' => '002_identity_foundation']);
        $ddl->execute('INSERT INTO schema_migrations (version, description, checksum,'
            . ' applied_at, applied_by) VALUES (:version, :description, :checksum,'
            . ' :applied_at, :applied_by)',
            ['version' => '002_identity_foundation',
             'description' => (string) ($trackerRowK['description'] ?? ''),
             'checksum' => (string) $trackerRowK['checksum'],
             'applied_at' => (string) $trackerRowK['applied_at'],
             'applied_by' => (string) ($trackerRowK['applied_by'] ?? '')]);
        $ddl->execute('UPDATE migration_steps SET status = :status'
            . ' WHERE migration_version = :version AND step_id = :step',
            ['status' => $statusBeforeK, 'version' => '002_identity_foundation',
             'step' => $ambiguousStepK]);
    }
    t($runner->status()['state'] === 'COMPLETE', 'section K leaves the ledger COMPLETE');
    t(journalLedger($ddl) === $journalBeforeK, 'section K restores the identical ledger');
}

print("MIGRATIONS_SCENARIO_OK state=" . ($state === 'EMPTY' ? 'empty' : 'applied')
    . " engine=" . $ddl->serverVersion() . " tables=" . tableCount($ddl) . "\n");
