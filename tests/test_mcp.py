"""clients/nas-search-mcp のテスト（標準ライブラリのみ、NAS・ssh 不要）。

nas-search の代わりに、引数を記録して決まった応答を返す偽物のスクリプトを使う。
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from test_unisearch import ROOT

MCP = os.path.join(ROOT, "clients", "nas-search-mcp")

FAKE_NAS_SEARCH = r'''
import json, os, sys, time
args = sys.argv[1:]
query = args[args.index("--") + 1] if "--" in args else None
if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(args) + "\n")
if args == ["--status"]:
    print(json.dumps({"schema_version": 1, "session": {"remaining_seconds": 60}}))
    sys.exit(0)
if query == "slow":
    time.sleep(float(os.environ.get("FAKE_SLEEP", "1.5")))
if query == "fail":
    sys.stderr.write(json.dumps({"schema_version": 1, "error": {
        "type": "session_required", "message": "No search session.", "api_code": None,
        "exit_code": 8}}) + "\n")
    sys.exit(8)
if query == "crash":
    sys.stderr.write("Traceback (most recent call last): ...\n")
    sys.exit(1)
if query == "garbage":
    print("not json")
    sys.exit(0)
print(json.dumps({"schema_version": 1, "query": query, "hits": [], "next_offset": None},
                 ensure_ascii=False))
'''


def load_mcp():
    loader = importlib.machinery.SourceFileLoader("nas_search_mcp", MCP)
    spec = importlib.util.spec_from_loader("nas_search_mcp", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


mcp = load_mcp()


def request(msg_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False)


def call(msg_id, name, arguments=None, meta=None):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    if meta is not None:
        params["_meta"] = meta
    return request(msg_id, "tools/call", params)


class McpTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nsmcp-")
        self.fake = os.path.join(self.tmp, "nas-search")
        with open(self.fake, "w", encoding="utf-8") as handle:
            handle.write(FAKE_NAS_SEARCH)
        self.log = os.path.join(self.tmp, "calls.log")
        self.environ = {"FAKE_LOG": self.log, "PATH": os.environ.get("PATH", ""),
                        "UNISEARCH_CLIENT_CONFIG": os.path.join(self.tmp, "none.conf")}
        self.out = io.StringIO()
        self.server = mcp.Server(self.fake, self.out, environ=self.environ)

    def tearDown(self):
        mcp.LANG = "ja"
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sent(self):
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    def last(self):
        return self.sent()[-1]

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as handle:
            return [json.loads(line) for line in handle]

    def tool(self, name, arguments=None, meta=None):
        self.server.handle_line(call(7, name, arguments, meta))
        reply = self.last()
        self.assertEqual(reply["id"], 7)
        return reply["result"]


class ProtocolTest(McpTestBase):
    def test_initialize_negotiates_version(self):
        for wanted, expected in (("2025-06-18", "2025-06-18"), ("2024-11-05", "2024-11-05"),
                                 ("1999-01-01", mcp.PROTOCOL_VERSIONS[0])):
            with self.subTest(wanted=wanted):
                self.server.handle_line(request(1, "initialize", {
                    "protocolVersion": wanted, "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"}}))
                result = self.last()["result"]
                self.assertEqual(result["protocolVersion"], expected)
                self.assertEqual(result["serverInfo"]["name"], "nas-search")
                self.assertIn("tools", result["capabilities"])

    def test_tools_list(self):
        self.server.handle_line(request(2, "tools/list"))
        tools = {tool["name"]: tool for tool in self.last()["result"]["tools"]}
        self.assertEqual(set(tools), {"search", "status"})
        self.assertEqual(tools["search"]["inputSchema"]["required"], ["query"])
        for tool in tools.values():
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_notifications_and_responses_get_no_reply(self):
        for line in ('{"jsonrpc": "2.0", "method": "notifications/initialized"}',
                     '{"jsonrpc": "2.0", "id": 5, "result": {}}', "   "):
            self.server.handle_line(line)
        self.assertEqual(self.out.getvalue(), "")

    def test_errors(self):
        self.server.handle_line("{not json")
        self.assertEqual(self.last(), {"jsonrpc": "2.0", "id": None,
                                       "error": {"code": -32700, "message": "Parse error"}})
        self.server.handle_line('[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]')
        self.assertEqual(self.last()["error"]["code"], -32600)
        self.server.handle_line(request(3, "resources/list"))
        self.assertEqual(self.last()["error"]["code"], -32601)
        self.server.handle_line(call(4, "rm", {}))
        self.assertEqual(self.last()["error"]["code"], -32602)
        self.server.handle_line(request(6, "ping"))
        self.assertEqual(self.last(), {"jsonrpc": "2.0", "id": 6, "result": {}})


class ToolCallTest(McpTestBase):
    def test_search_passes_arguments_after_double_dash(self):
        for query in ("test", "-dash", "--limit", "a;b", "$(id)", "在留 資格", 'say "hi"'):
            with self.subTest(query=query):
                result = self.tool("search", {"query": query, "limit": 5, "offset": 10})
                self.assertFalse(result["isError"])
                self.assertEqual(json.loads(result["content"][0]["text"])["query"], query)
                self.assertEqual(self.calls()[-1],
                                 ["--limit", "5", "--offset", "10", "--", query])

    def test_max_results_and_defaults(self):
        self.tool("search", {"query": "q", "max_results": 200})
        self.assertEqual(self.calls()[-1], ["--max-results", "200", "--", "q"])
        self.tool("search", {"query": "q"})
        self.assertEqual(self.calls()[-1], ["--", "q"])

    def test_status(self):
        for arguments in (None, {}):
            result = self.tool("status", arguments)
            self.assertFalse(result["isError"])
        self.assertEqual(self.calls(), [["--status"], ["--status"]])

    def test_bad_arguments_are_tool_errors_without_running(self):
        for arguments in ({}, {"query": ""}, {"query": 3}, {"query": "q", "limit": 0},
                          {"query": "q", "limit": True}, {"query": "q", "limit": "5"},
                          {"query": "q", "offset": 100001}, {"query": "q", "extra": 1},
                          "query"):
            with self.subTest(arguments=arguments):
                result = self.tool("search", arguments)
                self.assertTrue(result["isError"])
                error = json.loads(result["content"][0]["text"])["error"]
                self.assertEqual((error["type"], error["exit_code"]), ("usage", 2))
        result = self.tool("status", {"query": "q"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.calls(), [], "引数が不正なら nas-search を呼ばない")

    def test_nas_search_errors_pass_through(self):
        result = self.tool("search", {"query": "fail"})
        self.assertTrue(result["isError"])
        error = json.loads(result["content"][0]["text"])["error"]
        self.assertEqual((error["type"], error["exit_code"], error["message"]),
                         ("session_required", 8, "No search session."))

    def test_unexpected_failures(self):
        for query, kind, code in (("crash", "internal", 1), ("garbage", "response", 6)):
            with self.subTest(query=query):
                result = self.tool("search", {"query": query})
                self.assertTrue(result["isError"])
                error = json.loads(result["content"][0]["text"])["error"]
                self.assertEqual((error["type"], error["exit_code"]), (kind, code))
                self.assertNotIn("Traceback", result["content"][0]["text"])

    def test_missing_nas_search(self):
        self.server.nas_search = os.path.join(self.tmp, "nowhere")
        error = json.loads(self.tool("search", {"query": "q"})["content"][0]["text"])["error"]
        self.assertEqual((error["type"], error["exit_code"]), ("config", 2))

    def test_structured_content_only_for_newer_protocols(self):
        self.server.handle_line(request(1, "initialize", {"protocolVersion": "2025-03-26"}))
        self.assertNotIn("structuredContent", self.tool("search", {"query": "q"}))
        self.server.handle_line(request(1, "initialize", {"protocolVersion": "2025-06-18"}))
        result = self.tool("search", {"query": "q"})
        self.assertEqual(result["structuredContent"]["query"], "q")

    def test_english_messages(self):
        mcp.LANG = "en"
        error = json.loads(self.tool("search", {"query": "q", "limit": 0})
                           ["content"][0]["text"])["error"]
        self.assertEqual(error["message"], "limit must be an integer from 1 to 100")

    def test_language_follows_client_settings(self):
        conf = os.path.join(self.tmp, "client.conf")
        with open(conf, "w") as handle:
            handle.write("host=a@b\nlang = en\n")
        self.assertEqual(mcp.peek_language({"UNISEARCH_CLIENT_CONFIG": conf}), "en")
        self.assertEqual(mcp.peek_language({"UNISEARCH_CLIENT_CONFIG": conf,
                                            "UNISEARCH_LANG": "ja"}), "ja")


class LongSearchTest(McpTestBase):
    def test_progress_while_searching(self):
        self.server.progress_interval = 0.3
        self.environ["FAKE_SLEEP"] = "1.2"
        result = self.tool("search", {"query": "slow"}, meta={"progressToken": "tok"})
        self.assertFalse(result["isError"])
        progress = [m for m in self.sent() if m.get("method") == "notifications/progress"]
        self.assertGreaterEqual(len(progress), 2)
        values = [m["params"]["progress"] for m in progress]
        self.assertEqual(values, sorted(set(values)), "progress は増え続ける")
        self.assertTrue(all(m["params"]["progressToken"] == "tok" for m in progress))

    def test_no_progress_without_token(self):
        self.server.progress_interval = 0.3
        self.environ["FAKE_SLEEP"] = "1"
        self.tool("search", {"query": "slow"})
        self.assertEqual([m for m in self.sent() if "method" in m], [])

    def test_ping_answered_and_others_deferred_during_search(self):
        self.environ["FAKE_SLEEP"] = "0.8"
        self.server.inbox.put(request(20, "ping"))
        self.server.inbox.put(request(21, "tools/list"))
        self.server.handle_line(call(7, "search", {"query": "slow"}))
        ids = [m.get("id") for m in self.sent()]
        self.assertEqual(ids, [20, 7], "ping には検索の途中で答える")
        self.assertEqual(len(self.server.deferred), 1)

    def test_cancel_kills_search_and_sends_nothing(self):
        self.environ["FAKE_SLEEP"] = "30"
        self.server.inbox.put(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                          "params": {"requestId": 7, "reason": "user"}}))
        started = time.monotonic()
        self.server.handle_line(call(7, "search", {"query": "slow"}))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(self.sent(), [])

    def test_cancel_of_another_request_is_ignored(self):
        self.environ["FAKE_SLEEP"] = "0.5"
        self.server.inbox.put(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                          "params": {"requestId": 99}}))
        self.tool("search", {"query": "slow"})
        self.assertFalse(self.last()["result"]["isError"])

    def test_timeout(self):
        self.server.call_timeout = 0.5
        self.environ["FAKE_SLEEP"] = "30"
        error = json.loads(self.tool("search", {"query": "slow"})["content"][0]["text"])["error"]
        self.assertEqual((error["type"], error["exit_code"]), ("ssh", 5))


class PackageLayoutTest(unittest.TestCase):
    """pip で入れた形（nas_search_mcp/server.py と nas_search.py）でも nas-search を見つける。"""

    def load_from(self, path):
        loader = importlib.machinery.SourceFileLoader("packaged_server", path)
        spec = importlib.util.spec_from_loader("packaged_server", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_repository_layout(self):
        self.assertEqual(mcp.default_nas_search({}),
                         os.path.join(os.path.dirname(os.path.realpath(MCP)), "nas-search"))

    def test_installed_layout(self):
        tmp = tempfile.mkdtemp(prefix="nsmcp-pkg-")
        try:
            server = os.path.join(tmp, "server.py")
            shutil.copy(MCP, server)
            with open(os.path.join(tmp, "nas_search.py"), "w") as handle:
                handle.write(FAKE_NAS_SEARCH)
            module = self.load_from(server)
            found = module.default_nas_search({})
            self.assertEqual(os.path.realpath(found),
                             os.path.realpath(os.path.join(tmp, "nas_search.py")))
            self.assertEqual(module.default_nas_search({"UNISEARCH_NAS_SEARCH": "/x"}), "/x")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pyproject_maps_existing_files(self):
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as handle:
            text = handle.read()
        for source in ("pypi/__init__.py", "clients/nas-search", "clients/nas-search-mcp"):
            self.assertIn('"%s" = ' % source, text)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, source)), source)


    def test_registry_files_match_the_version(self):
        with open(os.path.join(ROOT, "server.json"), encoding="utf-8") as handle:
            server = json.load(handle)
        package = server["packages"][0]
        self.assertEqual(server["version"], mcp.VERSION)
        self.assertEqual(package["version"], mcp.VERSION)
        self.assertEqual(package["identifier"], "nas-search-mcp")
        self.assertLessEqual(len(server["description"]), 100)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            self.assertIn("mcp-name: " + server["name"], handle.read(),
                          "PyPI の説明（README.md）に Registry の名前が要る")
        with open(os.path.join(ROOT, "glama.json"), encoding="utf-8") as handle:
            self.assertIn("umayado17", json.load(handle)["maintainers"])

class StdioEndToEndTest(McpTestBase):
    """本物のプロセスとして起動し、stdin / stdout でやり取りする。"""

    def test_session(self):
        env = dict(self.environ, UNISEARCH_NAS_SEARCH=self.fake)
        proc = subprocess.Popen([sys.executable, MCP], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            def send(line):
                proc.stdin.write((line + "\n").encode("utf-8"))
                proc.stdin.flush()

            def receive():
                return json.loads(proc.stdout.readline().decode("utf-8"))

            send(request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "t", "version": "0"}}))
            self.assertEqual(receive()["result"]["protocolVersion"], "2025-06-18")
            send('{"jsonrpc": "2.0", "method": "notifications/initialized"}')
            send(request(2, "tools/list"))
            self.assertEqual(len(receive()["result"]["tools"]), 2)
            send(call(3, "search", {"query": "在留資格"}))
            result = receive()["result"]
            self.assertEqual(result["structuredContent"]["query"], "在留資格")
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=10), 0)
            self.assertEqual(proc.stdout.read(), b"", "stdout には MCP の応答だけを出す")
        finally:
            if proc.poll() is None:
                proc.kill()
            for stream in (proc.stdout, proc.stderr):
                stream.close()

    def test_version(self):
        out = subprocess.run([sys.executable, MCP, "--version"], stdout=subprocess.PIPE,
                             check=True).stdout.decode()
        self.assertEqual(out.strip(), "nas-search-mcp " + mcp.VERSION)


if __name__ == "__main__":
    unittest.main()
