# v11 第二轮（Stage 1 + 2 + 3）

源码：`v11-stage3` / `8290388c59d21992baf145c8b6237a6848e23969`。成本仅取 attempt 1 的 old20；重复只用于质量判断。

| 指标 | 实测 | 门槛 | 达标 |
|---|---:|---:|:---:|
| all_32_planned_attempts_scored | True | == True | 是 |
| allowed_attempts_only | True | == True | 是 |
| stable_pass_with_conditional_rescue | True | == True | 是 |
| variable_passes_of_18 | 11 | >= 8 | 是 |
| evidence_errors | 1 | == 0 | 否 |
| aider_preexisting_declarations | 0 | == 0 | 是 |
| requests_turns_while_failing | 9 | <= 8 | 否 |
| turns | 236 | <= 304 | 是 |
| every_declaration_reviewed_as_unrelated | False | == True | 否 |

| 轮次指标 | v10 | 本轮 attempt 1 |
|---|---:|---:|
| turns | 358 | 236 |
| reread_after_own_edit | 39 | 13 |
| reread_only_turns | 27 | 5 |
| fresh_read_blocked | 11 | 0 |
| search_calls | 102 | 82 |
| search_only_turns | 21 | 26 |
| turns_while_failing | 60 | 36 |
| completion_gates | 10 | 8 |
| preexisting_declared | 0 | 3 |

前台有用量请求：250；搜索轮：26；加权成本：2.13270475（相对估算，非账单）。

| 题目 | 分组 | attempt 1 | attempt 2 | attempt 3 |
|---|---|---|---|---|
| django__django-17029 | 恒过 | 通过 | 未运行 | 未运行 |
| sympy__sympy-23262 | 恒过 | 通过 | 未运行 | 未运行 |
| sphinx-doc__sphinx-9230 | 恒过 | 通过 | 未运行 | 未运行 |
| pydata__xarray-7233 | 恒过 | 失败 | 通过 | 通过 |
| pydata__xarray-3677 | 恒过 | 通过 | 未运行 | 未运行 |
| pytest-dev__pytest-7571 | 恒过 | 通过 | 未运行 | 未运行 |
| psf__requests-2931 | 恒过 | 通过 | 未运行 | 未运行 |
| terminal-polyglot-c-py | 恒过 | 通过 | 未运行 | 未运行 |
| aider-simple-linked-list | 恒过 | 通过 | 未运行 | 未运行 |
| aider-sgf-parsing | 恒过 | 通过 | 未运行 | 未运行 |
| django__django-11400 | 恒败 | 失败 | 未运行 | 未运行 |
| sympy__sympy-17630 | 恒败 | 失败 | 未运行 | 未运行 |
| sphinx-doc__sphinx-11510 | 恒败 | 失败 | 未运行 | 未运行 |
| pytest-dev__pytest-10356 | 恒败 | 失败 | 未运行 | 未运行 |
| terminal-cancel-async-tasks | 波动 | 失败 | 通过 | 通过 |
| terminal-custom-memory-heap-crash | 波动 | 通过 | 失败 | 通过 |
| terminal-schemelike-metacircular-eval | 波动 | 失败 | 通过 | 通过 |
| terminal-kv-store-grpc | 波动 | 失败 | 失败 | 失败 |
| terminal-cobol-modernization | 波动 | 通过 | 通过 | 通过 |
| psf__requests-5414 | 波动 | 失败 | 通过 | 通过 |

## 接受的 pre-existing 声明

- 1:pytest-dev__pytest-10356:1961:pytest: testing.test_mark.TestFunctional::test_mark_decorator_subclass_does_not_propagate_to_base：`python -m pytest testing/test_mark.py -q -k 'test_mark_decorator_multiple_inheritance or test_mark_decorator_subclass_does_not_propagate_to_base'`；基线证据 `{"status": "failed", "evidence_id": "d2ff827837dc5f974502e52a08a1b9facea01e6bb07eda559aa18353a0cfc526", "check": {"command": "python -m pytest testing/test_mark.py -q", "timeout": 120, "workspace_hash": "aa189c81e1cfc5dd6c728eb17d58f9a7784f36294863276f61e79f9ad2c41110", "status": "error", "reason": "tests_failed", "test_results": {"pytest: testing.test_mark.TestFunctional::test_keyword_added_for_session": "failed", "pytest: testing.test_mark.TestFunctional::test_keywords_at_node_level": "failed", "pytest: testing.test_mark.TestFunctional::test_mark_closest": "failed", "pytest: testing.test_mark.TestFunctional::test_mark_decorator_baseclasses_merged": "failed", "pytest: testing.test_mark.TestFunctional::test_mark_decorator_subclass_does_not_propagate_to_base": "failed", "pytest: testing.test_mark.TestFunctional::test_mark_dynamically_in_funcarg": "failed", "pytest: testing.test_mark.TestFunctional::test_mark_should_not_pass_to_siebling_class": "failed", "pytest: testing.test_mark.TestFunctional::test_no_marker_match_on_unmarked_names": "failed", "pytest: testing.test_mark::test_mark_expressions_no_smear": "failed", "pytest: testing.test_mark::test_mark_on_pseudo_function": "failed", "pytest: testing.test_mark::test_mark_option[(((  xyz))  )-expected_passed1]": "failed", "pytest: testing.test_mark::test_mark_option[not not xyz-expected_passed2]": "failed", "pytest: testing.test_mark::test_mark_option[xyz or xyz2-expected_passed5]": "failed", "pytest: testing.test_mark::test_mark_option[xyz-expected_passed0]": "failed", "pytest: testing.test_mark::test_mark_option[xyz2-expected_passed4]": "failed", "pytest: testing.test_mark::test_mark_option_custom[interface-expected_passed0]": "failed", "pytest: testing.test_mark::test_mark_option_custom[not interface-expected_passed1]": "failed", "pytest: testing.test_mark::test_markers_from_parametrize": "failed", "pytest: testing.test_mark::test_parametrized_collect_with_wrong_args": "failed", "pytest: testing.test_mark::test_strict_prohibits_unregistered_markers[--strict]": "failed"}}, "seq": 1580}`
- 1:psf__requests-2931:2026:pytest: test_requests.TestTimeout::test_connect_timeout：`python -m pytest test_requests.py -k 'TestTimeout and (test_connect_timeout or test_total_timeout_connect)' --tb=short`；基线证据 `{"status": "failed", "evidence_id": "a43e2f620b2142f4738c7a91ecd2d254a56c8f1d2cf00c39b7e9862452b46df3", "check": {"command": "python -m pytest test_requests.py -k 'TestTimeout and (test_connect_timeout or test_total_timeout_connect)'", "timeout": 120, "workspace_hash": "c203c756ad6abb45526057607a6a1ad8c20531704955525acce0f0d6836f1aa7", "status": "error", "reason": "tests_failed", "test_results": {"pytest: test_requests.TestTimeout::test_connect_timeout": "failed", "pytest: test_requests.TestTimeout::test_total_timeout_connect": "failed"}}, "seq": 1695}`
- 1:psf__requests-2931:2026:pytest: test_requests.TestTimeout::test_total_timeout_connect：`python -m pytest test_requests.py -k 'TestTimeout and (test_connect_timeout or test_total_timeout_connect)' --tb=short`；基线证据 `{"status": "failed", "evidence_id": "a43e2f620b2142f4738c7a91ecd2d254a56c8f1d2cf00c39b7e9862452b46df3", "check": {"command": "python -m pytest test_requests.py -k 'TestTimeout and (test_connect_timeout or test_total_timeout_connect)'", "timeout": 120, "workspace_hash": "c203c756ad6abb45526057607a6a1ad8c20531704955525acce0f0d6836f1aa7", "status": "error", "reason": "tests_failed", "test_results": {"pytest: test_requests.TestTimeout::test_connect_timeout": "failed", "pytest: test_requests.TestTimeout::test_total_timeout_connect": "failed"}}, "seq": 1695}`
- 3:psf__requests-5414:2311:pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout0]：`python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout1]'`；基线证据 `{"status": "failed", "evidence_id": "1334cdfeb85e99eba3a38460ed513d10e66016f1fad4e78cab328d31f4f01a91", "check": {"command": "python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]'", "timeout": 120, "workspace_hash": "f79bad4d85c40ababbffbe8bb57eab97a68d457b216936ed511f7fcfcdf83e98", "status": "error", "reason": "tests_failed", "test_results": {"pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout0]": "failed", "pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout1]": "failed", "pytest: tests.test_requests.TestTimeout::test_total_timeout_connect[timeout0]": "failed"}}, "seq": 2034}`
- 3:psf__requests-5414:2311:pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout1]：`python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout1]'`；基线证据 `{"status": "failed", "evidence_id": "1334cdfeb85e99eba3a38460ed513d10e66016f1fad4e78cab328d31f4f01a91", "check": {"command": "python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]'", "timeout": 120, "workspace_hash": "f79bad4d85c40ababbffbe8bb57eab97a68d457b216936ed511f7fcfcdf83e98", "status": "error", "reason": "tests_failed", "test_results": {"pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout0]": "failed", "pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout1]": "failed", "pytest: tests.test_requests.TestTimeout::test_total_timeout_connect[timeout0]": "failed"}}, "seq": 2034}`
- 3:psf__requests-5414:2311:pytest: tests.test_requests.TestTimeout::test_total_timeout_connect[timeout0]：`python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout1]'`；基线证据 `{"status": "failed", "evidence_id": "1334cdfeb85e99eba3a38460ed513d10e66016f1fad4e78cab328d31f4f01a91", "check": {"command": "python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]'", "timeout": 120, "workspace_hash": "f79bad4d85c40ababbffbe8bb57eab97a68d457b216936ed511f7fcfcdf83e98", "status": "error", "reason": "tests_failed", "test_results": {"pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout0]": "failed", "pytest: tests.test_requests.TestTimeout::test_connect_timeout[timeout1]": "failed", "pytest: tests.test_requests.TestTimeout::test_total_timeout_connect[timeout0]": "failed"}}, "seq": 2034}`
- 3:psf__requests-5414:2311:pytest: tests.test_requests.TestTimeout::test_total_timeout_connect[timeout1]：`python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout1]'`；基线证据 `{"status": "failed", "evidence_id": "d8e5765d2119e47817ce500e950dd8706bab681a608c824845aff49493dc7e67", "check": {"command": "python -m pytest 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout0]' 'tests/test_requests.py::TestTimeout::test_connect_timeout[timeout1]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout0]' 'tests/test_requests.py::TestTimeout::test_total_timeout_connect[timeout1]'", "timeout": 120, "workspace_hash": "f79bad4d85c40ababbffbe8bb57eab97a68d457b216936ed511f7fcfcdf83e98", "status": "error", "reason": "tests_failed", "test_results": {"pytest: tests.test_requests.TestTimeout::test_total_timeout_connect[timeout1]": "failed"}}, "seq": 2311}`

## 请求异常（全部尝试，包含缺用量）

- {"task": "aider-simple-linked-list", "attempt": 1, "background": false, "missing_usage": true, "request_id": "07c1a4381c734a79a2344cda7f235941", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "9e2a4e9b7484459e8b18e61115d01907", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "6a363eadb72b4e0d8069ef337915e2d6", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "86f1b1af090a4fb985ab43f39e973aa0", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "07232eaa85224d4ab3583f70766e6265", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "c1f65161a9a44f95a05ad17239b6dcdd", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "cc5f66acf01342bb9e0bd7e56301fc8f", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "a7e311dfd66342acb02c0e5caae28e41", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sphinx-doc__sphinx-9230", "attempt": 1, "background": false, "missing_usage": true, "request_id": "a4cfe7f28fca40039b3b0c4e88c6fcbc", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 1, "background": false, "missing_usage": true, "request_id": "bf8ab59a07df4b1899e8e395b23d126c", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 1, "background": false, "missing_usage": true, "request_id": "4a3b8fe791a64dc99c9bdcd97f0e8506", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 1, "background": false, "missing_usage": true, "request_id": "f52fb0e876c2455983072a45d4ae8481", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 1, "background": false, "missing_usage": true, "request_id": "5174058beb4841939a4a63381f02bdf6", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-5414", "attempt": 1, "background": false, "missing_usage": true, "request_id": "ba86edf3956744fab533eede7641b059", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-5414", "attempt": 1, "background": false, "missing_usage": true, "request_id": "04969d312eac4a8f90f34b2383e80130", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-5414", "attempt": 1, "background": false, "missing_usage": true, "request_id": "9834b8def76c473980ceb7e0c6d9ea02", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cobol-modernization", "attempt": 1, "background": false, "missing_usage": true, "request_id": "45b93d52e79948849d4fedc0d11fb5b9", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cobol-modernization", "attempt": 1, "background": false, "missing_usage": true, "request_id": "62855c9a16b043f8b377a7297b9641a0", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cobol-modernization", "attempt": 1, "background": false, "missing_usage": true, "request_id": "4a47c78f9b934cf2843a980c17ce19c1", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cobol-modernization", "attempt": 1, "background": false, "missing_usage": true, "request_id": "2b5e32a4122140228319cc63d582385a", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cobol-modernization", "attempt": 1, "background": false, "missing_usage": true, "request_id": "3c4590f122404c3a958ae0854c135e30", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-3677", "attempt": 1, "background": true, "missing_usage": true, "request_id": "3fa169b4b51443afb2903b3570390f69", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "terminal-custom-memory-heap-crash", "attempt": 1, "background": false, "missing_usage": true, "request_id": "bb437909373f4c0880d9eb10cedda179", "http_status": null, "error": "ConnectError", "terminal": null, "stream_error": null}
- {"task": "sphinx-doc__sphinx-11510", "attempt": 1, "background": true, "missing_usage": true, "request_id": "cf3b8407275c46df8d4d898b4332c439", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "terminal-schemelike-metacircular-eval", "attempt": 1, "background": false, "missing_usage": true, "request_id": "b0869b9ad0de496294fb0b0b085e3035", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-schemelike-metacircular-eval", "attempt": 1, "background": false, "missing_usage": true, "request_id": "4ee8e3ee31524caaab2eec7fc1017a49", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-schemelike-metacircular-eval", "attempt": 1, "background": false, "missing_usage": true, "request_id": "3eceefc6226d4d0090acac7ed238a50e", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "terminal-schemelike-metacircular-eval", "attempt": 1, "background": false, "missing_usage": true, "request_id": "56d6717dd8f841168d8a6e0e2312067f", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "terminal-schemelike-metacircular-eval", "attempt": 1, "background": true, "missing_usage": true, "request_id": "3a9a878651ec41fb8a4432c1f5897e27", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "sympy__sympy-17630", "attempt": 1, "background": false, "missing_usage": true, "request_id": "84213913182c4b68bf4cd2eb102a8e76", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sympy__sympy-17630", "attempt": 1, "background": false, "missing_usage": true, "request_id": "99b05e3f341d4869a56ba816b0daa2f7", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "sympy__sympy-17630", "attempt": 1, "background": false, "missing_usage": true, "request_id": "1f5e38efb60b43088a369cd5786cf554", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "bf1477da91af4377a66116dc6688c882", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "b0fee2c3ad1d4d3489b05b53565cb161", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "ef14b260dafb48de924402e2cbd1eb71", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "0e9bb8a6f9c74bbd834dfbdc0a5eefb8", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "16da258fa062437bb729e863eb6c08bb", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "c1853375a0de467aafa4ea1d624f5c9c", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "8b4aedea585940a5ba47c1f48e35c219", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pytest-dev__pytest-10356", "attempt": 1, "background": false, "missing_usage": true, "request_id": "90ddabbcf8a94328ab35d67ddcbd543a", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "psf__requests-2931", "attempt": 1, "background": false, "missing_usage": true, "request_id": "63f442de07374225b4466a49586f0c14", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-2931", "attempt": 1, "background": false, "missing_usage": true, "request_id": "9198a91dfb644ccf8b5540fadf917b3a", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-2931", "attempt": 1, "background": false, "missing_usage": true, "request_id": "adf9e710d9364d4981a57ed56354c6fb", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-2931", "attempt": 1, "background": false, "missing_usage": true, "request_id": "a057df71313341689bf45f2a36900f4c", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-kv-store-grpc", "attempt": 1, "background": false, "missing_usage": true, "request_id": "a782e52415bd457fb8c1ef875e36730e", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-kv-store-grpc", "attempt": 1, "background": false, "missing_usage": true, "request_id": "ab2860be77ea471e9e3575c86a516675", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cancel-async-tasks", "attempt": 2, "background": false, "missing_usage": true, "request_id": "560841f05cf64d30a2e86d1b9b25a9dd", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 2, "background": false, "missing_usage": true, "request_id": "7b684cacbf404c028c5b09331593c57f", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-5414", "attempt": 2, "background": false, "missing_usage": true, "request_id": "2e4680978a4d40bf905147c910e04adc", "http_status": null, "error": "ConnectError", "terminal": null, "stream_error": null}
- {"task": "terminal-custom-memory-heap-crash", "attempt": 2, "background": false, "missing_usage": true, "request_id": "be9152f522684e35a35368d044c792b8", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cancel-async-tasks", "attempt": 3, "background": false, "missing_usage": true, "request_id": "4c2a0eb465d344cf82c533f5ea278449", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "terminal-cancel-async-tasks", "attempt": 3, "background": false, "missing_usage": true, "request_id": "566788edbbd9405094cf3cebd5b6c2cc", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 3, "background": false, "missing_usage": true, "request_id": "78e09a75d57a4bbc9784c05c472b1e55", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 3, "background": false, "missing_usage": true, "request_id": "eb94ade1f69547368dbfa6d45ad42676", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 3, "background": false, "missing_usage": true, "request_id": "9fe0ee0d493c4cfa8b8e78884523d8e0", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}
- {"task": "psf__requests-5414", "attempt": 3, "background": true, "missing_usage": true, "request_id": "299e8e1451d34ad08c4fe843499e2966", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "terminal-custom-memory-heap-crash", "attempt": 3, "background": false, "missing_usage": true, "request_id": "6716d79463434f39b0852cd1ca861621", "http_status": 200, "error": null, "terminal": "response.failed", "stream_error": null}

完整证据与累计请求：`audit-round2.json`。
第二轮质量失败时按协议 revert Stage 3；第一轮失败停止，由用户决定是否回滚。
