Build a small standard-library Python CLI in this workspace, starting from no implementation.

Create `eventcount.py`, a useful `README.md`, and automated tests. The interface is:

```text
python eventcount.py INPUT
```

`INPUT` is a UTF-8 JSON Lines file. Ignore blank or whitespace-only lines. Every other line must be a JSON object with `category` and `count` fields; ignore extra fields. `category` must be a nonempty string after stripping leading/trailing whitespace, and its stripped value is the grouping key. `count` must be an integer greater than or equal to zero; JSON booleans are invalid.

For valid input, print exactly one JSON object followed by a newline, with no stderr output. Its fields are:

- `categories`: a list of objects containing `category` and aggregated `count`, sorted by category using Python string order;
- `records`: number of nonblank input records;
- `total`: sum of all counts.

Preserve Unicode characters in the output rather than escaping them. Empty input is valid and yields an empty `categories` list and zero `records`/`total`. Counts can exceed 64-bit range and must remain exact integers.

Wrong argument counts, unknown options, a missing/non-file/unreadable input, invalid UTF-8 or JSON, and invalid record structures/types must exit with code 2, emit a helpful nonempty stderr message without a traceback, and leave stdout empty. Never alter the input file. Use only the standard library at runtime. Run your tests before finishing and document usage and error behavior.
