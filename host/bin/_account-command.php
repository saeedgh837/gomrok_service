<?php
/** Shared, host-controlled CLI for creating private accounts and rotating credentials. */
declare(strict_types=1);

function runPrivateAccountCommand(string $script, string $defaultRole,
                                  string $passwordEnvironment, bool $requireName): never
{
    if (PHP_SAPI !== 'cli') {
        http_response_code(403);
        exit(1);
    }
    ini_set('display_errors', '0');
    error_reporting(E_ALL);
    foreach (['Schema', 'Database', 'Http', 'Ids', 'Config', 'Tokens', 'Roles',
        'Workspace', 'Auth'] as $classFile) {
        require_once dirname(__DIR__) . '/src/' . $classFile . '.php';
    }
    global $argv;
    foreach (array_slice($argv, 1) as $argument) {
        if (preg_match('/\A(?:--(?:username|display-name|tenant-slug|role)=.+|--password-stdin|--reset-password|--quiet|--help)\z/D', $argument) !== 1) {
            fwrite(STDERR, 'FAIL  unsupported option; use --help (password arguments are forbidden)' . PHP_EOL);
            exit(2);
        }
    }
    $options = getopt('', ['username:', 'password-stdin', 'display-name:',
        'tenant-slug:', 'role:', 'reset-password', 'quiet', 'help']);
    if ($options === false || ($requireName && !isset($options['username'])
        && !isset($options['help']))) {
        fwrite(STDERR, 'usage: php bin/' . $script . ' --username=NAME '
            . '[--password-stdin] [--role=admin|user] [--tenant-slug=NEW-SLUG] '
            . '[--reset-password] [--quiet]' . PHP_EOL);
        exit(2);
    }
    if (isset($options['help'])) {
        fwrite(STDOUT, "Create a new private dataset, never join an existing tenant.\n"
            . "  --username=NAME       global canonical login (3-64 ASCII characters)\n"
            . "  --password-stdin      read one redirected password line, without echo\n"
            . "  --display-name=NAME   optional display name\n"
            . "  --role=admin|user     membership role (creation only)\n"
            . "  --tenant-slug=SLUG    unused slug; config tenant_slug is ignored\n"
            . "  --reset-password      exact existing owner; no rename or role change\n"
            . "  --quiet               suppress account details\n"
            . 'Environment: ' . $passwordEnvironment . "; omit to generate once.\n");
        exit(0);
    }
    try {
        $name = Workspace::canonical((string) ($options['username'] ?? 'admin'));
        $role = isset($options['role']) ? (string) $options['role'] : $defaultRole;
        if (!Roles::isKnown($role)) {
            throw new InvalidArgumentException('--role must be admin or user');
        }
        if (isset($options['reset-password']) && !isset($options['username'])) {
            throw new InvalidArgumentException('password reset requires an explicit username');
        }
        $fromEnv = getenv($passwordEnvironment);
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
            $password = rtrim(strtr(base64_encode(random_bytes(24)), '+/', '-_'), '=');
        }
        if (strlen($password) < 12 || strlen($password) > 4096) {
            throw new InvalidArgumentException('password must contain 12 to 4096 bytes');
        }
        $hash = Auth::hashPassword($password);
        $config = Config::load();
        $db = Database::fromConfig($config['db']);
        $slug = isset($options['tenant-slug']) ? (string) $options['tenant-slug'] : null;
        if (isset($options['reset-password'])) {
            $account = Workspace::rotatePassword($db, $name, $hash, $slug,
                isset($options['role']) ? $role : null);
            $outcome = 'password rotated; user and device credentials revoked';
        } else {
            $account = Workspace::createAccount($db, $name, $hash,
                (string) ($options['display-name'] ?? $name), $role, $slug);
            $outcome = 'private account created';
        }
        fwrite(STDOUT, 'OK    ' . $outcome . PHP_EOL);
        if (!isset($options['quiet'])) {
            foreach (['username', 'role', 'user_id', 'tenant_id', 'slug'] as $key) {
                fwrite(STDOUT, '      ' . $key . ': ' . $account[$key] . PHP_EOL);
            }
        }
        // Supplied credentials never reappear in output, including failures.
        if ($generated) {
            fwrite(STDOUT, 'PASSWORD (shown once):' . PHP_EOL . '  ' . $password . PHP_EOL);
        }
        exit(0);
    } catch (PDOException $error) {
        $errno = (int) ($error->errorInfo[1] ?? 0);
        $message = $errno === 1062 ? 'username or tenant slug is already reserved'
            : 'database operation failed (errno ' . $errno . ')';
    } catch (ApiException | InvalidArgumentException $error) {
        $message = $error->getMessage();
    } catch (Throwable $error) {
        $message = 'account operation failed (' . get_class($error) . ')';
    }
    fwrite(STDERR, 'FAIL  ' . $message . PHP_EOL);
    exit(1);
}
