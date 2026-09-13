"""Explicit accounting and coverage witnesses; an empty result proves nothing."""

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


def evaluate(
    page_kind: str,
    contract_version: str,
    fields: tuple[Field, ...],
    coverage: Coverage,
    *,
    guards: tuple[Guard, ...] = (),
    removal: str = "none",
    sentinel_expected: str | None = None,
    sentinel_actual: str | None = None,
) -> Report:
    """No thresholds, acknowledgments, or network fallbacks live in evaluation."""
    checks = (
        Guard(
            "coverage_missing_page",
            coverage.expected_pages == coverage.observed_pages,
            "The complete ordered manifest must match the declared source unit",
        ),
        Guard(
            "coverage_repeated_page",
            len(set(coverage.observed_pages)) == len(coverage.observed_pages),
            "Each source page occurs once",
        ),
        Guard(
            "coverage_no_terminal",
            bool(coverage.terminal),
            "An explicit terminal witness is required",
        ),
        Guard(
            "coverage_count_mismatch",
            coverage.source_count is not None
            and coverage.source_count == coverage.interpreted_count,
            "Independent source and interpreted row counts must agree",
        ),
        Guard(
            "coverage_child_mismatch",
            coverage.listed_children == coverage.interpreted_children,
            "Every listed child is accounted for in source order",
        ),
        Guard(
            "coverage_listing_changed",
            not coverage.mutable_listing
            or bool(coverage.snapshot_token)
            or bool(
                coverage.revision_before and coverage.revision_before == coverage.revision_after
            ),
            "Mutable listings require a snapshot token or matching revision revalidation",
        ),
        Guard("coverage_limitation", not coverage.limitations, "; ".join(coverage.limitations)),
        Guard(
            "critical_unknown",
            not any(f.critical and f.disposition == "unknown" for f in fields),
            "Every critical field has a declared interpretation or exclusion",
        ),
        Guard(
            "accounting_invalid",
            all(
                f.disposition in {"handled", "excluded", "unknown"} and bool(f.reason)
                for f in fields
            ),
            "Accounting dispositions include reasons",
        ),
        Guard(
            "manual_sentinel_changed",
            sentinel_actual is None
            and sentinel_expected is None
            or sentinel_expected is not None
            and sentinel_actual == sentinel_expected,
            "Manual extraction inputs require the exact reviewed sentinel",
        ),
    )
    return Report(page_kind, contract_version, fields, coverage, (*checks, *guards), removal)
