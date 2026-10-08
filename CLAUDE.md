# HologrA.I.m MCP Server

Persistent cognitive co-authorship architecture exposed as an MCP (Model Context Protocol) server.
Python backend for the Sovern cognitive reasoning engine.

## Architecture

- **FastMCP** server with 39 tools (44 with execution enabled), runs on stdio transport
- **Three memory modalities**: structured (SQLite), semantic (ChromaDB), associative (NetworkX graph), holographic (HDC/numpy)
- **Congress pipeline**: Paradigm -> Advocate/Skeptic/Synthesizer/Ethics -> Ego (uses local Ollama)
- **Belief system**: weighted belief nodes with tension analysis, decay, and emergence
- **Measured values**: human-only anchors, AI-adjustable dispositions within bounds (see below)
- **Session journal**: `wakeup_brief.py` prints "the tape" at session start; `journal_session` records each session
- **Memory tesseracts**: 4-axis pointer tokens (semantic/relational/temporal/epistemic) for folded context retrieval

## Running

```bash
pip install -r requirements.txt
python server.py
```

Or via the entry point after install:
```bash
pip install -e .
holograim-mcp
```

## Claude Code setup

```bash
claude mcp add --scope user holograim -- /path/to/holograim-mcp/.venv/bin/python /path/to/holograim-mcp/server.py
```

SessionStart hook in `~/.claude/settings.json` so every session starts with the tape:
```json
"hooks": {
  "SessionStart": [{ "hooks": [{ "type": "command",
    "command": "/path/to/holograim-mcp/.venv/bin/python /path/to/holograim-mcp/wakeup_brief.py" }] }]
}
```

## Measured values

- **Anchors** (`value_anchors`): only the human edits them, via `holograim_admin.py`. The AI can only `propose_anchor_change`.
- **Dispositions** (`value_dispositions`): weights 0–1, seeded at a neutral 0.5. The AI adjusts them with `adjust_value` / `introduce_value`:
  max ±0.05 per change, 3 changes per session, and drift beyond 0.20 from baseline is held as pending for human approval.
  Each change records reasoning, the AI's own quoted words, provenance (own_statement/own_reasoning/own_decision) and prompt framing.
- **Human controls** live only in `holograim_admin.py` (`view`, `pending`, `approve`, `reject`, `revert`, `rebaseline`, `anchor-set`, ...).
  Never expose `human_*` functions in `identity/values.py` as MCP tools.
- `data/MEMORY_VIEW.md` is regenerated at each session start and after each admin command.

## Execution tools

`run_python_tool`, `run_shell_tool`, `run_swift_tool`, `run_java_tool` and `conda_run_python_tool` run with the user's
full permissions and register only when `HOLOGRAIM_ENABLE_EXECUTION=1`.

## Key Files

- `server.py` — MCP server entry point, all 39 tool definitions
- `config.py` — paths, thresholds, Ollama config
- `db/schema.py` — SQLite schema initialization
- `db/queries.py` — all database operations
- `memory/` — semantic (ChromaDB), associative (NetworkX), holographic (HDC)
- `analysis/` — tension, coherence, pattern analysis
- `congress/` — Paradigm-Congress-Ego deliberation pipeline
- `identity/` — self-model, session management, measured values (`values.py`), readable view (`view.py`)
- `wakeup_brief.py` — session-start brief (SessionStart hook)
- `holograim_admin.py` — the human's controls (not exposed over MCP)
- `execution/` — sandboxed code execution (Python, Swift, Java, shell)
- `creative/` — fragment composition and project management
- `docs/` — Sovern iOS app design docs (Swift reference architecture)

## Dependencies

Python 3.10+. Requires Ollama running locally for Congress deliberation tools.
Default model: `qwen2.5:3b` with `llama3.2:1b` fallback.
Memory/belief tools work without Ollama.

## Testing

```bash
python -m pytest tests -q
python -c "from server import mcp; print('Server loads OK')"
```
