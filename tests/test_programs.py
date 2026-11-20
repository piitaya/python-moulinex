"""Tests for the programs."""

from __future__ import annotations

import re

import pytest

from aiomoulinex import (
    COMPANION_DOMAIN,
    COMPANION_PROGRAMS,
    COOKEO_DOMAIN,
    COOKEO_PROGRAMS,
    PROGRAMS,
)


def test_domains() -> None:
    """Test that each kind of appliance has its programs."""
    assert PROGRAMS == {
        COOKEO_DOMAIN: COOKEO_PROGRAMS,
        COMPANION_DOMAIN: COMPANION_PROGRAMS,
    }
    assert len(COOKEO_PROGRAMS) == 180
    assert len(COMPANION_PROGRAMS) == 75


@pytest.mark.parametrize("programs", [COOKEO_PROGRAMS, COMPANION_PROGRAMS])
def test_format(programs: dict[str, str]) -> None:
    """Test that the keys look like those of the cloud and the names are usable."""
    for key, name in programs.items():
        assert re.fullmatch(r"PROGRAM_(\d{5,6}|REHEAT)", key), key
        assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", name), name


@pytest.mark.parametrize(
    ("domain", "key", "name"),
    [
        (COOKEO_DOMAIN, "PROGRAM_200213", "cooking_high_pressure"),
        (COOKEO_DOMAIN, "PROGRAM_200068", "cooking_ultra_high_pressure"),
        (COOKEO_DOMAIN, "PROGRAM_200217", "browning_high"),
        (COOKEO_DOMAIN, "PROGRAM_200215", "keep_warm"),
        (COOKEO_DOMAIN, "PROGRAM_REHEAT", "reheat"),
        (COOKEO_DOMAIN, "PROGRAM_201879", "actifry"),
        (COMPANION_DOMAIN, "PROGRAM_000002", "soup_smooth"),
        (COMPANION_DOMAIN, "PROGRAM_200186", "auto_clean"),
        (COMPANION_DOMAIN, "PROGRAM_201655", "steam_p5"),
    ],
)
def test_names(domain: str, key: str, name: str) -> None:
    """Test the name of a few programs."""
    assert PROGRAMS[domain][key] == name


def test_kinds_do_not_share_keys() -> None:
    """Test that a key belongs to one kind of appliance."""
    assert not COOKEO_PROGRAMS.keys() & COMPANION_PROGRAMS.keys()
