<?php
/** Create a private account with a user membership; never join another account. */
declare(strict_types=1);

require_once __DIR__ . '/_account-command.php';
runPrivateAccountCommand('create-user.php', 'user', 'IRICA_USER_PASSWORD', true);
