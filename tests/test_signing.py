"""Tests for the AWS signature."""

from __future__ import annotations

from datetime import UTC, datetime
import re

from aiomoulinex import AwsCredentials
from aiomoulinex.signing import signed_mqtt_path, signing_key

CREDENTIALS = AwsCredentials(
    access_key="AKIDEXAMPLE",
    secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
    session_token="AQoDYXdzEPT//////////wEXAMPLEtc764bNrC9SAPBSM22wDOk4x4HIZ8j4FZTwdQW+LE/+dXK==",
    expiration=0,
)
HOST = "a1p8u39dc9ign8-ats.iot.eu-west-1.amazonaws.com"


def test_signing_key() -> None:
    """Test the signing key against the example of the AWS documentation."""
    key = signing_key(CREDENTIALS.secret_key, "20120215", "us-east-1", "iam")
    assert key.hex() == (
        "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"
    )


def test_signed_mqtt_path() -> None:
    """Test the signed path against what the AWS SDK computes for the same input."""
    now = datetime(2026, 11, 20, 17, 30, 15, tzinfo=UTC)
    # The signature comes from awscrt 0.x, signing for iotdata without the token.
    assert signed_mqtt_path(CREDENTIALS, HOST, "eu-west-1", now) == (
        "/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIDEXAMPLE%2F20261120%2Feu-west-1%2Fiotdata%2Faws4_request"
        "&X-Amz-Date=20261120T173015Z"
        "&X-Amz-SignedHeaders=host"
        "&X-Amz-Signature="
        "c570cc65fd1fdcb10a57db4b48d787a3231da017baff2f5caf9f2e37b454afca"
        "&X-Amz-Security-Token=AQoDYXdzEPT%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEXAMPLE"
        "tc764bNrC9SAPBSM22wDOk4x4HIZ8j4FZTwdQW%2BLE%2F%2BdXK%3D%3D"
    )


def test_signature_depends_on_what_is_signed() -> None:
    """Test that the host, the region, the time and the key change the signature."""
    now = datetime(2026, 11, 20, 17, 30, 15, tzinfo=UTC)

    def signature(path: str) -> str:
        match = re.search(r"X-Amz-Signature=([0-9a-f]{64})&", path)
        assert match is not None
        return match.group(1)

    reference = signature(signed_mqtt_path(CREDENTIALS, HOST, "eu-west-1", now))
    other_key = AwsCredentials("AKIDEXAMPLE", "other", "token", 0)
    assert reference not in {
        signature(signed_mqtt_path(CREDENTIALS, "example.com", "eu-west-1", now)),
        signature(signed_mqtt_path(CREDENTIALS, HOST, "us-east-1", now)),
        signature(
            signed_mqtt_path(CREDENTIALS, HOST, "eu-west-1", now.replace(second=16))
        ),
        signature(signed_mqtt_path(other_key, HOST, "eu-west-1", now)),
    }


def test_signed_mqtt_path_uses_the_current_time() -> None:
    """Test that the path is signed for now by default."""
    path = signed_mqtt_path(CREDENTIALS, HOST, "eu-west-1")
    assert f"X-Amz-Date={datetime.now(UTC):%Y%m%dT%H}" in path
