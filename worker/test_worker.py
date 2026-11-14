import pathlib
import unittest
from usd_worker import dispatch


class WorkerTests(unittest.TestCase):
    def test_real_stage_metadata(self):
        path = pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda'
        before = path.read_bytes()
        result = dispatch({'operation': 'inspect', 'path': str(path)})
        self.assertEqual(result['upAxis'], 'Y')
        self.assertEqual(result['metersPerUnit'], 1)
        self.assertEqual(result['rootPrims'][0]['path'], '/World')
        self.assertFalse(result['payloadsLoaded'])
        self.assertEqual(path.read_bytes(), before)

    def test_capabilities_describe_actual_runtime(self):
        result = dispatch({'operation': 'capabilities'})
        self.assertIn('viewport', result)
        if not result['viewport']:
            self.assertEqual(result['renderers'], [])
            self.assertIn('viewportError', result)
        else:
            self.assertIn('gpu', result)

    def test_unknown_operation(self):
        with self.assertRaises(ValueError):
            dispatch({'operation': 'flatten'})


if __name__ == '__main__':
    unittest.main()
