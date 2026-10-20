import json
import os
import signal
import subprocess
import sys
import time
import unittest

from helpers import Home, load_cli

# Records start/end of each job into a shared trace file; sleeps to make overlaps visible.
JOB = ("import sys,time;f=open(sys.argv[1],'a');f.write(f'S {sys.argv[2]} {time.time()}\\n');f.flush();"
       "time.sleep(float(sys.argv[3]));f.write(f'E {sys.argv[2]} {time.time()}\\n')")


class Queue(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.trace = self.home.path / "trace"
        self.trace.touch()

    def tearDown(self):
        self.home.cleanup()

    def job(self, name, sleep=0.3, *extra):
        return self.home.popen("run", "--label", name, "--report-every", "999", *extra, "--",
                               sys.executable, "-c", JOB, str(self.trace), name, str(sleep),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def events(self, kind=None):
        lines = (self.home.path / "events.log").read_text().splitlines()
        return [e for e in map(json.loads, lines) if kind is None or e["kind"] == kind]

    def trace_events(self):
        return [line.split() for line in self.trace.read_text().splitlines()]

    def test_concurrent_jobs_never_overlap_and_run_in_arrival_order(self):
        procs = [self.job(f"j{i}", 0.2) for i in range(10)]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        running = None
        for kind, name, _ in self.trace_events():
            if kind == "S":
                self.assertIsNone(running, f"{name} started while {running} was running")
                running = name
            else:
                self.assertEqual(running, name)
                running = None
        starts = [name for kind, name, _ in self.trace_events() if kind == "S"]
        history = [json.loads(l) for l in (self.home.path / "history.log").read_text().splitlines()]
        self.assertEqual(starts, [h["label"] for h in sorted(history, key=lambda h: h["id"])])
        self.assertEqual(len(starts), 10)
        self.assertEqual(list((self.home.path / "queue").glob("*.json")), [])

    def test_exit_code_log_symlink_and_error_lines(self):
        log = self.home.path / "out" / "build.log"
        r = self.home.run("run", "--label", "fail", "--log", str(log), "--", "sh", "-c",
                          "echo 'a.swift:1:1: error: boom'; echo noise; exit 3")
        self.assertEqual(r.returncode, 3)
        self.assertTrue(log.is_symlink())
        self.assertIn("noise", log.read_text())
        self.assertNotIn("noise", r.stdout + r.stderr)
        self.assertIn("error: boom", r.stderr)
        self.assertEqual(self.events("finished")[0]["exit"], 3)

    def test_output_passes_through_without_log(self):
        r = self.home.run("run", "--", "echo", "hello")
        self.assertEqual((r.returncode, r.stdout), (0, "hello\n"))

    def test_waiting_gives_up_with_75(self):
        holder = self.job("long", 5)
        time.sleep(1)
        start = time.time()
        r = self.home.run("run", "--max-wait", "0.03", "--report-every", "999", "--", "true")
        self.assertEqual(r.returncode, 75)
        self.assertLess(time.time() - start, 10)
        self.assertIn("gave up", r.stderr)
        holder.kill()
        holder.wait()

    def test_status_shows_running_and_waiting(self):
        first = self.job("first", 3)
        time.sleep(1)
        second = self.job("second", 0.1)
        time.sleep(1)
        out = self.home.run("status").stdout
        self.assertRegex(out, r"RUNNING .*«first»")
        self.assertRegex(out, r"#2 .*«second».*waiting")
        tickets = json.loads(self.home.run("status", "--json").stdout)
        self.assertEqual([t["state"] for t in tickets], ["running", "waiting"])
        self.assertEqual(first.wait(timeout=30), 0)
        self.assertEqual(second.wait(timeout=30), 0)

    def test_cancel_waiting_ticket(self):
        holder = self.job("holder", 3)
        time.sleep(1)
        waiter = self.job("waiter", 0.1)
        time.sleep(1)
        self.assertEqual(self.home.run("cancel", str(waiter.pid)).returncode, 0)
        self.assertEqual(waiter.wait(timeout=10), 128 + signal.SIGTERM)
        self.assertEqual(holder.wait(timeout=30), 0)
        self.assertEqual([e["state"] for e in self.events("interrupted")], ["waiting"])

    def test_killed_wrapper_keeps_slot_until_orphan_is_killed(self):
        stubborn = ("import signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    f"open(sys.argv[1],'a').write(f'S orphan {{time.time()}}\\n');time.sleep(600)")
        victim = self.home.popen("run", "--label", "victim", "--", sys.executable, "-c", stubborn, str(self.trace),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        nxt = self.job("next", 0.1)
        time.sleep(0.5)
        os.kill(victim.pid, signal.SIGKILL)
        victim.wait()
        killed_at = time.time()
        self.assertEqual(nxt.wait(timeout=30), 0)
        started = {name: float(ts) for kind, name, ts in self.trace_events() if kind == "S"}
        # ORPHAN_GRACE is 2s in tests: the next job must wait for SIGKILL of the orphaned build.
        self.assertGreater(started["next"] - killed_at, 1.5)
        kinds = [e["kind"] for e in self.events()]
        for k in ("orphan_term", "orphan_kill", "reaped"):
            self.assertIn(k, kinds)

    def test_journal_rotates(self):
        cli = load_cli()
        cli.HOME, cli.EVENTS, cli.ROTATE_BYTES = self.home.path, self.home.path / "events.log", 1000
        for i in range(50):
            cli.event("x", i=i)
        self.assertTrue((self.home.path / "events.log.1").exists())
        self.assertLess((self.home.path / "events.log").stat().st_size, 2000)

if __name__ == "__main__":
    unittest.main()
