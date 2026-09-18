"""Count stripped, nonempty UTF-8 lines and emit a sorted JSON object."""

import argparse
from collections import Counter
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Count nonempty stripped lines in a UTF-8 text file."
    )
    parser.add_argument("input", metavar="INPUT.txt", help="UTF-8 input file")
    args = parser.parse_args(argv)

    counts = Counter()
    try:
        with open(args.input, "r", encoding="utf-8") as source:
            for line in source:
                text = line.strip()
                if text:
                    counts[text] += 1
    except UnicodeError:
        parser.exit(2, "error: input is not valid UTF-8\n")
    except OSError as error:
        parser.exit(2, "error: cannot read input: {}\n".format(error.strerror))

    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
