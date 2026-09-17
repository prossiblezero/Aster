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

`ROOT` may be one Markdown file or a directory. Directories are scanned recursively for `.md` files. With no `ROOT`, the current working directory's `manual` directory is checked. The default output format is `text`; an explicit `ROOT` overrides the default.

```sh
python -m mdcheck
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
- Heading anchors in pure fragment links (`#heading`) and local Markdown targets (`guide.md#heading`)

Anchors come from ATX headings outside fenced code: lines starting with 1-6 `#` characters followed by a space. Slugs lowercase the heading, remove ASCII punctuation except hyphens and underscores, replace whitespace sequences with `-`, and preserve non-ASCII characters. Repeated heading slugs receive `-1`, `-2`, and subsequent suffixes. Fragments are percent-decoded and matched exactly (case-sensitive). Pure fragment links refer to the source document and count toward `checked`. Empty fragments skip anchor validation. Missing paths report `missing_target`; existing Markdown files with missing anchors report `missing_anchor`. Non-Markdown targets, including directories, only require an existing path, regardless of their fragment.

URL schemes such as `http`, `https`, and `mailto`, and protocol-relative URLs beginning with `//`, are skipped. Reference-style links, Setext headings, and full CommonMark parsing are outside the current scope. Symbolic-link files and directories are not scanned, and the checker never accesses the network or modifies documents.

## Exit codes

- `0`: all checked local paths and Markdown anchors exist
- `1`: one or more local paths or Markdown anchors are missing
- `2`: invalid arguments, a missing root, or an input/read error

## Tests

Run the standard-library test suite from the repository root:

```sh
python -m unittest discover -s tests -v
```
