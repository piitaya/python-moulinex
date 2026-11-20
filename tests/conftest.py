"""Fixtures for the tests of aiomoulinex."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import datetime as dt
import ipaddress
import json
from pathlib import Path
import ssl
from typing import TYPE_CHECKING, Any

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from aiomoulinex import Appliance, MoulinexClient, Region, Tokens, const

from .fake_cloud import FakeCloud, id_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

FIXTURES = Path(__file__).parent / "fixtures"

COOKEO = Appliance(
    name="Cookeo Touch Wifi",
    thing_type="COO001",
    thing_uuid="11111111-2222-3333-4444-555555555555",
    cmmf="1510002402",
    model="Cookeo Touch Wifi",
    catalog_id="APPLIANCE_1090",
)
COMPANION = Appliance(
    name="i-Companion Touch XL",
    thing_type="COM001",
    thing_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    cmmf="8000036990",
    domain="PRO_COM",
    model="i-Companion Touch / Touch Pro",
    catalog_id="APPLIANCE_1091",
)


def load_fixture(name: str) -> dict[str, Any]:
    """Load a payload of the cloud."""
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return payload


def tokens(refresh_token: str = "refresh-1") -> Tokens:
    """Return the tokens of the account, as stored after a login."""
    return Tokens(
        access_token="stored", id_token=id_token(), refresh_token=refresh_token
    )


@pytest.fixture(scope="session")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Return a certificate for localhost and its key."""
    directory = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.fixture(scope="session")
def ssl_context(certificate: tuple[Path, Path]) -> ssl.SSLContext:
    """Return the TLS context of a client that trusts the fake cloud."""
    return ssl.create_default_context(cafile=certificate[0])


@pytest.fixture
async def cloud(
    certificate: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FakeCloud]:
    """Return a running cloud with the account of a Cookeo that is turned off."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)
    async with FakeCloud(context) as cloud:
        cloud.profile = load_fixture("profile.json")
        cloud.shadows[COOKEO.thing_name] = load_fixture("shadow_get_accepted.json")
        monkeypatch.setattr("aiomoulinex.auth.SF_BASE", cloud.url)
        monkeypatch.setattr("aiomoulinex.api.SEB_API_BASE", cloud.url)
        monkeypatch.setattr("aiomoulinex.api.SEB_IOT_BASE", cloud.url)
        monkeypatch.setattr("aiomoulinex.cognito.COGNITO_URL", f"{cloud.url}/cognito/")
        monkeypatch.setattr(const, "MQTT_ENDPOINT", "localhost")
        monkeypatch.setattr(const, "MQTT_PORT", cloud.port)
        monkeypatch.setattr(const, "RECONNECT_MIN_DELAY", 0.05)
        monkeypatch.setattr(const, "RECONNECT_MAX_DELAY", 0.2)
        monkeypatch.setattr(const, "INITIAL_STATE_TIMEOUT", 0.3)
        monkeypatch.setattr(const, "STATUS_RETRY_DELAYS", (0.2, 0.2, 0.2))
        yield cloud


@pytest.fixture
async def session(ssl_context: ssl.SSLContext) -> AsyncIterator[aiohttp.ClientSession]:
    """Return a session that trusts the fake cloud."""
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        yield session


@pytest.fixture
async def client(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    ssl_context: ssl.SSLContext,
) -> AsyncIterator[MoulinexClient]:
    """Return a client for the account. It is not connected yet."""
    client = MoulinexClient(
        session, tokens(), Region.MOULINEX_FR, ssl_context=ssl_context, timeout=2
    )
    yield client
    await client.disconnect()
