# NVIDIA ovrtx integration review — 10 November 2026

The integration is a standalone ovrtx/ovstage renderer in a separate process, not a Hydra render delegate. Isolation from the USD 25.08 Hydra process is appropriate because NVIDIA uses a different runtime. It is not yet a complete replacement for the existing viewport path.

## Findings and disposition

| Priority | Finding | Disposition |
|---|---|---|
| High | Scene replacement reused a populated native renderer after clear/repopulation. Production scene changes have crashed or stalled. | Rust now replaces the NVIDIA worker process when graph/path/revision/frame changes or an explicit reload is requested. Camera, sample refinement and resolution changes reuse the current worker. This containment is build-verified; repeated desktop scene replacement still needs acceptance. |
| High | Transform updates wrote derived worldMatrix columns instead of the documented local-transform/reset/hierarchy contract. | Initial population computes hierarchy before sealing. Camera writes omni:xform with MATRIX semantic and reset=true, computes hierarchy at the same ordinal, then seals. Nested translation/rotation/nonuniform scale render/readback passed. |
| High | Drag start/end changed render resolution, costing about 240–250 ms each transition even when individual camera requests took 8–11 ms. | NVIDIA now retains viewport resolution throughout gestures. Actual window resize still reconfigures the renderer. This trades higher motion pixel count for removal of repeated resize stalls. |
| High | Production character fidelity is incomplete. Simple cubes did not validate skeletons, skinning, curves, instances or complex materials. | Still open. Saved Uranus graph renders but looks incomplete/dark. A trial skeletal bake caused incorrect eyebrow scale and was removed. Do not claim full UsdSkel/material parity. |
| High | Shared material preview modes and comparison-conversion requests are ignored by the NVIDIA worker. | Still open. Auto fallback, Solid mode, handoff comparison conversion and selection diagnostics do not have Hydra-path parity. |
| Medium | Sample control is sent but unused; target is hard-coded to runtime default 512. | Still open. Actual frame progression/convergence is returned, but requested sample target is not implemented. |
| Medium | Installation/discovery reported material readiness without validating an actual scene. | Message now reports installation only. Compatibility remains scene-dependent. |
| Medium | Image readback copies GPU pixels to CPU, encodes JPEG/PNG, sends base64 through Rust/WebView and decodes each image. | Measured worker timing excludes WebView presentation and input scheduling. No zero-copy/shared GPU texture transport exists. Future latency claims must measure the full path. |
| Medium | World-transform readback alone does not prove final rendered skeletal correctness. | Kept separate acceptance criteria. Ordinary nested transform tests pass; production rig still fails visual acceptance. |
| Medium | Runtime dependencies were unpinned. | Pinned installed ovrtx 0.6.0.1791947691 / ovstage 0.3.0.1791986420 and the isolated Python dependency set. No SDK download or upgrade performed. |
| Low | Graph registration cache was shared across renderer IDs, causing missing-handle retries when switching. | Registration cache keys now include renderer ID. |
| Low | Render timing included encoding and other work, obscuring the bottleneck. | renderMs now reports step time; sceneMs/captureMs/totalMs remain separate. |

## Verified and unverified behavior

Verified using real RTX renders: plane and coloured cubes, parent translation/rotation/nonuniform scale, orbit/pan/zoom, exact landscape/portrait image dimensions, and runtime world-transform equality with upstream OpenUSD. Canonical transform test log: I:/AI/OrchFlow-build/ovrtx-canonical.log. Camera updates were 8–9 ms at 640x360; true resize remained about 252 ms. Saved Uranus worker updates were 9–11 ms, but its character image is not accepted as correct. That measurement is not desktop interaction FPS.

Renderer attachment stalled while the desktop NVIDIA process was active, then succeeded after the user closed OrchFlow. The root cause of the earlier native worker exit remains unconfirmed. Process isolation limits retained scene state; it does not establish that all driver/runtime faults are resolved.

No automatic source edits, material conversion or skinning bake is shipped. The private preview still flattens into a temporary snapshot at scene load; geometry is not re-exported on camera movement. Full source composition/rig/material parity requires explicit validation.

## Primary references

- https://nvidia-omniverse.github.io/ovstage/scene/transforms.html — transform semantics, hierarchy computation and watermark ordering.
- https://nvidia-omniverse.github.io/ovrtx/_modules/ovrtx/_src/renderer.html — attach/detach/step ownership contract.
- https://github.com/NVIDIA-Omniverse/ovrtx — standalone renderer and ovstage architecture; pre-release status.
- Installed ovstage population.py — open merges rather than replaces; time argument is seconds.

## Remaining acceptance work

1. Repeatedly switch two different scenes, including reload and NVIDIA-to-Storm transitions, through the desktop routing path.
2. Measure input-to-present latency and stalls at the actual viewport size; do not infer desktop smoothness from worker render time.
3. Resolve production rig/material fidelity before calling NVIDIA a generally compatible USD viewport.
4. Implement or explicitly disable unsupported shared viewport controls, then test each supported setting.


## Follow-up: Windows worker containment

Confirmed launcher/descendant cleanup by a targeted regression test. Worker cleanup now terminates a Windows Job Object containing the process tree, including base-Python renderer descendants. Real Rust routing A/B/A replacement test passes (13.76 seconds, three successful renders, no remaining NVIDIA worker). New phase-specific watchdog bounds native stalls and provides stack traces; injected-stall check passes. These results improve lifecycle evidence but do not prove the exact earlier hang's root cause or production skeletal fidelity.


## Follow-up: direct Uranus population stall

Direct Uranus.usd stalls at NVIDIA population with 45 unresolved shader asset paths and two invalid inputs; Body_base.usd succeeds. Sharing the existing selective Auto fallback with NVIDIA resolves this reproduced stall: same source completes in 8.17 s including startup, replacing only 13 affected material networks in private preview layers. Source data unchanged. Image remains visually incorrect (orientation/framing/character fidelity); load success is not full scene acceptance. Auto parity is now implemented for these diagnostics; Solid, comparison conversion and broader renderer parity remain open.
