<?php
/**
 * Roles.php — the spec §7 authorization matrix, in one place.
 *
 *   capability                                   admin   user
 *   read all synced data                          yes     yes
 *   create/edit cases, transactions, owners,
 *     identities, stages                          yes     yes
 *   open/close periods, delete records            yes      no
 *   settings read                                 yes     yes
 *   settings write                                yes      no
 *   users, roles, devices; enrol; run import      yes      no
 *
 * There are exactly two roles (spec §7: "no other roles in v1") and no
 * row-owner concept. A capability that is not listed here is denied, so a
 * new entity or operation defaults to "both may do it" only through the
 * explicit allowlist below — never through a missing check.
 */

declare(strict_types=1);

final class Roles
{
    public const ADMIN = 'admin';
    public const USER = 'user';

    /** The only roles the membership column may hold (mirrors the SQL CHECK). */
    public const ALL = [self::ADMIN, self::USER];

    /**
     * May this role write `entity` with `kind`?
     *
     * Reads are not gated here: every authenticated principal may read the
     * synced dataset (spec §7, first row of the matrix).
     */
    public static function canWrite(string $role, string $entity, string $kind): bool
    {
        if (!self::isKnown($role)) {
            return false;
        }
        if ($kind === 'delete') {
            // "open/close periods, delete records" is admin-only.
            return $role === self::ADMIN;
        }
        if ($entity === 'settings' || $entity === 'periods') {
            // settings write is admin-only; a period open/close is a periods
            // write, and the user column of the matrix says no.
            return $role === self::ADMIN;
        }
        // customers, customer_transactions, owners, identities, crm_devices
        return $kind === 'upsert';
    }

    /** Enrolling a machine is "create/modify devices" — admin only. */
    public static function canEnroll(string $role): bool
    {
        return $role === self::ADMIN;
    }

    /** Running the one-shot import is admin only (spec §8, §7). */
    public static function canImport(string $role): bool
    {
        return $role === self::ADMIN;
    }

    /** Reading the synced dataset: both roles. */
    public static function canRead(string $role): bool
    {
        return self::isKnown($role);
    }

    public static function isKnown(string $role): bool
    {
        return in_array($role, self::ALL, true);
    }

    /**
     * Assert a write capability or fail closed with 403 ROLE_FORBIDDEN.
     *
     * The message names the operation, never the record: a role refusal must
     * not become a probe for what exists (spec §7, no IDOR oracle).
     *
     * @throws ApiException
     */
    public static function requireWrite(string $role, string $entity,
                                        string $kind): void
    {
        if (!self::canWrite($role, $entity, $kind)) {
            throw new ApiException(403, Codes::ROLE_FORBIDDEN,
                'the acting role may not ' . $kind . ' ' . $entity
                . ' records');
        }
    }

    /** @throws ApiException */
    public static function requireEnroll(string $role): void
    {
        if (!self::canEnroll($role)) {
            throw new ApiException(403, Codes::ROLE_FORBIDDEN,
                'the acting role may not enrol devices');
        }
    }

    /** @throws ApiException */
    public static function requireImport(string $role): void
    {
        if (!self::canImport($role)) {
            throw new ApiException(403, Codes::ROLE_FORBIDDEN,
                'the acting role may not run an import');
        }
    }
}
