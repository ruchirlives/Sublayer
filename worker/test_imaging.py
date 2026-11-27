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

    def test_orthographic_projection_presets_picking_and_anchor_settle(self):
        from pxr import Usd, UsdGeom
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory() as folder:
            path = str(pathlib.Path(folder) / 'ortho_pair.usda')
            stage = Usd.Stage.CreateNew(path)
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(stage, 1.0)
            root = UsdGeom.Xform.Define(stage, '/World')
            stage.SetDefaultPrim(root.GetPrim())

            def define_box_mesh(prim_path, tx, tz, color):
                mesh = UsdGeom.Mesh.Define(stage, prim_path)
                h = 0.5
                mesh.CreatePointsAttr([
                    (-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
                    (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h),
                ])
                mesh.CreateFaceVertexCountsAttr([4, 4, 4, 4, 4, 4])
                mesh.CreateFaceVertexIndicesAttr([
                    4, 5, 6, 7,  1, 0, 3, 2,  0, 4, 7, 3,
                    5, 1, 2, 6,  3, 7, 6, 2,  0, 1, 5, 4,
                ])
                mesh.AddTranslateOp().Set((tx, 0.0, tz))
                mesh.CreateDisplayColorAttr([color])
                return mesh

            # Near box at (-1.2, 0, +1.5), Far box at (+1.2, 0, -1.5), identical size 1.0
            define_box_mesh('/World/NearBox', -1.2, 1.5, (0.95, 0.25, 0.25))
            define_box_mesh('/World/FarBox', 1.2, -1.5, (0.25, 0.65, 0.95))
            stage.GetRootLayer().Save()

            def side_span(img_b64):
                img = QImage.fromData(base64.b64decode(img_b64.split(',')[1]), 'PNG')
                w, h = img.width(), img.height()
                bg = img.pixelColor(0, 0)
                left_ys = [y for y in range(h) for x in range(w // 2) if img.pixelColor(x, y) != bg]
                right_ys = [y for y in range(h) for x in range(w // 2, w) if img.pixelColor(x, y) != bg]
                return (max(left_ys) - min(left_ys)), (max(right_ys) - min(right_ys))

            persp = self.request(path=path, width=480, height=320, yaw=0, pitch=0, zoom=1.2, projection='perspective', materials='solid', reload=True)
            ortho = self.request(path=path, width=480, height=320, yaw=0, pitch=0, zoom=1.2, projection='orthographic', materials='solid')
            self.assertEqual(persp['projection'], 'perspective')
            self.assertEqual(ortho['projection'], 'orthographic')
            p_left_h, p_right_h = side_span(persp['image'])
            o_left_h, o_right_h = side_span(ortho['image'])
            self.assertGreater(p_left_h, p_right_h + 10, 'Perspective must project the closer box taller than the far box')
            self.assertLessEqual(abs(o_left_h - o_right_h), 2, 'Orthographic must project identical-size boxes at different depths to equal height')

            # Exact Top (+90) and Bottom (-90) views must render cleanly with no gimbal singularity
            for pitch_deg in (90, -90):
                polar = self.request(path=path, width=320, height=240, yaw=0, pitch=pitch_deg, projection='orthographic', materials='solid')
                self.assertEqual(polar['projection'], 'orthographic')
                self.assertEqual(len(polar['viewProjection']), 4)

            # Orthographic picking hits the off-center front face (z = +2.0) via parallel ray
            vp = ortho['viewProjection']
            clip = [sum(v * vp[r][c] for r, v in enumerate((-1.2, 0.0, 2.0, 1.0))) for c in range(4)]
            u_near = (clip[0] / clip[3] + 1.0) * 0.5
            v_near = (1.0 - clip[1] / clip[3]) * 0.5
            picked = self.request(path=path, width=480, height=320, yaw=0, pitch=0, zoom=1.2, projection='orthographic', materials='solid', pick=[u_near, v_near])
            self.assertEqual(picked['pickedPrim'], '/World/NearBox')
            self.assertIsNotNone(picked['pickedPoint'])
            self.assertAlmostEqual(picked['pickedPoint'][0], -1.2, delta=0.05)
            self.assertAlmostEqual(picked['pickedPoint'][2], 2.0, delta=0.05)

            # Orthographic transform-anchor settling keeps zoom unchanged when translating along camera depth
            graph_base = {
                'targetNodeId': 'out',
                'nodes': [
                    {'id': 'src', 'operation': 'source', 'inputDependencyId': 'dep'},
                    {'id': 'xf', 'operation': 'transform', 'transform': {'translation': [0.0, 0.0, 0.0], 'rotation': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0]}},
                    {'id': 'out', 'operation': 'output'},
                ],
                'edges': [{'id': 'e1', 'source': 'src', 'target': 'xf'}, {'id': 'e2', 'source': 'xf', 'target': 'out'}],
                'dependencies': [{'id': 'dep', 'path': path}],
            }
            self.request(graph=graph_base, cameraSession='ortho-settle', frameFit=1, yaw=0, pitch=0, zoom=1.35, projection='orthographic', transformGizmoNodeId='xf', materials='solid')
            graph_moved = {
                **graph_base,
                'nodes': [
                    graph_base['nodes'][0],
                    {'id': 'xf', 'operation': 'transform', 'transform': {'translation': [0.0, 0.0, 2.0], 'rotation': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0]}},
                    graph_base['nodes'][2],
                ],
            }
            settled = self.request(graph=graph_moved, cameraSession='ortho-settle', frameFit=1, yaw=0, pitch=0, zoom=1.35, projection='orthographic', transformGizmoNodeId='xf', materials='solid')
            self.assertIsNotNone(settled['cameraSync'])
            self.assertAlmostEqual(settled['cameraSync']['zoom'], 1.35, places=6)

            # Reject invalid projection
            with self.assertRaises(ValueError):
                self.request(projection='fisheye')
            self.request()
