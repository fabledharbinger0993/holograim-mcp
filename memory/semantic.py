from typing import Any
import chromadb
from chromadb.config import Settings
from config import CHROMA_PATH

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None

COLLECTION_NAME = "holograim_semantic"


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(
            path=CHROMA_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def add_to_semantic(
    mem_id: str,
    content: str,
    confidence: float,
    source: str,
    tags: list[str],
) -> None:
    col = _get_collection()
    col.add(
        documents=[content],
        ids=[mem_id],
        metadatas=[{
            "confidence": confidence,
            "source": source,
            "tags": ",".join(tags),
        }],
    )


def query_semantic(
    query: str,
    top_k: int = 5,
    min_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    col = _get_collection()
    count = col.count()
    if count == 0:
        return []

    results = col.query(
        query_texts=[query],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )

    hits: list[dict[str, Any]] = []
    ids = results.get("ids", [[]])[0]
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for mem_id, doc, meta, dist in zip(ids, docs, metas, distances):
        conf = float(meta.get("confidence", 0.0))
        if conf < min_confidence:
            continue
        # cosine distance → similarity score (lower distance = higher similarity)
        score = max(0.0, 1.0 - dist)
        hits.append({
            "id": mem_id,
            "content": doc,
            "confidence": conf,
            "source": meta.get("source", ""),
            "tags": meta.get("tags", "").split(",") if meta.get("tags") else [],
            "similarity_score": round(score, 4),
            "modality": "semantic",
        })

    hits.sort(key=lambda x: x["similarity_score"], reverse=True)
    return hits
