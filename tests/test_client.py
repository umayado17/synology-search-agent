"""clients/nas-search のテスト（標準ライブラリのみ、NAS・ssh 不要）。"""
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from test_synosearch import ROOT, cli

CLIENT = os.path.join(ROOT, "clients", "nas-search")


def load_client():
    loader = importlib.machinery.SourceFileLoader("nas_search_client", CLIENT)
    spec = importlib.util.spec_from_loader("nas_search_client", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


client = load_client()

TRICKY_QUERIES = [
    "test", "在留資格", "在留 資格", "it's", 'say "hi"', "a;b", "$(id)", "`id`",
    "-dash", "--limit", "x\ny", "back\\slash", "*", "100%", "  spaced  ",
]


def page(hits, next_offset=None, has_error=False):
    return {"schema_version": 1, "query": "q", "offset": 0, "limit": 100, "total": None,
            "returned": len(hits), "next_offset": next_offset, "has_error": has_error,
            "unavailable": [], "hits": hits}


class FakeRunner:
    """subprocess.run の代わり。呼ばれた argv を記録し、用意した応答を順に返す。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        code, out, err = reply
        if not isinstance(out, str):
            out = json.dumps(out, ensure_ascii=False)
        return subprocess.CompletedProcess(argv, code, out.encode("utf-8"), err.encode("utf-8"))

    def remote_commands(self):
        return [argv[-1] for argv in self.calls]


class ClientTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nsc-")
        self.key = os.path.join(self.tmp, "agent_key")
        with open(self.key, "w") as handle:
            handle.write("dummy")
        self.env = {"SYNOSEARCH_HOST": "agent@nas.example", "SYNOSEARCH_KEY": self.key,
                    "SYNOSEARCH_CLIENT_CONFIG": os.path.join(self.tmp, "missing.conf")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_client(self, argv, replies, environ=None):
        runner = FakeRunner(replies)
        out, err = io.StringIO(), io.StringIO()
        code = client.main(argv, environ=environ or self.env, runner=runner,
                           stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue(), runner


class QuotingRoundTripTest(unittest.TestCase):
    """nas-search が作る文字列を、NAS 側入口の解釈処理に実際に通す。"""

    def test_queries_arrive_unchanged(self):
        for query in TRICKY_QUERIES:
            with self.subTest(query=query):
                command = client.search_command(query, 20, 40)
                self.assertEqual(cli.parse_gateway_command(command),
                                 ("search", {"query": query, "limit": 20, "offset": 40}))

    def test_status_command_is_accepted(self):
        self.assertEqual(cli.parse_gateway_command("status"), ("status", None))


class ClientBehaviourTest(ClientTestBase):
    def test_search_passes_output_and_uses_safe_ssh_options(self):
        result = page([{"share_path": "/archive/a"}], next_offset=None)
        code, out, err, runner = self.run_client(["在留資格", "--limit", "5"],
                                                 [(0, result, "")])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["hits"][0]["share_path"], "/archive/a")
        argv = runner.calls[0]
        self.assertEqual(argv[:3], ["ssh", "-i", self.key])
        for option in ("BatchMode=yes", "PasswordAuthentication=no",
                       "KbdInteractiveAuthentication=no", "IdentitiesOnly=yes"):
            self.assertIn(option, argv)
        self.assertEqual(argv[-2], "agent@nas.example")
        self.assertEqual(argv[-1], "search --limit 5 --offset 0 -- '在留資格'")

    def test_status(self):
        code, out, _, runner = self.run_client(["--status"], [(0, '{"session": {}}\n', "")])
        self.assertEqual(code, 0)
        self.assertEqual(runner.remote_commands(), ["status"])

    def test_remote_errors_pass_through(self):
        err_json = '{"schema_version": 1, "error": {"type": "session_required"}}\n'
        code, out, err, _ = self.run_client(["q"], [(8, "", err_json)])
        self.assertEqual(code, 8)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["error"]["type"], "session_required")

    def test_ssh_failure_becomes_json_error(self):
        code, out, err, _ = self.run_client(
            ["q"], [(255, "", "** WARNING: pq\nagent@nas: Permission denied (publickey).\n")])
        self.assertEqual(code, client.EXIT_NETWORK)
        error = json.loads(err)["error"]
        self.assertEqual(error["type"], "ssh")
        self.assertIn("Permission denied", error["message"])
        self.assertNotIn("WARNING", error["message"])

    def test_timeout_and_missing_ssh(self):
        code, _, err, _ = self.run_client(["q"], [subprocess.TimeoutExpired("ssh", 90)])
        self.assertEqual(code, client.EXIT_NETWORK)
        code, _, err, _ = self.run_client(["q"], [FileNotFoundError()])
        self.assertEqual(code, client.EXIT_NETWORK)

    def test_usage_errors_do_not_call_ssh(self):
        for argv in ([], ["  "], ["q", "--limit", "0"], ["q", "--limit", "101"],
                     ["q", "--offset", "-1"], ["q", "--max-results", "0"],
                     ["q", "--max-results", "1001"], ["q", "--status"], ["q", "--lim", "5"],
                     ["q", "--unknown"], ["x" * 1001]):
            with self.subTest(argv=argv):
                code, out, err, runner = self.run_client(argv, [])
                self.assertEqual(code, client.EXIT_USAGE)
                self.assertEqual(runner.calls, [])
                self.assertEqual(json.loads(err)["error"]["type"], "usage")


class ConfigTest(ClientTestBase):
    def test_missing_config_is_usage_error(self):
        env = {"SYNOSEARCH_CLIENT_CONFIG": os.path.join(self.tmp, "none.conf")}
        code, _, err, runner = self.run_client(["q"], [], environ=env)
        self.assertEqual(code, client.EXIT_USAGE)
        self.assertEqual(json.loads(err)["error"]["type"], "config")
        self.assertEqual(runner.calls, [])

    def test_config_file_and_precedence(self):
        conf = os.path.join(self.tmp, "client.conf")
        with open(conf, "w", encoding="utf-8") as handle:
            handle.write("# comment\nhost = file@nas\nkey = %s\n" % self.key)
        env = {"SYNOSEARCH_CLIENT_CONFIG": conf}
        _, _, _, runner = self.run_client(["q"], [(0, page([]), "")], environ=env)
        self.assertEqual(runner.calls[0][-2], "file@nas")
        env["SYNOSEARCH_HOST"] = "env@nas"
        _, _, _, runner = self.run_client(["q"], [(0, page([]), "")], environ=env)
        self.assertEqual(runner.calls[0][-2], "env@nas")
        _, _, _, runner = self.run_client(["q", "--host", "arg@nas"], [(0, page([]), "")],
                                          environ=env)
        self.assertEqual(runner.calls[0][-2], "arg@nas")

    def test_bad_host_or_key(self):
        env = dict(self.env, SYNOSEARCH_HOST="-oProxyCommand=evil")
        code, _, _, runner = self.run_client(["q"], [], environ=env)
        self.assertEqual(code, client.EXIT_USAGE)
        env = dict(self.env, SYNOSEARCH_KEY=os.path.join(self.tmp, "nokey"))
        code, _, _, runner = self.run_client(["q"], [], environ=env)
        self.assertEqual(code, client.EXIT_USAGE)
        self.assertEqual(runner.calls, [])


class MaxResultsTest(ClientTestBase):
    def test_pages_until_enough(self):
        first = page([{"share_path": "/p/%d" % i} for i in range(60)], next_offset=100)
        second = page([{"share_path": "/p/x%d" % i} for i in range(70)], next_offset=200)
        code, out, err, runner = self.run_client(["q", "--max-results", "120"],
                                                 [(0, first, ""), (0, second, "")])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["returned"], 120)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(result["next_offset"], 200)
        self.assertEqual(runner.remote_commands(), [
            "search --limit 100 --offset 0 -- q", "search --limit 100 --offset 100 -- q"])

    def test_stops_at_end(self):
        code, out, _, runner = self.run_client(
            ["q", "--max-results", "500"], [(0, page([{"share_path": "/p/1"}]), "")])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["next_offset"], None)
        self.assertEqual(len(runner.calls), 1)

    def test_error_midway_is_returned(self):
        err_json = '{"schema_version": 1, "error": {"type": "session_required"}}\n'
        code, out, err, _ = self.run_client(
            ["q", "--max-results", "300"],
            [(0, page([{}] * 100, next_offset=100), ""), (8, "", err_json)])
        self.assertEqual(code, 8)
        self.assertEqual(out, "")
        self.assertIn("session_required", err)

    def test_bad_json_page(self):
        code, _, err, _ = self.run_client(["q", "--max-results", "10"], [(0, "not json", "")])
        self.assertEqual(code, client.EXIT_RESPONSE)


if __name__ == "__main__":
    unittest.main()
