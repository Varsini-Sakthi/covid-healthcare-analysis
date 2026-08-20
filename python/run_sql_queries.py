"""
run_sql_queries.py
---------------------------------------------------------------------
Runs every query in sql/02_analysis_queries.sql against the SQLite
database and prints each result as a table. Handles the file's
comment blocks and multi-statement CTEs correctly (a naive split on
';' breaks on this file, since the leading comment header and the
CTEs inside each query both contain semicolon-free content followed
by a final ';').

Run:  python python/run_sql_queries.py
"""

import re
import sqlite3
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "covid_healthcare.db"
SQL_PATH = ROOT / "sql" / "02_analysis_queries.sql"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 160)


def split_statements(sql_text: str):
    """Split on semicolons that end a statement, skipping comment-only chunks."""
    # Remove full-line comments for the purposes of finding statement boundaries,
    # but keep the original text for execution.
    raw_statements = re.split(r";\s*\n", sql_text)
    statements = []
    for raw in raw_statements:
        # Strip comment-only lines from the start of each chunk to check if
        # there's real SQL left.
        lines = raw.split("\n")
        code_lines = [ln for ln in lines if ln.strip() and not ln.strip().startswith("--")]
        if code_lines:
            statements.append(raw.strip())
    return statements


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found at {DB_PATH}. Run generate_synthetic_data.py first.")

    conn = sqlite3.connect(DB_PATH)
    sql_text = SQL_PATH.read_text()
    statements = split_statements(sql_text)

    print(f"Found {len(statements)} queries in {SQL_PATH.name}\n")

    for i, stmt in enumerate(statements, start=1):
        # Grab the query's own comment header (the "-- Qn. ..." line) for a nice label
        label_match = re.search(r"--\s*(Q\d+\..*)", stmt)
        label = label_match.group(1).strip() if label_match else f"Query {i}"

        print("=" * 100)
        print(label)
        print("=" * 100)
        try:
            df = pd.read_sql(stmt, conn)
            print(df.head(15).to_string(index=False))
            if len(df) > 15:
                print(f"... ({len(df)} total rows)")
        except Exception as e:
            print(f"ERROR running this query: {e}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
