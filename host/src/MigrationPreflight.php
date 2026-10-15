<?php
/**
 * MigrationPreflight.php — read-only inspection of a migration target.
 *
 * Two jobs, both strictly read-only:
 *
 *   1. Normalise engine metadata. MySQL and MariaDB report the same logical
 *      column differently (integer display widths, quoted string defaults,
 *      `current_timestamp()` spelling), so every fact is reduced here to one
 *      engine-independent shape before anything compares it. Nothing in this
 *      file ever compares a `SHOW CREATE TABLE` string: those differ between
 *      engines for an identical logical schema.
 *
 *   2. Produce a COUNTS-ONLY report. `inspect()` returns
 *      `{safe, blockers:[{code, table, count}], migration_status}` and never a
 *      row value, a person's name, a national id, a customer record or a raw
 *      server log line. The only identifiers it may name are schema objects
 *      (tables), because "which object is unexpected" is the diagnosis.
 *
 * Migrations::status() and this class never write: no DDL, no DML, no advisory
 * lock, no journal row.
 */

declare(strict_types=1);

final class MigrationPreflight
{
    public const TRACKER_TABLE = 'schema_migrations';
    public const JOURNAL_TABLE = 'migration_steps';

    /**
     * The exact journal shape (host/src/Migrations.php creates it verbatim).
     * An existing journal with any other column set is UNKNOWN_JOURNAL and
     * blocks before a single statement of DDL runs.
     */
    public const JOURNAL_COLUMNS = [
        'migration_version', 'step_id', 'checksum', 'status',
        'cursor_value', 'started_at', 'completed_at',
    ];

    /** Blocker codes. Stable; the HTTP layer maps codes, never messages. */
    public const C_UNEXPECTED_TABLE = 'UNEXPECTED_TABLE';
    public const C_MISSING_TABLE    = 'MISSING_TABLE';
    public const C_MISSING_FILE     = 'MISSING_FILE';
    public const C_CHECKSUM_MISMATCH = 'CHECKSUM_MISMATCH';
    public const C_UNKNOWN_JOURNAL  = 'UNKNOWN_JOURNAL';
    public const C_FAILED_STEP      = 'FAILED_STEP';
    public const C_AMBIGUOUS_STEP   = 'AMBIGUOUS_STEP';
    public const C_PENDING_MIGRATION = 'PENDING_MIGRATION';

    /**
     * Codes of the scoped-relationship reference gate (scopedIntegrity()).
     * A separate namespace on purpose: these describe DATA the new constraints
     * would refuse to install, where the C_* codes above describe the migration
     * chain itself.
     */
    public const C_UNSUPPORTED_ENGINE = 'UNSUPPORTED_ENGINE';
    public const C_REFERENCE_TABLE_MISSING = 'REFERENCE_TABLE_MISSING';
    public const C_MISSING_PARENT_KEY = 'MISSING_PARENT_KEY';
    public const C_ORPHAN_REFERENCE = 'ORPHAN_REFERENCE';
    public const C_CROSS_TENANT_REFERENCE = 'CROSS_TENANT_REFERENCE';

    /**
     * The documented engine floor (host/INSTALL.md). Only a value from this map
     * is ever reported; a support claim is never inferred from a version that
     * was not actually exercised.
     */
    private const MINIMUM_VERSIONS = ['mariadb' => '10.3.0', 'mysql' => '5.7.0'];

    private Database $db;
    private string $directory;
    private ?string $engine = null;

    /** Per-instance table metadata cache; dropped after any executed DDL. */
    private array $cache = [];

    public function __construct(Database $db, string $directory)
    {
        $this->db = $db;
        $this->directory = rtrim(str_replace('\\', '/', $directory), '/');
    }

    public function database(): Database
    {
        return $this->db;
    }

    public function directory(): string
    {
        return $this->directory;
    }

    /** Drop the metadata cache after a statement changed the schema. */
    public function invalidate(): void
    {
        $this->cache = [];
    }

    // ------------------------------------------------------------------
    // Engine
    // ------------------------------------------------------------------

    /** 'mysql' or 'mariadb'. Never a version string, never a credential. */
    public function engine(): string
    {
        if ($this->engine === null) {
            $version = $this->db->serverVersion();
            $this->engine = stripos($version, 'MariaDB') !== false ? 'mariadb' : 'mysql';
        }
        return $this->engine;
    }

    // ------------------------------------------------------------------
    // Files and the file-level tracker
    // ------------------------------------------------------------------

    /** @return array<string, string> version => sha256 of the file */
    public function files(): array
    {
        $paths = glob($this->directory . '/*.sql');
        if ($paths === false || $paths === []) {
            throw new RuntimeException('MIGRATION_NO_FILES');
        }
        sort($paths, SORT_STRING);
        $out = [];
        foreach ($paths as $path) {
            $version = basename($path, '.sql');
            $checksum = hash_file('sha256', $path);
            if ($checksum === false) {
                throw new RuntimeException('MIGRATION_NO_FILES');
            }
            $out[$version] = $checksum;
        }
        return $out;
    }

    /**
     * @return array<string, string> version => recorded checksum
     */
    public function tracker(): array
    {
        if (!$this->tableExists(self::TRACKER_TABLE)) {
            return [];
        }
        $out = [];
        // Only version and checksum are selected, never `*`: `setup.php` creates
        // this table with three columns (version, checksum, applied_at) and
        // 001 adds description and applied_by, so a reader that named either
        // optional column would break the documented install path.
        foreach ($this->db->query('SELECT version, checksum FROM schema_migrations ORDER BY version') as $row) {
            $out[(string) $row['version']] = (string) $row['checksum'];
        }
        return $out;
    }

    /**
     * The tracker's actual columns, discovered rather than assumed. A writer
     * must intersect with this: setup.php's three-column tracker has no
     * description or applied_by, and a hard-coded INSERT of those two columns
     * would fail exactly when a new migration has to be recorded.
     *
     * @return list<string>
     */
    public function trackerColumns(): array
    {
        if (!$this->tableExists(self::TRACKER_TABLE)) {
            return [];
        }
        $names = [];
        foreach ($this->columnsOf(self::TRACKER_TABLE) as $name => $fact) {
            $names[] = $name;
        }
        sort($names);
        return $names;
    }

    /**
     * Project a desired tracker row onto the columns that actually exist.
     *
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    public static function trackerRow(array $columns, array $row): array
    {
        $known = array_map('strtolower', $columns);
        $out = [];
        foreach ($row as $name => $value) {
            if (in_array(strtolower((string) $name), $known, true)) {
                $out[(string) $name] = $value;
            }
        }
        return $out;
    }

    // ------------------------------------------------------------------
    // Journal
    // ------------------------------------------------------------------

    public function journalPresent(): bool
    {
        return $this->tableExists(self::JOURNAL_TABLE);
    }

    /** @return list<string> */
    public function journalColumns(): array
    {
        if (!$this->journalPresent()) {
            return [];
        }
        $names = [];
        foreach ($this->columnsOf(self::JOURNAL_TABLE) as $name => $fact) {
            $names[] = $name;
        }
        sort($names);
        return $names;
    }

    /**
     * Exact-set compatibility. A journal this build does not recognise is
     * refused rather than written into: guessing at an unknown column is how a
     * recovery path silently corrupts its own ledger.
     *
     * @param list<string> $columns
     */
    public function journalCompatible(array $columns): bool
    {
        $actual = array_map('strtolower', $columns);
        $expected = array_map('strtolower', self::JOURNAL_COLUMNS);
        sort($actual);
        sort($expected);
        return $actual === $expected;
    }

    /** @return array<string, array<string, mixed>> step_id => journal row */
    public function journalRows(string $version): array
    {
        if (!$this->journalPresent()) {
            return [];
        }
        $out = [];
        foreach ($this->db->query(
            'SELECT step_id, checksum, status, cursor_value, started_at, completed_at'
            . ' FROM migration_steps WHERE migration_version = :version ORDER BY step_id',
            ['version' => $version]
        ) as $row) {
            $out[(string) $row['step_id']] = $row;
        }
        return $out;
    }

    // ------------------------------------------------------------------
    // Engine-normalised metadata
    // ------------------------------------------------------------------

    /** @return list<string> base tables of the current schema, sorted */
    public function tables(): array
    {
        $rows = $this->db->query(
            "SELECT TABLE_NAME FROM information_schema.TABLES"
            . " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
            . ' ORDER BY TABLE_NAME'
        );
        $names = array_map(static fn (array $row): string => (string) $row['TABLE_NAME'], $rows);
        sort($names);
        return $names;
    }

    public function tableExists(string $table): bool
    {
        $value = $this->db->queryValue(
            "SELECT COUNT(*) FROM information_schema.TABLES"
            . " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
            . " AND TABLE_TYPE = 'BASE TABLE'",
            ['table' => $table]
        );
        return $value !== null && (int) $value === 1;
    }

    /**
     * Normalised state of one table.
     *
     * @return array{exists: bool,
     *   columns: array<string, array{sql: string, charset: ?string, collation: ?string,
     *                                 nullable: bool, default: ?string, auto: bool}>,
     *   indexes: array<string, array{unique: bool, columns: list<string>, type: string}>,
     *   foreign_keys: array<string, array{columns: list<string>, ref_table: string, ref_columns: list<string>}>}
     */
    public function tableState(string $table): array
    {
        if (isset($this->cache[$table])) {
            return $this->cache[$table];
        }
        $state = [
            'exists' => $this->tableExists($table),
            'columns' => [], 'indexes' => [], 'foreign_keys' => [],
        ];
        if ($state['exists']) {
            $state['columns'] = $this->columnsOf($table);
            $state['indexes'] = $this->indexesOf($table);
            $state['foreign_keys'] = $this->foreignKeysOf($table);
        }
        $this->cache[$table] = $state;
        return $state;
    }

    /** @return array<string, array{sql: string, charset: ?string, collation: ?string, nullable: bool, default: ?string, auto: bool}> */
    private function columnsOf(string $table): array
    {
        $rows = $this->db->query(
            'SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA,'
            . ' CHARACTER_SET_NAME, COLLATION_NAME'
            . ' FROM information_schema.COLUMNS'
            . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
            . ' ORDER BY ORDINAL_POSITION',
            ['table' => $table]
        );
        $out = [];
        foreach ($rows as $row) {
            $name = strtolower((string) $row['COLUMN_NAME']);
            $type = (string) $row['COLUMN_TYPE'];
            $out[$name] = [
                'sql' => $this->normalizeType($type),
                'charset' => $row['CHARACTER_SET_NAME'] === null
                    ? null : strtolower((string) $row['CHARACTER_SET_NAME']),
                'collation' => $row['COLLATION_NAME'] === null
                    ? null : strtolower((string) $row['COLLATION_NAME']),
                'nullable' => strcasecmp((string) $row['IS_NULLABLE'], 'YES') === 0,
                'default' => $this->normalizeDefault($row['COLUMN_DEFAULT'], $type),
                'auto' => stripos((string) $row['EXTRA'], 'auto_increment') !== false,
            ];
        }
        return $out;
    }

    /** @return array<string, array{unique: bool, columns: list<string>, type: string}> */
    private function indexesOf(string $table): array
    {
        $rows = $this->db->query(
            'SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, INDEX_TYPE'
            . ' FROM information_schema.STATISTICS'
            . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
            . ' ORDER BY INDEX_NAME, SEQ_IN_INDEX',
            ['table' => $table]
        );
        $out = [];
        foreach ($rows as $row) {
            $name = strtolower((string) $row['INDEX_NAME']);
            if (!isset($out[$name])) {
                $out[$name] = [
                    'unique' => (int) $row['NON_UNIQUE'] === 0,
                    'columns' => [],
                    'type' => strtolower((string) $row['INDEX_TYPE']),
                ];
            }
            $out[$name]['columns'][] = strtolower((string) $row['COLUMN_NAME']);
        }
        return $out;
    }

    /** @return array<string, array{columns: list<string>, ref_table: string, ref_columns: list<string>}> */
    private function foreignKeysOf(string $table): array
    {
        $rows = $this->db->query(
            'SELECT CONSTRAINT_NAME, COLUMN_NAME, ORDINAL_POSITION,'
            . ' REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME'
            . ' FROM information_schema.KEY_COLUMN_USAGE'
            . ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table'
            . ' AND REFERENCED_TABLE_NAME IS NOT NULL'
            . ' ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION',
            ['table' => $table]
        );
        $out = [];
        foreach ($rows as $row) {
            $name = strtolower((string) $row['CONSTRAINT_NAME']);
            if (!isset($out[$name])) {
                $out[$name] = ['columns' => [], 'ref_table' => strtolower((string) $row['REFERENCED_TABLE_NAME']),
                               'ref_columns' => []];
            }
            $out[$name]['columns'][] = strtolower((string) $row['COLUMN_NAME']);
            $out[$name]['ref_columns'][] = strtolower((string) $row['REFERENCED_COLUMN_NAME']);
        }
        return $out;
    }

    /**
     * Engine-normalise a declared type.
     *
     * The only transform is the integer display width: MariaDB always reports
     * `int(11)` and MySQL reports it up to 8.0.19, while Schema declares `int`.
     * Stripping it on BOTH engines keeps one comparison; leaving it in would
     * make the same logical column look different on the two supported
     * engines.
     */
    public function normalizeType(string $columnType): string
    {
        $type = strtolower(trim($columnType));
        if (preg_match('/^(tinyint|smallint|mediumint|int|integer|bigint)(\(\d+\))?(.*)$/',
                $type, $parts) === 1) {
            return $parts[1] . trim($parts[3]);
        }
        return $type;
    }

    /**
     * Engine-normalise a default to a plain string (or null for "no default").
     *
     * MySQL and MariaDB disagree on whether a string literal arrives quoted:
     * MariaDB always quotes, MySQL quoted before 8.0.19 and does not after.
     * Comparing the raw values would make a correct schema fail on one engine
     * and pass on the other, so the two engines are normalised separately and
     * the result is a single canonical string.
     */
    public function normalizeDefault(mixed $raw, string $columnType = ''): ?string
    {
        if ($raw === null) {
            return null;
        }
        $text = (string) $raw;
        if (strcasecmp($text, 'NULL') === 0) {
            return null;
        }
        if ($this->engine() === 'mariadb') {
            return $this->mariadbDefault($text, $columnType);
        }
        return $this->mysqlDefault($text, $columnType);
    }

    private function mariadbDefault(string $raw, string $type): string
    {
        if (strncasecmp($raw, 'current_timestamp', 17) === 0) {
            return 'current_timestamp()';
        }
        return $this->stripQuotes($raw, $type);
    }

    private function mysqlDefault(string $raw, string $type): string
    {
        if (strcasecmp($raw, 'CURRENT_TIMESTAMP') === 0
            || strncasecmp($raw, 'current_timestamp(', 19) === 0) {
            return 'current_timestamp()';
        }
        return $this->stripQuotes($raw, $type);
    }

    private function stripQuotes(string $raw, string $type): string
    {
        unset($type);
        if (strlen($raw) >= 2 && $raw[0] === "'" && substr($raw, -1) === "'") {
            return str_replace("''", "'", substr($raw, 1, -1));
        }
        return $raw;
    }

    // ------------------------------------------------------------------
    // Fact evaluation
    // ------------------------------------------------------------------

    /**
     * Compare declared facts with live metadata.
     *
     * @param array{tables?: array<string, mixed>, columns?: array<string, mixed>,
     *   indexes?: array<string, mixed>, foreign_keys?: array<string, mixed>,
     *   rows?: array<string, array<string, mixed>>} $facts
     * @return list<string> failing fact keys, e.g. "columns.tenants.lifecycle".
     *         Keys only — never the offending value.
     */
    public function evaluate(array $facts): array
    {
        $failures = [];
        foreach ($facts['tables'] ?? [] as $table => $expected) {
            $exists = $this->tableState((string) $table)['exists'];
            if ($expected === null ? $exists : !$exists) {
                $failures[] = 'tables.' . $table;
            }
        }
        foreach ($facts['columns'] ?? [] as $key => $expected) {
            [$table, $column] = $this->split($key);
            // information_schema returns identifier case as stored; MySQL and
            // MariaDB differ on Windows, so lookups are case-insensitive.
            $actual = $this->tableState($table)['columns'][strtolower($column)] ?? null;
            if ($expected === null) {
                if ($actual !== null) {
                    $failures[] = 'columns.' . $key;
                }
                continue;
            }
            if ($actual === null || !$this->columnEquals($expected, $actual)) {
                $failures[] = 'columns.' . $key;
            }
        }
        foreach ($facts['indexes'] ?? [] as $key => $expected) {
            [$table, $name] = $this->split($key);
            $actual = $this->tableState($table)['indexes'][strtolower($name)] ?? null;
            if ($expected === null) {
                if ($actual !== null) {
                    $failures[] = 'indexes.' . $key;
                }
                continue;
            }
            if ($actual === null || !$this->indexEquals($expected, $actual)) {
                $failures[] = 'indexes.' . $key;
            }
        }
        foreach ($facts['foreign_keys'] ?? [] as $key => $expected) {
            [$table, $name] = $this->split($key);
            $actual = $this->tableState($table)['foreign_keys'][strtolower($name)] ?? null;
            if ($expected === null) {
                if ($actual !== null) {
                    $failures[] = 'foreign_keys.' . $key;
                }
                continue;
            }
            if ($actual === null || !$this->foreignKeyEquals($expected, $actual)) {
                $failures[] = 'foreign_keys.' . $key;
            }
        }
        foreach ($facts['rows'] ?? [] as $table => $spec) {
            if ($this->rowCountMismatch($table, $spec)) {
                $failures[] = 'rows.' . $table;
            }
        }
        return $failures;
    }

    /** True when every fact holds. */
    public function satisfies(array $facts): bool
    {
        return $this->evaluate($facts) === [];
    }

    /**
     * Fact keys a step DECLARES, in the exact namespaced form evaluate()
     * reports them (`columns.tenants.lifecycle`, `rows.identity_state`, …).
     *
     * Declaration is orthogonal to evaluation: a step names an object whether
     * or not its claim about that object currently holds. That distinction is
     * what adoption supersession is judged on — a later step that re-states a
     * key takes responsibility for the object, so an earlier step's now-failing
     * claim about the same key is superseded rather than unproven. A key no
     * later step declares has nowhere to defer to and must hold outright.
     *
     * @param array{tables?: array<string, mixed>, columns?: array<string, mixed>,
     *   indexes?: array<string, mixed>, foreign_keys?: array<string, mixed>,
     *   rows?: array<string, mixed>} $facts
     * @return list<string>
     */
    public static function declaredKeys(array $facts): array
    {
        $keys = [];
        foreach (['tables', 'columns', 'indexes', 'foreign_keys', 'rows'] as $kind) {
            foreach (array_keys($facts[$kind] ?? []) as $key) {
                $keys[] = $kind . '.' . $key;
            }
        }
        return $keys;
    }

    private function split(string $key): array
    {
        $parts = explode('.', $key, 2);
        return count($parts) === 2 ? [$parts[0], $parts[1]] : [$parts[0], ''];
    }

    private function columnEquals(array $expected, array $actual): bool
    {
        if ($this->normalizeType((string) $expected['sql']) !== $actual['sql']) {
            return false;
        }
        if (($expected['charset'] ?? null) !== $actual['charset']) {
            return false;
        }
        if (($expected['collation'] ?? null) !== $actual['collation']) {
            return false;
        }
        if ((bool) $expected['nullable'] !== $actual['nullable']) {
            return false;
        }
        if ((bool) ($expected['auto'] ?? false) !== $actual['auto']) {
            return false;
        }
        $want = $expected['default'] ?? null;
        $want = $want === null ? null : (string) $want;
        return $want === $actual['default'];
    }

    private function indexEquals(array $expected, array $actual): bool
    {
        if ((bool) $expected['unique'] !== $actual['unique']) {
            return false;
        }
        if (array_map('strtolower', $expected['columns']) !== $actual['columns']) {
            return false;
        }
        $type = strtolower((string) ($expected['type'] ?? 'btree'));
        return $type === $actual['type'];
    }

    private function foreignKeyEquals(array $expected, array $actual): bool
    {
        return array_map('strtolower', $expected['columns']) === $actual['columns']
            && strtolower($expected['ref_table']) === $actual['ref_table']
            && array_map('strtolower', $expected['ref_columns']) === $actual['ref_columns'];
    }

    /** Row assertions are counts only; no value ever leaves this method. */
    private function rowCountMismatch(string $table, array $spec): bool
    {
        $state = $this->tableState($table);
        if (!$state['exists']) {
            return true;
        }
        if (isset($spec['mismatch'])) {
            $where = [];
            $params = [];
            foreach ($spec['mismatch'] as $column => $value) {
                // The column must exist LIVE, not merely be declared: a step
                // that backfills a column an earlier step has not added yet is
                // simply not satisfied, and must not raise a server error.
                if (!Schema::hasColumn($table, (string) $column)
                    || !isset($state['columns'][strtolower((string) $column)])) {
                    return true;
                }
                $quoted = '`' . str_replace('`', '``', (string) $column) . '`';
                $where[] = $quoted . ' IS NULL OR ' . $quoted . ' <> ?';
                $params[] = $value;
            }
            $count = (int) $this->db->queryValue(
                'SELECT COUNT(*) FROM `' . str_replace('`', '``', $table) . '` WHERE '
                . '(' . implode(') OR (', $where) . ')', $params);
            if ($count > (int) ($spec['max'] ?? 0)) {
                return true;
            }
        }
        $count = (int) $this->db->queryValue(
            'SELECT COUNT(*) FROM `' . str_replace('`', '``', $table) . '`');
        if (isset($spec['min_count']) && $count < (int) $spec['min_count']) {
            return true;
        }
        if (isset($spec['max_count']) && $count > (int) $spec['max_count']) {
            return true;
        }
        return false;
    }

    // ------------------------------------------------------------------
    // The scoped-relationship reference gate
    // ------------------------------------------------------------------

    /**
     * Read-only gate in front of installing the six same-tenant composite
     * foreign keys (host/migrations/003_scoped_business_keys.sql).
     *
     * Adding a constraint to a populated database either succeeds or fails on
     * the first bad row, and a failure halfway through leaves an unpredictable
     * mix of installed and not-installed objects. So the whole relationship set
     * is judged first: any orphan, any cross-tenant reference, any missing
     * supporting index — refuse, before a single ALTER runs.
     *
     * STRICTLY READ-ONLY. It never repairs, never deletes, never truncates and
     * never rewrites: a blocked installation changes no business record. The
     * caller (Migrations::applyScopedBusinessKeys) throws with this report and
     * the operator decides how the bad rows are resolved.
     *
     * The report is COUNTS-ONLY. Each blocker carries exactly
     * `code` / `table` / `constraint` / `count` — never a person's name, a
     * national id, a customer row, a tenant id, a row value or a server log
     * line. The only identifiers it may name are schema objects, because "which
     * object is at fault" is the diagnosis.
     *
     * `$providedIndexes` names the `table.index` supporting keys the migration
     * about to run CREATES ITSELF. 003 installs its four unique parent keys
     * before the constraints that need them, so a parent key that is absent
     * only because that file has not run yet is `pending`, not a blocker; a
     * key no pending step declares and that is still absent IS a blocker,
     * because InnoDB would then refuse the constraint mid-install.
     *
     * @param array<string, true> $providedIndexes `table.index` keys the caller
     *        is about to create
     * @return array{safe: bool,
     *   blockers: list<array{code: string, table: string, constraint: string, count: int}>,
     *   relationships: list<array{constraint: string, table: string,
     *     parent_key: string, orphan: int, cross_tenant: int}>}
     */
    public function scopedIntegrity(array $providedIndexes = []): array
    {
        $blockers = [];
        $relationships = [];

        $floor = self::MINIMUM_VERSIONS[$this->engine()] ?? null;
        $version = $this->serverVersionNumber();
        if ($floor === null || $version === '' || version_compare($version, $floor, '<')) {
            $blockers[] = ['code' => self::C_UNSUPPORTED_ENGINE, 'table' => '',
                           'constraint' => '', 'count' => 1];
        }

        foreach (Schema::scopedRelationships() as $relation) {
            $child = $relation['table'];
            $parent = $relation['ref_table'];
            if (!$this->tableExists($child) || !$this->tableExists($parent)) {
                $blockers[] = ['code' => self::C_REFERENCE_TABLE_MISSING, 'table' => $child,
                               'constraint' => $relation['constraint'], 'count' => 1];
                $relationships[] = ['constraint' => $relation['constraint'], 'table' => $child,
                    'parent_key' => 'missing', 'orphan' => 0, 'cross_tenant' => 0];
                continue;
            }

            $keyState = $this->supportingIndexState(
                $parent, $relation['parent_key'], $relation['ref_columns'], $providedIndexes);
            if ($keyState === 'missing') {
                $blockers[] = ['code' => self::C_MISSING_PARENT_KEY, 'table' => $parent,
                               'constraint' => $relation['parent_key'], 'count' => 1];
            }

            [$orphan, $cross] = $this->referenceCounts($relation);
            if ($orphan > 0) {
                $blockers[] = ['code' => self::C_ORPHAN_REFERENCE, 'table' => $child,
                               'constraint' => $relation['constraint'], 'count' => $orphan];
            }
            if ($cross > 0) {
                $blockers[] = ['code' => self::C_CROSS_TENANT_REFERENCE, 'table' => $child,
                               'constraint' => $relation['constraint'], 'count' => $cross];
            }
            $relationships[] = ['constraint' => $relation['constraint'], 'table' => $child,
                'parent_key' => $keyState, 'orphan' => $orphan, 'cross_tenant' => $cross];
        }

        return ['safe' => $blockers === [], 'blockers' => $blockers,
                'relationships' => $relationships];
    }

    /**
     * Supporting parent keys that the declared steps of `$versions` create.
     *
     * Derived from Schema::migrationSteps() postconditions — the same source
     * the SQL file implements — so a key a still-pending migration is about to
     * add reads `pending` here instead of `missing`, and the gate refuses on
     * the DATA rather than on a precondition the file satisfies itself. This
     * is the single derivation both callers use: Migrations::scopedGate()
     * passes the scoped file alone, inspect() passes every known file.
     *
     * @param list<string> $versions migration file stems
     * @return array<string, true> keyed `table.index`
     */
    public static function providedIndexes(array $versions): array
    {
        $provided = [];
        foreach ($versions as $version) {
            foreach (Schema::migrationSteps($version) as $step) {
                foreach (array_keys($step['post']['indexes'] ?? []) as $index) {
                    $provided[$index] = true;
                }
            }
        }
        return $provided;
    }

    /**
     * True when at least one table the six relationships span is present.
     *
     * A target that has never been migrated has no reference surface at all:
     * there is nothing to judge, and its honest report is "pending", not
     * "blocked". Once any of those tables exists, every relationship is
     * judged and a missing counterpart is reported as C_REFERENCE_TABLE_MISSING.
     */
    private function referenceSurfacePresent(): bool
    {
        foreach (Schema::scopedRelationships() as $relation) {
            if ($this->tableExists((string) $relation['table'])
                || $this->tableExists((string) $relation['ref_table'])) {
                return true;
            }
        }
        return false;
    }

    /**
     * Orphans and cross-tenant references for ONE relationship, as two counts.
     *
     * Both counts are computed against the parent's OWN id column, never
     * against the composite key, so they are meaningful before the composite
     * constraint exists — which is exactly when this gate runs. A cross-tenant
     * reference is one whose parent row exists but belongs to another tenant; an
     * orphan is one whose parent row does not exist at all. Counts only: no row
     * is selected, so no value can escape this method.
     *
     * @param array<string, mixed> $relation
     * @return array{0: int, 1: int} [orphan, cross_tenant]
     */
    private function referenceCounts(array $relation): array
    {
        // Every declared relationship is (tenant_id, reference) ->
        // (tenant_id, id); see Schema::SCOPED_RELATIONSHIPS.
        $child = $relation['table'];
        $parent = $relation['ref_table'];
        $tenant = $this->quote((string) $relation['columns'][0]);
        $reference = $this->quote((string) $relation['columns'][1]);
        $parentTenant = $this->quote((string) $relation['ref_columns'][0]);
        $parentId = $this->quote((string) $relation['ref_columns'][1]);

        $join = ' FROM `' . $child . '` c JOIN `' . $parent . '` p'
              . ' ON p.' . $parentId . ' = c.' . $reference;
        $cross = (int) $this->db->queryValue(
            'SELECT COUNT(*)' . $join
            . ' WHERE c.' . $tenant . ' IS NOT NULL'
            . ' AND p.' . $parentTenant . ' <> c.' . $tenant);

        $leftJoin = ' FROM `' . $child . '` c LEFT JOIN `' . $parent . '` p'
                  . ' ON p.' . $parentId . ' = c.' . $reference;
        $orphan = (int) $this->db->queryValue(
            'SELECT COUNT(*)' . $leftJoin
            . ' WHERE c.' . $reference . ' IS NOT NULL AND p.' . $parentId . ' IS NULL');

        return [$orphan, $cross];
    }

    /**
     * State of the supporting parent key a composite foreign key needs.
     *
     *   present  the UNIQUE index exists with exactly the declared columns
     *   pending  absent, but the migration about to run creates it
     *   missing  absent and nothing declares it — InnoDB would refuse the
     *            constraint, so this is a blocker
     *
     * A key that exists under the declared name with the wrong shape counts as
     * missing: the step that adds it would collide rather than succeed.
     *
     * @param array<string, true> $providedIndexes
     * @return 'present'|'pending'|'missing'
     */
    private function supportingIndexState(string $table, string $index, array $columns,
                                          array $providedIndexes): string
    {
        $actual = $this->tableState($table)['indexes'][strtolower($index)] ?? null;
        if ($actual !== null) {
            return $actual['unique'] && array_map('strtolower', $columns) === $actual['columns']
                ? 'present' : 'missing';
        }
        return isset($providedIndexes[$table . '.' . $index]) ? 'pending' : 'missing';
    }

    private function quote(string $identifier): string
    {
        return '`' . str_replace('`', '``', $identifier) . '`';
    }

    /** Numeric part of the server version, or '' when it cannot be parsed. */
    private function serverVersionNumber(): string
    {
        $version = $this->db->serverVersion();
        return preg_match('/\A(?:5\.5\.5-)?([0-9]+\.[0-9]+\.[0-9]+)/D', $version, $parts) === 1
            ? $parts[1] : '';
    }

    // ------------------------------------------------------------------
    // The counts-only report
    // ------------------------------------------------------------------

    /**
     * Read-only preflight of the target.
     *
     * Blockers carry `code`, `table` and `count` only, so the CLI adapter can
     * print them without any row value ever reaching a report.
     *
     * @return array{safe: bool,
     *   blockers: list<array{code: string, table: string, count: int}>,
     *   migration_status: array{state: string, applied_versions: int,
     *     pending_versions: int, verified_steps: int, pending_steps: int}}
     */
    public function inspect(): array
    {
        $files = $this->files();
        $tracker = $this->tracker();
        $blockers = [];

        // 1. Anything in the target that no migration owns. This is the rule
        //    that keeps a partial first run from being silently adopted: an
        //    exact, compatible journal and its verified step history may
        //    explain known objects, an arbitrary table may not.
        $owned = Schema::tables();
        foreach ($this->tables() as $table) {
            if (!in_array($table, $owned, true)) {
                $blockers[] = ['code' => self::C_UNEXPECTED_TABLE, 'table' => $table, 'count' => 1];
            }
        }

        // 2. A table an applied version owns but that is not there = partial
        //    DDL that no journal explains.
        foreach (array_keys($tracker) as $version) {
            foreach (Schema::tablesForVersion($version) as $table) {
                if (!$this->tableExists($table)) {
                    $blockers[] = ['code' => self::C_MISSING_TABLE, 'table' => $table, 'count' => 1];
                }
            }
        }

        // 3. Recorded files: unknown recorded file and checksum drift both fail
        //    before any DDL is attempted.
        $missing = 0;
        $drifted = 0;
        foreach ($tracker as $version => $checksum) {
            if (!isset($files[$version])) {
                $missing++;
            } elseif (!hash_equals($files[$version], $checksum)) {
                $drifted++;
            }
        }
        if ($missing > 0) {
            $blockers[] = ['code' => self::C_MISSING_FILE, 'table' => self::TRACKER_TABLE, 'count' => $missing];
        }
        if ($drifted > 0) {
            $blockers[] = ['code' => self::C_CHECKSUM_MISMATCH, 'table' => self::TRACKER_TABLE, 'count' => $drifted];
        }

        // 4. The journal must be one this build understands.
        if ($this->journalPresent() && !$this->journalCompatible($this->journalColumns())) {
            $blockers[] = ['code' => self::C_UNKNOWN_JOURNAL, 'table' => self::JOURNAL_TABLE, 'count' => 1];
        }

        // 5. Step ledger. Walk each version's steps IN ORDER and stop judging
        //    at the point the chain actually stands. A step further down is
        //    simply not reached yet; calling it ambiguous would report a
        //    perfectly normal half-migrated chain as corrupt.
        $verified = 0;
        $pending = 0;
        $failed = 0;
        $ambiguous = 0;
        foreach (array_keys($files) as $version) {
            $rows = $this->journalRows($version);
            $stopped = false;
            foreach (Schema::migrationSteps($version) as $step) {
                $row = $rows[$step['step_id']] ?? null;
                $status = $row === null ? '' : (string) $row['status'];
                if ($status === 'verified') {
                    $verified++;
                    continue;
                }
                $pending++;
                if ($status === 'failed') {
                    $failed++;
                    $stopped = true;
                    continue;
                }
                if ($stopped) {
                    continue;
                }
                if ($this->satisfies($step['post'])) {
                    continue;
                }
                if ($this->satisfies($step['pre'])) {
                    $stopped = true;
                    continue;
                }
                $ambiguous++;
                $stopped = true;
            }
        }
        if ($failed > 0) {
            $blockers[] = ['code' => self::C_FAILED_STEP, 'table' => self::JOURNAL_TABLE, 'count' => $failed];
        }
        if ($ambiguous > 0) {
            $blockers[] = ['code' => self::C_AMBIGUOUS_STEP, 'table' => self::JOURNAL_TABLE, 'count' => $ambiguous];
        }

        $pendingVersions = 0;
        foreach (array_keys($files) as $version) {
            if (!isset($tracker[$version])) {
                $pendingVersions++;
            }
        }
        if ($pendingVersions > 0) {
            $blockers[] = ['code' => self::C_PENDING_MIGRATION, 'table' => self::TRACKER_TABLE,
                           'count' => $pendingVersions];
        }

        // 6. The reference surface: the six same-tenant relationships 003
        //    installs. A pre-existing cross-tenant or orphan reference is the
        //    exact fault the installation gate refuses on, so `--status` has
        //    to surface it here — otherwise an operator discovers it only as a
        //    bare errno halfway through an ALTER. Same read-only sweep, same
        //    `pending` treatment of a parent key the pending file creates
        //    itself; only the shape is narrowed to this report's code/table/
        //    count triples. Skipped on a target with no relationship table at
        //    all, which is "not installed yet", not "blocked".
        if ($this->referenceSurfacePresent()) {
            $reference = $this->scopedIntegrity(self::providedIndexes(array_keys($files)));
            foreach ($reference['blockers'] as $blocker) {
                $blockers[] = ['code' => $blocker['code'], 'table' => $blocker['table'],
                               'count' => $blocker['count']];
            }
        }

        return [
            'safe' => $blockers === [],
            'blockers' => $blockers,
            'migration_status' => [
                'state' => $this->state($blockers, $pendingVersions, $pending),
                'applied_versions' => count($tracker),
                'pending_versions' => $pendingVersions,
                'verified_steps' => $verified,
                'pending_steps' => $pending,
            ],
        ];
    }

    /**
     * @param list<array{code: string, table: string, count: int}> $blockers
     */
    private function state(array $blockers, int $pendingVersions, int $pendingSteps): string
    {
        foreach ($blockers as $blocker) {
            if ($blocker['code'] === self::C_AMBIGUOUS_STEP) {
                return 'AMBIGUOUS';
            }
        }
        foreach ($blockers as $blocker) {
            if ($blocker['code'] !== self::C_PENDING_MIGRATION) {
                return 'BLOCKED';
            }
        }
        return ($pendingVersions > 0 || $pendingSteps > 0) ? 'PENDING' : 'COMPLETE';
    }
}
