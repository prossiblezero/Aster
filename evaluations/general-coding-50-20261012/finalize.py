"""Audit completed attempts and add post-run interpretation without changing scores."""
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime

import summarize as summary
from evaluate import AGENTS, HERE, PRIVATE, load, save


def main():
    summary.main()
    rows = load(HERE / 'results.json')
    stats = load(HERE / 'summary.json')
    tasks = load(PRIVATE / 'tasks.json')
    assert stats['finished'] == stats['graded'] == 150 and not stats['active']
    expected = {(agent, task['id']) for agent in AGENTS for task in tasks}
    assert len(expected) == 150
    assert {(row['agent'], row['task']) for row in rows} == expected
    assert load(HERE / 'environment-preflight.json')['ready']
    assert {r['id'] for r in load(HERE / 'preflight.json')['tasks']} == {t['id'] for t in tasks}

    launches, request_ids, request_errors = {}, set(), {}
    verified_request_hashes = 0
    background, smoke, no_test_status, upstream_faults = {}, {}, [], []
    for task in tasks:
        identities = set()
        for agent in AGENTS:
            job = agent + '-' + task['id']
            launch = load(PRIVATE / 'runs' / job / 'launch.json')
            assert hashlib.sha256(launch['prompt'].encode()).hexdigest() == launch['prompt_sha256']
            identities.add(tuple(launch[k] for k in ('prompt_sha256', 'image_id', 'model', 'seconds_limit')))
            launches[job] = launch
        assert len(identities) == 1, task['id']

    usage_keys = ('requests', 'missing_usage', 'request_errors', 'input_tokens',
                  'cached_tokens', 'output_tokens', 'total_tokens')
    for agent in AGENTS:
        errors = Counter()
        background_rows = []
        for row in (r for r in rows if r['agent'] == agent):
            root = PRIVATE / 'runs' / row['job']
            usage, requests = summary.previous.usage(root)
            assert all(usage[k] == row[k] for k in usage_keys)
            for request in requests:
                ident = request['request_id']
                assert ident not in request_ids
                request_ids.add(ident)
                payload_file = root / 'provider' / (ident + '.request.json')
                if request.get('request_sha256'):
                    assert hashlib.sha256(payload_file.read_bytes()).hexdigest() == request['request_sha256']
                    verified_request_hashes += 1
                payload = load(payload_file)
                assert payload['model'] == 'gpt-5.6-luna'
                assert payload['reasoning']['effort'] == 'medium'
                assert payload['max_output_tokens'] == 8192
                if request.get('error'):
                    errors[request['error']] += 1
                if request.get('stream_error'):
                    errors['stream_error'] += 1
                if request.get('http_status', 200) >= 400:
                    errors['http_' + str(request['http_status'])] += 1
                if request.get('terminal') in ('response.failed', 'response.incomplete'):
                    errors[request['terminal']] += 1
                if request.get('http_status', 200) >= 500 or request.get('stream_error'):
                    upstream_faults.append({'agent': agent, 'task': row['task'],
                        'request_id': ident, 'http_status': request.get('http_status'),
                        'stream_error': request.get('stream_error'), 'passed': row['passed'],
                        'normal_delivery_and_passed': row['normal_delivery_and_passed']})
                if request.get('original_parameters', {}).get('max_output_tokens') == 768:
                    background_rows.append(request)
            if row['kind'] == 'repository' and not (row['grade'] or {}).get('tests_status'):
                no_test_status.append(row['job'])
            if row['kind'] != 'repository' and not row['grade'].get('tests'):
                no_test_status.append(row['job'])
            if row['kind'] != 'repository' and row['passed']:
                assert row['grade']['tests'] == row['grade']['expected_tests']
        request_errors[agent] = dict(errors)
        background[agent] = {
            'requests': len(background_rows),
            'missing_usage': sum(not r.get('usage') for r in background_rows),
            'total_tokens': sum((r.get('usage') or {}).get('total_tokens', 0) for r in background_rows),
            'classification': 'original max_output_tokens=768, matching the frozen Aster maintenance client',
        }
        usage, _ = summary.previous.usage(PRIVATE / 'runs' / ('smoke-' + agent))
        smoke[agent] = usage | {'seconds': load(PRIVATE / 'runs' / ('smoke-' + agent) / 'result.json')['seconds']}

    aster = [r for r in rows if r['agent'] == 'aster']
    completed = [r for r in aster if r['phase'] == 'completed']
    incomplete_passes = [r['task'] for r in aster if r['passed'] and not r['normal_delivery_and_passed']]
    false_completions = [r['task'] for r in completed if not r['passed']]
    first_launch = min(r['started'] for r in launches.values())
    observed = load(HERE / 'completion.json')['observed_finished_at']
    analysis = {
        'all_150_unique_attempts_scored': True,
        'same_prompts_images_models_and_task_deadlines': True,
        'formal_provider_requests': len(request_ids),
        'formal_request_hashes_checked': verified_request_hashes,
        'request_errors_by_type': request_errors,
        'observed_upstream_faults': upstream_faults,
        'zero_test_status_jobs': no_test_status,
        'aster_completion': {'completed': len(completed), 'completed_but_failed': false_completions,
                             'passed_without_normal_delivery': incomplete_passes},
        'background_usage_included_in_formal_totals': background,
        'smoke_usage_excluded_from_formal_totals': smoke,
        'formal_and_smoke_requests': len(request_ids) + sum(r['requests'] for r in smoke.values()),
        'formal_and_smoke_reported_tokens': sum(r['total_tokens'] for r in rows) + sum(r['total_tokens'] for r in smoke.values()),
        'formal_and_smoke_missing_usage': sum(r['missing_usage'] for r in rows) + sum(r['missing_usage'] for r in smoke.values()),
        'formal_first_launch_utc': datetime.fromtimestamp(first_launch, UTC).isoformat(),
        'runner_completion_observed_utc': datetime.fromtimestamp(observed, UTC).isoformat(),
        'formal_wall_minutes_upper_bound': round((observed - first_launch) / 60, 2),
        'wall_time_note': 'Includes formal container setup, solving and scoring; excludes preflight and integration smokes. Completion is the controller exit observation, so this is an upper bound.',
        'post_run_analysis': True,
    }
    save(HERE / 'final-audit.json', analysis)

    report = (HERE / 'REPORT.md').read_text()
    report = report.replace('有供应商错误的题数', '有请求异常的题数')
    report = report.replace('供应商错误出现过不等于该题必然因此失败。',
        '请求异常包含客户端断连、读取错误和超时，不等于已证实的供应商故障，也不等于该题必然因此失败。')
    extra = ['## 完成状态与异常核对', '',
        f'Aster 内部标记完成 {len(completed)} 题，其中 {len(false_completions)} 题未通过独立原始测试；'
        f'另有 {len(incomplete_passes)} 题产物通过，但未正常交付。', '',
        '内部完成但测试失败：' + '、'.join('`' + t + '`' for t in false_completions) + '。', '',
        '通过但未正常交付：' + '、'.join('`' + t + '`' for t in incomplete_passes) + '。', '',
        'Aster 的 `astropy-14182` 提交主动删除随仓库提供的数据文件，导致 pytest 初始化失败；'
        '模型补丁本身成功应用。这是提交引入的问题，不记作外部环境故障。'
        '正则棋类题的 Aster 轨迹记录三次 120 秒流式请求超时，期间已收到响应事件，'
        '最终以 provider_error 结束；HTTP 200 后的 BrokenPipeError 不能证明供应商宕机。'
        '其余异常分类、完整性校验见 [最终审计](final-audit.json) 和 [失败证据](failure-notes.json)。', '',
        '另有 Aster 的 C++ meetup 题超时后编译失败：实现中的两个方法名与头文件声明不一致，'
        '因此没有进入 90 项断言；参考解预检正常。这是候选代码问题。', '',
        '末段也出现真实上游异常：pi 的 Requests 2931 收到 HTTP 503、围棋计数题收到 HTTP/2 流错误，'
        '两题均未通过；pi 的 gRPC 遇到流错误，产物仍通过但未正常交付。'
        'Codex 的 Requests 2931 和 SymPy 23262 也分别遇到 503 与流错误，原生恢复后仍通过。'
        '这些尝试均保留原结果，未追加重跑；一题之差不能据此解释为稳定能力排名。', '',
        '## 上游数据质量限制', '',
        '事后检查发现 BigCodeBench 35 的返回值文字与示例/测试矛盾；602 未在题干说明矩阵大小和制表符分隔；'
        '804 检查未说明的异常类型、精确 open/write 调用；1052 限定 pyplot.savefig，拒绝 figure.savefig。'
        '这些限制影响对失败原因的解释，尤其不能把 1052 的 mock 失败直接等同于未保存图片。'
        '本轮保留全部 50 题和原断言分数，没有据结果换题、重采样或抬高成绩。'
        '因此这是初版混合集，后续扩充前仍需审核题意与断言的一致性。'
        '详见 [数据质量记录](dataset-quality-notes.json)。', '',
        '## 耗时与接入检查', '',
        '| Agent | 答题中位秒 | 接入检查请求 | 接入检查已报告 token | 接入检查缺用量 |',
        '|---|---:|---:|---:|---:|']
    for agent in AGENTS:
        r = smoke[agent]
        extra.append(f'| {agent} | {stats["agents"][agent]["median_seconds"]:.1f} | {r["requests"]} | {r["total_tokens"]:,} | {r["missing_usage"]} |')
    extra += ['', f'正式评测从首个任务启动到观察到控制进程退出约 {analysis["formal_wall_minutes_upper_bound"]:.1f} 分钟'
        '（上界，含容器准备与评分；不含前期环境预检和接入检查）。'
        f'正式任务加接入检查合计 {analysis["formal_and_smoke_requests"]:,} 次请求、'
        f'{analysis["formal_and_smoke_reported_tokens"]:,} 已报告 token、'
        f'{analysis["formal_and_smoke_missing_usage"]} 次缺失用量。', '',
        'Aster 后台记忆请求已计入正式用量。本轮网关将所有请求统一为 medium 和 8192 输出上限，'
        '包括后台请求原本声明的 768 上限；该设置与原生默认配置不同，因此用量代表本轮固定协议。'
        '各客户端保留自身的请求超时和重试语义；整题预算相同不意味着单请求截止时间相同。', '',
        '最终报告可用 `python evaluations/general-coding-50-20261012/finalize.py` 从保留的证据重新生成；'
        '该后处理脚本不修改冻结的执行协议、任务数据或原始得分。', '']
    (HERE / 'REPORT.md').write_text(report + '\n' + '\n'.join(extra))
    print(json.dumps({k: analysis[k] for k in ('all_150_unique_attempts_scored', 'formal_request_hashes_checked',
          'formal_and_smoke_requests', 'formal_and_smoke_reported_tokens', 'formal_and_smoke_missing_usage')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
