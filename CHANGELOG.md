# Changelog

## [Unreleased]

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
