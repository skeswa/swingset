"""Versioned adapter accounting, independent of the observation writer.

These contracts describe captured single documents. A listing is never an
authoritative enumeration of an entire web site. Unknown shapes fail closed.

This is the shared core of every kind's contract: the pagination guard, the
warning fields, the declaration check and the report. Each kind's own rules
live in the module `kinds.CONTRACTS` names for it, and only that kind's parse
scope holds that module (D-0261). Project, link and build import none of
this: a core edit re-parses every kind and re-projects nothing.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from swingset.sources.base import PageKind, ParseContext, ParseResult
from swingset.sources.interpretation import declares

from .accounting import Accounting
from .kinds import contract, contract_version
from .report import Coverage, Field, Guard, Report

__all__ = ["contract_version", "declare", "evaluate", "inspect"]


def declare(page: PageKind, ctx: ParseContext, extract: Any, result: ParseResult) -> ParseResult:
    """Attach the declaration of an adapter whose parse is `declared`.

    The declaration is this contract run on the extract alone, without the
    body; `inspect` then checks the body's accounting against it. Admission
    applies it where a page is parsed, so no adapter imports admission.
    """
    if not declares(page):
        return result
    return replace(result, interpretation=inspect(ctx, b"", extract, result))


def inspect(ctx: ParseContext, body: bytes, extract: Any, result: ParseResult) -> Report:
    module = contract(ctx.kind)
    guards = [
        Guard(
            "contract_unassessed",
            module is not None,
            "This page kind has an explicit versioned contract",
        )
    ]
    accounted: Accounting = (
        module.accounting(ctx, body, extract, result)
        if module is not None
        else Accounting(
            (Field("$", "unknown", "No accounting contract has been reviewed for this page kind"),),
            (),
            None,
            len(result.observations),
        )
    )
    fields = list(accounted.fields)
    guards.extend(accounted.guards)
    source_count = accounted.source_count
    interpreted = accounted.interpreted_count
    terminal = accounted.terminal
    removal = accounted.removal
    children = accounted.listed_children
    parsed_children = accounted.interpreted_children
    text = body.decode("utf-8", "replace")
    if re.search(r"rel\s*=\s*['\"]?next\b|[?&](?:page|cursor)=", text, re.I):
        terminal = None
        guards.append(
            Guard(
                "pagination_unassembled",
                False,
                "Pagination links require an assembled and revalidated manifest",
            )
        )
    for warning in () if accounted.own_warnings else result.warnings:
        excluded = warning.code in accounted.bounded_warnings
        fields.append(
            Field(
                f"warning:{warning.code}",
                "excluded" if excluded else "unknown",
                warning.message
                + (
                    "; retained as a finding; no inferred outcome/partner bib or removal authority"
                    if excluded
                    else ""
                ),
            )
        )
    coverage = Coverage(
        (ctx.snapshot_id,),
        (ctx.snapshot_id,),
        terminal,
        source_count,
        interpreted,
        children,
        parsed_children,
    )
    if result.interpretation is not None:
        declared = result.interpretation
        fields.extend(declared.fields)
        guards.extend(declared.guards)
        guards.append(
            Guard(
                "declaration_mismatch",
                declared.page_kind == ctx.kind
                and declared.contract_version == contract_version(ctx.kind)
                and declared.coverage.source_count == source_count
                and declared.coverage.interpreted_count == interpreted
                and declared.coverage.listed_children == children,
                "Archived structure independently corroborates the adapter declaration",
            )
        )
    return evaluate(
        ctx.kind,
        contract_version(ctx.kind),
        tuple(dict.fromkeys(fields)),
        coverage,
        guards=tuple(dict.fromkeys(guards)),
        removal=removal,
    )


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
