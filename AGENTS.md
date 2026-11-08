# AI エージェント・開発者向けの作業ルール

## 目的と範囲

Synology NAS の Universal Search インデックスを使う、sudo 不要の検索ツールです。AI エージェントからは MCP サーバーとしても、コマンド（CLI）としても使えます。ファイルの更新やインデックスの変更はしません。

| ファイル | 動く場所 | 役目 |
|---|---|---|
| `unisearch` | NAS | 検索セッションと、DSM の検索 API を呼ぶ部分 |
| `clients/nas-search` | エージェント側 | SSH で `unisearch` を呼ぶコマンド |
| `clients/nas-search-mcp` | エージェント側 | `nas-search` を MCP の道具（`search`・`status`）として出すサーバー |

エージェント側の2つは PyPI のパッケージ `nas-search-mcp` としても配っています（`pyproject.toml`）。

## 動作が確認できた API の条件（変える前に docs/verification.md を読む）

- HTTPS の接続先（NAS 内）: `https://127.0.0.1:5001/webapi/entry.cgi`
- 認証: `SYNO.API.Auth` v6、`method=login`、`session=FileStation`、`format=sid`、`otp_code`
- 検索: `SYNO.Finder.FileIndexing.Search` v1、`method=search`、`_sid` に SID
- `keyword` は JSON で引用した文字列。`size` と `from` を送る
- `search_weight_list`: `SYNOMDWildcard:1`、`SYNOMDTextContent:1`、`SYNOMDSearchFileName:8.5`（`trailing_wildcard:true`）
- `fields`: `SYNOMDSharePath`、`SYNOMDFSName`、`SYNOMDFSSize`、`SYNOMDIsDir`、`SYNOMDContentModificationDate`
- `sorter_field="relevance"`、`sorter_direction="asc"`、`sorter_use_nature_sort=false`、`sorter_show_directory_first=true`
- ログアウト: `SYNO.API.Auth` v6 `logout`、`session=FileStation`

この条件は `tests/fixtures/verified_search_params.json` で回帰テストしています。非公開 API なので、応答が予告なく変わる前提でテストを書いてください。

## 禁止事項

- sudo や root 権限を要求しない。DSM の2段階認証を外さない
- パスワード・OTP・SID・CSRF トークンを、コード・コミット・出力・ログに残さない
- 認証の回避や、ブラウザなどからの認証済み Cookie の抜き出しを実装しない
- インデックスや共有フォルダを変更しない。全ファイルの再走査をさせない
- 実機で確かめていないことを「動作確認済み」と書かない
- エラーコード `119` / `120` の原因を1つに決めつけない（同じ番号でも前提によって意味が変わる）
- 秘密情報やファイルの中身を外部のサイトに送らない
- 個人の環境の値（ホスト名・IP・ユーザー名・実際のパス）をコミットしない。`<nas-host>` `<ssh-user>` などの置き換え用の文字を使う

## 開発の決まり

- Python 3.8 互換、標準ライブラリのみ
- テストは unittest（pytest なし）: `python3 -m unittest discover -s tests -v`
- CLI のオプション・JSON・終了コードを変えたら `docs/cli-spec.md` も直す
- MCP の道具（名前・引数・説明）を変えたら、`README.md` と `README.ja.md` の道具の表と `tests/test_mcp.py` も直す
- `nas-search-mcp` の版を上げたら、`server.json` の版も合わせる（`tests/test_mcp.py` が確かめる）
- 実機で確かめたら、結果を `docs/verification.md` に追記する。パスやファイル名は伏せ字にする
