# linecounts

`linecounts.py` counts repeated nonblank lines in a UTF-8 text file. Each line is
stripped with `str.strip()` before counting. The result is written as one JSON
object whose keys are sorted in Python string order.

## Usage

```console
$ python linecounts.py INPUT.txt
```

Exactly one input path is required. Use `python linecounts.py --help` for command
help. Invalid arguments, inaccessible inputs, directories, and non-UTF-8 input
exit with status 2 and print a short error to stderr.

## Example

Given a file named `示例 input.txt` containing:

```text
 apple
香蕉
apple

香蕉  
```

Run:

```console
$ python linecounts.py "示例 input.txt"
{"apple": 2, "香蕉": 2}
```

## Tests

```console
$ pytest -q
```
