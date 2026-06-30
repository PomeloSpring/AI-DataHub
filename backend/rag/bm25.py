"""BM25 — Okapi BM25 sparse retrieval for Chinese metadata.

Pure Python implementation, no external dependencies.
Designed for table/column/terminology metadata search in NL2SQL pipelines.

Usage:
    from backend.rag.bm25 import BM25

    bm25 = BM25()
    bm25.index([["订单", "数量", "统计"], ["用户", "信息", "查询"], ...])
    results = bm25.search(["订单", "数量"], top_k=5)
    # [(doc_index, score), ...]
"""

import math
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class BM25:
    """Okapi BM25 scoring over a pre-tokenized document corpus.

    Args:
        k1: Term frequency saturation parameter (default 1.5).
        b: Document length normalization parameter (default 0.75).
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        # Indexed state (populated by index())
        self._indexed = False
        self._doc_count = 0
        self._avg_dl = 0.0
        self._doc_lens: list[int] = []
        self._term_freqs: list[dict[str, int]] = []  # per-doc TF
        self._df: dict[str, int] = defaultdict(int)   # document frequency per term
        self._idf: dict[str, float] = {}               # precomputed IDF per term

    def index(self, documents: list[list[str]]) -> None:
        """Build inverted index from tokenized documents.

        Args:
            documents: List of token lists, one per document.
                       e.g. [["订单", "数量"], ["用户", "信息"], ...]
        """
        self._doc_count = len(documents)
        self._doc_lens = []
        self._term_freqs = []
        self._df = defaultdict(int)

        if self._doc_count == 0:
            self._indexed = True
            return

        # Pass 1: build per-doc TF and global DF
        total_len = 0
        for tokens in documents:
            tf = defaultdict(int)
            for token in tokens:
                tf[token] += 1
            self._term_freqs.append(dict(tf))
            doc_len = len(tokens)
            self._doc_lens.append(doc_len)
            total_len += doc_len
            # DF: count each unique term in this doc once
            for term in tf:
                self._df[term] += 1

        self._avg_dl = total_len / self._doc_count

        # Pass 2: precompute IDF for all terms
        # IDF formula: log((N - df + 0.5) / (df + 0.5) + 1)
        for term, df in self._df.items():
            self._idf[term] = math.log(
                (self._doc_count - df + 0.5) / (df + 0.5) + 1.0
            )

        self._indexed = True
        logger.debug(
            "BM25 indexed %d documents, avg_dl=%.1f, vocab=%d",
            self._doc_count, self._avg_dl, len(self._df),
        )

    def search(self, query_tokens: list[str], top_k: int = 20) -> list[tuple[int, float]]:
        """Score all documents against the query and return top-k results.

        Args:
            query_tokens: Tokenized query, e.g. ["订单", "数量"].
            top_k: Maximum number of results to return.

        Returns:
            List of (doc_index, score) sorted by score descending.
        """
        if not self._indexed or self._doc_count == 0:
            return []

        # Deduplicate query tokens (each term scored once per query)
        unique_tokens = list(set(query_tokens))

        # Filter to tokens that exist in the corpus
        query_terms = [t for t in unique_tokens if t in self._idf]
        if not query_terms:
            return []

        # Score all documents
        scores = []
        for doc_idx in range(self._doc_count):
            score = self._score_doc(doc_idx, query_terms)
            if score > 0:
                scores.append((doc_idx, score))

        # Sort by score descending
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]

    def _score_doc(self, doc_idx: int, query_terms: list[str]) -> float:
        """Compute BM25 score for a single document against query terms."""
        tf = self._term_freqs[doc_idx]
        doc_len = self._doc_lens[doc_idx]

        score = 0.0
        for term in query_terms:
            term_tf = tf.get(term, 0)
            if term_tf == 0:
                continue

            idf = self._idf[term]
            # BM25 TF component: (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl/avgdl))
            numerator = term_tf * (self.k1 + 1)
            denominator = term_tf + self.k1 * (
                1.0 - self.b + self.b * doc_len / self._avg_dl
            )
            score += idf * numerator / denominator

        return score

    @property
    def is_empty(self) -> bool:
        return not self._indexed or self._doc_count == 0


def rrf_merge(
    rankings: list[list[str]],
    k: int = 60,
    weights: list[float] = None,
) -> list[tuple[str, float]]:
    """Merge multiple rankings using Reciprocal Rank Fusion (RRF).

    RRF_score(d) = Σ weight_i / (k + rank_i(d))

    Args:
        rankings: Each element is an ordered list of item IDs (table names).
        k: RRF constant (default 60, standard value from the paper).
        weights: Per-ranking weights (default all 1.0).

    Returns:
        List of (item_id, rrf_score) sorted by score descending.
    """
    if weights is None:
        weights = [1.0] * len(rankings)

    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + weight / (k + rank)

    merged = sorted(scores.items(), key=lambda x: -x[1])
    return merged
