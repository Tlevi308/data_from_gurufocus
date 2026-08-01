#!/usr/bin/env python
"""
כלי פיתוח — חקירת המפתחות שה-API באמת מחזיר.

זה הכלי שפותרים איתו שדה שיצא MISSING בדוח הכיסוי: מחפשים את השם האמיתי
כאן, ומעדכנים את ``api_key`` במילון השדות (gurufocus/fields.py).

דוגמאות:
    python scripts/explore_keys.py AAPL                      # כל המפתחות לפי סקשן
    python scripts/explore_keys.py AAPL --search tax intang  # חיפוש תבנית
    python scripts/explore_keys.py AAPL --unmapped           # מה שקיים ולא נשלף
    python scripts/explore_keys.py AAPL --values --search ppe
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gurufocus.client import GuruFocusClient  # noqa: E402
from gurufocus.config import load_settings  # noqa: E402
from gurufocus.parsing import (  # noqa: E402
    block_to_records,
    extract_metadata,
    find_period_block,
    inventory_keys,
)
from gurufocus.resolver import resolve_fields, unmapped_api_keys  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbol", help="טיקר לחקירה")
    parser.add_argument("--period", default="quarterly",
                        choices=["quarterly", "annually", "ttm"])
    parser.add_argument("--search", nargs="+", metavar="PATTERN",
                        help="הצג רק מפתחות שמכילים אחת מהתבניות")
    parser.add_argument("--unmapped", action="store_true",
                        help="הצג רק מפתחות שקיימים ב-API אך לא נשלפים לפלט")
    parser.add_argument("--values", action="store_true",
                        help="הצג גם את הערך מהתקופה האחרונה")
    parser.add_argument("--refresh", action="store_true", help="התעלם מהמטמון")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    settings = load_settings()
    settings.validate()

    with GuruFocusClient(
        settings.api_key,
        timeout=settings.network.timeout_seconds,
        cache_dir=settings.cache.directory if settings.cache.enabled else None,
        cache_ttl_hours=settings.cache.ttl_hours,
    ) as client:
        payload = client.fundamentals(args.symbol, use_cache=not args.refresh)

    print(f"\nמפתחות ברמה העליונה: {list(payload)}")
    metadata = extract_metadata(payload)
    print(f"basic_information: {metadata}")

    block = find_period_block(payload, args.period)
    if block is None:
        print(f"לא נמצא בלוק '{args.period}'", file=sys.stderr)
        return 1

    records = block_to_records(block)
    keys = inventory_keys(records)
    last = records[-1] if records else {}
    print(f"תקופות: {len(records)} | מפתחות ייחודיים: {len(keys)}")

    if args.unmapped:
        resolution = resolve_fields(keys, metadata.keys())
        keys = unmapped_api_keys(keys, resolution.mapping)
        print(f"\n--- מפתחות שלא נשלפים לפלט ({len(keys)}) ---")

    if args.search:
        patterns = [p.lower() for p in args.search]
        keys = [k for k in keys if any(p in k.lower() for p in patterns)]
        print(f"\n--- תואמים ל-{args.search} ({len(keys)}) ---")

    current_section = None
    for key in keys:
        section, _, name = key.rpartition("::")
        section = section or "(row)"
        if section != current_section:
            print(f"\n[{section}]")
            current_section = section
        if args.values:
            print(f"   {name:<52} = {last.get(key)}")
        else:
            print(f"   {name}")

    if not keys:
        print("   — לא נמצאו מפתחות תואמים —")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
