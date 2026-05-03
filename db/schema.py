import sqlite3
from config import DB_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS beliefs (
    id TEXT PRIMARY KEY,
    stance TEXT NOT NULL,
    domain TEXT NOT NULL,
    confidence REAL NOT NULL,
    weight INTEGER NOT NULL,
    state TEXT NOT NULL,
    is_core INTEGER DEFAULT 0,
    reasoning TEXT,
    emerged_from TEXT,
    created_at REAL NOT NULL,
    last_reinforced REAL,
    revision_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS belief_revisions (
    id TEXT PRIMARY KEY,
    belief_id TEXT NOT NULL,
    previous_confidence REAL,
    new_confidence REAL,
    reason TEXT,
    timestamp REAL NOT NULL,
    FOREIGN KEY (belief_id) REFERENCES beliefs(id)
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT,
    tags TEXT,
    created_at REAL NOT NULL,
    reinforcement_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS congress_logs (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    paradigm_assessment TEXT,
    advocate_position TEXT,
    skeptic_position TEXT,
    synthesizer_position TEXT,
    ethics_review TEXT,
    ego_response TEXT,
    coherence_score REAL,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tensions (
    id TEXT PRIMARY KEY,
    belief_id TEXT NOT NULL,
    oscillation_count INTEGER DEFAULT 0,
    amplitude REAL DEFAULT 0.0,
    stability_score REAL DEFAULT 1.0,
    tension_reason TEXT,
    flagged_at REAL,
    resolved INTEGER DEFAULT 0,
    FOREIGN KEY (belief_id) REFERENCES beliefs(id)
);

CREATE TABLE IF NOT EXISTS memory_tesseracts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    cue_terms TEXT,
    semantic_axis REAL DEFAULT 0.5,
    relational_axis REAL DEFAULT 0.5,
    temporal_axis REAL DEFAULT 0.5,
    epistemic_axis REAL DEFAULT 0.5,
    metadata TEXT,
    usage_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_tesseract_links (
    id TEXT PRIMARY KEY,
    tesseract_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('memory', 'belief', 'congress_log', 'concept', 'tension')),
    target_id TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    created_at REAL NOT NULL,
    FOREIGN KEY (tesseract_id) REFERENCES memory_tesseracts(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_tesseracts_updated_at
    ON memory_tesseracts(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_tesseract_links_tesseract
    ON memory_tesseract_links(tesseract_id);

CREATE INDEX IF NOT EXISTS idx_memory_tesseract_links_target
    ON memory_tesseract_links(target_type, target_id);

-- ── Identity / Self-Model ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS identity_state (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL DEFAULT 'Holograim',
    voice_signature TEXT,
    aesthetic_notes TEXT,
    values_json TEXT,
    creative_style TEXT,
    session_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- ── Creative Fragments ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS creative_fragments (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    fragment_type TEXT NOT NULL CHECK (
        fragment_type IN ('prose','code','structure','question',
                          'aesthetic_note','design_decision','observation')
    ),
    language TEXT,
    content TEXT NOT NULL,
    title TEXT,
    tags TEXT,
    author TEXT NOT NULL DEFAULT 'ai' CHECK (author IN ('human','ai','joint')),
    memory_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_creative_fragments_project
    ON creative_fragments(project_id);

CREATE INDEX IF NOT EXISTS idx_creative_fragments_type
    ON creative_fragments(fragment_type);

-- ── Projects ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active','paused','archived')
    ),
    intent TEXT,
    aesthetic_direction TEXT,
    human_owner TEXT,
    ai_voice_notes TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- ── Open Questions ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS open_questions (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    domain TEXT,
    context TEXT,
    priority REAL DEFAULT 0.5,
    held_since REAL NOT NULL,
    resolved INTEGER DEFAULT 0,
    resolved_at REAL,
    resolution_insight TEXT,
    tags TEXT
);

CREATE INDEX IF NOT EXISTS idx_open_questions_resolved
    ON open_questions(resolved, held_since DESC);

-- ── Execution Logs ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS execution_logs (
    id TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    code_input TEXT NOT NULL,
    stdout TEXT,
    stderr TEXT,
    exit_code INTEGER,
    duration_ms REAL,
    project_id TEXT,
    created_at REAL NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
