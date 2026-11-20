"""SEB account login and token refresh."""

from __future__ import annotations

import html
import json
import logging
import re
import secrets
from typing import TYPE_CHECKING
import urllib.parse

import aiohttp
from yarl import URL

from ._http import is_unavailable, own_session, read_json, read_text, wrap_errors
from .const import DEFAULT_TIMEOUT, SF_BASE, SF_CLIENT_ID, SF_REDIRECT_URI
from .exceptions import (
    LoginError,
    LoginFailure,
    MoulinexAuthenticationError,
    MoulinexConnectionError,
    MoulinexError,
)
from .models import Tokens

if TYPE_CHECKING:
    from .regions import Region

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)

_FORM_RE = re.compile(r'<form id="([^"]+)"[^>]*\saction="([^"]+)"')
_A4J_ACTION_RE = (
    r"{}=function\([^)]*\)\{{A4J\.AJAX\.Submit\('[^']+',null,"
    r"\{{'similarityGroupingId':'([^']+)'"
)
_VIEW_STATE_RE = re.compile(
    r'name="(com\.salesforce\.visualforce\.\w+)" value="([^"]*)"'
)
_LOGIN_RESULT_RE = re.compile(r"afterLoginAttempt\((\w+),\s*(\w+),\s*(\w+)\)")
_JS_REDIRECT_RE = re.compile(r"window\.location\.replace\(([\"'])(.+?)\1\)")
_REDIRECT_URI_RE = re.compile(r"gsmoduser://tokens#[^\s'\"]+")
# Same order as the arguments of afterLoginAttempt().
_LOGIN_FAILURES = (
    LoginFailure.EMAIL_NOT_VERIFIED,
    LoginFailure.ACCOUNT_LOCKED,
    LoginFailure.PASSWORD_RESET_REQUIRED,
)
_LOGIN_FLOW_ACTION = "finishLoginFlowAndRedirectJS"
_MAX_REDIRECTS = 10


async def login(
    session: aiohttp.ClientSession, email: str, password: str, region: Region
) -> Tokens:
    """Log in with the SEB account email and password.

    The cookies of the login page stay out of the cookie jar of the session.

    Raises:
        LoginError: The SEB cloud refused the email and password.
        MoulinexConnectionError: The SEB cloud could not be reached.

    """
    state = secrets.token_urlsafe(16)
    # SEB disables the password grant, so replay the login page of the mobile app.
    with wrap_errors("The login"):
        async with own_session(session, cookies=True) as web:
            async with web.get(_authorize_url(region, state), timeout=_TIMEOUT) as resp:
                login_url = resp.url
                page = await _page(resp)
            location, body = await _a4j_submit(
                web,
                login_url,
                page,
                "loginAF",
                {"userEmail": email, "password": password},
            )
            if location is None:
                raise _login_error(body)
            redirect = await _follow_to_app(web, _join(login_url, location))
    return _parse_redirect(redirect, state)


async def refresh(session: aiohttp.ClientSession, refresh_token: str) -> Tokens:
    """Get new tokens from a refresh token.

    Raises:
        MoulinexAuthenticationError: The refresh token is refused or revoked.
        MoulinexConnectionError: The SEB cloud could not be reached.

    """
    with wrap_errors("The token refresh"):
        async with (
            own_session(session) as own,
            own.post(
                f"{SF_BASE}/services/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": SF_CLIENT_ID,
                },
                timeout=_TIMEOUT,
            ) as resp,
        ):
            if resp.status != 200:
                raise _refresh_error(resp.status, await read_text(resp))
            data = await read_json(resp, "The token refresh")
    try:
        return Tokens(
            access_token=data["access_token"],
            id_token=data["id_token"],
            refresh_token=data.get("refresh_token", refresh_token),
        )
    except KeyError as err:
        msg = f"Missing {err} in the refreshed tokens"
        raise MoulinexError(msg) from err


def _authorize_url(region: Region, state: str) -> URL:
    """Return the address of the login page of a region."""
    expid = f"expid_{region.brand}000{region.market}000{region.language}000true"
    return URL(f"{SF_BASE}/services/oauth2/authorize/{expid}").with_query(
        client_id=SF_CLIENT_ID,
        redirect_uri=SF_REDIRECT_URI,
        response_type="token id_token refresh_token",
        state=state,
        nonce=secrets.token_urlsafe(16),
    )


def _refresh_error(status: int, body: str) -> MoulinexError:
    """Return why the token endpoint did not give new tokens."""
    message = f"Token refresh failed ({status}): {body[:200]}"
    try:
        error = json.loads(body).get("error")
    except (AttributeError, ValueError):
        error = None
    # What Salesforce answers to a refresh token that is expired or revoked.
    # Any other refusal, like a blocked address, does not ask for a new login.
    if error == "invalid_grant":
        return MoulinexAuthenticationError(message)
    if is_unavailable(status):
        return MoulinexConnectionError(message)
    return MoulinexError(message)


def _login_error(body: str) -> MoulinexError:
    """Return why the login page did not let the account in."""
    if (result := _LOGIN_RESULT_RE.search(body)) is None:
        return MoulinexError("Unexpected response from the SEB login page")
    return LoginError(
        next(
            (
                reason
                for reason, flag in zip(_LOGIN_FAILURES, result.groups(), strict=True)
                if flag == "true"
            ),
            LoginFailure.INVALID_AUTH,
        )
    )


async def _a4j_submit(
    web: aiohttp.ClientSession,
    page_url: URL,
    page: str,
    action: str,
    params: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """Replay a Visualforce ``A4J.AJAX.Submit`` call, return the redirect and body."""
    form = _FORM_RE.search(page)
    action_match = re.search(_A4J_ACTION_RE.format(re.escape(action)), page)
    if form is None or action_match is None:
        msg = f"SEB page not recognised (no {action})"
        raise MoulinexError(msg)
    form_id, action_id = form.group(1), action_match.group(1)
    data = {
        "AJAXREQUEST": "_viewRoot",
        form_id: form_id,
        **{k: html.unescape(v) for k, v in _VIEW_STATE_RE.findall(page)},
        **(params or {}),
        action_id: action_id,
    }
    post_url = _join(page_url, html.unescape(form.group(2)))
    async with web.post(
        post_url, data=data, allow_redirects=False, timeout=_TIMEOUT
    ) as resp:
        _LOGGER.debug(
            "Login submit: %s %s%s", resp.status, post_url.host, post_url.path
        )
        return resp.headers.get("Location"), await _page(resp)


async def _follow_to_app(web: aiohttp.ClientSession, url: URL) -> str:
    """Follow the HTTP and JavaScript redirects up to the app redirect URI."""
    for _ in range(_MAX_REDIRECTS):
        async with web.get(url, allow_redirects=False, timeout=_TIMEOUT) as resp:
            _LOGGER.debug("Login redirect: %s %s%s", resp.status, url.host, url.path)
            location = resp.headers.get("Location")
            body = await _page(resp)
        if location is None and (script := _JS_REDIRECT_RE.search(body)):
            location = script.group(2)
        if location is None and _LOGIN_FLOW_ACTION in body:
            location, _ = await _a4j_submit(web, url, body, _LOGIN_FLOW_ACTION)
        if location is None:
            break
        if location.startswith(SF_REDIRECT_URI):
            return location
        url = _join(url, location)
    msg = "Login accepted but SEB never redirected to the app"
    raise MoulinexError(msg)


async def _page(resp: aiohttp.ClientResponse) -> str:
    """Return a page of the login, unless the cloud cannot answer right now."""
    if is_unavailable(resp.status):
        msg = f"The SEB login page is not available ({resp.status})"
        raise MoulinexConnectionError(msg)
    return await read_text(resp)


def _join(base: URL, location: str) -> URL:
    """Return the address a page of the login points to."""
    try:
        return base.join(URL(location, encoded=True))
    except (TypeError, ValueError) as err:
        msg = "The SEB login page points to an address that is not understood"
        raise MoulinexError(msg) from err


def _parse_redirect(redirect: str, state: str) -> Tokens:
    """Extract the tokens from the ``gsmoduser://tokens#...`` redirect."""
    if (match := _REDIRECT_URI_RE.search(redirect)) is None:
        msg = "No tokens in the SEB redirect"
        raise MoulinexError(msg)
    fields = dict(urllib.parse.parse_qsl(match.group(0).split("#", 1)[1]))
    if fields.get("state") != state:
        msg = "OAuth state mismatch"
        raise MoulinexError(msg)
    try:
        return Tokens(
            access_token=fields["access_token"],
            id_token=fields["id_token"],
            refresh_token=fields["refresh_token"],
        )
    except KeyError as err:
        msg = f"Missing {err} in the SEB redirect"
        raise MoulinexError(msg) from err
