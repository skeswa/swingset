"""Table-driven contest vocabulary."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ContestVocabulary:
    division: str
    age_division: str
    contest_type: str
    partner_mode: str
    dance_style: str
    combined_from: tuple[str, ...] = ()


def classify_contest(name: str) -> ContestVocabulary:
    value = re.sub(r"[^a-z0-9]+", " ", name.casefold().replace("&", "and")).strip()
    division = next(
        (
            canonical
            for phrase, canonical in (
                ("newcomer", "newcomer"),
                ("novice", "novice"),
                ("intermediate", "intermediate"),
                ("advanced", "advanced"),
                ("all star", "allstar"),
                ("allstar", "allstar"),
                ("all stars", "allstar"),
                ("champion", "champion"),
                ("invit", "invitational"),
                ("open", "open"),
            )
            if phrase in value
        ),
        "none",
    )
    age = next(
        (
            canonical
            for phrase, canonical in (
                ("junior", "juniors"),
                ("soph", "sophisticated"),
                ("master", "masters"),
            )
            if phrase in value
        ),
        "none",
    )
    if "jack" in value and "jill" in value:
        contest_type, partner = "jack_and_jill", "random_partner"
    elif "strictly" in value:
        contest_type, partner = "strictly", "open_couple"
    elif "classic" in value:
        contest_type, partner = "classic", "perm_couple"
    elif "showcase" in value:
        contest_type, partner = "showcase", "perm_couple"
    elif "pro am" in value or "proam" in value:
        contest_type, partner = "pro_am", "perm_couple"
    elif "rising star" in value:
        contest_type, partner = "rising_star", "perm_couple"
    else:
        contest_type, partner = "other", "open_couple"
    style = (
        "lindy"
        if "lindy" in value
        else "country"
        if "country" in value
        else "other"
        if "hustle" in value
        else "wcs"
    )
    combined = tuple(
        item
        for item in ("newcomer", "novice", "intermediate", "advanced", "allstar", "champion")
        if item.replace("allstar", "all star") in value
    )
    return ContestVocabulary(
        division, age, contest_type, partner, style, combined if len(combined) > 1 else ()
    )
