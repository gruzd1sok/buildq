"""Build bases preserve real filesystem incrementality across worktrees, under the existing queue."""
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
import unittest

from helpers import Home, ROOT


class Bases(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.repo = self.home.path / "repo with spaces"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "Source.txt").write_text("one\n")
        (self.repo / ".gitignore").write_text("ignored/\n*.xcconfig\nApp.xcodeproj/\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.other = self.home.path / "other worktree"
        self.git("worktree", "add", "-q", "--detach", str(self.other))

    def tearDown(self):
        self.home.cleanup()

    def git(self, *args, cwd=None):
        env = {**self.home.env, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def run_base(self, *command, cwd=None, options=(), want=0):
        result = self.home.run("run", "--base", "debug", "--json", *options, "--", *command,
                               cwd=cwd or self.repo)
        self.assertEqual(result.returncode, want, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        return Path(report["log"]).read_text()

    def base(self):
        bases = json.loads(self.home.run("bases", "--json").stdout)
        self.assertEqual(len(bases), 1)
        return Path(bases[0]["directory"])

    def test_worktrees_share_paths_and_only_changed_files_get_new_timestamps(self):
        (self.repo / "untracked.txt").write_text("dirty input")
        self.run_base("true")
        base = self.base()
        first = (base / "src" / "Source.txt").stat().st_mtime_ns
        (base / "DerivedData" / "cached").write_text("warm")
        self.run_base("true", cwd=self.other)
        self.assertEqual((base / "src" / "Source.txt").stat().st_mtime_ns, first)
        self.assertFalse((base / "src" / "untracked.txt").exists())
        self.assertEqual((base / "DerivedData" / "cached").read_text(), "warm")
        (self.other / "Source.txt").write_text("two\n")
        os.utime(self.other / "Source.txt", ns=(first, first))
        self.run_base("true", cwd=self.other)
        self.assertEqual((base / "src" / "Source.txt").read_text(), "two\n")
        self.assertNotEqual((base / "src" / "Source.txt").stat().st_mtime_ns, first)
        self.assertEqual((self.repo / "Source.txt").read_text(), "one\n")

    def test_incremental_builder_reuses_outputs_in_a_fresh_worktree(self):
        build = ("import os,pathlib; s=pathlib.Path('Source.txt'); "
                 "o=pathlib.Path(os.environ['BUILDQ_DERIVED_DATA'])/'object'; "
                 "dirty=not o.exists() or s.stat().st_mtime_ns>o.stat().st_mtime_ns; "
                 "o.write_text(s.read_text()) if dirty else None; print('compiled' if dirty else 'reused')")
        self.assertIn("compiled", self.run_base(sys.executable, "-c", build))
        self.assertIn("reused", self.run_base(sys.executable, "-c", build, cwd=self.other))
        (self.other / "Source.txt").write_text("edited")
        self.assertIn("compiled", self.run_base(sys.executable, "-c", build, cwd=self.other))

    def test_dirty_inputs_deletions_modes_and_file_directory_transitions(self):
        (self.repo / "folder").mkdir()
        (self.repo / "folder" / "input").write_text("first")
        self.run_base("true")
        base = self.base()
        (self.repo / "folder" / "input").unlink()
        (self.repo / "folder").rmdir()
        (self.repo / "folder").write_text("now a file")
        (self.repo / "Source.txt").chmod(0o755)
        self.run_base("true")
        self.assertEqual((base / "src" / "folder").read_text(), "now a file")
        self.assertTrue((base / "src" / "Source.txt").stat().st_mode & 0o111)
        (self.repo / "folder").unlink()
        (self.repo / "folder").mkdir()
        (self.repo / "folder" / "new").write_text("new child")
        (self.repo / "Source.txt").unlink()
        self.run_base("true")
        self.assertFalse((base / "src" / "Source.txt").exists())
        self.assertEqual((base / "src" / "folder" / "new").read_text(), "new child")

    def test_prepare_preserves_identical_generated_files_and_failure_stops_build(self):
        prepare = "mkdir -p App.xcodeproj; printf project > App.xcodeproj/project.pbxproj"
        self.run_base("true", options=("--prepare", prepare))
        project = self.base() / "src" / "App.xcodeproj" / "project.pbxproj"
        before = project.stat().st_mtime_ns
        self.run_base("true", cwd=self.other, options=("--prepare", prepare))
        self.assertEqual(before, project.stat().st_mtime_ns)
        self.assertFalse((self.repo / "App.xcodeproj").exists())
        output = self.run_base("sh", "-c", "echo MUST_NOT_RUN", options=("--prepare", "exit 42"), want=42)
        # The log's command header includes the command; its actual output must be absent.
        self.assertNotIn("\nMUST_NOT_RUN\n", output)

    def test_ignored_files_require_explicit_pathspec(self):
        (self.repo / "local.xcconfig").write_text("CONFIG=1")
        self.run_base("true")
        source = self.base() / "src"
        self.assertFalse((source / "local.xcconfig").exists())
        self.run_base("true", options=("--include-ignored", "*.xcconfig"))
        self.assertEqual((source / "local.xcconfig").read_text(), "CONFIG=1")
        self.run_base("true", cwd=self.other)
        self.assertFalse((source / "local.xcconfig").exists())

    def test_git_head_and_subdirectory_cwd_match_the_caller(self):
        (self.other / "child").mkdir()
        (self.other / "child" / "new").write_text("new")
        self.git("add", ".", cwd=self.other)
        self.git("commit", "-qm", "other", cwd=self.other)
        expected = self.git("rev-parse", "HEAD", cwd=self.other)
        log = self.run_base("sh", "-c", "git rev-parse HEAD; cat new; echo \"$BUILDQ_BASE_CACHE\"",
                            cwd=self.other / "child")
        self.assertIn(expected, log)
        self.assertIn("new", log)
        self.assertIn(str(self.base() / "cache"), log)

    def test_generated_source_changes_never_overwrite_worktree_and_are_reset_next_run(self):
        self.run_base("sh", "-c", "echo modified > Source.txt")
        self.assertEqual((self.repo / "Source.txt").read_text(), "one\n")
        log = self.run_base("cat", "Source.txt")
        self.assertIn("\none\n", log)

    def test_base_created_symlink_cannot_write_outside_the_base(self):
        self.run_base("true")
        outside = self.home.path / "outside"
        outside.mkdir()
        (self.base() / "src" / "unsafe").symlink_to(outside, target_is_directory=True)
        (self.repo / "unsafe").mkdir()
        (self.repo / "unsafe" / "file").write_text("must stay private")
        self.run_base("true", want=1)
        self.assertFalse((outside / "file").exists())

    def test_external_source_symlink_is_rejected(self):
        (self.repo / "external").symlink_to(self.home.path / "outside")
        self.run_base("true", want=1)

    def test_absolute_internal_symlink_is_rebased(self):
        (self.repo / "linked").symlink_to(self.repo / "Source.txt")
        self.run_base("true")
        link = self.base() / "src" / "linked"
        self.assertEqual(link.resolve(), self.base() / "src" / "Source.txt")

    def test_products_survive_reuse_and_existing_output_is_not_replaced(self):
        command = ("import os,pathlib; p=pathlib.Path(os.environ['BUILDQ_DERIVED_DATA'])/'Build/Products/App.app'; "
                   "p.mkdir(parents=True,exist_ok=True); (p/'data').write_text(pathlib.Path('Source.txt').read_text())")
        output = self.home.path / "saved products"
        self.run_base(sys.executable, "-c", command, options=("--products-to", str(output)))
        self.assertEqual((output / "App.app" / "data").read_text(), "one\n")
        (self.other / "Source.txt").write_text("next")
        self.run_base(sys.executable, "-c", command, cwd=self.other)
        self.assertEqual((output / "App.app" / "data").read_text(), "one\n")
        self.run_base("true", options=("--products-to", str(output)), want=1)
        self.assertEqual((output / "App.app" / "data").read_text(), "one\n")

    def test_export_rejects_links_to_files_or_directories_outside_the_products(self):
        make = ("import os,pathlib; p=pathlib.Path(os.environ['BUILDQ_DERIVED_DATA'])/'Build/Products'; "
                "p.mkdir(parents=True,exist_ok=True); c=pathlib.Path(os.environ['BUILDQ_BASE_CACHE']); "
                "(c/'data').write_text('mutable'); (p/'link').symlink_to(c/'data')")
        output = self.home.path / "unsafe-export"
        log = self.run_base(sys.executable, "-c", make,
                            options=("--products-to", str(output), "--no-dsym"), want=1)
        self.assertIn("product symlink", log)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.home.path.glob(".buildq-products-*")), [])
        link = self.base() / "DerivedData/Build/Products/link"
        link.unlink()
        link.symlink_to(self.base() / "cache", target_is_directory=True)
        self.run_base("true", options=("--products-to", str(output)), want=1)
        self.assertFalse(output.exists())

    def test_export_keeps_internal_relative_framework_links_independent(self):
        make = ("import os,pathlib; p=pathlib.Path(os.environ['BUILDQ_DERIVED_DATA'])/'Build/Products/F.framework'; "
                "(p/'Versions/A').mkdir(parents=True,exist_ok=True); (p/'Versions/A/F').write_text('first'); "
                "(p/'Versions/Current').symlink_to('A'); (p/'F').symlink_to('Versions/Current/F')")
        output = self.home.path / "framework-export"
        self.run_base(sys.executable, "-c", make, options=("--products-to", str(output), "--no-dsym"))
        exported = output / "F.framework"
        self.assertEqual(os.readlink(exported / "Versions/Current"), "A")
        self.assertEqual(os.readlink(exported / "F"), "Versions/Current/F")
        (self.base() / "DerivedData/Build/Products/F.framework/Versions/A/F").write_text("second")
        self.assertEqual((exported / "F").read_text(), "first")

    def test_export_rejects_absolute_internal_and_dangling_links(self):
        self.run_base("true")
        products = self.base() / "DerivedData/Build/Products"
        products.mkdir(parents=True)
        (products / "data").write_text("original")
        link = products / "link"
        output = self.home.path / "invalid-link-export"
        for target in (products / "data", "missing", "link"):
            with self.subTest(target=str(target)):
                link.symlink_to(target)
                log = self.run_base("true", options=("--products-to", str(output), "--no-dsym"), want=1)
                self.assertIn("product symlink", log)
                self.assertFalse(output.exists())
                link.unlink()

    def test_export_rejects_a_redirected_products_root(self):
        self.run_base("true")
        products = self.base() / "DerivedData/Build/Products"
        products.parent.mkdir(parents=True)
        (self.base() / "cache/data").write_text("mutable")
        products.symlink_to(self.base() / "cache", target_is_directory=True)
        output = self.home.path / "redirected-export"
        log = self.run_base("true", options=("--products-to", str(output)), want=1)
        self.assertIn("product directory redirects", log)
        self.assertFalse(output.exists())

    def fake_xcodebuild_for_export(self):
        tools = self.home.path / "fake-tools"
        tools.mkdir()
        tool = tools / "xcodebuild"
        tool.write_text("#!" + sys.executable + "\n" + '''import json,os,pathlib,sys
args = sys.argv[1:]
products = pathlib.Path(os.environ['BUILDQ_DERIVED_DATA']) / 'Build/Products/Debug'
for key in ('CONFIGURATION_BUILD_DIR', 'SYMROOT', 'BUILD_DIR', 'DSTROOT'):
    for arg in args:
        if arg.startswith(key + '='):
            products = pathlib.Path(arg.split('=', 1)[1])
products = pathlib.Path(os.environ.get('BUILDQ_TEST_PRODUCTS', str(products)))
if '-showBuildSettings' in args:
    if '-resultBundlePath' in args or '-archivePath' in args or '-resultStreamPath' in args:
        sys.exit(66)
    if os.environ.get('BUILDQ_TEST_BAD_SETTINGS'):
        print('invalid settings output')
    else:
        print(json.dumps([{'target': 'App', 'buildSettings': {
            'BUILT_PRODUCTS_DIR': str(products), 'TARGET_BUILD_DIR': str(products)}}]))
else:
    pathlib.Path(os.environ['BUILDQ_TEST_MARKER']).write_text(json.dumps(args))
    products.mkdir(parents=True, exist_ok=True)
    (products / 'App').write_text('current product')
''')
        tool.chmod(0o755)
        self.home.env["PATH"] = str(tools) + os.pathsep + self.home.env["PATH"]
        marker = self.home.path / "compiled"
        self.home.env["BUILDQ_TEST_MARKER"] = str(marker)
        return marker

    def test_export_checks_actual_xcode_settings_before_compiling(self):
        marker = self.fake_xcodebuild_for_export()
        self.run_base("true")
        stale = self.base() / "DerivedData/Build/Products/Debug/App"
        stale.parent.mkdir(parents=True)
        stale.write_text("old product")
        for setting in ("CONFIGURATION_BUILD_DIR", "SYMROOT", "BUILD_DIR", "DSTROOT"):
            with self.subTest(setting=setting):
                output = self.home.path / (setting + "-export")
                log = self.run_base("xcodebuild", "-scheme", "App", setting + "=" + str(self.home.path / "custom"),
                                    "build", options=("--products-to", str(output), "--no-dsym"), want=1)
                self.assertIn("products outside", log)
                self.assertFalse(marker.exists())
                self.assertFalse(output.exists())
        # A project/xcconfig can override the setting without a command-line assignment.
        self.home.env["BUILDQ_TEST_PRODUCTS"] = str(self.home.path / "project-custom")
        self.run_base("xcodebuild", "-scheme", "App", "build",
                      options=("--products-to", str(self.home.path / "project-export")), want=1)
        self.assertFalse(marker.exists())

    def test_export_settings_probe_preserves_the_actual_build_arguments(self):
        marker = self.fake_xcodebuild_for_export()
        results = self.home.path / "results.xcresult"
        output = self.home.path / "checked-export"
        self.run_base("xcodebuild", "-scheme", "App", "-resultBundlePath", str(results), "build",
                      options=("--products-to", str(output), "--no-dsym"))
        self.assertEqual((output / "Debug/App").read_text(), "current product")
        args = json.loads(marker.read_text())
        self.assertIn("-resultBundlePath", args)
        self.assertIn(str(results), args)
        self.assertNotIn("-showBuildSettings", args)

    def test_export_fails_closed_if_xcode_settings_cannot_be_read(self):
        marker = self.fake_xcodebuild_for_export()
        self.home.env["BUILDQ_TEST_BAD_SETTINGS"] = "1"
        output = self.home.path / "bad-settings-export"
        self.run_base("xcodebuild", "-scheme", "App", "build",
                      options=("--products-to", str(output)), want=1)
        self.assertFalse(marker.exists())
        self.assertFalse(output.exists())

    def test_build_failure_does_not_export_stale_products(self):
        output = self.home.path / "failed products"
        self.run_base("sh", "-c", "exit 65", options=("--products-to", str(output)), want=65)
        self.assertFalse(output.exists())
        self.assertEqual(json.loads(self.home.run("status", "--json").stdout), [])

    def test_history_retains_the_build_base_for_the_gui(self):
        self.run_base("true")
        self.run_base("sh", "-c", "exit 65", want=65)
        result = self.home.run("run", "--", "true", cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in (self.home.path / "history.log").read_text().splitlines()]
        self.assertEqual([record["base"] for record in records], ["debug", "debug", None])

    def test_direct_xcodebuild_uses_fixed_dd_and_maps_absolute_arguments(self):
        tools = self.home.path / "tools"
        tools.mkdir()
        tool = tools / "xcodebuild"
        tool.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\nprintf '%s:12:5: error: example\\n' \"$PWD/Source.txt\"\n")
        tool.chmod(0o755)
        self.home.env["PATH"] = str(tools) + os.pathsep + self.home.env["PATH"]
        log = self.run_base("xcodebuild", "-project", str(self.repo / "App.xcodeproj"), "build")
        self.assertIn(str(self.base() / "src" / "App.xcodeproj"), log)
        self.assertIn(str(self.base() / "DerivedData"), log)
        self.assertIn(str(self.repo / "Source.txt") + ":12:5: error: example", log)
        self.run_base("xcodebuild", "-derivedDataPath", "elsewhere", "build", want=1)

    def test_named_bases_are_separate_and_custom_root_is_supported(self):
        self.home.env["BUILDQ_BASE_ROOT"] = str(self.home.path / "build storage")
        self.run_base("true")
        result = self.home.run("run", "--base", "release", "--", "true", cwd=self.other)
        self.assertEqual(result.returncode, 0, result.stderr)
        bases = json.loads(self.home.run("bases", "--json").stdout)
        self.assertEqual({b["name"] for b in bases}, {"debug", "release"})
        self.assertTrue(all("build storage" in b["directory"] for b in bases))

    def test_same_base_name_in_two_repositories_keeps_build_data_separate(self):
        self.run_base("true")
        separate = self.home.path / "separate repository"
        separate.mkdir()
        self.git("init", "-q", cwd=separate)
        self.git("config", "user.name", "Test", cwd=separate)
        self.git("config", "user.email", "test@example.invalid", cwd=separate)
        (separate / "Source.txt").write_text("separate")
        self.git("add", ".", cwd=separate)
        self.git("commit", "-qm", "initial", cwd=separate)
        self.run_base("true", cwd=separate)
        bases = json.loads(self.home.run("bases", "--json").stdout)
        self.assertEqual(len({b["directory"] for b in bases}), 2)
        self.assertEqual({(Path(b["directory"]) / "src" / "Source.txt").read_text() for b in bases},
                         {"one\n", "separate"})

    def test_uninitialized_submodule_fails_explicitly(self):
        head = self.git("rev-parse", "HEAD")
        self.git("update-index", "--add", "--cacheinfo", "160000", head, "missing-submodule")
        output = self.run_base("true", want=1)
        self.assertIn("submodules are not supported", output)

    def test_options_require_a_base_and_names_cannot_escape_its_directory(self):
        for options in [("--base", "../escape"), ("--prepare", "true"), ("--products-to", "out")]:
            result = self.home.run("run", *options, "--", "true", cwd=self.repo)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)

    def test_invalid_state_and_nested_queue_fail_with_an_actionable_error(self):
        self.run_base("true")
        state = self.base() / "state.json"
        state.write_text("[]")
        output = self.run_base("true", want=1)
        self.assertIn("invalid build base state", output)
        self.assertNotIn("Traceback", output)
        state.unlink()
        self.run_base("true")
        from helpers import CLI
        self.run_base(sys.executable, str(CLI), "run", "--", "true", want=1)

    def test_normal_cancel_waits_for_a_stubborn_descendant(self):
        marker = self.home.path / "started"
        pidfile = self.home.path / "child-pid"
        code = ("import os,pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); "
                f"pathlib.Path({str(marker)!r}).touch(); time.sleep(600)")
        victim = self.home.popen("run", "--base", "debug", "--", sys.executable, "-c", code,
                                 cwd=self.repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "build did not start")
            self.assertEqual(self.home.run("cancel", str(victim.pid)).returncode, 0)
            self.assertEqual(victim.wait(timeout=25), 143)
            self.assertEqual(self.home.run("run", "--", "true").returncode, 0)
            child_pid = int(pidfile.read_text())
            # A dead child may briefly remain as a zombie, but must not still be executing.
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)],
                                    capture_output=True, text=True).stdout.strip()
            self.assertTrue(not status or status.startswith("Z"), status)
        finally:
            if victim.poll() is None:
                victim.kill()
                victim.wait(timeout=10)
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), 9)
                except ProcessLookupError:
                    pass

    def test_prepare_is_in_the_queue_and_sigkill_keeps_lock_until_descendants_die(self):
        marker = self.home.path / "started"
        stubborn = ("import pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                    f"pathlib.Path({str(marker)!r}).touch(); time.sleep(600)")
        # exec keeps the preparation process as the worker's child, in the same process group.
        prepare = "exec " + shlex.join([sys.executable, "-c", stubborn])
        victim = self.home.popen("run", "--base", "debug", "--prepare", prepare, "--", "true",
                                 cwd=self.repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        nxt = None
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "preparation did not start")
            # An ordinary queued command must wait for preparation as well as compilation.
            nxt = self.home.popen("run", "--", "true", cwd=self.other,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.2)
            self.assertIsNone(nxt.poll())
            victim.kill()
            victim.wait(timeout=10)
            self.assertEqual(nxt.wait(timeout=30), 0)
            events = [json.loads(l) for l in (self.home.path / "events.log").read_text().splitlines()]
            self.assertIn("orphan_kill", [e["kind"] for e in events])
        finally:
            if victim.poll() is None:
                victim.terminate()
                victim.wait(timeout=20)
            if nxt is not None and nxt.poll() is None:
                nxt.terminate()
                nxt.wait(timeout=20)

    @unittest.skipUnless(os.environ.get("BUILDQ_TEST_XCODE") == "1", "opt-in real Xcode build")
    def test_real_xcodebuild_reuses_dd_and_exports_independent_symbols(self):
        shutil.copytree(ROOT / "tests" / "fixtures" / "Hello.xcodeproj", self.repo / "Hello.xcodeproj")
        (self.repo / "main.c").write_text('#include <stdio.h>\nint main(void) { puts("42"); return 0; }\n')
        self.git("add", ".")
        self.git("commit", "-qm", "Xcode fixture")
        self.git("checkout", "--detach", self.git("rev-parse", "HEAD"), cwd=self.other)
        command = ("/usr/bin/xcodebuild", "-project", "Hello.xcodeproj", "-scheme", "Hello",
                   "-configuration", "Debug", "-destination", "platform=macOS", "build")
        exported = self.home.path / "xcode products"
        results = self.home.path / "build.xcresult"
        first = self.run_base(*command, "-resultBundlePath", str(results), options=("--products-to", str(exported)))
        self.assertTrue(results.is_dir())
        second = self.run_base(*command, cwd=self.other)
        (self.other / "main.c").write_text('#include <stdio.h>\nint main(void) { puts("43"); return 0; }\n')
        third = self.run_base(*command, cwd=self.other)
        counts = [len(re.findall(r"^CompileC ", log, re.M)) for log in (first, second, third)]
        self.assertEqual(counts, [1, 0, 1])
        binary = exported / "Debug" / "Hello"
        self.assertEqual(subprocess.check_output([str(binary)], text=True), "42\n")
        symbols = exported / "BuildQSymbols" / "Debug" / "Hello.dSYM" / "Contents" / "Resources"
        mappings = list(symbols.glob("*.plist"))
        self.assertTrue(mappings)
        with mappings[0].open("rb") as f:
            mapping = plistlib.load(f)
        self.assertEqual(mapping["DBGSourcePathRemapping"][str(self.base() / "src")], str(self.repo.resolve()))
        dsym = symbols.parent.parent
        debug = subprocess.run(["xcrun", "lldb", "--batch",
                                "-o", "target create " + shlex.quote(str(binary)),
                                "-o", "target symbols add " + shlex.quote(str(dsym)),
                                "-o", "breakpoint set --file " + shlex.quote(str(self.repo.resolve() / "main.c")) + " --line 2"],
                               capture_output=True, text=True)
        self.assertEqual(debug.returncode, 0, debug.stdout + debug.stderr)
        self.assertIn("Breakpoint 1:", debug.stdout)
        self.assertNotIn("no locations", debug.stdout)
        self.assertNotIn("pending", debug.stdout)

        custom = self.home.path / "custom build products"
        refused = self.home.path / "custom export"
        log = self.run_base(*command, "CONFIGURATION_BUILD_DIR=" + str(custom), cwd=self.other,
                            options=("--products-to", str(refused)), want=1)
        self.assertIn("products outside", log)
        self.assertFalse(custom.exists(), "the invalid export must be rejected before compilation")
        self.assertFalse(refused.exists())

        project = self.other / "Hello.xcodeproj/project.pbxproj"
        original = project.read_text()
        anchor = 'PRODUCT_NAME = "$(TARGET_NAME)";'
        self.assertIn(anchor, original)
        project.write_text(original.replace(anchor, anchor + f' CONFIGURATION_BUILD_DIR = "{custom}";'))
        log = self.run_base(*command, cwd=self.other, options=("--products-to", str(refused)), want=1)
        self.assertIn("products outside", log)
        self.assertFalse(custom.exists())
        self.assertFalse(refused.exists())
        print(f"\nXcode compile tasks: cold={counts[0]}, fresh worktree={counts[1]}, edit={counts[2]}")


if __name__ == "__main__":
    unittest.main()
