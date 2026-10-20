# buildq

**One Xcode build at a time across every agent and worktree on your Mac.**

[![CI](https://github.com/gruzd1sok/buildq/actions/workflows/ci.yml/badge.svg)](https://github.com/gruzd1sok/buildq/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
· [Русская версия](README.ru.md)

You run several AI agents in parallel (Claude Code, Codex, OpenCode, Cursor…), each in its own git
worktree of the same iOS project, and every one of them wants to build. A single `xcodebuild` of a
big app already uses every core. Two builds in parallel are barely faster than the same two one
after the other, and parallel `tuist generate` runs break each other's caches. Agents hit
timeouts, builds fail for no visible reason, and nobody can tell who is building what.

`buildq` is a small machine-wide FIFO queue for heavy commands:

```bash
buildq run --label "login fix" -- xcodebuild -scheme App build
```

The command waits for its turn, runs alone, releases the slot and exits with the build's own exit
code. Any number of agents, terminals and scripts can call it at once.

## Contents

- [Features](#features)
- [Install](#install)
- [Quick start](#quick-start)
- [Examples](#examples)
- [Menu bar app](#menu-bar-app)
- [Setting up agents](#setting-up-agents)
- [Configuration](#configuration)
- [Command reference](#command-reference)
- [How it works](#how-it-works)
- [FAQ](#faq)
- [Development](#development)

## Features

- **Strict FIFO, no races.** Each ticket gets a sequence number under a file lock, and the build
  slot itself is an `fcntl` lock. Two agents that enqueue in the same millisecond still get a fixed
  order and never overlap.
- **Crash-safe.** If an agent dies, the kernel releases its slot. If an agent is SIGKILLed in the
  middle of a build, its orphaned build keeps the slot until buildq stops it (SIGTERM, then
  SIGKILL), so even then two builds never run at once.
- **Agent-friendly output.** While a command waits, buildq reports its queue position. When the
  command finishes, buildq returns its exit code, and on failure prints only the `error:` lines,
  not 100 MB of build log.
- **One simulator per worktree.** `buildq sim` clones a prepared base simulator (already logged
  in, settings applied) for each worktree. The slot is held only while building. Installing,
  launching and UI checks happen outside the queue, in parallel.
- **Guard hook for Claude Code.** A raw `xcodebuild` or `tuist generate` is denied. The agent is
  told to re-run it through the queue.
- **Menu bar app.** A hammer icon works as a traffic light. Its popover shows the running build
  with progress and ETA, the waiting list and recent results. English and Russian.
- **Post-mortems.** buildq keeps a full log of every run and a JSONL journal of every queue
  decision: who waited for whom, how long, and what the load was.
- **Tiny.** The CLI is one Python file using only the standard library. The app is one SwiftUI
  file.

## Install

Requirements:
- macOS 13 or later;
- `python3` 3.9 or later (the one that comes with Xcode Command Line Tools is enough);
- Xcode 15 or later, only to build the menu bar app.

### Claude Code plugin

```
/plugin marketplace add gruzd1sok/buildq
/plugin install buildq@buildq
```

The plugin adds the `buildq` skill and the guard hook, and puts `buildq` on the PATH of Claude's
Bash tool. You still need Homebrew or a source install for the menu bar app, for other agents and
for your own terminal.

### Homebrew

```bash
brew tap gruzd1sok/buildq https://github.com/gruzd1sok/buildq
brew install gruzd1sok/buildq/buildq
brew services start buildq          # start the menu bar app now and at login
```

### From source

```bash
git clone https://github.com/gruzd1sok/buildq && cd buildq
make install          # CLI -> ~/.local/bin/buildq, app -> ~/Applications/BuildQBar.app + LaunchAgent
make install-cli      # only the CLI
make uninstall        # remove both (state in ~/.buildq is kept)
```

Use `PREFIX=/usr/local make install` to put the CLI somewhere else and `APP_DIR=/Applications` to
move the app.

## Quick start

```bash
# 1. Once: snapshot the simulator you have prepared (logged in, etc.) as the base for clones
buildq sim --refresh-base --source "iPhone 16 Pro"

# 2. In any worktree: build through the queue on this worktree's own simulator
buildq run --label "first build" --log /tmp/build.log -- \
  xcodebuild -workspace App.xcworkspace -scheme App \
  -destination "id=$(buildq sim --no-boot)" build

# 3. See what is going on
buildq status
```

## Examples

### Build an app

```bash
buildq run --label "App: login fix" --log /tmp/app-build.log -- \
  xcodebuild -workspace App.xcworkspace -scheme App \
  -destination "id=$(buildq sim --no-boot)" \
  -derivedDataPath ./DerivedData build
```

`--label` is what other agents and the menu bar see. With `--log`, the build output goes to that
file (a symlink to buildq's own copy). Your terminal stays clean, and only errors are printed on
failure.

### What the caller sees

Another build holds the slot, so this one waits:

```
[buildq] waiting: position 2 of 3 (waited 0s); running: app-feature-x [feature/x] «unit tests» for 3m12s
[buildq] waiting: position 2 of 3 (waited 1m00s); running: app-feature-x [feature/x] «unit tests» for 4m12s
[buildq] waiting: position 1 of 2 (waited 1m48s); running: app-onboarding [onboarding] «UI tests» for 0s
[buildq] slot acquired after 6m31s: App: login fix (log: /tmp/app-build.log)
[buildq] finished in 5m02s with exit 65; slot released
[buildq] errors from /Users/me/.buildq/logs/00000042-23785.log:
LoginViewModel.swift:48:17: error: cannot find 'session' in scope
** BUILD FAILED **
```

The exit code is 65, the same as `xcodebuild`'s, so scripts and agents can react to it as usual.

### Unit tests

```bash
buildq run --label "unit tests" --log /tmp/tests.log -- \
  xcodebuild test -workspace App.xcworkspace -scheme AppTests \
  -destination "id=$(buildq sim --no-boot)" -resultBundlePath /tmp/tests.xcresult
```

To build once and then run the tests several times, keep only the build in the queue:

```bash
buildq run --label "build-for-testing" -- xcodebuild build-for-testing -scheme AppTests \
  -destination "id=$(buildq sim --no-boot)" -derivedDataPath ./DD
xcodebuild test-without-building -scheme AppTests \
  -destination "id=$(buildq sim)" -derivedDataPath ./DD   # outside the queue, on your own simulator
```

### Several heavy steps in one slot

Project generation and the build that follows should not let another agent in between:

```bash
buildq run --label "generate + build" --log /tmp/build.log -- bash -c '
  tuist install && tuist generate --no-open &&
  xcodebuild -workspace App.xcworkspace -scheme App -destination "id=$(buildq sim --no-boot)" build
'
```

### Build, install and launch on the worktree's simulator

```bash
UDID=$(buildq sim)          # creates "wt-<worktree dir>" from buildq-base on first use, boots it

buildq run --label "run on sim" --log /tmp/build.log -- \
  xcodebuild -scheme App -destination "id=$UDID" -derivedDataPath ./DD build

# the slot is already free here, the next agent is building while you test
xcrun simctl install "$UDID" ./DD/Build/Products/Debug-iphonesimulator/App.app
xcrun simctl launch "$UDID" com.example.app
xcrun simctl io "$UDID" screenshot /tmp/after.png
```

Simulator management:

```bash
buildq sim --list                  # buildq-base and all wt-* clones with their state
buildq sim --name wt-experiment    # an extra clone with a custom name
buildq sim --rm                    # delete this worktree's clone (do it when removing the worktree)
buildq sim --refresh-base          # re-snapshot the base after you changed the source simulator
```

### Check the queue

```
$ buildq status
RUNNING  app-feature-x [feature/x] «unit tests»  for 4m12s  pid 23600  log /tmp/tests.log
#2       app [main] «App: login fix»  waiting 1m00s  pid 23785
#3       app-onboarding [onboarding] «UI tests»  waiting 12s  pid 24011

recent:
  2026-10-20 16:02:11  exit 0   ran   6m40s  waited      0s  app-onboarding  «generate + build»
  2026-10-20 16:09:37  exit 65  ran   1m02s  waited   6m31s  app  «App: login fix»
```

`buildq status --json` prints the raw tickets for scripts.

### Cancel a build

```bash
buildq cancel 23785              # by pid of the waiting/running buildq
buildq cancel 00000042-23785     # or by ticket id
```

The cancelled `buildq run` stops its build, frees the slot and exits with 143, printing why, so an
agent understands what happened. The menu bar app has the same action behind a confirmation.

### Find out what went wrong

```
$ buildq events -n 6
2026-10-20 16:03:05  enqueued           pid 23785  app «App: login fix»  {"ticket": "00000042-23785", "branch": "main", "ahead": ["app-feature-x [feature/x] «unit tests»"]}
2026-10-20 16:09:36  acquired           pid 23785  app «App: login fix»  {"waited": 391, "load": 14.2, ...}
2026-10-20 16:10:38  finished           pid 23785  app «App: login fix»  {"exit": 65, "waited": 391, "ran": 62, "full_log": "..."}
2026-10-20 16:11:02  hook_deny          pid 24410  app-onboarding  {"hit": "xcodebuild", "cmd": "xcodebuild -scheme App build", "session": "..."}
2026-10-20 16:20:40  orphan_term        pid 24518  app-feature-x «unit tests»  {"holder_pid": 23600, "pgid": 23611}
2026-10-20 16:21:00  orphan_kill        pid 24518  app-feature-x «unit tests»  {"pgid": 23611}

$ buildq events --kind timeout,interrupted,orphan_term --json | jq .
```

Every run's complete output is kept in `~/.buildq/logs/` (last 50 runs).

### Give up instead of waiting forever

```bash
buildq run --max-wait 15 --label "quick check" -- xcodebuild -scheme App build
echo $?    # 75 if the slot was not free within 15 minutes
```

`--max-wait 0` waits indefinitely. The default is 120 minutes.

### Use it from Makefiles and scripts

```make
build:
	buildq run --label "make build" -- xcodebuild -scheme App build

generate:
	buildq run --label "tuist generate" -- tuist generate --no-open
```

buildq is not tied to Xcode or agents. Anything heavy that should not run in parallel on one
machine can go through the queue: `swift build`, `fastlane`, `bazel`, Android Gradle builds.

### What the Claude Code hook does

An agent that runs a raw `xcodebuild -scheme App build` gets this instead:

```
`xcodebuild` must go through the shared build queue: other agents build in parallel and concurrent
builds slow down or break each other. Re-run it as `buildq run --label '<what>' --log <file> --
<same command>` with run_in_background=true and wait for the completion notification;
`buildq status` shows the queue. For the app use this worktree's own simulator: `buildq sim`
prints its UDID.
```

Light commands pass untouched:
- `xcodebuild -list`, `-showBuildSettings`, `-showdestinations`, `-version`, `-showsdks`;
- `xcodebuild test-without-building`;
- `tuist edit`;
- anything that already contains `buildq run`.

When you explicitly want a build outside the queue, prefix it with `BUILDQ_BYPASS=1`.

## Menu bar app

The hammer icon in the menu bar is a traffic light:

| Hammer | Meaning |
|---|---|
| grey outline | queue is empty |
| green, filled | building, no errors, nobody waiting |
| green outline | the last build succeeded (shown for 5 minutes) |
| yellow, filled | building and others wait, or it takes 1.5× longer than usual, or its agent died |
| red, filled | the current build already has compile errors |
| red outline | the last build failed (shown for 15 minutes) |

Hover the icon to see the reason; the popover header shows it too. Click the icon to open the
popover, which shows:

- **Now building:** worktree, label, branch, elapsed time and a progress bar against the
  worktree's usual build time. Below it, the target being compiled, the number of compiled files
  and the error count.
- **Waiting:** queue position, waiting time and the usual build time for each entry.
- **Recent:** the last six results, with exit code, wait time and build time in the tooltip.
- **Actions:**
  - follow the log in Terminal;
  - show only errors;
  - reveal the log in Finder;
  - stop a build or remove it from the queue;
  - open the event journal.

The app reads the files in `~/.buildq` and never touches the queue itself. It follows a growing
build log incrementally, so it stays responsive with logs over 100 MB. The interface follows the
system language (English or Russian).

## Setting up agents

### Claude Code

The plugin sets everything up. Without the plugin, add the hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "buildq hook", "timeout": 5 }]
      }
    ]
  }
}
```

Then copy [`skills/buildq/SKILL.md`](skills/buildq/SKILL.md) to `~/.claude/skills/buildq/SKILL.md`.

Claude Code can run `buildq run …` with `run_in_background: true`, so the agent is notified when the
build finishes instead of hitting the Bash timeout while it waits in the queue. The skill tells it
to do so.

### Codex, OpenCode, Cursor and others

Put `buildq` on `PATH` and add a rule to the project's `AGENTS.md` (or the agent's equivalent):

```markdown
## Building

Several agents build this project in parallel on one machine. Never run `xcodebuild build/test`
or `tuist generate` directly. Always use the shared queue:

    buildq run --label "<what you build>" --log <file> -- <command>

It blocks until your build is done and returns the command's exit code (75 = waited too long,
check `buildq status`). Use your worktree's own simulator: `UDID=$(buildq sim)`; never `booted`.
```

## Configuration

### Project-specific heavy commands

By default the hook blocks:
- `xcodebuild` (except the light calls listed above);
- `tuist generate|build|test|install|cache`.

You can add your project's wrappers in `~/.buildq/config.json`:

```json
{
  "guard": {
    "patterns": [
      "^make\\s+(generate|project|build_app)\\b",
      "^fastlane\\s+\\S+\\s+(build|test)\\b",
      "bootstrap\\.sh\\b"
    ],
    "builtin": true
  }
}
```

- Patterns are Python regular expressions. They are matched against every segment of the shell
  command (split on `&&`, `||`, `;`, `|`, subshells), after `VAR=value` assignments and wrappers
  such as `env`, `time`, `xcrun`, `caffeinate` and `nohup` are stripped, and with the tool's
  basename (so `./scripts/bootstrap.sh` becomes `bootstrap.sh`).
- `"builtin": false` disables the default `xcodebuild`/`tuist` rules.

See [`examples/config.json`](examples/config.json).

### Environment variables

| Variable | Meaning |
|---|---|
| `BUILDQ_HOME` | state directory (default `~/.buildq`) |
| `BUILDQ_BYPASS=1` | let this command through the guard hook |
| `BUILDQ_SIM_SOURCE` | source simulator UDID for `--refresh-base` (overrides the saved one) |
| `BUILDQ_ORPHAN_GRACE` | seconds between SIGTERM and SIGKILL of an orphaned build (default 20) |
| `BUILDQ_CLI` | path to `buildq` used by the menu bar app (set by the installers) |

## Command reference

| Command | What it does |
|---|---|
| `buildq run [--label L] [--log FILE] [--max-wait MIN] [--report-every SEC] -- CMD…` | wait for the slot, run `CMD`, release the slot |
| `buildq status [--json] [--history N]` | running build, waiting tickets, last N results |
| `buildq cancel PID\|TICKET` | stop a waiting or running build (SIGTERM to its `buildq run`) |
| `buildq events [-n N] [--kind K1,K2] [--json]` | the event journal |
| `buildq sim [--name N] [--no-boot]` | print (create and boot) this worktree's simulator UDID |
| `buildq sim --list` / `--rm` | list buildq simulators / delete this worktree's one |
| `buildq sim --refresh-base [--source UDID\|NAME]` | re-clone `buildq-base` from the source simulator |
| `buildq hook` | Claude Code PreToolUse hook (reads the tool call JSON on stdin) |

Exit codes of `buildq run`:

| Code | Meaning |
|---|---|
| the command's own | the command ran |
| 75 | gave up after `--max-wait` minutes in the queue |
| 128+N | `buildq run` was stopped by signal N (143 after `buildq cancel`) |

Event kinds in the journal:

| Kind | Logged when |
|---|---|
| `enqueued` | a ticket joins the queue (with who is ahead) |
| `acquired` | it gets the slot (with wait time and load) |
| `finished` | it finishes (with exit code and durations) |
| `interrupted` | it is cancelled or receives a signal |
| `timeout` | it gives up after `--max-wait` |
| `orphan_term`, `orphan_kill` | its holder died mid-build and the orphaned build is stopped |
| `reaped` | a dead ticket is removed |
| `hook_deny` | the hook blocks a command (with session id and cwd) |
| `sim_created`, `sim_deleted`, `sim_no_base`, `sim_base_refreshed` | simulator lifecycle |

## How it works

```
~/.buildq/
  seq             ticket counter, incremented under flock
  queue/*.json    live tickets: pid + its start time, worktree, branch, label, state
  run.lock        the build slot (fcntl lock, inherited by the build process)
  logs/*.log      complete output of the last 50 runs
  history.log     one JSON line per finished run
  events.log      the journal (rotated at 5 MB)
  config.json     optional guard configuration
  sim_source      UDID of the simulator that buildq-base is cloned from
```

1. `buildq run` takes the next sequence number under a lock and publishes its ticket atomically,
   so queue order is exactly arrival order.
2. Every 2 seconds it reaps dead tickets and checks its position. Only the first ticket tries the
   slot lock. A ticket counts as dead when its process is gone, checked by pid plus process start
   time, so a recycled pid does not keep a dead agent's place.
3. The build runs in its own process group and inherits the locked file descriptor. If the
   `buildq run` wrapper is SIGKILLed, the slot stays locked until the orphaned build is gone. The
   next waiter sends it SIGTERM, and SIGKILL after `BUILDQ_ORPHAN_GRACE` seconds.
4. When the build exits, buildq releases the slot, appends to history and the journal, and exits
   with the build's code.

## FAQ

**Why not just run builds in parallel?** On a large project, two builds at once finish at about
the same time as two builds in a row, but each one takes twice as long. Agents then hit their
timeouts, and tools with shared caches such as Tuist or SPM resolution can corrupt each other's
state. With a queue each build finishes as fast as possible, and you can see in real time who is
next.

**Does every worktree need its own DerivedData?** Yes, use `-derivedDataPath` per worktree. The
queue only decides *when* builds run; separate DerivedData keeps incremental builds incremental.

**Why clone simulators?** If every agent uses the `booted` simulator, they install over each
other's apps. A clone per worktree lets an agent test its build while the next agent's build is
already running, and cloning from a prepared base saves logging in again and again.

**Does it work on Linux?** `sim` and the menu bar app are macOS-only. The queue itself (`run`,
`status`, `cancel`, `events`, `hook`) uses only POSIX features, but it is tested on macOS only.

**What happens if my Mac reboots mid-build?** Locks die with their processes. Stale tickets are
reaped by the next `buildq` call.

## Development

```bash
make test       # unit, concurrency and localization tests (Python stdlib only)
make app        # build/BuildQBar.app
make install    # install your working copy
```

The concurrency tests start real processes:
- ten simultaneous enqueues;
- a SIGKILLed wrapper whose build ignores SIGTERM;
- timeouts;
- cancellation.

UI strings live in `app/Localizable.xcstrings`. `make test` fails if a string used in the Swift
source is missing from the catalog, lacks a translation, or has mismatched format specifiers. To
add a language, add its translations to the catalog and its code to `CFBundleLocalizations` in
`app/Info.plist`.

Issues and pull requests are welcome.

## License

[MIT](LICENSE)
