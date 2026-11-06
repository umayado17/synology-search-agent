"""表示の言語（lang = ja / en）のテスト（標準ライブラリのみ、NAS・ssh 不要）。

言語を変えても、JSON のキー・error.type・終了コードは変わらないことを確かめる。
"""
import ast
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import unittest.mock

from test_client import CLIENT, FakeRunner, client
from test_unisearch import ROOT, SCRIPT, FakePrompter, FakeTransport, cli

MCP = os.path.join(ROOT, "clients", "nas-search-mcp")

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def untranslated(path, allowed=()):
    """t(ja, en) の外にある日本語の文字列（コメントと docstring を除く）を返す。"""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    ok = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ok.add(id(first.value))  # docstring
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "t":
            if len(node.args) == 2:
                ok.add(id(node.args[0]))
        if isinstance(node, ast.Tuple) and len(node.elts) == 2:
            ok.add(id(node.elts[0]))  # AUTH_ERROR_HINTS の (ja, en)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and JAPANESE.search(node.value) and id(node) not in ok
                and node.value not in allowed):
            found.append((node.lineno, node.value))
    return found


class AllMessagesTranslatedTest(unittest.TestCase):
    """日本語の文を足したら、英語も一緒に書くこと。"""

    def test_server(self):
        self.assertEqual(untranslated(SCRIPT), [])

    def test_client(self):
        self.assertEqual(untranslated(CLIENT, ("lang は ja か en / lang must be ja or en",)), [])

    def test_mcp(self):
        self.assertEqual(untranslated(MCP), [])

    def test_english_side_has_no_japanese(self):
        for path in (SCRIPT, CLIENT, MCP):
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "t":
                    if any(isinstance(arg, ast.Starred) for arg in node.args):
                        continue  # t(*hint)。AUTH_ERROR_HINTS の組
                    self.assertEqual(len(node.args), 2, (path, node.lineno))
                    english = node.args[1]
                    self.assertIsInstance(english, ast.Constant, (path, node.lineno))
                    self.assertFalse(JAPANESE.search(english.value), (path, node.lineno))
                    self.assertEqual(english.value.count("%"),
                                     node.args[0].value.count("%"), (path, node.lineno))


class ServerLanguageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uslang-")
        self.config = os.path.join(self.tmp, "server.conf")
        self.env = {"UNISEARCH_STATE_DIR": os.path.join(self.tmp, "state"),
                    "UNISEARCH_CONFIG": self.config}

    def tearDown(self):
        cli.set_language("ja")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        with open(self.config, "w", encoding="utf-8") as handle:
            handle.write(text)

    def run_main(self, argv, responses=None, environ=None):
        out, err = io.StringIO(), io.StringIO()
        try:
            with unittest.mock.patch("sys.stderr", err):
                code = cli.main(argv, transport=FakeTransport(responses or {}),
                                prompter=FakePrompter(), stdout=out, stderr=err,
                                environ=environ or self.env, daemonize=False)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def login_failure(self):
        return self.run_main(["--json", "--user", "u", "q"],
                             {"login": {"success": False, "error": {"code": 400}}})

    def test_default_is_japanese(self):
        code, _, err = self.login_failure()
        error = json.loads(err)["error"]
        self.assertEqual(code, cli.EXIT_AUTH)
        self.assertIn("認証失敗", error["message"])

    def test_english_keeps_type_and_exit_code(self):
        _, _, ja = self.login_failure()
        self.write("# 表示を英語に\nlang = en\n")
        code, _, en = self.login_failure()
        ja, en = json.loads(ja)["error"], json.loads(en)["error"]
        self.assertEqual(code, cli.EXIT_AUTH)
        self.assertEqual(en["message"],
                         "Login failed (code 400): possibly a wrong account or password")
        self.assertEqual({k: v for k, v in ja.items() if k != "message"},
                         {k: v for k, v in en.items() if k != "message"})

    def test_english_prompts(self):
        self.write("lang = EN\n")
        prompter = FakePrompter()
        cli.main(["--user", "u", "q"], transport=FakeTransport(
            {"login": {"success": False, "error": {"code": 400}}}), prompter=prompter,
            stdout=io.StringIO(), stderr=io.StringIO(), environ=self.env)
        self.assertEqual(prompter.labels, ["DSM password for u: ",
                                           "2-step verification code for u (6 digits): "])
        self.assertIn("not your SSH login", prompter.notices[0])

    def test_english_usage_error(self):
        self.write("lang = en\n")
        code, _, err = self.run_main([])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("give search words", err)

    def test_english_help(self):
        self.write("lang = en\n")
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            code = self.run_main(["--help"])[0]
        self.assertEqual(code, 0)
        self.assertIn("number of results", out.getvalue())

    def test_bad_language_is_a_settings_error(self):
        self.write("lang = fr\n")
        code, _, err = self.run_main(["--json", "--user", "u", "q"])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertEqual(json.loads(err)["error"]["type"], "config")
        self.assertIn("lang", json.loads(err)["error"]["message"])

    def test_gateway_reads_language(self):
        self.write("lang = en\n")
        for command, expected in (("ls", "Only `search <words>"),
                                  ("status", "No search session")):
            with self.subTest(command=command):
                code, out, err = self.run_main(
                    ["--agent-gateway"], environ=dict(self.env, SSH_ORIGINAL_COMMAND=command))
                error = json.loads(err)["error"]
                self.assertIn(expected, error["message"])
                self.assertEqual(out, "")

    def test_gateway_ignores_broken_file_for_language(self):
        self.write("this line is broken\nlang = en\n")
        code, _, err = self.run_main(["--agent-gateway"],
                                     environ=dict(self.env, SSH_ORIGINAL_COMMAND="ls"))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("Only `search", json.loads(err)["error"]["message"])

    def test_broker_errors_follow_language(self):
        broker = cli.Broker(FakeTransport({}), "sid", ["/archive"], 3600, 0)
        cli.set_language("en")
        self.assertEqual(broker.handle({"op": "nope"})["error"]["message"],
                         "Unsupported operation")
        self.assertEqual(broker.handle({"op": "search", "query": ""})["error"]["type"], "usage")


class ClientLanguageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nslang-")
        self.key = os.path.join(self.tmp, "agent_key")
        with open(self.key, "w") as handle:
            handle.write("dummy")
        self.conf = os.path.join(self.tmp, "client.conf")
        self.env = {"UNISEARCH_HOST": "agent@nas.example", "UNISEARCH_KEY": self.key,
                    "UNISEARCH_CLIENT_CONFIG": self.conf}

    def tearDown(self):
        client.LANG = "ja"
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        with open(self.conf, "w", encoding="utf-8") as handle:
            handle.write(text)

    def run_client(self, argv, replies=(), environ=None):
        out, err = io.StringIO(), io.StringIO()
        code = client.main(argv, environ=environ or self.env, runner=FakeRunner(replies),
                           stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_default_is_japanese(self):
        code, _, err = self.run_client([])
        self.assertEqual(code, client.EXIT_USAGE)
        self.assertEqual(json.loads(err)["error"]["message"], "検索語を指定してください")

    def test_settings_file_and_environment(self):
        self.write("lang = en\n")
        code, _, err = self.run_client([])
        self.assertEqual(json.loads(err)["error"]["message"], "Give search words")
        code, _, err = self.run_client([], environ=dict(self.env, UNISEARCH_LANG="ja"))
        self.assertEqual(json.loads(err)["error"]["message"], "検索語を指定してください")

    def test_type_and_exit_code_do_not_change(self):
        ja = self.run_client(["q", "--limit", "0"])
        en = self.run_client(["q", "--limit", "0"], environ=dict(self.env, UNISEARCH_LANG="en"))
        self.assertEqual(ja[0], en[0])
        ja, en = json.loads(ja[2])["error"], json.loads(en[2])["error"]
        self.assertEqual(en["message"], "--limit must be 1-100")
        self.assertEqual(ja["type"], en["type"])

    def test_bad_language(self):
        self.write("lang = fr\n")
        code, _, err = self.run_client(["q"], [(0, {"hits": []}, "")])
        self.assertEqual(code, client.EXIT_USAGE)
        self.assertEqual(json.loads(err)["error"]["type"], "config")

    def test_broken_file_does_not_block_help(self):
        self.write("broken line\nlang = en\n")
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as caught:
                client.main(["--help"], environ=self.env, runner=FakeRunner([]),
                            stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("results per call", out.getvalue())

    def test_nas_messages_pass_through(self):
        nas_error = json.dumps({"schema_version": 1, "error": {
            "type": "session_required", "message": "No search session.", "api_code": None,
            "exit_code": 8}})
        code, _, err = self.run_client(["q"], [(8, "", nas_error + "\n")])
        self.assertEqual(code, 8)
        self.assertEqual(json.loads(err)["error"]["message"], "No search session.")


if __name__ == "__main__":
    unittest.main()
