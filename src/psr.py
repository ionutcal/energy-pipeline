"""ENTSO-E power system resource (PSR) types.

We store the code (e.g. "B04"), never the name. python-entsoe translates
codes into names ("Fossil Gas") using its own table, which only covers
B01–B20, so a response mixes names and raw codes ("B25"). Since `psr_type`
is part of the natural key, a change to that table would otherwise create
parallel rows for the same resource. Codes are part of the ENTSO-E standard
and don't change.
"""

from __future__ import annotations

# Code -> name, as defined by ENTSO-E. For B01–B20 the names match exactly
# what python-entsoe returns (checked in tests/test_psr.py).
PSR_NAMES = {
    "B01": "Biomass",
    "B02": "Fossil Brown coal/Lignite",
    "B03": "Fossil Coal-derived gas",
    "B04": "Fossil Gas",
    "B05": "Fossil Hard coal",
    "B06": "Fossil Oil",
    "B07": "Fossil Oil shale",
    "B08": "Fossil Peat",
    "B09": "Geothermal",
    "B10": "Hydro Pumped Storage",
    "B11": "Hydro Run-of-river and poundage",
    "B12": "Hydro Water Reservoir",
    "B13": "Marine",
    "B14": "Nuclear",
    "B15": "Other renewable",
    "B16": "Solar",
    "B17": "Waste",
    "B18": "Wind Offshore",
    "B19": "Wind Onshore",
    "B20": "Other",
    "B21": "AC Link",
    "B22": "DC Link",
    "B23": "Substation",
    "B24": "Transformer",
    "B25": "Energy storage",
}

_CODES_BY_NAME = {name.lower(): code for code, name in PSR_NAMES.items()}


def to_code(value: str) -> str:
    """The PSR code for a name or code; unknown values are returned unchanged."""
    if value in PSR_NAMES or value == "":
        return value
    return _CODES_BY_NAME.get(value.strip().lower(), value)


def is_known(value: str) -> bool:
    return value in PSR_NAMES
