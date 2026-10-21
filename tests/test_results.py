import json
import unittest

from helpers import Home

FAIL = ("echo noise; echo '/src/A.swift:12:5: error: cannot find X'; echo '/src/A.swift:12:5: error: cannot find X'; "
        "echo 'ld: fatal error: missing'; echo '** BUILD FAILED **'; exit 65")


class Results(unittest.TestCase):
    def setUp(self):
        self.home = Home()

    def tearDown(self):
        self.home.cleanup()

    def test_run_json_reports_parsed_errors(self):
        r = self.home.run("run", "--json", "--", "sh", "-c", FAIL)
        self.assertEqual(r.returncode, 65)
        out = json.loads(r.stdout)
        self.assertEqual(out["exit"], 65)
        self.assertEqual(out["errors"], [
            {"file": "/src/A.swift", "line": 12, "col": 5, "msg": "cannot find X"},
            {"msg": "ld: fatal error: missing"},
            {"msg": "** BUILD FAILED **"}])
        self.assertIn("noise", open(out["log"]).read())

    def test_run_json_success_has_no_errors(self):
        r = self.home.run("run", "--json", "--", "echo", "error: just a word")
        self.assertEqual(json.loads(r.stdout)["errors"], [])

    def test_quiet_hides_output_and_prints_errors_on_failure(self):
        ok = self.home.run("run", "--quiet", "--", "echo", "hello")
        self.assertEqual((ok.returncode, ok.stdout), (0, ""))
        bad = self.home.run("run", "--quiet", "--label", "b", "--", "sh", "-c", FAIL)
        self.assertEqual(bad.stdout, "")
        self.assertIn("/src/A.swift:12:5: error: cannot find X", bad.stderr)
        self.assertNotIn("noise", bad.stderr)

    def test_errors_and_log_of_latest_build(self):
        self.home.run("run", "--quiet", "--", "sh", "-c", FAIL)
        errors = self.home.run("errors", "--json")
        self.assertEqual(errors.returncode, 0)
        self.assertEqual(len(json.loads(errors.stdout)["errors"]), 3)
        self.assertEqual(self.home.run("log", "--tail", "1").stdout, "** BUILD FAILED **\n")
        self.home.run("run", "--quiet", "--", "echo", "second")
        self.assertEqual(self.home.run("log", "--tail", "1").stdout, "second\n")

    def test_errors_by_id_prefix(self):
        first = json.loads(self.home.run("run", "--json", "--", "sh", "-c", FAIL).stdout)["id"]
        self.home.run("run", "--quiet", "--", "true")
        r = self.home.run("errors", first[:8])
        self.assertIn("cannot find X", r.stdout)
        ambiguous = self.home.run("errors", "0000000")
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("several builds", ambiguous.stderr)
        self.assertNotEqual(self.home.run("errors", "99999999").returncode, 0)


if __name__ == "__main__":
    unittest.main()
