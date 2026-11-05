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

## 記録テンプレート

```
日付 / DSM build / python3 version
手順1: OK|NG（出力要約）
手順2: OK|NG（Auth min-max, Search min-max）
手順3: exit=
手順4: total= returned= exit=  形式所見:
手順5: 各 exit / api_code
```
