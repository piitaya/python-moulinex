"""AWS Cognito federation of SEB accounts."""

from __future__ import annotations

from typing import Any

import aiohttp

from ._http import own_session, read_json, wrap_errors
from .const import COGNITO_IDENTITY_POOL, COGNITO_PROVIDER, COGNITO_URL, DEFAULT_TIMEOUT
from .exceptions import (
    MoulinexAuthenticationError,
    MoulinexConnectionError,
    MoulinexError,
)
from .models import AwsCredentials

_TIMEOUT = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
_TEMPORARY_ERRORS = frozenset({"TooManyRequestsException", "InternalErrorException"})


async def fetch_credentials(
    session: aiohttp.ClientSession, id_token: str
) -> AwsCredentials:
    """Exchange an SEB id token for temporary AWS credentials.

    Raises:
        MoulinexAuthenticationError: Cognito refused the id token.
        MoulinexConnectionError: Cognito could not be reached.

    """
    logins = {COGNITO_PROVIDER: id_token}
    identity = await _call(
        session, "GetId", {"IdentityPoolId": COGNITO_IDENTITY_POOL, "Logins": logins}
    )
    try:
        credentials = (
            await _call(
                session,
                "GetCredentialsForIdentity",
                {"IdentityId": identity["IdentityId"], "Logins": logins},
            )
        )["Credentials"]
        return AwsCredentials(
            access_key=credentials["AccessKeyId"],
            secret_key=credentials["SecretKey"],
            session_token=credentials["SessionToken"],
            expiration=int(credentials["Expiration"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        msg = "Cognito returned an unexpected response"
        raise MoulinexError(msg) from err


async def _call(
    session: aiohttp.ClientSession, action: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Call an action of the Cognito identity service."""
    with wrap_errors(f"Cognito {action}"):
        async with (
            own_session(session) as own,
            own.post(
                COGNITO_URL,
                json=body,
                headers={
                    "Content-Type": "application/x-amz-json-1.1",
                    "X-Amz-Target": f"AWSCognitoIdentityService.{action}",
                },
                timeout=_TIMEOUT,
            ) as resp,
        ):
            if resp.status >= 500:
                msg = f"Cognito {action} failed ({resp.status})"
                raise MoulinexConnectionError(msg)
            data = await read_json(resp, f"Cognito {action}")
            if resp.status == 200:
                return data
            kind = str(data.get("__type", "")).rsplit("#", 1)[-1]
            message = f"Cognito {action} failed ({resp.status}): {kind}"
            if kind == "NotAuthorizedException":
                raise MoulinexAuthenticationError(message)
            if kind in _TEMPORARY_ERRORS:
                raise MoulinexConnectionError(message)
            raise MoulinexError(message)
