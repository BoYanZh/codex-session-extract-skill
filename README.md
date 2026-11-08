# Codex Session Extract Skill

An Agent Skill that loads project-scoped Codex CLI conversation context from local rollout files.

The extractor is intentionally **rollout-only**. It reads conversation content from:

```text
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
```

It may use `session_index.jsonl` for thread names and update times, but it never treats `history.jsonl` (input history only), SQLite databases, or ChatGPT web/app local caches as conversation content. Missing or empty rollouts fail explicitly instead of silently returning an unreliable reconstruction.

## Install

Install globally for Codex:

```powershell
gh skill install BoYanZh/codex-session-extract-skill codex-session-extract --agent codex --scope user
```

Install globally for OpenCode:

```powershell
gh skill install BoYanZh/codex-session-extract-skill codex-session-extract --agent opencode --scope user
```

For a reproducible project install, pin a release:

```powershell
gh skill install BoYanZh/codex-session-extract-skill codex-session-extract --agent opencode --scope project --pin v1.0.0
```

Use one installation scope for each agent and skill ID to avoid duplicate discovery.

## Use

Agents invoke the bundled script according to [`SKILL.md`](skills/codex-session-extract/SKILL.md). You can also run it directly:

```powershell
$extractor = 'skills/codex-session-extract/scripts/extract_codex_session.py'

python $extractor --project $PWD --list
python $extractor --project $PWD --latest
python $extractor --project $PWD --session <session-id>
python $extractor --project $PWD --latest --keyword comfy --tail 20
python $extractor --project $PWD --latest --format json --output session.json
```

Run `python $extractor --help` for all options. `--session` accepts an exact session ID or a unique ID prefix. Override the Codex home with `--root PATH` or the `CODEX_HOME` environment variable.

## Project scoping

Codex sessions can operate across multiple repositories. The extractor groups rollout records by user turn and uses tool working directories and path-bearing fields to retain turns that actively operated in the requested project. A command that merely mentions another repository does not move the turn into that repository. A session whose `session_meta.cwd` is the requested project but has no path-bearing tool call is kept whole.

The result is context recovery, not proof that every described change reached disk. Always compare extracted context with the current worktree, logs, and tests.

## Privacy

Rollouts can contain prompts, responses, file paths, commands, and tool output. Review generated Markdown or JSON before sharing it. The extractor performs local reads only and does not upload transcript data.

## Development

Python 3.10 or newer is required. The runtime uses only the Python standard library.

```powershell
python -m unittest discover -s tests -v
python -m py_compile skills/codex-session-extract/scripts/extract_codex_session.py
ruff check .
```

## License

[MIT](LICENSE)
