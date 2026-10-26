"""
Search Smart relay.

Sits between the website's API and the scraper:

  website API  --query-->  relay.py  --query-->  scraper
  website API  <--results-  relay.py  <-results-  scraper

Run:
  pip install fastapi uvicorn requests
  uvicorn relay:app --port 8000

Receiving the query (the website's API calls us):
  POST /search   body: {"query": "usb c cable"}
  GET  /search?q=usb+c+cable        (handy for testing in a browser)

Calling the scraper - pick ONE (see run_scraper below):
  * Partner's scraper is a web service: set SCRAPER_URL, e.g.
        SCRAPER_URL=http://partner-machine:5000/scrape uvicorn relay:app --port 8000
    We POST {"query": "..."} to it and expect a JSON list of products back
    (or {"results": [...]}).
  * Scraper is a Python file in this folder (like crawler.py): leave SCRAPER_URL
    unset and we call crawler.crawl() directly.

Each product we return looks like:
  {"name": str, "price": float, "currency": str, "url": str, "store": str}
"""
import os
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

SCRAPER_URL = os.environ.get("SCRAPER_URL")  # unset -> use local crawler.py
SCRAPER_TIMEOUT = 120                        # seconds to wait for the scraper
MAX_RESULTS = 20

app = FastAPI(title="Search Smart relay")

# Lets the website call this from a browser on another port/domain.
# Replace "*" with the real site URL before deploying anywhere public.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# --------------------------------------------------------------------------
# Step 2: hand the query to the scraper  (the one part to adapt to your partner)
# --------------------------------------------------------------------------
def run_scraper(query: str) -> list:
    if SCRAPER_URL:
        resp = requests.post(SCRAPER_URL, json={"query": query}, timeout=SCRAPER_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", []) if isinstance(data, dict) else data

    import crawler  # local fallback: the crawler.py file from this project
    return crawler.crawl(query)


# --------------------------------------------------------------------------
# Step 3: clean up whatever the scraper returned
# --------------------------------------------------------------------------
def clean(raw: list) -> list:
    seen, out = set(), []
    for item in raw:
        try:
            product = {
                "name": str(item["name"]).strip(),
                "price": float(item["price"]),
                "currency": str(item.get("currency", "USD")).upper(),
                "url": str(item["url"]),
                "store": str(item.get("store", "")),
            }
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed rows instead of crashing the whole request
        if not product["name"] or product["price"] <= 0 or product["url"] in seen:
            continue
        seen.add(product["url"])
        out.append(product)
    # cheapest first, kept separate by currency so USD is never ranked against EUR
    out.sort(key=lambda p: (p["currency"], p["price"]))
    return out[:MAX_RESULTS]


# --------------------------------------------------------------------------
# Steps 1 + 4: receive the query, return the results
# --------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=100)


def handle(query: str) -> dict:
    query = " ".join(query.split())  # trim and collapse whitespace
    if not query:
        raise HTTPException(status_code=400, detail="query is empty")
    try:
        raw = run_scraper(query)
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="scraper took too long")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"could not reach scraper: {e}")
    results = clean(raw)
    return {"query": query, "count": len(results), "results": results}


@app.post("/search")
def search_post(req: SearchRequest):
    return handle(req.query)


@app.get("/search")
def search_get(q: str = Query(..., min_length=1, max_length=100)):
    return handle(q)