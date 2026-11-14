import os
import struct
import tempfile
import unittest

from pxr import Usd, UsdGeom, UsdShade

from usd_graph import compile_graph, convert_source_to_usd, _open_source_stage
from usd_worker import dispatch


def _goz_block(tag, count, payload):
    length = 16 + len(payload)
    return struct.pack('<IIQ', tag, length, count) + payload


def _build_sample_goz(meshes):
    buf = bytearray(b'GoZb 1.0 ZBrush.................')
    for m in meshes:
        name_bytes = m['name'].encode('utf-8') + b'\x00'
        buf.extend(_goz_block(1, 1, name_bytes))

        pts_payload = b''.join(struct.pack('<3f', *p) for p in m['points'])
        buf.extend(_goz_block(10001, len(m['points']), pts_payload))

        faces_payload = b''.join(
            struct.pack('<4i', f[0], f[1], f[2], f[3] if len(f) == 4 else -1)
            for f in m['faces']
        )
        buf.extend(_goz_block(20001, len(m['faces']), faces_payload))

        if m.get('uvs'):
            uv_payload = b''.join(struct.pack('<8f', *uv8) for uv8 in m['uvs'])
            buf.extend(_goz_block(25001, len(m['uvs']), uv_payload))

        if m.get('polypaint'):
            pp_payload = b''.join(struct.pack('<4B', b, g, r, a) for b, g, r, a in m['polypaint'])
            buf.extend(_goz_block(30001, len(m['polypaint']), pp_payload))

        if m.get('mask'):
            mk_payload = b''.join(struct.pack('<H', v) for v in m['mask'])
            buf.extend(_goz_block(40001, len(m['mask']), mk_payload))

        if m.get('polygroups'):
            pg_payload = b''.join(struct.pack('<H', g) for g in m['polygroups'])
            buf.extend(_goz_block(50001, len(m['polygroups']), pg_payload))

        if m.get('diffuse_map'):
            buf.extend(_goz_block(60001, 1, m['diffuse_map'].encode('utf-8') + b'\x00'))

        if m.get('displacement_map'):
            buf.extend(_goz_block(70001, 1, m['displacement_map'].encode('utf-8') + b'\x00'))

        if m.get('normal_map'):
            buf.extend(_goz_block(80001, 1, m['normal_map'].encode('utf-8') + b'\x00'))

        buf.extend(_goz_block(0, 0, b''))
    return bytes(buf)


class GozSourceTests(unittest.TestCase):
    def test_goz_full_mesh_attributes_compile_convert_and_inspect(self):
        with tempfile.TemporaryDirectory() as tmp:
            goz_path = os.path.join(tmp, 'sculpt.goz')
            diff_tex = os.path.join(tmp, 'diff.png')
            norm_tex = os.path.join(tmp, 'norm.png')
            disp_tex = os.path.join(tmp, 'disp.exr')
            for p in (diff_tex, norm_tex, disp_tex):
                with open(p, 'wb') as f:
                    f.write(b'stub')

            data = _build_sample_goz([
                {
                    'name': 'GoZMesh_Head',
                    'points': [
                        (0.0, 0.0, 0.0),
                        (1.0, 0.0, 0.0),
                        (1.0, 1.0, 0.0),
                        (0.0, 1.0, 0.0),
                    ],
                    'faces': [
                        (0, 1, 2, 3),
                        (0, 1, 2),
                    ],
                    'uvs': [
                        (0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0),
                        (0.0, 1.0, 1.0, 1.0, 0.5, 0.0, 0.0, 0.0),
                    ],
                    'polypaint': [
                        (0, 0, 255, 255),
                        (0, 255, 0, 255),
                        (255, 0, 0, 255),
                        (255, 255, 255, 255),
                    ],
                    'mask': [0, 32767, 65535, 0],
                    'polygroups': [1, 2],
                    'diffuse_map': 'diff.png',
                    'normal_map': 'norm.png',
                    'displacement_map': 'disp.exr',
                },
                {
                    'name': 'GoZMesh_Eyes',
                    'points': [(2.0, 0.0, 0.0), (3.0, 0.0, 0.0), (2.5, 1.0, 0.0)],
                    'faces': [(0, 1, 2)],
                },
            ])
            with open(goz_path, 'wb') as f:
                f.write(data)

            stage = _open_source_stage(goz_path)
            self.assertEqual(UsdGeom.GetStageUpAxis(stage), UsdGeom.Tokens.y)
            head_prim = stage.GetPrimAtPath('/GoZ/Head')
            self.assertTrue(head_prim.IsValid())
            head_mesh = UsdGeom.Mesh(head_prim)
            self.assertEqual(list(head_mesh.GetFaceVertexCountsAttr().Get()), [4, 3])
            self.assertEqual(list(head_mesh.GetFaceVertexIndicesAttr().Get()), [0, 1, 2, 3, 0, 1, 2])

            pv_api = UsdGeom.PrimvarsAPI(head_mesh)
            st_vals = list(pv_api.GetPrimvar('st').Get())
            self.assertEqual(len(st_vals), 7)
            self.assertAlmostEqual(st_vals[0][0], 0.0, places=5)
            self.assertAlmostEqual(st_vals[0][1], 0.0, places=5)

            dc_vals = list(pv_api.GetPrimvar('displayColor').Get())
            self.assertAlmostEqual(dc_vals[0][0], 1.0, places=5)
            self.assertAlmostEqual(dc_vals[0][2], 0.0, places=5)

            mask_vals = list(pv_api.GetPrimvar('zbrush_mask').Get())
            self.assertEqual(len(mask_vals), 4)
            self.assertAlmostEqual(mask_vals[2], 1.0, places=5)

            pg_vals = list(pv_api.GetPrimvar('zbrush_polygroup').Get())
            self.assertEqual(pg_vals, [1, 2])
            self.assertTrue(stage.GetPrimAtPath('/GoZ/Head/polygroup_1').IsValid())
            self.assertTrue(stage.GetPrimAtPath('/GoZ/Head/polygroup_2').IsValid())

            bound_mat = UsdShade.MaterialBindingAPI(head_prim).GetDirectBinding().GetMaterial()
            self.assertTrue(bound_mat.GetPrim().IsValid())
            self.assertTrue(stage.GetPrimAtPath('/GoZ/Eyes').IsValid())

            graph = {
                'nodes': [
                    {
                        'id': 'src',
                        'operation': 'source',
                        'inputDependencyId': 'goz-src',
                        'axis': 'preserve',
                        'scale': 1.0,
                    }
                ],
                'edges': [],
                'dependencies': [{'id': 'goz-src', 'path': goz_path}],
                'targetNodeId': 'src',
            }
            compiled, _keepalive, _stats = compile_graph(graph, reload=True)
            self.assertTrue(compiled.GetPrimAtPath('/Scene/GoZ/Head').IsValid())

            out_usd = os.path.join(tmp, 'sculpt.usdc')
            conv = convert_source_to_usd({'path': goz_path, 'destination': out_usd})
            self.assertEqual(conv['sourceFormat'], 'goz')
            self.assertTrue(os.path.isfile(out_usd))
            reopened = Usd.Stage.Open(out_usd)
            self.assertTrue(reopened.GetPrimAtPath('/GoZ/Head').IsValid())

            inspected = dispatch({'operation': 'inspect', 'path': goz_path})
            self.assertEqual(inspected['defaultPrim'], '/GoZ')

    def test_source_axis_and_scale_apply_after_load(self):
        from pxr import Gf

        with tempfile.TemporaryDirectory() as tmp:
            goz_path = os.path.join(tmp, 'box.goz')
            with open(goz_path, 'wb') as f:
                f.write(
                    _build_sample_goz([
                        {
                            'name': 'Box',
                            'points': [(0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 2.0, 0.0)],
                            'faces': [(0, 1, 2)],
                        }
                    ])
                )

            cases = {
                'preserve': Gf.Vec3d(0.0, 1.0, 0.0),
                'Y': Gf.Vec3d(0.0, 1.0, 0.0),
                '-Y': Gf.Vec3d(0.0, -1.0, 0.0),
                'Z': Gf.Vec3d(0.0, 0.0, 1.0),
                '-Z': Gf.Vec3d(0.0, 0.0, -1.0),
                'X': Gf.Vec3d(1.0, 0.0, 0.0),
                '-X': Gf.Vec3d(-1.0, 0.0, 0.0),
            }
            for axis_opt, source_up in cases.items():
                graph = {
                    'nodes': [
                        {
                            'id': 'src',
                            'operation': 'source',
                            'inputDependencyId': 'goz-src',
                            'axis': axis_opt,
                            'scale': 0.2,
                        }
                    ],
                    'edges': [],
                    'dependencies': [{'id': 'goz-src', 'path': goz_path}],
                    'targetNodeId': 'src',
                }
                compiled, _keepalive, _stats = compile_graph(graph, reload=False)
                xform = UsdGeom.Xformable(compiled.GetDefaultPrim()).GetLocalTransformation()
                mapped = xform.Transform(source_up)
                self.assertAlmostEqual(mapped[0], 0.0, places=5, msg=axis_opt)
                self.assertAlmostEqual(mapped[1], 0.2, places=5, msg=axis_opt)
                self.assertAlmostEqual(mapped[2], 0.0, places=5, msg=axis_opt)

    def test_rejects_invalid_goz_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.goz')
            with open(bad, 'wb') as f:
                f.write(b'NOT_A_GOZ_FILE_00000000000000000')
            with self.assertRaises(ValueError):
                _open_source_stage(bad)


if __name__ == '__main__':
    unittest.main()
