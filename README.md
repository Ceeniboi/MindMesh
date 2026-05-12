# MindMesh

An interactive 3D knowledge map explorer. Type any topic and get a force-directed graph of related concepts — click any node to pull up a full dossier with definitions, key ideas, papers, quotes, controversies, timelines, and more.

![Demo](https://img.shields.io/badge/stack-Python%20%7C%20FastAPI%20%7C%20OpenAI%20%7C%20JS-blue)

## Features

- 3D interactive concept graph using ForceGraph3D
- Click any node to load a deep dossier powered by GPT-4o-mini
- Hover prefetching for instant click response
- Data pulled from Wikipedia, arXiv, and OpenAI
- Tabbed info panel: Overview, Key Ideas, Aliases, Papers, Quotes, Datasets/Tools, Controversies, Search Prompts, Timeline

## Tech Stack

- **Frontend** — HTML, CSS, vanilla JS, ForceGraph3D (WebGL)
- **Backend** — Python, FastAPI, Uvicorn
- **AI** — OpenAI API (GPT-4o-mini by default)
- **Data sources** — Wikipedia REST API, arXiv Atom feed

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/YOUR_USERNAME/MindMesh.git
cd MindMesh
```

**2. Create and activate a virtual environment**
```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Add your API key**

Create a `.env` file in the root:
```
OPENAI_API_KEY=your_key_here
```

## Running

Open two terminals:

**Terminal 1 — Backend**
```bash
.venv\Scripts\activate
uvicorn backend:app --reload
```

**Terminal 2 — Frontend**

Just open `index.html` in your browser.

The backend runs at `http://localhost:8000`.
