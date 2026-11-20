# OrchFlow implementation plan



## Goal



A Windows Rust desktop application with a React Flow graph over a USD scene viewport. Nodes transform USD stage artifacts, expose application handoff controls, and record reversible axis/unit adjustments. Renderer selection uses installed Hydra delegate capabilities.



## Architecture



- Tauri 2 hosts React/TypeScript and exposes narrow commands.

- Rust owns graph validation, persistence, jobs, artifacts, and application adapters.

- An isolated OpenUSD worker initially uses official Python bindings; a native rendering worker will own Hydra and GPU resources.

- The UI consumes frames and scene metadata; it does not implement USD composition.

- A stage reference identifies an external root layer and linked dependencies; original file locations and provenance belong in the single OrchFlow JSON.

- A later MCP server reuses Rust domain operations with structured errors, cancellation, and artifact IDs.



## Milestones



### 1. Desktop and graph foundation â€” in progress



- [x] Tauri shell compiled; React Flow canvas checked in browser.

- [x] Node create/edit/delete, connections, cycle rejection, graph persistence commands. Native dialog save/load verified while preserving and restoring the user's graph during the missing-texture fix.

- [x] USD worker capability detection and real stage inspection (upstream OpenUSD 26.11).

- [x] Sample USD stage and documented development setup.

- [x] Frontend production build, Rust formatting, five Rust tests, three real-runtime Python tests.



Remaining foundation verification: inspection command output through Tauri IPC. Rendering and native dialog save/load through Tauri are verified.



### 2. Native viewport proof â€” implemented and verified



- [x] Provisioned NVIDIA's upstream OpenUSD 25.08 / Python 3.12 Windows imaging build at `I:/AI/OpenUSD-runtime/25.08`, with a checksum-pinned setup helper and separate `.venv-imaging` environment. Core inspection fallback remains upstream 26.11.

- [x] Worker discovers installed delegates (Storm on this machine); delegate selection is validated with no silent fallback. Selected renderer reports AOVs and pause/stop support; schema/material feature parity is explicitly not inferred.

- [x] Persistent isolated worker renders a real layered stage with Hydra/Storm; desktop visually verified showing frames behind the React Flow overlay through Tauri IPC.

- [x] Orbit/pan/dolly, frame-all, bounded resize, graph/scene interaction toggle, selected source/published-output preview, time code, and explicit disk reload implemented. Native GPU integration verifies orbit, resize, Z-up and reload; an automated UI drag was stopped when user interaction was detected, so no claim of an automated end-to-end gesture test.

- [x] Verified on RTX 3090 / NVIDIA driver 610.88. Warm 800x600 layered frame via Rust: 1.31 ms render, 18.75 ms readback+PNG, 20.90 ms worker total, 21.68 ms Rust round trip, 20,701-byte PNG. Use PNG transport for this proof; shared-texture transport remains a future performance option.



Validation: six Rust unit tests plus one opt-in real-worker integration test; eight Python tests with full imaging runtime, including source-file preservation. Frontend build and Rust formatting pass. Desktop displayed the layered stage at 1130x788 with Storm selected and RTX 3090 reported.



Follow-up fix: reproduced a blank imported clothing stage caused by six unresolved shader textures, including a zero-opacity fallback. Added Auto/Scene/Solid material preview, missing-asset diagnostics, and a visible automatic solid-shading warning without source edits. Regression tests cover missing opacity, source-byte preservation, recovery after restoring a texture, relative shader assets, and UDIM resolution from referenced layers. Full imaging suite now has eleven tests.



Verified the corrected clothing preview in the restarted desktop app, with Auto selecting solid shading and reporting six missing assets. Preserved/restored the current graph using native save/open dialogs. The source USD's SHA-256 was unchanged before and after reproduction and verification. Frontend build, six Rust unit tests, the opt-in Rust/GPU integration test, and eleven Python tests passed.



Navigation follow-up: removed the 100 ms debounce and the per-camera stale-response check that starved display during continuous motion. Added an animation-frame-paced latest-view queue with one render/decode in flight, context-based invalidation, reduced-resolution JPEG during gestures, and lossless full-resolution refinement on release. Cached scene bounds and used a single render iteration during motion.



Automated validation: six scheduler tests, twelve Python tests including JPEG motion/PNG refinement and cached-bounds preservation, Rust unit tests and real-worker benchmarks. RTX 3090 clothing-stage measurement (30 warm frames/mode): full 1130x788 PNG median 40.08 ms, p95 40.79 ms; interactive 800x558 JPEG median 5.51 ms, p95 5.82 ms. These are worker/Rust timings, not measured WebView FPS. Final manual orbit/pan/screen verification is left to the user as requested; do not use screen interaction tools to perform it.



Proof limits: idle frames are bounded to 1440x1080 in the UI; interactive previews fit within 800x600. Each render has a convergence budget. Continuous playback/progressive refinement, production color management, and additional renderer/plugin compatibility tests are pending. Worker crashes/timeouts reset it for the next request. Runtime is provisioned for development, not packaged for redistribution.



### 3. Single-JSON storage and dependency editor â€” implemented, manual check pending



Design decision: OrchFlow owns one `.orchflow` JSON file. Native application projects, USD files, textures and other assets stay at their original locations. The JSON stores their original file paths in a shared dependency registry. OrchFlow must not create node folders, per-node JSON files, asset copies, symlinks, packaged projects or managed asset directories.



- [x] Schema v2: root `version`, `nodes`, `edges`, and `dependencies`. A dependency has a stable ID, category and original file path. Nodes reference dependency IDs for input USD, native project, published USD and supporting assets. File paths have one authoritative location in the registry; node labels/layout/settings stay in the same JSON.

- [x] Migrate v1 inline node paths to registry entries in memory on open. Deduplicate repeated original files, preserve graph/UI settings, and write v2 only when the user saves. Never modify the opened v1 file or external files automatically.

- [x] Dependency records may remain registered without a node reference. Shared dependencies can be referenced by several nodes. Missing/offline originals remain valid project links and are reported as unavailable rather than removed.

- [x] Dependencies tab: a spreadsheet-style grid with filename, file type/extension, category, original location, referencing node(s), availability, and a per-row Relink button. Include search/filter and explicit asset registration; referencing nodes are derived and read-only.

- [x] Relink browses a replacement original file and updates the registry entry, preserving its ID and all node references. If the replacement is already listed, warn and merge references into the existing entry rather than creating duplicates. Relinking OrchFlow references does not rewrite links embedded inside external USD/native files.

- [x] Browse stage checks the registry before adding a file. If already registered, reuse that entry for the selected node and show a shared-dependency warning. Compare Windows case/slash differences, normalized paths and filesystem aliases where resolvable.

- [x] Save/load only OrchFlow JSON. Validate IDs, graph topology, dependency references and conversion settings. Preserve missing paths; do not silently relocate, guess replacement files, rewrite external references, or take ownership of originals.

- [x] Automated migration, deduplication, shared-reference relinking and schema validation tests. Leave screen verification to the user.



Implemented v2 shape (transform settings are also stored on node data):



```json

{

  "version": 2,

  "dependencies": [

    { "id": "usd-input", "kind": "usd", "path": "I:/originals/scene.usd" },

    { "id": "native-project", "kind": "nativeProject", "path": "I:/originals/project.blend" },

    { "id": "texture", "kind": "asset", "path": "I:/originals/diffuse.png" }

  ],

  "nodes": [

    {

      "id": "blender-node",

      "type": "stage",

      "position": { "x": 100, "y": 100 },

      "data": {

        "label": "Blender",

        "operation": "application",

        "app": "Blender",

        "axis": "preserve",

        "scale": 1,

        "inputDependencyId": "usd-input",

        "nativeProjectDependencyId": "native-project",

        "publishedDependencyId": null,

        "assetDependencyIds": ["texture"]

      }

    }

  ],

  "edges": []

}

```



### Dependency discovery, missing-file search and pinned preview â€” implemented, manual check pending



- [x] Automatically discover referenced USD layers and assets, including unresolved shader files, using upstream `UsdUtils.ComputeAllDependencies`. Scan after Browse, project open, adding files, and opening Dependencies; provide an explicit Rescan USD action. Native application dependency discovery remains adapter-specific future work.

- [x] Register intended absolute missing paths anchored to their authoring USD layer, rather than omitting unresolved files. Track `usdReferences` provenance (`sourceDependencyId`, authored/intended `path`) so rescan preserves an explicit replacement link instead of recreating the original missing row.

- [x] Make Referenced by node(s) read-only. Derive direct source use, discovered USD provenance and downstream graph consumption. Remove the manual-association dropdown.

- [x] Add row checkboxes/select-all and Find Missing. Choose a directory, search recursively once for selected missing basenames (case-insensitive), and relink unique matches. Available records are skipped. Ambiguous matches require an explicit candidate selection and Use match action.

- [x] Bound searches to 250,000 entries, 64 levels and 100 candidates per file; avoid following directory symlinks/junctions. Report skipped/incomplete searches. Incomplete searches require review even when a single candidate was found. Do not copy/move/edit any original files.

- [x] Add a pin button to each node and its inspector. Only one background output is pinned at a time. Selecting/editing another node leaves that preview in place. Refresh when the pinned node or an upstream node/edge/source link changes; ignore downstream-only edits.

- [x] Persist `viewport.pinnedNodeId` in the same JSON. Preview graph signatures contain only the relevant upstream subgraph and ignore graph layout, labels and unrelated downstream nodes. Deleting a pinned node falls back to selection.

- [x] Tests cover nested missing references, deduplication, rescan after relinking, downstream usage, recursive unique/ambiguous/no matches, source preservation and pin/provenance persistence. Screen checks remain with the user.



Superseded by Asset Override below: replacements now affect the evaluated USD branch through private anonymous layers. Original external files and registry paths remain unchanged.



### Transform, fan-in and Output preview â€” implemented, manual check pending



- [x] Add Source, Transform, Output and Application nodes directly from the toolbar. Transform stores USD translation, XYZ rotation (degrees), and per-axis scale; defaults are zero translation/rotation and unit scale.

- [x] Evaluate every incoming edge for Transform and Output. Inputs are referenced under distinct, stable edge-based namespaces, so matching source prim names do not overwrite one another. Source nodes may also combine their external stage with incoming stages.

- [x] Author transforms with `UsdGeom.XformCommonAPI` on an enclosing Xform, outside instanceable input references. Output is an in-memory composed stage with a USD output socket and Hydra preview; it never automatically writes an external USD file.

- [x] Combine in a canonical Y-up, meters stage using wrapper transforms based on each source stage's own metadata. Source units/up-axis metadata and files are unchanged. Transform translation values are meters. External-application coordinate conversion remains a separate future feature.

- [x] Cache distinct source stages once per evaluation. Preserve existing instancing and mark read-only referenced inputs instanceable where USD supports it. Repeated branches share geometry prototypes rather than authoring independent geometry copies.

- [x] Single-root sources retain their original external layer references directly. Multi-root sources need one private anonymous layer-stack normalization per distinct asset to place all roots (including materials) under a common namespace. `FlattenLayerStack` preserves reference/payload/variant composition arcs; full stage flattening is not used. Namespace edits are restricted to private anonymous layers and originals are never edited. Unsupported namespace edits fail explicitly.

- [x] Preview graph identity excludes canvas positions/labels, so graph layout edits do not rebuild USD scenes. Render through the existing paced worker and keep the graph stages alive for anonymous references. Deliver embedded worker code via stdin to avoid Windows command-line size limits.

- [x] Automated checks cover multi-input bounds, transform order, unit/axis correction, multi-root material bindings, cycles, no-input errors, nonfinite transforms, GPU rendering, and a 20,000-point mesh shared as one geometry prototype across branches.



Limits: Application adapters are still unimplemented; an application node's existing published USD is treated as its external output rather than running the application. No file export, destructive mesh merge, or project-managed USD files are introduced. USD dependency discovery is implemented in the follow-up below. Missing asset warnings also traverse instance proxies.



Manual check remains with the user: create two source/transform branches, connect both to Output, change transforms, select Output, and inspect the Dependencies grid/relink warnings after restarting the updated Rust backend.



### Later: execution services â€” pending



- [ ] Typed sockets, DAG scheduling, cancellation, logs and invalidation when linked originals change.

- [ ] Publication/version provenance recorded in OrchFlow JSON while outputs stay in user-selected external locations; no managed artifact directories.

- [ ] Layer/reference/payload/variant operations, if added, require explicit scope and output destinations. The current storage pivot does not authorize rewriting external assets.



### 4. First application round trip â€” implemented, Blender acceptance pending



- [x] Separate dependency roles for upstream input, generated handoff/layers, editable native project, immutable initial export baseline, raw edited export, published composition and sparse edit layer. All links/provenance remain in OrchFlow JSON.

- [x] Explicit new-destination handoff and Blender native creation; reopen existing native files without reimport. Saving OrchFlow writes no external USD/native/assets.

- [x] Resolve application defaults and node axis/units/additional scale, apply an enclosing geometric conversion, and freeze forward/inverse matrices, graph/dependency revisions and full policy on preparation. Preserve animation time rates/ranges. Native import records Blender's own axis correction separately.

- [x] Blender adapter v2 imports the prepared scene, preserves user identities, captures a baseline export with recorded exporter settings, saves/reopens the native file and publishes both new files exclusively. Baseline is disclosed alongside the chosen native destination. No automatic asset copying.

- [x] Three-way comparison: preserved input vs initial baseline vs edited application export. Retain input elements the application never represented; apply only baseline-to-edited changes authorized by the recorded capabilities and node scope.

- [x] Stable round-trip/prim identity, compatible exact-path matches, explicit prim correspondence grid, deletion permission plus per-prim confirmation, reviewed-change reports, immutable baseline digest and stale-review protection.

- [x] Scope enforcement for transforms, points, topology, UVs, materials, animation and skeleton/skinning edits where correspondence is demonstrably safe. Preserve source transform interpolation using edit deltas. Reject unsupported remapping, invalid topology/UV/cardinality/skin data, ambiguous variants and conflicting property mappings instead of guessing.

- [x] Explicitly collect the user's edited USD, review it, then publish a new composed `.usda` plus sibling `.edits.usda`. Normalize with the recorded inverse to Y-up/meters. Raw export, originals and native project remain unchanged. Only successful publication updates downstream sockets/preview; failed publication retains last valid output.

- [x] Preserve unchanged reference/prototype sharing. Equal edits on eligible shared instance subtrees reuse an edited prototype; differing edits may require explicit instance separation reported by review.

- [x] Detect changed upstream graph or original dependency revisions and require explicit reconciliation. Never automatically reimport over native edits. Recorded publication inputs are checked before preview/composition.

- [x] Explicitly authorize changed verified node permissions against an unchanged baseline, application/adapter version and coordinate contract; preserve original native-import provenance.

- [x] Dedicated Rust commands build/validate publishing context; generic preview commands cannot write handoffs/publications. Embedded worker receives code over stdin, avoiding Windows command-line limits.

- [ ] User verifies Blender v2 import, baseline capture, custom-property retention, edited export settings, physical orientation/scale, materials/rigs, preview slider and downstream updates. No Blender execution or screen verification was performed for this milestone.

- [ ] Adapter-specific native rebase/merge/migration UI, new-geometry import with explicit identity assignment, variant-aware authoring, vertex/UV/joint remapping and other application adapters remain future work. Unsafe cases are blocked or retained as out-of-scope, never silently merged.



Publishing is an explicit external export; its preview/evaluation uses OpenUSD composition in memory. Exporting an already evaluated Output node and portable project packaging remain separate future operations.



### Material interoperability and preservation â€” planned



Material preservation is the default. MaterialX is the preferred exchange representation, with UsdPreviewSurface as a compatibility/preview fallback. OpenPBR is a target within the MaterialX strategy, subject to verification of the installed OpenUSD, MaterialX, renderer delegate and adapter versions. Native shader networks remain editable in each external application's native project file. Do not silently convert arbitrary shaders at application boundaries.



| Representation | Intended use | Limits to handle explicitly |

| --- | --- | --- |

| UsdPreviewSurface | Compatible PBR preview/fallback | Limited advanced/procedural shading; verify each adapter's support |

| MaterialX / OpenPBR | Preferred portable shading networks | App/delegate/version support varies; test the pinned runtime rather than assuming full support |

| Native application materials | Preserve the application's full editable shading workflow | Arbitrary native networks may not transfer faithfully |

| Baked PBR textures | Optional interoperability fallback for unsupported shaders | Loses procedural editability; requires explicit bake settings and external destinations |



Planned USD Scene â†’ Application Node â†’ Output â†’ Hydra workflow:



- [x] Retain incoming material definitions, texture references, networks and bindings alongside geometry. USD may carry several material representations/render contexts for an object; retain those that an adapter does not understand.

- [ ] Add an application-node material policy stored in OrchFlow JSON. Choices: **Preserve incoming; override only explicitly changed materials** (default), **Convert compatible materials to MaterialX/UsdPreviewSurface**, **Bake unsupported shaders to PBR textures**, or **Replace with exported application materials** (explicit choice).

- [ ] Add material conversion as an optional node operation, not an implicit step on every application boundary. Native Blender/renderer graphs are not assumed to translate automatically through UsdShade; adapters or a dedicated converter/baker must implement supported translations.

- [ ] Discover and report MaterialX/OpenPBR/UsdPreviewSurface support per installed renderer and application adapter. Validate Storm using the pinned OpenUSD imaging runtime and representative networks, texture color spaces and shader features. Do not present renderer support as interchangeable or universally complete.

- [ ] Use UsdShade render contexts and purpose-specific preview/full bindings where appropriate; validate material selection in each delegate. Define fallback and missing-texture diagnostics without changing source shader networks silently.

- [x] Preserve incoming materials when an application supplies geometry-only changes. Retain the original scene/material references, apply only eligible geometry overrides, and reapply existing bindings only when prim/geometry correspondence is verified.

- [x] Track stable prim identity, topology, UV sets, face/subset material assignments and deformation correspondence. Topology or UV changes can invalidate bindings/texture mapping; require explicit remapping or report the incompatibility rather than guessing.

- [ ] Capability-gate mesh-focused application exports (including the ZBrush adapter candidate). Their output must not automatically replace a complete incoming USD scene. Verify the installed plugin's actual behavior before enabling preservation/merge strategies.

- [x] Keep selective overrides in anonymous USD layers for in-memory evaluation. If an external publication/bake is explicitly requested later, require user-selected destinations; no managed material/texture directories, copies or symlinks are introduced by project save.

- [ ] Register material documents, textures and bake/publication outputs as external dependency links in the single OrchFlow JSON. File relinking alone does not translate shaders or rewrite authored external references.

- [x] Preserve shared USD instancing where material/geometry edits are compatible with sharing. Per-branch differing overrides may require distinct variants/prototypes or explicit de-instancing; never duplicate large geometry merely to retain an unchanged material.

- [ ] Automated fixtures for multiple render contexts, preview/full bindings, geometry-only overrides, topology/UV mismatch, shared-asset instancing, missing textures, explicit replacement and source-file preservation. Leave final visual verification to the user.



Initial material checkpoint implemented:



- [x] Read-only per-node output material inspector for direct stages and in-memory graph outputs. Report surface render contexts, shader IDs, allPurpose/preview/full bindings (including face subsets), missing shader assets and result limits.

- [x] Query installed Sdr definitions without claiming renderer parity or automatic native-shader translation. Verified the pinned runtime exposes 785 MaterialX definitions and four OpenPBR-related identifiers, including the OpenPBR surface definition. Cache capability discovery outside interactive rendering.

- [x] Validate multiple render contexts, subset bindings and unchanged source bytes. Geometry-selective override authoring is implemented by phase 4. Material conversion/baking and wholesale replacement remain planned.



Acceptance: a geometry-only edit must retain the original materials where correspondence permits; unsupported conversions must be reported; incoming networks must remain available unless replacement was explicitly chosen. MaterialX integration should begin with runtime/capability discovery and preservation tests before conversion/baking tools.



### 5. Broader USD and render support â€” pending



- [ ] Layer/variant inspector, asset resolver settings, schema/plugin discovery.

- [ ] Additional application adapters and renderer integration tests.

- [ ] Compatibility matrix: runtime support, renderer support, external-app preservation.

- [ ] Packaging with version-pinned runtime and plugin environment isolation.



### 6. MCP server - local stdio implementation complete; active UI bridge deferred



- [x] Expose validated project JSON, graph evaluation, artifact creation, inspection, MaterialX and rendered preview operations.

- [x] Reuse domain services rather than driving the UI.

- [x] Local stdio transport, explicit destination writes and revision-checked project saves; no active desktop session mutation.

- [x] Real stdio client tests cover initialization, tools, project saves, MaterialX export and real Storm images. Codex registration verified.



### Portable packaging â€” outside the current design



The earlier managed-folder, symlink and Make Portable proposals are superseded. OrchFlow currently owns only its JSON; packaging/copying external dependencies is not part of this implementation.



## Acceptance constraints



Never mark USD preview complete with a decorative placeholder. Never treat launching an application as a completed round trip. Persist conversion provenance; no destructive flattening by default. Unavailable renderers remain unavailable rather than silently falling back.









Current manual test point: restart the updated standalone build, open the clothing project, inspect the six missing shader rows, exercise selected-file Find Missing/candidate choices, pin a Transform/Output while editing an upstream node, and inspect output materials. Automated checks: frontend tests, Rust domain/search tests, Python/OpenUSD tests and Rust-to-Hydra integration; no screen automation used.





## Asset Override nodes â€” implemented



This supersedes the global Dependencies tab and global asset-relink behavior above.



- [x] Add Asset Override as a graph operation; combine every incoming USD connection.

- [x] Own a modal dependency grid on the node, scoped to dependencies discovered from its upstream sources. Open using **Edit Asset Overridesâ€¦** in the inspector.

- [x] Show original and replacement paths, type, derived referencing nodes and effective file availability. Keep multi-selection, recursive Find Missing, explicit ambiguous choices, rescan and reset.

- [x] Persist original absolute asset-path â†’ replacement dependency-ID mappings in that node's JSON settings. Reuse existing replacement file links with a warning. Source registry entries stay intact.

- [x] Apply replacement asset paths, including composition dependencies, using anonymous copies of affected layer specs and their composition ancestors. Preserve reference/payload arcs and instanceability; cache identical input/mapping combinations. Never save external files or flatten per branch.

- [x] Update selected/pinned downstream previews when upstream mappings change; sibling source branches stay independent. Chained overrides use original-path provenance.

- [x] Verify branch isolation, chaining, identical-override prototype sharing, unchanged source bytes and JSON persistence. Real Storm test confirms replacing a missing opacity texture restores authored scene-material preview without creating the missing original file.



Current scope: overrides match resolved/anchored absolute asset paths throughout this node's inputs, rather than per-prim selection. Two occurrences of the same absolute file share one mapping. Application adapters, material conversion/baking, export and MCP remain pending. The registry remains internal to the project; the editable grid is the Asset Override modal.



Manual checkpoint: restart the updated standalone build; connect **Source â†’ Asset Override â†’ Output**. Select Asset Override, open **Edit Asset Overridesâ€¦**, replace a missing texture (or select rows and Find Missing), then inspect/pin Output. Compare the Source preview, test Reset, and save/reopen the project. No screen automation was used.



## Existing native-project reopening checkpoint



- [x] Blender Application node exposes native-project and executable Browse/Relink, availability checks, and Open in App.

- [x] Persist `appExecutableDependencyId` (dependency category `executable`) alongside the existing native-project link. Older v2 files default to no executable. Saving writes only OrchFlow JSON.

- [x] Domain launch preparation validates the graph, application, absolute existing file links, executable extension and `.blend` native extension. Process spawning runs outside the UI thread with an argument vector, never shell interpolation. Process exit does not trigger publication.

- [x] Tests cover literal paths containing spaces/shell metacharacters, registry round trip, executable reuse/relink, missing files, directories, relative paths, unsupported apps/node operations and unchanged native bytes. Frontend build and Rust formatting pass.

- [ ] User checks actual Blender GUI reopening and save/reopen link persistence. Installed Blender was found at `C:/Program Files/Blender Foundation/Blender 5.2/blender.exe`; no GUI launch was performed.



This historical checkpoint completed reopening only. First import, coordinate snapshots and selective publication are now implemented in phase 4; Blender acceptance and native reconciliation tools remain pending.



## Application coordinate preparation â€” implemented draft checkpoint



- [x] Historical draft design, superseded by the application-wide catalog: `applicationProfiles` stored an application's chosen up-axis and meters-per-unit. Start with an editable Blender Z-up, 1 m/unit target; this is a preparation preference, not detection of a native scene's actual settings. Other apps require explicit profile configuration.

- [x] Application nodes resolve profile settings plus node up-axis/unit overrides and positive additional scale (default 1). Show effective target units, axis and linear factor.

- [x] Compose all incoming USD branches plus an explicitly linked input file for preflight. Treat upstream applications as existing publication leaves. Never use the target application's published output as its own input.

- [x] OpenUSD applies a non-destructive enclosing geometric transform to an anonymous handoff stage. Compute forward and inverse Gf matrices from canonical Y-up/meters: factor = additional scale / application meters-per-unit, then basis rotation. Show input and prospective handoff dimensions.

- [x] Explicitly record `coordinateDraft` in node JSON: resolved conventions, scale, matrices, OpenUSD version, graph digest and linked-input metadata revision. No extra files or automatic native creation/import/export. Existing projects continue to reopen without applying the draft.

- [x] Recheck compares current input/settings with the recorded draft; differing revisions are reported for future reconciliation. Revision uses paths, size, modification time and missing state, not file-content hashes or live filesystem watching.

- [x] Tests cover Y/Z, meters/centimeters/millimeters, non-unit additional scale, forward/inverse point recovery, shared multi-input composition, changed input revisions, source-byte preservation, JSON persistence and invalid units/profile/matrix values.



Important boundary: this is a prospective coordinate contract, not the completed native-project preparation snapshot in phase 4. Importer/exporter version/options, native-project identity and successful creation provenance must be recorded at actual first import. An exporter's own conversions must be verified before deciding which return corrections remain to apply. The shown full inverse is valid only when the complete handoff convention is retained in exported coordinates. No Blender round trip is claimed.



Manual checkpoint: connect one or more Sources/Transforms to an Application node. Choose Blender, use **Check input conversion**, try Z-up, 0.01 m/unit and scale 2, inspect dimensions/matrices, then **Record preparation draft in project**. Save/reopen and recheck. Change an upstream transform or profile and recheck to see the draft mismatch. Native project reopening remains unchanged.



## Explicit handoff export and application preview distinction â€” implemented



- [x] **Create handoff USDâ€¦** chooses a new `.usda` root and explicitly discloses its sibling `.layers` output folder. Export only generated anonymous graph/override/coordinate layers, preserving references/payloads/instanceability and original external asset links. Do not flatten a complete stage or copy original asset dependencies.

- [x] Recompute input preflight during creation; reopen generated layers for validation before publishing the root. Refuse existing root/folder destinations, including original inputs. Clean owned partial outputs on ordinary export failure. No native project creation/import/export or automatic overwrites.

- [x] Dedicated `create_handoff` Rust command validates project/node/settings and constructs the input graph itself. The generic preview/inspection command cannot invoke the write operation.

- [x] Register handoff root/layer dependency IDs and actual `CoordinateDraft`-shaped conversion record under `node.data.handoff`. Keep this handoff snapshot separate from native project/published output and from an unexecuted preparation draft. Original referenced files remain live links.

- [x] Add Handoff/Published preview slider, applying to the selected unpinned Application or the pinned Application. Preserve session choice per node. Downstream execution still uses published USD. Incoming-scene inspection remains separately available before conversion when unpinned.

- [x] Explain missing artifacts with specific empty states, avoid render requests for missing application outputs, and replace misleading preparing text on rendering errors. Application cards show publication state.

- [x] Real OpenUSD tests verify persistent layer reopening, shared prototypes, converted dimensions/units/axis, branch override persistence, existing-output refusal and unchanged sources. Frontend tests cover distinct preview roles, empty states and snapshot/registry persistence. Rust validates snapshot links and constructs the application input independently.



Manual test point: create a handoff, inspect its generated root/layer folder, save/reopen OrchFlow, and toggle Handoff/Published with the Application selected and pinned. Slider selection is session UI state. Creating a new handoff does not populate published output or create a native project. External importer/exporter double-conversion behaviour, native-project-bound provenance and first-import automation remain pending.



## Skeletal composition compatibility fix



- [x] Reproduced the reported `protoPath == newProtoPath` / skeleton-adapter unknown-prim errors with `I:/CGPROJECTS/Uranus/Uranus.usd` on upstream OpenUSD 25.08/Storm. The source root is a `SkelRoot`; automatically making enclosing graph references instanceable placed skeletons inside nested prototypes.

- [x] Avoid automatically marking references instanceable when their input stage contains SkelRoot/Skeleton prims, including source wrappers, graph fan-in and handoff conversion wrappers. Preserve USD reference sharing and original authored data; no stage flattening or authored geometry copies per branch. Ordinary mesh assets retain automatic prototype sharing. Skeletal branches require separate renderer evaluation and are not claimed to share Hydra geometry buffers.

- [x] Tests preserve skeletal animation query results across branches and exported handoffs, render skeletal graphs/handoffs through real Storm, and retain static-mesh sharing tests. Re-rendered the actual Uranus input without the reported prototype errors; source checksum unchanged. A separate source material-binding API warning remains and was not suppressed or rewritten.

- [x] No legacy handoff repair/migration/session fallback, per user instruction. Previously generated handoffs retain their old instance metadata and must be recreated at a new destination.



## First Blender import and graph insertion â€” implemented, manual checks pending



- [x] Create Blender project from recorded handoff with user-selected new `.blend` destination. Reject existing native links/destinations; preserve native edits and all existing publications. Launch a fixed embedded background adapter with JSON stdin, factory-empty scene and no shell interpolation.

- [x] Probe required importer options at runtime. Keep textures external (`IMPORT_NONE`), prevent a second unit conversion, set native scene unit scale, and record Blender's built-in Z-up conversion in actual native matrices. Capture original handoff conversion, adapter/Blender versions, options, dependency revision digest and imported counts in `node.data.nativeImport`.

- [x] Save/reopen a temporary native file, publish with exclusive filesystem linking, and clean owned temporary files. Bound background execution to 15 minutes and drain/cap logs. Link the result only after a completed adapter report, then request GUI reopening. Native Save and USD publish remain separate.

- [ ] User verifies complete Blender first-import execution, materials/rig/instances, physical dimensions/orientation and native reopening. User explicitly requested to perform Blender verification themselves; only API-option probes were run, not a completed first-import test. Do not mark round-trip acceptance complete.

- [x] Drag toolbar node types onto edges/nodes/empty canvas. Edge insertion reconnects source/new/target, preserving the downstream edge ID/target handle. Source drops connect downstream; other-node drops connect upstream. No silent node-type changes.

- [x] Shift all affected downstream nodes right as a group, preserving relative Y positions and unrelated nodes. Traverse branched/rejoined descendants once. Switch to Graph mode on toolbar drag and highlight drop targets.

- [x] Frontend domain tests cover insertion direction, branching/rejoin shifts, edge handles/identity, empty-canvas drops, invalid targets, immutable project data and native link/provenance registration. Rust guards existing native links; frontend production build and Rust formatting/tests pass. Screen/gesture checks remain with user.



## Application JSON and node scope â€” implemented configuration/policy layer



This replaces the earlier project-level `applicationProfiles` proposal. Only two configuration levels exist: application-wide JSON and node settings. Profile copies in handoff/native records are immutable provenance snapshots.



- [x] Separate `applications.json` in Tauri's application configuration directory, bundled initial template, bounded/validated reads, explicit atomic saves, stable profile IDs and monotonic revisions. Project saves never write this catalog.

- [x] Dedicated Applications tab with editable profile table and capability status/evidence table, add/remove, executable Browse, Save, Reload/discard and complete raw JSON view. Table edits advance revisions; app/adapter version edits reset capability assertions.

- [x] Store application/version/adapter identity, executable defaults, axis, units, default scale, importer/exporter behavior and explicit supported/unsupported/unverified records for transforms, points, topology, UVs, materials, animation, skeletons/skinning and deletions.

- [x] Node-level profile ID, edit-scope selection and coordinate/executable overrides. Grey unsupported/unverified/unknown categories with reasons; preserve-all default; deletion opt-in is distinct. Invalid retained scopes can be cleared explicitly.

- [x] Backend independently rejects unsupported scopes, unknown profiles and mismatching app identities at handoff/first import. Freeze full profile and scope in handoff/native policy snapshots; require matching policy for first import. Validate historical snapshots against their own recorded capabilities, not changed live defaults. Reopening is independent of scope capability changes.

- [x] First-import adapter verifies actual Blender version against profile pattern and requires adapter v2; no Blender execution checks were run, respecting user's instruction.

- [x] Reject project-level profiles and policy-less handoff/native records explicitly. No compatibility conversion/migration for this redesign.

- [x] Tests cover fail-closed gating, version reset, revisioned atomic persistence/repair, invalid profiles, old-layout rejection, immutable policy snapshots and JSON/table parsing. Production frontend/Rust builds and formatting checked.



Selective publishing is now implemented in phase 4 and the checkpoint below. Raw application exports cannot bypass review as complete replacements. Bundled round-trip capabilities remain unverified until validated; enabling a capability does not remove correspondence/conflict checks.



## Selective publishing checkpoint â€” 2026-11-08



Implemented: `nativeImport.baselineDependencyId`, adapter v2 baseline digest/exporter options, round-trip IDs, `publicationPolicy` explicit authorization, `publication` artifact IDs/report/provenance, review/mapping/deletion modal, new external sparse-layer publication and downstream promotion. No legacy baseline synthesis or migration. Old native preparations must be recreated on a new node/new destinations; existing native files can still be reopened where their current schema is valid.



Automated verification: frontend production build, frontend domain tests, Rust formatting/unit tests, upstream USD 25.08 fixtures and real headless Storm rendering. Fixtures cover dropped unsupported input data, units/axis, animation/transform interpolation, geometry/UV/material/subset/rig changes, stable mappings, explicit deletion, stale input/review rejection, exclusive outputs and a 20,000-point shared-asset edit. Embedded-worker dispatch is tested independently of Blender. Verified: 52 upstream USD tests (including 22 publisher tests), 31 frontend tests, 23 standard Rust tests and the opt-in Rust/real-GPU worker test pass. Production frontend build and Rust formatting pass; the custom-protocol desktop executable built at `I:/AI/OrchFlow-build/debug/orchflow-updated.exe`. Blender adapter syntax compiles; Blender execution remains untested by request.



User checkpoint:

1. In Applications, use Blender adapter version 2 and an actual matching application version. If a saved catalog uses adapter 1, edit it explicitly and verify the relevant capabilities; no silent migration occurs.

2. Create a new handoff and new native project. Confirm the disclosed sibling `.baseline.usdc` appears. Save OrchFlow. Existing native projects are never overwritten/reimported.

3. In Blender, edit a known mesh and export the complete prepared scene with the recorded exporter options/custom properties. Keep the round-trip identity, export axis/units/time rate and material/texture links.

4. Use **Review / Publish edited USDâ€¦**, browse the export and analyze. Inspect preserved/conflicting changes, correct any explicit prim mapping and confirm only intended deletions. If permissions changed, use **Authorize current scope for this baseline** first.

5. Publish to a new filename, compare Handoff/Published, inspect downstream Output and save/reopen OrchFlow. Change an upstream transform and confirm further publication requires reconciliation while native edits and the last valid publication remain available.



Remaining feature work: explicit shader conversion/baking, arbitrary new geometry/variant/rig remapping, native reconciliation tools, more adapters/renderers, portability export and MCP. These are not claimed by this checkpoint.


## Explicit upstream reconciliation and sparse adjustments - implemented 2026-11-08

The revised workflow uses USD composition with existing node opinions stronger than current upstream. It does not present conflict review. Native scene merge/reimport is a separate future feature.

- [x] Application **Reconcile upstream...** action reuses the last publication adjustment layer over current validated incoming graph, using frozen publication conversion. No Blender execution, new exported USD, or unchanged old input revision is required.
- [x] Explicit new destination writes root `.usda` plus sibling `.edits.usda`, `.input.usda` and `.input.layers` folder. Generated composition layers reference original assets without copying them. Refuse existing/registered destinations and clean owned partial outputs on ordinary failure.
- [x] Strong node opinions win at matching USD paths. Untouched upstream properties, new objects and unoverridden transforms/materials flow through. Array attributes replace whole arrays; no additive sculpt interpretation, conflict review or namespace guessing.
- [x] Rebind shared edited prototypes to current input and refresh unedited instance-root properties. Anchor relative asset/shader paths correctly when choosing another output directory.
- [x] Keep immutable native/baseline/handoff provenance, original export links and previous output files. Register new reconciled input/layer dependency IDs, graph revision, fresh source revisions and report. Promote output only after success; reject stale asynchronous UI promotion.
- [x] Prune strictly identical properties and redundant values/connections/metadata from newly generated adjustment layers against preserved input, in addition to baseline comparison. Keep required USD declarations, meaningful blocks/removals and changed whole arrays. Do not discard an existing edit during reconciliation simply because updated upstream happens to match it.
- [x] Automated cases cover stronger point precedence, new cameras/properties, upstream graph transforms, Z-up/centimeters/additional scale, repeated reconciliation, exclusive output, unchanged native/export files, shared edited 20,000-point prototypes, redundant UV omission, interpolation inheritance and registry/provenance persistence.
- [ ] User verifies the new button, file destinations, Handoff/Published and downstream previews, and project save/reopen. No screen tools or Blender execution used.

Manual checkpoint: publish once, change upstream Transform/source data, choose **Reconcile upstream...**, and save to a new filename. Output should contain new upstream data with node-authored properties still winning; Blender/native/export files need no resave. Save/reopen OrchFlow. Handoff remains the original native preparation; Published uses reconciled input. Later Blender-export review still uses its original preparation baseline; this feature does not migrate that native scene/baseline.

Native-project rebase/reimport, automatic namespace/vertex remapping and additive sculpt-delta authoring remain future work. Verification passed: 55 upstream USD tests (25 publishing/reconciliation), 31 frontend tests, 23 standard Rust tests and the opt-in Rust/real Storm worker test on RTX 3090. Frontend production build and Rust formatting pass. Updated custom-protocol executable: `I:/AI/OrchFlow-build/debug/orchflow-updated.exe`. Blender execution and screen verification remain with the user.

## Undo/redo, application templates, MCP and MaterialX authoring - 2026-11-08

- [x] Session-only bounded project history; Undo/Redo buttons and standard shortcuts, grouped node drag/field edits, restored graph/dependency/pin state, branch-safe redo and reset on project open. Preserve native input undo and exclude viewport/selection/discovery changes. External side effects are never reversed.
- [x] Bundled ZBrush, Marvelous Designer and Substance Painter profiles with official vendor references, app-owned axis/unit preferences, explicit provisional defaults and fail-closed edit categories. Add missing bundled templates without overwriting existing profiles; no fake native adapters or verified round trips.
- [x] Rust stdio MCP server reusing project/application/USD domain operations. Initialize/negotiation, tools/list/call, safe JSON saves with revision tokens, explicit output operations, error results and image blocks. Registered as `orchflow` in Codex with the pinned runtime. Does not control the active desktop UI or launch Blender.
- [x] MaterialX graph node with Standard Surface, universal PreviewSurface fallback, base color/metalness/roughness/specular/opacity, external color/roughness texture links, explicit branch bindings and stable preview invalidation. Bind at root/instance root to retain sharing; no silent instance-proxy edits.
- [x] Explicit new-destination material-only `.mtlx` export, parsed with the upstream USD MaterialX file-format plugin. Keep original textures linked and preserve incoming USD networks. Arbitrary MaterialX shader-graph editing/import, OpenPBR authoring, automatic conversion/baking and native material translation remain future work.
- [x] Automated history/registry/profile tests, backend parameter validation, real USD dual-context/binding/instancing/document/texture fixtures, real Storm MaterialX rendering and stdio MCP create/validate/save/render/document integration.
- [ ] User checks toolbar/keyboard undo on graph drag/delete/settings/links, editable new profiles, MaterialX controls/bindings/export and saved project reopening. Reload Codex MCP connections/new chat if newly registered tools are absent. No screen automation or Blender tests performed.

Verified: 36 frontend tests, 24 standard Rust tests, 60 upstream USD tests (including real Storm MaterialX rendering), and live stdio MCP handshake/15 tools/project safety/MaterialX export/render integration pass. Frontend production build and Rust formatting pass. Desktop executable: `I:/AI/OrchFlow-build/debug/orchflow-updated.exe`; MCP: `I:/AI/OrchFlow-build/debug/orchflow-mcp.exe`. No Blender or screen verification performed. MaterialX capability discovery/preservation remains distinct from arbitrary shader translation. MCP is no longer deferred; new workflow APIs must remain reusable through domain modules.

## Windows toolbar drag/drop correction - 2026-11-08

- [x] Found the default native Tauri/WebView2 drag-drop interceptor still enabled. Set the window's `dragDropEnabled` to false so existing HTML5 toolbar/canvas handlers receive Windows drag events. Native file selection continues through Browse dialogs.
- [x] Kept existing insertion/spacing/history behavior and frontend gesture handlers. Native configuration change requires a rebuilt/restarted desktop window.
- [ ] User checks toolbar drag onto empty canvas, edges, Source and other nodes in the rebuilt app. No screen interaction used. Build/check results recorded below.

Drag/drop fix verification: frontend production build, Rust formatting, three insertion domain tests and 24 Rust tests pass. Native configuration compiled into `I:/AI/OrchFlow-build/debug/orchflow-dragfix.exe`. The launcher selects the newest successful known desktop build, so the old running executable did not need to be closed or overwritten. Actual Windows gesture verification remains with the user.

## Selective Auto material preview - 2026-11-08

- [x] Auto keeps scene materials enabled and traverses material output connections to identify networks depending on unresolved texture or shader implementation assets. Unconnected missing assets do not invalidate an otherwise healthy material.
- [x] Replace only affected material networks with opaque neutral PreviewSurface/MaterialX fallback networks in private anonymous layer snapshots. Keep material bindings, face subsets, reference arcs and shared prototypes; never edit/save original layers or author preview fallbacks into graph publications.
- [x] Cache fallback stages per scene/time, reset the imaging engine when switching stage roots, restore original shading in Scene mode and keep explicit all-solid mode. Report `mixed` shading plus affected material paths and accurate missing-asset warnings.
- [x] Regression fixtures cover healthy colored material pixel output next to missing-opacity geometry, unconnected shaders, implementation assets, inherited instance materials, face subsets, one shared prototype, mode switching, asset restoration/relinking and unchanged source bytes.
- [ ] User checks the supplied scene in restarted Auto mode, then Scene/Solid and Reload after repairs. No screen interaction used.

Selective material preview verification: 64 upstream USD tests (including real Storm pixel/mode checks), 24 Rust tests, frontend production build, formatting and live MCP integration pass. Updated desktop `I:/AI/OrchFlow-build/debug/orchflow-updated.exe` and MCP executable rebuilt successfully. The launcher selects this newer desktop build; manual verification remains pending.

## Relink path spelling and invalid UV-reader preview - 2026-11-08

- [x] Find Missing returns ordinary drive/UNC paths instead of Rust's verbatim `\\?\` spelling. Registry path normalization/deduplication also cleans previously returned prefixes without editing original files or authored USD provenance.
- [x] Reproduced Storm `GetGLSLIdentifier` errors on the user's Uranus stage after relinking available textures: two UsdPrimvarReader nodes request `_`, rather than an actual usable UV name. Auto treats connected invalid readers as material-local fallback reasons; no UV set is guessed and no source shader is rewritten. Scene mode reports an actionable error before the native shader failure; Solid remains usable.
- [x] Actual headless Storm reproduction: 37 links repaired in an in-memory graph, 8 unresolved shader assets, 2 invalid readers, 4 fallback materials, successful mixed shading. All referenced source-layer checksums unchanged. A separate pre-existing missing MaterialBindingAPI warning remains unmodified.
- [x] Added prefix normalization/deduplication and invalid-reader preview/mode regression tests. Final automated checks/build recorded below. User performs desktop verification.

Relink/UV fix verification: 37 frontend tests, 65 upstream USD tests, 24 Rust tests, focused invalid-reader/unused-network regressions, frontend build, formatting and rebuilt MCP integration pass. Uranus mixed preview was rendered headlessly with source checksums preserved. Updated desktop build is `I:/AI/OrchFlow-build/debug/orchflow-dragfix.exe`; the launcher selects the newest build.

## Node-drop connection direction - 2026-11-08

- [x] Dropping a toolbar node onto Source, Transform, Application, Asset Override or MaterialX connects from the target output socket to the new node. Place the new node downstream and shift reachable descendants once; leave the target in place.
- [x] Output targets remain the exception: connect new node to Output input, shifting Output and descendants. Preserve existing edges, unrelated layout and undo grouping. Edge insertion and empty-canvas placement are unchanged.
- [x] Direction/spacing regressions cover every target type plus branches/rejoins and existing edge preservation. User verifies drag gestures; no screen interaction used.

Node-drop direction checks: 37 frontend tests, production frontend build and Rust formatting pass. Desktop executable rebuilt with the new behavior; backend domain behavior was unchanged.

## Scene overview and viewport selection - 2026-11-08

- [x] Left collapsible hierarchy panel follows the selected/pinned USD preview, including application preview role and composed graph paths. Lazy child loading, schema/instance labels, 500-sibling pagination, loading/errors and refresh.
- [x] Select a geometry prim or ancestor for native Hydra orange selection highlight; clear selection and reset it on stage identity changes. Include selection in render response invalidation to avoid stale highlights. No USD, graph or project history mutations.
- [x] Read-only `scene_children` worker/MCP operation uses upstream composed USD with instance proxies; original layers remain unchanged. No mesh-only loader or screen interaction.
- [x] Automated fixtures check direct/graph hierarchy paths, instance proxy children, invalid parents, real Storm highlighted/cleared image differences and source preservation. Final checks/build follow.
- [ ] User checks sidebar expansion, selection on the supplied scene, pinned previews and save/reopen behavior. Geometry/ancestor selections highlight; material/shader prims have no own geometry highlight.

Scene overview verification: 68 upstream USD tests, 24 Rust tests, frontend build/formatting and live MCP 16-tool hierarchy/render integration pass. Desktop rebuilt at `I:/AI/OrchFlow-build/debug/orchflow-updated.exe` because the open dragfix executable was locked. The launcher chooses the newer successful build; no user process was closed.

## Explain disabled native-application actions - 2026-11-08

- [x] Creation/Open controls derive their disabled state and visible explanations from the same prerequisite lists. Explain absent handoff versus preparation draft, Blender adapter v2, existing native link, executable, unsupported template app, desktop context and active operation.
- [x] Do not silently change profile versions, create handoffs/native files or bypass backend preparation checks. Executable selection alone cannot enable native creation. No Blender or screen verification performed; user checks the displayed reason.

## Requested desktop verification of native opening - 2026-11-08

User explicitly authorized desktop takeover. Observed the running OrchFlow window: native project `I:/CGPROJECTS/Uranus/project.blend` and Blender executable both report Available; Open in App is enabled, while native creation is disabled because the node already has a native project. Clicked Open in App and observed the launch status/process followed by a Blender 5.2.1 LTS window titled with the linked project and its Handoff scene collection. Native reopening is manually verified for this project. No USD import, native save/edit or publication was performed. This does not verify a complete native round trip. The running window predates the newly added disabled-reason labels. Observed viewport frame diagnostics were approximately 45-55 ms round trip; this is rendering/transport timing, not a measured button or Blender-load latency. No UI responsiveness bottleneck was established by this check.

## Geometry edit missing from publication - 2026-11-08

- [x] Inspected user's real files read-only. `project-edited.usdc` contains two changed Body vertices; `published.edits.usda` is a 119-byte input-only layer. Live node has empty edit scope and unverified capabilities, so publication omitted edits under the preservation policy.
- [x] Fixed stable prim identity detection for Blender animation exports whose custom string IDs are authored only as constant time samples. Actual mapping improved from unmapped Body data to all eight meshes mapped by identity, including an extra `/root` export prefix. Reject changing identity values rather than guessing.
- [x] Prevent a zero-applied-edit publication from silently writing another unchanged output. UI explains scope/capability authorization and disables Publish; backend also rejects it before file creation. Existing output is retained.
- [x] Identified a separate exporter mismatch: baseline spans frames 152-352 while edited export is a static frame with an added root. Do not silently treat loss of animation as a permitted geometry edit. User must match recorded export settings or resolve reported differences explicitly.
- [x] Regression tests cover sampled identity plus renamed export root and empty-scope no-op rejection. No capabilities were silently enabled, user USD/native files rewritten or new user publication produced. Final checks/build recorded below.

Geometry-publication fix checks: 70 USD tests, 24 Rust tests, frontend build and formatting pass. Updated desktop at `I:/AI/OrchFlow-build/debug/orchflow-dragfix.exe`. The active MCP executable was locked, so an updated MCP binary was built separately and the existing `orchflow` Codex registration updated to `I:/AI/OrchFlow-build/debug/orchflow-mcp-updated.exe`; active clients must reconnect to use it. No running app/server was terminated. User source/native/export/publication files were only read.

## Compact application workflow and shared executable defaults - 2026-11-08

- [x] Replace the long application sidebar with primary prepare/create or open actions, output publishing, an Application settings shortcut, and closed rollups for Allowed edits, Publication details and Advanced node settings. Hide redundant Create when a native project is already linked.
- [x] Move capability descriptions/configuration and persistent executable defaults to Applications. Shortcut focuses the node's profile even on repeated visits. Executable configuration is in Applications only; Advanced can clear previously saved overrides without silently deleting them. Preserve unsaved catalog drafts and historical snapshots.
- [x] New profile selections default node scope to all supported categories only; unverified/unsupported remain disabled. Existing scopes/baselines are not silently expanded. Allow all capable updates an existing node explicitly, and changed native publication permissions still require Apply.
- [x] Application nodes normally consume connected Source/graph outputs; remove the Additional input picker. Retain existing direct links rather than silently changing input, with an explicit removal action in Advanced only when one exists.
- [x] Rename Application coordinate preparation to Application preparation. Put axis, units, scale, matrices and checks in a closed Coordinates and scale rollup. Preparation actions remain visible when needed.
- [x] First-import preparation comparison ignores executable-only/revision-only differences while still rejecting changed coordinates, versions, capabilities or scope. Selecting an executable after handoff does not require regenerating unchanged scene data.
- [x] Automated default-scope, executable-persistence and immutable preparation-contract tests; final builds/check totals recorded below. No user application configuration or native/USD files were changed automatically. User checks layout and defaults in the app.

Preparation action clarification: Create handoff USD remains an independent USD-only action. Create Blender project is a separate button and operation that imports the already prepared handoff; Open in App reopens the existing native project. No automatic handoff-to-native chaining was added.

Executable UI refinement: removed executable Browse, path editing and promote-to-default controls from the sidebar entirely. Configure executables in Applications only. Existing saved node overrides are not silently deleted; Advanced exposes only an explicit Use global application default action to clear them.

Compact-sidebar checks: 39 frontend tests, 25 Rust tests (including executable-only preparation compatibility), production build and formatting pass. Desktop rebuilt at `I:/AI/OrchFlow-build/debug/orchflow-updated.exe`; the old open app was left running. No Blender execution or external project/asset writes were performed.


## Preparation action simplification - 2026-11-08

- [x] Removed the explicit Record preparation draft action. Conversion preview is read-only; successful handoff creation automatically records its actual conversion snapshot. Existing stored drafts are preserved without being used as handoffs.
- [x] Removed the duplicate handoff action from Advanced. A single Create/Recreate handoff USD action shows progress, cancellation and errors inline. Native project creation remains separate and requires a successfully registered handoff.
- [x] Frontend production build, 39 frontend tests and Rust formatting passed; desktop rebuilt. No Blender execution or user USD/native writes performed.
- [ ] User checks successful handoff saving and subsequent Create Blender project availability in the restarted desktop app.


## Missing publication controls - 2026-11-08

- [x] Always show the application USD output panel. Missing native-import provenance disables publication with a visible explanation rather than hiding the entire workflow. Preserve baseline requirements; never reconstruct a baseline from an already edited project.
- [ ] User verifies the visible explanation or opens the saved project containing the original import record.


## Explicit handoff replacement - 2026-11-08

- [x] Allow confirmed replacement of an existing OrchFlow-generated handoff, including reusing the node registry entry. Reject source files and symlink destinations. Generate fresh support layers, validate before atomic root replacement, and retain old support layers for existing references. Native projects/baselines are not rewritten.
- [x] Production build, 13 upstream USD graph tests and 25 Rust tests pass (2 GPU tests ignored). Desktop rebuilt; user verifies the save/replacement dialog.

Handoff confirmation permission fix: added dialog:allow-confirm to the main-window capability. The JavaScript confirm API invokes dialog.message internally; the explicit confirm permission authorizes that command. Frontend build, Rust formatting and desktop rebuild passed; user verifies the native confirmation after restart.


## Native creation feedback - 2026-11-08

- [x] Inspect saved Uranus project read-only: handoff is recorded; native import is absent. Existing project.blend and project.baseline.usdc predate the current handoff and remain protected. No baseline recovery or Blender execution attempted.
- [x] Show native creation progress, cancellation and errors beside Create Blender project instead of only the global footer. Successful completion still records native and baseline together before Open in App is offered.
- [ ] User retries with a new destination and checks completion or inline failure details.


## Confirmed Blender destination replacement - 2026-11-08

- [x] First-import creation accepts explicit replacement consent for existing destination .blend and .baseline.usdc. Existing linked-node reimport remains blocked. New files are imported/exported and verified before installing either destination.
- [x] Retain uniquely named hard-link backups, detect changed destination files during import, reject symlink targets, and restore the original pair if installation fails. New native and baseline records are registered together after success.
- [x] Three file-operation tests cover replacement/backups, rollback on locked native output and missing consent/concurrent changes. Production frontend build, formatting and 25 Rust tests pass (2 GPU tests ignored). No Blender execution or user artifact writes performed.
- [ ] User tests full confirmed replacement in Blender after restarting the rebuilt desktop.


## Large publication review memory - 2026-11-08

- [x] Read-only inspection confirms current baseline cloth has 201 point samples (152-352); edited export has zero point samples and stage range 0-0. Export checkbox state alone is not evidence of file contents. Existing review process observed at 15 GB RAM; not interrupted.
- [x] Fail promptly on sampled point-time mismatch when animation edits are unauthorized. Stream constant-delta checks with at most two frame-delta arrays rather than retaining all frames. Use native USD array equality as an exact fast path before tolerant recursive comparison.
- [x] 28 upstream USD publication tests pass, including a 201-frame mismatch regression. User restarts app and retries review; no source/baseline/export files rewritten.

Publication review stopped at user request: terminated only the verified desktop USD worker process (42920, child of app 38984); user artifacts were not modified. Also reuse the first sampled value during constant-value checks rather than fetching it for every frame. Desktop restart required to use embedded worker changes.


## Manual baseline workflow and explicit scene changes - 2026-11-08

- [x] Browse a manually exported unedited baseline separately from edited USD. Confirm its origin, validate handoff IDs and upstream revisions, fingerprint it and store links in the single project JSON. Keep historical native-import provenance unchanged. Exporter settings/unedited state remain user attestations, not automatic verification.
- [x] Selected-only baseline/export matching uses stable per-object IDs rather than requiring the handoff root. Verify existing geometry/light selection correspondence and block missing/ambiguous IDs. Rootless synthetic USD selection fixtures cover unchanged export and geometry edits; no claim of Blender manual-export acceptance yet.
- [x] New Blender creation prepares only the .blend; no automatic multi-GB baseline export. Capture native preparation separately and leave baseline selection to the user. Existing captured baselines remain usable.
- [x] Missing prims in application exports always preserve upstream objects, including projects with old deletion permissions. Add a Delete graph node that authors branch-local inactive opinions, validates paths, and rejects edits inside instance proxies.
- [x] Explicit new-prim root approval in review: initial support for Xform/Scope/Mesh additions, gated by relevant node capabilities/scopes and structural checks. Preserve external edited-export references. Unsupported schemas, relationships and unsafe ancestry are blocked; no arbitrary scene reconstruction.
- [x] Before the user's instruction to stop testing: 79 USD/file-operation tests, 41 frontend tests and 25 Rust tests passed (2 GPU tests ignored). Final build only; no further tests requested. No Blender runs or user source/native/USD writes performed.
- [ ] User verifies manual baseline selection, matched selected exports, new prim approval and Delete node in the rebuilt desktop. Existing vertex/coordinate correspondence checks remain strict.

## Later: selective application handoff

- [ ] Choose editable prims from the full upstream scene per application node; retain the full upstream scene for final composition.
- [ ] Prepare a subset USD handoff with stable prim identities, inherited coordinate transforms, units, materials/textures and required skeleton/blend-shape or other dependencies. Preserve references and instance sharing; exclude unrelated scene branches without copying geometry.
- [ ] Create the native application project from this subset to reduce import, memory and export costs. Record the selected prim set and dependency closure in immutable preparation provenance.
- [ ] Compare matching subset baseline/edited exports, then publish permitted adjustments and explicitly approved additions over the complete upstream scene. Objects outside the selection remain untouched; deletions require Delete nodes.
- [ ] Changing the selection requires explicit new preparation/reconciliation. Report unsupported or ambiguous dependencies rather than silently dropping them.
- [ ] Verification is deferred until this phase is implemented and the user requests it.


## Fresh publication inputs after external saves - 2026-11-08

- [x] Read-only file comparison confirmed cloth_shape_0 has the same stable ID in current handoff, manual baseline and edited export; Blender adds a parent Xform but retains the mesh ID.
- [x] Manual baseline validation and publication review now force-reload file-backed root and used dependency layers from disk, rather than reuse stale Sdf registry contents after external saves or handoff replacement. No missing-ID check is bypassed.
- [x] Reject baseline/handoff changes during manual validation before registering fingerprints. Source/native/USD files remain unmodified.
- [ ] User retries Browse baseline USD in the rebuilt app. Build only; no tests run at the user's request.


## Body correspondence float precision - 2026-11-08

- [x] Read-only inspection of current handoff and baseline: body IDs and vertex counts match (32,340 each). Composed transforms differ by float32 export rounding; maximum mapped local-point displacement is about 2.81e-6 (about 2.81e-8 metres after the body transform).
- [x] Replace near-zero scalar correspondence comparison with an eight-float32-epsilon mesh-magnitude error bound, retaining topology/count checks and rejection beyond the bound. Apply only to baseline/input correspondence; baseline/edited change detection and delta computation remain unchanged.
- [ ] User reruns Analyze changes in the rebuilt app. Build only; no tests run per user instruction. No publication or original-file writes performed.


## Confirmed publication replacement - 2026-11-08

- [x] Allow explicit confirmed replacement of a generated publication root/edit-layer pair, including reusing the node's registered output links. Protect handoff, baseline, edited export, referenced inputs and unrelated registered dependencies; reject symlink destinations.
- [x] Stage and reopen the new composition before installing it, retain uniquely named backup files, detect destination changes, and roll back installed files if replacement fails. Reopen refreshed final layers before reporting success; show the linked destination in publication feedback.
- [x] Persist a fresh publication revision and include it in graph preview identity so republishing at the same filenames invalidates selected/pinned downstream previews. Older records without revisions retain their original graph identity.
- [ ] User verifies confirmed overwrite and Published preview after restarting. Build only; no tests per user instruction. No user publication files overwritten by the coding agent.


## Publication policy key-order correction - 2026-11-08

- [x] Node publication registration compared JSON strings and falsely rejected equivalent policy objects when typed Rust snapshots and worker responses serialized object fields in different orders. Compare JSON values recursively, ignoring object-key order while retaining all values, array order, and baseline/handoff identity checks.
- [x] Clarify actual rejection feedback: files may already have been written; only the node output link was left unchanged. Do not imply an overwritten file was preserved by a frontend registration error.
- [ ] User retries publication and confirms the Published preview in the rebuilt app. Build only; no tests per user instruction. Existing user files are not modified by this code change.


Publication review refinement: Prim correspondence is now collapsed by default with a native disclosure arrow. Expand/collapse retains mappings, filtering and pagination. Build only; no tests per user instruction.


## Stable Handoff/Published comparison camera - 2026-11-08

- [x] Correct the viewport behaviour: switching Handoff/Published previously reset camera controls and independently fitted each stage's bounds. Preserve camera/time by application-node identity across preview-role changes.
- [x] Use the handoff as a common framing reference in canonical Y-up/metres, applying its recorded inverse only in the handoff render camera matrix. Published output already contains the inverse. Original scene paths, USD layers, output coordinates and prim selection remain unchanged.
- [x] Reuse comparison bounds across role switches, independent of edited skeletal bounds. Invalidate the framing reference when handoff content/conversion or time changes. Other nodes retain existing framing behaviour.
- [ ] User checks toggling Handoff/Published retains orbit, pan and zoom and displays only intended scene differences. Build only; no tests per user instruction.


## Sparse asset-override persistence and reconciliation cost - 2026-11-08

- [x] Diagnosed reconciliation writing a 5.3 GB ASCII copy of the combined geometry layer when only its material reference needed replacement. Worker stopped at user request; partial generated files left untouched.
- [x] File-backed layers without sublayers/variants now use sparse asset-property and explicit reference/payload overlays over original file-backed layers. Probe asset-typed properties without copying numeric geometry arrays. Retain original references/instance sharing; complex stacks keep the previous implementation rather than guessing composition semantics.
- [x] Reconciliation composes/prepares its input once, reuses that prepared stage for handoff persistence and compares upstream revision metadata afterward. Skip unused bounds measurements; no repeated graph preflights.
- [ ] User retries with a fresh destination (the interrupted output folder remains). Build only; no tests or Blender runs per instruction. Runtime acceptance pending.


## Live application adjustments supersede publication/reconciliation files - 2026-11-08

This section supersedes the disk-backed application publication/reconciliation workflow above.

- [x] Review baseline/edited exports once and Apply reviewed edits to an anonymous adjustment layer. Persist the reviewed sparse USD opinions, baseline/edited links, frozen conversion/policy, topology guards and external-file revision checks inside the single OrchFlow JSON. No generated output USD is written by Apply.
- [x] Application graph evaluation includes its live upstream branches and composes the frozen adjustment over a newly prepared anonymous input. Material/asset relinks and ordinary upstream transforms flow through without a Reconcile button, Blender reimport, or recalculating baseline/edited differences.
- [x] Cache parsed adjustment layers in memory; reopen projects from stored opinions without Analyse. Changed baseline/edited files and unsafe upstream topology/correspondence require review. USD point arrays remain complete edited-property opinions and may enlarge project JSON.
- [x] Replace the disk-publication action with Apply reviewed edits; remove Reconcile from the sidebar. Export final USD is separate and explicitly writes the selected destination and disclosed sibling layer folder.
- [x] Existing saved disk-backed publication links remain untouched until explicitly applying a reviewed live adjustment. Do not auto-convert or delete old outputs or interrupted reconciliation files.
- [ ] User checks apply/save/reopen, material relinks, upstream changes and downstream merges in the rebuilt app. Build only; no tests or Blender execution at user's request.

Large unedited scene assets should be Source stage branches merged downstream of the Application output, avoiding unnecessary Blender import while preserving USD reference/instance sharing. Selective handoff remains a later phase.

Live-review refinement: upstream graph/material changes do not invalidate the frozen baseline-to-edited review solely because an old input revision differs. Baseline/edited fingerprints, handoff identities, vertex correspondence and live topology guards still apply. Transform-only layout stores small matrix opinions; large unedited geometry remains externally referenced.


## Camera interaction transport for live adjustments - 2026-11-08

- [x] Avoid retransmitting and reserializing the full graph/embedded adjustment for every orbit, pan and zoom request. Register each graph with a session handle once; subsequent render requests contain camera state and the handle only.
- [x] Replace giant graph strings in the UI render-queue context with stable handles; the worker skips repeated graph JSON decoding and hashing during camera motion. New graph changes get new handles and invalidate rendering. Bounded per-session caches and automatic reregistration handle worker restarts/eviction.
- [ ] User compares camera responsiveness on the live Published output after restart. Build only; no tests per user instruction.


## MaterialX insertion preserves application correspondence - 2026-11-08

- [x] Single-input MaterialX now authors a private material overlay over the upstream layer with the same default prim and namespace, rather than wrapping/referencing the scene under another edge-derived input path. Preserve existing geometry paths, references and instances while changing only material networks/bindings.
- [x] Existing live adjustments can continue to find their guarded body paths after inserting this material-only node. Actual topology/identity checks remain enabled; multi-input MaterialX still performs explicit scene combination.
- [ ] User inserts/toggles MaterialX upstream of Application after restart and checks live Published output. Build only; no tests or user USD writes.


## MaterialX shading-rule table and compact preview controls - 2026-11-08

- [x] MaterialX inspector opens a movable, resizable, non-blocking table window. It remains open when selecting/editing other canvas nodes; no backdrop captures the canvas. Row drafts apply explicitly and are separate from canvas edits.
- [x] Add, duplicate, reorder, enable/disable and delete rows. Columns contain simple/regex selector, material name, linear RGB, metalness, roughness, specular, opacity and external color/roughness texture links. Per-row .mtlx export remains explicit.
- [x] Simple search is a case-insensitive full-path substring (* means all); regex is case-sensitive. Backend validation evaluates shadeable geometry/subsets and instance roots, reports matched/effective counts and skipped instance-proxy descendants. Last matching row wins per target; descendant assignments can override broader matched parents. Invalid regex/settings are reported before Apply. No geometry copies/de-instancing.
- [x] Persist rules and shared texture links in project JSON, validate rules/settings and dependency kinds at the Rust boundary, and author in the anonymous MaterialX overlay without changing geometry paths. Preserve an existing single-material node until rules are explicitly applied.
- [x] Remove Preview incoming scene button and redundant Published/downstream status beside the Handoff/Published slider.
- [ ] User verifies movable-window/canvas interaction, selectors, row precedence and live upstream material propagation. Production build only; no tests per user instruction.


Reopen investigation: saved Uranus JSON retains 37 asset relinks but has a disk publication and no liveAdjustment. Save now reads projectRef after destination selection, avoiding an outdated pre-dialog snapshot. The output panel explicitly identifies a disk-backed node and the one-time Apply/save transition. No automatic baseline reconstruction or user-file modification. Build only; no tests.


Removed disk-publication fallback from Application previews and graph execution. An old publishedDependencyId is historical data only and no longer opens published.usda. Nodes lacking liveAdjustment show an explicit Apply/save instruction, and default to handoff preview. Source nodes may still open external USD normally. Build only; no tests.


Save rejection diagnosed from user error: UsdService adds roundTripMs to responses and recordLiveAdjustment spread that transport field into a strict domain record. Whitelist persisted adjustment fields. Accept/discard roundTripMs on incoming adjustment records for recovery; never serialize it. Save errors now show a prominent dialog. Build only; no tests.


## Coherent Application inspector - 2026-11-08

- [x] Group application profile and shared Settings shortcut together. Present Handoff, Native project and Live output as three consistently numbered workflow cards with compact filenames, state badges and clear primary actions.
- [x] Keep existing handoff re-preparation in a closed rollup; retain separate explicit handoff/native creation. Show Open in the chosen app and relink alongside the native file. No executable editor in the sidebar.
- [x] Present baseline and applied-export file summaries instead of repeated full paths. Retain full paths in tooltips/details. Keep file export separate in a closed rollup; retain allowed edits, coordinates and advanced diagnostics as closed sections.
- [x] Remove the irrelevant disabled source-file inspection action on Application nodes; keep source-stage inspection available. Collapse material/shading diagnostics and reduce repeated workflow copy.
- [ ] User checks layout and action discoverability. Production frontend and desktop build only; no tests or screen interaction per instructions. No domain/persistence or source-file changes.

Application preparation remains a visible workflow card even after native-project creation: keep Create/Recreate handoff available and coordinate/scale diagnostics in its closed rollup. Native opening stays in the separate Native project card. This does not automatically reimport or overwrite a linked native project. Build only; no tests.


## Frontend hot-reload development session - 2026-11-08

- [x] Launch Vite/Tauri dev with the existing OpenUSD environment, reused external Cargo directory and explicit Development (live reload) window title.
- [x] Document frontend Fast Refresh versus automatic Rust rebuild/restart and compiled snapshot launches. Existing production app and user project files remain untouched.
- [ ] User opens the saved project in the development window and verifies future UI edits refresh in place. No tests or screen interaction; development compiler/launch checks only.


## Shared handoffs and named destinations - 2026-11-08

- [x] Default new handoff name is [Project name]_[Node name]_handoff.usd, with invalid Windows filename characters sanitized. Accept USD or USDA roots.
- [x] Reuse existing handoff explicitly from another node in the same project. Validate input graph equivalence, unchanged input revisions, coordinate conventions and round-trip identity, without writing the file or changing IDs. Preserve originating application-input namespace edge identities in the receiving node so subsequent edits map to the same prim paths.
- [x] Refuse replacement of a handoff referenced by another node in the project; choose Reuse or a new destination. A linked native project cannot silently switch handoffs. Cross-project/unregistered reuse is not implemented because no trustworthy preparation snapshot is available.
- [x] Hide Blender creation controls and .blend-specific instructions on non-Blender nodes. Manual native project linking remains available; do not imply automated support.
- [ ] User verifies sharing between compatible sibling nodes, named defaults and non-Blender presentation. Build only; no tests or Blender execution.


## Operation progress feedback - 2026-11-08

- [x] Accessible animated indeterminate bars with operation labels and elapsed seconds for project load/save and dependency discovery, baseline validation, analysis, live-adjustment application, final export, handoff preparation/reuse, native-project work and MaterialX evaluation/export.
- [x] Show project progress beneath the toolbar and application progress beside the relevant action/modal. Do not invent percentages where the backend has no measurable progress events. Respect reduced-motion preferences; stop indicators on completion/error/cancellation.
- [ ] User verifies visibility for long operations. Build only; no tests per instruction.


## Parallel output drop layout - 2026-11-08

- [x] Dropping a toolbar node onto a non-Output target adds a parallel downstream connection and places the new node beside the target. Move existing direct output branches downward when they overlap the new placement; pack multiple branches with measured node sizes and spacing.
- [x] Move descendants with their displaced branch, handling rejoins once with the maximum required offset. Preserve horizontal positions, original edges and unrelated nodes. Edge insertion and drops onto Output retain their existing horizontal insertion behaviour.
- [ ] User verifies drag placement and spacing. Build only; no tests per instruction.


## Manual baselines for configured applications - 2026-11-08

- [x] Remove the Blender-only restriction from manual baseline linking. Any configured Application profile with a handoff and linked native project can submit its unedited USD export for validation.
- [x] Retain source revision, handoff identity, coordinates and policy validation. Do not enable unverified ZBrush scopes or pretend an automated adapter exists. Exports that drop custom IDs remain blocked pending explicit correspondence support.
- [x] Replace Blender-specific manual baseline wording with application-neutral instructions. Native automation controls remain app-specific.
- [ ] User verifies the ZBrush baseline/edited exports and capability scope. Build only; no tests or ZBrush runs.


## Optional native project for manual USD workflow - 2026-11-08

- [x] Baseline linking/review/live adjustment no longer requires a native project file. Manual baseline provenance records a null native link and depends on the handoff, baseline/export, coordinates and policy only.
- [x] Relax only manual provenance: automated native imports still require their native identity; native creation/opening still requires the actual project. Remove the sidebar prerequisite message and disabled condition.
- [ ] User verifies handoff + manual baseline/edited USD without linking .zpr/.blend. Build only; no tests or application execution.


## Explicit manual-export correspondence when custom IDs are absent - 2026-11-08

- [x] Baseline validation can return an object-mapping dialog for exported geometry/lights without handoff IDs. User explicitly chooses a schema-compatible input prim for each unmatched object; no name-based guess or identity-check bypass.
- [x] Validate unique logical selection, reject conflicting IDs and changed baseline fingerprints, and persist correspondence in manual baseline provenance. Subsequent review merges those mappings with explicit edited-path overrides and retains coordinate/topology/vertex checks.
- [ ] User maps the ZBrush baseline mesh and reviews its edited export. An explicit mapping alone does not prove vertex order or topology correspondence. Build only; no tests or application execution.

Explicit correspondence completion: corrected a handoff-identity local variable shadowing the per-prim ID helper in the missing-ID dialog path, and reuse the source ID index while building unmatched-object rows. Frontend and Rust/Python syntax build checks only; no runtime tests or third-party app launches.


## Renamed ZBrush export root correspondence - 2026-11-09

- [x] Read-only inspection of Body_Base.usd and Body_edited.usd: both have 32,340 points, identical face topology, Y-up and 0.01 m/unit. Both omit OrchFlow IDs. Paths differ at /Body_Base versus /Body_edited; 1,655 vertices differ.
- [x] Stop aborting review before inventory when edited paths differ. Return explicit correspondence conflicts, open/focus the corresponding baseline row, and offer edited prim paths as input suggestions. User chooses the new edited path; do not guess names or bypass identity/topology/coordinate guards.
- [ ] User maps the edited root and reruns Analyse. Build and Python syntax compilation only; no tests or third-party app runs.


## ZBrush local-origin alignment and correspondence dropdowns - 2026-11-09

- [x] Read-only inspection confirms baseline/input topology matches, but ZBrush baseline local points differ by an almost constant translation. Do not treat this as float rounding or guess a vertex permutation.
- [x] Offer an explicit translation-only baseline alignment when every corresponding vertex verifies within float32 precision and topology matches. Approval is included in correspondence/review token and revalidated before applying points/normals; arbitrary affine alignment is not inferred.
- [x] Replace correspondence autocomplete inputs with full schema-compatible dropdowns so the existing path does not filter out other candidates. Missing paths get a clear placeholder; changing a prim resets its alignment approval.
- [ ] User confirms offered alignment and reruns analysis. Build and syntax checks only; no tests or app execution.


## Stable viewport and retained application correspondence - 2026-11-09

- [x] Preserve orbit, pan, zoom, timeline and canonical camera framing when selecting nodes or switching Handoff/Published. Frame all explicitly refits; raw source previews use a render-only Y-up/metres conversion.
- [x] Save approved correspondence and baseline coordinate alignment with live adjustments in the project JSON, and seed subsequent reviews from them. Topology, identity and file-revision guards still apply.
- [ ] User verifies camera continuity and repeated edit review. Build/syntax checks only; no tests or third-party application execution.


## OBJ and Alembic application baseline/edited exports - 2026-11-09

- [x] Application baseline and edited pickers accept USD, OBJ and Alembic (.abc). Require matching baseline/edited formats; final graph output remains upstream USD. Source-stage import/handoff remains USD.
- [x] Read polygon OBJ into anonymous USD meshes, retaining vertex order, n-gons, object partitions, face-varying UVs and normals, including negative indices. Groups do not split vertices or change topology. Reject unsupported non-polygon statements, partial corner data and invalid indices/coordinates. MTL/shader conversion and vertex colours are not imported; preserve upstream materials.
- [x] Read Alembic through upstream usdAbc, with actionable missing-plugin errors. Retain mesh transforms/time samples and require existing animation/edit-scope checks. Polygon-mesh edit support only; unsupported Gprim schemas fail closed.
- [x] OBJ/Alembic interpret coordinates using the frozen handoff axis/unit convention, explicitly disclosed at baseline selection. Metadata is authored only into private anonymous layers. Same-settings/vertex-order attestations, explicit object mapping, baseline fingerprints, topology/coordinate checks and reviewed-token enforcement remain required.
- [x] External OBJ/ABC files remain linked assets in project JSON; converted geometry is transient. Only the sparse reviewed adjustment persists. MCP baseline validation accepts explicit mappings and immutable digest.
- [ ] User verifies real OBJ/Alembic round trips and animated Alembic where enabled. Build and syntax checks only, no tests or third-party execution per instruction.

Build verification: frontend production build, Rust formatting, desktop executable build and MCP executable build passed. Python readers and the complete embedded worker passed syntax compilation. Existing frontend bundle-size and Rust unused transport-field warnings remain. No runtime tests or third-party applications were run.


## Coordinate alignment action feedback - 2026-11-09

- [x] Replace the silent alignment-selection action with **Use alignment and analyse**. Commit the approved mapping, then automatically rerun analysis with it; retain stale-input and reviewed-token guards.
- [x] Clear the old actionable review while reanalysing and show alignment-specific status/progress. Applying reviewed edits remains a separate action.
- [ ] User verifies the button refreshes conflicts/results without another Analyse click. Build only; no tests or external application execution per instruction.


## MaterialX-to-Application preview scale continuity - 2026-11-09

- [x] Preserve axis, metres-per-unit and timeline metadata on anonymous MaterialX, Delete, Asset Override and application adjustment overlay roots. Stage metrics must be authored on the overlay root rather than assumed to inherit from its sublayers.
- [x] Treat compiled graph previews as already Y-up/metres. Apply render-only axis/unit conversion only to raw external stages, or the frozen inverse for handoff comparison. Retain the same session camera across MaterialX/Application selection.
- [x] Keep the existing application return inverse and recorded edit-coordinate convention; no source files or saved adjustments are rewritten.
- [ ] User checks MaterialX versus ZBrush Published/Handoff size continuity. Build and syntax checks only; no tests or external application execution.

## USD evaluation and interaction performance - planned 2026-11-09

Proposed work, not yet implemented or verified. Start with measurements and prioritise the largest repeated costs. Existing render-graph handles and preview caches are a foundation; they do not establish persistent node-output caching.

- [ ] Instrument source loading, graph/node composition, dependency discovery, file fingerprinting, material preparation, Hydra rendering, image capture and transport separately. Record cold versus warm timings, cache hits/misses and memory use on representative large scenes and branching graphs, targeting RTX 3090 / 64 GB RAM.
- [ ] Cache evaluated node outputs and their anonymous layers across requests. Key results by semantic settings, stable input-edge identities, upstream output revisions and external dependency revisions. Invalidate the changed node and reachable downstream nodes only; selection, node layout and camera movement must not invalidate USD evaluation. Preserve pin-preview semantics, instance sharing and branch-local overrides.
- [ ] Keep orbit/pan/zoom rendering separate from graph evaluation. Render the existing stage without dependency scans, file hashing, graph recompilation or baseline comparisons. Reuse prepared material diagnostics/fallbacks until relevant scene, material, dependency or time changes require an update; keep camera continuity when displaying cached node outputs.
- [ ] Reuse open source stages and normalised source representations across branches and evaluations. Preserve reference/instance sharing rather than copying geometry per node. Bound caches by memory with safe eviction and layer lifetimes, and release them on project close or worker restart.
- [ ] Schedule expensive dependency discovery, filesystem checks and fingerprinting on project load, explicit refresh, export analysis or detected dependency changes rather than camera frames and selection changes. Reuse unchanged results without weakening immutable baseline fingerprints, reviewed tokens, coordinate provenance or topology/identity guards; invalidate stale reviews when inputs change.
- [ ] Update small anonymous transform/material/asset-override layers in place where safe, allowing USD change propagation and Hydra updates instead of rebuilding the complete stage or renderer. Batch related edits and discard superseded work; keep native work outside the UI thread and prevent stale asynchronous results from replacing newer state.
- [ ] Add selective handoff/native import and explicit payload-loading controls where appropriate, building on the deferred selective-handoff work. Load only the working set needed for editing/preview while preserving unselected upstream content and honest unloaded-content indicators. Explicit final exports must honour their requested scene scope.
- [ ] Compare before/after cold load, warm node switching, upstream edit propagation and camera interaction, including memory growth and shared large assets. Confirm unchanged branches are reused and dependency changes still refresh affected outputs. Define measured acceptance targets after profiling; leave desktop and third-party application verification to the user.

A single-file handoff simplifies storage; it is not itself an evaluation-speed optimisation. Composing/flattening a snapshot may increase export time, file size and memory use. Keep that explicit export work separate from interactive in-memory evaluation.

## Binary USD for geometry-bearing disk exports - 2026-11-10

- [x] Treat the empty asset path reported by OpenUSD 25.08 for same-file prototype references as internal to the snapshot. Preserve the external-dependency guard and validate that the reopened snapshot uses only its root and session layers. Verified headlessly with instanced geometry, branch-local asset overrides, and skeletal handoffs. Blender Quickstart still requires manual verification.

- [x] Handoff and final scene export share a writer that explicitly produces Crate binary for .usd and .usdc destinations, independent of USD_DEFAULT_FILE_FORMAT. Verify the staged binary encoding before installation; preserve the single-file snapshot, coordinate and composition checks. Keep .usda as an explicit text/debugging option.
- [x] Offer binary USD first in handoff/final save dialogs, retain the project/node handoff naming convention with .usd, and default final export to final.usd. Existing ASCII and binary inputs remain readable; handoff reuse also accepts .usdc.
- [x] Recommend binary .usdc or binary-encoded .usd for manually exported baseline/edited files, with identical exporter settings. Do not rewrite immutable baselines or user-owned exports. In-memory evaluation and the current project-JSON adjustment representation are unchanged.
- [x] Focused upstream OpenUSD check on temporary mesh fixtures: .usd and .usdc have Crate headers and reopen with geometry/coordinate checks; .usda remains ASCII. Verified with USD_DEFAULT_FILE_FORMAT=usda and no supporting layer folders. No user files were converted and no third-party application was launched.
- [x] Frontend production build, Rust formatting check and desktop build passed. Built orchflow-binary.exe separately to avoid replacing the main conversation's desktop executable. Existing bundle-size and unused transport-field warnings remain.
- [ ] User verifies save-dialog defaults and real third-party binary round trips. Large-scene loading/memory benchmarks remain part of the planned performance phase; no speedup figure is claimed.


## Single-file handoff and authorized Uranus folder cleanup - 2026-11-09

- [x] Supersede multi-file handoff export with upstream OpenUSD flattened scene snapshots. Preserve scene instancing, handoff IDs, axis/units, transforms and current composed materials. Keep texture/resource files linked; retain original source layers and non-destructive graph evaluation.
- [x] Default .usd to binary storage; retain .usda text support. Validate staged snapshot composition/default prim/coordinates before installation. Return an empty generated-layer list and update inspector/MCP wording.
- [x] Superseded by user request on 2026-11-10: discard obsolete generated files without conversion or a legacy archive. Cleared nine .layers folders, three generated handoff roots and the partial archive to Windows Recycle Bin. Permanent deletion was rejected by automatic approval review. Cleared handoff-ready records and generated-layer links in the saved project; retained both live adjustments, native files, baseline/edited exports and original source USD/assets. Current Rust domain load validates the saved JSON.
- [x] Frontend/desktop/MCP builds, Rust formatting and Python syntax checks.
- [ ] User recreates handoffs with the single-file exporter and verifies application import. No test suites or third-party app execution.

Cleanup completion (2026-11-10): zero .layers folders and zero old generated handoff roots remain in Uranus. Saved JSON passes the current Rust loader and retains both live adjustments. Historical handoff dependency IDs remain provenance; allow explicit recreation at the same missing, node-owned handoff path, and record missing dependencies without trying to scan nonexistent USD files. Reopen the saved project to pick up cleaned links.

## Application Live output and simpler change review - 2026-11-10

- [x] Move edited-export browsing and selected filename into section 03 Live output. Analyse change opens the review modal and automatically analyses the committed file/mapping state.
- [x] Simplify the review modal to a compact summary, expandable object matches and results, with full paths available in tooltips/selectors. Open issues needing attention; collapse change/preserved lists and advanced exporter/new-object controls by default.
- [x] Keep Analyse again, Apply reviewed edits and Close in a visible footer. Retain explicit alignment approval, reviewed-token guards and separate edit application.
- [ ] User verifies the new sidebar/modal flow. Frontend build verification recorded below; no desktop or third-party application interaction.

Verification: TypeScript and Vite production build passed. Desktop appearance and interaction remain for the user to verify; no backend changes or test suites were needed for this UI edit.


## Selective handoff and stable minimal namespaces - 2026-11-10

- [x] Persist per-Application handoff selection and frozen handoff selection provenance. Search/browse the input hierarchy with multi-select; explicit Whole scene option. Empty selected drafts are allowed; export requires valid non-proxy roots.
- [x] Prune only a private handoff overlay before snapshotting. Include selected descendants, ancestor transforms, bound materials/subsets, skeleton/animation relationship targets and attribute connections. Retain instance boundaries; reject proxy-child extraction. Full live upstream composition remains independent of handoff selection.
- [x] Source output uses /Scene/<asset-root>. Single-input Output/Transform/MaterialX/Asset Override/Delete preserve namespaces. Only real merges add stable branch containers. Handoff and return conversions apply on existing roots, with no Inputs/Input, Units/Asset, Handoff/Input or PreservedInput chains.
- [x] Track namespace v2 in graphs/handoffs/adjustments. Reject previous handoff/adjustment provenance explicitly; no compatibility migration or automatic native reimport. Preserve existing saved data.
- [x] TypeScript/Vite production build, Rust formatting and desktop/MCP builds, plus individual/embedded worker syntax checks. No test suites or third-party application execution per user instruction.
- [ ] User checks selected body handoff size/content, material/rig preservation, transforms/units, stable single-input paths and a fresh application round trip. Actual measurements remain pending.
- [ ] Further simplify source-authored/exported model hierarchy through explicit stable-ID/path mapping when appropriate; never confuse scene graph flattening with namespace simplification.

## Node type colours - 2026-11-10

- [x] Give Source, Application, Transform, Delete, Asset Override, MaterialX and Output distinct dark fills and accent colours on their type labels, top borders, sockets and pin controls.
- [x] Keep text type labels and an independent bright selection outline; retain a filled pin state so colour alone does not indicate selection or pinning.
- [ ] User verifies appearance on the graph. Frontend build verification follows; no graph data or backend behaviour changed.

Verification: TypeScript and Vite production build passed for node type colours. Manual appearance check remains with the user.


## Selected handoff regeneration feedback - 2026-11-10

- [x] Read-only diagnosis: the 1,297,512,802-byte handoff is an earlier whole-scene export with eight meshes and no selection/namespace-v2 metadata. Current body selection retains one 32,340-point mesh plus its material and required rig, excluding the 124,152-point/51-sample cloth. No export file was rewritten during diagnosis.
- [x] Permit confirmed replacement of a node-owned historical generated handoff when its current handoff record is empty. Worker still requires generated-file ownership and overwrite confirmation; refuse actual scene inputs (including composition-layer aliases).
- [x] Show actual resulting filename, size and exported selection scope on successful handoff creation; keep failure messages distinct from preview errors.
- [x] Frontend/desktop builds, Rust formatting and embedded-worker syntax checks. No test suites or third-party application execution.
- [ ] User recreates the selected handoff and checks the displayed resulting size.

## Clearer selective handoff object picker - 2026-11-10

- [x] Use compact hierarchy rows with aligned arrows, checkboxes, names and subdued schema types. Fix inherited column-oriented label styling that separated checkboxes from names.
- [x] Show partial selection on ancestors, clearly mark children included by a selected parent, and explain disabled instance-proxy selections. Keep existing backend selection safeguards.
- [x] Add a separate Selected panel with names, full paths, individual removal and Clear all; distinguish flat search results from the expandable scene hierarchy. Include empty/loading feedback, Collapse all, responsive layout and a clear footer.
- [ ] User verifies object selection and visual layout. No backend/export behaviour changed.

Verification: TypeScript and Vite production build passed for the revised handoff picker. Desktop interaction and appearance remain for user verification; no backend changes or test suites were needed for this UI edit.


## Baseline correspondence after handoff recreation - 2026-11-10

- [x] Detect obsolete/missing/incompatible baseline-to-input paths before publication review and surface an explicit remapping request. Open the manual baseline matching flow, preserving baseline digest checks and requiring user confirmation; never guess the replacement path.
- [x] Seed review from the current confirmed baseline mappings. Reuse old edited-path/alignment choices only for a matching graph revision and input mapping, so old saved live correspondence cannot override the newly chosen input. Retain saved adjustments and external files.
- [x] Baseline validation can return the mapping inventory for incompatible explicit input paths instead of a dead-end exception. Identity conflicts, topology and coordinate guards remain enforced.
- [x] Frontend/desktop builds, Rust formatting and embedded-worker syntax checks. No test suites or third-party execution.
- [ ] User confirms Body_Base/Body_001 against the current /Handoff/root/.../Body_001 path and reviews the edited export.

## Scene material selection highlights assigned meshes - 2026-11-10

- [x] Resolve material clicks against the original preview stage using upstream UsdShade binding resolution. Include inherited/direct/collection bindings across all-purpose, preview and full purposes, face subsets (highlight their mesh) and instance-proxy meshes without de-instancing or editing sources.
- [x] Cache binding users and resolved selections across camera frames; invalidate on USD ObjectsChanged notices or stage replacement. Retain ordinary object/ancestor selection and clear unused-material selection cleanly.
- [x] Add a viewport selection count/no-matching-mesh message and update Scene overview guidance.
- [x] Four real upstream USD headless checks passed: inherited overrides, collection/subset bindings, shared instances, purpose-specific bindings and cache invalidation. No desktop or third-party applications launched.
- [ ] User checks visible Storm highlights after restarting the backend/development session. Frontend/backend build verification follows.

Verification: TypeScript/Vite production build, Rust formatting/check and complete embedded-worker syntax checks passed. Four headless binding/selection checks passed. Visual Hydra selection remains assigned to the user; restart the development session to rebuild/load the embedded worker.


## Autodesk Aurora / MaterialX Hydra integration - 2026-11-10

- [x] Download official Autodesk Aurora v25.08-beta at 50eea04cc44c7b8c6bcfffdc68338755684bf3c8. Build DirectX HdAurora against the host USD 25.08 / MaterialX 1.39.3 SDK; reuse its ABI rather than loading Autodesk's separate USD build. Install vendor dependencies and licensing outside the project tree.
- [x] Reproducible setup script and isolated runtime manifest/plugin registration. Keep Storm default/available, preserve existing plugin paths and source files, and report missing installation/ABI/library failures. Native warnings/errors remain stderr-only.
- [x] HdAurora prefers mtlx, converting native Hydra MaterialX networks with upstream HdMtlx. Filter imported library declarations from per-material documents and supply Aurora's runtime MaterialX libraries. Explicit st primvar mapping to texcoord 0 matches Aurora mesh support; no guessing of arbitrary UV sets or source rewriting.
- [x] Add sample target control and idle refinement, using Aurora's native current-sample counter to stop when complete. Preserve camera behavior and interactive low-cost frames. Give initial Aurora shader compilation a separate worker timeout.
- [x] Headless GPU checks on RTX 3090: discover both delegates; render Standard Surface with deliberately conflicting PreviewSurface color and confirm MaterialX wins; render the authored st color texture; switch back to Storm in the same process. No third-party native application launched.
- [x] Final GPU acceptance: solid MaterialX center RGB [210,61,42] beats deliberately green compatibility surface; st color texture renders [22,35,209]; both Aurora scenes converge at 16/16 samples. Storm then renders in the same process. Verification report is at I:/AI/Autodesk-Aurora-build/verification.json.
- [x] Frontend production build, Rust formatting, desktop build, individual/embedded Python syntax checks and setup PowerShell/CMake configuration checks passed. Headless-server build passed. No broad test suites or Blender/native-app execution.
- [ ] User checks desktop renderer switching, interaction, scene lighting and complex real-project MaterialX support. Denoising, upscaling and full renderer/schema parity are not claimed.


## MaterialX authoring system and procedural shading - 2026-11-10

- [x] Extended `MaterialXSettings` schema and validation in Rust (`src-tauri/src/project.rs`) and TypeScript (`src/fileRegistry.ts`):
  - Full Standard Surface channels: `baseWeight`, `specularColor`, `specularIor`, `transmission`, `transmissionColor`, `emission`, `emissionColor`, `coat`, `coatRoughness`, `coatColor`, `sheen`, `sheenRoughness`, `sheenColor`, `subsurface`, `subsurfaceColor`.
  - Seven external texture dependency slots: `baseColor`, `roughness`, `metallic`, `emission`, `normal` (`ND_normalmap_float`), `opacity`, `transmission`. All textures remain external dependencies in the OrchFlow project model.
  - Procedural generators (`ProceduralConfig`): Perlin noise 2D/3D (`ND_noise2d_color3`, `ND_noise3d_color3`), Fractal 3D / fBm (`ND_fractal3d_color3`, `ND_fractal3d_float`), Cellular / Worley (`ND_worleynoise2d_float`), and ramps, supporting both UV (`st`) and object position (`Pobject`) spaces with scale, amplitude, and octaves.
- [x] OpenUSD Python worker shading authoring (`worker/usd_graph.py`):
  - Generates compliant `ND_standard_surface_surfaceshader` with dual-context fallback to `UsdPreviewSurface`.
  - Normal mapping node network connects `ND_image_vector3` with `st` primvars into `ND_normalmap_float`.
  - Procedural shader authoring creates connected generator nodes for `proceduralBaseColor` and `proceduralRoughness`.
  - `materialx_document()` generates clean, portable MaterialX 1.38 XML documents with all standard surface inputs, textures, normal maps, and procedural nodes.
- [x] Upgraded MaterialX Authoring UI (`src/MaterialXPanel.tsx` & `src/App.css`):
  - Compact rule overview table with active feature badges (`Coat`, `Sheen`, `Trans`, `Emit`, `SSS`, `Norm`, `ProcCol`, `ProcRgh`, texture count), simple/regex search selectors, RGB color swatches, and rule actions.
  - Interactive multi-tab shader inspector drawer for the active rule:
    - **Surface & Specular**: Base weight, color picker + RGB inputs, metalness, roughness, specular weight, tint color, IOR ($\ge 1.0$), and opacity.
    - **Transmission & Emission**: Transmission weight and tint color; emission luminance and tint color.
    - **Coat, Sheen & SSS**: Clearcoat weight, roughness, and color; sheen weight, roughness, and color; subsurface scattering weight and color.
    - **Texture Maps**: Dedicated browser and clear actions for all 7 texture channels, showing filename pills and full registry path tooltips.
    - **Procedurals**: Toggleable procedural base color and roughness generators with type selection (noise, fractal, Worley, ramp), space selection (UV vs Object), frequency scale, amplitude, and octaves.
- [x] Verification:
  - Frontend production build (`npm run build`) passed with zero errors.
  - Rust test suite (`cargo test`) passed (25 passed, 0 failed, 2 ignored GPU benchmarks).
  - Rust formatting (`cargo fmt --check`) passed.
  - Python MaterialX test suites (`test_materialx.py`, `test_selective_materials.py`, `test_material_selection.py`) passed (15/15 tests passed).
- [x] Subsurface scattering and diffuse base weight resolution:
  - Corrected `m.get('baseWeight')` value extraction: in Python, `0.0 or 1.0` evaluated to `1.0`, which prevented setting `baseWeight` to 0.
  - Implemented physical diffuse/subsurface energy blending for the `UsdPreviewSurface` fallback: `diffuse_mult = base_weight * (1.0 - subsurface) * (1.0 - metalness) * (1.0 - transmission)` and `subsurface_mult = subsurface * (1.0 - metalness) * (1.0 - transmission)`, ensuring rasterizers (Storm) immediately reflect subsurface and zero diffuse weights.
  - Patched Aurora's `MaterialGenerator.cpp` to correctly map `{ "subsurface_color", "subsurfaceColor" }` and authored `subsurface_radius` and `subsurface_scale` so both Storm and Aurora physically reflect subsurface scattering and zero base weight.
- [ ] User visually verifies authoring and previewing advanced materials and procedurals under Hydra Storm and Autodesk Aurora in the desktop application.

- [x] MaterialX SSS scale and RGB scattering radius controls author independent Standard Surface inputs; explicit full/preview bindings override incoming purpose-specific materials. Frontend build verified; Aurora visual acceptance pending.

- Aurora SSS correction: vendor StandardSurfaceBSDF.slang computes scaled radius but does not consume it in the diffuse-transmission approximation. Physical radius-dependent SSS is NOT implemented/verified. Editor now discloses this limitation; prior physical SSS claims above are superseded. Texture inheritance report remains under investigation; source Body mesh has a direct all-purpose binding, not a collection binding.

- Saved Uranus MaterialX graph inspection confirms body/headgear resolve to their authored texture-free MaterialX shaders. Material identities now include a deterministic settings digest to isolate delegate state across shader edits. This addresses possible retained renderer properties; desktop confirmation remains pending.

- [x] Aurora rendering correction (2026-11-10): reproduced texture-like body detail and ignored SSS in a fresh real Uranus render. Native material XML and mesh binding traces confirm texture-free MaterialX is delivered to the body. Constant green emission isolates the body from separately shaded head/clothing.
- [x] Corrected environment material/light sampling to select the existing diffuse-transmission SSS approximation consistently with subsurface weight/colour; retained the documented lack of radius-dependent physical scattering. Real GPU render now changes blue headgear to its configured red SSS colour.
- [x] Invalid non-face-varying normal counts regenerate renderer-private normals instead of padding normals or extending geometry points. Incoming source USD remains unchanged.
- [x] Added repeatable patches in scripts/aurora/patch_shading.py. Installer now reports locked binaries instead of silently skipping copies. Active locked desktop binaries were preserved; a side-by-side sss-sampling-v1 runtime is selected through runtime.json and binaryRoot.
- [x] Frontend, Rust formatting and desktop/native delegate builds passed. No broad tests or native application imports. Desktop restart/visual acceptance remains with user.

- Scale verification: fresh installed Aurora GPU renders of saved Uranus graph at SSS scale 0.001 and 1000 both converged at 64 samples and produced byte-identical PNGs (SHA256 F4D288EA781853E4D0329896093BD1AC4D8CDE99A65B18A036CAFEFEB45EB9B7). Local source maps/stores subsurface_scale but the three subsurface_radius_scaled results are never read by shading. No source/project edits during this probe.


## Aurora v26.08 preview upgrade â€” 2026-11-10

- [x] Built and staged official commit 15f3e91c316d3fbabf8d4550daeaaaacfc7d3454 in a separate source/build/runtime. The prior v25.08 runtime and current desktop process remain intact.
- [x] New renderer uses MaterialX 1.39.5 (Autodesk tag v1.39.5.20260603.dev_adsk), Slang 2026.14.1 and the existing host USD 25.08 ABI. Private MaterialX is statically linked; the host 1.39.3 HdMtlx converter is isolated in its own translation unit and exchanges XML strings only.
- [x] Reproducible setup-aurora26.ps1, pinned external preparation, native MaterialX network bridge, unit setting and native-log routing. Retained exact-path SSS approximation sampling/normal handling corrections; fixed a duplicate scalar mx_square helper in the new Slang compilation path.
- [x] Real saved Uranus scene converged at 64 samples on RTX 3090 with v26.08; native MaterialX rules and source material fallback compose. Switched to Storm in the same process and rendered successfully. Evidence: I:/AI/Autodesk-Aurora-26.08-build/verified-scene.png and verify-switch.log.
- [x] Activated the verified Slang runtime through an atomic manifest switch; runtime.previous.json retains the prior manifest. Loaded DLLs are never replaced. Runtime diagnostics disclose version, MaterialX version and pipeline.
- [ ] Experimental MDL remains disabled. Its initial build/load succeeded, but real-stage rendering failed on legacy Hydra material parameter binding (optimized-away uniform properties); runtime switching also exposed upstream shader compilation failures. Do not advertise MDL acceptance or radius-dependent SSS as implemented. NVIDIA MDL SDK 2026.0.0 is downloaded for further investigation.
- [ ] Desktop restart/manual visual acceptance remains with the user. Denoising/upscaling remain disabled and are not claimed as part of this upgrade.


## Experimental MDL adapter build â€” 2026-11-10

- [x] Separate pinned source/build/runtime and reproducible scripts/setup-aurora26-mdl.ps1; scripts/dev-mdl.ps1 selects it for one development session. Regular dev.ps1 continues to use the verified Slang runtime.
- [x] Native MaterialX values stay in immutable XML. Legacy Hydra constants and external texture paths are baked into generated XML instead of applying incompatible legacy Aurora property names to MDL's compiled argument layout. Native files and source material opinions are unchanged.
- [x] Constant red MaterialX rendered [238,0,0] and converged at 8 samples using MDL before the user requested stopping render checks.
- [x] Texture probe identified missing external-resource MDL search roots; added drive-relative resource resolution without copying assets or reducing paths to basenames. Opacity probe identified primary rays ignoring MDL cutout opacity; primary paths now use the same MDL opacity expression as shadow paths. Native build passes.
- [ ] Latest texture/opacity fixes, full saved scene and SSS scale are not render-verified. User explicitly stopped testing and requested build only. No further render checks were performed after that instruction.


## NVIDIA Omniverse RTX renderer via ovrtx â€” 2026-11-10

Status: implemented and verified through automated tests (`scripts/test-ovrtx.py` and `scripts/test-materialx-ovrtx.py`); Rust backend service multiplexing and frontend UI renderer selection wired. Desktop visual inspection left to the user.

Added NVIDIA Omniverse RTX as an optional, high-fidelity viewport renderer using NVIDIA's standalone `ovrtx` and `ovstage` Python SDK. This provides native RTX real-time ray tracing and path tracing, full MaterialX/MDL shading, and RTX sensor/simulation pipeline capabilities without requiring a full Omniverse Kit installation or launcher. Storm and Aurora remain active, independent renderers.

### Architecture & Isolation Boundary

```
+-----------------------------------------------------------------------------------+
| React Flow Viewport (Tauri Frontend)                                              |
| Camera Gestures (eye/target/up) | Renderer Dropdown | Progressive / Motion Frames |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Rust Backend (src-tauri/src/usd.rs & lib.rs)                                      |
| Service Dispatcher: routes render requests based on selected renderer             |
+-----------------------------------------------------------------------------------+
           |                                                      |
           v                                                      v
+------------------------------------+   +------------------------------------------+
| Hydra Worker (worker/usd_worker.py)|   | Omniverse RTX Worker (worker/ovrtx_worker) |
| Python 3.12 (.venv-imaging)        |   | Python 3.12 (.venv-ovrtx)                |
| Upstream OpenUSD 25.08             |   | Standalone ovrtx + ovstage SDK           |
| Renderers: Storm, Aurora           |   | Renderer: Omniverse RTX Path Tracer / RT |
+------------------------------------+   +------------------------------------------+
```

1. **Process Isolation**: `ovrtx` bundles its own USD and runtime libraries. To prevent ABI and symbol collisions with OrchFlow's pinned OpenUSD 25.08 imaging runtime, `ovrtx` executes in its own isolated Python worker process (`worker/ovrtx_worker.py`) backed by a dedicated virtual environment (`.venv-ovrtx`).
2. **Shared Protocol**: The worker implements the existing OrchFlow stdio protocol: stdout emits JSON lines prefixed with `ORCHFLOW:`, stderr captures native NVIDIA logs and driver diagnostics, and stdin receives JSON render requests.
3. **Asset Protection**: In-memory graph stages are bridged without altering original external USD files, texture assets, or native application projects.

---

### Phased Implementation Roadmap

#### Phase 1: Environment Provisioning & Discovery (`scripts/setup-ovrtx.ps1`)
- [x] Create `scripts/setup-ovrtx.ps1` with automated dependency validation:
  - Verified NVIDIA display driver meets RTX requirements (driver 610.88 on RTX 3090).
  - Created dedicated virtual environment at `.venv-ovrtx` using Python 3.12 (isolated from `.venv-imaging`).
  - Installed pinned PyPI packages: `ovrtx==0.6.0.1791947691`, `ovstage==0.3.0.1791986420`, `usd-core==26.11`, `numpy==2.5.4`, and `pillow==13.0.0`.
  - Validated package import, schema registration (`ovrtx.register_schema_paths()`), and basic renderer instantiation.
- [x] Recorded runtime manifest at `I:/AI/OrchFlow-renderers/ovrtx/runtime.json`:
  - Fields: `name`, `ovrtxVersion`, `ovstageVersion`, `driverVersion`, `device`, `installed`, `available`, `features` (Real-Time Ray Tracing, Path Tracing, MaterialX, MDL).
- [x] Implemented `worker/ovrtx_runtime.py` discovery helper (analogous to `renderer_runtime.py` for Aurora) to inspect the manifest, test process health, and report availability to the Rust backend without throwing.

#### Phase 2: Standalone RTX Worker (`worker/ovrtx_worker.py`)
- [x] Created `worker/ovrtx_worker.py` with standalone lifecycle management:
  - Startup: calls `ovrtx.register_schema_paths()`, instantiates `ovrtx.Renderer()`, instantiates `ovstage.Stage("orchflow-viewport")`, and attaches stage via `renderer.attach_ovstage(stage)`.
  - Shutdown: clean teardown via `ExitStack` / `atexit` calling `stage.destroy()` and `renderer.destroy()`.
- [x] Implemented USD RenderProduct and RenderVar setup:
  - Authored `/Render/RenderProduct_Viewport` with requested dimensions (`Gf.Vec2i(width, height)`).
  - Authored `/Render/RenderVar_LdrColor` targeted by the product.
- [x] Implemented camera synchronization:
  - Authored transient `/Render/Camera` prim in the stage overlay.
  - Converted OrchFlow viewport camera parameters (eye, target, up, FOV, clipping range) to USD camera transforms (`Gf.Matrix4d().SetLookAt()`) and attributes (`focalLength`, `horizontalAperture`, `verticalAperture`, `clippingRange`).
  - Connected camera to the render product: `orderedVars` and `camera` target `/Render/Camera`.
- [x] Implemented warm-up and step loop:
  - Tracked scene change state via ordinal counters (`ordinal += 1`).
  - Executed warm-up steps (`renderer.step()`) during initial stage load or material changes to allow RTX shader compilation and texture streaming before readback.
  - Stepped renderer for interactive motion (`delta_time=1/60`) and converged refinement.
- [x] Readback and image compression:
  - Zero-copy converted mapped DLPack tensor to NumPy array `(H, W, 4)`.
  - Compressed to JPEG for interactive gesture frames and lossless PNG for idle/converged frames.
  - Returned base64 data URI over the `ORCHFLOW:` protocol.

#### Phase 3: Stage Handoff & Live Graph Bridge
- [x] Bridged OrchFlow in-memory composed stages to `ovstage`:
  - When rendering with Omniverse RTX, exported the evaluated anonymous stage into a flattened temporary binary file in `%TEMP%/orchflow_ovrtx_<hash>.usdc` via `compiled_stage.Flatten()`.
  - Called `ovstage.population.open_usd(stage, temp_path, ordinal=ordinal)`.
  - Sealed stage write floor: `stage.advance_write_floor(ordinal).wait()`.
  - Automatic cleanup of mapped DLPack buffers to avoid unmap warnings.
- [x] Implemented default lighting fallback: added `UsdLux.DomeLight` (ambient 20.0) + `UsdLux.DistantLight` (key 800.0, rotated -45 deg) when no scene lights exist.

#### Phase 4: MaterialX & MDL Shading Pipeline
- [x] Verified native MaterialX standard surface networks:
  - Tested `ND_standard_surface_surfaceshader` network authored by OrchFlow in Omniverse RTX (`scripts/test-materialx-ovrtx.py`).
  - Validated parameters: `base`, `base_color`, `metalness`, `specular_roughness`, `specular_IOR`, `transmission`, `emission`, `coat`, `sheen`.
  - Validated subsurface scattering parameters: `subsurface`, `subsurface_color`, `subsurface_radius`, `subsurface_scale`, and `subsurface_anisotropy`.
  - Added safe `safe_vec3f` helper and default fallback in `worker/usd_graph.py` to prevent `pxr.Gf.Vec3f() argument after * must be an iterable, not NoneType` exceptions on missing or null color/radius parameters.
  - Authored dual snake_case (`subsurface_color`, `subsurface_radius`, `subsurface_scale`) and camelCase (`subsurfaceColor`, `subsurfaceRadius`, `subsurfaceScale`) attributes to ensure universal interoperability across Aurora, Cycles, and Omniverse RTX.
- [x] Texture relinking: extended `src/fileRegistry.ts` (`relinkMaterialTextures`) to track and relink all 7 texture dependency slots (`baseColor`, `roughness`, `metallic`, `emission`, `normal`, `opacity`, `transmission`).

#### Phase 5: Backend Service Multiplexing & UI Integration
- [x] Extended Rust backend (`src-tauri/src/usd.rs`):
  - Refactored `UsdService` to multiplex between the primary OpenUSD worker (`worker`) and the dedicated Omniverse RTX worker (`ovrtx_worker`).
  - Routed `"renderer": "OmniverseRTX"` requests to `ovrtx_worker.py --serve`; routed `HdStormRendererPlugin`, `HdAuroraRendererPlugin`, and `HdCyclesPlugin` to `usd_worker.py`.
  - Returned unified capability list in `get_capabilities`: appended `Omniverse RTX (NVIDIA ovrtx)` when `is_ovrtx_available()` confirms manifest and venv presence.
- [x] Frontend UI exposure (`src/NativeViewport.tsx`):
  - Added `OmniverseRTX` to progressive refinement triggers and displayed runtime status pill in viewport toolbar.

#### Phase 6: Automated Verification & Acceptance Criteria
- [x] Automated headless test script `scripts/test-ovrtx.py`:
  - Verified `ovrtx` and `ovstage` import, renderer initialization (3.64s), 5 warm-up steps (0.35s), and 320x240 frame render (step = 4.5ms, readback = 6.5ms, 100% valid pixels).
- [x] Automated MaterialX test script `scripts/test-materialx-ovrtx.py`:
  - Evaluated MaterialX Standard Surface red sphere with subsurface scattering and coat in Omniverse RTX. Verified foreground color dominance (R=36.0, G=22.7, B=21.5).
- [x] Rust backend and frontend verification:
  - `cargo fmt --check --manifest-path src-tauri/Cargo.toml` passed.
  - `cargo check --manifest-path src-tauri/Cargo.toml` passed with 0 errors.
  - `cargo test --manifest-path src-tauri/Cargo.toml` passed all 27 unit tests.
  - `npm run build` passed with 0 TypeScript/Vite errors.
- [ ] Explicit manual verification boundary:
  - Desktop UI visual acceptance and interactive viewport orbit in the running desktop app remain assigned to the user.

---

### References
- [NVIDIA ovrtx documentation & overview](https://nvidia-omniverse.github.io/ovrtx/)
- [ovrtx Python Getting Started & Lifecycle](https://nvidia-omniverse.github.io/ovrtx/python_api/getting_started.html)
- [ovstage Scene Loading & Population](https://nvidia-omniverse.github.io/ovrtx/scene/loading_usd.html)
- [Hardware & Driver Requirements](https://nvidia-omniverse.github.io/ovrtx/driver_requirements.html)
- [Omniverse Materials & Shading Architecture](https://docs.omniverse.nvidia.com/materials-and-rendering/latest/materials.html)


### Cycles integration and display-buffer correction â€” 2026-11-10

- [x] Built ABI-pinned standalone HdCycles against host USD 25.08, including CUDA sm_86 kernel; packaged isolated runtime revisions with atomic manifest activation.
- [x] Added explicit delegate selection, progressive samples, device/error diagnostics and renderer-private MaterialX subset translation. Unsupported and approximate mappings are reported; source networks remain unchanged.
- [x] Fixed render-buffer reallocation when dimensions/format change at equal byte count; clear resized PBO storage and preserve GL unpack state for half-RGBA uploads. Compiled updated delegate and activated a new side-by-side revision.
- [x] Clarified SSS distance units without changing saved user material values. Saved body/headgear use scale 1 and radius (1,1,1), representing metre-scale scattering.
- [x] Non-render verification: native delegate/kernel build, CUDA device/delegate initialization, Storm/Aurora initialization, Python and PowerShell syntax checks, npm build, Rust formatting/check and desktop executable build passed. Vite retains its bundle-size advisory; custom Rust output emits output-name warnings.
- [ ] User visual verification after restarting backend: screenshot noise/darkness, resize behavior, materials and SSS scale response. Buffer fixes and unit diagnosis do not establish the screenshot's complete cause. No additional frame-render or Blender tests: user explicitly requested builds/error checks only.


### NVIDIA viewport request correction â€” 2026-11-10

- [x] Corrected ovrtx worker graph transport: decode serialized graphs, cache bounded graph handles, and return the shared unknown-handle error so the frontend can re-register after switching renderer or worker restart.
- [x] Retain compiled graph keepalives/statistics; use a private flattened stage for external-file previews before authoring renderer camera/light opinions.
- [x] Consume viewport yaw/pitch/zoom/pan and frame-fit session controls; author camera before initial population and on subsequent camera updates. Return the common viewport timing/frame fields.
- [x] Verified worker syntax and serialized/cached/missing-handle protocol paths without initializing or rendering. Frontend build and Rust format/check passed (existing Vite chunk-size advisory).
- [ ] User verification after backend restart: NVIDIA scene display and navigation. No render tests performed; Cycles work is on hold at user request.


## Clear scene / fresh project UI â€” 2026-11-10

- [x] Toolbar Clear scene opens a warning dialog with Cancel and explicit Clear scene actions. Confirmation removes nodes, edges and project dependency links, closes node editors, unpins preview, resets selections/canvas and starts an Untitled workspace.
- [x] Clear is intentionally not undoable: confirmation clears session history. Saved projects, external USD/assets/native files and application-wide settings are not written or deleted.
- [x] A project-session guard rejects callbacks captured before clearing, preventing pending node editors from reintroducing the old graph.
- [x] Frontend production build passed. No render or application tests requested; desktop interaction remains user verification.


### NVIDIA camera updates and aspect handling â€” 2026-11-10

- [x] Replaced same-path full-scene reopen on orbit/pan with fresh inline anonymous camera-only USD population. Idle refinement no longer reopens the scene; scene replacements clear previous population and use fresh temporary filenames to avoid stale same-path source caches.
- [x] Use consistent vertical field of view and viewport-matched aperture, explicit square pixel aspect and render-product conform policy. Reject unexpected pixel tensor extents rather than stretching stale frames silently.
- [x] Non-render checks using NVIDIA environment OpenUSD: orbit/pan changes camera transform; aperture aspect equals wide viewport; render product APIs valid. Worker syntax, frontend build and Rust format/check pass. Initial camera check in imaging Python lacked pxr on its default import path; rerun successfully in NVIDIA Python.
- [ ] User verification after backend restart: NVIDIA navigation, image proportions and idle behavior. No renderer initialization or frame rendering performed for these changes.


### NVIDIA plane-and-cubes real render verification â€” 2026-11-10

- [x] User explicitly renewed render-test authorization for a simple plane/cubes scene. Ran isolated worker renders with three coloured cubes and a plane; user project/assets untouched.
- [x] Real execution exposed reset_usd NOT_FOUND on first population; corrected reset to run only when a previous scene exists.
- [x] Rendered initial/orbit/pan/zoom at 640x360 and portrait resize at 360x640. Verified exact pixel dimensions, coloured object presence, and distinct frames (mean absolute differences 2.58, 3.36 and 5.24 levels/255). Inspected initial and orbit PNGs: cubes visible and view changes without apparent stretching.
- [x] Successful render times approximately 0.34â€“0.52 seconds per request after renderer construction. Evidence and isolated test driver under I:/AI/OrchFlow-build/ovrtx-navigation and I:/AI/OrchFlow-build/test-ovrtx-navigation.py; log ovrtx-navigation.log.
- [ ] Main desktop interaction and user's large scene remain separate acceptance checks. Runtime reports PCIe x8 vs maximum x16 and unavailable DLSS-NR; renders completed despite these warnings. No Cycles tests run.


### Cycles delegate removal — 2026-11-10

- [x] Removed Cycles runtime discovery/registration, embedded worker adapter, MaterialX-to-Cycles translator, render settings/diagnostics, frontend controls and setup/build scripts at the user's request. Previous Cycles milestones above are historical and no longer describe shipped functionality.
- [x] Removed active Cycles setup documentation/instructions and renderer-specific material hints. External downloaded sources, runtime binaries and build caches remain untouched; app no longer registers them.
- [x] Verified frontend build, Rust formatting/check and headless renderer discovery: Storm and Aurora present; Cycles absent. NVIDIA standalone worker/routing unchanged. Existing Vite bundle-size advisory remains. No render tests performed.
- [ ] Restart backend to discard any delegate already loaded in the active process. Manual UI check remains with the user.


### NVIDIA interaction profiling and Storm switch mitigation â€” 2026-11-10

- [x] Profiled real plane/cubes interaction. Inline camera USD population caused ~300 ms renderer step invalidation despite only 3â€“5 ms of population work. Replaced ordinary orbit/pan/zoom with direct ovstage camera worldMatrix writes; measured 7.5â€“16.5 ms total versus 314â€“333 ms previously at 640x360. Pixel difference assertions confirm views still change.
- [x] Resolution changes update render product/camera only, without recompiling/flattening/exporting geometry. Resize still costs ~317 ms due renderer reconfiguration; first load ~441 ms. These are simple-scene worker timings, not production-scene desktop FPS.
- [x] Return actual NVIDIA frame convergence/progression, add scene/step/readback timing fields and reduce idle batch to two steps.
- [x] Release isolated NVIDIA worker/GPU allocations when routing a render back to Storm or another Hydra renderer. Build/check passes; this is a memory-pressure mitigation, not a reproduced/proven fix for the reported Storm crash.
- [x] Python syntax, Rust formatting/check and frontend production build passed. Interaction evidence: I:/AI/OrchFlow-build/ovrtx-interaction.log.
- [ ] User desktop verification: navigation on production scene and repeated NVIDIA-to-Storm switches. Full crash root cause remains unconfirmed.


### NVIDIA production-scene diagnostics â€” 2026-11-10

- [x] Cache graph digests per registered handle rather than serializing/hashing the full graph per camera update. Resize now directly writes camera aperture/clipping and product resolution; it no longer replaces the retained scene USD with a camera-only population source. Frame changes invalidate evaluated scene data.
- [x] Populate composed world transforms once using upstream UsdGeom.XformCache; preserve scene geometry and instances. Real nested parent translation/rotation/nonuniform-scale test reads NVIDIA worldMatrix back and verifies equality with upstream USD after orbit/pan/zoom/resize. Navigation measured ~8â€“10 ms, resize ~240 ms.
- [x] Saved Uranus graph real render completed: initial ~5.1 s, camera updates ~8.5â€“10.5 ms at 640x360; no worker exit in isolated run. Its character remains incomplete/dark: production skeletal/material acceptance has NOT passed. A trial BakeSkinning caused erroneous eyebrow scale and was removed; no implicit baking shipped.
- [x] Native fault handler writes to stderr; Rust reports process exit status when available instead of only disconnected channel. Unique NVIDIA stage names avoid shared fixed identifiers.
- [x] Isolated renderer attachment stalled while the desktop NVIDIA process was running, then succeeded after user closed OrchFlow. This is evidence of concurrent-runtime interference, not a proven explanation of the desktop worker exit. No source assets/project were changed.
- [ ] Resolve remaining production skeletal/material fidelity and reproduce native exit. Keep these open; simple-cube/nested-transform success does not establish full UsdSkel parity. Logs: ovrtx-interaction6.log, ovrtx-nested.log, ovrtx-uranus.log under I:/AI/OrchFlow-build.


### Full NVIDIA integration review â€” 2026-11-10

See OVRTX_REVIEW.md for findings, primary references and remaining acceptance work. Corrected transform contract to omni:xform/reset + CPU_INCREMENTAL hierarchy computation before sealing (real nested transform render/readback passed). Retained fixed NVIDIA viewport resolution through gestures; replaced scene lifetimes in separate native worker processes at backend routing boundary; renderer-specific graph registration; explicit frame-time seconds; honest discovery message and pinned installed dependencies. Removed blind eight-step warm-up and corrected renderMs accounting. Production skeletal/material fidelity, shared viewport control parity, requested sample target, full input-to-present latency and repeated desktop scene replacement remain OPEN. Simple-scene timings do not establish those outcomes.


### NVIDIA stalled startup and Windows worker ownership â€” 2026-11-10

- [x] Windows worker lifetime now uses a KILL_ON_JOB_CLOSE Job Object. The venv launcher can have a separate base-Python descendant; killing only Child does not contain that process tree. A targeted regression test using the actual NVIDIA venv launcher and a sleeping descendant passed: job closure terminates the descendant.
- [x] Added phase logs for renderer creation/attachment, graph compilation/flatten, population, transform compute, frame render and pixel readback. Per-request watchdog emits phase-specific error and stack trace, then exits native worker on a stalled phase (60 seconds ordinarily, 120 seconds first frame shader compilation, 30 seconds subsequent render). Error responses discard the NVIDIA worker. Injected-stall watchdog check passed.
- [x] Real Rust service routing test rendered three scene-identity replacements A/B/A in fresh workers and shut down successfully in 13.76 seconds; no NVIDIA worker processes remained. Same simple plane/cubes asset reused to isolate lifecycle behavior. This verifies routing/process lifetime, not all production assets or the exact historical stall.
- [ ] Production scene acceptance and full UI latency remain open. The posted PCIe x8 / unavailable DLSS-NR / repeated profiler-registration warnings also occur in successful renders and do not identify the stall by themselves. A new phase-specific report will locate any remaining stall.


### Direct Uranus.usd population hang reproduction â€” 2026-11-10

- [x] Direct tested I:/CGPROJECTS/Uranus/Body_base.usd and Uranus.usd through the actual NVIDIA stdio worker. Body_base completed in 5.11 s including startup; Uranus stalled in populating NVIDIA scene and watchdog exited at 66.89 s total.
- [x] Uranus contains 45 unresolved shader asset paths (omniverse://localhost/Projects/Uranus/textures/...) and two invalid connected primvar inputs. Shared the existing selective shader diagnostic/fallback helpers between Hydra and NVIDIA in worker/material_preview.py; embedded worker includes that module. Auto replaces only affected material networks in private layers; no source files or textures changed.
- [x] Rerun same files: Body_base succeeds; Uranus population/render completes in 8.17 s including startup (2.14 s request work, 13 affected material fallbacks), instead of timing out. Artifacts/logs under I:/AI/OrchFlow-build/ovrtx-direct and ovrtx-direct-fixed.log.
- [ ] Visual correctness remains OPEN: direct Uranus image is small/misoriented/incomplete. Successful population is not skeletal/coordinate/material parity. Scene mode explicitly rejects known unresolved shader assets/invalid inputs rather than silently passing them into NVIDIA. Missing assets and fallback count returned to viewport.

Verification for shared material-helper extraction: nine targeted asset/selective-material tests completed (eight passed, one skipped in the isolated NVIDIA USD environment); frontend build, Rust formatting/check and desktop executable build passed. Existing Vite chunk-size advisory and custom Rust output-name warnings remain.

NVIDIA request phase tracing is quiet by default. Set ORCHFLOW_OVRTX_DEBUG=1 before launching OrchFlow to enable it. Verified both logging modes and the watchdog's phase-specific timeout response. Frontend build and Rust format/check passed.

NVIDIA preview disk usage: found 25 abandoned preview snapshots totaling 16.6 GB on C: with about 60 MB free after a renderer switch reported corrupt crate values. Removed the inactive snapshots. New worker-owned scratch directories under the NVIDIA runtime root are cleaned on worker shutdown. Failed or empty exports return a concise error before population. Verified partial-write cleanup, successful snapshot reopen, frontend build, Rust check, and three consecutive NVIDIA replacements with an empty scratch directory afterward. Desktop switching on the original project remains to be checked.

## Renderer scope: Storm, Autodesk Aurora and NVIDIA Omniverse RTX

- [x] Removed Aurora runtime registration, render settings, frontend controls, setup scripts, and the experimental MDL launcher. The Aurora milestones above record historical work.
- [x] Limited OpenUSD renderer discovery to Storm. NVIDIA Omniverse RTX remains available through its standalone worker. External Aurora downloads and build caches are outside the app removal scope.
- [x] Re-enabled Autodesk Aurora (`HdAuroraRendererPlugin`) as a Hydra delegate in `worker/usd_worker.py` (`_configure_aurora_runtime()`), reading `ORCHFLOW_AURORA_ROOT` (defaulting to `I:/AI/OrchFlow-renderers/aurora`) and `runtime.json` (`binaryRoot` / `pluginInfo`) to register its DLLs and `PXR_PLUGINPATH_NAME`, and removing the `HdStormRendererPlugin`-only filter on `UsdImagingGL.Engine.GetRendererPlugins()`.
- [x] Verified the frontend build, Rust formatting and test suite, and worker capability discovery. The OpenUSD worker advertised only Storm and the standalone worker reported NVIDIA RTX available. GPU render tests were skipped.

MaterialX SSS authoring uses the Standard Surface inputs subsurface, subsurface_color, subsurface_radius and subsurface_scale. Removed the three legacy camel-case shader aliases and included radius and scale in portable .mtlx exports. Verified authored USD ports, numeric values, the universal fallback connection, exported values, and six MaterialX tests without rendering. Frontend build and Rust format/check passed. NVIDIA visual response to SSS scale and radius remains unverified.

NVIDIA MaterialX preview now returns camera framing to the viewport, which passes it to replacement workers for the same viewport session and Frame all generation. Explicit Frame all uses the cached full-scene bounds, including on camera-only updates. Verified identical camera matrices across worker replacement and fresh fitting after Frame all or a new session. The private NVIDIA render product also explicitly enables the real-time SSS pass using omni:rtx:rt:sss:enabled. Frontend build and Rust format/check passed. Desktop Apply behavior and SSS appearance remain to be checked on the user's scene.

NVIDIA Auto preview recognizes MDL modules shipped in the installed runtime. A healthy authored MDL surface survives a broken alternate shader output. The private preview disconnects the broken output and blocks unresolved assets to avoid population stalls. Verified OmniPBR module and definition preservation across two shared USD instances with a broken Preview Surface texture. Asset Override and selective fallback now skip empty asset paths during anchoring. Verified the OpenUSD 26.11 error on an empty path and passed four targeted regression checks. Frontend build and Rust format/check passed. Actual MDL appearance in NVIDIA remains to be checked on an authored MDL scene.

NVIDIA viewport exposes Real-Time Path Tracing and Path Tracing. The mode is part of the NVIDIA worker and preview request identity. The existing viewport camera anchor is carried across worker replacement. Verified authored render product tokens, the default and invalid-mode rejection, plus frontend build and Rust formatting/check. The isolated GPU check timed out while the desktop NVIDIA worker was active. Visual mode switching remains to be verified.

## Blender Importer Node — 2026-11-12

- [x] Implemented new `Blender` node (`operation: "blender"`) in the React Flow graph and Rust project model (`NodeData.blenderSelections`, `NodeData.blenderObjects`).
- [x] Background Blender geometry inspector (`inspect_blender_file`) runs headless Blender (`--background --factory-startup`) to extract geometry objects (`MESH`, `CURVE`, etc.) with vertex and polygon counts.
- [x] Frontend `BlenderInspector` displays a scrollable checkbox list of discovered geometry with `[All]` and `[None]` batch selection buttons, allowing the user to limit import selections.
- [x] Headless Blender USD export (`export_blender_to_usd`) exports selected geometry (or all geometry) via `bpy.ops.wm.usd_export` into a user-selected or auto-proposed `.usda`/`.usd` file.
- [x] Automatic registration of `.blend` as `nativeProject` dependency and exported `.usd` as `usd` dependency in the shared dependency registry.
- [x] `compile_graph` evaluates the Blender node as a source node loading the exported USD stage (`source_stage`), enabling downstream nodes (Transform, MaterialX, Light, Material Bind, Delete, Output) and real-time Hydra/Storm and Omniverse RTX viewport preview.
- [x] Verified via Rust unit tests (`test_find_blender_executable_finds_system_blender`, `test_blender_node_project_validation_and_round_trip`), Python graph tests (`worker/test_blender_node.py`), `npm run build`, and `scripts/build.ps1` desktop compilation.
- [ ] Visual verification of imported Blender models and interactive user selection remains with the user in the desktop UI.

## Interaction Speed Optimisation & Live Renderer Activity Reporting — 2026-11-13

- [x] **Interaction Speed Optimisations**:
  - Accelerated interactive mouse gestures (orbit, pan, zoom) by optimizing interactive preview JPEG quality to 70 in `worker/ovrtx_worker.py` and 72 in `worker/usd_worker.py`, reducing base64 IPC payload size by 35–40% and cutting browser main-thread JPEG decoding time in half during mouse dragging.
  - Added global LRU stage caches in `worker/usd_graph.py` (`_SOURCE_STAGE_CACHE` and `_MULTI_ROOT_CACHE`) keyed by normalized path and mtime. Graph evaluations across node switches reuse parsed in-memory OpenUSD stages without repeatedly re-reading or flattening heavy asset files from disk.
  - Implemented in-memory Blender subset stage caching (`_BLENDER_STAGE_CACHE`), bringing cached subset retrieval down from ~2855ms to 0.15ms.
  - Added `clear_graph_caches()` wired to the "Clear scene" operation, honoring user-controlled GPU/RAM flushing.
- [x] **Live Renderer & Importer Activity Reporting**:
  - Workers emit structured stdout status notifications (`ORCHFLOW_STATUS:{"phase": ...}`) across all execution stages without polluting the command response protocol channel.
  - Rust backend `src-tauri/src/usd.rs` parses `ORCHFLOW_STATUS:` in worker reader threads and invokes a shared `StatusListener`.
  - Tauri 2 setup hook in `src-tauri/src/lib.rs` forwards status events to the frontend via `app.emit("renderer-status", payload)`. `inspect_blender_file` and `export_blender_to_usd` emit status events as well.
  - `src/NativeViewport.tsx` listens for `"renderer-status"` and displays a real-time HUD pill (`.viewport-busy-hud`) with an animated glowing pulse dot whenever `busy` is true (even when `frame` is already displayed, resolving the previous silent freeze during heavy node switches).
  - Viewport caption displays active status during render, and post-render reports detailed performance metrics including `updateKind` (`cache-hit`, `material-delta`, or `full-rebuild`), render, capture, round-trip ms, sample progression, and VRAM.
  - Added styling and keyframe pulse animation in `src/App.css`.
- [x] **Verification**:
  - Rust unit tests: 30 passed, 0 failed (`cargo test --manifest-path src-tauri/Cargo.toml --lib`).
  - Rust formatting: verified cleanly (`cargo fmt --check --manifest-path src-tauri/Cargo.toml`).
  - Frontend production build: verified cleanly in 560ms (`npm run build`).
  - Headless dual-renderer tests verified:
    - Test 1: Stage and multi-root caching hit verification & `clear_graph_caches()` flush.
    - Test 2: Headless Blender extraction on `I:\CGPROJECTS\Uranus\project.blend` (2841ms first time -> 0.15ms cached).
    - Test 3: Storm render request & interactive JPEG encoding with live status emissions.
    - Test 4: Omniverse RTX render on `project.blend` in `.venv-ovrtx` with 18 live status events, `updateKind: full-rebuild`, timing and VRAM accounting.
  - Full desktop release build: verified cleanly with exit code 0 (`scripts\build.ps1`).
- [ ] User interactive verification: check smooth orbit responsiveness and real-time activity HUD pill while switching nodes in the desktop UI.

## GoZ (ZBrush) Source Node Support — 2026-11-14

- [x] Implemented binary `.goz` parser (`_parse_goz_file`) and in-memory OpenUSD stage builder (`_open_goz_source_stage`) in `worker/usd_graph.py`, supporting multi-mesh/SubTool streams, triangles/quads, UVs (`st` with V-flip), Polypaint (`displayColor`), Mask (`primvars:zbrush_mask`), Polygroups (`primvars:zbrush_polygroup` and `UsdGeom.Subset`), and Diffuse/Normal/Displacement texture maps (`UsdPreviewSurface`).
- [x] Wired `.goz` into `_open_source_stage`, `compile_graph` reload cache handling, `convert_source_to_usd`, and `usd_worker.py` `inspect` operation.
- [x] Enabled `convert_source_to_usd` in `src-tauri/src/usd.rs` (`UsdService::request`) so Source node USD conversion works through Tauri IPC.
- [x] Updated `src/App.tsx` file browser filters, Source inspector label, `Convert to USD...` action, and `rescanUsdDependencies` (skipping non-USD source files).
- [x] Verified with `worker/test_goz_source.py` (2 passed), `npm run build` (passed), and `cargo fmt --check` (passed). Real `.goz` file loading and desktop viewport rendering remain with the user for manual verification.

## Viewport transform anchor continuity — 2026-11-15

- [x] Project the gizmo relative to the anchor translation carried by the displayed frame. Both renderers settle the anchor at the new world position after a drag, including when an orbit request supersedes the release frame. Rebase queued camera input when settling adjusts pan and zoom.
- [x] Verified consecutive drags and orbit with real headless Storm and NVIDIA RTX renders, including restoration of the previous anchor in a replacement RTX worker. Frontend build and Python syntax checks passed.
- [x] Route viewport picks through the render queue so a pick that settles a drag also applies its camera correction. Project the gizmo using the displayed frame's camera and the shared orbit anchor. A local MCP test picked the translated mesh, set the picked world point as the pivot, orbited, and verified the next translation moved that pivot once in both renderers.
- [x] Update the shared anchor during interactive renders and track the active Transform by ID when a downstream preview is pinned. Verified through local MCP that both renderers moved the anchor during interactive frames with Output as the evaluation target, preserved the picked pivot through orbit, and applied the next drag's translation once.
- [x] Hide viewport render progress and performance details during gizmo drags. The frontend build and render queue tests passed.
- [x] Enable pointer events on the viewport Time and playback range drag handles. Frontend build passed. Desktop drag verification remains with the user.
- [x] Preserve in-progress numeric text and the caret in inspector fields while applying valid values to the model. Frontend build passed. Desktop verification of small decimal edits remains with the user.
- [x] Wrap viewport and timeline controls within the available canvas width. Frontend build passed. Narrow-window desktop verification remains with the user.
- [x] Storm retains the stage and Hydra engine for transform-only edits through single-input Output or Camera previews. It updates the matrix in the Transform's anonymous USD layer. Verified updated rendered pixels and composed world coordinates, including a return to identity, with the same stage and engine. On the simple 800 × 450 pinned-Output benchmark, median worker drag time went from 31.3 ms to 6.3 ms and graph compiles went from 20 to zero across 20 warm frames. Desktop input-to-display timing remains unverified.
- [ ] Desktop verification remains with the user: translate twice and orbit immediately after release in each renderer.

## Hair Node GPU Physics Simulation (NVIDIA Warp) — 2026-11-14

- [x] Added encapsulated GPU hair strand simulation in `worker/hair_sim.py` using NVIDIA Warp (`warp-lang` + `numpy` installed on `I:\AI\OrchFlow-packages\py312` and loaded lazily only when `simEnabled` is true).
- [x] Implemented GPU-parallel local–global stretch/bend Discrete Elastic Rod constraints, root pinning, gravity/wind, stage up-axis & unit scaling, and `wp.Mesh` BVH collider response with Coulomb-style friction.
- [x] Authors time-sampled `points` into the anonymous `hair.usda` overlay layer with an in-memory LRU simulation cache (`_SIM_CACHE`); source layers remain untouched.
- [x] Extended `HairSettings` and validation in `src-tauri/src/project.rs`, `src/fileRegistry.ts`, `worker/usd_graph.py` (`apply_hair`, `inspect_stage_curves` returning both curves and meshes), and `src/HairInspector.tsx` (collapsible GPU Physics Simulation section).
- [x] Added viewport timeline playback controls (`⏮`, `◀`, `▶`/`⏸`, `▶|`, scrubber, `Time`, start/end range, `Loop`) in `src/NativeViewport.tsx` that advance frames only after each render completes.
- [x] Vectorized NumPy/OpenUSD array conversion (`Vt.Vec3fArray.FromNumpy`), merged AABB-culled collider `wp.Mesh` BVH, and defaulted `colliderQuery` to `__none__`; verified 48-frame simulation of `MikoCharacter` (12,600 strands / 259,080 vertices) in ~2.29s–2.79s and end-to-end `usd_worker.py` Storm render in 5.17s.
- [x] Verified with `worker/test_hair_sim.py` (3 passed on `cuda:0` NVIDIA GeForce RTX 3090), `npm run build` (passed), and `cargo fmt --check` (passed). Desktop viewport playback in the restarted app remains with the user.


## Blender Quickstart automatic baseline — 2026-11-15

- [x] Prepare & Open in Blender discloses the handoff USD and `<name>.baseline.usdc` beside the chosen `.blend`. The background adapter exports from the freshly imported scene, validates the standalone USD and its coordinates and handoff identities, fingerprints it, and checks that the staged native project reopens before installing the pair. Existing destinations require individual replacement authorization. The adapter retains backups and rolls back a partial install.
- [x] Register the validated pair using the existing native import record, with frozen conversion, dependency revisions and exporter settings. Apply Edits exposes a Blender command using the captured settings. The separate Create Blender project action still uses the manual baseline workflow.
- [x] Verified the frontend production build, Rust formatting and unit tests, and headless OpenUSD fixtures for identity and coordinate validation, collision handling, rollback and backup retention. Existing frontend and publication test failures also occurred in the untouched checkout.
- [ ] Verify with a real stage and the configured Blender 5.3 installation. Run Quickstart, confirm the linked baseline, export a small edit with the recorded settings, and review it in Sublayer.

## Blender Quickstart rerun — 2026-11-15

- [x] Show Rerun Quickstart when the Blender Application node has a handoff or native link. Use a fresh destination for linked artifacts. Confirm replacement of eligible unlinked outputs using the existing file-write guards.
- [x] Stage the new handoff and native preparation privately. Commit all three new links together after baseline capture succeeds. Retain previous files and dependency entries, preserve downstream output, and keep the link change in session undo history.
- [x] Frontend production build, Rust formatting and 32 Rust tests passed. Focused frontend and backend rerun tests verified staged link replacement and protected destinations. The frontend suite retains four previously reproduced failures.
- [ ] In the desktop app, rerun an already prepared Blender node to a new destination and verify that it opens the fresh project with the captured baseline. Verify that cancellation or preparation failure leaves the original links active.

## Performance Optimizations & Clarisse-Scale Scene Architecture — 2026-11-16

- [x] **Loading, interaction, and binary transport optimizations**:
  - Split heavy frontend panels (`DependencyTable`, `HairInspector`, `BlenderInspector`, `MaterialXPanel`, `react-vendor`, `xyflow`) into async/manual chunks in `vite.config.ts`.
  - Fixed `RenderQueue` frame dropping during interactive motion and added in-memory stage/bounds caching across `worker/usd_worker.py` and `worker/ovrtx_worker.py`, plus binary PNG/JPEG frame transport (`src-tauri/src/usd.rs`, `src-tauri/src/lib.rs`, `src/NativeViewport.tsx`).
- [x] **Step 1 — Prototype-aware USD traversal**:
  - Added `iter_scene_and_prototype_prims` in `worker/material_preview.py` and `worker/usd_worker.py` so scene/material inspection walks prototypes and non-instanced prims without redundantly traversing every instance proxy.
- [x] **Step 2 — Viewport Purpose & Complexity controls**:
  - Added `Purpose` (`render` / `proxy` / `both`) and `Complexity` (`low` / `medium` / `high`) controls in `src/NativeViewport.tsx` and `worker/usd_worker.py` for lightweight proxy and tessellation previews.
- [x] **Step 3 — `Scatter` (`UsdGeom.PointInstancer`) node**:
  - Added `Scatter` node (`operation: "scatter"`) across `src-tauri/src/project.rs` (`ScatterSettings`), `src/fileRegistry.ts`, `src/graphInsertion.ts`, `src/App.tsx`, and `worker/usd_graph.py` (`apply_scatter`) with `random`, `grid`, and `circle` distributions, seed, area bounds, scale min/max, random yaw, and `passThrough` (retaining upstream USD prims alongside `/Scene/Scatter`).
  - Fixed `Vt.QuathArray.FromNumpy` quaternion layout (`(imag_x, imag_y, imag_z, real_w)`) so scattered instances preserve stage up-axis orientation.
- [x] **Step 4 — Surface-aware `Scatter` & spacing/slope masking**:
  - Added `surface` distribution mode to `Scatter` with optional `surfacePrimPath` (or automatic last-input surface when 2+ inputs are connected), area-weighted triangle sampling (`_sample_surface_points` respecting `leftHanded` mesh orientation and world transforms), `alignToNormal`, `maxSlopeDeg` filtering, and 3D spatial-hash `minSpacing` rejection (`_filter_min_spacing`).
- [x] **Scatter prototype prim table & input prim picker**:
  - Added `prototypes` (`ScatterPrototypeRule[]`: `id`, `enabled`, `primPath`) to `ScatterSettings` across `src-tauri/src/project.rs`, `src/fileRegistry.ts`, `src/ScatterInspector.tsx`, and `worker/usd_graph.py` (`apply_scatter` / `_resolve_scatter_prototype_prim`).
  - `ScatterInspector` provides a table-based CRUD list (`+ Add`, toggle `On`, edit `Prim Path`, remove `✕`) and a `Select from input…` modal prim browser querying `scene_children` on the node's upstream graph (`graphPreview(project, nodeId, true)`).
  - `apply_scatter` references each enabled prototype prim (rejecting missing or instance-proxy prims) and picks a random prototype index per scatter instance (`rng.integers(0, len(proto_paths), ...)`), falling back to whole connected inputs when the prototype table is empty.
- [x] **Group (`UsdGeom.Xform`) node**:
  - Added `Group` node (`operation: "group"`) across `src-tauri/src/project.rs`, `src-tauri/src/application.rs`, `src/fileRegistry.ts`, `src/graphInsertion.ts`, `src/GroupInspector.tsx`, `src/App.tsx`, and `worker/usd_graph.py`.
  - Wraps all connected upstream inputs under a named `UsdGeom.Xform` at `/Scene/<GroupName>` with `translation`, `rotation`, and `scale` authored directly on the group `Xform` prim so grouped assemblies can be positioned and selected as a single prototype in `Scatter`.
- [x] **Step 5 — Lazy USD Payloads & Viewport Draw Mode (`Shaded` / `Wireframe` / `Points` / `Bounds`)**:
  - Added `loadPayloads` (`bool`, default `true`) to `NodeData` (`src-tauri/src/project.rs`, `src/fileRegistry.ts`, `src/App.tsx`). When unchecked on a `Source` node, `_open_source_stage` and `wrap_source_stage` open with `Usd.Stage.LoadNone` (keyed separately in `_SOURCE_STAGE_CACHE` / `_MULTI_ROOT_CACHE`) so heavy payloaded sub-assemblies stay unloaded until enabled.
  - Added `Draw` mode selector (`Shaded`, `Wireframe`, `Points`, `Bounds`) to `src/NativeViewport.tsx` and `worker/usd_worker.py`, wiring `UsdImagingGL.DrawMode` (`DRAW_SHADED_SMOOTH`, `DRAW_WIREFRAME`, `DRAW_POINTS`) and session-layer `UsdGeom.ModelAPI` `bounds` draw mode.
- [x] **Verification**:
  - `npm run build` (`tsc && vite build`), `cargo fmt --manifest-path src-tauri/Cargo.toml --check`, `cargo test --manifest-path src-tauri/Cargo.toml --target-dir I:/AI/OrchFlow-build project::tests` (17 passed in `tauri_app_lib` and `orchflow_mcp`), and Python unit tests (`worker/test_worker.py`, `worker/test_assets.py`, `worker/test_selective_materials.py`: 16 passed) passed.
- [ ] **Remaining manual/desktop verification**: Rebuild/restart the desktop app (`.\scripts\build.ps1`) and verify `Scatter` `passThrough`, `surface` scattering, `loadPayloads` toggling, and viewport `Draw` modes (`Shaded` / `Wireframe` / `Points` / `Bounds`) in the live viewport.

## Hair comb root masking

- [x] Comb and Surface Comb exclude camera-backfacing roots before picking stroke depth and strands. The mask uses the nearest collider face normal when available and the first nondegenerate root segment otherwise. Root eligibility stays fixed through the stroke.
- [x] Six hair worker tests passed on the RTX 3090, including overlapping roots with and without a collider. The frontend and desktop builds and Rust formatting check passed. Rust library tests recorded 31 passes, four ignored tests and one failure in Blender executable discovery.
- [ ] Verify both comb brushes on a groom in the desktop viewport. Orbit to the other side and start a new stroke to check that root eligibility follows the view.

## Hair groom persistence across node changes

- [x] Queue the final brush sample and commit against the originating Hair node before rendering a new preview. Apply returned groom deltas to that node's project data even when the old preview image is discarded.
- [x] Verified queued commits across preview changes and empty previews. Seven hair worker tests passed on the RTX 3090, including recomposition from saved deltas after switching to Source, Output and back to Hair. Frontend build and Rust formatting passed. An existing viewport resolution assertion still fails, and the Rust run excluded Blender executable discovery.
- [ ] In the desktop app, finish a stroke, immediately select another node, then return to the Hair node and verify the stroke is still present.

## Avoid scene recompilation after a live groom commit

- [x] Storm and Omniverse RTX keep the live stage when the project sends back the exact groom just committed on the previewed static Hair node. Keep brush opinions in private session layers. Undo, redo, upstream changes and other revisions use normal evaluation when their graph differs.
- [x] Commit an unsent final brush sample with the stroke, reuse its local points for delta encoding, and bypass compiled disk snapshots for editable hair previews. RTX reuses the registered graph digest for scene hierarchy lookup.
- [x] Headless Storm and Omniverse RTX tests on the RTX 3090 confirmed stage reuse, no graph compile on commit, and correct points after undo and redo. The two-strand commit confirmation frames took about 7 ms in Storm and 6 ms in RTX. Seven hair worker tests, focused queue tests, frontend and desktop builds, and Rust formatting passed. The Rust test run excluded Blender executable discovery.
- [ ] Verify repeated comb strokes and node changes on a production groom in the desktop viewport.

### Groom hierarchy refresh and downstream cache follow-up

- [x] Keep the Scene Overview hierarchy stable across groom point and strand-count changes. Read-only downstream previews use the regular caches, and selecting an editable Hair node prepares its simulation session.
- [x] Accept the committed live groom through a single-input Output or Camera chain. Early RTX hierarchy queries for the exact pending commit reuse the live stage.
- [x] Verified three consecutive commits through a pinned Output on both Storm and Omniverse RTX with graph compilation disabled after setup. Hierarchy-key and hair-worker tests passed. Rebuilt the frontend and desktop executable, and passed Rust formatting and 31 library tests with Blender discovery excluded.

- [x] Replace the per-segment Comb and Surface Comb clamp after desktop feedback. Limit the pulled point's reach from its root and let the solver distribute the deformation.
- [x] Limit live RTX brush frames and groom confirmation frames to one render step, with further refinement handled by subsequent frames.
- [ ] Verify comb shape, length, and stroke-release responsiveness in the desktop app. Testing is assigned to the user.

- [x] Reuse the already-rendered RTX brush frame for an exact groom confirmation with unchanged view options. Limit refinement to one step per request while grooming is active. Frontend and desktop builds passed.
- [ ] Verify the release delay on the production groom in the updated desktop build. Testing is assigned to the user.

## Guide-based interactive hair grooming

- [x] Add deterministic spatial guide selection, weighted follower binding, normalized arc-length correspondence, and a Warp follower deformation kernel.
- [x] Wire Comb and Surface Comb on dense grooms to compact guide buffers and reconstruct the full groom for rendering and persistence. Small grooms and selections without an active guide use the full solver.
- [x] Add a local–global ADMM elastic solve for guides. Stretch and bend-chord projections share a banded global solve with fixed roots. Contact still uses the existing collider projections.
- [x] Gather guide brush weights and targets once per stroke. Run each comb substep directly on the compact guide buffers, then reconstruct followers once per brush sample. Report RTX groom computation and encoding separately from rendering.
- [x] Add a refittable GPU BVH for discrete guide-to-guide segment contacts, with corrections distributed to both segments and tangential slip limited by Coulomb friction. Run contact projection after the elastic solve and before scalp collision per substep.
- [x] Transport root-aligned material frames along guide segments on the GPU. Use those frames to deform follower offsets and fade root correction into the strand.
- [x] Project guide friction into angular motion about the strand tangents. Solve a root-fixed torsion chain per substep and apply the resulting roll to follower deformation. The angular solve is split from the positional solve and starts from zero twist at stroke start.
- [x] Report guide strand count, full-solver fallback count, groom time and encoding time in the viewport. Cap Storm grooming previews at 960×720 and stop stationary Comb samples. Coalesced pointer events preserve the cursor position from the previous dispatched sample.
- [x] Save updated groom curves without another full USD traversal and use a run-length zlib strategy in the existing delta format. Bound follower rotation offsets and guide contact corrections after desktop feedback about isolated strands.
- [x] Apply guide comb motion through weighted spring attachments inside the elastic system. Attachments fall off along the guide around the picked point. The brush releases the attachments during settling.
- [x] Interleave guide contact projection with the rod solve. Keep the inertial and brush targets and ADMM duals across both passes, and feed contact targets back into the global system. Transport rest bending into the live frame and let contact-driven twist rotate that bending direction.
- [x] Precompute follower guide segment indices and interpolation fractions once per guide binding. The viewport deformation kernel reuses those lookups on each sample.
- [x] Reuse the rendered brush frame in Storm when the graph confirms the exact groom and the view matches. Use the same view comparison in Storm and RTX.
- [x] Use one quasistatic guide solve per interactive comb sample, with the full cursor delta and no inertial velocity carry-over. Preserve authored substeps for animated simulation and full-strand brush fallback. Factor the guide system in root-relative coordinates.
- [x] Bound guide and follower motion after unstable brush feedback. Reject non-finite proposals, limit segment stretch by backing off the whole strand toward its last sample, and cap collision diameter when authored render widths are large for the guide discretization.
- [x] Convert brush points to USD-local coordinates on the GPU before Storm readback. Synchronize RTX point buffers once per brush request and stage undo snapshots directly into reusable pinned host buffers.
- [x] Sweep moving follower points against the scalp collider during the per-strand stability pass. Back off the whole strand at the earliest entering hit, and leave guide contact to the guide solver.
- [x] Keep bounded ADMM duals across brush samples within a stroke. Reuse the banded factor where a guide has no contacts and end iterations when both guide motion and constraint residual fall below the segment-relative tolerance.
- [x] Compact the active brush point and follower lists once per stroke. Reconstruction and follower scalp checks visit only selected, unpinned strands.
- [x] Rank nearby follower guides by root distance and rest-curve shape at quarter and half arc length. Exclude opposing guide directions when the candidate set contains a compatible guide.
- [x] Keep RTX pin colors and counts stable during ordinary comb strokes. Refresh them when the pin display, pin selection or topology changes. Detect topology changes from pending session flags without traversing the scene on each brush sample.
- [x] Commit the displayed quasistatic Comb and Surface Comb shape on release when all pointer samples have been dispatched. If a sample is still pending, solve it as part of the commit.
- [x] Reuse the USD-local GPU buffer for a stationary comb release. In RTX, skip the point upload only when the same buffer was written to the current scene at its current ordinal.
- [x] Report separate CUDA stream timings for guide solving and follower reconstruction using the existing brush synchronization. Display the latest encoding time even for commits with no solve.
- [x] Include axial roll mobility in the guide contact tangent-plane solve. Apply the same Coulomb-limited correction to guide translations and twist, with root frames fixed. The contact projection and rod solve remain staggered.
- [x] Resolve new guide compression relative to the preceding sample when imported curves already overlap. Let moving segments contact unselected guides near pinned roots, with corrections distributed only to movable vertices.
- [x] Build compact active-guide and active-segment lists at stroke start. Launch elastic, twist and contact queries only for the brush selection while retaining all guides as collision candidates.
- [x] Index curve prim paths on the live USD stage and reuse them for brush samples and pin overlays. Invalidate on prim resyncs or stage changes.
- [x] Initialize guide collision bounds and material frames once per stroke. Update moving guide bounds and transport moving guide frames per sample, while retaining stationary guides in the collision tree and follower bindings.
- [x] Encode localized groom commits as sorted point indices and exact float deltas when fewer than a quarter of the points changed. Keep dense entries and topology overrides readable.
- [x] Convert brush displacements relative to paired world-space and USD-local reference points so untouched points keep their exact authored values through transformed previews and commits.
- [x] Snapshot follower points in the reconstruction kernel as it writes them. Initialize fixed root snapshots once per stroke and avoid copying the full groom on every pointer sample.
- [x] Search guide candidates near both the follower root and its quarter-length landmark. Discard duplicate candidates before weighting to better resolve neighboring layers and partings.
- [x] Prepare interactive Hair sessions without flattening or repopulating RTX when the same graph is already rendered and has no pending live geometry changes.
- [x] Update queued viewport requests with a just-committed groom when they still reference the exact pre-stroke graph, including required commits for quick consecutive strokes.
- [x] Reuse the last RTX brush image on a stationary release when points and view match. Report groom encoding separately from any renderer work still needed on release.
- [x] Dispatch guide spring target updates only for selected guides. Bound Surface Comb travel before the scalp query and use signed scalp clearance so the target follows the outside of the surface.
- [x] Preserve the previous contact side for nearby interior guide-segment crossings. Leave endpoint contacts free to slide and retain the discrete broad phase.
- [x] Apply the strand-level stretch and scalp guard to Comb and Surface Comb strokes using the full-strand fallback.
- [x] Run guide scalp midpoint and point collision kernels over the active guide edge list. Keep the original dispatch for animated and full-strand solves.
- [x] Measure the current comb arc lengths on the GPU at stroke start. Use the stroke pose for target reach, spring falloff and per-sample motion limits after earlier edits.
- [x] Preserve binary image bytes in cached RTX brush frames through transport. Clear the save status when the commit reply arrives and release the queue with an explicit error if a response lacks image data.
- [x] Acknowledge RTX groom commits without resending the image when the viewport confirms it already displays that exact frame. Keep the cached bytes available when the viewport needs them.
- [x] Blend follower displacements around their exact stroke-start points. Keep stationary guides' weight in the blend while skipping their frame transport and reconstruction work.
- [x] Carry the rest bend through near half-turn guide reversals and skip twist trigonometry on untwisted bend projections.
- [x] Read Smooth and Clump neighbors from a stable substep snapshot. Align Clump samples by normalized strand progress, limit per-substep pull, and apply the strand-level stretch and scalp guard to both brushes. Commit the displayed shape on release.
- [x] Use stroke-start arc length for Clump correspondence and Smooth neighbor interpolation, preserving correspondence on unevenly sampled strands.
- [x] Skip the second guide rod solve when the first pass met its convergence tolerance and contact changed neither the position targets nor the twist angles.
- [x] Skip the twist-chain solve for guides with zero existing twist and zero friction impulse.
- [x] Weight each guide contact target by its squared endpoint contribution. Preserve strong contact constraints near the collision and reduce the influence on distant segment endpoints.
- [x] Skip guarded brush solves on curve prims the stroke misses and skip stationary release readback and encoding for clean sessions. Retain the last release round-trip timing alongside encoding time.
- [x] Apply the existing 960×720 grooming preview cap to RTX as well as Storm. Keep the preview size stable across pointer-down and mouse-up and restore the normal viewport size when leaving grooming.
- [x] Feed each projected guide contact into the rod solve at its interpolated point of contact. Include the cross term between neighboring vertices in the banded system and scale the contact block as a whole for dense clumps. The two contacting rods still exchange targets through the contact projection pass.
- [x] Limit sliding friction in the local effective-mass metric, retaining a direct solve for sticking contacts. Keep the stroke-start separation as a lower bound on the target distance for persistent contacts so residual compression does not erase their normal load.
- [x] Store rest bend chords in the guide's material frame at stroke start. Transport that frame from the root during each local bend projection and use the same transport for follower deformation. The bend-chord energy remains an approximation of the published rod energy.
- [x] Allocate the guide budget across root neighborhoods and select representatives by root and rest-curve shape within each neighborhood. Keep the guide count unchanged while improving coverage across neighboring layers and partings.
- [x] Synchronize the producing Warp stream before handing live points to RTX. Report the RTX point upload and write-floor wait separately from simulation and rendering.
- [x] Reuse the live stage when acknowledging an exact groom commit through single-input MaterialX and Material Bind overlays. These overlays preserve curve paths, geometry and transforms.
- [x] Cache the factorization of the root-fixed twist chain across brush samples.
- [x] Cache guide rest segment lengths when building the stroke's material frames and reuse them in stretch projection. Keep the rest-vector fallback for collapsed edges.
- [x] Keep a separate cached banded factor for the elastic and brush system over a stroke. Rebuild the contact factor for guides with transient contacts and keep the brush targets current on every sample.
- [x] Derive guide vertex inertia from the adjacent stroke-rest segment lengths and use the same inverse masses for contact. Keep pinned roots fixed and bound mass ratios and per-contact displacement.
- [x] Exclude followers with no moving guide from the per-sample reconstruction and scalp guard lists. Run the guide motion guard over selected guides only.
- [x] Weight the guide's segment extension energy by inverse rest segment length. Reuse the per-edge weights in the cached elastic factor and its contact updates, with bounds for degenerate sampling.
- [x] Replace two-point bend chords with a three-point, rest-length-weighted change of segment direction. Project the rest bend in the live material frame and include all three coefficients in the banded solve. This remains a surrogate for the published discrete-rod curvature energy.
- [x] Feed a bounded elastic bend torque into the same guide twist solve as contact friction. Use that updated twist in the second positional pass, keeping the root material frame fixed.
- [x] Register the expected post-stroke graph inside the RTX worker. Use its handle when the viewport requests that exact graph, avoiding a second transfer of the groom payload. Re-register normally if the worker has lost the handle.
- [x] Factor each guide's twist chain at stroke start using segment-length-based axial inertia and joint stiffness. Use the same axial inertia for the rolling term in contact friction.
- [ ] Verify Warp execution, root anchoring, partings, follower scalp contact, and persisted brush results through the desktop workflow.
- [ ] Verify the interleaved approximation and implement the paper's coupled frictional constraints and rod energies. Measure simulation, reconstruction, rendering and release separately on a comparable groom on the RTX 3090 before claiming parity with the demo.

## Cache Sculptor & GPU Cloth Physics Brush — 2026-11-19

- [x] Added `Cache Sculptor` node (`operation: "cache_edit"`) across `src-tauri/src/project.rs` (`CacheEditSettings`, `CacheEditLayer`, `CacheEditDelta`, `CacheEditMask` with full boundary validation), `src-tauri/src/application.rs`, `src/fileRegistry.ts`, `src/graphInsertion.ts`, `src/App.tsx`, and `worker/usd_graph.py` (`apply_cache_edit`).
- [x] Implemented Cache Sculptor non-destructive animation cache sculpting in `worker/cache_edit.py` and `worker/cloth_kernels.py`:
  - Stackable sculpt layers with per-layer enable/mute, opacity weight (`-2..2`), temporal influence modes (`frameRange`, `singleFrame`, `allFrames`), center frame, fade-in/fade-out frames, and falloff curves (`smoothstep`, `gaussian`, `linear`, `constant`).
  - Optional triangular-weighted temporal smoothing filter (`temporalSmoothFrames` / `temporalSmoothStrength`) across animated time samples.
  - Compact sparse delta (`indicesZB64` + `deltasZB64`) and uint8 vertex mask (`weightsZB64`) zlib+base64 encoding stored inside the single `.orchflow` project JSON; USD evaluation authors into an anonymous `cache_edit.usda` overlay layer without modifying source files.
- [x] Implemented NVIDIA Warp CUDA (with NumPy CPU fallback) sculpt & Cloth Physics brushes in `worker/cloth_kernels.py`:
  - Sculpt brushes: `Grab`, `Smooth` (volume-preserving Taubin), `Inflate` (along live normals), `Pinch`, `Flatten`, `Mask` / `Unmask`.
  - GPU XPBD Cloth Physics brushes: `Cloth Drag` (elastic patch pull preserving structural edge lengths, interior shared-edge bending hinges, mask pinning, and `wp.Mesh` BVH body collider push-out with Coulomb friction), `Cloth Relax` (gravity drape & wrinkle/poke-through settling over collider), and `Cloth Push` (normal push solved with cloth elasticity & collision).
- [x] Wired live viewport sculpting into `src/NativeViewport.tsx` (toolbar toggle, 10 brushes, `[`/`]` radius hotkeys, Invert/Shift, Undo Stroke, circular brush cursor) and both `worker/usd_worker.py` (Storm session-layer point updates) and `worker/ovrtx_worker.py` (Omniverse RTX zero-copy DLPack point uploads via `_write_hair_points_to_ovstage`), plus `src/CacheEditInspector.tsx` (mesh scanner, layer stack, cloth physics parameters, temporal filter, mask controls).
- [x] Verified:
  - `worker/test_cache_edit.py`: 10/10 passed on `cuda:0` (NVIDIA GeForce RTX 3090), including automatic under-the-hood coarse cloth proxy solving (`worker/cloth_proxy.py` + `cloth_reconstruct_fine_kernel` in `worker/cloth_kernels.py`) for dense meshes (> 8,000 vertices): 96,100 fine vertices -> 4,096 proxy nodes in 262.1 ms one-time init, 24.3 ms per interactive brush step + fine reconstruction.
  - `npm run build` (`tsc && vite build`): passed cleanly in 159ms.
  - `cargo test --manifest-path src-tauri/Cargo.toml --target-dir I:/AI/OrchFlow-build project::tests`: 17/17 passed in `tauri_app_lib` and `orchflow_mcp`.
  - Python syntax check (`py_compile`) passed on `cloth_kernels.py`, `cache_edit.py`, `usd_graph.py`, `usd_worker.py`, and `ovrtx_worker.py`.
  - Note: `cargo fmt --check` reported zero diffs in modified files (`project.rs`, `application.rs`); one pre-existing formatting diff remains in `src-tauri/src/lib.rs:570`.
- [ ] **Remaining manual/desktop verification**: Rebuild/restart the desktop app (`.\scripts\build.ps1`), connect an animated USD/Alembic cloth mesh into a `Cache Edit` node, test `Cloth Drag` / `Cloth Relax` / `Cloth Push` and sculpt layers across frames in both Storm and Omniverse RTX viewports, and verify Undo/Redo and project save/reload.

## Cache Sculptor — Full-Garment GPU Cloth Simulation ("Simulate in Sublayer") — 2026-11-19

- [x] Extended `CacheEditSettings` across `src-tauri/src/project.rs` (with serde defaults and boundary validation), `src/fileRegistry.ts`, and `src/CacheEditInspector.tsx` with `simEnabled`, `simMode` (`animated` / `static`), `simStartFrame`, `simEndFrame`, `simFps`, `simSettleSteps`, `simFabricPreset` (`custom` / `silk` / `cotton` / `denim` / `wool` / `leather`), `simAirDrag`, `simWind`, `simSelfCollision`, `simSelfThickness`, `simPinToCollider`, and `simCacheVersion`.
- [x] Added 5 NVIDIA Warp GPU kernels in `worker/cloth_kernels.py`: `_cloth_sim_predict` (gravity, aerodynamic normal drag/wind, speed clamping, pin blending), `_cloth_self_collision` (`wp.HashGrid` spatial hash skipping 1-ring topological neighbors), `_cloth_bind_collider_pins` / `_cloth_eval_collider_pins` (bind masked vertices to nearest collider triangle so waistbands/collars/straps track animated character bodies), and `_cloth_lerp_collider_pts` (per-substep animated collider BVH refit).
- [x] Implemented `_simulate_cloth_mesh` and `_CLOTH_SIM_CACHE` LRU caching in `worker/cache_edit.py` (with NumPy CPU fallback), integrated before Sculpt Layers in `apply_cache_edit` so simulated garment caches author into the anonymous `cache_edit.usda` overlay layer and Sculpt Layers stack non-destructively on top without modifying source USD files.
- [x] Verified:
  - `worker/test_cache_edit.py`: 8/8 passed on `cuda:0` (NVIDIA GeForce RTX 3090), including animated cloth + moving collider + collider-bound vertex pins + self-collision + Sculpt Layer stacking + LRU cache hit + unchanged source SHA-256, and static drape mode.
  - `npm run build` (`tsc && vite build`): passed cleanly in 166ms.
  - `cargo test --manifest-path src-tauri/Cargo.toml --target-dir I:/AI/OrchFlow-build project::tests`: 17/17 passed in `tauri_app_lib` and `orchflow_mcp`; zero `cargo fmt` diffs in `src-tauri/src/project.rs` (one pre-existing diff remains in `src-tauri/src/lib.rs:570`).
- [ ] **Remaining manual/desktop verification**: Rebuild/restart the desktop app (`.\scripts\build.ps1`), connect a rest garment USD (e.g. from Marvelous Designer) and animated character body into a `Cache Sculptor` node, paint pin masks on the waistband/collar, enable **Cloth Simulation**, scrub the timeline in Storm/RTX, and layer a Sculpt Layer fix on top.

## Cache Sculptor — Explicit `Sim` vs `Play` & Skeletal Playback Overlay — 2026-11-20

- [x] Fixed `frame-reopen` in `worker/ovrtx_worker.py` so skeletal/transform-animated scenes (e.g., `DancerCharacter`) collect time-sampled `Mesh`/`BasisCurves` `point_updates` from `self.current_usd_stage` and overlay them onto `ovstage` via `_write_hair_points_to_ovstage` after `open_usd(self.temp_usd_path)`.
- [x] Decoupled simulation from playback and parameter tweaks:
  - Added `_INTERACTIVE_MANUAL_SIM = True` in `worker/ovrtx_worker.py` and `worker/usd_worker.py` so opening projects or tweaking cloth parameters never auto-runs cloth simulation or triggers full scene recompiles (`_try_fast_cache_edit_delta` returns in < 1 ms for sim-parameter-only edits).
  - Added incremental GPU/CPU solver stepping (`step_cloth_simulation`, `_CLOTH_LIVE_CACHE`, `_CLOTH_LIVE_RUNNERS`, `clear_cloth_sim_cache`) in `worker/cache_edit.py` and wired `clothSimStep` in `worker/ovrtx_worker.py` and `worker/usd_worker.py`.
  - Added explicit **Sim** / **⏹ Sim** button next to **Play (`▶`)** in `src/NativeViewport.tsx` (and **Clear Cache** in `src/CacheEditInspector.tsx`): clicking **Sim** advances the simulation frame-by-frame from the current frame and replaces the cache from that frame onwards (preserving earlier cached frames), while **Play (`▶`)** plays existing cached frames without running simulation.
- [x] Verified:
  - `worker/test_cache_edit.py`: 12/12 passed on `cuda:0` (RTX 3090), including `test_manual_sim_mode_and_step_cloth_simulation_from_current_frame` (manual mode skips auto-sim, `Sim` advances from frame 5, re-simulating from frame 6 replaces frames 6+ while preserving frame 5, and `apply_cache_edit` replays cached frames).
  - `npm run build`: passed in 164 ms with 0 warnings.
  - `cargo test --manifest-path src-tauri/Cargo.toml --target-dir I:/AI/OrchFlow-build project::tests`: 17/17 passed in both crates.
- [ ] **Remaining manual/desktop verification**: Rebuild/launch desktop (`.\scripts\build-and-launch.ps1`), open `DancerCharacter.sublayer`, click **Sim** from frame 0 (or mid-timeline) to watch live cloth draping over the dancing body, tweak `Substeps` (verify instant response with no recompile), re-click **Sim** mid-timeline to replace cache from that frame onwards, and click **Play (`▶`)** to verify smooth cached playback.
