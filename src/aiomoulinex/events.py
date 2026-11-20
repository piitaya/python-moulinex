"""Events a client tells its listeners about."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .exceptions import MoulinexAuthenticationError, MoulinexError
    from .models import Appliance, ApplianceState, Tokens


@dataclass(frozen=True, slots=True)
class ApplianceUpdated:
    """The cooking status or the shadow of an appliance changed."""

    appliance: Appliance
    state: ApplianceState


@dataclass(frozen=True, slots=True)
class ApplianceAdded:
    """An appliance was linked to the account after the client connected."""

    appliance: Appliance


@dataclass(frozen=True, slots=True)
class ApplianceRemoved:
    """An appliance is no longer linked to the account."""

    appliance: Appliance


@dataclass(frozen=True, slots=True)
class ConnectionLost:
    """The connection to the cloud is lost, the client keeps trying to connect."""

    error: MoulinexError


@dataclass(frozen=True, slots=True)
class ConnectionRestored:
    """The connection to the cloud is back."""


@dataclass(frozen=True, slots=True)
class AuthenticationFailed:
    """The cloud refused the refresh token, the client stopped.

    The account has to log in again, and a new client has to be created.
    """

    error: MoulinexAuthenticationError


@dataclass(frozen=True, slots=True)
class TokensUpdated:
    """The refresh token changed, store the tokens to connect the next time.

    Refreshing the tokens does not change the refresh token as a rule, so this
    seldom happens.
    """

    tokens: Tokens


type Event = (
    ApplianceUpdated
    | ApplianceAdded
    | ApplianceRemoved
    | ConnectionLost
    | ConnectionRestored
    | AuthenticationFailed
    | TokensUpdated
)
