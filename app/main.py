from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import graph
from .agent import ask
from .ingest import ingest

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="LineageGuard")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Question(BaseModel):
    question: str


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.post("/api/ingest")
def api_ingest():
    try:
        return ingest()
    except Exception as e:
        print("ingest failed:", repr(e))
        raise HTTPException(500, f"Ingest failed: {e}")


@app.get("/api/graph")
def api_graph():
    nodes, seen, out = [], set(), []

    def add(kind: str, key: str):
        if (kind, key) not in seen:
            seen.add((kind, key))
            nodes.append({"id": f"{kind}:{key}", "label": key, "group": kind})

    try:
        edges = graph.rows(
            """
            MATCH (a)-[r]->(b)
            WHERE type(r) IN ['READS','WRITES','POWERED_BY','OWNS','FEEDS']
            RETURN labels(a)[0] AS al, coalesce(a.id, a.name) AS ak, type(r) AS rel,
                   labels(b)[0] AS bl, coalesce(b.id, b.name) AS bk
            """
        )
    except Exception as e:
        # Graph not created yet (sample workspace not loaded) -> return empty graph
        print("graph read failed:", repr(e))
        return {"nodes": [], "edges": []}

    for e in edges:
        add(e["al"], e["ak"])
        add(e["bl"], e["bk"])
        out.append({"from": f'{e["al"]}:{e["ak"]}', "to": f'{e["bl"]}:{e["bk"]}', "label": e["rel"]})
    return {"nodes": nodes, "edges": out}


@app.post("/api/ask")
def api_ask(body: Question):
    try:
        return ask(body.question)
    except Exception as e:
        print("ask failed:", repr(e))
        raise HTTPException(500, str(e))


@app.get("/api/findings")
def api_findings():
    try:
        return graph.rows(
            "MATCH (f:Finding) RETURN f.text AS text, f.table AS table, f.ts AS ts ORDER BY ts DESC LIMIT 20",
            graph=graph.memory_graph(),
        )
    except Exception:
        return []