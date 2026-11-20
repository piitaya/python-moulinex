"""Follow the appliances of an account, and print what changes.

Usage: python monitor.py [--debug] <email> [region]

The region is one of the values of `Region`, `moulinex_fr` by default. With
`--debug`, every message of the cloud is printed as it comes in.
"""

from __future__ import annotations

import asyncio
import contextlib
from getpass import getpass
import logging
import sys

import aiohttp

from aiomoulinex import (
    ApplianceUpdated,
    Event,
    LoginError,
    MoulinexClient,
    MoulinexError,
    Region,
    login,
)


def on_event(event: Event) -> None:
    """Print an event of the client."""
    if not isinstance(event, ApplianceUpdated):
        print(type(event).__name__)
        return
    status, shadow = event.state.status, event.state.shadow
    print(
        f"{event.appliance.name}:",
        "on" if shadow.connected else "off",
        status.state if status else None,
        event.appliance.program(status),
        status.phase_progress if status else None,
    )


async def main(email: str, region: Region) -> None:
    """Log in, connect to the cloud and print the events until interrupted."""
    async with aiohttp.ClientSession() as session:
        try:
            tokens = await login(session, email, getpass(), region)
        except LoginError as err:
            print(f"Login refused: {err.reason}")
            return
        client = MoulinexClient(session, tokens, region)
        client.add_listener(on_event)
        try:
            await client.connect()
        except MoulinexError as err:
            print(f"Could not connect: {type(err).__name__}: {err}")
            return
        print("Following", ", ".join(a.name for a in client.appliances.values()))
        try:
            await asyncio.Event().wait()
        finally:
            await client.disconnect()


if __name__ == "__main__":
    arguments = [argument for argument in sys.argv[1:] if argument != "--debug"]
    if len(arguments) not in (1, 2):
        sys.exit(__doc__)
    if "--debug" in sys.argv:
        logging.basicConfig(format="%(asctime)s %(message)s")
        logging.getLogger("aiomoulinex.client").setLevel(logging.DEBUG)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(arguments[0], Region((arguments[1:] or ["moulinex_fr"])[0])))
