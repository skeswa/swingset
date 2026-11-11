"""Pure numerical probes retain the exact backend boundary and input identity."""

import hashlib
import json
from pathlib import Path

import pytest

from swingset.deploy import numerics


@pytest.fixture
def captured(tmp_path):
    names = {16984: ("Eli", "Wilson"), 13703: ("Anna", "Kratochwil"), 10681: ("Ben", "Howard")}
    dancers = [
        {
            "requested_wsdc_id": key,
            "first_name": first,
            "last_name": last,
            "name_norm": (first + " " + last).lower(),
            "present": True,
            "primary_role": "leader" if key != 13703 else "follower",
            "merged_into_wsdc_id": None,
        }
        for key, (first, last) in names.items()
    ]
    subjects = [
        {
            "requested_subject_id": key,
            "candidate_wsdc_id": dancer,
            "name_raw": "Emily Wilson"
            if dancer == 16984
            else ("Anna Koch" if dancer == 13703 else "Ben Ha"),
            "role": "leader" if dancer == 10681 else "follower",
            "present": True,
        }
        for key, dancer in numerics.SUBJECTS.items()
    ]
    documents = [
        {"section": "identity", "complete": True},
        {"section": "dependencies", "complete": True},
        {
            "section": "input_metadata",
            "scope": "three_dancers_five_exact_entry_ids_persisted_locator_subset_only",
            "complete": True,
            "subjects": subjects,
            "dancers": dancers,
        },
        {"section": "identity_final", "complete": True},
    ]
    path = tmp_path / "capture.jsonl"
    path.write_text("\n".join(json.dumps(doc, indent=2) for doc in documents))
    return path, documents


def label(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_name_gate_boundaries_report_exact_binary_values_and_backend(captured):
    path, _ = captured
    result = numerics.run(path, label(path))
    assert len(result["pairs"]) == 5
    for pair in result["pairs"]:
        python = pair["scores"]["python"]
        if pair["dancer_id"] == 16984:
            assert pair["normalized_subject"] == "emily wilson"
            assert pair["normalized_dancer"] == "eli wilson"
            assert python["jaro_winkler"] == {
                "repr": "0.9199999999999999",
                "hex": "0x1.d70a3d70a3d70p-1",
            }
            assert python["fuzzy_gate_admitted"] is False
            cpp = pair["scores"]["cpp_generic"]
            assert (cpp["jaro_winkler"]["hex"], cpp["fuzzy_gate_admitted"]) in {
                ("0x1.d70a3d70a3d70p-1", False),
                ("0x1.d70a3d70a3d71p-1", True),
            }
        else:
            assert all(score["jaro_winkler"]["repr"] == "0.92" for score in pair["scores"].values())
            assert all(score["fuzzy_gate_admitted"] for score in pair["scores"].values())
        assert pair["merged_into"] is None
        assert all(score["token_admitted"] is False for score in pair["scores"].values())
    for backend in result["backends"].values():
        for function in backend.values():
            assert function["file_sha256"] == numerics.file_hash(Path(function["file"]))
    assert result["input_sha256"] == label(path)
    assert result["thresholds"]["jaro"]["hex"] == (0.92).hex()
    assert result["runtime"]["rapidfuzz"]
    assert len(json.dumps(result).encode()) < 24 * 1024


@pytest.mark.parametrize(
    "problem",
    [
        "sha",
        "truncated",
        "extra_doc",
        "missing_doc",
        "wrong_subject",
        "duplicate_subject",
        "merged",
        "incomplete",
        "oversize",
    ],
)
def test_unbound_or_incomplete_inputs_refuse_without_backend_work(captured, monkeypatch, problem):
    path, documents = captured
    expected = label(path)
    if problem == "sha":
        expected = "a" * 64
    elif problem == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
        expected = label(path)
    else:
        if problem == "extra_doc":
            documents.append({"section": "extra"})
        elif problem == "missing_doc":
            documents.pop()
        elif problem == "wrong_subject":
            documents[2]["subjects"][0]["requested_subject_id"] = "unreviewed"
        elif problem == "duplicate_subject":
            documents[2]["subjects"][0] = documents[2]["subjects"][1]
        elif problem == "merged":
            documents[2]["dancers"][0]["merged_into_wsdc_id"] = 1
        elif problem == "incomplete":
            documents[2]["complete"] = False
        path.write_text("\n".join(json.dumps(doc) for doc in documents))
        expected = label(path)
        if problem == "oversize":
            monkeypatch.setattr(numerics, "MAX_BYTES", 1)
    # Input parsing/identity rejects before any score; merged state is checked
    # after backend selection but before normalizing or evaluating the pair.
    if problem != "merged":
        monkeypatch.setattr(
            numerics, "backends", lambda: pytest.fail("backend evaluated for invalid input")
        )
    with pytest.raises((ValueError, KeyError)):
        numerics.run(path, expected)


def test_optional_cpp_absence_keeps_real_default_and_python_results(captured, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def absent(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "rapidfuzz.distance" and "metrics_cpp" in fromlist:
            raise ImportError("private unsupported runtime detail")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", absent)
    path, _ = captured
    result = numerics.run(path, label(path))
    assert result["unavailable"] == {"backend": "cpp_generic", "error_type": "ImportError"}
    assert set(result["backends"]) == {"default", "python"}
    assert all(set(pair["scores"]) == {"default", "python"} for pair in result["pairs"])
    assert "private unsupported" not in json.dumps(result)


def test_cli_failure_is_sanitized_and_success_is_bounded_json(captured, capsys):
    path, _ = captured
    assert numerics.main(["--input", str(path), "--input-sha256", "a" * 64]) == 1
    assert json.loads(capsys.readouterr().out) == {"complete": False, "error_type": "ValueError"}
    assert numerics.main(["--input", str(path), "--input-sha256", label(path)]) == 0
    assert json.loads(capsys.readouterr().out)["format"] == "swingset-pure-link-numerics-v1"


@pytest.fixture
def generic_capture(captured):
    path, old = captured
    payload = old[2]
    dancers = [row for row in payload["dancers"] if row["requested_wsdc_id"] != 10681]
    base = payload["subjects"][0]
    subjects = [
        dict(base, subject_kind="entry", candidate_wsdc_id=16984),
        dict(base, subject_kind="entry", candidate_wsdc_id=13703),
        dict(
            base,
            subject_kind="judge",
            requested_subject_id="soswing/judge/1",
            candidate_wsdc_id=13703,
            name_raw="Anna Koch",
            role="unknown",
        ),
    ]
    expected = [
        {
            "subject_kind": row["subject_kind"],
            "subject_id": row["requested_subject_id"],
            "candidate_wsdc_id": row["candidate_wsdc_id"],
        }
        for row in subjects
    ]
    value = {
        "scope": "exact_retained_pairs_names_only",
        "pair_set_exact": True,
        "requested_pairs": 3,
        "requested_dancers": 2,
        "requested_unique_subjects": 2,
        "pairs": expected,
        "subjects": subjects,
        "dancers": dancers,
    }
    documents = [old[0], {"section": "input_metadata", "complete": True, "payload": value}, old[-1]]
    path.write_text("\n".join(json.dumps(doc, indent=2) for doc in documents))
    return path, documents


def test_wrapped_generic_capture_keeps_repeated_subjects_as_distinct_pairs(generic_capture):
    path, _ = generic_capture
    result = numerics.run(path, label(path))
    assert len(result["pairs"]) == 3
    assert [(row["subject_kind"], row["dancer_id"]) for row in result["pairs"]] == [
        ("entry", 16984),
        ("entry", 13703),
        ("judge", 13703),
    ]
    assert result["pairs"][0]["subject_id"] == result["pairs"][1]["subject_id"]
    assert "payload" not in result and "locators" not in json.dumps(result)


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_pair",
        "missing_pair",
        "count",
        "subject_count",
        "dancer_count",
        "duplicate_dancer",
        "kind",
        "extra_subject",
        "pair_set",
        "absent",
        "merged",
        "stored_norm",
        "utf8",
        "duplicate_key",
        "incomplete_identity",
    ],
)
def test_generic_capture_fails_closed_on_ambiguous_or_unsafe_metadata(generic_capture, problem):
    path, documents = generic_capture
    value = documents[1]["payload"]
    if problem == "duplicate_pair":
        value["pairs"][1] = value["pairs"][0]
    elif problem == "missing_pair":
        value["pairs"].pop()
    elif problem == "count":
        value["requested_pairs"] = 4
    elif problem == "subject_count":
        value["requested_unique_subjects"] = 3
    elif problem == "dancer_count":
        value["requested_dancers"] = 3
    elif problem == "duplicate_dancer":
        value["dancers"].append(value["dancers"][0])
        value["requested_dancers"] += 1
    elif problem == "kind":
        value["subjects"][0]["subject_kind"] = "other"
        value["pairs"][0]["subject_kind"] = "other"
    elif problem == "extra_subject":
        value["subjects"].append(dict(value["subjects"][0], candidate_wsdc_id=1))
    elif problem == "pair_set":
        value["pair_set_exact"] = False
    elif problem == "absent":
        value["subjects"][0]["present"] = False
    elif problem == "merged":
        value["dancers"][0]["merged_into_wsdc_id"] = 1
    elif problem == "stored_norm":
        value["subjects"][0].update(stored_name_norm_available=True, name_norm="wrong")
    elif problem == "utf8":
        value["subjects"][0]["name_raw"] = "é" * 513
    elif problem == "incomplete_identity":
        documents[0]["complete"] = False
    text = "\n".join(json.dumps(doc) for doc in documents)
    if problem == "duplicate_key":
        text = text.replace('"scope":', '"scope": "duplicate", "scope":', 1)
    path.write_text(text)
    with pytest.raises((ValueError, KeyError)):
        numerics.run(path, label(path))


@pytest.mark.parametrize("field", ["subjects", "dancers"])
def test_generic_capture_refuses_more_than_256_rows(generic_capture, field):
    path, documents = generic_capture
    value = documents[1]["payload"]
    value[field] *= 257
    path.write_text("\n".join(json.dumps(doc) for doc in documents))
    with pytest.raises(ValueError):
        numerics.run(path, label(path))


def test_injected_one_ulp_scores_serialize_without_rounding_or_backend_override(
    captured, monkeypatch
):
    def below(left, right):
        return float.fromhex("0x1.d70a3d70a3d70p-1")

    def threshold(left, right):
        return float.fromhex("0x1.d70a3d70a3d71p-1")

    def token(left, right):
        return 0.0

    monkeypatch.setattr(
        numerics,
        "backends",
        lambda: ({"below": (below, token), "threshold": (threshold, token)}, None),
    )
    path, _ = captured
    result = json.loads(json.dumps(numerics.run(path, label(path))))
    for pair in result["pairs"]:
        assert pair["scores"]["below"]["jaro_winkler"] == {
            "repr": "0.9199999999999999",
            "hex": "0x1.d70a3d70a3d70p-1",
        }
        assert pair["scores"]["below"]["fuzzy_gate_admitted"] is False
        assert pair["scores"]["threshold"]["jaro_winkler"] == {
            "repr": "0.92",
            "hex": "0x1.d70a3d70a3d71p-1",
        }
        assert pair["scores"]["threshold"]["fuzzy_gate_admitted"] is True


def test_report_limit_fails_without_truncation(captured, monkeypatch, capsys):
    path, _ = captured
    monkeypatch.setattr(numerics, "MAX_REPORT_BYTES", 1)
    assert numerics.main(["--input", str(path), "--input-sha256", label(path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"complete": False, "error_type": "ValueError"}


@pytest.mark.parametrize(
    "field", ["requested_pairs", "requested_unique_subjects", "requested_dancers"]
)
def test_generic_requested_counts_must_be_integers(generic_capture, field):
    path, documents = generic_capture
    documents[1]["payload"][field] = float(documents[1]["payload"][field])
    path.write_text("\n".join(json.dumps(doc) for doc in documents))
    with pytest.raises(ValueError):
        numerics.run(path, label(path))


def test_backend_identity_refuses_unloaded_modules_without_importing():
    import sys

    def function():
        return None

    function.__module__ = "unloaded_numerical_backend_fixture"
    assert function.__module__ not in sys.modules
    with pytest.raises(ValueError, match="not loaded"):
        numerics.backend_identity(function)
    assert function.__module__ not in sys.modules
