"""Compatibility entry point; authoritative datasets and reports use benchmark v2."""

from benchmark import main

if __name__ == "__main__":
    raise SystemExit(main())
