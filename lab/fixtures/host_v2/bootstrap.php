<?php
/** Disposable Phase B fixture adapter. Never reads application configuration. */
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('log_errors', '0');

function same(mixed $expected, mixed $actual): void
{
    if ($expected !== $actual) {
        throw new RuntimeException('FIXTURE_ASSERTION_FAILED');
    }
}

function expectApiCode(string $expected, callable $operation): void
{
    if (!class_exists('ApiException', false)) {
        // A type hint on an unloaded class would silently never match and turn
        // every negative expectation into FIXTURE_EXPECTED_REJECTION.
        throw new RuntimeException('FIXTURE_EXCEPTION_CLASS_MISSING');
    }
    try {
        $operation();
    } catch (ApiException $error) {
        same($expected, $error->errorCode);
        return;
    }
    throw new RuntimeException('FIXTURE_EXPECTED_REJECTION');
}

final class BFixture
{
    private const MAX_CONFIG = 16384;
    private static array $connections = [];
    private static array $server = [];

    public static function repoRoot(): string
    {
        return dirname(__DIR__, 3);
    }

    public static function same(mixed $expected, mixed $actual): void
    {
        same($expected, $actual);
    }

    public static function code(string $expected, callable $operation): void
    {
        expectApiCode($expected, $operation);
    }

    public static function password(): string
    {
        return bin2hex(random_bytes(16));
    }

    private static function text(mixed $value): bool
    {
        // Match Python str.strip whitespace, including its four C0 separators.
        return is_string($value) && !str_contains($value, "\0")
            && preg_match('/[^\s\p{Z}\x{0085}\x{001c}-\x{001f}]/u', $value) === 1;
    }

    private static function keys(mixed $object, array $expected): bool
    {
        if (!$object instanceof stdClass) {
            return false;
        }
        $actual = array_keys(get_object_vars($object));
        sort($actual);
        sort($expected);
        return $actual === $expected;
    }

    /** Pure structural/identity check; no product imports, process or PDO access. */
    public static function validate(mixed $config, mixed $marker = null): stdClass
    {
        if (!self::keys($config, ['fixture', 'run_id', 'instance_id', 'php_path', 'migrate', 'runtime'])
            || $config->fixture !== true || !is_string($config->run_id)
            || preg_match('/\A[0-9a-f]{16}\z/D', $config->run_id) !== 1
            || !self::text($config->instance_id) || !self::text($config->php_path)) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        $dbname = 'irica_b_test_' . $config->run_id;
        foreach (['migrate', 'runtime'] as $role) {
            $connection = $config->$role;
            if (!self::keys($connection, ['host', 'port', 'dbname', 'user', 'password'])
                || $connection->host !== '127.0.0.1' || $connection->port !== 3307
                || $connection->dbname !== $dbname
                || !self::text($connection->user) || !self::text($connection->password)) {
                throw new RuntimeException('FIXTURE_TARGET_REJECTED');
            }
        }
        if ($config->migrate->user === $config->runtime->user) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        if (func_num_args() > 1 && (!self::keys($marker, ['run_id', 'instance_id', 'dbname'])
            || $marker->run_id !== $config->run_id || $marker->instance_id !== $config->instance_id
            || $marker->dbname !== $dbname)) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        return $config;
    }

    /** Strict UTF-8 JSON, bounded size/depth and no shadowed duplicate keys. */
    public static function decode(string $raw): mixed
    {
        if (strlen($raw) > self::MAX_CONFIG) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        try {
            $decoded = json_decode($raw, false, 64, JSON_THROW_ON_ERROR);
        } catch (JsonException $error) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        preg_match_all('/"(?:[^"\\\\]|\\\\.)*"|[{}\[\]:,]/s', $raw, $matches);
        $stack = [];
        foreach ($matches[0] as $index => $token) {
            if ($token === '{' || $token === '[') {
                $stack[] = [];
            } elseif ($token === '}' || $token === ']') {
                array_pop($stack);
            } elseif ($token[0] === '"' && ($matches[0][$index + 1] ?? '') === ':') {
                $key = json_decode($token, true, 64, JSON_THROW_ON_ERROR);
                $top = count($stack) - 1;
                if (array_key_exists($key, $stack[$top])) {
                    throw new RuntimeException('FIXTURE_TARGET_REJECTED');
                }
                $stack[$top][$key] = true;
            }
        }
        return $decoded;
    }

    /**
     * Ruling 11: never read the credential file over SMB, or through an
     * extended/device path. Flattening first catches every spelling of `\\`.
     */
    public static function isLocalPath(string $path): bool
    {
        return !str_starts_with(str_replace('\\', '/', $path), '//');
    }

    public static function readConfig(string $path): stdClass
    {
        $normalized = str_replace('\\', '/', $path);
        $root = strtolower(str_replace('\\', '/', self::repoRoot())) . '/';
        if (!self::text($path) || !self::isLocalPath($path)
            || strtolower(pathinfo($path, PATHINFO_EXTENSION)) !== 'json'
            || preg_match('~\A(?:[A-Za-z]:/|/)~', $normalized) !== 1
            || str_starts_with(strtolower($normalized), $root)) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        $resolved = realpath($path);
        if ($resolved === false) {
            throw new RuntimeException('FIXTURE_CONFIG_MISSING');
        }
        if (!self::isLocalPath($resolved)
            || str_starts_with(strtolower(str_replace('\\', '/', $resolved)), $root)) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        $source = @fopen($resolved, 'rb');
        if ($source === false) {
            throw new RuntimeException('FIXTURE_CONFIG_MISSING');
        }
        try {
            $info = fstat($source);
            if ($info === false || ($info['mode'] & 0170000) !== 0100000 || $info['size'] > self::MAX_CONFIG) {
                throw new RuntimeException('FIXTURE_TARGET_REJECTED');
            }
            $raw = fread($source, self::MAX_CONFIG);
        } finally {
            fclose($source);
        }
        if ($raw === false) {
            throw new RuntimeException('FIXTURE_CONFIG_MISSING');
        }
        return self::validate(self::decode($raw));
    }

    public static function validateServer(string $driver, string $version, string $timezone): void
    {
        $matched = preg_match('/\A(?:5\.5\.5-)?([0-9]+\.[0-9]+\.[0-9]+)(?:[-.].*)?\z/D', $version, $parts);
        // Documented support floor, host/INSTALL.md: "MySQL 5.7+/MariaDB 10.3+".
        $minimum = str_contains($version, 'MariaDB') ? '10.3.0' : '5.7.0';
        if ($driver !== 'mysql' || $timezone !== '+00:00' || $matched !== 1
            || version_compare($parts[1], $minimum, '<')) {
            throw new RuntimeException('FIXTURE_SERVER_REJECTED');
        }
    }

    /** Server/driver/time-zone/table-count record for the run log; never a credential. */
    public static function readyLine(string $driver, string $version, string $timezone, int $tables): string
    {
        self::validateServer($driver, $version, $timezone);
        if ($tables < 0) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        $line = 'FIXTURE_READY driver=' . $driver . ' server=' . $version
              . ' tz=' . $timezone . ' tables=' . $tables;
        if (preg_match('/\AFIXTURE_READY driver=[a-z]{1,16} server=[0-9A-Za-z.+:~\/\-]{1,96}'
            . ' tz=\+[0-9]{2}:[0-9]{2} tables=[0-9]{1,18}\z/D', $line) !== 1) {
            throw new RuntimeException('FIXTURE_SERVER_REJECTED');
        }
        return $line;
    }

    public static function readyRecord(): string
    {
        if (!isset(self::$server['tables'])) {
            throw new RuntimeException('FIXTURE_RUNTIME_MISSING');
        }
        return self::readyLine(self::$server['driver'], self::$server['version'],
                               self::$server['timezone'], self::$server['tables']);
    }

    /**
     * A populated target is legal (Task 2 fills it); a leftover owned probe is not.
     * Pristine-before-001 belongs to the migration scenario, not to this gate.
     */
    public static function assertNoProbeObjects(mixed $count): void
    {
        if (!is_int($count) || $count < 0) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        if ($count !== 0) {
            throw new RuntimeException('FIXTURE_TARGET_NOT_EMPTY');
        }
    }

    public static function assertDdlDenied(?array $info): void
    {
        if (($info[0] ?? null) !== '42000' || !in_array($info[1] ?? null, [1142, 1044], true)) {
            throw new RuntimeException('FIXTURE_DDL_CHECK_FAILED');
        }
    }

    /**
     * Effective-grant scope gate for BOTH principals. Deliberately no role
     * lookup: the role-inspection function is MySQL 8.0+ and would raise the
     * engine floor above the documented MySQL 5.7+/MariaDB 10.3+ (host/INSTALL.md).
     */
    public static function validatePrincipalGrants(array $grants, string $dbname, bool $allowDdl): void
    {
        $dml = ['SELECT', 'INSERT', 'UPDATE', 'DELETE'];
        $required = $allowDdl ? array_merge($dml, ['CREATE', 'DROP', 'ALTER', 'INDEX']) : $dml;
        $allowed = $allowDdl ? array_merge($required, ['REFERENCES']) : $dml;
        $granted = [];
        $markerSelect = false;
        foreach ($grants as $grant) {
            if (!is_string($grant) || stripos($grant, 'WITH GRANT OPTION') !== false
                || preg_match('/\AGRANT (.+) ON (.+) TO .+\z/Ds', $grant, $parts) !== 1) {
                throw new RuntimeException('FIXTURE_GRANT_REJECTED');
            }
            $privileges = array_map('trim', explode(',', $parts[1]));
            // SHOW GRANTS can escape schema-name underscores in its pattern spelling.
            $scope = str_replace('\\_', '_', $parts[2]);
            if ($scope === '*.*' && $privileges === ['USAGE']) {
                continue;
            }
            if ($scope === '`' . $dbname . '`.*') {
                if (array_diff($privileges, $allowed) !== []) {
                    throw new RuntimeException('FIXTURE_GRANT_REJECTED');
                }
                $granted = array_merge($granted, $privileges);
            } elseif ($scope === '`irica_b_fixture_control`.`runs`' && $privileges === ['SELECT']) {
                $markerSelect = true;
            } else {
                throw new RuntimeException('FIXTURE_GRANT_REJECTED');
            }
        }
        if (!$markerSelect || array_diff($required, $granted) !== []) {
            throw new RuntimeException('FIXTURE_GRANT_REJECTED');
        }
    }

    private static function asCount(mixed $value): int
    {
        if (is_int($value) && $value >= 0) {
            return $value;
        }
        if (is_string($value) && preg_match('/\A[0-9]{1,18}\z/D', $value) === 1) {
            return (int) $value;
        }
        throw new RuntimeException('FIXTURE_TARGET_REJECTED');
    }

    /** Grant strings only; SHOW GRANTS' column name carries the account name. */
    private static function grants(Database $db): array
    {
        return array_map(static fn(array $row): mixed => array_values($row)[0], $db->query('SHOW GRANTS'));
    }

    private static function verifyConnection(Database $db, stdClass $config): void
    {
        if ($db->queryValue('SELECT DATABASE()') !== $config->runtime->dbname) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        $rows = $db->query(
            'SELECT run_id, instance_id, dbname FROM irica_b_fixture_control.runs WHERE run_id = :run_id LIMIT 2',
            ['run_id' => $config->run_id]
        );
        if (count($rows) !== 1) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        self::validate($config, (object) $rows[0]);
        $driver = (string) $db->pdo()->getAttribute(PDO::ATTR_DRIVER_NAME);
        $version = (string) $db->pdo()->getAttribute(PDO::ATTR_SERVER_VERSION);
        $timezone = (string) $db->queryValue('SELECT @@session.time_zone');
        self::validateServer($driver, $version, $timezone);
        self::$server = ['driver' => $driver, 'version' => $version, 'timezone' => $timezone];
    }

    /** Reject leftover owned probes, and return the target's object count. */
    private static function targetObjects(Database $db, stdClass $config): int
    {
        $row = $db->queryOne(
            'SELECT COUNT(*) AS total,'
            . ' COALESCE(SUM(CASE WHEN LEFT(table_name, 20) = :prefix THEN 1 ELSE 0 END), 0) AS probes'
            . ' FROM information_schema.tables WHERE table_schema = :schema',
            ['schema' => $config->runtime->dbname, 'prefix' => '__irica_b_ddl_probe_']
        );
        if ($row === null || !array_key_exists('total', $row) || !array_key_exists('probes', $row)) {
            throw new RuntimeException('FIXTURE_TARGET_REJECTED');
        }
        self::assertNoProbeObjects(self::asCount($row['probes']));
        return self::asCount($row['total']);
    }

    public static function db(bool $migration = false): Database
    {
        if (self::$connections === []) {
            $path = getenv('IRICA_B_FIXTURE_CONFIG');
            if (!is_string($path) || $path === '') {
                throw new RuntimeException('FIXTURE_CONFIG_MISSING');
            }
            // Static guards finish before extensions, product code or PDO are used.
            $config = self::readConfig($path);
            if (PHP_VERSION_ID < 80100 || !extension_loaded('pdo_mysql')
                || !extension_loaded('mbstring')) {
                throw new RuntimeException('FIXTURE_RUNTIME_MISSING');
            }
            date_default_timezone_set('UTC');
            require_once self::repoRoot() . '/host/src/Database.php';
            try {
                $runtime = Database::fromConfig((array) $config->runtime);
                self::verifyConnection($runtime, $config);
                self::targetObjects($runtime, $config);
                $probe = '__irica_b_ddl_probe_' . $config->run_id;
                try {
                    $runtime->execute('CREATE TABLE `' . $config->runtime->dbname . '`.`' . $probe . '` (`id` INT NOT NULL)');
                } catch (PDOException $error) {
                    self::assertDdlDenied($error->errorInfo);
                    $denied = true;
                }
                if (!isset($denied)) {
                    // Preserve this exact owned probe for controller cleanup/reprovisioning.
                    throw new RuntimeException('FIXTURE_RUNTIME_DDL_ALLOWED');
                }
                self::validatePrincipalGrants(self::grants($runtime), $config->runtime->dbname, false);
                $ddl = Database::fromConfig((array) $config->migrate);
                self::verifyConnection($ddl, $config);
                self::validatePrincipalGrants(self::grants($ddl), $config->runtime->dbname, true);
                // The real DDL attempt above must have created nothing.
                self::$server['tables'] = self::targetObjects($ddl, $config);
            } catch (PDOException $error) {
                throw new RuntimeException('FIXTURE_CONNECTION_FAILED');
            }
            // Cache only once both distinct principals have passed all checks.
            self::$connections = ['runtime' => $runtime, 'migrate' => $ddl];
        }
        return self::$connections[$migration ? 'migrate' : 'runtime'];
    }

    private const PREREQUISITE_LABELS = [
        'FIXTURE_TARGET_REJECTED', 'FIXTURE_CONFIG_MISSING', 'FIXTURE_RUNTIME_MISSING',
        'FIXTURE_SERVER_REJECTED', 'FIXTURE_DDL_CHECK_FAILED', 'FIXTURE_RUNTIME_DDL_ALLOWED',
        'FIXTURE_GRANT_REJECTED', 'FIXTURE_CONNECTION_FAILED', 'FIXTURE_ARGUMENT_REJECTED',
        'FIXTURE_TARGET_NOT_EMPTY', 'FIXTURE_EXCEPTION_CLASS_MISSING',
    ];

    private const SCENARIO_LABELS = [
        'FIXTURE_ASSERTION_FAILED', 'FIXTURE_EXPECTED_REJECTION', 'FIXTURE_SCENARIO_FAILED',
    ];

    public static function failureLabel(Throwable $error): string
    {
        $message = $error->getMessage();
        return in_array($message, self::PREREQUISITE_LABELS, true)
            || in_array($message, self::SCENARIO_LABELS, true)
            ? $message : 'FIXTURE_SCENARIO_FAILED';
    }

    /** Prerequisite refusals are exit 2; a behavioural scenario failure is exit 1. */
    public static function failureExit(string $label): int
    {
        return in_array($label, self::SCENARIO_LABELS, true) ? 1 : 2;
    }
}

set_exception_handler(static function (Throwable $error): void {
    $label = BFixture::failureLabel($error);
    print($label . "\n");
    exit(BFixture::failureExit($label));
});

// These CLI modes are fixture-only; none exposes arbitrary SQL, files or HTTP.
if (isset($_SERVER['SCRIPT_FILENAME']) && realpath($_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    try {
        $args = array_slice($argv, 1);
        if ($args === ['--validate-json']) {
            $raw = file_get_contents('php://stdin', false, null, 0, 16385);
            if ($raw === false) {
                throw new RuntimeException('FIXTURE_TARGET_REJECTED');
            }
            $payload = BFixture::decode($raw);
            if (!$payload instanceof stdClass || !property_exists($payload, 'config')
                || array_diff(array_keys(get_object_vars($payload)), ['config', 'marker']) !== []) {
                throw new RuntimeException('FIXTURE_TARGET_REJECTED');
            }
            if (property_exists($payload, 'marker')) {
                BFixture::validate($payload->config, $payload->marker);
            } else {
                BFixture::validate($payload->config);
            }
            print("FIXTURE_VALID\n");
        } elseif ($args === ['--check-prerequisites']
                  || (count($args) === 3 && $args[0] === '--check-prerequisites' && $args[1] === '--fixture-config')) {
            if (count($args) === 3) {
                putenv('IRICA_B_FIXTURE_CONFIG=' . $args[2]);
            }
            BFixture::db();
            BFixture::db(true);
            print(BFixture::readyRecord() . "\n");
        } else {
            throw new RuntimeException('FIXTURE_ARGUMENT_REJECTED');
        }
    } catch (Throwable $error) {
        $label = BFixture::failureLabel($error);
        print($label . "\n");
        exit(BFixture::failureExit($label));
    }
}
