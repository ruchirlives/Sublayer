"""USD collider mesh triangulation, UsdSkel skinning, and space transforms for Hair."""


def _points_to_world_np(pts, world_mat, np):
    """Transform row-major (N, 3) points by a 4x4 USD local-to-world matrix."""
    pts_np = np.asarray(pts, dtype=np.float32)
    if pts_np.size == 0:
        return pts_np.reshape(0, 3)
    m = np.asarray(world_mat, dtype=np.float32)
    return np.ascontiguousarray(pts_np @ m[:3, :3] + m[3, :3], dtype=np.float32)


def _world_np_to_local_vt(pts_np, inv_world_mat, Vt, np):
    """Transform world-space (N, 3) float32 points into a local-space `Vt.Vec3fArray`."""
    m = np.asarray(inv_world_mat, dtype=np.float32)
    local_np = np.ascontiguousarray(pts_np @ m[:3, :3] + m[3, :3], dtype=np.float32)
    return Vt.Vec3fArray.FromNumpy(local_np)


def _transform_might_be_time_varying(prim):
    """Return True if `prim` or any ancestor has a time-varying USD transform."""
    from pxr import Sdf, UsdGeom

    curr = prim
    while curr and curr.GetPath() != Sdf.Path.absoluteRootPath:
        xf = UsdGeom.Xformable(curr)
        if xf and xf.TransformMightBeTimeVarying():
            return True
        curr = curr.GetParent()
    return False


def _build_skel_context(stage):
    """Collect `{prim_path: (SkinningQuery, SkelQuery)}` for skinned meshes on `stage`."""
    from pxr import Usd, UsdSkel

    skel_cache = UsdSkel.Cache()
    bindings = {}
    has_roots = False
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdSkel.Root):
            skel_cache.Populate(UsdSkel.Root(prim), Usd.TraverseInstanceProxies())
            has_roots = True
    if not has_roots:
        return {}
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if prim.HasAPI(UsdSkel.BindingAPI):
            skin_q = skel_cache.GetSkinningQuery(prim)
            if skin_q:
                skel_prim = skin_q.GetSkeleton() if hasattr(skin_q, "GetSkeleton") else None
                if not skel_prim:
                    rel = UsdSkel.BindingAPI(prim).GetSkeletonRel()
                    targets = rel.GetTargets() if rel else []
                    if targets:
                        skel_prim = stage.GetPrimAtPath(targets[0])
                if skel_prim and skel_prim.IsValid() and skel_prim.IsA(UsdSkel.Skeleton):
                    skel_q = skel_cache.GetSkelQuery(UsdSkel.Skeleton(skel_prim))
                    if skel_q:
                        bindings[str(prim.GetPath())] = (skin_q, skel_q)
    return bindings


def _triangulate_mesh(mesh, time_code, xform_cache, np, skel_ctx=None):
    """Evaluate (optionally skinned) world-space vertices and triangle indices for `mesh`."""
    pts = mesh.GetPointsAttr().Get(time_code)
    counts = mesh.GetFaceVertexCountsAttr().Get(time_code)
    indices = mesh.GetFaceVertexIndicesAttr().Get(time_code)
    if not pts or not counts or not indices:
        return None, None

    world_mat = xform_cache.GetLocalToWorldTransform(mesh.GetPrim())
    if skel_ctx:
        entry = skel_ctx.get(str(mesh.GetPrim().GetPath()))
        if entry is not None:
            skin_q, skel_q = entry
            xforms = skel_q.ComputeSkinningTransforms(time_code)
            if xforms:
                mapper = skin_q.GetJointMapper()
                if mapper:
                    xforms = mapper.RemapTransforms(xforms)
                if skin_q.ComputeSkinnedPoints(xforms, pts, time_code):
                    world_mat = xform_cache.GetLocalToWorldTransform(skel_q.GetPrim())

    pts_np = _points_to_world_np(pts, world_mat, np)
    counts_np = np.asarray(counts, dtype=np.int32)
    idx_np = np.asarray(indices, dtype=np.int32)
    if counts_np.size == 0 or idx_np.size == 0:
        return None, None

    if np.all(counts_np == 3):
        return pts_np, np.ascontiguousarray(idx_np, dtype=np.int32)
    if np.all(counts_np == 4):
        quads = idx_np.reshape(-1, 4)
        tris = quads[:, [0, 1, 2, 0, 2, 3]].reshape(-1)
        return pts_np, np.ascontiguousarray(tris, dtype=np.int32)

    tris = []
    offset = 0
    for c_int in counts_np.tolist():
        if c_int >= 3:
            v0 = int(idx_np[offset])
            for k in range(1, c_int - 1):
                tris.append(v0)
                tris.append(int(idx_np[offset + k]))
                tris.append(int(idx_np[offset + k + 1]))
        offset += c_int

    if not tris:
        return None, None
    return pts_np, np.asarray(tris, dtype=np.int32)


def _build_merged_collider_mesh(collider_prims, time_code, xform_cache, hair_min, hair_max, np, skel_ctx=None):
    """Merge AABB-overlapping collider meshes into `(pts_np, tris_np, any_animated)`."""
    from pxr import UsdGeom

    all_pts = []
    all_tris = []
    vert_offset = 0
    any_animated = False

    for c_prim in collider_prims:
        mesh = UsdGeom.Mesh(c_prim)
        c_pts, c_tris = _triangulate_mesh(mesh, time_code, xform_cache, np, skel_ctx=skel_ctx)
        if c_pts is None or c_tris is None or len(c_pts) == 0 or len(c_tris) == 0:
            continue
        c_min = c_pts.min(axis=0)
        c_max = c_pts.max(axis=0)
        if np.any(c_max < hair_min) or np.any(c_min > hair_max):
            continue
        all_pts.append(c_pts)
        all_tris.append(c_tris + vert_offset)
        vert_offset += len(c_pts)
        if mesh.GetPointsAttr().ValueMightBeTimeVarying() or _transform_might_be_time_varying(c_prim):
            any_animated = True
        elif skel_ctx and str(c_prim.GetPath()) in skel_ctx:
            _, skel_q = skel_ctx[str(c_prim.GetPath())]
            anim_q = skel_q.GetAnimQuery()
            if anim_q and (anim_q.JointTransformsMightBeTimeVarying() or len(anim_q.GetJointTransformTimeSamples()) > 1):
                any_animated = True
            elif _transform_might_be_time_varying(skel_q.GetPrim()):
                any_animated = True

    if not all_pts:
        return None, None, False

    return (
        np.ascontiguousarray(np.concatenate(all_pts, axis=0), dtype=np.float32),
        np.ascontiguousarray(np.concatenate(all_tris, axis=0), dtype=np.int32),
        any_animated,
    )
