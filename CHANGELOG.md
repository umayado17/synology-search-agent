# Changelog

`unisearch` (runs on the NAS) and `nas-search` (runs on the agent's machine) have their own version numbers. The JSON output has `schema_version: 1` in both.

## Planned

- English messages for the commands, chosen by a setting in `server.conf` and `client.conf` (for example `lang = en`). Messages are Japanese only in 0.7.0. Error `type` values and exit codes will not change.

## unisearch 0.7.0 / nas-search 0.4.0 (2026-11-06)

First public release (beta).

- **Renamed** the command from `synosearch` to `unisearch`. DSM ships an unrelated `/usr/syno/bin/synosearch`, and `syno*` names belong to Synology. Settings and state moved to `~/.config/unisearch/` and `~/.unisearch/`; environment variables are now `UNISEARCH_*`. Behaviour did not change.
- Checked on a DS225+ with DSM 7.4.1: install, `--check-api`, starting a search session, the session surviving the SSH logout, search from two agent machines, refusal of anything other than search through the agent key, the permission split, paging, paging while files are added or removed, and client-side timeouts and interruptions. See [docs/verification.md](docs/verification.md).
- Docs: English README, the agent skill in English, a screenshot of the Universal Search indexing settings, and the background story.

## Earlier versions (not published)

- **0.6.1 / client 0.3.1**: The SSH entry point always waits long enough for the slowest search the hard limits allow (250 s), and `nas-search` waits 260 s by default, so the client never gives up before the NAS.
- **0.6.0 / client 0.3.0**: Settings files (`server.conf`, `client.conf`); sessions up to 90 days (default 8 hours); the DSM account name is shown before the password and OTP prompts.
- **0.5.0 / client 0.2.0**: When results are limited to allowed folders, `offset` and `next_offset` count only allowed results, and `total` is always `null`, so out-of-scope counts are never revealed. `scan_limit` (exit 4) stops long scans through out-of-scope results.
- **0.4.0 / client 0.1.0**: Interactive search, the search session (a person logs in with OTP; the SID stays in memory), the SSH entry point for a search-only key, `nas-search`, and the agent skill.
