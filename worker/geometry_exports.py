"""Read-only application export readers; all converted layers are anonymous."""
import hashlib
import math
from pathlib import Path


def export_format(path):
    extension=Path(path).suffix.lower()
    if extension in ('.usd','.usda','.usdc'):return 'usd'
    if extension in ('.obj','.abc'):return extension[1:]
    raise ValueError('Choose a USD, OBJ or Alembic (.abc) application export')


def obj_export_stage(path,conversion):
    from pxr import Usd,UsdGeom,Vt,Gf,Tf
    points=[];uvs=[];normals=[];objects={};name='Mesh';declared={}
    def index(value,size):
        number=int(value)
        result=number-1 if number>0 else size+number
        if number==0 or result<0 or result>=size:raise ValueError('OBJ index outside its declared array')
        return result
    def numbers(tokens,count):
        result=[float(v) for v in tokens]
        if len(result)<count or not all(math.isfinite(v) for v in result):raise ValueError('Invalid/non-finite OBJ coordinates')
        return result
    with open(path,encoding='utf-8-sig',errors='strict') as stream:
        pending=''
        for line_number,line in enumerate(stream,1):
            line=line.split('#',1)[0].strip()
            if line.endswith(chr(92)):
                pending+=line[:-1]+' ';continue
            line=pending+line;pending=''
            if not line:continue
            if len(points)>10000000 or len(uvs)>20000000 or len(normals)>20000000:raise ValueError('OBJ exceeds the geometry import budget; split the export')
            token,*values=line.split()
            try:
                if token=='v':
                    v=numbers(values,3)
                    if len(v) not in (3,4,6,7):raise ValueError('Unsupported OBJ vertex layout')
                    if len(v)==4:
                        if v[3]==0:raise ValueError('Zero homogeneous vertex weight')
                        v=[c/v[3] for c in v[:3]]
                    declared.setdefault(name,[]).append(len(points));points.append(tuple(v[:3]))
                elif token=='vt':
                    v=numbers(values,1)
                    if len(v)>2 and v[2]!=0:raise ValueError('Three-dimensional OBJ texture coordinates are not supported')
                    uvs.append((v[0],v[1] if len(v)>1 else 0.0))
                elif token=='vn':normals.append(tuple(numbers(values,3)[:3]))
                elif token=='o':name=' '.join(values) or 'Mesh'
                elif token=='f':
                    if len(values)<3:raise ValueError('OBJ polygon needs at least three corners')
                    face=[]
                    for corner in values:
                        fields=corner.split('/')
                        if len(fields)>3 or not fields[0]:raise ValueError('Invalid OBJ face corner')
                        face.append((index(fields[0],len(points)),index(fields[1],len(uvs)) if len(fields)>1 and fields[1] else None,index(fields[2],len(normals)) if len(fields)>2 and fields[2] else None))
                    objects.setdefault(name,[]).append(face)
                elif token in ('g','s','mtllib','usemtl'):pass
                else:raise ValueError('Unsupported OBJ statement: '+token+' (polygon meshes only)')
            except (ValueError,OverflowError) as error:raise ValueError(f'OBJ line {line_number}: {error}') from error
        if pending:raise ValueError('Unfinished OBJ line continuation')
    if not objects:raise ValueError('OBJ contains no polygon meshes')
    if any(not math.isfinite(c) or abs(c)>3.4028234e38 for arrays in (points,uvs,normals) for value in arrays for c in value):raise ValueError('OBJ coordinates exceed finite float32 range')
    stage=Usd.Stage.CreateInMemory('application-obj.usda')
    UsdGeom.SetStageUpAxis(stage,conversion['upAxis']);UsdGeom.SetStageMetersPerUnit(stage,conversion['metersPerUnit'])
    root=UsdGeom.Xform.Define(stage,'/OBJ');stage.SetDefaultPrim(root.GetPrim())
    used_names=set()
    for raw,faces in objects.items():
        identifier=Tf.MakeValidIdentifier(raw)
        if identifier in used_names:identifier+='_'+hashlib.sha256(raw.encode()).hexdigest()[:12]
        used_names.add(identifier)
        vertices=sorted({corner[0] for face in faces for corner in face}|set(declared.get(raw,[]))) if len(objects)>1 else list(range(len(points)))
        remap={v:i for i,v in enumerate(vertices)}
        corners=[corner for face in faces for corner in face]
        mesh=UsdGeom.Mesh.Define(stage,'/OBJ/'+identifier)
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*points[v]) for v in vertices]))
        mesh.CreateFaceVertexCountsAttr([len(face) for face in faces])
        mesh.CreateFaceVertexIndicesAttr([remap[c[0]] for c in corners])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        if any(c[1] is not None for c in corners):
            if any(c[1] is None for c in corners):raise ValueError('Partial OBJ UV data is not supported')
            from pxr import Sdf
            uv=UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.faceVarying)
            uv.Set(Vt.Vec2fArray([Gf.Vec2f(*uvs[c[1]]) for c in corners]))
        if any(c[2] is not None for c in corners):
            if any(c[2] is None for c in corners):raise ValueError('Partial OBJ normal data is not supported')
            mesh.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(*normals[c[2]]) for c in corners]))
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    return stage


def open_geometry_export(path,conversion):
    """None delegates ordinary USD to the existing fresh-layer loader."""
    kind=export_format(path)
    if kind=='usd':return None
    if kind=='obj':return obj_export_stage(path,conversion)
    from pxr import Sdf,Usd,UsdGeom
    if not Sdf.FileFormat.FindByExtension('abc'):
        raise ValueError('Alembic support requires the upstream OpenUSD usdAbc plugin; it is missing from this runtime')
    try:
        source=Sdf.Layer.FindOrOpen(str(path))
        if not source or not source.Reload(True):raise ValueError('Unable to read Alembic archive')
    except Exception as error:raise ValueError('Unable to read Alembic with the OpenUSD usdAbc plugin: '+str(error)) from error
    # Never author metadata into the external archive. Units/axis are an explicit
    # export contract from the frozen handoff, since cache formats vary by app.
    archive=Usd.Stage.Open(source)
    if not archive:raise ValueError('Unable to open Alembic stage')
    layer=Sdf.Layer.CreateAnonymous('application-alembic.usda')
    layer.timeCodesPerSecond=archive.GetTimeCodesPerSecond()
    layer.framesPerSecond=archive.GetFramesPerSecond()
    layer.startTimeCode=archive.GetStartTimeCode();layer.endTimeCode=archive.GetEndTimeCode()
    layer.subLayerPaths=[source.identifier]
    stage=Usd.Stage.Open(layer)
    if not stage:raise ValueError('Unable to compose Alembic archive')
    UsdGeom.SetStageUpAxis(stage,conversion['upAxis']);UsdGeom.SetStageMetersPerUnit(stage,conversion['metersPerUnit'])
    count=0
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):count+=1
        elif prim.IsA(UsdGeom.Gprim):raise ValueError('Alembic application edits currently support polygon meshes, not '+prim.GetTypeName())
    if not count:raise ValueError('Alembic archive contains no polygon meshes')
    return stage
