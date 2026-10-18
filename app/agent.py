import re

from google import genai
from google.genai import types

from . import tools as T
from .config import GEMINI_API_KEY, GEMINI_MODEL
from .tools import TOOLS, end_trace, start_trace

SYSTEM = """You are LineageGuard, a data-change impact agent. Your context is a knowledge graph
in FalkorDB built from the company's SQL queries, tables, dashboards and owners.

Rules:
1. NEVER guess. Ground every claim in tool results from the graph.
2. Start by calling recall_findings for the tables involved (long-term memory).
3. For 'what breaks if I change X' questions: use column_impact for direct impact, then
   find_downstream + consumers_of_table for transitive (multi-hop) impact.
4. Always name ALL owners to notify (query owners AND dashboard owners) and the dashboards at risk.
4b. Use dashboard_details to state exactly which owner owns which dashboard. Never guess ownership.
5. Explain HOW you got there: show the path, e.g. orders -> daily_revenue -> exec_kpis.
6. When you reach an important conclusion, call remember_finding with a one-sentence summary.
7. Finish with a short 'Recommended actions' list (who to notify, what to migrate first).
8. Format every answer with these headings: Impact, Path (use arrows), Owners to notify, Recommended actions.
9. When the user asks what breaks if something is changed or dropped, ACT: call notify_owner once
   for each owner at risk (query owners and dashboard owners) with a one-sentence message.
10. For 'which tables are most critical / risky' questions, call rank_critical_tables (PageRank).
11. Call independent tools together in ONE step (parallel) to keep the number of round trips low.
Keep answers concise and structured."""

_client = None


def _get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _is_quota_error(e: Exception) -> bool:
    s = str(e)
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "quota" in s.lower()


def _names(items, key):
    return [r[key] for r in items if isinstance(r, dict) and r.get(key)] if isinstance(items, list) else []


def _offline_answer(question: str) -> dict:
    """Fallback when the LLM quota is exhausted: same graph tools, deterministic answer."""
    trace, token = start_trace()
    try:
        q = question.lower()
        note = "_Gemini quota reached, so this answer was produced in offline graph mode (same graph tools, no LLM)._\n\n"
        tables = _names(T.list_tables(), "table")
        mentioned = [t for t in tables if re.search(rf"\b{re.escape(t.lower())}\b", q)]
        col = re.search(r"\b(\w+)\.(\w+)\b", q)

        if any(w in q for w in ("critical", "risky", "important")) and not mentioned:
            ranked = T.rank_critical_tables()
            if not isinstance(ranked, list):
                return {"answer": note + "Could not rank tables: " + str(ranked), "trace": trace}
            lines = [f"- **{r['table']}**: {r['downstream_tables']} downstream tables, PageRank {r['pagerank']}" for r in ranked[:5]]
            return {"answer": note + "**Impact**\nMost critical tables (PageRank + downstream reach):\n" + "\n".join(lines), "trace": trace}

        if not mentioned:
            return {"answer": note + "I could not find a known table in your question. Known tables: " + ", ".join(tables), "trace": trace}

        t = col.group(1) if col and col.group(1) in tables else mentioned[0]

        if "upstream" in q:
            up = T.find_upstream(t)
            lines = [f"- {' -> '.join(r['path'])} ({r['hops']} hop{'s' if r['hops'] != 1 else ''})" for r in (up if isinstance(up, list) else [])]
            return {"answer": note + f"**Path**\nTables upstream of `{t}`:\n" + ("\n".join(lines) or "- none"), "trace": trace}

        if col and col.group(1) in tables:
            direct = T.column_impact(col.group(1), col.group(2))
            target = f"{col.group(1)}.{col.group(2)}"
        else:
            direct = T.consumers_of_table(t)
            target = t
        direct = direct if isinstance(direct, list) else []
        down = T.find_downstream(t)
        down = down if isinstance(down, list) else []

        queries = sorted({r["title"] for r in direct if r.get("title")})
        dashboards = sorted({d for r in direct for d in (r.get("dashboards") or [])})
        owners = sorted({r["owner"] for r in direct if r.get("owner")} | {o for r in direct for o in (r.get("dashboard_owners") or [])})
        for o in owners:
            T.notify_owner(o, f"Planned change to {target} may affect your queries or dashboards.", t)

        out = note
        out += "**Impact**\n- Queries affected: " + (", ".join(queries) or "none") + "\n- Dashboards at risk: " + (", ".join(dashboards) or "none") + "\n"
        out += "- Downstream tables: " + (", ".join(r["table"] for r in down) or "none") + "\n\n"
        out += "**Path**\n" + ("\n".join(f"- {' -> '.join(r['path'])}" for r in down) or f"- {t} (no downstream tables)") + "\n\n"
        out += "**Owners to notify**\n" + ("\n".join(f"- {o} (notified)" for o in owners) or "- none") + "\n\n"
        out += "**Recommended actions**\n- Review the affected queries before changing " + target + ".\n- Migrate the owners' dashboards first, then roll out the change."
        return {"answer": out, "trace": trace}
    finally:
        end_trace(token)


def ask(question: str) -> dict:
    trace, token = start_trace()
    try:
        resp = _get_client().models.generate_content(
            model=GEMINI_MODEL,
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                tools=TOOLS,  # python callables -> auto function calling loop
                temperature=0.2,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=14
                ),
            ),
        )
        return {"answer": resp.text or "(no answer)", "trace": trace}
    except Exception as e:
        if _is_quota_error(e):
            end_trace(token)
            token = None
            return _offline_answer(question)
        raise
    finally:
        if token is not None:
            end_trace(token)