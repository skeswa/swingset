"""Project source-event declarations without reading state or running adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from swingset.schedule.event_evidence import request, request_id
from swingset.schedule.event_request_kind import is_source_event_request, purpose


def declarations(
    value: Mapping[str, Any],
    *,
    anchor_watch: Mapping[str, Any] | None = None,
    anchor_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    context = value["recipe"]["context"]
    source = context["source"]
    groups: dict[str, dict[str, dict[str, Any]]] = {}

    def add(
        ref: str | None,
        page: dict[str, Any],
        spec: dict[str, Any] | None = None,
        role: str = "listed",
    ) -> None:
        if not ref or ref.endswith(":unknown"):
            return
        key = request_id(page)
        row = groups.setdefault(ref, {}).setdefault(
            key, {"request": page, "watches": [], "roles": []}
        )
        if spec is not None and spec not in row["watches"]:
            row["watches"].append(spec)
        if role not in row["roles"]:
            row["roles"].append(role)

    for spec in value["result"]["watches"]:
        if spec["source"] == source and is_source_event_request(
            source=source,
            parser=spec["parser"],
            watch_kind=spec["kind"],
            source_ref=spec["source_ref"],
        ):
            add(
                spec["source_ref"], request(source, spec["method"], spec["url"], spec["form"]), spec
            )
    for observation in value["result"]["observations"]:
        if observation["scope"]["kind"] != "source_event":
            continue
        ref = observation["scope"]["ref"]
        if not ref or ref.endswith(":unknown"):
            continue
        groups.setdefault(ref, {})
        if observation["kind"] == "file_row":
            add(ref, request(source, "GET", observation["payload"]["url"]), role="listed_file")
        elif purpose(context["kind"]) == "result":
            watch = anchor_watch
            if watch is None:
                raise ValueError("parent_watch_missing")
            spec = {
                key: watch[key]
                for key in (
                    "watch_id",
                    "source",
                    "kind",
                    "method",
                    "url",
                    "form",
                    "parser",
                    "source_ref",
                    "archive_url",
                )
            }
            snapshot = anchor_snapshot
            if snapshot is None:
                raise ValueError("parent_snapshot_missing")
            spec.update(
                {"method": snapshot["method"], "url": snapshot["url"], "form": snapshot["form"]}
            )
            add(
                ref,
                request(source, snapshot["method"], snapshot["url"], snapshot["form"]),
                spec,
                "direct_result",
            )
    known_parent = purpose(context["kind"]) in {"event_index", "result"}
    if (
        known_parent
        and context.get("source_ref")
        and not context["source_ref"].endswith(":unknown")
    ):
        groups.setdefault(context["source_ref"], {})
    return groups
