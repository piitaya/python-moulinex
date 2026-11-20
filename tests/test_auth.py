"""Tests for the login and the token refresh."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
import pytest

from aiomoulinex import (
    LoginError,
    LoginFailure,
    MoulinexAuthenticationError,
    MoulinexConnectionError,
    MoulinexError,
    Region,
    Tokens,
    login,
    refresh,
)

from .fake_cloud import EMAIL, PASSWORD, USER_ID, id_token

if TYPE_CHECKING:
    from .fake_cloud import FakeCloud


async def test_login(cloud: FakeCloud, session: aiohttp.ClientSession) -> None:
    """Test logging in with the email and password of the account."""
    tokens = await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)

    assert tokens == Tokens(
        access_token="access-0", id_token=id_token(), refresh_token="refresh-1"
    )
    assert tokens.user_id == USER_ID
    assert cloud.login_forms == [
        {
            "AJAXREQUEST": "_viewRoot",
            "loginPage:loginForm": "loginPage:loginForm",
            "com.salesforce.visualforce.ViewState": "state&1",
            "com.salesforce.visualforce.ViewStateVersion": "202611",
            "userEmail": EMAIL,
            "password": PASSWORD,
            "loginPage:loginForm:j_id7": "loginPage:loginForm:j_id7",
        }
    ]


async def test_login_leaves_the_session_alone(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
) -> None:
    """Test that the login keeps its cookies to itself, and the session open."""
    await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)

    assert len(session.cookie_jar) == 0
    assert not session.closed
    assert session.connector is not None
    assert not session.connector.closed
    await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


@pytest.mark.parametrize(
    ("flags", "reason"),
    [
        (("false", "false", "false"), LoginFailure.INVALID_AUTH),
        (("true", "false", "false"), LoginFailure.EMAIL_NOT_VERIFIED),
        (("false", "true", "false"), LoginFailure.ACCOUNT_LOCKED),
        (("false", "false", "true"), LoginFailure.PASSWORD_RESET_REQUIRED),
    ],
)
async def test_login_refused(
    cloud: FakeCloud,
    session: aiohttp.ClientSession,
    flags: tuple[str, str, str],
    reason: LoginFailure,
) -> None:
    """Test the reasons the cloud gives for refusing a login."""
    cloud.login_flags = flags

    with pytest.raises(LoginError) as error:
        await login(session, EMAIL, "wrong", Region.MOULINEX_FR)

    assert error.value.reason is reason
    assert str(error.value) == reason.value
    assert isinstance(error.value, MoulinexAuthenticationError)


async def test_login_unexpected_answer(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test a login page that answers something that is not understood."""
    cloud.login_body = "<html>Maintenance</html>"

    with pytest.raises(MoulinexError, match="Unexpected response") as error:
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)

    assert not isinstance(error.value, MoulinexAuthenticationError)


async def test_login_page_not_recognised(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a login page that changed."""
    monkeypatch.setattr("tests.fake_cloud.LOGIN_PAGE", "<html>New page</html>")

    with pytest.raises(MoulinexError, match="no loginAF"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_state_mismatch(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test that tokens meant for another login are not accepted."""
    cloud.login_state = "another"

    with pytest.raises(MoulinexError, match="state mismatch"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_without_all_tokens(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test a redirect to the app that misses a token."""
    cloud.redirect_fields = {"refresh_token": ""}

    with pytest.raises(MoulinexError, match="Missing 'refresh_token'"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_never_redirects_to_the_app(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a login that is accepted but ends on a page without tokens."""
    monkeypatch.setattr("tests.fake_cloud.LOGIN_FLOW_PAGE", "<html>Welcome</html>")

    with pytest.raises(MoulinexError, match="never redirected"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_redirects_forever(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test that a login that keeps redirecting gives up."""
    cloud.redirect_loop = True

    with pytest.raises(MoulinexError, match="never redirected"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_redirect_without_tokens(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test a redirect to the app that tells the login is not authorized."""
    cloud.app_redirect = "gsmoduser://tokens?error=access_denied"

    with pytest.raises(MoulinexError, match="No tokens"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_cloud_unreachable(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that a network error is an error of this library."""
    monkeypatch.setattr("aiomoulinex.auth.SF_BASE", "https://localhost:1")

    with pytest.raises(MoulinexConnectionError, match="The login failed"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_with_closed_session(cloud: FakeCloud) -> None:  # noqa: ARG001
    """Test a session that is already closed."""
    session = aiohttp.ClientSession()
    await session.close()

    with pytest.raises(MoulinexConnectionError, match="closed"):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_refresh(cloud: FakeCloud, session: aiohttp.ClientSession) -> None:
    """Test that the refresh token is kept when the cloud sends no new one."""
    tokens = await refresh(session, "refresh-1")

    assert tokens == Tokens(
        access_token="access-1", id_token=id_token(), refresh_token="refresh-1"
    )
    assert cloud.token_requests == 1


async def test_refresh_with_new_refresh_token(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test that a new refresh token replaces the old one."""
    cloud.next_refresh_token = "refresh-2"

    tokens = await refresh(session, "refresh-1")

    assert tokens.refresh_token == "refresh-2"


async def test_refresh_token_revoked(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
) -> None:
    """Test a refresh token that is expired or revoked."""
    with pytest.raises(MoulinexAuthenticationError, match="invalid_grant"):
        await refresh(session, "revoked")


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, {"error": "invalid_grant"}, MoulinexAuthenticationError),
        (401, {"error": "unauthorized"}, MoulinexError),
        (403, {"error": "blocked"}, MoulinexError),
        (400, {"error": "unsupported_grant_type"}, MoulinexError),
        (400, ["not", "an", "object"], MoulinexError),
        (429, {"error": "rate_limited"}, MoulinexConnectionError),
        (503, None, MoulinexConnectionError),
        (200, ["not", "an", "object"], MoulinexError),
        (200, {"access_token": "a"}, MoulinexError),
    ],
)
async def test_refresh_errors(
    cloud: FakeCloud,
    session: aiohttp.ClientSession,
    status: int,
    body: object,
    error: type[MoulinexError],
) -> None:
    """Test what the answers of the token endpoint are turned into."""
    cloud.token_status = status
    cloud.token_body = body

    with pytest.raises(MoulinexError) as raised:
        await refresh(session, "refresh-1")

    assert type(raised.value) is error


async def test_refresh_not_json(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test a token endpoint that answers a page."""
    cloud.token_text = "<html>Maintenance</html>"

    with pytest.raises(MoulinexError, match="unexpected response") as raised:
        await refresh(session, "refresh-1")

    assert type(raised.value) is MoulinexError


async def test_refresh_cloud_unreachable(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that a network error is an error of this library."""
    monkeypatch.setattr("aiomoulinex.auth.SF_BASE", "https://localhost:1")

    with pytest.raises(MoulinexConnectionError, match="The token refresh failed"):
        await refresh(session, "refresh-1")


async def test_refresh_keeps_cookies_out_of_the_session(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
) -> None:
    """Test that the cookies of the cloud do not end up in a shared session."""
    await refresh(session, "refresh-1")

    assert len(session.cookie_jar) == 0


async def test_refresh_with_closed_session(cloud: FakeCloud) -> None:  # noqa: ARG001
    """Test a session that is already closed."""
    session = aiohttp.ClientSession()
    await session.close()

    with pytest.raises(MoulinexConnectionError, match="closed"):
        await refresh(session, "refresh-1")


@pytest.mark.parametrize("path", ["/Login", "/frontdoor.jsp", "/apex/LoginFlow"])
@pytest.mark.parametrize("status", [429, 503])
async def test_login_cloud_unavailable(
    cloud: FakeCloud, session: aiohttp.ClientSession, path: str, status: int
) -> None:
    """Test that a login page that cannot answer is not taken for a refusal."""
    cloud.failures[path] = (status, b"<html>\xe9chec</html>")

    with pytest.raises(MoulinexConnectionError, match=str(status)):
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)


async def test_login_redirect_that_is_not_an_address(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test a redirect of the login that cannot be followed."""
    cloud.app_redirect = "http://["

    with pytest.raises(MoulinexError, match="not understood") as raised:
        await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)

    assert type(raised.value) is MoulinexError


async def test_refresh_timeout(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that a cloud that does not answer in time is a connection error."""
    monkeypatch.setattr(
        "aiomoulinex.auth._TIMEOUT", aiohttp.ClientTimeout(total=0.000001)
    )

    with pytest.raises(MoulinexConnectionError, match="timed out"):
        await refresh(session, "refresh-1")
