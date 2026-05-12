from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
import asyncio
import os, json, re, time, requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import feedparser  # arXiv Atom

# ---------------------------
# Env / Client
# ---------------------------
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in .env")

raw_model = os.getenv("MODEL_NAME", "gpt-4o-mini") or "gpt-4o-mini"
MODEL_NAME = raw_model.split("#", 1)[0].strip().strip('"').strip("'")

ALLOW_ORIGIN = os.getenv("ALLOW_ORIGIN", "*")
DEV_MODE = (os.getenv("DEV_MODE", "1") == "1")

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOW_ORIGIN] if ALLOW_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# Utilities
# ---------------------------
def safe_json_loads(text: str, default=None):
    try:
        return json.loads(text)
    except Exception:
        return default

def _trim_6000(s: str) -> str:
    if len(s) <= 6000: return s
    return (s[:6000].rsplit(".", 1)[0] + ".") if "." in s[:6000] else s[:6000]

# ---------------------------
# Wikipedia context (with TTL cache to avoid refetching per node click)
# ---------------------------
_WIKI_CACHE: dict[str, tuple[float, str]] = {}
_WIKI_TTL_SEC = 60 * 30  # 30 min

def _wiki_title(topic: str) -> str:
    # Wikipedia accepts spaces-as-underscores; URL-encode to handle '#', '/', '?', etc.
    return quote(topic.strip().replace(" ", "_"), safe="_")

def fetch_context(topic: str) -> str:
    key = topic.strip().lower()
    now = time.time()
    cached = _WIKI_CACHE.get(key)
    if cached and now - cached[0] < _WIKI_TTL_SEC:
        return cached[1]

    title = _wiki_title(topic)
    parts = []
    try:
        r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=8)
        if r.ok:
            s = r.json().get("extract", "")
            if s: parts.append(s)
    except Exception as e:
        print("Wiki summary error:", e)

    try:
        rel = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/related/{title}", timeout=8)
        if rel.ok:
            pages = rel.json().get("pages", [])[:2]
            for p in pages:
                ex = p.get("extract", "")
                if ex: parts.append(ex)
    except Exception as e:
        print("Wiki related error:", e)

    combined = "\n".join(parts).strip()
    out = _trim_6000(combined if combined else topic)
    _WIKI_CACHE[key] = (now, out)
    return out

def fetch_wiki_summary(title: str) -> str:
    """Single-page wiki summary by exact title; safe fallback to title."""
    t = _wiki_title(title)
    try:
        r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}", timeout=6)
        if r.ok:
            return r.json().get("extract", "") or title
    except Exception as e:
        print("Wiki node summary error:", e)
    return title


# ---------------------------
# Crossref & arXiv sources
# ---------------------------
def fetch_crossref(query: str, rows: int = 6):
    endpoint = "https://api.crossref.org/works"
    params = { "query": query, "rows": rows, "select": "title,URL,author,issued,type" }
    out = []
    try:
        r = requests.get(endpoint, params=params, timeout=8)
        if r.ok:
            items = r.json().get("message", {}).get("items", [])
            for it in items:
                title = (it.get("title") or [""])[0]
                it_url = it.get("URL")
                year = None
                issued = (it.get("issued") or {}).get("date-parts", [])
                if issued and issued[0]:
                    year = issued[0][0]
                if title and it_url:
                    out.append({ "title": title.strip(), "url": it_url, "year": year, "source": "Crossref" })
    except Exception as e:
        print("Crossref error:", e)
    return out

def fetch_arxiv(query: str, max_results: int = 6):
    # Don't pass URL to feedparser.parse directly — it has no timeout and can hang for minutes.
    # Fetch with requests (bounded timeout), then parse the bytes.
    url = "http://export.arxiv.org/api/query"
    params = {"search_query": f"all:{query}", "start": 0, "max_results": max_results}
    out = []
    try:
        r = requests.get(url, params=params, timeout=6)
        if not r.ok:
            return out
        feed = feedparser.parse(r.content)
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            link = entry.get("link")
            year = None
            published = entry.get("published")
            if published:
                year = published[:4]
            if title and link:
                out.append({
                    "title": title,
                    "url": link,
                    "year": int(year) if (year and year.isdigit()) else None,
                    "source": "arXiv"
                })
    except Exception as e:
        print("arXiv error:", e)
    return out

def gather_sources(query: str, max_each: int = 4):
    papers = []
    papers.extend(fetch_crossref(query, rows=max_each+2))
    papers.extend(fetch_arxiv(query, max_results=max_each+2))
    seen = set(); unique = []
    for p in papers:
        k = (p["title"].lower(), p["url"])
        if k not in seen:
            seen.add(k); unique.append(p)
    return unique[:10]

# ---------------------------
# Concept map (unchanged behavior)
# ---------------------------
def call_model_map(topic: str, context_text: str) -> dict:
    articles = gather_sources(topic, max_each=4)
    bullets = "\n".join(f"- {a['title']} ({a.get('year') or ''}) — {a['url']} [{a['source']}]" for a in articles)

    system_msg = (
        "You create compact concept maps for research topics.\n"
        "Return ONLY JSON with keys: nodes (array), links (array), gaps (array).\n"
        "Each node: {\"id\": string, \"label\": string, \"val\": number, \"color\": string,\n"
        "            \"summary\": string (1–2 lines), \"sources\": [ {title,url,year,source} ] }.\n"
        "Each link: {\"source\": string, \"target\": string, \"relation\": string}.\n"
        "10–20 nodes max. Short, precise labels. 'val' ~ 4..12.\n"
        "Attach up to 3 relevant items from the ARTICLES list to each node's 'sources'."
    )
    user_msg = (
        f"TOPIC:\n{topic}\n\n"
        f"WIKIPEDIA CONTEXT (may be partial):\n{_trim_6000(context_text)}\n\n"
        f"ARTICLES (titles/urls):\n{bullets}\n\n"
        "OUTPUT: Return JSON only. No prose, no markdown."
    )

    chat = client.chat.completions.create(
        model=MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[{"role":"system","content":system_msg},{"role":"user","content":user_msg}]
    )
    raw = chat.choices[0].message.content or "{}"
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    data = safe_json_loads(raw, default={}) or {}
    data.setdefault("nodes", []); data.setdefault("links", []); data.setdefault("gaps", [])
    for n in data["nodes"]:
        n.setdefault("val", 6); n.setdefault("color", "#58c7ff")
        n.setdefault("label", n.get("id","")); n.setdefault("summary", ""); n.setdefault("sources", [])
    return data


# ------------- speed-first map generator (no Crossref/arXiv/Wiki calls) -------------
def call_model_map_light(topic: str) -> dict:
    """Fast: just nodes + links. No external grounding."""
    system_msg = (
        "You create compact concept maps for a research topic.\n"
        "Return ONLY JSON with keys: nodes (array), links (array), gaps (array).\n"
        "Each node: {\"id\": string, \"label\": string, \"val\": number, \"color\": string, \"summary\": string}.\n"
        "10–20 nodes max. Short, precise, non-duplicative labels. 'val' ~ 4..12. Keep summaries 1 sentence max."
    )
    user_msg = (
        f"TOPIC:\n{topic}\n\n"
        "OUTPUT: JSON only. No prose, no markdown."
    )

    chat = client.chat.completions.create(
        model=MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system_msg},
                  {"role": "user", "content": user_msg}]
    )
    raw = chat.choices[0].message.content or "{}"
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    data = safe_json_loads(raw, default={}) or {}
    data.setdefault("nodes", []); data.setdefault("links", []); data.setdefault("gaps", [])
    for n in data["nodes"]:
        n.setdefault("val", 6)
        n.setdefault("color", "#58c7ff")
        n.setdefault("label", n.get("id",""))
        n.setdefault("summary", "")
        n.setdefault("sources", [])
    return data


# ---------------------------
# Dossier (strict spec) + robust fallback
# ---------------------------
SYSTEM_DOSSIER = """You are a research assistant that writes compact, grounded dossiers for a subtopic node.

CRITICAL CONSTRAINTS:
- The dossier MUST be about the NODE as it relates to the TOPIC. If the node label is ambiguous, choose the interpretation consistent with the TOPIC and standard usage in that field.
- Do NOT drift to unrelated meanings (e.g., chemical N4 when the topic is Quantum Physics).
- Prefer information aligned with the provided WIKIPEDIA CONTEXTS and ARTICLES; if unsure, state uncertainty briefly rather than inventing.

Return ONLY JSON with keys:
- id, label, definition (2–4 sentences)
- key_ideas (3–7)
- aliases (0–6)
- papers (4–6) each {title, year, url, reason}
- quotes (0–3) {quote, source_title, url} (≤200 chars)
- datasets_tools (0–6) {name, url, note}
- controversies (0–6)
- search_prompts (3–8)
- timeline (0–6) {year, event}
"""

def build_user_prompt(topic: str, node_id: str, context_text: str, articles: list[dict]) -> str:
    bullets = "\n".join(f"- {a['title']} ({a.get('year') or ''}) — {a['url']} [{a.get('source','')}]" for a in articles)
    return (
        f"TOPIC: {topic}\n"
        f"NODE:  {node_id}\n\n"
        f"WIKIPEDIA CONTEXT (may be partial):\n{_trim_6000(context_text)}\n\n"
        f"ARTICLES (titles/urls):\n{bullets}\n\n"
        "OUTPUT: Return JSON only. No prose outside of JSON."
    )

def _fallback_dossier(topic: str, node_id: str, ctx: str) -> dict:
    """If LLM fails, return a decent dossier purely from sources."""
    srcs = gather_sources(f"{node_id} {topic}", max_each=4)
    definition = (ctx.split("\n", 1)[0] if ctx else "")[:500]
    papers = [{"title": s["title"], "year": s.get("year"), "url": s["url"], "reason": "Relevant to the node/topic."} for s in srcs[:6]]
    # Heuristic timeline: distinct years from papers
    years = sorted({p["year"] for p in papers if p.get("year")}, key=int)
    timeline = [{"year": int(y), "event": f"Notable publication related to {node_id}"} for y in years][:6]
    prompts = [
        f"{node_id} overview",
        f"{node_id} {topic} tutorial",
        f"{node_id} site:arxiv.org",
        f"{node_id} site:scholar.google.com",
        f"{node_id} reproducible code github"
    ]
    return {
        "id": node_id, "label": node_id,
        "definition": definition or "",
        "key_ideas": [], "aliases": [],
        "papers": papers,
        "quotes": [],
        "datasets_tools": [],
        "controversies": [],
        "search_prompts": prompts,
        "timeline": timeline
    }

def call_model_dossier(topic: str, node_id: str, context_text: str) -> dict:
    articles = gather_sources(f"{node_id} {topic}", max_each=4)
    user_msg = build_user_prompt(topic, node_id, context_text, articles)

    chat = client.chat.completions.create(
        model=MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_DOSSIER},
            {"role": "user", "content": user_msg}
        ]
    )
    raw = chat.choices[0].message.content or "{}"
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    data = safe_json_loads(raw, default={}) or {}

    dossier = {
        # Always pin the id to the requested node so the frontend cache key matches,
        # regardless of whatever the LLM hallucinates here.
        "id": node_id,
        "label": data.get("label") or node_id,
        "definition": data.get("definition") or "",
        "key_ideas": data.get("key_ideas") or [],
        "aliases": data.get("aliases") or [],
        "papers": data.get("papers") or [],
        "quotes": data.get("quotes") or [],
        "datasets_tools": data.get("datasets_tools") or [],
        "controversies": data.get("controversies") or [],
        "search_prompts": data.get("search_prompts") or [],
        "timeline": data.get("timeline") or []
    }

    # Normalize papers
    fixed_papers = []
    for p in dossier["papers"]:
        if not isinstance(p, dict): p = {}
        year_val = p.get("year")
        try:
            year_val = int(year_val) if year_val is not None else None
        except Exception:
            year_val = None
        fixed_papers.append({
            "title": p.get("title",""),
            "year": year_val,
            "url": p.get("url",""),
            "reason": p.get("reason","")
        })
    dossier["papers"] = fixed_papers

    # Trim quotes ≤200 chars
    trimmed = []
    for q in dossier["quotes"]:
        if not isinstance(q, dict): q = {}
        quote = (q.get("quote") or "")[:200]
        trimmed.append({
            "quote": quote,
            "source_title": q.get("source_title",""),
            "url": q.get("url","")
        })
    dossier["quotes"] = trimmed

    return dossier

# =============================================================================
# Dossier cache + background prefetch pipeline
# =============================================================================
# Strategy:
#   1. /map kicks off a background "phase 1" dossier (LLM-only, no external I/O)
#      for every node, capped by a semaphore. Phase 1 lands in _DOSSIER_CACHE
#      within ~3-8s per node, so by the time the user clicks anything most
#      nodes are already populated.
#   2. After phase 1 lands for a node, a separate "phase 2" task fires that
#      hits Crossref + arXiv in parallel and merges papers into the cached
#      dossier. The user sees the partial first, then sources fill in on the
#      next read.
#   3. /node either returns the cache hit, awaits an in-flight task, or
#      synthesizes on the spot if neither.
#
# Cache key is (topic_lower, node_id_lower). Topics cancel previous topics'
# in-flight tasks to free up the semaphore.
# =============================================================================

def _dkey(topic: str, node_id: str) -> tuple[str, str]:
    return (topic.strip().lower(), node_id.strip().lower())

_DOSSIER_CACHE: dict[tuple[str, str], dict] = {}
_DOSSIER_INFLIGHT: dict[tuple[str, str], "asyncio.Task"] = {}
_TOPIC_TASKS: dict[str, list["asyncio.Task"]] = {}

# Phase 1 (OpenAI calls): cap concurrency to avoid rate-limiting and overload.
_PHASE1_SEM: "asyncio.Semaphore | None" = None
# Phase 2 (Crossref + arXiv): more permissive, mostly I/O wait.
_PHASE2_SEM: "asyncio.Semaphore | None" = None

def _get_sems() -> tuple["asyncio.Semaphore", "asyncio.Semaphore"]:
    """Lazy-create semaphores so they bind to the running event loop."""
    global _PHASE1_SEM, _PHASE2_SEM
    if _PHASE1_SEM is None:
        _PHASE1_SEM = asyncio.Semaphore(5)
    if _PHASE2_SEM is None:
        _PHASE2_SEM = asyncio.Semaphore(8)
    return _PHASE1_SEM, _PHASE2_SEM


# ---- Phase 1: LLM-only dossier (no external I/O) ----------------------------
SYSTEM_DOSSIER_FAST = """You are a research assistant writing a compact dossier for a NODE inside a TOPIC.

CRITICAL CONSTRAINTS:
- Disambiguate the NODE in the context of the TOPIC. Do NOT drift to unrelated meanings.
- Be concise and factual. If unsure about a fact, omit it.
- Do NOT invent papers, quotes, URLs, or datasets. Those will be added later by an external source step.

Return ONLY JSON with these keys (omit none):
- label: short human-friendly label (string)
- definition: 2-4 sentence plain-language definition (string)
- key_ideas: 3-7 short bullet strings (array of strings)
- aliases: 0-6 alternative names/abbrevs (array of strings)
- controversies: 0-6 short bullet strings (array of strings)
- search_prompts: 3-8 specific google-able query strings (array of strings)
- timeline: 0-6 entries each {year:int, event:string} (array)
"""

def call_model_dossier_fast_sync(topic: str, node_id: str) -> dict:
    user_msg = (
        f"TOPIC: {topic}\n"
        f"NODE:  {node_id}\n\n"
        "OUTPUT: JSON only. No prose, no markdown, no code fences."
    )
    chat = client.chat.completions.create(
        model=MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_DOSSIER_FAST},
            {"role": "user", "content": user_msg}
        ],
    )
    raw = chat.choices[0].message.content or "{}"
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    data = safe_json_loads(raw, default={}) or {}

    # Normalize timeline years to ints when possible.
    timeline = []
    for t in (data.get("timeline") or []):
        if not isinstance(t, dict): continue
        y = t.get("year")
        try:
            y = int(y) if y is not None else None
        except Exception:
            y = None
        timeline.append({"year": y, "event": str(t.get("event") or "")})

    return {
        "id": node_id,
        "label": data.get("label") or node_id,
        "definition": data.get("definition") or "",
        "key_ideas": [str(x) for x in (data.get("key_ideas") or [])],
        "aliases": [str(x) for x in (data.get("aliases") or [])],
        "papers": [],            # filled by phase 2
        "quotes": [],            # filled by phase 2 (best effort)
        "datasets_tools": [],    # filled by phase 2 (best effort)
        "controversies": [str(x) for x in (data.get("controversies") or [])],
        "search_prompts": [str(x) for x in (data.get("search_prompts") or [])],
        "timeline": timeline,
        "_phase1_done": True,
        "_enriched": False,
    }


# ---- Phase 2: parallel sources fetch + merge --------------------------------
def _fetch_sources_parallel(query: str, max_each: int = 6) -> list[dict]:
    """Crossref + arXiv concurrently in a small thread pool. Bounded latency."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fetch_crossref, query, max_each)
        f2 = ex.submit(fetch_arxiv, query, max_each)
        cross = f1.result() or []
        arx = f2.result() or []
    seen = set(); merged = []
    for s in cross + arx:
        k = (s["title"].lower(), s["url"])
        if k in seen: continue
        seen.add(k); merged.append(s)
    return merged

def enrich_dossier_with_sources_sync(topic: str, node_id: str, base: dict) -> dict:
    """Add papers (and a tiny datasets_tools heuristic) without re-calling the LLM."""
    sources = _fetch_sources_parallel(f"{node_id} {topic}", max_each=6)
    papers = []
    for s in sources[:6]:
        papers.append({
            "title": s["title"],
            "year": s.get("year"),
            "url": s["url"],
            "reason": f"Source from {s.get('source','external')} matching '{node_id}' in '{topic}'.",
        })
    out = dict(base)  # don't mutate cached object inplace until ready
    out["papers"] = papers
    # Heuristic timeline if model gave none and we have years.
    if not out.get("timeline"):
        years = sorted({p["year"] for p in papers if p.get("year")})
        out["timeline"] = [{"year": int(y), "event": f"Notable publication related to {node_id}"} for y in years[:6]]
    out["_enriched"] = True
    return out


# ---- Async pipeline + cache plumbing ----------------------------------------
async def _phase1_async(topic: str, node_id: str) -> dict:
    sem1, _ = _get_sems()
    async with sem1:
        try:
            return await asyncio.to_thread(call_model_dossier_fast_sync, topic, node_id)
        except Exception as e:
            print(f"[dossier:phase1] LLM failed for {node_id!r}: {e!r}")
            ctx = await asyncio.to_thread(fetch_context, topic)
            fb = await asyncio.to_thread(_fallback_dossier, topic, node_id, ctx)
            fb.setdefault("_phase1_done", True)
            fb.setdefault("_enriched", False)
            return fb

async def _phase2_async(topic: str, node_id: str) -> None:
    """Enrich the cached dossier in-place with sources. Best effort."""
    _, sem2 = _get_sems()
    key = _dkey(topic, node_id)
    base = _DOSSIER_CACHE.get(key)
    if not base or base.get("_enriched"):
        return
    async with sem2:
        try:
            enriched = await asyncio.to_thread(enrich_dossier_with_sources_sync, topic, node_id, base)
            _DOSSIER_CACHE[key] = enriched
        except Exception as e:
            print(f"[dossier:phase2] enrichment failed for {node_id!r}: {e!r}")

async def _generate_dossier(topic: str, node_id: str) -> dict:
    key = _dkey(topic, node_id)
    if key in _DOSSIER_CACHE:
        return _DOSSIER_CACHE[key]
    dossier = await _phase1_async(topic, node_id)
    _DOSSIER_CACHE[key] = dossier
    # Fire phase 2 without awaiting: cache will update when done.
    t2 = asyncio.create_task(_phase2_async(topic, node_id))
    _TOPIC_TASKS.setdefault(topic.strip().lower(), []).append(t2)
    return dossier

async def _get_or_create_dossier(topic: str, node_id: str) -> dict:
    """Cache hit, await in-flight, or generate fresh."""
    key = _dkey(topic, node_id)
    if key in _DOSSIER_CACHE:
        return _DOSSIER_CACHE[key]
    inflight = _DOSSIER_INFLIGHT.get(key)
    if inflight is not None:
        try:
            await inflight
        except Exception:
            pass
        if key in _DOSSIER_CACHE:
            return _DOSSIER_CACHE[key]
    task = asyncio.create_task(_generate_dossier(topic, node_id))
    _DOSSIER_INFLIGHT[key] = task
    task.add_done_callback(lambda _t, k=key: _DOSSIER_INFLIGHT.pop(k, None))
    _TOPIC_TASKS.setdefault(topic.strip().lower(), []).append(task)
    return await task

def _cancel_other_topics(current_topic: str) -> None:
    """When a new topic is generated, cancel prefetches for older topics so the
    semaphore isn't blocked. Already-completed tasks are no-ops."""
    cur = current_topic.strip().lower()
    for t, tasks in list(_TOPIC_TASKS.items()):
        if t == cur: continue
        for task in tasks:
            if not task.done():
                task.cancel()
        _TOPIC_TASKS.pop(t, None)

def _spawn_prefetch_for_map(topic: str, nodes: list[dict]) -> None:
    """Schedule background dossier generation for every node in the map."""
    for n in nodes:
        nid = n.get("id")
        if not nid: continue
        key = _dkey(topic, nid)
        if key in _DOSSIER_CACHE or key in _DOSSIER_INFLIGHT:
            continue
        task = asyncio.create_task(_generate_dossier(topic, nid))
        _DOSSIER_INFLIGHT[key] = task
        task.add_done_callback(lambda _t, k=key: _DOSSIER_INFLIGHT.pop(k, None))
        _TOPIC_TASKS.setdefault(topic.strip().lower(), []).append(task)


# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def health():
    return {"status": "ok", "model": MODEL_NAME}

@app.get("/map")
async def map_endpoint(topic: str = Query(..., min_length=2, max_length=120)):
    try:
        data = await asyncio.to_thread(call_model_map_light, topic)
    except Exception as e:
        print("OpenAI map call failed:", repr(e))
        data = {
            "nodes": [{"id": topic, "label": topic, "val": 10, "color": "#00ff88"}],
            "links": [],
            "gaps": []
        }
        if DEV_MODE: data["error"] = str(e)

    nodes = data.get("nodes", []); links = data.get("links", [])
    if not nodes:
        nodes = [{"id": topic, "label": topic, "val": 10, "color": "#00ff88"}]

    # Dedupe nodes by id (3d-force-graph crashes on duplicates), keep first occurrence.
    seen_ids = set()
    deduped = []
    for n in nodes:
        nid = n.get("id")
        if not nid or nid in seen_ids:
            continue
        seen_ids.add(nid)
        n.setdefault("val", 6)
        n.setdefault("color", "#58c7ff")
        n.setdefault("label", n.get("id",""))
        n.setdefault("summary", "")
        n.setdefault("sources", [])
        deduped.append(n)

    # Drop links whose endpoints were dropped/never existed.
    valid_links = [l for l in links
                   if isinstance(l, dict)
                   and l.get("source") in seen_ids
                   and l.get("target") in seen_ids]

    data["nodes"], data["links"] = deduped, valid_links
    data.setdefault("gaps", [])

    # Free up the semaphore from any older topic that's still prefetching,
    # then warm every node of the new topic in the background.
    _cancel_other_topics(topic)
    _spawn_prefetch_for_map(topic, deduped)

    return data


@app.get("/node")
async def node_dossier(
    topic: str = Query(..., min_length=2, max_length=120),
    id: str = Query(..., min_length=1, max_length=160)
):
    try:
        dossier = await _get_or_create_dossier(topic, id)
        return {"topic": topic, "id": id, "dossier": dossier}
    except Exception as e:
        print("Dossier pipeline failed; using fallback:", repr(e))
        ctx = await asyncio.to_thread(fetch_context, topic)
        fallback = await asyncio.to_thread(_fallback_dossier, topic, id, ctx)
        resp = {"topic": topic, "id": id, "dossier": fallback}
        if DEV_MODE: resp["error"] = f"fallback:{e}"
        return resp


@app.get("/node/status")
async def node_status(
    topic: str = Query(..., min_length=2, max_length=120),
    id: str = Query(..., min_length=1, max_length=160),
):
    """Tiny endpoint for the frontend to poll for the enriched (phase 2) version
    after it has shown the phase 1 result. Cheap: just a dict lookup."""
    key = _dkey(topic, id)
    cached = _DOSSIER_CACHE.get(key)
    if cached:
        return {
            "topic": topic, "id": id,
            "phase1_done": bool(cached.get("_phase1_done")),
            "enriched": bool(cached.get("_enriched")),
            "dossier": cached,
        }
    return {"topic": topic, "id": id, "phase1_done": False, "enriched": False, "dossier": None}

@app.get("/models")
def list_models():
    try:
        models = client.models.list()
        ids = [m.id for m in models.data][:50]
        return {"models": ids}
    except Exception as e:
        return {"error": str(e)}
