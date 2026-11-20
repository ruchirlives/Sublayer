# Hair Node Architecture & Developer Guide

The OrchFlow **Hair** node modifies `UsdGeom.BasisCurves` width profiles and, when `simEnabled` is `true`, runs GPU-accelerated XPBD Discrete Elastic Rod simulation and interactive 3D grooming via NVIDIA Warp.

---

## 1. High-Level Data Flow

```
HairInspector.tsx (UI settings: widths, sim, colliders, groomDeltas reset)
        │
        ▼
usd_graph.py :: apply_hair(stage, cfg)
  ├─ Always: _apply_hair_widths(...)          # constant or root-to-tip tapered widths
  └─ If simEnabled:
       └─ hair_sim.py :: apply_hair_simulation(stage, cfg, _hair_query_matches)
            ├─ Static mode: settle XPBD strands, apply saved groomDeltas / topology overrides
            └─ Dynamic mode: simulate frame range [startFrame..endFrame], cache results
```

Interactive grooming runs in the live viewport without rebuilding the whole USD stage on every mouse move:

```
NativeViewport.tsx (pointer down/move/up -> hairBrush requests)
        │
        ▼
ovrtx_worker.py / usd_worker.py
  ├─ step_hair_brush(...)        # updates GPU Warp session & writes curve points
  ├─ export_session_groom_deltas # on pointer-up (commitUsd), encodes sparse deltas
  └─ onCommitHairGroom(...)      # persists hairNode.hair.groomDeltas into project JSON
```

---

## 2. Python Worker Module Layout

The GPU simulation and grooming backend is split across focused modules in `worker/`:

| Module | Responsibility | Key Entrypoints / Symbols |
| :--- | :--- | :--- |
| `worker/hair_sim.py` | Orchestrator & public API | `apply_hair_simulation`, `step_hair_brush`, `export_session_groom_deltas`, `get_pinned_strand_vertex_colors`, `get_active_hair_points`, `pop_topology_changed`, `reset_hair_sessions`, `_ACTIVE_SIM_SESSIONS` |
| `worker/hair_kernels.py` | Lazy Warp init & GPU kernels | `_ensure_warp`, `_build_kernels` (XPBD predict/stretch/bend, BVH mesh & segment collision, comb/grab/smooth/part/puff/clump/pin/backface-masking kernels) |
| `worker/hair_cache.py` | Caching & groom serialization | `_SIM_CACHE`, `_load_hair_disk_cache`, `_save_hair_disk_cache`, `_compute_cache_key`, `_encode_groom_delta`, `_decode_groom_delta`, `_decode_topology_override` |
| `worker/hair_colliders.py` | Collider & space transforms | `_build_skel_context`, `_triangulate_mesh`, `_build_merged_collider_mesh`, `_points_to_world_np`, `_world_np_to_local_vt`, `_transform_might_be_time_varying` |
| `worker/hair_topology_brushes.py` | Topology & drag-line brushes | `_apply_length_or_cut_brush`, `_rebuild_session_topology`, `_apply_add_or_delete_brush`, `_apply_lengthen_drag_brush`, `_apply_cut_line_brush` |
| `worker/hair_guides.py` | Guide-strand brush solver | `step_guide_brush`, `bound_brush_sample`, `bound_full_brush`, `gather_guide_inertia` |
| `worker/hair_rods.py` | Discrete Elastic Rod math | `build_director_transport`, `build_rod_kernel`, `build_twist_kernel` |
| `worker/hair_contacts.py` | BVH contact projection | `build_contact_kernels` |

`hair_sim.py` re-exports `_build_skel_context` and `_triangulate_mesh` so existing callers such as `worker/test_character.py` continue to work without import changes.

---

## 3. Groom Delta Storage (`groomDeltas`)

Saved grooms live inside the single OrchFlow project JSON on the Hair node (`node.data.hair.groomDeltas`), keyed by USD prim path:

- **Vertex-only edits** (`comb`, `surfaceComb`, `grab`, `smooth`, `part`, `puff`, `clump`, `lengthen`, `shorten`, `cut`, etc.):
  - Stored as sparse Z-RLE compressed base64 float32 local-space offsets (`deltasZB64` + optional `pointIndicesZB64`) relative to `base_settled_local_np`.
- **Topology edits** (`add`, `delete` strands):
  - Stored with `countsZB64`, `pointsZB64`, and optional `widthsZB64` so added/deleted curves recompose deterministically.

---

## 4. Verification Commands

Run the Hair & Character Python tests (requires `PYTHONPATH=worker` and `.venv-ovrtx`):

```powershell
$env:PYTHONPATH="worker"; .\.venv-ovrtx\Scripts\python.exe -m unittest worker.test_hair_sim worker.test_character -v
```

Syntax-check all hair worker modules:

```powershell
.\.venv-ovrtx\Scripts\python.exe -m py_compile worker\hair_sim.py worker\hair_kernels.py worker\hair_cache.py worker\hair_colliders.py worker\hair_topology_brushes.py worker\hair_guides.py worker\hair_rods.py worker\hair_contacts.py
```
