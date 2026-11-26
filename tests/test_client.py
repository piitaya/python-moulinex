"""Tests for the client."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import TYPE_CHECKING

import pytest

from aiomoulinex import (
    ApplianceAdded,
    ApplianceRemoved,
    ApplianceShadow,
    ApplianceState,
    ApplianceUpdated,
    AuthenticationFailed,
    ConnectionLost,
    ConnectionRestored,
    CookingState,
    CookingStatus,
    Event,
    MoulinexAuthenticationError,
    MoulinexClient,
    MoulinexConnectionError,
    MoulinexError,
    Region,
    TokensUpdated,
    const,
    login,
)

from .conftest import COMPANION, COOKEO, load_fixture, tokens
from .fake_cloud import EMAIL, PASSWORD, SECRET_KEY, SESSION_TOKEN, id_token, wait_until

if TYPE_CHECKING:
    import aiohttp

    from .fake_cloud import FakeCloud

SHADOW = COOKEO.shadow_topic
UUID = COOKEO.thing_uuid


def record(client: MoulinexClient) -> list[Event]:
    """Return a list the events of a client are added to."""
    events: list[Event] = []
    client.add_listener(events.append)
    return events


def status_of(client: MoulinexClient, uuid: str = UUID) -> CookingStatus:
    """Return the cooking status of an appliance, which has to be known."""
    status = client.states[uuid].status
    assert status is not None
    return status


async def wait_for(events: list[Event], kind: type[Event], count: int = 1) -> None:
    """Wait until a number of events of a kind happened."""
    await wait_until(lambda: sum(isinstance(event, kind) for event in events) >= count)


async def test_connect(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test that connecting follows the appliances and asks for their state."""
    events = record(client)

    await client.connect()

    assert client.connected
    assert client.error is None
    assert client.appliances == {UUID: COOKEO}
    connection = await cloud.wait_connected()
    assert connection.identifier.startswith("mqtt-")
    assert connection.subscriptions == [
        f"{SHADOW}/get/accepted",
        f"{SHADOW}/update/accepted",
        f"{SHADOW}/update/documents",
        COOKEO.cooking_status_topic,
    ]
    assert cloud.published == [
        (f"{SHADOW}/get", b""),
        (COOKEO.status_request_topic, b"{}"),
    ]
    assert cloud.policies == [
        {"openIdToken": client.tokens.id_token, "thingType": "COO001"}
    ]
    assert cloud.profile_perimeters == ["MOULINEX_FR"]

    await wait_for(events, ApplianceUpdated)
    shadow = ApplianceShadow.from_json(load_fixture("shadow_get_accepted.json"))
    state = ApplianceState(shadow=shadow)
    assert client.states == {UUID: state}
    # The refresh token did not change: there is nothing new to store.
    assert events == [ApplianceUpdated(COOKEO, state)]
    assert client.tokens.access_token == "access-1"


async def test_connect_signs_the_websocket(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that the cloud gets a signed request for the MQTT websocket."""
    await client.connect()

    (handshake,) = cloud.handshakes
    assert handshake.headers["Host"] == "localhost"
    assert handshake.headers["Sec-WebSocket-Protocol"] == "mqtt"
    assert handshake.query["X-Amz-Credential"].endswith(
        "/eu-west-1/iotdata/aws4_request"
    )
    assert handshake.query["X-Amz-Security-Token"]


async def test_connect_twice(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test that a connected client does not connect again."""
    await client.connect()
    await client.connect()

    assert len(cloud.handshakes) == 1
    assert cloud.token_requests == 1


async def test_context_manager(
    cloud: FakeCloud, session: aiohttp.ClientSession, ssl_context: ssl.SSLContext
) -> None:
    """Test that the client connects and disconnects as a context manager."""
    async with MoulinexClient(
        session, tokens(), Region.MOULINEX_FR, ssl_context=ssl_context
    ) as client:
        assert client.connected
        await cloud.wait_connected()

    assert not client.connected
    await wait_until(lambda: not cloud.connections)


async def test_default_ssl_context(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    ssl_context: ssl.SSLContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that the client creates a TLS context when it gets none."""
    created: list[bool] = []

    def create_default_context() -> ssl.SSLContext:
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        created.append(True)
        return ssl_context

    monkeypatch.setattr(ssl, "create_default_context", create_default_context)

    async with MoulinexClient(session, tokens(), Region.MOULINEX_FR) as client:
        assert client.connected

    assert created == [True]


async def test_unknown_region(session: aiohttp.ClientSession) -> None:
    """Test a region the library does not know is refused at once."""
    with pytest.raises(ValueError, match="nowhere"):
        MoulinexClient(session, tokens(), "nowhere")  # type: ignore[arg-type]


async def test_two_appliances(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test following a Cookeo and a Companion with one connection."""
    cloud.profile = load_fixture("profile_two_appliances.json")
    cloud.statuses["COM001/" + COMPANION.thing_uuid] = load_fixture("status_ready.json")
    events = record(client)

    await client.connect()

    assert client.appliances == {UUID: COOKEO, COMPANION.thing_uuid: COMPANION}
    assert {policy["thingType"] for policy in cloud.policies} == {"COO001", "COM001"}
    connection = await cloud.wait_connected()
    assert len(connection.subscriptions) == 8
    assert len(cloud.handshakes) == 1
    await wait_for(events, ApplianceUpdated, 2)
    assert status_of(client, COMPANION.thing_uuid).state is CookingState.READY
    assert client.states[UUID].status is None


async def test_status_is_replaced(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test that nothing is kept from the status before: each one is whole."""
    cloud.statuses[f"COO001/{UUID}"] = load_fixture("status_cooking.json")
    events = record(client)
    await client.connect()
    await wait_for(events, ApplianceUpdated, 2)
    cooking = status_of(client)
    assert cooking.state is CookingState.COOKING
    assert cooking.elapsed_time == 16
    assert COOKEO.program(cooking) == "browning_low"

    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_done.json"))
    await wait_for(events, ApplianceUpdated, 3)

    done = status_of(client)
    assert done.state is CookingState.DONE
    assert done.elapsed_time is None
    assert COOKEO.program(done) is None
    assert done.received_at >= cooking.received_at
    assert events[-1] == ApplianceUpdated(COOKEO, client.states[UUID])
    assert client.states[UUID].shadow.firmware.ui_version == "5.0.54"

    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_idle.json"))
    await wait_for(events, ApplianceUpdated, 4)

    assert status_of(client).state is CookingState.IDLE


async def test_messages_that_say_nothing(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that a message that changes nothing is not passed on."""
    events = record(client)
    await client.connect()
    await wait_for(events, ApplianceUpdated)

    await cloud.publish(
        f"{SHADOW}/get/accepted", load_fixture("shadow_get_accepted.json")
    )
    await cloud.publish(COOKEO.cooking_status_topic, b"not json")
    await cloud.publish(COOKEO.cooking_status_topic, ["a", "list"])
    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_ready.json"))
    await wait_for(events, ApplianceUpdated, 2)

    assert sum(isinstance(event, ApplianceUpdated) for event in events) == 2
    assert status_of(client).state is CookingState.READY


async def test_status_requested_when_appliance_comes_online(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that the client asks a Cookeo that is turned on what it is doing."""
    events = record(client)
    await client.connect()
    await wait_for(events, ApplianceUpdated)
    assert client.states[UUID].shadow.connected is False
    cloud.statuses[f"COO001/{UUID}"] = load_fixture("status_warming.json")

    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_online.json")
    )
    await cloud.wait_published(COOKEO.status_request_topic, 2)
    await wait_for(events, ApplianceUpdated, 3)

    assert client.states[UUID].shadow.connected is True
    assert status_of(client).phase_progress == 6

    # The same news on the other topic does not ask again.
    await cloud.publish(
        f"{SHADOW}/update/documents",
        load_fixture("shadow_update_documents_online.json"),
    )
    await client.request_status(COOKEO)
    await cloud.wait_published(COOKEO.status_request_topic, 3)
    assert [topic for topic, _ in cloud.published].count(
        COOKEO.status_request_topic
    ) == 3


async def test_request_status_not_connected(client: MoulinexClient) -> None:
    """Test asking for a status without a connection."""
    with pytest.raises(MoulinexConnectionError, match="not connected"):
        await client.request_status(COOKEO)


async def test_reconnect(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test that the client connects again when the connection is lost."""
    events = record(client)
    await client.connect()
    await wait_for(events, ApplianceUpdated)
    state = client.states[UUID]

    await cloud.drop_connections()
    await wait_for(events, ConnectionLost)

    assert not client.connected
    assert isinstance(client.error, MoulinexConnectionError)
    assert client.states == {UUID: state}

    await wait_for(events, ConnectionRestored)

    assert client.connected
    assert client.error is None
    assert cloud.token_requests == 2
    # The kind of appliance already has its policies.
    assert len(cloud.policies) == 1
    await cloud.wait_published(f"{SHADOW}/get", 2)
    await cloud.wait_published(COOKEO.status_request_topic, 2)
    lost = next(event for event in events if isinstance(event, ConnectionLost))
    assert lost.error is not None
    assert events.index(lost) < events.index(ConnectionRestored())


async def test_reconnect_keeps_trying(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test that the client keeps trying while the cloud does not answer."""
    events = record(client)
    await client.connect()
    cloud.profile_status = 503

    await cloud.drop_connections()
    await wait_for(events, ConnectionLost)
    await wait_until(lambda: len(cloud.profile_perimeters) >= 4)

    assert not client.connected
    assert "503" in str(client.error)
    assert sum(isinstance(event, ConnectionLost) for event in events) == 1

    cloud.profile_status = 200
    await wait_for(events, ConnectionRestored)
    assert client.connected


async def test_credentials_are_renewed(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the client connects again before the AWS credentials expire."""
    monkeypatch.setattr(const, "CREDENTIALS_RENEWAL_MARGIN", 0)
    cloud.credentials_lifetime = 1.2
    cloud.next_refresh_token = "refresh-2"
    events = record(client)
    await client.connect()
    assert client.tokens.refresh_token == "refresh-2"
    cloud.credentials_lifetime = 3600

    await wait_until(lambda: len(cloud.handshakes) >= 2)
    await cloud.wait_published(f"{SHADOW}/get", 2)

    assert client.connected
    assert cloud.token_requests == 2
    assert not any(
        isinstance(event, ConnectionLost | ConnectionRestored) for event in events
    )
    # Only the refresh token that changed is told about.
    tokens_events = [event for event in events if isinstance(event, TokensUpdated)]
    assert len(tokens_events) == 1
    assert tokens_events[0].tokens.refresh_token == "refresh-2"
    first, second = cloud.handshakes
    assert first.query["X-Amz-Signature"] != second.query["X-Amz-Signature"]


async def test_appliances_are_read_again(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that appliances that are added or removed are noticed at a renewal."""
    monkeypatch.setattr(const, "CREDENTIALS_RENEWAL_MARGIN", 0)
    cloud.credentials_lifetime = 1.2
    events = record(client)
    await client.connect()
    assert not any(isinstance(event, ApplianceAdded) for event in events)

    cloud.profile = load_fixture("profile_two_appliances.json")
    await wait_for(events, ApplianceAdded)

    assert ApplianceAdded(COMPANION) in events
    assert set(client.appliances) == {UUID, COMPANION.thing_uuid}
    assert client.states[COMPANION.thing_uuid] == ApplianceState()
    assert {policy["thingType"] for policy in cloud.policies} == {"COO001", "COM001"}
    cooked = client.states[UUID]

    # A profile without appliances is not believed.
    products = cloud.profile["household"]["products"]
    cloud.profile["household"]["products"] = []
    perimeters = len(cloud.profile_perimeters)
    await wait_until(lambda: len(cloud.profile_perimeters) >= perimeters + 2)
    assert set(client.appliances) == {UUID, COMPANION.thing_uuid}

    cloud.profile["household"]["products"] = products[:1]
    await wait_for(events, ApplianceRemoved)

    assert ApplianceRemoved(COMPANION) in events
    assert client.appliances == {UUID: COOKEO}
    assert client.states == {UUID: cooked}
    await wait_until(
        lambda: (
            len(cloud.connections[-1].subscriptions) == 4
            if cloud.connections
            else False
        )
    )


async def test_connect_refresh_token_refused(
    cloud: FakeCloud, session: aiohttp.ClientSession, ssl_context: ssl.SSLContext
) -> None:
    """Test connecting with a refresh token that is revoked."""
    client = MoulinexClient(
        session, tokens("revoked"), Region.MOULINEX_FR, ssl_context=ssl_context
    )
    events = record(client)

    with pytest.raises(MoulinexAuthenticationError, match="invalid_grant"):
        await client.connect()

    assert not client.connected
    assert isinstance(client.error, MoulinexAuthenticationError)
    assert events == []
    assert cloud.token_requests == 1
    assert not cloud.handshakes


async def test_refresh_token_refused_while_connected(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that the client stops and tells when the account has to log in again."""
    events = record(client)
    await client.connect()
    cloud.refresh_token = "another"

    await cloud.drop_connections()
    await wait_for(events, AuthenticationFailed)

    assert not client.connected
    assert isinstance(client.error, MoulinexAuthenticationError)
    lost, failed = events[-2:]
    assert isinstance(lost, ConnectionLost)
    assert failed == AuthenticationFailed(client.error)
    await asyncio.sleep(0.3)
    assert cloud.token_requests == 2

    # New tokens are not picked up: a new client is needed. This one can retry.
    cloud.refresh_token = "refresh-1"
    await client.connect()
    assert client.connected


@pytest.mark.parametrize(
    ("setting", "value", "error", "message"),
    [
        ("profile_status", 500, MoulinexConnectionError, "500"),
        ("profile_status", 401, MoulinexError, "401"),
        ("policies_status", 403, MoulinexError, "403"),
        ("cognito_error", (400, "NotAuthorizedException"), MoulinexError, "Cognito"),
        ("token_status", 503, MoulinexConnectionError, "503"),
        ("refuse_subscriptions", True, MoulinexConnectionError, "refused to follow"),
    ],
)
async def test_connect_errors(
    cloud: FakeCloud,
    client: MoulinexClient,
    setting: str,
    value: object,
    error: type[MoulinexError],
    message: str,
) -> None:
    """Test the errors of connecting, only a refused refresh token asks to log in."""
    setattr(cloud, setting, value)
    events = record(client)

    with pytest.raises(MoulinexError, match=message) as raised:
        await client.connect()

    assert type(raised.value) is error
    assert not client.connected
    assert client.error is raised.value
    assert not any(isinstance(event, ConnectionLost) for event in events)
    # The client does not keep trying by itself.
    requests = cloud.token_requests
    await asyncio.sleep(0.2)
    assert cloud.token_requests == requests


async def test_connect_signature_refused(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that AWS IoT refusing the connection is a connection error."""
    monkeypatch.setattr(const, "AWS_REGION", "us-east-1")

    with pytest.raises(MoulinexConnectionError):
        await client.connect()

    assert len(cloud.handshakes) == 1


async def test_connect_cancelled(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test that cancelling a connection attempt leaves nothing behind."""
    cloud.verify_signature = False
    task = asyncio.ensure_future(client.connect())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client._task is None
    assert not client.connected


async def test_message_that_cannot_be_handled_is_dropped(
    cloud: FakeCloud,
    client: MoulinexClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that a bug in reading a message does not end the connection."""
    events = record(client)
    await client.connect()
    await wait_for(events, ApplianceUpdated)
    original = CookingStatus.from_json.__func__  # type: ignore[attr-defined]
    calls: list[bool] = []

    def from_json(
        cls: type[CookingStatus], payload: dict[str, object]
    ) -> CookingStatus:
        if not calls:
            calls.append(True)
            msg = "Bug"
            raise RuntimeError(msg)
        status: CookingStatus = original(cls, payload)
        return status

    monkeypatch.setattr(CookingStatus, "from_json", classmethod(from_json))

    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_idle.json"))
    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_ready.json"))
    await wait_for(events, ApplianceUpdated, 2)

    assert status_of(client).state is CookingState.READY
    assert "Could not handle a message" in caplog.text
    assert client.connected
    assert len(cloud.handshakes) == 1
    assert not any(isinstance(event, ConnectionLost) for event in events)


async def test_unexpected_error_does_not_stop_the_client(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that a bug in the connection does not end it for good."""
    events = record(client)
    await client.connect()
    calls: list[bool] = []

    async def fail_once(*_: object) -> None:
        monkeypatch.undo()
        calls.append(True)
        msg = "Bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(client, "_load_appliances", fail_once)

    await cloud.drop_connections()
    await wait_for(events, ConnectionLost)
    await wait_until(lambda: "Unexpected error" in str(client.error))
    assert calls == [True]


async def test_listener_that_fails(
    cloud: FakeCloud,  # noqa: ARG001
    client: MoulinexClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that a listener that raises does not stop the others."""

    def fail(_: Event) -> None:
        msg = "Bug"
        raise RuntimeError(msg)

    remove = client.add_listener(fail)
    events = record(client)

    await client.connect()
    await wait_for(events, ApplianceUpdated)

    assert client.connected
    assert "Error in a listener" in caplog.text
    remove()
    remove()
    assert client._listeners == [events.append]


async def test_events_iterator(cloud: FakeCloud, client: MoulinexClient) -> None:
    """Test following a client with an iterator that ends with the connection."""
    received: list[Event] = []

    async def follow() -> None:
        async for event in client.events():
            received.append(event)  # noqa: PERF401

    task = asyncio.ensure_future(follow())
    await asyncio.sleep(0)
    await client.connect()
    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_ready.json"))
    await wait_for(received, ApplianceUpdated, 2)

    await client.disconnect()
    async with asyncio.timeout(5):
        await task

    assert [type(event) for event in received] == [ApplianceUpdated] * 2
    assert not client._queues


async def test_messages_that_are_not_followed(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that a message on another topic changes nothing."""
    events = record(client)
    await client.connect()
    await wait_for(events, ApplianceUpdated)

    assert client._handle_message("COO001/other/cookingStatus", b"{}") is None
    assert len(events) == 1
    await cloud.wait_connected()


async def test_status_is_emptied_when_appliance_turns_off(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that the last cooking does not show again the next time it is on."""
    cloud.shadows[COOKEO.thing_name] = load_fixture("shadow_get_accepted_online.json")
    cloud.statuses[f"COO001/{UUID}"] = load_fixture("status_cooking.json")
    events = record(client)
    await client.connect()
    await wait_for(events, ApplianceUpdated, 2)
    assert client.states[UUID].shadow.connected is True
    assert status_of(client).state is CookingState.COOKING
    del cloud.statuses[f"COO001/{UUID}"]

    await cloud.publish(
        f"{SHADOW}/update/documents",
        load_fixture("shadow_update_documents_offline.json"),
    )
    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_offline.json")
    )
    await wait_for(events, ApplianceUpdated, 3)

    state = client.states[UUID]
    assert state.shadow.connected is False
    assert state.shadow.firmware.ui_version == "5.0.54"
    assert state.status is None
    assert events[-1] == ApplianceUpdated(COOKEO, state)

    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_online.json")
    )
    await wait_for(events, ApplianceUpdated, 4)

    assert client.states[UUID].shadow.connected is True
    assert client.states[UUID].status is None
    assert COOKEO.program(client.states[UUID].status) is None


async def test_connect_waits_for_the_state_of_the_appliances(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the state is known when connecting is done, without waiting."""
    monkeypatch.setattr(const, "INITIAL_STATE_TIMEOUT", 30)
    cloud.profile = load_fixture("profile_two_appliances.json")
    cloud.shadows[COOKEO.thing_name] = load_fixture("shadow_get_accepted_online.json")
    cloud.statuses[f"COO001/{UUID}"] = load_fixture("status_idle.json")
    cloud.shadows[COMPANION.thing_name] = load_fixture("shadow_get_accepted.json")

    await client.connect()

    assert client.connected
    assert client.states[UUID].shadow.connected is True
    assert status_of(client).state is CookingState.IDLE
    # The Companion is turned off: it has no cooking status to wait for.
    assert client.states[COMPANION.thing_uuid].shadow.connected is False
    assert client.states[COMPANION.thing_uuid].status is None


async def test_connect_does_not_wait_forever(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that an appliance that does not answer does not block connecting."""
    del cloud.shadows[COOKEO.thing_name]
    events = record(client)
    loop = asyncio.get_running_loop()
    started = loop.time()

    await client.connect()

    assert 0.25 < loop.time() - started < 2
    assert client.connected
    assert client.error is None
    assert client.states == {UUID: ApplianceState()}
    assert not any(isinstance(event, ConnectionRestored) for event in events)


async def test_connect_waits_for_the_status_of_an_appliance_that_is_on(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that a Cookeo that is turned on and says nothing is waited for."""
    cloud.shadows[COOKEO.thing_name] = load_fixture("shadow_get_accepted_online.json")
    loop = asyncio.get_running_loop()
    started = loop.time()

    await client.connect()

    assert loop.time() - started > 0.25
    assert client.states[UUID].shadow.connected is True
    assert client.states[UUID].status is None


async def test_reconnect_does_not_wait_for_the_appliances(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the connection is back as soon as the cloud is."""
    events = record(client)
    await client.connect()
    monkeypatch.setattr(const, "INITIAL_STATE_TIMEOUT", 30)
    del cloud.shadows[COOKEO.thing_name]

    await cloud.drop_connections()
    await wait_for(events, ConnectionRestored)

    assert client.connected


def requests_for_status(cloud: FakeCloud) -> int:
    """Return how many times the Cookeo was asked for its status."""
    return [topic for topic, _ in cloud.published].count(COOKEO.status_request_topic)


async def test_disconnect_while_the_connection_opens(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that no connection is left behind when stopped while one opens."""
    cloud.handshake_delay = 0.4
    connecting = asyncio.ensure_future(client.connect())
    await wait_until(lambda: len(cloud.handshakes) == 1)

    await client.disconnect()

    with pytest.raises(MoulinexError, match="Disconnected while connecting"):
        await connecting
    await asyncio.sleep(0.8)
    assert not cloud.connections
    assert client._task is None
    assert not client.connected


async def test_connect_cancelled_while_the_connection_opens(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that cancelling a connection attempt closes what it opened."""
    cloud.handshake_delay = 0.4
    connecting = asyncio.ensure_future(client.connect())
    await wait_until(lambda: len(cloud.handshakes) == 1)

    connecting.cancel()

    with pytest.raises(asyncio.CancelledError):
        await connecting
    await asyncio.sleep(0.8)
    assert not cloud.connections
    assert client._task is None


async def test_connect_while_disconnecting(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that connecting again waits for a disconnection that is going on."""
    await client.connect()
    disconnecting = asyncio.ensure_future(client.disconnect())
    await asyncio.sleep(0)

    await client.connect()
    await disconnecting

    assert client.connected
    assert client._stopping is None
    await wait_until(lambda: len(cloud.connections) == 1)
    assert client.states[UUID].shadow.connected is False


async def test_connect_waits_for_the_shadow_whatever_comes_first(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that a status or another news of the shadow is not enough to go on."""
    monkeypatch.setattr(const, "INITIAL_STATE_TIMEOUT", 30)
    del cloud.shadows[COOKEO.thing_name]
    cloud.statuses[f"COO001/{UUID}"] = load_fixture("status_idle.json")
    connecting = asyncio.ensure_future(client.connect())
    await wait_until(
        lambda: client.states.get(UUID, ApplianceState()).status is not None
    )
    await asyncio.sleep(0.1)
    assert not connecting.done()

    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_other.json")
    )
    await asyncio.sleep(0.1)
    assert not connecting.done()
    assert not client.connected

    await cloud.publish(
        f"{SHADOW}/get/accepted", load_fixture("shadow_get_accepted_online.json")
    )
    async with asyncio.timeout(5):
        await connecting

    assert client.connected
    assert client.states[UUID].shadow.connected is True
    assert status_of(client).state is CookingState.IDLE


async def test_renewal_that_fails_keeps_the_connection(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that the cloud failing at a renewal does not end what works."""
    # With the margin of 5 minutes, the renewal starts within 2 seconds.
    cloud.credentials_lifetime = 302
    events = record(client)
    await client.connect()
    cloud.profile_status = 503
    requests = cloud.token_requests

    await wait_until(lambda: cloud.token_requests >= requests + 3)
    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_ready.json"))
    await wait_for(events, ApplianceUpdated, 2)

    assert status_of(client).state is CookingState.READY
    assert client.connected
    assert len(cloud.handshakes) == 1

    cloud.credentials_lifetime = 3600
    cloud.profile_status = 200
    await wait_until(lambda: len(cloud.handshakes) == 2)
    await cloud.wait_published(f"{SHADOW}/get", 2)

    assert client.connected
    assert not any(
        isinstance(event, ConnectionLost | ConnectionRestored) for event in events
    )


async def test_renewal_that_keeps_failing_ends_with_the_credentials(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the connection is given up once the credentials are expired."""
    monkeypatch.setattr(const, "CREDENTIALS_RENEWAL_MARGIN", 1)
    cloud.credentials_lifetime = 2.9
    events = record(client)
    await client.connect()
    cloud.profile_status = 503

    await wait_for(events, ConnectionLost)

    assert "503" in str(client.error)
    assert not client.connected


async def test_refresh_token_refused_at_a_renewal(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that a renewal that asks for a new login stops the client."""
    cloud.credentials_lifetime = 302
    events = record(client)
    await client.connect()
    cloud.refresh_token = "another"

    await wait_for(events, AuthenticationFailed)

    assert not client.connected
    assert isinstance(events[-2], ConnectionLost)
    await wait_until(lambda: not cloud.connections)


async def test_status_of_an_appliance_that_is_said_to_be_off(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that a status is kept even when the shadow did not catch up yet."""
    events = record(client)
    await client.connect()
    assert client.states[UUID].shadow.connected is False

    await cloud.publish(COOKEO.cooking_status_topic, load_fixture("status_ready.json"))
    await wait_for(events, ApplianceUpdated, 2)

    assert client.states[UUID].shadow.connected is False
    assert status_of(client).state is CookingState.READY


async def test_reconnection_delays(
    cloud: FakeCloud, client: MoulinexClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that the attempts are spaced more and more, until one succeeds."""
    caplog.set_level(logging.DEBUG, logger="aiomoulinex.client")
    events = record(client)
    await client.connect()

    def delays() -> list[object]:
        return [
            record.args[0]
            for record in caplog.records
            if record.msg.startswith("Lost the connection")
            and isinstance(record.args, tuple)
        ]

    cloud.profile_status = 503
    await cloud.drop_connections()
    await wait_until(lambda: len(delays()) >= 4)
    assert delays()[:4] == [0.05, 0.1, 0.2, 0.2]

    cloud.profile_status = 200
    await wait_for(events, ConnectionRestored)
    caplog.clear()
    await cloud.drop_connections()
    await wait_until(lambda: len(delays()) >= 1)
    assert delays()[0] == 0.05


async def test_policies_attached_again_after_a_refusal(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that a refused subscription makes the client ask for access again."""
    cloud.refuse_subscriptions = True
    with pytest.raises(MoulinexConnectionError, match="refused to follow"):
        await client.connect()
    cloud.refuse_subscriptions = False

    await client.connect()

    assert client.connected
    assert len(cloud.policies) == 2


async def test_status_asked_again_after_turning_on(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that a Cookeo that just turned on is asked until it answers."""
    events = record(client)
    await client.connect()
    assert requests_for_status(cloud) == 1
    cloud.statuses[f"COO001/{UUID}"] = load_fixture("status_idle.json")
    cloud.ignored_status_requests = 2

    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_online.json")
    )
    await cloud.publish(
        f"{SHADOW}/update/documents",
        load_fixture("shadow_update_documents_online.json"),
    )
    await wait_until(lambda: client.states[UUID].status is not None)

    assert status_of(client).state is CookingState.IDLE
    # At connection, when it turned on, and twice more until it answered.
    assert requests_for_status(cloud) == 4
    await asyncio.sleep(0.6)
    assert requests_for_status(cloud) == 4
    # The shadow when connecting, its two updates, and the status.
    assert sum(isinstance(event, ApplianceUpdated) for event in events) == 4


async def test_status_is_not_asked_forever(
    cloud: FakeCloud, client: MoulinexClient
) -> None:
    """Test that the client gives up on a Cookeo that never answers."""
    await client.connect()
    cloud.ignored_status_requests = 100

    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_online.json")
    )
    await wait_until(lambda: requests_for_status(cloud) == 5)
    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_other.json")
    )
    await asyncio.sleep(0.9)

    assert requests_for_status(cloud) == 5
    assert client.states[UUID].status is None

    # It is asked again the next time it turns on.
    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_offline.json")
    )
    await cloud.publish(
        f"{SHADOW}/update/accepted", load_fixture("shadow_update_accepted_online.json")
    )
    await wait_until(lambda: requests_for_status(cloud) > 5)


async def test_connect_asks_again_an_appliance_that_is_on(
    cloud: FakeCloud, client: MoulinexClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that a first request that is lost is made up for while connecting."""
    monkeypatch.setattr(const, "INITIAL_STATE_TIMEOUT", 30)
    cloud.shadows[COOKEO.thing_name] = load_fixture("shadow_get_accepted_online.json")
    cloud.statuses[f"COO001/{UUID}"] = load_fixture("status_idle.json")
    cloud.ignored_status_requests = 1

    await client.connect()

    assert status_of(client).state is CookingState.IDLE
    assert requests_for_status(cloud) == 2


async def test_nothing_secret_is_logged(
    cloud: FakeCloud,  # noqa: ARG001
    session: aiohttp.ClientSession,
    client: MoulinexClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that the logs can be shared, even the most detailed ones."""
    caplog.set_level(logging.DEBUG)
    tokens = await login(session, EMAIL, PASSWORD, Region.MOULINEX_FR)
    events = record(client)

    await client.connect()
    await wait_for(events, ApplianceUpdated)
    await client.disconnect()

    assert "Message of Cookeo Touch Wifi on /shadow/get/accepted" in caplog.text
    for secret in (
        EMAIL,
        PASSWORD,
        tokens.refresh_token,
        id_token(),
        SECRET_KEY,
        SESSION_TOKEN,
        "X-Amz-Security-Token",
    ):
        assert secret not in caplog.text
