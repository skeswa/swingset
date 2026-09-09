"""Public fixed vocabularies.

Values are persisted and published.  Member names may change internally, but
values are part of the dataset API.
"""

from enum import StrEnum


class Source(StrEnum):
    WSDC_CALENDAR = "wsdc_calendar"
    WSDC_REGISTRY = "wsdc_registry"
    EEPRO = "eepro"
    SCORING_DANCE = "scoringdance"
    WORLD_DANCE_REGISTRY = "wdr"
    DANCE_CONVENTION = "dcn"
    GENERIC = "generic"


class ScopeKind(StrEnum):
    SOURCE_EVENT = "source_event"
    DANCER = "dancer"
    SOURCE_INDEX = "source_index"
    CALENDAR = "calendar"


class WorkStage(StrEnum):
    PARSE = "parse"
    PROJECT = "project"
    LINK = "link"


class MatchMethod(StrEnum):
    NAME_DATE = "name_date"
    ALIAS = "alias"
    OVERRIDE = "override"


class WatchState(StrEnum):
    DORMANT = "dormant"
    UPCOMING = "upcoming"
    LIVE = "live"
    COOLING = "cooling"
    ARCHIVED = "archived"
    GONE = "gone"
    PAUSED = "paused"
    BACKFILL = "backfill"


class WSDCStatus(StrEnum):
    REGISTRY = "registry"
    TRIAL = "trial"
    UNCONFIRMED = "unconfirmed"
    UNKNOWN = "unknown"


class Division(StrEnum):
    NEWCOMER = "newcomer"
    NOVICE = "novice"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    ALLSTAR = "allstar"
    CHAMPION = "champion"
    OPEN = "open"
    INVITATIONAL = "invitational"
    NONE = "none"


class AgeDivision(StrEnum):
    NONE = "none"
    JUNIORS = "juniors"
    SOPHISTICATED = "sophisticated"
    MASTERS = "masters"


class ContestType(StrEnum):
    JACK_AND_JILL = "jack_and_jill"
    STRICTLY = "strictly"
    CLASSIC = "classic"
    SHOWCASE = "showcase"
    PRO_AM = "pro_am"
    RISING_STAR = "rising_star"
    OTHER = "other"


class PartnerMode(StrEnum):
    RANDOM_PARTNER = "random_partner"
    OPEN_COUPLE = "open_couple"
    PERM_COUPLE = "perm_couple"


class DanceStyle(StrEnum):
    WCS = "wcs"
    LINDY = "lindy"
    COUNTRY = "country"
    OTHER = "other"


class ParseStatus(StrEnum):
    PARSED = "parsed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class RoundType(StrEnum):
    PRELIM = "prelim"
    QUARTERFINAL = "quarterfinal"
    SEMIFINAL = "semifinal"
    FINAL = "final"


class ScoringMethod(StrEnum):
    CALLBACK = "callback"
    RELATIVE_PLACEMENT = "relative_placement"


class CallbackLegend(StrEnum):
    WSDC_10 = "wsdc_10"
    LEGACY_3 = "legacy_3"
    UNKNOWN = "unknown"


class Role(StrEnum):
    LEADER = "leader"
    FOLLOWER = "follower"
    COUPLE = "couple"
    UNKNOWN = "unknown"


class CallbackMark(StrEnum):
    YES = "yes"
    ALT1 = "alt1"
    ALT2 = "alt2"
    ALT3 = "alt3"
    NO = "no"


class CallbackOutcome(StrEnum):
    PROMOTED = "promoted"
    ALTERNATE_1 = "alternate_1"
    ALTERNATE_2 = "alternate_2"
    ALTERNATE_3 = "alternate_3"
    ELIMINATED = "eliminated"


class SubjectKind(StrEnum):
    ENTRY = "entry"
    JUDGE = "judge"


class LinkMethod(StrEnum):
    SOURCE_ID = "source_id"
    REGISTRY_PLACEMENT = "registry_placement"
    BIB_REUSE = "bib_reuse"
    NAME_UNIQUE = "name_unique"
    NAME_SCORED = "name_scored"
    ASSIGNMENT = "assignment"
    MANUAL = "manual"
    NONE = "none"


class LinkStatus(StrEnum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"
    SUPPRESSED = "suppressed"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingKind(StrEnum):
    PARSE_FAILURE = "parse_failure"
    CONFLICT = "conflict"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN_ENUM = "unknown_enum"
    REGISTRY_DIFF = "registry_diff"


ENUMS: tuple[type[StrEnum], ...] = tuple(
    value
    for value in globals().values()
    if isinstance(value, type) and issubclass(value, StrEnum) and value is not StrEnum
)


def enums_markdown() -> str:
    """Return the generated public enum reference."""
    blocks = ["# Enum vocabularies", "", "Generated from `swingset.model.enums`.", ""]
    for enum_type in sorted(ENUMS, key=lambda item: item.__name__):
        blocks.extend(
            (f"## {enum_type.__name__}", "", ", ".join(f"`{item.value}`" for item in enum_type), "")
        )
    return "\n".join(blocks)
