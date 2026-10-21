import json
import subprocess
import unittest

from helpers import Home, load_cli

cli = load_cli()
VKCLIENT_LIKE = {"guard": {"patterns": [r"^make\s+(tuist_gen|use_caches\w*)\b", r"use_caches\.sh\b"]}}


class CheckCommand(unittest.TestCase):
    def hit(self, cmd, config=None):
        return cli.check_command(cmd, config or {})

    def test_heavy_builds_are_caught(self):
        for cmd in ["xcodebuild -scheme App build",
                    "xcodebuild test -scheme App -destination id=X",
                    "cd wt && xcodebuild build",
                    "FOO=1 env xcrun /usr/bin/xcodebuild build",
                    "time caffeinate xcodebuild archive",
                    "echo start; xcodebuild build | xcbeautify",
                    "(xcodebuild build)",
                    "out=$(xcodebuild build)",
                    "tuist generate --no-open",
                    "tuist build App",
                    "tuist test", "tuist install", "tuist cache"]:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.hit(cmd))

    def test_light_commands_pass(self):
        for cmd in ["xcodebuild -list", "xcodebuild -version", "xcodebuild -showsdks",
                    "xcodebuild -showBuildSettings -scheme App", "xcodebuild -showdestinations -scheme App",
                    "xcodebuild -resolvePackageDependencies", "xcodebuild test-without-building -xctestrun x",
                    "tuist edit", "tuist version", "git log --grep xcodebuild", "echo xcodebuild",
                    "make lint", "grep -r 'tuist generate' docs"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.hit(cmd))

    def test_queued_and_bypassed_commands_pass(self):
        self.assertIsNone(self.hit("buildq run --label x -- xcodebuild build"))
        self.assertIsNone(self.hit("BUILDQ_BYPASS=1 xcodebuild build"))

    def test_config_patterns(self):
        self.assertIsNone(self.hit("make tuist_gen"))
        self.assertEqual(self.hit("make tuist_gen", VKCLIENT_LIKE), "make tuist_gen")
        self.assertIsNotNone(self.hit("cd x && ./scripts/use_caches.sh -f", VKCLIENT_LIKE))
        self.assertIsNone(self.hit("make lint", VKCLIENT_LIKE))

    def test_builtin_rules_can_be_disabled(self):
        self.assertIsNone(self.hit("xcodebuild build", {"guard": {"builtin": False}}))

    def test_bad_pattern_does_not_crash(self):
        self.assertIsNotNone(self.hit("xcodebuild build", {"guard": {"patterns": ["("]}}))


class HookProcess(unittest.TestCase):
    def setUp(self):
        self.home = Home()

    def tearDown(self):
        self.home.cleanup()

    def call(self, cmd, **extra):
        payload = json.dumps({"tool_input": {"command": cmd, "description": "d", **extra}, "cwd": "/tmp/wt",
                              "session_id": "s1"})
        return self.home.run("hook", input=payload)

    def events(self):
        return [json.loads(l) for l in (self.home.path / "events.log").read_text().splitlines()]

    def test_rewrite_keeps_other_fields_and_goes_to_background(self):
        r = self.call("xcodebuild -scheme App build", timeout=1000)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", out)
        new = out["updatedInput"]
        self.assertEqual((new["description"], new["timeout"], new["run_in_background"]), ("d", 1000, True))
        self.assertIn(" run --quiet --label xcodebuild -- ", new["command"])
        self.assertIn("buildq", out["additionalContext"])
        self.assertIsNone(cli.check_command(new["command"], {}))
        ev = self.events()
        self.assertEqual([e["kind"] for e in ev], ["hook_rewrite"])
        self.assertEqual(ev[0]["session"], "s1")

    def test_rewritten_command_runs_in_the_queue_with_shell_semantics(self):
        (self.home.path / "config.json").write_text(json.dumps({"guard": {"patterns": [r"^echo heavy"]}}))
        cmd = "X='a b'; echo heavy \"$X\" | tr a-z A-Z"
        new = json.loads(self.call(cmd).stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertNotIn("--quiet", new)
        r = subprocess.run(new, shell=True, env=self.home.env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "HEAVY A B\n"))
        history = (self.home.path / "history.log").read_text().splitlines()
        self.assertEqual(json.loads(history[0])["label"], "echo heavy \"$X\"")

    def test_deny_mode(self):
        (self.home.path / "config.json").write_text(json.dumps({"guard": {"mode": "deny"}}))
        out = json.loads(self.call("xcodebuild -scheme App build").stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("buildq run", out["permissionDecisionReason"])
        self.assertEqual([e["kind"] for e in self.events()], ["hook_deny"])

    def test_allow_is_silent(self):
        r = self.call("ls -la")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_config_file_is_used(self):
        (self.home.path / "config.json").write_text(json.dumps(VKCLIENT_LIKE))
        self.assertIn("updatedInput", self.call("make use_caches").stdout)

    def test_garbage_input_never_blocks(self):
        r = self.home.run("hook", input="not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
