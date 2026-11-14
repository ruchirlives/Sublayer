"""Resolver diagnostics must respect layer-relative paths and UDIM patterns."""
import pathlib
import tempfile
import unittest
from usd_worker import missing_shader_assets
from pxr import Sdf, Usd, UsdShade


class AssetDiagnosticsTests(unittest.TestCase):
    def test_missing_assets_are_deduplicated_and_resolved_files_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / 'present.png').touch()
            stage = Usd.Stage.CreateNew(str(root / 'scene.usda'))
            for name in ('First', 'Second'):
                shader = UsdShade.Shader.Define(stage, '/' + name)
                shader.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./missing.png'))
                shader.CreateInput('existing', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./present.png'))
            result = missing_shader_assets(stage, 0)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['path'], './missing.png')
            self.assertEqual(len(result[0]['usedBy']), 2)

    def test_udim_tiles_resolve_relative_to_referenced_layer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            assets = root / 'assets'
            assets.mkdir()
            (assets / 'diffuse.1001.png').touch()
            source = Usd.Stage.CreateNew(str(assets / 'asset.usda'))
            source.DefinePrim('/Asset', 'Xform')
            shader = UsdShade.Shader.Define(source, '/Asset/Texture')
            shader.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./diffuse.<UDIM>.png'))
            source.GetRootLayer().Save()
            stage = Usd.Stage.CreateNew(str(root / 'scene.usda'))
            stage.DefinePrim('/Ref').GetReferences().AddReference('./assets/asset.usda', '/Asset')
            self.assertEqual(missing_shader_assets(stage, 0), [])
            shader.GetInput('file').Set(Sdf.AssetPath('./absent.<UDIM>.png'))
            self.assertEqual(len(missing_shader_assets(stage, 0)), 1)

class DependencyDiscoveryTests(unittest.TestCase):
    def test_discovery_includes_nested_missing_shader_assets_with_absolute_paths(self):
        from usd_worker import dispatch
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            nested=root/'layers';nested.mkdir()
            (nested/'present.png').touch()
            child=Usd.Stage.CreateNew(str(nested/'child.usda'))
            child.DefinePrim('/Asset','Xform')
            shader=UsdShade.Shader.Define(child,'/Asset/Texture')
            shader.CreateIdAttr('UsdUVTexture')
            shader.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./missing.png'))
            shader.CreateInput('other',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./present.png'))
            child.GetRootLayer().Save()
            stage=Usd.Stage.CreateNew(str(root/'scene.usda'))
            stage.DefinePrim('/Ref').GetReferences().AddReference('./layers/child.usda','/Asset')
            stage.GetRootLayer().Save()
            before={p:p.read_bytes() for p in [root/'scene.usda',nested/'child.usda',nested/'present.png']}
            result=dispatch({'operation':'dependencies','path':str(root/'scene.usda')})
            found={pathlib.Path(item['path']).resolve():item['kind'] for item in result['files']}
            self.assertEqual(found[(nested/'missing.png').resolve()],'asset')
            self.assertEqual(found[(nested/'present.png').resolve()],'asset')
            self.assertEqual(found[(nested/'child.usda').resolve()],'usd')
            self.assertNotIn((root/'scene.usda').resolve(),found)
            self.assertEqual(before,{p:p.read_bytes() for p in before})


class MaterialInspectionTests(unittest.TestCase):
    def test_reports_material_contexts_and_subset_bindings_without_editing_files(self):
        from usd_worker import dispatch
        from pxr import UsdGeom
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'material.usda'
            stage=Usd.Stage.CreateNew(str(path))
            mesh=UsdGeom.Mesh.Define(stage,'/Mesh')
            material=UsdShade.Material.Define(stage,'/Looks/Material')
            shader=UsdShade.Shader.Define(stage,'/Looks/Material/Shader')
            shader.CreateIdAttr('UsdPreviewSurface')
            output=shader.CreateOutput('surface',Sdf.ValueTypeNames.Token)
            material.CreateSurfaceOutput().ConnectToSource(output)
            material.CreateSurfaceOutput('mtlx').ConnectToSource(output)
            subset=UsdGeom.Subset.Define(stage,'/Mesh/Faces')
            subset.CreateElementTypeAttr('face');subset.CreateIndicesAttr([0])
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material)
            stage.GetRootLayer().Save();before=path.read_bytes()
            report=dispatch({'operation':'inspect','path':str(path)})['materialInspection']
            contexts={output['context'] for output in report['materials'][0]['outputs']}
            self.assertEqual(contexts,{'universal','mtlx'})
            self.assertTrue(any(binding['prim']=='/Mesh/Faces' for binding in report['bindings']))
            self.assertIn('UsdPreviewSurface',report['shaderIdentifiers'])
            self.assertIn('materialXDefinitions',report['support'])
            self.assertEqual(path.read_bytes(),before)
