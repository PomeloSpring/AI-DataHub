"""RAG Retrieval Strategies — pluggable metadata retrieval for NL2SQL.

Usage:
    from services.datamind.rag.strategies import get_strategy

    strategy = get_strategy("hybrid")
    result = strategy.retrieve(question="...", datasource_id=123)
"""

from services.datamind.rag.strategies.base import RetrievalStrategy
from services.datamind.rag.strategies.full_table import FullTableStrategy
from services.datamind.rag.strategies.column_first import ColumnFirstStrategy
from services.datamind.rag.strategies.two_stage import TwoStageStrategy
from services.datamind.rag.strategies.bidirectional import BidirectionalStrategy
from services.datamind.rag.strategies.graph import GraphStrategy
from services.datamind.rag.strategies.hybrid import HybridStrategy
from services.datamind.rag.strategies.ontology import OntologyFirstStrategy

# ── Strategy Registry ──────────────────────────────────────────────

STRATEGIES: dict[str, type[RetrievalStrategy]] = {
    "hybrid": HybridStrategy,
    "full_table": FullTableStrategy,
    "column_first": ColumnFirstStrategy,
    "two_stage": TwoStageStrategy,
    "bidirectional": BidirectionalStrategy,
    "graph": GraphStrategy,
    "ontology_first": OntologyFirstStrategy,
}

# Allowed values for config/UI
STRATEGY_CHOICES = list(STRATEGIES.keys())
DEFAULT_STRATEGY = "hybrid"


def get_strategy(name: str = None) -> RetrievalStrategy:
    """Get a retrieval strategy instance by name.

    Args:
        name: Strategy name. Falls back to DEFAULT_STRATEGY if not found.

    Returns:
        Instantiated strategy object.
    """
    if name and name in STRATEGIES:
        return STRATEGIES[name]()
    return STRATEGIES[DEFAULT_STRATEGY]()


def get_strategy_from_config(model_id: int = None) -> RetrievalStrategy:
    """Get the default strategy from system config (adh_system_config)."""
    try:
        from services.datamind.api.model_config import get_system_config
        name = get_system_config("retrieval_strategy", DEFAULT_STRATEGY)
        return get_strategy(name)
    except Exception:
        return get_strategy(DEFAULT_STRATEGY)


__all__ = [
    "RetrievalStrategy",
    "STRATEGIES",
    "STRATEGY_CHOICES",
    "DEFAULT_STRATEGY",
    "get_strategy",
    "get_strategy_from_config",
]
