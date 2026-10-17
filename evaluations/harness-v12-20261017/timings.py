"""Aggregate per-phase runtime timings (runs/<run_id>/timings.json inside each .aster.tar). Offline."""
import argparse
import json
import tarfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parents[1] / 'artifacts/private' / HERE.name


def collect(name):
    totals, tasks = defaultdict(lambda: [0.0, 0]), 0
    for archive in sorted((PRIVATE / name / 'runs').glob('aster-*/.aster.tar')):
        with tarfile.open(archive) as bundle:
            members = [m for m in bundle.getmembers() if m.name.endswith('/timings.json')]
            for member in members:
                for phase, (seconds, count) in json.load(bundle.extractfile(member)).items():
                    totals[phase][0] += seconds
                    totals[phase][1] += count
            tasks += bool(members)
    return tasks, dict(totals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    args = parser.parse_args()
    tasks, totals = collect(args.name)
    print(f'tasks with timings: {tasks}')
    print('| phase | seconds | count | ms per call |\n|---|---:|---:|---:|')
    for phase, (seconds, count) in sorted(totals.items(), key=lambda kv: -kv[1][0]):
        print(f'| {phase} | {seconds:.1f} | {count} | {1000 * seconds / max(count, 1):.0f} |')
    (HERE / 'runs' / args.name / 'timings.json').write_text(json.dumps({'tasks': tasks, 'phases': totals}, indent=2) + '\n')


if __name__ == '__main__':
    main()
