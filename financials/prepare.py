"""Prepare supplied financial sources; real writes require --write."""
import argparse
import json
from pathlib import Path
import sys

from financials.statements import STATEMENTS
from financials.workpapers import prepare_workpapers


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("company_dir", type=Path)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--currency", choices=["CNY"], required=True)
    parser.add_argument("--write", action="store_true", help="Create a new candidate version; never overwrite")
    args = parser.parse_args(argv)
    try:
        sources = {k: json.loads((args.source_dir / f"{k}.json").read_text(encoding="utf-8"), parse_constant=lambda value: None if value == "NaN" else float(value)) for k in STATEMENTS}
        result = prepare_workpapers(sources, ticker=args.ticker, as_of=args.as_of,
                                   currency=args.currency, company_dir=args.company_dir, write=args.write)
    except (OSError, ValueError, TypeError) as error:
        print(f"Cannot prepare financial workpapers: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
