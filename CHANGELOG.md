# Changelog

`unisearch` (runs on the NAS) and `nas-search` (runs on the agent's machine) have their own version numbers. The JSON output has `schema_version: 1` in both.

## unisearch 0.9.0 / nas-search 0.6.0 (unreleased)

- **MCP server.** New `clients/nas-search-mcp` offers `search` and `status` as MCP tools (stdio), so MCP apps such as Claude Code can search without a shell. It runs `nas-search` next to it, so the settings, key, JSON and exit codes are the same. Standard library only. It sends progress notifications during long searches when the app asks for them, and stops the search when the app cancels it.
- **Faster repeated calls.** `nas-search` keeps the SSH connection open for 60 seconds (`ControlMaster`) and reuses it, which saved about 0.1–0.3 s per call in our tests. Turn it off with `reuse_connection=no` in `client.conf`.
- No change on the NAS side apart from the version number; reinstalling `unisearch` is not needed.

## unisearch 0.8.0 / nas-search 0.5.0 (2026-11-07)

First public release (beta).

- **English messages.** Set `lang = en` in `server.conf` on the NAS and in `client.conf` (or `UNISEARCH_LANG=en`) on the agent's machine. The default is Japanese (`lang = ja`). JSON keys, error `type` values and exit codes do not depend on the language. A running search session keeps the language it started with.
- Docs: English README, the agent skill in English, a screenshot of the Universal Search indexing settings, and the background story.

## Earlier versions (not published)

- **0.7.0 / client 0.4.0** (2026-11-06): **Renamed** the command from `synosearch` to `unisearch`. DSM ships an unrelated `/usr/syno/bin/synosearch`, and `syno*` names belong to Synology. Settings and state moved to `~/.config/unisearch/` and `~/.unisearch/`; environment variables are now `UNISEARCH_*`. Behaviour did not change. Checked on a DS225+ with DSM 7.4.1: install, `--check-api`, starting a search session, the session surviving the SSH logout, search from two agent machines, refusal of anything other than search through the agent key, the permission split, paging, paging while files are added or removed, and client-side timeouts and interruptions. See [docs/verification.md](docs/verification.md).
- **0.6.1 / client 0.3.1**: The SSH entry point always waits long enough for the slowest search the hard limits allow (250 s), and `nas-search` waits 260 s by default, so the client never gives up before the NAS.
- **0.6.0 / client 0.3.0**: Settings files (`server.conf`, `client.conf`); sessions up to 90 days (default 8 hours); the DSM account name is shown before the password and OTP prompts.
- **0.5.0 / client 0.2.0**: When results are limited to allowed folders, `offset` and `next_offset` count only allowed results, and `total` is always `null`, so out-of-scope counts are never revealed. `scan_limit` (exit 4) stops long scans through out-of-scope results.
- **0.4.0 / client 0.1.0**: Interactive search, the search session (a person logs in with OTP; the SID stays in memory), the SSH entry point for a search-only key, `nas-search`, and the agent skill.
