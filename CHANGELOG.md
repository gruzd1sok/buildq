# Changelog

## [Unreleased]

- Product export verifies actual Xcode build settings before compiling and rejects redirected
  product directories instead of copying stale default outputs. Export also rejects external,
  absolute-to-base, dangling and cyclic product symlinks while preserving internal framework links.
- Reusable build bases: `run --base NAME` mirrors worktree inputs at a fixed path and reuses
  per-base DerivedData. Preparation (`--prepare`), compilation and product export all stay under
  the machine queue, including orphan recovery and cancellation of nested processes.
- Explicit ignored inputs (`--include-ignored`), independent product/dSYM export (`--products-to`,
  `--no-dsym`), `bases --json`, `base_synced` events, configurable base storage and opt-in real
  Xcode integration coverage. No shared CAS is enabled.
- BuildQBar marks base builds with their base name in the running card, queue and history;
  older history records without base information remain readable.
- Queue priorities: `major`, `normal` (default), `minor`. Waiting builds go by priority, FIFO within
  one; a running build is not preempted. `buildq run --priority`, `$BUILDQ_PRIORITY`, and
  `priority.default` / `priority.rules` (`env`, `label`, `worktree`, `branch`) in the config.
- `buildq priority <ticket|pid> <level>` changes a waiting build's priority; `status` prints ticket ids.
- BuildQBar shows a priority badge and lets you change it from a waiting row.

## [0.2.0] - 2026-10-21

- `buildq hook` now rewrites a heavy command into `buildq run … -- "$SHELL" -c '<command>'` and
  runs it in the background instead of denying it. It makes no permission decision, so the user's
  permission rules still apply. `"guard": {"mode": "deny"}` keeps the old behaviour.
- `buildq run --quiet` (output only in the log) and `--json` (one result object with parsed errors).
- `buildq errors` and `buildq log` for the latest or any past build.

## [0.1.0] - 2026-10-20

First public release.

- `buildq run`: machine-wide strict-FIFO build slot with crash-safe locking, orphan handling,
  waiting reports, `--log` with error extraction, `--max-wait` (exit 75).
- `buildq status`, `cancel`, `events`; full per-run logs, history and a rotating JSONL journal.
- `buildq sim`: per-worktree simulator clones from a prepared base (`--refresh-base --source`).
- `buildq hook`: Claude Code PreToolUse guard with configurable patterns (`~/.buildq/config.json`).
- BuildQBar menu bar app with a traffic-light icon, build progress and actions; English and Russian.
- Claude Code plugin (skill + hook + CLI on PATH), Homebrew formula, `make install`.
