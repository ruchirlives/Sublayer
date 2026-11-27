import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

USD_ROOT = os.environ.get('ORCHFLOW_USD_ROOT') or r'I:\AI\OpenUSD-runtime\25.08'
if os.path.isdir(USD_ROOT):
    for sub in ('bin', 'lib', os.path.join('plugin', 'usd')):
        d = os.path.join(USD_ROOT, sub)
        if os.path.isdir(d) and hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(d)
    os.environ['PATH'] = os.pathsep.join([
        os.path.join(USD_ROOT, 'bin'),
        os.path.join(USD_ROOT, 'lib'),
        os.path.join(USD_ROOT, 'plugin', 'usd'),
        os.environ.get('PATH', ''),
    ])
    os.environ.setdefault('PXR_MTLX_STDLIB_SEARCH_PATHS', os.path.join(USD_ROOT, 'libraries'))
    py_dir = os.path.join(USD_ROOT, 'lib', 'python')
    if os.path.isdir(py_dir) and py_dir not in sys.path:
        sys.path.insert(0, py_dir)

WORKER_DIR = os.path.dirname(os.path.abspath(__file__))
if WORKER_DIR not in sys.path:
    sys.path.insert(0, WORKER_DIR)

from pxr import Usd, UsdGeom, UsdShade
import megascans
from usd_graph import compile_graph, convert_source_to_usd, _open_source_stage


# Minimal valid 1x1 PNG bytes
TINY_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0'
    b'\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _make_test_glb(path):
    positions = struct.pack('<9f', 0.0, 0.0, 0.0, 1.5, 0.0, 0.0, 0.0, 2.0, 0.0)
    uvs = struct.pack('<6f', 0.0, 1.0, 1.0, 1.0, 0.0, 0.0)
    indices = struct.pack('<3H', 0, 1, 2) + b'\x00\x00'
    img_bytes = TINY_PNG + (b'\x00' * ((4 - len(TINY_PNG) % 4) % 4))
    bin_blob = positions + uvs + indices + img_bytes

    off_pos = 0
    off_uv = len(positions)
    off_idx = off_uv + len(uvs)
    off_img = off_idx + len(indices)

    doc = {
        'asset': {'version': '2.0'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'nodes': [{'name': 'FabProp', 'mesh': 0, 'translation': [1.0, 2.0, 3.0]}],
        'meshes': [{
            'name': 'PropMesh',
            'primitives': [{
                'attributes': {'POSITION': 0, 'TEXCOORD_0': 1},
                'indices': 2,
                'material': 0,
            }],
        }],
        'materials': [{
            'name': 'PropMat',
            'pbrMetallicRoughness': {
                'baseColorTexture': {'index': 0},
                'metallicRoughnessTexture': {'index': 0},
                'metallicFactor': 0.2,
                'roughnessFactor': 0.7,
            },
            'alphaMode': 'MASK',
            'alphaCutoff': 0.4,
        }],
        'textures': [{'source': 0}],
        'images': [{'bufferView': 3, 'mimeType': 'image/png'}],
        'bufferViews': [
            {'buffer': 0, 'byteOffset': off_pos, 'byteLength': len(positions)},
            {'buffer': 0, 'byteOffset': off_uv, 'byteLength': len(uvs)},
            {'buffer': 0, 'byteOffset': off_idx, 'byteLength': 6},
            {'buffer': 0, 'byteOffset': off_img, 'byteLength': len(TINY_PNG)},
        ],
        'accessors': [
            {'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3'},
            {'bufferView': 1, 'componentType': 5126, 'count': 3, 'type': 'VEC2'},
            {'bufferView': 2, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'},
        ],
        'buffers': [{'byteLength': len(bin_blob)}],
    }
    json_bytes = json.dumps(doc).encode('utf-8')
    json_bytes += b' ' * ((4 - len(json_bytes) % 4) % 4)
    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)
    header = struct.pack('<4sII', b'glTF', 2, total_len)
    c0 = struct.pack('<II', len(json_bytes), 0x4E4F534A) + json_bytes
    c1 = struct.pack('<II', len(bin_blob), 0x004E4942) + bin_blob
    with open(path, 'wb') as f:
        f.write(header + c0 + c1)


def _make_ascii_fbx(path):
    content = """
; FBX 7.4.0 project file
GlobalSettings:  {
    Properties70:  {
        P: "UpAxis", "int", "Integer", "",1
        P: "UpAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",1.0
    }
}
Objects:  {
    Geometry: 100, "Geometry::Quad", "Mesh" {
        Vertices: *12 {
            a: 0, 0, 0, 100, 0, 0, 100, 200, 0, 0, 200, 0
        }
        PolygonVertexIndex: *4 {
            a: 0, 1, 2, -4
        }
        LayerElementUV: 0 {
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "Direct"
            UV: *8 {
                a: 0, 0, 1, 0, 1, 1, 0, 1
            }
        }
    }
    Model: 200, "Model::WallTile", "Mesh" {
        Properties70:  {
            P: "Lcl Translation", "Lcl Translation", "", "A", 0, 0, 0
        }
    }
}
Connections:  {
    C: "OO",200,0
    C: "OO",100,200
}
"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def _sha256(fp):
    with open(fp, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestMegascansAndFabImporters(unittest.TestCase):
    def test_glb_stage_builder_and_uv_flip(self):
        with tempfile.TemporaryDirectory() as tmp:
            glb_path = os.path.join(tmp, 'fab_prop.glb')
            _make_test_glb(glb_path)
            sha_before = _sha256(glb_path)

            stage = _open_source_stage(glb_path)
            meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
            mats = [p for p in stage.Traverse() if p.IsA(UsdShade.Material)]
            self.assertEqual(len(meshes), 1)
            self.assertEqual(len(mats), 1)

            st_pv = UsdGeom.PrimvarsAPI(meshes[0]).GetPrimvar('st').Get()
            # glTF V=1.0 -> OpenUSD st V=0.0; glTF V=0.0 -> OpenUSD st V=1.0
            self.assertAlmostEqual(float(st_pv[0][1]), 0.0, places=5)
            self.assertAlmostEqual(float(st_pv[2][1]), 1.0, places=5)

            sha_after = _sha256(glb_path)
            self.assertEqual(sha_before, sha_after)

    def test_ascii_fbx_and_companion_megascans_pbr(self):
        with tempfile.TemporaryDirectory() as tmp:
            fbx_path = os.path.join(tmp, 'tile_LOD0.fbx')
            _make_ascii_fbx(fbx_path)
            with open(os.path.join(tmp, 'tile_4K_Albedo.png'), 'wb') as f:
                f.write(TINY_PNG)
            with open(os.path.join(tmp, 'tile_4K_Normal_LOD0.png'), 'wb') as f:
                f.write(TINY_PNG)
            with open(os.path.join(tmp, 'tile_4K_Roughness.png'), 'wb') as f:
                f.write(TINY_PNG)
            desc_path = os.path.join(tmp, 'tile.json')
            with open(desc_path, 'w', encoding='utf-8') as f:
                json.dump({'name': 'Stone Wall Tile', 'categories': ['3d']}, f)

            graph = {
                'targetNodeId': 'out',
                'nodes': [
                    {
                        'id': 'src',
                        'operation': 'source',
                        'inputDependencyId': 'ms',
                        'megascansLod': 0,
                        'megascansResolution': '4K',
                        'megascansNormalConvention': 'directx',
                    },
                    {'id': 'out', 'operation': 'output'},
                ],
                'edges': [{'id': 'e1', 'source': 'src', 'target': 'out'}],
                'dependencies': [{'id': 'ms', 'kind': 'usd', 'path': desc_path}],
            }
            stage, _alive, _ = compile_graph(graph)
            bb = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default']).ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedRange()
            # 100cm x 200cm in FBX (UnitScaleFactor=1.0) -> 1.0m x 2.0m in canonical USD meters!
            sz = bb.GetSize()
            self.assertAlmostEqual(float(sz[0]), 1.0, places=4)
            self.assertAlmostEqual(float(sz[1]), 2.0, places=4)

            # Verify DirectX normal flip shader node was authored inside the referenced stage
            shader_ids = [
                UsdShade.Shader(p).GetIdAttr().Get()
                for p in stage.Traverse(Usd.TraverseInstanceProxies())
                if p.IsA(UsdShade.Shader)
            ]
            self.assertIn('ND_normalmap_float', shader_ids)
            self.assertIn('ND_multiply_vector3', shader_ids)

            # Verify explicit Convert to USD writes standalone USD without touching source files
            out_usdc = os.path.join(tmp, 'exported_tile.usdc')
            conv = convert_source_to_usd({'path': desc_path, 'destination': out_usdc})
            self.assertTrue(os.path.isfile(conv['destination']))
            self.assertGreater(conv['primCount'], 3)

    def test_fab_semantic_tags_zip_single_fbx_and_one_k_maps(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'asset.fbx')
            _make_ascii_fbx(source)
            archive = os.path.join(tmp, 'FabAsset.zip')
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.write(source, 'Wrapper/asset.fbx')
                zipped.writestr('Wrapper/asset.json', json.dumps({
                    'semanticTags': {'name': 'Fab Stone'},
                    'assetCategories': {'3d': {}},
                }))
                for channel in ('BaseColor', 'Specular', 'Cavity'):
                    zipped.writestr('Wrapper/asset_1K_' + channel + '.png', TINY_PNG)
            destination = os.path.join(tmp, 'extracted')
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(destination)
            folder = os.path.join(destination, 'Wrapper')
            before = {name: hashlib.sha256(Path(folder, name).read_bytes()).hexdigest()
                      for name in os.listdir(folder)}
            info = megascans.inspect_megascans_folder(folder)
            self.assertEqual(info['name'], 'Fab Stone')
            self.assertIn('1K', info['availableResolutions'])
            self.assertIn('specular', info['maps'])
            self.assertIn('cavity', info['maps'])
            self.assertIn('albedo', info['maps'])
            stage, alive, _ = compile_graph({
                'targetNodeId': 'asset',
                'nodes': [{'id': 'asset', 'operation': 'source', 'inputDependencyId': 'asset',
                           'megascansResolution': '1K'}],
                'edges': [],
                'dependencies': [{'id': 'asset', 'path': os.path.join(folder, 'asset.json')}],
            })
            self.assertTrue(any(prim.IsA(UsdGeom.Mesh) for prim in stage.Traverse(Usd.TraverseInstanceProxies())))
            after = {name: hashlib.sha256(Path(folder, name).read_bytes()).hexdigest()
                     for name in os.listdir(folder)}
            self.assertEqual(before, after)

    @unittest.skipUnless(
        os.path.isfile(r'I:\EpicGames\Megascans\Downloaded\3dplant\plants_3d_tbdpec3r\tbdpec3r.json'),
        'Local Megascans library fixture not available',
    )
    def test_real_megascans_library_lod_and_scatter(self):
        plant_json = r'I:\EpicGames\Megascans\Downloaded\3dplant\plants_3d_tbdpec3r\tbdpec3r.json'
        sha_before = _sha256(plant_json)

        graph = {
            'targetNodeId': 'scat',
            'nodes': [
                {
                    'id': 'plant',
                    'operation': 'source',
                    'inputDependencyId': 'p_dep',
                    'instanced': False,
                    'megascansLod': 2,
                    'megascansResolution': '2K',
                },
                {
                    'id': 'scat',
                    'operation': 'scatter',
                    'scatter': {
                        'count': 12,
                        'seed': 7,
                        'distribution': 'grid',
                        'areaSize': [4.0, 0.0, 4.0],
                        'scaleMin': 0.9,
                        'scaleMax': 1.1,
                        'randomYaw': True,
                        'passThrough': False,
                        'prototypes': [
                            {'id': 'r1', 'enabled': True, 'primPath': '/Scene/Megascans/Var1'},
                            {'id': 'r2', 'enabled': True, 'primPath': '/Scene/Megascans/Var2'},
                        ],
                    },
                },
            ],
            'edges': [{'id': 'e1', 'source': 'plant', 'target': 'scat'}],
            'dependencies': [{'id': 'p_dep', 'kind': 'usd', 'path': plant_json}],
        }
        stage, _alive, _ = compile_graph(graph)
        instancer = UsdGeom.PointInstancer(stage.GetPrimAtPath('/Scene/Scatter'))
        self.assertTrue(instancer.GetPrim().IsValid())
        self.assertEqual(len(instancer.GetPositionsAttr().Get()), 12)
        self.assertEqual(len(instancer.GetPrototypesRel().GetTargets()), 2)

        sha_after = _sha256(plant_json)
        self.assertEqual(sha_before, sha_after)


if __name__ == '__main__':
    unittest.main()
