# 実機での確認手順（DSM 7.4 で確認）

NAS 上の設定・ファイルは変更しません。CLI は `scripts/remote-run.sh` でスクリプト本体をコマンドラインに埋め込んで実行するため、NAS にファイルを置きません（`~/.local/bin` への配置は検証完了後の別判断）。

パスワード・OTP は **NAS 側 TTY に本人が直接入力**します。エージェントやログには渡しません。

## 0. 事前準備（Mac 側、初回のみ）

ホスト鍵を信頼する前に、既知の安全な経路（DSM に直接ログイン済みのSSH端末など）で指紋を照合します。

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub   # NAS 側で実行
```

一致したら Mac 側で一度だけ対話的に接続して `known_hosts` に登録します（`yes` と入力）。

SSH がパスワード認証だけの場合、以下の非対話の手順（`BatchMode=yes`）は `Permission denied` になります。そのときは先に本人が接続を1本開き、ControlMaster で共有すると、パスワードを渡さずに実行できます。

## 1. SSH 接続（非対話）

```bash
ssh -o BatchMode=yes <nas-host> 'id; python3 --version; grep -E "productversion|buildnumber" /etc.defaults/VERSION'
```

期待: 一般ユーザーの `id`、Python 3.8 以上、`productversion="7.4"`。鍵認証が無ければ BatchMode で失敗します（それ自体は記録すべき結果）。

## 2. API の存在確認（認証なし・非対話）

```bash
scripts/remote-run.sh <nas-host> --check-api; echo "exit=$?"
```

期待: `"ok": true`、exit=0。Auth は v1〜7、Search は v1 / `entry.cgi`。

## 3. TTY なしで即失敗すること（非対話）

```bash
scripts/remote-run.sh <nas-host> test --json; echo "exit=$?"
```

期待: 入力待ちで固まらず、stderr に `"type": "no_tty"`、exit=7。NAS へのAPI呼び出しは発生しない。

## 4. 対話検索（本人が入力）

```bash
scripts/remote-run.sh -t <nas-host> test --limit 5
scripts/remote-run.sh -t <nas-host> test --limit 5 --json; echo "exit=$?"
scripts/remote-run.sh -t <nas-host> test --limit 3 --raw > raw-test.json
```

- 1回ごとに OTP が必要です（毎回ログイン→ログアウト）。同じ OTP の再利用は失敗し得ます
- 期待: 総件数が GUI の検索結果と同程度、exit=0、stdout は JSON 1行
- `raw-test.json` で以下を確認し、記録は `docs/verification.md` に追記（**パスやファイル名に機微情報が含まれる場合は伏せ字にしてからコミット**）
  - `hits` 要素がフラットか、`SYNOMD*` キーがそのまま並ぶか
  - `SYNOMDSharePath` が `/共有名/...` 形式か、ファイル名を含むか
  - `SYNOMDContentModificationDate` の型（epoch秒/ミリ秒/文字列）
  - `SYNOMDFSSize` / `SYNOMDIsDir` の型
- その後 `tests/fixtures/search_response_sample.json` を伏せ字化した実形式に置き換え、`cli-spec.md` の型を確定

## 5. エラー系（本人が入力）

| 操作 | 期待 |
|---|---|
| パスワードを誤入力 | exit=3、`api_code` 400 前後 |
| OTP を誤入力 | exit=3、`api_code` 404 前後 |
| 検索中に Ctrl-C | exit=130、ログアウト警告が出ないこと |
| `--limit 0` | exit=2、NAS 呼び出しなし |

コード値は DSM 側の前提で変わり得るため、観測値をそのまま記録し原因は断定しないこと。

## 6. v0.7.0 リリース候補の確認

v0.4.0 の後に変えたところ（ページ送り、設定ファイル、待ち時間、名前の変更）を、導入した形で確かめます。手順は [agent-setup.md](agent-setup.md) で導入した後に行います。パスワードと2段階認証のコードは、本人が入力します。

ここで使う検索語・件数・パスは、記録するときに伏せ字にします。結果を保存したファイル（`page*.json` など）はコミットしません。

### 6-1. 導入

| 確認 | 期待 |
|---|---|
| `~/.local/bin/unisearch --version` | `unisearch 0.7.0` |
| `nas-search --version`（エージェント側） | `nas-search 0.4.0` |
| `~/.local/bin/unisearch --check-api` | `"ok": true`、exit 0 |
| `authorized_keys` のエージェント用の行 | 1行だけで、`command=` が `unisearch` を指す |
| `session start` の表示 | パスワードと2段階認証の入力欄に DSM アカウント名が出る。「1回の検索は最長 95 秒」と出る（既定の設定のとき） |
| `nas-search --status` | exit 0。`remaining_seconds` が有効期間に合う |

### 6-2. 権限の分離（新しい版で）

検索専用アカウントで始めた検索セッションで、次を確かめます。

- 許可していない共有フォルダの中のファイル名で検索して、0件になる
- 許可したフォルダのファイルは見つかる
- [verification.md](verification.md) の「検索セッションと SSH 入口」で断られたコマンド（`ls`、`search test; ls`、`--allow-prefix` 付き、ポート転送など）が、新しい版でも exit 2 などで断られる

### 6-3. ページ送り（索引が変わらないとき）

結果が数百件ある検索語を選びます。索引の状態が「Finished」で、検索の間にファイルを変えないようにします。

```bash
# まとめて取る
nas-search "<検索語>" --max-results 300 > all.json
# 20件ずつ取る（next_offset をたどる）
off=0; n=1
while [ -n "$off" ]; do
  nas-search "<検索語>" --limit 20 --offset "$off" > "page$n.json" || break
  off=$(python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["next_offset"]; print("" if v is None else v)' "page$n.json")
  n=$((n+1)); [ "$n" -gt 20 ] && break
done
# 重複がないか、まとめて取った結果と同じか
python3 - <<'PY'
import glob, json
pages = sorted(glob.glob("page*.json"), key=lambda p: int(p[4:-5]))
paged = [h["share_path"] for p in pages for h in json.load(open(p))["hits"]]
whole = [h["share_path"] for h in json.load(open("all.json"))["hits"]]
print("paged", len(paged), "unique", len(set(paged)))
print("whole", len(whole), "unique", len(set(whole)))
print("same order:", paged[:len(whole)] == whole[:len(paged)])
PY
```

期待: `unique` が件数と同じ（重複なし）、`same order: True`。

### 6-4. ページ送り（索引が更新されている最中）

ページの間に索引が変わると、重複や取りこぼしが起きえます（[cli-spec.md](cli-spec.md#範囲を絞るときのページ送り)）。実際にどうなるかを記録します。**テスト用のフォルダにファイルを置く作業は、本人がします。** 終わったら、置いたファイルを消します。

1. 許可した共有フォルダの中に、テスト用のフォルダを作る（索引の対象の中）
2. まず1ページ目を取る: `nas-search "<検索語>" --limit 20 > before.json`
3. テスト用のフォルダに、名前に検索語を含むファイルを10個ほど作る
4. Universal Search の設定の「Indexed Folder List」で状態が「Finished」に戻るのを待つ（かかった時間も記録する）
5. 2ページ目を取る: `nas-search "<検索語>" --limit 20 --offset 20 > after.json`
6. `before.json` と `after.json` の `share_path` に重なりがあるか、新しいファイルがどちらかに出たかを記録する
7. 同じことを、ファイルを消す場合でも試す
8. テスト用のファイルとフォルダを消す

結果は「重複 N 件・取りこぼしの疑い N 件」のように数で記録し、原因は断定しません。

### 6-5. 異常のとき

| 操作 | 期待 |
|---|---|
| `session start --max-age 2m --keepalive 0` で始め、3分後に検索 | exit 8（`session_required`）。セッションは自分で終わっている |
| 検索の途中でエージェント側のネットワークを切る | `nas-search` が exit 5。NAS 側の検索セッションは動き続け、`--status` の `consecutive_failures` は増えない |
| `nas-search --timeout 5` で、時間のかかる検索をする | exit 5（応答が 5 秒ない）。検索セッションは動き続ける |
| DSM の画面で、検索専用アカウントの接続を切る | 3回続けて検索が失敗すると、セッションが終わる（exit 8）。`last_error` の値をそのまま記録する |
| NAS を再起動する | セッションは消える。入口は exit 8。人が始め直すまで検索できない |
| 範囲外の結果がとても多い検索語 | `scan_limit`（exit 4）。同じ呼び出しを繰り返すと続きを読む |

### 6-6. 長い時間の運用

既定の 8 時間、24 時間、7 日の順に試します。90 日の試験が終わるのを待たずに公開してよいですが、試していない期間は「未確認」と書きます。

- 1時間ごとに `nas-search --status` を記録する（`remaining_seconds`、`searches`、`consecutive_failures`、`last_error`）。エージェントに任せてかまいません。パスワードや OTP は要りません
- 1日に数回、実際に検索して結果が返るか確かめる
- 終わったら、NAS の `~/.unisearch/broker.log` で、keepalive の失敗やログアウトの記録を確かめる（イベント名と回数だけが書かれています）

## 7. v0.8.0 の確認（英語の表示）

v0.7.0 から変えたのは表示の文だけです（`lang` の設定）。7-1〜7-3 はパスワードも OTP も要りません。動いている検索セッションは止めずに確かめられます（セッションは始めたときのプログラムのまま動き続けます）。

### 7-1. 入れ替え

[agent-setup.md](agent-setup.md) の手順1で `unisearch` を置き直し、SHA-256 が一致することを確かめます。エージェント側の `nas-search` も置き直します。

| 確認 | 期待 |
|---|---|
| `~/.local/bin/unisearch --version` | `unisearch 0.8.0` |
| `nas-search --version`（エージェント側） | `nas-search 0.5.0` |
| `nas-search --status`（入れ替えの前から動いているセッションで） | exit 0。`searches` が増え続けている |
| `nas-search "<結果がある検索語>" --limit 3` | exit 0。結果が返る |

### 7-2. NAS 側の英語の表示

試しの設定ファイルを別に作るので、いつもの `server.conf` は変えません。

```bash
ssh <ssh-user>@<nas-host> 'c=$(mktemp) && printf "lang = en\n" > "$c" && UNISEARCH_CONFIG="$c" ~/.local/bin/unisearch --help | head -3; UNISEARCH_CONFIG="$c" SSH_ORIGINAL_COMMAND=ls ~/.local/bin/unisearch --agent-gateway; echo "exit=$?"; rm -f "$c"'
```

期待: ヘルプが英語。2つ目は `"type": "usage"` で、`message` が `Only` から始まる英語、`exit=2`。

### 7-3. エージェント側の英語の表示

```bash
UNISEARCH_LANG=en nas-search; echo "exit=$?"
```

期待: `"message": "Give search words"`、`exit=2`。`UNISEARCH_LANG` を外すと日本語に戻る。

### 7-4. 入力欄（任意。本人が入力）

```bash
ssh -t <ssh-user>@<nas-host> 'c=$(mktemp) && printf "lang = en\n" > "$c"; UNISEARCH_CONFIG="$c" ~/.local/bin/unisearch --no-session --user search-agent --json test --limit 1; echo "exit=$?"; rm -f "$c"'
```

期待: 「DSM password for search-agent:」と「2-step verification code for search-agent (6 digits):」が出て、検索できる。

## 記録テンプレート

```
日付 / DSM build / python3 version
手順1: OK|NG（出力要約）
手順2: OK|NG（Auth min-max, Search min-max）
手順3: exit=
手順4: total= returned= exit=  形式所見:
手順5: 各 exit / api_code
手順6-1: version / check-api / 表示
手順6-2: 範囲外 0件か / 断られたコマンド
手順6-3: paged= unique= whole= same order=
手順6-4: 索引の更新にかかった時間 / 重複 N 件 / 取りこぼしの疑い N 件
手順6-5: 各 exit / last_error
手順6-6: 期間 / 最後まで検索できたか / keepalive の失敗の回数
手順7: version / 入れ替え後も検索セッションが動くか / 英語の表示（NAS・エージェント）/ 入力欄
```
