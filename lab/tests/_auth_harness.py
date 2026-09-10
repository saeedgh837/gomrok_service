"""Shared login-gate helper for HTTP-level panel tests (v1.2.0).

Boots the panel with the gate active but installs a valid session cookie into
every outgoing request by wrapping urllib.request.urlopen inside the test's
setUp — so existing call sites stay untouched while the real auth path
(gate + session validation) is exercised end to end.
"""
import panel_auth


def seed_test_users():
    """Isolated users file with the three seeded accounts; returns the token."""
    panel_auth.seed_default_users()
    return panel_auth.create_session("saeed")


def install_auth_cookie(testcase, token):
    """Patch urllib.request.urlopen for the duration of the test so every
    request carries the session cookie."""
    import urllib.request
    from unittest.mock import patch

    original = urllib.request.urlopen

    def urlopen_with_cookie(url, *args, **kwargs):
        if isinstance(url, str):
            url = urllib.request.Request(url)
        if hasattr(url, "headers") and \
                "irica_session=" not in str(url.headers.get("Cookie")):
            url.add_header("Cookie", "irica_session=" + token)
        return original(url, *args, **kwargs)

    patcher = patch.object(urllib.request, "urlopen", urlopen_with_cookie)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return original
