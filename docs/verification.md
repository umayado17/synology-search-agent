# 実機での確認記録

作者の環境で確かめた結果のまとめです。ホスト名・ユーザー名・パスは伏せています。

ここに書いた確認は、コマンド名が `synosearch` だったころ（v0.6 まで）のものです。v0.7.0 で `unisearch` に名前を変えました（中身の動きは変えていません）。下の記録では新しい名前で書いています。

## 環境

- Synology DS225+、DSM 7.4.1（build 90080）
- NAS 側 Python 3.8.15、ログインシェル `/bin/sh`
- 接続: 開発用の Mac から VPN 経由の SSH（パスワード認証）
- `synowebapi`（`/usr/syno/bin/synowebapi`）は `root:root`・権限 `700` で、一般ユーザーは直接実行できない
- `synopkg status SynoFinder` は `263` / `stop` を返したが、GUI と検索エンジン（`synoelasticd`）は動いていた。263 の原因は調べていない

## API を探した経過（2026-11-04）

1. sudo なしで `SYNO.API.Info` を問い合わせ、`SYNO.Finder.FileIndexing.Search` が `entry.cgi`・version 1・`requestFormat: JSON` と分かった
2. `SYNO.API.Auth` は version 1〜7
3. 認証なしの検索はエラー 119
4. `SYNO.API.Auth` v6 の通常ログインはエラー 403（OTP が必要）。OTP 付きでログインできた
5. `session=DSM` では File Station API も検索 API も 119
6. `session=FileStation` にすると File Station の `list_share` は成功。検索 API は、条件を省くと 120
7. `FileStation` セッションで、検索の重み・返すフィールド・並べ方を指定すると検索に成功した（`test` で 1,333 件、5件取得）
8. ログアウトも成功

成功した条件は [AGENTS.md](../AGENTS.md) と `tests/fixtures/verified_search_params.json` にあります。

## CLI の実機確認（2026-11-04）

`scripts/remote-run.sh` で、NAS にファイルを置かずに実行しました。

| 確認 | 結果 |
|---|---|
| `--check-api` | 成功（exit 0）。Auth v1〜7、Search v1 / `entry.cgi` |
| 端末なしで実行 | 入力を待たずに exit 7（`no_tty`）。DSM API は呼ばない |
| 対話検索（テキスト） | 成功（exit 0） |
| `--raw` | 成功（exit 0）。応答の形式は下に記載 |
| `--json` | 成功（exit 0）。[cli-spec.md](cli-spec.md) の形式どおり |
| パスワード誤入力 | `認証失敗（コード 400）`、exit 3 |

見つかって直した不具合:

1. ユーザー名の入力で `UnsupportedOperation` になった。端末を `"r+"` で開いていたため（端末はシークできない）。読み書きを分けて開くように直し、擬似端末のテストを追加した
2. パスワードと OTP の見出しが `DSM?????` に化けた。C ロケールの NAS で `getpass` が見出しを書くため。見出しは UTF-8 で自分で書くように直した

### 検索 API の応答の形（DSM 7.4.1）

- `data` のキー: `has_error`、`hits`、`total`、`unavail`
- `hits` の要素は平らなオブジェクト。頼んだ5つのフィールドのほかに `SYNOMDExtension`、`SYNOMDOwnerUserID`（null）、`SYNOMDPath`（`/volume1/<共有>/...`）、`additional`（`doc_id` など）、`readable` も返る
- `SYNOMDSharePath` は `/<共有フォルダ>/<途中のパス>/<名前>` の形で、名前まで含む
- 値の型: 日時は UNIX 秒の**文字列**、サイズも**文字列**（フォルダは `"4096"`）、`SYNOMDIsDir` は `"y"` / `"n"`
- インデックス作成中は `total` が数分のうちに変わった

テスト用の応答データ（`tests/fixtures/search_response_sample.json`）は、この形のまま架空のパスにしたものです。

## 検索範囲の確認（2026-11-04）

検索専用の DSM ユーザー（グループ `users` のみ・2段階認証あり）で試しました。

- **検索結果はログインしたユーザーの権限で絞られる**ことを確認した
- 共有フォルダ `homes` を読めるようにすると、全ユーザーのホームが結果に出た。DSM の権限では `homes` の中の特定ユーザーだけを許すことはできない。そのため検索させたいファイルは別の共有フォルダに移した
- `users` グループ経由で読める共有フォルダも結果に出た。検索専用ユーザーに個別に「アクセスなし」を設定すると消えた
- 結論: DSM の権限で範囲を絞り、さらに CLI の `--allow-prefix` でも絞る（二重の守り）

## 検索セッションと SSH 入口（2026-11-05、v0.4.0）

[agent-setup.md](agent-setup.md) の手順どおりに導入しました。

- 検索セッションは、始めた SSH を切ったあとも動き続けた（DSM は止めなかった）
- 検索専用の鍵だけで（`BatchMode=yes`・パスワード認証なしで）:
  - `status`: exit 0。パスワード・OTP なしで使えた
  - `search "test" --limit 20`: exit 0。20件すべて許可した共有フォルダの中。`total: null`、`next_offset: 20`
  - `--offset 20` で次のページ、日本語の検索語も正しく届いた
- 拒否されたもの（すべて exit 2、stderr に `usage` の JSON）: `ls`、`cat /etc/passwd`、`search test; ls`、`search test --raw`、`search test --allow-prefix /homes`、`stop`、空のコマンド
- ポート転送（`-L 15001:127.0.0.1:5001`）は `administratively prohibited` で拒否された（`restrict` が効いている）

## まだ確かめていないこと

- v0.5.0 の変更（範囲を絞るときのページ送り、`nas-search --max-results` の取り方）の実機での動作。単体テストのみ
- v0.6.0・v0.6.1 の変更（設定ファイル、最大90日、入力時のアカウント名の表示、入口の待ち時間）の実機での動作。単体テストのみ
- 24時間を超えるセッションで DSM 側のログインがもつか
- 索引が更新されている最中のページ送りで、重複や取りこぼしがどのくらい起きるか（手順は [nas-verification.md](nas-verification.md) の 6-4）
- NAS の再起動・通信断・DSM 側での切断のときの、新しい版の動き（同じく 6-5）
- とても遅い検索（走査の上限まで読み、最後の API 呼び出しも遅い場合）で、入口の 250 秒以内に応答が返るか。10 秒の余裕に、プロセス間の通信や JSON の処理が収まるか

- keepalive（10分ごと）で、検索セッションが 8 時間もつか
- DSM の FileStation セッションの有効期限・同時に持てる数
- エラー 404（OTP 誤り）など、パスワード誤り以外の認証エラーの実機での値
- `119` / `120` の正確な意味
- 他の機種・DSM バージョンでの動作
