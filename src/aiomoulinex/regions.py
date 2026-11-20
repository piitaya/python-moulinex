"""Regions of the SEB accounts."""

from __future__ import annotations

from enum import StrEnum


class Region(StrEnum):
    """Brand and market an SEB account belongs to.

    These are the markets where the SEB cloud lists a Cookeo or a Companion.
    """

    MOULINEX_FR = "moulinex_fr"
    TEFAL_GB = "tefal_gb"
    MOULINEX_BE = "moulinex_be"
    KRUPS_DE = "krups_de"
    MOULINEX_IT = "moulinex_it"
    MOULINEX_ES = "moulinex_es"
    MOULINEX_PT = "moulinex_pt"
    MOULINEX_CH = "moulinex_ch"
    MOULINEX_DZ = "moulinex_dz"
    MOULINEX_AE = "moulinex_ae"
    TEFAL_PL = "tefal_pl"
    TEFAL_AU = "tefal_au"
    TEFAL_UA = "tefal_ua"
    TEFAL_RU = "tefal_ru"
    TEFAL_HU = "tefal_hu"
    TEFAL_CZ = "tefal_cz"
    TEFAL_SK = "tefal_sk"
    TEFAL_BG = "tefal_bg"
    TEFAL_RO = "tefal_ro"
    KRUPS_MX = "krups_mx"
    TFAL_JP = "tfal_jp"

    @property
    def brand(self) -> str:
        """Return the brand as the login page expects it, e.g. ``moulinex``."""
        return self.value.partition("_")[0]

    @property
    def brand_name(self) -> str:
        """Return the name of the brand, e.g. ``Moulinex``."""
        return _BRAND_NAMES[self.brand]

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
        return f"{_PERIMETER_BRANDS.get(self.brand, self.brand.upper())}_{self.market}"


_BRAND_NAMES = {
    "moulinex": "Moulinex",
    "tefal": "Tefal",
    "krups": "Krups",
    "tfal": "T-fal",
}
# The brand is not written the same everywhere in the SEB cloud.
_PERIMETER_BRANDS = {"tfal": "T-FAL"}
_LANGUAGES = {
    Region.MOULINEX_FR: "fr",
    Region.TEFAL_GB: "en",
    Region.MOULINEX_BE: "fr",
    Region.KRUPS_DE: "de",
    Region.MOULINEX_IT: "it",
    Region.MOULINEX_ES: "es",
    Region.MOULINEX_PT: "ptPT",
    Region.MOULINEX_CH: "de",
    Region.MOULINEX_DZ: "fr",
    Region.MOULINEX_AE: "enUS",
    Region.TEFAL_PL: "pl",
    Region.TEFAL_AU: "enUS",
    Region.TEFAL_UA: "uk",
    Region.TEFAL_RU: "ru",
    Region.TEFAL_HU: "hu",
    Region.TEFAL_CZ: "cs",
    Region.TEFAL_SK: "sk",
    Region.TEFAL_BG: "bg",
    Region.TEFAL_RO: "ro",
    Region.KRUPS_MX: "es",
    Region.TFAL_JP: "ja",
}
