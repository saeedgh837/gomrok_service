<?php
/** Create a private administrator account, or rotate an exact owner's credentials. */
declare(strict_types=1);

require_once __DIR__ . '/_account-command.php';
runPrivateAccountCommand('create-admin.php', 'admin', 'IRICA_ADMIN_PASSWORD', false);
