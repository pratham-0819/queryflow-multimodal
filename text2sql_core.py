"""Core logic for QueryFlow: schema-aware NLP -> SELECT/INSERT/UPDATE/DELETE SQL."""
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional
import pandas as pd
from openai import OpenAI

# Automatically load .env if present
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path)
    else:
        load_dotenv()
except Exception:
    pass

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

# Legacy alias
MODEL_NAME = NVIDIA_DEFAULT_MODEL


def get_provider_and_model(api_key: Optional[str] = None) -> tuple[str, str]:
    """Detect active AI provider and model based on config/keys."""
    explicit_provider = os.getenv("AI_PROVIDER", "").strip().lower()

    if api_key:
        k = api_key.strip()
        if k.startswith("nvapi-"):
            return "nvidia", os.getenv("NVIDIA_MODEL") or NVIDIA_DEFAULT_MODEL
        if k.startswith("AIza") or k.startswith("AQ."):
            return "gemini", os.getenv("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
        if k.startswith("sk-"):
            return "openai", os.getenv("OPENAI_MODEL") or OPENAI_DEFAULT_MODEL
        if explicit_provider in ("gemini", "nvidia", "openai"):
            default_m = (
                GEMINI_DEFAULT_MODEL if explicit_provider == "gemini"
                else NVIDIA_DEFAULT_MODEL if explicit_provider == "nvidia"
                else OPENAI_DEFAULT_MODEL
            )
            return explicit_provider, os.getenv(f"{explicit_provider.upper()}_MODEL") or default_m

    if explicit_provider == "gemini":
        return "gemini", os.getenv("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    if explicit_provider == "nvidia":
        return "nvidia", os.getenv("NVIDIA_MODEL") or NVIDIA_DEFAULT_MODEL
    if explicit_provider == "openai":
        return "openai", os.getenv("OPENAI_MODEL") or OPENAI_DEFAULT_MODEL

    if os.getenv("GEMINI_API_KEY"):
        return "gemini", os.getenv("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    if os.getenv("NVIDIA_API_KEY"):
        return "nvidia", os.getenv("NVIDIA_MODEL") or NVIDIA_DEFAULT_MODEL
    if os.getenv("OPENAI_API_KEY"):
        return "openai", os.getenv("OPENAI_MODEL") or OPENAI_DEFAULT_MODEL

    return "nvidia", NVIDIA_DEFAULT_MODEL


def get_client(api_key: Optional[str] = None) -> OpenAI:
    provider, model = get_provider_and_model(api_key)

    if provider == "gemini":
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("No Gemini API key found. Set GEMINI_API_KEY in .env or enter a key in the app.")
        client = OpenAI(base_url=GEMINI_BASE_URL, api_key=key, timeout=20.0, max_retries=0)
    elif provider == "openai":
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("No OpenAI API key found. Set OPENAI_API_KEY in .env or enter a key in the app.")
        client = OpenAI(api_key=key, timeout=20.0, max_retries=0)
    else:
        key = api_key or os.getenv("NVIDIA_API_KEY")
        if not key:
            raise ValueError("No API key found. Set GEMINI_API_KEY or NVIDIA_API_KEY in .env, or enter a key in the app.")
        client = OpenAI(base_url=os.getenv("NVIDIA_BASE_URL", NVIDIA_BASE_URL), api_key=key, timeout=20.0, max_retries=0)

    # Keep app metadata off the SDK's private `_provider` field. Newer OpenAI
    # SDK versions use that field internally when cloning clients with options.
    client._queryflow_provider = provider
    client._queryflow_model = model
    return client


def introspect_schema(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [r["name"] for r in cur.fetchall()]
    schema = {}
    for table in tables:
        cur.execute(f"PRAGMA table_info('{table}')")
        columns = [{"name": r["name"], "type": r["type"], "notnull": bool(r["notnull"]), "pk": bool(r["pk"]), "default": r["dflt_value"]} for r in cur.fetchall()]
        cur.execute(f"PRAGMA foreign_key_list('{table}')")
        fks = [{"column": r["from"], "references_table": r["table"], "references_column": r["to"]} for r in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) AS c FROM '{table}'")
        row_count = cur.fetchone()["c"]
        cur.execute(f"SELECT * FROM '{table}' LIMIT 5")
        sample_rows = [dict(r) for r in cur.fetchall()]
        schema[table] = {"columns": columns, "foreign_keys": fks, "row_count": row_count, "sample_rows": sample_rows}
    conn.close()
    return schema


def schema_to_prompt_text(schema: dict) -> str:
    lines = []
    for table, info in schema.items():
        defs = []
        for c in info["columns"]:
            tag = " PRIMARY KEY" if c["pk"] else ""
            nn = " NOT NULL" if c["notnull"] else ""
            defs.append(f"  {c['name']} {c['type']}{tag}{nn}")
        for fk in info["foreign_keys"]:
            defs.append(f"  FOREIGN KEY ({fk['column']}) REFERENCES {fk['references_table']}({fk['references_column']})")
        lines.append(f"CREATE TABLE {table} (\n" + ",\n".join(defs) + "\n);")
    return "\n\n".join(lines)


SYSTEM_PROMPT = """You are an expert SQLite SQL generator for a database assistant.
The user can ask to READ data, ADD data, UPDATE data, or DELETE data.

Choose exactly one operation based on intent:
- READ -> one SELECT (WITH ... SELECT is allowed)
- ADD -> one INSERT INTO ... VALUES statement
- UPDATE -> one UPDATE ... SET ... WHERE statement
- DELETE -> one DELETE FROM ... WHERE statement
- READ queries may use INNER JOIN, LEFT JOIN, RIGHT JOIN (if supported), CROSS JOIN, and multiple JOINs.
- Prefer explicit JOIN ... ON conditions using the supplied foreign-key relationships.
- For questions involving multiple tables, JOIN the tables instead of guessing or querying unrelated tables.

Rules:
- Use ONLY tables and columns in the supplied schema.
- Foreign keys in the schema are reliable join hints. For example, if orders.customer_id REFERENCES customers.customer_id, use orders JOIN customers ON orders.customer_id = customers.customer_id.
- Never invent a relationship when the schema gives no safe relationship.
- Output ONLY one valid SQLite statement. No markdown, comments, explanations, or multiple statements.
- Never generate DROP, ALTER, CREATE, REPLACE, ATTACH, DETACH, PRAGMA, VACUUM, REINDEX, TRUNCATE, GRANT, or REVOKE.
- For UPDATE, a WHERE clause is mandatory. Never update all rows.
- For DELETE, a WHERE clause is mandatory. Never delete all rows.
- Do not invent required values. If required information is missing, output exactly:
  SELECT 'UNANSWERABLE_WITH_GIVEN_SCHEMA' AS error;
- For ADD, do not explicitly insert an INTEGER primary key unless the user supplied it; let SQLite auto-generate it when possible.
- For ADD, include all required NOT NULL columns unless the schema supplies a default.
- For UPDATE, make the WHERE clause specific to the user's request.
- For DELETE, make the WHERE clause specific to the user's request.
- If the request is ambiguous or cannot be mapped safely to the schema, output the unanswerable statement.
"""


def build_prompt(schema_text: str, question: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Database schema:\n{schema_text}\n\nNatural-language request:\n{question}\n\nSQL:"},
    ]


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    else:
        text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _clean_sql(sql: str) -> str:
    cleaned = _strip_code_fences(sql)
    lines = []
    for line in cleaned.splitlines():
        line_clean = re.sub(r"--.*$", "", line).strip()
        if line_clean:
            lines.append(line_clean)
    return " ".join(lines)


def generate_sql(client: OpenAI, schema_text: str, question: str) -> str:
    provider = getattr(client, "_queryflow_provider", "nvidia")
    model = getattr(client, "_queryflow_model", MODEL_NAME)

    kwargs = {
        "model": model,
        "messages": build_prompt(schema_text, question),
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 1024,
    }
    if provider == "nvidia" and "nemotron" in model.lower():
        kwargs["extra_body"] = {"reasoning_budget": 2048}

    # Build list of candidate models (primary model first, then alternatives)
    models_to_try = [model]
    if provider == "gemini":
        for alt in ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]:
            if alt not in models_to_try:
                models_to_try.append(alt)

    last_exc = None
    deadline = time.monotonic() + 45
    for m in models_to_try:
        kwargs["model"] = m
        # Bound total provider wait time and disable SDK-level hidden retries.
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                request_client = client.with_options(timeout=min(15.0, remaining), max_retries=0)
                completion = request_client.chat.completions.create(**kwargs)
                return _clean_sql(completion.choices[0].message.content or "")
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                is_transient = "503" in err_str or "overloaded" in err_str or "unavailable" in err_str or "429" in err_str
                is_not_found = "404" in err_str or "not found" in err_str

                if is_not_found:
                    break  # Skip straight to next candidate model

                if is_transient and attempt < 1 and time.monotonic() < deadline:
                    time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                    continue

                break  # Try next candidate model

    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to generate SQL from AI provider.")


class UnsafeSQLError(Exception):
    pass


# Forbidden DDL/administrative commands (UPDATE is intentionally NOT forbidden; it is guarded with WHERE & confirmation)
FORBIDDEN = [
    "drop", "alter", "create", "attach", "detach",
    "pragma", "vacuum", "reindex", "truncate", "grant", "revoke"
]


def _single_statement(sql: str) -> str:
    cleaned = _clean_sql(sql)
    if not cleaned:
        raise UnsafeSQLError("Empty SQL generated.")
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if ";" in cleaned:
        raise UnsafeSQLError("Multiple statements are not allowed.")
    return cleaned


def validate_sql(sql: str, operation: str | None = None) -> str:
    cleaned = _single_statement(sql)
    low = cleaned.lower()

    # Block destructive DDL keywords
    if any(re.search(rf"\b{re.escape(k)}\b", low) for k in FORBIDDEN):
        raise UnsafeSQLError("Forbidden SQL keyword detected.")

    # Block REPLACE INTO or statements starting with REPLACE (while allowing scalar REPLACE() functions)
    if re.search(r"\breplace\s+into\b", low) or low.startswith("replace"):
        raise UnsafeSQLError("REPLACE statements are not allowed.")

    if operation == "query":
        if not (low.startswith("select") or low.startswith("with")):
            raise UnsafeSQLError("Query mode only permits SELECT statements.")
    elif operation == "insert":
        if not low.startswith("insert"):
            raise UnsafeSQLError("Add mode only permits INSERT statements.")
    elif operation == "update":
        if not low.startswith("update"):
            raise UnsafeSQLError("Update mode only permits UPDATE statements.")
        if not re.search(r"\bset\b", low):
            raise UnsafeSQLError("UPDATE requires a SET clause.")
        if not re.search(r"\bwhere\b", low):
            raise UnsafeSQLError("UPDATE requires a WHERE clause. Updating all rows is blocked.")
    elif operation == "delete":
        if not low.startswith("delete"):
            raise UnsafeSQLError("Delete mode only permits DELETE statements.")
        if not re.search(r"\bwhere\b", low):
            raise UnsafeSQLError("DELETE requires a WHERE clause. Deleting all rows is blocked.")
    else:
        if not (low.startswith("select") or low.startswith("with") or low.startswith("insert") or low.startswith("update") or low.startswith("delete")):
            raise UnsafeSQLError("Only SELECT, INSERT, UPDATE, and DELETE are allowed.")
        if low.startswith("update") and not re.search(r"\bwhere\b", low):
            raise UnsafeSQLError("UPDATE requires a WHERE clause.")
        if low.startswith("delete") and not re.search(r"\bwhere\b", low):
            raise UnsafeSQLError("DELETE requires a WHERE clause.")
    return cleaned


def detect_operation(sql: str) -> str:
    low = _single_statement(sql).lower()
    if low.startswith("insert"): return "insert"
    if low.startswith("update"): return "update"
    if low.startswith("delete"): return "delete"
    return "query"


def run_query(db_path: str, sql: str, row_limit: int = 500) -> pd.DataFrame:
    validated = validate_sql(sql, "query")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(validated, conn)
        return df.head(row_limit)
    finally:
        conn.close()


def execute_write(db_path: str, sql: str, operation: str) -> dict:
    validated = validate_sql(sql, operation)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cur = conn.execute(validated)
        affected = cur.rowcount if cur.rowcount != -1 else 0
        conn.commit()
        return {"affected_rows": affected, "last_row_id": cur.lastrowid if operation == "insert" else None}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
