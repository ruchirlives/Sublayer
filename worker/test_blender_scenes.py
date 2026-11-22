"""Scene routing/cache regressions; never launches Blender."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import usd_worker  # Configure the upstream USD runtime before importing pxr.
import usd_graph
from pxr import Usd, UsdGeom


class BlenderScenes(unittest.TestCase):
    def tearDown(self):
        usd_graph.clear_graph_caches()

    def test_skeletal_snapshot_uses_saved_pose_frame_not_single_render_frame(self):
        paths = [Path(usd_graph.__file__), Path(__file__).parents[1] / 'src-tauri/src/blender_node.rs']
        for path in paths:
            source = path.read_text()
            snippet = source[source.index('export_scene = bpy.context.scene'):]
            snippet = snippet[:snippet.index('res = bpy.ops.wm.usd_export(**kwargs)')]
            for start, end, current, skeletons, expected in [
                (0, 0, 13, True, (13, 13)),
                (1, 1, 13, True, (13, 13)),
                (0, 48, 13, True, (0, 48)),
                (0, 0, 13, False, (0, 0)),
            ]:
                with self.subTest(exporter=path.name, start=start, end=end, skeletons=skeletons):
                    scene = SimpleNamespace(frame_start=start, frame_end=end, frame_current=current)
                    exec(snippet, {'bpy': SimpleNamespace(context=SimpleNamespace(scene=scene)),
                                   'kwargs': {'export_armatures': skeletons, 'export_animation': skeletons}})
                    self.assertEqual((scene.frame_start, scene.frame_end), expected)
                    self.assertEqual(scene.frame_current, current)

    def test_both_exporters_honor_scene_even_when_objects_are_shared(self):
        worker = Path(usd_graph.__file__).read_text()
        extraction = worker[worker.index("scene_name = config.get('sceneName')"):]
        extraction = extraction[:extraction.index('target_scene = bpy.context.window.scene')]
        rust = Path(__file__).parents[1].joinpath('src-tauri/src/blender_node.rs').read_text()
        export = rust[rust.index("scene_name = config.get('sceneName')"):]
        export = export[:export.index('def _enable_collections')]
        for script in (extraction, export):
            with self.subTest(exporter=script[:60]):
                cycles = SimpleNamespace(name='Cycles', objects=[SimpleNamespace(name='Body')])
                octane = SimpleNamespace(name='Octane', objects=cycles.objects)

                class Scenes(list):
                    def get(self, name):
                        return next((s for s in self if s.name == name), None)

                window = SimpleNamespace(scene=cycles)
                bpy = SimpleNamespace(data=SimpleNamespace(scenes=Scenes([cycles, octane])),
                                      context=SimpleNamespace(window=window, scene=cycles))
                env = {'bpy': bpy, 'config': {'sceneName': 'Octane'}, 'selected': ['Body']}
                exec(script, env)
                self.assertIs(window.scene, octane)
                env['config']['sceneName'] = 'Missing'
                with self.assertRaisesRegex(RuntimeError, 'no longer exists'):
                    exec(script, env)

    def test_disk_keys_separate_scenes_and_skeleton_modes(self):
        keys = {usd_graph._blender_disk_cache_paths('shared.blend', 1, 2, skeletons, scene)[1]
                for scene in (None, 'Cycles', 'Octane') for skeletons in (False, True)}
        self.assertEqual(len(keys), 6)

    def test_full_cache_never_reuses_another_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            blend = Path(folder, 'test.blend')
            blend.touch()
            norm = os.path.normcase(os.path.abspath(blend))
            mtime = blend.stat().st_mtime
            for scene in ('Cycles', 'Octane'):
                stage = Usd.Stage.CreateInMemory()
                UsdGeom.Xform.Define(stage, '/root')
                entry = {'raw_layer': stage.GetRootLayer(), 'mat_bundles': {}, 'has_materials': False,
                         'scene': scene, 'scene_objects': {'Body'}, 'default_visible': ['Body']}
                usd_graph._BLENDER_FULL_LAYER_CACHE[(norm, mtime, scene, True, scene)] = entry
            with patch.object(usd_graph, 'find_blender_executable', side_effect=AssertionError('Unexpected launch')):
                for scene in ('Octane', 'Cycles', 'Octane'):
                    result = usd_graph._get_or_extract_full_blender_layer(
                        str(blend), mtime, selected_objects=['Body'], export_skeletons=True, scene_name=scene)
                    self.assertEqual(result['scene'], scene)

    def test_filtered_stage_cache_separates_identical_selections(self):
        stages = {}
        for scene in ('Cycles', 'Octane'):
            stage = Usd.Stage.CreateInMemory()
            root = UsdGeom.Xform.Define(stage, '/root')
            stage.SetDefaultPrim(root.GetPrim())
            UsdGeom.Mesh.Define(stage, '/root/Body')
            root.GetPrim().SetCustomDataByKey('testScene', scene)
            stages[scene] = stage

        def extract(*args, scene_name=None, **kwargs):
            return {'layer': stages[scene_name].GetRootLayer(), 'default_visible': ['Body']}

        with tempfile.TemporaryDirectory() as folder:
            blend = Path(folder, 'test.blend')
            blend.touch()
            with patch.object(usd_graph, '_get_or_extract_full_blender_layer', side_effect=extract) as mocked:
                for scene in ('Octane', 'Cycles', 'Octane'):
                    stage = usd_graph.get_or_extract_blender_memory_stage(str(blend), ['Body'], scene_name=scene)
                    self.assertEqual(stage.GetDefaultPrim().GetCustomDataByKey('testScene'), scene)
                self.assertEqual(mocked.call_count, 2)

    def test_graph_routes_same_file_to_distinct_scenes(self):
        with tempfile.TemporaryDirectory() as folder:
            blend = Path(folder, 'test.blend')
            blend.touch()
            graph = {'targetNodeId': 'out', 'dependencies': [{'id': 'blend', 'path': str(blend)}],
                     'nodes': [{'id': scene, 'operation': 'blender', 'nativeProjectDependencyId': 'blend',
                                'blenderScene': scene, 'blenderSelections': ['Body']}
                               for scene in ('Cycles', 'Octane')] + [{'id': 'out', 'operation': 'output'}],
                     'edges': [{'id': scene, 'source': scene, 'target': 'out'} for scene in ('Cycles', 'Octane')]}

            def extract(*args, scene_name=None, **kwargs):
                stage = Usd.Stage.CreateInMemory()
                root = UsdGeom.Xform.Define(stage, '/root')
                stage.SetDefaultPrim(root.GetPrim())
                UsdGeom.Mesh.Define(stage, '/root/' + scene_name)
                return stage

            with patch.object(usd_graph, 'get_or_extract_blender_memory_stage', side_effect=extract) as mocked:
                stage, alive, _ = usd_graph.compile_graph(graph)
                self.assertEqual({c.kwargs['scene_name'] for c in mocked.call_args_list}, {'Cycles', 'Octane'})
                self.assertEqual({p.GetName() for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)}, {'Cycles', 'Octane'})

    def test_blender_extract_script_forces_exit_after_success(self):
        worker_src = Path(usd_graph.__file__).read_text(encoding='utf-8')
        marker = "print('\\nORCHFLOW_BLENDER_EXPORT:SUCCESS', flush=True)"
        idx = worker_src.index(marker)
        tail = worker_src[idx:idx + 200]
        self.assertIn('os._exit(0)', tail)

    def test_blender_extract_process_completes_even_if_child_hangs_after_success(self):
        script = (
            "import sys, time\n"
            "_ = sys.stdin.readline()\n"
            "print('ORCHFLOW_BLENDER_META:{\"scene\":\"Scene\"}', flush=True)\n"
            "print('ORCHFLOW_BLENDER_EXPORT:SUCCESS', flush=True)\n"
            "time.sleep(30)\n"
        )
        started = time.monotonic()
        rc, stdout, _ = usd_graph._run_blender_extract_process(
            [sys.executable, '-u', '-c', script],
            '{}',
            timeout_seconds=10.0,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(rc, 0)
        self.assertIn('ORCHFLOW_BLENDER_EXPORT:SUCCESS', stdout)
        self.assertLess(elapsed, 6.0)

    def test_blender_extract_process_times_out_cleanly_without_success(self):
        script = "import sys, time\n_ = sys.stdin.readline()\ntime.sleep(30)\n"
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            usd_graph._run_blender_extract_process(
                [sys.executable, '-u', '-c', script],
                '{}',
                timeout_seconds=0.5,
            )
        self.assertLess(time.monotonic() - started, 5.0)


if __name__ == '__main__':
    unittest.main()
