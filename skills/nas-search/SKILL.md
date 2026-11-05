---
name: nas-search
description: Search the user's Synology NAS archive (old PC backups indexed by Universal Search) by file name and full text, and get JSON results with paths, sizes and dates. Use when the user asks to find, locate or list files or documents in their NAS / archive / old backups / 昔のPCのバックアップ, or asks "NASにあった〇〇の資料を探して". Read-only; it cannot open, change or delete files.
---

# nas-search: NAS のアーカイブを検索する

`nas-search` は、NAS の Universal Search（全文検索インデックス）を SSH 経由で検索し、結果を JSON で返すコマンドです。対象は、本人が許可した共有フォルダ（例 `/archive`）の中だけです。

## できること / できないこと

- できる: ファイル名と本文（インデックス済みのもの）での検索。結果はファイル名・パス・サイズ・更新日時・フォルダかどうか
- できない: ファイルの中身を読む、ダウンロード、変更、削除。許可範囲外の検索。NAS 上での任意のコマンド実行
- 本文の抜粋は返りません。中身が必要なら、見つかったパスをユーザーに伝えて判断を仰いでください

## 使い方

```bash
nas-search "検索語"                         # 既定20件
nas-search "在留資格 申請" --limit 50         # 空白を含む語は引用符で1つにする
nas-search "検索語" --offset 20              # 次のページ（前回の next_offset を使う）
nas-search "検索語" --max-results 200        # ページを自動で送って最大200件集める
nas-search --limit 10 -- "-で始まる語"        # オプションは -- より前に置く
nas-search --status                          # 検索セッションの状態（残り時間など）
```

- 検索語はシェルを通らずにそのまま NAS に届きます（`;` や `$()` も単なる文字）
- 1回の検索は数秒〜十数秒かかることがあります。むやみに連続で呼ばず、`--max-results` でまとめて取ってください
- 語はファイル名の前方一致に強く重み付けされ、本文一致も含みます。見つからないときは語を短くする・別表記（全角/半角、英語/日本語）を試す

## 結果（stdout、JSON 1行）

```json
{"schema_version": 1, "query": "test", "offset": 0, "limit": 20, "total": null,
 "returned": 20, "next_offset": 20, "has_error": false, "unavailable": [],
 "hits": [{"name": "test.pdf", "share_path": "/archive/Drive/test.pdf",
           "path": "/volume1/archive/Drive/test.pdf", "extension": "pdf",
           "size": 12345, "is_dir": false, "modified": 1396685721,
           "modified_utc": "2014-04-05T08:15:21Z", "readable": true}]}
```

- `share_path` をユーザーへの説明に使う（`path` は NAS 内部の絶対パス）
- `total` は常に `null`（範囲外の件数を漏らさないため）。続きがあるかは `next_offset` が `null` でないかで判断
- `returned` が `limit` より少なくても、`next_offset` があれば続きがある（範囲外の結果を除いた分だけ減る）
- `modified` は UNIX 秒、`modified_utc` は UTC。日本時間で伝えるときは +9 時間

## 終了コードと対応

| コード | 意味 | エージェントの対応 |
|---|---|---|
| 0 | 成功 | 結果を使う（0件もあり得る） |
| 2 | 使い方の誤り | stderr の `message` を読んで引数を直す。同じ呼び出しを繰り返さない |
| 4 | NAS の検索 API がエラー | 1回だけ再試行。続くならユーザーに報告 |
| 5 | SSH 接続・応答なし | NAS やネットワークが止まっている可能性。ユーザーに報告 |
| 6 | 応答形式が想定外 | ユーザーに報告（NAS の更新などで API が変わった可能性） |
| 8 | 検索セッションが無い・期限切れ | **ユーザーに「NAS の検索セッションを開始してください」と頼む**。自分でログインしようとしない |

エラーは stderr に JSON 1行（`{"error": {"type", "message", "exit_code"}}`）で出ます。

## 守ること

- パスワードや認証コードを求めたり、扱ったりしない。セッション開始は必ず本人が行う
- 許可範囲外を探すために、別の手段（他の SSH 鍵、ファイル共有など）を試さない
- 検索結果のファイル名・パスには個人情報が含まれ得る。必要な範囲だけ引用し、外部サービスに送らない

## 設定（人間が一度だけ）

接続先と鍵は環境変数か `~/.config/synosearch/client.conf` に置きます（詳細はリポジトリの `docs/agent-setup.md`）。

```
host=<ssh-user>@<nas-host>
key=~/.ssh/synosearch_agent
```

未設定だと終了コード 2（`type: config`）になります。
