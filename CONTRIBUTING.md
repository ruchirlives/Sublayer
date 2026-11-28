# Contributing to Sublayer

Thanks for helping improve Sublayer. The project is a Windows desktop app with a Rust/Tauri backend, React/TypeScript/React Flow frontend, and Python/OpenUSD workers.

## Good ways to help

- Test the [first 15 minutes](docs/QUICKSTART.md) on a clean Windows installation and report the first confusing or failing step.
- Report minimal reproducible USD/MaterialX/Hydra renderer issues. Share only sample scenes you are permitted to distribute.
- Improve tutorials, accessibility, error messages and real-world pipeline documentation.
- Review USD composition, reference sharing, instance handling, DCC round-tripping and renderer behaviour.

## Before opening a PR

1. Search existing issues and describe the user-visible problem.
2. Keep changes narrow and preserve non-destructive behaviour and original asset paths.
3. Follow [AGENTS.md](AGENTS.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for project-specific invariants.
4. Run `npm run build`, `npm test`, and relevant Rust/Python checks. Report anything you could not test.
5. Distinguish automated test coverage from a verified desktop workflow.

Do not commit credentials, licensed third-party assets, runtime DLLs or private project files. Proprietary NVIDIA runtime components must remain separately obtained in accordance with their own licences.

For vulnerabilities or exposed credentials, avoid publishing exploitable details in a public issue; contact the maintainer privately through GitHub's available private reporting or contact mechanism.
