"""検索セッション（待機役）とAI用入口のテスト（標準ライブラリのみ、NAS不要）。

実際に UNIX ソケットを開き、待機役をスレッドで動かして検証する。
"""
import io
import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest
import unittest.mock

from test_synosearch import (FakePrompter, FakeTransport, LOGIN_OK, LOGOUT_OK, SECRETS,
                             cli, fixture)

ALLOWED = "/archive"


def mixed_response():
    return {"success": True, "data": {"total": 999, "has_error": False, "unavail": [], "hits": [
        {"SYNOMDFSName": "a.doc", "SYNOMDSharePath": "/archive/Drive/a.doc",
         "SYNOMDFSSize": "10", "SYNOMDIsDir": "n"},
        {"SYNOMDFSName": "secret.txt", "SYNOMDSharePath": "/homes/other/secret.txt"},
        {"SYNOMDFSName": "x", "SYNOMDSharePath": "/public/other/x"},
    ]}}


def short_tempdir():
    # UNIX ソケットのパス長制限（macOS は104バイト）を避けるため短い場所に作る
    for base in ("/tmp", None):
        try:
            return tempfile.mkdtemp(prefix="ss-", dir=base)
        except OSError:
            continue
    raise unittest.SkipTest("一時ディレクトリを作れません")


class Clock:
    def __init__(self, now=1000000.0):
        self.now = now

    def __call__(self):
        return self.now


class BrokerUnitTest(unittest.TestCase):
    """ソケットを使わず Broker.handle / tick を直接試す。"""

    def make(self, responses=None, max_age=3600, keepalive=0):
        transport = FakeTransport(responses or {"search": mixed_response(), "logout": LOGOUT_OK})
        clock = Clock()
        broker = cli.Broker(transport, "dummy-sid-ABC123", [ALLOWED], max_age, keepalive, clock)
        return broker, transport, clock

    def test_search_is_filtered_and_hides_total(self):
        broker, transport, _ = self.make()
        response = broker.handle({"op": "search", "query": "test", "limit": 3})
        self.assertTrue(response["ok"])
        result = response["result"]
        self.assertEqual([h["share_path"] for h in result["hits"]], ["/archive/Drive/a.doc"])
        self.assertIsNone(result["total"])
        self.assertIsNone(result["next_offset"], "API の結果が3件で終わり")
        self.assertIsNone(result["unavailable"])
        self.assertEqual(transport.calls[0][1]["_sid"], "dummy-sid-ABC123")

    def test_status_never_contains_sid(self):
        broker, _, _ = self.make()
        status = json.dumps(broker.handle({"op": "status"}))
        self.assertNotIn("dummy-sid", status)
        self.assertIn(ALLOWED, status)

    def test_invalid_requests(self):
        broker, transport, _ = self.make()
        for request in ([], {"op": "raw"}, {"op": "search"},
                        {"op": "search", "query": "  "},
                        {"op": "search", "query": "x" * 1001},
                        {"op": "search", "query": "q", "limit": 0},
                        {"op": "search", "query": "q", "limit": True},
                        {"op": "search", "query": "q", "offset": -1},
                        {"op": "search", "query": "q", "offset": "5"}):
            with self.subTest(request=request):
                response = broker.handle(request)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["exit_code"], cli.EXIT_USAGE)
        self.assertEqual(transport.calls, [], "不正な要求ではDSMを呼ばない")

    def test_expiry_stops_and_requires_new_session(self):
        broker, transport, clock = self.make(max_age=60)
        clock.now += 61
        response = broker.handle({"op": "search", "query": "q"})
        self.assertEqual(response["error"]["type"], "session_required")
        self.assertTrue(broker.stopping)
        self.assertEqual(transport.calls, [])

    def test_consecutive_failures_stop_broker(self):
        broker, _, _ = self.make({"search": {"success": False, "error": {"code": 119}}})
        for _ in range(cli.MAX_CONSECUTIVE_FAILURES):
            self.assertFalse(broker.stopping)
            response = broker.handle({"op": "search", "query": "q"})
            self.assertEqual(response["error"]["api_code"], 119)
        self.assertTrue(broker.stopping)
        self.assertEqual(broker.stop_reason, "consecutive_failures")

    def test_keepalive_runs_on_interval(self):
        broker, transport, clock = self.make(keepalive=600)
        broker.tick()
        self.assertEqual(transport.calls, [])
        clock.now += 600
        broker.tick()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1]["size"], "1")

    def test_close_logs_out_once(self):
        broker, transport, _ = self.make()
        broker.close()
        broker.close()
        self.assertEqual(transport.methods(), ["logout"])


class SessionIntegrationTest(unittest.TestCase):
    """session start（前面実行）をスレッドで動かし、CLI と入口から実際のソケット越しに使う。"""

    def setUp(self):
        self.tmp = short_tempdir()
        self.state = os.path.join(self.tmp, "state")
        self.env = {"SYNOSEARCH_STATE_DIR": self.state,
                    "SYNOSEARCH_CONFIG": os.path.join(self.state, "none.conf")}
        self.sock = cli.socket_path_in(self.state)
        self.transport = FakeTransport({"login": LOGIN_OK, "search": mixed_response(),
                                        "logout": LOGOUT_OK})
        self.start_out = io.StringIO()
        self.start_err = io.StringIO()
        self.thread = threading.Thread(target=self._run_start, daemon=True)
        self.thread.start()
        deadline = time.time() + 5
        while not os.path.exists(self.sock):
            if time.time() > deadline or not self.thread.is_alive():
                self.fail("待機役が起動しない: %s" % self.start_err.getvalue())
            time.sleep(0.02)

    def _run_start(self):
        self.start_code = cli.main(
            ["session", "start", "--user", "search-agent", "--allow-prefix", ALLOWED,
             "--keepalive", "0", "--foreground"],
            transport=self.transport, prompter=FakePrompter(), stdout=self.start_out,
            stderr=self.start_err, environ=self.env, daemonize=False)

    def tearDown(self):
        if self.thread.is_alive():
            cli.main(["session", "stop"], stdout=io.StringIO(), stderr=io.StringIO(),
                     environ=self.env)
            self.thread.join(5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, argv, environ=None):
        out, err = io.StringIO(), io.StringIO()
        client_transport = FakeTransport({})  # 呼ばれたら KeyError で失敗する
        code = cli.main(argv, transport=client_transport, prompter=FakePrompter(),
                        stdout=out, stderr=err, environ=environ or self.env)
        self.assertEqual(client_transport.calls, [], "セッション利用時にクライアントはDSMを呼ばない")
        return code, out.getvalue(), err.getvalue()

    def gateway(self, command):
        env = dict(self.env, SSH_ORIGINAL_COMMAND=command)
        return self.call(["--agent-gateway"], env)

    def test_permissions_are_private(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.sock).st_mode), 0o600)

    def test_cli_uses_session_without_prompt(self):
        code, out, err = self.call(["test", "--json", "--limit", "3"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual([h["share_path"] for h in result["hits"]], ["/archive/Drive/a.doc"])
        self.assertEqual(result["hits"][0]["size"], 10)

    def test_client_can_only_narrow(self):
        code, out, _ = self.call(["test", "--json", "--allow-prefix", "/archive/Other"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hits"], [])
        code, out, _ = self.call(["test", "--json", "--allow-prefix", "/homes"])
        self.assertEqual(json.loads(out)["hits"], [], "クライアントの指定で範囲は広がらない")

    def test_gateway_search_and_status(self):
        code, out, err = self.gateway('search "test" --limit 3')
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)["hits"]), 1)
        code, out, _ = self.gateway("status")
        self.assertEqual(code, 0)
        session = json.loads(out)["session"]
        self.assertEqual(session["allow_prefixes"], [ALLOWED])
        self.assertGreaterEqual(session["searches"], 0)

    def test_gateway_rejects_everything_else(self):
        before = len(self.transport.calls)
        for command in ("", "ls", "rm -rf /", "search", "search ''", "search x --raw",
                        "search x; cat /etc/passwd", "search x --allow-prefix /homes",
                        "status extra", "stop", "session stop", "search x --limit 101",
                        "search x --lim 5", 'search "unterminated', "search x --user admin"):
            with self.subTest(command=command):
                code, out, err = self.gateway(command)
                self.assertEqual(code, cli.EXIT_USAGE)
                self.assertEqual(out, "")
                self.assertEqual(json.loads(err)["error"]["type"], "usage")
        self.assertEqual(len(self.transport.calls), before, "拒否した命令ではDSMを呼ばない")
        self.assertTrue(self.thread.is_alive(), "入口から待機役を止められない")

    def test_second_start_is_refused(self):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["session", "start", "--user", "u", "--allow-prefix", ALLOWED,
                         "--foreground"], transport=FakeTransport({}), prompter=FakePrompter(),
                        stdout=out, stderr=err, environ=self.env, daemonize=False)
        self.assertEqual(code, cli.EXIT_SESSION_STATE)

    def test_oversized_request_is_rejected(self):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(self.sock)
        try:
            client.sendall(b"x" * (cli.MAX_REQUEST_BYTES + 10))
            reply = json.loads(cli.recv_line(client, 1 << 20))
        finally:
            client.close()
        self.assertFalse(reply["ok"])
        self.assertTrue(self.thread.is_alive())

    def test_no_secrets_anywhere(self):
        outputs = [self.start_out.getvalue(), self.start_err.getvalue()]
        outputs += list(self.call(["test", "--json"])[1:])
        outputs += list(self.gateway("status")[1:])
        log_path = os.path.join(self.state, cli.LOG_NAME)
        cli.main(["session", "stop"], stdout=io.StringIO(), stderr=io.StringIO(), environ=self.env)
        self.thread.join(5)
        with open(log_path, encoding="utf-8") as fh:
            log_text = fh.read()
        self.assertEqual(stat.S_IMODE(os.stat(log_path).st_mode), 0o600)
        self.assertNotIn('"test"', log_text, "検索語はログに書かない")
        for text in outputs + [log_text]:
            for secret in SECRETS:
                self.assertNotIn(secret, text)

    def test_stop_logs_out_and_removes_socket(self):
        code, out, _ = self.call(["session", "stop"])
        self.assertEqual(code, 0)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.start_code, 0)
        self.assertEqual(self.transport.methods()[-1], "logout")
        self.assertFalse(os.path.exists(self.sock))
        code, _, err = self.gateway('search "test"')
        self.assertEqual(code, cli.EXIT_SESSION_REQUIRED)
        self.assertEqual(json.loads(err)["error"]["type"], "session_required")


class NoSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = short_tempdir()
        self.env = {"SYNOSEARCH_STATE_DIR": os.path.join(self.tmp, "state"),
                    "SYNOSEARCH_CONFIG": os.path.join(self.tmp, "none.conf")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gateway_without_session(self):
        err = io.StringIO()
        code = cli.main(["--agent-gateway"], stdout=io.StringIO(), stderr=err,
                        environ=dict(self.env, SSH_ORIGINAL_COMMAND="status"))
        self.assertEqual(code, cli.EXIT_SESSION_REQUIRED)

    def test_stale_socket_is_replaced_on_start(self):
        state = self.env["SYNOSEARCH_STATE_DIR"]
        os.mkdir(state, 0o700)
        path = cli.socket_path_in(state)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(path)
        stale.close()  # ファイルだけ残る
        code = cli.main(["--agent-gateway"], stdout=io.StringIO(), stderr=io.StringIO(),
                        environ=dict(self.env, SSH_ORIGINAL_COMMAND="status"))
        self.assertEqual(code, cli.EXIT_SESSION_REQUIRED)
        listener = cli.create_listener(path)
        listener.close()
        os.unlink(path)

    def test_start_requires_allow_prefix_and_valid_durations(self):
        for argv in (["session", "start", "--user", "u"],
                     ["session", "start", "--user", "u", "--allow-prefix", "/"],
                     ["session", "start", "--user", "u", "--allow-prefix", ALLOWED,
                      "--max-age", "91d"],
                     ["session", "start", "--user", "u", "--allow-prefix", ALLOWED,
                      "--keepalive", "5s"]):
            with self.subTest(argv=argv):
                transport = FakeTransport({})
                try:
                    with unittest.mock.patch("sys.stderr", io.StringIO()):
                        code = cli.main(argv, transport=transport, prompter=FakePrompter(),
                                        stdout=io.StringIO(), stderr=io.StringIO(),
                                        environ=self.env, daemonize=False)
                except SystemExit as exc:
                    code = exc.code
                self.assertEqual(code, cli.EXIT_USAGE)
                self.assertEqual(transport.calls, [], "引数エラーではログインしない")

    def test_login_failure_leaves_no_socket(self):
        transport = FakeTransport({"login": {"success": False, "error": {"code": 400}}})
        code = cli.main(["session", "start", "--user", "u", "--allow-prefix", ALLOWED],
                        transport=transport, prompter=FakePrompter(), stdout=io.StringIO(),
                        stderr=io.StringIO(), environ=self.env, daemonize=False)
        self.assertEqual(code, cli.EXIT_AUTH)
        self.assertFalse(os.path.exists(cli.socket_path_in(self.env["SYNOSEARCH_STATE_DIR"])))

    def test_parse_gateway_command(self):
        self.assertEqual(cli.parse_gateway_command("status"), ("status", None))
        self.assertEqual(cli.parse_gateway_command('search "在留 資格" --limit 5 --offset 10'),
                         ("search", {"query": "在留 資格", "limit": 5, "offset": 10}))
        self.assertEqual(cli.parse_gateway_command("search -- -dash"),
                         ("search", {"query": "-dash", "limit": 10, "offset": 0}))

    def test_parse_duration(self):
        self.assertEqual(cli.parse_duration("8h"), 28800)
        self.assertEqual(cli.parse_duration("90m"), 5400)
        self.assertEqual(cli.parse_duration("0"), 0)
        self.assertEqual(cli.parse_duration("7d"), 7 * 86400)
        for bad in ("", "h", "1.5h", "-1m", "8w", None):
            self.assertIsNone(cli.parse_duration(bad), bad)


@unittest.skipUnless(hasattr(os, "fork"), "fork が無い環境")
class DaemonizeTest(unittest.TestCase):
    """本番と同じく fork して端末から切り離した待機役を、親が終了した後に使えること。"""

    def test_detached_broker_survives_parent_return(self):
        tmp = short_tempdir()
        env = {"SYNOSEARCH_STATE_DIR": os.path.join(tmp, "state")}
        try:
            transport = FakeTransport({"login": LOGIN_OK, "search": mixed_response(),
                                       "logout": LOGOUT_OK})
            out = io.StringIO()
            code = cli.main(["session", "start", "--user", "search-agent", "--allow-prefix",
                             ALLOWED, "--keepalive", "0"], transport=transport,
                            prompter=FakePrompter(), stdout=out, stderr=io.StringIO(),
                            environ=env)
            self.assertEqual(code, 0)
            self.assertIn("検索セッションを開始しました", out.getvalue())
            gw_out, gw_err = io.StringIO(), io.StringIO()
            code = cli.main(["--agent-gateway"], stdout=gw_out, stderr=gw_err,
                            environ=dict(env, SSH_ORIGINAL_COMMAND='search "test"'))
            self.assertEqual(code, 0, gw_err.getvalue())
            self.assertEqual(json.loads(gw_out.getvalue())["returned"], 1)
            code = cli.main(["session", "stop"], stdout=io.StringIO(), stderr=io.StringIO(),
                            environ=env)
            self.assertEqual(code, 0)
            sock = cli.socket_path_in(env["SYNOSEARCH_STATE_DIR"])
            deadline = time.time() + 5
            while os.path.exists(sock) and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(os.path.exists(sock), "停止後にソケットが消える")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FixtureStillValidTest(unittest.TestCase):
    def test_fixture_through_broker(self):
        transport = FakeTransport({"search": fixture("search_response_sample.json")})
        broker = cli.Broker(transport, "sid", ["/share"], 3600, 0)
        result = broker.handle({"op": "search", "query": "q", "limit": 5})["result"]
        self.assertEqual(result["returned"], 2)


if __name__ == "__main__":
    unittest.main()
