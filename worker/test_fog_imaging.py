"""Opt-in RTX pixel tests. ORCHFLOW_TEST_FOG_RTX=1; optional ORCHFLOW_TEST_FOG_VDB.

VDB fixture: smoke.vdb from https://www.openvdb.org/download/ (external test asset).
"""
import base64
import copy
import io
import os
import pathlib
import tempfile
import unittest

if os.environ.get('ORCHFLOW_TEST_FOG_RTX') == '1':
    from ovrtx_worker import dispatch, close_viewport
    import numpy as np
    from PIL import Image
    from pxr import Usd, UsdGeom


@unittest.skipUnless(os.environ.get('ORCHFLOW_TEST_FOG_RTX') == '1', 'Opt-in RTX test')
class FogImagingTests(unittest.TestCase):
    def tearDown(self):
        close_viewport()

    def pixels(self, graph, **patch):
        request = dict(operation='render', path='graph://fog', graph=graph, renderer='OmniverseRTX',
                       ovrtxMode='pathTracing', width=320, height=240, maxSamples=64,
                       materials='scene', yaw=20, pitch=10, zoom=1)
        request.update(patch)
        for _ in range(4):
            result = dispatch(copy.deepcopy(request))
        self.assertFalse(result.get('missingAssets'))
        return np.asarray(Image.open(io.BytesIO(base64.b64decode(result['image'].split(',', 1)[1]))).convert('RGB')).astype(float)

    def graph(self, path, config):
        return {'targetNodeId':'fog','nodes':[{'id':'source','operation':'source','inputDependencyId':'asset'},
                {'id':'fog','operation':'fog','fog':config}], 'edges':[{'id':'e','source':'source','target':'fog'}],
                'dependencies':[{'id':'asset','path':str(path)}]}

    def test_atmosphere_pixels_update_disable_and_remove_in_both_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)/'cube.usda'
            stage = Usd.Stage.CreateNew(str(path))
            cube = UsdGeom.Mesh.Define(stage, '/Cube')
            cube.CreatePointsAttr([(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)])
            cube.CreateFaceVertexCountsAttr([4]*6)
            cube.CreateFaceVertexIndicesAttr([0,3,2,1,4,5,6,7,0,1,5,4,1,2,6,5,2,3,7,6,3,0,4,7])
            cube.CreateSubdivisionSchemeAttr('none')
            stage.SetDefaultPrim(cube.GetPrim())
            UsdGeom.SetStageMetersPerUnit(stage, 1)
            UsdGeom.SetStageUpAxis(stage, 'Y')
            stage.GetRootLayer().Save()
            original = path.read_bytes()
            for mode in ('pathTracing','realtime'):
                graph = self.graph(path, dict(enabled=False, volumesEnabled=False))
                clear = self.pixels(graph, ovrtxMode=mode)
                graph['nodes'][1]['fog'] = dict(volumesEnabled=False, color=[.1,.3,.8], endDistance=1, distanceDensity=100, heightDensity=100, height=100)
                haze = self.pixels(graph, ovrtxMode=mode)
                diff = abs(haze-clear).mean()
                self.assertGreater(diff, 5, (mode, diff))
                graph['nodes'][1]['fog']['enabled'] = False
                reset = self.pixels(graph, ovrtxMode=mode)
                self.assertLess(abs(reset-clear).mean(), 3)
                graph['targetNodeId'] = 'source'
                removed = self.pixels(graph, ovrtxMode=mode)
                self.assertLess(abs(removed-clear).mean(), 3)
            self.assertEqual(path.read_bytes(), original)

    @unittest.skipUnless(os.environ.get('ORCHFLOW_TEST_FOG_VDB'), 'External density VDB fixture not configured')
    def test_real_vdb_density_pixels_bounds_refresh_and_source_preservation(self):
        import hashlib
        path = pathlib.Path(os.environ['ORCHFLOW_TEST_FOG_VDB'])
        original = hashlib.sha256(path.read_bytes()).digest()
        graph = self.graph(path, dict(atmosphereEnabled=False, densityScale=0, volumeColor=[.4,.4,.4]))
        clear = self.pixels(graph)
        graph['nodes'][1]['fog']['densityScale'] = 10
        smoke = self.pixels(graph)
        diff = abs(smoke-clear).mean(axis=2)
        self.assertGreater((diff > 5).sum(), 100)
        self.assertLess((diff > 5).sum(), diff.size*.5)  # localized smoke, not full-frame fog
        graph['nodes'].append({'id':'haze','operation':'fog','fog':{'volumesEnabled':False,'atmosphereEnabled':False}})
        graph['edges'].append({'id':'tail','source':'fog','target':'haze'})
        graph['targetNodeId']='haze'
        passthrough = self.pixels(graph)
        self.assertLess(abs(passthrough-smoke).mean(), 2)
        graph['nodes'][1]['fog']['densityScale'] = 0
        reset = self.pixels(graph)
        self.assertLess(abs(reset-clear).mean(), 1)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), original)


if __name__ == '__main__':
    unittest.main()
