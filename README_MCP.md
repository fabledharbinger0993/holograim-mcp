# HologrA.I.m MCP Server

Python MCP server exposing persistent, confidence-weighted cognitive memory and a multi-agent Congress deliberation engine to any connected Claude instance.

## Install

```bash
cd /Users/cameronkelly/FABLEDHARBINGER/GIT_REPOS/HologrAIm/mcp_server
pip install fastmcp chromadb networkx numpy --break-system-packages
```

## Run (verify startup)

```bash
python server.py
# Should output to stderr: HologrA.I.m MCP running on stdio
```

## Claude Desktop Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "holograim": {
      "command": "python3",
      "args": [
        "/Volumes/DJMT/FABLEDHARBINGER/GIT_REPOS/Holograim-mcp/holograim-mcp/server.py"
      ],
      "env": {}
    }
  }
}
```

Restart Claude Desktop after editing the config.

## Prerequisites

- Ollama running locally: `ollama serve`
- Model pulled: `ollama pull qwen2.5:3b`
- Python 3.11+

## Tools Exposed

| Tool | Description |
|------|-------------|
| `store_memory` | Store to all 3 modalities (requires confidence ≥ 0.7) |
| `query_memory` | Semantic + optional holographic recall |
| `get_beliefs_tool` | Retrieve belief nodes with optional tension analysis |
| `update_belief` | Adjust confidence, triggers state transitions + tension check |
| `congress_deliberate` | Full Paradigm→Congress→Ego via Ollama |
| `self_review` | Coherence score, dominance patterns, self-insight |
| `get_tensions_tool` | Oscillating/high-amplitude belief tensions |
| `decay_beliefs` | Weekly confidence decay pass |
| `graph_neighbors` | Associative graph traversal up to 4 hops |

## Data Storage

All data persists in `mcp_server/data/`:
- `holograim.db` — SQLite (beliefs, memories, congress logs, tensions)
- `chroma/` — ChromaDB semantic vectors
- `graph.gpickle` — NetworkX associative graph (pickle format)
- `holographic_composite.npy` — HDC composite array

## Ollama Troubleshooting

```bash
# Ensure Ollama is running
ollama serve

# Check model availability
curl http://localhost:11434/api/tags

# Pull model if missing
ollama pull qwen2.5:3b
```
