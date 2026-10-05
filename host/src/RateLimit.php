<?php
/**
 * RateLimit.php — DB-backed login throttling (spec §10.5).
 *
 * Keyed on account + IP, stored in `login_attempts`, so every PHP worker sees
 * the same counters (an in-process limiter would reset on each request under
 * cPanel's per-request model).
 *
 * Two controls, both temporary by construction:
 *   * a growing delay — after `delay_after` consecutive failures each further
 *     attempt must wait `base * 2^n`, capped at `max_delay`;
 *   * a temporary lock — after `lock_after` consecutive failures the account
 *     is refused for `lock_seconds` and then simply works again.
 *
 * There is deliberately no "banned" column and no permanent lockout: a lock
 * that never expires is a denial-of-service weapon against a known username
 * (spec §10.5, "no permanent lockout (DoS)").
 *
 * A successful login writes a `success` row, which resets the consecutive
 * failure count.
 */

declare(strict_types=1);

final class RateLimit
{
    /**
     * @param array<string, mixed> $cfg the `rate_limit` section
     */
    public function __construct(private readonly array $cfg)
    {
    }

    private function int(string $key, int $default): int
    {
        $value = $this->cfg[$key] ?? $default;
        return is_int($value) && $value >= 0 ? $value : $default;
    }

    /**
     * Refuse the attempt when the window says so.
     *
     * @throws ApiException 429 RATE_LIMITED with Retry-After when a delay or
     *         the temporary lock still applies
     */
    public function check(\Database $db, string $account, string $ip,
                          string $now): void
    {
        $window = $this->int('window_seconds', 900);
        if ($window <= 0) {
            return;
        }
        $since = gmdate('Y-m-d H:i:s', strtotime($now) - $window);
        $rows = $db->query(
            'SELECT outcome, attempted_at FROM login_attempts'
            . ' WHERE account = ? AND ip = ? AND attempted_at > ?'
            . ' ORDER BY attempted_at DESC, id DESC LIMIT 50',
            [$account, $ip, $since]
        );

        $failures = 0;
        $last = null;
        foreach ($rows as $row) {
            if (($row['outcome'] ?? '') === 'success') {
                break;
            }
            $failures++;
            if ($last === null) {
                $last = (string) ($row['attempted_at'] ?? $now);
            }
        }
        if ($failures === 0 || $last === null) {
            return;
        }
        $age = max(0, strtotime($now) - strtotime($last));

        $lockAfter = $this->int('lock_after', 8);
        $lockSeconds = $this->int('lock_seconds', 300);
        if ($lockAfter > 0 && $failures >= $lockAfter) {
            if ($age < $lockSeconds) {
                throw new ApiException(429, Codes::RATE_LIMITED,
                    'login attempts are temporarily locked',
                    max(1, $lockSeconds - $age));
            }
            // The lock expired: start counting again instead of extending a
            // lock the operator has already waited out.
            return;
        }

        $delayAfter = $this->int('delay_after', 4);
        $base = $this->int('base_delay_seconds', 1);
        $max = $this->int('max_delay_seconds', 60);
        if ($delayAfter <= 0 || $failures < $delayAfter || $base <= 0) {
            return;
        }
        $exponent = min(20, $failures - $delayAfter);
        $delay = min($max, $base * (2 ** $exponent));
        if ($age < $delay) {
            throw new ApiException(429, Codes::RATE_LIMITED,
                'login attempts are being throttled', max(1, $delay - $age));
        }
    }

    /** Record one attempt. Never called for a request the limiter refused. */
    public function record(\Database $db, ?string $tenantId, string $account,
                           string $ip, string $outcome, string $now): void
    {
        $db->insert('login_attempts', [
            'tenant_id' => $tenantId,
            'account' => substr($account, 0, 120),
            'ip' => substr($ip, 0, 45),
            'attempted_at' => $now,
            'outcome' => $outcome,
        ]);
        $this->prune($db, $now);
    }

    /**
     * Drop rows outside any window that could still matter.
     *
     * Runs at most once a minute so a login storm does not turn a throttle
     * check into a write amplification problem.
     */
    private function prune(\Database $db, string $now): void
    {
        static $lastPrune = 0;
        $timestamp = strtotime($now);
        if ($timestamp - $lastPrune < 60) {
            return;
        }
        $lastPrune = $timestamp;
        $window = max($this->int('window_seconds', 900),
            $this->int('lock_seconds', 300));
        $cutoff = gmdate('Y-m-d H:i:s', $timestamp - ($window * 4));
        try {
            $db->delete('login_attempts', 'attempted_at < ?', [$cutoff]);
        } catch (\Throwable $error) {
            // Housekeeping must never fail a login; the row is harmless.
            unset($error);
        }
    }
}
