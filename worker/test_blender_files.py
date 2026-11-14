import ast
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

# Load file-publication helpers without importing bpy or starting Blender.
tree = ast.parse(pathlib.Path(__file__).with_name('blender_create.py').read_text())
helpers = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('revision', 'publish_pair')], type_ignores=[])
namespace = {'os': os}
exec(compile(helpers, 'blender_create.py', 'exec'), namespace)
revision, publish = namespace['revision'], namespace['publish_pair']

class NativeFiles(unittest.TestCase):
    def fixture(self, folder):
        pairs = []
        for name in ('baseline', 'native'):
            source, target = pathlib.Path(folder)/('new-'+name), pathlib.Path(folder)/name
            source.write_bytes(b'new '+name.encode()); target.write_bytes(b'old '+name.encode())
            pairs.append((str(source), str(target)))
        return pairs

    def test_replacement_and_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            pairs = self.fixture(folder)
            backups = publish(pairs, True, revision([t for _,t in pairs]))
            self.assertEqual(len(backups), 2)
            for _, target in pairs: self.assertTrue(pathlib.Path(target).read_bytes().startswith(b'new'))
            for backup in backups: self.assertTrue(pathlib.Path(backup).read_bytes().startswith(b'old'))

    def test_failure_restores_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            pairs = self.fixture(folder)
            real_replace = os.replace
            def replace(source, target):
                if source == pairs[1][0]: raise OSError('locked native')
                return real_replace(source, target)
            with patch('os.replace', side_effect=replace):
                with self.assertRaises(OSError): publish(pairs, True, revision([t for _,t in pairs]))
            for _,target in pairs: self.assertTrue(pathlib.Path(target).read_bytes().startswith(b'old'))

    def test_changed_destination_and_no_consent_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            pairs = self.fixture(folder); expected = revision([t for _,t in pairs])
            with self.assertRaises(RuntimeError): publish(pairs, False, expected)
            pathlib.Path(pairs[0][1]).write_bytes(b'changed during import')
            with self.assertRaises(RuntimeError): publish(pairs, True, expected)
