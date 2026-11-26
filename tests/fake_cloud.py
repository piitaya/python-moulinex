"""A fake SEB cloud: the login page, the REST API, Cognito and AWS IoT."""

from __future__ import annotations

import asyncio
import base64
import contextlib
from datetime import UTC, datetime
import json
import struct
import time
from typing import TYPE_CHECKING, Any, Self

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from aiomoulinex import AwsCredentials
from aiomoulinex.signing import signed_mqtt_path

if TYPE_CHECKING:
    from collections.abc import Callable
    import ssl
    from types import TracebackType

EMAIL = "jane.doe@example.com"
PASSWORD = "correct horse"
USER_ID = "005000000000001AAA"
AWS_REGION = "eu-west-1"
ACCESS_KEY = "ASIAEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
SESSION_TOKEN = "IQoJb3JpZ2luX2Vj//////////wEXAMPLE+token/with=signs=="

CONNECT = 1
CONNACK = 2
PUBLISH = 3
PUBACK = 4
SUBSCRIBE = 8
SUBACK = 9
PINGREQ = 12
PINGRESP = 13

LOGIN_PAGE = """
<html><body>
<form id="loginPage:loginForm" name="loginPage:loginForm" method="post" action="/Login?lang=fr&amp;step=1">
<input type="hidden" name="com.salesforce.visualforce.ViewState" value="state&amp;1" />
<input type="hidden" name="com.salesforce.visualforce.ViewStateVersion" value="202611" />
</form>
<script>loginAF=function(userEmail,password){A4J.AJAX.Submit('loginPage:loginForm',null,{'similarityGroupingId':'loginPage:loginForm:j_id7','parameters':{}})};</script>
</body></html>
"""

LOGIN_FLOW_PAGE = """
<html><body>
<form id="flowPage:flowForm" method="post" action="/LoginFlow">
<input type="hidden" name="com.salesforce.visualforce.ViewState" value="flow" />
</form>
<script>finishLoginFlowAndRedirectJS=function(){A4J.AJAX.Submit('flowPage:flowForm',null,{'similarityGroupingId':'flowPage:flowForm:j_id3','parameters':{}})};</script>
</body></html>
"""


async def wait_until(condition: Callable[[], bool]) -> None:
    """Wait until something that happens in the background is true."""
    async with asyncio.timeout(5):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


def id_token(user_id: str = USER_ID) -> str:
    """Return an unsigned id token for an account."""
    claims = {"sub": f"https://account.groupeseb.com/id/00D000000000001AAA/{user_id}"}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


def _packet(kind: int, body: bytes, flags: int = 0) -> bytes:
    """Build an MQTT packet."""
    length = len(body)
    encoded = bytearray()
    while True:
        digit = length % 128
        length //= 128
        encoded.append(digit | 0x80 if length else digit)
        if not length:
            break
    return bytes([kind << 4 | flags]) + bytes(encoded) + body


def _text(body: bytes, offset: int) -> tuple[str, int]:
    """Read a length-prefixed text from the body of an MQTT packet."""
    (length,) = struct.unpack_from("!H", body, offset)
    start = offset + 2
    return body[start : start + length].decode(), start + length


def _split(buffer: bytearray) -> tuple[int, int, bytes] | None:
    """Take a whole MQTT packet from the buffer, if there is one."""
    length = 0
    for index in range(1, 5):
        if len(buffer) <= index:
            return None
        length |= (buffer[index] & 0x7F) << (7 * (index - 1))
        if not buffer[index] & 0x80:
            break
    start = index + 1
    if len(buffer) < start + length:
        return None
    kind, flags = buffer[0] >> 4, buffer[0] & 0x0F
    body = bytes(buffer[start : start + length])
    del buffer[: start + length]
    return kind, flags, body


class Connection:
    """An MQTT client connected to the fake AWS IoT."""

    def __init__(self, socket: web.WebSocketResponse, identifier: str) -> None:
        """Initialize the connection."""
        self.socket = socket
        self.identifier = identifier
        self.subscriptions: list[str] = []

    async def publish(self, topic: str, payload: Any) -> None:
        """Send a message to the client if it subscribed to the topic."""
        if topic not in self.subscriptions:
            return
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        name = topic.encode()
        await self.socket.send_bytes(
            _packet(PUBLISH, struct.pack("!H", len(name)) + name + data)
        )


class FakeCloud:
    """The SEB cloud on localhost.

    The attributes tell what the cloud answers, the lists what it was asked.
    """

    def __init__(self, ssl_context: ssl.SSLContext) -> None:
        """Initialize the cloud."""
        self.profile: dict[str, Any] = {}
        self.profile_status = 200
        self.shadows: dict[str, dict[str, Any]] = {}
        self.statuses: dict[str, dict[str, Any]] = {}
        self.login_flags = ("false", "false", "false")
        self.login_body: str | None = None
        self.login_state: str | None = None
        self.redirect_fields: dict[str, str] = {}
        self.refresh_token = "refresh-1"
        self.next_refresh_token: str | None = None
        self.token_status = 200
        self.token_body: Any = None
        self.cognito_error: tuple[int, str] | None = None
        self.cognito_credentials: Any = None
        self.app_redirect: str | None = None
        self.redirect_loop = False
        self.policies_status = 200
        self.credentials_lifetime = 3600.0
        self.refuse_subscriptions = False
        self.handshake_delay = 0.0
        self.ignored_status_requests = 0
        self.token_text: str | None = None
        # What the cloud answers instead, by path: a status and a body.
        self.failures: dict[str, tuple[int, bytes]] = {}
        self.verify_signature = True

        self.login_forms: list[dict[str, str]] = []
        self.token_requests = 0
        self.profile_perimeters: list[str] = []
        self.profile_keys: list[str] = []
        self.policies: list[dict[str, str]] = []
        self.cognito_calls: list[tuple[str, dict[str, Any]]] = []
        self.published: list[tuple[str, bytes]] = []
        self.handshakes: list[web.Request] = []
        self.connections: list[Connection] = []
        self.connected = asyncio.Event()

        self._states: dict[str, str] = {}
        self._access_tokens = 0
        app = web.Application(middlewares=[self._fail])
        app.router.add_get("/services/oauth2/authorize/{expid}", self._authorize)
        app.router.add_get("/Login", self._login_page)
        app.router.add_post("/Login", self._login)
        app.router.add_get("/frontdoor.jsp", self._frontdoor)
        app.router.add_get("/apex/LoginFlow", self._login_flow_page)
        app.router.add_post("/LoginFlow", self._login_flow)
        app.router.add_get("/setup/RemoteAccess", self._remote_access)
        app.router.add_post("/services/oauth2/token", self._token)
        app.router.add_get("/common-api/profiles/me", self._profile)
        app.router.add_post("/entity/v1/update", self._attach_policies)
        app.router.add_post("/cognito/", self._cognito)
        app.router.add_get("/mqtt", self._mqtt)
        app.router.add_route("*", "/page", self._page)
        self._ssl_context = ssl_context
        self._server = TestServer(app, host="127.0.0.1")

    @property
    def port(self) -> int:
        """Return the port the cloud listens on."""
        port = self._server.port
        assert port is not None
        return port

    @property
    def url(self) -> str:
        """Return the address of the cloud."""
        return f"https://localhost:{self.port}"

    @property
    def access_token(self) -> str:
        """Return the access token the cloud accepts."""
        return f"access-{self._access_tokens}"

    async def __aenter__(self) -> Self:
        """Start the cloud."""
        await self._server.start_server(ssl=self._ssl_context)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the cloud."""
        await self.drop_connections()
        await self._server.close()

    async def publish(self, topic: str, payload: Any) -> None:
        """Send a message to the clients that subscribed to the topic."""
        for connection in self.connections:
            await connection.publish(topic, payload)

    async def drop_connections(self) -> None:
        """Close the MQTT connections, like a network that fails."""
        self.connected.clear()
        for connection in list(self.connections):
            await connection.socket.close()

    async def wait_connected(self) -> Connection:
        """Wait for an MQTT client, and return its connection."""
        async with asyncio.timeout(5):
            await self.connected.wait()
        return self.connections[-1]

    async def wait_published(self, topic: str, count: int = 1) -> None:
        """Wait until the clients published a number of messages on a topic."""
        await wait_until(
            lambda: [name for name, _ in self.published].count(topic) >= count
        )

    @web.middleware
    async def _fail(self, request: web.Request, handler: Any) -> web.StreamResponse:
        """Answer an error instead of what was asked for, when told to."""
        if request.path in self.failures:
            status, body = self.failures[request.path]
            return web.Response(
                status=status, body=body, content_type="text/html", charset="utf-8"
            )
        response: web.StreamResponse = await handler(request)
        return response

    async def _page(self, _: web.Request) -> web.Response:
        return web.Response(text="<html>Not what was asked</html>")

    async def _authorize(self, request: web.Request) -> web.Response:
        assert request.match_info["expid"] == "expid_moulinex000FR000fr000true"
        assert request.query["response_type"] == "token id_token refresh_token"
        assert request.query["redirect_uri"] == "gsmoduser://tokens"
        session = f"session-{len(self._states)}"
        self._states[session] = request.query["state"]
        response = web.Response(status=302, headers={"Location": "/Login?lang=fr"})
        response.set_cookie("sid", session)
        return response

    async def _login_page(self, request: web.Request) -> web.Response:
        assert request.cookies["sid"] in self._states
        return web.Response(text=LOGIN_PAGE, content_type="text/html")

    async def _login(self, request: web.Request) -> web.Response:
        assert request.cookies["sid"] in self._states
        form = dict(await request.post())
        self.login_forms.append({key: str(value) for key, value in form.items()})
        if self.login_body is not None:
            return web.Response(text=self.login_body)
        if form.get("userEmail") == EMAIL and form.get("password") == PASSWORD:
            return web.Response(
                text="<ajax/>", headers={"Location": "/frontdoor.jsp?sid=1"}
            )
        flags = ", ".join(self.login_flags)
        return web.Response(text=f"<script>afterLoginAttempt({flags});</script>")

    async def _frontdoor(self, request: web.Request) -> web.Response:
        assert request.cookies["sid"] in self._states
        target = "/frontdoor.jsp" if self.redirect_loop else "/apex/LoginFlow"
        return web.Response(
            text=f"<script>window.location.replace('{target}');</script>"
        )

    async def _login_flow_page(self, _: web.Request) -> web.Response:
        return web.Response(text=LOGIN_FLOW_PAGE, content_type="text/html")

    async def _login_flow(self, request: web.Request) -> web.Response:
        form = await request.post()
        assert form["flowPage:flowForm:j_id3"] == "flowPage:flowForm:j_id3"
        return web.Response(text="<ajax/>", headers={"Location": "/setup/RemoteAccess"})

    async def _remote_access(self, request: web.Request) -> web.Response:
        fields = {
            "access_token": self.access_token,
            "id_token": id_token(),
            "refresh_token": self.refresh_token,
            "state": self.login_state or self._states[request.cookies["sid"]],
            **self.redirect_fields,
        }
        fragment = "&".join(f"{key}={value}" for key, value in fields.items() if value)
        if self.app_redirect is not None:
            return web.Response(status=302, headers={"Location": self.app_redirect})
        return web.Response(
            text=f'<script>window.location.replace("gsmoduser://tokens#{fragment}");</script>'
        )

    async def _token(self, request: web.Request) -> web.Response:
        self.token_requests += 1
        form = await request.post()
        assert form["grant_type"] == "refresh_token"
        if self.token_text is not None:
            return web.Response(text=self.token_text, content_type="text/html")
        if self.token_body is not None or self.token_status != 200:
            return web.json_response(self.token_body, status=self.token_status)
        if form["refresh_token"] != self.refresh_token:
            return web.json_response(
                {
                    "error": "invalid_grant",
                    "error_description": "expired access/refresh token",
                },
                status=400,
            )
        self._access_tokens += 1
        tokens = {"access_token": self.access_token, "id_token": id_token()}
        if self.next_refresh_token is not None:
            self.refresh_token = self.next_refresh_token
            self.next_refresh_token = None
            tokens["refresh_token"] = self.refresh_token
        response = web.json_response(tokens)
        response.set_cookie("BrowserId", "from-the-token-endpoint")
        return response

    async def _profile(self, request: web.Request) -> web.Response:
        self.profile_perimeters.append(request.headers["DCP-Context"])
        self.profile_keys.append(request.headers["ApiKey"])
        assert request.headers["Grant-Type"] == "RCUToken"
        assert request.headers["ApiKey"]
        if request.headers["Authorization"] != f"Bearer {self.access_token}":
            return web.json_response({"message": "Unauthorized"}, status=401)
        if self.profile_status != 200:
            return web.Response(text="Oops", status=self.profile_status)
        response = web.json_response(self.profile)
        response.set_cookie("session", "from-the-api")
        return response

    async def _attach_policies(self, request: web.Request) -> web.Response:
        assert request.headers["ApiKey"]
        self.policies.append(await request.json())
        if self.policies_status != 200:
            return web.Response(text="Oops", status=self.policies_status)
        return web.json_response({})

    async def _cognito(self, request: web.Request) -> web.Response:
        assert request.headers["Content-Type"] == "application/x-amz-json-1.1"
        action = request.headers["X-Amz-Target"].removeprefix(
            "AWSCognitoIdentityService."
        )
        body = await request.json()
        self.cognito_calls.append((action, body))
        content_type = "application/x-amz-json-1.1"
        if self.cognito_error is not None:
            status, kind = self.cognito_error
            return web.json_response(
                {"__type": kind, "message": "Refused"},
                status=status,
                content_type=content_type,
            )
        if action == "GetId":
            return web.json_response(
                {"IdentityId": "eu-west-1:identity"}, content_type=content_type
            )
        credentials = self.cognito_credentials or {
            "AccessKeyId": ACCESS_KEY,
            "SecretKey": SECRET_KEY,
            "SessionToken": SESSION_TOKEN,
            "Expiration": time.time() + self.credentials_lifetime,
        }
        return web.json_response(
            {"IdentityId": body["IdentityId"], "Credentials": credentials},
            content_type=content_type,
        )

    def _signature_is_valid(self, request: web.Request) -> bool:
        """Return whether the client signed its request like AWS IoT expects."""
        now = datetime.strptime(request.query["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
        expected = signed_mqtt_path(
            AwsCredentials(ACCESS_KEY, SECRET_KEY, SESSION_TOKEN, 0),
            request.headers["Host"],
            AWS_REGION,
            now,
        )
        return request.raw_path == expected and abs(time.time() - now.timestamp()) < 60

    async def _mqtt(self, request: web.Request) -> web.StreamResponse:
        self.handshakes.append(request)
        await asyncio.sleep(self.handshake_delay)
        if self.verify_signature and not self._signature_is_valid(request):
            return web.Response(status=403)
        socket = web.WebSocketResponse(protocols=("mqtt",))
        await socket.prepare(request)
        connection: Connection | None = None
        buffer = bytearray()
        try:
            async for frame in socket:
                if frame.type is not aiohttp.WSMsgType.BINARY:
                    continue
                buffer.extend(frame.data)
                while (packet := _split(buffer)) is not None:
                    connection = await self._handle(socket, connection, *packet)
        finally:
            if connection is not None:
                with contextlib.suppress(ValueError):
                    self.connections.remove(connection)
        return socket

    async def _handle(
        self,
        socket: web.WebSocketResponse,
        connection: Connection | None,
        kind: int,
        flags: int,
        body: bytes,
    ) -> Connection | None:
        """Answer a packet of an MQTT client."""
        if kind == CONNECT:
            _, offset = _text(body, 0)
            identifier, _ = _text(body, offset + 4)
            connection = Connection(socket, identifier)
            self.connections.append(connection)
            await socket.send_bytes(_packet(CONNACK, bytes([0, 0])))
            self.connected.set()
        elif connection is None:
            await socket.close()
        elif kind == SUBSCRIBE:
            offset, granted = 2, bytearray()
            while offset < len(body):
                topic, offset = _text(body, offset)
                offset += 1
                if self.refuse_subscriptions:
                    granted.append(0x80)
                else:
                    connection.subscriptions.append(topic)
                    granted.append(1)
            await socket.send_bytes(_packet(SUBACK, body[:2] + bytes(granted)))
        elif kind == PUBLISH:
            topic, offset = _text(body, 0)
            if flags & 0x06:
                await socket.send_bytes(_packet(PUBACK, body[offset : offset + 2]))
                offset += 2
            self.published.append((topic, body[offset:]))
            await self._answer(connection, topic)
        elif kind == PINGREQ:
            await socket.send_bytes(_packet(PINGRESP, b""))
        return connection

    async def _answer(self, connection: Connection, topic: str) -> None:
        """Answer a request for a shadow or a cooking status."""
        if topic.endswith("/shadow/get"):
            thing = topic.split("/")[2]
            if thing in self.shadows:
                await connection.publish(f"{topic}/accepted", self.shadows[thing])
        elif topic.endswith("/getCookingStatus"):
            if self.ignored_status_requests:
                self.ignored_status_requests -= 1
                return
            prefix = topic.removesuffix("/getCookingStatus")
            if prefix in self.statuses:
                await connection.publish(
                    f"{prefix}/cookingStatus", self.statuses[prefix]
                )
