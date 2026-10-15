<?php
/**
 * test_integrity.php — Phase B scoped-relationship suite (`--suite integrity`).
 *
 * Runs against the separately provisioned fixture instance only. It never reads
 * application configuration, never touches production, and never prints a
 * credential, a row value, a name or a national id: a failure is always one of
 * the fixed FIXTURE_* labels emitted by bootstrap.php.
 *
 * What this suite proves
 *   001 models every business relationship as an ID-only foreign key, which
 *   proves the referenced row EXISTS but not that it belongs to the SAME tenant
 *   as the row pointing at it. This suite drives all six same-tenant
 *   relationships through INSERT and UPDATE, in both directions, and proves:
 *
 *     1. Before 003, the ID-only foreign key ADMITS the cross-tenant row.
 *        The matrix runs unconditionally, so on a target without 003 it fails
 *        with CROSS_TENANT_REFERENCE_ACCEPTED — the RED this suite was written
 *        against, not a duplicate UUID and not a missing required field.
 *     2. A pre-existing cross-tenant row BLOCKS installation of the new
 *        constraints, with a counts-only report, and blocking repairs,
 *        deletes and truncates nothing. That proof runs on the REAL
 *        installation path — Migrations::apply(), which is what
 *        host/bin/migrate.php executes — not only on the dedicated entry
 *        point, and it runs on an ALREADY-APPLIED target too, because the
 *        gate must be consulted by the production path every time. A negative
 *        control drives MigrationPreflight::scopedIntegrity() directly and
 *        fails if the gate ever becomes degenerate (always safe).
 *     3. After 003, every cross-tenant INSERT and UPDATE is refused with
 *        SQLSTATE 23000 / errno 1452 — never 1062 (duplicate key), never
 *        1048 (NOT NULL), never a CHECK errno — and the named constraint is
 *        the one that fired, proven by its ordered
 *        information_schema.KEY_COLUMN_USAGE columns.
 *     4. The same-tenant positive control carries the same columns and
 *        succeeds, so a rejection can never be explained by a missing required
 *        field or a reused UUID.
 *     5. A permitted (NULL) foreign key still inserts, and a tombstoned parent
 *        (deleted_at set) still resolves — a soft delete is not a SQL delete.
 *     6. `--status` (MigrationPreflight::inspect()) surfaces the same
 *        reference fault as a blocker, so an operator sees the code, the table
 *        and the count before any ALTER is attempted.
 *
 * Nothing here drops or truncates a table. Synthetic rows this suite creates
 * are removed by this suite, in dependency order, in a `finally` block; the
 * product is never asked to "repair" anything.
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
foreach (['Schema', 'Database', 'MigrationPreflight', 'Migrations'] as $classFile) {
    require_once $repoRoot . '/host/src/' . $classFile . '.php';
}

const MIG_DIR_NAME = 'host/migrations';
const SCOPED_VERSION = '003_scoped_business_keys';

/** One fixed synthetic timestamp; no clock, no locale, no drift. */
const STAMP = '2026-01-01T00:00:00+00:00';

/** Synthetic markers that must NEVER appear in a structured report. */
const PII_CUSTOMER_NAME = 'PII_MARKER_CUSTOMER_ALPHA';
const PII_NATIONAL_ID = '9876543210';

/**
 * The six same-tenant relationships this suite enforces, exactly as the task
 * brief lists them.
 *
 * Columns are carried as an ORDERED list, never flattened into a set: the
 * composite foreign key (tenant_id, customer_id) and the key
 * (customer_id, tenant_id) are different constraints and only the first is the
 * one 003 installs. Every assertion below compares the ordered list.
 *
 * `parent_seed` names the seed key under each synthetic tenant (…_a / …_b).
 *
 * @var array<string, array{constraint: string, child: string,
 *   child_columns: list<string>, parent: string, parent_columns: list<string>,
 *   parent_key: string, fk_column: string, parent_seed: string}>
 */
const RELATIONSHIPS = [
    'settings_period' => [
        'constraint' => 'fk_settings_period_scoped',
        'child' => 'settings', 'child_columns' => ['tenant_id', 'current_period_id'],
        'parent' => 'periods', 'parent_columns' => ['tenant_id', 'id'],
        'parent_key' => 'uq_periods_tenant_id', 'fk_column' => 'current_period_id',
        'parent_seed' => 'period',
    ],
    'crm_device_customer' => [
        'constraint' => 'fk_crm_devices_customer_scoped',
        'child' => 'crm_devices', 'child_columns' => ['tenant_id', 'customer_id'],
        'parent' => 'customers', 'parent_columns' => ['tenant_id', 'id'],
        'parent_key' => 'uq_customers_tenant_id', 'fk_column' => 'customer_id',
        'parent_seed' => 'customer',
    ],
    'crm_device_owner' => [
        'constraint' => 'fk_crm_devices_owner_scoped',
        'child' => 'crm_devices', 'child_columns' => ['tenant_id', 'owner_id'],
        'parent' => 'owners', 'parent_columns' => ['tenant_id', 'id'],
        'parent_key' => 'uq_owners_tenant_id', 'fk_column' => 'owner_id',
        'parent_seed' => 'owner',
    ],
    'crm_device_period' => [
        'constraint' => 'fk_crm_devices_period_scoped',
        'child' => 'crm_devices', 'child_columns' => ['tenant_id', 'period_id'],
        'parent' => 'periods', 'parent_columns' => ['tenant_id', 'id'],
        'parent_key' => 'uq_periods_tenant_id', 'fk_column' => 'period_id',
        'parent_seed' => 'period',
    ],
    'tx_customer' => [
        'constraint' => 'fk_customer_tx_customer_scoped',
        'child' => 'customer_transactions', 'child_columns' => ['tenant_id', 'customer_id'],
        'parent' => 'customers', 'parent_columns' => ['tenant_id', 'id'],
        'parent_key' => 'uq_customers_tenant_id', 'fk_column' => 'customer_id',
        'parent_seed' => 'customer',
    ],
    'tx_device' => [
        'constraint' => 'fk_customer_tx_device_scoped',
        'child' => 'customer_transactions', 'child_columns' => ['tenant_id', 'device_id'],
        'parent' => 'crm_devices', 'parent_columns' => ['tenant_id', 'id'],
        'parent_key' => 'uq_crm_devices_tenant_id', 'fk_column' => 'device_id',
        'parent_seed' => 'device',
    ],
];

/** The unique parent keys a composite foreign key needs, in key order. */
const PARENT_KEYS = [
    'customers' => 'uq_customers_tenant_id',
    'owners' => 'uq_owners_tenant_id',
    'periods' => 'uq_periods_tenant_id',
    'crm_devices' => 'uq_crm_devices_tenant_id',
];

/**
 * The ID-only historical keys 001 declared for the same six relationships.
 * This task adds a stronger guarantee; it retires nothing, so every one of
 * them must still be there afterwards.
 *
 * @var array<string, array{table: string, column: string, parent: string}>
 */
const HISTORICAL_KEYS = [
    'fk_settings_period' => ['table' => 'settings', 'column' => 'current_period_id',
                             'parent' => 'periods'],
    'fk_crm_devices_customer' => ['table' => 'crm_devices', 'column' => 'customer_id',
                                  'parent' => 'customers'],
    'fk_crm_devices_owner' => ['table' => 'crm_devices', 'column' => 'owner_id',
                               'parent' => 'owners'],
    'fk_crm_devices_period' => ['table' => 'crm_devices', 'column' => 'period_id',
                                'parent' => 'periods'],
    'fk_customer_tx_customer' => ['table' => 'customer_transactions',
                                  'column' => 'customer_id', 'parent' => 'customers'],
    'fk_customer_tx_device' => ['table' => 'customer_transactions', 'column' => 'device_id',
                                'parent' => 'crm_devices'],
];

/** The tables a refusal could have touched. Counts only. */
const COUNTED_TABLES = ['settings', 'crm_devices', 'customer_transactions',
                        'customers', 'owners', 'periods', 'tenants'];

/**
 * The two synthetic tenants and their parents. File-scope, read by the row
 * helpers below exactly as test_migrations.php reads its file-scope `$ddl`.
 *
 * @var array<string, string>
 */
$ids = [];

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

/** A fresh RFC-4122-shaped UUID. Never reused between attempts. */
function uuid(): string
{
    $bytes = random_bytes(16);
    $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
    $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
    $hex = bin2hex($bytes);
    return substr($hex, 0, 8) . '-' . substr($hex, 8, 4) . '-' . substr($hex, 12, 4)
        . '-' . substr($hex, 16, 4) . '-' . substr($hex, 20, 12);
}

// ---------------------------------------------------------------------------
// Row builders. Every builder returns a COMPLETE, valid row: if a write is
// refused, the only reason left is the foreign key under test.
// ---------------------------------------------------------------------------

function tenantRow(string $uuid, string $slug): array
{
    return ['id' => $uuid, 'slug' => $slug, 'name' => 'Synthetic ' . $slug,
            'status' => 'active', 'created_at' => STAMP, 'updated_at' => STAMP];
}

function periodRow(string $uuid, string $tenant): array
{
    return ['id' => $uuid, 'tenant_id' => $tenant, 'legacy_id' => null,
            'row_version' => 1, 'created_at' => STAMP, 'updated_at' => STAMP,
            'number' => 1, 'label' => 'Synthetic Period', 'opened_at' => STAMP,
            'opened_by' => 'system'];
}

function customerRow(string $uuid, string $tenant, array $extra = []): array
{
    return array_merge(['id' => $uuid, 'tenant_id' => $tenant, 'legacy_id' => null,
        'created_at' => STAMP, 'updated_at' => STAMP,
        'full_name' => PII_CUSTOMER_NAME, 'address' => '', 'mobile' => '09120000000',
        'telegram_id' => '', 'instagram_id' => '', 'notes' => '',
        'created' => STAMP, 'updated' => STAMP], $extra);
}

function ownerRow(string $uuid, string $tenant): array
{
    return ['id' => $uuid, 'tenant_id' => $tenant, 'legacy_id' => null,
            'created_at' => STAMP, 'updated_at' => STAMP,
            'first_name' => 'Synthetic', 'last_name' => 'Passenger',
            'national' => PII_NATIONAL_ID, 'birth' => '13700101',
            'mobile' => '09120000003', 'card_no' => '', 'passport' => '',
            'created' => STAMP, 'updated' => STAMP];
}

function deviceRow(string $uuid, string $tenant, array $extra = []): array
{
    return array_merge(['id' => $uuid, 'tenant_id' => $tenant, 'legacy_id' => null,
        'created_at' => STAMP, 'updated_at' => STAMP, 'name' => 'Synthetic Case',
        'serial1' => strtoupper(substr(str_replace('-', '', $uuid), 0, 12)),
        'customer_id' => null, 'owner_id' => null, 'period_id' => null,
        'created' => STAMP, 'updated' => STAMP], $extra);
}

function transactionRow(string $uuid, string $tenant, array $extra = []): array
{
    return array_merge(['id' => $uuid, 'tenant_id' => $tenant, 'legacy_id' => null,
        'created_at' => STAMP, 'updated_at' => STAMP, 'seq' => 1,
        'customer_id' => null, 'at' => STAMP, 'amount' => 1000, 'note' => '',
        'kind' => 'payment', 'image' => '', 'device_id' => null], $extra);
}

function settingsRow(string $uuid, string $tenant, array $extra = []): array
{
    return array_merge(['id' => $uuid, 'tenant_id' => $tenant, 'legacy_id' => null,
        'created_at' => STAMP, 'updated_at' => STAMP,
        'business_name' => 'Synthetic Business', 'business_phone' => '000',
        'links' => null], $extra);
}

/**
 * One complete case row for one relationship of the six-row matrix.
 *
 * `$crossTenant` is the ONLY thing that changes between a negative case and
 * its same-tenant positive control: the row's own tenant stays tenant A, and
 * the single foreign-key column named by the relationship points at tenant B's
 * parent instead of tenant A's. Every call mints a fresh UUID, so a negative
 * INSERT can never collide with a row an earlier attempt inserted — a
 * duplicate-key 1062 would be worthless as evidence.
 *
 * @param array<string, string> $ids
 * @return array{table: string, fk: string, parent_id: string, row: array<string, mixed>}
 */
function caseRow(string $relationship, bool $crossTenant, array $ids): array
{
    $spec = RELATIONSHIPS[$relationship];
    $tenant = $ids['tenant_a'];
    $parent = $ids[$spec['parent_seed'] . ($crossTenant ? '_b' : '_a')];
    switch ($relationship) {
        case 'settings_period':
            $row = settingsRow(uuid(), $tenant, ['current_period_id' => $parent]);
            break;
        case 'tx_customer':
            $row = transactionRow(uuid(), $tenant,
                ['customer_id' => $parent, 'seq' => random_int(1, 2000000000)]);
            break;
        case 'tx_device':
            // The device is the reference under test; the customer is a plain
            // same-tenant value so it can never be the reason for a refusal.
            $row = transactionRow(uuid(), $tenant,
                ['customer_id' => $ids['customer_a'], 'device_id' => $parent,
                 'seq' => random_int(1, 2000000000)]);
            break;
        default:
            $row = deviceRow(uuid(), $tenant);
            $row[$spec['fk_column']] = $parent;
            break;
    }
    return ['table' => $spec['child'], 'fk' => $spec['fk_column'],
            'parent_id' => $parent, 'row' => $row];
}

/**
 * Ruling 23 — a LOCAL function, not `BFixture::crossTenantCaseRow()`.
 *
 * `BFixture` is declared in the frozen bootstrap.php and PHP cannot redeclare
 * or extend it from this file, so the helper the brief sketches lives here.
 *
 * Returns every required valid column of one synthetic case row drawn from TWO
 * synthetic tenants. Apart from a fresh per-attempt UUID and the single
 * tenant-scoped foreign key, the returned row is identical to its same-tenant
 * positive control (`sameTenantCaseRow()`), so a rejection can only be
 * explained by the tenant mismatch.
 *
 * @return array<string, mixed>
 */
function crossTenantCaseRow(string $relationship = 'crm_device_customer'): array
{
    global $ids;
    return caseRow($relationship, true, $ids)['row'];
}

/** The same row, same tenant, same columns: the positive control. */
function sameTenantCaseRow(string $relationship = 'crm_device_customer'): array
{
    global $ids;
    return caseRow($relationship, false, $ids)['row'];
}

// ---------------------------------------------------------------------------
// information_schema readers. Ordered lists only — a composite key compared as
// a set would accept the columns in the wrong order.
// ---------------------------------------------------------------------------

/** @return list<string> the child columns of ONE named constraint, in order */
function constraintChildColumns(Database $db, string $table, string $constraint): array
{
    $rows = $db->query(
        'SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
        . ' AND CONSTRAINT_NAME = :constraint AND REFERENCED_TABLE_NAME IS NOT NULL'
        . ' ORDER BY ORDINAL_POSITION',
        ['table' => $table, 'constraint' => $constraint]);
    return array_map(static fn (array $row): string => (string) $row['COLUMN_NAME'], $rows);
}

/** @return array{table: string, columns: list<string>} the parent side, in order */
function constraintParent(Database $db, string $table, string $constraint): array
{
    $rows = $db->query(
        'SELECT REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME'
        . ' FROM information_schema.KEY_COLUMN_USAGE'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
        . ' AND CONSTRAINT_NAME = :constraint AND REFERENCED_TABLE_NAME IS NOT NULL'
        . ' ORDER BY ORDINAL_POSITION',
        ['table' => $table, 'constraint' => $constraint]);
    $columns = [];
    $referenced = '';
    foreach ($rows as $row) {
        $referenced = (string) $row['REFERENCED_TABLE_NAME'];
        $columns[] = (string) $row['REFERENCED_COLUMN_NAME'];
    }
    return ['table' => $referenced, 'columns' => $columns];
}

/** @return list<string> the columns of ONE index, in SEQ_IN_INDEX order */
function indexColumns(Database $db, string $table, string $index): array
{
    $rows = $db->query(
        'SELECT COLUMN_NAME FROM information_schema.STATISTICS'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
        . ' AND INDEX_NAME = :index ORDER BY SEQ_IN_INDEX',
        ['table' => $table, 'index' => $index]);
    return array_map(static fn (array $row): string => (string) $row['COLUMN_NAME'], $rows);
}

function indexIsUnique(Database $db, string $table, string $index): bool
{
    $value = scalar($db, 'SELECT NON_UNIQUE FROM information_schema.STATISTICS'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
        . ' AND INDEX_NAME = :index LIMIT 1',
        ['table' => $table, 'index' => $index]);
    return $value !== null && (int) $value === 0;
}

function constraintCount(Database $db, string $table, string $constraint): int
{
    return (int) scalar($db, 'SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE'
        . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
        . ' AND CONSTRAINT_NAME = :constraint AND REFERENCED_TABLE_NAME IS NOT NULL',
        ['table' => $table, 'constraint' => $constraint]);
}

// ---------------------------------------------------------------------------
// Write assertions.
// ---------------------------------------------------------------------------

/**
 * A cross-tenant write MUST fail as a foreign-key violation: SQLSTATE 23000
 * with errno 1452. Anything weaker is not evidence — errno 1062 is a duplicate
 * key, 1048 is NOT NULL, and a CHECK failure is a different errno entirely, so
 * asserting 1452 exactly rules all of them out.
 */
function expectForeignKeyRejection(callable $operation, string $what): void
{
    try {
        $operation();
    } catch (PDOException $error) {
        $state = (string) ($error->errorInfo[0] ?? $error->getCode());
        $errno = (int) ($error->errorInfo[1] ?? 0);
        t($state === '23000', $what . ': SQLSTATE must be 23000, got ' . $state);
        t($errno === 1452,
            $what . ': errno must be 1452 (foreign key fails), got ' . $errno
            . ' — 1062 is a duplicate key and is not this failure');
        return;
    }
    throw new RuntimeException('CROSS_TENANT_REFERENCE_ACCEPTED: ' . $what);
}

/** The positive control: the same columns, same tenant, must be accepted. */
function expectAccepted(callable $operation, string $what): void
{
    try {
        $operation();
    } catch (PDOException $error) {
        $state = (string) ($error->errorInfo[0] ?? $error->getCode());
        $errno = (int) ($error->errorInfo[1] ?? 0);
        t(false, $what . ': the positive control was refused with ' . $state . '/' . $errno);
    }
}

// ---------------------------------------------------------------------------
// Synthetic population: two tenants, and one parent of every kind under each.
// ---------------------------------------------------------------------------

function seedSynthetic(Database $db, array &$ids): void
{
    $ids = [
        'tenant_a' => uuid(), 'tenant_b' => uuid(),
        'customer_a' => uuid(), 'customer_b' => uuid(),
        'customer_tombstone' => uuid(),
        'owner_a' => uuid(), 'owner_b' => uuid(),
        'period_a' => uuid(), 'period_b' => uuid(),
        'device_a' => uuid(), 'device_b' => uuid(),
    ];
    $db->insert('tenants', tenantRow($ids['tenant_a'],
        'integrity-a-' . substr($ids['tenant_a'], 0, 8)));
    $db->insert('tenants', tenantRow($ids['tenant_b'],
        'integrity-b-' . substr($ids['tenant_b'], 0, 8)));

    foreach (['a', 'b'] as $side) {
        $tenant = $ids['tenant_' . $side];
        $db->insert('periods', periodRow($ids['period_' . $side], $tenant));
        $db->insert('customers', customerRow($ids['customer_' . $side], $tenant));
        $db->insert('owners', ownerRow($ids['owner_' . $side], $tenant));
        $db->insert('crm_devices', deviceRow($ids['device_' . $side], $tenant));
    }
    // A tombstoned parent under tenant A: a soft delete is not a SQL delete.
    $db->insert('customers', customerRow($ids['customer_tombstone'], $ids['tenant_a'],
        ['deleted_at' => STAMP]));
}

/** Remove exactly what this suite created, in dependency order. */
function clearSynthetic(Database $db, array $ids): void
{
    if ($ids === []) {
        return;
    }
    $params = ['a' => $ids['tenant_a'], 'b' => $ids['tenant_b']];
    foreach (['customer_transactions', 'crm_devices', 'settings', 'customers',
              'owners', 'periods'] as $table) {
        $db->execute('DELETE FROM `' . $table . '` WHERE tenant_id IN (:a, :b)', $params);
    }
    $db->execute('DELETE FROM `tenants` WHERE id IN (:a, :b)', $params);
}

/** Row counts of the objects a refusal could have touched. Counts only. */
function preservationCounts(Database $db, array $ids): array
{
    $out = [];
    foreach (COUNTED_TABLES as $table) {
        // `tenants` keys its own rows by id; every other counted table is
        // scoped by the tenant column the relationships are about.
        $column = $table === 'tenants' ? 'id' : 'tenant_id';
        $out[$table] = (int) scalar($db,
            'SELECT COUNT(*) FROM `' . $table . '` WHERE `' . $column . '` IN (:a, :b)',
            ['a' => $ids['tenant_a'], 'b' => $ids['tenant_b']]);
    }
    return $out;
}

/**
 * Normalised metadata of every table, as one hash. An identical hash before
 * and after an operation proves that operation executed no DDL.
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

/** A report must never carry a row value, a name, a national id or a secret. */
function assertSanitised(array $report, string $what): void
{
    $encoded = json_encode($report);
    t($encoded !== false, $what . ' must be encodable');
    t(strpos($encoded, PII_CUSTOMER_NAME) === false, $what . ' must never carry a customer name');
    t(strpos($encoded, PII_NATIONAL_ID) === false, $what . ' must never carry a national id');
    foreach (['password', 'token_hash', 'jwt_secret', 'private_key'] as $secret) {
        t(strpos($encoded, $secret) === false, $what . ' must never carry a credential field');
    }
}

// ---------------------------------------------------------------------------
// Drive the suite.
// ---------------------------------------------------------------------------

$directory = $repoRoot . '/' . MIG_DIR_NAME;
$ddl = BFixture::db(true);
$app = BFixture::db(false);
$preflight = new MigrationPreflight($ddl, $directory);
$runner = new Migrations($ddl, $directory);

$activation = 'unknown';
// Which blocker proof actually ran. Printed at the end so a green run can
// never hide the fact that only one of the two paths was exercised.
$pendingProof = 'skipped';
$appliedProof = 'skipped';

try {
    // --- A. The base chain only. The scoped-relationship file is installed
    //         deliberately and separately below, so a target that already has
    //         001+002 is never pulled forward by this step.
    $files = $preflight->files();
    $hasScoped = isset($files[SCOPED_VERSION]);
    $runner->apply(['001_initial', '002_identity_foundation']);
    t(isset($preflight->tracker()['001_initial']), 'the initial schema is recorded');
    t(isset($preflight->tracker()['002_identity_foundation']), 'the identity schema is recorded');

    // --- B. Two synthetic tenants with one parent of every kind under each.
    seedSynthetic($app, $ids);

    // --- C. A pre-existing cross-tenant row must BLOCK installation of the
    //         constraints, and blocking must change nothing at all. This is
    //         only constructible while 003 is still pending: once the
    //         composite keys exist the engine itself refuses the row.
    if ($hasScoped && !isset($preflight->tracker()[SCOPED_VERSION])) {
        $activation = 'blocked-then-applied';
        $pendingProof = 'pending-state-integration';
        // Legal today: the ID-only foreign key only proves the customer exists.
        $badDevice = uuid();
        $app->insert('crm_devices', deviceRow($badDevice, $ids['tenant_a'],
            ['customer_id' => $ids['customer_b']]));
        $countsBefore = preservationCounts($app, $ids);
        $fingerprintBefore = schemaFingerprint($preflight);

        $gate = $runner->scopedGate();
        t($gate['safe'] === false, 'the reference gate must refuse a cross-tenant row');
        t(in_array(MigrationPreflight::C_CROSS_TENANT_REFERENCE,
            array_column($gate['blockers'], 'code'), true),
            'the gate must report the cross-tenant reference by code');
        t(array_column($gate['blockers'], 'code')
            === [MigrationPreflight::C_CROSS_TENANT_REFERENCE],
            'the single synthetic row is the ONLY thing this target is refused for');
        foreach ($gate['blockers'] as $blocker) {
            t(array_keys($blocker) === ['code', 'table', 'constraint', 'count'],
                'a reference blocker carries exactly code, table, constraint and count');
            t(is_int($blocker['count']) && $blocker['count'] > 0,
                'a reference blocker carries a positive count');
        }
        // Non-vacuous: the parent keys are absent only because 003 has not run,
        // so the gate must read them as pending and refuse on the DATA, not on
        // a precondition the file is about to satisfy itself.
        foreach ($gate['relationships'] as $relationshipReport) {
            t($relationshipReport['parent_key'] === 'pending',
                'a parent key 003 creates itself must read pending, not missing');
        }
        // The orphan counter and the cross-tenant counter are independent
        // numbers, not one figure reported twice: the single synthetic row
        // faults exactly one of them. An orphan is not constructible here at
        // all — the ID-only key 001 declared already refuses a missing
        // parent — so this is the evidence that the orphan branch is wired.
        $faulted = array_values(array_filter($gate['relationships'],
            static fn (array $relationshipReport): bool => $relationshipReport['constraint']
                === RELATIONSHIPS['crm_device_customer']['constraint']));
        t(count($faulted) === 1
            && $faulted[0]['table'] === 'crm_devices'
            && $faulted[0]['cross_tenant'] === 1
            && $faulted[0]['orphan'] === 0,
            'the fault is attributed to one relationship with one cross-tenant row');
        // The supporting-index half of the gate is exercised, not bypassed:
        // asked with nothing declared as provided, the same absent keys must
        // be reported as MISSING_PARENT_KEY.
        t(in_array(MigrationPreflight::C_MISSING_PARENT_KEY,
            array_column($preflight->scopedIntegrity()['blockers'], 'code'), true),
            'an absent parent key nothing declares must be reported as missing');
        assertSanitised($gate, 'the reference gate report');

        $refused = '';
        $refusedBlockers = null;
        try {
            $runner->applyScopedBusinessKeys();
        } catch (MigrationBlockedReference $error) {
            $refused = $error->getMessage();
            $refusedBlockers = $error->blockers();
        }
        t(str_starts_with($refused, Migrations::E_BLOCKED_REFERENCE),
            'a pre-existing cross-tenant row must block the installation');
        t(is_array($refusedBlockers) && $refusedBlockers === $gate['blockers'],
            'the refusal carries the same counts-only report the gate produced');
        assertSanitised(['blockers' => $refusedBlockers], 'the refusal report');
        t(preservationCounts($app, $ids) === $countsBefore,
            'blocking the installation must change no business record');
        t((int) scalar($app, 'SELECT COUNT(*) FROM crm_devices WHERE id = :id',
            ['id' => $badDevice]) === 1,
            'blocking must not delete, truncate or repair the offending row');
        t(schemaFingerprint($preflight) === $fingerprintBefore,
            'blocking the installation must execute no DDL');
        t(!isset($preflight->tracker()[SCOPED_VERSION]),
            'a blocked installation must not record the file as applied');
        t($runner->status()['state'] !== 'COMPLETE',
            'a blocked target must not report COMPLETE');

        // Removing the row is THIS SUITE's cleanup of its own test data —
        // the product is never asked to repair a business record.
        $app->execute('DELETE FROM crm_devices WHERE id = :id', ['id' => $badDevice]);
        t($runner->scopedGate()['safe'] === true,
            'the gate opens again once the synthetic row is gone');

        $report = $runner->applyScopedBusinessKeys();
        t($report['state'] === 'COMPLETE', 'a clean target installs the scoped constraints');
        t(isset($preflight->tracker()[SCOPED_VERSION]),
            'a successful install records the file');
    } elseif ($hasScoped) {
        $activation = 'already-applied';
        $pendingProof = 'skipped-003-already-applied';
        t($runner->scopedGate()['safe'] === true,
            'an already-applied target carries no orphan or cross-tenant reference');
    } else {
        $activation = 'no-scoped-file';
        $pendingProof = 'skipped-no-scoped-file';
    }

    // --- C2. The PRODUCTION path, on every target. host/bin/migrate.php runs
    //         Migrations::apply(), and host/public/setup.php runs its own
    //         per-file splitter; the dedicated entry point above is not the
    //         only door. This section therefore runs even when 003 is already
    //         recorded — which is the carried-forward fixture state from now
    //         on — so a regression that makes the gate degenerate can never
    //         hide behind an already-applied branch.
    //
    //         A cross-tenant row cannot be INSERTed normally here: the engine
    //         refuses it, which is the whole point of 003. A legacy row that
    //         predates the constraints is the state under test, so it is
    //         planted with the session's foreign-key check disabled for the
    //         duration of the assertions and removed in the `finally`. No
    //         schema object is dropped, truncated or reset.
    if ($hasScoped) {
        $appliedProof = 'applied-target-negative-control';
        // Fail loudly and early if a previous run ever died inside the window
        // below and left its planted row behind: a stale fault would otherwise
        // surface as a confusing blocker-count mismatch further down.
        t($runner->scopedGate()['safe'] === true,
            'the target must hold no reference fault before the control row is planted');
        $badDevice = uuid();
        $app->pdo()->exec('SET FOREIGN_KEY_CHECKS = 0');
        try {
            $app->insert('crm_devices', deviceRow($badDevice, $ids['tenant_a'],
                ['customer_id' => $ids['customer_b']]));
            $countsBefore = preservationCounts($app, $ids);
            $fingerprintBefore = schemaFingerprint(new MigrationPreflight($ddl, $directory));
            $trackedBefore = $preflight->tracker();

            // I-2's negative control, driven DIRECTLY against
            // MigrationPreflight::scopedIntegrity() so it cannot be bypassed by
            // any entry point: a deliberately WRONG provided-index set — the
            // four index names filed under a table that does not exist — can
            // satisfy nothing, so it must never be able to turn a data fault
            // into `safe`. This is the control that FAILS if the gate ever
            // degenerates into returning safe unconditionally, on a target
            // where 003 is already applied and no cross-tenant row can be
            // inserted through the engine any more.
            $wrongProvided = [];
            foreach (array_keys(MigrationPreflight::providedIndexes([SCOPED_VERSION])) as $key) {
                $wrongProvided['no_such_table.' . substr($key, (int) strrpos($key, '.') + 1)] = true;
            }
            $negativeControl = $preflight->scopedIntegrity($wrongProvided);
            t($negativeControl['safe'] === false,
                'the negative control must refuse: a deliberately wrong'
                . ' provided-index set must not make the gate safe');
            t(array_column($negativeControl['blockers'], 'code')
                === [MigrationPreflight::C_CROSS_TENANT_REFERENCE],
                'the negative control must report C_CROSS_TENANT_REFERENCE');
            assertSanitised($negativeControl, 'the negative-control report');

            // The gate the installation paths consult, driven the same way.
            $gate = $runner->scopedGate();
            t($gate['safe'] === false,
                'the reference gate must refuse a target that holds a cross-tenant row');
            t(array_column($gate['blockers'], 'code')
                === [MigrationPreflight::C_CROSS_TENANT_REFERENCE],
                'the single planted row is the ONLY thing this target is refused for');
            foreach ($gate['blockers'] as $blocker) {
                t(array_keys($blocker) === ['code', 'table', 'constraint', 'count'],
                    'a reference blocker carries exactly code, table, constraint and count');
                t(is_int($blocker['count']) && $blocker['count'] === 1,
                    'the reference blocker counts exactly the planted row');
            }
            $faulted = array_values(array_filter($gate['relationships'],
                static fn (array $relationshipReport): bool => $relationshipReport['constraint']
                    === RELATIONSHIPS['crm_device_customer']['constraint']));
            t(count($faulted) === 1
                && $faulted[0]['table'] === 'crm_devices'
                && $faulted[0]['cross_tenant'] === 1
                && $faulted[0]['orphan'] === 0,
                'the fault is attributed to one relationship with one cross-tenant row');
            assertSanitised($gate, 'the reference gate report');

            // The REAL path: Migrations::apply(), exactly as host/bin/migrate.php
            // calls it. Refusal must happen before a single scoped step runs.
            $refused = '';
            $refusedBlockers = null;
            try {
                $runner->apply();
            } catch (MigrationBlockedReference $error) {
                $refused = $error->getMessage();
                $refusedBlockers = $error->blockers();
            }
            t(str_starts_with($refused, Migrations::E_BLOCKED_REFERENCE),
                'Migrations::apply() must refuse a target that holds a cross-tenant row');
            t(is_array($refusedBlockers) && $refusedBlockers === $gate['blockers'],
                'apply() refuses with the same counts-only report the gate produced');
            assertSanitised(['blockers' => $refusedBlockers], 'the apply() refusal report');
            t(preservationCounts($app, $ids) === $countsBefore,
                'the refused apply() must change no business record');
            t((int) scalar($app, 'SELECT COUNT(*) FROM crm_devices WHERE id = :id',
                ['id' => $badDevice]) === 1,
                'the refused apply() must not delete, truncate or repair the offending row');
            // A FRESH preflight: the suite's own instance caches table state,
            // so reusing it here would compare a stale snapshot with itself.
            t(schemaFingerprint(new MigrationPreflight($ddl, $directory)) === $fingerprintBefore,
                'the refused apply() must execute no DDL for the scoped steps');
            t($preflight->tracker() === $trackedBefore,
                'the refused apply() must record no file and no step');

            // --status has to surface the reference blocker, not stay silent
            // until an ALTER dies with a bare errno.
            $status = $runner->status();
            $statusFault = array_values(array_filter($status['blockers'],
                static fn (array $blocker): bool => $blocker['code']
                    === MigrationPreflight::C_CROSS_TENANT_REFERENCE));
            t(count($statusFault) === 1,
                'migrate.php --status must surface C_CROSS_TENANT_REFERENCE as a blocker');
            t($statusFault[0]['table'] === 'crm_devices' && $statusFault[0]['count'] === 1,
                'the status blocker carries the table and the count of the planted row');
            t(array_keys($statusFault[0]) === ['code', 'table', 'count'],
                'a status blocker carries exactly code, table and count');
            t($status['safe'] === false,
                'a target holding a cross-tenant row must not report safe');
            t($status['state'] !== 'COMPLETE',
                'a target holding a cross-tenant row must not report COMPLETE');
            assertSanitised($status, 'the status report');
        } finally {
            // Removing the row is THIS SUITE's cleanup of its own synthetic
            // data; the product is never asked to repair a business record.
            $app->execute('DELETE FROM crm_devices WHERE id = :id', ['id' => $badDevice]);
            $app->pdo()->exec('SET FOREIGN_KEY_CHECKS = 1');
        }
        t($runner->scopedGate()['safe'] === true,
            'the gate opens again once the synthetic row is gone');

        // The other documented installation path, host/public/setup.php, runs
        // its own statement splitter and never calls apply(). It cannot be
        // driven here — it reads host/config/config.php, which this suite must
        // never open — so its gate PLACEMENT is asserted structurally: inside
        // the per-file loop (001 and 002 therefore exist first) and before a
        // single statement of that file is split or executed.
        $setup = (string) file_get_contents($repoRoot . '/host/public/setup.php');
        $setupLoop = strpos($setup, 'foreach ($files as $file)');
        $setupGate = strpos($setup, '$version === Migrations::SCOPED_VERSION');
        $setupSplit = strpos($setup, '$stmts = setupSplitStatements(');
        $setupExec = strpos($setup, 'foreach ($stmts as $stmt)');
        t($setupLoop !== false && $setupGate !== false
            && $setupSplit !== false && $setupExec !== false,
            'setup.php must gate the scoped file inside its per-file apply loop');
        t($setupLoop < $setupGate && $setupGate < $setupSplit
            && $setupSplit < $setupExec,
            'setup.php must run the reference gate after the loop starts and'
            . ' before that file\'s statements are split or executed');
        t(strpos($setup, "'MigrationPreflight', 'Migrations'") !== false,
            'setup.php must load the classes the gate is built from');
        t(substr_count($setup, '$version === Migrations::SCOPED_VERSION') === 1,
            'setup.php must gate once per file, not once before the whole loop');
    } else {
        $appliedProof = 'skipped-no-scoped-file';
    }

    // --- D. The declared metadata, only when the file exists: 4 unique parent
    //         keys, 6 composite constraints, 6 retained historical keys —
    //         every column list ORDERED exactly as declared.
    if ($hasScoped) {
        foreach (PARENT_KEYS as $table => $index) {
            t(indexIsUnique($ddl, $table, $index), $table . '.' . $index . ' must be unique');
            t(indexColumns($ddl, $table, $index) === ['tenant_id', 'id'],
                $table . '.' . $index . ' must be (tenant_id, id) in that order');
        }
        foreach (RELATIONSHIPS as $name => $spec) {
            t(constraintCount($ddl, $spec['child'], $spec['constraint']) > 0,
                $name . ': the named constraint must exist');
            t(constraintChildColumns($ddl, $spec['child'], $spec['constraint'])
                === $spec['child_columns'],
                $name . ': the child columns are in the declared order');
            $parent = constraintParent($ddl, $spec['child'], $spec['constraint']);
            t($parent['table'] === $spec['parent'],
                $name . ': the referenced table is the declared parent');
            t($parent['columns'] === $spec['parent_columns'],
                $name . ': the referenced columns are in the declared order');
            t(indexIsUnique($ddl, $spec['parent'], $spec['parent_key']),
                $name . ': the parent carries its supporting unique key');
        }
        foreach (HISTORICAL_KEYS as $name => $spec) {
            t(constraintCount($ddl, $spec['table'], $name) === 1,
                'the historical ID-only key ' . $name . ' must be retained');
            t(constraintChildColumns($ddl, $spec['table'], $name) === [$spec['column']],
                'the historical key ' . $name . ' still carries its single column');
            t(constraintParent($ddl, $spec['table'], $name)['table'] === $spec['parent'],
                'the historical key ' . $name . ' still references the same parent');
        }
    }

    // --- E. The six-row matrix, ALWAYS: INSERT rejection, same-tenant success,
    //         UPDATE rejection, untouched row — for every relationship. This is
    //         the RED surface: without 003 the ID-only keys admit the row and
    //         the first case fails with CROSS_TENANT_REFERENCE_ACCEPTED.
    foreach (RELATIONSHIPS as $name => $spec) {
        $cross = caseRow($name, true, $ids);
        expectForeignKeyRejection(
            static fn () => $app->insert($cross['table'], $cross['row']),
            $name . ' cross-tenant INSERT');

        $same = caseRow($name, false, $ids);
        expectAccepted(
            static fn () => $app->insert($same['table'], $same['row']),
            $name . ' same-tenant INSERT');

        // The same row, same tenant, now repointed at the other tenant's
        // parent: the write path must be closed as tightly as the insert.
        expectForeignKeyRejection(
            static function () use ($app, $cross, $same): void {
                $app->execute(
                    'UPDATE `' . $same['table'] . '` SET `' . $same['fk'] . '` = :target'
                    . ' WHERE id = :id',
                    ['target' => $cross['parent_id'], 'id' => $same['row']['id']]);
            },
            $name . ' cross-tenant UPDATE');

        t((int) scalar($app,
               'SELECT COUNT(*) FROM `' . $same['table'] . '` WHERE id = :id'
               . ' AND `' . $same['fk'] . '` = :expected',
               ['id' => $same['row']['id'], 'expected' => $same['parent_id']]) === 1,
            $name . ': the refused UPDATE must leave the row untouched');
    }

    // --- F. Nullable semantics preserved: no reference is not a failure, and
    //         a tombstoned parent still resolves.
    expectAccepted(static fn () => $app->insert('crm_devices',
        deviceRow(uuid(), $ids['tenant_a'],
            ['customer_id' => null, 'owner_id' => null, 'period_id' => null])),
        'crm_devices with a permitted NULL foreign key');
    expectAccepted(static fn () => $app->insert('customer_transactions',
        transactionRow(uuid(), $ids['tenant_a'],
            ['customer_id' => $ids['customer_a'], 'seq' => random_int(1, 2000000000),
             'device_id' => null])),
        'customer_transactions with a permitted NULL device_id');
    // tenant_b has no settings row of its own, so this one is free to insert.
    expectAccepted(static fn () => $app->insert('settings',
        settingsRow(uuid(), $ids['tenant_b'], ['current_period_id' => null])),
        'settings with a permitted NULL current_period_id');
    expectAccepted(static fn () => $app->insert('crm_devices',
        deviceRow(uuid(), $ids['tenant_a'], ['customer_id' => $ids['customer_tombstone']])),
        'crm_devices pointing at a tombstoned same-tenant customer');
    expectAccepted(static fn () => $app->insert('customer_transactions',
        transactionRow(uuid(), $ids['tenant_a'],
            ['customer_id' => $ids['customer_tombstone'],
             'seq' => random_int(1, 2000000000), 'device_id' => null])),
        'customer_transactions pointing at a tombstoned same-tenant customer');

    // --- G. The suite leaves the chain COMPLETE and the gate open.
    t($runner->status()['state'] === 'COMPLETE',
        'the integrity suite leaves the migration chain COMPLETE');
} finally {
    clearSynthetic($app, $ids);
}

print('INTEGRITY_SCENARIO_OK relationships=' . count(RELATIONSHIPS)
    . ' activation=' . $activation
    . ' pending-proof=' . $pendingProof
    . ' applied-proof=' . $appliedProof
    . ' engine=' . $ddl->serverVersion() . "\n");
