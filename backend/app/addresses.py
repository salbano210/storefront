"""Mock address validation: deterministic, no network, no LLM.

A real brand checks a delivery address with a service (SmartyStreets, Google
Address Validation) before it ships anything. This is the demo's stand-in, and it
behaves the way those services do:

  * a structurally complete US address is accepted, normalised to one canonical form
  * a missing or impossible part is rejected, with the reason
  * a near-miss of the address on file is rejected *with the correction*, which is
    what lets the agent ask "did you mean ...?" instead of losing the order

What it deliberately does not do is confirm the address is *real* -- that needs a
network and a data vendor. The demo only claims what it can prove: the address is
well-formed, internally consistent, and either matches or differs from the one on
file in a way worth confirming.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# ZIP ranges by state. Enough to catch "Boston, MA 90028", which is the mistake
# this check exists for.
KNOWN_STATES: dict[str, tuple[int, int]] = {
    "AZ": (85001, 86556),
    "CA": (90001, 96162),
    "CO": (80001, 81658),
    "FL": (32003, 34997),
    "GA": (30001, 31999),
    "IL": (60001, 62999),
    "MA": (1001, 2791),
    "NC": (27006, 28909),
    "NJ": (7001, 8989),
    "NY": (10001, 14975),
    "OH": (43001, 45999),
    "OR": (97001, 97920),
    "PA": (15001, 19640),
    "TX": (73301, 88595),
    "WA": (98001, 99403),
}

STATE_NAMES: dict[str, str] = {
    "AZ": "Arizona",
    "CA": "California",
    "CO": "Colorado",
    "FL": "Florida",
    "GA": "Georgia",
    "IL": "Illinois",
    "MA": "Massachusetts",
    "NC": "North Carolina",
    "NJ": "New Jersey",
    "NY": "New York",
    "OH": "Ohio",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "TX": "Texas",
    "WA": "Washington",
}

# How close to the address on file counts as "you probably meant this one".
NEAR_MISS_RATIO = 0.85

PARTS = ("street", "city", "state", "zip")


STREET_SUFFIXES = {
    "st",
    "street",
    "ave",
    "avenue",
    "rd",
    "road",
    "blvd",
    "boulevard",
    "ln",
    "lane",
    "dr",
    "drive",
    "way",
    "ct",
    "court",
    "pl",
    "place",
    "ter",
    "terrace",
    "hwy",
    "highway",
}


def parse(raw: str) -> dict[str, str]:
    """Split a free-text US address into street / city / state / ZIP (best effort).

    Tolerates missing commas and odd spacing, because that is how creators write
    addresses in a chat box.
    """
    parts = {"street": "", "city": "", "state": "", "zip": ""}
    text = " ".join((raw or "").replace(";", ",").split())
    if not text:
        return parts

    # The state is the last 2-letter token that is a real state code.
    state_match = None
    for candidate in re.finditer(r"\b([A-Za-z]{2})\b", text):
        if candidate.group(1).upper() in KNOWN_STATES:
            state_match = candidate

    tail = text
    if state_match:
        parts["state"] = state_match.group(1).upper()
        tail = text[state_match.end() :]

    # A ZIP is 5 digits (optionally +4) and follows the state.
    zip_match = re.search(r"\b(\d{5})(?:-(\d{4}))?\b", tail)
    if zip_match:
        parts["zip"] = zip_match.group(1) + (f"-{zip_match.group(2)}" if zip_match.group(2) else "")
        tail = tail[: zip_match.start()] + " " + tail[zip_match.end() :]

    head = " ".join((text[: state_match.start()] if state_match else text).split())
    # The state and ZIP have been lifted out, which can leave a trailing comma
    # ("123 Example Street, Boston,") that would otherwise be mistaken for the
    # street/city separator.
    head = head.strip(" ,")
    head = " ".join(head.replace(",", " , ").split())
    if "," in head:
        street, _, city = head.rpartition(",")
        parts["street"] = street.strip(" ,")
        parts["city"] = city.strip(" ,")
    else:
        # No comma: "123 Example Street Boston MA 02110". Assume the last token is
        # the city, unless it is a street suffix -- in which case the city is simply
        # absent and we should say so rather than invent one.
        tokens = head.split()
        if len(tokens) >= 3 and tokens[-1].lower().strip(".") not in STREET_SUFFIXES:
            parts["street"] = " ".join(tokens[:-1])
            parts["city"] = tokens[-1]
        else:
            parts["street"] = head.strip(" ,")
    return parts


def normalise(parts: dict[str, str]) -> str:
    """The canonical single-line form we store and ship to."""
    return (
        f"{parts['street'].title()}, {parts['city'].title()}, "
        f"{parts['state']} {parts['zip']}"
    )


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def _zip_in_state(state: str, zip_code: str) -> bool:
    bounds = KNOWN_STATES.get(state)
    if bounds is None:
        return False
    digits = int(zip_code.split("-")[0])
    return bounds[0] <= digits <= bounds[1]


def validate(raw: str, *, on_file: str | None = None) -> dict[str, Any]:
    """Check an address and, when it fails, offer the closest known-good one.

    ``on_file`` is the address the creator already has with the brand. It is both
    the suggestion we make and the thing a near-miss is compared against.
    """
    parts = parse(raw)
    missing = [name for name in PARTS if not parts[name]]

    result: dict[str, Any] = {
        "valid": False,
        "input": (raw or "").strip(),
        "parts": parts,
        "missing": missing,
        "normalised": None,
        "reason": None,
        "suggestion": None,
        "suggestion_source": None,
        "source": "mock address validation service",
    }

    def suggest(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        if on_file:
            result["suggestion"] = on_file
            result["suggestion_source"] = "address on file"
        return result

    if not parts["street"] and not parts["city"] and not parts["state"] and not parts["zip"]:
        return suggest("no address was given")

    if missing:
        return suggest("missing " + ", ".join(missing))

    if parts["state"] not in KNOWN_STATES:
        return suggest(f"'{parts['state']}' is not a US state code")

    if not _zip_in_state(parts["state"], parts["zip"]):
        return suggest(
            f"ZIP {parts['zip']} is not in {STATE_NAMES[parts['state']]} ({parts['state']})"
        )

    canonical = normalise(parts)

    # A typo that happens to land on a valid ZIP would otherwise sail through, so
    # compare against the address we already hold before accepting it.
    if on_file and canonical.lower() != on_file.strip().lower():
        if _similarity(canonical, on_file) >= NEAR_MISS_RATIO:
            return suggest("this is very close to the address on file")

    result["valid"] = True
    result["normalised"] = canonical
    return result
