"""設定ファイル（server.conf）、セッション期間の上限、認証入力の表示のテスト。"""
import io
import os
import shutil
import tempfile
import unittest
import unittest.mock

from test_unisearch import (FakePrompter, FakeTransport, LOGIN_OK, LOGOUT_OK, SECRETS, cli,
                             read_terminal_until)

ALLOWED = "/archive"


class SettingsFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sst-")
        self.path = os.path.join(self.tmp, "server.conf")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_missing_file_gives_defaults(self):
        settings = cli.load_settings(self.path)
        self.assertEqual(settings, {"session_max_age": 8 * 3600, "session_keepalive": 600,
                                    "http_timeout": 45, "scan_time_budget": 40,
                                    "scan_max_pages": 20, "lang": "ja"})

    def test_file_values_and_comments(self):
        self.write("# 長めに使う\nsession_max_age = 7d\nSESSION_KEEPALIVE=5m\n\n"
                   "scan_time_budget = 60\nhttp_timeout = 30\nscan_max_pages = 50\n")
        settings = cli.load_settings(self.path)
        self.assertEqual(settings["session_max_age"], 7 * 86400)
        self.assertEqual(settings["session_keepalive"], 300)
        self.assertEqual(settings["scan_max_pages"], 50)
        self.assertEqual((settings["scan_time_budget"], settings["http_timeout"]), (60, 30))

    def test_hard_limits_cannot_be_exceeded(self):
        for line in ("session_max_age = 91d", "session_max_age = 30s", "http_timeout = 121",
                     "http_timeout = 0", "scan_time_budget = 500", "scan_max_pages = 101",
                     "scan_max_pages = 1.5", "session_keepalive = soon"):
            with self.subTest(line=line):
                self.write(line + "\n")
                with self.assertRaises(cli.CliError) as ctx:
                    cli.load_settings(self.path)
                self.assertEqual((ctx.exception.kind, ctx.exception.exit_code),
                                 ("config", cli.EXIT_USAGE))

    def test_ninety_days_is_allowed(self):
        self.write("session_max_age = 90d\n")
        self.assertEqual(cli.load_settings(self.path)["session_max_age"], 90 * 86400)

    def test_secrets_and_unknown_names_are_rejected(self):
        for line in ("password = x", "dsm_password=x", "otp = 123456", "sid = abc",
                     "api_token = t", "max_age = 8h", "just a line"):
            with self.subTest(line=line):
                self.write(line + "\n")
                with self.assertRaises(cli.CliError) as ctx:
                    cli.load_settings(self.path)
                self.assertEqual(ctx.exception.kind, "config")
                self.assertNotIn("abc", ctx.exception.message)
                self.assertNotIn("123456", ctx.exception.message)

    def test_config_path_from_environment(self):
        self.assertEqual(cli.config_path_from({"UNISEARCH_CONFIG": self.path}), self.path)
        self.assertTrue(cli.config_path_from({}).endswith(
            os.path.join(".config", "unisearch", "server.conf")))

    def test_bad_config_is_json_error_for_interactive_search(self):
        self.write("password = hunter2\n")
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["test", "--json"], prompter=FakePrompter(), stdout=out, stderr=err,
                        environ={"UNISEARCH_STATE_DIR": os.path.join(self.tmp, "state"),
                                 "UNISEARCH_CONFIG": self.path})
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn('"config"', err.getvalue())
        self.assertNotIn("hunter2", err.getvalue())

    def test_interactive_timeout_comes_from_file_unless_given(self):
        self.write("http_timeout = 30\n")
        env = {"UNISEARCH_STATE_DIR": os.path.join(self.tmp, "state"),
               "UNISEARCH_CONFIG": self.path}
        seen = []
        real = cli.HttpTransport

        def spy(timeout):
            seen.append(timeout)
            return real(timeout)
        with unittest.mock.patch.object(cli, "HttpTransport", spy):
            cli.main(["--check-api", "--json"], stdout=io.StringIO(), stderr=io.StringIO(),
                     environ=env)
            cli.main(["--check-api", "--json", "--timeout", "7"], stdout=io.StringIO(),
                     stderr=io.StringIO(), environ=env)
        self.assertEqual(seen, [30, 7])


class GatewayWaitTest(unittest.TestCase):
    """入口の待ち時間が、検索セッションでありうる最長の検索より長いこと。"""

    def test_wait_covers_the_largest_allowed_settings(self):
        longest = cli.search_time_limit(cli.SERVER_SETTINGS["scan_time_budget"][3],
                                        cli.SERVER_SETTINGS["http_timeout"][3])
        self.assertGreaterEqual(cli.gateway_wait(), longest)
        # session start --timeout 120 で、設定ファイルは既定のまま（レビューで指摘された例）
        self.assertGreater(cli.gateway_wait(), cli.search_time_limit(40, 120))

    def test_gateway_does_not_take_the_wait_from_the_config_file(self):
        tmp = tempfile.mkdtemp(prefix="sgw-")
        self.addCleanup(shutil.rmtree, tmp, True)
        conf = os.path.join(tmp, "server.conf")
        with open(conf, "w", encoding="utf-8") as handle:
            handle.write("http_timeout = 1\nscan_time_budget = 5\n")  # 開始後に小さく書き換えた
        waits = []

        def fake_request(socket_path, request, timeout):
            waits.append(timeout)
            return {"ok": True, "result": {"hits": []}}
        for config in (conf, os.path.join(tmp, "missing.conf")):
            with unittest.mock.patch.object(cli, "broker_request", fake_request):
                code = cli.main(["--agent-gateway"], stdout=io.StringIO(), stderr=io.StringIO(),
                                environ={"UNISEARCH_STATE_DIR": os.path.join(tmp, "state"),
                                         "UNISEARCH_CONFIG": config,
                                         "SSH_ORIGINAL_COMMAND": "search test"})
            self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(waits, [cli.gateway_wait()] * 2)


class SessionStartSettingsTest(unittest.TestCase):
    """session start が、引数 ＞ 設定ファイル ＞ 既定値 の順で値を選ぶこと。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sss-", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.conf = os.path.join(self.tmp, "server.conf")
        self.env = {"UNISEARCH_STATE_DIR": os.path.join(self.tmp, "st"),
                    "UNISEARCH_CONFIG": self.conf}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, extra, conf=None):
        if conf is not None:
            with open(self.conf, "w", encoding="utf-8") as handle:
                handle.write(conf)
        captured = {}

        def fake_run_broker(broker, listener, socket_path):
            captured["broker"] = broker
            listener.close()
            os.unlink(socket_path)
        prompter = FakePrompter()
        out, err = io.StringIO(), io.StringIO()
        transport = FakeTransport({"login": LOGIN_OK, "logout": LOGOUT_OK})
        with unittest.mock.patch.object(cli, "run_broker", fake_run_broker):
            code = cli.main(["session", "start", "--user", "search-agent", "--allow-prefix",
                             ALLOWED] + extra, transport=transport, prompter=prompter,
                            stdout=out, stderr=err, environ=self.env, daemonize=False)
        self.assertEqual(code, 0, err.getvalue())
        return captured["broker"], prompter, out.getvalue()

    def lifetime(self, broker):
        return broker.expires_at - broker.started_at

    def test_defaults(self):
        broker, _, out = self.start([])
        self.assertEqual(self.lifetime(broker), 8 * 3600)
        self.assertEqual(broker.keepalive, 600)
        self.assertNotIn("注意", out)

    def test_file_then_arguments(self):
        conf = "session_max_age = 3d\nsession_keepalive = 5m\nscan_max_pages = 7\n"
        broker, _, out = self.start([], conf)
        self.assertEqual((self.lifetime(broker), broker.keepalive), (3 * 86400, 300))
        self.assertEqual(broker.scan_max_pages, 7)
        self.assertIn("DSM 側のログインがこの期間もつかは確認されていません", out)
        broker, _, _ = self.start(["--max-age", "2h", "--keepalive", "0"], conf)
        self.assertEqual((self.lifetime(broker), broker.keepalive), (2 * 3600, 0))

    def test_start_message_shows_the_longest_search(self):
        _, _, out = self.start([])
        self.assertIn("1回の検索は最長 95 秒", out)
        _, _, out = self.start(["--timeout", "120"], "scan_time_budget = 60\n")
        self.assertIn("1回の検索は最長 190 秒", out)

    def test_ninety_days_by_argument(self):
        broker, _, _ = self.start(["--max-age", "90d"])
        self.assertEqual(self.lifetime(broker), 90 * 86400)

    def test_prompts_name_the_dsm_account(self):
        _, prompter, out = self.start([])
        self.assertEqual(prompter.labels, ["search-agent の DSM パスワード: ",
                                           "search-agent の2段階認証コード（6桁）: "])
        self.assertEqual(len(prompter.notices), 1)
        self.assertIn("DSM アカウント: search-agent", prompter.notices[0])
        self.assertIn("SSH のログイン用ではなく", prompter.notices[0])
        for secret in SECRETS:
            self.assertNotIn(secret, out)


class InteractivePromptTest(unittest.TestCase):
    def test_interactive_search_names_the_account(self):
        prompter = FakePrompter(username="alice")
        response = {"success": True, "data": {"total": 0, "hits": []}}
        code = cli.main(["q", "--json", "--no-session"], prompter=prompter,
                        transport=FakeTransport({"login": LOGIN_OK, "search": response,
                                                 "logout": LOGOUT_OK}),
                        stdout=io.StringIO(), stderr=io.StringIO(),
                        environ={"UNISEARCH_STATE_DIR": "/nonexistent/s",
                                 "UNISEARCH_CONFIG": "/nonexistent/c"})
        self.assertEqual(code, 0)
        self.assertTrue(all(label.startswith("alice の") for label in prompter.labels))
        self.assertIn("DSM アカウント: alice", prompter.notices[0])

    def test_control_characters_in_name_are_neutralised(self):
        prompter = FakePrompter()
        cli.ask_credentials(prompter, "evil\x1b[2J", "検索")
        self.assertNotIn("\x1b", prompter.notices[0] + "".join(prompter.labels))

    def test_tty_notice_and_label_reach_the_terminal(self):
        import pty
        master, slave = pty.openpty()
        try:
            cli.TtyPrompter(os.ttyname(slave)).notice("DSM アカウント: search-agent\n")
            self.assertIn("search-agent", read_terminal_until(master, "search-agent"))
        finally:
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
