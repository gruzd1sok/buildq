---
name: buildq
description: Shared FIFO build queue and per-worktree iOS simulators for parallel agents on this Mac. Use BEFORE any xcodebuild build/test/archive or tuist generate/build/test (and any project-specific heavy build command), and whenever you need a simulator to install and run the app. Triggers — build, xcodebuild, tuist generate, run on simulator, build queue, buildq.
---

# buildq

Several agents may work on this Mac at once, each in its own git worktree. One Xcode build already
uses every core, and parallel builds or `tuist generate` runs slow each other down and fight over
shared caches. `buildq` serializes them: one heavy command at a time, strict FIFO across all
worktrees and agents.

Each worktree gets **its own simulator clone**, so the queue holds the slot only while the command
runs. Installing, launching and checking the app on your simulator happens outside the queue, and
the next agent's build starts right after yours finishes.

A PreToolUse hook denies raw `xcodebuild` and `tuist generate|build|test|install|cache` in Bash
(plus any patterns from `~/.buildq/config.json`). Light calls pass without the queue:
`xcodebuild -list|-showBuildSettings|-showdestinations|-version`, `test-without-building`.

## Build

Run with `run_in_background: true` and wait for the completion notification — do not poll and do
not sleep. Waiting can take a while if others are building.

```bash
buildq run --label "App build: fix login crash" --log /tmp/<scratch>/build.log -- \
  xcodebuild -workspace App.xcworkspace -scheme App \
  -destination "id=$(buildq sim --no-boot)" build
```

- Status lines go to stderr: `waiting: position 2 of 3 …; running: <worktree> [branch] «label» for 4m10s`,
  then `slot acquired …`, then `finished in … with exit N; slot released`.
- With `--log` the command output goes to that file; on failure buildq prints its `error:` lines.
  Without `--log` the output passes through.
- The exit code is the command's exit code. `75` means it gave up after `--max-wait` minutes
  (default 120): tell the user who is ahead (`buildq status`) instead of retrying in a loop.
- Several heavy steps in one slot: `buildq run --label "gen+build" -- bash -c 'tuist generate --no-open && xcodebuild …'`.
- Agents without background tasks just run `buildq run …` in the foreground; it blocks until done.

## Status

```bash
buildq status            # running build, waiting positions, recent finished builds
buildq status --json
buildq cancel <pid|id>   # only your own build, or when the user asks
```

## Simulator

```bash
UDID=$(buildq sim)       # this worktree's simulator "wt-<worktree dir>", cloned from buildq-base and booted
xcrun simctl install "$UDID" <path/to/App.app>
xcrun simctl launch "$UDID" <bundle id>
buildq sim --list        # all buildq simulators
buildq sim --rm          # delete this worktree's simulator when the worktree goes away
```

- Never use `booted` or the user's own simulator — always your `$UDID`.
- Clones come from `buildq-base`, a shut-down copy of the user's prepared simulator (logged in,
  settings applied). If it is missing, ask the user to run
  `buildq sim --refresh-base --source <UDID or name>` — it briefly shuts that simulator down.

## Escape hatch

Only when the user explicitly says to build outside the queue: prefix the command with `BUILDQ_BYPASS=1`.

## When something goes wrong

- `buildq events [-n 50] [--kind finished,interrupted,…]` — the journal `~/.buildq/events.log`:
  `enqueued` (who was ahead), `acquired` (wait time, load average), `finished` (exit, durations),
  `interrupted`, `timeout`, `orphan_term|orphan_kill` (a holder died mid-build; its build keeps the
  slot until it is stopped), `reaped`, `hook_deny`, `sim_*`.
- Full output of every run: `~/.buildq/logs/<ticket>.log` (last 50 kept); `--log FILE` is a symlink to it.

Start from `buildq events` and the matching full log when the user asks what went wrong.
