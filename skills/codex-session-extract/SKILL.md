---
description: Extract Codex CLI chat transcripts for a project and load prior prompts, responses, and tool activity into the current agent context. Use when asked to find, list, load, continue, recover, or search a Codex session for a workspace.
license: MIT
metadata:
  github-path: skills/codex-session-extract
  github-repo: https://github.com/BoYanZh/codex-session-extract-skill
name: codex-session-extract
---
# Codex Session Extraction

Use the bundled extractor. It treats `sessions/YYYY/MM/DD/rollout-*.jsonl`
under `CODEX_HOME` (default `~/.codex`) as the only conversation-content source.

## Run

Resolve this Skill's directory as `$skillRoot`, then run:

```powershell
$extractor = Join-Path $skillRoot 'scripts\extract_codex_session.py'

python $extractor --project $PWD --list
python $extractor --project $PWD --latest
python $extractor --project $PWD --session <session-id>
```

Useful narrowing options:

- `--keyword TEXT` may be repeated to keep matching parts.
- `--tail N` keeps the last N matching parts.
- `--format json` produces structured output.
- `--max-part-chars 0` disables content truncation when the extra context is necessary.
- `--root PATH` selects a non-default Codex home directory (`CODEX_HOME`).

Session titles come from the first user message, falling back to
`session_index.jsonl` thread names. `--session` accepts an exact session ID
or a unique ID prefix. One conversation can span several repositories, so
extraction groups records into user turns and keeps turns that mention the
requested project or ran tools with it as the working directory. A session
whose rollout `session_meta.cwd` is the requested project but has no
path-bearing tool call is kept whole.

## Load context

Read the extracted output and summarize goals, completed changes, unresolved
failures, and the last successful state. Cross-check the repository's current
files, `git status`, and recent commits because rollouts can end after an
error or an empty assistant response.

`history.jsonl` holds input history only and is never treated as transcript
content. ChatGPT web/app local caches (IndexedDB/LevelDB) are best-effort and
out of scope: for dependable ChatGPT web history, parse the official export
ZIP instead. An empty or missing rollout is an explicit unsupported case.
Report the session ID and error; raw SQLite recovery is outside this Skill's
reliability boundary.
