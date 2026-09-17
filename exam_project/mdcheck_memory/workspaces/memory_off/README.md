# mdcheck

`mdcheck` is a small command-line tool that checks local path targets in Markdown files. It requires Python 3.12 or newer and has no runtime dependencies outside the Python standard library.

## Installation

From a source checkout:

```console
python -m pip install .
```

Installation provides both the `mdcheck` command and the `python -m mdcheck` module entry point.

## Usage

```console
python -m mdcheck [ROOT] --format text|json
```

`ROOT` may be a Markdown file or a directory. It defaults to `docs`. The output format defaults to `text`; `json` emits exactly one object with `checked` and `broken` fields.

Examples:

```console
python -m mdcheck
python -m mdcheck README.md
python -m mdcheck examples/docs --format json
```

For directory inputs, `mdcheck` recursively scans `.md` files in deterministic order. Symbolic-link files and directories are not scanned. Relative targets are resolved from the Markdown file containing the link. Existing files and directories are both valid targets, and percent-encoded paths such as `guide%20one.md` are decoded before checking.

The checker handles common inline links and images. For pure fragment links and fragments on local Markdown files, it validates anchors generated from ATX headings outside fenced code blocks. Heading text is lowercased, ASCII punctuation other than `-` and `_` is removed, whitespace becomes `-`, and repeated slugs receive `-1`, `-2`, and subsequent numeric suffixes. Percent-encoded fragments are decoded before exact matching. Empty fragments and fragments on non-Markdown targets do not trigger anchor validation, though their local target occurrence is still counted.

Links inside backtick or tilde fenced code blocks are ignored. URL schemes such as `https:` and `mailto:`, and protocol-relative URLs beginning with `//`, are skipped. No full CommonMark parsing is attempted.

Exit status is `0` when every checked target exists, `1` when broken links are found, and `2` for invalid arguments, missing input, or filesystem/read errors. Diagnostics for operational errors are written to stderr. Documents are never modified and no network requests are made.

## Development

Run the standard-library test suite from the repository root:

```console
python -m unittest discover -s tests -v
```
