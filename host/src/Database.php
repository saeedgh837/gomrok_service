<?php
/**
 * Database.php — the only PDO wrapper the IRICA cloud-sync host uses.
 *
 * Deliberately small and framework-free: no Composer dependency, no ORM, no
 * query builder. Contract: lab/docs/CLOUD_SYNC_SPEC.md §6 (the operations the
 * API needs) and §10.6 (prepared statements everywhere, identifier
 * allowlisting).
 *
 * Security invariants, all enforced here so no route can opt out:
 *   * Never concatenate user input into SQL. Values are always bound.
 *   * Identifiers (table and column names) only ever come from
 *     Schema::tables() / Schema::columnsFor(), which are constants declared in
 *     Schema.php — an allowlist this module owns. A name that is not on it
 *     throws before any SQL is built.
 *   * ATTR_EMULATE_PREPARES is false, so the server does the type handling and
 *     a bound value can never be reinterpreted as syntax.
 *
 * Every timestamp the server stores is UTC (spec §2); the connection pins
 * time_zone to '+00:00' so an expression added later cannot drift.
 */

declare(strict_types=1);

final class Database
{
    /** SQLSTATEs that mean "the transaction is retryable". */
    private const RETRYABLE_SQLSTATE = [
        '40001' => true, // serialization failure / deadlock
    ];

    /** MySQL errno values that mean "retry this transaction". */
    private const RETRYABLE_ERRNO = [
        1213 => true, // ER_LOCK_DEADLOCK
        1205 => true, // ER_LOCK_WAIT_TIMEOUT
    ];

    private \PDO $pdo;

    /** Bounded transaction retries: attempts beyond this rethrow. */
    private int $transactionAttempts;

    /** Base backoff in microseconds; grows exponentially, capped below. */
    private int $backoffBaseUs;

    private function __construct(\PDO $pdo, int $transactionAttempts = 3,
                                 int $backoffBaseUs = 50000)
    {
        $this->pdo = $pdo;
        $this->transactionAttempts = max(1, $transactionAttempts);
        $this->backoffBaseUs = max(0, $backoffBaseUs);
    }

    // ------------------------------------------------------------------
    // Construction
    // ------------------------------------------------------------------

    /**
     * Build a connection from a config array.
     *
     * Config comes IN: the caller (workstream C-host) owns host/config/, so
     * this class never opens a file path of its own and never reads an
     * environment variable to find credentials.
     *
     * Recognised keys: host, port, dbname, user, password, plus the optional
     * transaction_attempts and backoff_base_us tuning knobs.
     *
     * @param array<string, mixed> $cfg
     * @throws \InvalidArgumentException on a missing or malformed key
     * @throws \PDOException             on connection or session failure
     */
    public static function fromConfig(array $cfg): self
    {
        $host = self::requireString($cfg, 'host');
        $dbname = self::requireString($cfg, 'dbname');
        $user = self::requireString($cfg, 'user');
        $password = self::requireString($cfg, 'password');
        $port = $cfg['port'] ?? 3306;
        if (!is_int($port) && !(is_string($port) && ctype_digit($port))) {
            throw new \InvalidArgumentException('port must be an integer');
        }
        $port = (int) $port;
        if ($port < 1 || $port > 65535) {
            throw new \InvalidArgumentException('port out of range: ' . $port);
        }

        // utf8mb4 is set in the DSN, not with SET NAMES: the charset must be
        // right from the very first round trip, before any session command runs.
        $dsn = sprintf(
            'mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4',
            $host,
            $port,
            $dbname
        );

        $options = [
            \PDO::ATTR_ERRMODE            => \PDO::ERRMODE_EXCEPTION,
            \PDO::ATTR_DEFAULT_FETCH_MODE => \PDO::FETCH_ASSOC,
            \PDO::ATTR_EMULATE_PREPARES   => false,
            \PDO::ATTR_STRINGIFY_FETCHES  => false,
        ];
        // Connect timeout, seconds. Only meaningful with mysqlnd, which is what
        // every supported PHP build ships; guard so a non-mysqlnd build still
        // constructs rather than fatalling on an unknown attribute.
        if (defined('PDO::MYSQL_ATTR_TIMEOUT')) {
            $options[\PDO::MYSQL_ATTR_TIMEOUT] = 5;
        }

        $pdo = new \PDO($dsn, $user, $password, $options);

        // Session setup, one statement at a time: multi-statement init commands
        // are not portable across mysqlnd/mysqli builds.
        //   time_zone        -> every stored timestamp is UTC (spec §2)
        //   lock wait timeout -> a stuck row lock fails fast instead of hanging
        //                        a request for the server default (50 s)
        $pdo->exec("SET time_zone = '+00:00'");
        $pdo->exec('SET SESSION innodb_lock_wait_timeout = 5');

        $attempts = isset($cfg['transaction_attempts'])
            ? (int) $cfg['transaction_attempts'] : 3;
        $base = isset($cfg['backoff_base_us'])
            ? (int) $cfg['backoff_base_us'] : 50000;

        return new self($pdo, $attempts, $base);
    }

    /**
     * @param array<string, mixed> $cfg
     */
    private static function requireString(array $cfg, string $key): string
    {
        if (!array_key_exists($key, $cfg)) {
            throw new \InvalidArgumentException('missing config key: ' . $key);
        }
        $value = $cfg[$key];
        if (!is_string($value) || $value === '') {
            throw new \InvalidArgumentException('config key must be a non-empty string: ' . $key);
        }
        return $value;
    }

    public function pdo(): \PDO
    {
        return $this->pdo;
    }

    // ------------------------------------------------------------------
    // Identifier allowlist (spec §10.6)
    // ------------------------------------------------------------------

    /**
     * Quote a table name, after proving it is one Schema.php declares.
     *
     * This is the ONLY path a name takes into SQL. The backtick quoting is not
     * the safety mechanism — the allowlist is; quoting merely keeps a legal
     * name readable.
     */
    public function table(string $table): string
    {
        if (!\Schema::isTable($table)) {
            throw new \InvalidArgumentException(
                'table is not on the schema allowlist: ' . $table
            );
        }
        return '`' . $table . '`';
    }

    /**
     * Quote a column name, after proving it belongs to `$table`.
     */
    public function column(string $table, string $column): string
    {
        if (!\Schema::hasColumn($table, $column)) {
            throw new \InvalidArgumentException(
                'column is not on the schema allowlist: ' . $table . '.' . $column
            );
        }
        return '`' . $column . '`';
    }

    /**
     * A list of allowlisted column names, for a column set built at runtime.
     *
     * @param list<string> $columns
     * @return list<string> quoted, in the order given
     */
    public function columns(string $table, array $columns): array
    {
        return array_map(
            static fn (string $column): string => self::quoteColumn($table, $column),
            $columns
        );
    }

    private static function quoteColumn(string $table, string $column): string
    {
        if (!\Schema::hasColumn($table, $column)) {
            throw new \InvalidArgumentException(
                'column is not on the schema allowlist: ' . $table . '.' . $column
            );
        }
        return '`' . $column . '`';
    }

    // ------------------------------------------------------------------
    // Reading
    // ------------------------------------------------------------------

    /**
     * Run a SELECT and return every row.
     *
     * @param array<int|string, mixed> $params positional (0..n) or named keys
     * @return list<array<string, mixed>>
     */
    public function query(string $sql, array $params = []): array
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        /** @var list<array<string, mixed>> $rows */
        $rows = $stmt->fetchAll();
        return $rows;
    }

    /**
     * Run a SELECT and return the first row, or null.
     *
     * @param array<int|string, mixed> $params
     * @return array<string, mixed>|null
     */
    public function queryOne(string $sql, array $params = []): ?array
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        $row = $stmt->fetch();
        return $row === false ? null : $row;
    }

    /**
     * Run a SELECT and return the single scalar of the first row.
     *
     * @param array<int|string, mixed> $params
     */
    public function queryValue(string $sql, array $params = []): mixed
    {
        $row = $this->queryOne($sql, $params);
        if ($row === null || $row === []) {
            return null;
        }
        return array_values($row)[0];
    }

    // ------------------------------------------------------------------
    // Writing
    // ------------------------------------------------------------------

    /**
     * Execute a statement and return the affected row count.
     *
     * @param array<int|string, mixed> $params
     */
    public function execute(string $sql, array $params = []): int
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return $stmt->rowCount();
    }

    /**
     * INSERT one row. Table and columns come from the allowlist; every value
     * is bound. Returns the affected row count (1 on success).
     *
     * @param array<string, mixed> $row column => value
     */
    public function insert(string $table, array $row): int
    {
        if ($row === []) {
            throw new \InvalidArgumentException('insert requires at least one column');
        }
        $names = array_keys($row);
        $sql = 'INSERT INTO ' . $this->table($table)
            . ' (' . implode(', ', $this->columns($table, $names)) . ')'
            . ' VALUES (' . implode(', ', array_fill(0, count($names), '?')) . ')';
        $values = [];
        foreach ($row as $column => $value) {
            $values[] = $this->normalizeValue($table, (string) $column, $value);
        }
        return $this->execute($sql, $values);
    }

    /**
     * Prepare one bound value for its column.
     *
     * The only transformation is on datetime columns: spec §4 puts an explicit
     * offset on the wire, a SQL DATETIME has none, so the offset is folded into
     * UTC here rather than left for the server to reject with a bare 1292.
     * Everything else is passed through untouched — no coercion, no guessing.
     */
    private function normalizeValue(string $table, string $column,
                                    mixed $value): mixed
    {
        if (!is_string($value)) {
            return $value;
        }
        $spec = \Schema::column($table, $column);
        if ($spec === null || $spec['type'] !== 'datetime') {
            return $value;
        }
        return \Schema::toSqlTimestamp($value);
    }

    /**
     * UPDATE rows matching a WHERE clause written by the caller from allowlisted
     * columns and bound parameters. Returns the affected row count, which the
     * API uses for compare-and-swap (spec §6.1): a CAS that matches 0 rows is a
     * version conflict.
     *
     * @param array<string, mixed> $set    column => value
     * @param array<int|string, mixed> $whereParams
     */
    public function update(string $table, array $set, string $where,
                           array $whereParams = []): int
    {
        if ($set === []) {
            throw new \InvalidArgumentException('update requires at least one column');
        }
        if (trim($where) === '') {
            throw new \InvalidArgumentException('update requires a WHERE clause');
        }
        $assignments = [];
        $values = [];
        foreach ($set as $column => $value) {
            $assignments[] = $this->column($table, (string) $column) . ' = ?';
            $values[] = $this->normalizeValue($table, (string) $column, $value);
        }
        $sql = 'UPDATE ' . $this->table($table)
            . ' SET ' . implode(', ', $assignments)
            . ' WHERE ' . $where;
        return $this->execute($sql, array_merge($values, array_values($whereParams)));
    }

    /**
     * DELETE rows matching a WHERE clause. The application does not use this
     * for synced business rows — those are tombstoned (spec §4) — so the API
     * should prefer update() with deleted_at set. It exists for housekeeping
     * (expired idempotency keys, old login_attempts, expired sessions).
     *
     * @param array<int|string, mixed> $whereParams
     */
    public function delete(string $table, string $where, array $whereParams = []): int
    {
        if (trim($where) === '') {
            throw new \InvalidArgumentException('delete requires a WHERE clause');
        }
        $sql = 'DELETE FROM ' . $this->table($table) . ' WHERE ' . $where;
        return $this->execute($sql, array_values($whereParams));
    }

    // ------------------------------------------------------------------
    // Transactions (spec §6.1: a whole mutation batch commits together)
    // ------------------------------------------------------------------

    /**
     * Run `$fn` inside a transaction, retrying a bounded number of times when
     * the server reports a deadlock or lock-wait timeout.
     *
     * Retries are bounded and backed off with jitter: an infinite retry loop on
     * a contended row would turn a transient conflict into an outage.
     * Non-retryable errors rethrow immediately — a retry would only replay a
     * deterministic failure.
     *
     * @template T
     * @param \Closure(self): T $fn
     * @return T whatever $fn returned
     * @throws \Throwable the last failure, after the attempt budget is spent
     */
    public function transaction(callable $fn): mixed
    {
        $attempts = $this->transactionAttempts;
        $last = null;
        for ($attempt = 1; $attempt <= $attempts; $attempt++) {
            try {
                $this->pdo->beginTransaction();
                $result = $fn($this);
                $this->pdo->commit();
                return $result;
            } catch (\Throwable $error) {
                if ($this->pdo->inTransaction()) {
                    try {
                        $this->pdo->rollBack();
                    } catch (\Throwable $rollbackError) {
                        // A failed rollback must not mask the original error.
                        unset($rollbackError);
                    }
                }
                $last = $error;
                if (!$this->isRetryable($error) || $attempt === $attempts) {
                    throw $error;
                }
                $this->backoff($attempt);
            }
        }
        throw $last instanceof \Throwable ? $last : new \RuntimeException('transaction failed');
    }

    /**
     * Deadlock (1213) and lock-wait timeout (1205) are the only retryable
     * outcomes; a constraint violation or a syntax error never is.
     */
    private function isRetryable(\Throwable $error): bool
    {
        if (!$error instanceof \PDOException) {
            return false;
        }
        $code = (string) $error->getCode();
        if (isset(self::RETRYABLE_SQLSTATE[$code])) {
            return true;
        }
        // Everything else is decided by the server errno: SQLSTATE HY000 is
        // far too broad to retry on by itself.
        $info = $error->errorInfo ?? null;
        $errno = is_array($info) && isset($info[1]) ? (int) $info[1] : 0;
        return isset(self::RETRYABLE_ERRNO[$errno]);
    }

    /** Exponential backoff with full jitter, capped at 1 second. */
    private function backoff(int $attempt): void
    {
        if ($this->backoffBaseUs <= 0) {
            return;
        }
        $ceiling = min(1000000, $this->backoffBaseUs * (2 ** ($attempt - 1)));
        usleep(random_int(0, (int) $ceiling));
    }

    // ------------------------------------------------------------------
    // Diagnostics
    // ------------------------------------------------------------------

    /** Server version, for logs and the health probe. Never a credential. */
    public function serverVersion(): string
    {
        $value = $this->queryValue('SELECT VERSION()');
        return is_string($value) ? $value : '';
    }

    /** The connection time zone, asserted by the tests to be UTC. */
    public function sessionTimeZone(): string
    {
        $value = $this->queryValue('SELECT @@session.time_zone');
        return is_string($value) ? $value : '';
    }

    /** True while a transaction is open on this connection. */
    public function inTransaction(): bool
    {
        return $this->pdo->inTransaction();
    }
}
