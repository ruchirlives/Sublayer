"""Upstream OpenUSD inspection and persistent offscreen Hydra rendering worker.

The process owns its Qt GL context and USD stage. It never writes source assets.
In --serve mode protocol records are prefixed, keeping native stdout diagnostics
separate from responses. Writes are restricted to explicit handoff/publication
operations whose context and destinations are validated by the Rust backend.
"""
import base64
import atexit
import json
import math
import os
import sys
import time
import functools
if 'compile_graph' not in globals():
    from usd_graph import compile_graph, coordinate_preflight, export_handoff, materialx_document, materialx_rule_matches, reuse_handoff, inspect_stage_materials, inspect_stage_lights, inspect_stage_curves, clear_graph_caches, query_scene_children, compute_material_users

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



configure_runtime()

if 'compute_visible_geometry_bounds' not in globals():
    from viewport_bounds import compute_visible_geometry_bounds, compute_selection_bounds, isolate_prim_in_stage, apply_viewport_visibility


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
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
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
    from material_preview import missing_shader_assets, invalid_shader_inputs, selective_material_preview


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
        if graph:stage,alive,_=compile_graph(graph,reload_flag)
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
                        for p in UsdImagingGL.Engine.GetRendererPlugins()
                        if str(p) == 'HdStormRendererPlugin']
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

    def render(self, request):
        from pxr import Usd, UsdGeom, UsdImagingGL, Gf, Glf, CameraUtil, UsdLux, Sdf
        started = time.perf_counter()
        width = int(finite_number(request.get('width', 960), 'width', 64, 2048))
        height = int(finite_number(request.get('height', 640), 'height', 64, 2048))
        yaw = math.radians(finite_number(request.get('yaw', 35), 'yaw', -36000, 36000))
        pitch = math.radians(finite_number(request.get('pitch', 20), 'pitch', -89, 89))
        zoom = finite_number(request.get('zoom', 1), 'zoom', 0.05, 100)
        pan_x = finite_number(request.get('panX', 0), 'panX', -100, 100)
        pan_y = finite_number(request.get('panY', 0), 'panY', -100, 100)
        materials = request.get('materials', 'auto')
        if materials not in ('auto', 'scene', 'solid'):
            raise ValueError('Material preview must be auto, scene, or solid')
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
            import hashlib
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
            self.path = None
        stage_changed = (self.path != path) or bool(request.get('reload'))
        if stage_changed:
            # New engine prevents stale prims from a previously selected stage.
            if self.path is not None:
                self.engine = None
                self.engine = UsdImagingGL.Engine(engine_params)
                if not self.engine.SetRendererPlugin(renderer):
                    raise RuntimeError('Unable to reinitialize selected delegate')
            if graph:
                stage, self.graph_stages, self.graph_stats = compile_graph(graph, request.get('reload', False))
                self.outline_key = json.dumps(graph, sort_keys=True)
            else:
                stage = Usd.Stage.Open(path)
                self.graph_stages, self.graph_stats = [], None
                self.outline_key = None
            if stage is None:
                raise RuntimeError('Unable to open USD stage')
            if request.get('reload') and not graph:
                stage.Reload()
            self.stage, self.path = stage, path
            if self.selection_resolver is not None:self.selection_resolver.close()
            self.selection_resolver=ViewportSelection(stage)
            self.texture_diagnostics = None
            self.auto_preview = None
            self.bounds = None
        stage = self.stage
        frame = finite_number(request.get('frame', stage.GetStartTimeCode()), 'frame', -1e12, 1e12)
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
                sel_range = compute_selection_bounds(reference, [target_prim], Usd.TimeCode(frame))
                if sel_range is not None and not sel_range.IsEmpty():
                    canonical=Gf.BBox3d(sel_range, bounds_conversion).ComputeAlignedRange()
                else:
                    visible_bounds = compute_visible_geometry_bounds(reference, Usd.TimeCode(frame))
                    canonical=Gf.BBox3d(visible_bounds, bounds_conversion).ComputeAlignedRange()
            else:
                visible_bounds = compute_visible_geometry_bounds(reference, Usd.TimeCode(frame))
                canonical=Gf.BBox3d(visible_bounds, bounds_conversion).ComputeAlignedRange()
            self.camera_anchor=((Gf.Vec3d(0),1.0) if canonical.IsEmpty() else (canonical.GetMidpoint(),max(canonical.GetSize().GetLength()*.5,.001)))
            self.camera_fit_key=fit_key
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
        center += right * (pan_x * radius) + camera_up * (pan_y * radius)
        aspect = width / height
        fov = max(5.0, min(170.0, float(request.get('fov') or 45.0)))
        half_fov = math.radians(22.5)
        fit_distance = radius / math.sin(math.atan(math.tan(half_fov) * min(aspect, 1))) * 1.15
        distance = fit_distance * zoom
        eye = center + forward * distance
        view = view_conversion * Gf.Matrix4d().SetLookAt(eye, center, up)
        frustum = Gf.Frustum()
        frustum.SetPerspective(fov, aspect, max(radius * 0.0001, distance - radius * 2), distance + radius * 4)
        if self.size != (width, height):
            self.fbo = None
            fmt = self.QtOpenGL.QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(self.QtOpenGL.QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
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
        has_scene_lights = any(
            ('Light' in p.GetTypeName() or (hasattr(UsdLux, 'LightAPI') and p.HasAPI(UsdLux.LightAPI)))
            and not p.GetPath().HasPrefix(Sdf.Path('/Render/DefaultLights'))
            for p in stage.Traverse(Usd.TraverseInstanceProxies())
        )
        session = stage.GetSessionLayer()
        if not has_scene_lights:
            light = Glf.SimpleLight()
            light_eye = view_conversion.GetInverse().Transform(eye)
            light.position = Gf.Vec4f(float(light_eye[0]), float(light_eye[1]), float(light_eye[2]), 1)
            light.ambient = Gf.Vec4f(0.3, 0.3, 0.3, 1)
            light.diffuse = Gf.Vec4f(0.9, 0.9, 0.9, 1)
            engine.SetLightingState([light], Glf.SimpleMaterial(), Gf.Vec4f(0.25, 0.25, 0.25, 1))
        else:
            def_prim = stage.GetPrimAtPath('/Render/DefaultLights')
            if def_prim and def_prim.IsValid():
                with Usd.EditContext(stage, session):
                    stage.RemovePrim(Sdf.Path('/Render/DefaultLights'))
            engine.SetLightingState([], Glf.SimpleMaterial(), Gf.Vec4f(0.04, 0.04, 0.04, 1))
        params = UsdImagingGL.RenderParams()
        params.frame = Usd.TimeCode(frame)
        params.clearColor = Gf.Vec4f(0.055, 0.08, 0.09, 1)
        params.enableLighting = True
        params.enableSceneLights = True
        params.enableSceneMaterials = scene_materials
        selection,selection_info=self.selection_resolver.resolve(request.get('selection',[]))
        engine.SetSelected([Sdf.Path(p) for p in selection if stage.GetPrimAtPath(p)])
        engine.SetSelectionColor(Gf.Vec4f(1,.55,.05,1))
        params.highlight=bool(selection)
        params.colorCorrectionMode = 'sRGB'
        if stage_changed:
            report_status('Hydra Storm: Rendering scene...')
        render_start = time.perf_counter()
        iterations = 0
        while True:
            engine.Render(stage.GetPseudoRoot(), params)
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
        if not image.save(buffer, 'JPG' if interactive else 'PNG', 72 if interactive else -1):
            raise RuntimeError('Failed to encode viewport frame')
        buffer.close()
        encoded = bytes(data)
        picked_prim = None
        pick = request.get('pick')
        if isinstance(pick, (list, tuple)) and len(pick) == 2:
            picked_prim = self._pick(engine, stage, view, frustum, params, pick, width, height)
        self.fbo.release()
        capture_ms = (time.perf_counter() - capture_start) * 1000
        return {'image': f'data:image/{encoding};base64,' + base64.b64encode(encoded).decode('ascii'),
                'width': width, 'height': height, 'renderer': renderer,
                'interactive': interactive, 'encoding': encoding, 'encodedBytes': len(encoded),
                'graphStats': self.graph_stats,
                'selectionInfo':selection_info,
                'pickedPrim': picked_prim,
                'materialMode': material_mode,
                'fallbackMaterials':fallback_materials,
                'invalidShaderInputs':self.shader_issues,
                'missingAssets': missing_assets,
                'warnings': ([f'{len(missing_assets)} shader asset(s) could not be resolved. '
                              + ((f'Solid fallback applied to {len(fallback_materials)} material(s); other materials remain enabled and source USD is unchanged.' if fallback_materials else 'No connected materials affected; scene shading is retained and source USD is unchanged.') if materials == 'auto' else
                                 'Scene materials may render incorrectly or transparently.' if scene_materials else
                                 'Solid preview does not use scene materials.')]
                             if missing_assets else []) + ([f'{len(self.shader_issues)} invalid shader primvar input(s). '+ ('Affected materials use solid fallback; no UV set was guessed or source USD changed.' if materials=='auto' else 'Solid preview ignores these materials.')] if self.shader_issues else []),
                'rendererCapabilities': {
                    'aovs': [str(aov) for aov in engine.GetRendererAovs()],
                    'pause': engine.IsPauseRendererSupported(),
                    'stop': engine.IsStopRendererSupported(),
                    'featureParity': 'not inferred; renderer-specific schema/material support requires verification'},
                'gpu': self.gpu, 'converged': converged, 'frame': frame,
                'renderMs': round(render_ms, 2), 'captureMs': round(capture_ms, 2),
                'totalMs': round((time.perf_counter() - started) * 1000, 2),
                'pngBytes': len(encoded) if encoding == 'png' else 0,
                'upAxis': req_up}

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
            sys.stderr.write(f'Storm pick error: {e}\n')
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
        return {'cleared': True, 'cachedScenes': 0}
    if operation == 'scene_children':
        return scene_children(request)
    if operation == 'materialx_rule_matches':
        return materialx_rule_matches(request)
    if operation == 'inspect_stage_materials':
        return inspect_stage_materials(request)
    if operation == 'inspect_stage_lights':
        return inspect_stage_lights(request)
    if operation == 'inspect_stage_curves':
        return inspect_stage_curves(request)
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
                print(_PROTOCOL + json.dumps(result), flush=True)
        else:
            print(json.dumps(respond(json.load(sys.stdin))))
    finally:
        close_viewport()


