"""
creative/composer.py

Block-based creative composition layer.  Works with creative_fragments and
projects tables to enable long, low-cost composition without loading everything
into context at once.

Design principles:
- A fragment is an atomic unit: one piece of prose, code, a design note,
  an open question, an aesthetic observation.
- A project is the container.  It carries voice intent and aesthetic direction.
- compose_fragments assembles selected fragments into a named document without
  requiring all fragments to exist in memory simultaneously.
- The AI and human are equal co-authors.  author ∈ {human, ai, joint}.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from db.queries import (
    insert_fragment,
    get_fragments,
    get_fragment_by_id,
    insert_project,
    get_projects,
    get_project_by_id,
    update_project,
)

# Valid fragment types — mirrors schema CHECK constraint
FRAGMENT_TYPES = {
    "prose", "code", "structure", "question",
    "aesthetic_note", "design_decision", "observation",
}

# Valid author values
AUTHOR_VALUES = {"human", "ai", "joint"}

# Supported output formats for compose_fragments
OUTPUT_FORMATS = {"markdown", "html", "plain", "code_only"}


def create_fragment(
    content: str,
    fragment_type: str,
    author: str = "ai",
    project_id: Optional[str] = None,
    language: Optional[str] = None,
    title: Optional[str] = None,
    tags: Optional[list[str]] = None,
    memory_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Store a creative fragment.  Returns the new fragment record.
    """
    fragment_type = fragment_type.strip().lower()
    if fragment_type not in FRAGMENT_TYPES:
        return {
            "error": f"Invalid fragment_type '{fragment_type}'. "
                     f"Allowed: {sorted(FRAGMENT_TYPES)}"
        }

    author = author.strip().lower()
    if author not in AUTHOR_VALUES:
        return {
            "error": f"Invalid author '{author}'. Allowed: {sorted(AUTHOR_VALUES)}"
        }

    if project_id is not None:
        proj = get_project_by_id(project_id)
        if not proj:
            return {"error": f"Project '{project_id}' not found."}

    fragment_id = insert_fragment(
        content=content,
        fragment_type=fragment_type,
        author=author,
        project_id=project_id,
        language=language,
        title=title,
        tags=tags,
        memory_id=memory_id,
    )

    return get_fragment_by_id(fragment_id) or {"error": "Failed to retrieve created fragment."}


def list_fragments(
    project_id: Optional[str] = None,
    fragment_type: Optional[str] = None,
    author: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return fragments matching filters.  Tags are parsed from JSON."""
    rows = get_fragments(
        project_id=project_id,
        fragment_type=fragment_type,
        author=author,
        limit=limit,
    )
    for r in rows:
        if isinstance(r.get("tags"), str):
            try:
                r["tags"] = json.loads(r["tags"])
            except Exception:
                r["tags"] = []
    return rows


def compose_fragments(
    fragment_ids: list[str],
    title: str = "Untitled Composition",
    output_format: str = "markdown",
) -> dict[str, Any]:
    """
    Assemble a list of fragments (by ID) into a single named output string.

    Ordering: follows the order of fragment_ids as provided.
    Format options: markdown, html, plain, code_only.

    Returns the composed document as a string and metadata about what was
    included, skipped, and what the composition looks like.
    """
    output_format = output_format.strip().lower()
    if output_format not in OUTPUT_FORMATS:
        return {
            "error": f"Invalid output_format '{output_format}'. "
                     f"Allowed: {sorted(OUTPUT_FORMATS)}"
        }

    found: list[dict] = []
    missing: list[str] = []

    for fid in fragment_ids:
        row = get_fragment_by_id(fid)
        if row:
            if isinstance(row.get("tags"), str):
                try:
                    row["tags"] = json.loads(row["tags"])
                except Exception:
                    row["tags"] = []
            found.append(row)
        else:
            missing.append(fid)

    if not found:
        return {
            "error": "No fragments found for the given IDs.",
            "missing_ids": missing,
            "document": "",
        }

    document = _render(title=title, fragments=found, fmt=output_format)

    return {
        "title": title,
        "format": output_format,
        "fragment_count": len(found),
        "missing_ids": missing,
        "char_count": len(document),
        "document": document,
        "composed_at": time.time(),
    }


def _render(title: str, fragments: list[dict], fmt: str) -> str:
    """Internal renderer — produce the final document string."""
    if fmt == "markdown":
        parts = [f"# {title}\n"]
        for frag in fragments:
            frag_title = frag.get("title") or frag.get("fragment_type", "fragment").replace("_", " ").title()
            ft = frag.get("fragment_type", "prose")
            content = frag.get("content", "")
            author = frag.get("author", "ai")
            parts.append(f"\n## {frag_title}\n")
            parts.append(f"*Author: {author}*\n")
            if ft == "code":
                lang = frag.get("language") or ""
                parts.append(f"\n```{lang}\n{content}\n```\n")
            else:
                parts.append(f"\n{content}\n")
        return "\n".join(parts)

    elif fmt == "html":
        html_parts = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="UTF-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            f"  <title>{_escape_html(title)}</title>",
            "  <style>",
            "    body { font-family: 'Georgia', serif; max-width: 800px; margin: 3rem auto; "
            "padding: 0 1.5rem; line-height: 1.7; color: #1a1a1a; }",
            "    h1 { font-size: 2rem; border-bottom: 2px solid #333; padding-bottom: .5rem; }",
            "    h2 { font-size: 1.3rem; color: #444; margin-top: 2rem; }",
            "    pre { background: #f4f4f4; padding: 1rem; border-radius: 4px; "
            "overflow-x: auto; font-size: .9rem; }",
            "    code { font-family: 'Menlo', 'Consolas', monospace; }",
            "    .author { font-style: italic; color: #888; font-size: .85rem; }",
            "    .fragment-type { text-transform: uppercase; letter-spacing: .08em; "
            "font-size: .75rem; color: #aaa; }",
            "  </style>",
            "</head>",
            "<body>",
            f"  <h1>{_escape_html(title)}</h1>",
        ]
        for frag in fragments:
            frag_title = frag.get("title") or frag.get("fragment_type", "fragment").replace("_", " ").title()
            ft = frag.get("fragment_type", "prose")
            content = frag.get("content", "")
            author = frag.get("author", "ai")
            html_parts.append(f'  <section>')
            html_parts.append(f'    <h2>{_escape_html(frag_title)}</h2>')
            html_parts.append(f'    <p class="author">— {_escape_html(author)}</p>')
            if ft == "code":
                lang = frag.get("language") or ""
                html_parts.append(
                    f'    <pre><code class="language-{_escape_html(lang)}">'
                    f'{_escape_html(content)}</code></pre>'
                )
            else:
                paras = content.split("\n\n")
                for p in paras:
                    if p.strip():
                        html_parts.append(f"    <p>{_escape_html(p.strip())}</p>")
            html_parts.append("  </section>")
        html_parts += ["</body>", "</html>"]
        return "\n".join(html_parts)

    elif fmt == "code_only":
        code_parts = []
        for frag in fragments:
            if frag.get("fragment_type") == "code":
                lang = frag.get("language") or ""
                code_parts.append(f"// --- {frag.get('title') or 'fragment'} ({lang}) ---")
                code_parts.append(frag.get("content", ""))
                code_parts.append("")
        return "\n".join(code_parts)

    else:  # plain
        parts = [title, "=" * len(title), ""]
        for frag in fragments:
            frag_title = frag.get("title") or frag.get("fragment_type", "fragment")
            parts.append(f"[{frag_title}]")
            parts.append(frag.get("content", ""))
            parts.append("")
        return "\n".join(parts)


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ── Project helpers ───────────────────────────────────────────────────────────

def create_project(
    name: str,
    description: str = "",
    intent: str = "",
    aesthetic_direction: str = "",
    human_owner: str = "human",
    ai_voice_notes: str = "",
) -> dict[str, Any]:
    """Create a new co-authorship project.  Returns the new project record."""
    name = name.strip()
    if not name:
        return {"error": "Project name is required."}
    project_id = insert_project(
        name=name,
        description=description,
        intent=intent,
        aesthetic_direction=aesthetic_direction,
        human_owner=human_owner,
        ai_voice_notes=ai_voice_notes,
    )
    return get_project_by_id(project_id) or {"error": "Failed to retrieve created project."}


def get_project_summary(project_id: str, fragment_limit: int = 10) -> dict[str, Any]:
    """Return a project and its most recent fragments."""
    project = get_project_by_id(project_id)
    if not project:
        return {"error": f"Project '{project_id}' not found."}
    fragments = list_fragments(project_id=project_id, limit=fragment_limit)
    return {
        "project": project,
        "fragment_count": len(fragments),
        "recent_fragments": fragments,
    }
