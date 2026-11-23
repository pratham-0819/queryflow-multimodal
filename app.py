import os
import secrets
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import media

from text2sql_core import (
    introspect_schema, schema_to_prompt_text, get_client, get_provider_and_model,
    generate_sql, validate_sql, detect_operation, run_query, execute_write, UnsafeSQLError
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "sample.db"
HISTORY_DB_PATH = BASE_DIR / "query_history.db"
WEB_DIR = BASE_DIR / "web"
UPLOAD_DIR = BASE_DIR / "uploaded_dbs"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="QueryFlow — NLP to SQL", version="4.0")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

PREVIEWS: dict[str, dict] = {}
ACTIVE_DB_PATH = DEFAULT_DB_PATH
ACTIVE_DB_NAME = "sample.db"
media.init_media_schema(DEFAULT_DB_PATH)

class QueryRequest(BaseModel):
    question: str
    api_key: Optional[str] = None

class ExecuteRequest(BaseModel):
    token: str
    confirmed: bool


def init_history():
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            question TEXT NOT NULL,
            sql TEXT NOT NULL,
            operation TEXT NOT NULL,
            database_name TEXT NOT NULL,
            status TEXT NOT NULL,
            affected_rows INTEGER,
            error TEXT
        )
    """)
    conn.commit()
    conn.close()


def add_history(question, sql, operation, status, affected_rows=None, error=None):
    conn = sqlite3.connect(HISTORY_DB_PATH)
    cur = conn.execute(
        "INSERT INTO history(question,sql,operation,database_name,status,affected_rows,error) VALUES (?,?,?,?,?,?,?)",
        (question, sql, operation, ACTIVE_DB_NAME, status, affected_rows, error),
    )
    hid = cur.lastrowid
    conn.commit(); conn.close()
    return hid


def update_history(hid, status, affected_rows=None, error=None):
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.execute("UPDATE history SET status=?, affected_rows=?, error=? WHERE id=?", (status, affected_rows, error, hid))
    conn.commit(); conn.close()


def get_history(limit=50):
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def current_db():
    if not ACTIVE_DB_PATH.exists():
        raise HTTPException(500, "Active database not found.")
    return str(ACTIVE_DB_PATH)

init_history()

@app.get("/")
def home():
    return FileResponse(str(WEB_DIR / "index.html"))

@app.get("/api/schema")
def schema():
    return introspect_schema(current_db())

@app.get("/api/history")
def history():
    return get_history()

@app.delete("/api/history")
def clear_history():
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.execute("DELETE FROM history")
    conn.commit(); conn.close()
    return {"ok": True}

@app.get("/api/database")
def database_info():
    provider, model = get_provider_and_model()
    return {
        "name": ACTIVE_DB_NAME,
        "is_custom": ACTIVE_DB_PATH != DEFAULT_DB_PATH,
        "provider": provider,
        "model": model,
    }

@app.post("/api/upload-sql")
async def upload_sql(file: UploadFile = File(...)):
    global ACTIVE_DB_PATH, ACTIVE_DB_NAME
    if not file.filename or not file.filename.lower().endswith(".sql"):
        raise HTTPException(400, "Please select a .sql file.")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "SQL file is too large. Maximum size is 5 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "The SQL file must be UTF-8 encoded.")
    if not text.strip():
        raise HTTPException(400, "The SQL file is empty.")

    db_path = UPLOAD_DIR / f"{secrets.token_hex(8)}.db"
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(text)
        conn.commit(); conn.close()
        media.init_media_schema(db_path)
        # Verify that SQLite can introspect the imported database.
        introspect_schema(str(db_path))
    except Exception as exc:
        try: conn.close()
        except Exception: pass
        db_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not import SQL file: {type(exc).__name__}: {exc}")

    ACTIVE_DB_PATH = db_path
    ACTIVE_DB_NAME = file.filename
    media.DB_PATH = db_path.resolve()
    return {"ok": True, "name": ACTIVE_DB_NAME, "schema": introspect_schema(str(ACTIVE_DB_PATH))}

@app.post("/api/reset-database")
def reset_database():
    global ACTIVE_DB_PATH, ACTIVE_DB_NAME
    ACTIVE_DB_PATH = DEFAULT_DB_PATH
    ACTIVE_DB_NAME = "sample.db"
    media.DB_PATH = DEFAULT_DB_PATH.resolve()
    provider, model = get_provider_and_model()
    return {
        "ok": True,
        "name": ACTIVE_DB_NAME,
        "provider": provider,
        "model": model,
        "schema": introspect_schema(current_db()),
    }

@app.post("/api/media/upload")
async def upload_media(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    if not files or len(files) > 20:
        raise HTTPException(400, "Upload between 1 and 20 files at a time.")
    uploaded = []
    for file in files:
        uploaded.append(await media.save_upload(file))
    for item in uploaded:
        background_tasks.add_task(media.process_media, item["media_id"])
    return {"ok": True, "files": uploaded}

@app.get("/api/media")
def media_list(media_type: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None,
               limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    if media_type and media_type not in media.EXTENSIONS:
        raise HTTPException(400, "Invalid media type.")
    allowed_statuses = {"pending", "processing", "completed", "failed"}
    if status and status not in allowed_statuses:
        raise HTTPException(400, "Invalid processing status.")
    # Older imported databases may predate media support. Create the metadata
    # table lazily so browsing them does not fail with a 500 response.
    media.init_media_schema(media.DB_PATH)
    return {"files": media.list_media(media_type, status, q, limit, offset)}

@app.get("/api/media/{media_id}")
def media_detail(media_id: str):
    return media.get_media(media_id)

@app.get("/api/media/{media_id}/status")
def media_status(media_id: str):
    item = media.get_media(media_id)
    return {"media_id": item["media_id"], "processing_status": item["processing_status"],
            "processing_error": item["processing_error"]}

@app.get("/api/media/{media_id}/content")
def media_content(media_id: str):
    item = media.get_media(media_id)
    if item["media_type"] not in {"image", "video", "audio"}:
        raise HTTPException(415, "Inline preview is only available for media files.")
    return FileResponse(media.safe_path(media_id), media_type=item["mime_type"], filename=item["original_name"],
                        content_disposition_type="inline")

@app.get("/api/media/{media_id}/download")
def media_download(media_id: str):
    item = media.get_media(media_id)
    return FileResponse(media.safe_path(media_id), media_type="application/octet-stream",
                        filename=item["original_name"])

@app.delete("/api/media/{media_id}")
def media_delete(media_id: str):
    return media.delete_media(media_id)

@app.post("/api/media/{media_id}/process")
def media_process(media_id: str, background_tasks: BackgroundTasks):
    media.get_media(media_id)
    background_tasks.add_task(media.process_media, media_id)
    return {"ok": True, "media_id": media_id, "processing_status": "pending"}

class MediaSearchRequest(BaseModel):
    query: str
    limit: int = 20

@app.post("/api/media/semantic-search")
def media_semantic_search(req: MediaSearchRequest):
    if not req.query.strip():
        raise HTTPException(400, "Enter words to search for.")
    return media.semantic_search(req.query.strip(), max(1, min(req.limit, 100)))

@app.post("/api/query")
def query(req: QueryRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question first.")
    try:
        db_path = current_db()
        schema = introspect_schema(db_path)
        client = get_client(req.api_key or None)
        sql = generate_sql(client, schema_to_prompt_text(schema), question)
        operation = detect_operation(sql)
        validated = validate_sql(sql, operation)

        if "UNANSWERABLE_WITH_GIVEN_SCHEMA" in validated:
            hid = add_history(question, validated, operation, "unanswerable", error="Cannot answer with current schema")
            return {"ok": False, "operation": operation, "question": question, "sql": validated, "history_id": hid, "error": "This request cannot be answered with the current database schema."}

        if operation == "query":
            df = run_query(db_path, validated)
            rows = df.astype(object).where(df.notna(), None).to_dict(orient="records")
            hid = add_history(question, validated, operation, "executed", len(df))
            return {"ok": True, "operation": operation, "question": question, "sql": validated, "history_id": hid, "columns": list(df.columns), "rows": rows, "row_count": len(df)}

        token = secrets.token_urlsafe(24)
        hid = add_history(question, validated, operation, "awaiting_confirmation")
        PREVIEWS[token] = {"sql": validated, "operation": operation, "question": question, "history_id": hid}
        if len(PREVIEWS) > 100:
            for k in list(PREVIEWS)[:25]: PREVIEWS.pop(k, None)
        action = {"insert": "added to", "update": "updated in", "delete": "deleted from"}[operation]
        return {"ok": True, "requires_confirmation": True, "operation": operation, "question": question, "sql": validated, "token": token, "history_id": hid, "message": f"This will be {action} the database. Review the SQL and confirm before execution."}
    except UnsafeSQLError as exc:
        return {"ok": False, "error": f"Blocked by SQL safety guardrails: {exc}"}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        err_type = type(exc).__name__
        err_msg = str(exc)
        if "503" in err_msg or "overloaded" in err_msg.lower():
            err_msg = "Google AI model servers are temporarily experiencing high traffic (503 Overloaded). Please retry your request in a few seconds."
        elif "NotFoundError" in err_type or "404" in err_msg:
            model_info = getattr(client, '_queryflow_model', os.getenv('GEMINI_MODEL', 'gemini-3.8-flash'))
            err_msg = f"AI Model '{model_info}' returned 404 (Not Found). Please check GEMINI_MODEL in .env."
        elif "AuthenticationError" in err_type or "401" in err_msg:
            err_msg = "Invalid API key (401). Please check GEMINI_API_KEY in .env or enter a valid key."
        return {"ok": False, "error": f"{err_type}: {err_msg}"}

@app.post("/api/execute")
def execute(req: ExecuteRequest):
    if not req.confirmed:
        raise HTTPException(400, "Manual confirmation is required before changing data.")
    preview = PREVIEWS.pop(req.token, None)
    if not preview:
        raise HTTPException(400, "This confirmation has expired or was already used. Generate the request again.")
    try:
        result = execute_write(str(ACTIVE_DB_PATH), preview["sql"], preview["operation"])
        update_history(preview["history_id"], "executed", result.get("affected_rows"))
        message = {"insert": "Data added successfully.", "update": "Data updated successfully.", "delete": "Data deleted successfully."}[preview["operation"]]
        return {"ok": True, "operation": preview["operation"], "sql": preview["sql"], **result, "message": message}
    except Exception as exc:
        update_history(preview["history_id"], "failed", error=str(exc))
        if isinstance(exc, UnsafeSQLError):
            raise HTTPException(400, f"Blocked by SQL safety guardrails: {exc}")
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")
