"""Megascans, Fab, FBX, and glTF/GLB non-destructive OpenUSD stage builders.

All external meshes, JSON descriptors, and textures remain at their original locations
(such as the central Megascans/Fab library) and are referenced in anonymous in-memory
OpenUSD stages without copying or mutating source files. Only packed binary containers
(.glb embedded image buffers) materialize read-only texture slices in the OS temp cache.
"""

import base64
import hashlib
import json
import math
import os
import re
import struct
import tempfile
import zlib


MEGASCANS_JSON_EXTS = ('.json',)
FBX_EXTS = ('.fbx',)
GLTF_EXTS = ('.gltf', '.glb')


def is_megascans_or_fab_json(path):
    """Return True if `path` looks like a Quixel Megascans or Fab asset JSON descriptor."""
    if not isinstance(path, str) or not path.lower().endswith('.json'):
        return False
    if not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) > 16 * 1024 * 1024:
            return False
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            data = json.load(f)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    # Sublayer Fab manifest
    if data.get('sublayerAssetKind') in ('fab', 'megascans'):
        return True
    # Quixel Bridge / Megascans descriptor (<id>.json)
    if any(k in data for k in ('components', 'meshes', 'maps', 'categories', 'semanticTags', 'pack')):
        if 'nodes' not in data and 'edges' not in data:
            return True
    # Fab metadata.json
    if any(k in data for k in ('listingUid', 'assetId', 'assetNamespace', 'files', 'gltf', 'textures')):
        if 'nodes' not in data and 'edges' not in data:
            return True
    return False


# ---------------------------------------------------------------------------
# Shared Dual-Context PBR Material Builder (MaterialX Standard Surface + UsdPreviewSurface)
# ---------------------------------------------------------------------------

def _sdr_has_mtlx():
    if os.environ.get('ORCHFLOW_SKIP_SDR_CHECK'):
        return True
    try:
        from pxr import Sdr
        return bool(Sdr.Registry().GetShaderNodeByIdentifier('ND_standard_surface_surfaceshader'))
    except Exception:
        return False


def author_pbr_material(
    stage,
    mat_path_str,
    *,
    base_color=(0.8, 0.8, 0.8),
    roughness=0.5,
    metalness=0.0,
    specular=0.5,
    emissive_color=(0.0, 0.0, 0.0),
    opacity=1.0,
    alpha_cutoff=None,
    subsurface=0.0,
    thin_walled=False,
    normal_scale=1.0,
    normal_convention='opengl',
    albedo_tex=None,
    roughness_tex=None,
    gloss_tex=None,
    metalness_tex=None,
    orm_tex=None,
    gltf_mr_tex=None,
    specular_tex=None,
    normal_tex=None,
    bump_tex=None,
    opacity_tex=None,
    albedo_alpha_cutout=False,
    translucency_tex=None,
    emissive_tex=None,
    uv_primvar='st',
):
    """Author a dual-context MaterialX Standard Surface + UsdPreviewSurface material."""
    from pxr import Gf, Sdf, UsdShade

    mat_path = Sdf.Path(mat_path_str)
    mat = UsdShade.Material.Define(stage, mat_path)
    use_mtlx = _sdr_has_mtlx()

    is_cutout = bool(opacity_tex or albedo_alpha_cutout or (alpha_cutoff is not None))
    effective_thin = bool(thin_walled or is_cutout or translucency_tex or subsurface > 0.0)
    effective_sss = float(subsurface) if subsurface > 0.0 else (0.42 if translucency_tex else 0.0)
    cutoff = float(alpha_cutoff) if alpha_cutoff is not None else 0.35

    # 1. UsdPreviewSurface (always authored for Storm / fallback compatibility)
    preview = UsdShade.Shader.Define(stage, mat_path.AppendChild('PreviewSurface'))
    preview.CreateIdAttr('UsdPreviewSurface')
    preview.CreateOutput('surface', Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(), 'surface')

    preview.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*[float(c) for c in base_color[:3]]))
    preview.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(float(roughness))
    preview.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(float(metalness))
    preview.CreateInput('ior', Sdf.ValueTypeNames.Float).Set(1.5)
    preview.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(float(opacity))
    if any(float(c) > 0.0 for c in emissive_color[:3]) or emissive_tex:
        preview.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(c) for c in (emissive_color if any(emissive_color) else (1.0, 1.0, 1.0))[:3]])
        )
    if is_cutout:
        preview.CreateInput('opacityThreshold', Sdf.ValueTypeNames.Float).Set(cutoff)

    puv = None

    def _get_preview_uv():
        nonlocal puv
        if puv is None:
            puv = UsdShade.Shader.Define(stage, mat_path.AppendChild('PreviewUV_st'))
            puv.CreateIdAttr('UsdPrimvarReader_float2')
            puv.CreateInput('varname', Sdf.ValueTypeNames.Token).Set(uv_primvar)
            puv.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        return puv

    def _preview_tex(node_name, file_path, colorspace, outputs):
        reader = _get_preview_uv()
        tex = UsdShade.Shader.Define(stage, mat_path.AppendChild(node_name))
        tex.CreateIdAttr('UsdUVTexture')
        tex.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(file_path))
        tex.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set(colorspace)
        tex.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
        for out_name, out_type in outputs:
            tex.CreateOutput(out_name, out_type)
        return tex

    if albedo_tex and os.path.isfile(albedo_tex):
        outs = [('rgb', Sdf.ValueTypeNames.Float3)]
        if albedo_alpha_cutout and not opacity_tex:
            outs.append(('a', Sdf.ValueTypeNames.Float))
        ptex = _preview_tex('PreviewTex_Albedo', albedo_tex, 'sRGB', outs)
        preview.GetInput('diffuseColor').ConnectToSource(ptex.ConnectableAPI(), 'rgb')
        if albedo_alpha_cutout and not opacity_tex:
            preview.GetInput('opacity').ConnectToSource(ptex.ConnectableAPI(), 'a')

    if gltf_mr_tex and os.path.isfile(gltf_mr_tex):
        ptex = _preview_tex(
            'PreviewTex_MetallicRoughness',
            gltf_mr_tex,
            'raw',
            [('g', Sdf.ValueTypeNames.Float), ('b', Sdf.ValueTypeNames.Float)],
        )
        preview.GetInput('roughness').ConnectToSource(ptex.ConnectableAPI(), 'g')
        preview.GetInput('metallic').ConnectToSource(ptex.ConnectableAPI(), 'b')
    elif orm_tex and os.path.isfile(orm_tex):
        ptex = _preview_tex(
            'PreviewTex_ORM',
            orm_tex,
            'raw',
            [('g', Sdf.ValueTypeNames.Float), ('b', Sdf.ValueTypeNames.Float)],
        )
        preview.GetInput('roughness').ConnectToSource(ptex.ConnectableAPI(), 'g')
        preview.GetInput('metallic').ConnectToSource(ptex.ConnectableAPI(), 'b')
    else:
        if roughness_tex and os.path.isfile(roughness_tex):
            ptex = _preview_tex('PreviewTex_Roughness', roughness_tex, 'raw', [('r', Sdf.ValueTypeNames.Float)])
            preview.GetInput('roughness').ConnectToSource(ptex.ConnectableAPI(), 'r')
        if metalness_tex and os.path.isfile(metalness_tex):
            ptex = _preview_tex('PreviewTex_Metalness', metalness_tex, 'raw', [('r', Sdf.ValueTypeNames.Float)])
            preview.GetInput('metallic').ConnectToSource(ptex.ConnectableAPI(), 'r')

    if opacity_tex and os.path.isfile(opacity_tex):
        ptex = _preview_tex('PreviewTex_Opacity', opacity_tex, 'raw', [('r', Sdf.ValueTypeNames.Float)])
        preview.GetInput('opacity').ConnectToSource(ptex.ConnectableAPI(), 'r')

    if emissive_tex and os.path.isfile(emissive_tex):
        ptex = _preview_tex('PreviewTex_Emissive', emissive_tex, 'sRGB', [('rgb', Sdf.ValueTypeNames.Float3)])
        preview.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(ptex.ConnectableAPI(), 'rgb')

    if normal_tex and os.path.isfile(normal_tex):
        ptex = _preview_tex('PreviewTex_Normal', normal_tex, 'raw', [('rgb', Sdf.ValueTypeNames.Float3)])
        ptex.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(
            Gf.Vec4f(2.0, -2.0 if str(normal_convention).lower() == 'directx' else 2.0, 2.0, 1.0)
        )
        ptex.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set(
            Gf.Vec4f(-1.0, 1.0 if str(normal_convention).lower() == 'directx' else -1.0, -1.0, 0.0)
        )
        preview.CreateInput('normal', Sdf.ValueTypeNames.Normal3f).ConnectToSource(ptex.ConnectableAPI(), 'rgb')

    if not use_mtlx:
        return mat

    # 2. MaterialX Standard Surface (preferred by Omniverse RTX, Aurora, and Storm MaterialX)
    surface = UsdShade.Shader.Define(stage, mat_path.AppendChild('StandardSurface'))
    surface.CreateIdAttr('ND_standard_surface_surfaceshader')
    surface.CreateOutput('out', Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput('mtlx').ConnectToSource(surface.ConnectableAPI(), 'out')

    surface.CreateInput('base', Sdf.ValueTypeNames.Float).Set(1.0)
    surface.CreateInput('base_color', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*[float(c) for c in base_color[:3]]))
    surface.CreateInput('metalness', Sdf.ValueTypeNames.Float).Set(float(metalness))
    surface.CreateInput('specular', Sdf.ValueTypeNames.Float).Set(float(specular))
    surface.CreateInput('specular_roughness', Sdf.ValueTypeNames.Float).Set(float(roughness))
    surface.CreateInput('specular_IOR', Sdf.ValueTypeNames.Float).Set(1.5)
    surface.CreateInput('opacity', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(float(opacity)))
    if effective_thin:
        surface.CreateInput('thin_walled', Sdf.ValueTypeNames.Bool).Set(True)
    if effective_sss > 0.0:
        surface.CreateInput('subsurface', Sdf.ValueTypeNames.Float).Set(effective_sss)
        surface.CreateInput('subsurface_color', Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(c) for c in base_color[:3]])
        )
        surface.CreateInput('subsurface_scale', Sdf.ValueTypeNames.Float).Set(0.25)
        surface.CreateInput('subsurface_radius', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.8, 1.0, 0.4))
    if any(float(c) > 0.0 for c in emissive_color[:3]) or emissive_tex:
        surface.CreateInput('emission', Sdf.ValueTypeNames.Float).Set(1.0)
        surface.CreateInput('emission_color', Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(c) for c in (emissive_color if any(emissive_color) else (1.0, 1.0, 1.0))[:3]])
        )

    mtlx_uv = None

    def _get_mtlx_uv():
        nonlocal mtlx_uv
        if mtlx_uv is None:
            mtlx_uv = UsdShade.Shader.Define(stage, mat_path.AppendChild('UV_st'))
            mtlx_uv.CreateIdAttr('ND_geompropvalue_vector2')
            mtlx_uv.CreateInput('geomprop', Sdf.ValueTypeNames.String).Set(uv_primvar)
            mtlx_uv.CreateOutput('out', Sdf.ValueTypeNames.Float2)
        return mtlx_uv

    def _mtlx_img(node_name, kind, file_path, colorspace):
        uv_node = _get_mtlx_uv()
        img = UsdShade.Shader.Define(stage, mat_path.AppendChild(node_name))
        img.CreateIdAttr(f'ND_image_{kind}')
        f_in = img.CreateInput('file', Sdf.ValueTypeNames.Asset)
        f_in.Set(Sdf.AssetPath(file_path))
        f_in.GetAttr().SetColorSpace(colorspace)
        img.CreateInput('texcoord', Sdf.ValueTypeNames.Float2).ConnectToSource(uv_node.ConnectableAPI(), 'out')
        out_type = {
            'color3': Sdf.ValueTypeNames.Color3f,
            'color4': Sdf.ValueTypeNames.Color4f,
            'vector3': Sdf.ValueTypeNames.Float3,
            'float': Sdf.ValueTypeNames.Float,
        }[kind]
        img.CreateOutput('out', out_type)
        return img

    if albedo_tex and os.path.isfile(albedo_tex):
        if albedo_alpha_cutout and not opacity_tex:
            img4 = _mtlx_img('Image_base_color4', 'color4', albedo_tex, 'srgb_texture')
            conv3 = UsdShade.Shader.Define(stage, mat_path.AppendChild('BaseColorRGB'))
            conv3.CreateIdAttr('ND_convert_color4_color3')
            conv3.CreateInput('in', Sdf.ValueTypeNames.Color4f).ConnectToSource(img4.ConnectableAPI(), 'out')
            conv3.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
            surface.GetInput('base_color').ConnectToSource(conv3.ConnectableAPI(), 'out')

            ext_a = UsdShade.Shader.Define(stage, mat_path.AppendChild('BaseColorAlpha'))
            ext_a.CreateIdAttr('ND_extract_color4')
            ext_a.CreateInput('in', Sdf.ValueTypeNames.Color4f).ConnectToSource(img4.ConnectableAPI(), 'out')
            ext_a.CreateInput('index', Sdf.ValueTypeNames.Int).Set(3)
            ext_a.CreateOutput('out', Sdf.ValueTypeNames.Float)
            op_c = UsdShade.Shader.Define(stage, mat_path.AppendChild('AlphaToColor3'))
            op_c.CreateIdAttr('ND_convert_float_color3')
            op_c.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(ext_a.ConnectableAPI(), 'out')
            op_c.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
            surface.GetInput('opacity').ConnectToSource(op_c.ConnectableAPI(), 'out')
        else:
            img_c = _mtlx_img('Image_base_color', 'color3', albedo_tex, 'srgb_texture')
            surface.GetInput('base_color').ConnectToSource(img_c.ConnectableAPI(), 'out')
            if effective_sss > 0.0 and not translucency_tex:
                surface.GetInput('subsurface_color').ConnectToSource(img_c.ConnectableAPI(), 'out')

    if translucency_tex and os.path.isfile(translucency_tex):
        img_tr = _mtlx_img('Image_translucency', 'color3', translucency_tex, 'srgb_texture')
        surface.GetInput('subsurface_color').ConnectToSource(img_tr.ConnectableAPI(), 'out')

    packed_mr = gltf_mr_tex if (gltf_mr_tex and os.path.isfile(gltf_mr_tex)) else (orm_tex if (orm_tex and os.path.isfile(orm_tex)) else None)
    if packed_mr:
        img_mr = _mtlx_img('Image_packed_mr', 'vector3', packed_mr, 'raw')
        ext_g = UsdShade.Shader.Define(stage, mat_path.AppendChild('Extract_RoughnessG'))
        ext_g.CreateIdAttr('ND_extract_vector3')
        ext_g.CreateInput('in', Sdf.ValueTypeNames.Float3).ConnectToSource(img_mr.ConnectableAPI(), 'out')
        ext_g.CreateInput('index', Sdf.ValueTypeNames.Int).Set(1)
        ext_g.CreateOutput('out', Sdf.ValueTypeNames.Float)
        if abs(float(roughness) - 1.0) > 1e-4 and gltf_mr_tex:
            mul_r = UsdShade.Shader.Define(stage, mat_path.AppendChild('Scale_Roughness'))
            mul_r.CreateIdAttr('ND_multiply_float')
            mul_r.CreateInput('in1', Sdf.ValueTypeNames.Float).ConnectToSource(ext_g.ConnectableAPI(), 'out')
            mul_r.CreateInput('in2', Sdf.ValueTypeNames.Float).Set(float(roughness))
            mul_r.CreateOutput('out', Sdf.ValueTypeNames.Float)
            surface.GetInput('specular_roughness').ConnectToSource(mul_r.ConnectableAPI(), 'out')
        else:
            surface.GetInput('specular_roughness').ConnectToSource(ext_g.ConnectableAPI(), 'out')

        ext_b = UsdShade.Shader.Define(stage, mat_path.AppendChild('Extract_MetallicB'))
        ext_b.CreateIdAttr('ND_extract_vector3')
        ext_b.CreateInput('in', Sdf.ValueTypeNames.Float3).ConnectToSource(img_mr.ConnectableAPI(), 'out')
        ext_b.CreateInput('index', Sdf.ValueTypeNames.Int).Set(2)
        ext_b.CreateOutput('out', Sdf.ValueTypeNames.Float)
        if abs(float(metalness) - 1.0) > 1e-4 and gltf_mr_tex:
            mul_m = UsdShade.Shader.Define(stage, mat_path.AppendChild('Scale_Metalness'))
            mul_m.CreateIdAttr('ND_multiply_float')
            mul_m.CreateInput('in1', Sdf.ValueTypeNames.Float).ConnectToSource(ext_b.ConnectableAPI(), 'out')
            mul_m.CreateInput('in2', Sdf.ValueTypeNames.Float).Set(float(metalness))
            mul_m.CreateOutput('out', Sdf.ValueTypeNames.Float)
            surface.GetInput('metalness').ConnectToSource(mul_m.ConnectableAPI(), 'out')
        else:
            surface.GetInput('metalness').ConnectToSource(ext_b.ConnectableAPI(), 'out')
    else:
        if roughness_tex and os.path.isfile(roughness_tex):
            img_r = _mtlx_img('Image_roughness', 'float', roughness_tex, 'raw')
            surface.GetInput('specular_roughness').ConnectToSource(img_r.ConnectableAPI(), 'out')
        elif gloss_tex and os.path.isfile(gloss_tex):
            img_g = _mtlx_img('Image_gloss', 'float', gloss_tex, 'raw')
            inv_g = UsdShade.Shader.Define(stage, mat_path.AppendChild('Invert_Gloss'))
            inv_g.CreateIdAttr('ND_subtract_float')
            inv_g.CreateInput('in1', Sdf.ValueTypeNames.Float).Set(1.0)
            inv_g.CreateInput('in2', Sdf.ValueTypeNames.Float).ConnectToSource(img_g.ConnectableAPI(), 'out')
            inv_g.CreateOutput('out', Sdf.ValueTypeNames.Float)
            surface.GetInput('specular_roughness').ConnectToSource(inv_g.ConnectableAPI(), 'out')

        if metalness_tex and os.path.isfile(metalness_tex):
            img_m = _mtlx_img('Image_metalness', 'float', metalness_tex, 'raw')
            surface.GetInput('metalness').ConnectToSource(img_m.ConnectableAPI(), 'out')
        elif specular_tex and os.path.isfile(specular_tex):
            img_s = _mtlx_img('Image_specular', 'color3', specular_tex, 'srgb_texture')
            surface.CreateInput('specular_color', Sdf.ValueTypeNames.Color3f).ConnectToSource(img_s.ConnectableAPI(), 'out')

    if opacity_tex and os.path.isfile(opacity_tex):
        img_op = _mtlx_img('Image_opacity', 'float', opacity_tex, 'raw')
        op_c3 = UsdShade.Shader.Define(stage, mat_path.AppendChild('Opacity_FloatToColor3'))
        op_c3.CreateIdAttr('ND_convert_float_color3')
        op_c3.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(img_op.ConnectableAPI(), 'out')
        op_c3.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
        surface.GetInput('opacity').ConnectToSource(op_c3.ConnectableAPI(), 'out')

    if emissive_tex and os.path.isfile(emissive_tex):
        img_em = _mtlx_img('Image_emissive', 'color3', emissive_tex, 'srgb_texture')
        surface.GetInput('emission_color').ConnectToSource(img_em.ConnectableAPI(), 'out')

    if normal_tex and os.path.isfile(normal_tex):
        img_n = _mtlx_img('Image_normal', 'vector3', normal_tex, 'raw')
        norm_source = (img_n.ConnectableAPI(), 'out')
        if str(normal_convention).lower() == 'directx':
            # Flip DirectX (-Y) green channel: out = vec3(in.x, 1.0 - in.y, in.z)
            mul = UsdShade.Shader.Define(stage, mat_path.AppendChild('FlipNormalY_Mul'))
            mul.CreateIdAttr('ND_multiply_vector3')
            mul.CreateInput('in1', Sdf.ValueTypeNames.Float3).ConnectToSource(img_n.ConnectableAPI(), 'out')
            mul.CreateInput('in2', Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(1.0, -1.0, 1.0))
            mul.CreateOutput('out', Sdf.ValueTypeNames.Float3)
            add = UsdShade.Shader.Define(stage, mat_path.AppendChild('FlipNormalY_Add'))
            add.CreateIdAttr('ND_add_vector3')
            add.CreateInput('in1', Sdf.ValueTypeNames.Float3).ConnectToSource(mul.ConnectableAPI(), 'out')
            add.CreateInput('in2', Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(0.0, 1.0, 0.0))
            add.CreateOutput('out', Sdf.ValueTypeNames.Float3)
            norm_source = (add.ConnectableAPI(), 'out')
        nmap = UsdShade.Shader.Define(stage, mat_path.AppendChild('NormalMap'))
        nmap.CreateIdAttr('ND_normalmap_float')
        nmap.CreateInput('in', Sdf.ValueTypeNames.Float3).ConnectToSource(*norm_source)
        nmap.CreateInput('scale', Sdf.ValueTypeNames.Float).Set(float(normal_scale))
        nmap.CreateOutput('out', Sdf.ValueTypeNames.Float3)
        surface.CreateInput('normal', Sdf.ValueTypeNames.Float3).ConnectToSource(nmap.ConnectableAPI(), 'out')
    elif bump_tex and os.path.isfile(bump_tex):
        img_b = _mtlx_img('Image_bump', 'float', bump_tex, 'raw')
        bump = UsdShade.Shader.Define(stage, mat_path.AppendChild('Bump'))
        bump.CreateIdAttr('ND_bump_vector3')
        bump.CreateInput('height', Sdf.ValueTypeNames.Float).ConnectToSource(img_b.ConnectableAPI(), 'out')
        bump.CreateInput('scale', Sdf.ValueTypeNames.Float).Set(0.005)
        bump.CreateOutput('out', Sdf.ValueTypeNames.Float3)
        surface.CreateInput('normal', Sdf.ValueTypeNames.Float3).ConnectToSource(bump.ConnectableAPI(), 'out')

    return mat


def bind_material_both_purposes(prim, material, stronger=False):
    from pxr import UsdShade
    api = UsdShade.MaterialBindingAPI.Apply(prim)
    strength = UsdShade.Tokens.strongerThanDescendants if stronger else UsdShade.Tokens.fallbackStrength
    api.Bind(material, bindingStrength=strength)
    for purpose in (UsdShade.Tokens.full, UsdShade.Tokens.preview):
        api.Bind(material, bindingStrength=strength, materialPurpose=purpose)


# ---------------------------------------------------------------------------
# 1. Native FBX (Binary 6xxx/7xxx + ASCII) -> OpenUSD In-Memory Stage Builder
# ---------------------------------------------------------------------------

def _parse_fbx_binary(data):
    if len(data) < 27 or not data.startswith(b'Kaydara FBX Binary'):
        raise ValueError('Invalid FBX binary header')
    import numpy as np

    version = struct.unpack_from('<I', data, 23)[0]
    is64 = version >= 7500
    hdr_fmt = '<QQQB' if is64 else '<IIIB'
    hdr_sz = struct.calcsize(hdr_fmt)
    null_sz = 25 if is64 else 13
    total_len = len(data)

    def _read_prop(pos):
        if pos >= total_len:
            raise ValueError('Truncated FBX property')
        tc = chr(data[pos])
        pos += 1
        if tc == 'Y':
            return struct.unpack_from('<h', data, pos)[0], pos + 2
        if tc == 'C':
            return struct.unpack_from('<B', data, pos)[0], pos + 1
        if tc == 'I':
            return struct.unpack_from('<i', data, pos)[0], pos + 4
        if tc == 'F':
            return struct.unpack_from('<f', data, pos)[0], pos + 4
        if tc == 'D':
            return struct.unpack_from('<d', data, pos)[0], pos + 8
        if tc == 'L':
            return struct.unpack_from('<q', data, pos)[0], pos + 8
        if tc in ('S', 'R'):
            ln = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            raw = data[pos:pos + ln]
            if tc == 'S':
                return raw.decode('utf-8', errors='replace'), pos + ln
            return bytes(raw), pos + ln
        if tc in ('f', 'd', 'l', 'i', 'b'):
            arr_len, enc, comp_len = struct.unpack_from('<III', data, pos)
            pos += 12
            buf = memoryview(data)[pos:pos + comp_len]
            if enc == 1:
                raw_bytes = zlib.decompress(buf)
            elif enc == 0:
                raw_bytes = buf
            else:
                raise ValueError(f'Unsupported FBX array encoding: {enc}')
            dt = {'f': '<f4', 'd': '<f8', 'l': '<i8', 'i': '<i4', 'b': '<u1'}[tc]
            arr = np.frombuffer(raw_bytes, dtype=dt, count=arr_len)
            return arr, pos + comp_len
        raise ValueError(f'Unsupported FBX property type code: {tc!r}')

    def _read_nodes(pos, limit):
        nodes = []
        while pos + hdr_sz <= limit:
            end_off, n_props, prop_len, name_len = struct.unpack_from(hdr_fmt, data, pos)
            if end_off == 0:
                break
            if end_off > total_len or end_off <= pos:
                break
            p = pos + hdr_sz
            name = data[p:p + name_len].decode('utf-8', errors='replace')
            p += name_len
            props = []
            prop_end = p + prop_len
            for _ in range(n_props):
                val, p = _read_prop(p)
                props.append(val)
            p = max(p, prop_end)
            children = []
            if p + null_sz <= end_off:
                children = _read_nodes(p, end_off)
            elif p < end_off:
                children = _read_nodes(p, end_off)
            nodes.append((name, props, children))
            pos = end_off
        return nodes

    return _read_nodes(27, total_len)


def _parse_fbx_ascii(text):
    """Lightweight tokenizer/parser for ASCII FBX 6.x / 7.x files."""
    import numpy as np

    token_re = re.compile(
        r';[^\n]*|"(?:[^"\\]|\\.)*"|\*\d+|\{|\}|,|:|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[A-Za-z_][A-Za-z0-9_.-]*'
    )
    tokens = [t for t in token_re.findall(text) if not t.startswith(';')]
    n_tok = len(tokens)
    idx = 0

    def _parse_val(tok):
        if tok.startswith('"') and tok.endswith('"'):
            return tok[1:-1]
        if any(c in tok for c in ('.', 'e', 'E')):
            try:
                return float(tok)
            except ValueError:
                return tok
        try:
            return int(tok)
        except ValueError:
            return tok

    def _read_block(stop_on_rbrace=False):
        nonlocal idx
        nodes = []
        while idx < n_tok:
            tok = tokens[idx]
            if tok == '}':
                if stop_on_rbrace:
                    idx += 1
                    break
                idx += 1
                continue
            if idx + 1 < n_tok and tokens[idx + 1] == ':':
                name = tok
                idx += 2
                props = []
                # FBX 7.x array header: *N { a: ... }
                if idx < n_tok and tokens[idx].startswith('*') and idx + 1 < n_tok and tokens[idx + 1] == '{':
                    idx += 2  # skip *N {
                    if idx + 1 < n_tok and tokens[idx] == 'a' and tokens[idx + 1] == ':':
                        idx += 2
                    nums = []
                    is_float = False
                    while idx < n_tok and tokens[idx] != '}':
                        t = tokens[idx]
                        idx += 1
                        if t == ',':
                            continue
                        if '.' in t or 'e' in t or 'E' in t:
                            is_float = True
                        nums.append(t)
                    if idx < n_tok and tokens[idx] == '}':
                        idx += 1
                    arr = np.array(nums, dtype=np.float64 if is_float else np.int64)
                    nodes.append((name, [arr], []))
                    continue

                while idx < n_tok and tokens[idx] != '{':
                    # Check if next token starts a new sibling `OtherName:`
                    if idx + 1 < n_tok and tokens[idx + 1] == ':':
                        break
                    if tokens[idx] == '}':
                        break
                    if tokens[idx] == ',':
                        idx += 1
                        continue
                    props.append(_parse_val(tokens[idx]))
                    idx += 1

                # Convert long homogeneous numeric property lists (FBX 6.x inline arrays)
                if len(props) > 4 and name in ('Vertices', 'PolygonVertexIndex', 'Normals', 'UV', 'UVIndex', 'Colors', 'ColorIndex', 'Materials') and all(isinstance(x, (int, float)) for x in props):
                    has_f = any(isinstance(x, float) for x in props)
                    props = [np.array(props, dtype=np.float64 if has_f else np.int32)]

                children = []
                if idx < n_tok and tokens[idx] == '{':
                    idx += 1
                    children = _read_block(stop_on_rbrace=True)
                nodes.append((name, props, children))
            else:
                idx += 1
        return nodes

    return _read_block(stop_on_rbrace=False)


def _fbx_clean_name(raw_name, fallback='Object'):
    if not isinstance(raw_name, str):
        return fallback
    base = raw_name.split('\x00', 1)[0].strip()
    if '::' in base:
        base = base.split('::')[-1].strip()
    return base or fallback


def _fbx_props70_dict(children):
    out = {}
    for cname, _, cchildren in children:
        if cname in ('Properties70', 'Properties60'):
            for pname, pprops, _ in cchildren:
                if pname in ('P', 'Property') and len(pprops) >= 5:
                    key = str(pprops[0])
                    vals = pprops[4:]
                    out[key] = vals[0] if len(vals) == 1 else vals
    return out


def _fbx_euler_matrix(rx_deg, ry_deg, rz_deg, rot_order=0):
    from pxr import Gf
    rx = Gf.Rotation(Gf.Vec3d(1, 0, 0), float(rx_deg))
    ry = Gf.Rotation(Gf.Vec3d(0, 1, 0), float(ry_deg))
    rz = Gf.Rotation(Gf.Vec3d(0, 0, 1), float(rz_deg))
    orders = {
        0: (rx, ry, rz),  # XYZ
        1: (rx, rz, ry),  # XZY
        2: (ry, rz, rx),  # YZX
        3: (ry, rx, rz),  # YXZ
        4: (rz, rx, ry),  # ZXY
        5: (rz, ry, rx),  # ZYX
    }
    r1, r2, r3 = orders.get(int(rot_order), (rx, ry, rz))
    m1 = Gf.Matrix4d(1.0); m1.SetRotate(r1)
    m2 = Gf.Matrix4d(1.0); m2.SetRotate(r2)
    m3 = Gf.Matrix4d(1.0); m3.SetRotate(r3)
    return m1 * m2 * m3


def _fbx_model_matrices(p70):
    from pxr import Gf

    def _vec3(key, default=(0.0, 0.0, 0.0)):
        v = p70.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return (float(v[0]), float(v[1]), float(v[2]))
        return default

    t = _vec3('Lcl Translation', (0.0, 0.0, 0.0))
    r = _vec3('Lcl Rotation', (0.0, 0.0, 0.0))
    s = _vec3('Lcl Scaling', (1.0, 1.0, 1.0))
    pre_r = _vec3('PreRotation', (0.0, 0.0, 0.0))
    post_r = _vec3('PostRotation', (0.0, 0.0, 0.0))
    rot_off = _vec3('RotationOffset', (0.0, 0.0, 0.0))
    rot_piv = _vec3('RotationPivot', (0.0, 0.0, 0.0))
    scl_off = _vec3('ScalingOffset', (0.0, 0.0, 0.0))
    scl_piv = _vec3('ScalingPivot', (0.0, 0.0, 0.0))
    rot_order = p70.get('RotationOrder', 0)
    if isinstance(rot_order, (list, tuple)):
        rot_order = rot_order[0] if rot_order else 0

    def _tmat(xyz):
        m = Gf.Matrix4d(1.0)
        m.SetTranslate(Gf.Vec3d(*xyz))
        return m

    def _smat(xyz):
        m = Gf.Matrix4d(1.0)
        m.SetScale(Gf.Vec3d(*xyz))
        return m

    # USD uses row-vector convention (v' = v * M):
    # FBX column-vector M = T * Roff * Rp * Rpre * R * Rpost^-1 * Rp^-1 * Soff * Sp * S * Sp^-1
    # Row-vector equivalent multiplies in reverse order:
    local_m = (
        _tmat((-scl_piv[0], -scl_piv[1], -scl_piv[2]))
        * _smat(s)
        * _tmat(scl_piv)
        * _tmat(scl_off)
        * _tmat((-rot_piv[0], -rot_piv[1], -rot_piv[2]))
        * _fbx_euler_matrix(*post_r, 0).GetInverse()
        * _fbx_euler_matrix(*r, rot_order)
        * _fbx_euler_matrix(*pre_r, 0)
        * _tmat(rot_piv)
        * _tmat(rot_off)
        * _tmat(t)
    )

    gt = _vec3('GeometricTranslation', (0.0, 0.0, 0.0))
    gr = _vec3('GeometricRotation', (0.0, 0.0, 0.0))
    gs = _vec3('GeometricScaling', (1.0, 1.0, 1.0))
    geom_m = _smat(gs) * _fbx_euler_matrix(*gr, 0) * _tmat(gt)
    return local_m, geom_m


def open_fbx_source_stage(path, *, auto_companion_pbr=True):
    """Parse a binary or ASCII .fbx file into an in-memory OpenUSD stage."""
    import numpy as np
    from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, Vt

    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise ValueError(f'FBX file does not exist: {path}')
    base_dir = os.path.dirname(abs_path)

    with open(abs_path, 'rb') as f:
        raw = f.read()
    if raw.startswith(b'Kaydara FBX Binary'):
        tree = _parse_fbx_binary(raw)
    else:
        tree = _parse_fbx_ascii(raw.decode('utf-8', errors='replace'))

    # 1. GlobalSettings: units and up-axis
    up_axis_int = 1
    up_sign = 1
    unit_scale_cm = 1.0
    for nname, _, nchildren in tree:
        if nname == 'GlobalSettings':
            gs = _fbx_props70_dict(nchildren)
            if 'UpAxis' in gs and not isinstance(gs['UpAxis'], (list, tuple)):
                up_axis_int = int(gs['UpAxis'])
            if 'UpAxisSign' in gs and not isinstance(gs['UpAxisSign'], (list, tuple)):
                up_sign = int(gs['UpAxisSign'])
            if 'UnitScaleFactor' in gs and not isinstance(gs['UnitScaleFactor'], (list, tuple)):
                v = float(gs['UnitScaleFactor'])
                if math.isfinite(v) and v > 0:
                    unit_scale_cm = v

    # FBX UnitScaleFactor is centimeters per FBX unit (1.0 -> 0.01 m/unit, 100.0 -> 1.0 m/unit)
    meters_per_unit = 0.01 * unit_scale_cm

    # 2. Objects & Connections
    geometries = {}
    models = {}
    materials = {}
    textures = {}
    videos = {}
    legacy_id = -1

    def _obj_id_and_name(props, default_label):
        nonlocal legacy_id
        if len(props) >= 2 and isinstance(props[0], int):
            return props[0], _fbx_clean_name(props[1], default_label)
        if len(props) >= 1 and isinstance(props[0], str):
            oid = _fbx_clean_name(props[0], f'{default_label}_{abs(legacy_id)}')
            legacy_id -= 1
            return oid, oid
        oid = legacy_id
        legacy_id -= 1
        return oid, default_label

    for nname, _, nchildren in tree:
        if nname == 'Objects':
            for oname, oprops, ochildren in nchildren:
                if oname == 'Geometry':
                    kind = str(oprops[2]) if len(oprops) >= 3 else 'Mesh'
                    if kind not in ('Mesh', ''):
                        continue
                    oid, clean = _obj_id_and_name(oprops, 'Mesh')
                    geometries[oid] = (clean, ochildren)
                elif oname == 'Model':
                    kind = str(oprops[2]) if len(oprops) >= 3 else 'Mesh'
                    oid, clean = _obj_id_and_name(oprops, 'Model')
                    models[oid] = (clean, kind, _fbx_props70_dict(ochildren))
                elif oname == 'Material':
                    oid, clean = _obj_id_and_name(oprops, 'Material')
                    materials[oid] = (clean, _fbx_props70_dict(ochildren))
                elif oname == 'Texture':
                    oid, clean = _obj_id_and_name(oprops, 'Texture')
                    rel_fn = ''
                    abs_fn = ''
                    for cn, cp, _ in ochildren:
                        if cn == 'RelativeFilename' and cp:
                            rel_fn = str(cp[0])
                        elif cn == 'FileName' and cp:
                            abs_fn = str(cp[0])
                    textures[oid] = (clean, rel_fn, abs_fn)
                elif oname == 'Video':
                    oid, clean = _obj_id_and_name(oprops, 'Video')
                    rel_fn = ''
                    abs_fn = ''
                    for cn, cp, _ in ochildren:
                        if cn == 'RelativeFilename' and cp:
                            rel_fn = str(cp[0])
                        elif cn == 'FileName' and cp:
                            abs_fn = str(cp[0])
                    videos[oid] = (clean, rel_fn, abs_fn)

    if not geometries:
        raise ValueError(f'FBX contains no polygon meshes: {path}')

    model_parent = {}
    model_children = {}
    model_geoms = {}
    model_mats = {}
    mat_tex_props = {}
    tex_video = {}

    for nname, _, nchildren in tree:
        if nname == 'Connections':
            for cname, cprops, _ in nchildren:
                if cname not in ('C', 'Connect') or len(cprops) < 3:
                    continue
                ctype = str(cprops[0])
                src = cprops[1]
                dst = cprops[2]
                if isinstance(src, str):
                    src = _fbx_clean_name(src, src)
                if isinstance(dst, str):
                    dst = _fbx_clean_name(dst, dst)
                prop_name = str(cprops[3]) if ctype == 'OP' and len(cprops) >= 4 else ''

                if src in models and (dst == 0 or dst == 'Scene' or dst in models):
                    parent_key = 0 if dst in (0, 'Scene') else dst
                    model_parent[src] = parent_key
                    model_children.setdefault(parent_key, []).append(src)
                elif src in geometries and dst in models:
                    model_geoms.setdefault(dst, []).append(src)
                elif src in materials and dst in models:
                    model_mats.setdefault(dst, []).append(src)
                elif src in textures and dst in materials:
                    mat_tex_props.setdefault(dst, {})[prop_name or 'DiffuseColor'] = src
                elif src in videos and dst in textures:
                    tex_video[dst] = src

    stage = Usd.Stage.CreateInMemory('source-fbx.usda')
    if up_axis_int == 2:
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    else:
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, meters_per_unit)

    root = UsdGeom.Xform.Define(stage, '/FBX')
    stage.SetDefaultPrim(root.GetPrim())
    if up_axis_int == 0:
        # Rare X-up FBX: rotate X-up into Y-up
        rot_m = Gf.Matrix4d(1.0)
        rot_m.SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), 90.0 * (1 if up_sign >= 0 else -1)))
        root.AddTransformOp().Set(rot_m)
    elif up_sign < 0:
        rot_m = Gf.Matrix4d(1.0)
        rot_m.SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), 180.0))
        root.AddTransformOp().Set(rot_m)

    # Resolve FBX textures
    def _resolve_fbx_tex(tex_id):
        if tex_id not in textures:
            return None
        _, rel_fn, abs_fn = textures[tex_id]
        vid_id = tex_video.get(tex_id)
        candidates = []
        if rel_fn:
            candidates.append(os.path.join(base_dir, rel_fn.replace('\\', '/')))
        if vid_id in videos:
            _, vrel, vabs = videos[vid_id]
            if vrel:
                candidates.append(os.path.join(base_dir, vrel.replace('\\', '/')))
            if vabs:
                candidates.append(vabs)
        if abs_fn:
            candidates.append(abs_fn)
            candidates.append(os.path.join(base_dir, os.path.basename(abs_fn.replace('\\', '/'))))
        for cand in candidates:
            norm = os.path.abspath(cand)
            if os.path.isfile(norm):
                return norm
        return None

    usd_materials = {}
    any_textured_material = False
    if materials:
        UsdGeom.Scope.Define(stage, '/FBX/_Materials')
        used_mat_ids = set()
        for mat_id, (mname, mp70) in materials.items():
            mid = Tf.MakeValidIdentifier(mname or 'Material')
            if mid in used_mat_ids:
                mid += '_' + hashlib.sha256(f'{mid}:{mat_id}'.encode()).hexdigest()[:6]
            used_mat_ids.add(mid)

            tlinks = mat_tex_props.get(mat_id, {})
            diff_tex = None
            norm_tex = None
            bump_tex = None
            rough_tex = None
            metal_tex = None
            opac_tex = None
            emis_tex = None
            for pkey, tid in tlinks.items():
                pk = pkey.lower()
                resolved = _resolve_fbx_tex(tid)
                if not resolved:
                    continue
                any_textured_material = True
                if 'diffuse' in pk or 'basecolor' in pk or 'albedo' in pk:
                    diff_tex = resolved
                elif 'normal' in pk:
                    norm_tex = resolved
                elif 'bump' in pk:
                    bump_tex = resolved
                elif 'rough' in pk or 'shininess' in pk:
                    rough_tex = resolved
                elif 'metal' in pk:
                    metal_tex = resolved
                elif 'transparen' in pk or 'opacity' in pk:
                    opac_tex = resolved
                elif 'emissive' in pk:
                    emis_tex = resolved

            kd = mp70.get('DiffuseColor')
            base_c = (
                (float(kd[0]), float(kd[1]), float(kd[2]))
                if isinstance(kd, (list, tuple)) and len(kd) >= 3
                else (0.8, 0.8, 0.8)
            )
            shininess = mp70.get('Shininess') or mp70.get('ShininessExponent')
            if isinstance(shininess, (int, float)) and float(shininess) > 0:
                rough_val = max(0.05, min(1.0, math.sqrt(2.0 / (float(shininess) + 2.0))))
            else:
                rough_val = 0.5

            usd_materials[mat_id] = author_pbr_material(
                stage,
                f'/FBX/_Materials/{mid}',
                base_color=base_c,
                roughness=rough_val,
                albedo_tex=diff_tex,
                roughness_tex=rough_tex,
                metalness_tex=metal_tex,
                normal_tex=norm_tex,
                bump_tex=bump_tex,
                opacity_tex=opac_tex,
                emissive_tex=emis_tex,
            )

    def _extract_layer_array(gchildren, elem_name, val_node_name, idx_node_name, stride, n_verts, n_corners, n_polys):
        for cn, _, cch in gchildren:
            if cn != elem_name:
                continue
            mapping = 'ByPolygonVertex'
            ref_type = 'Direct'
            raw_vals = None
            raw_idx = None
            for sn, sp, _ in cch:
                if sn == 'MappingInformationType' and sp:
                    mapping = str(sp[0])
                elif sn == 'ReferenceInformationType' and sp:
                    ref_type = str(sp[0])
                elif sn == val_node_name and sp:
                    raw_vals = np.asarray(sp[0], dtype=np.float32)
                elif sn == idx_node_name and sp:
                    raw_idx = np.asarray(sp[0], dtype=np.int32)
            if raw_vals is None or raw_vals.size < stride:
                continue
            vals = raw_vals[: (raw_vals.size // stride) * stride].reshape(-1, stride)
            if ref_type in ('IndexToDirect', 'Index') and raw_idx is not None and raw_idx.size > 0:
                safe_idx = np.clip(raw_idx, 0, len(vals) - 1)
                expanded = vals[safe_idx]
            else:
                expanded = vals

            ml = mapping.lower()
            if 'polygonvertex' in ml and len(expanded) == n_corners:
                return expanded, 'faceVarying'
            if ('vertice' in ml or 'controlpoint' in ml or 'vertex' in ml) and len(expanded) == n_verts:
                return expanded, 'vertex'
            if 'polygon' in ml and len(expanded) == n_polys:
                return expanded, 'uniform'
            if 'allsame' in ml and len(expanded) >= 1:
                return expanded[:1], 'constant'
        return None, None

    def _author_fbx_mesh(parent_path, mesh_label, geom_id, assigned_mats, geom_m):
        _, gchildren = geometries[geom_id]
        raw_verts = None
        raw_pvi = None
        raw_mat_indices = None
        for cn, cp, cch in gchildren:
            if cn == 'Vertices' and cp:
                raw_verts = np.asarray(cp[0], dtype=np.float32)
            elif cn == 'PolygonVertexIndex' and cp:
                raw_pvi = np.asarray(cp[0], dtype=np.int32)
            elif cn == 'LayerElementMaterial':
                for sn, sp, _ in cch:
                    if sn == 'Materials' and sp:
                        raw_mat_indices = np.asarray(sp[0], dtype=np.int32)

        if raw_verts is None or raw_pvi is None or raw_verts.size < 9 or raw_pvi.size < 3:
            return None

        pts = raw_verts[: (raw_verts.size // 3) * 3].reshape(-1, 3)
        if geom_m != Gf.Matrix4d(1.0):
            gm_np = np.array([[geom_m[r][c] for c in range(4)] for r in range(4)], dtype=np.float32)
            pts = pts @ gm_np[:3, :3] + gm_np[3, :3]

        neg_mask = raw_pvi < 0
        face_ends = np.flatnonzero(neg_mask)
        if face_ends.size == 0:
            return None
        starts = np.empty_like(face_ends)
        starts[0] = 0
        if face_ends.size > 1:
            starts[1:] = face_ends[:-1] + 1
        counts = (face_ends - starts + 1).astype(np.int32)
        indices = np.where(neg_mask, ~raw_pvi, raw_pvi).astype(np.int32)
        used_len = int(face_ends[-1]) + 1
        indices = np.clip(indices[:used_len], 0, len(pts) - 1)

        n_verts = len(pts)
        n_corners = len(indices)
        n_polys = len(counts)

        mesh_path = parent_path.AppendChild(Tf.MakeValidIdentifier(mesh_label))
        while stage.GetPrimAtPath(mesh_path):
            mesh_path = parent_path.AppendChild(mesh_path.name + '_')
        mesh = UsdGeom.Mesh.Define(stage, mesh_path)
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(pts, dtype=np.float32)))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.ascontiguousarray(counts, dtype=np.int32)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(np.ascontiguousarray(indices, dtype=np.int32)))
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr(True)
        mn = pts.min(axis=0)
        mx = pts.max(axis=0)
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(float(mn[0]), float(mn[1]), float(mn[2])), Gf.Vec3f(float(mx[0]), float(mx[1]), float(mx[2]))]))

        normals, norm_interp = _extract_layer_array(gchildren, 'LayerElementNormal', 'Normals', 'NormalsIndex', 3, n_verts, n_corners, n_polys)
        if normals is not None and norm_interp in ('faceVarying', 'vertex', 'uniform'):
            if geom_m != Gf.Matrix4d(1.0):
                rot3 = np.array([[geom_m[r][c] for c in range(3)] for r in range(3)], dtype=np.float32)
                normals = normals @ rot3
                nlen = np.linalg.norm(normals, axis=1, keepdims=True)
                normals = np.where(nlen > 1e-8, normals / np.maximum(nlen, 1e-8), normals)
            mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(normals, dtype=np.float32)))
            interp_tok = {'faceVarying': UsdGeom.Tokens.faceVarying, 'vertex': UsdGeom.Tokens.vertex, 'uniform': UsdGeom.Tokens.uniform}[norm_interp]
            mesh.SetNormalsInterpolation(interp_tok)

        uvs, uv_interp = _extract_layer_array(gchildren, 'LayerElementUV', 'UV', 'UVIndex', 2, n_verts, n_corners, n_polys)
        if uvs is not None and uv_interp in ('faceVarying', 'vertex'):
            pv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'st',
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying if uv_interp == 'faceVarying' else UsdGeom.Tokens.vertex,
            )
            pv.Set(Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(uvs[:, :2], dtype=np.float32)))

        colors, col_interp = _extract_layer_array(gchildren, 'LayerElementColor', 'Colors', 'ColorIndex', 4, n_verts, n_corners, n_polys)
        if colors is not None and col_interp in ('faceVarying', 'vertex'):
            pv_c = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'displayColor',
                Sdf.ValueTypeNames.Color3fArray,
                UsdGeom.Tokens.faceVarying if col_interp == 'faceVarying' else UsdGeom.Tokens.vertex,
            )
            pv_c.Set(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(colors[:, :3], dtype=np.float32)))

        valid_mats = [usd_materials[m] for m in assigned_mats if m in usd_materials]
        if len(valid_mats) == 1:
            bind_material_both_purposes(mesh.GetPrim(), valid_mats[0])
        elif len(valid_mats) > 1 and raw_mat_indices is not None and len(raw_mat_indices) == n_polys:
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
            for slot_idx, mat_obj in enumerate(valid_mats):
                face_ids = np.flatnonzero(raw_mat_indices == slot_idx).astype(np.int32)
                if face_ids.size == 0:
                    continue
                sub = UsdGeom.Subset.Define(stage, mesh_path.AppendChild(f'mat_{slot_idx}'))
                sub.CreateElementTypeAttr(UsdGeom.Tokens.face)
                sub.CreateFamilyNameAttr(UsdShade.Tokens.materialBind)
                sub.CreateIndicesAttr(Vt.IntArray.FromNumpy(face_ids))
                bind_material_both_purposes(sub.GetPrim(), mat_obj)
        elif valid_mats:
            bind_material_both_purposes(mesh.GetPrim(), valid_mats[0])
        return mesh

    emitted_geoms = set()
    visited_models = set()

    def _walk_model(mod_id, parent_usd_path):
        if mod_id in visited_models or mod_id not in models:
            return
        visited_models.add(mod_id)
        mname, _mkind, mp70 = models[mod_id]
        local_m, geom_m = _fbx_model_matrices(mp70)
        g_ids = model_geoms.get(mod_id, [])
        c_ids = model_children.get(mod_id, [])
        m_ids = model_mats.get(mod_id, [])

        # Single leaf mesh with identity local transform -> author Mesh directly without extra wrapper
        if len(g_ids) == 1 and not c_ids and local_m == Gf.Matrix4d(1.0):
            emitted_geoms.add(g_ids[0])
            _author_fbx_mesh(parent_usd_path, mname, g_ids[0], m_ids, geom_m)
            return

        xf_path = parent_usd_path.AppendChild(Tf.MakeValidIdentifier(mname))
        while stage.GetPrimAtPath(xf_path):
            xf_path = parent_usd_path.AppendChild(xf_path.name + '_')
        xf = UsdGeom.Xform.Define(stage, xf_path)
        if local_m != Gf.Matrix4d(1.0):
            xf.AddTransformOp().Set(local_m)
        for i, gid in enumerate(g_ids):
            emitted_geoms.add(gid)
            gname = geometries[gid][0] if len(g_ids) > 1 else f'{mname}_Shape'
            _author_fbx_mesh(xf_path, gname or f'Shape_{i}', gid, m_ids, geom_m)
        for cid in c_ids:
            _walk_model(cid, xf_path)

    for top_mod in model_children.get(0, []):
        _walk_model(top_mod, root.GetPath())

    for gid, (gname, _) in geometries.items():
        if gid not in emitted_geoms:
            _author_fbx_mesh(root.GetPath(), gname or 'Mesh', gid, [], Gf.Matrix4d(1.0))

    # Auto-discover sibling Megascans / Fab PBR textures if the FBX itself carried no textures
    if auto_companion_pbr and not any_textured_material:
        companion_maps = discover_folder_pbr_maps(base_dir)
        if any(companion_maps.values()):
            UsdGeom.Scope.Define(stage, '/FBX/_Materials')
            auto_mat = _author_discovered_pbr(stage, '/FBX/_Materials/AutoPBR', companion_maps)
            bind_material_both_purposes(root.GetPrim(), auto_mat, stronger=True)

    return stage


# ---------------------------------------------------------------------------
# 2. Native glTF 2.0 (.gltf + .glb) -> OpenUSD In-Memory Stage Builder
# ---------------------------------------------------------------------------

def _load_gltf_container(path):
    abs_path = os.path.abspath(path)
    ext = os.path.splitext(abs_path)[1].lower()
    base_dir = os.path.dirname(abs_path)
    if ext == '.glb':
        with open(abs_path, 'rb') as f:
            raw = f.read()
        if len(raw) < 20:
            raise ValueError(f'Invalid GLB header: {path}')
        magic, version, _total_len = struct.unpack_from('<4sII', raw, 0)
        if magic != b'glTF' or version != 2:
            raise ValueError(f'Unsupported GLB magic/version in {path}')
        pos = 12
        json_doc = None
        bin_chunk = b''
        while pos + 8 <= len(raw):
            chunk_len, chunk_type = struct.unpack_from('<II', raw, pos)
            pos += 8
            payload = raw[pos:pos + chunk_len]
            pos += chunk_len
            if chunk_type == 0x4E4F534A:  # JSON
                json_doc = json.loads(payload.decode('utf-8'))
            elif chunk_type == 0x004E4942:  # BIN\x00
                bin_chunk = payload
        if not isinstance(json_doc, dict):
            raise ValueError(f'GLB contains no JSON chunk: {path}')
        return json_doc, [bin_chunk], base_dir, abs_path

    with open(abs_path, 'r', encoding='utf-8-sig') as f:
        json_doc = json.load(f)
    if not isinstance(json_doc, dict):
        raise ValueError(f'Invalid glTF JSON root: {path}')
    buffers = []
    for buf_def in (json_doc.get('buffers') or []):
        uri = buf_def.get('uri') or ''
        if uri.startswith('data:'):
            _, b64_part = uri.split(',', 1)
            buffers.append(base64.b64decode(b64_part))
        elif uri:
            from urllib.parse import unquote
            buf_fp = os.path.abspath(os.path.join(base_dir, unquote(uri)))
            if not os.path.isfile(buf_fp):
                raise ValueError(f'Missing glTF binary buffer: {buf_fp}')
            with open(buf_fp, 'rb') as bf:
                buffers.append(bf.read())
        else:
            buffers.append(b'')
    return json_doc, buffers, base_dir, abs_path


def _read_gltf_accessor(doc, buffers, acc_idx):
    import numpy as np

    if acc_idx is None:
        return None
    accessors = doc.get('accessors') or []
    buffer_views = doc.get('bufferViews') or []
    if acc_idx < 0 or acc_idx >= len(accessors):
        return None
    acc = accessors[acc_idx]
    comp_type = int(acc.get('componentType', 5126))
    count = int(acc.get('count', 0))
    acc_type = str(acc.get('type', 'SCALAR'))
    n_comp = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}.get(acc_type, 1)
    dt_str, comp_sz = {
        5120: ('<i1', 1),
        5121: ('<u1', 1),
        5122: ('<i2', 2),
        5123: ('<u2', 2),
        5125: ('<u4', 4),
        5126: ('<f4', 4),
    }[comp_type]
    if count <= 0:
        return np.zeros((0, n_comp) if n_comp > 1 else (0,), dtype=np.float32)

    bv_idx = acc.get('bufferView')
    if bv_idx is None:
        arr = np.zeros((count, n_comp), dtype=np.dtype(dt_str))
    else:
        bv = buffer_views[bv_idx]
        buf_data = buffers[int(bv.get('buffer', 0))]
        bv_offset = int(bv.get('byteOffset', 0))
        acc_offset = int(acc.get('byteOffset', 0))
        elem_sz = n_comp * comp_sz
        stride = int(bv.get('byteStride') or elem_sz)
        start = bv_offset + acc_offset
        if stride == elem_sz:
            raw = memoryview(buf_data)[start:start + count * elem_sz]
            arr = np.frombuffer(raw, dtype=dt_str, count=count * n_comp).reshape(count, n_comp)
        else:
            total_span = (count - 1) * stride + elem_sz
            raw = np.frombuffer(memoryview(buf_data)[start:start + total_span], dtype=np.uint8)
            strided = np.lib.stride_tricks.as_strided(raw, shape=(count, elem_sz), strides=(stride, 1))
            arr = np.ascontiguousarray(strided).view(dt_str).reshape(count, n_comp)

    if acc.get('normalized') and comp_type != 5126:
        arr = arr.astype(np.float32)
        if comp_type == 5120:
            arr = np.maximum(arr / 127.0, -1.0)
        elif comp_type == 5121:
            arr = arr / 255.0
        elif comp_type == 5122:
            arr = np.maximum(arr / 32767.0, -1.0)
        elif comp_type == 5123:
            arr = arr / 65535.0
        elif comp_type == 5125:
            arr = arr / 4294967295.0
    return arr if n_comp > 1 else arr.reshape(count)


def _resolve_gltf_texture_path(doc, buffers, tex_info, base_dir, cache_dir):
    if not isinstance(tex_info, dict):
        return None
    tex_idx = tex_info.get('index')
    textures = doc.get('textures') or []
    images = doc.get('images') or []
    buffer_views = doc.get('bufferViews') or []
    if tex_idx is None or tex_idx < 0 or tex_idx >= len(textures):
        return None
    img_idx = textures[tex_idx].get('source')
    if img_idx is None or img_idx < 0 or img_idx >= len(images):
        return None
    img = images[img_idx]
    uri = img.get('uri') or ''
    if uri and not uri.startswith('data:'):
        from urllib.parse import unquote
        ext_fp = os.path.abspath(os.path.join(base_dir, unquote(uri)))
        return ext_fp if os.path.isfile(ext_fp) else None

    # Embedded GLB bufferView or data URI -> extract once to OS temp cache
    raw_bytes = None
    mime = str(img.get('mimeType') or '').lower()
    if uri.startswith('data:'):
        hdr, b64_part = uri.split(',', 1)
        if 'image/png' in hdr:
            mime = 'image/png'
        elif 'image/jpeg' in hdr or 'image/jpg' in hdr:
            mime = 'image/jpeg'
        elif 'image/webp' in hdr:
            mime = 'image/webp'
        raw_bytes = base64.b64decode(b64_part)
    elif img.get('bufferView') is not None:
        bv = buffer_views[int(img['bufferView'])]
        buf = buffers[int(bv.get('buffer', 0))]
        off = int(bv.get('byteOffset', 0))
        ln = int(bv.get('byteLength', 0))
        raw_bytes = bytes(memoryview(buf)[off:off + ln])

    if not raw_bytes:
        return None
    if raw_bytes.startswith(b'\x89PNG'):
        ext = '.png'
    elif raw_bytes.startswith(b'\xff\xd8\xff'):
        ext = '.jpg'
    elif 'webp' in mime:
        ext = '.webp'
    else:
        ext = '.png' if 'png' in mime else '.jpg'

    os.makedirs(cache_dir, exist_ok=True)
    out_fp = os.path.join(cache_dir, f'img_{img_idx}{ext}')
    if not os.path.isfile(out_fp) or os.path.getsize(out_fp) != len(raw_bytes):
        tmp_fp = f'{out_fp}.{os.getpid()}.tmp'
        with open(tmp_fp, 'wb') as out_f:
            out_f.write(raw_bytes)
        os.replace(tmp_fp, out_fp)
    return out_fp


def open_gltf_source_stage(path):
    """Parse a .gltf or .glb 2.0 asset into an in-memory OpenUSD stage."""
    import numpy as np
    from pxr import Gf, Sdf, Tf, Usd, UsdGeom, Vt

    doc, buffers, base_dir, abs_path = _load_gltf_container(path)
    st = os.stat(abs_path)
    cache_key = hashlib.sha256(f'{os.path.normcase(abs_path)}|{st.st_mtime_ns}|{st.st_size}'.encode()).hexdigest()[:20]
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_gltf_cache', cache_key)

    stage = Usd.Stage.CreateInMemory('source-gltf.usda')
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, '/GLTF')
    stage.SetDefaultPrim(root.GetPrim())

    # 1. Materials
    usd_mats = {}
    gltf_mats = doc.get('materials') or []
    if gltf_mats:
        UsdGeom.Scope.Define(stage, '/GLTF/_Materials')
        used_mnames = set()
        for mi, mdef in enumerate(gltf_mats):
            raw_mname = str(mdef.get('name') or f'Material_{mi}')
            mid = Tf.MakeValidIdentifier(raw_mname)
            if mid in used_mnames:
                mid += f'_{mi}'
            used_mnames.add(mid)

            pbr = mdef.get('pbrMetallicRoughness') or {}
            bcf = pbr.get('baseColorFactor') or [1.0, 1.0, 1.0, 1.0]
            base_c = (float(bcf[0]), float(bcf[1]), float(bcf[2]))
            base_a = float(bcf[3]) if len(bcf) >= 4 else 1.0
            rough_f = float(pbr.get('roughnessFactor', 1.0))
            metal_f = float(pbr.get('metallicFactor', 1.0 if pbr.get('metallicRoughnessTexture') else 0.0))
            if 'metallicFactor' in pbr:
                metal_f = float(pbr['metallicFactor'])
            if 'roughnessFactor' not in pbr and not pbr.get('metallicRoughnessTexture'):
                rough_f = 0.5

            albedo_fp = _resolve_gltf_texture_path(doc, buffers, pbr.get('baseColorTexture'), base_dir, cache_dir)
            mr_fp = _resolve_gltf_texture_path(doc, buffers, pbr.get('metallicRoughnessTexture'), base_dir, cache_dir)
            norm_info = mdef.get('normalTexture')
            norm_fp = _resolve_gltf_texture_path(doc, buffers, norm_info, base_dir, cache_dir)
            norm_scale = float(norm_info.get('scale', 1.0)) if isinstance(norm_info, dict) else 1.0
            emis_fp = _resolve_gltf_texture_path(doc, buffers, mdef.get('emissiveTexture'), base_dir, cache_dir)
            ef = mdef.get('emissiveFactor') or [0.0, 0.0, 0.0]
            alpha_mode = str(mdef.get('alphaMode') or 'OPAQUE').upper()
            alpha_cutoff = float(mdef.get('alphaCutoff', 0.5)) if alpha_mode == 'MASK' else None

            usd_mats[mi] = author_pbr_material(
                stage,
                f'/GLTF/_Materials/{mid}',
                base_color=base_c,
                roughness=rough_f,
                metalness=metal_f,
                emissive_color=(float(ef[0]), float(ef[1]), float(ef[2])),
                opacity=base_a if alpha_mode == 'BLEND' else 1.0,
                alpha_cutoff=alpha_cutoff,
                normal_scale=norm_scale,
                albedo_tex=albedo_fp,
                gltf_mr_tex=mr_fp,
                normal_tex=norm_fp,
                emissive_tex=emis_fp,
                albedo_alpha_cutout=(alpha_mode in ('MASK', 'BLEND') and bool(albedo_fp)),
            )

    # 2. Meshes & Nodes
    gltf_meshes = doc.get('meshes') or []
    gltf_nodes = doc.get('nodes') or []
    mesh_authored = [0]

    def _node_matrix(ndef):
        if isinstance(ndef.get('matrix'), list) and len(ndef['matrix']) == 16:
            # glTF stores column-major 4x4, which matches USD row-vector 4x4 directly!
            vals = [float(x) for x in ndef['matrix']]
            return Gf.Matrix4d(
                vals[0], vals[1], vals[2], vals[3],
                vals[4], vals[5], vals[6], vals[7],
                vals[8], vals[9], vals[10], vals[11],
                vals[12], vals[13], vals[14], vals[15],
            )
        m = Gf.Matrix4d(1.0)
        s = ndef.get('scale')
        if isinstance(s, list) and len(s) == 3:
            sm = Gf.Matrix4d(1.0)
            sm.SetScale(Gf.Vec3d(float(s[0]), float(s[1]), float(s[2])))
            m = m * sm
        r = ndef.get('rotation')
        if isinstance(r, list) and len(r) == 4:
            # glTF quaternion is [x, y, z, w]
            quat = Gf.Quatd(float(r[3]), Gf.Vec3d(float(r[0]), float(r[1]), float(r[2])))
            rm = Gf.Matrix4d(1.0)
            rm.SetRotate(quat)
            m = m * rm
        t = ndef.get('translation')
        if isinstance(t, list) and len(t) == 3:
            tm = Gf.Matrix4d(1.0)
            tm.SetTranslate(Gf.Vec3d(float(t[0]), float(t[1]), float(t[2])))
            m = m * tm
        return m

    def _author_primitive(parent_path, prim_label, pdef):
        attrs = pdef.get('attributes') or {}
        if 'POSITION' not in attrs:
            return
        mode = int(pdef.get('mode', 4))
        if mode not in (4, 5, 6):
            return
        pos = np.ascontiguousarray(_read_gltf_accessor(doc, buffers, attrs['POSITION']), dtype=np.float32)
        if pos is None or len(pos) < 3:
            return

        if pdef.get('indices') is not None:
            raw_idx = np.asarray(_read_gltf_accessor(doc, buffers, pdef['indices']), dtype=np.int32).reshape(-1)
        else:
            raw_idx = np.arange(len(pos), dtype=np.int32)

        if mode == 4:
            tri_count = len(raw_idx) // 3
            if tri_count <= 0:
                return
            indices = raw_idx[: tri_count * 3]
        elif mode == 5:  # TRIANGLE_STRIP
            if len(raw_idx) < 3:
                return
            tris = []
            for k in range(len(raw_idx) - 2):
                if k % 2 == 0:
                    tris.extend((raw_idx[k], raw_idx[k + 1], raw_idx[k + 2]))
                else:
                    tris.extend((raw_idx[k + 1], raw_idx[k], raw_idx[k + 2]))
            indices = np.asarray(tris, dtype=np.int32)
            tri_count = len(indices) // 3
        else:  # TRIANGLE_FAN
            if len(raw_idx) < 3:
                return
            tris = []
            for k in range(1, len(raw_idx) - 1):
                tris.extend((raw_idx[0], raw_idx[k], raw_idx[k + 1]))
            indices = np.asarray(tris, dtype=np.int32)
            tri_count = len(indices) // 3

        indices = np.clip(indices, 0, len(pos) - 1)
        counts = np.full(tri_count, 3, dtype=np.int32)

        mpath = parent_path.AppendChild(Tf.MakeValidIdentifier(prim_label))
        while stage.GetPrimAtPath(mpath):
            mpath = parent_path.AppendChild(mpath.name + '_')
        mesh = UsdGeom.Mesh.Define(stage, mpath)
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pos))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(counts))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(indices))
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)

        mat_idx = pdef.get('material')
        dbl = True
        if mat_idx is not None and 0 <= mat_idx < len(gltf_mats):
            dbl = bool(gltf_mats[mat_idx].get('doubleSided', False))
        mesh.CreateDoubleSidedAttr(dbl)

        mn = pos.min(axis=0)
        mx = pos.max(axis=0)
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(float(mn[0]), float(mn[1]), float(mn[2])), Gf.Vec3f(float(mx[0]), float(mx[1]), float(mx[2]))]))

        if 'NORMAL' in attrs:
            nrm = _read_gltf_accessor(doc, buffers, attrs['NORMAL'])
            if nrm is not None and len(nrm) == len(pos):
                mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(nrm[:, :3], dtype=np.float32)))
                mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)

        if 'TEXCOORD_0' in attrs:
            uv = _read_gltf_accessor(doc, buffers, attrs['TEXCOORD_0'])
            if uv is not None and len(uv) == len(pos):
                uv_f = np.ascontiguousarray(uv[:, :2], dtype=np.float32).copy()
                # glTF V=0 is top-left; OpenUSD st V=0 is bottom-left
                uv_f[:, 1] = 1.0 - uv_f[:, 1]
                pv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
                pv.Set(Vt.Vec2fArray.FromNumpy(uv_f))

        if 'COLOR_0' in attrs:
            col = _read_gltf_accessor(doc, buffers, attrs['COLOR_0'])
            if col is not None and len(col) == len(pos):
                pv_c = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('displayColor', Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex)
                pv_c.Set(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(col[:, :3], dtype=np.float32)))

        if mat_idx in usd_mats:
            bind_material_both_purposes(mesh.GetPrim(), usd_mats[mat_idx])
        mesh_authored[0] += 1

    visited_nodes = set()

    def _walk_gltf_node(nidx, parent_path):
        if nidx < 0 or nidx >= len(gltf_nodes) or nidx in visited_nodes:
            return
        visited_nodes.add(nidx)
        ndef = gltf_nodes[nidx]
        nname = str(ndef.get('name') or f'Node_{nidx}')
        local_m = _node_matrix(ndef)
        mesh_idx = ndef.get('mesh')
        children = ndef.get('children') or []

        xf_path = parent_path.AppendChild(Tf.MakeValidIdentifier(nname))
        while stage.GetPrimAtPath(xf_path):
            xf_path = parent_path.AppendChild(xf_path.name + '_')
        xf = UsdGeom.Xform.Define(stage, xf_path)
        if local_m != Gf.Matrix4d(1.0):
            xf.AddTransformOp().Set(local_m)

        if mesh_idx is not None and 0 <= mesh_idx < len(gltf_meshes):
            mdef = gltf_meshes[mesh_idx]
            mname = str(mdef.get('name') or nname or f'Mesh_{mesh_idx}')
            prims = mdef.get('primitives') or []
            for pi, pdef in enumerate(prims):
                plabel = mname if len(prims) == 1 else f'{mname}_Sub_{pi}'
                _author_primitive(xf_path, plabel, pdef)

        for cidx in children:
            _walk_gltf_node(int(cidx), xf_path)

    scenes = doc.get('scenes') or []
    scene_idx = int(doc.get('scene', 0))
    if scenes and 0 <= scene_idx < len(scenes):
        root_nodes = scenes[scene_idx].get('nodes') or []
    else:
        child_set = {c for nd in gltf_nodes for c in (nd.get('children') or [])}
        root_nodes = [i for i in range(len(gltf_nodes)) if i not in child_set]

    for rn in root_nodes:
        _walk_gltf_node(int(rn), root.GetPath())

    if mesh_authored[0] == 0 and gltf_meshes:
        for mi, mdef in enumerate(gltf_meshes):
            mname = str(mdef.get('name') or f'Mesh_{mi}')
            for pi, pdef in enumerate(mdef.get('primitives') or []):
                _author_primitive(root.GetPath(), mname if pi == 0 else f'{mname}_{pi}', pdef)

    if mesh_authored[0] == 0:
        raise ValueError(f'glTF asset contains no triangle meshes: {path}')

    return stage


# ---------------------------------------------------------------------------
# 3. Megascans & Fab Asset Discovery + Complete USD Stage Builder
# ---------------------------------------------------------------------------

_RES_RANK = {'16k': 16000, '8k': 8192, '4k': 4096, '2k': 2048, '1k': 1024}
_IMG_EXTS = {'.jpg', '.jpeg', '.png', '.exr', '.tif', '.tiff', '.tga', '.webp'}
_MESH_EXT_PRIORITY = {'.usd': 0, '.usdc': 1, '.usda': 2, '.usdz': 3, '.abc': 4, '.fbx': 5, '.glb': 6, '.gltf': 7, '.obj': 8}


def _extract_res_tag(filename):
    m = re.search(r'(?:^|[_\-\s])(16k|8k|4k|2k|1k)(?:[_\-\s.]|$)', filename, re.IGNORECASE)
    return m.group(1).lower() if m else ''


def _extract_lod_num(filename):
    if re.search(r'highpoly', filename, re.IGNORECASE):
        return -1
    m = re.search(r'lod\s*(\d+)', filename, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _classify_pbr_channel(filename):
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    if any(t in stem for t in ('_thumb', '_preview', '_retina', '_brush')) or stem.endswith('_sp'):
        return None
    tokens = [t for t in re.split(r'[^a-z0-9]+', stem) if t]
    tset = set(tokens)
    if 'albedo' in tset or 'basecolor' in tset or ('base' in tset and 'color' in tset) or 'diffuse' in tset or 'col' in tset:
        return 'albedo'
    if 'orm' in tset or 'arm' in tset or ('occlusionroughnessmetallic' in stem):
        return 'orm'
    if 'roughness' in tset or 'rough' in tset:
        return 'roughness'
    if 'gloss' in tset or 'glossiness' in tset:
        return 'gloss'
    if 'metalness' in tset or 'metallic' in tset or 'metal' in tset:
        return 'metalness'
    if 'specular' in tset or 'spec' in tset:
        return 'specular'
    if 'normal' in tset or 'nrm' in tset or 'nor' in tset:
        return 'normal'
    if 'bump' in tset:
        return 'bump'
    if 'opacity' in tset or 'alpha' in tset or 'mask' in tset:
        return 'opacity'
    if 'translucency' in tset or 'subsurface' in tset or 'sss' in tset or 'transmission' in tset:
        return 'translucency'
    if 'emissive' in tset or 'emission' in tset:
        return 'emissive'
    if 'ao' in tset or 'ambientocclusion' in tset or 'occlusion' in tset:
        return 'ao'
    if 'cavity' in tset:
        return 'cavity'
    if 'displacement' in tset or 'disp' in tset or 'height' in tset:
        return 'displacement'
    return None


def discover_folder_pbr_maps(folder, *, resolution='auto', lod=0, include_billboard=False):
    """Scan an asset folder (excluding Thumbs/previews unless needed) for PBR texture channels."""
    if not os.path.isdir(folder):
        return {}
    wanted_res = str(resolution or 'auto').strip().lower()
    wanted_lod = int(lod) if lod is not None else 0

    candidates = []  # (is_thumb_dir, is_billboard, res_rank, res_tag, ext_pref, lod_num, channel, full_path)
    for cur_root, dirs, files in os.walk(folder):
        rel = os.path.relpath(cur_root, folder).replace('\\', '/').lower()
        parts = [p for p in rel.split('/') if p and p != '.']
        if any(p == 'previews' for p in parts):
            continue
        is_thumb = any(p == 'thumbs' for p in parts)
        is_bboard = any('billboard' in p for p in parts)
        if is_bboard != bool(include_billboard):
            continue
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _IMG_EXTS:
                continue
            if 'billboard' in fn.lower() and not include_billboard:
                continue
            ch = _classify_pbr_channel(fn)
            if not ch:
                continue
            rtag = _extract_res_tag(fn)
            if not rtag:
                for p in parts:
                    if p in _RES_RANK:
                        rtag = p
                        break
            rrank = _RES_RANK.get(rtag, 2048)
            lod_n = _extract_lod_num(fn)
            # Prefer standard 8-bit/16-bit PNG/JPG over massive EXR for Albedo/Normal unless EXR is only option
            ext_score = 2 if ext in ('.png', '.jpg', '.jpeg') else (1 if ext in ('.tif', '.tiff', '.webp') else 0)
            candidates.append((0 if not is_thumb else 1, rrank, rtag, ext_score, lod_n, ch, os.path.abspath(os.path.join(cur_root, fn))))

    grouped = {}
    for is_thumb, rrank, rtag, ext_score, lod_n, ch, fp in candidates:
        res_match = 1 if (wanted_res != 'auto' and rtag == wanted_res) else 0
        lod_exact = 1 if (lod_n is not None and lod_n == wanted_lod) else 0
        lod_dist = 0 if lod_n is None else abs(lod_n - wanted_lod)
        # Higher tuple wins
        score = (
            1 - is_thumb,
            res_match,
            rrank if wanted_res == 'auto' else -abs(rrank - _RES_RANK.get(wanted_res, 4096)),
            -lod_dist,
            lod_exact,
            ext_score,
        )
        prev = grouped.get(ch)
        if prev is None or score > prev[0]:
            grouped[ch] = (score, fp)

    return {ch: val[1] for ch, val in grouped.items()}


def _author_discovered_pbr(stage, mat_path_str, maps, *, normal_convention='opengl'):
    return author_pbr_material(
        stage,
        mat_path_str,
        albedo_tex=maps.get('albedo'),
        roughness_tex=maps.get('roughness'),
        gloss_tex=maps.get('gloss'),
        metalness_tex=maps.get('metalness'),
        orm_tex=maps.get('orm'),
        specular_tex=maps.get('specular'),
        normal_tex=maps.get('normal'),
        bump_tex=maps.get('bump'),
        opacity_tex=maps.get('opacity'),
        translucency_tex=maps.get('translucency'),
        emissive_tex=maps.get('emissive'),
        normal_convention=normal_convention,
    )


def _ensure_static_default_samples(stage):
    """Promote single-sample static Alembic attributes (e.g. frame 1.0) to Usd.TimeCode.Default()."""
    from pxr import Sdf, Usd, UsdGeom

    needs_fix = False
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            pts_attr = UsdGeom.Mesh(prim).GetPointsAttr()
            if pts_attr and pts_attr.Get(Usd.TimeCode.Default()) is None and pts_attr.GetNumTimeSamples() == 1:
                needs_fix = True
                break
    if not needs_fix:
        return stage

    anon = Sdf.Layer.CreateAnonymous('static_abc_fix.usda')
    anon.subLayerPaths = [stage.GetRootLayer().identifier]
    wrapped = Usd.Stage.Open(anon)
    wrapped._keepalive_source = stage
    for prim in wrapped.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        for attr in prim.GetAuthoredAttributes():
            if attr.Get(Usd.TimeCode.Default()) is None and attr.GetNumTimeSamples() == 1:
                ts = attr.GetTimeSamples()[0]
                val = attr.Get(Usd.TimeCode(ts))
                if val is not None:
                    attr.Set(val, Usd.TimeCode.Default())
    return wrapped


def inspect_megascans_folder(folder_or_json):
    """Inspect a Megascans or Fab asset folder / JSON and return meshes, variations, LODs, and PBR maps."""
    if os.path.isfile(folder_or_json):
        folder = os.path.dirname(os.path.abspath(folder_or_json))
        json_path = os.path.abspath(folder_or_json)
    else:
        folder = os.path.abspath(folder_or_json)
        json_path = None
        if os.path.isdir(folder):
            for fn in sorted(os.listdir(folder)):
                fp = os.path.join(folder, fn)
                if fn.lower().endswith('.json') and is_megascans_or_fab_json(fp):
                    json_path = fp
                    break

    meta = {}
    if json_path and os.path.isfile(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8-sig', errors='replace') as f:
                meta = json.load(f)
        except Exception:
            meta = {}

    sem_name = (meta.get('semanticTags') or {}).get('name') if isinstance(meta.get('semanticTags'), dict) else None
    name = str(meta.get('name') or sem_name or os.path.basename(folder) or 'Asset').strip()
    categories = [str(c).lower() for c in (meta.get('categories') or [])]
    if isinstance(meta.get('assetCategories'), dict):
        categories.extend(str(k).lower() for k in meta['assetCategories'].keys())

    # Check for 3D plant / assembly variations (Var1, Var2, ...)
    var_dirs = []
    if os.path.isdir(folder):
        for entry in sorted(os.listdir(folder), key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]):
            full_e = os.path.join(folder, entry)
            if os.path.isdir(full_e) and re.match(r'^var\d+$', entry, re.IGNORECASE):
                var_dirs.append((entry, full_e))

    def _scan_meshes_in(search_dir, allow_subdirs=True):
        found = []  # (lod_num, ext_prio, path)
        for cur_root, _dirs, files in os.walk(search_dir):
            rel = os.path.relpath(cur_root, search_dir).replace('\\', '/').lower()
            parts = [p for p in rel.split('/') if p and p != '.']
            if any(p in ('previews', 'thumbs') for p in parts):
                continue
            if not allow_subdirs and parts:
                continue
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in _MESH_EXT_PRIORITY:
                    continue
                lod_n = _extract_lod_num(fn)
                if lod_n == -1:
                    continue
                found.append((0 if lod_n is None else lod_n, lod_n is not None, _MESH_EXT_PRIORITY[ext], os.path.abspath(os.path.join(cur_root, fn))))
        return found

    variations = []
    all_lods = set()
    if var_dirs:
        for vname, vdir in var_dirs:
            vmeshes = _scan_meshes_in(vdir, allow_subdirs=True)
            by_lod = {}
            for lod_n, _has_lod, ext_p, fp in vmeshes:
                all_lods.add(lod_n)
                prev = by_lod.get(lod_n)
                if prev is None or ext_p < prev[0]:
                    by_lod[lod_n] = (ext_p, fp)
            if by_lod:
                variations.append({'name': vname, 'lods': {k: v[1] for k, v in sorted(by_lod.items())}})
    else:
        top_meshes = _scan_meshes_in(folder, allow_subdirs=True)
        has_explicit_lods = any(has_lod for _, has_lod, _, _ in top_meshes)
        if has_explicit_lods:
            by_lod = {}
            for lod_n, _has_lod, ext_p, fp in top_meshes:
                all_lods.add(lod_n)
                prev = by_lod.get(lod_n)
                if prev is None or ext_p < prev[0]:
                    by_lod[lod_n] = (ext_p, fp)
            if by_lod:
                variations.append({'name': Tf.MakeValidIdentifier(name) if 'Tf' in globals() else re.sub(r'[^A-Za-z0-9_]', '_', name), 'lods': {k: v[1] for k, v in sorted(by_lod.items())}})
        elif top_meshes:
            # Non-LOD pack (e.g. Fab multi-model folder or single GLB/FBX)
            # Pick best format group
            best_ext_p = min(x[2] for x in top_meshes)
            chosen = [x[3] for x in top_meshes if x[2] == best_ext_p]
            all_lods.add(0)
            for idx_m, fp in enumerate(chosen):
                vlabel = os.path.splitext(os.path.basename(fp))[0] if len(chosen) > 1 else name
                variations.append({'name': vlabel, 'lods': {0: fp}})

    # Resolutions available
    resolutions = set()
    if os.path.isdir(folder):
        for cur_root, _dirs, files in os.walk(folder):
            rel = os.path.relpath(cur_root, folder).replace('\\', '/').lower()
            if 'thumbs' in rel or 'previews' in rel:
                continue
            for fn in files:
                if os.path.splitext(fn)[1].lower() in _IMG_EXTS:
                    rt = _extract_res_tag(fn)
                    if rt:
                        resolutions.add(rt.upper())

    maps_preview = discover_folder_pbr_maps(folder, resolution='auto', lod=0, include_billboard=False)
    is_surface_only = len(variations) == 0 and bool(maps_preview)
    if '3dplant' in categories or var_dirs:
        asset_type = '3dplant'
    elif is_surface_only or 'surface' in categories or 'atlas' in categories:
        asset_type = 'surface' if not variations else '3d'
    else:
        asset_type = '3d'

    return {
        'name': name,
        'folder': folder,
        'jsonPath': json_path,
        'assetType': asset_type,
        'availableLods': sorted(all_lods),
        'availableResolutions': sorted(resolutions, key=lambda r: -_RES_RANK.get(r.lower(), 0)),
        'variations': variations,
        'maps': maps_preview,
    }


def open_megascans_source_stage(
    path,
    *,
    open_stage_fn,
    lod=0,
    resolution='auto',
    normal_convention='opengl',
    reload=False,
):
    """Build a canonical Y-up, 1.0-meter in-memory OpenUSD stage for a Megascans or Fab asset."""
    from pxr import Gf, Sdf, Tf, Usd, UsdGeom

    info = inspect_megascans_folder(path)
    variations = info['variations']
    if not variations:
        if info['maps']:
            raise ValueError(
                f'"{info["name"]}" is a texture-only Megascans Surface/Atlas. Drop or load it as a MaterialX node to bind onto geometry.'
            )
        raise ValueError(f'No supported 3D geometry (.usd, .abc, .fbx, .glb, .gltf, .obj) found in {info["folder"]}')

    wanted_lod = int(lod) if lod is not None else 0
    norm_conv = str(normal_convention or 'opengl').lower()
    folder = info['folder']

    stage = Usd.Stage.CreateInMemory('source-megascans.usda')
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, '/Megascans')
    stage.SetDefaultPrim(root.GetPrim())

    UsdGeom.Scope.Define(stage, '/Megascans/_Materials')
    main_maps = discover_folder_pbr_maps(folder, resolution=resolution, lod=wanted_lod, include_billboard=False)
    bboard_maps = discover_folder_pbr_maps(folder, resolution=resolution, lod=wanted_lod, include_billboard=True)

    main_mat = _author_discovered_pbr(stage, '/Megascans/_Materials/MainPBR', main_maps, normal_convention=norm_conv) if main_maps else None
    bboard_mat = _author_discovered_pbr(stage, '/Megascans/_Materials/BillboardPBR', bboard_maps, normal_convention=norm_conv) if bboard_maps else None

    keepalive = []
    used_var_names = set()

    for var in variations:
        lod_map = var['lods']
        if not lod_map:
            continue
        chosen_lod = min(lod_map.keys(), key=lambda k: abs(int(k) - wanted_lod))
        mesh_fp = lod_map[chosen_lod]
        ext = os.path.splitext(mesh_fp)[1].lower()

        if ext == '.fbx':
            sub_stage = open_fbx_source_stage(mesh_fp, auto_companion_pbr=False)
        elif ext in ('.gltf', '.glb'):
            sub_stage = open_gltf_source_stage(mesh_fp)
        else:
            sub_stage = open_stage_fn(mesh_fp, reload=reload)
            if ext == '.abc':
                sub_stage = _ensure_static_default_samples(sub_stage)
        keepalive.append(sub_stage)

        raw_vname = var['name'] or 'Asset'
        vid = Tf.MakeValidIdentifier(raw_vname)
        if vid in used_var_names:
            vid += f'_{len(used_var_names) + 1}'
        used_var_names.add(vid)

        var_xf = UsdGeom.Xform.Define(stage, f'/Megascans/{vid}')
        # Convert sub_stage units/up-axis to canonical 1.0 m, Y-up
        s_mpu = float(UsdGeom.GetStageMetersPerUnit(sub_stage) or 1.0)
        s_up = str(UsdGeom.GetStageUpAxis(sub_stage) or 'Y').upper()
        conv_m = Gf.Matrix4d(1.0)
        if s_up == 'Z':
            conv_m.SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), -90.0))
        if abs(s_mpu - 1.0) > 1e-7:
            sm = Gf.Matrix4d(1.0)
            sm.SetScale(Gf.Vec3d(s_mpu, s_mpu, s_mpu))
            conv_m = conv_m * sm
        if conv_m != Gf.Matrix4d(1.0):
            var_xf.AddTransformOp(opSuffix='units').Set(conv_m)

        def_prim = sub_stage.GetDefaultPrim()
        if def_prim and def_prim.IsValid():
            ref_target = var_xf.GetPrim()
            if def_prim.IsA(UsdGeom.Gprim):
                # Default prim is itself a Mesh (e.g. usdAbc) -> reference on a child Mesh prim
                mesh_child = UsdGeom.Mesh.Define(stage, var_xf.GetPath().AppendChild(Tf.MakeValidIdentifier(def_prim.GetName())))
                ref_target = mesh_child.GetPrim()
            ref_target.GetReferences().AddReference(sub_stage.GetRootLayer().identifier, def_prim.GetPath())
        else:
            for child in sub_stage.GetPseudoRoot().GetChildren():
                cprim = stage.OverridePrim(var_xf.GetPath().AppendChild(child.GetName()))
                cprim.GetReferences().AddReference(sub_stage.GetRootLayer().identifier, child.GetPath())

        # Bind Megascans PBR material unless a glTF/USD asset already carries its own textured shaders
        has_own_maps = ext in ('.gltf', '.glb', '.usd', '.usda', '.usdc', '.usdz') and not main_maps
        if not has_own_maps:
            is_bb_lod = bool(bboard_mat and len(lod_map) >= 3 and chosen_lod == max(lod_map.keys()) and info['assetType'] == '3dplant')
            chosen_mat = bboard_mat if is_bb_lod else main_mat
            if chosen_mat is not None:
                bind_material_both_purposes(var_xf.GetPrim(), chosen_mat, stronger=True)

    stage._megascans_keepalive = keepalive
    return stage


def collect_stage_external_files(stage, root_path):
    """Collect external texture and layer dependencies from an in-memory converted stage."""
    from pxr import Sdf

    temp_root = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    gltf_cache_root = os.path.join(temp_root, 'sublayer_gltf_cache')
    root_norm = os.path.normcase(os.path.realpath(root_path)) if os.path.exists(root_path) else ''
    found = {}

    def _add(fp):
        if not fp or not isinstance(fp, str):
            return
        abs_fp = os.path.abspath(fp)
        norm_fp = os.path.normcase(os.path.realpath(abs_fp)) if os.path.exists(abs_fp) else os.path.normcase(abs_fp)
        if norm_fp == root_norm or norm_fp.startswith(gltf_cache_root):
            return
        ext = os.path.splitext(abs_fp)[1].lower()
        kind = 'usd' if ext in ('.usd', '.usda', '.usdc', '.usdz') else 'asset'
        found.setdefault(norm_fp, {'path': abs_fp, 'kind': kind})

    for prim in stage.Traverse():
        for attr in prim.GetAttributes():
            val = attr.Get()
            if isinstance(val, Sdf.AssetPath):
                _add(val.resolvedPath or val.path)
            elif isinstance(val, (list, tuple)):
                for item in val:
                    if isinstance(item, Sdf.AssetPath):
                        _add(item.resolvedPath or item.path)
    return list(found.values())
