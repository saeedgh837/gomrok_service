<?php
/**
 * host_v2_front.php — the disposable fixture adapter for the `/v2` routes.
 *
 * Contract: lab/tests/test_host_v2_api.py and execution rulings 43/44. It is
 * started ONLY as
 *
 *     php -S 127.0.0.1:8766 lab/fixtures/host_v2_front.php
 *
 * and it refuses to be anything else. It is not a web application: it is the
 * smallest possible front that can drive the real `V2Api` over a loopback
 * socket, so the HTTP contract can be proved without touching a host config.
 *
 * What it deliberately NEVER loads: `Config::load()`, `host/config/config.php`,
 * `host/public/setup.php`, the Telegram relay, the installed panel or any live
 * host configuration. Its database connection comes from the already-validated
 * fixture file/marker/runtime triple in bootstrap.php, and nothing else.
 *
 * Guards, in order, all fail-closed:
 *   1. `PHP_SAPI === 'cli-server'` — never a CGI, FPM or CLI body;
 *   2. an EXACT loopback peer (`127.0.0.1` or `::1`), not 127/8;
 *   3. a version path, classified before the request is captured;
 *   4. `V2Api::assertTransport(..., fixture: true)` — the only call in this
 *      package that may pass `fixture=true`, and only for that exact peer.
 *
 * Production `host/public/index.php` always passes `fixture=false`.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
ini_set('html_errors', '0');
error_reporting(E_ALL);

if (PHP_SAPI !== 'cli-server') {
    // Not a diagnostic page: there is nothing to say to a non-loopback caller.
    http_response_code(500);
    exit;
}

$peer = isset($_SERVER['REMOTE_ADDR']) && is_string($_SERVER['REMOTE_ADDR'])
    ? $_SERVER['REMOTE_ADDR'] : '';
if ($peer !== '127.0.0.1' && $peer !== '::1') {
    http_response_code(403);
    exit;
}

define('HOST_ROOT', dirname(__DIR__, 2) . '/host');

foreach (['Schema', 'Database', 'Http', 'Ids', 'Log', 'Tokens', 'Roles',
    'RateLimit', 'Auth', 'Accounts', 'Workspaces', 'AccountSessions',
    'V2Api'] as $classFile) {
    require_once HOST_ROOT . '/src/' . $classFile . '.php';
}

// The approved fixture file/marker/runtime connection, and nothing else.
require_once __DIR__ . '/host_v2/bootstrap.php';

$started = microtime(true);
$statusCode = 500;
$errorCode = Codes::SERVER_ERROR;
$routeLabel = 'unrouted';
$request = null;
$log = new Log(v2FixtureLogPath());

try {
    // Classified BEFORE the request is captured, exactly as index.php does it.
    $rawUri = isset($_SERVER['REQUEST_URI']) && is_string($_SERVER['REQUEST_URI'])
        ? $_SERVER['REQUEST_URI'] : '/';
    $routeLabel = V2Api::logLabel(
        isset($_SERVER['REQUEST_METHOD']) && is_string($_SERVER['REQUEST_METHOD'])
            ? strtoupper($_SERVER['REQUEST_METHOD']) : 'GET', $rawUri);

    if (!V2Api::isVersion2Request($rawUri)) {
        // This adapter serves /v2 and nothing else.
        throw new ApiException(404, Codes::HTTP_404, 'no such endpoint');
    }

    $db = BFixture::db();
    $request = Request::capture($_SERVER, $_GET, V2Api::MAX_BODY_BYTES);
    V2Api::assertTransport($request->remoteAddr, false, true);

    $result = (new V2Api($db, $log, []))->dispatch($request, time());
    $statusCode = $result['status'];
    $errorCode = v2ErrorCodeOf($result);
    Response::emit($result['status'], $result['body'], $result['headers']);
} catch (ApiException $error) {
    $statusCode = $error->status;
    $errorCode = $error->errorCode;
    Response::fail($error, v2RequestId($request));
} catch (\Throwable $error) {
    $statusCode = 500;
    $errorCode = Codes::SERVER_ERROR;
    Response::fail(new ApiException(500, Codes::SERVER_ERROR,
        'internal server error'), v2RequestId($request));
    $log->line(['status' => 500, 'code' => $errorCode, 'level' => 'error']
        + Log::describe($error));
} finally {
    // ONE request line, and only ever a known route: never user input.
    $log->line([
        'request_id' => v2RequestId($request),
        'method' => $routeLabel === 'unrouted' ? ''
            : explode(' ', $routeLabel)[0],
        'route' => $routeLabel,
        'status' => $statusCode,
        'duration_ms' => (int) round((microtime(true) - $started) * 1000),
        'code' => $errorCode,
    ]);
}

/** The request id a failure envelope should carry. */
function v2RequestId(?Request $request): string
{
    return $request instanceof Request ? $request->requestId : Ids::v4();
}

/** The error code of a dispatch result, or '' for a success. */
function v2ErrorCodeOf(array $result): string
{
    return isset($result['body']['error']['code'])
        && is_string($result['body']['error']['code'])
        ? $result['body']['error']['code'] : '';
}

/**
 * The one log file the api suite asserts against. Fixture-only, never a
 * product path, and never derived from anything a caller can influence.
 */
function v2FixtureLogPath(): string
{
    $override = getenv('IRICA_V2_LOG');
    if (is_string($override) && trim($override) !== '') {
        return trim($override);
    }
    return sys_get_temp_dir() . '/irica-v2-fixture-access.log';
}
