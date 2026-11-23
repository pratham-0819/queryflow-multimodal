"""Automated test suite to verify all fixes in qf_updated."""
import os
import sys
from pathlib import Path

# Ensure qf_updated is in sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import app
from text2sql_core import (
    validate_sql, detect_operation, run_query, execute_write,
    get_provider_and_model, get_client, UnsafeSQLError
)
from fastapi.testclient import TestClient


def test_sql_validation():
    print("Testing SQL validation...")

    # 1. SELECT and JOIN queries
    v = validate_sql("SELECT * FROM customers", "query")
    assert v == "SELECT * FROM customers"

    v = validate_sql("SELECT c.name, o.order_date FROM customers c JOIN orders o ON c.customer_id = o.customer_id", "query")
    assert "JOIN" in v

    # 2. Scalar REPLACE in SELECT (must not be blocked)
    v = validate_sql("SELECT REPLACE(email, '@example.com', '@test.com') FROM customers", "query")
    assert "REPLACE" in v

    # 3. INSERT
    v = validate_sql("INSERT INTO customers (name, email, country, signup_date) VALUES ('Alice', 'alice@test.com', 'India', '2026-01-01')", "insert")
    assert v.startswith("INSERT")

    # 4. UPDATE with SET and WHERE (Must succeed now!)
    v = validate_sql("UPDATE customers SET country = 'USA' WHERE customer_id = 1", "update")
    assert v.startswith("UPDATE")

    # 5. UPDATE without WHERE (Must be blocked)
    try:
        validate_sql("UPDATE customers SET country = 'USA'", "update")
        assert False, "Should have blocked UPDATE without WHERE"
    except UnsafeSQLError:
        pass

    # 6. DELETE with WHERE (Must succeed)
    v = validate_sql("DELETE FROM customers WHERE customer_id = 999", "delete")
    assert v.startswith("DELETE")

    # 7. DELETE without WHERE (Must be blocked)
    try:
        validate_sql("DELETE FROM customers", "delete")
        assert False, "Should have blocked DELETE without WHERE"
    except UnsafeSQLError:
        pass

    # 8. DDL attacks (Must all be blocked)
    for bad_sql in [
        "DROP TABLE customers",
        "ALTER TABLE customers ADD COLUMN x TEXT",
        "CREATE TABLE hack (id INT)",
        "PRAGMA foreign_keys = OFF",
        "ATTACH DATABASE 'evil.db' AS evil",
        "VACUUM",
        "SELECT * FROM customers; DROP TABLE orders;"
    ]:
        try:
            validate_sql(bad_sql)
            assert False, f"Should have blocked: {bad_sql}"
        except UnsafeSQLError:
            pass

    print("✓ SQL validation tests passed!")


def test_operation_detection():
    print("Testing operation detection...")
    assert detect_operation("SELECT * FROM customers") == "query"
    assert detect_operation("WITH cte AS (SELECT 1) SELECT * FROM cte") == "query"
    assert detect_operation("INSERT INTO customers VALUES (1, 'A', 'B', 'C', 'D')") == "insert"
    assert detect_operation("UPDATE customers SET name = 'A' WHERE customer_id = 1") == "update"
    assert detect_operation("DELETE FROM customers WHERE customer_id = 1") == "delete"
    print("✓ Operation detection tests passed!")


def test_provider_detection():
    print("Testing provider detection...")
    provider, model = get_provider_and_model()
    print(f"  Active provider: {provider}, model: {model}")
    assert provider in ("gemini", "nvidia", "openai")
    assert model is not None

    # Key inference tests
    assert get_provider_and_model("nvapi-12345")[0] == "nvidia"
    assert get_provider_and_model("AQ.12345")[0] == "gemini"
    assert get_provider_and_model("AIzaSy12345")[0] == "gemini"
    assert get_provider_and_model("sk-12345")[0] == "openai"
    print("✓ Provider detection tests passed!")


def test_database_and_fastapi_endpoints():
    print("Testing FastAPI endpoints...")
    client = TestClient(app.app)

    # 1. Root HTML
    r = client.get("/")
    assert r.status_code == 200
    assert "QueryFlow" in r.text

    # 2. Database info
    r = client.get("/api/database")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "sample.db"
    assert "provider" in data
    assert "model" in data

    # 3. Schema
    r = client.get("/api/schema")
    assert r.status_code == 200
    schema = r.json()
    for expected_table in ["customers", "products", "orders", "order_items"]:
        assert expected_table in schema

    # 4. History
    r = client.get("/api/history")
    assert r.status_code == 200

    # 5. Execute guarded write for UPDATE
    app.PREVIEWS["test-token-unit-update"] = {
        "sql": "UPDATE customers SET country = 'India' WHERE customer_id = 1",
        "operation": "update",
        "question": "Set country to India for customer 1",
        "history_id": 1,
    }
    r = client.post("/api/execute", json={"token": "test-token-unit-update", "confirmed": True})
    assert r.status_code == 200
    res = r.json()
    assert res["ok"] is True
    assert res["operation"] == "update"
    assert res["affected_rows"] == 1

    print("✓ FastAPI endpoint tests passed!")


if __name__ == "__main__":
    test_sql_validation()
    test_operation_detection()
    test_provider_detection()
    test_database_and_fastapi_endpoints()
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")
