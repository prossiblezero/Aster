# eventcount

`eventcount.py` is a standard-library Python command-line tool that validates a UTF-8 JSON Lines file and aggregates nonnegative integer counts by category.

## Requirements

Python 3.9 or newer. The tool has no third-party runtime dependencies.

## Usage

```text
python eventcount.py INPUT
```

`INPUT` must be a readable regular file. Use `--` before a filename that begins with a hyphen.

Blank and whitespace-only lines are ignored. Every other line must be one JSON object containing:

- `category`: a string that is nonempty after leading and trailing whitespace is removed. The stripped string is used as the grouping key.
- `count`: an integer greater than or equal to zero. JSON booleans are not accepted as integers.

Additional object fields are ignored. Counts use Python's arbitrary-precision integers, so values larger than 64 bits remain exact.

For example, given `events.jsonl`:

```jsonl
{"category":"books","count":2}
{"category":" games ","count":5,"source":"shop"}
{"category":"books","count":3}
```

run:

```console
$ python eventcount.py events.jsonl
{"categories":[{"category":"books","count":5},{"category":"games","count":5}],"records":3,"total":10}
```

Categories are sorted using Python string ordering. Output is one UTF-8 JSON object followed by a newline; Unicode characters are emitted directly rather than as ASCII escape sequences. Empty input produces:

```json
{"categories":[],"records":0,"total":0}
```

## Errors

The command exits with status `2`, writes a diagnostic to standard error, and writes nothing to standard output when:

- the argument count is wrong or an option is unknown;
- the input is missing, is not a regular file, or cannot be read;
- the file contains invalid UTF-8 or invalid JSON; or
- a nonblank line has an invalid record shape, missing field, or invalid field type/value.

Diagnostics identify the input line when applicable. The input file is opened read-only and is never modified.

## Tests

Run the standard-library test suite from this directory:

```console
python -m unittest -v
```
