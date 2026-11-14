"""Opt-in real GPU integration tests. Requires the imaging runtime environment."""
import base64
import os
import pathlib
import tempfile
import unittest
from usd_worker import dispatch, close_viewport, finite_number


class RequestValidationTests(unittest.TestCase):
    def test_reject_nonfinite_and_out_of_bounds_camera_values(self):
        for value in (float('nan'), float('inf'), -1, True, '64'):
            with self.assertRaises(ValueError):
                finite_number(value, 'width', 64, 2048)


@unittest.skipUnless(os.environ.get('ORCHFLOW_USD_ROOT'), 'Full OpenUSD imaging runtime not configured')
class ImagingTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        close_viewport()

    def request(self, **overrides):
        req = {'operation': 'render', 'renderer': 'HdStormRendererPlugin',
               'path': str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'layered.usda'),
               'width': 480, 'height': 320}
        req.update(overrides)
        return dispatch(req)

    def test_discovery_reports_real_gpu(self):
        caps = dispatch({'operation': 'capabilities'})
        self.assertTrue(caps['viewport'], caps.get('viewportError'))
        self.assertIn('HdStormRendererPlugin', [p['id'] for p in caps['renderers']])
        self.assertIn('NVIDIA', caps['gpu'])

    def test_render_orbit_resize_and_source_preservation(self):
        root = pathlib.Path(__file__).resolve().parents[1] / 'samples'
        before = {p: p.read_bytes() for p in root.glob('*.usda')}
        first = self.request()
        second = self.request(yaw=95, width=640, height=480)
        self.assertNotEqual(first['image'], second['image'])
        self.assertEqual((second['width'], second['height']), (640, 480))
        from PySide6.QtGui import QImage
        png = base64.b64decode(first['image'].split(',')[1])
        image = QImage.fromData(png, 'PNG')
        self.assertFalse(image.isNull())
        self.assertNotEqual(image.pixelColor(240, 160), image.pixelColor(0, 0))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_skeleton_graph_and_handoff_render_without_instance_adapter_errors(self):
        from test_graph import skeletal_fixture, graph
        from usd_graph import export_handoff
        with tempfile.TemporaryDirectory() as folder:
            path=skeletal_fixture(folder)
            spec=graph(path)
            rendered=self.request(graph=spec,materials='solid')
            self.assertTrue(rendered['image'].startswith('data:image/png'))
            output=pathlib.Path(folder)/'handoff.usda'
            export_handoff({'graph':spec,'app':'Blender','upAxis':'Z','metersPerUnit':1,'destination':str(output)})
            rendered=self.request(path=str(output),materials='solid',frame=1)
            self.assertTrue(rendered['image'].startswith('data:image/png'))

    def test_scoped_publication_renders_through_application_output(self):
        from test_publish import PublishTests
        from usd_publish import review_publication,publish_reviewed
        with tempfile.TemporaryDirectory() as folder:
            request,source,baseline,edited=PublishTests().fixture(folder,['points'])
            edited.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(2,0,0),(0,1,0)]);edited.GetRootLayer().Save()
            review=review_publication(request);request.update(reviewToken=review['token'],destination=str(pathlib.Path(folder)/'publication.usda'))
            result=publish_reviewed(request)
            graph={'targetNodeId':'app','nodes':[{'id':'app','operation':'application','publishedDependencyId':'output','publication':{'sourceRevisions':request['sourceRevisions']}}],'edges':[],'dependencies':[{'id':'output','path':result['path']}]}
            rendered=self.request(graph=graph,materials='solid')
            self.assertTrue(rendered['image'].startswith('data:image/png'))
            from PySide6.QtGui import QImage
            image=QImage.fromData(base64.b64decode(rendered['image'].split(',')[1]));background=image.pixelColor(0,0)
            self.assertTrue(any(image.pixelColor(x,y)!=background for x in range(20,480,20) for y in range(20,320,20)))

    def test_missing_renderer_does_not_fallback(self):
        with self.assertRaisesRegex(ValueError, 'no fallback'):
            self.request(renderer='not-installed')

    def test_graph_output_renders_shared_instances_and_motion_frames(self):
        from test_graph import graph
        path=pathlib.Path(__file__).resolve().parents[1]/'samples'/'layered.usda'
        spec=graph(path)
        result=self.request(graph=spec)
        self.assertEqual(result['graphStats']['uniqueSourceAssets'],1)
        self.assertGreater(result['graphStats']['prototypes'],0)
        self.assertTrue(result['image'].startswith('data:image/png;base64,'))
        motion=self.request(graph=spec,interactive=True,yaw=80)
        self.assertTrue(motion['image'].startswith('data:image/jpeg;base64,'))
        self.assertNotEqual(result['image'],motion['image'])

    def test_interactive_jpeg_and_lossless_release_preserve_camera_and_bounds(self):
        from PySide6.QtGui import QImage
        full = self.request(width=640, height=480)
        motion = self.request(interactive=True, width=480, height=360, yaw=65, panX=0.1)
        self.assertTrue(motion['interactive'])
        self.assertEqual(motion['encoding'], 'jpeg')
        self.assertTrue(motion['image'].startswith('data:image/jpeg;base64,'))
        image = QImage.fromData(base64.b64decode(motion['image'].split(',')[1]))
        self.assertEqual((image.width(), image.height()), (480, 360))
        self.assertFalse(image.isNull())
        final = self.request(interactive=False, width=640, height=480)
        self.assertFalse(final['interactive'])
        self.assertEqual(final['encoding'], 'png')
        self.assertTrue(final['image'].startswith('data:image/png;base64,'))
        self.assertEqual(full['image'], final['image'], 'panning must not mutate cached scene bounds')

    def test_missing_opacity_texture_uses_visible_auto_preview_without_edits(self):
        from pxr import Usd, UsdGeom, UsdShade, Sdf
        from PySide6.QtGui import QImage, QColor
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            path = str(root / 'missing-opacity.usda')
            stage = Usd.Stage.CreateNew(path)
            sphere = UsdGeom.Sphere.Define(stage, '/Sphere')
            material = UsdShade.Material.Define(stage, '/Material')
            shader = UsdShade.Shader.Define(stage, '/Material/Surface')
            shader.CreateIdAttr('UsdPreviewSurface')
            material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput('surface', Sdf.ValueTypeNames.Token))
            texture = UsdShade.Shader.Define(stage, '/Material/Opacity')
            texture.CreateIdAttr('UsdUVTexture')
            texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./opacity.png'))
            texture.CreateInput('fallback', Sdf.ValueTypeNames.Float4).Set((0, 0, 0, 1))
            shader.CreateInput('opacity', Sdf.ValueTypeNames.Float).ConnectToSource(texture.CreateOutput('r', Sdf.ValueTypeNames.Float))
            UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
            stage.GetRootLayer().Save()
            before = pathlib.Path(path).read_bytes()
            auto = self.request(path=path, reload=True)
            self.assertEqual(auto['materialMode'], 'mixed')
            self.assertEqual(auto['fallbackMaterials'], ['/Material'])
            self.assertEqual(auto['missingAssets'][0]['path'], './opacity.png')
            self.assertTrue(auto['warnings'])
            image = QImage.fromData(base64.b64decode(auto['image'].split(',')[1]), 'PNG')
            self.assertNotEqual(image.pixelColor(240, 160), image.pixelColor(0, 0))
            scene = self.request(path=path, materials='scene')
            self.assertEqual(scene['materialMode'], 'scene')
            self.assertNotEqual(auto['image'], scene['image'])
            self.assertEqual(pathlib.Path(path).read_bytes(), before)
            restored = QImage(2, 2, QImage.Format.Format_RGBA8888)
            restored.fill(QColor('white'))
            replacement = str(root / 'replacement-opacity.png')
            self.assertTrue(restored.save(replacement))
            spec = {'targetNodeId':'override', 'nodes':[
                {'id':'source','operation':'source','inputDependencyId':'usd'},
                {'id':'override','operation':'assetOverride','assetOverrides':[
                    {'originalPath':str(root/'opacity.png'),'replacementDependencyId':'texture'}]}],
                'edges':[{'id':'edge','source':'source','target':'override'}],
                'dependencies':[{'id':'usd','path':path},{'id':'texture','path':replacement}]}
            overridden = self.request(graph=spec, reload=True)
            self.assertEqual(overridden['materialMode'], 'scene')
            self.assertEqual(overridden['missingAssets'], [])
            self.assertFalse((root/'opacity.png').exists())
            self.assertEqual(pathlib.Path(path).read_bytes(), before)
            self.assertTrue(restored.save(str(root / 'opacity.png')))
            repaired = self.request(path=path, reload=True)
            self.assertEqual(repaired['materialMode'], 'scene')
            self.assertEqual(repaired['missingAssets'], [])
            self.assertEqual(repaired['warnings'], [])
            self.assertEqual(pathlib.Path(path).read_bytes(), before)
            self.request()

    def test_z_up_and_reload(self):
        from pxr import Usd, UsdGeom
        with tempfile.TemporaryDirectory() as folder:
            path = str(pathlib.Path(folder) / 'z.usda')
            stage = Usd.Stage.CreateNew(path)
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
            sphere = UsdGeom.Sphere.Define(stage, '/Sphere')
            sphere.CreateDisplayColorAttr([(0.8, 0.3, 0.1)])
            stage.GetRootLayer().Save()
            first = self.request(path=path, reload=True)
            self.assertEqual(first['upAxis'], 'Z')
            sphere.CreateDisplayColorAttr([(0.1, 0.3, 0.8)])
            stage.GetRootLayer().Save()
            second = self.request(path=path, reload=True)
            self.assertNotEqual(first['image'], second['image'])
            self.request()  # release the temporary stage before cleanup on Windows

    def test_aurora_delegate_renders_and_switches_to_storm(self):
        caps = dispatch({'operation': 'capabilities'})
        ids = [p['id'] for p in caps['renderers']]
        if 'HdAuroraRendererPlugin' not in ids:
            self.skipTest('HdAuroraRendererPlugin not installed')
        aurora = self.request(renderer='HdAuroraRendererPlugin')
        self.assertEqual(aurora['renderer'], 'HdAuroraRendererPlugin')
        self.assertTrue(aurora['image'].startswith('data:image/png;base64,'))
        from PySide6.QtGui import QImage
        image = QImage.fromData(base64.b64decode(aurora['image'].split(',')[1]), 'PNG')
        self.assertFalse(image.isNull())
        self.assertNotEqual(image.pixelColor(240, 160), image.pixelColor(0, 0))
        storm = self.request(renderer='HdStormRendererPlugin')
        self.assertEqual(storm['renderer'], 'HdStormRendererPlugin')
        self.assertTrue(storm['image'].startswith('data:image/png;base64,'))
