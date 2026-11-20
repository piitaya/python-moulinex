"""Helpers for the HTTP calls to the SEB cloud."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import USER_AGENT
from .exceptions import (
    MoulinexAuthenticationError,
    MoulinexConnectionError,
    MoulinexError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@contextmanager
def wrap_errors(action: str) -> Iterator[None]:
    """Turn the errors of aiohttp into errors of this library."""
    try:
        yield
    except TimeoutError as err:
        msg = f"{action} timed out"
        raise MoulinexConnectionError(msg) from err
    except aiohttp.ClientError as err:
        msg = f"{action} failed: {err}"
        raise MoulinexConnectionError(msg) from err


@asynccontextmanager
async def own_session(
    session: aiohttp.ClientSession, *, cookies: bool = False
) -> AsyncIterator[aiohttp.ClientSession]:
    """Return a session on the connections of another one, with its own cookies.

    The cookies of the SEB cloud stay out of the session of the caller. They
    are only kept, for as long as this session lives, when asked for.
    """
    if session.closed or (connector := session.connector) is None:
        msg = "The session is closed"
        raise MoulinexConnectionError(msg)
    async with aiohttp.ClientSession(
        connector=connector,
        connector_owner=False,
        cookie_jar=aiohttp.CookieJar() if cookies else aiohttp.DummyCookieJar(),
        headers={"User-Agent": USER_AGENT},
    ) as own:
        yield own


def is_unavailable(status: int) -> bool:
    """Return whether a status tells that the cloud cannot answer right now."""
    return status == 429 or status >= 500


def status_error(status: int, message: str) -> MoulinexError:
    """Return the error for a response that is not a success."""
    if status in (401, 403):
        return MoulinexAuthenticationError(message)
    if is_unavailable(status):
        return MoulinexConnectionError(message)
    return MoulinexError(message)


async def read_text(resp: aiohttp.ClientResponse) -> str:
    """Return the body of a response, whatever its encoding says."""
    return await resp.text(errors="replace")


async def read_json(resp: aiohttp.ClientResponse, action: str) -> dict[str, Any]:
    """Return the JSON object of a response."""
    try:
        data = await resp.json(content_type=None)
    except ValueError as err:
        msg = f"{action} returned an unexpected response"
        raise MoulinexError(msg) from err
    if not isinstance(data, dict):
        msg = f"{action} returned an unexpected response"
        raise MoulinexError(msg)
    return data
