# unisearch CLI 仕様（v0.7.0 / JSON schema_version 1）

この文書が CLI の外部契約です。変更時は `tests/test_unisearch.py` を同時に更新してください。

## 書式

```
unisearch QUERY [--limit N] [--offset N] [--json | --raw] [--user NAME] [--timeout SEC] [--allow-prefix PATH ...]
unisearch --check-api
unisearch session start --allow-prefix PATH [--user NAME] [--max-age 8h] [--keepalive 10m] [--timeout SEC] [--foreground]
unisearch session status [--json]
unisearch session stop
unisearch --agent-gateway        # authorized_keys の command= 専用。SSH_ORIGINAL_COMMAND を解釈
unisearch --version | --help
```

| オプション | 既定 | 制約 / 説明 |
|---|---|---|
| `QUERY` | 必須 | 空文字・空白のみは不可。APIへはJSON文字列として送る |
| `--limit` | 10 | 1〜100（API `size`） |
| `--offset` | 0 | 0以上。`--allow-prefix` なしでは API の `from`。ありでは「許可範囲内で何件目から」（下の「範囲を絞るときのページ送り」） |
| `--json` | off | 正規化済みJSONを stdout へ1行で出力 |
| `--raw` | off | APIの `data` を無加工で出力。**実機の応答形式検証用**で、形式は保証しない |
| `--user` | 対話入力 | DSMユーザー名。パスワードとOTPは常に対話入力 |
| `--timeout` | 設定ファイル、無ければ 45 | HTTPタイムアウト秒（0 < x ≤ 600） |
| `--allow-prefix` | なし | `/共有名[/サブパス]`。この配下（`share_path` が一致または `PATH/` で始まる）の結果だけを出す。複数指定可。`/` 単独・`..` を含むものは引数エラー。`--raw` とは併用不可。DSM の権限設定に重ねる多層防御 |
| `--no-session` | off | 検索セッションが動いていても使わず、対話ログインで検索 |
| `--check-api` | – | 認証なしで `SYNO.API.Info` を照会し、必要APIの有無・バージョン範囲をJSONで出力 |

パスワード・OTP は **`/dev/tty` からのみ**読みます。聞く前に、どの DSM アカウントの入力かを表示します（例 `search-agent の DSM パスワード:`）。SSH のログインに使うアカウントと区別するためです。引数・環境変数・stdin からは受け付けません。
TTY が無い場合（例: `ssh host unisearch ...`）は通信を一切行わず終了コード 7 で終了します。

## stdout（成功時）

`--json`（型は 2026-11-04 実機 DSM 7.4.1 の応答で確定）:

```json
{"schema_version": 1, "query": "test", "offset": 0, "limit": 5, "total": 3666, "returned": 5, "next_offset": 5,
 "has_error": false, "unavailable": [],
 "hits": [{"name": "Test", "share_path": "/share/dir/Test", "path": "/volume1/share/dir/Test",
           "extension": "", "size": 4096, "is_dir": true, "modified": 1244279630,
           "modified_utc": "2009-06-06T09:13:50Z", "readable": true}]}
```

| キー | 型 | 由来（API） | 備考 |
|---|---|---|---|
| `total` | int\|null | `data.total` | 総ヒット数。範囲を絞るとき（`--allow-prefix`・検索セッション・入口）は常に `null`。インデックス作成中は短時間でも変動する |
| `returned` | int | `len(hits)` | 絞り込み後の件数 |
| `next_offset` | int\|null | 下記 | 続きがあり得るときの次の `--offset`。無ければ `null`。絞り込みなし: API が `limit` 件返したとき `offset + 件数`。絞り込みあり: `offset + returned` |
| `has_error` | bool\|null | `data.has_error` | 意味は未確認。そのまま渡す |
| `unavailable` | array\|null | `data.unavail` | 意味は未確認。絞り込みなしではそのまま渡す。絞り込みありでは常に `null`（範囲外の情報を含み得るため） |
| `hits[].name` | str\|null | `SYNOMDFSName` | |
| `hits[].share_path` | str\|null | `SYNOMDSharePath` | `/<共有フォルダ名>/.../<名前>`（名前を含む完全パス） |
| `hits[].path` | str\|null | `SYNOMDPath` | `/volume1/...` の NAS 内絶対パス。要求フィールド外だが実機で返る |
| `hits[].extension` | str\|null | `SYNOMDExtension` | フォルダは `""` |
| `hits[].size` | int\|null | `SYNOMDFSSize` | バイト。API は文字列（`"4096"`）で返すので整数化 |
| `hits[].is_dir` | bool\|null | `SYNOMDIsDir` | API は `"y"`/`"n"` |
| `hits[].modified` | int\|null | `SYNOMDContentModificationDate` | UNIX 秒。API は文字列 |
| `hits[].modified_utc` | str\|null | 同上 | `YYYY-MM-DDTHH:MM:SSZ` |
| `hits[].readable` | bool\|null | `readable` | |

変換できない値・欠けたキーは `null`。`SYNOMDOwnerUserID`・`additional` は出力しない（必要なら `--raw`）。互換性のない変更をするときは `schema_version` を上げます。

検索結果はログインしたDSMユーザーの権限の範囲で返ると考えられます（administrators 所属ユーザーでは他ユーザーの `/homes` 配下も返った）。

### 範囲を絞るときのページ送り

`--allow-prefix`・検索セッション・入口では、結果を許可範囲内だけに絞ります。このとき:

- `offset` と `next_offset` は**許可範囲内で何件目か**で数えます。範囲外の結果は数えません
- `returned` は `limit` 以下です。`next_offset` が `null` でなければ、そこから続きを取れます（索引が変わらない間は、抜け・重複なし）
- `total` と `unavailable` は常に `null` です。範囲外の結果がいくつあっても、出力は同じになります
- NAS の API からは100件ずつ読みます。1回の呼び出しで読むのは最大2,000件・約40秒までです。それでも許可範囲内が1件も見つからないときは `scan_limit` エラー（終了コード 4）になります
- 検索セッションは、検索語ごとに読んだ位置を覚えます（最近の32語、メモリのみ）。続きのページや `scan_limit` の後の再呼び出しは、その位置から読みます
- **索引が変わると、抜けや重複が起きえます。** 続きのページは、前のページで読み終えた API の位置から読みます。ページの間にファイルの追加・削除や索引の更新があると、結果の順番がずれて、同じ結果が2回出たり、1件飛ばしたりすることがあります。DSM の API には、ある時点の結果を固定して続きを読む仕組みがないため、範囲を絞らない検索でも同じです。必要なら `share_path` で重複を除き、大事な場合は検索し直してください
- セッションを使う対話検索でさらに `--allow-prefix` を付けると、セッションの結果から除くだけです。そのため `returned` が減ることがあります

テキスト出力（既定）は人間向けで、形式は保証しません。制御文字は `\xNN` に置換されます。

`--check-api`:

```json
{"schema_version": 1, "ok": true, "apis": {"SYNO.API.Auth": {"present": true, "required_version": 6,
  "path": "entry.cgi", "min_version": 1, "max_version": 7, "supported": true}, "...": {}}}
```

## stderr と終了コード

`--json` / `--raw` / `--check-api` 時、エラーは stderr に JSON 1行:

```json
{"schema_version": 1, "error": {"type": "auth", "message": "認証失敗（コード 404）：…", "api_code": 404, "exit_code": 3}}
```

それ以外は `エラー: <message>`。ログアウト失敗は終了コードに影響せず、stderr に警告を出すのみ。

| 終了コード | type | 意味 |
|---|---|---|
| 0 | – | 成功（`--check-api` は全APIが対応バージョンのとき） |
| 1 | `internal` | 想定外の例外（メッセージ本文は出さず型名のみ） |
| 2 | – | 引数エラー（argparse、テキストのみ） |
| 3 | `auth` | ログイン失敗。`api_code` は DSM のコード（ヒントは参考で断定しない） |
| 4 | `api` / `scan_limit` | 検索APIが `success:false`（`119`/`120` 等の意味は前提に依存するため解釈しない）。`scan_limit` は範囲外の結果が多く読み進められなかったとき |
| 5 | `network` | 接続不可・TLS・HTTPエラー・タイムアウト |
| 6 | `response` | 応答がJSONでない/形式が想定外、`--check-api` で非対応 |
| 7 | `no_tty` | 対話入力に必要なTTYが無い（検索セッションも無い） |
| 8 | `session_required` | 検索セッションが無い・期限切れ（入口・`session status/stop`） |
| 9 | `session_running` / `state` | すでにセッションが動いている、状態ディレクトリが不正 |
| 130 | `interrupted` | Ctrl-C |

## セキュリティ上の不変条件（テストで固定）

- パスワード・OTP・SID は stdout/stderr に出ない
- ログイン成功後は、検索失敗・中断時も必ずログアウトを試みる
- 接続先は `https://127.0.0.1:5001` 固定。TLS 検証省略はループバック限定の前提

## 検索セッション（v0.4.0〜）

本人が TTY で一度ログインし、期限までの間「検索」だけを受け付ける待機役を動かします。設計は `docs/design-noninteractive-auth.md`、導入は `docs/agent-setup.md`。

- `session start`: ユーザー名・パスワード・OTP を TTY から読みログイン → バックグラウンドに回る（`--foreground` で前面実行）。`--allow-prefix` は必須で、結果は常にこの範囲に絞られる。`--max-age` は 1m〜90d（既定 8h）、`--keepalive` は 0 または 1m〜max-age（既定 10m、`keepalive` という語で1件検索してセッションを保つ）。既定値は設定ファイルで変えられる。24時間を超える期間では、DSM 側で先に切れる可能性を表示する（DSM 側のログインがその期間もつかは未確認。切れた場合は検索・keepalive が3回続けて失敗した時点で終了する）
- 状態は `~/.unisearch/`（0700）。`broker.sock`（0600）と `broker.log`（0600）。`UNISEARCH_STATE_DIR` で変更可
- SID はプロセスのメモリにだけ置き、ファイル・ログ・応答に出さない。ログに検索語は書かない（イベント名と件数のみ）
- Linux では接続元の UID を `SO_PEERCRED` で確認し、同じユーザー以外を拒否
- 期限到来・`session stop`・SIGTERM・DSM 呼び出しの3回連続失敗で、ログアウトしてソケットを消して終了（`scan_limit` は失敗に数えない）
- セッション中、`unisearch QUERY` はパスワードを聞かずにセッションを使う（`--no-session`・`--raw`・`--user` 指定時は対話）。クライアントの `--allow-prefix` は範囲を狭めることしかできない
- `session status` の JSON: `{"schema_version":1,"session":{"started_utc","expires_utc","remaining_seconds","allow_prefixes","searches","consecutive_failures","last_error"}}`

## 設定ファイル（NAS 側、v0.6.0）

`~/.config/unisearch/server.conf`（環境変数 `UNISEARCH_CONFIG` で変更可）。無くても既定値で動きます。形式は `名前 = 値`、`#` から始まる行はコメントです。

```
# 例
session_max_age = 7d
session_keepalive = 10m
http_timeout = 45
scan_time_budget = 40
scan_max_pages = 20
```

| 名前 | 既定 | 範囲（コードで固定） | 意味 |
|---|---|---|---|
| `session_max_age` | 8h | 1m〜90d | 検索セッションの有効期間（`--max-age` の既定値） |
| `session_keepalive` | 10m | 0 または 1m〜有効期間 | セッションを保つ検索の間隔（`--keepalive` の既定値） |
| `http_timeout` | 45 | 1〜120 秒 | DSM API 1回の待ち時間（`--timeout` の既定値） |
| `scan_time_budget` | 40 | 5〜120 秒 | 範囲を絞るとき、1回の検索で読み進める時間の上限 |
| `scan_max_pages` | 20 | 1〜100 | 同じく、1回の検索で API を呼ぶ回数の上限（1回100件） |

- 優先順: コマンドライン引数 ＞ 設定ファイル ＞ 既定値
- 1回の検索の最長は、検索セッションを始めたときの値で `scan_time_budget + http_timeout + 10` 秒です（既定 95 秒）。`session start` が開始時に表示します。`--timeout` で `http_timeout` を上書きした場合は、その値で計算します
- 入口（`--agent-gateway`）は設定ファイルを読みません。検索セッションの応答を、設定でとれる最大値から決めた 250 秒（120 + 120 + 10）まで待ちます。`--timeout` の上書きや開始後の設定の書き換えで、入口が先に待ちをやめることはありません
- エージェント側の待ち時間（`nas-search` の `timeout`）の既定は 260 秒で、入口の 250 秒より長くしてあります。NAS 側の設定だけを変えても、エージェント側が先に切ることはありません。短くする場合は、開始時に表示された最長の秒数より長くしてください
- 範囲外の値・知らない名前・書式の誤りは終了コード 2（`type: config`）。パスワード・OTP・SID・トークンなどを表す名前は書けません（拒否します）
- 設定を変えたら、検索セッションを始め直してください（動いているセッションは開始時の値のまま）
- 応答サイズの上限（16MB）、検索語の長さ（1000文字）、offset の上限（100000）などはコードで固定です

## AIエージェント用の入口（`--agent-gateway`）

`authorized_keys` の `command=` から起動され、`SSH_ORIGINAL_COMMAND` の次の2形式だけを受け付けます。シェルは起動しません。

```
search <検索語> [--limit N] [--offset N]     → stdout に検索結果 JSON（上記 --json と同じ形、total は null）
status                                        → stdout に {"schema_version":1,"session":{...}}
```

- 検索語に空白を含むときは引用符で囲む。`-` で始まる語は `search -- -語`
- それ以外（`--raw`・`--allow-prefix`・`--user`・`stop`・未知の語・`;` など）はすべて終了コード 2
- エラーは stderr に JSON 1行。セッションが無ければ終了コード 8（`session_required`）。資格情報の入力は求めない

## エージェント側コマンド `clients/nas-search`（v0.4.0）

入口（`--agent-gateway`）を ssh で呼ぶ薄いラッパー。出力・終了コードは入口と同じで、加えて:

- `--max-results N`（1〜1000）: ページを送り、最大 N 件を1つの JSON にまとめる（`pages_fetched` を追加、最大20ページ）。1回に頼む件数は `min(100, 残り件数)` なので、取った結果を捨てない。最後の `next_offset` から続きを取れる
- `--status` は検索語・`--max-results`・`--offset` と併用できない（exit 2）
- 設定: `--host`/`--key` > 環境変数 `UNISEARCH_HOST`/`UNISEARCH_KEY` > `~/.config/unisearch/client.conf`（`UNISEARCH_CLIENT_CONFIG` で変更可）。未設定は exit 2（`type: config`）
- `client.conf` に書ける名前: `host`、`key`、`timeout`（1回の呼び出しの上限秒、5〜600、既定 260。`--timeout` が優先）、`connect_timeout`（ssh の接続待ち秒、1〜120、既定 15）。知らない名前・パスワードなどは exit 2
- ssh 自体の失敗（ssh の終了コード 255）は exit 5（`type: ssh`）。パスワード認証は試さない
- 送る文字列は `search --limit N --offset N -- <shlex.quote(検索語)>`
