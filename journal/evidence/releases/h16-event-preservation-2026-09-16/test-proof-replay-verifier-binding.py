"""Offline checks for captured-once replay marker/bundle binding."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('prepared_replay_verifier',Path(__file__).with_name('verify-proof-replay.py'))
verify=importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

class BindingTests(unittest.TestCase):
    def binding(self,**changes):
        return {'source':str(verify.SOURCE),'source_receipt_sha256':verify.SOURCE_SHA,'scratch':str(verify.SCRATCH),'input_bundle_hash':'a'*64,'marker_sha256':'b'*64,**changes}

    def check(self,value,wrong_digest=False):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'binding.json'
            path.write_text(json.dumps(value))
            return verify.load_binding(path,'0'*64 if wrong_digest else verify.sha(path))

    def test_exact_binding_is_returned(self):
        value=self.binding()
        self.assertEqual(self.check(value),value)

    def test_receipt_hash_is_required(self):
        with self.assertRaisesRegex(ValueError,'receipt changed'):
            self.check(self.binding(),wrong_digest=True)

    def test_source_receipt_and_scratch_are_fixed(self):
        for change in ({'source':'wrong'},{'source_receipt_sha256':'wrong'},{'scratch':'wrong'}):
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'source/scratch'):
                self.check(self.binding(**change))

    def test_bundle_and_marker_need_full_hashes(self):
        for key in ('input_bundle_hash','marker_sha256'):
            for invalid in (None,'','g'*64,'a'*63,42):
                with self.subTest(key=key,invalid=invalid),self.assertRaisesRegex(ValueError,'hashes'):
                    self.check(self.binding(**{key:invalid}))

if __name__=='__main__': unittest.main()
