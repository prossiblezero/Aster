"""Rebuild the comparison from preserved requests and independent test reports."""
import gzip
import hashlib
import statistics
import time

from evaluate import AGENTS, HERE, PRIVATE, imported, load, save, verify_frozen

previous = imported('general50_summary_helpers', HERE.parent / 'general-coding-20261010/summarize.py')


def aggregate(rows):
    result = {key: sum(bool(r[key]) for r in rows) for key in
              ('passed', 'graded', 'grading_error', 'timed_out', 'normal_delivery_and_passed')}
    result.update(finished=len(rows), seconds=round(sum(r['seconds'] for r in rows), 3),
                  median_seconds=round(statistics.median(r['seconds'] for r in rows), 3) if rows else None)
    for key in ('requests', 'request_errors', 'missing_usage', 'input_tokens', 'cached_tokens', 'output_tokens', 'total_tokens'):
        result[key] = sum(r[key] for r in rows)
    result['uncached_input_tokens'] = result['input_tokens'] - result['cached_tokens']
    result['reported_tokens_per_pass'] = round(result['total_tokens'] / result['passed']) if result['passed'] else None
    return result


def main():
    manifest = verify_frozen()
    tasks = load(PRIVATE / 'tasks.json')
    rows, active = [], []
    for task in tasks:
        for agent in AGENTS:
            root = PRIVATE / 'runs' / (agent + '-' + task['id'])
            if not (root / 'result.json').exists():
                if (root / 'launch.json').exists():
                    active.append({'job': root.name, 'elapsed': round(time.time() - load(root / 'launch.json')['started'])})
                continue
            row = load(root / 'result.json')
            usage, requests = previous.usage(root)
            row.update(usage)
            final, phase, stop = previous.final_text(root, agent)
            row.update(final=final, phase=phase, stop_reason=stop, source=task['source'],
                       language=task['language'], exposure=task['exposure'], difficulty=task['difficulty'])
            if task['kind'] == 'repository':
                score_root = PRIVATE / 'logs/evaluation' / ('general50-' + agent) / agent / task['id']
                report = score_root / 'report.json'
                grade = load(report).get(task['id']) if report.exists() else None
                row.update(grade=grade, graded=grade is not None, passed=bool(grade and grade.get('resolved')),
                           grading_error=bool(grade and (grade.get('infra_failure') or grade.get('infra_failure_reason'))))
                test_output = score_root / 'test_output.txt'
                patch = (root / 'prediction.patch').read_bytes()
                assert hashlib.sha256(patch).hexdigest() == row['patch_sha256']
            else:
                row.update(graded='grade' in row, passed=bool(row.get('grade', {}).get('passed')), grading_error=False)
                test_output = root / 'grading/test_output.txt'
            row['normal_delivery_and_passed'] = (row['passed'] and row['exit_code'] == 0 and bool(final)
                                                  and (agent != 'aster' or phase == 'completed'))
            row['provider_fault_observed'] = row['request_errors'] > 0
            public = HERE / 'results' / agent / task['id']
            save(public / 'result.json', row)
            save(public / 'launch.json', load(root / 'launch.json'))
            save(public / 'usage.json', [{k: v for k, v in r.items() if k != 'usage'} |
                 {'usage': {k: v for k, v in (r.get('usage') or {}).items() if k != 'attribution'}} for r in requests])
            if task['kind'] == 'repository':
                (public / 'prediction.patch').write_bytes(patch)
            if test_output.exists():
                (public / 'tests.txt.gz').write_bytes(gzip.compress(test_output.read_bytes(), mtime=0))
            rows.append(row)
    summary = {'planned': 150, 'finished': len(rows), 'graded': sum(r['graded'] for r in rows), 'active': active,
               'agents': {}}
    for agent in AGENTS:
        group = [r for r in rows if r['agent'] == agent]
        stats = aggregate(group)
        for field in ('source', 'exposure', 'language'):
            stats['by_' + field] = {value: aggregate([r for r in group if r[field] == value])
                                    for value in sorted({t[field] for t in tasks})}
        summary['agents'][agent] = stats
    save(HERE / 'results.json', rows)
    save(HERE / 'summary.json', summary)
    audit = {'complete': len(rows) == summary['graded'] == 150, 'source_and_inputs_frozen': True,
             'preflight_ready': load(HERE / 'preflight.json')['ready'],
             'unique_jobs': len({r['job'] for r in rows}), 'agents': {a: sum(r['agent'] == a for r in rows) for a in AGENTS},
             'score_policy': 'one fresh attempt per task; no historical substitutions',
             'formal_provider_requests': sum(r['requests'] for r in rows),
             'missing_usage': sum(r['missing_usage'] for r in rows)}
    save(HERE / 'audit.json', audit)
    lines = ['# Aster / pi / Codex：50 题同模型比较', '',
             ('已完成全部 150 次尝试和独立评分。' if audit['complete'] else
              f'评测进行中：完成 {len(rows)}/150 次，已评分 {summary["graded"]}/150 次。以下不是最终排名。'), '',
             '固定 50 题：SWE-bench Verified 20、Terminal-Bench 8、Aider Polyglot 12、BigCodeBench 10。'
             '旧 20 题已参与开发，新增 30 题单列；三者本轮全部重新运行，未拼接历史最好成绩。', '',
             '| Agent | 独立通过 / 50 | 正常交付且通过 | 请求数 | 已报告总 token | 缺用量请求 | 累计答题分钟 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for agent, stats in summary['agents'].items():
        lines.append(f'| {agent} | {stats["passed"]}/50 ({stats["passed"] * 2}%) | {stats["normal_delivery_and_passed"]}/50 | '
                     f'{stats["requests"]:,} | {stats["total_tokens"]:,} | {stats["missing_usage"]} | {stats["seconds"] / 60:.1f} |')
    for field, title in [('source', '按来源'), ('exposure', '旧题与新增题')]:
        lines += ['', '## ' + title, '', '| 分组 | 题数 | Aster | pi | Codex |', '|---|---:|---:|---:|---:|']
        for value in sorted({t[field] for t in tasks}):
            n = sum(t[field] == value for t in tasks)
            results = [str(summary['agents'][a]['by_' + field][value]['passed']) + '/' + str(n) for a in AGENTS]
            lines.append('| ' + value + f' | {n} | ' + ' | '.join(results) + ' |')
    lines += ['', '## 用量口径', '',
              '| Agent | 非缓存输入 | 缓存输入 | 输出 | 超时题数 | 有供应商错误的题数 |',
              '|---|---:|---:|---:|---:|---:|']
    for a, stats in summary['agents'].items():
        lines.append(f'| {a} | {stats["uncached_input_tokens"]:,} | {stats["cached_tokens"]:,} | {stats["output_tokens"]:,} | '
                     f'{stats["timed_out"]} | {sum(r["agent"] == a and r["provider_fault_observed"] for r in rows)} |')
    lines += ['', '缺失用量不当作零成本；没有可信供应商单价，不换算金额。供应商错误出现过不等于该题必然因此失败。', '',
              '## 逐题结果', '', '| 任务 | 来源 | 分组 | Aster | pi | Codex |', '|---|---|---|---|---|---|']
    for task in tasks:
        values = []
        for a in AGENTS:
            r = next((r for r in rows if r['agent'] == a and r['task'] == task['id']), None)
            value = '待运行' if r is None else ('通过' if r['passed'] else ('评分中' if not r['graded'] else '未通过'))
            if r and r['timed_out']:
                value += '（超时）'
            if r and r['grading_error']:
                value += '（评分错误）'
            values.append(value)
        lines.append(f'| `{task["id"]}` | {task["source"]} | {task["exposure"]} | ' + ' | '.join(values) + ' |')
    lines += ['', '## 复现与边界', '',
              f'Aster `{manifest["aster_commit"][:7]}`、pi {manifest["pi_version"]}、Codex CLI {manifest["codex_version"]}；'
              '同模型 gpt-5.6-luna，medium，900 秒/题，64000 上下文，8192 输出上限，4 CPU / 8 GiB，6 槽并发。', '',
              '这是公开任务的固定本地适配混合集，不是上游完整榜单。Python 仍占主导；公开数据可能被基础模型见过。'
              '单次 50 题仍不足以断言稳定排名；本轮没有产品改动前后对照，不能归因于验收或成本优化。', '',
              '独立评分保留原测试断言。Aider/BigCodeBench 采用新容器，只转交指定解答文件；SWE 使用官方评分。'
              'Terminal 为保留本地服务在题目专属容器内注入测试，原断言的防作弊和覆盖能力有限。'
              'JavaScript 启用必做 xtest/xit，明确可选 test.skip 保留；C++ 启用全部必做测试。', '',
              '[协议](PROTOCOL.md) · [冻结清单](manifest.json) · [正反预检](preflight.json) · [逐请求和逐题结果](results.json) · '
              '[完整性检查](audit.json) · [选题](../../datasets/general-coding-50-20261012/selection.json)', '']
    (HERE / 'REPORT.md').write_text('\n'.join(lines))
    print(json_text({'finished': len(rows), 'graded': summary['graded'], 'active': active,
                     'agents': {a: {'passed': s['passed'], 'finished': s['finished']} for a, s in summary['agents'].items()}}))


def json_text(value):
    import json
    return json.dumps(value, ensure_ascii=False)


if __name__ == '__main__':
    main()
