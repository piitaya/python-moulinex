"""Asynchronous Python client for the Moulinex Cookeo and Companion."""

from .api import attach_policies, fetch_profile
from .auth import login, refresh
from .catalog import COUNTRIES
from .client import MoulinexClient
from .cognito import fetch_credentials
from .const import COMPANION_DOMAIN, COOKEO_DOMAIN
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
    LoginError,
    LoginFailure,
    MoulinexAuthenticationError,
    MoulinexConnectionError,
    MoulinexError,
)
from .models import (
    Appliance,
    ApplianceShadow,
    ApplianceState,
    AwsCredentials,
    CookingMode,
    CookingState,
    CookingStatus,
    Firmware,
    Notification,
    Profile,
    Recipe,
    Step,
    Tokens,
)
from .programs import COMPANION_PROGRAMS, COOKEO_PROGRAMS, PROGRAMS
from .regions import Region

__all__ = [
    "COMPANION_DOMAIN",
    "COMPANION_PROGRAMS",
    "COOKEO_DOMAIN",
    "COOKEO_PROGRAMS",
    "COUNTRIES",
    "PROGRAMS",
    "Appliance",
    "ApplianceAdded",
    "ApplianceRemoved",
    "ApplianceShadow",
    "ApplianceState",
    "ApplianceUpdated",
    "AuthenticationFailed",
    "AwsCredentials",
    "ConnectionLost",
    "ConnectionRestored",
    "CookingMode",
    "CookingState",
    "CookingStatus",
    "Event",
    "Firmware",
    "LoginError",
    "LoginFailure",
    "MoulinexAuthenticationError",
    "MoulinexClient",
    "MoulinexConnectionError",
    "MoulinexError",
    "Notification",
    "Profile",
    "Recipe",
    "Region",
    "Step",
    "Tokens",
    "TokensUpdated",
    "attach_policies",
    "fetch_credentials",
    "fetch_profile",
    "login",
    "refresh",
]
