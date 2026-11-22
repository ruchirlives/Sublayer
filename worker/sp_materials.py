"""Author sparse MaterialX + UsdPreviewSurface layers from Substance Painter manifests."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"M_{cleaned}"
    return Tf.MakeValidIdentifier(cleaned)


def _author_sp_material(stage: Usd.Stage, mat_path: Sdf.Path, channels: dict[str, str]) -> UsdShade.Material:
    """Author a dual-context MaterialX (Standard Surface) + UsdPreviewSurface material."""
    material = UsdShade.Material.Define(stage, mat_path)

    # --- MaterialX Standard Surface ---
    mtlx = UsdShade.Shader.Define(stage, mat_path.AppendChild("StandardSurface"))
    mtlx.CreateIdAttr("ND_standard_surface_surfaceshader")
    mtlx.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)
    mtlx.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(1.0)
    mtlx_out = mtlx.CreateOutput("out", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput("mtlx").ConnectToSource(mtlx_out)

    uv_mtlx = UsdShade.Shader.Define(stage, mat_path.AppendChild("MtlxUV"))
    uv_mtlx.CreateIdAttr("ND_geompropvalue_vector2")
    uv_mtlx.CreateInput("geomprop", Sdf.ValueTypeNames.String).Set("st")
    uv_mtlx_out = uv_mtlx.CreateOutput("out", Sdf.ValueTypeNames.Float2)

    # --- UsdPreviewSurface fallback ---
    preview = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewSurface"))
    preview.CreateIdAttr("UsdPreviewSurface")
    preview_out = preview.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(preview_out)

    uv_prev = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewUV"))
    uv_prev.CreateIdAttr("UsdPrimvarReader_float2")
    uv_prev.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    uv_prev_out = uv_prev.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    def _mtlx_image(child: str, path: str, is_color: bool, colorspace: str) -> UsdShade.Output:
        img = UsdShade.Shader.Define(stage, mat_path.AppendChild(child))
        img.CreateIdAttr("ND_image_color3" if is_color else "ND_image_float")
        file_in = img.CreateInput("file", Sdf.ValueTypeNames.Asset)
        file_in.Set(Sdf.AssetPath(path))
        file_in.GetAttr().SetColorSpace(colorspace)
        img.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_mtlx_out)
        return img.CreateOutput("out", Sdf.ValueTypeNames.Color3f if is_color else Sdf.ValueTypeNames.Float)

    def _preview_image(child: str, path: str, colorspace: str) -> UsdShade.Shader:
        tex = UsdShade.Shader.Define(stage, mat_path.AppendChild(child))
        tex.CreateIdAttr("UsdUVTexture")
        file_in = tex.CreateInput("file", Sdf.ValueTypeNames.Asset)
        file_in.Set(Sdf.AssetPath(path))
        tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(colorspace)
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_prev_out)
        return tex

    if channels.get("baseColor"):
        p = channels["baseColor"]
        mtlx.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            _mtlx_image("MtlxBaseColor", p, True, "srgb_texture")
        )
        tex = _preview_image("PreviewBaseColor", p, "sRGB")
        preview.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        )
    else:
        mtlx.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.8, 0.8, 0.8))
        preview.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.8, 0.8, 0.8))

    if channels.get("roughness"):
        p = channels["roughness"]
        mtlx.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).ConnectToSource(
            _mtlx_image("MtlxRoughness", p, False, "raw")
        )
        tex = _preview_image("PreviewRoughness", p, "raw")
        preview.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(
            tex.CreateOutput("r", Sdf.ValueTypeNames.Float)
        )
    else:
        mtlx.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(0.5)
        preview.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)

    if channels.get("metallic"):
        p = channels["metallic"]
        mtlx.CreateInput("metalness", Sdf.ValueTypeNames.Float).ConnectToSource(
            _mtlx_image("MtlxMetallic", p, False, "raw")
        )
        tex = _preview_image("PreviewMetallic", p, "raw")
        preview.CreateInput("metallic", Sdf.ValueTypeNames.Float).ConnectToSource(
            tex.CreateOutput("r", Sdf.ValueTypeNames.Float)
        )
    else:
        mtlx.CreateInput("metalness", Sdf.ValueTypeNames.Float).Set(0.0)
        preview.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)

    if channels.get("normal"):
        p = channels["normal"]
        norm_img = UsdShade.Shader.Define(stage, mat_path.AppendChild("MtlxNormalTex"))
        norm_img.CreateIdAttr("ND_image_vector3")
        f_in = norm_img.CreateInput("file", Sdf.ValueTypeNames.Asset)
        f_in.Set(Sdf.AssetPath(p))
        f_in.GetAttr().SetColorSpace("raw")
        norm_img.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_mtlx_out)

        norm_map = UsdShade.Shader.Define(stage, mat_path.AppendChild("MtlxNormalMap"))
        norm_map.CreateIdAttr("ND_normalmap")
        norm_map.CreateInput("in", Sdf.ValueTypeNames.Vector3f).ConnectToSource(
            norm_img.CreateOutput("out", Sdf.ValueTypeNames.Vector3f)
        )
        mtlx.CreateInput("normal", Sdf.ValueTypeNames.Vector3f).ConnectToSource(
            norm_map.CreateOutput("out", Sdf.ValueTypeNames.Vector3f)
        )

        tex = _preview_image("PreviewNormal", p, "raw")
        tex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2.0, 2.0, 2.0, 1.0))
        tex.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1.0, -1.0, -1.0, 0.0))
        preview.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(
            tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        )

    if channels.get("opacity"):
        p = channels["opacity"]
        # Guard against SP exporting an all-black opacity map when no opacity channel was painted
        wire_opacity = True
        try:
            import os
            if "<UDIM>" not in p and os.path.isfile(p):
                from PIL import Image
                with Image.open(p) as im:
                    extrema = im.convert("L").getextrema()
                    if extrema == (0, 0):
                        wire_opacity = False
        except Exception:
            pass
        if wire_opacity:
            mtlx.CreateInput("opacity", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                _mtlx_image("MtlxOpacity", p, True, "raw")
            )
            tex = _preview_image("PreviewOpacity", p, "raw")
            preview.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                tex.CreateOutput("r", Sdf.ValueTypeNames.Float)
            )

    if channels.get("emissive"):
        p = channels["emissive"]
        mtlx.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(1.0)
        mtlx.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            _mtlx_image("MtlxEmissive", p, True, "srgb_texture")
        )
        tex = _preview_image("PreviewEmissive", p, "sRGB")
        preview.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        )

    return material


def build_sp_material_layer(upstream_stage: Usd.Stage, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a sparse anonymous .usda layer applying Substance Painter texture sets to upstream prims."""
    texture_sets = manifest.get("textureSets") or {}
    if not isinstance(texture_sets, dict) or not texture_sets:
        raise ValueError("Substance Painter manifest contains no textureSets.")

    # Index upstream materials and meshes
    mat_to_prims: dict[str, list[Sdf.Path]] = {}
    mesh_by_name: dict[str, list[Sdf.Path]] = {}
    all_meshes: list[Sdf.Path] = []
    skipped_proxies: list[str] = []

    for prim in upstream_stage.Traverse():
        if prim.IsInstanceProxy():
            if prim.IsA(UsdGeom.Mesh):
                skipped_proxies.append(str(prim.GetPath()))
            continue
        if prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Subset):
            path = prim.GetPath()
            if prim.IsA(UsdGeom.Mesh):
                all_meshes.append(path)
                mesh_by_name.setdefault(prim.GetName().lower(), []).append(path)
            binding_api = UsdShade.MaterialBindingAPI(prim)
            bound_mat, _ = binding_api.ComputeBoundMaterial()
            if bound_mat and bound_mat.GetPrim().IsValid():
                mat_name = bound_mat.GetPrim().GetName().lower()
                mat_to_prims.setdefault(mat_name, []).append(path)
                mat_base = re.sub(r"_[0-9a-f]{8,}$", "", mat_name)
                if mat_base and mat_base != mat_name:
                    mat_to_prims.setdefault(mat_base, []).append(path)

    layer = Sdf.Layer.CreateAnonymous("sp_materials.usda")
    layer.customLayerData = {
        "orchflowNamespaceVersion": 2,
        "sublayerSyncId": str(time.time_ns()),
    }
    layer.defaultPrim = "Handoff"
    out_stage = Usd.Stage.Open(layer)
    handoff_root = Sdf.Path("/Handoff")
    looks_root = handoff_root.AppendChild("Looks")
    out_stage.OverridePrim(handoff_root)
    UsdGeom.Scope.Define(out_stage, looks_root)

    applied: list[dict[str, Any]] = []
    unmatched: list[str] = []
    bound_count = 0

    for ts_name, ts_data in texture_sets.items():
        channels = (ts_data or {}).get("channels") or {}
        if not channels:
            unmatched.append(ts_name)
            continue

        key = ts_name.lower()
        safe_key = _safe_name(ts_name).lower()
        base_key = re.sub(r"_[0-9a-f]{8,}$", "", key)
        targets = (
            mat_to_prims.get(key)
            or mat_to_prims.get(safe_key)
            or mat_to_prims.get(base_key)
            or mesh_by_name.get(key)
            or mesh_by_name.get(safe_key)
            or mesh_by_name.get(base_key)
            or []
        )
        if not targets and len(texture_sets) == 1 and all_meshes:
            targets = all_meshes

        if not targets:
            unmatched.append(ts_name)
            continue

        mat_path = looks_root.AppendChild(f"SP_{_safe_name(ts_name)}")
        material = _author_sp_material(out_stage, mat_path, channels)

        bound_paths: list[str] = []
        for target_path in targets:
            handoff_target = target_path.ReplacePrefix(Sdf.Path("/Scene"), handoff_root)
            over_prim = out_stage.OverridePrim(handoff_target)
            UsdShade.MaterialBindingAPI.Apply(over_prim).Bind(
                material, bindingStrength=UsdShade.Tokens.strongerThanDescendants
            )
            bound_paths.append(str(target_path))
            bound_count += 1

        applied.append({
            "textureSet": ts_name,
            "materialPath": str(mat_path),
            "channels": sorted(channels.keys()),
            "boundPrims": bound_paths,
            "udim": bool((ts_data or {}).get("udim", False)),
        })

    if not applied:
        raise ValueError(
            "No Substance Painter texture sets matched any upstream materials or meshes. "
            f"Unmatched: {', '.join(unmatched)}"
        )

    return {
        "layer": layer.ExportToString(),
        "report": {
            "applied": applied,
            "unmatchedTextureSets": unmatched,
            "skippedProxies": skipped_proxies,
            "textureSetCount": len(applied),
            "boundPrimCount": bound_count,
        },
    }
