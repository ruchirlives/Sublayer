# Sublayer: open-source adoption and launch plan

**Version:** 28 November 2026 · **Status:** proposed operating plan, not evidence that outreach has occurred.

## Positioning

**Category:** OpenUSD-based visual DCC workflow orchestration and scene composition.

**One-line promise:** *Connect your 3D tools through a non-destructive USD node graph.*

**Target user:** Independent 3D artists, technical artists and small studios on Windows already using Blender and/or USD, who want to assemble, preview and pass scenes between tools without committing everything to one DCC.

**Differentiators worth demonstrating:**
1. Original USD/assets remain linked; a single `.sublayer` project holds the graph.
2. Blender handoff and sparse-adjustment publishing with explicit upstream reconciliation.
3. Multiple Hydra delegates: Storm, Aurora and optional NVIDIA Omniverse RTX.
4. Substance Painter bridge and MaterialX publishing.
5. Optional MCP interface for headless scene operations.

**Avoid:** 'free Houdini replacement', 'one-click compatibility with every 3D app', 'production-ready pipeline', or guaranteed visual/material parity. ZBrush and Marvelous Designer are profile templates. Explain unsupported features and OS limits.

## Audience, message, demonstration

| Audience | Pain | Demonstration | CTA |
| --- | --- | --- | --- |
| Blender/USD artist | Switching tools breaks the context or forces exports | USD Source → Transform → Output; return to Blender with explicit handoff | Try the 15-minute quickstart |
| Environment artist | Repeated props and landscape assembly | Asset → Instancer/Scatter → Storm preview | Download and share a scene |
| Lookdev artist | Renderer differences and material transfers | MaterialX → Storm/Aurora comparison with limitations visible | Test a material workflow |
| Pipeline TD/open-source developer | Custom USD integration effort | Headless MCP inspection + graph composition | File reproducible issue / contribute |

## Launch prerequisites and blockers (complete before broad promotion)

- [ ] Verify an actual downloadable signed or clearly labelled unsigned Windows installer exists on the latest release, with version, file size and checksum.
- [ ] Fresh-install test on a clean Windows machine: installer, runtime wizard, USD Source → Output preview, save/reopen, uninstall.
- [ ] Confirm the first-run prerequisites, bundled/open-source versus optional proprietary runtimes, and approximate download sizes.
- [ ] Record 60-90 seconds of *real* app operation (not generated mock-ups) and capture 3 screenshots: graph+viewport, one workflow, renderer comparison.
- [ ] Create a freely redistributable small USD demo and confirm licences for each sample asset.
- [ ] Review repo and Git history for secrets, personal paths, proprietary binaries, and third-party licence compliance.
- [ ] Confirm maintainership bandwidth, issue response cadence and support expectations.
- [ ] Add GitHub repo topics and description manually in repository settings (connector does not expose these write controls).

## 90-day programme

### Weeks 1–2: make the first success easy

- Above-the-fold homepage: benefit-first sentence, screenshot, Download, Quickstart and Report issue links.
- Release notes explain what works, what's experimental and requirements; provide sha256 checksum and release-specific test notes.
- Publish a 60–90 s clip: show actual scene, connect nodes, instance asset, switch renderers, save/reopen.
- Ask **5–10 technically relevant testers** to attempt the quickstart without coaching. Observe their first failure and revise instructions.
- Set up issue templates and a weekly triage habit.

### Weeks 3–4: targeted public launch

Publish in a staggered sequence so feedback can be incorporated, not a simultaneous blast. Follow each community's self-promotion rules.

1. **Polycount Software Releases:** working video, accurate limitations, GitHub Releases, specific feedback request. [Category](https://polycount.com/categories/software-releases).
2. **Blender Artists / Blender community:** specifically the Blender/USD round-trip, not a generic 'new 3D tool' pitch. [Blender Artists](https://blenderartists.org/).
3. **SideFX forums / OpenUSD technical communities:** highlight stage/layer semantics and open technical questions, without presenting Sublayer as a Solaris replacement. [SideFX forum](https://www.sidefx.com/forum/).
4. **Reddit**, in suitable weekly project/share threads only where permitted (e.g. r/blender, r/vfx, r/GraphicsProgramming, r/opensource): lead with a genuine recorded demonstration and reply to feedback.
5. **Hacker News Show HN:** only once the clean install and walkthrough are reproducible; prioritise engineering details and open-source repository. [Show HN guidance](https://news.ycombinator.com/showhn.html).

Do not create false endorsements, mass-post identical copy, or contact users unsolicited.

### Month 2: prove workflows

- Publish three 2–4 min tutorials: **Blender → Sublayer → Blender**, **scatter/instancing**, **MaterialX → Aurora/RTX**.
- Create a small example collection with licence metadata.
- Submit a short factual news tip to **CG Channel** and other editorial outlets, including screenshots, release changelog and actual supported workflows; editorial coverage is not guaranteed.
- Offer a small demo to USD technical users; document failures publicly and fix the largest blocker.

### Month 3: turn interest into contributors

- Public roadmap, 'good first issue' candidates validated by maintainers, CONTRIBUTING guidance, reproducible tests.
- Invite useful bug reports and short showcases, with permission before reposting.
- Publish a changelog-driven update and a public 'what we learned' post.

## Channel assets and copy

### GitHub/social headline
**Sublayer: open-source node-based USD scene composition and DCC workflows for Windows.**

### Short launch post
> I've open-sourced Sublayer, a Windows desktop app for assembling and previewing OpenUSD scenes in a visual node graph. It includes instancing/scattering, Hydra rendering with Storm/Aurora (optional Omniverse RTX), MaterialX, and explicit Blender/Substance Painter workflows. Original files stay linked rather than being silently overwritten. It's early-stage and I'd especially value feedback from artists using USD across applications. Source, releases and quickstart: https://github.com/ruchirlives/Sublayer

### Technical community angle
> Sublayer uses Tauri/React Flow + Rust + upstream OpenUSD workers to compose USD graphs while preserving external assets. The interesting problem is round-tripping native DCC edits into sparse USD layers without trampling upstream changes. If you're experienced with USD layering and Hydra, I'd welcome an architecture review or a minimal reproducible scene that exposes a weakness.

### Direct editorial tip subject
**Open-source Windows USD scene composer: Sublayer (Blender, MaterialX, Hydra)**

> Sublayer is a newly public GPL-3.0-or-later Windows node-based OpenUSD composition and DCC workflow tool. It features native-file-preserving Blender round-trips, a Substance Painter bridge, material authoring and Storm/Aurora rendering. Optional NVIDIA RTX requires a separately installed runtime. Demo video: [ADD VERIFIED VIDEO]. Screenshots: [ADD PRESS KIT]. Download/release notes: https://github.com/ruchirlives/Sublayer/releases. Known limitations: [LINK TO RELEASE NOTES].

### 60–90 second video storyboard
0–10s: familiar problem (assets across apps) and real graph+viewport.
10–25s: import a small USD scene and manipulate a Transform node.
25–40s: scatter/instance assets, demonstrate a live viewport change.
40–60s: MaterialX/render-preview comparison; clearly identify renderers.
60–75s: demonstrate a genuinely working Blender or Painter round-trip.
75–90s: show save/reopen, GitHub release URL and limitations.

## Measurement: instrument the funnel without collecting personal telemetry

Weekly manual scorecard:
- **Awareness:** referral sources, post engagements, video views (native channel reporting).
- **Interest:** GitHub visitors/views and unique visitors (Insights → Traffic; historic data is limited).
- **Acquisition:** release asset download counts per version (GitHub API/release page), not equivalent to successful installs.
- **Activation:** opt-in testers who successfully preview and save a USD graph; report denominator and Windows/GPU configurations.
- **Quality:** install blockers, issue first-response time, confirmed bugs, resolved bugs, repeat testers.
- **Community:** meaningful external contributors, accepted PRs, reproducible issues.

**Initial targets to test, not forecasts:** 10 successful clean-install evaluations, 5 reproducible feedback reports, 3 external contributions/issues of substance, and a median first issue response within 7 days. Recalibrate after 4 weeks; avoid arbitrary download vanity goals.

## Ongoing cadence

- Monday: fix onboarding blocker and review crashes/issues.
- Wednesday: one demo clip, tutorial or meaningful technical update.
- Friday: respond to feedback and update release notes.
- Monthly: ship verified improvements and a plain changelog.

## Ownership and guardrails

Do not publish claims of production suitability unless demonstrated. Use only owned/licensed screenshots and demo assets. Do not claim proprietary components are GPL. Include system requirements and test scope. Keep GitHub the canonical download location. Do not collect email addresses or phone-home usage data just for marketing without a clear purpose, lawful basis and privacy notice.

## Context / references

- [Sublayer README](../../README.md)
- [OpenUSD products directory](https://openusd.org/release/usd_products.html) (ecosystem context)
- [Solaris](https://www.sidefx.com/products/houdini/lookdev/solaris/) (adjacent category)
- [Polycount Software Releases](https://polycount.com/categories/software-releases)
- [GitHub release links](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)
