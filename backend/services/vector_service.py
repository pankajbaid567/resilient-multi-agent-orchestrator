"""FAISS-based vector memory service for episodic within-task retrieval."""

from __future__ import annotations

import importlib.util
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_VECTOR_DISABLED_WARNING = "FAISS/sentence-transformers not available. Vector memory disabled."
_vector_warning_logged = False


def _log_vector_disabled_once() -> None:
    """Emit one warning when vector memory dependencies are unavailable."""
    global _vector_warning_logged
    if _vector_warning_logged:
        return
    logger.warning(_VECTOR_DISABLED_WARNING)
    _vector_warning_logged = True


class NoOpMemory:
    """Fallback memory implementation that stores nothing and returns no matches."""

    def store(self, text: str, metadata: dict | None = None) -> None:
        """No-op store."""
        return

    def search(self, query: str, top_k: int = 3, score_threshold: float = 1.5) -> List[dict]:
        """No-op search."""
        return []

    def clear(self):
        """No-op clear."""
        return

    @property
    def size(self) -> int:
        """No-op memory is always empty."""
        return 0


class VectorMemory:
    """FAISS-based vector memory for contextual retrieval."""

    def __init__(self):
        """Initialize lazy attributes for embedder and FAISS index."""
        self._embedder = None
        self._index = None
        self._documents: list[dict[str, Any]] = []
        self._initialized = False
        self._disabled = False

    def _ensure_initialized(self):
        """Lazy-initialize FAISS index and embedder on first use."""
        if self._initialized:
            return
        if self._disabled:
            raise ImportError(_VECTOR_DISABLED_WARNING)

        try:
            from sentence_transformers import SentenceTransformer
            import faiss
        except ImportError as exc:
            self._disabled = True
            raise ImportError(_VECTOR_DISABLED_WARNING) from exc

        self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        self._index = faiss.IndexFlatL2(384)
        self._initialized = True

    def store(self, text: str, metadata: dict | None = None) -> None:
        """Store a text with metadata as a FAISS embedding entry."""
        if not text or not text.strip():
            return

        try:
            self._ensure_initialized()
        except ImportError:
            _log_vector_disabled_once()
            return

        if self._embedder is None or self._index is None:
            return

        vector = self._embedder.encode([text], convert_to_numpy=True)
        vector = vector.astype("float32")
        self._index.add(vector)

        payload = {
            "text": text,
            "metadata": dict(metadata or {}),
        }
        self._documents.append(payload)

    def search(self, query: str, top_k: int = 3, score_threshold: float = 1.5) -> List[dict]:
        """Search for similar texts and return filtered nearest neighbors."""
        if not query or not query.strip():
            return []

        try:
            self._ensure_initialized()
        except ImportError:
            _log_vector_disabled_once()
            return []

        if self._embedder is None or self._index is None:
            return []

        if self.size == 0:
            return []

        query_vector = self._embedder.encode([query], convert_to_numpy=True)
        query_vector = query_vector.astype("float32")

        k = max(1, min(int(top_k), self.size))
        distances, indices = self._index.search(query_vector, k)

        results: list[dict[str, Any]] = []
        for distance, doc_index in zip(distances[0], indices[0]):
            idx = int(doc_index)
            score = float(distance)

            if idx < 0 or idx >= self.size:
                continue
            if score > float(score_threshold):
                continue

            document = self._documents[idx]
            results.append(
                {
                    "text": str(document.get("text", "")),
                    "metadata": dict(document.get("metadata") or {}),
                    "score": score,
                }
            )

        return results

    def clear(self):
        """Clear all stored documents and reset index."""
        self._documents = []
        if self._index is not None and hasattr(self._index, "reset"):
            self._index.reset()

    @property
    def size(self) -> int:
        """Number of documents stored."""
        return len(self._documents)

    @property
    def disabled(self) -> bool:
        """Whether this instance has disabled vector operations due to missing backend."""
        return self._disabled


class MemoryManager:
    """Manages separate VectorMemory instances per task."""

    def __init__(self):
        self._memories: Dict[str, VectorMemory | NoOpMemory] = {}
        self._backend_available: bool | None = None

    def get_memory(self, task_id: str) -> VectorMemory | NoOpMemory:
        """Get or create memory for a specific task."""
        key = str(task_id)
        existing = self._memories.get(key)
        if existing is not None:
            if isinstance(existing, VectorMemory) and existing.disabled:
                noop = NoOpMemory()
                self._memories[key] = noop
                return noop
            return existing

        if self._is_backend_available():
            memory: VectorMemory | NoOpMemory = VectorMemory()
        else:
            memory = NoOpMemory()

        self._memories[key] = memory
        return memory

    def clear_memory(self, task_id: str):
        """Delete memory for a task."""
        key = str(task_id)
        memory = self._memories.pop(key, None)
        if memory is not None:
            memory.clear()

    def store_step_result(self, task_id: str, step_id: str, step_name: str, output: str):
        """Convenience: store a step's result."""
        text = f"Step '{step_name}': {str(output)[:500]}"
        metadata = {
            "task_id": task_id,
            "step_id": step_id,
            "step_name": step_name,
            "type": "result",
        }
        memory = self.get_memory(task_id)
        memory.store(text, metadata)
        self._downgrade_if_disabled(task_id, memory)

    def store_error(self, task_id: str, step_id: str, error: str):
        """Convenience: store an error for future reference."""
        text = f"Error in step '{step_id}': {str(error)[:500]}"
        metadata = {
            "task_id": task_id,
            "step_id": step_id,
            "step_name": step_id,
            "type": "error",
        }
        memory = self.get_memory(task_id)
        memory.store(text, metadata)
        self._downgrade_if_disabled(task_id, memory)

    def query_relevant_context(self, task_id: str, query: str, top_k: int = 3) -> List[str]:
        """Return relevant context strings for a step."""
        memory = self.get_memory(task_id)
        results = memory.search(query, top_k)
        self._downgrade_if_disabled(task_id, memory)
        return [str(match.get("text", "")) for match in results]

    def _is_backend_available(self) -> bool:
        """Check dependency availability once without importing heavy model modules."""
        if self._backend_available is not None:
            return self._backend_available

        try:
            faiss_spec = importlib.util.find_spec("faiss")
            st_spec = importlib.util.find_spec("sentence_transformers")
            if faiss_spec is None or st_spec is None:
                raise ImportError(_VECTOR_DISABLED_WARNING)
            self._backend_available = True
        except ImportError:
            self._backend_available = False
            _log_vector_disabled_once()

        return self._backend_available

    def _downgrade_if_disabled(self, task_id: str, memory: VectorMemory | NoOpMemory) -> None:
        """Replace disabled vector memory with a stable no-op memory instance."""
        if isinstance(memory, VectorMemory) and memory.disabled:
            self._memories[str(task_id)] = NoOpMemory()


class VectorService:
    """Backward-compatible vector service facade over VectorMemory."""

    def __init__(self) -> None:
        self._memory = VectorMemory()

    def add_documents(self, texts: list[str]) -> None:
        """Back-compat helper to store plain text documents."""
        for text in texts:
            self._memory.store(text, metadata={"type": "result"})

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Back-compat search API returning rank/document/distance records."""
        matches = self._memory.search(query=query, top_k=k, score_threshold=1.5)
        results: list[dict[str, Any]] = []
        for rank, match in enumerate(matches, start=1):
            results.append(
                {
                    "rank": rank,
                    "document": match.get("text", ""),
                    "distance": float(match.get("score", 0.0)),
                }
            )
        return results


_manager: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    """Return singleton memory manager."""
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager


__all__ = [
    "MemoryManager",
    "NoOpMemory",
    "VectorMemory",
    "VectorService",
    "get_memory_manager",
]
