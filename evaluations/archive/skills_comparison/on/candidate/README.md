# eventcount

`eventcount.py` is a standard-library Python command-line tool that aggregates counts by category from a UTF-8 JSON Lines file.

## Usage

```console
python eventcount.py INPUT
```

`INPUT` must be a readable regular file. Blank and whitespace-only lines are ignored. Every other line must contain one JSON object with:

- `category`: a string that is nonempty after leading and trailing whitespace is stripped. The stripped string is used as the grouping key.
- `count`: a nonnegative JSON integer. Booleans, floating-point values, strings, and negative integers are rejected.

Extra object fields are ignored. Counts use Python's arbitrary-precision integers, so values larger than 64 bits remain exact. The input file is opened read-only and is never modified.

For example, given `events.jsonl`:

```jsonl
{"category":"books","count":2}
{"category":" music ","count":4,"source":"shop"}
{"category":"books","count":3}
```

running:

```console
python eventcount.py events.jsonl
```

prints one JSON object and a trailing newline:

```json
{"categories":[{"category":"books","count":5},{"category":"music","count":4}],"records":3,"total":9}
```

Categories are sorted using Python string order. `records` counts nonblank records, and `total` is the sum of their counts. Unicode is emitted directly rather than as JSON `\u` escapes. Empty or whitespace-only input produces:

```json
{"categories":[],"records":0,"total":0}
```

## Errors

The command exits with status `2`, writes a diagnostic to standard error, and writes nothing to standard output when:

- the argument count is wrong or an option is unknown;
- the input path is missing, is not a file, or cannot be read;
- the file is not valid UTF-8;
- a nonblank line is not valid JSON or is not a JSON object;
- a required field is missing or has an invalid value or type.

Expected input and usage errors do not produce Python tracebacks.

## Tests

Run the automated tests with:

```console
python -m unittest -v
```

The suite uses only the Python standard library and exercises the CLI through subprocesses as well as the unreadable-file handling path directly.
