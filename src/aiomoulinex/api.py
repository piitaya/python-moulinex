"""SEB cloud REST API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiohttp

from ._http import own_session, read_json, read_text, status_error, wrap_errors
from .const import DEFAULT_TIMEOUT, SEB_API_BASE, SEB_API_KEY, SEB_IOT_BASE
from .exceptions import MoulinexError
from .models import Profile

if TYPE_CHECKING:
    from .regions import Region

_TIMEOUT = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)


async def fetch_profile(
    session: aiohttp.ClientSession, access_token: str, region: Region
) -> Profile:
    """Fetch the profile of the account, including its appliances.

    Raises:
        MoulinexAuthenticationError: The access token is refused or expired.
        MoulinexConnectionError: The SEB cloud could not be reached.

    """
    return await fetch_profile_of_perimeter(
        session, access_token, region.profile_perimeter
    )


async def fetch_profile_of_perimeter(
    session: aiohttp.ClientSession, access_token: str, profile_perimeter: str
) -> Profile:
    """Fetch the profile of the account from the name of its region in the cloud."""
    with wrap_errors("Fetching the profile"):
        async with (
            own_session(session) as own,
            own.get(
                f"{SEB_API_BASE}/common-api/profiles/me",
                headers={
                    "ApiKey": SEB_API_KEY,
                    "Authorization": f"Bearer {access_token}",
                    "Grant-Type": "RCUToken",
                    "DCP-Context": profile_perimeter,
                    "Accept": "application/json",
                },
                timeout=_TIMEOUT,
            ) as resp,
        ):
            data = await _json(resp, "Fetching the profile")
    try:
        return Profile.from_json(data)
    except (AttributeError, KeyError, TypeError) as err:
        msg = "The SEB cloud returned a profile that is not understood"
        raise MoulinexError(msg) from err


async def attach_policies(
    session: aiohttp.ClientSession, id_token: str, thing_type: str
) -> None:
    """Give the Cognito identity of the account access to the MQTT topics.

    Raises:
        MoulinexAuthenticationError: The id token is refused or expired.
        MoulinexConnectionError: The SEB cloud could not be reached.

    """
    with wrap_errors("Attaching the IoT policies"):
        async with (
            own_session(session) as own,
            own.post(
                f"{SEB_IOT_BASE}/entity/v1/update",
                json={"openIdToken": id_token, "thingType": thing_type},
                headers={"ApiKey": SEB_API_KEY},
                timeout=_TIMEOUT,
            ) as resp,
        ):
            # What it answers is of no use, only that it accepts.
            await _check(resp, "Attaching the IoT policies")


async def _check(resp: aiohttp.ClientResponse, action: str) -> None:
    """Raise the error of a response that is not a success."""
    if not 200 <= resp.status < 300:
        raise status_error(
            resp.status,
            f"{action} failed ({resp.status}): {(await read_text(resp))[:200]}",
        )


async def _json(resp: aiohttp.ClientResponse, action: str) -> dict[str, Any]:
    """Return the JSON body of a successful response."""
    await _check(resp, action)
    return await read_json(resp, action)
