"""Opt-in renderer regression for consecutive gizmo drags and orbit."""
import copy
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get("ORCHFLOW_TEST_GIZMO_RENDERER"), "Select a gizmo test renderer")
class TransformGizmoTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ORCHFLOW_TEST_GIZMO_RENDERER") == "HdStormRendererPlugin", "Uses the Hydra engine")
    def test_pinned_output_updates_private_transform_in_place(self):
        from usd_worker import dispatch, close_viewport, viewport
        from pxr import UsdGeom
        self.addCleanup(close_viewport)
        path = Path(__file__).resolve().parents[1] / "samples" / "cube.usda"
        original = path.read_bytes()
        move = {"id": "move", "operation": "transform", "transform": {"translation": [0, 0, 0]}}
        graph = {
            "targetNodeId": "output",
            "dependencies": [{"id": "asset", "path": str(path)}],
            "nodes": [
                {"id": "source", "operation": "source", "inputDependencyId": "asset"},
                move,
                {"id": "output", "operation": "output"},
            ],
            "edges": [
                {"id": "input", "source": "source", "target": "move"},
                {"id": "preview", "source": "move", "target": "output"},
            ],
        }
        request = {
            "operation": "render", "renderer": "HdStormRendererPlugin", "width": 160, "height": 120,
            "graph": graph, "cameraSession": "pinned", "interactive": True, "materials": "auto",
            "transformGizmoNodeId": "move",
        }
        before = dispatch(request)
        stage, engine = viewport().stage, viewport().engine
        cube = stage.GetPrimAtPath("/Scene/World/Cube")
        for x in (1, 2, 0):
            move["transform"]["translation"] = [x, 0, 0]
            frame = dispatch(request)
            if frame.get("cameraSync"):
                request.update(frame["cameraSync"])
            self.assertIs(viewport().stage, stage)
            self.assertIs(viewport().engine, engine)
            self.assertEqual(list(UsdGeom.XformCache().GetLocalToWorldTransform(cube).ExtractTranslation()), [x, 0, 0])
            if x:
                self.assertNotEqual(frame["image"], before["image"])
        self.assertEqual(path.read_bytes(), original)

    def test_translation_anchor_survives_settle_and_next_gesture(self):
        renderer = os.environ["ORCHFLOW_TEST_GIZMO_RENDERER"]
        if renderer == "OmniverseRTX":
            from ovrtx_worker import dispatch, close_viewport
        else:
            from usd_worker import dispatch, close_viewport
        self.addCleanup(close_viewport)

        path = Path(__file__).resolve().parents[1] / "samples" / "layered.usda"
        graph = {
            "targetNodeId": "move",
            "dependencies": [{"id": "asset", "path": str(path)}],
            "nodes": [
                {"id": "source", "operation": "source", "inputDependencyId": "asset"},
                {"id": "move", "operation": "transform", "transform": {"translation": [0, 0, 0]}},
            ],
            "edges": [{"id": "input", "source": "source", "target": "move"}],
        }
        camera = {"yaw": 35, "pitch": 20, "panX": 0, "panY": 0, "zoom": 1}

        def render(x, dragging=False, orbiting=False, retained=None, **overrides):
            spec = copy.deepcopy(graph)
            spec["nodes"][1]["transform"]["translation"][0] = x
            result = dispatch({
                "operation": "render", "renderer": renderer, "width": 160, "height": 120,
                "graph": spec, "cameraSession": "gizmo-test", "materials": "solid",
                "interactive": dragging or orbiting, "transformGizmoNodeId": "move",
                "cameraAnchor": retained, **camera, **overrides,
            })
            if result.get("cameraSync"):
                camera.update(result["cameraSync"])
            return result["cameraAnchor"]

        initial = render(0)

        def check_anchor(anchor, x):
            self.assertEqual(anchor["translation"], [x, 0, 0])
            for actual, origin, delta in zip(anchor["center"], initial["center"], [x, 0, 0]):
                self.assertAlmostEqual(actual, origin + delta)

        check_anchor(render(1, dragging=True), 1)
        check_anchor(render(1), 1)
        check_anchor(render(1, dragging=True), 1)
        check_anchor(render(2, dragging=True), 2)
        # Orbit may supersede a queued non-interactive release frame.
        camera["yaw"] += 10
        settled = render(2, orbiting=True)
        check_anchor(settled, 2)
        camera["yaw"] += 10
        check_anchor(render(2, orbiting=True), 2)

        if renderer == "OmniverseRTX":
            close_viewport()
            check_anchor(render(3, retained=settled), 3)

        # Repivoting to an already-translated world point must acknowledge the
        # current translation, even when the interactive drag never settled.
        render(4, dragging=True)
        point = [4.2, 0.4, 0.7]
        repivot = {"orbitPivotSeq": 1, "orbitPivotPoint": point}
        anchor = render(4, **repivot)
        self.assertEqual(anchor["center"], point)
        self.assertEqual(anchor["translation"], [4, 0, 0])
        self.assertEqual(render(4, **repivot)["center"], point)
        next_anchor = render(5)
        self.assertAlmostEqual(next_anchor["center"][0], point[0] + 1)
        self.assertEqual(next_anchor["center"][1:], point[1:])
        self.assertEqual(next_anchor["translation"], [5, 0, 0])

        # A pinned downstream preview renders the gizmo's node as an input.
        graph["nodes"].append({"id": "output", "operation": "output"})
        graph["edges"].append({"id": "preview", "source": "move", "target": "output"})
        graph["targetNodeId"] = "output"
        live = render(6, dragging=True)
        self.assertEqual(live["transformTarget"], "move")
        self.assertEqual(live["translation"], [6, 0, 0])
        self.assertAlmostEqual(live["center"][0], point[0] + 2)
        camera["yaw"] += 10
        self.assertEqual(render(6, orbiting=True)["center"], live["center"])
