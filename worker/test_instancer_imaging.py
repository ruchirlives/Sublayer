"""Headless GPU verification; run separately in Storm and RTX Python runtimes.
Set ORCHFLOW_TEST_INSTANCER_RTX=1 to select the standalone NVIDIA worker.
"""
import os
import copy
import unittest

# Runtime setup must precede importing pxr fixtures in the Storm environment.
if os.environ.get('ORCHFLOW_TEST_INSTANCER_RTX') == '1':
    from ovrtx_worker import dispatch, close_viewport
    RENDERER = 'OmniverseRTX'
else:
    from usd_worker import dispatch, close_viewport
    RENDERER = 'HdStormRendererPlugin'
import test_instancer
from pxr import Gf

@unittest.skipUnless(os.environ.get('ORCHFLOW_USD_ROOT') or os.environ.get('ORCHFLOW_TEST_INSTANCER_RTX'), 'Imaging runtime not configured')
class InstancerImagingTests(unittest.TestCase):
    def test_generated_instances_render_pick_and_refresh(self):
        fixture = test_instancer.InstancerTests()
        fixture.setUp()
        try:
            request = {'operation':'render','path':'graph://manual','graph':fixture.graph,'renderer':RENDERER,'width':480,'height':320,'materials':'solid','yaw':0,'pitch':0,'zoom':1.1,'projection':'orthographic','ovrtxMode':'realtime'}
            first = dispatch(copy.deepcopy(request))
            self.assertEqual(len(first['instancerHandles']),2)
            handle = first['instancerHandles'][0]
            vp = Gf.Matrix4d(*[v for row in first['viewProjection'] for v in row])
            point = vp.Transform(Gf.Vec3d(*handle['origin']))
            picked = dispatch(dict(copy.deepcopy(request),pick=[(point[0]+1)/2,(1-point[1])/2]))
            self.assertTrue(picked['pickedPrim'].startswith(handle['path']+'/'))
            self.assertIsNotNone(picked['pickedObject']['origin'])
            self.assertTrue(picked['pickedObject']['sourcePrim'].startswith(handle['path']+'/'))
            fixture.graph['nodes'][1]['instancer']['instances'][0]['transform']['translation']=[3,2,0]
            changed = dispatch(copy.deepcopy(request))
            self.assertAlmostEqual(changed['instancerHandles'][0]['origin'][1]-first['instancerHandles'][0]['origin'][1],2,places=5)
            self.assertNotEqual(first['image'],changed['image'])
            # The analytic Cube has no triangle hit; pivot picking remains usable.
            self.assertEqual(handle['id'],'one')
        finally:
            close_viewport()
            fixture.doCleanups()

if __name__ == '__main__':
    unittest.main()
