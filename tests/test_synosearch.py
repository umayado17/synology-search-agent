"""synosearch の単体・回帰テスト（標準ライブラリ unittest のみ、NAS不要）。

実行: python3 -m unittest discover -s tests -v
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "synosearch")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def load_cli():
    loader = importlib.machinery.SourceFileLoader("synosearch_cli", SCRIPT)
    spec = importlib.util.spec_from_loader("synosearch_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cli = load_cli()

# テスト用のダミー値。実在の資格情報ではない。
USER = "testuser"
PASSWORD = "dummy-password-XYZ"
OTP = "otp-987654-dummy"
SID = "dummy-sid-ABC123"
SECRETS = (PASSWORD, OTP, SID)


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


class FakeTransport:
    """API呼び出しを記録し、method ごとに用意した応答（または例外）を返す。"""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def call(self, url, params):
        self.calls.append((url, dict(params)))
        key = params.get("method")
        response = self.responses[key]
        if isinstance(response, BaseException):
            raise response
        return response

    def methods(self):
        return [params.get("method") for _, params in self.calls]


class FakePrompter:
    def __init__(self, username=USER, password=PASSWORD, otp=OTP, error=None):
        self._username = username
        self._secrets = [password, otp]
        self.error = error
        self.asked_username = False

    def username(self):
        self.asked_username = True
        if self.error:
            raise self.error
        return self._username

    def secret(self, label):
        if self.error:
            raise self.error
        return self._secrets.pop(0)


LOGIN_OK = {"success": True, "data": {"sid": SID}}
NO_SESSION_ENV = {"SYNOSEARCH_STATE_DIR": "/nonexistent/synosearch-test-state"}
LOGOUT_OK = {"success": True}


def run_cli(argv, responses, prompter=None):
    transport = FakeTransport(responses)
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, transport=transport, prompter=prompter or FakePrompter(),
                    stdout=out, stderr=err, environ=NO_SESSION_ENV)
    return code, out.getvalue(), err.getvalue(), transport


class SearchParamsRegressionTest(unittest.TestCase):
    """docs/verification.md の実機成功条件から外れていないことを固定する。"""

    def test_matches_verified_golden(self):
        golden = fixture("verified_search_params.json")
        params = cli.build_search_params("test", 5, 0, "<SID>")
        self.assertEqual(set(params), set(golden))
        for key, value in golden.items():
            if key in ("search_weight_list", "fields"):
                self.assertEqual(json.loads(params[key]), json.loads(value), key)
            else:
                self.assertEqual(params[key], value, key)

    def test_auth_and_session_constants(self):
        self.assertEqual(cli.API_URL, "https://127.0.0.1:5001/webapi/entry.cgi")
        self.assertEqual((cli.AUTH_API, cli.AUTH_VERSION, cli.SESSION_NAME),
                         ("SYNO.API.Auth", "6", "FileStation"))
        self.assertEqual((cli.SEARCH_API, cli.SEARCH_VERSION),
                         ("SYNO.Finder.FileIndexing.Search", "1"))

    def test_keyword_is_json_quoted_and_unicode_safe(self):
        params = cli.build_search_params('在留"資格', 1, 0, "s")
        self.assertEqual(json.loads(params["keyword"]), '在留"資格')

    def test_login_params(self):
        transport = FakeTransport({"login": LOGIN_OK})
        cli.login(transport, USER, PASSWORD, OTP)
        url, params = transport.calls[0]
        self.assertEqual(url, cli.API_URL)
        self.assertEqual(params, {
            "api": "SYNO.API.Auth", "version": "6", "method": "login",
            "account": USER, "passwd": PASSWORD, "otp_code": OTP,
            "session": "FileStation", "format": "sid"})


class JsonContractTest(unittest.TestCase):
    def test_success_json_schema(self):
        code, out, err, transport = run_cli(
            ["test", "--limit", "5", "--json"],
            {"login": LOGIN_OK, "search": fixture("search_response_sample.json"),
             "logout": LOGOUT_OK})
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(transport.methods(), ["login", "search", "logout"])
        result = json.loads(out)
        self.assertEqual(sorted(result), sorted([
            "schema_version", "query", "offset", "limit", "total", "returned",
            "next_offset", "has_error", "unavailable", "hits"]))
        self.assertIsNone(result["next_offset"], "2件 < limit 5 なので続きなし")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["total"], 3666)
        self.assertEqual(result["returned"], 2)
        self.assertIs(result["has_error"], False)
        self.assertEqual(result["unavailable"], [])
        self.assertEqual(out.count("\n"), 1, "stdoutはJSON1行")

    def test_hit_types_follow_real_device_format(self):
        """実機では数値が文字列、is_dir が "y"/"n" で返る（2026-11-04）。"""
        _, out, _, _ = run_cli(
            ["test", "--json"],
            {"login": LOGIN_OK, "search": fixture("search_response_sample.json"),
             "logout": LOGOUT_OK})
        folder, document = json.loads(out)["hits"]
        self.assertEqual(sorted(folder), sorted([
            "name", "share_path", "path", "extension", "size", "is_dir",
            "modified", "modified_utc", "readable"]))
        self.assertEqual(folder, {
            "name": "Test", "share_path": "/share/example/Test",
            "path": "/volume1/share/example/Test", "extension": "",
            "size": 4096, "is_dir": True, "modified": 1244279630,
            "modified_utc": "2009-06-06T09:13:50Z", "readable": True})
        self.assertIs(document["is_dir"], False)
        self.assertEqual(document["size"], 1234)

    def test_converters_tolerate_unexpected_values(self):
        self.assertEqual(cli.to_int(" 12 "), 12)
        self.assertEqual(cli.to_int(7), 7)
        for bad in (None, "", "1.5", "abc", True, [], {}):
            self.assertIsNone(cli.to_int(bad), bad)
        self.assertIs(cli.to_bool("y"), True)
        self.assertIs(cli.to_bool("n"), False)
        self.assertIsNone(cli.to_bool("maybe"))
        self.assertIsNone(cli.to_utc_iso("x"))
        self.assertIsNone(cli.to_utc_iso("99999999999999999999"))
        self.assertIsNone(cli.to_text(5))

    def test_raw_passes_data_through(self):
        sample = fixture("search_response_sample.json")
        code, out, _, _ = run_cli(["test", "--raw"],
                                  {"login": LOGIN_OK, "search": sample, "logout": LOGOUT_OK})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["raw"], sample["data"])

    def test_missing_fields_become_null(self):
        code, out, _, _ = run_cli(
            ["x", "--json"],
            {"login": LOGIN_OK, "logout": LOGOUT_OK,
             "search": {"success": True, "data": {"hits": [{}, "bad"]}}})
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertIsNone(result["total"])
        self.assertEqual(result["hits"][1]["name"], None)
        self.assertIsNone(result["has_error"])
        self.assertIsNone(result["unavailable"])

    def test_text_output_and_offset_numbering(self):
        code, out, _, _ = run_cli(
            ["test", "--offset", "20"],
            {"login": LOGIN_OK, "search": fixture("search_response_sample.json"),
             "logout": LOGOUT_OK})
        self.assertEqual(code, 0)
        self.assertIn("総件数: 3666", out)
        self.assertIn("21. Test", out)

    def test_text_output_neutralizes_control_chars(self):
        evil = {"success": True, "data": {"total": 1, "hits": [
            {"SYNOMDFSName": "a\x1b[31mb\x9bc", "SYNOMDSharePath": "/s\rx"}]}}
        code, out, _, _ = run_cli(["q"], {"login": LOGIN_OK, "search": evil,
                                          "logout": LOGOUT_OK})
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x9b", out)
        self.assertNotIn("\r", out)


class ErrorAndExitCodeTest(unittest.TestCase):
    def assert_json_error(self, err, kind, exit_code, api_code=None):
        payload = json.loads(err)
        self.assertEqual(payload["error"]["type"], kind)
        self.assertEqual(payload["error"]["exit_code"], exit_code)
        self.assertEqual(payload["error"]["api_code"], api_code)

    def test_auth_failure(self):
        code, out, err, transport = run_cli(
            ["test", "--json"],
            {"login": {"success": False, "error": {"code": 404}}})
        self.assertEqual(code, cli.EXIT_AUTH)
        self.assertEqual(out, "")
        self.assert_json_error(err, "auth", 3, 404)
        self.assertEqual(transport.methods(), ["login"], "未ログインならログアウトしない")

    def test_search_failure_still_logs_out(self):
        code, out, err, transport = run_cli(
            ["test", "--json"],
            {"login": LOGIN_OK, "search": {"success": False, "error": {"code": 120}},
             "logout": LOGOUT_OK})
        self.assertEqual(code, cli.EXIT_API)
        self.assertEqual(out, "")
        self.assert_json_error(err, "api", 4, 120)
        self.assertEqual(transport.methods(), ["login", "search", "logout"])

    def test_network_failure(self):
        code, _, err, _ = run_cli(
            ["test", "--json"],
            {"login": cli.CliError("network", "接続できません", cli.EXIT_NETWORK)})
        self.assertEqual(code, cli.EXIT_NETWORK)
        self.assert_json_error(err, "network", 5)

    def test_malformed_response(self):
        code, _, err, transport = run_cli(
            ["test", "--json"],
            {"login": LOGIN_OK, "search": {"success": True, "data": {"hits": "x"}},
             "logout": LOGOUT_OK})
        self.assertEqual(code, cli.EXIT_RESPONSE)
        self.assert_json_error(err, "response", 6)
        self.assertEqual(transport.methods()[-1], "logout")

    def test_login_without_sid(self):
        code, _, _, _ = run_cli(["test"], {"login": {"success": True, "data": {}}})
        self.assertEqual(code, cli.EXIT_RESPONSE)

    def test_no_tty(self):
        prompter = FakePrompter(error=cli.CliError("no_tty", "TTYなし", cli.EXIT_NO_TTY))
        code, _, err, transport = run_cli(["test", "--json"], {}, prompter)
        self.assertEqual(code, cli.EXIT_NO_TTY)
        self.assert_json_error(err, "no_tty", 7)
        self.assertEqual(transport.calls, [])

    def test_interrupt_during_search_logs_out(self):
        code, _, _, transport = run_cli(
            ["test"], {"login": LOGIN_OK, "search": KeyboardInterrupt(),
                       "logout": LOGOUT_OK})
        self.assertEqual(code, cli.EXIT_INTERRUPTED)
        self.assertEqual(transport.methods(), ["login", "search", "logout"])

    def test_logout_failure_is_warning_only(self):
        code, out, err, _ = run_cli(
            ["test", "--json"],
            {"login": LOGIN_OK, "search": fixture("search_response_sample.json"),
             "logout": {"success": False}})
        self.assertEqual(code, 0)
        json.loads(out)
        self.assertIn("ログアウト", err)

    def test_unexpected_exception_hides_message(self):
        code, _, err, _ = run_cli(
            ["test"], {"login": LOGIN_OK, "search": RuntimeError(PASSWORD),
                       "logout": LOGOUT_OK})
        self.assertEqual(code, cli.EXIT_INTERNAL)
        self.assertNotIn(PASSWORD, err)

    def test_user_option_skips_username_prompt(self):
        prompter = FakePrompter()
        code, _, _, transport = run_cli(
            ["test", "--user", "alice"],
            {"login": LOGIN_OK, "search": fixture("search_response_sample.json"),
             "logout": LOGOUT_OK}, prompter)
        self.assertEqual(code, 0)
        self.assertFalse(prompter.asked_username)
        self.assertEqual(transport.calls[0][1]["account"], "alice")


class UsageTest(unittest.TestCase):
    def assert_usage_error(self, argv):
        with self.assertRaises(SystemExit) as ctx:
            with unittest.mock.patch("sys.stderr", io.StringIO()):
                cli.main(argv, transport=FakeTransport({}), prompter=FakePrompter())
        self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)

    def test_bad_arguments_exit_2(self):
        for argv in ([], [""], ["q", "--limit", "0"], ["q", "--limit", "101"],
                     ["q", "--offset", "-1"], ["q", "--limit", "abc"],
                     ["q", "--timeout", "0"], ["q", "--unknown"]):
            with self.subTest(argv=argv):
                self.assert_usage_error(argv)


class SecretHygieneTest(unittest.TestCase):
    def test_secrets_never_printed(self):
        scenarios = [
            {"login": LOGIN_OK, "search": fixture("search_response_sample.json"),
             "logout": LOGOUT_OK},
            {"login": {"success": False, "error": {"code": 400}}},
            {"login": LOGIN_OK, "search": {"success": False, "error": {"code": 119}},
             "logout": {"success": False}},
        ]
        for responses in scenarios:
            for mode in ([], ["--json"], ["--raw"]):
                with self.subTest(mode=mode, responses=list(responses)):
                    _, out, err, _ = run_cli(["test"] + mode, responses)
                    for secret in SECRETS:
                        self.assertNotIn(secret, out)
                        self.assertNotIn(secret, err)

    def test_source_has_no_hardcoded_credentials(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        for needle in ("passwd\": \"", "otp_code\": \"", "_sid\": \""):
            self.assertNotIn(needle, source)


class CheckApiTest(unittest.TestCase):
    def test_ok(self):
        code, out, _, transport = run_cli(
            ["--check-api"], {"query": fixture("api_info_sample.json")})
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["ok"])
        self.assertEqual(transport.calls[0][0], cli.INFO_URL)
        self.assertNotIn("_sid", transport.calls[0][1])

    def test_missing_search_api(self):
        info = fixture("api_info_sample.json")
        del info["data"]["SYNO.Finder.FileIndexing.Search"]
        code, out, _, _ = run_cli(["--check-api"], {"query": info})
        self.assertEqual(code, cli.EXIT_RESPONSE)
        self.assertFalse(json.loads(out)["ok"])

    def test_version_out_of_range(self):
        info = fixture("api_info_sample.json")
        info["data"]["SYNO.API.Auth"]["maxVersion"] = 5
        code, _, _, _ = run_cli(["--check-api"], {"query": info})
        self.assertEqual(code, cli.EXIT_RESPONSE)


class TtyPrompterTest(unittest.TestCase):
    """本物の擬似端末で TtyPrompter を動かす（2026-11-04 実機で UnsupportedOperation の回帰）。"""

    def test_username_reads_from_pseudo_terminal(self):
        import pty
        master, slave = pty.openpty()
        try:
            os.write(master, "alice\n".encode("utf-8"))
            name = cli.TtyPrompter(os.ttyname(slave)).username()
            self.assertEqual(name, "alice")
            self.assertIn("DSMユーザー名", os.read(master, 1024).decode("utf-8"))
        finally:
            os.close(master)
            os.close(slave)

    def test_missing_tty_is_no_tty_error(self):
        with self.assertRaises(cli.CliError) as ctx:
            cli.TtyPrompter("/nonexistent/tty").username()
        self.assertEqual(ctx.exception.exit_code, cli.EXIT_NO_TTY)



def hit(share_path):
    return {"SYNOMDFSName": share_path.rsplit("/", 1)[-1], "SYNOMDSharePath": share_path}


class AllowPrefixTest(unittest.TestCase):
    """--allow-prefix: 許可した共有パス配下以外の結果を捨てる（多層防御）。"""

    PATHS = ["/archive/notePC/a.txt", "/homes/other/Drive/b.txt", "/archive",
             "/archive2/c.txt", "/archive/../homes/x", None, "relative/d.txt"]

    def run_filtered(self, extra):
        response = {"success": True, "data": {
            "total": 3666, "hits": [hit(p) if p else {} for p in self.PATHS]}}
        return run_cli(["q", "--json", "--limit", str(len(self.PATHS))] + extra,
                       {"login": LOGIN_OK, "search": response, "logout": LOGOUT_OK})

    def test_only_allowed_paths_are_returned(self):
        code, out, err, _ = self.run_filtered(["--allow-prefix", "/archive/"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual([h["share_path"] for h in result["hits"]],
                         ["/archive/notePC/a.txt", "/archive"])
        self.assertIsNone(result["total"], "範囲外の件数を漏らさない")
        self.assertEqual(result["next_offset"], len(self.PATHS))

    def test_dotdot_never_escapes_into_allowed_prefix(self):
        self.assertFalse(cli.path_allowed("/archive/../homes/x", ["/homes"]))
        self.assertFalse(cli.path_allowed("/homes/../archive/a", ["/archive"]))
        self.assertTrue(cli.path_allowed("/archive//a/./b", ["/archive"]))

    def test_multiple_prefixes(self):
        _, out, _, _ = self.run_filtered(
            ["--allow-prefix", "/archive/notePC", "--allow-prefix", "/homes/other"])
        self.assertEqual([h["share_path"] for h in json.loads(out)["hits"]],
                         ["/archive/notePC/a.txt", "/homes/other/Drive/b.txt"])

    def test_text_output_is_filtered_too(self):
        _, out, _, _ = run_cli(
            ["q", "--allow-prefix", "/archive"],
            {"login": LOGIN_OK, "logout": LOGOUT_OK, "search": {"success": True, "data": {
                "total": 2, "hits": [hit("/homes/other/x"), hit("/archive/y")]}}})
        self.assertNotIn("/homes/other", out)
        self.assertIn("/archive/y", out)
        self.assertIn("総件数: 不明", out)

    def test_without_filter_total_is_kept(self):
        _, out, _, _ = self.run_filtered([])
        result = json.loads(out)
        self.assertEqual(result["total"], 3666)
        self.assertEqual(result["returned"], len(self.PATHS))

    def test_invalid_prefixes_are_usage_errors(self):
        for bad in ("/", "archive", "/a/../b", "", "//"):
            with self.subTest(prefix=bad):
                with self.assertRaises(SystemExit) as ctx:
                    with unittest.mock.patch("sys.stderr", io.StringIO()):
                        cli.main(["q", "--allow-prefix", bad],
                                 transport=FakeTransport({}), prompter=FakePrompter())
                self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)

    def test_raw_cannot_bypass_filter(self):
        with self.assertRaises(SystemExit) as ctx:
            with unittest.mock.patch("sys.stderr", io.StringIO()):
                cli.main(["q", "--raw", "--allow-prefix", "/archive"],
                         transport=FakeTransport({}), prompter=FakePrompter())
        self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)

    def test_next_offset_when_page_is_full(self):
        _, out, _, _ = run_cli(
            ["q", "--json", "--limit", "2", "--offset", "10"],
            {"login": LOGIN_OK, "logout": LOGOUT_OK, "search": {"success": True, "data": {
                "total": 50, "hits": [hit("/a/1"), hit("/a/2")]}}})
        self.assertEqual(json.loads(out)["next_offset"], 12)


class HttpTransportTest(unittest.TestCase):
    def test_connection_refused_maps_to_network_error(self):
        transport = cli.HttpTransport(timeout=2)
        with self.assertRaises(cli.CliError) as ctx:
            transport.call("https://127.0.0.1:9/webapi/entry.cgi", {"a": "b"})
        self.assertEqual(ctx.exception.exit_code, cli.EXIT_NETWORK)


import unittest.mock  # noqa: E402  (UsageTest で使用)

if __name__ == "__main__":
    unittest.main()
