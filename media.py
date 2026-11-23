"""Secure media storage, metadata, extraction and optional AI processing."""
from __future__ import annotations

import mimetypes
import os
import re
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    from fastapi import HTTPException, UploadFile
except ImportError:  # Enables metadata-only unit checks without installing the web stack.
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            self.status_code, self.detail = status_code, detail
            super().__init__(detail)
    class UploadFile:  # type-only fallback; upload endpoints require FastAPI.
        filename: str | None
        content_type: str | None
        async def read(self, size: int = -1): ...

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("QUERYFLOW_DB_PATH", str(BASE_DIR / "sample.db"))).resolve()
STORAGE_ROOT = Path(os.getenv("MEDIA_STORAGE_DIR", str(BASE_DIR / "media_store"))).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg"},
    "video": {".mp4", ".mov", ".avi", ".webm"},
    "document": {".pdf", ".txt", ".docx"},
}
MIME_PREFIX = {"image": "image/", "audio": "audio/", "video": "video/"}
MEDIA_TABLE = "queryflow_media_files"


@contextmanager
def connect(path: str | Path | None = None):
    conn = sqlite3.connect(str(path or DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_media_schema(path: str | Path = DB_PATH):
    with connect(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS queryflow_media_files (
            media_id TEXT PRIMARY KEY, file_name TEXT NOT NULL, original_name TEXT NOT NULL,
            media_type TEXT NOT NULL CHECK(media_type IN ('image','audio','video','document')),
            mime_type TEXT NOT NULL, file_size INTEGER NOT NULL CHECK(file_size >= 0),
            storage_path TEXT NOT NULL, description TEXT, extracted_text TEXT, ai_summary TEXT,
            processing_status TEXT NOT NULL DEFAULT 'pending' CHECK(processing_status IN ('pending','processing','completed','failed')),
            processing_error TEXT, uploaded_at TEXT NOT NULL, processed_at TEXT,
            width INTEGER, height INTEGER, duration_seconds REAL, format TEXT NOT NULL,
            legacy_media_id TEXT UNIQUE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_qf_media_type_uploaded ON queryflow_media_files(media_type, uploaded_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_qf_media_status ON queryflow_media_files(processing_status)")

        # Older imported databases may already contain a user-owned table named
        # `media_files` with a different schema. Leave it untouched and copy only
        # compatible rows whose stored files are inside our media storage root.
        legacy_columns = {r[1] for r in db.execute("PRAGMA table_info('media_files')")}
        if {"media_id", "file_name", "original_name", "media_type", "storage_path"}.issubset(legacy_columns):
            legacy_rows = db.execute("SELECT * FROM media_files").fetchall()
            for legacy in legacy_rows:
                row = dict(legacy)
                old_id = str(row.get("media_id", ""))
                stored = str(row.get("storage_path", ""))
                candidate = (STORAGE_ROOT / stored).resolve()
                if not old_id or not stored or not candidate.is_relative_to(STORAGE_ROOT):
                    continue
                try:
                    media_id = str(uuid.UUID(old_id))
                except ValueError:
                    media_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"queryflow:{Path(path).resolve()}:{old_id}"))
                media_type = row.get("media_type")
                if media_type not in EXTENSIONS:
                    continue
                ext = Path(row.get("original_name") or row.get("file_name") or "").suffix.lower()
                mime = row.get("mime_type") or mimetypes.guess_type("file" + ext)[0] or "application/octet-stream"
                fmt = row.get("format") or ext.lstrip(".") or "bin"
                status = row.get("processing_status")
                if status not in {"pending", "processing", "completed", "failed"}:
                    status = "pending"
                db.execute("""INSERT OR IGNORE INTO queryflow_media_files
                    (media_id,file_name,original_name,media_type,mime_type,file_size,storage_path,description,
                     extracted_text,ai_summary,processing_status,processing_error,uploaded_at,processed_at,
                     width,height,duration_seconds,format,legacy_media_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (media_id, row.get("file_name") or candidate.name, row.get("original_name") or candidate.name,
                     media_type, mime, int(row.get("file_size") or candidate.stat().st_size), stored,
                     row.get("description"), row.get("extracted_text"), row.get("ai_summary"), status,
                     row.get("processing_error"), row.get("uploaded_at") or datetime.now(timezone.utc).isoformat(),
                     row.get("processed_at"), row.get("width"), row.get("height"), row.get("duration_seconds"),
                     fmt, old_id))


def _kind(ext: str) -> str:
    for kind, extensions in EXTENSIONS.items():
        if ext in extensions:
            return kind
    raise HTTPException(415, "Unsupported file extension.")


def _signature(kind: str, ext: str, data: bytes) -> bool:
    if ext == ".txt":
        try: data[:4096].decode("utf-8")
        except UnicodeDecodeError: return False
        return b"\x00" not in data[:4096]
    if ext == ".pdf": return data.startswith(b"%PDF-")
    if ext == ".png": return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}: return data.startswith(b"\xff\xd8\xff")
    if ext == ".webp": return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if ext == ".wav": return data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    if ext == ".ogg": return data.startswith(b"OggS")
    if ext == ".mp3": return data.startswith(b"ID3") or data[:1] == b"\xff"
    if ext == ".m4a": return len(data) >= 12 and data[4:8] == b"ftyp"
    if ext in {".mp4", ".mov"}: return len(data) >= 12 and data[4:8] == b"ftyp"
    if ext == ".webm": return data.startswith(b"\x1aE\xdf\xa3")
    if ext == ".avi": return data.startswith(b"RIFF") and data[8:12] == b"AVI "
    if ext == ".docx": return data.startswith(b"PK\x03\x04")
    return False


async def save_upload(upload: UploadFile) -> dict:
    original = Path((upload.filename or "").replace("\\", "/")).name
    ext = Path(original).suffix.lower()
    kind = _kind(ext)
    expected_mime = mimetypes.guess_type("f" + ext)[0] or "application/octet-stream"
    claimed = (upload.content_type or "application/octet-stream").lower()
    document_mimes = {
        ".pdf": {"application/pdf"},
        ".txt": {"text/plain"},
        ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    }
    mime_ok = claimed == "application/octet-stream"
    if kind in MIME_PREFIX:
        mime_ok = mime_ok or claimed.startswith(MIME_PREFIX[kind])
    else:
        mime_ok = mime_ok or claimed in document_mimes.get(ext, set())
    if not mime_ok:
        raise HTTPException(415, "MIME type does not match the file extension.")
    chunks, size = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk: break
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES: raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES}-byte upload limit.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data or not _signature(kind, ext, data): raise HTTPException(415, "File content does not match a supported format.")
    media_id = str(uuid.uuid4())
    folder = STORAGE_ROOT / kind
    folder.mkdir(parents=True, exist_ok=True)
    target = (folder / f"{media_id}{ext}").resolve()
    if not target.is_relative_to(STORAGE_ROOT): raise HTTPException(400, "Invalid storage path.")
    target.write_bytes(data)
    try:
        with connect() as db:
            cur = db.execute("INSERT INTO queryflow_media_files(media_id,file_name,original_name,media_type,mime_type,file_size,storage_path,uploaded_at,format) VALUES(?,?,?,?,?,?,?,?,?)",
                (media_id, target.name, original, kind, expected_mime, size, str(target.relative_to(STORAGE_ROOT)), datetime.now(timezone.utc).isoformat(), ext.lstrip('.')))
        row = get_media(media_id)
        return row
    except Exception:
        target.unlink(missing_ok=True)
        raise


def get_media(media_id: str):
    try: media_id = str(uuid.UUID(media_id))
    except ValueError: raise HTTPException(404, "Media file not found.")
    with connect() as db: row = db.execute("SELECT * FROM queryflow_media_files WHERE media_id=?", (media_id,)).fetchone()
    if not row: raise HTTPException(404, "Media file not found.")
    result = dict(row)
    result.pop("storage_path", None)
    return result


def list_media(media_type: str | None = None, status: str | None = None, q: str | None = None, limit: int = 100, offset: int = 0):
    clauses, values = [], []
    if media_type: clauses.append("media_type=?"); values.append(media_type)
    if status: clauses.append("processing_status=?"); values.append(status)
    if q:
        clauses.append("(original_name LIKE ? OR description LIKE ? OR extracted_text LIKE ? OR ai_summary LIKE ?)")
        values.extend([f"%{q}%"] * 4)
    sql = "SELECT media_id,file_name,original_name,media_type,mime_type,file_size,storage_path,description,extracted_text,ai_summary,processing_status,processing_error,uploaded_at,processed_at,width,height,duration_seconds,format FROM queryflow_media_files"
    if clauses: sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY uploaded_at DESC LIMIT ? OFFSET ?"; values.extend([limit, offset])
    with connect() as db:
        results = []
        for record in db.execute(sql, values):
            item = dict(record)
            stored = item.pop("storage_path", None)
            candidate = (STORAGE_ROOT / str(stored or "")).resolve()
            item["file_available"] = bool(candidate.is_relative_to(STORAGE_ROOT) and candidate.is_file())
            results.append(item)
        return results


def safe_path(media_id: str) -> Path:
    row = get_media(media_id)
    with connect() as db: stored = db.execute("SELECT storage_path FROM queryflow_media_files WHERE media_id=?", (row["media_id"],)).fetchone()[0]
    path = (STORAGE_ROOT / stored).resolve()
    if not path.is_relative_to(STORAGE_ROOT) or not path.is_file(): raise HTTPException(404, "Stored media file is unavailable.")
    return path


def delete_media(media_id: str):
    path = safe_path(media_id)
    with connect() as db: db.execute("DELETE FROM queryflow_media_files WHERE media_id=?", (str(uuid.UUID(media_id)),))
    path.unlink(missing_ok=True)
    return {"ok": True}


def _extract(path: Path, row: dict):
    ext = path.suffix.lower(); kind = row["media_type"]; extracted = ""; description = None; summary = None; width = height = duration = None
    if kind == "document":
        if ext == ".txt": extracted = path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                extracted = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)[:1_000_000]
            except ImportError: pass
        elif ext == ".docx":
            try:
                from docx import Document
                extracted = "\n".join(p.text for p in Document(str(path)).paragraphs)[:1_000_000]
            except ImportError: pass
    elif kind == "image":
        try:
            from PIL import Image
            with Image.open(path) as im: width, height, row["format"] = im.width, im.height, (im.format or row["format"]).lower()
        except ImportError: pass
        vision_key = os.getenv("VISION_API_KEY")
        if vision_key:
            try:
                from openai import OpenAI
                import base64
                client = OpenAI(api_key=vision_key, base_url=os.getenv("VISION_BASE_URL") or None)
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                response = client.chat.completions.create(
                    model=os.getenv("VISION_MODEL", "gpt-4o-mini"),
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": "Describe the visible contents of this image concisely."},
                        {"type": "image_url", "image_url": {"url": f"data:{row['mime_type']};base64,{encoded}"}},
                    ]}], max_tokens=300,
                )
                description = response.choices[0].message.content
            except Exception:
                raise
    elif kind == "audio" and os.getenv("TRANSCRIPTION_API_KEY"):
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("TRANSCRIPTION_API_KEY"), base_url=os.getenv("TRANSCRIPTION_BASE_URL") or None)
        with path.open("rb") as audio_file:
            transcript = client.audio.transcriptions.create(model=os.getenv("TRANSCRIPTION_MODEL", "whisper-1"), file=audio_file)
        extracted = transcript.text[:1_000_000]
    if kind == "video":
        ffprobe = os.getenv("FFPROBE_PATH", "ffprobe")
        try:
            r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)], capture_output=True, text=True, timeout=15, check=True)
            import json
            info = json.loads(r.stdout); duration = float(info.get("format", {}).get("duration", 0)) or None
            v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
            width, height = v.get("width"), v.get("height")
        except (OSError, subprocess.SubprocessError, ValueError): pass
    return extracted, description, summary, width, height, duration


def process_media(media_id: str):
    row = get_media(media_id); path = safe_path(media_id)
    with connect() as db: db.execute("UPDATE queryflow_media_files SET processing_status='processing',processing_error=NULL WHERE media_id=?", (row["media_id"],))
    try:
        extracted, description, summary, width, height, duration = _extract(path, row)
        # Summarize only when the existing NVIDIA text provider is configured.
        if (extracted or description) and os.getenv("NVIDIA_API_KEY"):
            from openai import OpenAI
            client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=os.getenv("NVIDIA_API_KEY"))
            response = client.chat.completions.create(
                model=os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-nano-30b-a3b"),
                messages=[{"role": "user", "content": "Summarize this extracted media content in two sentences. Do not add facts.\n\n" + (description or extracted)[:12000]}],
                temperature=0, max_tokens=180,
            )
            summary = response.choices[0].message.content
        with connect() as db:
            db.execute("UPDATE queryflow_media_files SET extracted_text=?,description=?,ai_summary=?,width=?,height=?,duration_seconds=?,processing_status='completed',processed_at=? WHERE media_id=?",
                (extracted or None, description, summary, width, height, duration, datetime.now(timezone.utc).isoformat(), row["media_id"]))
    except Exception as exc:
        with connect() as db: db.execute("UPDATE queryflow_media_files SET processing_status='failed',processing_error=? WHERE media_id=?", (str(exc)[:1000], row["media_id"]))


def semantic_search(query: str, limit: int = 20):
    # Graceful keyword fallback when no embedding backend is configured.
    normalized_query = re.sub(r"[^\w]+", " ", query.lower())
    terms = [t for t in normalized_query.split() if len(t) > 1]
    if not terms: return {"mode": "keyword_fallback", "results": []}
    filename = "replace(replace(replace(lower(COALESCE({field},'')), '_',' '), '-',' '), '.',' ') LIKE ?"
    clauses = " OR ".join([filename.format(field="original_name") + " OR " + filename.format(field="file_name") + " OR lower(COALESCE(extracted_text,'')) LIKE ? OR lower(COALESCE(description,'')) LIKE ? OR lower(COALESCE(ai_summary,'')) LIKE ?"] * len(terms))
    values = [v for t in terms for v in (f"%{t}%",) * 5]
    with connect() as db:
        rows = [dict(r) for r in db.execute(f"SELECT media_id,file_name,original_name,media_type,file_size,storage_path,description,ai_summary,processing_status FROM queryflow_media_files WHERE {clauses} ORDER BY uploaded_at DESC LIMIT ?", (*values, limit))]
        for item in rows:
            candidate = (STORAGE_ROOT / str(item.pop("storage_path", ""))).resolve()
            item["file_available"] = bool(candidate.is_relative_to(STORAGE_ROOT) and candidate.is_file())
    return {"mode": "keyword_fallback", "results": rows}
