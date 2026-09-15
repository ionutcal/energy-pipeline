import pytest

from src.psr import PSR_NAMES, is_known, to_code


@pytest.mark.parametrize(
    "value, code",
    [
        ("Fossil Gas", "B04"),
        ("fossil gas", "B04"),
        ("Hydro Run-of-river and poundage", "B11"),
        ("B25", "B25"),
        ("", ""),
        ("Tidal kite", "Tidal kite"),
    ],
)
def test_to_code(value, code):
    assert to_code(value) == code


def test_every_standard_code_is_known():
    assert all(is_known(code) for code in PSR_NAMES)
    assert not is_known("Fossil Gas")


def test_names_match_what_python_entsoe_returns():
    # If the package renames a type, to_code would stop recognizing it and
    # new rows would be stored under the name instead of the code.
    mappings = pytest.importorskip("entsoe._mappings")
    for code, name in mappings.PSR_TYPES.items():
        assert PSR_NAMES[code] == name, code
