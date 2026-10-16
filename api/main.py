
# This is a basic API I got AI to write, it does not seem complicated 
# and you can see how def post, def get, reads and writes from the 
# json file. 

#This also runs seperate from everything else, this is its own server, 
# run this command in the terminal to localy host it.
# uvicorn main:app --reload
# run this to make it run onlne with :http:/ip-address:8000" 
# uvicorn main:app --host 0.0.0.0 --port 80


from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import json
import os

app = FastAPI()


# ─── File where data is saved ───
DB_FILE = "items.json"


class Item(BaseModel):
    name: str
    price: float
    description: Optional[str] = None
    in_stock: bool = True


# ─────────────────────────────────────────────────────────────
# LOAD: Read items.json from disk into memory at startup
# ─────────────────────────────────────────────────────────────
def load_db() -> dict:
    """
    If items.json exists, read it and return the dict.
    If it doesn't exist (first run), return an empty dict.
    """
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}


# ─────────────────────────────────────────────────────────────
# SAVE: Write the in-memory dict to items.json on disk
# ─────────────────────────────────────────────────────────────
def save_db(db: dict):
    """
    Write the dict to items.json.
    indent=2 makes it human-readable.
    default=str handles non-JSON types (like datetime).
    """
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, default=str)


# ─────────────────────────────────────────────────────────────
# Initialize: load data from disk, compute next_id
# ─────────────────────────────────────────────────────────────
fake_db: dict = load_db()

# Figure out the next ID based on existing keys
# If DB is empty, start at 1
next_id: int = max([int(k) for k in fake_db.keys()], default=0) + 1


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/items")
def list_items():
    return {"total": len(fake_db), "items": fake_db}


@app.get("/items/{item_id}")
def get_item(item_id: int):
    # JSON keys are strings, so convert to str for lookup
    key = str(item_id)
    if key not in fake_db:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"id": item_id, **fake_db[key]}


@app.post("/items", status_code=201)
def create_item(item: Item):
    global next_id

    # Store as a plain dict (JSON-friendly), keyed by string
    fake_db[str(next_id)] = item.model_dump()

    # ← THE KEY LINE: save to disk
    save_db(fake_db)

    created = {"id": next_id, **item.model_dump()}
    next_id += 1
    return created
