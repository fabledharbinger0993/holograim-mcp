# HologrA.I.m MCP Server

Persistent cognitive co-authorship architecture exposed as an MCP (Model Context Protocol) server.
Python backend for the Sovern cognitive reasoning engine.

## Architecture

- **FastMCP** server with 39 tools, runs on stdio transport
- **Three memory modalities**: structured (SQLite), semantic (ChromaDB), associative (NetworkX graph), holographic (HDC/numpy)
- **Congress pipeline**: Paradigm -> Advocate/Skeptic/Synthesizer/Ethics -> Ego (uses local Ollama)
- **Belief system**: weighted belief nodes with tension analysis, decay, and emergence
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

## Claude Code MCP Plugin

Add to `.claude/settings.json`:
```json
{
  "mcpServers": {
    "holograim": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "/path/to/holograim-mcp"
    }
  }
}
```

## Key Files

- `server.py` — MCP server entry point, all 39 tool definitions
- `config.py` — paths, thresholds, Ollama config
- `db/schema.py` — SQLite schema initialization
- `db/queries.py` — all database operations
- `memory/` — semantic (ChromaDB), associative (NetworkX), holographic (HDC)
- `analysis/` — tension, coherence, pattern analysis
- `congress/` — Paradigm-Congress-Ego deliberation pipeline
- `identity/` — self-model and session management
- `execution/` — sandboxed code execution (Python, Swift, Java, shell)
- `creative/` — fragment composition and project management
- `docs/` — Sovern iOS app design docs (Swift reference architecture)

## Dependencies

Requires Ollama running locally for Congress deliberation tools.
Default model: `qwen2.5:3b` with `llama3.2:1b` fallback.
Memory/belief tools work without Ollama.

## Testing

```bash
python -c "from server import mcp; print('Server loads OK')"
```
