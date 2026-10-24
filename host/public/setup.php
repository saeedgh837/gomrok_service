<?php
/** Controlled no-SSH maintenance and one-shot operator bootstrap, never customer creation. */
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
ini_set('html_errors', '0');
error_reporting(E_ALL);

if (!defined('HOST_ROOT')) {
    define('HOST_ROOT', dirname(__DIR__));
}
foreach (['Schema', 'Database', 'Http', 'Ids', 'Config', 'Log', 'Tokens', 'Roles',
          'RateLimit', 'Workspace', 'Auth', 'OperatorAuth'] as $classFile) {
    require_once HOST_ROOT . '/src/' . $classFile . '.php';
}

header('Cache-Control: no-store, no-cache, max-age=0, must-revalidate');
header('Pragma: no-cache');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('Referrer-Policy: no-referrer');
header('Cross-Origin-Opener-Policy: same-origin');
header('Cross-Origin-Resource-Policy: same-origin');
header("Content-Security-Policy: default-src 'none'; style-src 'self'; "
    . "base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
header('Content-Type: text/html; charset=UTF-8');

function setupEscape(string $text): string
{
    return htmlspecialchars($text, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// Keep the migration CLI's SQL splitting rules, including quoted semicolons.
function setupSplitStatements(string $sql): array
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
            $close = $state === 'single' ? "'" : ($state === 'double' ? '"' : '`');
            if ($char === $close) {
                if ($next === $close) {
                    $buffer .= $next;
                    $i++;
                    continue;
                }
                $state = 'normal';
            }
            continue;
        }
        if (($char === '-' && $next === '-') || $char === '#'
            || ($char === '/' && $next === '/')) {
            $state = 'line_comment';
        } elseif ($char === '/' && $next === '*') {
            $state = 'block_comment';
            $buffer .= '/*';
            $i++;
            continue;
        } elseif ($char === "'") {
            $state = 'single';
        } elseif ($char === '"') {
            $state = 'double';
        } elseif ($char === '`') {
            $state = 'backtick';
        } elseif ($char === ';') {
            if (trim($buffer) !== '') {
                $statements[] = trim($buffer);
            }
            $buffer = '';
            continue;
        }
        $buffer .= $char;
    }
    if (trim($buffer) !== '') {
        $statements[] = trim($buffer);
    }
    return $statements;
}

function setupMigrate(array $cfg): string
{
    $db = Database::fromConfig($cfg['db_migrate'] ?? $cfg['db']);
    $tracker = (int) $db->queryValue('SELECT COUNT(*) FROM information_schema.TABLES'
        . ' WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?', ['schema_migrations']);
    $applied = [];
    if ($tracker === 1) {
        foreach ($db->query('SELECT version, checksum FROM schema_migrations ORDER BY version') as $row) {
            $applied[$row['version']] = $row['checksum'];
        }
    }
    $files = glob(HOST_ROOT . '/migrations/*.sql');
    if (!is_array($files) || $files === []) {
        throw new RuntimeException('migration files unavailable');
    }
    sort($files);
    $pending = [];
    $lines = [];
    // Check every recorded checksum before executing any new DDL.
    foreach ($files as $file) {
        $version = basename($file, '.sql');
        $checksum = hash_file('sha256', $file);
        if (!is_string($checksum)) {
            throw new RuntimeException('migration file unreadable');
        }
        if (isset($applied[$version])) {
            if (!hash_equals($applied[$version], $checksum)) {
                throw new RuntimeException('migration checksum mismatch');
            }
            $lines[] = 'SKIP ' . $version;
        } else {
            $pending[] = ['version' => $version, 'checksum' => $checksum, 'file' => $file];
        }
    }
    if ($applied === [] && (int) $db->queryValue(
        'SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()'
        . ' AND TABLE_NAME<>?', ['schema_migrations']) !== 0) {
        throw new RuntimeException('untracked schema cannot be adopted');
    }
    foreach ($pending as $entry) {
        $source = file_get_contents($entry['file']);
        if (!is_string($source) || setupSplitStatements($source) === []) {
            throw new RuntimeException('migration file unreadable');
        }
        foreach (setupSplitStatements($source) as $statement) {
            $db->pdo()->exec($statement);
        }
        // Migration 001 creates the complete CLI-compatible tracker itself.
        $db->insert('schema_migrations', ['version' => $entry['version'],
            'description' => basename($entry['file']), 'checksum' => $entry['checksum'],
            'applied_at' => gmdate('Y-m-d H:i:s'),
            'applied_by' => substr((string) $db->queryValue('SELECT CURRENT_USER()'), 0, 64)]);
        $lines[] = 'APPLY ' . $entry['version'];
    }
    return implode("\n", $lines);
}

$notice = '';
$output = '';
$csrf = '';
$available = false;
$success = false;
http_response_code(200);
try {
    if (!Config::freshBoolean('operator_bootstrap_enabled', false)) {
        throw new ApiException(404, Codes::NOT_FOUND, 'Setup is closed.');
    }
    $cfg = Config::load();
    $setupKey = $cfg['setup_key'] ?? null;
    if (!is_string($setupKey) || preg_match('/\A[a-f0-9]{64}\z/D', $setupKey) !== 1
        || $setupKey === str_repeat('0', 64)) {
        throw new ApiException(403, Codes::PERMISSION_DENIED, 'Setup is not configured.');
    }
    $remote = is_string($_SERVER['REMOTE_ADDR'] ?? null) ? $_SERVER['REMOTE_ADDR'] : '';
    $https = is_string($_SERVER['HTTPS'] ?? null) ? $_SERVER['HTTPS'] : '';
    $forwarded = is_string($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? null)
        ? $_SERVER['HTTP_X_FORWARDED_PROTO'] : '';
    $proxies = is_array($cfg['trusted_proxies'] ?? null) ? $cfg['trusted_proxies'] : [];
    if (!Request::isSecureTransport($forwarded, $remote, $https, $proxies,
        ($cfg['allow_loopback_http'] ?? false) === true)) {
        throw new ApiException(403, Codes::HTTPS_REQUIRED, 'HTTPS is required.');
    }
    $method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    if (!in_array($method, ['GET', 'POST'], true)) {
        header('Allow: GET, POST');
        throw new ApiException(405, Codes::HTTP_405, 'Use GET or POST.');
    }
    if ($_GET !== [] || ($_SERVER['QUERY_STRING'] ?? '') !== '') {
        throw new ApiException(400, Codes::BAD_PAYLOAD, 'Query parameters are not accepted.');
    }
    if ($method === 'POST') {
        $length = $_SERVER['CONTENT_LENGTH'] ?? '';
        if (!is_scalar($length) || !ctype_digit((string) $length) || (int) $length > 16384) {
            throw new ApiException(413, Codes::PAYLOAD_TOO_LARGE, 'Setup request is too large.');
        }
        $raw = file_get_contents('php://input', false, null, 0, 16385);
        if (!is_string($raw) || strlen($raw) > 16384) {
            throw new ApiException(413, Codes::PAYLOAD_TOO_LARGE, 'Setup request is too large.');
        }
        $contentType = strtolower(trim(explode(';', (string) ($_SERVER['CONTENT_TYPE'] ?? ''))[0]));
        if ($contentType !== 'application/x-www-form-urlencoded') {
            throw new ApiException(415, Codes::UNSUPPORTED_MEDIA_TYPE, 'Use an HTML setup form.');
        }
    }

    // This session carries only a CSRF token and an absolute issue time, never credentials.
    ini_set('session.use_strict_mode', '1');
    ini_set('session.use_only_cookies', '1');
    ini_set('session.use_trans_sid', '0');
    session_name('irica_operator_setup');
    session_cache_limiter('');
    session_set_cookie_params(['lifetime' => 900, 'path' => '/',
        'secure' => Request::isSecureTransport($forwarded, $remote, $https, $proxies),
        'httponly' => true, 'samesite' => 'Strict']);
    if (!session_start()) {
        throw new RuntimeException('setup session unavailable');
    }
    $validSession = is_string($_SESSION['csrf'] ?? null)
        && is_int($_SESSION['issued_at'] ?? null)
        && $_SESSION['issued_at'] <= time() && $_SESSION['issued_at'] > time() - 900;
    if (!$validSession) {
        $_SESSION = ['csrf' => bin2hex(random_bytes(32)), 'issued_at' => time()];
        if (!session_regenerate_id(true)) {
            throw new RuntimeException('setup session renewal failed');
        }
    }
    $csrf = $_SESSION['csrf'];
    $available = true;
    if ($method === 'POST') {
        $postedCsrf = $_POST['csrf'] ?? null;
        $postedKey = $_POST['key'] ?? null;
        if (!$validSession || !is_string($postedCsrf) || strlen($postedCsrf) !== 64
            || !hash_equals($csrf, $postedCsrf) || !is_string($postedKey)
            || strlen($postedKey) !== 64 || !hash_equals($setupKey, $postedKey)) {
            throw new ApiException(403, Codes::PERMISSION_DENIED, 'Setup authorization failed.');
        }
        $action = $_POST['action'] ?? null;
        if (!is_string($action) || !in_array($action, ['migrate', 'create_operator', 'smoke_test'], true)) {
            throw new ApiException(403, Codes::PERMISSION_DENIED, 'Setup action is not permitted.');
        }
        if ($action === 'create_operator') {
            $username = $_POST['username'] ?? null;
            $password = $_POST['password'] ?? null;
            $confirmation = $_POST['password_confirmation'] ?? null;
            if (!is_string($username) || !is_string($password) || !is_string($confirmation)
                || $password !== $confirmation || strlen($password) < 12
                || strlen($password) > 4096 || str_contains($password, "\0")
                || (Auth::passwordAlgo() === PASSWORD_BCRYPT && strlen($password) > 72)) {
                throw new ApiException(400, Codes::BAD_PAYLOAD,
                    'Supply matching passwords of at least 12 bytes within the hash limit.');
            }
            $name = Workspace::canonical($username);
            $hash = Auth::hashPassword($password);
            unset($password, $confirmation, $_POST['password'], $_POST['password_confirmation']);
            OperatorAuth::create(Database::fromConfig($cfg['db']), $name, $hash, true);
            unset($hash);
            $notice = 'Operator created. Sign in to the operator panel with the chosen credentials.';
        } elseif ($action === 'migrate') {
            $output = setupMigrate($cfg);
            $notice = 'Migrations completed.';
        } else {
            $lines = ['PHP ' . PHP_VERSION];
            foreach (['pdo_mysql', 'mbstring', 'json', 'hash', 'openssl', 'sodium'] as $extension) {
                if (!extension_loaded($extension)) {
                    throw new RuntimeException('required extension unavailable');
                }
                $lines[] = 'PASS extension ' . $extension;
            }
            $db = Database::fromConfig($cfg['db']);
            $db->queryValue('SELECT COUNT(*) FROM schema_migrations');
            if ($db->queryOne('SELECT bootstrap_consumed FROM platform_operator_state WHERE id=1') === null) {
                throw new RuntimeException('bootstrap state missing');
            }
            $lines[] = 'PASS application database and private-account schema';
            $output = implode("\n", $lines);
            $notice = 'Health checks completed.';
        }
        $_SESSION['csrf'] = $csrf = bin2hex(random_bytes(32));
        $success = true;
    }
} catch (ApiException $error) {
    http_response_code($error->status);
    $notice = $error->getMessage();
} catch (Throwable $error) {
    http_response_code(503);
    $notice = 'Setup failed. Check configuration and migration integrity through the hosting panel; no credentials are shown here.';
} finally {
    unset($setupKey, $postedKey, $_POST['key'], $_POST['password'], $_POST['password_confirmation']);
    if (session_status() === PHP_SESSION_ACTIVE) {
        session_write_close();
    }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>IRICA · Secure setup</title>
  <link rel="stylesheet" href="setup.css">
</head>
<body>
<main class="setup-shell">
  <header><p class="eyebrow">IRICA / Deployment maintenance</p>
    <h1>Secure setup</h1>
    <p>Install the schema and create the first platform operator. Customer accounts belong in the operator panel.</p>
  </header>
  <?php if ($notice !== ''): ?>
  <div class="notice <?= $success ? 'success' : 'error' ?>" role="<?= $success ? 'status' : 'alert' ?>"><?= setupEscape($notice) ?></div>
  <?php endif; ?>
  <?php if ($output !== ''): ?><pre><?= setupEscape($output) ?></pre><?php endif; ?>
  <?php if ($available): ?>
  <section class="setup-card" aria-labelledby="maintenance-heading">
    <h2 id="maintenance-heading">Maintenance action</h2>
    <p class="hint">Run migrations first, then create the operator. Operator creation is permanently closed after the first success, including host CLI creation.</p>
    <form method="post" action="setup.php">
      <input type="hidden" name="csrf" value="<?= setupEscape($csrf) ?>">
      <label for="key">Setup secret</label>
      <input id="key" name="key" type="password" required maxlength="64" autocomplete="off" aria-describedby="key-hint">
      <p id="key-hint" class="hint">Enter the temporary secret from the hosting configuration. Never put it in a URL.</p>
      <label for="action">Action</label>
      <select id="action" name="action">
        <option value="migrate">1. Run database migrations</option>
        <option value="create_operator">2. Create first platform operator</option>
        <option value="smoke_test">3. Check host health</option>
      </select>
      <fieldset><legend>For operator creation only</legend>
        <label for="username">Operator username</label>
        <input id="username" name="username" autocomplete="username" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}" aria-describedby="username-hint">
        <p id="username-hint" class="hint">3–64 ASCII letters, numbers, dots, underscores or hyphens. Names are case-insensitive.</p>
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="new-password" maxlength="4096" aria-describedby="password-hint">
        <p id="password-hint" class="hint">At least 12 bytes. A bcrypt host accepts at most 72 bytes. Passwords are not trimmed.</p>
        <label for="password-confirmation">Confirm password</label>
        <input id="password-confirmation" name="password_confirmation" type="password" autocomplete="new-password" maxlength="4096">
      </fieldset>
      <button type="submit">Run selected action</button>
    </form>
  </section>
  <aside class="closing-note"><strong>Close the maintenance window when finished.</strong>
    Set <code>operator_bootstrap_enabled</code> to <code>false</code> and remove <code>setup_key</code> in the hosting configuration. Generate a new secret for every future maintenance window.</aside>
  <?php endif; ?>
</main>
</body>
</html>
