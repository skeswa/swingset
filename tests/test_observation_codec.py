from dataclasses import dataclass
from unittest.mock import patch

import pytest

from swingset.model import observations as codec
from swingset.sources.records import Cell, ResultRow, ResultTable, RoundSheet


def test_repeated_nested_records_resolve_hints_once_and_keep_typed_values() -> None:
    payload = RoundSheet(
        "round_sheet",
        "source:event",
        "round",
        "Novice",
        "Prelims",
        (
            ResultTable(
                "Leaders",
                (Cell("BIB"), Cell("Name")),
                tuple(
                    ResultRow((Cell(str(i)), Cell("Alex", (("title", "name"),)))) for i in range(20)
                ),
            ),
        ),
    )
    encoded = codec.encode_payload(payload)
    codec._TYPE_HINTS.clear()
    with patch.object(codec, "get_type_hints", wraps=codec.get_type_hints) as resolve:
        assert codec.decode_payload("round_sheet", encoded) == payload
        assert codec.decode_payload("round_sheet", encoded) == payload
    assert [call.args[0] for call in resolve.call_args_list] == [
        RoundSheet,
        ResultTable,
        Cell,
        ResultRow,
    ]


def test_cached_decoder_keeps_registration_alias_and_missing_field_defaults() -> None:
    @dataclass(frozen=True)
    class CodecRegistrationExample:
        kind: str
        value: str
        optional: str | None = None

    conventional = "codec_registration_example"
    alias = "codec_registration_example_alias"
    try:
        codec.register_observation_type(CodecRegistrationExample)
        value = CodecRegistrationExample(alias, "retained")
        encoded = codec.encode_payload(value)
        assert codec.decode_payload(alias, encoded) == value
        assert codec.decode_payload(
            conventional, '{"kind":"codec_registration_example","value":"old"}'
        ) == CodecRegistrationExample(conventional, "old")
        with pytest.raises(ValueError, match="duplicate observation kind"):
            codec.register_observation_type(CodecRegistrationExample)
    finally:
        codec._DECODERS.pop(conventional, None)
        codec._DECODERS.pop(alias, None)
        codec._TYPE_HINTS.clear()
