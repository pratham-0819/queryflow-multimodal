# QueryFlow — NLP to SQL v4

QueryFlow is a modern FastAPI + HTML/CSS/JavaScript Text-to-SQL assistant supporting Google Gemini, NVIDIA Nemotron, and OpenAI with SQLite. The NLP box supports **SELECT, multi-table JOIN, INSERT, guarded UPDATE, and DELETE**.

## Features
- Natural-language SELECT queries
- Working multi-table JOIN generation using schema foreign keys
- Natural-language INSERT, UPDATE, and DELETE
- Manual checkbox confirmation before every database change (INSERT/UPDATE/DELETE)
- Exact generated SQL preview before writes
- Persistent SQL history stored in `query_history.db`
- Click a history item to restore its NLP request
- Optional **Use my SQL file** mode; the user must explicitly enable it and select a `.sql` file
- Uploaded SQL scripts are imported into an isolated SQLite database for the current server session
- Dynamic schema explorer and row counts
- SQL safety guardrails: DROP, ALTER, CREATE, PRAGMA, etc. are blocked
- Guarded writes: UPDATE and DELETE require a WHERE clause and explicit confirmation

## Quick Start

### Windows (PowerShell):
```powershell
cd qf_updated

# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies and configure the API key
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and set GEMINI_API_KEY (or configure another provider).

# 3. Create the local sample database
python create_sample_db.py

# 4. Start the server
python -m uvicorn app:app --reload --port 8000
```

### Windows (Command Prompt - CMD):
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
copy .env.example .env
# Edit .env and set GEMINI_API_KEY (or configure another provider).
python create_sample_db.py
python -m uvicorn app:app --reload --port 8000
```

### Linux / macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GEMINI_API_KEY (or configure another provider).
python3 create_sample_db.py
python -m uvicorn app:app --reload --port 8000
```

Open your browser at: **http://127.0.0.1:8000**

## Environment Configuration (.env)

Configure your API provider in `qf_updated/.env`:

```env
# Provider choice: 'gemini', 'nvidia', or 'openai'
AI_PROVIDER=gemini

# Gemini configuration
GEMINI_API_KEY=YOUR_GEMINI_KEY
GEMINI_MODEL=gemini-2.5-flash

# NVIDIA configuration (optional)
# NVIDIA_API_KEY=YOUR_NVIDIA_KEY
# NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b

# OpenAI configuration (optional)
# OPENAI_API_KEY=YOUR_OPENAI_KEY
# OPENAI_MODEL=gpt-4o-mini
```

## SQL NLP Syntax / Prompt Guide

You do **not** need to write SQL. Write a normal English instruction in the NLP box.

### General pattern

```text
[Action] + [what you want] + [table/entity] + [conditions] + [filters/grouping/sorting]
```

### 1. Read / SELECT

```text
Show all customers from India.
```

```text
How many customers are there per country?
```

```text
Show the top 5 products by price.
```

### 2. JOIN

When information belongs to more than one table, mention the entities/tables you want to combine. QueryFlow uses the schema's foreign keys as JOIN hints.

```text
Show customer names with their order dates and order status, joining customers and orders.
```

```text
Show each product name and total quantity sold by joining products and order_items.
```

```text
Show customer names and their total spending by joining customers, orders, and order_items.
```

```text
Which customers have never placed an order? Join customers with orders and show customers with no matching order.
```

### 3. Add / INSERT

```text
Add a new customer named Rahul Sharma with email rahul@example.com from India and signup date 2026-08-31.
```

```text
Add a product called Wireless Mouse in Electronics costing 799 with 50 units in stock.
```

QueryFlow shows the generated `INSERT` and **does not execute it until you tick the manual confirmation checkbox**.

### 4. Update / UPDATE

```text
Update the customer named Rahul Sharma and change the country to India.
```

```text
Set the price of Wireless Mouse to 899 where product_id is 31.
```

QueryFlow shows the generated `UPDATE` and requires manual confirmation. An UPDATE without a `WHERE` clause is blocked.

### 5. Delete / DELETE

```text
Delete the customer named Rahul Sharma with email rahul@example.com.
```

```text
Delete products in the Electronics category where stock is 0.
```

QueryFlow shows the generated `DELETE` and requires manual confirmation. A DELETE without a `WHERE` clause is blocked.

## Important NLP rules

- Use the actual table/entity names when the request could be ambiguous.
- For JOINs, mention the tables/entities that must be combined.
- Give values required for an INSERT.
- For DELETE, give a specific condition identifying the rows.
- If the schema cannot safely answer the request, QueryFlow returns `UNANSWERABLE_WITH_GIVEN_SCHEMA` rather than inventing columns or relationships.
- Only one SQL statement is generated at a time.
- CRUD support: Create = INSERT, Read = SELECT, Update = UPDATE, Delete = DELETE.
- UPDATE and DELETE always require a WHERE clause and manual confirmation.

## Optional SQL file

The **Use my SQL file** checkbox is off by default. When enabled, select a `.sql` file and click **Import SQL**. The script is loaded into an isolated SQLite database and becomes the active database for NLP queries. Use **Use sample database** to switch back.

The SQL history is kept separately in `query_history.db`, so changing the active database does not erase history.

## Multimodal Media Library

The Media section stores uploaded images, audio, video, and documents on disk, with searchable metadata in the active SQLite database's `queryflow_media_files` table. This dedicated table name avoids collisions with a `media_files` table that may already belong to an imported database. Uploads are limited to 20 files per request and 50 MiB per file by default. File type, MIME type, and file signatures are checked; stored names are generated by the server. Supported formats are JPG/JPEG, PNG, WEBP, MP3, WAV, M4A, OGG, MP4, MOV, AVI, WEBM, PDF, TXT, and DOCX. Media is saved under `media_store/`; set `MEDIA_STORAGE_DIR` in `.env` to choose another storage location, and `MAX_UPLOAD_BYTES` to change the per-file limit.

Text files are extracted directly. PDF and DOCX extraction requires optional `pypdf` and `python-docx`; image dimensions require optional Pillow; video dimensions and duration use `ffprobe` when installed. Optional AI processing uses `VISION_API_KEY`, `VISION_MODEL`, and optionally `VISION_BASE_URL` for image descriptions, `TRANSCRIPTION_API_KEY`, `TRANSCRIPTION_MODEL`, and optionally `TRANSCRIPTION_BASE_URL` for audio transcription, and the configured NVIDIA key for summaries. Without these optional keys, uploads, previews, metadata, basic extraction, and keyword search remain available. Media search currently uses keyword matching; vector embeddings, OCR, and video frame analysis are not included.

Install optional extractors only if needed:

```powershell
pip install pypdf python-docx Pillow
```

The Media Library lets you preview and download media, inspect processing status, retry processing, and delete uploaded files. Metadata is added with `CREATE TABLE IF NOT EXISTS`, so existing sample database tables and rows are retained. When a custom SQL database is active, its media metadata table is initialized there as well. Back up the SQLite database and `media_store` together.
