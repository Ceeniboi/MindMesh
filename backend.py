from pathlib import Path
from dotenv import load_dotenv
import os, json, re, requests
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
# Wikipedia context
# ---------------------------
def fetch_context(topic: str) -> str:
    title = topic.strip().replace(" ", "_")
    parts = []
    try:
        r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=10)
        if r.ok:
            s = r.json().get("extract", "")
            if s: parts.append(s)
    except Exception as e:
        print("Wiki summary error:", e)

    try:
        rel = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/related/{title}", timeout=10)
        if rel.ok:
            pages = rel.json().get("pages", [])[:2]
            for p in pages:
                ex = p.get("extract", "")
                if ex: parts.append(ex)
    except Exception as e:
        print("Wiki related error:", e)

    combined = "\n".join(parts).strip()
    return _trim_6000(combined if combined else topic)

# ---------------------------
# Crossref & arXiv sources
# ---------------------------
def fetch_crossref(query: str, rows: int = 6):
    url = "https://api.crossref.org/works"
    params = { "query": query, "rows": rows, "select": "title,URL,author,issued,type" }
    out = []
    try:
        r = requests.get(url, params=params, timeout=12)
        if r.ok:
            items = r.json().get("message", {}).get("items", [])
            for it in items:
                title = (it.get("title") or [""])[0]
                url = it.get("URL")
                year = None
                issued = it.get("issued", {}).get("date-parts", [])
                if issued and issued[0]:
                    year = issued[0][0]
                if title and url:
                    out.append({ "title": title.strip(), "url": url, "year": year, "source": "Crossref" })
    except Exception as e:
        print("Crossref error:", e)
    return out

def fetch_arxiv(query: str, max_results: int = 6):
    q = f"http://export.arxiv.org/api/query?search_query=all:{requests.utils.quote(query)}&start=0&max_results={max_results}"
    out = []
    try:
        feed = feedparser.parse(q)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link")
            year = None
            if entry.get("published"):
                year = entry.published[:4]
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

# ---------------------------
# Dossier (strict spec) + robust fallback
# ---------------------------
SYSTEM_DOSSIER = """You are a research assistant that writes compact, grounded dossiers for a subtopic node.

Return ONLY JSON with keys:
- id: string (node id)
- label: string (human-friendly)
- definition: string (2–4 sentences)
- key_ideas: array of 3–7 short strings
- aliases: array of 0–6 short strings
- papers: array of 4–6 objects, each:
    {title: string, year: number, url: string, reason: string}
    Include ~2 seminal (older, widely-cited) and ~3 recent (last 5–7 years) where possible.
- quotes: array of 0–3 objects, each:
    {quote: string, source_title: string, url: string}
    Keep quotes ≤200 chars and pull from provided sources when possible.
- datasets_tools: array of 0–6 objects, each:
    {name: string, url: string, note: string}
- controversies: array of 0–6 short strings
- search_prompts: array of 3–8 queries (strings) people can paste into Google/Scholar
- timeline: array of 0–6 objects, each:
    {year: number, event: string}

Be precise, avoid fluff, prefer the ARTICLES provided. If unsure, state uncertainty briefly rather than hallucinating.
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
        "id": data.get("id") or node_id,
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

# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def health():
    return {"status": "ok", "model": MODEL_NAME}

@app.get("/map")
def map_endpoint(topic: str = Query(..., min_length=2, max_length=120)):
    ctx = fetch_context(topic)
    try:
        data = call_model_map(topic, ctx)
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
    for n in nodes:
        n.setdefault("val", 6); n.setdefault("color", "#58c7ff"); n.setdefault("label", n.get("id",""))
    data["nodes"], data["links"] = nodes, links
    data.setdefault("gaps", [])
    return data

@app.get("/node")
def node_dossier(
    topic: str = Query(..., min_length=2, max_length=120),
    id: str = Query(..., min_length=1, max_length=160)
):
    ctx = fetch_context(topic)
    try:
        dossier = call_model_dossier(topic, id, ctx)
        return {"topic": topic, "id": id, "dossier": dossier}
    except Exception as e:
        print("LLM dossier call failed; using fallback:", repr(e))
        fallback = _fallback_dossier(topic, id, ctx)
        resp = {"topic": topic, "id": id, "dossier": fallback}
        if DEV_MODE: resp["error"] = f"fallback:{e}"
        return resp

@app.get("/models")
def list_models():
    try:
        models = client.models.list()
        ids = [m.id for m in models.data][:50]
        return {"models": ids}
    except Exception as e:
        return {"error": str(e)}
