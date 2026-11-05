"""Mirror bootstrap never overwrites a repository or distributes an owner token."""

import hashlib
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from swingset.deploy import mirror

SECRET = "hf_ThisIsASecretToken123456789"
HEAD, BASELINE = "a" * 40, "b" * 40
TARGET = "skeswa/swingset-rehearsal"


@pytest.fixture
def job(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    configured = {
        "format": mirror.FORMAT,
        "source": mirror.SOURCE,
        "target": TARGET,
        "source_head": HEAD,
        "baseline": BASELINE,
        "source_commits": 2,
        "maximum_source_bytes": mirror.MAXIMUM_BYTES,
    }
    policy, credentials, result = (
        tmp_path / name for name in ("policy.json", "credentials.env", "result.json")
    )
    policy.write_text(json.dumps(configured))
    policy.chmod(0o600)
    credentials.write_text("HF_REHEARSAL_TOKEN='" + SECRET + "'\n")
    credentials.chmod(0o600)
    state = SimpleNamespace(
        existing=False,
        private=True,
        destination_ids=[HEAD, BASELINE],
        refs_match=True,
        source_changed=False,
        post_status=201,
        provider_error=False,
        redirect=False,
        used_bytes=100,
        actor="skeswa",
        global_permissions=[],
        scope_type="user",
        scope_id="account",
        calls=[],
        starts=[],
        clock=1000.0,
    )

    def sleep(seconds):
        state.clock += seconds

    monkeypatch.setattr(mirror, "time", SimpleNamespace(monotonic=lambda: state.clock, sleep=sleep))
    monkeypatch.setattr(mirror.sys, "platform", "linux")

    def dispatch(request):
        state.starts.append(state.clock)
        state.calls.append(request)
        path = request.url.path
        if request.method == "POST":
            assert path == "/api/datasets/skeswa/swingset/duplicate"
            assert json.loads(request.content) == {"repository": TARGET, "visibility": "private"}
            if state.provider_error:
                raise RuntimeError(SECRET)
            return httpx.Response(state.post_status, json={"error": SECRET})
        assert request.method == "GET"
        if path == "/api/whoami-v2":
            return httpx.Response(
                200,
                json={
                    "name": state.actor,
                    "id": "account",
                    "auth": {
                        "accessToken": {
                            "role": "fineGrained",
                            "fineGrained": {
                                "global": state.global_permissions,
                                "scoped": [
                                    {
                                        "entity": {"type": state.scope_type, "_id": state.scope_id},
                                        "permissions": [
                                            "repo.write",
                                            "repo.content.read",
                                            "repo.access.read",
                                            "repo.contribute.write",
                                        ],
                                    }
                                ],
                            },
                        }
                    },
                },
            )
        destination = TARGET in path
        posted = any(r.method == "POST" for r in state.calls)
        if destination and not posted and not state.existing:
            return httpx.Response(404)
        if "commits/main" in path:
            ids = state.destination_ids if destination else [HEAD, BASELINE]
            headers = (
                {"Link": '<https://evil.invalid/steal>; rel="next"'}
                if state.redirect and not destination
                else {}
            )
            return httpx.Response(200, json=[{"id": v} for v in ids], headers=headers)
        if path.endswith("/refs"):
            target = BASELINE if destination and not state.refs_match else HEAD
            return httpx.Response(
                200, json={"branches": [{"name": "main", "targetCommit": target}], "tags": []}
            )
        if request.url.query:
            return httpx.Response(200, json={"usedStorage": state.used_bytes})
        revision = BASELINE if "/revision/" in path else HEAD
        if not destination and posted and state.source_changed:
            revision = "c" * 40
        return httpx.Response(
            200,
            json={
                "id": TARGET if destination else mirror.SOURCE,
                "sha": revision,
                "private": state.private if destination else False,
                "siblings": [{"rfilename": "data/a.parquet"}, {"rfilename": "_meta/manifest.json"}],
            },
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(dispatch)
    monkeypatch.setattr(
        mirror.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    return SimpleNamespace(
        state=state, policy=policy, credentials=credentials, result=result, configured=configured
    )


def invoke(job):
    return mirror.run(job.policy, job.credentials, job.result)


def writes(job):
    return [r for r in job.state.calls if r.method != "GET"]


def test_private_copy_preserves_history_refs_and_source_without_secret_output(job):
    before = logging.root.manager.disable
    report = invoke(job)
    assert report["complete"] and report["source_head_unchanged"]
    assert report["all_source_history_equal"] and report["all_manual_refs_equal"]
    assert report["baseline_downloads_pending"] and report["application_token_pending"]
    assert len(writes(job)) == 1
    assert all(
        b - a >= 2.1 - 1e-8 for a, b in zip(job.state.starts, job.state.starts[1:], strict=False)
    )
    body = job.result.read_text()
    assert json.loads(body) == report
    assert SECRET not in body and hashlib.sha256(SECRET.encode()).hexdigest() not in body
    assert job.result.stat().st_mode & 0o777 == 0o600
    assert logging.root.manager.disable == before


@pytest.mark.parametrize(
    "target",
    [
        mirror.SOURCE,
        "skeswa/swingset-archive",
        "other/swingset-rehearsal",
        "skeswa/swingset-rehearsal/../../live",
    ],
)
def test_invalid_destination_refuses_before_credential_read_or_network(job, target):
    job.configured["target"] = target
    job.policy.write_text(json.dumps(job.configured))
    report = invoke(job)
    assert not report["complete"] and not report["mutation_attempted"]
    assert not job.state.calls


@pytest.mark.parametrize(
    "damage", ["existing", "actor", "global", "scope_type", "scope_id", "too_large", "redirect"]
)
def test_wrong_identity_existing_target_and_source_bounds_refuse_before_copy(job, damage):
    if damage == "existing":
        job.state.existing = True
    elif damage == "actor":
        job.state.actor = "other"
    elif damage == "global":
        job.state.global_permissions = ["repo.write"]
    elif damage == "scope_type":
        job.state.scope_type = "repo"
    elif damage == "scope_id":
        job.state.scope_id = "other"
    elif damage == "too_large":
        job.state.used_bytes = mirror.MAXIMUM_BYTES + 1
    else:
        job.state.redirect = True
    report = invoke(job)
    assert not report["complete"] and not writes(job)
    assert all(r.url.host == "huggingface.co" for r in job.state.calls)


@pytest.mark.parametrize("damage", ["private", "history", "refs", "source", "http", "transport"])
def test_post_copy_failure_is_ambiguous_and_never_retried(job, damage):
    if damage == "private":
        job.state.private = False
    elif damage == "history":
        job.state.destination_ids = [HEAD]
    elif damage == "refs":
        job.state.refs_match = False
    elif damage == "source":
        job.state.source_changed = True
    elif damage == "http":
        job.state.post_status = 503
    else:
        job.state.provider_error = True
    report = invoke(job)
    assert not report["complete"] and report["mutation_attempted"]
    assert report["no_automatic_retry"] and len(writes(job)) == 1
    assert SECRET not in job.result.read_text()
    with pytest.raises(FileExistsError):
        invoke(job)
    assert len(writes(job)) == 1


@pytest.mark.parametrize("damage", ["permissions", "symlink", "duplicate"])
def test_private_credential_binding_refuses_before_network(job, damage):
    if damage == "permissions":
        job.credentials.chmod(0o644)
    elif damage == "symlink":
        replacement = job.credentials.with_suffix(".real")
        job.credentials.rename(replacement)
        job.credentials.symlink_to(replacement)
    else:
        job.credentials.write_text(
            "HF_REHEARSAL_TOKEN=" + SECRET + "\nHF_REHEARSAL_TOKEN=" + SECRET + "\n"
        )
    report = invoke(job)
    assert not report["complete"] and not job.state.calls
    assert SECRET not in job.result.read_text()


def test_cli_reaches_maintained_command_without_opening_a_pipeline_database(job, capsys):
    from swingset import cli

    assert (
        cli.main(
            [
                "mirror-bootstrap",
                "--policy",
                str(job.policy),
                "--credentials-file",
                str(job.credentials),
                "--result",
                str(job.result),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert json.loads(output)["complete"] and SECRET not in output


def test_receipt_fsync_failure_after_copy_reports_unknown_outcome(job, monkeypatch, capsys):
    def fail(_descriptor):
        raise OSError(SECRET)

    with monkeypatch.context() as patch:
        patch.setattr(mirror.os, "fsync", fail)
        assert (
            mirror.main(
                [
                    "--policy",
                    str(job.policy),
                    "--credentials-file",
                    str(job.credentials),
                    "--result",
                    str(job.result),
                ]
            )
            == 1
        )
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["mutation_attempted"] is None
    assert report["mutation_outcome"] == "unknown" and report["no_automatic_retry"]
    assert not report["complete"] and len(writes(job)) == 1
    assert SECRET not in output
    with pytest.raises(FileExistsError):
        invoke(job)
    assert len(writes(job)) == 1
