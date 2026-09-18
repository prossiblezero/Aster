# csvtotals

`csvtotals.py` groups a UTF-8 CSV file by `category` and exactly sums its `amount` values using Python's standard-library `Decimal` type. It writes a category-sorted JSON object whose amounts have two decimal places.

## Requirements

- Python 3.9 or newer
- `pytest` only for running the test suite; the tool itself uses only the Python standard library

## Usage

```bash
python csvtotals.py INPUT.csv
```

The CSV header must contain `category` and `amount`; other columns are allowed. UTF-8 BOM input is accepted. Blank records are ignored, category values are trimmed, and quoted category names can contain commas. Amounts must be valid finite decimal values. Totals are rounded only after summing, using `ROUND_HALF_UP`.

Successful output goes only to stdout and the exit status is `0`. Invalid input or an unreadable/missing file produces a short stderr message, no stdout output, and exit status `2`.

## Example

Given `sales.csv`:

```csv
category,amount,note
Office,10.005,Paper
"Food, drinks",4.25,Lunch
Office,-2.00,Refund
```

Run:

```bash
python csvtotals.py sales.csv
```

Output:

```json
{"Food, drinks": "4.25", "Office": "8.01"}
```

A file containing only `category,amount` outputs `{}`.

## Tests

```bash
python -m pytest -q
```
