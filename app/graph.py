"""Thin FalkorDB helper layer."""
from falkordb import FalkorDB

from .config import (
    FALKOR_HOST,
    FALKOR_PORT,
    FALKOR_USERNAME,
    FALKOR_PASSWORD,
    GRAPH_NAME,
    MEMORY_GRAPH_NAME,
)

_db = None


def _client() -> FalkorDB:
    global _db
    if _db is None:
        _db = FalkorDB(
            host=FALKOR_HOST,
            port=FALKOR_PORT,
            username=FALKOR_USERNAME,
            password=FALKOR_PASSWORD,
            socket_connect_timeout=6,  # never hang the UI on a bad network
            socket_timeout=30,
        )
    return _db


def get_graph(name: str | None = None):
    return _client().select_graph(name or GRAPH_NAME)


def memory_graph():
    # Separate graph = isolated agent memory (FalkorDB holds many graphs per instance).
    return _client().select_graph(MEMORY_GRAPH_NAME)


def _clean(v):
    """Turn FalkorDB Node/Edge/Path objects into JSON-friendly values."""
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "properties"):
        out = dict(v.properties)
        if hasattr(v, "labels"):
            out["_labels"] = list(v.labels)
        if hasattr(v, "relation"):
            out["_type"] = v.relation
        return out
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def rows(cypher: str, params: dict | None = None, graph=None) -> list[dict]:
    """Run a READ-ONLY query and return a list of dicts."""
    g = graph or get_graph()
    res = g.ro_query(cypher, params or {})
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    return [dict(zip(cols, [_clean(x) for x in r])) for r in res.result_set]


def write(cypher: str, params: dict | None = None, graph=None):
    g = graph or get_graph()
    return g.query(cypher, params or {})


def reset_main_graph():
    try:
        get_graph().delete()
    except Exception:
        pass


def ensure_indexes():
    for stmt in (
        "CREATE INDEX FOR (t:Table) ON (t.name)",
        "CREATE INDEX FOR (q:Query) ON (q.id)",
        "CREATE INDEX FOR (c:Column) ON (c.id)",
        "CREATE INDEX FOR (d:Dashboard) ON (d.name)",
        "CREATE INDEX FOR (p:Person) ON (p.name)",
    ):
        try:
            write(stmt)
        except Exception:
            pass  # index already exists