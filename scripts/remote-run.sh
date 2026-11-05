#!/usr/bin/env bash
# NAS にファイルを置かずに unisearch を実行する。
# スクリプト本体を base64 でコマンドラインに埋め込み、リモートの python3 -c で実行する。
#
#   scripts/remote-run.sh <nas-host> --check-api            # 非対話（TTYなし）
#   scripts/remote-run.sh -t <nas-host> test --limit 5      # 対話（パスワード/OTPはNAS側TTYへ直接入力）
#
# 資格情報はこのスクリプトを経由しない。引数に秘密情報を渡さないこと。
set -euo pipefail

tty_flag=-T
if [ "${1:-}" = "-t" ]; then tty_flag=-t; shift; fi
if [ $# -lt 1 ]; then
  echo "usage: $0 [-t] HOST [unisearch の引数...]" >&2
  exit 2
fi
host=$1; shift

root=$(cd "$(dirname "$0")/.." && pwd)
payload=$(base64 < "$root/unisearch" | tr -d '\n')

# POSIX sh 向けに単一引用符でクォートする
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

remote="python3 -c 'import base64,sys; exec(compile(base64.b64decode(sys.argv.pop(1)), \"unisearch\", \"exec\"))' $payload"
for arg in "$@"; do remote="$remote $(quote "$arg")"; done

exec "${SSH_BIN:-ssh}" "$tty_flag" "$host" "$remote"
