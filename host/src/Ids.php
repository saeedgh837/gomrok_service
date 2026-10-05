<?php
/**
 * Ids.php — UUID helpers shared by every host module.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §4 (new server-side ids are full
 * UUID v4; a migrated row's id is uuid5 of (installation, entity, legacy id))
 * and §15.1/§15.2 (the two synthetic legacy-id formulas).
 *
 * uuid5 is implemented from sha1 because PHP has no built-in UUID builder and
 * this package carries no dependency (spec §6: plain PHP 8.1, no Composer).
 * It must agree byte for byte with Python's uuid.uuid5(uuid.NAMESPACE_URL, …),
 * which is asserted by lab/tests/test_host_api.py against cloud_sync.ids.
 */

declare(strict_types=1);

final class Ids
{
    /** RFC 4122 name space for URL — identical to Python's uuid.NAMESPACE_URL. */
    public const NAMESPACE_URL = '6ba7b811-9dad-11d1-80b4-00c04fd430c8';

    /** Synthetic legacy id of the single settings row (spec §15.2). */
    public const SETTINGS_LEGACY_ID = 'settings/settings';

    /** The seven spec §3 entities, in the order the client emits them. */
    public const ENTITIES = [
        'customers', 'customer_transactions', 'owners', 'identities',
        'crm_devices', 'periods', 'settings',
    ];

    /** Entities whose `id` is minted by the client, so they carry no legacy id. */
    public const SYNTHETIC_ID_ENTITIES = ['customer_transactions', 'settings'];

    public static function isUuid(string $value): bool
    {
        return preg_match(
            '/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/',
            strtolower($value)
        ) === 1;
    }

    public static function isEntity(string $entity): bool
    {
        return in_array($entity, self::ENTITIES, true);
    }

    public static function isSynthetic(string $entity): bool
    {
        return in_array($entity, self::SYNTHETIC_ID_ENTITIES, true);
    }

    /** A fresh UUID v4, from the CSPRNG. */
    public static function v4(): string
    {
        $bytes = random_bytes(16);
        $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
        $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        $hex = bin2hex($bytes);
        return sprintf(
            '%s-%s-%s-%s-%s',
            substr($hex, 0, 8),
            substr($hex, 8, 4),
            substr($hex, 12, 4),
            substr($hex, 16, 4),
            substr($hex, 20, 12)
        );
    }

    /**
     * uuid5(NAMESPACE_URL, "<source_installation_id>/<entity>/<legacy_id>").
     *
     * Deterministic: the same triple always yields the same row id, so a
     * replayed import or a second machine lands on the same record (spec §4).
     *
     * @throws InvalidArgumentException when any part is blank or the entity is
     *         not one of the seven spec §3 names (the caller turns that into a
     *         stable error code; the message never repeats the values).
     */
    public static function uuid5(string $sourceInstallationId, string $entity,
                                 string $legacyId): string
    {
        if ($sourceInstallationId === '' || $legacyId === ''
            || !self::isEntity($entity)) {
            throw new InvalidArgumentException(
                'uuid5 requires a non-empty installation and legacy id and a '
                . 'known entity'
            );
        }
        $name = $sourceInstallationId . '/' . $entity . '/' . $legacyId;
        $namespace = self::hexToBytes(str_replace('-', '', self::NAMESPACE_URL));
        $hash = sha1($namespace . $name, true);
        $hex = bin2hex(substr($hash, 0, 16));
        // RFC 4122 version 5 (nibble 12) and variant bits (nibble 16).
        $hex[12] = '5';
        $hex[16] = dechex((hexdec($hex[16]) & 0x3) | 0x8);
        return sprintf(
            '%s-%s-%s-%s-%s',
            substr($hex, 0, 8),
            substr($hex, 8, 4),
            substr($hex, 12, 4),
            substr($hex, 16, 4),
            substr($hex, 20, 12)
        );
    }

    private static function hexToBytes(string $hex): string
    {
        $out = '';
        $length = strlen($hex);
        for ($i = 0; $i + 1 < $length; $i += 2) {
            $out .= chr((int) hexdec(substr($hex, $i, 2)));
        }
        return $out;
    }

    /**
     * The synthetic legacy id of one ledger row (spec §15.2).
     *
     * @throws InvalidArgumentException when the parts are unusable
     */
    public static function transactionLegacyId(string $customerLegacyId,
                                               int $seq): string
    {
        if ($customerLegacyId === '' || $seq < 0) {
            throw new InvalidArgumentException(
                'transaction legacy id requires a customer legacy id and a '
                . 'sequence number'
            );
        }
        return $customerLegacyId . '/' . $seq;
    }
}
