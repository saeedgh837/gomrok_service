# IRICA cloud-sync host — installation (cPanel)

This directory is the API Saeed runs on his own hosting. It is plain PHP 8.1
with one front controller: no Composer, no framework, no build step. Follow
the steps in order; each one ends with something you can check.

The contract for everything below is `lab/docs/CLOUD_SYNC_SPEC.md` —
§2 topology, §6 HTTP API, §7 roles, §8 import, §10 security. The security
rationale lives in [`SECURITY.md`](SECURITY.md); read it before you change any
code in this package.

---

## 0. What you need before starting

| Requirement | Why |
|---|---|
| PHP **8.1 or newer** with `pdo_mysql`, `mbstring`, `json`, `hash` | `smoke-test.php` fails without them |
| `openssl`, `sodium`, `fileinfo` (recommended) | TLS and password hardening |
| `password_algos()` containing `argon2id` (recommended) | spec §10.4; falls back to `PASSWORD_DEFAULT` otherwise |
| Apache with `mod_rewrite` and `mod_headers` | `public/.htaccess` routes every request and strips CORS |
| MySQL 5.7+/MariaDB 10.3+ reachable on `127.0.0.1` only | spec §2 |
| A subdomain with a valid certificate (`api.<domain>`) | spec §2 — TLS is mandatory |
| SSH or cPanel **Terminal** access | the four `bin/` tools are CLI only |

Check PHP from the shell cPanel gives you:

```sh
php -v                      # must be 8.1+
php -m | egrep 'pdo_mysql|mbstring|openssl|sodium|fileinfo'
php -r 'var_dump(in_array("argon2id", password_algos()));'
```

If `pdo_mysql` is missing, enable it in cPanel → *Select PHP Version* →
*Extensions* (or ask your host). Everything below assumes the `php` on your
PATH is the same PHP that will serve the site — `smoke-test.php` will tell you
if they differ.

---

## 1. Create the database and two accounts

cPanel → **MySQL Databases**. Create:

1. A database, e.g. `youruser_irica`. Character set **utf8mb4**,
   collation **utf8mb4_unicode_ci**.
2. An **application** user, e.g. `youruser_irica_app`, with a long generated
   password. Grant it **SELECT, INSERT, UPDATE, DELETE** on that database
   only — **never** `CREATE`, `ALTER`, `DROP` (spec §2, §10.6).
3. Note the **privileged** credentials you will use for the migration only —
   usually the `youruser_` cPanel MySQL admin account. This account exists
   only for step 5; it is never written to `config.php` as the app account.

cPanel does not expose a "no DDL" grant screen in every theme; if yours only
offers *All Privileges*, create the user anyway and verify the restriction
later — `smoke-test.php` refuses to pass a run where the app account can
`CREATE`, and it performs a real `CREATE TABLE` probe, not a permission guess.

MySQL must listen on localhost. If it is exposed publicly, that is outside
this package: fix it at the server level before continuing.

---

## 2. Upload the package

Upload the whole `host/` folder **outside** `public_html`, e.g.

```
/home/youruser/irica-host/
├── bin/           migrate.php, create-admin.php, create-user.php, smoke-test.php
├── config/        config.php goes here (you create it in step 3)
├── migrations/    001_initial.sql
├── public/        ← the ONLY web-reachable directory
├── src/           PHP sources
└── INSTALL.md  SECURITY.md
```

Fix ownership and permissions once:

```sh
cd /home/youruser/irica-host
find . -type d -exec chmod 755 {} +
find . -type f -exec chmod 644 {} +
chmod 700 bin
chmod 600 config            # only your account may read config.php later
```

`config/`, `src/`, `migrations/` and `bin/` must **not** be under
`public_html`. `.htaccess` denies them anyway (spec §10.8), but keeping them
outside the document root means a misconfigured vhost still cannot serve them.

---

## 3. Point the domain at `public/` and write the config

**Document root.** cPanel → *Domains* / *MultiPHP Manager* → set the
`api.<domain>` document root to `/home/youruser/irica-host/public`.

**TLS.** cPanel → *SSL/TLS Status* → run AutoSSL for `api.<domain>`, or
install a Let's Encrypt certificate. HTTP must redirect to HTTPS;
`public/.htaccess` does that redirect for you (loopback is exempt so the
health probe can run locally).

**Config file.** Copy the template and edit it:

```sh
cd /home/youruser/irica-host
cp config/config.example.php config/config.php
php -r 'echo bin2hex(random_bytes(32)), PHP_EOL;'   # paste into jwt_secret
chmod 600 config/config.php
```

Fill in every `REPLACE_…` value: `db.host`, `db.port`, `db.dbname`,
`db.user`, `db.password`, and `db_migrate` (the privileged account from
step 1, or `null` if you knowingly gave the app account DDL — don't).
Config refuses to load while a placeholder is still present, and it refuses
an all-zero `jwt_secret`.

Leave `smoke_probe_url` as `null` until step 9.

**If the document root lives outside the package** and the `log_path`
default cannot be created, set `log_path` to a directory the PHP user can
write, e.g. `/home/youruser/irica-host/var/log/api.log`. The logger creates
the directory itself (`0750`).

---

## 3.5. Shared hosting without SSH or Terminal access

If your cPanel plan does not provide SSH or Terminal access, you have two safe options to run migrations, create the admin user, and verify the host:

### Option A: Web Setup Tool (`setup.php`) — Recommended for non-technical setup

1. In `config/config.php`, set a strong random secret in `setup_key`:
   ```php
   'setup_key' => 'YOUR_RANDOM_LONG_SECRET_KEY',
   ```
2. Open your browser and navigate to:
   ```
   https://api.<domain>/setup.php?key=YOUR_RANDOM_LONG_SECRET_KEY
   ```
3. Use the web interface to:
   - Run database migrations (creates all 18 tables).
   - Create the initial admin user.
   - Run the smoke test to verify extensions and database connectivity.
   - Click **"حذف خودکار فایل setup.php"** to permanently delete the setup script from the server once finished.

### Option B: Temporary cPanel Cron Job

cPanel → **Cron Jobs**:
1. Schedule a job to run once (e.g. `* * * * *`):
   ```sh
   /usr/local/bin/php /home/youruser/irica-host/bin/migrate.php > /home/youruser/migrate.log 2>&1
   ```
2. Check `migrate.log` in cPanel File Manager to confirm migration status.
3. Run the admin creation cron:
   ```sh
   /usr/local/bin/php /home/youruser/irica-host/bin/create-admin.php --username=admin > /home/youruser/admin.log 2>&1
   ```
4. Check `admin.log` to retrieve the generated admin password.
5. Delete the cron jobs immediately from cPanel.

---

## 4. Create the schema

Run migrations from the shell, **never** from a URL:

```sh
cd /home/youruser/irica-host
php bin/migrate.php --status     # what is recorded so far
php bin/migrate.php              # applies 001_initial.sql
php bin/migrate.php --status     # one row, checksum matches the file
```

Behaviour you can rely on:

* a migration already recorded with the same checksum is **skipped**;
* a recorded checksum that does not match the file is a **hard refusal**
  (exit 2) — migrations are forward-only, there is no `down.sql`;
* a database that already has tables but no `schema_migrations` row is
  **refused** rather than written into (exit 2). If you are adopting an
  existing schema, that is a manual decision — record what you did.

`migrate.php` reads `db_migrate` when it is set, otherwise `db`. It must be
the account that holds `CREATE`.

---

## 5. Create the first administrator

```sh
cd /home/youruser/irica-host
IRICA_ADMIN_PASSWORD='…' php bin/create-admin.php --username=admin
```

* If `IRICA_ADMIN_PASSWORD` is absent a random one is generated and printed
  **once** — copy it immediately; it is never recoverable from the database.
* The password never appears on the command line (shell history) and the
  hash is never printed.
* The script is **single-use**: it refuses while an admin membership already
  exists, and tells you to use `--reset-password` instead.
* `--reset-password` bumps the user's `token_version` and revokes every live
  session for that user, which is the correct way to force a re-login.

```
php bin/create-admin.php --help
```

This is the account the desktop client logs in with; it is what mints the
first enrolled device (spec §7: enrolment is admin-only).

---

## 6. Create one account per colleague

`create-admin.php` is single-use by design, so it is not how a second person
gets in. **Every colleague gets their own username** — one account per person,
never a shared login — created by `bin/create-user.php`, the CLI that spec §15
makes the only way to add an account (there is deliberately no HTTP endpoint
for it):

```sh
cd /home/youruser/irica-host
IRICA_USER_PASSWORD='…' php bin/create-user.php \
    --username=amir --display-name='Amir' --role=user
php bin/create-user.php --username=sara --display-name='Sara' --role=user
php bin/create-user.php --help
```

* `--role=user` is the **non-administrator**: it reads everything and creates
  and edits cases, but it may not write `settings`, open or close a period,
  delete a record, enrol a device or run an import (spec §7). Use
  `--role=admin` only for somebody who really administers the installation.
* With no `--password=` and no `IRICA_USER_PASSWORD`, a random password is
  generated and printed **once** — record it in the password manager
  immediately, before it scrolls away; it exists nowhere else and no hash is
  ever printed. Prefer the environment form over `--password=` so the secret
  stays out of your shell history.
* A username that already exists — including `admin` — is refused with exit 1
  and creates nothing.
* Keep `create-user.php` for as long as the installation runs: it is the only
  supported way to add an account later. `create-admin.php` is the bootstrap
  and the password-rotation path, and can be deleted after step 5.

---

## 7. Verify the install

```sh
cd /home/youruser/irica-host
php bin/smoke-test.php
```

`Result: PASS` means: PHP and its extensions are adequate, `config.php`
exists, is not the shipped example, and carries a usable `jwt_secret`, the
log directory is writable, the database answers, the recorded migration
checksum matches the file, the app account holds exactly the DML grants and
**empirically cannot** run DDL, the tenant exists, `public/index.php` does not
emit CORS and does enforce the HTTPS gate, and `.htaccess` denies
`migrations/`, `config/` and `bin/`.

`WARN` on the live HTTP probe simply means `smoke_probe_url` is still `null`.

Then check the service itself:

```sh
curl -s https://api.<domain>/v1/health/live
# {"ok":true,"time":"2026-10-05T09:00:00+00:00"}
curl -sI http://api.<domain>/v1/health/live | head -1
# HTTP/1.1 301  (loopback excepted, everything else must be redirected)
```

Only `ok` and `time` come back — the liveness probe deliberately leaks no
version, no configuration and no dependency detail (spec §6).

---

## 8. Lock it down

1. `chmod 600 config/config.php` and confirm the file is not reachable:
   `curl -sI https://api.<domain>/config/config.php` → `403` or `404`.
   Same for `/src/`, `/migrations/`, `/bin/`, and `/.git`.
2. If your PHP runs as CGI/FPM rather than `mod_php`, `.htaccess`
   `php_flag`/`php_value` directives are ignored — put the same settings
   (`display_errors off`, `log_errors on`, `upload_max_filesize 16M`,
   `post_max_size 17M`) in a `.user.ini` in `public/` or in the per-domain
   PHP ini. `smoke-test.php` and `public/index.php` both set
   `display_errors = 0` in code as well, so a missing directive degrades
   safely rather than exposing a stack trace.
3. Remove shell access you do not need. The four `bin/` scripts are the
   only CLI surface; they print no credential.

---

## 9. Connect the desktop client

Set the panel's sync endpoint to `https://api.<domain>/v1` and log in with
the administrator from step 5. The first push from an empty host is the
one-shot import (spec §8): it is accepted exactly once per tenant, replays
byte-for-byte on retry, and every later import is answered
`409 DATASET_NOT_EMPTY`. After it lands, `tenant_state.bootstrap_status`
is `done` and `source_installation_id` records which installation the data
came from.

---

## 10. Backups, restore and rotation

* **Back up the database nightly**, encrypted at rest, and *test a restore*.
  The schema is forward-only: the restore path is "load the dump, then run
  `php bin/migrate.php --status` and apply anything newer" — there is no
  rollback script by design (spec §10.10).
* Include `config/config.php` in the encrypted backup. Losing it is
  recoverable (you have the DB dump and the procedures below); leaking it is not.
* **Rotation:**
  * `jwt_secret` — rotating invalidates every access token at once (they are
    HS256-signed with it). Do it in a maintenance window; devices recover on
    their next refresh **only if** the refresh-token hashes still match, which
    they do, because refresh tokens are opaque and stored as SHA-256 hashes
    that do not depend on the key. See `SECURITY.md` for the full design.
  * Admin password — `create-admin.php --reset-password`, which also revokes
    that user's sessions.
  * App DB password — change it in MySQL and in `config.php`, then re-run
    `smoke-test.php`.
* **Log retention:** `var/log/api.log` contains no secret and no PII by
  construction (see `SECURITY.md`), but it is still operational data — rotate
  it with the host's logrotate and keep the retention window short.

---

## 11. Upgrades

```sh
# 1. back up first (dump + config)
# 2. upload the new package beside the old one, then swap directories
cd /home/youruser
mv irica-host irica-host.prev && mv irica-host.new irica-host
# 3. config.php is yours, not the package's — copy it across
cp irica-host.prev/config/config.php irica-host/config/config.php
# 4. migrate, then verify
php irica-host/bin/migrate.php
php irica-host/bin/smoke-test.php
# 5. keep the previous directory until the client has pushed once
```

If step 4 fails, swap the directories back — no destructive step exists, so a
rollback is just putting the old folder and the old dump in place.

---

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `500` with an empty body | `config.php` missing, placeholder left in it, or an unusable `jwt_secret`; `display_errors` is correctly off | read `var/log/api.log` — the line carries `code: CONFIG_INVALID` and names the key, never its value |
| `403 HTTPS_REQUIRED` on every request | `.htaccess` cannot see `HTTPS=on` or `X-Forwarded-Proto` | TLS is not terminating in front of Apache, or a proxy is not setting the header |
| `404 NOT_FOUND` on a path that looks right | `base_path` mismatch, or the document root is not `public/` | match the config to the URL: `base_path` `/v1` → `/v1/...` |
| `415 UNSUPPORTED_MEDIA_TYPE` | the client did not send `application/json` | proxy or client stripping `Content-Type` |
| `429 RATE_LIMITED` with `Retry-After` | deliberate, temporary (spec §10.5) | wait for the header; it always releases |
| `401 AUTH_REQUIRED` on `/sync/*` | no device token, or a device token where a user token is required | the client must enroll first (`POST /sync-devices/enroll`) |
| `409 DATASET_NOT_EMPTY` on import | the tenant already holds rows (spec §8) | this is correct after the first import; do not empty the tables to retry |
| `smoke-test` says the account can `CREATE` | the cPanel grant screen gave it all privileges | revoke DDL in MySQL, then re-run |
| `migrate.php` exits 2 on checksum | the migration file changed after it was applied | never edit an applied migration; add a new one |

Every failure envelope looks the same and is safe to log or paste:

```json
{"ok":false,"error":{"code":"STABLE_CODE","message":"operator text","request_id":"<uuid>"}}
```

`code` is matched by the client (which renders Persian); `message` is for the
operator and is written to contain **no password, token, national id, phone
number or name** (spec §10.9). Always quote `request_id` in a bug report — it
joins to the log line without exposing anything else.
