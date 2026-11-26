"""Tests for the REST API and Cognito."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
import pytest

from aiomoulinex import (
    AwsCredentials,
    MoulinexAuthenticationError,
    MoulinexConnectionError,
    MoulinexError,
    Region,
    attach_policies,
    fetch_credentials,
    fetch_profile,
)
from aiomoulinex.const import SEB_API_KEYS

from .conftest import COMPANION, COOKEO, load_fixture
from .fake_cloud import ACCESS_KEY, SECRET_KEY, SESSION_TOKEN, id_token

if TYPE_CHECKING:
    from .fake_cloud import FakeCloud


async def test_fetch_profile(cloud: FakeCloud, session: aiohttp.ClientSession) -> None:
    """Test fetching the appliances of an account."""
    cloud.profile = load_fixture("profile_two_appliances.json")

    profile = await fetch_profile(session, "access-0", Region.MOULINEX_FR)

    assert profile.profile_perimeter == "MOULINEX_FR"
    assert profile.appliances == (COOKEO, COMPANION)
    assert cloud.profile_perimeters == ["MOULINEX_FR"]


async def test_fetch_profile_access_token_refused(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
) -> None:
    """Test an access token that is expired."""
    with pytest.raises(MoulinexAuthenticationError, match="401"):
        await fetch_profile(session, "expired", Region.MOULINEX_FR)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, MoulinexAuthenticationError),
        (404, MoulinexError),
        (429, MoulinexConnectionError),
        (500, MoulinexConnectionError),
    ],
)
async def test_fetch_profile_errors(
    cloud: FakeCloud,
    session: aiohttp.ClientSession,
    status: int,
    error: type[MoulinexError],
) -> None:
    """Test what the answers of the cloud are turned into."""
    cloud.profile_status = status

    with pytest.raises(MoulinexError, match="Oops") as raised:
        await fetch_profile(session, "access-0", Region.MOULINEX_FR)

    assert type(raised.value) is error


@pytest.mark.parametrize(
    "profile",
    [
        ["not", "an", "object"],
        {
            "household": {
                "products": [{"iot": {"thingType": "C"}, "domains": ["PRO_COO"]}]
            }
        },
        {"household": {"products": ["Cookeo"]}},
    ],
)
async def test_fetch_profile_not_understood(
    cloud: FakeCloud, session: aiohttp.ClientSession, profile: object
) -> None:
    """Test a profile in a shape this library does not know."""
    cloud.profile = profile  # type: ignore[assignment]

    with pytest.raises(MoulinexError) as raised:
        await fetch_profile(session, "access-0", Region.MOULINEX_FR)

    assert type(raised.value) is MoulinexError


async def test_fetch_profile_cloud_unreachable(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that a network error is an error of this library."""
    monkeypatch.setattr("aiomoulinex.api.SEB_API_BASE", "https://localhost:1")

    with pytest.raises(MoulinexConnectionError, match="Fetching the profile failed"):
        await fetch_profile(session, "access-0", Region.MOULINEX_FR)


async def test_attach_policies(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test giving the account access to the topics of a kind of appliance."""
    await attach_policies(session, "id-token", "COO001")

    assert cloud.policies == [{"openIdToken": "id-token", "thingType": "COO001"}]


async def test_attach_policies_refused(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test an id token that is expired."""
    cloud.policies_status = 401

    with pytest.raises(MoulinexAuthenticationError):
        await attach_policies(session, "id-token", "COO001")


async def test_fetch_credentials(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test exchanging an id token for AWS credentials."""
    cloud.credentials_lifetime = 3600

    credentials = await fetch_credentials(session, id_token())

    assert credentials == AwsCredentials(
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        session_token=SESSION_TOKEN,
        expiration=credentials.expiration,
    )
    assert isinstance(credentials.expiration, int)
    logins = {"account.groupeseb.com": id_token()}
    assert cloud.cognito_calls == [
        (
            "GetId",
            {
                "IdentityPoolId": "eu-west-1:f25c4598-48da-4fda-9c1a-27fe30bd2ffe",
                "Logins": logins,
            },
        ),
        (
            "GetCredentialsForIdentity",
            {"IdentityId": "eu-west-1:identity", "Logins": logins},
        ),
    ]


@pytest.mark.parametrize(
    ("status", "kind", "error"),
    [
        (400, "NotAuthorizedException", MoulinexAuthenticationError),
        (
            400,
            "com.amazonaws.cognito.identity.model#NotAuthorizedException",
            MoulinexAuthenticationError,
        ),
        (400, "TooManyRequestsException", MoulinexConnectionError),
        (400, "InternalErrorException", MoulinexConnectionError),
        (502, "", MoulinexConnectionError),
        (400, "ResourceNotFoundException", MoulinexError),
    ],
)
async def test_fetch_credentials_errors(
    cloud: FakeCloud,
    session: aiohttp.ClientSession,
    status: int,
    kind: str,
    error: type[MoulinexError],
) -> None:
    """Test what the answers of Cognito are turned into."""
    cloud.cognito_error = (status, kind)

    with pytest.raises(MoulinexError, match="Cognito GetId failed") as raised:
        await fetch_credentials(session, id_token())

    assert type(raised.value) is error


@pytest.mark.parametrize(
    "credentials", [{"AccessKeyId": "key"}, ["not", "an", "object"], "soon"]
)
async def test_fetch_credentials_not_understood(
    cloud: FakeCloud, session: aiohttp.ClientSession, credentials: object
) -> None:
    """Test credentials in a shape this library does not know."""
    cloud.cognito_credentials = credentials

    with pytest.raises(MoulinexError, match="unexpected response") as raised:
        await fetch_credentials(session, id_token())

    assert type(raised.value) is MoulinexError


async def test_fetch_credentials_not_json(
    cloud: FakeCloud, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test an answer that does not come from Cognito, like a page of a proxy."""
    monkeypatch.setattr("aiomoulinex.cognito.COGNITO_URL", f"{cloud.url}/page")

    with pytest.raises(MoulinexError, match="unexpected response"):
        await fetch_credentials(session, id_token())


async def test_badly_encoded_error(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test an error page whose text is not in the encoding it says."""
    cloud.failures["/common-api/profiles/me"] = (502, b"<html>\xe9chec</html>")

    with pytest.raises(MoulinexConnectionError, match="502"):
        await fetch_profile(session, "access-0", Region.MOULINEX_FR)


async def test_attach_policies_without_an_answer(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test that accepting is enough, whatever the cloud answers with it."""
    cloud.failures["/entity/v1/update"] = (204, b"")

    await attach_policies(session, "id-token", "COO001")


async def test_cookies_stay_out_of_the_session(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test that the cookies of the cloud do not end up in a shared session."""
    await fetch_profile(session, cloud.access_token, Region.MOULINEX_FR)
    await attach_policies(session, "id-token", "COO001")
    await fetch_credentials(session, id_token())

    assert len(session.cookie_jar) == 0


async def test_closed_session(cloud: FakeCloud) -> None:  # noqa: ARG001
    """Test that a session that is closed is an error of this library."""
    async with aiohttp.ClientSession() as session:
        pass

    with pytest.raises(MoulinexConnectionError, match="closed"):
        await fetch_profile(session, "access-0", Region.MOULINEX_FR)
    with pytest.raises(MoulinexConnectionError, match="closed"):
        await attach_policies(session, "id-token", "COO001")
    with pytest.raises(MoulinexConnectionError, match="closed"):
        await fetch_credentials(session, id_token())


async def test_fetch_profile_uses_the_key_of_the_brand(
    cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    """Test that each brand has its key, which gives all its kinds of appliances."""
    for region in (
        Region.MOULINEX_FR,
        Region.MOULINEX_CH,
        Region.TEFAL_GB,
        Region.KRUPS_MX,
        Region.TFAL_JP,
    ):
        await fetch_profile(session, cloud.access_token, region)

    assert cloud.profile_perimeters == [
        "MOULINEX_FR",
        "MOULINEX_CH",
        "TEFAL_GB",
        "KRUPS_MX",
        "T-FAL_JP",
    ]
    moulinex, switzerland, tefal, krups, tfal = cloud.profile_keys
    assert moulinex == switzerland
    assert len({moulinex, tefal, krups, tfal}) == 4
    assert set(SEB_API_KEYS) == {region.brand for region in Region}
