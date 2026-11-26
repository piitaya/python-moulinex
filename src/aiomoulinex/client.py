"""Client that follows the appliances of an SEB account."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
import json
import logging
import secrets
import ssl
import sys
import time
from typing import TYPE_CHECKING, Any, Self

import aiomqtt

from . import const
from .api import attach_policies, fetch_profile
from .auth import refresh
from .cognito import fetch_credentials
from .events import (
    ApplianceAdded,
    ApplianceRemoved,
    ApplianceUpdated,
    AuthenticationFailed,
    ConnectionLost,
    ConnectionRestored,
    Event,
    TokensUpdated,
)
from .exceptions import (
    MoulinexAuthenticationError,
    MoulinexConnectionError,
    MoulinexError,
)
from .models import ApplianceShadow, ApplianceState, CookingStatus
from .regions import Region
from .signing import signed_mqtt_path

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from types import TracebackType

    import aiohttp

    from .models import Appliance, AwsCredentials, Tokens

_LOGGER = logging.getLogger(__name__)

# What AWS IoT answers to a subscription it refuses.
_SUBSCRIPTION_REFUSED = 0x80


class MoulinexClient:
    """Client that follows the Cookeo and Companion of an SEB account.

    The appliances push their cooking status and their shadow over MQTT. The
    client keeps the connection open, renews the tokens and the AWS credentials
    before they expire, connects again when the connection is lost, and tells
    its listeners what changes.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: Tokens,
        region: Region,
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float = const.DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client.

        Without ``ssl_context``, the client creates a default one in an executor.
        ``timeout`` is that of the MQTT connection, in seconds.
        """
        self._session = session
        self._tokens = tokens
        self._region = Region(region)
        self._ssl_context = ssl_context
        self._timeout = timeout
        # AWS IoT needs a distinct client id for each connection of an identity.
        self._identifier = f"mqtt-{secrets.token_hex(8)}"

        self._appliances: dict[str, Appliance] = {}
        self._states: dict[str, ApplianceState] = {}
        self._topics: dict[str, tuple[Appliance, bool]] = {}
        self._thing_types: set[str] = set()
        self._loaded = False
        self._awaited: set[str] = set()
        self._asking: dict[str, asyncio.Task[None]] = {}

        self._listeners: list[Callable[[Event], None]] = []
        self._queues: set[asyncio.Queue[Event | None]] = set()

        self._task: asyncio.Task[None] | None = None
        self._stopping: asyncio.Task[None] | None = None
        self._following = False
        self._ready = False
        self._mqtt: aiomqtt.Client | None = None
        self._connect_lock = asyncio.Lock()
        self._first_attempt = asyncio.Event()
        self._connected = False
        self._error: MoulinexError | None = None

    @property
    def tokens(self) -> Tokens:
        """Return the tokens of the account, as last refreshed."""
        return self._tokens

    @property
    def appliances(self) -> dict[str, Appliance]:
        """Return the appliances of the account by ``thing_uuid``."""
        return dict(self._appliances)

    @property
    def states(self) -> dict[str, ApplianceState]:
        """Return the last known state of each appliance by ``thing_uuid``."""
        return dict(self._states)

    @property
    def connected(self) -> bool:
        """Return whether the client is connected to the cloud.

        It stays ``True`` during the short reconnection that renews the AWS
        credentials. The appliances keep their last known state while it is
        ``False``.
        """
        return self._connected

    @property
    def error(self) -> MoulinexError | None:
        """Return why the client is not connected, if that is known."""
        return self._error

    def add_listener(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        """Call the listener for each event, from the event loop.

        Returns a function that removes the listener.
        """
        self._listeners.append(listener)

        def remove_listener() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove_listener

    def events(self) -> AsyncIterator[Event]:
        """Iterate over the events from now on, until the client stops.

        Call it while the client is connected, or before ``connect``.
        """
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues.add(queue)

        async def iterate() -> AsyncIterator[Event]:
            try:
                while (event := await queue.get()) is not None:
                    yield event
            finally:
                self._queues.discard(queue)

        return iterate()

    async def connect(self) -> None:
        """Connect to the cloud, and wait until the state of the appliances is known.

        That is the shadow of each appliance, and the cooking status of those
        that are turned on. An appliance that does not answer within a few
        seconds is not waited for any longer.

        The client then keeps following the appliances until ``disconnect``.
        It stops by itself when the cloud refuses the refresh token.

        Raises:
            MoulinexAuthenticationError: The cloud refused the refresh token.
            MoulinexConnectionError: The cloud could not be reached.
            MoulinexError: The cloud answered something that is not understood.

        """
        async with self._connect_lock:
            if self._task is not None and not self._task.done():
                return
            if (stopping := self._stopping) is not None:
                # A disconnection is still going on: let it end first.
                await asyncio.gather(stopping, return_exceptions=True)
            loop = asyncio.get_running_loop()
            if self._ssl_context is None:
                self._ssl_context = await loop.run_in_executor(
                    None, ssl.create_default_context
                )
            self._first_attempt.clear()
            self._error = None
            self._task = loop.create_task(self._run(), name="aiomoulinex")
            try:
                await self._first_attempt.wait()
            except asyncio.CancelledError:
                await self.disconnect()
                raise
            if self._connected:
                return
            error = self._error
            await self.disconnect()
            raise error or MoulinexError("Disconnected while connecting")

    async def disconnect(self) -> None:
        """Disconnect from the cloud."""
        if (task := self._task) is None:
            return
        self._task = None
        self._stopping = task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self._stopping is task:
            self._stopping = None

    async def request_status(self, appliance: Appliance) -> None:
        """Ask an appliance to send its cooking status.

        The client already does this when it connects, and when an appliance
        comes back online.

        Raises:
            MoulinexConnectionError: The client is not connected.

        """
        if (mqtt := self._mqtt) is None:
            msg = "The client is not connected"
            raise MoulinexConnectionError(msg)
        try:
            await self._request_status(mqtt, appliance)
        except aiomqtt.MqttError as err:
            raise MoulinexConnectionError(str(err)) from err

    async def __aenter__(self) -> Self:
        """Connect to the cloud."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Disconnect from the cloud."""
        await self.disconnect()

    async def _run(self) -> None:
        """Keep a connection to the cloud open, until cancelled."""
        task = asyncio.current_task()
        delay = const.RECONNECT_MIN_DELAY
        credentials: AwsCredentials | None = None
        try:
            while True:
                self._ready = False
                error: MoulinexError
                try:
                    if credentials is None:
                        credentials = await self._prepare()
                    # Ends with the credentials for the next connection.
                    credentials = await self._listen(credentials)
                    delay = const.RECONNECT_MIN_DELAY
                    continue
                except aiomqtt.MqttError as err:
                    error = MoulinexConnectionError(str(err))
                except MoulinexError as err:
                    error = err
                except Exception:  # pylint: disable=broad-exception-caught
                    # A bug must not end the connection for good.
                    _LOGGER.exception("Unexpected error in the connection")
                    error = MoulinexError("Unexpected error, see the log")
                if task is not None and task.cancelling():
                    # Closing the connection failed, while asked to stop.
                    return
                credentials = None
                if not self._retry_after(error):
                    return
                if self._ready:
                    # The connection was up: this is not one more failed attempt.
                    delay = const.RECONNECT_MIN_DELAY
                _LOGGER.debug(
                    "Lost the connection to the cloud, next attempt in %.0f s: %s",
                    delay,
                    error,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, const.RECONNECT_MAX_DELAY)
        finally:
            self._connected = False
            # Never leave connect() or an iterator waiting, whatever happened.
            self._first_attempt.set()
            for queue in self._queues:
                queue.put_nowait(None)

    def _retry_after(self, error: MoulinexError) -> bool:
        """Tell the listeners about a failure, return whether to connect again."""
        self._error = error
        self._set_connected(connected=False)
        if isinstance(error, MoulinexAuthenticationError):
            # Only a new login gets the client going again.
            _LOGGER.debug("The cloud refused the refresh token: %s", error)
            if self._first_attempt.is_set():
                self._emit(AuthenticationFailed(error))
            return False
        if not self._first_attempt.is_set():
            # The caller of connect() decides if and when to try again.
            _LOGGER.debug("Could not connect to the cloud: %s", error)
            return False
        return True

    async def _prepare(self) -> AwsCredentials:
        """Renew the tokens, read the appliances again and get AWS credentials."""
        tokens = await refresh(self._session, self._tokens.refresh_token)
        changed = tokens.refresh_token != self._tokens.refresh_token
        self._tokens = tokens
        if changed:
            self._emit(TokensUpdated(tokens))
        try:
            await self._load_appliances()
            return await fetch_credentials(self._session, tokens.id_token)
        except MoulinexAuthenticationError as err:
            # The tokens are new: logging in again would not change the answer.
            raise MoulinexError(str(err)) from err

    async def _load_appliances(self) -> None:
        """Read the appliances of the account, and tell which ones changed."""
        profile = await fetch_profile(
            self._session, self._tokens.access_token, self._region
        )
        appliances = {
            appliance.thing_uuid: appliance for appliance in profile.appliances
        }
        if self._appliances and not appliances:
            # More likely a hiccup of the cloud than every appliance being removed.
            _LOGGER.debug(
                "The profile has no appliances anymore, keeping the known ones"
            )
            return
        for thing_type in {appliance.thing_type for appliance in appliances.values()}:
            if thing_type not in self._thing_types:
                await attach_policies(self._session, self._tokens.id_token, thing_type)
                self._thing_types.add(thing_type)
        removed = [a for uuid, a in self._appliances.items() if uuid not in appliances]
        added = [a for uuid, a in appliances.items() if uuid not in self._appliances]
        self._appliances = appliances
        self._states = {
            uuid: self._states.get(uuid, ApplianceState()) for uuid in appliances
        }
        if not self._loaded:
            # The caller of connect() reads the first appliances from the client.
            self._loaded = True
            return
        for appliance in removed:
            self._emit(ApplianceRemoved(appliance))
        for appliance in added:
            self._emit(ApplianceAdded(appliance))

    @contextlib.asynccontextmanager
    async def _open(self, credentials: AwsCredentials) -> AsyncIterator[aiomqtt.Client]:
        """Open the MQTT connection to AWS IoT, and close it at the end."""
        mqtt = aiomqtt.Client(
            hostname=const.MQTT_ENDPOINT,
            port=const.MQTT_PORT,
            identifier=self._identifier,
            protocol=aiomqtt.ProtocolVersion.V311,
            transport="websockets",
            websocket_path=signed_mqtt_path(
                credentials, const.MQTT_ENDPOINT, const.AWS_REGION
            ),
            # The signature covers the host without its port.
            websocket_headers={"Host": const.MQTT_ENDPOINT},
            tls_context=self._ssl_context,
            keepalive=const.MQTT_KEEPALIVE,
            timeout=self._timeout,
            logger=_LOGGER.getChild("mqtt"),
        )
        # pylint: disable-next=unnecessary-dunder-call
        opening = asyncio.ensure_future(mqtt.__aenter__())
        try:
            await asyncio.shield(opening)
        except asyncio.CancelledError:
            # The MQTT client leaves a connection behind when it is stopped
            # while it opens one: let it open, then close it.
            with contextlib.suppress(aiomqtt.MqttError):
                await opening
                await mqtt.__aexit__(None, None, None)
            raise
        try:
            yield mqtt
        finally:
            await mqtt.__aexit__(*sys.exc_info())

    async def _listen(self, credentials: AwsCredentials) -> AwsCredentials:
        """Follow the appliances over MQTT until the credentials have to be renewed.

        Returns the credentials for the next connection.
        """
        loop = asyncio.get_running_loop()
        async with self._open(credentials) as mqtt:
            self._mqtt = mqtt
            self._topics = {}
            waiting: asyncio.TimerHandle | None = None
            # The messages are still handled while the credentials are renewed.
            reader = loop.create_task(self._read(mqtt))
            renewer = loop.create_task(self._renew(credentials))
            if not self._first_attempt.is_set():
                # connect() also waits for the first state of the appliances.
                self._awaited = set(self._appliances)
            try:
                for appliance in self._appliances.values():
                    await self._subscribe(mqtt, appliance)
                for appliance in self._appliances.values():
                    await self._request_status(mqtt, appliance)
                _LOGGER.debug(
                    "Connected, following %s appliances", len(self._appliances)
                )
                self._following = True
                self._check_ready()
                if not self._ready:
                    waiting = loop.call_later(
                        const.INITIAL_STATE_TIMEOUT, self._set_ready
                    )
                await asyncio.wait(
                    {reader, renewer}, return_when=asyncio.FIRST_COMPLETED
                )
                if reader.done():
                    reader.result()
                    msg = "The cloud closed the connection"
                    raise MoulinexConnectionError(msg)
                return renewer.result()
            finally:
                if waiting is not None:
                    waiting.cancel()
                pending = [reader, renewer, *self._asking.values()]
                self._asking = {}
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                self._awaited = set()
                self._following = False
                self._mqtt = None
                self._topics = {}

    async def _read(self, mqtt: aiomqtt.Client) -> None:
        """Handle the messages of the appliances, until the connection ends."""
        loop = asyncio.get_running_loop()
        async for message in mqtt.messages:
            back_online = self._handle_message(str(message.topic), message.payload)
            if back_online is not None:
                await self._request_status(mqtt, back_online)
            for uuid, appliance in self._appliances.items():
                if not self._misses_status(uuid):
                    # Start over the next time the appliance turns on.
                    if (asking := self._asking.pop(uuid, None)) is not None:
                        asking.cancel()
                elif uuid not in self._asking:
                    self._asking[uuid] = loop.create_task(
                        self._ask_again(mqtt, appliance)
                    )

    def _misses_status(self, uuid: str) -> bool:
        """Return whether an appliance is turned on and did not tell its status."""
        state = self._states.get(uuid)
        return state is not None and bool(state.shadow.connected) and not state.status

    async def _ask_again(self, mqtt: aiomqtt.Client, appliance: Appliance) -> None:
        """Ask an appliance for its status again, until it answers or a few times.

        An appliance that just turned on is said to be connected a few seconds
        before it hears a request, so the first one is lost.
        """
        for delay in const.STATUS_RETRY_DELAYS:
            await asyncio.sleep(delay)
            if not self._misses_status(appliance.thing_uuid):
                return
            with contextlib.suppress(aiomqtt.MqttError):
                await self._request_status(mqtt, appliance)

    async def _renew(self, credentials: AwsCredentials) -> AwsCredentials:
        """Wait until the credentials are about to expire, return new ones.

        A renewal that fails is tried again for as long as the credentials last,
        so that a hiccup of the cloud does not end a connection that works.
        """
        await asyncio.sleep(
            max(
                const.RECONNECT_MIN_DELAY,
                credentials.expiration - time.time() - const.CREDENTIALS_RENEWAL_MARGIN,
            )
        )
        delay = const.RECONNECT_MIN_DELAY
        while True:
            try:
                return await self._prepare()
            except MoulinexAuthenticationError:
                raise
            except MoulinexError as err:
                if time.time() + delay >= credentials.expiration:
                    raise
                _LOGGER.debug(
                    "Could not renew the credentials, next attempt in %.0f s: %s",
                    delay,
                    err,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, const.RECONNECT_MAX_DELAY)

    async def _subscribe(self, mqtt: aiomqtt.Client, appliance: Appliance) -> None:
        """Subscribe to the topics of an appliance and request its shadow."""
        shadow = appliance.shadow_topic
        topics = {
            f"{shadow}/get/accepted": True,
            f"{shadow}/update/accepted": True,
            f"{shadow}/update/documents": True,
            appliance.cooking_status_topic: False,
        }
        granted = await mqtt.subscribe([(topic, 1) for topic in topics])
        if any(_code(code) >= _SUBSCRIPTION_REFUSED for code in granted):
            # The policies may be gone: attach them again at the next attempt.
            self._thing_types.discard(appliance.thing_type)
            msg = f"The cloud refused to follow {appliance.thing_name}"
            raise MoulinexConnectionError(msg)
        for topic, is_shadow in topics.items():
            self._topics[topic] = (appliance, is_shadow)
        await mqtt.publish(f"{shadow}/get", payload="", qos=1)

    async def _request_status(self, mqtt: aiomqtt.Client, appliance: Appliance) -> None:
        """Ask an appliance to send its cooking status."""
        await mqtt.publish(appliance.status_request_topic, payload="{}", qos=1)

    def _handle_message(self, topic: str, payload: Any) -> Appliance | None:
        """Handle a message, return the appliance to ask for its status if any."""
        if (target := self._topics.get(topic)) is None:
            return None
        appliance, is_shadow = target
        if (state := self._states.get(appliance.thing_uuid)) is None:
            return None
        kind = topic.rsplit(appliance.thing_uuid, 1)[-1]
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            _LOGGER.debug("Ignoring a message of %s that is not understood", kind)
            return None
        _LOGGER.debug("Message of %s on %s: %s", appliance.name, kind, data)
        back_online = False
        try:
            if is_shadow:
                shadow = state.shadow.merge(ApplianceShadow.from_json(data))
                back_online = state.shadow.connected is False and bool(shadow.connected)
                updated = replace(state, shadow=shadow)
                if shadow.connected is False:
                    # What an appliance cooked says nothing once it is turned off.
                    updated = replace(updated, status=None)
            else:
                # Each message holds the whole status, nothing is kept from
                # before. It is kept even when the shadow says that the appliance
                # is turned off: one that reports a status is turned on.
                updated = replace(state, status=CookingStatus.from_json(data))
        except Exception:  # pylint: disable=broad-exception-caught
            # A message that is not understood must not end the connection.
            _LOGGER.exception("Could not handle a message of %s", kind)
            return None
        if updated != state:
            self._states[appliance.thing_uuid] = updated
            self._emit(ApplianceUpdated(appliance, updated))
        if _is_known(updated):
            self._awaited.discard(appliance.thing_uuid)
            self._check_ready()
        return appliance if back_online else None

    def _check_ready(self) -> None:
        """Tell that the connection is up once nothing is waited for anymore."""
        if self._following and not self._awaited and not self._ready:
            self._set_ready()

    def _set_ready(self) -> None:
        """Tell that the connection is up and the appliances are followed."""
        self._awaited = set()
        self._ready = True
        self._error = None
        self._set_connected(connected=True)
        self._first_attempt.set()

    def _set_connected(self, *, connected: bool) -> None:
        """Set whether the client is connected, and tell the listeners."""
        if self._connected == connected:
            return
        self._connected = connected
        if connected:
            # Nothing was lost when connect() has not returned yet.
            if self._first_attempt.is_set():
                self._emit(ConnectionRestored())
        elif self._error is not None:
            self._emit(ConnectionLost(self._error))

    def _emit(self, event: Event) -> None:
        """Tell the listeners and the iterators about an event."""
        for queue in self._queues:
            queue.put_nowait(event)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # pylint: disable=broad-exception-caught
                # A listener that fails must not end the connection.
                _LOGGER.exception("Error in a listener")


def _is_known(state: ApplianceState) -> bool:
    """Return whether an appliance told enough for connect() to stop waiting.

    That is whether it is turned on, and its cooking status when it is: an
    appliance that is turned off has none.
    """
    if state.shadow.connected is None:
        return False
    return state.shadow.connected is False or state.status is not None


def _code(granted: Any) -> int:
    """Return the number of what the cloud answered to a subscription."""
    value = getattr(granted, "value", granted)
    return value if isinstance(value, int) else 0
