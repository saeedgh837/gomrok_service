<?php
/** Host-controlled operator creation and exact-realm credential recovery. */
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
try {
    foreach (array_slice($argv, 1) as $argument) {
        if (preg_match('/\A(?:--username=.+|--password-stdin|--reset-password|--quiet|--help)\z/D', $argument) !== 1) {
            throw new InvalidArgumentException('unsupported option; use --help (password arguments are forbidden)');
        }
    }
    $options = getopt('', ['username:', 'password-stdin', 'reset-password', 'quiet', 'help']);
    if (isset($options['help'])) {
        fwrite(STDOUT, "Create a platform operator, never a CRM account.\n"
            . "  --username=NAME    explicit canonical operator name\n"
            . "  --password-stdin   read one password line from standard input, without echo\n"
            . "  --reset-password   rotate only this existing operator's credential\n"
            . "  --quiet            suppress identity details\n"
            . "Use IRICA_OPERATOR_PASSWORD or --password-stdin; omit both to generate once.\n");
        exit(0);
    }
    if (!isset($options['username']) || !is_string($options['username'])) {
        throw new InvalidArgumentException('an explicit --username=NAME is required');
    }
    $name = Workspace::canonical($options['username']);
    $fromEnv = getenv('IRICA_OPERATOR_PASSWORD');
    $password = is_string($fromEnv) && $fromEnv !== '' ? $fromEnv : null;
    if (isset($options['password-stdin'])) {
        if ($password !== null) {
            throw new InvalidArgumentException('choose one password input source');
        }
        if (function_exists('stream_isatty') && stream_isatty(STDIN)) {
            throw new InvalidArgumentException('password input must be redirected, not echoed in a terminal');
        }
        $line = fgets(STDIN, 4099);
        if (!is_string($line)) {
            throw new InvalidArgumentException('password input is missing');
        }
        $password = preg_replace('/\r?\n\z/D', '', $line);
    }
    $generated = $password === null;
    if ($generated) {
        $password = Tokens::newOpaque();
    }
    if (strlen($password) < 12 || strlen($password) > 4096) {
        throw new InvalidArgumentException('password must contain 12 to 4096 bytes');
    }
    $hash = Auth::hashPassword($password);
    $cfg = Config::load();
    $db = Database::fromConfig($cfg['db']);
    $reset = isset($options['reset-password']);
    $result = $reset ? OperatorAuth::rotatePassword($db, $name, $hash)
        : OperatorAuth::create($db, $name, $hash);
    fwrite(STDOUT, 'OK    ' . ($reset ? 'operator password rotated; sessions revoked' : 'operator created') . PHP_EOL);
    if (!isset($options['quiet'])) {
        fwrite(STDOUT, '      username: ' . $result['username'] . PHP_EOL
            . '      operator_id: ' . $result['operator_id'] . PHP_EOL);
    }
    if ($generated) {
        fwrite(STDOUT, 'PASSWORD (shown once):' . PHP_EOL . '  ' . $password . PHP_EOL);
    }
    exit(0);
} catch (PDOException $error) {
    $errno = (int) ($error->errorInfo[1] ?? 0);
    $message = $errno === 1062 ? 'operator name is already reserved'
        : 'database operation failed (errno ' . $errno . ')';
} catch (ApiException | InvalidArgumentException $error) {
    $message = $error->getMessage();
} catch (Throwable $error) {
    $message = 'operator operation failed (' . get_class($error) . ')';
}
fwrite(STDERR, 'FAIL  ' . $message . PHP_EOL);
exit(1);
