<?php
/**
 * Log.php — the request log and the audit trail.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §5 ("nothing in Authorization, Cookie,
 * token, password, request body, outbox payload or PII may reach a log — logs
 * carry request_id, route, status, duration, internal UUIDs only") and §10.9
 * ("no passwords, tokens, cookies, national ids, phone numbers or names in any
 * server log or audit row; audit_events stores metadata, never a plaintext
 * diff of PII").
 *
 * Two enforcement rules make that structural rather than a code-review hope:
 *   1. the logger accepts a fixed set of scalar fields — there is no API that
 *      takes a body, a header or a query string;
 *   2. an exception is reduced to its class plus, for PDO, the SQLSTATE and
 *      errno. PDOException::getMessage() is never written, because a duplicate
 *      key violation embeds the offending value in the driver's text.
 */

declare(strict_types=1);

final class Log
{
    private const ALLOWED_FIELDS = [
        'ts', 'request_id', 'method', 'route', 'status', 'duration_ms',
        'code', 'tenant_id', 'user_id', 'device_id', 'action', 'outcome',
        'entity', 'operation', 'count', 'conflicts', 'cursor', 'errno',
        'sqlstate', 'error_class', 'error_file', 'error_line', 'level',
        'ip', 'detail',
    ];

    private ?string $path;

    public function __construct(?string $path)
    {
        $this->path = $path !== null && $path !== '' ? $path : null;
        if ($this->path !== null) {
            $directory = dirname($this->path);
            if (!is_dir($directory)) {
                @mkdir($directory, 0750, true);
            }
            // PHP's own warnings share the file, so display_errors must be off
            // and every internal message lands somewhere the operator controls.
            @ini_set('display_errors', '0');
            @ini_set('display_startup_errors', '0');
            @ini_set('log_errors', '1');
            @ini_set('error_log', $this->path);
        } else {
            @ini_set('display_errors', '0');
        }
    }

    public function path(): ?string
    {
        return $this->path;
    }

    /**
     * Write one structured line. Values are scalars; anything else is dropped.
     *
     * @param array<string, mixed> $fields
     */
    public function line(array $fields): void
    {
        if ($this->path === null) {
            return;
        }
        $record = ['ts' => gmdate('Y-m-d\TH:i:s\Z')];
        foreach ($fields as $key => $value) {
            if (!in_array($key, self::ALLOWED_FIELDS, true)) {
                continue;
            }
            if ($value === null) {
                continue;
            }
            if (is_bool($value)) {
                $record[$key] = $value;
            } elseif (is_int($value)) {
                $record[$key] = $value;
            } elseif (is_string($value)) {
                // Strings are bounded so a stray value can never dominate the
                // file, and control characters are stripped for line safety.
                $record[$key] = preg_replace('/[\x00-\x1f\x7f]/', ' ',
                    substr($value, 0, 200)) ?? '';
            }
        }
        $json = json_encode($record, JSON_UNESCAPED_SLASHES);
        if ($json === false) {
            return;
        }
        @file_put_contents($this->path, $json . "\n",
            FILE_APPEND | LOCK_EX);
    }

    /**
     * Reduce an exception to fields that are safe to persist.
     *
     * @return array<string, mixed>
     */
    public static function describe(\Throwable $error): array
    {
        $fields = ['error_class' => get_class($error)];
        if ($error instanceof \PDOException) {
            $info = $error->errorInfo ?? null;
            if (is_array($info)) {
                if (isset($info[0]) && is_string($info[0])) {
                    $fields['sqlstate'] = $info[0];
                }
                if (isset($info[1]) && is_int($info[1])) {
                    $fields['errno'] = $info[1];
                }
            }
            if (!isset($fields['sqlstate'])) {
                $fields['sqlstate'] = (string) $error->getCode();
            }
            return $fields;
        }
        // Our own exceptions never carry a credential, so the text is useful
        // and safe. Anything else is reported by class and location only.
        if ($error instanceof \RuntimeException
            || $error instanceof \InvalidArgumentException
            || $error instanceof \LogicException) {
            $fields['detail'] = $error->getMessage();
        }
        $file = $error->getFile();
        $fields['error_file'] = basename($file);
        $fields['error_line'] = $error->getLine();
        return $fields;
    }

    /**
     * Append one audit_events row (spec §10.9: metadata only).
     *
     * The caller passes action/actor/outcome and a metadata array whose keys
     * are counted, coded or internal identifiers — never record contents.
     * A failed audit write is logged and swallowed: losing an audit row must
     * not turn a successful login into a 500.
     *
     * @param array<string, mixed> $event
     */
    public function audit(\Database $db, array $event, bool $required = false): void
    {
        $metadata = $event['metadata'] ?? null;
        $encoded = null;
        if (is_array($metadata) && $metadata !== []) {
            $encoded = json_encode($metadata, JSON_UNESCAPED_SLASHES);
            if ($encoded === false || strlen($encoded) > 60000) {
                $encoded = null;
            }
        }
        try {
            $db->insert('audit_events', [
                'id' => Ids::v4(),
                'tenant_id' => (string) $event['tenant_id'],
                'occurred_at' => gmdate('Y-m-d H:i:s'),
                'action' => substr((string) $event['action'], 0, 64),
                'actor_type' => substr((string) ($event['actor_type'] ?? 'system'),
                    0, 16),
                'actor_user_id' => $event['actor_user_id'] ?? null,
                ...(isset($event['actor_operator_id'])
                    ? ['actor_operator_id' => $event['actor_operator_id']] : []),
                'actor_device_id' => $event['actor_device_id'] ?? null,
                'entity' => substr((string) ($event['entity'] ?? ''), 0, 64),
                'entity_id' => $event['entity_id'] ?? null,
                'request_id' => $event['request_id'] ?? null,
                'ip' => isset($event['ip']) && is_string($event['ip'])
                    ? substr($event['ip'], 0, 45) : null,
                'outcome' => substr((string) ($event['outcome'] ?? 'ok'), 0, 16),
                'metadata' => $encoded,
            ]);
        } catch (\Throwable $error) {
            if ($required) {
                throw $error;
            }
            $fields = self::describe($error);
            $fields['action'] = 'audit.write_failed';
            $fields['level'] = 'error';
            $this->line($fields);
        }
    }
}
