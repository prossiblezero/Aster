# mdcheck

`mdcheck` is a small command-line checker for local paths in Markdown inline links and images. It requires Python 3.12 or newer and has no runtime dependencies outside the standard library.

## Installation

From a checkout, run:

```sh
python -m pip install .
```

Installation provides both the `mdcheck` command and the `python -m mdcheck` module entry point. Running directly from a checkout does not require installation.

## Usage

```sh
python -m mdcheck [ROOT] [--format text|json]
```

`ROOT` may be one Markdown file or a directory. Directories are scanned recursively for `.md` files. The default root is `docs`, and the default output format is `text`.

```sh
python -m mdcheck examples
python -m mdcheck README.md --format json
mdcheck docs
```

JSON output is one object with a `checked` count and a `broken` array. Each broken entry contains `source`, `line`, `target`, and `reason`.

## Supported scope

- Inline links (`[label](target)`) and images (`![alt](target)`)
- Relative and absolute local paths, UTF-8 paths, and percent-encoded paths such as `%20`
- Optional quoted titles and angle-bracket destinations
- Existing files and directories as valid targets
- Deterministic recursive scanning and result ordering
- Fenced code blocks opened by three or more backticks or tildes

URL schemes such as `http`, `https`, and `mailto`, protocol-relative URLs beginning with `//`, and pure fragment links such as `#heading` are skipped. For a target containing both a path and fragment, only the path is checked. Anchor existence is not checked. Reference-style links and full CommonMark parsing are outside the current scope. Symbolic-link files and directories are not scanned, and the checker never accesses the network or modifies documents.

## Exit codes

- `0`: all checked local targets exist
- `1`: one or more local targets are missing
- `2`: invalid arguments, a missing root, or an input/read error

## Tests

Run the standard-library test suite from the repository root:

```sh
python -m unittest discover -s tests -v
```
