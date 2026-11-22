import os
import tempfile
import unittest
import usd_worker
import usd_graph
from pxr import Usd, UsdGeom

class BlenderNodeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.test_usd = os.path.join(self._tmp.name, 'test_blender_node_stage.usda')
        st = Usd.Stage.CreateNew(self.test_usd)
        UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
        root = UsdGeom.Xform.Define(st, '/root')
        st.SetDefaultPrim(root.GetPrim())
        mesh = UsdGeom.Mesh.Define(st, '/root/ImportedMesh')
        st.GetRootLayer().Save()

    def tearDown(self):
        usd_graph.clear_graph_caches()
        self._tmp.cleanup()

    def test_blender_node_compilation_with_exported_usd(self):
        graph = {
            'targetNodeId': 'blender1',
            'nodes': [
                {
                    'id': 'blender1',
                    'operation': 'blender',
                    'label': 'Blender',
                    'publishedDependencyId': 'dep_usd',
                }
            ],
            'edges': [],
            'dependencies': [
                {'id': 'dep_usd', 'path': self.test_usd}
            ]
        }
        stage, alive, stats = usd_graph.compile_graph(graph)
        self.assertTrue(stage)
        prim = stage.GetPrimAtPath('/Scene/root/ImportedMesh')
        self.assertTrue(prim.IsValid(), 'Expected imported mesh under /Scene/root/ImportedMesh')

    def test_blender_node_missing_usd_raises_clear_error(self):
        graph = {
            'targetNodeId': 'blender1',
            'nodes': [
                {
                    'id': 'blender1',
                    'operation': 'blender',
                    'label': 'Blender',
                }
            ],
            'edges': [],
            'dependencies': []
        }
        with self.assertRaises(ValueError) as ctx:
            usd_graph.compile_graph(graph)
        self.assertIn('needs a linked .blend file', str(ctx.exception))

    def test_blender_node_connects_to_downstream_transform(self):
        graph = {
            'targetNodeId': 'xf1',
            'nodes': [
                {
                    'id': 'blender1',
                    'operation': 'blender',
                    'label': 'Blender',
                    'publishedDependencyId': 'dep_usd',
                },
                {
                    'id': 'xf1',
                    'operation': 'transform',
                    'label': 'Transform',
                    'transform': {
                        'translation': [10.0, 0.0, 0.0],
                        'rotation': [0.0, 0.0, 0.0],
                        'scale': [1.0, 1.0, 1.0]
                    }
                }
            ],
            'edges': [
                {'id': 'e1', 'source': 'blender1', 'target': 'xf1'}
            ],
            'dependencies': [
                {'id': 'dep_usd', 'path': self.test_usd}
            ]
        }
        stage, alive, stats = usd_graph.compile_graph(graph)
        self.assertTrue(stage)
        self.assertEqual(stats['evaluatedNodes'], 2)

    def test_filter_preserves_usd_instances_and_referenced_prototypes(self):
        from pxr import Sdf, UsdShade

        st = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
        root = UsdGeom.Xform.Define(st, '/root')
        st.SetDefaultPrim(root.GetPrim())

        # Materials
        mats = UsdGeom.Scope.Define(st, '/root/_materials')
        mat_keep = UsdShade.Material.Define(st, '/root/_materials/Gold')
        mat_drop = UsdShade.Material.Define(st, '/root/_materials/Unused')

        # Prototypes (as exported by Blender with use_instancing=True)
        UsdGeom.Scope.Define(st, '/root/prototypes')
        proto_keep = UsdGeom.Xform.Define(st, '/root/prototypes/Bangles_SRC')
        mesh_keep = UsdGeom.Mesh.Define(st, '/root/prototypes/Bangles_SRC/Torus')
        UsdShade.MaterialBindingAPI.Apply(mesh_keep.GetPrim()).Bind(mat_keep)

        proto_drop = UsdGeom.Xform.Define(st, '/root/prototypes/Unused_SRC')
        mesh_drop = UsdGeom.Mesh.Define(st, '/root/prototypes/Unused_SRC/Box')
        UsdShade.MaterialBindingAPI.Apply(mesh_drop.GetPrim()).Bind(mat_drop)

        # Instance prim with sanitized name (Blender name "Torus_INST.001" -> USD "Torus_INST_001")
        inst_keep = UsdGeom.Xform.Define(st, '/root/Torus_INST_001')
        inst_keep.GetPrim().GetReferences().AddInternalReference('/root/prototypes/Bangles_SRC')
        inst_keep.GetPrim().SetInstanceable(True)

        # Unselected instance prim
        inst_drop = UsdGeom.Xform.Define(st, '/root/Unused_INST')
        inst_drop.GetPrim().GetReferences().AddInternalReference('/root/prototypes/Unused_SRC')
        inst_drop.GetPrim().SetInstanceable(True)

        usd_graph._filter_blender_stage_in_memory(st, {'Torus_INST.001'})

        # Selected instance, its prototype mesh, and its bound material must survive and stay visible
        kept_prim = st.GetPrimAtPath('/root/Torus_INST_001')
        self.assertTrue(kept_prim.IsValid())
        self.assertTrue(kept_prim.IsInstance())
        self.assertEqual(UsdGeom.Imageable(kept_prim).ComputeVisibility(), UsdGeom.Tokens.inherited)
        self.assertTrue(st.GetPrimAtPath('/root/prototypes/Bangles_SRC/Torus').IsValid())
        self.assertTrue(st.GetPrimAtPath('/root/_materials/Gold').IsValid())

        # Unselected instance remains in stage with visibility=invisible (not deleted)
        hidden_prim = st.GetPrimAtPath('/root/Unused_INST')
        self.assertTrue(hidden_prim.IsValid())
        self.assertEqual(UsdGeom.Imageable(hidden_prim).ComputeVisibility(), UsdGeom.Tokens.invisible)
        self.assertTrue(st.GetPrimAtPath('/root/prototypes/Unused_SRC').IsValid())
        self.assertTrue(st.GetPrimAtPath('/root/_materials/Unused').IsValid())

    def test_blender_node_applies_scale_without_transform_node(self):
        cube_usd = os.path.join(self._tmp.name, 'cube_cm.usda')
        st = Usd.Stage.CreateNew(cube_usd)
        UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(st, 0.01)
        root = UsdGeom.Xform.Define(st, '/root')
        st.SetDefaultPrim(root.GetPrim())
        cube = UsdGeom.Cube.Define(st, '/root/Box')
        cube.CreateSizeAttr().Set(2.0)
        st.GetRootLayer().Save()

        base_graph = {
            'targetNodeId': 'blender1',
            'nodes': [
                {
                    'id': 'blender1',
                    'operation': 'blender',
                    'label': 'Blender',
                    'publishedDependencyId': 'dep_usd',
                    'scale': 1.0,
                }
            ],
            'edges': [],
            'dependencies': [{'id': 'dep_usd', 'path': cube_usd}],
        }
        stage_1x, _, _ = usd_graph.compile_graph(base_graph)
        bbox_1x = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(stage_1x.GetPrimAtPath('/Scene/root/Box')).ComputeAlignedRange()

        scaled_graph = {
            **base_graph,
            'nodes': [{**base_graph['nodes'][0], 'scale': 100.0}],
        }
        stage_100x, _, _ = usd_graph.compile_graph(scaled_graph)
        bbox_100x = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(stage_100x.GetPrimAtPath('/Scene/root/Box')).ComputeAlignedRange()

        # 0.01 m/unit * 2.0 = 0.02 m at scale=1.0; 100x scale restores 2.0 m
        self.assertAlmostEqual(bbox_1x.GetSize()[0], 0.02, places=4)
        self.assertAlmostEqual(bbox_100x.GetSize()[0], 2.0, places=4)

if __name__ == '__main__':
    unittest.main()
