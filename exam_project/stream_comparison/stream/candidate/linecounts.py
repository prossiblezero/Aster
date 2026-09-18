"""Count nonblank, stripped UTF-8 lines and emit a sorted JSON object."""

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Count nonblank lines in a UTF-8 text file.",
        allow_abbrev=False,
    )
    parser.add_argument("input", metavar="INPUT.txt", help="UTF-8 input file")
    args = parser.parse_args(argv)

    counts = {}
    try:
        with Path(args.input).open("r", encoding="utf-8") as source:
            for line in source:
                key = line.strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")

    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
