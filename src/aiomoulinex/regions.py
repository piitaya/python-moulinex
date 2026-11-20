"""Regions of the SEB accounts."""

from __future__ import annotations

from enum import StrEnum


class Region(StrEnum):
    """Brand and market an SEB account belongs to."""

    MOULINEX_FR = "moulinex_fr"
    TEFAL_GB = "tefal_gb"
    MOULINEX_BE = "moulinex_be"
    KRUPS_DE = "krups_de"
    MOULINEX_IT = "moulinex_it"
    MOULINEX_ES = "moulinex_es"

    @property
    def brand(self) -> str:
        """Return the brand as the login page expects it, e.g. ``moulinex``."""
        return self.value.partition("_")[0]

    @property
    def market(self) -> str:
        """Return the market as the login page expects it, e.g. ``FR``."""
        return self.value.partition("_")[2].upper()

    @property
    def language(self) -> str:
        """Return the language of the login page."""
        return _LANGUAGES[self]

    @property
    def profile_perimeter(self) -> str:
        """Return the name of the region in the SEB cloud, e.g. ``MOULINEX_FR``."""
        return self.value.upper()


_LANGUAGES = {
    Region.MOULINEX_FR: "fr",
    Region.TEFAL_GB: "en",
    Region.MOULINEX_BE: "fr",
    Region.KRUPS_DE: "de",
    Region.MOULINEX_IT: "it",
    Region.MOULINEX_ES: "es",
}
