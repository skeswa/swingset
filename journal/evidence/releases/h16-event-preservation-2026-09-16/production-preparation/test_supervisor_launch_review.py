import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('reviewed_supervisor', '/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation/supervise-initialization.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def test_interrupted_launch_stops_potentially_started_unit(tmp_path):
    class Backend:
        stopped = []
        def authority(self):
            return {'hold':'unchanged','baseline':'unchanged'}
        def launch(self, command, log):
            self.launched = command
            raise KeyboardInterrupt('signal after launcher started')
        def stop(self, unit):
            self.stopped.append(unit)
        def settled(self, unit):
            return {'writer_lock_released':True}
    args = SimpleNamespace(session='reviewed',max_invocations=1,gate=tmp_path/'gate',marker=tmp_path/'marker',driver=tmp_path/'driver',gate_sha256='1'*64,marker_sha256='2'*64,driver_sha256=m.DRIVER_SHA)
    backend=Backend()
    with pytest.raises(KeyboardInterrupt):
        m.supervise(args,backend,tmp_path)
    assert backend.stopped == ['swingset-h16-init-reviewed-001.service']
    assert (tmp_path/'batch-001-final.json').is_file()
