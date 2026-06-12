"""Gate 3 spot-check: print the 10 most recent transactions for manual verification.

Run from the project root:
    uv run python scripts/spot_check.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("finance.db")


def main() -> None:
    if not DB_PATH.exists():
        print(f"No DB found at {DB_PATH.resolve()} — run `finance sync` first.")
        return

    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT booking_date, amount, currency, description"
        " FROM transactions"
        " ORDER BY booking_date DESC, id DESC"
        " LIMIT 10"
    ).fetchall()

    if not rows:
        print("No transactions stored.")
        return

    print(f"{'Date':<12} {'Amount':>10} {'Cur':<4} Description")
    print("-" * 70)
    for booking_date, amount, currency, description in rows:
        print(f"{booking_date:<12} {amount:>10} {currency:<4} {description}")


if __name__ == "__main__":
    main()
