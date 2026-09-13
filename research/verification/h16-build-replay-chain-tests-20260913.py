import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location("build_rehearsal", "/tmp/h16-build-rehearsal.py")
driver=importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)

@pytest.mark.parametrize("field", [None,"status","scratch","source_receipt_sha256","input_bundle_hash","marker_sha256","unfinished_by_scope","network_requests","parse_executed","published"])
def test_replay_receipt_must_bind_exact_completed_source_state(field):
    receipt={"format":"offline-derivation-replay-v1","status":"current","scratch":"/var/tmp/replay","source_receipt_sha256":"source","input_bundle_hash":"bundle","marker_sha256":"marker","unfinished_by_scope":{},"network_requests":0,"parse_executed":False,"published":False}
    args=dict(replay=Path("/var/tmp/replay"),receipt="source",bundle="bundle",marker_sha256="marker")
    if field is None:
        driver.require_current_replay(receipt,**args)
    else:
        receipt[field]="mismatched evidence"
        with pytest.raises(ValueError,match="exact scratch"):
            driver.require_current_replay(receipt,**args)
