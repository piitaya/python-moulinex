"""Tests for the models."""

from __future__ import annotations

from datetime import UTC, datetime
import re

import pytest

from aiomoulinex import (
    Appliance,
    ApplianceShadow,
    ApplianceState,
    AwsCredentials,
    CookingMode,
    CookingState,
    CookingStatus,
    Firmware,
    MoulinexError,
    Notification,
    Profile,
    Recipe,
    Region,
    Step,
    Tokens,
    TokensUpdated,
)
from aiomoulinex.catalog import MODELS

from .conftest import COMPANION, COOKEO, load_fixture
from .fake_cloud import USER_ID, id_token

RECEIVED = datetime(2026, 11, 20, 17, 42, 8, tzinfo=UTC)


def status(name: str) -> CookingStatus:
    """Return the cooking status of a fixture."""
    return CookingStatus.from_json(load_fixture(f"status_{name}.json"), RECEIVED)


def test_tokens() -> None:
    """Test the id of the account and storing the tokens."""
    tokens = Tokens(access_token="a", id_token=id_token(), refresh_token="r")
    assert tokens.user_id == USER_ID
    assert tokens.to_dict() == {
        "access_token": "a",
        "id_token": id_token(),
        "refresh_token": "r",
    }
    assert Tokens.from_dict(tokens.to_dict()) == tokens


def test_tokens_are_not_printed() -> None:
    """Test that the tokens and the AWS secrets stay out of what is printed."""
    tokens = Tokens(
        access_token="secret-a", id_token="secret-i", refresh_token="secret-r"
    )
    assert "secret" not in repr(tokens)
    assert "secret" not in repr(TokensUpdated(tokens))
    credentials = AwsCredentials("key", "secret-k", "secret-s", 0)
    assert "secret" not in repr(credentials)
    assert "key" in repr(credentials)


@pytest.mark.parametrize("token", ["abc", "a.b.c", "a.eyJ9.c", "a.WzFd.c", ""])
def test_tokens_of_another_kind(token: str) -> None:
    """Test an id token that does not tell which account it belongs to."""
    tokens = Tokens(access_token="a", id_token=token, refresh_token="r")
    with pytest.raises(MoulinexError, match="which account"):
        _ = tokens.user_id


# The markets where the configuration of the SEB cloud lists a Cookeo or a
# Companion: name in the cloud, brand and market of the login, and one of the
# languages the cloud lists for that market.
MARKETS = [
    (Region.MOULINEX_FR, "moulinex_fr", "MOULINEX_FR", "moulinex", "FR", "fr"),
    (Region.TEFAL_GB, "tefal_gb", "TEFAL_GB", "tefal", "GB", "en"),
    (Region.MOULINEX_BE, "moulinex_be", "MOULINEX_BE", "moulinex", "BE", "fr"),
    (Region.KRUPS_DE, "krups_de", "KRUPS_DE", "krups", "DE", "de"),
    (Region.MOULINEX_IT, "moulinex_it", "MOULINEX_IT", "moulinex", "IT", "it"),
    (Region.MOULINEX_ES, "moulinex_es", "MOULINEX_ES", "moulinex", "ES", "es"),
    (Region.MOULINEX_PT, "moulinex_pt", "MOULINEX_PT", "moulinex", "PT", "ptPT"),
    (Region.MOULINEX_CH, "moulinex_ch", "MOULINEX_CH", "moulinex", "CH", "de"),
    (Region.MOULINEX_DZ, "moulinex_dz", "MOULINEX_DZ", "moulinex", "DZ", "fr"),
    (Region.MOULINEX_AE, "moulinex_ae", "MOULINEX_AE", "moulinex", "AE", "enUS"),
    (Region.TEFAL_PL, "tefal_pl", "TEFAL_PL", "tefal", "PL", "pl"),
    (Region.TEFAL_AU, "tefal_au", "TEFAL_AU", "tefal", "AU", "enUS"),
    (Region.TEFAL_UA, "tefal_ua", "TEFAL_UA", "tefal", "UA", "uk"),
    (Region.TEFAL_RU, "tefal_ru", "TEFAL_RU", "tefal", "RU", "ru"),
    (Region.TEFAL_HU, "tefal_hu", "TEFAL_HU", "tefal", "HU", "hu"),
    (Region.TEFAL_CZ, "tefal_cz", "TEFAL_CZ", "tefal", "CZ", "cs"),
    (Region.TEFAL_SK, "tefal_sk", "TEFAL_SK", "tefal", "SK", "sk"),
    (Region.TEFAL_BG, "tefal_bg", "TEFAL_BG", "tefal", "BG", "bg"),
    (Region.TEFAL_RO, "tefal_ro", "TEFAL_RO", "tefal", "RO", "ro"),
    (Region.KRUPS_MX, "krups_mx", "KRUPS_MX", "krups", "MX", "es"),
    (Region.TFAL_JP, "tfal_jp", "T-FAL_JP", "tfal", "JP", "ja"),
]


def test_regions_are_all_the_markets() -> None:
    """Test that every region is checked below, in the order they are listed."""
    assert list(Region) == [market[0] for market in MARKETS]
    assert all(re.fullmatch(r"[a-z]+_[a-z]{2}", region.value) for region in Region)


@pytest.mark.parametrize(
    ("region", "value", "perimeter", "brand", "market", "language"), MARKETS
)
def test_region(
    region: Region, value: str, perimeter: str, brand: str, market: str, language: str
) -> None:
    """Test what the login page and the cloud need to know about a region."""
    assert region.value == value
    assert Region(value) is region
    assert region.profile_perimeter == perimeter
    assert (region.brand, region.market, region.language) == (brand, market, language)


def test_brand_names() -> None:
    """Test the name of the brand of a region, as the brand writes it."""
    assert Region.MOULINEX_FR.brand_name == "Moulinex"
    assert Region.TEFAL_GB.brand_name == "Tefal"
    assert Region.KRUPS_DE.brand_name == "Krups"
    assert Region.TFAL_JP.brand_name == "T-fal"
    assert {region.brand_name for region in Region} == {
        "Moulinex",
        "Tefal",
        "Krups",
        "T-fal",
    }


def test_appliance_topics() -> None:
    """Test the MQTT topics of an appliance."""
    uuid = COOKEO.thing_uuid
    assert COOKEO.thing_name == f"COO001-{uuid}"
    assert COOKEO.cooking_status_topic == f"COO001/{uuid}/cookingStatus"
    assert COOKEO.status_request_topic == f"COO001/{uuid}/getCookingStatus"
    assert COOKEO.shadow_topic == f"$aws/things/COO001-{uuid}/shadow"


def test_appliance_programs() -> None:
    """Test the programs of each kind of appliance."""
    assert "cooking_high_pressure" in COOKEO.programs
    assert "soup_smooth" not in COOKEO.programs
    assert "soup_smooth" in COMPANION.programs
    assert list(COOKEO.programs) == sorted(set(COOKEO.programs))
    assert Appliance("Grill", "OPT001", "uuid", domain="PRO_OPT").programs == ()

    assert COOKEO.program(status("cooking")) == "browning_low"
    assert COOKEO.program(status("recipe_cooking")) == "browning_high"
    assert COOKEO.program(status("ingredient_done")) == "cooking_low_pressure"
    assert COOKEO.program(status("idle")) is None
    assert COOKEO.program(status("done")) is None
    assert COOKEO.program(None) is None
    # Not a program of a Companion.
    assert COMPANION.program(status("cooking")) is None
    unknown = CookingStatus(RECEIVED, raw_state="cooking", program_key="PROGRAM_1")
    assert COOKEO.program(unknown) is None


def test_profile() -> None:
    """Test the profile of an account with one Cookeo."""
    profile = Profile.from_json(load_fixture("profile.json"))
    assert profile.profile_perimeter == "MOULINEX_FR"
    assert profile.appliances == (COOKEO,)


def test_profile_keeps_connected_cookeo_and_companion() -> None:
    """Test that the other products of an account are left out."""
    profile = Profile.from_json(load_fixture("profile_two_appliances.json"))
    assert profile.appliances == (COOKEO, COMPANION)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"household": None},
        {"household": {}},
        {"household": {"products": None}},
        {"household": {"products": [{"name": "Cookeo", "domains": ["PRO_COO"]}]}},
        {"household": {"products": [{"iot": {"thingType": "COO001"}}]}},
    ],
)
def test_profile_without_appliances(payload: dict[str, object]) -> None:
    """Test the profiles that have nothing to follow."""
    assert Profile.from_json(payload) == Profile()


def test_profile_defaults() -> None:
    """Test a product without a name or a model."""
    product = {
        "name": "",
        "domains": ["PRO_COO", "PRO_OTHER"],
        "cmmf": 1510002402,
        "iot": {"thingType": "COO001", "functionalId": "uuid"},
    }
    profile = Profile.from_json({"household": {"products": [product]}})
    assert profile.appliances == (Appliance("Cookeo", "COO001", "uuid", "1510002402"),)
    assert profile.appliances[0].model is None
    del product["cmmf"]
    profile = Profile.from_json({"household": {"products": [product]}})
    assert profile.appliances[0].cmmf is None
    product["domains"] = ["PRO_COM"]
    profile = Profile.from_json({"household": {"products": [product]}})
    assert profile.appliances[0].name == "Companion"


def product(appliance: object) -> dict[str, object]:
    """Return a product of a profile, with its identifier in the catalog."""
    return {
        "name": "Mine",
        "appliance": appliance,
        "domains": ["PRO_COO"],
        "iot": {"thingType": "COO001", "functionalId": "uuid"},
    }


@pytest.mark.parametrize(
    ("appliance", "perimeter", "model"),
    [
        ("APPLIANCE_1090", "MOULINEX_FR", "Cookeo Touch Wifi"),
        ("APPLIANCE_1090", "MOULINEX_PT", "Cookeo Touch Wifi"),
        # One name for a brand, however its markets write it.
        ("APPLIANCE_1090", "TEFAL_GB", "Cook4Me Touch"),
        ("APPLIANCE_1090", "TEFAL_AU", "Cook4Me Touch"),
        ("APPLIANCE_1090", "TEFAL_PL", "Cook4Me Touch"),
        ("APPLIANCE_1090", "KRUPS_DE", "Cook4Me Touch Wifi"),
        ("APPLIANCE_1108", "T-FAL_JP", "クックフォーミー タッチ 3L"),
        ("APPLIANCE_1091", "MOULINEX_PT", "i-Companion Touch / Touch Pro"),
        ("APPLIANCE_1091", "TEFAL_PL", "i-Companion Touch"),
        # Not sold by that brand, or in a market that is not known.
        ("APPLIANCE_1195", "TEFAL_GB", "Cookeo 9IN1"),
        ("APPLIANCE_1091", "KRUPS_MX", "i-Companion Touch / Touch Pro"),
        ("APPLIANCE_1232", "SEB_XX", "Cookeo Infinity Autostir"),
        ("APPLIANCE_1232", None, "Cookeo Infinity Autostir"),
        ("APPLIANCE_9999", "MOULINEX_FR", None),
        (None, "MOULINEX_FR", None),
        (1090, "MOULINEX_FR", None),
    ],
)
def test_profile_models(
    appliance: object, perimeter: str | None, model: str | None
) -> None:
    """Test the name of the model of an appliance, which depends on the brand."""
    payload = {
        "profilePerimeter": perimeter,
        "household": {"products": [product(appliance)]},
    }
    (parsed,) = Profile.from_json(payload).appliances
    assert parsed.model == model
    assert parsed.name == "Mine"


def test_catalog() -> None:
    """Test that the catalog holds the appliances that connect to the cloud."""
    assert sorted(MODELS) == [
        "APPLIANCE_1090",
        "APPLIANCE_1091",
        "APPLIANCE_1108",
        "APPLIANCE_1129",
        "APPLIANCE_1195",
        "APPLIANCE_1208",
        "APPLIANCE_1226",
        "APPLIANCE_1227",
        "APPLIANCE_1232",
    ]
    brands = {region.brand for region in Region}
    for names in MODELS.values():
        assert names
        assert set(names) <= brands
        assert all(names.values())


def test_status_idle() -> None:
    """Test what a Cookeo that is turned on and does nothing answers."""
    idle = status("idle")
    assert idle == CookingStatus(
        received_at=RECEIVED,
        event_date=datetime(2026, 11, 20, 17, 40, 18, tzinfo=UTC),
        version=1,
    )
    assert idle.state is CookingState.IDLE
    assert idle.raw_state is None
    assert idle.mode is None
    assert idle.phase_progress is None


def test_status_cooking() -> None:
    """Test a Cookeo that browns, asked for its status while it does."""
    cooking = status("cooking")
    assert cooking == CookingStatus(
        received_at=RECEIVED,
        raw_state="cooking",
        raw_mode="manual",
        elapsed_time=16,
        program_key="PROGRAM_200143",
        event_date=datetime(2026, 11, 20, 17, 42, 7, tzinfo=UTC),
        operation_start=datetime(2026, 11, 20, 17, 41, 51, tzinfo=UTC),
        version=9,
    )
    assert cooking.state is CookingState.COOKING
    assert cooking.mode is CookingMode.MANUAL
    # A browning has no duration: the Cookeo sends 0.
    assert cooking.duration is None
    assert cooking.temperature is None
    assert cooking.remaining_time is None
    assert cooking.phase_progress is None
    assert cooking.recipe is None
    assert cooking.step is None
    assert cooking.notification is None


def test_status_ready() -> None:
    """Test a program that is picked and not started."""
    ready = status("ready")
    assert ready.state is CookingState.READY
    assert ready.program_key == "PROGRAM_200143"
    assert ready.elapsed_time is None


def test_status_warming() -> None:
    """Test that the progress is that of a Cookeo that warms up."""
    warming = status("warming")
    assert warming.state is CookingState.WARMING
    assert warming.progress == 6
    assert warming.phase_progress == 6


@pytest.mark.parametrize("name", ["alert", "paused"])
def test_progress_outside_of_a_phase(name: str) -> None:
    """Test that a progress that is sent in another state is not shown."""
    other = status(name)
    assert other.progress == 6
    assert other.phase_progress is None


def test_status_alert_with_notification() -> None:
    """Test the instruction a Cookeo shows before it starts."""
    alert = status("alert_notification")
    assert alert.state is CookingState.ALERT
    assert alert.notification == Notification(
        code="30027", severity="notif", time_to_live=1440
    )
    assert alert.alert_description is None
    assert alert.error is None
    assert status("alert").notification is None


def test_status_done() -> None:
    """Test a program that is stopped by hand: little is left of it."""
    done = status("done")
    assert done.state is CookingState.DONE
    assert done.mode is None
    assert done.program_key is None
    assert done.operation_start == datetime(2026, 11, 20, 17, 42, 27, tzinfo=UTC)


def test_status_recipe_preparation() -> None:
    """Test a step of a recipe that does not use the Cookeo."""
    preparation = status("recipe_preparation")
    assert preparation.state is CookingState.STOPPED
    assert preparation.mode is CookingMode.RECIPE
    assert preparation.recipe == Recipe(
        title="Velouté de légumes", functional_id="1000001", variant_id="2000001"
    )
    assert preparation.step == Step(
        index=0, functional_id="3000000", type="PREPARATION"
    )
    assert preparation.program_key is None


def test_status_recipe_cooking() -> None:
    """Test a step of a recipe that cooks."""
    cooking = status("recipe_cooking")
    assert cooking.state is CookingState.COOKING
    assert cooking.step == Step(index=2, functional_id="3000002", type="COOKING")
    assert cooking.program_key == "PROGRAM_200063"
    assert status("recipe_ready").state is CookingState.READY
    assert status("recipe_done").state is CookingState.DONE
    assert status("recipe_done").recipe == cooking.recipe


def test_status_ingredient_cooking() -> None:
    """Test cooking an ingredient: a mode in two words, and a duration."""
    warming = status("ingredient_warming")
    assert warming.raw_mode == "ingredientCooking"
    assert warming.mode is CookingMode.INGREDIENT_COOKING
    assert warming.phase_progress == 10
    assert warming.recipe == Recipe(
        title="Lentille", functional_id="M_FOOD_280", variant_id="255977"
    )
    assert warming.duration is None
    assert status("ingredient_done").duration == 1440


def test_status_values_that_were_not_captured() -> None:
    """Test the values of the app that the captured messages do not hold."""
    payload = {
        "currentStatus": "keep warm",
        "mode": "program",
        "remainingTime": 540,
        "alertDescr": "Close the lid",
        "error": "E12",
        "currentOperation": {
            "program": {"key": "PROGRAM_200215"},
            "parameters": [
                {"type": "DURATION", "value": "-1"},
                {"type": "TEMPERATURE", "value": "63"},
            ],
        },
    }
    keep_warm = CookingStatus.from_json(payload)
    assert keep_warm.raw_state == "keep warm"
    assert keep_warm.state is CookingState.KEEP_WARM
    assert keep_warm.mode is CookingMode.PROGRAM
    assert keep_warm.remaining_time == 540
    assert keep_warm.alert_description == "Close the lid"
    assert keep_warm.error == "E12"
    assert keep_warm.duration is None
    assert keep_warm.temperature == 63
    assert abs((datetime.now(UTC) - keep_warm.received_at).total_seconds()) < 5

    releasing = CookingStatus(RECEIVED, raw_state="depressurization", progress=80)
    assert releasing.state is CookingState.DEPRESSURIZATION
    assert releasing.phase_progress == 80


def test_states_and_modes() -> None:
    """Test the values a state and a mode can have."""
    assert [state.value for state in CookingState] == [
        "idle",
        "stopped",
        "ready",
        "paused",
        "warming",
        "cooking",
        "depressurization",
        "keep_warm",
        "done",
        "delayed",
        "alert",
    ]
    assert [mode.value for mode in CookingMode] == [
        "manual",
        "program",
        "recipe",
        "ingredient_cooking",
    ]
    for state in CookingState:
        assert CookingStatus(RECEIVED, raw_state=state.value).state is state


def test_status_unknown_values() -> None:
    """Test a state and a mode this library does not know."""
    unknown = CookingStatus(RECEIVED, raw_state="Self-Cleaning", raw_mode="turbo")
    assert unknown.state is None
    assert unknown.mode is None
    assert unknown.phase_progress is None


@pytest.mark.parametrize(
    "payload",
    [
        {"currentOperation": None, "currentRecipe": "x", "currentStep": 3},
        {"currentOperation": {"program": "x", "parameters": "y"}},
        {"currentOperation": {"parameters": ["DURATION", {"type": "DURATION"}]}},
        {"currentStatus": "", "mode": 3, "progress": True, "elapsedTime": "soon"},
        {"notification": "x", "eventDate": "yesterday", "operationStart": 12},
    ],
)
def test_status_unexpected_shapes(payload: dict[str, object]) -> None:
    """Test that what does not look like it should is left out."""
    assert CookingStatus.from_json(payload, RECEIVED) == CookingStatus(RECEIVED)


def test_status_partial_objects() -> None:
    """Test a recipe, a step and a notification that miss what they should hold."""
    payload = {"currentRecipe": {"variant": 1}, "currentStep": {}, "notification": {}}
    parsed = CookingStatus.from_json(payload, RECEIVED)
    assert parsed.recipe == Recipe()
    assert parsed.step == Step()
    assert parsed.notification == Notification()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(90, 90), ("90", 90), (0, None), ("0", None), ("-1", None), (None, None)],
)
def test_status_parameters(value: object, expected: int | None) -> None:
    """Test the duration of a program in every shape it comes in."""
    operation = {"parameters": [{"type": "DURATION", "value": value}]}
    parsed = CookingStatus.from_json({"currentOperation": operation}, RECEIVED)
    assert parsed.duration == expected


def test_shadow_get() -> None:
    """Test the shadow of a Cookeo that is turned off."""
    shadow = ApplianceShadow.from_json(load_fixture("shadow_get_accepted.json"))
    assert shadow == ApplianceShadow(
        connected=False,
        last_connection=1795023772120,
        last_disconnection=1795026275854,
        synchronizing=False,
        firmware=Firmware(
            ui_version="5.0.54", wifi_version="3.6.3", update_priority="low"
        ),
    )


def test_shadow_update_keeps_what_is_known() -> None:
    """Test that an update of the shadow only changes what it holds."""
    shadow = ApplianceShadow.from_json(load_fixture("shadow_get_accepted.json"))
    update = ApplianceShadow.from_json(
        load_fixture("shadow_update_accepted_online.json")
    )
    assert update == ApplianceShadow(connected=True, last_connection=1795199262856)
    merged = shadow.merge(update)
    assert merged.connected is True
    assert merged.last_connection == 1795199262856
    assert merged.last_disconnection == 1795026275854
    assert merged.firmware.ui_version == "5.0.54"


def test_shadow_documents() -> None:
    """Test that the current state is read from a message with two states."""
    shadow = ApplianceShadow.from_json(
        load_fixture("shadow_update_documents_online.json")
    )
    assert shadow.connected is True
    assert shadow.last_connection == 1795199262856
    assert shadow.firmware.wifi_version == "3.6.3"


def test_shadow_turns_off() -> None:
    """Test the two messages of a Cookeo that is turned off."""
    online = ApplianceShadow.from_json(load_fixture("shadow_get_accepted_online.json"))
    for name in ("shadow_update_documents_offline", "shadow_update_accepted_offline"):
        update = ApplianceShadow.from_json(load_fixture(f"{name}.json"))
        assert update.connected is False
        assert update.last_disconnection == 1795198147230
        assert online.merge(update).connected is False
        assert online.merge(update).firmware.ui_version == "5.0.54"


def test_shadow_update_about_something_else() -> None:
    """Test an update that does not tell whether the Cookeo is turned on."""
    update = ApplianceShadow.from_json(
        load_fixture("shadow_update_accepted_other.json")
    )
    assert update.connected is None
    assert update.synchronizing is False
    offline = ApplianceShadow(connected=False)
    assert offline.merge(update).connected is False
    whole = ApplianceShadow.from_json(
        load_fixture("shadow_update_documents_other.json")
    )
    assert whole.connected is True


def test_shadow_goes_offline() -> None:
    """Test that a Cookeo that turns off is not taken for an unknown value."""
    online = ApplianceShadow(connected=True)
    assert online.merge(ApplianceShadow(connected=False)).connected is False


@pytest.mark.parametrize(
    "payload",
    [{}, {"state": None}, {"state": {}}, {"state": {"reported": []}}, {"current": 1}],
)
def test_shadow_without_reported_state(payload: dict[str, object]) -> None:
    """Test the messages that say nothing about the appliance."""
    assert ApplianceShadow.from_json(payload) == ApplianceShadow()


@pytest.mark.parametrize(
    "reported",
    [
        {"status": "online"},
        {"status": ["connected"]},
        {"status": {"connected": "false", "synchronizing": 1}},
        {"status": {"lastConnection": "soon", "lastDisconnection": [1]}},
        {"firmware": "5.0.54"},
        {"firmware": {"files": 3, "priority": 1}},
        {
            "firmware": {
                "files": ["ui", {"type": ["ui"], "version": "1"}, {"type": "ui"}]
            }
        },
    ],
)
def test_shadow_unexpected_shapes(reported: dict[str, object]) -> None:
    """Test that what does not look like it should is left out."""
    payload = {"state": {"reported": reported}}
    assert ApplianceShadow.from_json(payload) == ApplianceShadow()


def test_shadow_firmware_shapes() -> None:
    """Test firmware files that are not what they should be."""
    reported = {"firmware": {"files": ["ui", {"type": "ui", "version": "1.2"}]}}
    shadow = ApplianceShadow.from_json({"state": {"reported": reported}})
    assert shadow.firmware == Firmware(ui_version="1.2")


def test_numbers_too_big_to_read() -> None:
    """Test a number that JSON accepts and that is not one."""
    parsed = CookingStatus.from_json({"progress": float("inf")}, RECEIVED)
    assert parsed.progress is None


def test_models_cannot_be_changed() -> None:
    """Test that what a listener gets cannot be changed under the client."""
    shadow = ApplianceShadow(connected=True)
    with pytest.raises(AttributeError):
        shadow.connected = False  # type: ignore[misc]
    with pytest.raises(AttributeError):
        COOKEO.name = "Other"  # type: ignore[misc]
    assert {COOKEO: 1}[COOKEO] == 1


def test_appliance_state_defaults() -> None:
    """Test the state of an appliance nothing is known about yet."""
    assert ApplianceState() == ApplianceState(None, ApplianceShadow())
