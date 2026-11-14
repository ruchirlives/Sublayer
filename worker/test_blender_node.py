import os
import unittest
import usd_worker
import usd_graph
from pxr import Usd, UsdGeom

class BlenderNodeTests(unittest.TestCase):
    def setUp(self):
        self.test_usd = os.path.abspath('test_blender_node_stage.usda')
        st = Usd.Stage.CreateNew(self.test_usd)
        UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
        root = UsdGeom.Xform.Define(st, '/root')
        st.SetDefaultPrim(root.GetPrim())
        mesh = UsdGeom.Mesh.Define(st, '/root/ImportedMesh')
        st.GetRootLayer().Save()

    def tearDown(self):
        if os.path.isfile(self.test_usd):
            os.remove(self.test_usd)

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

if __name__ == '__main__':
    unittest.main()
