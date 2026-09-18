# Python CLI quality checklist

- Test the command as a subprocess at least once, checking exit status,
  stdout and stderr together. Function-only tests can miss CLI wiring errors.
- Identify whether arguments denote files, directories or standard input.
  Match the requested interface exactly and test missing/extra arguments.
- Validate input types explicitly. Python booleans are instances of `int`;
  consider whether the specification intends to accept them as numbers.
- Check empty input, Unicode, malformed bytes, malformed data and boundary
  values relevant to the contract. Do not silently coerce invalid types.
- Validate before publishing output when partial output would violate the
  contract. Expected user mistakes should produce readable diagnostics.
- Make deterministic ordering explicit when requested. Avoid accidental
  dependence on filesystem order or insertion order from external input.
- Treat source inputs as read-only. Confirm they are unchanged after both
  successful and failing calls.
- Keep runtime dependencies in the standard library when practical.
  Report only tests actually run, including failures and limitations.
