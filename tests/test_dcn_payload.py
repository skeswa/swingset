"""Data-only Nuxt grammar rejects execution and bounds expansion."""

import pytest

from swingset.sources.base import ExtractError
from swingset.sources.dcn.nuxt import (
    MAX_BODY_BYTES,
    MAX_DEPTH,
    MAX_NODES,
    MAX_SCRIPT_BYTES,
    decode_script,
    evaluate_nuxt,
)


def script(value, parameters="", arguments=""):
    return f"window.__NUXT__=(function({parameters}){{return {value}}}({arguments}));"


def test_literal_aliases_and_undefined_follow_json_data_semantics():
    assert decode_script(
        script("{name:a,items:[b,null,void 0],missing:void 0,n:-1.5e2}", "a,b", '"literal",true')
    ) == {"name": "literal", "items": [True, None, None], "n": -150.0}
    assert decode_script(script('{literal:"function() { new Thing(); }",__proto__:{x:1}}')) == {
        "literal": "function() { new Thing(); }",
        "__proto__": {"x": 1},
    }


@pytest.mark.parametrize(
    "source",
    [
        script("{x:process.exit(0)}"),
        script("{x:a.constructor}", "a", "{}"),
        script("{x:1+2}"),
        script("{x:new Date()}"),
        script("{x:(()=>1)()}"),
        script("{x:NaN}"),
        script("{x:1e9999}"),
        script("{x:missing}"),
        script("{x:a}", "a", "globalThis"),
        script("{x:a}", "a,a", "1,2"),
        script("{x:a}", "a,b", "1"),
        script("{x:1,x:2}"),
        script('{["x"]:1}'),
        script("{}") + "process.exit(0)",
        "window.__NUXT__=(function(){while(true){} return {}}());",
        script('{x:"\\x41"}'),
        script("{x:[,]}"),
        script("{x:true,}"),
    ],
)
def test_unknown_or_executable_grammar_is_rejected(source):
    with pytest.raises(ExtractError):
        decode_script(source)


def test_depth_node_script_and_body_bounds():
    with pytest.raises(ExtractError, match="structural"):
        decode_script(script("{x:" + "[" * (MAX_DEPTH + 1) + "0" + "]" * (MAX_DEPTH + 1) + "}"))
    with pytest.raises(ExtractError, match="structural"):
        decode_script(script("{x:[" + ",".join("0" for _ in range(MAX_NODES)) + "]}"))
    with pytest.raises(ExtractError, match="script byte"):
        decode_script(" " * (MAX_SCRIPT_BYTES + 1))
    with pytest.raises(ExtractError, match="body byte"):
        evaluate_nuxt(b" " * (MAX_BODY_BYTES + 1))


def test_alias_expansion_is_bounded_separately_from_input_size():
    argument = "{items:[" + ",".join("0" for _ in range(1000)) + "]}"
    with pytest.raises(ExtractError, match="expansion"):
        decode_script(script("{items:[" + ",".join("a" for _ in range(60)) + "]}", "a", argument))


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<script>not a payload</script>",
        b"\xff",
        ("<script>" + script("{}") + "</script>").encode() * 2,
    ],
)
def test_absent_ambiguous_or_invalid_encoding_payload_is_rejected(body):
    with pytest.raises(ExtractError):
        evaluate_nuxt(body)
