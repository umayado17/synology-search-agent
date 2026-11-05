# synology-search-agent-cli

Synology NAS の **Universal Search（全文検索）の既存インデックス**を、SSH から検索して JSON で受け取るための小さな CLI です。AI エージェントから使うことを想定しています。

- **sudo 不要・root 不要**。DSM の一般ユーザーとして動きます
- **2段階認証（OTP）はそのまま**。外しません
- **読み取り専用**。ファイル・インデックス・共有フォルダは変更しません
- Python 3.8 以上の標準ライブラリだけで動きます（追加パッケージ不要）

> [!WARNING]
> Synology が公開していない Web API（`SYNO.Finder.FileIndexing.Search`）を使っています。DSM の更新で予告なく動かなくなる可能性があります。Synology 社とは関係のない非公式ツールです。

## 動作確認済みの環境

- Synology DS225+、DSM 7.4.1（build 90080）、NAS 側 Python 3.8.15
- 状態: β版。8時間以上の連続運用や、他の機種での動作は未確認
- 確認した内容は [docs/verification.md](docs/verification.md)

他の機種・DSM バージョンでは未確認です。まず `--check-api` で API があるか確かめてください。

## 構成

```
AIエージェント ──ssh（検索専用の鍵）──▶ NAS: synosearch --agent-gateway
   nas-search                                   │（UNIXソケット）
                                                ▼
                                     検索セッション（本人が OTP で開始）
                                                │
                                                ▼
                                     DSM Web API（127.0.0.1:5001）
```

| ファイル | 役割 |
|---|---|
| [`synosearch`](synosearch) | NAS 上で動く本体。対話検索、検索セッション、SSH 入口 |
| [`clients/nas-search`](clients/nas-search) | エージェント側のコマンド。ssh を組み立てて結果を返す |
| [`skills/nas-search/SKILL.md`](skills/nas-search/SKILL.md) | エージェント向けの説明書（Claude Code のスキル形式） |
| [`scripts/remote-run.sh`](scripts/remote-run.sh) | NAS にファイルを置かずに試すためのスクリプト |

## 使い方

### 1. 対話で検索する（人が使う）

```bash
ssh -t <ssh-user>@<nas-host> '~/.local/bin/synosearch "test" --limit 5'
ssh -t <ssh-user>@<nas-host> '~/.local/bin/synosearch "test" --limit 20 --json'
```

ユーザー名・DSM パスワード・OTP を端末から入力します。毎回ログインしてログアウトします。

### 2. AI エージェントから使う

1. 人が NAS で検索セッションを始める（パスワードと OTP を入力）。期限は既定 8 時間（1分〜24時間で指定可）。期限が切れたら、また人が始め直す
2. エージェントは検索専用の SSH 鍵で `nas-search "検索語"` を呼ぶ。パスワードも OTP も不要

```bash
nas-search "test" --limit 20
nas-search --status
```

導入手順は [docs/agent-setup.md](docs/agent-setup.md) にあります。

## 安全のための設計

- パスワード・OTP・SID はファイルにもログにも残しません。SID は検索セッションのメモリの中だけに置きます
- 検索セッションへの接続は、本人だけが読めるソケット（0600）からに限ります
- エージェント用の鍵は `authorized_keys` の `restrict,command=` で検索しかできません。シェル・ポート転送は使えません
- 検索範囲は二重に絞ります。主な守りは DSM の共有フォルダ権限で、CLI の `--allow-prefix` は補助です。絞り込み時は範囲外の件数を結果に出しません
- 検索専用の DSM アカウント（管理者でない・2段階認証あり）を使うことを推奨します

詳しい検討は [docs/design-noninteractive-auth.md](docs/design-noninteractive-auth.md) にあります。

> [!NOTE]
> TLS 証明書の検証は、接続先が NAS 自身（`127.0.0.1`）の場合に限って省いています。リモートの URL に変えて使わないでください。

## 仕様とテスト

- オプション・JSON の形式・終了コード: [docs/cli-spec.md](docs/cli-spec.md)
- 実機での確認手順: [docs/nas-verification.md](docs/nas-verification.md)

テストは標準ライブラリだけで動きます。NAS は要りません。

```bash
python3 -m unittest discover -s tests -v
```

## 参考

- [atkaper/synology-universal-search-cli](https://github.com/atkaper/synology-universal-search-cli)

## ライセンス

[MIT](LICENSE)
