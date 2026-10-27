"""Print a candidate card from supplied JSON; no persistence or network."""
import json
import sys

from research.evidence import build_card


def main():
    try:
        card = build_card(json.load(sys.stdin))
    except (ValueError, TypeError) as error:
        print(f"Invalid evidence input: {error}", file=sys.stderr)
        return 2
    print(json.dumps(card, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
