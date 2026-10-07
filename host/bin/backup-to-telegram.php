<?php
/**
 * backup-to-telegram.php — Automated 4-hour database backup to Telegram.
 *
 * Dumps all IRICA cloud database tables, compresses with gzip, and sends
 * the .sql.gz archive as a document to the operator's Telegram channel/chat.
 *
 * Can be run via:
 *   1. cPanel CronJob every 4 hours:
 *      0 *\/4 * * * /usr/local/bin/php /home/USER/host/bin/backup-to-telegram.php >> /home/USER/host/var/log/backup.log 2>&1
 *   2. CLI manual execution:
 *      php host/bin/backup-to-telegram.php [--dry-run]
 *   3. HTTP API (POST /v1/admin/backup) by authenticated admin in CRM panel.
 */

declare(strict_types=1);

require_once __DIR__ . '/../src/Schema.php';
require_once __DIR__ . '/../src/Config.php';
require_once __DIR__ . '/../src/Database.php';

/**
 * Perform full MySQL database dump and deliver via Telegram.
 *
 * @param Database|null $db
 * @param array<string, mixed>|null $config
 * @param array<string, mixed> $options
 * @return array<string, mixed>
 */
function run_database_backup(?Database $db = null, ?array $config = null, array $options = []): array
{
    $hostRoot = dirname(__DIR__);
    if ($config === null) {
        $configFile = $hostRoot . '/config/config.php';
        if (!file_exists($configFile)) {
            throw new \RuntimeException('config.php not found at ' . $configFile);
        }
        $config = require $configFile;
    }

    if ($db === null) {
        $dbConfig = $config['db'] ?? [];
        $db = Database::fromConfig($dbConfig);
    }

    $pdo = $db->pdo();
    $nowUtc = gmdate('Y-m-d H:i:s');
    $dateSlug = gmdate('Ymd_His');
    $dbName = $config['db']['dbname'] ?? 'irica_cloud';

    // -----------------------------------------------------------------------
    // 1. Generate full SQL dump via PDO
    // -----------------------------------------------------------------------
    $tables = Schema::tables();
    $sqlParts = [];
    $sqlParts[] = "-- ============================================================";
    $sqlParts[] = "-- IRICA Cloud CRM Database Backup";
    $sqlParts[] = "-- Date (UTC): " . $nowUtc;
    $sqlParts[] = "-- Database: " . $dbName;
    $sqlParts[] = "-- ============================================================";
    $sqlParts[] = "";
    $sqlParts[] = "SET FOREIGN_KEY_CHECKS = 0;";
    $sqlParts[] = "SET SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO';";
    $sqlParts[] = "SET NAMES utf8mb4;";
    $sqlParts[] = "";

    $totalRows = 0;
    $dumpedTables = 0;

    foreach ($tables as $table) {
        try {
            // Check if table exists in DB
            $check = $pdo->prepare("SHOW TABLES LIKE ?");
            $check->execute([$table]);
            if (!$check->fetch()) {
                continue;
            }

            $sqlParts[] = "-- ------------------------------------------------------------";
            $sqlParts[] = "-- Table structure for `{$table}`";
            $sqlParts[] = "-- ------------------------------------------------------------";
            $sqlParts[] = "DROP TABLE IF EXISTS `{$table}`;";

            $createStmt = $pdo->query("SHOW CREATE TABLE `{$table}`");
            $createRow = $createStmt->fetch(\PDO::FETCH_NUM);
            if ($createRow && isset($createRow[1])) {
                $sqlParts[] = $createRow[1] . ";";
            }
            $sqlParts[] = "";

            // Dump rows
            $dataStmt = $pdo->query("SELECT * FROM `{$table}`");
            $rows = $dataStmt->fetchAll(\PDO::FETCH_ASSOC);
            $rowCount = count($rows);
            $totalRows += $rowCount;
            $dumpedTables++;

            if ($rowCount > 0) {
                $sqlParts[] = "-- Data for table `{$table}` ({$rowCount} rows)";
                $cols = array_keys($rows[0]);
                $quotedCols = array_map(fn($c) => "`{$c}`", $cols);
                $colsList = implode(', ', $quotedCols);

                // Chunk inserts in batches of 100 for safety and speed
                $chunks = array_chunk($rows, 100);
                foreach ($chunks as $chunk) {
                    $valSets = [];
                    foreach ($chunk as $row) {
                        $escapedVals = [];
                        foreach ($cols as $col) {
                            $val = $row[$col];
                            if ($val === null) {
                                $escapedVals[] = "NULL";
                            } elseif (is_int($val) || is_float($val)) {
                                $escapedVals[] = (string) $val;
                            } else {
                                $escapedVals[] = $pdo->quote((string) $val);
                            }
                        }
                        $valSets[] = "(" . implode(', ', $escapedVals) . ")";
                    }
                    $sqlParts[] = "INSERT INTO `{$table}` ({$colsList}) VALUES\n  " . implode(",\n  ", $valSets) . ";";
                }
                $sqlParts[] = "";
            }
        } catch (\Throwable $e) {
            $sqlParts[] = "-- [WARNING] Failed to dump table `{$table}`: " . $e->getMessage();
        }
    }

    $sqlParts[] = "SET FOREIGN_KEY_CHECKS = 1;";
    $sqlParts[] = "-- Backup completed successfully at " . gmdate('Y-m-d H:i:s') . " UTC.";

    $fullSql = implode("\n", $sqlParts);
    $uncompressedBytes = strlen($fullSql);
    $gzippedSql = gzencode($fullSql, 9);
    if ($gzippedSql === false) {
        throw new \RuntimeException('Failed to gzip database dump');
    }
    $compressedBytes = strlen($gzippedSql);
    $sha256 = hash('sha256', $gzippedSql);

    // Save temporary file
    $tmpDir = $hostRoot . '/var/tmp';
    if (!is_dir($tmpDir)) {
        @mkdir($tmpDir, 0750, true);
    }
    if (!is_writable($tmpDir)) {
        $tmpDir = sys_get_temp_dir();
    }
    $fileName = sprintf('irica_db_backup_%s_%s.sql.gz', $dbName, $dateSlug);
    $filePath = rtrim($tmpDir, '/\\') . DIRECTORY_SEPARATOR . $fileName;

    if (file_put_contents($filePath, $gzippedSql) === false) {
        throw new \RuntimeException('Failed to write backup archive to ' . $filePath);
    }

    $dryRun = !empty($options['dry_run']);
    $telegramResult = null;

    if (!$dryRun) {
        // -------------------------------------------------------------------
        // 2. Resolve Telegram Credentials & Send via Bot API
        // -------------------------------------------------------------------
        $botToken = $options['bot_token']
            ?? $config['backup']['bot_token']
            ?? $config['telegram']['bot_token']
            ?? getenv('TELEGRAM_BOT_TOKEN')
            ?? '';

        $chatId = $options['chat_id']
            ?? $config['backup']['chat_id']
            ?? $config['telegram']['chat_id']
            ?? getenv('TELEGRAM_CHAT_ID')
            ?? '';

        // If chat ID not in config, check settings table in DB
        if (empty($chatId)) {
            try {
                $settingRow = $pdo->query("SELECT report_chat_id, registration_chat_id FROM settings LIMIT 1")->fetch();
                if ($settingRow) {
                    $chatId = !empty($settingRow['report_chat_id'])
                        ? $settingRow['report_chat_id']
                        : ($settingRow['registration_chat_id'] ?? '');
                }
            } catch (\Throwable $e) {
                // Ignore DB lookup error
            }
        }

        if (empty($botToken) || empty($chatId)) {
            @unlink($filePath);
            throw new \RuntimeException('Telegram bot_token or chat_id is missing; cannot deliver backup');
        }

        $sizeKb = round($compressedBytes / 1024, 1);
        $caption = "💾 پشتیبان خودکار پایگاه داده ابری IRICA\n"
            . "📅 زمان (UTC): " . $nowUtc . "\n"
            . "📦 جداول: " . $dumpedTables . " جدول | کل ردیف‌ها: " . number_format($totalRows) . "\n"
            . "📊 حجم: " . $sizeKb . " KB (فشرده)\n"
            . "🔒 SHA-256: " . substr($sha256, 0, 16) . "...";

        $url = "https://api.telegram.org/bot" . trim($botToken) . "/sendDocument";
        $ch = curl_init($url);
        if ($ch === false) {
            @unlink($filePath);
            throw new \RuntimeException('curl_init failed for Telegram API');
        }

        $postFields = [
            'chat_id' => trim((string) $chatId),
            'caption' => $caption,
            'document' => new \CURLFile($filePath, 'application/gzip', $fileName),
        ];

        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $postFields,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 60,
            CURLOPT_CONNECTTIMEOUT => 15,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
        ]);

        $response = curl_exec($ch);
        $curlErr = curl_error($ch);
        $httpCode = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        @unlink($filePath); // Always clean up temporary file

        if ($response === false || $httpCode < 200 || $httpCode >= 300) {
            throw new \RuntimeException(sprintf(
                'Telegram delivery failed (HTTP %d): %s %s',
                $httpCode,
                $curlErr,
                is_string($response) ? substr($response, 0, 200) : ''
            ));
        }

        $decoded = json_decode((string) $response, true);
        $telegramResult = [
            'http_code' => $httpCode,
            'ok' => is_array($decoded) && !empty($decoded['ok']),
            'message_id' => $decoded['result']['message_id'] ?? null,
        ];
    } else {
        @unlink($filePath);
    }

    return [
        'ok' => true,
        'timestamp' => $nowUtc,
        'database' => $dbName,
        'tables_dumped' => $dumpedTables,
        'total_rows' => $totalRows,
        'uncompressed_bytes' => $uncompressedBytes,
        'compressed_bytes' => $compressedBytes,
        'sha256' => $sha256,
        'filename' => $fileName,
        'telegram' => $telegramResult,
        'dry_run' => $dryRun,
    ];
}

// ---------------------------------------------------------------------------
// CLI Execution Handler
// ---------------------------------------------------------------------------
if (php_sapi_name() === 'cli' && realpath($_SERVER['SCRIPT_FILENAME'] ?? '') === realpath(__FILE__)) {
    $lockFile = dirname(__DIR__) . '/var/tmp/backup.lock';
    $lockDir = dirname($lockFile);
    if (!is_dir($lockDir)) {
        @mkdir($lockDir, 0750, true);
    }

    $lockHandle = @fopen($lockFile, 'c');
    if (!$lockHandle || !flock($lockHandle, LOCK_EX | LOCK_NB)) {
        echo "[" . gmdate('Y-m-d H:i:s') . "] Another backup process is already running. Exiting.\n";
        exit(0);
    }

    $options = [];
    foreach ($argv as $arg) {
        if ($arg === '--dry-run') {
            $options['dry_run'] = true;
        } elseif (str_starts_with($arg, '--token=')) {
            $options['bot_token'] = substr($arg, 8);
        } elseif (str_starts_with($arg, '--chat_id=')) {
            $options['chat_id'] = substr($arg, 10);
        }
    }

    try {
        echo "[" . gmdate('Y-m-d H:i:s') . "] Starting 4-hour IRICA database backup...\n";
        $res = run_database_backup(null, null, $options);
        echo "[" . gmdate('Y-m-d H:i:s') . "] Backup SUCCESS: "
            . $res['tables_dumped'] . " tables, "
            . $res['total_rows'] . " rows, "
            . round($res['compressed_bytes'] / 1024, 1) . " KB.\n";
        if (!empty($res['telegram']['ok'])) {
            echo "[" . gmdate('Y-m-d H:i:s') . "] Delivered to Telegram message_id: " . $res['telegram']['message_id'] . "\n";
        }
    } catch (\Throwable $e) {
        echo "[" . gmdate('Y-m-d H:i:s') . "] Backup FAILED: " . $e->getMessage() . "\n";
        exit(1);
    } finally {
        flock($lockHandle, LOCK_UN);
        fclose($lockHandle);
    }
}
