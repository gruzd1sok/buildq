# Working on buildq

This repository implements the queue itself. For using it in another project, read
[README.md](README.md) and [the agent skill](skills/buildq/SKILL.md).
`CLAUDE.md` is a relative symlink to this file; keep one source of guidance.

## Map

- `bin/buildq`: executable Python CLI, Python 3.9+, standard library only. Queue, hooks,
  simulators, reusable build bases and product export live here. No Python packaging step.
- `app/Sources/BuildQBar.swift`: macOS 13+ SwiftUI/AppKit menu bar app, built directly with
  `swiftc` by `Makefile`. Reads queue/history/log files; actions invoke the CLI or signal a job.
- `app/*.xcstrings`: English/Russian localization. Every `L(...)` key needs a catalog entry.
- `tests/`: Python `unittest`. `helpers.Home` isolates state in a temporary `BUILDQ_HOME`;
  base tests create temporary git repositories. Queue tests exercise real processes and locks.
- `docs/build-bases.md`: detailed synchronization, preparation, export and debugging contracts.
- `skills/buildq/SKILL.md`, `hooks/hooks.json`, `.claude-plugin/`: agent-facing distribution.
- `Formula/buildq.rb`, `.github/workflows/ci.yml`: Homebrew packaging and macOS CI.

## Contracts

- One heavy command at a time across the machine. Priority is `major > normal > minor`, FIFO
  within a priority; never preempt a running build. Ticket allocation and execution use separate locks.
- Descendants must retain the running lock descriptor. Cancellation/orphan recovery must not
  release the queue while compilation still runs. Cover normal signals and a SIGKILLed wrapper
  with a stubborn descendant; mocks alone do not verify this contract.
- Synchronization, preparation, settings queries, compilation and export all stay inside the
  queue. Do not nest `buildq run` inside its worker/preparation command: it would wait on itself.
- Keep exit codes and output compatible. Status goes to stderr; `run --json` emits one result
  object on stdout. Exit 75 means queue wait timeout. Queue/history JSON is also an interface to
  BuildQBar: add optional metadata compatibly with old records, updating producer and consumer.
- Call the feature a **build base** / **сборочная база**. A base badge identifies the mode;
  it does not prove a cache hit. Bases belong to a git common directory and a base name.
- Source and DerivedData paths stay fixed. Preserve unchanged file timestamps; changed content
  must get a changed timestamp. Mirror local edits and deletions; ignored inputs require explicit
  pathspecs. Never copy build-time source changes back into the caller's worktree.
- Preparation is explicit (`--prepare`), not a guessed Tuist command. It runs from the base's
  repository root; the build uses the caller's corresponding subdirectory. No shared CAS or
  project build-setting overrides are enabled implicitly.
- `--products-to` publishes into a new directory after successful export. For direct Xcode,
  verify actual `BUILT_PRODUCTS_DIR` and `TARGET_BUILD_DIR` before compiling; reject external
  overrides rather than exporting stale defaults. Custom scripts own their output location contract.
- Exported products must outlive base reuse. Reject escaping/dangling/cyclic links; preserve
  internal relative framework links. Keep dSYMs outside signed bundles, generate them while holding
  the queue, and validate the final product tree before publishing. See the detailed guide.
- App installation/UI checks happen outside the queue on the caller worktree's simulator.
  Never use the user's main simulator or an ambiguous `booted` destination for automated checks.

## Verification

Run relevant checks for a change; use the full suite for queue/base behavior:

```sh
/usr/bin/python3 -m unittest discover -s tests -v
bin/buildq run --label 'BuildQBar build' -- make app
```

CI uses macOS system Python; preserve Python 3.9 compatibility. Process tests need `ps`, signals
and process groups: sandbox denials are not evidence of queue regressions. For GUI changes,
compile the app and check localization, both themes and decoding records without new fields.

The real Xcode/LLDB test is opt-in and runs inside the machine queue:

```sh
bin/buildq run --label 'build bases integration' -- env BUILDQ_TEST_XCODE=1 PYTHONPATH=tests \
  /usr/bin/python3 -m unittest test_bases.Bases.test_real_xcodebuild_reuses_dd_and_exports_independent_symbols -v
```

It checks cold/new-worktree/edited compile counts, stable symbols, result bundles and product
directory rejection on a tiny fixture. It is not a performance benchmark of a large client app.

`make install-cli` changes the installed CLI; `make install-app` builds/installs the app and
restarts its LaunchAgent. Use them when installation is part of the task. Keep experiments in
temporary state directories; never delete/repoint the user's live state, caches or simulators
as a test fixture. `BUILDQ_BASE_ROOT` controls base storage independently of `BUILDQ_HOME`.

## Documentation and releases

Keep English/Russian READMEs aligned. Update the distributed skill when agent behavior changes:
Claude Code hook/background options do not apply automatically to Codex. Add unreleased work
under `Unreleased` in `CHANGELOG.md`. Only bump a release deliberately: consistency spans
`bin/buildq`, `.claude-plugin/plugin.json`, the changelog and the Homebrew formula URL/checksum.

Preserve unrelated working-tree changes. Report actual checks and distinguish local results
from CI. Do not commit generated builds, logs, runtime state, credentials or machine-specific paths.
