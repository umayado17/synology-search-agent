# AIエージェント向けの導入手順

NAS 上のファイルを追加・変更する手順です。**すべて本人が実行**します（エージェントは実行しない）。
前提（例）:

- 検索専用の DSM ユーザー `search-agent`（グループ `users` のみ・2段階認証あり）。検索させたい共有フォルダ（例 `/archive`）だけ読み取り専用、それ以外は「アクセスなし」
- Universal Search が入っていて、その共有フォルダが索引の対象になっている（[README の「使う前の準備」](../README.ja.md#使う前の準備nas-側)）。対象にしないと索引は作られず、検索しても何も返らない
- NAS に SSH できるユーザー `<ssh-user>`（DSM では administrators のみ SSH 可）
- `<nas-host>` は NAS のホスト名か IP、`<HOME>` は `<ssh-user>` のホームディレクトリ

以下、Mac の作業ディレクトリはこのリポジトリ、NAS は `<ssh-user>@<nas-host>` とします。

## 1. CLI を NAS に置く（<ssh-user> のホームのみ）

```bash
ssh <ssh-user>@<nas-host> 'mkdir -p ~/.local/bin && cat > ~/.local/bin/unisearch && chmod 700 ~/.local/bin/unisearch' < unisearch
```

同じものが置かれたか確認（2つの値が一致すること）:

```bash
shasum -a 256 unisearch
```

```bash
ssh <ssh-user>@<nas-host> 'sha256sum ~/.local/bin/unisearch; echo HOME=$HOME'
```

ここで表示される `HOME=` の値（例 `/var/services/homes/<ssh-user>`）を手順4で使います。

## 2. 検索セッションを始める（毎日1回など）

```bash
ssh -t <ssh-user>@<nas-host> '~/.local/bin/unisearch session start --user search-agent --allow-prefix /archive --max-age 8h'
```

SSH のパスワード（<ssh-user>）→ DSM パスワード（search-agent）→ 2FA コード（search-agent）の順に入力します。
DSM の入力の前に「DSM アカウント: search-agent」と表示されます。SSH のアカウントではなく、こちらのパスワードとコードを入れてください。

期間を毎回指定したくないときは、NAS の `~/.config/unisearch/server.conf` に書いておけます（例 `session_max_age = 7d`。最大 90d。表示を英語にするなら `lang = en`）。24時間を超える期間で DSM 側のログインがもつかは未確認です。
「検索セッションを開始しました」と出たら、SSH を閉じてかまいません。

## 3. SSH を閉じても残っているか確認（初回は必ず）

```bash
ssh <ssh-user>@<nas-host> '~/.local/bin/unisearch session status'
```

「動作中」と出れば OK。終了コード 8（セッションが無い）なら、DSM が SSH 切断時にプロセスを止めている可能性があります（未検証事項）。結果を記録してください。

## 4. AIエージェント専用の SSH 鍵を登録する

エージェントを動かすマシンで鍵を作ります（この鍵ファイルはエージェント以外に渡さない）:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/unisearch_agent -N "" -C unisearch-agent
```

NAS の `~/.ssh/authorized_keys` に**1行**追加します。`<HOME>` は手順1の値、`<公開鍵>` は `~/.ssh/unisearch_agent.pub` の中身です:

```
restrict,command="<HOME>/.local/bin/unisearch --agent-gateway" <公開鍵>
```

- `restrict`: ポート転送・PTY・エージェント転送などを禁止
- `command=`: この鍵では、何を要求しても検索の入口しか動かない（シェルに入れない）
- 鍵ログインがうまくいかないときは、DSM の sshd がホームや `.ssh` の権限を厳しく見ている場合があります（`~/.ssh` は 700、`authorized_keys` は 600）。ホームディレクトリ自体の権限変更は影響が大きいので、変更前に相談してください

## 5. エージェントからの使い方

```bash
ssh -i ~/.ssh/unisearch_agent -o IdentitiesOnly=yes <ssh-user>@<nas-host> 'search "在留資格" --limit 20'
```

```bash
ssh -i ~/.ssh/unisearch_agent -o IdentitiesOnly=yes <ssh-user>@<nas-host> status
```

- 成功: stdout に JSON 1行、終了コード 0
- セッション切れ: 終了コード 8、stderr に `{"error":{"type":"session_required",...}}` → 人に手順2を依頼
- ページ送り: 結果の `next_offset` を次の `--offset` に使う

## 6. エージェント側に `nas-search` とスキルを入れる（推奨）

エージェントが長い ssh コマンドを組み立てずに済むよう、エージェントを動かすマシンに次を置きます。

```bash
mkdir -p ~/.local/bin ~/.config/unisearch
cp clients/nas-search ~/.local/bin/nas-search && chmod 755 ~/.local/bin/nas-search
printf 'host=<ssh-user>@<nas-host>\nkey=~/.ssh/unisearch_agent\n# timeout=260\n# lang=en\n' > ~/.config/unisearch/client.conf
chmod 600 ~/.config/unisearch/client.conf
```

`timeout` の既定は 260 秒です。NAS 側の入口が待つ最長（250 秒）より長いので、NAS の設定を変えても既定のままで足ります。短くする場合は、`session start` が表示する「1回の検索は最長 N 秒」より長くしてください。

`nas-search` は ssh の接続を使い回します（v0.9.0〜、`ControlMaster`）。最後の呼び出しから 60 秒のあいだ接続を残し、続けて呼ぶと接続の手間（この環境で約 0.3 秒）を省きます。接続の出入り口（ソケット）は `~/.ssh/unisearch-<鍵ごとの印>-<接続ごとの印>` に作られます（`~/.ssh` の中なので、ほかのユーザーからは使えません）。使い回しをやめるには `client.conf` に `reuse_connection=no` と書きます。

確認:

```bash
nas-search --status
```

```bash
nas-search "test" --limit 5
```

Claude Code で使う場合は、スキルを登録します（リポジトリ内の説明書を参照する形）:

```bash
mkdir -p ~/.claude/skills && ln -s "$PWD/skills/nas-search" ~/.claude/skills/nas-search
```

他のエージェントには `skills/nas-search/SKILL.md` の本文を指示として渡してください。

### MCP で使う場合（v0.9.0〜）

シェルを使えないアプリや、MCP で道具を渡したいときは、`nas-search` と同じ場所に `nas-search-mcp` を置きます。設定（`client.conf`）と鍵は `nas-search` のものをそのまま使います。

```bash
cp clients/nas-search-mcp ~/.local/bin/nas-search-mcp && chmod 755 ~/.local/bin/nas-search-mcp
```

Claude Code:

```bash
claude mcp add nas-search -- python3 ~/.local/bin/nas-search-mcp
```

ほかの MCP アプリ（設定ファイルに書く形）では、`mcpServers` に次を足します。`~` が使えないアプリがあるので、フルパスで書きます。`python3` が見つからないときは、`which python3` で出るパスに変えます。

```json
{"mcpServers": {"nas-search": {"command": "python3", "args": ["/Users/<you>/.local/bin/nas-search-mcp"]}}}
```

- 道具は `search`（`query`、`limit`、`offset`、`max_results`）と `status` の2つだけです。どちらも読むだけで、NAS のファイルは変えません
- 結果とエラーは `nas-search` と同じ JSON です。エラーのときは `isError: true` で、中身は `{"error": {"type", "message", "exit_code"}}`
- スキル（SKILL.md）は要りません。使い方の説明は MCP の道具の説明に入っています

- `nas-search` はパスワード認証を一切試しません（`BatchMode`・`PasswordAuthentication=no`）。鍵の不備で NAS 側にログイン失敗が積み上がることを防ぐためです
- 検索語は `--` の後に引用して送るので、`;` `$()` 引用符 改行 `-` で始まる語もそのまま届きます

## 止める・取り消す

- 今日の検索をやめる: `ssh <ssh-user>@<nas-host> '~/.local/bin/unisearch session stop'`
- エージェントの鍵を無効にする: `authorized_keys` から該当の1行を消す
- すべてやめる: 上の2つに加えて `~/.local/bin/unisearch` と `~/.unisearch/` を削除

## 記録すること（docs/verification.md へ）

- 手順3: SSH 切断後も残ったか
- keepalive（10分）で 8時間保てたか、途中で切れたならおよその時間（DSM のセッション失効は未検証）
- 手順5: 結果が `/archive/` だけか、禁止した命令（例 `ls`）が終了コード 2 で拒否されるか
