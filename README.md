# OrchFlow



Windows desktop USD workflow editor built with Rust, Tauri 2, React, TypeScript, and React Flow. Read [AGENTS.md](AGENTS.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) before contributing.



## Current functionality



- Create, configure, move, connect, and delete nodes. Reject graph cycles; validate saved/loaded projects in Rust.

- Save/open `.orchflow` JSON projects through native dialogs.

- Inspect USD using official upstream bindings without writing source layers.

- Preview a selected source stage or an application node's published USD through native OpenUSD Imaging/Hydra behind React Flow.

- Discover installed delegates and select a renderer without silent fallback. Storm is verified on an RTX 3090; other delegates are not installed/tested here.

- Separate Graph and Scene modes. Drag to orbit; Shift/right/middle-drag to pan; scroll to dolly. Frame all resets the camera. Resize updates the frame; time code and explicit disk reload are supported.

- Persist separate native-project and published-USD paths, application selection, axis override and positive scale (default 1).



Blender first import with immutable baseline, reversible coordinate snapshots and selective edit-layer publishing are implemented. Blender execution/round-trip acceptance remains with the user. Undo/redo, a local MCP server and MaterialX authoring are implemented. Native-project rebase tools, additional native adapters and shader conversion/baking remain planned. Schema v2 stores all external file links in a shared dependency registry inside one JSON; the previous project/node folder proposal is superseded.



## Run with native Hydra imaging (PowerShell)



Prerequisites: Node/npm, Rust MSVC toolchain, Visual Studio C++ build tools, WebView2, GPU OpenGL support, and Python 3.12. The setup helper uses `uv` to create the Python environment.



```powershell

npm install

.\scripts\setup-imaging.ps1

.\scripts\dev.ps1

```



The setup helper downloads the pinned [NVIDIA OpenUSD Windows build](https://developer.nvidia.com/openusd) (USD 25.08, Python 3.12), verifies its SHA-256, and extracts it to `I:\AI\OpenUSD-runtime\25.08`. Download size is about 486 MB; extracted runtime is about 1.34 GB. Python packages live in project-local `.venv-imaging`. The runtime is not committed or bundled with the application; retain its license notices and review redistribution terms before packaging.



`dev.ps1` chooses the imaging runtime when available. Explicit `ORCHFLOW_PYTHON` takes precedence. For a custom runtime:



```powershell

$env:ORCHFLOW_USD_ROOT = 'I:\AI\OpenUSD-runtime\25.08'

$env:ORCHFLOW_PYTHON = (Resolve-Path '.venv-imaging\Scripts\python.exe').Path

npm run tauri dev

```



Manual environment creation without uv (using an installed Python 3.12):



```powershell

py -3.12 -m venv .venv-imaging

.\.venv-imaging\Scripts\python.exe -m pip install -r worker/requirements-imaging.txt

```



Extract the full runtime and set the two environment variables above. Do not install `usd-core` into the imaging environment; bindings are loaded from the matching runtime's `lib/python` directory. Its DLL paths and MaterialX standard library are configured inside the worker.



In the app, assign `samples/layered.usda` to the source node's USD root layer using Browse. It references `samples/cube.usda`, preserving composition. The scene renders automatically. For application nodes, use reviewed publication to create their downstream USD and select Published in the preview slider. Scene mode fades and disables node interactions so the camera receives gestures. Graph mode restores node editing. Inspect USD separately shows stage metadata with payloads unloaded; rendering loads payloads for the preview.



Only available delegates appear in the dropdown. Discovery does not prove feature parity or compatibility; selection is validated by creating that delegate. Unavailable/incompatible delegates produce errors. Other renderer-specific materials, plugins and environments require separate verification.



Material preview defaults to **Auto**: if shader input/implementation assets cannot be resolved, it substitutes neutral solid materials only for connected material networks that depend on those assets. Working materials remain enabled, including materials on other face subsets. Missing paths are still listed. This prevents a missing opacity texture with a transparent fallback from making valid geometry appear absent. **Scene materials** explicitly keeps authored shading even when assets are missing; **Solid preview** explicitly disables scene materials. These are renderer settings only: source layers, materials, paths, units and geometry are not modified. Relative assets and UDIM tiles are resolved using USD's resolver and authoring layer. Fallbacks use private anonymous layer snapshots, preserving original bindings and instance sharing. Unconnected missing assets do not disable a healthy material. This check covers composed shader input/implementation asset attributes, not a complete project dependency audit. Restore the referenced assets and use **Reload** to recheck them.



## Core-only inspection fallback



```powershell

python -m venv .venv

.\.venv\Scripts\python.exe -m pip install -r worker/requirements.txt

$env:ORCHFLOW_PYTHON = (Resolve-Path '.venv\Scripts\python.exe').Path

# Leave ORCHFLOW_USD_ROOT unset when using this Python 3.13 core wheel.

npm run tauri dev

```



The core wheel is OpenUSD 26.11 and intentionally has no imaging support. The UI reports this explicitly. The full imaging proof uses upstream 25.08, not 26.11; pinning a single shipping runtime remains future packaging work.



`npm run dev` serves the editor in a browser, where native dialogs and USD commands are unavailable. No mesh-only JavaScript USD loader is used.



## Checks



```powershell

npm run build

npm test

cargo fmt --manifest-path src-tauri/Cargo.toml -- --check

cargo test --manifest-path src-tauri/Cargo.toml

.\.venv\Scripts\python.exe -m unittest discover -s worker -p 'test_*.py'



$env:ORCHFLOW_USD_ROOT = 'I:\AI\OpenUSD-runtime\25.08'

$env:ORCHFLOW_PYTHON = (Resolve-Path '.venv-imaging\Scripts\python.exe').Path

.\.venv-imaging\Scripts\python.exe -m unittest discover -s worker -p 'test_*.py'

cargo test --manifest-path src-tauri/Cargo.toml real_worker_renders_persistent_frames -- --ignored --nocapture

```



The real GPU tests verify delegate discovery, nonblank images, camera orbit, resize, Z-up stages, explicit reload, preservation of source files, invalid renderer rejection, and persistent requests through Rust. Without `ORCHFLOW_USD_ROOT`, imaging tests skip.



## Viewport architecture and limits



A persistent isolated Python process owns upstream C++ OpenUSD, a Qt offscreen OpenGL context, the stage and Hydra engine. Rust's reusable `UsdService` serializes newline-JSON requests and receives prefixed responses so native stdout diagnostics do not corrupt the protocol. A 60-second request timeout or crash discards the worker; the next request starts a new one. Shutdown terminates the child. Slow operations run outside the UI thread.



React displays native frames; it does not interpret scene data. Camera requests use an animation-frame-paced queue with one render/decode in flight and one replaceable pending view. There is no camera debounce. Completed camera frames remain displayable during motion; only scene, renderer, time, material mode, or reload changes invalidate old responses. This avoids starving the display during continuous input. Images decode before being presented, keeping the previous image visible until its replacement is ready.



While orbiting/panning or scrolling, previews preserve aspect ratio within 800×600 and use JPEG quality 82. Pointer release/cancellation restores full display resolution and lossless PNG; wheel input refines after 140 ms of inactivity (motion frames do not wait). Idle frames are limited to 1440×1080 in the UI and 2048×2048 at the worker boundary. Scene bounds and asset diagnostics are cached by stage/time and invalidated on reload. Motion frames use one render iteration; idle renders retain the convergence budget (128 iterations or roughly three seconds between render calls). Unconverged frames are labelled. A single long blocking Render call is bounded by the Rust process timeout. Continuous playback/progressive refinement is not implemented.



Measured on RTX 3090 / driver 610.88, imported clothing stage, 30 warm frames per mode through Rust: full 1130×788 PNG median 40.08 ms / p95 40.79 ms; interactive 800×558 JPEG median 5.51 ms / p95 5.82 ms. Median readback/encoding fell from 37.13 ms to 3.89 ms. These measurements exclude WebView display and input latency; they are not measured UI frame rates or performance guarantees for complex scenes. Shared GPU textures remain a future performance option. Display uses preview lighting and sRGB output; production color management is pending.



`npm test` uses Node's built-in test runner (tested on Node 24) to verify continuous-motion display, latest-view coalescing, single-request backpressure, full-quality release, scene invalidation, error recovery, disposal, and preview sizing. An opt-in Rust navigation benchmark uses the sample scene by default or a stage supplied via `ORCHFLOW_BENCH_STAGE`:



```powershell

cargo test --manifest-path src-tauri/Cargo.toml measure_interactive_navigation -- --ignored --nocapture

```



After changing the worker, save your graph, close the running desktop app and restart with `.\scripts\dev.ps1` so the embedded worker is rebuilt. Manual screen verification is left to the user: drag continuously in Scene mode to check that views update before release, try Shift/right-drag panning and wheel dolly, then confirm that the image sharpens and the caption returns to full resolution on release.



## Project storage



OrchFlow owns one `.orchflow` JSON file. All USD files, native application projects and assets remain at their original locations. Schema v2 uses a root dependency registry with stable IDs, descriptive categories and original paths; nodes reference those IDs. The JSON also owns graph layout, node settings and future conversion/provenance records.



The previous project-folder, node-folder, per-node JSON, asset-copy and symlink proposals are superseded. No managed directories or portable asset packages are created. Saving or relinking an OrchFlow project must not modify originals or rewrite paths embedded in external files.



Asset Override nodes own the dependency grid modal. Branch-local relinking updates evaluated USD asset paths in memory; original registry entries and files remain unchanged. Browse stage reuses an existing registered file with a warning. Missing/offline originals remain registered.



Version-1 projects migrate in memory on open; only an explicit save writes version 2. Application profile/conversion history remains planned: up-axis/unit preferences and per-node scale (default 1) will be recorded in this same JSON.



Project saves currently write directly to the selected JSON file. Avoid concurrent writers until transactional saves are implemented. Manual screen verification is left to the user.



## Transform and Output nodes



Add two Source nodes (or fan out one shared Source), connect them to Transform nodes, then connect both branches to one Output input. All incoming edges contribute to the composed USD stage. Select Transform or Output to preview the result. Transform exposes translation in meters, XYZ rotation in degrees, and independent XYZ scale. The operations use USD Xform ops and preserve source geometry. Output is an in-memory stage, not an automatic file export.



Inputs receive separate stable namespaces to avoid colliding prim names. Composed previews use Y-up and meters, with non-destructive wrapper corrections for source units/up-axis. Repeated input assets are cached and referenced through instanceable prims so shared branches can use the same geometry prototypes. A regression test verifies that two transformed branches of a 20,000-point mesh contain one shared mesh prototype.



Single-root inputs reference original layers directly. For multi-root stages, a private anonymous layer-stack snapshot is normalized under one root so sibling material bindings remain valid. Reference/payload/variant arcs remain composition arcs; no full-stage flattening or per-branch geometry copies are performed. These preview layers are never written to disk. Unsupported namespace edits produce errors. Application nodes consume their last successfully reviewed publication; explicit Blender creation/reopening is available separately.



USD files now discover referenced layers/assets automatically, including missing shader files anchored to their authoring layer. Discovery runs after Browse/open and when opening the Dependencies tab; Rescan USD is also available. Native application dependency discovery remains planned. Referenced by node(s) is read-only and derives source use plus downstream graph consumption. Relinking the registry does not rewrite authored references inside external files.



A frontend hot reload cannot add Rust commands to an already running app. If you see `Command find_dependency not found`, the running backend is outdated: copy unsaved project JSON using the offered backup button if needed, then close the old window and launch the updated backend. Do not run new frontend code against an old process when testing schema v2.



Material interoperability is tracked in `IMPLEMENTATION_PLAN.md`: reviewed publication preserves incoming networks/bindings by default, prefer MaterialX with UsdPreviewSurface fallback, and retain native materials in external app projects. Conversion, baking and replacement will be explicit node policies/operations with capability checks. Geometry-only overrides require verified topology/UV/material correspondence. Selective material network/binding overrides are implemented by reviewed publishing; MaterialX/OpenPBR conversion and baking are not implemented yet.



For the standalone build produced in this session, close the old OrchFlow window and run:



```powershell

.\scripts\run-built.ps1

```



This launches `I:/AI/OrchFlow-build/debug/orchflow-updated.exe`, built with embedded frontend assets and the new Rust commands, without needing Vite. The launcher sets the local imaging environment. If an old process cannot save the new schema, use **Copy project JSON** before closing it, paste that JSON into a `.orchflow` file, and open it in the updated app. The currently running app is not restarted automatically.



## Missing-file search and pinned previews



Select dependency rows with the checkboxes, then choose **Find Missing** and a folder. The app searches that folder recursively for case-insensitive filename matches. Unique matches are relinked; multiple matches appear in an explicit Choose match/Use match control in Actions. No-match rows stay missing. Existing files are skipped. Directory links/junctions are not traversed, and large/inaccessible searches report their limits instead of silently choosing an uncertain match.



Missing USD references retain their authored-path provenance in JSON. An explicit relink survives Rescan USD; the original missing reference is not duplicated into a second row. The availability column describes the registry link's current target. Asset Override nodes apply replacements to the evaluated branch through anonymous USD layers; original files retain their authored paths.



Each node card and inspector has a **Pin output** control. Pin one node to keep its output behind the graph while selecting other nodes. Changes to that output's upstream subgraph refresh it; downstream-only edits do not. Unpin to resume selection-driven preview. The pinned node ID is saved in `viewport` inside the same project JSON.



The new **Materials → Inspect output materials** panel is read-only. It lists material surface contexts, shader IDs, geometry/face-subset bindings and missing textures, plus installed MaterialX/OpenPBR shader definitions. Available definitions do not imply that arbitrary Blender/native shaders can be converted or that all delegates render every feature identically. Conversion, baking and selective override authoring remain planned.



Build/test caches for this checkpoint are on `I:/AI/OrchFlow-build` to avoid filling the workspace drive. `scripts/run-built.ps1` launches the updated standalone binary there; it does not restart an existing process automatically.



## Branch-local asset replacements



Create **Source → Asset Override → Output**, select Asset Override, and click **Edit Asset Overrides…**. The dependency grid is now a modal owned by that node. It lists connected upstream USD dependencies, including unresolved shader assets. Relink or selected-row Find Missing stores replacements in that node and updates its downstream Hydra preview; Reset removes a local replacement. Existing registered replacement files are reused with a warning.



Original registry paths and external files remain untouched. Evaluation copies only affected USD layer specs and composition ancestors into anonymous layers, preserving references and instanceability. Identical override branches share prepared layers. Mappings match anchored absolute asset paths across the node's inputs; per-prim material editing and portable export remain future work.



## Reopen an existing Blender project



Add an Application node and choose Blender. **Browse / Relink native project…** selects an existing `.blend`; **Browse executable…** selects the installed `blender.exe`. Both links are persisted in the project registry, with availability and a Check linked files action. **Open in App** requests a visible Blender session with that native project, passing its absolute path directly as one process argument. No shell is used and no import, reconciliation, coordinate conversion or publication is triggered. Successful process creation does not prove Blender loaded the file; verify that in Blender.



The launch uses Blender’s documented file argument: [Blender command-line syntax](https://docs.blender.org/manual/en/3.3/advanced/command_line/arguments.html). The Blender adapter supports reopening, first import and selective publication. Houdini/Maya selections remain configurable but reopening is disabled. A missing native file stays missing until you relink it; OrchFlow never creates/replaces it automatically. The USD publication link remains separate. Axis and scale retain their saved values and are not applied when reopening an existing project. See first import and selective publishing below; automatic native reconciliation remains pending.



Manual checkpoint: restart using `scripts/run-built.ps1`, add an Application node, choose Blender, browse an existing `.blend` and `C:/Program Files/Blender Foundation/Blender 5.2/blender.exe`, then use Open in App. Save/reopen the OrchFlow JSON and verify both links persist. Try a missing native-project path and check the relink error. No screen automation or GUI launch was performed during implementation.



## Application coordinate drafts



Application nodes now offer **Check input conversion** before first import. Configure the application's shared target up-axis and meters-per-unit, then optionally override axis/units on the node. Additional scale defaults to 1. Blender's initial editable target is Z-up and 1 m/unit; verify this against your intended Blender scene settings.



Preflight composes incoming branches and the node's linked input USD, applies an anonymous enclosing USD transform, and reports dimensions plus forward/inverse matrices. For a meter-based input, centimeter target and additional scale 2, the linear factor is 200. **Record preparation draft in project** stores conventions, matrices and input revision fingerprints in the OrchFlow JSON. Recheck after changes to compare with that draft. File revisions use metadata, not content hashes or a watcher.



This creates no native project or external handoff file. It does not apply conversions when reopening an existing native project. Actual native creation captures a separate completed preparation snapshot; Blender execution/round-trip verification remains pending with the user. The draft inverse alone is applicable only when the exporter retains the complete handoff convention.



## Create handoff USD and compare previews



On an Application node, **Create handoff USD…** writes one `.usd` (binary by default) or `.usda` scene snapshot. It composes incoming branches and applies the recorded target up-axis/units/additional scale. USD composition is baked into this one handoff; texture/resource files remain linked. No supporting layer folder is created. Original sources remain unchanged. This is an explicit handoff export, not portable packaging.



The app validates the staged snapshot before installation. Replacing an existing OrchFlow handoff requires explicit confirmation; source files remain protected. It registers the single handoff link and actual conversion/input revision in `node.data.handoff`. Save OrchFlow to retain this record. Handoff creation is separate from native import and publication. Recreate it explicitly to include upstream scene changes; texture links remain external.



Application preview controls now include a two-position **Handoff USD ↔ Published USD** slider. It applies to the selected node when unpinned, or the pinned Application node while you select/edit other nodes. Slider choice is retained per node during the session and does not change downstream graph evaluation; downstream uses published USD. The handoff preview shows the exported application input file. **Preview incoming scene** additionally shows the connected composition before application conversion when unpinned. Empty states explicitly say **No handoff USD yet** or **No published output yet**; rendering failures no longer say processing is still underway.



Manual check: connect Source → Application, create a handoff in a new destination, and inspect it with the slider on Handoff. Create a reviewed publication, slide to Published, pin the Application node, select another node, and toggle the slider again. Save/reopen to verify handoff links and the conversion record. No screen automation was used.



Skeletal stages use a reference-sharing path without OrchFlow's automatic enclosing instances, avoiding the USD 25.08 Storm skeleton-adapter errors caused by nested SkelRoot prototypes. Ordinary mesh assets continue to use shared instances. No original geometry/layers are rewritten. If you created a skeletal handoff before this correction, recreate it with **Create handoff USD…** using a new filename; existing handoffs are not migrated or repaired automatically.



## First Blender project from handoff



After creating a handoff and choosing `blender.exe`, click **Create Blender project from handoff…** and choose a new `.blend` filename. OrchFlow starts Blender in the background with factory-empty settings, imports the recorded handoff, saves/reopens a temporary native file for validation, captures the initial `.baseline.usdc` export, exclusively publishes both new files, then links and opens the native project in Blender. Existing native destinations and nodes with an existing native-project link are refused, including missing native links: use Relink or Open instead. No upstream reimport occurs when reopening.



The adapter requires USD import options verified on installed Blender 5.2.1. Texture packing/copying is disabled (`IMPORT_NONE`), extra importer unit conversion is disabled, and Blender scene units use the handoff meters-per-unit. Blender's Y-to-Z axis correction is recorded in the actual native forward/inverse matrices. The original handoff conversion, Blender/adapter versions, importer/exporter options, baseline digest, source revisions and object counts are saved in `nativeImport` in OrchFlow JSON. Profile changes do not rewrite that provenance or the native project. Original USD remains the authoritative preservation source; Blender imports only its supported USD features. Selective publishing uses the baseline comparison described below.



Blender creation/opening is awaiting your manual verification. API options were probed locally, but no complete first-import execution is claimed. Test with your handoff, save/reopen OrchFlow to check the native link, and verify orientation, scale, textures and rig in Blender. The background import has a 15-minute timeout; failures leave existing native links unchanged. Save OrchFlow after success.



## Drag nodes into the graph



Toolbar node buttons still support clicking. Drag one onto an edge to split that edge into source → new node → target. The target and all downstream nodes move right together to free space; shared descendants move once and unrelated nodes stay put. The downstream edge retains its ID/target socket.



Dropping on any node except an Output connects target → new node from the target output socket and shifts existing descendants. Dropping on an Output connects new node → Output and shifts that Output and its descendants. Dropping on empty canvas places an unconnected node there. Dragging switches to Graph mode; nodes/edges highlight as drop targets. Dropping onto a node adds a connection; it does not replace that node or change the dragged node type.



## Application-level profiles and node edit scope



Use the **Applications** tab for the shared `applications.json`. Its resolved path is shown in the tab; on Windows it is normally `%APPDATA%/com.orchflow.desktop/applications.json`. The catalog is loaded from bundled defaults until you explicitly save it. It is separate from every `.orchflow` project. There is no project-level application configuration and no compatibility migration for the previous `applicationProfiles` field.



The profile table edits stable ID, application/version, revision, adapter version, up-axis, meters-per-unit, default scale, executable and import/export behavior. Select **Edit capabilities** on a row to edit every category's supported/unsupported/unverified status and reason/evidence. Add/remove profiles, browse executables, reload/discard and save are supported; **Full JSON editor** exposes the complete file. Table edits advance the revision; raw JSON edits must increase it manually. Changing app/adapter versions through the table resets capability assertions to unverified. File writes validate the catalog and replace it atomically.



Application nodes select a profile and their own edit scope. Unsupported, unknown and unverified options are greyed out with explanations. Scope defaults to preserve-all/empty, with deletion permission separate from geometry edits. Nodes may override coordinates and executable; otherwise they use the application defaults. Unknown profiles remain unavailable; no guessed capability or profile is substituted. Capability assertions are configuration, not proof of universal USD preservation.



Handoff/native snapshots record the full resolved profile/version and the selected node scope as historical provenance. This is not a project configuration level. Backend handoff/first-import checks reject unsupported selected scopes and profile/scope changes since the handoff; recreate the handoff explicitly. Native reopening does not reimport or apply new preferences. First import also checks Blender's actual version against the configured version pattern and checks required importer options. Blender execution remains for you to verify.



**Publication:** raw exports are reviewed against an immutable initial baseline and merged over preserved input. A raw link cannot bypass review as a complete replacement. Bundled round-trip capabilities start unverified; configure only capabilities you have verified. Unsupported correspondence and unsafe remapping remain blocked.



Manual checkpoint: open Applications, configure your Blender executable/defaults, edit a capability status/reason and Save. Select that profile on an Application node; verify unavailable categories are grey and only supported ones are selectable. Create a new handoff, change its node scope/profile, and confirm first import refuses the mismatch. Save/reopen the project to check snapshots without a root `applicationProfiles` field. Old project-level profiles and old snapshots lacking the new policy are rejected, with no migration.



## Selective publishing from Blender



Use Blender adapter **version 2** in Applications. Saved version-1 catalogs are not silently upgraded: edit the adapter version explicitly, increase the profile revision, and verify capabilities. Existing native preparations without a baseline are not reconstructed. Create a new Application node/handoff/native destination to start a new round trip while retaining your existing files.



First creation writes a disclosed sibling `<native filename>.baseline.usdc` containing Blender's initial unedited export. This captures what Blender actually retained, rather than assuming it understands every input USD schema. Keep the baseline unchanged. Native import and baseline execution still require your Blender verification.



After editing in Blender, export the complete prepared scene using the recorded **exporterOptions** shown in the publication dialog. In particular, retain custom properties/round-trip identity, instancing, axis, units, animation time rate and external texture links. The adapter uses [Blender 5.2 USD operators](https://docs.blender.org/api/5.2/bpy.ops.wm.html); behavior notes in the application JSON do not override its executable options.



Choose **Review / Publish edited USD…**, browse the export, then **Analyze changes**. Review compares original preserved input, initial baseline and edited export. Input-only cameras, schemas, materials and other unrepresented data survive. Only changes allowed by the frozen capabilities/node scope are authored. Compatible identities/paths match automatically; the correspondence table lets you resolve renames explicitly. Deletions need both permission and individual confirmation. Ambiguous geometry/face/UV/joint/variant remapping blocks publication rather than guessing.



If you verified new capabilities or changed node permissions after import, use **Authorize current scope for this baseline** explicitly. This checks the unchanged input/baseline, app/adapter version and coordinate contract, and records a separate publication policy. It does not rewrite native-import provenance or reimport anything. Changed input graphs/files or coordinate conventions require explicit reconciliation/new preparation.



**Publish reviewed edits…** writes a new chosen `.usda` and sibling `.edits.usda`. The edit layer references preserved input; the root applies the recorded return conversion to Y-up/meters. Originals, raw export, native file and previous publication stay unchanged. Registered/existing destinations are refused. Only successful publication updates output sockets and switches preview to Published. Save OrchFlow to retain the new dependency links/report. Failed or stale reviews retain the previous valid output.



Identical compatible edits to shared static-asset branches reuse an edited prototype. Branch-specific changes may need separate evaluation; review reports affected instance ancestors. Skeleton-bearing scenes retain the existing reference-sharing path. Automatic shader translation/baking, new-geometry identity assignment, arbitrary topology/rig remapping and native rebase tools remain future work.



Manual test: create a fresh v2 round trip, edit a mesh, export with recorded settings, review/publish, compare Handoff/Published and downstream Output, then save/reopen. Verify physical scale/orientation, textures and rig in Blender yourself. Change an upstream transform and check that subsequent publishing requests reconciliation without altering native edits or silently accepting a stale export.


## Reconcile upstream without another Blender export

After publishing once, choose **Reconcile upstream...** on the Application node. OrchFlow composes the current connected input under the existing adjustment layer, using its recorded axis/units/scale. It does not run Blender, require a new export or present conflict review.

Node opinions win at matching USD paths. Upstream data with no override flows through, including added objects and untouched transforms/materials. A points override replaces the entire upstream points array; ordinary USD layering is not additive sculpting. Renamed/removed paths use normal USD composition semantics without automatic remapping.

Choose a new `.usda` destination. This explicitly writes the root and sibling `.edits.usda`, `.input.usda` and `.input.layers` folder; retain these together. Original assets remain linked, and existing/registered destinations are refused. Only success updates the node output. Save OrchFlow to retain new links/revisions. Handoff still previews the original native-preparation handoff; Published shows the reconciled output.

New adjustment generation omits identical properties and redundant values/connections/metadata. Changed arrays remain whole-array opinions. Reconciliation preserves previously published edit intent even when new upstream values happen to match it. Compatible shared edited branches retain prototype sharing.

Manual test: change an upstream Transform and add an untouched object/property after publishing, reconcile to a new filename, and verify preview/downstream Output plus save/reopen. Blender native/export files need no resave. Native/baseline snapshots remain historical: reconciliation updates USD output, not Blender's editable scene or its original baseline for later export review.

## Undo and redo

Toolbar Undo/Redo and Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y restore project edits: nodes, positions, connections, node settings, dependency links and pins. Node drag updates and focused inspector edits are grouped. Selection, measurements, camera navigation, renderer settings and automatic dependency discovery do not add history entries. Text inputs retain native text undo; the Applications tab keeps its own edit/save/discard flow.

History is session-only, bounded to 100 entries and roughly 64 MB of snapshots (retaining at least the latest entry), and resets when opening another project. Saving does not erase it. Undo/redo changes project JSON state, not generated USD files, native projects, external textures or already executed application work. New edits after Undo discard the redo branch.

## Additional application templates

Bundled profiles now include ZBrush, Marvelous Designer and Substance Painter. Existing catalogs can use **Add missing bundled profiles**, then Save, to add these without replacing customized profiles. Set the installed version and executable yourself. Possible edit categories remain unverified/disabled until tested; profile templates do not install launch/import adapters.

- ZBrush: the [official USD plugin](https://help.maxon.net/zbr/en-us/Content/html/user-guide/zbrush-other-programs/usd-format/usd-format.html) handles meshes, not entire scenes. The template limits the intended workflow to geometry/UV interchange and retains incoming scene/material data. Physical units and Tool Export scale/offset need verification.
- Marvelous Designer: [USD documentation](https://www.marvelousdesigner.com/manuals/articles/47358343098777) covers garments, avatars, materials and animation and identifies Y-up. The template's millimeter handoff units are a provisional editable preference; match actual import/export scale settings.
- Substance Painter: [USD Asset export](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/export/export-window/export-settings) exports material/texture assets and optional mesh assemblies. The template focuses on materials, preserving upstream geometry. Its Y-up/meters handoff preference is provisional, not detection of the installed app.

## MCP for test projects

`orchflow-mcp` is a headless Rust stdio MCP server reusing the desktop validator, domain request builders and persistent OpenUSD worker. It exposes project creation/load/validation/save, application profiles, USD capabilities/inspection/dependencies/rendering, handoff export, publication review/publish, upstream reconciliation and MaterialX document generation/export. Image results are native MCP image blocks. It does not launch Blender or control the active desktop editor. Load/reload a generated `.orchflow` file in the app to inspect it manually.

The `orchflow` server has been registered using the [Codex MCP CLI](https://developers.openai.com/codex/mcp). Reload MCP connections or start a new Codex chat if this chat's tool inventory has not refreshed. Registration can be inspected with `codex mcp get orchflow`. Rebuild both executables with `scripts/build.ps1`; do not replace a running/locked binary. Re-registration if needed:

```powershell
codex mcp add orchflow --env ORCHFLOW_PYTHON=E:/CODINGPROJECTS/Rust/OrchFlow/.venv-imaging/Scripts/python.exe --env ORCHFLOW_USD_ROOT=I:/AI/OpenUSD-runtime/25.08 -- I:/AI/OrchFlow-build/debug/orchflow-mcp.exe
python scripts/test-mcp.py
```

Project tools return unsaved JSON until `project_save` is called. Existing saves require the revision token returned by `project_load`, preventing an accidental stale replacement. Artifact tools explicitly write new destinations and return links for the caller to retain in project JSON; they never silently save the project or modify an active UI session. The stdio server exposes no shell execution or arbitrary Python execution.

## MaterialX authoring node

Add or drag **MaterialX** between a Source/Transform and Output. The node authors a MaterialX `standard_surface` network and a universal UsdPreviewSurface fallback in anonymous USD layers. Edit linear RGB base color, metalness, roughness, specular weight and opacity; link base-color (sRGB) and roughness (raw) textures from their original locations. Missing textures remain visible to existing asset diagnostics. PreviewSurface approximates the MaterialX parameters; delegates are not guaranteed to match perfectly.

The default `*` target binds the material over the entire incoming branch with stronger binding priority, preserving shared geometry/prototypes. Alternatively enter absolute composed USD prim paths, one per line. Missing targets and instance-proxy descendants produce an error; select an instance root to retain sharing. Existing material networks remain in the incoming scene. This is explicit material authoring/binding, not native shader conversion, baking, arbitrary graph editing or OpenPBR authoring.

**Export MaterialX document...** writes a new `.mtlx` at your chosen location, linking external textures without copying them. It registers the document in OrchFlow; Save to retain that link. USD binding targets and the PreviewSurface fallback stay in the USD graph and are not embedded in the material-only document. Existing/registered destinations are refused. The exported document reopens through the pinned upstream USD MaterialX plugin; native graph rendering is tested with real Storm. Other apps/delegates remain for verification.

Windows toolbar drag/drop requires `app.windows[].dragDropEnabled: false`: Tauri's default native WebView2 file-drop handler otherwise intercepts HTML5 drag events. OrchFlow uses browser drag/drop for toolbar nodes and native Browse dialogs for file selection. Configuration reference: https://v2.tauri.app/reference/config/#dragdropenabled. Restart the built app after this native setting changes; frontend reload alone is insufficient.

Auto also falls back per material when a connected UsdPrimvarReader requests an empty or `_` UV/primvar name that the pinned Storm shader generator cannot use. It reports that reason without guessing a UV set. Scene mode reports an actionable error for those inputs; explicitly repair the original shader in an external USD editor if its correct UV binding is known. Find Missing and registry registration normalize Windows verbatim drive/UNC prefixes into ordinary file links.

## Scene overview

The collapsible left sidebar lists the current preview stage's USD prim hierarchy, including instance children, with names and schema types. Expand rows lazily; wide sibling lists load in pages. Click geometry or an ancestor to highlight its rendered subtree in orange; Clear selection removes highlighting. Selection follows the active/pinned preview and Handoff/Published choice and clears when that scene identity changes. It is session-only, with no graph edits, undo entries or USD writes. Material/shader prims remain inspectable hierarchy rows but have no geometry of their own to highlight. Refresh hierarchy rereads the stage; use viewport Reload after external scene/texture edits to refresh rendering diagnostics too.

Publication refuses to write an unchanged scene when analysis finds zero permitted edits. Check the application's verified capability and node scope (for vertex edits: Geometry points), explicitly authorize changed permissions against the existing baseline, then analyze again. Saving a native file or exporting USD does not itself change node permissions. Use the recorded exporter options: a static single-frame export from an animated baseline can introduce animation differences that require reconciliation. Constant time-sampled prim IDs are read as stable identities, including exports with an added root prefix; varying IDs remain ambiguous.

## Compact application sidebar

Application nodes receive input through graph connections: browse files on Source nodes and connect them to the Application. The redundant Additional input USD picker is removed; pre-existing direct links remain unchanged and can be explicitly cleared in Advanced node settings.

The application sidebar focuses on Application settings, a closed Allowed edits rollup, Application preparation/native opening, and publishing. New application-profile selections initially allow every capability marked supported; unverified/unsupported categories stay unavailable. Existing saved scopes and native preparation policies are retained until explicitly changed/applied. Use Allow all capable in the rollup for existing nodes. Capabilities and persistent executable defaults are configured in Applications; the shortcut opens the matching profile.

Coordinates, units, scale, matrices and conversion checks live in a closed Coordinates and scale rollup within advanced preparation. Detailed native paths and provenance are under Advanced node settings. Executable browsing/editing belongs exclusively in Applications and persists across projects. Previously saved node overrides are retained until explicitly cleared using Use global application default; no new override can be added in the sidebar. Changing only executable location/revision does not invalidate an otherwise identical preparation contract.


## Manual Blender baseline workflow

Create a handoff, create or link the Blender project, and manually export the unedited baseline before making changes. Export edited USD with the same selection, custom properties and settings. In USD output, use Browse baseline USD, then Publish edited USD to select/review the edited file. New native creation no longer automatically exports a baseline. Keep baseline files unchanged.

Selected exports match stable OrchFlow IDs and need not contain the original root; unsafe correspondence is blocked. Application exports preserve missing objects. Use a Delete node for removal and explicit new-prim approval for supported additions. Selective handoff preparation is planned for later.


## Live application output

Choose baseline and edited USD, review, then Apply reviewed edits. This writes no USD files: reviewed sparse opinions are saved inside the OrchFlow JSON and restored as anonymous layers on project open. Current upstream materials/transforms compose beneath those opinions automatically; no Reconcile action is needed. Changes to baseline/edited files or unsafe upstream topology require a new review. Export final USD is optional and writes one binary USD snapshot by default, with textures/resources linked externally. Keep large unedited assets in Source branches merged downstream of the application.


## Frontend hot reload

Use `scripts/dev.ps1` for UI work. The window is titled **OrchFlow - Development (live reload)** and loads Vite at localhost:1420. React/CSS edits update that window through Fast Refresh. Rust changes rebuild/restart automatically; embedded Python worker edits still require a backend rebuild. Compiled windows launched with `scripts/run-built.ps1` do not hot-reload.

The launcher retains OpenUSD configuration and defaults Cargo output to `I:/AI/OrchFlow-build`. Keep the dev launcher running and open your saved project in the development window. Existing compiled app windows are left open; they are not converted to dev mode. Large structural UI changes can reset component state, so save projects normally.

Manual baseline selection is profile-based, including ZBrush and other configured applications. It does not require a native-import automation adapter. Stable handoff identity, coordinate correspondence and verified edit capabilities remain required; template capabilities are not automatically enabled. Native creation/opening automation is still Blender-specific.


## Application geometry exports

Manual baseline and edited exports can use USD, polygon OBJ or Alembic (.abc); use the same format and exporter settings for both. Map exported objects to their handoff prims explicitly when IDs are absent. OBJ/Alembic coordinates must match the handoff's recorded axis and metres per unit, disclosed when selecting the baseline. Baseline/input topology and vertex correspondence checks remain mandatory.

OBJ preserves polygon vertex order, object meshes, UVs and normals in a transient USD representation; it does not import MTL shaders, vertex colours, curves or free-form surfaces. Alembic uses the upstream [usdAbc plugin](https://openusd.org/dev/plugins_alembic.html), with polygon meshes, transforms and time samples subject to node capabilities. A runtime without usdAbc reports the missing reader. These are application export readers; Source stage inputs and handoffs remain USD.

Upstream USD materials and data outside the permitted edit scope remain preserved. Importing exports writes no intermediate files; reviewed sparse USD adjustments are stored in the OrchFlow JSON. Real OBJ/Alembic application verification is pending with the user.


## Single-file handoff snapshots

Create handoff USD writes one composed scene snapshot, with geometry/material opinions, stable IDs, transforms and coordinate metadata. It creates no supporting .layers folder. Both .usd and .usdc destinations explicitly write binary Crate data, independent of USD_DEFAULT_FILE_FORMAT; .usda remains a text option for debugging. Final scene export uses the same writer and defaults to final.usd. Textures and shader resource files remain external links; this is not portable asset packaging.

Upstream [OpenUSD flattening](https://openusd.org/release/api/class_usd_stage.html) retains scene instancing and the currently selected variants, while resolving the layer/reference structure into the snapshot. Original sources and the in-memory graph retain their composition structure. Recreate a handoff explicitly to incorporate upstream scene changes.

For manually exported baseline and edited USD, choose binary .usdc (or binary encoding with .usd) in the third-party application's exporter and keep both exports' settings identical. OrchFlow accepts existing ASCII files but does not rewrite immutable baselines or user-owned exports. Anonymous evaluation layers remain in memory; adjustment text inside the single project JSON is unchanged by this disk-export encoding choice.


## Selective handoff and simpler generated hierarchy

Application preparation → Handoff contents offers Whole scene or Selected objects. Choose objects from a searchable incoming-scene hierarchy. Parent selection includes its subtree; required bound materials, rig targets and connection dependencies are included automatically. Ancestor transforms and original asset-relative paths remain intact. Instance proxy children cannot be extracted independently; select their containing instance. This reduces exported content, not the initial source-stage load.

Single-input nodes preserve /Scene/<asset-root> paths. Only multi-input merges add branch namespaces. Handoff and return coordinate transforms live on the existing /Handoff and /Scene roots, removing repeated generated Units/Asset, Inputs/Input and PreservedInput levels. Original source-authored hierarchy remains.

Namespace v2 changes generated prim paths. Previous handoffs and saved adjustments require a fresh handoff/matching baseline/review; they are not silently remapped. Existing native/export files and JSON adjustments are retained. Review still preserves upstream objects outside the exported selection.


## Viewport renderers

Storm is the default OpenUSD viewport renderer. NVIDIA Omniverse RTX is available when its standalone runtime is installed.

Select NVIDIA RTX to choose Real-Time Path Tracing or Path Tracing. Real-Time Path Tracing is the default. Changing modes preserves the viewport camera framing.

The toolbar's Clear scene button starts an empty Untitled workspace after a warning prompt. It resets undo history; saved projects and external linked files remain on disk.
