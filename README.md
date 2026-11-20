# Python: Moulinex

[![GitHub Release][releases-shield]][releases]
[![Python Versions][python-versions-shield]][pypi]
[![License][license-shield]](LICENSE.md)
[![Build Status][build-shield]][build]

Asynchronous Python client for the Moulinex Cookeo multicookers and Companion cooking robots connected to Wi-Fi.

## About

The client goes through the same Groupe SEB cloud as the Moulinex app, so the appliance must already be set up in that app. It logs in with the account of the app, then follows what each appliance reports: the cooking state, the program, the remaining time, the recipe in progress, and whether the appliance is turned on.

- Fully asynchronous, built on [aiohttp](https://github.com/aio-libs/aiohttp) and [aiomqtt](https://github.com/empicano/aiomqtt). No AWS SDK.
- Push based: the appliances tell the cloud what changed, and the client tells you.
- One connection per account, for all its appliances.
- Renews the tokens and connects again by itself.
- Typed models and typed errors.

The library is read-only. The cloud offers no way to control an appliance, so programs can only be started or stopped on the appliance itself.

## Supported appliances

| Model                  | Tested |
| ---------------------- | ------ |
| Cookeo Touch WiFi      | ✓      |
| Cookeo Touch Pro WiFi  | ✓      |
| Cookeo Touch Mini WiFi | ✓      |
| Cookeo Wi-Fi 8-in-1    |        |
| Cookeo Wi-Fi 9-in-1    |        |
| Cookeo Wi-Fi 10-in-1   |        |
| Cookeo Infinity        | ✓      |
| i-Companion Touch XL   |        |

The untested models use the same cloud and should work. Bluetooth-only models such as the Cookeo Connect are not supported, and neither are other Groupe SEB appliances such as the OptiGrill.

## Installation

```bash
pip install aiomoulinex
```

## Usage

### Logging in

```python
import aiohttp

from aiomoulinex import LoginError, Region, login

async with aiohttp.ClientSession() as session:
    try:
        tokens = await login(
            session, "jane@example.com", "password", Region.MOULINEX_FR
        )
    except LoginError as err:
        print(err.reason)  # invalid_auth, email_not_verified, account_locked, ...
```

`Region` is the brand and country of the account, the one picked in the Moulinex app. The library only uses the connections of the session it is given: the cookies of the cloud stay out of it, so it is safe to pass a session that is shared with other code.

Store `tokens.to_dict()` and the region, not the password. `Tokens.from_dict()` reads them back, and `tokens.user_id` identifies the account: it stays the same when the email changes.

### Following the appliances

```python
import asyncio

import aiohttp

from aiomoulinex import ApplianceUpdated, Event, MoulinexClient, Region, Tokens


async def main(tokens: Tokens) -> None:
    async with aiohttp.ClientSession() as session:
        client = MoulinexClient(session, tokens, Region.MOULINEX_FR)

        def on_event(event: Event) -> None:
            if isinstance(event, ApplianceUpdated):
                status = event.state.status
                print(
                    event.appliance.name,
                    status.state if status else None,
                    event.appliance.program(status),
                )

        client.add_listener(on_event)
        await client.connect()
        print(client.appliances)
        await asyncio.sleep(3600)
        await client.disconnect()
```

`connect()` refreshes the tokens, reads the appliances of the account, connects to the cloud and asks each appliance for its state. It returns once that state is known: the shadow of each appliance, and the cooking status of those that are turned on. An appliance that does not answer within 3 seconds is not waited for any longer. From then on:

- `client.appliances` holds the appliances of the account, by `thing_uuid`.
- `client.states` holds the last known `ApplianceState` of each appliance. Its `status` is what the appliance is cooking, its `shadow` tells whether it is turned on and which firmware it runs.
- `client.connected` is `False` while the connection is lost. The client keeps trying to connect again, and `client.states` keeps the last known state.
- `client.error` is why the client is not connected, if that is known.
- `client.tokens` holds the tokens as last refreshed.

Instead of a listener, the events can be read with `async for event in client.events()`. It ends when the client disconnects.

An appliance that is turned on needs a few seconds before it answers. The client asks for its status again 4, 10 and 20 seconds after it turned on, until it answers.

### Events

| Event                  | Meaning                                                                |
| ---------------------- | ---------------------------------------------------------------------- |
| `ApplianceUpdated`     | The cooking status or the shadow of an appliance changed.              |
| `ApplianceAdded`       | An appliance was linked to the account after the client connected.     |
| `ApplianceRemoved`     | An appliance is no longer linked to the account.                       |
| `ConnectionLost`       | The connection is lost. The client keeps trying to connect again.      |
| `ConnectionRestored`   | The connection is back.                                                |
| `AuthenticationFailed` | The cloud refused the refresh token. The client stopped: log in again. |
| `TokensUpdated`        | The refresh token changed. Store the tokens to connect the next time.  |

The client reads the appliances of the account again each time it renews its credentials, a little less than once an hour. That is when added and removed appliances are noticed.

### Cooking status

`ApplianceState.status` is a `CookingStatus`, for a Companion as well. Each message of an appliance holds its whole status, so a status is never mixed with the one before: a value that is `None` does not apply at that moment.

The status itself is `None` until the appliance reports one, and again each time it turns off. What it was cooking is not shown again when it is turned back on.

- `state`: a `CookingState`. `idle` is defined by this library, for an appliance that is turned on and reports no state. The other values are those of the appliance: `stopped`, `ready`, `paused`, `warming`, `cooking`, `depressurization`, `keep_warm`, `done`, `delayed` and `alert`. It is `None` for a value this library does not know, which `raw_state` then holds.
- `mode`: a `CookingMode`: `manual`, `program`, `recipe` or `ingredient_cooking`.
- `appliance.program(status)`: the program in use, such as `cooking_high_pressure`. `appliance.programs` lists the programs that kind of appliance can run.
- `phase_progress`: a percentage, while the appliance warms up or releases pressure. `None` in every other state, like in the Moulinex app. `progress` holds what the appliance sent.
- `elapsed_time` and `remaining_time`: in seconds. The appliance does not send them every second: count from `received_at`, the time the message came in.
- `duration` and `temperature`: what the program is set to, in seconds and degrees Celsius.
- `recipe` and `step`: the recipe in progress and its current step. For an ingredient cooking, the recipe holds the ingredient.
- `notification`: the code of a message the appliance shows on its screen.
- `error` and `alert_description`: what the appliance reports when something is wrong.
- `event_date` and `operation_start`: the dates the appliance sends, read as UTC.

`ApplianceState.shadow.connected` tells whether the appliance is turned on and connected to the cloud.

### Errors

All errors are subclasses of `MoulinexError`:

| Error                         | Meaning                                                        |
| ----------------------------- | -------------------------------------------------------------- |
| `MoulinexConnectionError`     | The cloud could not be reached, or it did not answer in time.  |
| `MoulinexAuthenticationError` | The cloud refused the tokens. The account has to log in again. |
| `LoginError`                  | The cloud refused the email and password. `reason` tells why.  |

`LoginError` is a `MoulinexAuthenticationError`, so catch it first. A plain `MoulinexError` means the cloud answered something this library does not understand.

### TLS

Creating a TLS context reads files from disk. The client does that in an executor. To reuse a context of your own, pass it as `ssl_context`.

## Changelog & Releases

This repository keeps a change log using [GitHub's releases][releases] functionality.

Releases are based on [Semantic Versioning][semver], and use the format of `MAJOR.MINOR.PATCH`.

## Setting up a development environment

This Python project is managed with [uv][uv]. You need at least Python 3.13.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy src tests
```

The checks also run as hooks with `uv run prek run --all-files`. The Prettier hook needs Node.js and `npm install`.

The tests run against a fake cloud that is part of the test suite, and need no account. `tests/fixtures/README.md` tells which payloads come from a real account.

## Capturing what an appliance sends

Some models have not been tested, the Companion among them. To help with yours, capture what it sends:

```bash
uv run python scripts/debug.py capture --seconds 600
uv run python scripts/debug.py check capture.jsonl
```

`capture` logs in, then writes every message of the appliances of the account to `capture.jsonl`. Start a program on the appliance while it runs. The identifiers of the appliances and of the recipes are replaced, so the file can be shared in an issue. The first run asks for the email and password of the account and keeps a login token in a `.env` file, which git ignores. The password is not stored.

`check` prints what the library does not understand in a capture: fields it does not read, and states, modes and programs it does not know.

## Disclaimer

This library is not affiliated with Groupe SEB, Moulinex or Tefal. It relies on an undocumented cloud API that can change at any time.

## License

MIT License, see [LICENSE.md](LICENSE.md).

[build-shield]: https://github.com/piitaya/python-moulinex/actions/workflows/tests.yaml/badge.svg
[build]: https://github.com/piitaya/python-moulinex/actions
[license-shield]: https://img.shields.io/github/license/piitaya/python-moulinex.svg
[pypi]: https://pypi.org/project/aiomoulinex/
[python-versions-shield]: https://img.shields.io/pypi/pyversions/aiomoulinex.svg
[releases-shield]: https://img.shields.io/github/release/piitaya/python-moulinex.svg
[releases]: https://github.com/piitaya/python-moulinex/releases
[semver]: http://semver.org/spec/v2.0.0.html
[uv]: https://docs.astral.sh/uv/
