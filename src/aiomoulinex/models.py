"""Data models for aiomoulinex."""

from __future__ import annotations

import base64
import contextlib
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from enum import StrEnum
import json
import re
from typing import TYPE_CHECKING, Any, Self

from .catalog import MODELS
from .const import COMPANION_DOMAIN, COOKEO_DOMAIN
from .exceptions import MoulinexError
from .programs import PROGRAMS
from .regions import Region

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


class CookingState(StrEnum):
    """What an appliance is doing.

    ``IDLE`` is defined by this library, for an appliance that is turned on and
    reports no state. The other values are those of the appliance.
    """

    IDLE = "idle"
    STOPPED = "stopped"
    READY = "ready"
    PAUSED = "paused"
    WARMING = "warming"
    COOKING = "cooking"
    DEPRESSURIZATION = "depressurization"
    KEEP_WARM = "keep_warm"
    DONE = "done"
    DELAYED = "delayed"
    ALERT = "alert"


class CookingMode(StrEnum):
    """How a program was started."""

    MANUAL = "manual"
    PROGRAM = "program"
    RECIPE = "recipe"
    INGREDIENT_COOKING = "ingredient_cooking"


_DEFAULT_NAMES = {COOKEO_DOMAIN: "Cookeo", COMPANION_DOMAIN: "Companion"}
PROGRESS_STATES = frozenset({CookingState.WARMING, CookingState.DEPRESSURIZATION})


def _snake_case(value: str) -> str:
    """Return a value of the appliance, like ``keep warm``, in snake case."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    return re.sub(r"[\s-]+", "_", words).lower()


def _flag(value: Any) -> bool | None:
    """Return a yes or no of a message, ``None`` when it is something else."""
    return value if isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    """Return a text of a message, ``None`` when it is missing or empty."""
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> int | None:
    """Return a number of a message, which comes as a number or as a text."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return None


def _utc(value: Any) -> datetime | None:
    """Return a date of a message.

    The appliance writes the time in UTC, followed by an offset of one hour
    that is not true: the offset is ignored.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return None


def _merge[T: DataclassInstance](current: T, update: T) -> T:
    """Return ``current`` with the fields set in ``update`` applied."""
    return replace(
        current,
        **{
            f.name: value
            for f in fields(update)
            if (value := getattr(update, f.name)) not in (None, "")
        },
    )


@dataclass(frozen=True, slots=True)
class Tokens:
    """SEB OAuth tokens. They are left out of what is printed of them."""

    access_token: str = field(repr=False)
    id_token: str = field(repr=False)
    refresh_token: str = field(repr=False)

    @property
    def user_id(self) -> str:
        """Return the id of the SEB account, read from the id token.

        Raises:
            MoulinexError: The id token is not one of the SEB cloud.

        """
        try:
            payload = self.id_token.split(".")[1]
            claims = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            return str(claims["sub"]).rsplit("/", 1)[-1]
        except (IndexError, KeyError, TypeError, ValueError) as err:
            msg = "The id token does not tell which account it belongs to"
            raise MoulinexError(msg) from err

    def to_dict(self) -> dict[str, str]:
        """Return the tokens as a dictionary that can be stored."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Self:
        """Return the tokens stored with ``to_dict``."""
        return cls(
            access_token=data["access_token"],
            id_token=data["id_token"],
            refresh_token=data["refresh_token"],
        )


@dataclass(frozen=True, slots=True)
class AwsCredentials:
    """Temporary AWS credentials from Cognito."""

    access_key: str
    secret_key: str = field(repr=False)
    session_token: str = field(repr=False)
    expiration: int


@dataclass(frozen=True, slots=True)
class Appliance:
    """Cookeo or Companion linked to the SEB account."""

    name: str
    thing_type: str
    thing_uuid: str
    cmmf: str | None = None
    domain: str = COOKEO_DOMAIN
    model: str | None = None
    catalog_id: str | None = None

    @property
    def programs(self) -> tuple[str, ...]:
        """Return the names of the programs this kind of appliance can run."""
        return tuple(sorted(set(PROGRAMS.get(self.domain, {}).values())))

    def program(self, status: CookingStatus | None) -> str | None:
        """Return the name of the program of a status, e.g. ``browning_high``.

        It is ``None`` without a program, and for a program that is not known.
        """
        if status is None or status.program_key is None:
            return None
        return PROGRAMS.get(self.domain, {}).get(status.program_key)

    @property
    def thing_name(self) -> str:
        """Return the AWS IoT thing name."""
        return f"{self.thing_type}-{self.thing_uuid}"

    @property
    def cooking_status_topic(self) -> str:
        """Return the MQTT topic of the cooking status."""
        return f"{self.thing_type}/{self.thing_uuid}/cookingStatus"

    @property
    def status_request_topic(self) -> str:
        """Return the MQTT topic that makes the appliance send its status."""
        return f"{self.thing_type}/{self.thing_uuid}/getCookingStatus"

    @property
    def shadow_topic(self) -> str:
        """Return the MQTT topic prefix of the AWS IoT shadow."""
        return f"$aws/things/{self.thing_name}/shadow"


@dataclass(frozen=True, slots=True)
class Notification:
    """Message the appliance shows on its screen, like an instruction."""

    code: str | None = None
    severity: str | None = None
    time_to_live: int | None = None


@dataclass(frozen=True, slots=True)
class Recipe:
    """Recipe in progress, or ingredient for an ingredient cooking."""

    title: str | None = None
    functional_id: str | None = None
    variant_id: str | None = None


@dataclass(frozen=True, slots=True)
class Step:
    """Step of the recipe in progress. The index of the first step is 0."""

    index: int | None = None
    functional_id: str | None = None
    type: str | None = None


@dataclass(frozen=True, slots=True)
class CookingStatus:
    """Cooking status of an appliance, as told by one message.

    Each message holds the whole status: a value that is ``None`` does not
    apply at that moment.
    """

    received_at: datetime
    raw_state: str | None = None
    raw_mode: str | None = None
    progress: int | None = None
    elapsed_time: int | None = None
    remaining_time: int | None = None
    program_key: str | None = None
    duration: int | None = None
    temperature: int | None = None
    error: str | None = None
    alert_description: str | None = None
    notification: Notification | None = None
    recipe: Recipe | None = None
    step: Step | None = None
    event_date: datetime | None = None
    operation_start: datetime | None = None
    version: int | None = None

    @property
    def state(self) -> CookingState | None:
        """Return what the appliance is doing.

        It is ``IDLE`` when the appliance reports no state, and ``None`` when
        it reports one this library does not know: see ``raw_state`` then.
        """
        if self.raw_state is None:
            return CookingState.IDLE
        with contextlib.suppress(ValueError):
            return CookingState(_snake_case(self.raw_state))
        return None

    @property
    def mode(self) -> CookingMode | None:
        """Return how the program was started, ``None`` without a known mode."""
        if self.raw_mode is not None:
            with contextlib.suppress(ValueError):
                return CookingMode(_snake_case(self.raw_mode))
        return None

    @property
    def phase_progress(self) -> int | None:
        """Return the progress of warming up or releasing pressure, in percent.

        It is ``None`` in every other state. The appliance may still send a
        progress then, but it means nothing: its app does not show it either.
        """
        return self.progress if self.state in PROGRESS_STATES else None

    @classmethod
    def from_json(
        cls, payload: dict[str, Any], received_at: datetime | None = None
    ) -> Self:
        """Parse a ``cookingStatus`` MQTT message, received now by default."""
        operation = _mapping(payload.get("currentOperation"))
        notification = payload.get("notification")
        recipe = payload.get("currentRecipe")
        step = payload.get("currentStep")
        return cls(
            received_at=received_at or datetime.now(UTC),
            raw_state=_text(payload.get("currentStatus")),
            raw_mode=_text(payload.get("mode")),
            progress=_number(payload.get("progress")),
            elapsed_time=_number(payload.get("elapsedTime")),
            remaining_time=_number(payload.get("remainingTime")),
            program_key=_text(_mapping(operation.get("program")).get("key")),
            duration=_parameter(operation.get("parameters"), "DURATION"),
            temperature=_parameter(operation.get("parameters"), "TEMPERATURE"),
            error=_text(payload.get("error")),
            alert_description=_text(payload.get("alertDescr")),
            notification=(
                Notification(
                    code=_text(notification.get("code")),
                    severity=_text(notification.get("severity")),
                    time_to_live=_number(notification.get("timeToLive")),
                )
                if isinstance(notification, dict)
                else None
            ),
            recipe=(
                Recipe(
                    title=_text(recipe.get("title")),
                    functional_id=_text(recipe.get("functionalId")),
                    variant_id=_text(
                        _mapping(recipe.get("variant")).get("functionalId")
                    ),
                )
                if isinstance(recipe, dict)
                else None
            ),
            step=(
                Step(
                    index=_number(step.get("stepIndex")),
                    functional_id=_text(step.get("functionalId")),
                    type=_text(_mapping(step.get("type")).get("key")),
                )
                if isinstance(step, dict)
                else None
            ),
            event_date=_utc(payload.get("eventDate")),
            operation_start=_utc(payload.get("operationStart")),
            version=_number(payload.get("version")),
        )


def _model(identifier: Any, perimeter: Any) -> str | None:
    """Return the name of a model, as the brand of the account calls it.

    A model that the brand does not sell gets the name of another brand.
    """
    names = MODELS.get(identifier) if isinstance(identifier, str) else None
    if not names:
        return None
    region = next((r for r in Region if r.profile_perimeter == perimeter), None)
    if region is not None and region.brand in names:
        return names[region.brand]
    return next(iter(names.values()))


def _mapping(value: Any) -> dict[str, Any]:
    """Return an object of a message, empty when it is something else."""
    return value if isinstance(value, dict) else {}


def _parameter(parameters: Any, kind: str) -> int | None:
    """Return a parameter of a program, 0 and -1 stand for one that is not set."""
    for parameter in parameters if isinstance(parameters, list) else []:
        if isinstance(parameter, dict) and parameter.get("type") == kind:
            value = _number(parameter.get("value"))
            return value if value is not None and value > 0 else None
    return None


@dataclass(frozen=True, slots=True)
class Firmware:
    """Firmware versions reported in the shadow."""

    ui_version: str | None = None
    wifi_version: str | None = None
    update_priority: str | None = None


@dataclass(frozen=True, slots=True)
class ApplianceShadow:
    """Reported state of the AWS IoT shadow, filled in by partial updates."""

    connected: bool | None = None
    last_connection: int | None = None
    last_disconnection: int | None = None
    synchronizing: bool | None = None
    firmware: Firmware = field(default_factory=Firmware)

    def merge(self, update: Self) -> Self:
        """Return this shadow updated with the fields set in ``update``."""
        return replace(
            _merge(self, update), firmware=_merge(self.firmware, update.firmware)
        )

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Self:
        """Parse a shadow ``get``, ``update`` or ``documents`` MQTT message."""
        payload = _mapping(payload.get("current")) or payload
        reported = _mapping(_mapping(payload.get("state")).get("reported"))
        status = _mapping(reported.get("status"))
        firmware = _mapping(reported.get("firmware"))
        files = firmware.get("files")
        versions = {
            kind: _text(file.get("version"))
            for file in (files if isinstance(files, list) else [])
            if isinstance(file, dict) and (kind := _text(file.get("type")))
        }
        return cls(
            connected=_flag(status.get("connected")),
            last_connection=_number(status.get("lastConnection")),
            last_disconnection=_number(status.get("lastDisconnection")),
            synchronizing=_flag(status.get("synchronizing")),
            firmware=Firmware(
                ui_version=versions.get("ui"),
                wifi_version=versions.get("wifi"),
                update_priority=_text(firmware.get("priority")),
            ),
        )


@dataclass(frozen=True, slots=True)
class ApplianceState:
    """Latest cooking status and shadow of an appliance.

    The status is ``None`` until the appliance reports one, and again each
    time it turns off.
    """

    status: CookingStatus | None = None
    shadow: ApplianceShadow = field(default_factory=ApplianceShadow)


@dataclass(frozen=True, slots=True)
class Profile:
    """Account of the SEB cloud and its connected appliances."""

    profile_perimeter: str | None = None
    appliances: tuple[Appliance, ...] = ()

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Self:
        """Parse the profile of the SEB cloud, keep the Cookeo and Companion."""
        return cls(
            profile_perimeter=_text(payload.get("profilePerimeter")),
            appliances=tuple(
                Appliance(
                    name=product.get("name") or _DEFAULT_NAMES[domain],
                    thing_type=product["iot"]["thingType"],
                    thing_uuid=product["iot"]["functionalId"],
                    cmmf=(
                        str(product["cmmf"])
                        if product.get("cmmf") is not None
                        else None
                    ),
                    domain=domain,
                    model=_model(
                        product.get("appliance"), payload.get("profilePerimeter")
                    ),
                    catalog_id=_text(product.get("appliance")),
                )
                for product in (payload.get("household") or {}).get("products") or []
                if product.get("iot")
                for domain in product.get("domains") or []
                if domain in PROGRAMS
            ),
        )
