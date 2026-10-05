<?php
/**
 * Tokens.php — access tokens (compact JWS, HS256) and opaque refresh tokens.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §5.
 *
 * Why HS256 with hash_hmac rather than an RSA/ES256 JWS: there is exactly one
 * verifier (this host) and one issuer (this host), so a shared secret is
 * sufficient and the signature can be produced with `hash_hmac`, which every
 * PHP build has. RS256 would need the openssl extension and a key pair for no
 * added security, and this package must run on an unknown cPanel PHP build
 * with no Composer install (spec §6). The choice and its reasoning are
 * recorded in host/SECURITY.md.
 *
 * The algorithm is pinned to HS256 and `none` is rejected outright: the
 * header is parsed only to confirm the algorithm, never to choose one.
 *
 * Refresh tokens are 256 bits of CSPRNG output, stored only as SHA-256
 * (spec §5: "stored hashed server-side"), rotated on every use and
 * one-time-use per family.
 */

declare(strict_types=1);

final class Tokens
{
    public const ISSUER = 'irica-host';
    public const AUDIENCE_USER = 'user';
    public const AUDIENCE_DEVICE = 'sync';
    public const KIND_USER = 'user';
    public const KIND_DEVICE = 'device';

    /** Tolerated clock skew, in seconds, when checking `nbf`. */
    public const CLOCK_SKEW = 60;

    public function __construct(private readonly string $secret)
    {
    }

    /**
     * Sign a compact JWS. `exp`/`iat`/`nbf` are Unix seconds (UTC).
     *
     * @param array<string, mixed> $claims
     */
    public function sign(array $claims): string
    {
        $header = ['alg' => 'HS256', 'typ' => 'JWT'];
        $payload = ['iss' => self::ISSUER] + $claims;
        $signingInput = self::b64(json_encode($header, JSON_UNESCAPED_SLASHES))
            . '.' . self::b64(json_encode($payload, JSON_UNESCAPED_SLASHES));
        $signature = hash_hmac('sha256', $signingInput, $this->secret, true);
        return $signingInput . '.' . self::b64($signature);
    }

    /**
     * Verify a compact JWS and return its claims.
     *
     * @throws ApiException 401 AUTH_REQUIRED when the token is not ours
     * @return array<string, mixed>
     */
    public function verify(string $jwt, int $now): array
    {
        $parts = explode('.', $jwt);
        if (count($parts) !== 3 || $parts[0] === '' || $parts[1] === ''
            || $parts[2] === '') {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token is malformed');
        }
        $signingInput = $parts[0] . '.' . $parts[1];
        $expected = hash_hmac('sha256', $signingInput, $this->secret, true);
        $given = self::unb64($parts[2]);
        if ($given === null || !hash_equals($expected, $given)) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token signature does not verify');
        }
        $header = self::unb64json($parts[0]);
        if (!is_array($header) || ($header['alg'] ?? null) !== 'HS256') {
            // The algorithm is pinned: a token claiming `none` or RS256 is
            // rejected before anything else is read.
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token uses an unsupported algorithm');
        }
        $claims = self::unb64json($parts[1]);
        if (!is_array($claims)) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token payload is malformed');
        }
        if (($claims['iss'] ?? null) !== self::ISSUER) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token issuer is not this host');
        }
        foreach (['sub', 'aud', 'kind', 'tid', 'sid'] as $required) {
            if (!isset($claims[$required]) || !is_string($claims[$required])
                || $claims[$required] === '') {
                throw new ApiException(401, Codes::AUTH_REQUIRED,
                    'access token is missing a required claim');
            }
        }
        if (!isset($claims['exp']) || !is_int($claims['exp'])) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token has no expiry');
        }
        if ($claims['exp'] <= $now) {
            throw new ApiException(401, Codes::AUTH_EXPIRED,
                'access token has expired');
        }
        if (isset($claims['nbf']) && is_int($claims['nbf'])
            && $claims['nbf'] > $now + self::CLOCK_SKEW) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token is not valid yet');
        }
        if (isset($claims['iat']) && is_int($claims['iat'])
            && $claims['iat'] > $now + self::CLOCK_SKEW) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token was issued in the future');
        }
        return $claims;
    }

    /** A fresh 256-bit opaque token, hex encoded. */
    public static function newOpaque(): string
    {
        return bin2hex(random_bytes(32));
    }

    /** The stored form of an opaque token: plain SHA-256, 64 hex chars. */
    public static function hashOpaque(string $token): string
    {
        return hash('sha256', $token);
    }

    private static function b64(string $raw): string
    {
        return rtrim(strtr(base64_encode($raw), '+/', '-_'), '=');
    }

    private static function unb64(string $encoded): ?string
    {
        $padding = strlen($encoded) % 4;
        if ($padding === 2) {
            $encoded .= '==';
        } elseif ($padding === 3) {
            $encoded .= '=';
        } elseif ($padding === 1) {
            return null;
        }
        $decoded = base64_decode(strtr($encoded, '-_', '+/'), true);
        return $decoded === false ? null : $decoded;
    }

    /**
     * @return array<string, mixed>|null
     */
    private static function unb64json(string $encoded): ?array
    {
        $raw = self::unb64($encoded);
        if ($raw === null) {
            return null;
        }
        try {
            $value = json_decode($raw, true, 32, JSON_BIGINT_AS_STRING);
        } catch (\JsonException $error) {
            return null;
        }
        return is_array($value) ? $value : null;
    }
}
