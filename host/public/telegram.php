<?php
/**
 * telegram.php — Secure Telegram Bot API relay for shared hosting.
 *
 * Forwards requests from local IRICA panels to https://api.telegram.org
 * allowing Iranian desktop clients to send messages, reports, receipts, and photos
 * without requiring a client-side VPN or proxy.
 *
 * Security controls:
 * 1. display_errors disabled — errors never leak internals.
 * 2. Optional shared secret verification (config: telegram_relay_secret / X-Relay-Secret header).
 * 3. Strict regex validation for bot token format and Telegram API method name.
 * 4. cURL with strict SSL verification, connect and read timeouts.
 * 5. Supports multipart file uploads (sendPhoto / sendDocument) using CURLFile.
 * 6. Health check: GET /telegram.php returns 200 {"ok": true, "service": "irica-telegram-relay"}.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
ini_set('html_errors', '0');
error_reporting(E_ALL);

header('Content-Type: application/json; charset=utf-8');
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: no-referrer');
header('Cache-Control: no-store');

// ---------------------------------------------------------------------------
// 1. Health check (GET with no method or ping)
// ---------------------------------------------------------------------------
$httpMethod = $_SERVER['REQUEST_METHOD'] ?? 'GET';
if ($httpMethod === 'GET' && empty($_GET['method']) && empty($_SERVER['PATH_INFO'])) {
    http_response_code(200);
    echo json_encode([
        'ok' => true,
        'service' => 'irica-telegram-relay',
        'status' => 'operational',
        'file_uploads' => (bool) ini_get('file_uploads'),
        'upload_max_filesize' => ini_get('upload_max_filesize'),
        'post_max_size' => ini_get('post_max_size'),
        'temp_dir' => sys_get_temp_dir(),
        'open_basedir' => ini_get('open_basedir') ?: 'none',
        'dir_writable' => is_writable(__DIR__),
        'parent_writable' => is_writable(dirname(__DIR__)),
        'time' => gmdate('Y-m-d\TH:i:s\Z'),
    ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

if ($httpMethod !== 'POST') {
    http_response_code(405);
    echo json_encode([
        'ok' => false,
        'error_code' => 405,
        'description' => 'Method Not Allowed: only POST is supported for Telegram actions',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// ---------------------------------------------------------------------------
// 2. Secret validation (optional host-side secret)
// ---------------------------------------------------------------------------
$hostRoot = dirname(__DIR__);
$configFile = $hostRoot . '/config/config.php';
$expectedSecret = '';
if (file_exists($configFile)) {
    try {
        $cfg = require $configFile;
        if (is_array($cfg) && !empty($cfg['telegram_relay_secret']) && is_string($cfg['telegram_relay_secret'])) {
            $expectedSecret = trim($cfg['telegram_relay_secret']);
        }
    } catch (\Throwable $e) {
        // ignore config load errors
    }
}
if ($expectedSecret === '') {
    $envSecret = getenv('TELEGRAM_RELAY_SECRET');
    if (is_string($envSecret) && trim($envSecret) !== '') {
        $expectedSecret = trim($envSecret);
    }
}

if ($expectedSecret !== '') {
    $givenSecret = $_SERVER['HTTP_X_RELAY_SECRET']
        ?? $_POST['relay_secret']
        ?? $_POST['secret']
        ?? $_GET['secret']
        ?? '';
    if (!is_string($givenSecret) || !hash_equals($expectedSecret, trim($givenSecret))) {
        http_response_code(403);
        echo json_encode([
            'ok' => false,
            'error_code' => 403,
            'description' => 'Forbidden: invalid or missing relay secret',
        ], JSON_UNESCAPED_UNICODE);
        exit;
    }
}

// ---------------------------------------------------------------------------
// 3. Extract bot token and method
// ---------------------------------------------------------------------------
$botToken = '';
$tgMethod = '';

// Check PATH_INFO e.g. /bot<token>/<method> or /<method>
$pathInfo = $_SERVER['PATH_INFO'] ?? '';
if ($pathInfo !== '') {
    $pathInfo = trim($pathInfo, '/');
    if (preg_match('#^bot([0-9]+:[A-Za-z0-9_-]+)/([A-Za-z0-9_]+)$#', $pathInfo, $m)) {
        $botToken = $m[1];
        $tgMethod = $m[2];
    } elseif (preg_match('#^([A-Za-z0-9_]+)$#', $pathInfo, $m)) {
        $tgMethod = $m[1];
    }
}

if ($botToken === '') {
    $rawToken = $_SERVER['HTTP_X_TELEGRAM_BOT_TOKEN']
        ?? $_POST['token']
        ?? $_POST['bot_token']
        ?? $_GET['token']
        ?? '';
    if (is_string($rawToken)) {
        $botToken = trim($rawToken);
    }
}

if ($tgMethod === '') {
    $rawMethod = $_POST['method'] ?? $_GET['method'] ?? '';
    if (is_string($rawMethod)) {
        $tgMethod = trim($rawMethod);
    }
}

// Validate token format: numeric user ID + colon + base64/url token
if (!preg_match('/^[0-9]+:[A-Za-z0-9_-]{20,64}$/', $botToken)) {
    http_response_code(400);
    echo json_encode([
        'ok' => false,
        'error_code' => 400,
        'description' => 'Bad Request: invalid Telegram bot token format',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// Validate method format (e.g. sendMessage, sendPhoto, sendDocument, getMe)
if (!preg_match('/^[A-Za-z0-9_]{1,64}$/', $tgMethod)) {
    http_response_code(400);
    echo json_encode([
        'ok' => false,
        'error_code' => 400,
        'description' => 'Bad Request: invalid Telegram method name',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// ---------------------------------------------------------------------------
// 4. Prepare cURL request to https://api.telegram.org
// ---------------------------------------------------------------------------
$telegramUrl = 'https://api.telegram.org/bot' . $botToken . '/' . $tgMethod;
$ch = curl_init($telegramUrl);
if ($ch === false) {
    http_response_code(500);
    echo json_encode([
        'ok' => false,
        'error_code' => 500,
        'description' => 'Server Error: unable to initialize cURL',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

$postFields = [];
$isMultipart = !empty($_FILES);
$tempFilesToDelete = [];

// Helper to get safe upload directory inside project or sys temp
$safeTempDir = function () use ($hostRoot): string {
    $candidates = [
        __DIR__,
        $hostRoot . '/var/tmp',
        $hostRoot . '/var',
        sys_get_temp_dir(),
    ];
    foreach ($candidates as $dir) {
        if (is_dir($dir) && is_writable($dir)) {
            return $dir;
        }
        if (!is_dir($dir)) {
            @mkdir($dir, 0750, true);
            if (is_dir($dir) && is_writable($dir)) {
                return $dir;
            }
        }
    }
    return sys_get_temp_dir();
};

if ($isMultipart) {
    // Copy POST fields, excluding internal relay params
    foreach ($_POST as $k => $v) {
        if ($k !== 'token' && $k !== 'bot_token' && $k !== 'method' && $k !== 'secret' && $k !== 'relay_secret') {
            $postFields[$k] = $v;
        }
    }
    // Attach files as CURLFile (move to local temp if needed for open_basedir safety)
    $tmpDir = $safeTempDir();
    foreach ($_FILES as $fieldName => $file) {
        if (is_array($file['error'])) {
            continue; // nested file arrays not used
        }
        if ($file['error'] === UPLOAD_ERR_OK && is_uploaded_file($file['tmp_name'])) {
            $mime = !empty($file['type']) ? $file['type'] : 'application/octet-stream';
            $safeName = preg_replace('/[^A-Za-z0-9_\-\.]/', '_', (string) ($file['name'] ?? 'upload.bin'));
            $localTarget = $tmpDir . '/tg_up_' . bin2hex(random_bytes(6)) . '_' . $safeName;
            if (@move_uploaded_file($file['tmp_name'], $localTarget)) {
                $tempFilesToDelete[] = $localTarget;
                $postFields[$fieldName] = new \CURLFile($localTarget, $mime, $safeName);
            } else {
                $postFields[$fieldName] = new \CURLFile($file['tmp_name'], $mime, $safeName);
            }
        }
    }
} elseif (!empty($_POST)) {
    foreach ($_POST as $k => $v) {
        if ($k !== 'token' && $k !== 'bot_token' && $k !== 'method' && $k !== 'secret' && $k !== 'relay_secret') {
            $postFields[$k] = $v;
        }
    }
} else {
    // Check raw body (JSON)
    $rawInput = file_get_contents('php://input');
    if ($rawInput !== false && trim($rawInput) !== '') {
        $decoded = json_decode($rawInput, true);
        if (is_array($decoded)) {
            unset($decoded['token'], $decoded['bot_token'], $decoded['method'], $decoded['secret'], $decoded['relay_secret']);
            $postFields = $decoded;
        }
    }
}

// Support base64 encoded photo upload for environments where multipart temp files fail
if (!empty($postFields['photo_base64'])) {
    $rawPhoto = base64_decode((string) $postFields['photo_base64'], true);
    unset($postFields['photo_base64']);
    if ($rawPhoto !== false && strlen($rawPhoto) > 0) {
        $tmpDir = $safeTempDir();
        $targetPath = $tmpDir . '/tg_photo_' . bin2hex(random_bytes(6)) . '.jpg';
        if (@file_put_contents($targetPath, $rawPhoto) !== false) {
            $tempFilesToDelete[] = $targetPath;
            $postFields['photo'] = new \CURLFile($targetPath, 'image/jpeg', 'receipt.jpg');
            $isMultipart = true;
        }
    }
}

// Support base64 encoded document upload
if (!empty($postFields['document_base64'])) {
    $rawDoc = base64_decode((string) $postFields['document_base64'], true);
    $docName = preg_replace('/[^A-Za-z0-9_\-\.]/', '_', (string) ($postFields['document_name'] ?? 'document.bin'));
    unset($postFields['document_base64'], $postFields['document_name']);
    if ($rawDoc !== false && strlen($rawDoc) > 0) {
        $tmpDir = $safeTempDir();
        $targetPath = $tmpDir . '/tg_doc_' . bin2hex(random_bytes(6)) . '_' . $docName;
        if (@file_put_contents($targetPath, $rawDoc) !== false) {
            $tempFilesToDelete[] = $targetPath;
            $postFields['document'] = new \CURLFile($targetPath, 'application/octet-stream', $docName);
            $isMultipart = true;
        }
    }
}

$curlHeaders = [
    'Accept: application/json',
    'User-Agent: IRICA-Telegram-Relay/1.0',
];

curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_CONNECTTIMEOUT => 10,
    CURLOPT_TIMEOUT => 90,
    CURLOPT_SSL_VERIFYPEER => true,
    CURLOPT_SSL_VERIFYHOST => 2,
]);

if (!$isMultipart && !empty($postFields)) {
    // Send as JSON for clean structured requests
    $curlHeaders[] = 'Content-Type: application/json';
    curl_setopt($ch, CURLOPT_HTTPHEADER, $curlHeaders);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'POST');
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($postFields, JSON_UNESCAPED_UNICODE));
} elseif (!empty($postFields)) {
    // cURL handles multipart boundaries automatically when given an array with CURLFile
    curl_setopt($ch, CURLOPT_HTTPHEADER, $curlHeaders);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $postFields);
} else {
    curl_setopt($ch, CURLOPT_HTTPHEADER, $curlHeaders);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'POST');
    curl_setopt($ch, CURLOPT_POSTFIELDS, '');
}

try {
    $response = curl_exec($ch);
    $httpStatus = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $curlErrno = curl_errno($ch);
    $curlError = curl_error($ch);
} finally {
    curl_close($ch);
    foreach ($tempFilesToDelete as $tmpFile) {
        if (file_exists($tmpFile)) {
            @unlink($tmpFile);
        }
    }
}

if ($curlErrno !== 0 || $response === false) {
    http_response_code(502);
    echo json_encode([
        'ok' => false,
        'error_code' => 502,
        'description' => 'Bad Gateway: cURL network error contacting Telegram: ' . $curlError,
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

http_response_code($httpStatus > 0 ? $httpStatus : 200);
echo $response;
