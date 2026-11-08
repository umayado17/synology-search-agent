# Security policy

[日本語は下にあります](#日本語)

## Reporting a problem

Please do not open a public issue for a security problem. Use **"Report a vulnerability"** on the repository's Security tab (GitHub private vulnerability reporting).

Never include passwords, OTP codes, SIDs, private keys, or real host names, user names or file paths from your NAS in a report. Replace them with placeholders such as `<nas-host>`.

## Scope

In scope:

- `unisearch` letting an agent do more than search (for example, a shell, other commands, or port forwarding through the agent key)
- `nas-search` or the MCP server `nas-search-mcp` passing a query or tool argument to a shell or into SSH options, or doing anything other than search and status
- The PyPI package `nas-search-mcp` containing code that differs from `clients/` in this repository
- Results from outside the allowed folders, or the number of out-of-scope results, reaching the agent
- Passwords, OTP codes or SIDs written to files, logs or output
- Another local user reaching the search session

Out of scope:

- Problems in Synology DSM, Universal Search or its Web API. Report those to Synology.
- A NAS whose SSH port is exposed to the internet, or a search-only account that was given more permissions than the docs recommend.

## Supported versions

Only the latest release is supported. This is a beta, unofficial tool that uses an unpublished DSM API.

## 日本語

セキュリティの問題は、公開の Issue に書かないでください。リポジトリの Security タブにある「Report a vulnerability」から知らせてください。

報告には、パスワード、2段階認証のコード、SID、秘密鍵、NAS の本物のホスト名・ユーザー名・パスを書かないでください。`<nas-host>` のような置き換えの名前を使ってください。
