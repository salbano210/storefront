"""Address validation: parsing, the checks, and the suggestions."""

from __future__ import annotations

import pytest

from app import addresses

ON_FILE = "123 Example Street, Boston, MA 02110"


def test_a_complete_address_is_accepted_and_normalised():
    result = addresses.validate("123 example street, boston, ma 02110", on_file=ON_FILE)
    assert result["valid"] is True
    assert result["normalised"] == ON_FILE
    assert result["reason"] is None
    assert result["suggestion"] is None


def test_missing_commas_still_parse():
    """Creators type addresses into a chat box, not a form."""
    result = addresses.validate("123 Example Street Boston MA 02110", on_file=ON_FILE)
    assert result["valid"] is True
    assert result["normalised"] == ON_FILE


def test_a_genuinely_different_address_is_accepted():
    """Moving house is not a validation failure."""
    result = addresses.validate("500 New Road, Denver, CO 80202", on_file=ON_FILE)
    assert result["valid"] is True
    assert result["normalised"] == "500 New Road, Denver, CO 80202"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", "no address was given"),
        ("123 Example Street, Boston, MA", "missing zip"),
        ("123 Example Street, Boston", "missing state, zip"),
        ("123 Example Street, Boston, MA 0211", "missing zip"),
    ],
)
def test_incomplete_addresses_name_what_is_missing(raw, expected):
    result = addresses.validate(raw, on_file=ON_FILE)
    assert result["valid"] is False
    assert result["reason"] == expected
    assert result["suggestion"] == ON_FILE


def test_a_zip_outside_the_state_is_rejected():
    result = addresses.validate("123 Example Street, Boston, MA 90028", on_file=ON_FILE)
    assert result["valid"] is False
    assert "not in Massachusetts" in result["reason"]
    assert result["suggestion"] == ON_FILE


def test_a_near_miss_is_offered_back_as_the_correction():
    """A typo that lands on a valid ZIP would otherwise sail through."""
    result = addresses.validate("123 Example Street, Boston, MA 02111", on_file=ON_FILE)
    assert result["valid"] is False
    assert "close to the address on file" in result["reason"]
    assert result["suggestion"] == ON_FILE


def test_an_address_nothing_like_the_one_on_file_still_gets_the_suggestion():
    result = addresses.validate("10 Downing Street, London", on_file=ON_FILE)
    assert result["valid"] is False
    assert result["suggestion"] == ON_FILE


def test_no_suggestion_without_an_address_on_file():
    result = addresses.validate("123 Example Street, Boston, MA", on_file=None)
    assert result["valid"] is False
    assert result["suggestion"] is None


def test_every_seeded_creator_address_validates_against_itself():
    """The fixture addresses must be acceptable, or the demo's happy path breaks."""
    from app.services import data_store

    for profile in data_store.seed_profiles():
        result = addresses.validate(profile.shipping_address, on_file=profile.shipping_address)
        assert result["valid"] is True, f"{profile.id}: {result['reason']}"
        assert result["normalised"] == profile.shipping_address
