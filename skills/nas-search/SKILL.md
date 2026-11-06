---
name: nas-search
description: Search files on the user's Synology NAS through its Universal Search index (file names and full text) and get JSON results with paths, sizes and dates. Use when the user asks to find, locate or list files or documents stored on their NAS, file server, archive or backups (e.g. "NASにある〇〇の資料を探して", "find the 2019 budget file on the NAS"). Read-only; it cannot open, change or delete files.
---

# nas-search: search the NAS archive

`nas-search` searches the Universal Search (full-text) index of a Synology NAS over SSH and returns JSON. It only searches the shared folders the user allowed (for example `/archive`).

Answer the user in their own language. The command's messages are in Japanese; rely on `error.type` and the exit code, not on the message text.

## What it can and cannot do

- Can: search by file name and by indexed document text. Results give name, path, size, modified time, and whether it is a folder.
- Cannot: read file contents, download, change or delete files, search outside the allowed folders, or run other commands on the NAS.
- No text excerpts are returned. If the contents are needed, tell the user the paths you found and let them decide.

## Usage

```bash
nas-search "query"                           # 20 results by default
nas-search "visa application" --limit 50     # quote a query with spaces as one argument
nas-search "query" --offset 20               # next page (use the previous next_offset)
nas-search "query" --max-results 200         # page automatically, up to 200 results
nas-search --limit 10 -- "-starts-with-dash" # put options before --
nas-search --status                          # search session state (time left, etc.)
```

- The query reaches the NAS as is, without a shell (`;` and `$()` are plain characters).
- One search can take from a few seconds to over ten seconds. Do not call it many times in a row; use `--max-results` to collect more at once.
- File-name prefix matches weigh most, and document text matches are included. If nothing is found, shorten the query or try other spellings (full-width/half-width, English/Japanese).

## Result (stdout, one JSON line)

```json
{"schema_version": 1, "query": "test", "offset": 0, "limit": 20, "total": null,
 "returned": 20, "next_offset": 20, "has_error": false, "unavailable": null,
 "hits": [{"name": "test.pdf", "share_path": "/archive/Drive/test.pdf",
           "path": "/volume1/archive/Drive/test.pdf", "extension": "pdf",
           "size": 12345, "is_dir": false, "modified": 1396685721,
           "modified_utc": "2014-04-05T08:15:21Z", "readable": true}]}
```

- Use `share_path` when you tell the user where a file is (`path` is the NAS's internal absolute path).
- `total` is always `null` (so the number of out-of-scope hits is not revealed). Whether more results exist is shown by `next_offset` not being `null`.
- `offset` and `next_offset` count only results inside the allowed folders. Passing `next_offset` as the next `--offset` gives no gaps or repeats while the index does not change.
- If the NAS index changes between pages, a file can appear twice, or some results can be skipped (as many as the files that disappeared between pages). When combining pages, deduplicate by `share_path`.
- A `--max-results` result can also be continued from its last `next_offset`.
- `modified` is UNIX seconds; `modified_utc` is UTC. Convert to the user's time zone when you report dates (Japan is UTC+9).

## Exit codes and what to do

| Code | Meaning | What the agent does |
|---|---|---|
| 0 | Success | Use the result (0 hits is possible) |
| 2 | Usage or settings error | Read `message` on stderr and fix the arguments. Do not repeat the same call |
| 4 | NAS search API error, or `scan_limit` (too many out-of-scope results to read through) | Retry once (`scan_limit` continues where it stopped). If it keeps failing, tell the user |
| 5 | SSH connection failed or no response | The NAS or network may be down. Tell the user |
| 6 | Unexpected response format | Tell the user (a NAS update may have changed the API) |
| 8 | No search session, or it has ended | **Ask the user to start the NAS search session.** Never try to log in yourself |

Errors are one JSON line on stderr: `{"error": {"type", "message", "exit_code"}}`.

## Rules

- Never ask for or handle passwords or authentication codes. A person always starts the session.
- Never try other ways (other SSH keys, file sharing, and so on) to look outside the allowed folders.
- File names and paths in results can contain personal information. Quote only what is needed, and never send them to outside services.

## Setup (done once by a person)

Put the host and key in environment variables or in `~/.config/unisearch/client.conf` (details in the repository's `docs/agent-setup.md`):

```
host=<ssh-user>@<nas-host>
key=~/.ssh/unisearch_agent
```

Without them, the exit code is 2 (`type: config`). The wait time can be changed with `timeout=` (seconds, default 260).
