# Reusable build bases

A build base is a persistent checkout and DerivedData at a fixed path. `buildq run --base debug`
mirrors the calling worktree into that checkout and builds there. Every worktree using the same
git common directory and base name shares it. Separate repositories and separate base names do
not share build data.

This borrows the fixed-path approach described by [xcslot](https://github.com/ertembiyik/xcslot).
It keeps buildq's existing priorities, queue, simulator ownership and process cleanup. Only one
heavy command runs on the Mac, including synchronization, generation and symbol export.

## Build

```bash
buildq run --base debug --quiet -- \
  xcodebuild -project App.xcodeproj -scheme App -destination 'platform=macOS' build
```

Direct `xcodebuild` and `xcrun xcodebuild` commands automatically receive the base's
`-derivedDataPath`. Absolute worktree paths in their arguments are redirected into the base.
Do not pass your own `-derivedDataPath`; use `--products-to` for persistent products instead.
Shell strings are never rewritten. For a custom script, explicitly use the environment:

```bash
buildq run --base debug -- bash -c '
  xcodebuild -workspace App.xcworkspace -scheme App \
    -derivedDataPath "$BUILDQ_DERIVED_DATA" build
'
```

The wrapped command runs in the base's equivalent of the caller's current directory. Compiler
diagnostics naming existing worktree files are mapped back to the worktree; generated files keep
their actual base paths. Files with whitespace in their relative paths may retain base paths.
Normal `--quiet`, `--json`, `--label`, `--priority`, `--log` and timeout options still work. A JSON
result includes `base` and `products` (null unless a successful run requested an export).

## Preparing a generated project

```bash
buildq run --base debug \
  --prepare 'CI/tuist install --path .' \
  --prepare 'make tuist_fast' \
  -- xcodebuild -workspace VKClient.xcworkspace -scheme VKClient build
```

Preparation is explicit: buildq never guesses the Tuist executable, wrapper, dependency policy
or generation flags. Each `--prepare` runs through `/bin/sh` from the base's repository root
before every build. If a step fails, later steps and compilation do not run. All steps remain
inside the queue. Do not call `buildq run` from a preparation script: it would wait on itself;
buildq rejects a nested invocation.

The first run must prepare dependencies in the base, even if they were resolved in the calling
worktree. Ignored build folders, including `Tuist/.build`, persist in the base and are not copied
from the caller. Generated `.xcodeproj`, `.xcworkspace` and `Derived` files whose contents remain
identical get their previous timestamps back after each preparation step. Other generated
locations are not covered by this timestamp restoration.

## Inputs and isolation

Tracked and non-ignored untracked files are mirrored, including uncommitted changes. A tracked
file deleted in the caller is removed from the base. Files are compared by contents and mode;
unchanged files are not rewritten. Symlinks within the repository are supported, and absolute
internal links are redirected into the base. External links and submodules fail explicitly.

Ignored inputs require an explicit, repeatable git pathspec:

```bash
buildq run --base debug --include-ignored '*.xcconfig' -- xcodebuild -scheme App build
```

Use narrow patterns for local inputs; do not include whole build/dependency directories. Git LFS
files must already be materialized in the calling worktree. Buildq creates its detached base
checkout without checkout or LFS smudging, then copies the actual calling worktree files.
Version scripts see the calling worktree's commit, with a detached HEAD in the base.

Source changes made by generation, tests or linters remain in the base. There is no automatic
write-back of snapshots, lockfiles or fixes into the caller. The next run restores mirrored
inputs from its caller. Generated files and dependency caches not mirrored from the caller stay
in the base; project preparation is responsible for keeping them current across branches.

Do not edit the caller while its inputs are being synchronized. Detected content changes during
copying fail the run; this is not a transactional snapshot of simultaneous edits across files.
After synchronization, editing the caller does not change the running build.

## Products and debugging

```bash
buildq run --base debug --products-to /tmp/app-build-123 -- \
  xcodebuild -workspace App.xcworkspace -scheme App \
  -destination "id=$(buildq sim --no-boot)" build
```

The output directory must not exist. Products are copied to a temporary sibling directory and
published only after a successful build and completed export. A later run refuses to overwrite
that directory. Without export, products in the base are valid only until its next build starts.

For direct `xcodebuild`/`xcrun xcodebuild` invocations, export first queries the actual
`BUILT_PRODUCTS_DIR` and `TARGET_BUILD_DIR` with `-showBuildSettings -json`, after preparation and
inside the queue. If a command-line setting, project or xcconfig redirects any target's products
outside the base's `DerivedData/Build/Products`, the run fails before compilation. Remove the
override to use `--products-to`, or omit `--products-to` and manage custom outputs explicitly.
An unreadable or invalid settings response also fails the export instead of copying old products.
The settings query does not receive `-resultBundlePath`, `-resultStreamPath` or `-archivePath`,
so those output paths remain available to the actual build.

Only symlinks whose targets exist within the copied product tree are accepted. External,
absolute links back to the base, dangling links and symlink cycles fail before publication;
the destination is not created. Relative links inside a framework, such as `Versions/Current`,
are preserved without changing the bundle's signature. The same validation runs after dSYM
creation, so added symbol files cannot introduce a dependency on the base either.

By default Mach-O products receive standalone dSYMs under `BuildQSymbols`, with a source-path
mapping to the original worktree. Debug objects are linked while the queue is still held, before
another build can replace them. The original app/framework bundles are not modified, preserving
their signatures. Load the exported dSYMs into the debugger when debugging an exported product.
`--no-dsym` skips this step for install-only builds. Existing generated dSYMs are also copied.

Product export currently uses a regular copy, not APFS clones. Only
`DerivedData/Build/Products` is exported; archives and `.xcresult` bundles are not handed back
automatically. Pass absolute paths outside the source tree for results that must persist.
For custom commands, set the product location through `BUILDQ_DERIVED_DATA`.
Buildq cannot inspect the build settings of arbitrary shell scripts; those commands must write
the products to `BUILDQ_DERIVED_DATA/Build/Products` themselves. The product-tree and symlink
validation still applies to their exports.

After export, installing and checking the application happens outside the queue on the calling
worktree's simulator. Simulator ownership is unchanged.

## Storage and caches

```text
~/.buildq/bases/<repository-id>/<name>/
  src/          detached checkout at a permanent path
  DerivedData/  reusable build database, products and Xcode caches
  cache/        persistent directory available to custom commands
  state.json    mirrored file manifest and last worktree/commit
```

Set `BUILDQ_BASE_ROOT=/Volumes/YourSSD/buildq-bases` or `bases.root` in `~/.buildq/config.json`
to place bases on an SSD. The environment variable wins. Choose the location before warming a
base: moving it changes absolute paths and requires a cold build. `BUILDQ_HOME` still controls
the shared queue and logs independently of base storage.

The command and preparation steps receive:

| Variable | Value |
|---|---|
| `BUILDQ_BASE_DIR` | Permanent base directory |
| `BUILDQ_WORKTREE` | Calling worktree root |
| `BUILDQ_DERIVED_DATA` | Base's DerivedData |
| `BUILDQ_BASE_CACHE` | Base's cache directory for custom commands |

The persistent source path also preserves generator/dependency caches inside `src`. No global
compilation cache, prefix mapping or build-setting override is enabled. `cache/` is available
to custom scripts; buildq does not automatically redirect third-party caches into it.

Use `buildq bases` or `buildq bases --json` to inspect paths. Bases are not automatically pruned.
To remove one, wait until no build uses it, remove its `src` through `git worktree remove --force`,
then remove its remaining DerivedData/cache/state. The first subsequent build starts cold.

## Expected performance

The first build of a base is cold. A new worktree with the same inputs can reuse its completed
tasks because source paths and unchanged timestamps remain stable. Real edits and dependency
changes still recompile affected code. Switching between distant branches can discard much of
the warm state. Use separate base names for toolchains and incompatible build settings.

The opt-in integration test uses a tiny actual Xcode project and checks compile counts of
`1` for the initial build, `0` for a fresh worktree and `1` after a source edit. It also checks that
an exported binary and dSYM source mapping survive reuse of the base. This verifies the mechanism;
it is not a benchmark of VKClient or a claim about its speedup.
The test also verifies that result-bundle creation works with the settings query and that
custom product directories configured through command-line settings or the project are rejected
before compilation.
