import json
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

    def call(self, cmd):
        payload = json.dumps({"tool_input": {"command": cmd}, "cwd": "/tmp/wt", "session_id": "s1"})
        return self.home.run("hook", input=payload)

    def test_deny_output_and_event(self):
        r = self.call("xcodebuild -scheme App build")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("buildq run", out["permissionDecisionReason"])
        ev = [json.loads(l) for l in (self.home.path / "events.log").read_text().splitlines()]
        self.assertEqual([e["kind"] for e in ev], ["hook_deny"])
        self.assertEqual(ev[0]["session"], "s1")

    def test_allow_is_silent(self):
        r = self.call("ls -la")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_config_file_is_used(self):
        (self.home.path / "config.json").write_text(json.dumps(VKCLIENT_LIKE))
        self.assertIn("deny", self.call("make use_caches").stdout)

    def test_garbage_input_never_blocks(self):
        r = self.home.run("hook", input="not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
