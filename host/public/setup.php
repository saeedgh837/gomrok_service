<?php
/**
 * setup.php — Web installer for shared hosting environments without SSH access.
 *
 * Security requirements:
 * 1. Must be protected by `setup_key` in config/config.php and ?key= query parameter.
 * 2. Compares key using constant-time hash_equals.
 * 3. Can run migrations, create the admin user, create colleague users, and run smoke tests.
 * 4. Provides a one-click button to delete itself (unlink) when setup is complete.
 */

declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
error_reporting(E_ALL);

define('HOST_ROOT', dirname(__DIR__));

foreach ([
    'Schema', 'Database', 'Http', 'Ids', 'Config', 'Log', 'Tokens', 'Roles',
    'RateLimit', 'Auth', 'Sync', 'Import',
] as $classFile) {
    require_once HOST_ROOT . '/src/' . $classFile . '.php';
}

$cfg = null;
$configError = '';
try {
    $cfg = Config::load();
} catch (Throwable $e) {
    $configError = $e->getMessage();
}

$setupKey = (is_array($cfg) && isset($cfg['setup_key']) && is_string($cfg['setup_key']))
    ? trim($cfg['setup_key'])
    : '';

$reqKey = (string) ($_GET['key'] ?? ($_POST['key'] ?? ''));

function h(string $text): string
{
    return htmlspecialchars($text, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// Split SQL file into statements (same logic as migrate.php)
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
                if ($next === $close && $state !== 'backtick') {
                    $buffer .= $next;
                    $i++;
                    continue;
                }
                if ($state === 'backtick' && $next === '`') {
                    $buffer .= $next;
                    $i++;
                    continue;
                }
                $state = 'normal';
            }
            $buffer .= $char;
            continue;
        }

        if ($char === '-' && $next === '-') {
            $state = 'line_comment';
            $buffer .= $char;
            continue;
        }
        if ($char === '#' || ($char === '/' && $next === '/')) {
            $state = 'line_comment';
            $buffer .= $char;
            continue;
        }
        if ($char === '/' && $next === '*') {
            $state = 'block_comment';
            $buffer .= '/*';
            $i++;
            continue;
        }
        if ($char === "'") {
            $state = 'single';
            $buffer .= $char;
            continue;
        }
        if ($char === '"') {
            $state = 'double';
            $buffer .= $char;
            continue;
        }
        if ($char === '`') {
            $state = 'backtick';
            $buffer .= $char;
            continue;
        }

        if ($char === ';') {
            $trimmed = trim($buffer);
            if ($trimmed !== '') {
                $statements[] = $trimmed;
            }
            $buffer = '';
            continue;
        }

        $buffer .= $char;
    }
    $trimmed = trim($buffer);
    if ($trimmed !== '') {
        $statements[] = $trimmed;
    }
    return $statements;
}

$notice = '';
$noticeType = 'info';
$output = '';

if ($configError !== '') {
    $notice = 'خطا در بارگذاری تنظیمات: ' . $configError;
    $noticeType = 'error';
} elseif ($setupKey === '') {
    $notice = 'کلید setup_key در فایل config/config.php تنظیم نشده است. لطفاً ابتدا در config.php یک مقدار تصادفی برای setup_key مشخص کنید.';
    $noticeType = 'error';
} elseif (!hash_equals($setupKey, $reqKey)) {
    http_response_code(403);
    $notice = 'دسترسی غیرمجاز. کلید امنیتی (key) نادرست است.';
    $noticeType = 'error';
} else {
    // Key is valid - handle actions
    $action = (string) ($_POST['action'] ?? '');
    if ($action === 'migrate') {
        try {
            $dbConfig = $cfg['db_migrate'] ?? $cfg['db'];
            $db = Database::fromConfig($dbConfig);
            $db->pdo()->exec(
                'CREATE TABLE IF NOT EXISTS schema_migrations ('
                . '  version     VARCHAR(128) NOT NULL,'
                . '  checksum    VARCHAR(64)  NOT NULL,'
                . '  applied_at  DATETIME     NOT NULL,'
                . '  PRIMARY KEY (version)'
                . ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;'
            );

            $appliedRows = $db->query('SELECT version, checksum FROM schema_migrations ORDER BY version');
            $applied = [];
            foreach ($appliedRows as $row) {
                $applied[(string) $row['version']] = (string) $row['checksum'];
            }

            $migDir = HOST_ROOT . '/migrations';
            $files = glob($migDir . '/*.sql');
            sort($files);

            $log = [];
            foreach ($files as $file) {
                $version = basename($file, '.sql');
                $checksum = hash_file('sha256', $file);
                if (isset($applied[$version])) {
                    if ($applied[$version] !== $checksum) {
                        throw new RuntimeException("Migration checksum mismatch for {$version}");
                    }
                    $log[] = "SKIP {$version} (already applied)";
                    continue;
                }

                $stmts = setupSplitStatements((string) file_get_contents($file));
                foreach ($stmts as $stmt) {
                    $db->pdo()->exec($stmt);
                }
                $now = gmdate('Y-m-d H:i:s');
                $db->insert('schema_migrations', [
                    'version' => $version,
                    'checksum' => $checksum,
                    'applied_at' => $now,
                ]);
                $log[] = "APPLY {$version} (success, " . count($stmts) . " statements)";
            }
            $notice = 'مایگریشن‌ها با موفقیت اجرا شدند.';
            $noticeType = 'success';
            $output = implode("\n", $log);
        } catch (Throwable $e) {
            $notice = 'خطا در اجرای مایگریشن: ' . $e->getMessage();
            $noticeType = 'error';
        }
    } elseif ($action === 'create_admin') {
        try {
            $db = Database::fromConfig($cfg['db']);
            $username = trim((string) ($_POST['username'] ?? 'admin'));
            if ($username === '') {
                throw new RuntimeException('نام کاربری نمی‌تواند خالی باشد.');
            }
            $tenantSlug = trim((string) ($_POST['tenant_slug'] ?? ($cfg['tenant_slug'] ?? 'default')));
            $displayName = trim((string) ($_POST['display_name'] ?? 'مدیر سامانه'));
            $password = trim((string) ($_POST['password'] ?? ''));

            $generated = false;
            if ($password === '') {
                $password = rtrim(strtr(base64_encode(random_bytes(18)), '+/', '-_'), '=');
                $generated = true;
            }

            $now = gmdate('Y-m-d H:i:s');
            $tenant = $db->queryOne('SELECT id FROM tenants WHERE slug = ?', [$tenantSlug]);
            if ($tenant === null) {
                $tenantId = Ids::v4();
                $db->insert('tenants', [
                    'id' => $tenantId, 'slug' => $tenantSlug, 'name' => 'IRICA',
                    'status' => 'active', 'created_at' => $now, 'updated_at' => $now,
                ]);
            } else {
                $tenantId = (string) $tenant['id'];
            }

            $existing = $db->queryOne(
                'SELECT u.id FROM memberships m JOIN users u ON u.id = m.user_id WHERE m.tenant_id = ? AND m.role = ? LIMIT 1',
                [$tenantId, Roles::ADMIN]
            );
            if ($existing !== null) {
                throw new RuntimeException('حساب مدیر در این مستأجر از قبل وجود دارد. ایجاد مجدد مدیر اصلی مجاز نیست.');
            }

            $dupUser = $db->queryOne('SELECT id FROM users WHERE tenant_id = ? AND username = ?', [$tenantId, $username]);
            if ($dupUser !== null) {
                throw new RuntimeException("کاربری با نام {$username} قبلاً ثبت شده است.");
            }

            $hash = Auth::hashPassword($password);
            $userId = Ids::v4();
            $db->transaction(function (Database $tx) use ($userId, $tenantId, $username, $displayName, $hash, $now): void {
                $tx->insert('users', [
                    'id' => $userId, 'tenant_id' => $tenantId, 'username' => $username,
                    'display_name' => $displayName, 'password_hash' => $hash,
                    'status' => 'active', 'token_version' => 1, 'must_change_password' => 1,
                    'created_at' => $now, 'updated_at' => $now,
                ]);
                $tx->insert('memberships', [
                    'id' => Ids::v4(), 'tenant_id' => $tenantId, 'user_id' => $userId,
                    'role' => Roles::ADMIN, 'created_at' => $now, 'updated_at' => $now,
                ]);
            });

            $notice = "حساب مدیر '{$username}' با موفقیت ساخته شد.";
            $noticeType = 'success';
            $output = "نام کاربری: " . $username . "\nگذرواژه: " . $password . ($generated ? " (تولید تصادفی)" : "") . "\n\nحتماً این اطلاعات را یادداشت و ذخیره کنید.";
        } catch (Throwable $e) {
            $notice = 'خطا در ساخت مدیر: ' . $e->getMessage();
            $noticeType = 'error';
        }
    } elseif ($action === 'create_user') {
        try {
            $db = Database::fromConfig($cfg['db']);
            $username = trim((string) ($_POST['username'] ?? ''));
            if ($username === '') {
                throw new RuntimeException('نام کاربری نمی‌تواند خالی باشد.');
            }
            $role = trim((string) ($_POST['role'] ?? 'user'));
            if (!in_array($role, Roles::ALL, true)) {
                throw new RuntimeException('نقش نامعتبر است.');
            }
            $tenantSlug = trim((string) ($_POST['tenant_slug'] ?? ($cfg['tenant_slug'] ?? 'default')));
            $displayName = trim((string) ($_POST['display_name'] ?? $username));
            $password = trim((string) ($_POST['password'] ?? ''));

            $generated = false;
            if ($password === '') {
                $password = rtrim(strtr(base64_encode(random_bytes(18)), '+/', '-_'), '=');
                $generated = true;
            }

            $now = gmdate('Y-m-d H:i:s');
            $tenant = $db->queryOne('SELECT id FROM tenants WHERE slug = ?', [$tenantSlug]);
            if ($tenant === null) {
                throw new RuntimeException("مستأجر '{$tenantSlug}' یافت نشد. ابتدا حساب مدیر را ایجاد کنید.");
            }
            $tenantId = (string) $tenant['id'];

            $dupUser = $db->queryOne('SELECT id FROM users WHERE tenant_id = ? AND username = ?', [$tenantId, $username]);
            if ($dupUser !== null) {
                throw new RuntimeException("کاربری با نام {$username} از قبل وجود دارد.");
            }

            $hash = Auth::hashPassword($password);
            $userId = Ids::v4();
            $db->transaction(function (Database $tx) use ($userId, $tenantId, $username, $displayName, $hash, $role, $now): void {
                $tx->insert('users', [
                    'id' => $userId, 'tenant_id' => $tenantId, 'username' => $username,
                    'display_name' => $displayName, 'password_hash' => $hash,
                    'status' => 'active', 'token_version' => 1, 'must_change_password' => 1,
                    'created_at' => $now, 'updated_at' => $now,
                ]);
                $tx->insert('memberships', [
                    'id' => Ids::v4(), 'tenant_id' => $tenantId, 'user_id' => $userId,
                    'role' => $role, 'created_at' => $now, 'updated_at' => $now,
                ]);
            });

            $notice = "حساب کاربر '{$username}' با نقش '{$role}' ساخته شد.";
            $noticeType = 'success';
            $output = "نام کاربری: " . $username . "\nنقش: " . $role . "\nگذرواژه: " . $password . ($generated ? " (تولید تصادفی)" : "") . "\n\nحتماً این اطلاعات را به همکار خود تحویل دهید.";
        } catch (Throwable $e) {
            $notice = 'خطا در ساخت حساب کاربر: ' . $e->getMessage();
            $noticeType = 'error';
        }
    } elseif ($action === 'smoke_test') {
        try {
            $lines = [];
            $fails = 0;
            $pass = function (string $msg) use (&$lines) { $lines[] = "PASS  " . $msg; };
            $fail = function (string $msg) use (&$lines, &$fails) { $lines[] = "FAIL  " . $msg; $fails++; };

            if (version_compare(PHP_VERSION, '8.1.0', '>=')) {
                $pass("نسخه PHP: " . PHP_VERSION);
            } else {
                $fail("نسخه PHP باید حداقل 8.1 باشد (فعلی: " . PHP_VERSION . ")");
            }

            foreach (['pdo_mysql', 'mbstring', 'json', 'hash', 'openssl', 'sodium'] as $ext) {
                if (extension_loaded($ext)) {
                    $pass("اکستنشن {$ext} فعال است");
                } else {
                    $fail("اکستنشن {$ext} فعال نیست");
                }
            }

            if (in_array('argon2id', password_algos(), true)) {
                $pass("الگوریتم هش Argon2id در دسترس است");
            } else {
                $pass("الگوریتم رمزنگاری پیش‌فرض PASSWORD_DEFAULT فعال است");
            }

            $db = Database::fromConfig($cfg['db']);
            $db->query('SELECT 1');
            $pass("اتصال دیتابیس با حساب کاربری اپلیکیشن برقرار است");

            $tenant = $db->queryOne('SELECT id FROM tenants WHERE slug = ?', [$cfg['tenant_slug'] ?? 'default']);
            if ($tenant !== null) {
                $pass("مستأجر پیش‌فرض (Tenant) در دیتابیس ثبت شده است");
            } else {
                $lines[] = "WARN  مستأجر هنوز ایجاد نشده (پس از ایجاد اولین مدیر ساخته می‌شود)";
            }

            $migCount = (int) $db->queryValue('SELECT COUNT(*) FROM schema_migrations');
            $pass("تعداد مایگریشن‌های اعمال‌شده: {$migCount}");

            $notice = $fails === 0 ? 'آزمون سلامت کامل شد (PASS)' : 'آزمون سلامت دارای خطا بود (FAIL)';
            $noticeType = $fails === 0 ? 'success' : 'error';
            $output = implode("\n", $lines);
        } catch (Throwable $e) {
            $notice = 'خطا در آزمون سلامت: ' . $e->getMessage();
            $noticeType = 'error';
        }
    } elseif ($action === 'delete_self') {
        if (@unlink(__FILE__)) {
            echo '<!DOCTYPE html><html dir="rtl" lang="fa"><head><meta charset="utf-8"><title>پایان نصب</title>'
                . '<style>body{font-family:sans-serif;background:#0f172a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}'
                . '.card{background:#1e293b;padding:32px;border-radius:12px;border:1px solid #334155;text-align:center;max-width:440px;}'
                . 'h2{color:#22c55e;margin-top:0;}p{color:#94a3b8;line-height:1.7;}</style></head><body>'
                . '<div class="card"><h2>فایل نصب با موفقیت حذف شد</h2>'
                . '<p>فایل setup.php جهت امنیت سرور به طور کامل از هاست پاک شد. اکنون می‌توانید پنل دسکتاپ را به API متصل کنید.</p></div></body></html>';
            exit;
        } else {
            $notice = 'حذف خودکار فایل ناموفق بود. لطفاً به صورت دستی فایل setup.php را از پوشه public حذف کنید.';
            $noticeType = 'error';
        }
    }
}
?>
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>راه‌انداز دیتابیس ابری IRICA</title>
    <style>
        :root {
            --bg: #0f172a;
            --card: #1e293b;
            --card-sub: #334155;
            --border: #475569;
            --text: #f8fafc;
            --muted: #94a3b8;
            --brand: #2563eb;
            --brand-hover: #1d4ed8;
            --success: #22c55e;
            --error: #ef4444;
            --warning: #f59e0b;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            padding: 24px 16px;
            background: var(--bg);
            color: var(--text);
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, Tahoma, sans-serif;
            font-size: 14px;
            line-height: 1.6;
        }
        .container {
            max-width: 680px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            margin-bottom: 24px;
        }
        .header h1 {
            margin: 0 0 8px;
            font-size: 22px;
            font-weight: 700;
        }
        .header p {
            margin: 0;
            color: var(--muted);
            font-size: 13px;
        }
        .alert {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-weight: 500;
        }
        .alert-info { background: #1e3a8a; color: #bfdbfe; }
        .alert-success { background: #14532d; color: #bbf7d0; border: 1px solid var(--success); }
        .alert-error { background: #7f1d1d; color: #fecaca; border: 1px solid var(--error); }
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .card-title {
            margin: 0 0 12px;
            font-size: 16px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .btn {
            background: var(--brand);
            color: #fff;
            border: none;
            padding: 9px 18px;
            border-radius: 6px;
            font-family: inherit;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .btn:hover { background: var(--brand-hover); }
        .btn-success { background: #16a34a; }
        .btn-success:hover { background: #15803d; }
        .btn-danger { background: #dc2626; }
        .btn-danger:hover { background: #b91c1c; }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 12px;
        }
        @media (max-width: 600px) {
            .form-row { grid-template-columns: 1fr; }
        }
        .form-group {
            display: flex;
            flex-direction: column;
            gap: 4px;
            margin-bottom: 12px;
        }
        .form-group label {
            font-size: 12px;
            color: var(--muted);
        }
        input, select {
            background: var(--card-sub);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 8px 12px;
            color: var(--text);
            font-family: inherit;
            font-size: 13px;
        }
        input:focus, select:focus {
            outline: none;
            border-color: var(--brand);
        }
        pre {
            background: #020617;
            padding: 14px;
            border-radius: 8px;
            border: 1px solid #1e293b;
            color: #38bdf8;
            font-family: monospace;
            font-size: 12px;
            overflow-x: auto;
            white-space: pre-wrap;
            direction: ltr;
            text-align: left;
            margin-top: 14px;
        }
        .hint {
            font-size: 12px;
            color: var(--muted);
            margin-top: 6px;
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>راه‌اندازی دیتابیس ابری (محیط هاست اشتراکی)</h1>
        <p>نصب اسکیما، ساخت کاربران و تست سرویس بدون نیاز به SSH</p>
    </div>

    <?php if ($notice !== ''): ?>
        <div class="alert alert-<?= h($noticeType) ?>"><?= h($notice) ?></div>
    <?php endif; ?>

    <?php if ($output !== ''): ?>
        <pre><?= h($output) ?></pre>
    <?php endif; ?>

    <?php if ($setupKey !== '' && hash_equals($setupKey, $reqKey)): ?>

        <!-- Step 1: Migration -->
        <div class="card">
            <h2 class="card-title">۱. ساخت جدول‌های دیتابیس (Migration)</h2>
            <p class="hint">با کلیک روی این دکمه، تمام ۱۸ جدول و ایندکس‌های پایگاه‌داده روی MySQL ایجاد می‌شوند.</p>
            <form method="POST">
                <input type="hidden" name="key" value="<?= h($reqKey) ?>">
                <input type="hidden" name="action" value="migrate">
                <button type="submit" class="btn btn-success">اجرای مایگریشن دیتابیس</button>
            </form>
        </div>

        <!-- Step 2: Create Admin -->
        <div class="card">
            <h2 class="card-title">۲. ساخت حساب مدیر اصلی (Admin)</h2>
            <p class="hint">حساب مدیر برای ورود اول، ثبت سیستم‌ها و انتقال پرونده‌ها استفاده می‌شود.</p>
            <form method="POST">
                <input type="hidden" name="key" value="<?= h($reqKey) ?>">
                <input type="hidden" name="action" value="create_admin">
                <div class="form-row">
                    <div class="form-group">
                        <label>نام کاربری:</label>
                        <input type="text" name="username" value="admin" required>
                    </div>
                    <div class="form-group">
                        <label>نام نمایشی:</label>
                        <input type="text" name="display_name" value="مدیر سامانه">
                    </div>
                </div>
                <div class="form-group">
                    <label>گذرواژه (اختیاری — در صورت خالی بودن گذرواژهٔ تصادفی قوی تولید می‌شود):</label>
                    <input type="text" name="password" placeholder="حداقل ۱۲ کاراکتر یا خالی بگذارید">
                </div>
                <button type="submit" class="btn">ایجاد مدیر اصلی</button>
            </form>
        </div>

        <!-- Step 3: Create User (Optional) -->
        <div class="card">
            <h2 class="card-title">۳. ساخت حساب برای همکاران (اختیاری)</h2>
            <p class="hint">هر یک از همکاران می‌تواند با نام کاربری و دسترسی اختصاصی وارد شود.</p>
            <form method="POST">
                <input type="hidden" name="key" value="<?= h($reqKey) ?>">
                <input type="hidden" name="action" value="create_user">
                <div class="form-row">
                    <div class="form-group">
                        <label>نام کاربری همکار:</label>
                        <input type="text" name="username" placeholder="مثال: reza" required>
                    </div>
                    <div class="form-group">
                        <label>نقش دسترسی:</label>
                        <select name="role">
                            <option value="user">کاربر عادی (ویرایش و ثبت پرونده‌ها)</option>
                            <option value="admin">مدیر (دسترسی کامل)</option>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label>گذرواژه (اختیاری):</label>
                    <input type="text" name="password" placeholder="حداقل ۱۲ کاراکتر یا خالی بگذارید">
                </div>
                <button type="submit" class="btn">ایجاد حساب همکار</button>
            </form>
        </div>

        <!-- Step 4: Smoke Test -->
        <div class="card">
            <h2 class="card-title">۴. آزمون سلامت هاست (Smoke Test)</h2>
            <p class="hint">بررسی سلامت وب‌سرور، اتصالات دیتابیس و توابع رمزنگاری.</p>
            <form method="POST">
                <input type="hidden" name="key" value="<?= h($reqKey) ?>">
                <input type="hidden" name="action" value="smoke_test">
                <button type="submit" class="btn">اجرای تست سلامت</button>
            </form>
        </div>

        <!-- Step 5: Self-Destruct -->
        <div class="card" style="border-color: #7f1d1d;">
            <h2 class="card-title" style="color: #f87171;">۵. پایان کار و حذف فایل Setup</h2>
            <p class="hint">پس از اطمینان از انجام مراحل بالا، جهت امنیت سرور حتماً فایل راه‌انداز را حذف کنید.</p>
            <form method="POST" onsubmit="return confirm('آیا از حذف کامل فایل setup.php اطمینان دارید؟');">
                <input type="hidden" name="key" value="<?= h($reqKey) ?>">
                <input type="hidden" name="action" value="delete_self">
                <button type="submit" class="btn btn-danger">حذف خودکار فایل setup.php از سرور</button>
            </form>
        </div>

    <?php endif; ?>
</div>
</body>
</html>
