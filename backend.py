from pathlib import Path
from dotenv import load_dotenv
import os, json, re, requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import feedparser  # for arXiv

# Load the .env that sits next to backend.py and override any OS vars
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Strip inline comments/quotes just in case
raw_model = os.getenv("MODEL_NAME", "gpt-4o-mini") or "gpt-4o-mini"
MODEL_NAME = raw_model.split("#", 1)[0].strip().strip('"').strip("'")

ALLOW_ORIGIN = os.getenv("ALLOW_ORIGIN", "*")
DEV_MODE = (os.getenv("DEV_MODE", "1") == "1")

if not OPENAI_API_KEY:
  raise RuntimeError("Missing OPENAI_API_KEY in .env")

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()
app.add_middleware(
  CORSMiddleware,
  allow_origins=[ALLOW_ORIGIN] if ALLOW_ORIGIN != "*" else ["*"],
  allow_methods=["*"],
  allow_headers=["*"],
)

# ---------------------------
# Wikipedia context
# ---------------------------
def fetch_context(topic: str) -> str:
  title = topic.strip().replace(" ", "_")
  texts = []
  try:
    r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=8)
    if r.ok:
      s = r.json().get("extract", "")
      if s: texts.append(s)
  except Exception as e:
    print("Wiki summary error:", e)

  try:
    rel = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/related/{title}", timeout=8)
    if rel.ok:
      pages = rel.json().get("pages", [])[:2]
      for p in pages:
        ex = p.get("extract","")
        if ex: texts.append(ex)
  except Exception as e:
    print("Wiki related error:", e)

  combined = "\n".join(texts).strip()
  return combined if combined else topic

# ---------------------------
# Crossref & arXiv sources
# ---------------------------
def fetch_crossref(topic: str, rows: int = 5):
  url = "https://api.crossref.org/works"
  params = {
    "query": topic,
    "rows": rows,
    "select": "title,URL,author,issued,type"
  }
  out = []
  try:
    r = requests.get(url, params=params, timeout=8)
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
          out.append({
            "title": title.strip(),
            "url": url,
            "year": year,
            "source": "Crossref"
          })
  except Exception as e:
    print("Crossref error:", e)
  return out

def fetch_arxiv(topic: str, max_results: int = 5):
  q = f"http://export.arxiv.org/api/query?search_query=all:{requests.utils.quote(topic)}&start=0&max_results={max_results}"
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
          "year": year,
          "source": "arXiv"
        })
  except Exception as e:
    print("arXiv error:", e)
  return out

def gather_sources(topic: str, max_each: int = 4):
  papers = []
  papers.extend(fetch_crossref(topic, rows=max_each))
  papers.extend(fetch_arxiv(topic, max_results=max_each))
  seen = set()
  unique = []
  for p in papers:
    k = (p["title"].lower(), p["url"])
    if k not in seen:
      seen.add(k)
      unique.append(p)
  return unique[:8]  # cap

# ---------------------------
# Model call (Chat Completions JSON mode)
# ---------------------------
def call_model(topic: str, context_text: str) -> dict:
  """
  Use Chat Completions JSON mode to get {nodes, links, gaps}.
  Pass a small list of external articles for grounding, and ask
  the model to attach up to 3 relevant sources to each node + a 1–2 line summary.
  """
  articles = gather_sources(topic, max_each=4)
  articles_bullets = "\n".join(
    [f"- {a['title']} ({a.get('year') or ''}) — {a['url']} [{a['source']}]"
     for a in articles]
  )

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
    f"WIKIPEDIA CONTEXT (may be partial):\n{context_text[:6000]}\n\n"
    f"ARTICLES (titles/urls):\n{articles_bullets}\n\n"
    "OUTPUT: Return JSON only. No prose, no markdown."
  )

  chat = client.chat.completions.create(
    model=MODEL_NAME,
    response_format={"type": "json_object"},
    messages=[
      {"role": "system", "content": system_msg},
      {"role": "user", "content": user_msg}
    ]
    # Note: do not set temperature for models that only support default
  )

  raw = chat.choices[0].message.content or "{}"
  if raw.startswith("```"):
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)

  data = json.loads(raw)
  data.setdefault("nodes", [])
  data.setdefault("links", [])
  data.setdefault("gaps", [])
  # safe defaults
  for n in data["nodes"]:
    n.setdefault("val", 6)
    n.setdefault("color", "#58c7ff")
    n.setdefault("label", n.get("id",""))
    n.setdefault("summary", "")
    n.setdefault("sources", [])
  return data

# (Schema kept for reference; not enforced server-side in this version)
SCHEMA = {
  "type": "object",
  "properties": {
    "nodes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id":      {"type": "string"},
          "label":   {"type": "string"},
          "val":     {"type": "number"},
          "color":   {"type": "string"},
          "summary": {"type": "string"},
          "sources": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "title": {"type": "string"},
                "url":   {"type": "string"},
                "year":  {"type": "number"},
                "source":{"type": "string"}
              },
              "required": ["title","url"]
            }
          }
        },
        "required": ["id","label"]
      }
    },
    "links": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "source":   {"type": "string"},
          "target":   {"type": "string"},
          "relation": {"type": "string"}
        },
        "required": ["source","target"]
      }
    },
    "gaps": {"type": "array", "items": {"type": "string"}}
  },
  "required": ["nodes","links"]
}

# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def health():
  return {"status": "ok", "model": MODEL_NAME, "repr": repr(MODEL_NAME)}

@app.get("/map")
def map_endpoint(topic: str = Query(..., min_length=2, max_length=120)):
  ctx = fetch_context(topic)
  try:
    data = call_model(topic, ctx)
  except Exception as e:
    print("OpenAI call failed:", repr(e))
    data = {
      "nodes": [{"id": topic, "label": topic, "val": 10, "color": "#00ff88"}],
      "links": [],
      "gaps": []
    }
    if DEV_MODE:
      data["error"] = str(e)

  # Post-process safety nets
  nodes = data.get("nodes", [])
  links = data.get("links", [])
  if not nodes:
    nodes = [{"id": topic, "label": topic, "val": 10, "color": "#00ff88"}]
  for n in nodes:
    n.setdefault("val", 6)
    n.setdefault("color", "#58c7ff")
    n.setdefault("label", n.get("id", ""))
  data["nodes"], data["links"] = nodes, links
  data.setdefault("gaps", [])
  return data

@app.get("/models")
def list_models():
  try:
    models = client.models.list()
    ids = [m.id for m in models.data][:50]
    return {"models": ids}
  except Exception as e:
    return {"error": str(e)}
