"""Explicit accounting and coverage witnesses; an empty result proves nothing.

These are the report's data types, which project, link and build read too.
The checks that build a report live in `contracts.evaluate`, which only the
parse scopes hold (D-0261).
"""

from dataclasses import asdict, dataclass

from swingset.fetch.archive import canonical, digest


@dataclass(frozen=True)
class Field:
    path: str
    disposition: str  # handled, excluded, unknown
    reason: str
    critical: bool = True


@dataclass(frozen=True)
class Coverage:
    expected_pages: tuple[str, ...]
    observed_pages: tuple[str, ...]
    terminal: str | None
    source_count: int | None
    interpreted_count: int
    listed_children: tuple[str, ...] = ()
    interpreted_children: tuple[str, ...] = ()
    mutable_listing: bool = False
    revision_before: str | None = None
    revision_after: str | None = None
    snapshot_token: str | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Guard:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Report:
    page_kind: str
    contract_version: str
    fields: tuple[Field, ...]
    coverage: Coverage
    guards: tuple[Guard, ...]
    proposed_removal: str = "none"

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(sorted({guard.code for guard in self.guards if not guard.passed}))

    @property
    def state(self) -> str:
        if not self.failures:
            return "staged"
        if set(self.failures) <= {
            "coverage_missing_page",
            "coverage_no_terminal",
            "manifest_artifact_missing",
        }:
            return "waiting_for_inputs"
        return "needs_review"

    def as_dict(self) -> dict[str, object]:
        return {**asdict(self), "failures": self.failures, "state": self.state}

    @property
    def digest(self) -> str:
        return digest(canonical(self.as_dict()))
