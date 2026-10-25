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
├── migrations/    001_initial.sql through 004_legacy_import_verification.sql
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

Fill in every `REPLACE_…` value under `db`. If the application account
is correctly DML-only, set `db_migrate` to a **complete second database
connection array** (`host`, `port`, `dbname`, `user`, `password`) for the
privileged account from step 1. Use `null` only when the `db` account
itself intentionally holds DDL privileges — not recommended.
Config refuses to load while a placeholder is still present, and it refuses
an all-zero `jwt_secret`.

Leave `smoke_probe_url` as `null` until step 9.

**If the document root lives outside the package** and the `log_path`
default cannot be created, set `log_path` to a directory the PHP user can
write, e.g. `/home/youruser/irica-host/var/log/api.log`. The logger creates
the directory itself (`0750`).

---

## 3.5. Shared hosting without SSH or Terminal access

If your hosting plan does not provide SSH, use the controlled setup page. It creates a **platform operator**, not a customer or a shared CRM administrator. Customer onboarding is a separate operator-panel workflow.

1. In the private `config/config.php`, set `operator_bootstrap_enabled` to the literal boolean `true` and set `setup_key` to a newly generated 64-character lowercase hexadecimal secret (`bin2hex(random_bytes(32))`). Do not reuse the JWT signing key.
2. Use native HTTPS. If a reverse proxy terminates TLS, list its exact IPv4/IPv6 peer addresses in `trusted_proxies`; it must overwrite `X-Forwarded-Proto` with a single scheme. Leave `allow_loopback_http` false on deployed hosts, including loopback-proxy deployments.
3. Open `https://api.<domain>/setup.php` **without any query string**. Enter the temporary secret in the password-type form field. A separate HttpOnly, SameSite=Strict setup cookie binds the CSRF token; HTTPS sets Secure. The setup session expires after 15 minutes.
4. Select **Run database migrations**, then **Create first platform operator** and enter the chosen operator username and matching password. Supplied passwords and setup secrets are never shown again. This operator cannot sign in to CRM or enroll a customer device.
5. Select **Check host health**. No shared tenant is required.
6. Immediately set `operator_bootstrap_enabled` to `false` and remove `setup_key`. The page returns 404 while disabled. Use a fresh secret for every later maintenance window.

Operator creation has a durable one-shot database gate shared with `bin/create-operator.php`. Disabling/deleting an operator, rotating the secret, rerunning migrations or reopening a maintenance window never rearms creation. Later deliberately authorized migrations still work with setup enabled, a fresh secret and CSRF. Exact-name operator recovery is host-controlled through `create-operator.php --reset-password`; it never promotes a customer.

Do not place passwords in a cron command or leave generated passwords in web-accessible output. CLI-only deployments may run `bin/migrate.php` and `bin/create-operator.php` directly, with private environment/redirected-STDIN input; see each command's `--help`.

---

## 4. Create the schema

For CLI-equipped deployments, run migrations from the shell. On no-SSH hosting use only the protected setup form in §3.5, never a secret-bearing URL:

```sh
cd /home/youruser/irica-host
php bin/migrate.php --status     # what is recorded so far
php bin/migrate.php              # applies every pending migration (001, 002, 003, ...)
php bin/migrate.php --status     # every applied migration has a matching checksum
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

## 5. Create a private customer administrator

Platform operators and customer administrators are separate identities. Use the
protected operator setup above for platform management. A host-controlled customer
CLI always creates a **new private dataset**, never a member of another account.

```sh
php bin/create-admin.php --username=customer-one --password-stdin
```

Redirect a password-manager supplied line into standard input; interactive echoed
input is refused. Alternatively supply `IRICA_ADMIN_PASSWORD` through a private
process environment, not a command containing the secret. Do not use both sources.
Passwords are not trimmed; they require at least 12 bytes, at most 4096 bytes
(72 on bcrypt-only PHP), and cannot contain a null byte. `--password=...` is
forbidden and rejected before database access.

If no password source is supplied, a random password is printed **once**, only
after successful creation. Supplied passwords and hashes are never printed.

The command is not single-use: each unused canonical username creates an isolated
owner/workspace. `--reset-password --username=EXACT-NAME` only rotates an existing
owner's password and revokes their user/device credentials. It never creates,
renames, promotes or activates an account. Optional role/slug arguments on reset
are identity constraints, not changes.

```sh
php bin/create-admin.php --help
```

## 6. Private accounts and desktop machines

Every customer login owns exactly one dataset. Different logins never share CRM
data, even when both have the tenant-local `admin` role. That role gives no
platform authority or whole-database backup access. Web onboarding gives each
customer the `admin` role within their own dataset.

`bin/create-user.php` is the equivalent host-controlled creation/recovery command
with a default tenant-local `user` role. Use `--role=admin` for full control of
that new private dataset. `--tenant-slug` must be unused; it cannot join a workspace
and configured `tenant_slug` is ignored. Username matching is case-insensitive
under the strict ASCII policy; a duplicate canonical name creates nothing.

```sh
php bin/create-user.php --username=customer-two --role=admin --password-stdin
```

The private environment alternative is `IRICA_USER_PASSWORD`. With neither an
environment password nor `--password-stdin`, a generated password is shown once.
The same password-argument, input-source and byte-limit rules apply to both CLIs.
Keep the host commands protected and available for exact-name recovery.

Multiple PCs using the **same customer login** synchronize that account's private
data. Each PC receives a distinct device ID and refresh-token family; revoking
one machine does not grant or revoke access to any other customer's workspace.
A second login is a second dataset, not another member of the first.

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
3. Remove shell access you do not need. The five `bin/` scripts are the
   supported CLI surface: migrate, create-admin, create-user, smoke-test
   and backup-to-telegram. They do not print credentials except the
   documented one-time generated account password during account setup.

---

## 9. Connect the desktop client

Set the panel's sync endpoint to `https://api.<domain>/v1` and log in with a
user account. On a new PC the client self-enrols a machine owned by that user;
the resulting device credential is separate from the user's browser/login
session. The same username may be active on multiple enrolled PCs at once.

For a genuinely empty server, the existing bootstrap path remains a one-shot
initial import (spec §8): it is accepted exactly once per tenant, replays
byte-for-byte on retry, and every later bootstrap import is answered
`409 DATASET_NOT_EMPTY`. After it lands, `tenant_state.bootstrap_status` is
`done` and `source_installation_id` records which installation supplied the
initial dataset.

Do **not** weaken or bypass that one-shot guard to merge old customer files
into a populated server. Use the reviewed Legacy Migration Center below.

---

## 9.5. Migrate a legacy local CRM database (v1.13.1 or older)

The browser migration center is for old local `crm_store.json` databases from
version **1.13.1 or older**, including stores that predate a formal version
marker. It is separate from `/bootstrap/import` and is safe to use when the
online tenant already contains CRM rows.

### Before opening the migration window

1. Apply every host migration and verify the install:
   ```sh
   php bin/migrate.php
   php bin/migrate.php --status
   php bin/smoke-test.php
   ```
2. Take a fresh server database backup and keep the original legacy JSON file
   unchanged. The legacy file contains customer PII; do not paste it into logs,
   tickets, chat rooms, or source control.
3. In `config/config.php`, temporarily enable the migration surface:
   ```php
   'legacy_import_enabled' => true,
   ```
   It is `false` in `config.example.php` by design. Both the page and every
   `/v1/legacy-import/*` API endpoint enforce this switch server-side.
4. Open `https://api.<domain>/legacy-import.php` over HTTPS and sign in with an
   **administrator** account. The page keeps the user access token in browser
   memory only; it does not use cookies, localStorage, or sessionStorage.

### Analyze and review

1. Select the old `crm_store.json`. The browser checks JSON syntax and the
   configured import size limit, then sends it to `POST /v1/legacy-import/analyze`.
2. Analyze is a dry run with respect to live CRM tables. The server normalizes
   the old schema, stores staged normalized records, records the source
   SHA-256, and classifies rows as create, skip, conflict, invalid, or pending.
   The raw uploaded JSON text is **not** stored in the database.
3. Check the summary counts and expand staged rows when you need to inspect the
   normalized source values. Exact or safe matches may already be classified as
   skip; uncertain matches are never silently merged.
4. Resolve every conflict or invalid row explicitly. Available review decisions
   are create, merge, skip, and reject where valid. Merge/skip requires a live
   target UUID. If the server found multiple candidates and therefore supplies
   no target, **do not guess a UUID**: independently verify the intended live
   record, or choose create/reject.
5. Financial transaction rows are append-safe. The legacy transaction sequence
   is not authoritative on a live server; apply locks the customer ledger and
   allocates the next server sequence instead of overwriting an existing entry.

### Apply, reconcile, and close the window

1. Apply is enabled only when the job has zero unresolved staged rows. The
   final write runs in one database transaction: accepted rows commit together
   or the database rolls the whole apply back on an invariant failure.
2. A completed job is replay-safe. Re-applying the same job, or analyzing the
   exact same source file again for the same tenant, does not duplicate the
   migration.
3. Before declaring success, reconcile the old source and online dataset:
   customer, owner, device/case, period and transaction counts; customer
   balances; case stages; customer-device and owner-device links; and done,
   closed and problem totals. Financial sums must match exactly after accounting
   for transactions that were deliberately rejected during review.
4. Immediately close the migration window in `config/config.php`:
   ```php
   'legacy_import_enabled' => false,
   ```
   Reload `https://api.<domain>/legacy-import.php`; it must now return `404`.
   The API routes are disabled by the same switch on their next request.

If a browser request is interrupted, reload the page, sign in again, and resume
with the migration job UUID shown in the review. Do not edit staging tables or
live CRM tables manually to "finish" an interrupted job.

---

## 10. Backups, restore and rotation

* **Back up the database nightly**, encrypted at rest, and *test a restore*.
  `backup-to-telegram.php` discovers every live base table from
  `information_schema` and includes database triggers, so operational
  tables introduced by migrations 002/004 and the device-ownership
  trigger from migration 003 are part of the dump. When `db_migrate` is
  configured, backup uses that privileged connection for controlled
  metadata reads while the runtime app account remains DML-only.
* **Restore into a separate database before trusting a backup.** Load the
  `.sql` dump, compare the source/restored base-table and trigger counts,
  verify representative CRM/financial rows, then run
  `php bin/migrate.php --status` against the restored database and apply
  only genuinely newer migrations. The schema is forward-only; there is
  no destructive down migration (spec §10.10).
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
| `401 AUTH_REQUIRED` on `/sync/*` | no live device credential, or the wrong token kind was sent | sign in again and let the client self-enrol its machine (`POST /sync-devices/self-enroll`) |
| `403 PERMISSION_DENIED` on `/legacy-import/*` | the temporary legacy migration surface is disabled | enable `legacy_import_enabled` only for a reviewed migration window, then turn it off again |
| `404` on `/legacy-import.php` | migration mode is disabled, or the page is not present in the deployed package | this is expected after migration; enable the switch only when intentionally migrating |
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
