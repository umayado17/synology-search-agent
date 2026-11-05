"""許可範囲での絞り込みとページ送り（search_filtered）のテスト。

offset / next_offset は「許可範囲内で何件目か」で数える。範囲外の結果の数が
結果のどこにも表れないこと、ページを続けて取っても抜けや重複がないことを確かめる。
"""
import unittest

from test_synosearch import cli

ALLOWED = ["/archive"]


class CorpusTransport:
    """API の from / size に従って、用意した結果の列から切り出して返す偽物。"""

    def __init__(self, paths, unavail=None):
        self.paths = list(paths)
        self.unavail = unavail if unavail is not None else []
        self.calls = []

    def call(self, url, params):
        start, size = int(params["from"]), int(params["size"])
        self.calls.append((start, size))
        hits = [{"SYNOMDFSName": p.rsplit("/", 1)[-1], "SYNOMDSharePath": p}
                for p in self.paths[start:start + size]]
        return {"success": True, "data": {"total": len(self.paths), "has_error": False,
                                          "unavail": self.unavail, "hits": hits}}


def allowed(n):
    return ["/archive/f%03d" % i for i in range(n)]


def interleave(allowed_paths, outside_per_item):
    """許可範囲内の1件ごとに、範囲外を outside_per_item 件ずつ挟む。"""
    paths = []
    for index, path in enumerate(allowed_paths):
        paths.extend("/homes/other/s%d_%d" % (index, j) for j in range(outside_per_item))
        paths.append(path)
    return paths


def walk(transport, limit, checkpoints=None):
    """next_offset をたどって全ページを取り、(各ページの結果, 全件) を返す。"""
    pages, offset = [], 0
    while offset is not None:
        result = cli.search_filtered(transport, "sid", "q", limit, offset, ALLOWED, checkpoints)
        pages.append(result)
        offset = result["next_offset"]
    names = [hit["share_path"] for page in pages for hit in page["hits"]]
    return pages, names


def visible(result):
    """エージェントから見える値（hits の中身以外）。"""
    return {key: value for key, value in result.items() if key != "hits"}


class FilteredPagingTest(unittest.TestCase):
    def test_walking_pages_returns_every_allowed_hit_once(self):
        expected = allowed(130)
        for limit in (1, 7, 20, 100):
            for checkpoints in (None, {}):
                with self.subTest(limit=limit, cache=checkpoints is not None):
                    transport = CorpusTransport(interleave(expected, 2))
                    pages, names = walk(transport, limit, checkpoints)
                    self.assertEqual(names, expected)
                    for page in pages:
                        self.assertLessEqual(page["returned"], limit)
                        self.assertIsNone(page["total"])
                        self.assertIsNone(page["unavailable"])

    def test_offsets_count_allowed_hits_only(self):
        transport = CorpusTransport(interleave(allowed(30), 3))
        result = cli.search_filtered(transport, "sid", "q", 10, 10, ALLOWED)
        self.assertEqual([h["share_path"] for h in result["hits"]], allowed(30)[10:20])
        self.assertEqual(result["next_offset"], 20)

    def test_outside_hits_do_not_change_anything_visible(self):
        """範囲外が0件でも大量でも、エージェントに見える値は同じ（件数を推測できない）。"""
        base = allowed(25)
        for limit, offset in ((10, 0), (10, 20), (30, 0), (5, 25)):
            with self.subTest(limit=limit, offset=offset):
                results = [
                    cli.search_filtered(CorpusTransport(paths, unavail=["/homes"]), "sid", "q",
                                        limit, offset, ALLOWED)
                    for paths in (base, interleave(base, 1), interleave(base, 7),
                                  base + ["/homes/other/x%d" % i for i in range(150)])]
                for other in results[1:]:
                    self.assertEqual(visible(other), visible(results[0]))
                    self.assertEqual(other["hits"], results[0]["hits"])

    def test_no_allowed_hits_looks_the_same_as_no_hits(self):
        empty = cli.search_filtered(CorpusTransport([]), "sid", "q", 20, 0, ALLOWED)
        outside = cli.search_filtered(
            CorpusTransport(["/homes/other/%d" % i for i in range(250)]), "sid", "q", 20, 0,
            ALLOWED)
        self.assertEqual(visible(empty), visible(outside))
        self.assertEqual((outside["returned"], outside["next_offset"]), (0, None))

    def test_checkpoint_resumes_instead_of_rescanning(self):
        transport = CorpusTransport(interleave(allowed(300), 1))
        checkpoints = {}
        cli.search_filtered(transport, "sid", "q", 100, 0, ALLOWED, checkpoints)
        transport.calls.clear()
        result = cli.search_filtered(transport, "sid", "q", 100, 100, ALLOWED, checkpoints)
        self.assertEqual(result["hits"][0]["share_path"], "/archive/f100")
        self.assertEqual(transport.calls[0][0], 201, "API の先頭から読み直さない（f100 の位置から）")

    def test_checkpoint_is_not_used_for_earlier_offsets(self):
        transport = CorpusTransport(interleave(allowed(50), 1))
        checkpoints = {}
        cli.search_filtered(transport, "sid", "q", 20, 20, ALLOWED, checkpoints)
        result = cli.search_filtered(transport, "sid", "q", 5, 0, ALLOWED, checkpoints)
        self.assertEqual([h["share_path"] for h in result["hits"]], allowed(50)[:5])

    def test_checkpoints_are_bounded(self):
        transport = CorpusTransport(allowed(3))
        checkpoints = {}
        for index in range(cli.SCAN_CACHE_SIZE + 10):
            cli.search_filtered(transport, "sid", "q%d" % index, 5, 0, ALLOWED, checkpoints)
        self.assertEqual(len(checkpoints), cli.SCAN_CACHE_SIZE)

    def test_scan_limit_error_and_resume(self):
        limit_raw = cli.SCAN_PAGE_SIZE * cli.MAX_SCAN_PAGES
        paths = ["/homes/other/%d" % i for i in range(limit_raw + 50)] + allowed(3)
        checkpoints = {}
        with self.assertRaises(cli.CliError) as ctx:
            cli.search_filtered(CorpusTransport(paths), "sid", "q", 10, 0, ALLOWED, checkpoints)
        self.assertEqual(ctx.exception.kind, "scan_limit")
        self.assertEqual(ctx.exception.exit_code, cli.EXIT_API)
        result = cli.search_filtered(CorpusTransport(paths), "sid", "q", 10, 0, ALLOWED,
                                     checkpoints)
        self.assertEqual([h["share_path"] for h in result["hits"]], allowed(3))

    def test_time_budget_stops_scanning(self):
        ticks = iter(range(0, 1000, 30))  # 呼ぶたびに30秒進む時計
        transport = CorpusTransport(["/homes/other/%d" % i for i in range(1000)] + allowed(1))
        with self.assertRaises(cli.CliError) as ctx:
            cli.search_filtered(transport, "sid", "q", 10, 0, ALLOWED, {},
                                clock=lambda: next(ticks))
        self.assertEqual(ctx.exception.kind, "scan_limit")
        self.assertLess(len(transport.calls), cli.MAX_SCAN_PAGES)

    def test_broker_scan_limit_does_not_count_as_failure(self):
        limit_raw = cli.SCAN_PAGE_SIZE * cli.MAX_SCAN_PAGES
        transport = CorpusTransport(["/homes/other/%d" % i for i in range(limit_raw + 10)])
        broker = cli.Broker(transport, "sid", ALLOWED, 3600, 0)
        for _ in range(cli.MAX_CONSECUTIVE_FAILURES + 1):
            response = broker.handle({"op": "search", "query": "q", "limit": 10})
        self.assertTrue(response["ok"], "2回目からは続きを読み、最後まで届く")
        self.assertEqual(broker.failures, 0)
        self.assertFalse(broker.stopping)


if __name__ == "__main__":
    unittest.main()
