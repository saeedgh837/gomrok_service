"""Shared login-gate helper for HTTP-level panel tests (v1.2.0).

Boots the panel with the gate active but installs a valid session cookie into
every outgoing request by wrapping urllib.request.urlopen inside the test's
setUp — so existing call sites stay untouched while the real auth path
(gate + session validation) is exercised end to end.

The same wrapper is also the suite's single place for the CSRF header: it
reads the token the server issued for that session and sends it as
X-CSRF-Token, which is what the panel's own page JS does through the
irica_csrf cookie. Every HTTP test in the suite goes through urlopen, so no
call site has to know the header exists.
"""
import panel_auth


def seed_test_users():
    """Isolated users file with the three seeded accounts; returns the token."""
    panel_auth.seed_default_users()
    return panel_auth.create_session("saeed")


def install_auth_cookie(testcase, token):
    """Patch urllib.request.urlopen for the duration of the test so every
    request carries the session cookie and the session's CSRF token."""
    import urllib.request
    from unittest.mock import patch

    original = urllib.request.urlopen
    csrf = panel_auth.session_csrf(token)

    def urlopen_with_cookie(url, *args, **kwargs):
        if isinstance(url, str):
            url = urllib.request.Request(url)
        if hasattr(url, "headers"):
            if "irica_session=" not in str(url.headers.get("Cookie")):
                url.add_header("Cookie", "irica_session=" + token)
            if csrf and not url.headers.get("X-CSRF-Token"):
                url.add_header("X-CSRF-Token", csrf)
        return original(url, *args, **kwargs)

    patcher = patch.object(urllib.request, "urlopen", urlopen_with_cookie)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return original
