"""Capture what an account receives from the cloud, and check a capture.

    python scripts/debug.py capture [--seconds 60] [--raw] [--region moulinex_fr] [file]
    python scripts/debug.py check <file>

`capture` logs in, follows the appliances and writes every message to a file,
one JSON object per line. The first run asks for the email and the password
of the account, then keeps a login token in the `.env` file at the root of
the repository, which git ignores. The password is not stored. Unless `--raw`
is given, the identifiers of the appliances and of the recipes are replaced,
so the file can be shared.

`check` reads a capture and prints what the library does not understand in
it: fields it does not read, and states, modes and programs it does not know.

Both only read: nothing is sent to an appliance but a request for its status.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import asdict
from datetime import UTC, datetime
from getpass import getpass
import json
from pathlib import Path
import sys
from typing import Any

import aiohttp

from aiomoulinex import (
    PROGRAMS,
    Appliance,
    CookingStatus,
    Event,
    LoginError,
    MoulinexAuthenticationError,
    MoulinexClient,
    MoulinexError,
    Region,
    Tokens,
    TokensUpdated,
    login,
)

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
STORED_LOGIN = "MOULINEX_REFRESH_TOKEN"

# What the library reads in a cooking status and in a shadow. A `*` stands
# for each item of a list.
READ_STATUS = {
    "currentStatus",
    "mode",
    "progress",
    "elapsedTime",
    "remainingTime",
    "error",
    "alertDescr",
    "eventDate",
    "operationStart",
    "version",
    "currentOperation.program.key",
    "currentOperation.parameters.*.type",
    "currentOperation.parameters.*.value",
    "notification.code",
    "notification.severity",
    "notification.timeToLive",
    "currentRecipe.title",
    "currentRecipe.functionalId",
    "currentRecipe.variant.functionalId",
    "currentStep.stepIndex",
    "currentStep.functionalId",
    "currentStep.type.key",
}
READ_SHADOW = {
    "state.reported.status.connected",
    "state.reported.status.lastConnection",
    "state.reported.status.lastDisconnection",
    "state.reported.status.synchronizing",
    "state.reported.firmware.priority",
    "state.reported.firmware.files.*.type",
    "state.reported.firmware.files.*.version",
}
READ_PARAMETERS = {"DURATION", "TEMPERATURE"}
# What is known and left out on purpose, so it is not reported each time.
LEFT_OUT = {
    "currentOperation.program.name",
    "currentStep.type.name",
    "state.reported.attributes.*.key",
    "state.reported.attributes.*.value",
    "state.reported.firmware.files.*.name",
    "state.reported.firmware.files.*.product",
    "state.reported.firmware.updateDate",
    "state.reported.firmware.version",
    "state.reported.status.otaUpdated",
    "state.reported.status.updating",
}


def read_env() -> dict[str, str]:
    """Return the values of the .env file."""
    if not ENV_PATH.exists():
        return {}
    lines = ENV_PATH.read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def store_env(key: str, value: str | None) -> None:
    """Write, replace or remove one value of the .env file."""
    values = read_env()
    if value is None:
        values.pop(key, None)
    else:
        values[key] = value
    ENV_PATH.touch(mode=0o600)
    ENV_PATH.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


def ask_credentials() -> tuple[str, str] | None:
    """Ask for the email and password, unless a login token is stored."""
    if read_env().get(STORED_LOGIN):
        return None
    return input("Email of the account: "), getpass("Password: ")


async def get_tokens(
    session: aiohttp.ClientSession, region: Region, credentials: tuple[str, str] | None
) -> Tokens:
    """Return the stored login token, or log in and store a new one."""
    if credentials is None:
        return Tokens(
            access_token="", id_token="", refresh_token=read_env()[STORED_LOGIN]
        )
    tokens = await login(session, *credentials, region)
    store_env(STORED_LOGIN, tokens.refresh_token)
    return tokens


def anonymize(payload: Any) -> None:
    """Replace the identifiers of a recipe in a cooking status."""
    if not isinstance(payload, dict):
        return
    recipe, step = payload.get("currentRecipe"), payload.get("currentStep")
    # The identifier of an ingredient is not personal: it is the same for all.
    if isinstance(recipe, dict) and not str(recipe.get("functionalId")).startswith(
        "M_"
    ):
        recipe.update(title="Recipe", functionalId="1000001")
        if isinstance(recipe.get("variant"), dict):
            recipe["variant"]["functionalId"] = "2000001"
        if isinstance(step, dict):
            step["functionalId"] = f"30000{step.get('stepIndex', 0):02}"


class RecordingClient(MoulinexClient):
    """Client that keeps every message it receives."""

    messages: list[dict[str, Any]]

    def _handle_message(self, topic: str, payload: Any) -> Any:
        with contextlib.suppress(TypeError, ValueError):
            self.messages.append(
                {
                    "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
                    "topic": topic,
                    "payload": json.loads(payload),
                }
            )
        return super()._handle_message(topic, payload)


async def capture(
    region: Region, seconds: float, credentials: tuple[str, str] | None
) -> tuple[list[Appliance], list[dict[str, Any]]]:
    """Follow the appliances of the account, return them and what they sent."""

    def on_event(event: Event) -> None:
        if isinstance(event, TokensUpdated):
            store_env(STORED_LOGIN, event.tokens.refresh_token)
        else:
            print(type(event).__name__)

    async with aiohttp.ClientSession() as session:
        try:
            tokens = await get_tokens(session, region, credentials)
        except LoginError as err:
            sys.exit(f"Login refused: {err.reason}")
        client = RecordingClient(session, tokens, region)
        client.messages = []
        client.add_listener(on_event)
        try:
            await client.connect()
        except MoulinexAuthenticationError:
            store_env(STORED_LOGIN, None)
            sys.exit("The stored login token is refused. Run again to log in.")
        except MoulinexError as err:
            sys.exit(f"Could not connect: {type(err).__name__}: {err}")
        appliances = list(client.appliances.values())
        print("Following", ", ".join(a.name for a in appliances) or "no appliance")
        print(f"Capturing for {seconds:.0f} seconds, Ctrl+C to stop earlier.")
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(seconds)
        await client.disconnect()
    return appliances, client.messages


def write_capture(
    path: Path,
    appliances: list[Appliance],
    messages: list[dict[str, Any]],
    *,
    raw: bool,
) -> None:
    """Write a capture to a file, without the identifiers unless asked for."""
    header = {"appliances": [asdict(appliance) for appliance in appliances]}
    text = "\n".join(json.dumps(line) for line in [header, *messages])
    if not raw:
        for index, appliance in enumerate(appliances, start=1):
            text = text.replace(
                appliance.thing_uuid, f"{index:08}-0000-0000-0000-000000000000"
            )
        lines = [json.loads(line) for line in text.splitlines()]
        for line in lines[1:]:
            anonymize(line["payload"])
        text = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
    path.write_text(text + "\n")
    print(f"{len(messages)} messages written to {path}")


def paths(value: Any, prefix: str = "") -> set[str]:
    """Return the path of each value in a message."""
    if isinstance(value, dict):
        return {
            path
            for key, item in value.items()
            for path in paths(item, f"{prefix}.{key}" if prefix else key)
        }
    if isinstance(value, list):
        return {path for item in value for path in paths(item, f"{prefix}.*")}
    return {prefix}


def check_status(payload: dict[str, Any], programs: dict[str, str]) -> dict[str, Any]:
    """Return what the library does not understand in a cooking status."""
    status = CookingStatus.from_json(payload)
    operation = payload.get("currentOperation")
    parameters = operation.get("parameters") if isinstance(operation, dict) else None
    kinds = [
        parameter.get("type")
        for parameter in (parameters if isinstance(parameters, list) else [])
        if isinstance(parameter, dict)
    ]
    return {
        "Fields of the cooking status that are not read": paths(payload)
        - READ_STATUS
        - LEFT_OUT,
        "States that are not known": {status.raw_state} if status.state is None else (),
        "Modes that are not known": (
            {status.raw_mode} if status.raw_mode and status.mode is None else ()
        ),
        "Programs that are not known": (
            {status.program_key}
            if status.program_key and status.program_key not in programs
            else ()
        ),
        "Parameters of a program that are not read": set(kinds) - READ_PARAMETERS,
    }


def check(path: Path) -> None:
    """Print what the library does not understand in a capture."""
    header, *messages = [json.loads(line) for line in path.read_text().splitlines()]
    domains = {
        f"{a['thing_type']}/{a['thing_uuid']}": a["domain"]
        for a in header["appliances"]
    }
    findings: dict[str, set[str]] = {}
    for message in messages:
        topic, payload = message["topic"], message["payload"]
        if not isinstance(payload, dict):
            continue
        if topic.endswith("/cookingStatus"):
            domain = domains.get(topic.removesuffix("/cookingStatus"), "")
            found = check_status(payload, PROGRAMS.get(domain, {}))
        else:
            state = payload.get("current", payload).get("state")
            found = {
                "Fields of the shadow that are not read": paths(state, "state")
                - READ_SHADOW
                - LEFT_OUT
            }
        for kind, values in found.items():
            findings.setdefault(kind, set()).update(str(value) for value in values)

    print(f"{len(messages)} messages of {len(domains)} appliances")
    if not any(findings.values()):
        print("The library understands all of it.")
    for kind, values in sorted(findings.items()):
        if values:
            print(f"\n{kind}:")
            print("\n".join(f"  {value}" for value in sorted(values)))


def main() -> None:
    """Run the command given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture", help="write the messages to a file")
    capture_parser.add_argument(
        "file", nargs="?", type=Path, default=Path("capture.jsonl")
    )
    capture_parser.add_argument("--seconds", type=float, default=60)
    capture_parser.add_argument("--region", type=Region, default=Region.MOULINEX_FR)
    capture_parser.add_argument(
        "--raw", action="store_true", help="keep the identifiers"
    )
    check_parser = commands.add_parser("check", help="check a captured file")
    check_parser.add_argument("file", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "check":
        check(arguments.file)
        return
    credentials = ask_credentials()
    with contextlib.suppress(KeyboardInterrupt):
        appliances, messages = asyncio.run(
            capture(arguments.region, arguments.seconds, credentials)
        )
        write_capture(arguments.file, appliances, messages, raw=arguments.raw)


if __name__ == "__main__":
    main()
