"""Upstream OpenUSD inspection and persistent offscreen Hydra rendering worker.

The process owns its Qt GL context and USD stage. It never writes source assets.
In --serve mode protocol records are prefixed, keeping native stdout diagnostics
separate from responses. Writes are restricted to explicit handoff/publication
operations whose context and destinations are validated by the Rust backend.
"""
import base64
import atexit
import copy
import hashlib
import json
import math
import os
import sys
import time
import functools
if 'compile_graph' not in globals():
    from usd_graph import compile_graph, coordinate_preflight, export_handoff, materialx_document, materialx_rule_matches, asset_override_inputs, reuse_handoff, inspect_stage_materials, inspect_stage_lights, inspect_stage_curves, inspect_stage_skeletons, export_character_body, convert_source_to_usd, clear_graph_caches, query_scene_children, compute_material_users, resolve_prim_material, _open_source_stage, _build_transform_matrix
from usd_graph import editable_hair_node_id, graph_after_groom_commit, groom_frame_view, open_hair_preview_stage

if 'review_publication' not in globals():
    from usd_publish import review_publication, publish_reviewed, reconcile_upstream, validate_manual_baseline, create_live_adjustment

_PROTOCOL = 'ORCHFLOW:'
_viewport = None
_dll_dirs = []
_native_runtime_libraries = []


def report_status(phase, details=None):
    try:
        payload = {"phase": phase}
        if details:
            payload["details"] = details
        sys.stdout.write("ORCHFLOW_STATUS:" + json.dumps(payload) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _configure_aurora_runtime():
    """Register an installed Autodesk Aurora Hydra delegate if present."""
    candidates = [os.environ.get('ORCHFLOW_AURORA_ROOT')]
    worker_dir = os.environ.get('ORCHFLOW_WORKER_DIR')
    if worker_dir:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(worker_dir)), 'runtimes', 'aurora'))
    local_app = os.environ.get('LOCALAPPDATA')
    if local_app:
        candidates.append(os.path.join(local_app, 'Sublayer', 'runtimes', 'aurora'))
    candidates.append('I:/AI/OrchFlow-renderers/aurora')
    aurora_root = next((os.path.abspath(c) for c in candidates if c and os.path.isdir(c)), None)
    if not aurora_root:
        return
    manifest_path = os.path.join(aurora_root, 'runtime.json')
    binary_rel = 'bin'
    plugin_rel = os.path.join('bin', 'usd', 'hdAurora', 'resources', 'plugInfo.json')
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8-sig') as handle:
                manifest = json.load(handle)
            binary_rel = manifest.get('binaryRoot') or binary_rel
            plugin_rel = manifest.get('pluginInfo') or plugin_rel
        except Exception as exc:
            sys.stderr.write(f'Aurora manifest unreadable ({manifest_path}): {exc}\n')
            return
    binary_root = os.path.abspath(os.path.join(aurora_root, binary_rel))
    plugin_info = os.path.abspath(os.path.join(aurora_root, plugin_rel))
    if not os.path.isdir(binary_root) or not os.path.isfile(plugin_info):
        sys.stderr.write(f'Aurora runtime incomplete at {aurora_root}; skipping Hydra plugin registration.\n')
        return
    dll_dirs = [binary_root]
    usd_plugin_dir = os.path.join(binary_root, 'usd')
    if os.path.isdir(usd_plugin_dir):
        dll_dirs.append(usd_plugin_dir)
    for directory in dll_dirs:
        if hasattr(os, 'add_dll_directory'):
            _dll_dirs.append(os.add_dll_directory(directory))
    current_path = os.environ.get('PATH', '')
    os.environ['PATH'] = os.pathsep.join(dll_dirs + ([current_path] if current_path else []))
    plugin_dir = os.path.dirname(plugin_info)
    existing_plugins = [p for p in os.environ.get('PXR_PLUGINPATH_NAME', '').split(os.pathsep) if p]
    if plugin_dir not in existing_plugins:
        os.environ['PXR_PLUGINPATH_NAME'] = os.pathsep.join(existing_plugins + [plugin_dir])


def configure_runtime():
    # USD stack tracing and OIIO must share the OS debugging libraries, not
    # incompatible copies found on PATH (for example Oculus runtime dbghelp).
    if os.name=='nt':
        import ctypes
        system=os.path.join(os.environ.get('SystemRoot','C:/Windows'),'System32')
        for name in ('dbghelp.dll','dbgeng.dll'):
            _native_runtime_libraries.append(ctypes.WinDLL(os.path.join(system,name)))
    root = os.environ.get('ORCHFLOW_USD_ROOT')
    if not root:
        return
    root = os.path.abspath(root)
    sys.path.insert(0, os.path.join(root, 'lib', 'python'))
    for folder in ('bin', 'lib'):
        directory = os.path.join(root, folder)
        if os.path.isdir(directory) and hasattr(os, 'add_dll_directory'):
            _dll_dirs.append(os.add_dll_directory(directory))
    os.environ['PATH'] = os.pathsep.join([os.path.join(root, 'bin'), os.path.join(root, 'lib'), os.environ.get('PATH', '')])
    os.environ.setdefault('PXR_MTLX_STDLIB_SEARCH_PATHS', os.path.join(root, 'libraries'))
    _configure_aurora_runtime()



configure_runtime()

if 'compute_visible_geometry_bounds' not in globals():
    from viewport_bounds import compute_visible_geometry_bounds, compute_selection_bounds, isolate_prim_in_stage, apply_viewport_visibility, raycast_prim_hit, transform_gizmo_translation


@functools.lru_cache(maxsize=1)
def material_capabilities():
    try:
        from pxr import Sdr
        registry=Sdr.Registry()
        names=[str(name) for name in registry.GetShaderNodeIdentifiers()]
        return {'previewSurfaceDefinition':bool(registry.GetShaderNodeByIdentifier('UsdPreviewSurface')),
                'materialXDefinitions':sum(name.startswith('ND_') for name in names),
                'openPBRDefinitions':[name for name in names if 'open_pbr' in name.lower() or 'openpbr' in name.lower()],
                'note':'Installed shader definitions do not guarantee renderer feature parity or native shader conversion.'}
    except ImportError as error:
        return {'previewSurfaceDefinition':False,'materialXDefinitions':0,'openPBRDefinitions':[], 'note':str(error)}


def inspect_materials(stage):
    from pxr import Usd, UsdGeom, UsdShade
    materials,bindings,shaders=[],[],set()
    inspected=0
    truncated=False
    for prim in iter_scene_and_prototype_prims(stage):
        inspected+=1
        if inspected>50000:
            truncated=True;break
        if prim.IsA(UsdShade.Shader):
            identifier=UsdShade.Shader(prim).GetIdAttr().Get()
            if identifier:shaders.add(str(identifier))
        if prim.IsA(UsdShade.Material) and len(materials)<500:
            outputs=[]
            for output in UsdShade.Material(prim).GetSurfaceOutputs():
                name=str(output.GetFullName())
                context=':'.join(name.split(':')[1:-1]) or 'universal'
                outputs.append({'context':context,'connections':[str(path) for path in output.GetAttr().GetConnections()]})
            materials.append({'path':str(prim.GetPath()),'outputs':outputs})
        if (prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.Subset)) and len(bindings)<500:
            bound={}
            for purpose in ('allPurpose','preview','full'):
                material,_=UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(purpose)
                if material:bound[purpose]=str(material.GetPath())
            if bound:bindings.append({'prim':str(prim.GetPath()),'materials':bound})
    return {'materials':materials,'bindings':bindings,'shaderIdentifiers':sorted(shaders),
            'missingTextures':missing_shader_assets(stage,stage.GetStartTimeCode()),
            'limits':{'materialRows':500,'bindingRows':500,'visitedPrims':inspected,'truncated':truncated or len(materials)>=500 or len(bindings)>=500},
            'support':material_capabilities()}


def finite_number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite and between {low} and {high}')
    return float(value)


if 'missing_shader_assets' not in globals():
    from material_preview import iter_scene_and_prototype_prims, missing_shader_assets, invalid_shader_inputs, selective_material_preview


_outline_cache=None

def scene_children(request):
    from pxr import Usd
    global _outline_cache, _viewport
    graph=request.get('graph');graph=json.loads(graph) if isinstance(graph,str) else graph
    key=json.dumps(graph,sort_keys=True) if graph else request.get('path')
    if not key:raise ValueError('Choose a stage to inspect')
    reload_flag=bool(request.get('reload'))
    if not reload_flag and _viewport is not None and _viewport.stage is not None:
        vp_key=json.dumps(graph,sort_keys=True) if graph else None
        if (_viewport.path==key) or (vp_key and getattr(_viewport,'outline_key',None)==vp_key):
            return query_scene_children(_viewport.stage,request)
    if _outline_cache is None or _outline_cache[0]!=key or reload_flag:
        if graph:
            import usd_graph as _ug
            _prev_skip=getattr(_ug,'_SKIP_SIMULATION',False)
            _ug._SKIP_SIMULATION=True
            try:
                stage,alive,_=compile_graph(graph,reload_flag)
            finally:
                _ug._SKIP_SIMULATION=_prev_skip
        else:
            stage=Usd.Stage.Open(key);alive=[]
            if stage and reload_flag:stage.Reload()
        if not stage:raise ValueError('Unable to open scene hierarchy')
        _outline_cache=(key,stage,alive)
    return query_scene_children(_outline_cache[1],request)


class ViewportSelection:
    """Resolve material clicks on the original stage, independent of preview shading.

    Binding relationships are uniform. Cache until USD changes, not camera motion.
    No stage opinions or instanceability are modified by selection.
    """
    def __init__(self,stage):
        from pxr import Tf,Usd
        self.stage=stage
        self.material_users=None
        self.results={}
        self.notice=Tf.Notice.Register(Usd.Notice.ObjectsChanged,self._changed,stage)

    def _changed(self,notice,sender):
        self.material_users=None
        self.results.clear()

    def _bindings(self):
        return compute_material_users(self.stage)

    def resolve(self,paths):
        from pxr import Sdf,UsdShade
        if not isinstance(paths,list) or len(paths)>1000 or any(not isinstance(path,str) for path in paths):raise ValueError('Invalid selected prim paths')
        key=tuple(paths)
        if key in self.results:return self.results[key]
        selected=set();materials=set();meshes=set()
        for text in paths:
            path=Sdf.Path(text)
            if not path.IsAbsolutePath() or not (path.IsPrimPath() or path==Sdf.Path.absoluteRootPath):raise ValueError('Selection requires absolute prim paths')
            prim=self.stage.GetPrimAtPath(path)
            if not prim:continue
            if prim.IsA(UsdShade.Material):materials.add(str(path))
            else:selected.add(str(path))
        if materials:
            if self.material_users is None:self.material_users=self._bindings()
            for material in materials:meshes.update(self.material_users.get(material,()))
        selected.update(meshes)
        result=(sorted(selected),{'materialCount':len(materials),'meshCount':len(meshes)})
        if len(self.results)>=16:self.results.clear()
        self.results[key]=result
        return result

    def close(self):
        self.notice.Revoke()
        self.stage=None
        self.material_users=None
        self.results.clear()


def _transform_only_node(prev_graph, next_graph, live_transforms):
    if not isinstance(prev_graph, dict) or not isinstance(next_graph, dict):
        return None
    target_id = next_graph.get('targetNodeId')
    if not target_id or prev_graph.get('targetNodeId') != target_id:
        return None
    if any(
        prev_graph.get(k) != next_graph.get(k)
        for k in set(prev_graph.keys()) | set(next_graph.keys())
        if k != 'nodes'
    ):
        return None
    prev_nodes = prev_graph.get('nodes')
    next_nodes = next_graph.get('nodes')
    if not isinstance(prev_nodes, list) or not isinstance(next_nodes, list) or len(prev_nodes) != len(next_nodes):
        return None
    changed_node = None
    for pn, nn in zip(prev_nodes, next_nodes):
        if pn == nn:
            continue
        if (
            not isinstance(pn, dict)
            or not isinstance(nn, dict)
            or pn.get('id') != nn.get('id')
            or nn.get('id') not in live_transforms
            or nn.get('operation') != 'transform'
        ):
            return None
        if {k: v for k, v in pn.items() if k != 'transform'} != {k: v for k, v in nn.items() if k != 'transform'}:
            return None
        changed_node = nn
    return changed_node


def _define_bbox_mesh(stage, path, box_min, box_max):
    from pxr import UsdGeom
    x0, y0, z0 = float(box_min[0]), float(box_min[1]), float(box_min[2])
    x1, y1, z1 = float(box_max[0]), float(box_max[1]), float(box_max[2])
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ])
    mesh.CreateFaceVertexCountsAttr([4, 4, 4, 4, 4, 4])
    mesh.CreateFaceVertexIndicesAttr([
        0, 3, 2, 1,
        4, 5, 6, 7,
        0, 1, 5, 4,
        2, 3, 7, 6,
        0, 4, 7, 3,
        1, 2, 6, 5,
    ])
    return mesh


def _sync_bounds_proxy(stage, session, frame, enable):
    from pxr import Sdf, Usd, UsdGeom
    proxy_path = Sdf.Path('/Render/BoundsProxy')
    scene_prim = stage.GetPrimAtPath('/Scene')
    has_proxy = bool(session.GetPrimAtPath(proxy_path))
    if not enable:
        if has_proxy:
            with Usd.EditContext(stage, session):
                stage.RemovePrim(proxy_path)
        return

    if has_proxy:
        return

    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode(frame),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )
    with Usd.EditContext(stage, session):
        UsdGeom.Scope.Define(stage, '/Render')
        UsdGeom.Scope.Define(stage, proxy_path)
        targets = list(scene_prim.GetChildren()) if (scene_prim and scene_prim.IsValid()) else list(stage.GetPseudoRoot().GetChildren())
        box_idx = 0
        for child in targets:
            if child.GetPath() == Sdf.Path('/Render'):
                continue
            if child.IsA(UsdGeom.PointInstancer):
                src_inst = UsdGeom.PointInstancer(child)
                proto_targets = src_inst.GetPrototypesRel().GetTargets()
                if not proto_targets:
                    continue
                dst_inst_path = proxy_path.AppendChild(f'Inst_{box_idx}')
                box_idx += 1
                dst_inst = UsdGeom.PointInstancer.Define(stage, dst_inst_path)
                proto_scope = dst_inst_path.AppendChild('Prototypes')
                UsdGeom.Scope.Define(stage, proto_scope)
                dst_protos = []
                for p_idx, p_target in enumerate(proto_targets):
                    p_prim = stage.GetPrimAtPath(p_target)
                    b_min, b_max = (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)
                    if p_prim and p_prim.IsValid():
                        bound = bbox_cache.ComputeLocalBound(p_prim).ComputeAlignedRange()
                        if not bound.IsEmpty():
                            b_min, b_max = bound.GetMin(), bound.GetMax()
                    bp_path = proto_scope.AppendChild(f'Box_{p_idx}')
                    _define_bbox_mesh(stage, bp_path, b_min, b_max)
                    dst_protos.append(bp_path)
                dst_inst.CreatePrototypesRel().SetTargets(dst_protos)
                for attr_name in ('protoIndices', 'positions', 'orientations', 'scales'):
                    val = child.GetAttribute(attr_name).Get(Usd.TimeCode(frame))
                    if val is not None:
                        dst_inst.GetPrim().GetAttribute(attr_name).Set(val)
            else:
                bound = bbox_cache.ComputeWorldBound(child).ComputeAlignedRange()
                if bound.IsEmpty():
                    continue
                _define_bbox_mesh(stage, proxy_path.AppendChild(f'Box_{box_idx}'), bound.GetMin(), bound.GetMax())
                box_idx += 1


def _normalize_storm_lights(stage, session, frame):
    import math
    from pxr import Sdf, Usd, UsdLux
    lights = []
    max_eff = 0.0
    for prim in iter_scene_and_prototype_prims(stage):
        if prim.GetPath().HasPrefix(Sdf.Path('/Render/DefaultLights')):
            continue
        is_light = 'Light' in prim.GetTypeName() or (hasattr(UsdLux, 'LightAPI') and prim.HasAPI(UsdLux.LightAPI))
        if not is_light:
            continue
        int_attr = prim.GetAttribute('inputs:intensity') or prim.GetAttribute('intensity')
        if not int_attr or not int_attr.IsValid():
            continue
        val = int_attr.Get(Usd.TimeCode(frame))
        if val is None or not isinstance(val, (int, float)) or val <= 0.0:
            continue
        exp_attr = prim.GetAttribute('inputs:exposure') or prim.GetAttribute('exposure')
        exp = float(exp_attr.Get(Usd.TimeCode(frame)) or 0.0) if (exp_attr and exp_attr.IsValid()) else 0.0
        eff = float(val) * (2.0 ** max(-20.0, min(20.0, exp)))
        lights.append((prim, int_attr.GetName(), float(val)))
        if eff > max_eff:
            max_eff = eff
    if max_eff <= 10.0 or not lights:
        return
    # Scale physical/RTX-range intensities (e.g. 1000-3000) into Storm's un-tonemapped [0..2] range.
    scale = 2.0 / max_eff
    with Usd.EditContext(stage, session):
        for prim, attr_name, orig_val in lights:
            if not prim.IsInPrototype():
                prim.GetAttribute(attr_name).Set(orig_val * scale)


class Viewport:
    def __init__(self):
        from PySide6 import QtCore, QtGui, QtOpenGL
        from OpenGL import GL
        from pxr import UsdImagingGL, Glf
        self.QtCore, self.QtGui, self.QtOpenGL, self.GL = QtCore, QtGui, QtOpenGL, GL
        self.app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication(['OrchFlow USD Worker'])
        fmt = QtGui.QSurfaceFormat()
        fmt.setVersion(4, 5)
        fmt.setProfile(QtGui.QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
        fmt.setDepthBufferSize(24)
        self.surface = QtGui.QOffscreenSurface()
        self.surface.setFormat(fmt)
        self.surface.create()
        self.context = QtGui.QOpenGLContext()
        self.context.setFormat(fmt)
        if not self.context.create() or not self.context.makeCurrent(self.surface):
            raise RuntimeError('Unable to create the OpenGL context required by Hydra')
        # Modern OpenUSD initializes GL dispatch from the current context.
        self.gpu = GL.glGetString(GL.GL_RENDERER).decode('utf-8', errors='replace')
        self.gl_version = GL.glGetString(GL.GL_VERSION).decode('utf-8', errors='replace')
        self.plugins = [{'id': str(p), 'name': UsdImagingGL.Engine.GetRendererDisplayName(p),
                         'support': 'discovered; validated when selected'}
                        for p in UsdImagingGL.Engine.GetRendererPlugins()]
        self.engine = None
        self.renderer = None
        self.stage = None
        self.path = None
        self.fbo = None
        self.size = None
        self.texture_diagnostics = None
        self.texture_frame = None
        self.bounds = None
        self.bounds_frame = None
        self.graph_stages = []
        self.graph_stats = None
        self.auto_preview = None
        self.render_stage_id = None
        self.selection_resolver = None
        self.has_scene_lights = None
        self._normalized_lights_key = None
        self.renderer_capabilities = None
        self.stage_cache = {}
        self.current_graph = None
        self.pending_groom_graph = None
        self.pending_groom_frame = None
        self.prepared_hair_node_id = None
        self.camera_anchor_translation = None
        self._last_orbit_pivot_seq = 0

    def render(self, request):
        from pxr import Usd, UsdGeom, UsdImagingGL, Gf, Glf, CameraUtil, UsdLux, Sdf
        started = time.perf_counter()
        width = int(finite_number(request.get('width', 960), 'width', 64, 2048))
        height = int(finite_number(request.get('height', 640), 'height', 64, 2048))
        yaw = math.radians(finite_number(request.get('yaw', 35), 'yaw', -36000, 36000))
        pitch = math.radians(finite_number(request.get('pitch', 20), 'pitch', -89, 89))
        zoom = finite_number(request.get('zoom', 1), 'zoom', 0.001, 100)
        pan_x = finite_number(request.get('panX', 0), 'panX', -100, 100)
        pan_y = finite_number(request.get('panY', 0), 'panY', -100, 100)
        materials = request.get('materials', 'auto')
        if materials not in ('auto', 'scene', 'solid'):
            raise ValueError('Material preview must be auto, scene, or solid')
        draw_mode = request.get('drawMode', 'shaded')
        if draw_mode not in ('shaded', 'solid', 'wireframe', 'points', 'bounds'):
            draw_mode = 'shaded'
        if draw_mode == 'solid':
            materials = 'solid'
        interactive = request.get('interactive', False)
        if not isinstance(interactive, bool):
            raise ValueError('interactive must be a boolean')
        path = request.get('path')
        graph = request.get('graph')
        graph_key=request.get('graphKey')
        if graph_key:
            if not isinstance(graph_key,str) or len(graph_key)>100:raise ValueError('Invalid render graph handle')
            if not hasattr(self,'render_graphs'):self.render_graphs={}
            if graph:
                if isinstance(graph,str):graph=json.loads(graph)
                if len(self.render_graphs)>=8 and graph_key not in self.render_graphs:self.render_graphs.pop(next(iter(self.render_graphs)))
                self.render_graphs[graph_key]=graph
            else:
                graph=self.render_graphs.get(graph_key)
                if graph is None:raise ValueError('Unknown render graph handle; register graph again')
            path='graph:'+graph_key
        elif graph:
            if isinstance(graph, str):
                graph = json.loads(graph)
            path = 'graph:' + hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
        if not isinstance(path, str) or not path.strip():
            raise ValueError('A USD root layer path is required')
        renderer = request.get('renderer')
        if renderer not in [p['id'] for p in self.plugins]:
            raise ValueError('Requested Hydra delegate is not installed; no fallback was used')
        if not self.context.makeCurrent(self.surface):
            raise RuntimeError('Lost OpenGL context')
        engine_params=UsdImagingGL.Engine.Parameters()
        engine_params.rendererPluginId=renderer
        if self.renderer != renderer:
            self.engine = None
            self.engine = UsdImagingGL.Engine(engine_params)
            if not self.engine.SetRendererPlugin(renderer):
                self.engine = None
                self.renderer = None
                raise RuntimeError(f'Hydra delegate {renderer} cannot run in this GPU context')
            self.renderer = renderer
            self.renderer_capabilities = {
                'aovs': [str(aov) for aov in self.engine.GetRendererAovs()],
                'pause': self.engine.IsPauseRendererSupported(),
                'stop': self.engine.IsStopRendererSupported(),
                'featureParity': 'not inferred; renderer-specific schema/material support requires verification',
            }
            self.path = None
        if request.get('reload'):
            self.stage_cache.clear()
        editable_hair_id = editable_hair_node_id(graph, request.get('activeHairNodeId'))
        needs_hair_session = editable_hair_id is not None and editable_hair_id != self.prepared_hair_node_id
        stage_changed = self.path != path or bool(request.get('reload')) or needs_hair_session
        groom_committed = (
            stage_changed and self.path is not None and not needs_hair_session and not request.get('reload')
            and self.pending_groom_graph == (graph, request.get('cameraSession'), request.get('frame'))
        )
        groom_frame = self.pending_groom_frame
        if stage_changed:
            self.pending_groom_graph = None
            self.pending_groom_frame = None
        transform_only_node = None
        if stage_changed and not groom_committed and not needs_hair_session and graph and self.stage is not None and self.current_graph is not None and not request.get('reload') and self.renderer == renderer:
            transform_only_node = _transform_only_node(self.current_graph, graph, self.live_transforms)
        if groom_committed:
            self.path = path
            self.current_graph = graph
            self.outline_key = json.dumps(graph, sort_keys=True)
            if (groom_frame is not None and groom_frame[0] == groom_frame_view(request)
                    and not request.get('pick') and not request.get('hairBrush')):
                return {
                    **groom_frame[1], 'updateKind': 'groom-commit', 'interactive': interactive,
                    'renderMs': 0.0, 'captureMs': 0.0,
                    'totalMs': round((time.perf_counter() - started) * 1000, 2),
                }
        elif transform_only_node is not None:
            matrix = _build_transform_matrix(transform_only_node.get('transform'))
            # The stage is mutable and can no longer serve its previous cache key.
            self.stage_cache.pop(self.path, None)
            for attr in self.live_transforms[transform_only_node['id']]:
                attr.Set(matrix)
            if self.auto_preview is not None and self.auto_preview[0] != self.stage:
                self.auto_preview = None
            self.path = path
            self.current_graph = copy.deepcopy(graph)
            self.outline_key = json.dumps(graph, sort_keys=True)
        elif stage_changed:
            # New engine prevents stale prims from a previously selected stage.
            if self.path is not None:
                self.engine = None
                self.engine = UsdImagingGL.Engine(engine_params)
                if not self.engine.SetRendererPlugin(renderer):
                    raise RuntimeError('Unable to reinitialize selected delegate')
            cached = None if request.get('reload') or editable_hair_id is not None else self.stage_cache.get(path)
            if cached is not None:
                stage, self.graph_stages, self.graph_stats, self.outline_key, self.has_scene_lights, self.live_transforms = cached
            else:
                self.live_transforms = {}
                private_hair_preview = False
                if graph:
                    import usd_graph as _ug
                    _prev_manual = getattr(_ug, '_INTERACTIVE_MANUAL_SIM', False)
                    _ug._INTERACTIVE_MANUAL_SIM = True
                    try:
                        stage, self.graph_stages, self.graph_stats = compile_graph(
                            graph, request.get('reload', False), live_transforms=self.live_transforms,
                            interactive_hair=editable_hair_id is not None,
                        )
                    finally:
                        _ug._INTERACTIVE_MANUAL_SIM = _prev_manual
                    preview = open_hair_preview_stage(stage, graph)
                    private_hair_preview = preview != stage
                    if private_hair_preview:
                        self.graph_stages.append(stage)
                        stage = preview
                    self.outline_key = json.dumps(graph, sort_keys=True)
                else:
                    stage = Usd.Stage.Open(path)
                    self.graph_stages, self.graph_stats = [], None
                    self.outline_key = None
                if stage is None:
                    raise RuntimeError('Unable to open USD stage')
                if request.get('reload') and not graph:
                    stage.Reload()
                self.has_scene_lights = None
                if editable_hair_id is None:
                    if len(self.stage_cache) >= 4:
                        self.stage_cache.pop(next(iter(self.stage_cache)))
                    self.stage_cache[path] = (stage, self.graph_stages, self.graph_stats, self.outline_key, None, self.live_transforms)
            self.stage, self.path = stage, path
            self.prepared_hair_node_id = editable_hair_id
            self.current_graph = copy.deepcopy(graph) if graph else None
            if self.selection_resolver is not None:self.selection_resolver.close()
            self.selection_resolver=ViewportSelection(stage)
            self.texture_diagnostics = None
            self.auto_preview = None
            self.bounds = None
            self._normalized_lights_key = None
        stage = self.stage
        frame = finite_number(request.get('frame', stage.GetStartTimeCode()), 'frame', -1e12, 1e12)
        cloth_sim_step = request.get('clothSimStep')
        if isinstance(cloth_sim_step, dict) and cloth_sim_step.get('active') and stage is not None:
            active_graph = graph or self.current_graph
            if isinstance(active_graph, dict):
                import cache_edit as _ce
                sim_frame = int(round(float(frame)))
                origin_frame = cloth_sim_step.get('originFrame')
                reset_sim = bool(cloth_sim_step.get('reset', False))
                for n in active_graph.get('nodes') or []:
                    if n.get('operation') == 'cache_edit':
                        ce_cfg = n.get('cacheEdit')
                        if isinstance(ce_cfg, dict):
                            _ce.step_cloth_simulation(
                                stage,
                                ce_cfg,
                                sim_frame,
                                origin_frame=origin_frame,
                                reset=reset_sim,
                                session_id=cloth_sim_step.get('session'),
                            )
        if materials == 'solid':
            missing_assets = []
            self.shader_issues = []
            self.auto_preview = None
        else:
            if self.texture_diagnostics is None or self.texture_frame != frame:
                self.texture_diagnostics = missing_shader_assets(stage, frame)
                self.texture_frame = frame
                self.shader_issues = invalid_shader_inputs(stage,frame)
                self.auto_preview = None
            missing_assets = self.texture_diagnostics
        fallback_materials=[]
        if materials=='scene' and self.shader_issues:
            raise ValueError('Cannot render source materials: '+self.shader_issues[0]['reason']+' at '+self.shader_issues[0]['path']+'. Use Auto or Solid preview, or explicitly repair the original shader UV binding.')
        if materials=='auto' and (missing_assets or self.shader_issues):
            if self.auto_preview is None:self.auto_preview=selective_material_preview(stage,missing_assets,frame,self.shader_issues)
            stage,preview_alive,fallback_materials=self.auto_preview
        scene_materials = materials != 'solid'
        material_mode='mixed' if fallback_materials else 'scene' if scene_materials else 'solid'
        if self.render_stage_id != stage.GetRootLayer().identifier:
            self.engine=None;self.engine=UsdImagingGL.Engine(engine_params)
            if not self.engine.SetRendererPlugin(renderer):raise RuntimeError('Unable to initialize selected delegate for material preview')
            self.render_stage_id=stage.GetRootLayer().identifier
        isolated_prim = request.get('isolatedPrim')
        hidden_prims = sorted(set(request.get('hiddenPrims') or []))
        if (isolated_prim != getattr(self, 'active_isolated_prim', None)
                or hidden_prims != getattr(self, 'active_hidden_prims', [])
                or self.path != path or request.get('reload')):
            apply_viewport_visibility(stage, isolated_prim, hidden_prims)
            self.active_isolated_prim = isolated_prim
            self.active_hidden_prims = hidden_prims
        # Camera framing is shared across node previews in canonical Y-up metres.
        # Only the first view or an explicit Frame all request fits the bounds.
        view_conversion=Gf.Matrix4d(1)
        comparison=request.get('comparison')
        if isinstance(comparison,str):comparison=json.loads(comparison)
        conversion=Gf.Matrix4d(1)
        if comparison:
            matrix=comparison.get('inverse')
            if not isinstance(matrix,list) or len(matrix)!=4 or any(not isinstance(row,list) or len(row)!=4 for row in matrix):raise ValueError('Invalid comparison conversion')
            conversion=Gf.Matrix4d(*[finite_number(value,'comparison matrix',-1e100,1e100) for row in matrix for value in row])
            if abs(conversion.GetDeterminant())<1e-100:raise ValueError('Singular comparison conversion')
            if comparison.get('handoff'):view_conversion=conversion
        else:
            rotation=Gf.Matrix4d(1)
            if UsdGeom.GetStageUpAxis(stage)==UsdGeom.Tokens.z:
                rotation.SetRotate(Gf.Rotation(Gf.Vec3d(1,0,0),-90))
            # Compiled graphs are already Y-up/metres. Only raw external
            # stages need a display conversion; never rescale graph geometry.
            if not graph:view_conversion=Gf.Matrix4d().SetScale(UsdGeom.GetStageMetersPerUnit(stage))*rotation
        fit_key=(request.get('cameraSession'),request.get('frameFit',0))
        target_prim = request.get('frameTarget')
        transform_target, cur_trans = transform_gizmo_translation(graph, request.get('transformGizmoNodeId'))
        if not request.get('cameraSession') or getattr(self,'camera_fit_key',None)!=fit_key:
            reference=stage
            bounds_conversion=view_conversion
            if comparison:
                reference_path=comparison.get('path')
                if not isinstance(reference_path,str) or not os.path.isabs(reference_path):raise ValueError('Invalid comparison handoff path')
                reference=Usd.Stage.Open(reference_path)
                if not reference:raise ValueError('Comparison handoff could not be opened')
                bounds_conversion=conversion
            if target_prim:
                if not hasattr(self, 'scene_radius') or self.path != path or request.get('reload'):
                    visible_bounds = compute_visible_geometry_bounds(reference, Usd.TimeCode(frame))
                    scene_canonical = Gf.BBox3d(visible_bounds, bounds_conversion).ComputeAlignedRange()
                    self.scene_radius = 1.0 if scene_canonical.IsEmpty() else max(scene_canonical.GetSize().GetLength() * .5, .001)
                sel_range = compute_selection_bounds(reference, [target_prim], Usd.TimeCode(frame))
                if sel_range is not None and not sel_range.IsEmpty():
                    canonical=Gf.BBox3d(sel_range, bounds_conversion).ComputeAlignedRange()
                else:
                    visible_bounds = compute_visible_geometry_bounds(reference, Usd.TimeCode(frame))
                    canonical=Gf.BBox3d(visible_bounds, bounds_conversion).ComputeAlignedRange()
            else:
                visible_bounds = compute_visible_geometry_bounds(reference, Usd.TimeCode(frame))
                canonical=Gf.BBox3d(visible_bounds, bounds_conversion).ComputeAlignedRange()
                self.scene_radius = 1.0 if canonical.IsEmpty() else max(canonical.GetSize().GetLength() * .5, .001)
            self.camera_anchor=((Gf.Vec3d(0),1.0) if canonical.IsEmpty() else (canonical.GetMidpoint(),max(canonical.GetSize().GetLength()*.5,.001)))
            self.camera_fit_key=fit_key
            self.camera_anchor_translation = cur_trans
            self.camera_anchor_target = transform_target
        center,radius=Gf.Vec3d(self.camera_anchor[0]),self.camera_anchor[1]
        req_up = str(request.get('upAxis', 'Y')).upper()
        if req_up == 'Z':
            up = Gf.Vec3d(0, 0, 1)
            forward = Gf.Vec3d(math.sin(yaw)*math.cos(pitch), -math.cos(yaw)*math.cos(pitch), math.sin(pitch))
        else:
            up = Gf.Vec3d(0, 1, 0)
            forward = Gf.Vec3d(math.sin(yaw)*math.cos(pitch), math.sin(pitch), math.cos(yaw)*math.cos(pitch))
        right = Gf.Cross(up, forward).GetNormalized()
        camera_up = Gf.Cross(forward, right).GetNormalized()
        aspect = width / height
        fov = max(5.0, min(170.0, float(request.get('fov') or 45.0)))
        half_fov = math.radians(22.5)
        sin_k = math.sin(math.atan(math.tan(half_fov) * min(aspect, 1)))
        camera_sync = None
        new_center = None
        orbit_seq = request.get('orbitPivotSeq')
        if orbit_seq and orbit_seq != self._last_orbit_pivot_seq:
            self._last_orbit_pivot_seq = orbit_seq
            orbit_target = request.get('orbitPivotTarget')
            orbit_point = request.get('orbitPivotPoint')
            if (
                isinstance(orbit_point, (list, tuple))
                and len(orbit_point) == 3
                and all(isinstance(v, (int, float)) and math.isfinite(v) for v in orbit_point)
            ):
                new_center = Gf.Vec3d(float(orbit_point[0]), float(orbit_point[1]), float(orbit_point[2]))
            elif orbit_target:
                sel_range = compute_selection_bounds(stage, [orbit_target], Usd.TimeCode(frame))
                if sel_range is not None and not sel_range.IsEmpty():
                    new_center = Gf.BBox3d(sel_range, view_conversion).ComputeAlignedRange().GetMidpoint()
            if new_center is not None:
                self.camera_anchor_translation = cur_trans
                self.camera_anchor_target = transform_target
        elif transform_target != self.camera_anchor_target:
            self.camera_anchor_translation = cur_trans
            self.camera_anchor_target = transform_target
        elif cur_trans is not None and cur_trans != self.camera_anchor_translation:
            delta_t = Gf.Vec3d(*cur_trans) - Gf.Vec3d(*self.camera_anchor_translation)
            new_center = center + view_conversion.TransformDir(delta_t)
            self.camera_anchor_translation = cur_trans
        if new_center is not None:
            old_pivot = center + right * (pan_x * radius) + camera_up * (pan_y * radius)
            old_dist = (radius / sin_k) * 1.15 * zoom
            old_eye = old_pivot + forward * old_dist
            delta_eye = old_eye - new_center
            new_dist = max(1e-4, float(Gf.Dot(delta_eye, forward)))
            pan_x = float(Gf.Dot(delta_eye, right)) / radius
            pan_y = float(Gf.Dot(delta_eye, camera_up)) / radius
            zoom = new_dist / ((radius / sin_k) * 1.15)
            self.camera_anchor = (Gf.Vec3d(new_center), radius)
            center = Gf.Vec3d(new_center)
            camera_sync = {'panX': pan_x, 'panY': pan_y, 'zoom': zoom}
        center += right * (pan_x * radius) + camera_up * (pan_y * radius)
        fit_distance = radius / sin_k * 1.15
        distance = fit_distance * zoom
        eye = center + forward * distance
        hair_brush = request.get('hairBrush')
        committed_groom_deltas = None
        committed_groom_node_id = None
        hair_stats = None
        hair_sim_ms = None
        groom_encode_ms = None
        if isinstance(hair_brush, dict) and isinstance(self.current_graph, dict):
            tid = hair_brush.get('nodeId') or request.get('activeHairNodeId') or self.current_graph.get('targetNodeId')
            hair_cfg = None
            for n in self.current_graph.get('nodes') or []:
                if n.get('id') == tid and n.get('operation') == 'hair':
                    hair_cfg = n.get('hair')
                    break
            if isinstance(hair_cfg, dict) and hair_cfg.get('simEnabled'):
                _here = globals().get('__file__')
                for _cand in (
                    os.environ.get('ORCHFLOW_WORKER_DIR'),
                    os.path.dirname(os.path.abspath(_here)) if _here else None,
                    os.path.join(os.getcwd(), 'worker'),
                    r'e:\CODINGPROJECTS\Rust\Sublayer\worker',
                ):
                    if _cand and os.path.isdir(_cand) and _cand not in sys.path:
                        sys.path.insert(0, _cand)
                        break
                import hair_sim
                _query_fn = globals().get('_hair_query_matches')
                if _query_fn is None:
                    from usd_graph import _hair_query_matches as _query_fn
                inv_vc = view_conversion.GetInverse()
                s_eye = inv_vc.Transform(eye)
                s_fwd = inv_vc.TransformDir(-forward).GetNormalized()
                s_right = inv_vc.TransformDir(right).GetNormalized()
                s_up = inv_vc.TransformDir(camera_up).GetNormalized()
                cam_basis = {
                    'origin': (float(s_eye[0]), float(s_eye[1]), float(s_eye[2])),
                    'forward': (float(s_fwd[0]), float(s_fwd[1]), float(s_fwd[2])),
                    'right': (float(s_right[0]), float(s_right[1]), float(s_right[2])),
                    'up': (float(s_up[0]), float(s_up[1]), float(s_up[2])),
                    'tan_half_fov': float(math.tan(math.radians(fov * 0.5))),
                    'aspect': float(aspect),
                }
                hair_stats = {'totalStrands': 0, 'guideStrands': 0, 'fullSolveStrands': 0}
                hair_started = time.perf_counter()
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    updates = hair_sim.step_hair_brush(
                        stage, hair_cfg, _query_fn, hair_brush, cam_basis, diagnostics=hair_stats,
                    )
                hair_sim_ms = (time.perf_counter() - hair_started) * 1000
                if updates:
                    self.pending_groom_graph = None
                    self.pending_groom_frame = None
                if bool(hair_brush.get('commitUsd')) and (updates or hair_brush.get('undoStroke')):
                    encode_started = time.perf_counter()
                    committed_groom_deltas = hair_sim.export_session_groom_deltas(
                        stage, hair_cfg, _query_fn, local_updates=updates, acknowledge=True,
                    )
                    groom_encode_ms = (time.perf_counter() - encode_started) * 1000
                    committed_groom_node_id = tid
                    expected_graph = graph_after_groom_commit(self.current_graph, tid, committed_groom_deltas)
                    if expected_graph is not None:
                        self.pending_groom_graph = (expected_graph, request.get('cameraSession'), request.get('frame'))
        cache_brush = request.get('cacheBrush')
        committed_cache_deltas = None
        committed_cache_masks = None
        committed_cache_layer_id = None
        committed_cache_node_id = None
        cache_sim_ms = None
        if isinstance(cache_brush, dict) and isinstance(self.current_graph, dict):
            ctid = cache_brush.get('nodeId') or request.get('activeCacheEditNodeId') or self.current_graph.get('targetNodeId')
            cache_cfg = None
            for n in self.current_graph.get('nodes') or []:
                if n.get('id') == ctid and n.get('operation') == 'cache_edit':
                    cache_cfg = n.get('cacheEdit')
                    break
            if isinstance(cache_cfg, dict):
                _here = globals().get('__file__')
                for _cand in (
                    os.environ.get('ORCHFLOW_WORKER_DIR'),
                    os.path.dirname(os.path.abspath(_here)) if _here else None,
                    os.path.join(os.getcwd(), 'worker'),
                    r'e:\CODINGPROJECTS\Rust\Sublayer\worker',
                ):
                    if _cand and os.path.isdir(_cand) and _cand not in sys.path:
                        sys.path.insert(0, _cand)
                        break
                import cache_edit as _cache_edit
                inv_vc = view_conversion.GetInverse()
                s_eye = inv_vc.Transform(eye)
                s_fwd = inv_vc.TransformDir(-forward).GetNormalized()
                s_right = inv_vc.TransformDir(right).GetNormalized()
                s_up = inv_vc.TransformDir(camera_up).GetNormalized()
                cam_basis = {
                    'origin': (float(s_eye[0]), float(s_eye[1]), float(s_eye[2])),
                    'forward': (float(s_fwd[0]), float(s_fwd[1]), float(s_fwd[2])),
                    'right': (float(s_right[0]), float(s_right[1]), float(s_right[2])),
                    'up': (float(s_up[0]), float(s_up[1]), float(s_up[2])),
                    'tan_half_fov': float(math.tan(math.radians(fov * 0.5))),
                    'aspect': float(aspect),
                }
                c_started = time.perf_counter()
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    c_updates = _cache_edit.step_cache_brush(
                        stage, cache_cfg, cache_brush, cam_basis, frame=frame, write_usd=True, return_gpu=False,
                    )
                cache_sim_ms = (time.perf_counter() - c_started) * 1000
                if bool(cache_brush.get('commitUsd')) and (
                    c_updates
                    or cache_brush.get('undoStroke')
                    or cache_brush.get('clearMask')
                    or cache_brush.get('invertMask')
                    or str(cache_brush.get('tool', '')).endswith('mask')
                ):
                    committed_cache_layer_id, committed_cache_deltas, committed_cache_masks = (
                        _cache_edit.export_session_cache_deltas(stage, cache_cfg, frame=frame)
                    )
                    committed_cache_node_id = ctid
        show_cache_mask = bool(request.get('showCacheMask')) or (
            isinstance(cache_brush, dict) and str(cache_brush.get('tool', '')).lower() in ('mask', 'unmask')
        )
        if show_cache_mask and isinstance(self.current_graph, dict):
            from pxr import UsdShade, Vt
            ctid_m = (
                (cache_brush.get('nodeId') if isinstance(cache_brush, dict) else None)
                or request.get('activeCacheEditNodeId')
                or self.current_graph.get('targetNodeId')
            )
            mask_cfg = None
            for n in self.current_graph.get('nodes') or []:
                if n.get('id') == ctid_m and n.get('operation') == 'cache_edit':
                    mask_cfg = n.get('cacheEdit')
                    break
            if isinstance(mask_cfg, dict):
                _here = globals().get('__file__')
                for _cand in (
                    os.environ.get('ORCHFLOW_WORKER_DIR'),
                    os.path.dirname(os.path.abspath(_here)) if _here else None,
                    os.path.join(os.getcwd(), 'worker'),
                    r'e:\CODINGPROJECTS\Rust\Sublayer\worker',
                ):
                    if _cand and os.path.isdir(_cand) and _cand not in sys.path:
                        sys.path.insert(0, _cand)
                        break
                import cache_edit as _cache_edit
                mask_colors = _cache_edit.get_cache_mask_vertex_colors(stage, mask_cfg, frame=frame)
                mask_layer = getattr(self, '_cache_mask_layer', None)
                if mask_layer is None:
                    mask_layer = Sdf.Layer.CreateAnonymous('cache_mask_overlay.usda')
                    self._cache_mask_layer = mask_layer
                sess_layer = stage.GetSessionLayer()
                if mask_layer.identifier not in list(sess_layer.subLayerPaths):
                    sess_layer.subLayerPaths.insert(0, mask_layer.identifier)
                mask_layer.Clear()
                if mask_colors:
                    with Usd.EditContext(stage, mask_layer):
                        mat_path = Sdf.Path('/__OrchFlowMaskPreviewMat')
                        mat = UsdShade.Material.Define(stage, mat_path)
                        surf = UsdShade.Shader.Define(stage, mat_path.AppendChild('PreviewSurface'))
                        surf.CreateIdAttr('UsdPreviewSurface')
                        surf.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(0.6)
                        reader = UsdShade.Shader.Define(stage, mat_path.AppendChild('DisplayColorReader'))
                        reader.CreateIdAttr('UsdPrimvarReader_float3')
                        reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('displayColor')
                        reader.CreateInput('fallback', Sdf.ValueTypeNames.Float3).Set((0.65, 0.67, 0.70))
                        out_p = reader.CreateOutput('result', Sdf.ValueTypeNames.Float3)
                        surf.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(out_p)
                        mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(), 'surface')
                        for ppath, colors_np in mask_colors:
                            over_p = stage.OverridePrim(ppath)
                            mb = UsdShade.MaterialBindingAPI.Apply(over_p)
                            mb.Bind(mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants)
                            for purpose in (UsdShade.Tokens.full, UsdShade.Tokens.preview):
                                mb.Bind(mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants, materialPurpose=purpose)
                            UsdGeom.PrimvarsAPI(over_p).CreatePrimvar(
                                'displayColor', Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex
                            ).Set(Vt.Vec3fArray.FromNumpy(colors_np))
                self._cache_mask_active = True
        elif getattr(self, '_cache_mask_active', False):
            mask_layer = getattr(self, '_cache_mask_layer', None)
            if mask_layer is not None:
                mask_layer.Clear()
            self._cache_mask_active = False
        view = view_conversion * Gf.Matrix4d().SetLookAt(eye, center, up)
        frustum = Gf.Frustum()
        clip_radius = max(radius, getattr(self, 'scene_radius', radius))
        trans_reach = Gf.Vec3d(*cur_trans).GetLength() if cur_trans else 0.0
        near_clip = max(1e-4, min(clip_radius * 0.001, distance * 0.001))
        far_clip = max(distance * 100.0, clip_radius * 100.0, trans_reach * 10.0, near_clip * 1000.0)
        frustum.SetPerspective(fov, aspect, near_clip, far_clip)
        if self.size != (width, height):
            self.fbo = None
            fmt = self.QtOpenGL.QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(self.QtOpenGL.QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
            fmt.setSamples(4)
            self.fbo = self.QtOpenGL.QOpenGLFramebufferObject(width, height, fmt)
            if not self.fbo.isValid():
                fmt.setSamples(0)
                self.fbo = self.QtOpenGL.QOpenGLFramebufferObject(width, height, fmt)
            if not self.fbo.isValid():
                raise RuntimeError('Unable to allocate viewport framebuffer')
            self.size = (width, height)
        self.fbo.bind()
        GL = self.GL
        GL.glViewport(0, 0, width, height)
        GL.glClearColor(0.055, 0.08, 0.09, 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        engine = self.engine
        engine.SetCameraState(view, frustum.ComputeProjectionMatrix())
        engine.SetRenderViewport(Gf.Vec4d(0, 0, width, height))
        engine.SetRenderBufferSize(Gf.Vec2i(width, height))
        if materials == 'solid':
            has_scene_lights = False
        else:
            if self.has_scene_lights is None:
                self.has_scene_lights = any(
                    ('Light' in p.GetTypeName() or (hasattr(UsdLux, 'LightAPI') and p.HasAPI(UsdLux.LightAPI)))
                    and not p.GetPath().HasPrefix(Sdf.Path('/Render/DefaultLights'))
                    for p in iter_scene_and_prototype_prims(stage)
                )
                if self.path in self.stage_cache:
                    s, gs, gst, ok, _, live = self.stage_cache[self.path]
                    self.stage_cache[self.path] = (s, gs, gst, ok, self.has_scene_lights, live)
            has_scene_lights = self.has_scene_lights
        session = stage.GetSessionLayer()
        if not has_scene_lights:
            # 3-point studio rig matching Omniverse RTX's warm key + cool sky fill balance.
            inv_vc = view_conversion.GetInverse()
            key_dir = inv_vc.TransformDir(Gf.Vec3d(0.7, 1.0, 0.7)).GetNormalized()
            fill_dir = inv_vc.TransformDir(Gf.Vec3d(-0.8, 0.45, -0.75)).GetNormalized()
            key_light = Glf.SimpleLight()
            key_light.position = Gf.Vec4f(float(key_dir[0]), float(key_dir[1]), float(key_dir[2]), 0.0)
            key_light.ambient = Gf.Vec4f(0.18, 0.19, 0.21, 1.0)
            key_light.diffuse = Gf.Vec4f(1.05, 1.01, 0.95, 1.0)
            key_light.specular = Gf.Vec4f(0.85, 0.85, 0.85, 1.0)
            fill_light = Glf.SimpleLight()
            fill_light.position = Gf.Vec4f(float(fill_dir[0]), float(fill_dir[1]), float(fill_dir[2]), 0.0)
            fill_light.ambient = Gf.Vec4f(0.0, 0.0, 0.0, 1.0)
            fill_light.diffuse = Gf.Vec4f(0.38, 0.42, 0.50, 1.0)
            fill_light.specular = Gf.Vec4f(0.25, 0.28, 0.32, 1.0)
            mat = Glf.SimpleMaterial()
            mat.ambient = Gf.Vec4f(0.22, 0.22, 0.24, 1.0)
            mat.diffuse = Gf.Vec4f(0.88, 0.88, 0.88, 1.0)
            mat.specular = Gf.Vec4f(0.35, 0.35, 0.35, 1.0)
            mat.shininess = 32.0
            engine.SetLightingState([key_light, fill_light], mat, Gf.Vec4f(0.14, 0.15, 0.17, 1.0))
        else:
            def_prim = stage.GetPrimAtPath('/Render/DefaultLights')
            if def_prim and def_prim.IsValid():
                with Usd.EditContext(stage, session):
                    stage.RemovePrim(Sdf.Path('/Render/DefaultLights'))
            if self._normalized_lights_key != (id(stage), frame):
                _normalize_storm_lights(stage, session, frame)
                self._normalized_lights_key = (id(stage), frame)
            has_dome = any(
                'DomeLight' in p.GetTypeName()
                and not p.GetPath().HasPrefix(Sdf.Path('/Render/DefaultLights'))
                for p in iter_scene_and_prototype_prims(stage)
            )
            scene_amb = Gf.Vec4f(0.04, 0.04, 0.04, 1.0) if has_dome else Gf.Vec4f(0.14, 0.15, 0.17, 1.0)
            engine.SetLightingState([], Glf.SimpleMaterial(), scene_amb)
        purpose = request.get('purpose', 'render')
        if purpose not in ('render', 'proxy', 'both'):
            purpose = 'render'
        complexity_name = request.get('complexity', 'medium')
        complexity_map = {'low': 1.0, 'medium': 1.1, 'high': 1.25}
        draw_mode_map = {
            'shaded': UsdImagingGL.DrawMode.DRAW_SHADED_SMOOTH,
            'solid': UsdImagingGL.DrawMode.DRAW_SHADED_SMOOTH,
            'wireframe': UsdImagingGL.DrawMode.DRAW_WIREFRAME,
            'points': UsdImagingGL.DrawMode.DRAW_POINTS,
            'bounds': UsdImagingGL.DrawMode.DRAW_WIREFRAME,
        }
        _sync_bounds_proxy(stage, session, frame, draw_mode == 'bounds')
        params = UsdImagingGL.RenderParams()
        params.frame = Usd.TimeCode(frame)
        params.clearColor = Gf.Vec4f(0.055, 0.08, 0.09, 1)
        params.enableLighting = draw_mode in ('shaded', 'solid')
        params.enableSceneLights = (draw_mode == 'shaded') and scene_materials
        params.enableSceneMaterials = scene_materials and (draw_mode == 'shaded')
        params.showRender = purpose in ('render', 'both')
        params.showProxy = purpose in ('proxy', 'both')
        params.showGuides = False
        params.complexity = 1.0 if not scene_materials else complexity_map.get(complexity_name, 1.1)
        params.drawMode = draw_mode_map[draw_mode]
        params.enableSampleAlphaToCoverage = (draw_mode == 'shaded') and scene_materials
        selection,selection_info=self.selection_resolver.resolve(request.get('selection',[]))
        engine.SetSelected([Sdf.Path(p) for p in selection if stage.GetPrimAtPath(p)])
        engine.SetSelectionColor(Gf.Vec4f(1,.55,.05,1))
        params.highlight=bool(selection)
        params.colorCorrectionMode = 'sRGB'
        if stage_changed:
            renderer_name = next((p['name'] for p in self.plugins if p['id'] == renderer), renderer)
            report_status(f'Hydra {renderer_name}: Rendering scene...')
        render_root = stage.GetPrimAtPath('/Render/BoundsProxy') if draw_mode == 'bounds' else stage.GetPseudoRoot()
        if not render_root or not render_root.IsValid():
            render_root = stage.GetPseudoRoot()
        render_start = time.perf_counter()
        iterations = 0
        while True:
            engine.Render(render_root, params)
            iterations += 1
            converged = engine.IsConverged()
            if interactive or converged or iterations >= 128 or time.perf_counter() - render_start > 3:
                break
        GL.glFinish()
        render_ms = (time.perf_counter() - render_start) * 1000
        capture_start = time.perf_counter()
        image = self.fbo.toImage()
        data = self.QtCore.QByteArray()
        buffer = self.QtCore.QBuffer(data)
        buffer.open(self.QtCore.QIODevice.OpenModeFlag.WriteOnly)
        # JPEG avoids PNG compression cost during motion; idle frames stay lossless.
        encoding = 'jpeg' if interactive else 'png'
        if not image.save(buffer, 'JPG' if interactive else 'PNG', 85 if interactive else -1):
            raise RuntimeError('Failed to encode viewport frame')
        buffer.close()
        encoded = bytes(data)
        picked_prim = None
        picked_material = None
        picked_point = None
        pick = request.get('pick')
        if isinstance(pick, (list, tuple)) and len(pick) == 2:
            picked_prim = self._pick(engine, stage, view, frustum, params, pick, width, height)
            if picked_prim:
                u_p = float(pick[0])
                v_p = float(pick[1])
                tan_half = math.tan(math.radians(fov * 0.5))
                sx = (u_p * 2.0 - 1.0) * tan_half * aspect
                sy = (1.0 - v_p * 2.0) * tan_half
                rdir_v = (-forward + right * sx + camera_up * sy).GetNormalized()
                vc_inv = view_conversion.GetInverse()
                ro_s = vc_inv.Transform(eye)
                rd_s = vc_inv.TransformDir(rdir_v)
                hit = raycast_prim_hit(
                    stage,
                    picked_prim,
                    (ro_s[0], ro_s[1], ro_s[2]),
                    (rd_s[0], rd_s[1], rd_s[2]),
                    Usd.TimeCode(frame),
                )
                hit_mesh_path = None
                hit_face_index = None
                if hit is not None:
                    hit_s = hit["point"]
                    hit_v = view_conversion.Transform(Gf.Vec3d(*hit_s))
                    picked_point = [float(hit_v[0]), float(hit_v[1]), float(hit_v[2])]
                    hit_mesh_path = hit.get("meshPath")
                    hit_face_index = hit.get("faceIndex")
                picked_material = resolve_prim_material(
                    stage,
                    picked_prim,
                    hit_mesh_path=hit_mesh_path,
                    face_index=hit_face_index,
                )
        self.fbo.release()
        capture_ms = (time.perf_counter() - capture_start) * 1000
        binary_frame = bool(request.get('binaryFrame'))
        response = {
                'width': width, 'height': height, 'renderer': renderer,
                'interactive': interactive, 'encoding': encoding, 'encodedBytes': len(encoded),
                'graphStats': self.graph_stats,
                'selectionInfo':selection_info,
                'pickedPrim': picked_prim,
                'pickedMaterial': picked_material,
                'pickedPoint': picked_point,
                'cameraSync': camera_sync,
                'materialMode': material_mode,
                'fallbackMaterials':fallback_materials,
                'hairGroomDeltas': committed_groom_deltas,
                'hairGroomNodeId': committed_groom_node_id,
                'hairSolveStats': hair_stats,
                'hairSimMs': hair_sim_ms,
                'groomEncodeMs': groom_encode_ms,
                'cacheEditNodeId': committed_cache_node_id,
                'cacheEditLayerId': committed_cache_layer_id,
                'cacheEditDeltas': committed_cache_deltas,
                'cacheEditMasks': committed_cache_masks,
                'cacheSimMs': cache_sim_ms,
                'updateKind': 'groom-commit' if groom_committed else None,
                'invalidShaderInputs':self.shader_issues,
                'missingAssets': missing_assets,
                'warnings': ([f'{len(missing_assets)} shader asset(s) could not be resolved. '
                              + ((f'Solid fallback applied to {len(fallback_materials)} material(s); other materials remain enabled and source USD is unchanged.' if fallback_materials else 'No connected materials affected; scene shading is retained and source USD is unchanged.') if materials == 'auto' else
                                 'Scene materials may render incorrectly or transparently.' if scene_materials else
                                 'Solid preview does not use scene materials.')]
                             if missing_assets else []) + ([f'{len(self.shader_issues)} invalid shader primvar input(s). '+ ('Affected materials use solid fallback; no UV set was guessed or source USD changed.' if materials=='auto' else 'Solid preview ignores these materials.')] if self.shader_issues else []),
                'rendererCapabilities': self.renderer_capabilities or {
                    'aovs': [str(aov) for aov in engine.GetRendererAovs()],
                    'pause': engine.IsPauseRendererSupported(),
                    'stop': engine.IsStopRendererSupported(),
                    'featureParity': 'not inferred; renderer-specific schema/material support requires verification'},
                'gpu': self.gpu, 'converged': converged, 'frame': frame,
                'cameraAnchor': {
                    'session': str(request.get('cameraSession') or ''),
                    'fit': int(request.get('frameFit', 0)),
                    'center': [float(self.camera_anchor[0][0]), float(self.camera_anchor[0][1]), float(self.camera_anchor[0][2])],
                    'radius': float(self.camera_anchor[1]),
                    'translation': list(self.camera_anchor_translation or [0.0, 0.0, 0.0]),
                    'transformTarget': self.camera_anchor_target,
                },
                'renderMs': round(render_ms, 2), 'captureMs': round(capture_ms, 2),
                'totalMs': round((time.perf_counter() - started) * 1000, 2),
                'pngBytes': len(encoded) if encoding == 'png' else 0,
                'upAxis': req_up}
        if binary_frame:
            response['_binary_bytes'] = encoded
        else:
            response['image'] = f'data:image/{encoding};base64,' + base64.b64encode(encoded).decode('ascii')
        if (committed_groom_deltas is not None and self.pending_groom_graph is not None
                and not request.get('pick') and not camera_sync):
            self.pending_groom_frame = (
                groom_frame_view(request),
                {key: value for key, value in response.items() if key not in ('hairGroomDeltas', 'hairGroomNodeId')},
            )
        return response

    def _pick(self, engine, stage, view, frustum, params, pick, width, height):
        # Ctrl+click picking: narrow the render frustum to one pixel and use
        # Hydra's intersection test. Read-only; never edits the stage.
        from pxr import Gf, Sdf
        try:
            u = min(1.0, max(0.0, float(pick[0])))
            v = min(1.0, max(0.0, float(pick[1])))
            narrowed = frustum.ComputeNarrowedFrustum(
                Gf.Vec2d(2.0 * u - 1.0, 1.0 - 2.0 * v),
                Gf.Vec2d(1.0 / max(1, width), 1.0 / max(1, height)))
            result = engine.TestIntersection(view, narrowed.ComputeProjectionMatrix(),
                                             stage.GetPseudoRoot(), params)
        except Exception as e:
            sys.stderr.write(f'Hydra pick error: {e}\n')
            return None
        if not result:
            return None
        values = result if isinstance(result, (list, tuple)) else [result]
        hit = next((x for x in values if isinstance(x, Sdf.Path) and not x.isEmpty), None)
        if hit is None:
            return None
        prim = stage.GetPrimAtPath(hit)
        while not prim and hit != Sdf.Path.absoluteRootPath:
            hit = hit.GetParentPath()
            prim = stage.GetPrimAtPath(hit)
        return str(hit) if prim else None

    def close(self):
        # Destroy Hydra resources before Qt tears down their owning context.
        if self.selection_resolver is not None:self.selection_resolver.close()
        self.context.makeCurrent(self.surface)
        self.engine = None
        self.fbo = None
        self.stage = None
        self.graph_stages = []
        self.context.doneCurrent()


def viewport():
    global _viewport
    if _viewport is None:
        _viewport = Viewport()
        # Register after PySide imports so GPU cleanup precedes Qt's atexit hooks.
        atexit.register(close_viewport)
    return _viewport


def close_viewport():
    global _viewport
    if _viewport is not None:
        _viewport.close()
        _viewport = None


def dispatch(request):
    if not isinstance(request, dict):
        raise ValueError('Request must be an object')
    try:
        from pxr import Usd, UsdGeom
    except ImportError as exc:
        raise RuntimeError('OpenUSD unavailable. Configure ORCHFLOW_USD_ROOT and ORCHFLOW_PYTHON.') from exc
    operation = request.get('operation')
    if operation == 'clear_scene':
        clear_graph_caches()
        try:
            _here = globals().get('__file__')
            for _cand in (
                os.environ.get('ORCHFLOW_WORKER_DIR'),
                os.path.dirname(os.path.abspath(_here)) if _here else None,
                os.path.join(os.getcwd(), 'worker'),
                r'e:\CODINGPROJECTS\Rust\Sublayer\worker',
            ):
                if _cand and os.path.isdir(_cand) and _cand not in sys.path:
                    sys.path.insert(0, _cand)
                    break
            import hair_sim
            hair_sim.reset_hair_sessions()
            import cache_edit as _ce
            _ce.reset_cache_sessions()
        except Exception:
            pass
        return {'cleared': True, 'cachedScenes': 0}
    if operation == 'reset_hair':
        try:
            _here = globals().get('__file__')
            for _cand in (
                os.environ.get('ORCHFLOW_WORKER_DIR'),
                os.path.dirname(os.path.abspath(_here)) if _here else None,
                os.path.join(os.getcwd(), 'worker'),
                r'e:\CODINGPROJECTS\Rust\Sublayer\worker',
            ):
                if _cand and os.path.isdir(_cand) and _cand not in sys.path:
                    sys.path.insert(0, _cand)
                    break
            import hair_sim
            hair_sim.reset_hair_sessions()
            import cache_edit as _ce
            _ce.reset_cache_sessions()
        except Exception:
            pass
        if _viewport is not None:
            _viewport.pending_groom_graph = None
            _viewport.pending_groom_frame = None
            _viewport.path = None
        return {'reset': True}
    if operation == 'scene_children':
        return scene_children(request)
    if operation == 'materialx_rule_matches':
        return materialx_rule_matches(request)
    if operation == 'asset_override_inputs':
        return asset_override_inputs(request)
    if operation == 'inspect_stage_materials':
        return inspect_stage_materials(request)
    if operation == 'inspect_stage_lights':
        return inspect_stage_lights(request)
    if operation == 'inspect_stage_curves':
        return inspect_stage_curves(request)
    if operation == 'inspect_stage_skeletons':
        return inspect_stage_skeletons(request)
    if operation == 'export_character_body':
        return export_character_body(request)
    if operation == 'convert_source_to_usd':
        return convert_source_to_usd(request)
    if operation == 'materialx_document':
        return materialx_document(request)
    if operation == 'reconcile_upstream':
        return reconcile_upstream(request)
    if operation == 'apply_live_adjustment':
        return create_live_adjustment(request)
    if operation == 'export_live_output':
        stage,alive,stats=compile_graph(request['graph'],reload=True)
        return export_handoff(request,prepared=({},stage,alive),tag_identity=False)
    if operation == 'validate_manual_baseline':
        return validate_manual_baseline(request)
    if operation == 'review_publication':
        return review_publication(request)
    if operation == 'publish_reviewed':
        return publish_reviewed(request)
    if operation == 'reuse_handoff':
        return reuse_handoff(request)
    if operation == 'handoff_export':
        return export_handoff(request)
    if operation == 'coordinate_preflight':
        return coordinate_preflight(request)
    if operation == 'dependencies':
        from pxr import Sdf, UsdUtils
        path = request.get('path')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('A USD root layer path is required')
        root = Sdf.Layer.FindOrOpen(path)
        if root is None:
            raise ValueError('Unable to open USD root layer for dependency discovery')
        root.Reload()
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(path)
        files = {}
        for layer in layers:
            if layer.realPath:
                files[layer.realPath] = {'path': layer.realPath, 'kind': 'usd'}
        for asset in list(assets) + list(unresolved):
            # ComputeAllDependencies anchors unresolved assets against the layer
            # that authored them, including references in unselected variants.
            if asset:
                kind = 'usd' if os.path.splitext(asset)[1].lower() in ('.usd', '.usda', '.usdc', '.usdz') else 'asset'
                files.setdefault(asset, {'path': asset, 'kind': kind})
        root_key = os.path.normcase(os.path.realpath(path))
        return {'files': [entry for entry in files.values() if os.path.normcase(os.path.realpath(entry['path'])) != root_key],
                'unresolved': list(unresolved)}
    if operation == 'capabilities':
        result = {'version': '.'.join(map(str, Usd.GetVersion())), 'inspection': True,
                  'viewport': False, 'renderers': [], 'rendererDiscovery': 'unavailable'}
        try:
            v = viewport()
            result.update(viewport=True, renderers=v.plugins, gpu=v.gpu, glVersion=v.gl_version,
                          rendererDiscovery='installed delegates; compatibility checked on render')
        except Exception as exc:
            result['viewportError'] = str(exc)
        return result
    if operation == 'render':
        return viewport().render(request)
    if operation != 'inspect':
        raise ValueError('Unsupported USD worker operation')
    path = request.get('path')
    if not isinstance(path, str) or not path.strip():
        raise ValueError('A USD root layer path is required')
    keepalive=[]
    if request.get('graph'):
        graph=request['graph']
        stage,keepalive,_=compile_graph(json.loads(graph) if isinstance(graph,str) else graph)
    else:
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.abc', '.obj', '.vdb', '.goz', '.sublayer', '.orchflow'):
            stage = _open_source_stage(path)
        else:
            stage = Usd.Stage.Open(path, load=Usd.Stage.LoadNone)
    if stage is None:
        raise RuntimeError('Unable to open USD stage')
    roots = [{'path': str(p.GetPath()), 'type': p.GetTypeName()} for p in stage.GetPseudoRoot().GetChildren()]
    return {'rootLayer': stage.GetRootLayer().identifier,
            'upAxis': str(UsdGeom.GetStageUpAxis(stage)),
            'metersPerUnit': UsdGeom.GetStageMetersPerUnit(stage),
            'defaultPrim': str(stage.GetDefaultPrim().GetPath()) if stage.GetDefaultPrim() else None,
            'startTime': stage.GetStartTimeCode(), 'endTime': stage.GetEndTimeCode(),
            'rootPrims': roots, 'usedLayers': [l.identifier for l in stage.GetUsedLayers()],
            'payloadsLoaded': bool(request.get('graph')),
            'materialInspection':inspect_materials(stage),
            'note': 'Used layers are not an exhaustive asset dependency manifest.'}


def respond(request):
    try:
        return dispatch(request)
    except Exception as exc:
        return {'error': str(exc)}


if __name__ == '__main__':
    try:
        if '--serve' in sys.argv:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    result = respond(request)
                except Exception as exc:
                    result = {'error': str(exc)}
                binary_bytes = result.pop('_binary_bytes', None) if isinstance(result, dict) else None
                if binary_bytes is not None:
                    result['binaryBytes'] = len(binary_bytes)
                    sys.stdout.flush()
                    sys.stdout.buffer.write((_PROTOCOL + json.dumps(result) + '\n').encode('utf-8'))
                    sys.stdout.buffer.write(binary_bytes)
                    sys.stdout.buffer.flush()
                else:
                    print(_PROTOCOL + json.dumps(result), flush=True)
        else:
            print(json.dumps(respond(json.load(sys.stdin))))
    finally:
        close_viewport()


