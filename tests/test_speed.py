"""速さのための工夫のテスト（標準ライブラリのみ、NAS・ssh 不要）。

- エージェント側: ssh の接続を使い回す（ControlMaster）
"""
import os
import shutil
import tempfile
import unittest

from test_client import ClientTestBase, client


class ConnectionReuseTest(ClientTestBase):
    def options(self, argv):
        return argv[argv.index("-T") + 1:-2]

    def test_reuse_is_on_by_default(self):
        code, _, err, runner = self.run_client(["q"], [(0, {"hits": []}, "")])
        self.assertEqual(code, 0, err)
        options = self.options(runner.calls[0])
        self.assertIn("ControlMaster=auto", options)
        self.assertIn("ControlPersist=%d" % client.CONTROL_PERSIST, options)
        path = [o for o in options if o.startswith("ControlPath=")][0][len("ControlPath="):]
        self.assertTrue(path.endswith("-%C"))
        self.assertLessEqual(len(path.encode()) - 2 + 40, client.MAX_SOCKET_PATH)

    def test_turn_off(self):
        with open(self.env["UNISEARCH_CLIENT_CONFIG"], "w") as handle:
            handle.write("reuse_connection = no\n")
        _, _, _, runner = self.run_client(["q"], [(0, {"hits": []}, "")])
        self.assertFalse(any(o.startswith("Control") for o in self.options(runner.calls[0])))

    def test_bad_value(self):
        with open(self.env["UNISEARCH_CLIENT_CONFIG"], "w") as handle:
            handle.write("reuse_connection = maybe\n")
        code, _, _, runner = self.run_client(["q"], [])
        self.assertEqual(code, client.EXIT_USAGE)
        self.assertEqual(runner.calls, [])

    def test_other_key_gets_another_socket(self):
        short = tempfile.mkdtemp(dir="/tmp")  # macOS の既定の一時フォルダはパスが長すぎる
        try:
            first = client.connection_options(15, "/keys/a", True, ssh_dir=short)
            second = client.connection_options(15, "/keys/b", True, ssh_dir=short)
        finally:
            shutil.rmtree(short, ignore_errors=True)
        self.assertIn("ControlMaster=auto", first)
        self.assertNotEqual(first, second)

    def test_no_reuse_without_ssh_dir_or_with_long_path(self):
        missing = os.path.join(self.tmp, "no-such-dir")
        self.assertEqual(client.connection_options(15, "/k", True, ssh_dir=missing),
                         ["-o", "ConnectTimeout=15"])
        deep = tempfile.mkdtemp(prefix="x" * 60, dir=self.tmp)
        try:
            self.assertEqual(client.connection_options(15, "/k", True, ssh_dir=deep),
                             ["-o", "ConnectTimeout=15"])
        finally:
            shutil.rmtree(deep, ignore_errors=True)

    def test_paging_reuses_the_same_socket(self):
        replies = [(0, {"hits": [{"share_path": "/a/%d" % i} for i in range(100)],
                        "next_offset": 100}, ""),
                   (0, {"hits": [{"share_path": "/a/x"}], "next_offset": None}, "")]
        code, _, err, runner = self.run_client(["q", "--max-results", "150"], replies)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.options(runner.calls[0]), self.options(runner.calls[1]))


if __name__ == "__main__":
    unittest.main()
