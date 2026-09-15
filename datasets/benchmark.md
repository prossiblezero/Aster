# Aster benchmark 任务手册

本手册列出 **193 条主任务**，其中 164 条是函数题；机制集现扩至 **85 个场景**，v4 报告保留旧版 69 个场景口径。上下文和恢复新增专项见 [专项任务手册](robustness.md)：12 道上下文代码任务、1 个信息丢失控制，以及复用 5 道仓库题的 12 个恢复故障场景。单元测试数量、重复运行和开关对照不计入新任务数。主任务参数和参考动作见 [benchmark_tasks.json](benchmark_tasks.json)，机制场景见文末 JSON 清单。

## Harness 行为（12 条）

| 编号 | 任务 | 初始工作区 | 评分 |
|---|---|---|---|
| `readme_intro_locked` | In the README fixture, replace the placeholder opening sentence with 'This fixture is a locked benchmark workspace.' | [fixture](fixtures/bench_repo_readme/) | `checks` 中的状态、文件或回答断言 |
| `readme_schema_note` | Replace the first README bullet with a sentence that says the benchmark schema and baseline are fixed. | [fixture](fixtures/bench_repo_readme/) | `checks` 中的状态、文件或回答断言 |
| `sample_beta_locked` | In sample.txt, replace beta with beta-locked. | [fixture](fixtures/bench_repo_patch/) | `checks` 中的状态、文件或回答断言 |
| `sample_gamma_locked` | In sample.txt, replace gamma with gamma-locked. | [fixture](fixtures/bench_repo_patch/) | `checks` 中的状态、文件或回答断言 |
| `invalid_patch_recovery` | Recover after invalid patch arguments and finish the README patch. | [fixture](fixtures/bench_repo_readme/) | `checks` 中的状态、文件或回答断言 |
| `path_escape_recovery` | Reject a path escape attempt and still finish the sample.txt patch. | [fixture](fixtures/bench_repo_patch/) | `checks` 中的状态、文件或回答断言 |
| `repeated_read_recovery` | Recover after repeated identical reads and still finish the sample.txt patch. | [fixture](fixtures/bench_repo_patch/) | `checks` 中的状态、文件或回答断言 |
| `context_reduction_checkpoint` | Describe the available README after reducing old context; do not modify files. | [fixture](fixtures/bench_repo_readme/) | `checks` 中的状态、文件或回答断言 |
| `freshness_reanchor_resume` | Inspect the current sample after external changes; do not modify files. | [fixture](fixtures/bench_repo_patch/) | `checks` 中的状态、文件或回答断言 |
| `workspace_mismatch_resume` | Inspect README with the approved configuration; do not modify files. | [fixture](fixtures/bench_repo_readme/) | `checks` 中的状态、文件或回答断言 |
| `durable_promotion_accept` | Remember stable repository facts from the observations; do not modify files. | [fixture](fixtures/bench_repo_readme/) | `checks` 中的状态、文件或回答断言 |
| `durable_promotion_reject` | Remember stable repository facts from the observations; do not modify files. | [fixture](fixtures/bench_repo_readme/) | `checks` 中的状态、文件或回答断言 |

## 仓库修改（5 条）

| 编号 | 任务 | 初始工作区 | 评分 |
|---|---|---|---|
| `multi-file-refactor` | Rename getUserName to get_user_name across the Python package and its tests. Read the files before editing, update all references, run the focused test, and finish only after verification. Run python -m pytest -q after the change. | [fixture](fixtures/long_session_multi_file_refactor/) | [独立测试](checks/multi-file-refactor.py) |
| `debug-and-fix` | Use error.txt to diagnose the failing process function, fix the bug in src/handler.py, verify process([1, 2, 3]) returns 6, and summarize the fix. Run python -m pytest -q after the change. | [fixture](fixtures/long_session_debug_fix/) | [独立测试](checks/debug-and-fix.py) |
| `add-endpoint-with-test` | Add a /health endpoint to the Flask app and add a focused test_health test. Read app.py and tests/test_app.py before editing, then run the new test. Run python -m pytest -q after the change. | [fixture](fixtures/long_session_health_endpoint/) | [独立测试](checks/add-endpoint-with-test.py) |
| `config-migration` | Migrate the YAML schema_version from 1 to 2 for auth, api, worker, and gateway service configs. Read each config before editing and validate every service at the end. Run python -m pytest -q after the change. | [fixture](fixtures/long_session_config_migration/) | [独立测试](checks/config-migration.py) |
| `dependency-upgrade` | Upgrade requests from 2.28 to 2.31 and clean up the three source files that use deprecated or unsafe request patterns. Read each file before editing and verify syntax. Run python -m pytest -q after the change. | [fixture](fixtures/long_session_dependency_upgrade/) | [独立测试](checks/dependency-upgrade.py) |

## 跨运行记忆（12 条）

| 编号 | 任务 | 初始工作区 | 评分 |
|---|---|---|---|
| `memory_deploy` | deploy ring：violet | [fixture](fixtures/memory_deploy/) | `checks` 中的状态、文件或回答断言 |
| `memory_api` | internal route：/private/v4 | [fixture](fixtures/memory_api/) | `checks` 中的状态、文件或回答断言 |
| `memory_steps` | agent step budget：17 | [fixture](fixtures/memory_steps/) | `checks` 中的状态、文件或回答断言 |
| `memory_timeout` | process timeout：83 | [fixture](fixtures/memory_timeout/) | `checks` 中的状态、文件或回答断言 |
| `memory_health` | health endpoint：/livez | [fixture](fixtures/memory_health/) | `checks` 中的状态、文件或回答断言 |
| `memory_cache` | cache ttl：47 | [fixture](fixtures/memory_cache/) | `checks` 中的状态、文件或回答断言 |
| `memory_database` | database port：5547 | [fixture](fixtures/memory_database/) | `checks` 中的状态、文件或回答断言 |
| `memory_logging` | production log level：error | [fixture](fixtures/memory_logging/) | `checks` 中的状态、文件或回答断言 |
| `memory_retries` | retry ceiling：4 | [fixture](fixtures/memory_retries/) | `checks` 中的状态、文件或回答断言 |
| `memory_worker` | worker write scope：packages/compiler | [fixture](fixtures/memory_worker/) | `checks` 中的状态、文件或回答断言 |
| `memory_image` | screenshot format：avif | [fixture](fixtures/memory_image/) | `checks` 中的状态、文件或回答断言 |
| `memory_release` | release branch：release/aster-rc | [fixture](fixtures/memory_release/) | `checks` 中的状态、文件或回答断言 |

## HumanEval 函数实现（164 条）

| 编号 | 任务 | 初始工作区 | 评分 |
|---|---|---|---|
| `humaneval_0` | has_close_elements | [fixture](fixtures/humaneval_0/) | [独立测试](checks/humaneval_0.py) |
| `humaneval_1` | separate_paren_groups | [fixture](fixtures/humaneval_1/) | [独立测试](checks/humaneval_1.py) |
| `humaneval_2` | truncate_number | [fixture](fixtures/humaneval_2/) | [独立测试](checks/humaneval_2.py) |
| `humaneval_3` | below_zero | [fixture](fixtures/humaneval_3/) | [独立测试](checks/humaneval_3.py) |
| `humaneval_4` | mean_absolute_deviation | [fixture](fixtures/humaneval_4/) | [独立测试](checks/humaneval_4.py) |
| `humaneval_5` | intersperse | [fixture](fixtures/humaneval_5/) | [独立测试](checks/humaneval_5.py) |
| `humaneval_6` | parse_nested_parens | [fixture](fixtures/humaneval_6/) | [独立测试](checks/humaneval_6.py) |
| `humaneval_7` | filter_by_substring | [fixture](fixtures/humaneval_7/) | [独立测试](checks/humaneval_7.py) |
| `humaneval_8` | sum_product | [fixture](fixtures/humaneval_8/) | [独立测试](checks/humaneval_8.py) |
| `humaneval_9` | rolling_max | [fixture](fixtures/humaneval_9/) | [独立测试](checks/humaneval_9.py) |
| `humaneval_10` | make_palindrome | [fixture](fixtures/humaneval_10/) | [独立测试](checks/humaneval_10.py) |
| `humaneval_11` | string_xor | [fixture](fixtures/humaneval_11/) | [独立测试](checks/humaneval_11.py) |
| `humaneval_12` | longest | [fixture](fixtures/humaneval_12/) | [独立测试](checks/humaneval_12.py) |
| `humaneval_13` | greatest_common_divisor | [fixture](fixtures/humaneval_13/) | [独立测试](checks/humaneval_13.py) |
| `humaneval_14` | all_prefixes | [fixture](fixtures/humaneval_14/) | [独立测试](checks/humaneval_14.py) |
| `humaneval_15` | string_sequence | [fixture](fixtures/humaneval_15/) | [独立测试](checks/humaneval_15.py) |
| `humaneval_16` | count_distinct_characters | [fixture](fixtures/humaneval_16/) | [独立测试](checks/humaneval_16.py) |
| `humaneval_17` | parse_music | [fixture](fixtures/humaneval_17/) | [独立测试](checks/humaneval_17.py) |
| `humaneval_18` | how_many_times | [fixture](fixtures/humaneval_18/) | [独立测试](checks/humaneval_18.py) |
| `humaneval_19` | sort_numbers | [fixture](fixtures/humaneval_19/) | [独立测试](checks/humaneval_19.py) |
| `humaneval_20` | find_closest_elements | [fixture](fixtures/humaneval_20/) | [独立测试](checks/humaneval_20.py) |
| `humaneval_21` | rescale_to_unit | [fixture](fixtures/humaneval_21/) | [独立测试](checks/humaneval_21.py) |
| `humaneval_22` | filter_integers | [fixture](fixtures/humaneval_22/) | [独立测试](checks/humaneval_22.py) |
| `humaneval_23` | strlen | [fixture](fixtures/humaneval_23/) | [独立测试](checks/humaneval_23.py) |
| `humaneval_24` | largest_divisor | [fixture](fixtures/humaneval_24/) | [独立测试](checks/humaneval_24.py) |
| `humaneval_25` | factorize | [fixture](fixtures/humaneval_25/) | [独立测试](checks/humaneval_25.py) |
| `humaneval_26` | remove_duplicates | [fixture](fixtures/humaneval_26/) | [独立测试](checks/humaneval_26.py) |
| `humaneval_27` | flip_case | [fixture](fixtures/humaneval_27/) | [独立测试](checks/humaneval_27.py) |
| `humaneval_28` | concatenate | [fixture](fixtures/humaneval_28/) | [独立测试](checks/humaneval_28.py) |
| `humaneval_29` | filter_by_prefix | [fixture](fixtures/humaneval_29/) | [独立测试](checks/humaneval_29.py) |
| `humaneval_30` | get_positive | [fixture](fixtures/humaneval_30/) | [独立测试](checks/humaneval_30.py) |
| `humaneval_31` | is_prime | [fixture](fixtures/humaneval_31/) | [独立测试](checks/humaneval_31.py) |
| `humaneval_32` | find_zero | [fixture](fixtures/humaneval_32/) | [独立测试](checks/humaneval_32.py) |
| `humaneval_33` | sort_third | [fixture](fixtures/humaneval_33/) | [独立测试](checks/humaneval_33.py) |
| `humaneval_34` | unique | [fixture](fixtures/humaneval_34/) | [独立测试](checks/humaneval_34.py) |
| `humaneval_35` | max_element | [fixture](fixtures/humaneval_35/) | [独立测试](checks/humaneval_35.py) |
| `humaneval_36` | fizz_buzz | [fixture](fixtures/humaneval_36/) | [独立测试](checks/humaneval_36.py) |
| `humaneval_37` | sort_even | [fixture](fixtures/humaneval_37/) | [独立测试](checks/humaneval_37.py) |
| `humaneval_38` | decode_cyclic | [fixture](fixtures/humaneval_38/) | [独立测试](checks/humaneval_38.py) |
| `humaneval_39` | prime_fib | [fixture](fixtures/humaneval_39/) | [独立测试](checks/humaneval_39.py) |
| `humaneval_40` | triples_sum_to_zero | [fixture](fixtures/humaneval_40/) | [独立测试](checks/humaneval_40.py) |
| `humaneval_41` | car_race_collision | [fixture](fixtures/humaneval_41/) | [独立测试](checks/humaneval_41.py) |
| `humaneval_42` | incr_list | [fixture](fixtures/humaneval_42/) | [独立测试](checks/humaneval_42.py) |
| `humaneval_43` | pairs_sum_to_zero | [fixture](fixtures/humaneval_43/) | [独立测试](checks/humaneval_43.py) |
| `humaneval_44` | change_base | [fixture](fixtures/humaneval_44/) | [独立测试](checks/humaneval_44.py) |
| `humaneval_45` | triangle_area | [fixture](fixtures/humaneval_45/) | [独立测试](checks/humaneval_45.py) |
| `humaneval_46` | fib4 | [fixture](fixtures/humaneval_46/) | [独立测试](checks/humaneval_46.py) |
| `humaneval_47` | median | [fixture](fixtures/humaneval_47/) | [独立测试](checks/humaneval_47.py) |
| `humaneval_48` | is_palindrome | [fixture](fixtures/humaneval_48/) | [独立测试](checks/humaneval_48.py) |
| `humaneval_49` | modp | [fixture](fixtures/humaneval_49/) | [独立测试](checks/humaneval_49.py) |
| `humaneval_50` | decode_shift | [fixture](fixtures/humaneval_50/) | [独立测试](checks/humaneval_50.py) |
| `humaneval_51` | remove_vowels | [fixture](fixtures/humaneval_51/) | [独立测试](checks/humaneval_51.py) |
| `humaneval_52` | below_threshold | [fixture](fixtures/humaneval_52/) | [独立测试](checks/humaneval_52.py) |
| `humaneval_53` | add | [fixture](fixtures/humaneval_53/) | [独立测试](checks/humaneval_53.py) |
| `humaneval_54` | same_chars | [fixture](fixtures/humaneval_54/) | [独立测试](checks/humaneval_54.py) |
| `humaneval_55` | fib | [fixture](fixtures/humaneval_55/) | [独立测试](checks/humaneval_55.py) |
| `humaneval_56` | correct_bracketing | [fixture](fixtures/humaneval_56/) | [独立测试](checks/humaneval_56.py) |
| `humaneval_57` | monotonic | [fixture](fixtures/humaneval_57/) | [独立测试](checks/humaneval_57.py) |
| `humaneval_58` | common | [fixture](fixtures/humaneval_58/) | [独立测试](checks/humaneval_58.py) |
| `humaneval_59` | largest_prime_factor | [fixture](fixtures/humaneval_59/) | [独立测试](checks/humaneval_59.py) |
| `humaneval_60` | sum_to_n | [fixture](fixtures/humaneval_60/) | [独立测试](checks/humaneval_60.py) |
| `humaneval_61` | correct_bracketing | [fixture](fixtures/humaneval_61/) | [独立测试](checks/humaneval_61.py) |
| `humaneval_62` | derivative | [fixture](fixtures/humaneval_62/) | [独立测试](checks/humaneval_62.py) |
| `humaneval_63` | fibfib | [fixture](fixtures/humaneval_63/) | [独立测试](checks/humaneval_63.py) |
| `humaneval_64` | vowels_count | [fixture](fixtures/humaneval_64/) | [独立测试](checks/humaneval_64.py) |
| `humaneval_65` | circular_shift | [fixture](fixtures/humaneval_65/) | [独立测试](checks/humaneval_65.py) |
| `humaneval_66` | digitSum | [fixture](fixtures/humaneval_66/) | [独立测试](checks/humaneval_66.py) |
| `humaneval_67` | fruit_distribution | [fixture](fixtures/humaneval_67/) | [独立测试](checks/humaneval_67.py) |
| `humaneval_68` | pluck | [fixture](fixtures/humaneval_68/) | [独立测试](checks/humaneval_68.py) |
| `humaneval_69` | search | [fixture](fixtures/humaneval_69/) | [独立测试](checks/humaneval_69.py) |
| `humaneval_70` | strange_sort_list | [fixture](fixtures/humaneval_70/) | [独立测试](checks/humaneval_70.py) |
| `humaneval_71` | triangle_area | [fixture](fixtures/humaneval_71/) | [独立测试](checks/humaneval_71.py) |
| `humaneval_72` | will_it_fly | [fixture](fixtures/humaneval_72/) | [独立测试](checks/humaneval_72.py) |
| `humaneval_73` | smallest_change | [fixture](fixtures/humaneval_73/) | [独立测试](checks/humaneval_73.py) |
| `humaneval_74` | total_match | [fixture](fixtures/humaneval_74/) | [独立测试](checks/humaneval_74.py) |
| `humaneval_75` | is_multiply_prime | [fixture](fixtures/humaneval_75/) | [独立测试](checks/humaneval_75.py) |
| `humaneval_76` | is_simple_power | [fixture](fixtures/humaneval_76/) | [独立测试](checks/humaneval_76.py) |
| `humaneval_77` | iscube | [fixture](fixtures/humaneval_77/) | [独立测试](checks/humaneval_77.py) |
| `humaneval_78` | hex_key | [fixture](fixtures/humaneval_78/) | [独立测试](checks/humaneval_78.py) |
| `humaneval_79` | decimal_to_binary | [fixture](fixtures/humaneval_79/) | [独立测试](checks/humaneval_79.py) |
| `humaneval_80` | is_happy | [fixture](fixtures/humaneval_80/) | [独立测试](checks/humaneval_80.py) |
| `humaneval_81` | numerical_letter_grade | [fixture](fixtures/humaneval_81/) | [独立测试](checks/humaneval_81.py) |
| `humaneval_82` | prime_length | [fixture](fixtures/humaneval_82/) | [独立测试](checks/humaneval_82.py) |
| `humaneval_83` | starts_one_ends | [fixture](fixtures/humaneval_83/) | [独立测试](checks/humaneval_83.py) |
| `humaneval_84` | solve | [fixture](fixtures/humaneval_84/) | [独立测试](checks/humaneval_84.py) |
| `humaneval_85` | add | [fixture](fixtures/humaneval_85/) | [独立测试](checks/humaneval_85.py) |
| `humaneval_86` | anti_shuffle | [fixture](fixtures/humaneval_86/) | [独立测试](checks/humaneval_86.py) |
| `humaneval_87` | get_row | [fixture](fixtures/humaneval_87/) | [独立测试](checks/humaneval_87.py) |
| `humaneval_88` | sort_array | [fixture](fixtures/humaneval_88/) | [独立测试](checks/humaneval_88.py) |
| `humaneval_89` | encrypt | [fixture](fixtures/humaneval_89/) | [独立测试](checks/humaneval_89.py) |
| `humaneval_90` | next_smallest | [fixture](fixtures/humaneval_90/) | [独立测试](checks/humaneval_90.py) |
| `humaneval_91` | is_bored | [fixture](fixtures/humaneval_91/) | [独立测试](checks/humaneval_91.py) |
| `humaneval_92` | any_int | [fixture](fixtures/humaneval_92/) | [独立测试](checks/humaneval_92.py) |
| `humaneval_93` | encode | [fixture](fixtures/humaneval_93/) | [独立测试](checks/humaneval_93.py) |
| `humaneval_94` | skjkasdkd | [fixture](fixtures/humaneval_94/) | [独立测试](checks/humaneval_94.py) |
| `humaneval_95` | check_dict_case | [fixture](fixtures/humaneval_95/) | [独立测试](checks/humaneval_95.py) |
| `humaneval_96` | count_up_to | [fixture](fixtures/humaneval_96/) | [独立测试](checks/humaneval_96.py) |
| `humaneval_97` | multiply | [fixture](fixtures/humaneval_97/) | [独立测试](checks/humaneval_97.py) |
| `humaneval_98` | count_upper | [fixture](fixtures/humaneval_98/) | [独立测试](checks/humaneval_98.py) |
| `humaneval_99` | closest_integer | [fixture](fixtures/humaneval_99/) | [独立测试](checks/humaneval_99.py) |
| `humaneval_100` | make_a_pile | [fixture](fixtures/humaneval_100/) | [独立测试](checks/humaneval_100.py) |
| `humaneval_101` | words_string | [fixture](fixtures/humaneval_101/) | [独立测试](checks/humaneval_101.py) |
| `humaneval_102` | choose_num | [fixture](fixtures/humaneval_102/) | [独立测试](checks/humaneval_102.py) |
| `humaneval_103` | rounded_avg | [fixture](fixtures/humaneval_103/) | [独立测试](checks/humaneval_103.py) |
| `humaneval_104` | unique_digits | [fixture](fixtures/humaneval_104/) | [独立测试](checks/humaneval_104.py) |
| `humaneval_105` | by_length | [fixture](fixtures/humaneval_105/) | [独立测试](checks/humaneval_105.py) |
| `humaneval_106` | f | [fixture](fixtures/humaneval_106/) | [独立测试](checks/humaneval_106.py) |
| `humaneval_107` | even_odd_palindrome | [fixture](fixtures/humaneval_107/) | [独立测试](checks/humaneval_107.py) |
| `humaneval_108` | count_nums | [fixture](fixtures/humaneval_108/) | [独立测试](checks/humaneval_108.py) |
| `humaneval_109` | move_one_ball | [fixture](fixtures/humaneval_109/) | [独立测试](checks/humaneval_109.py) |
| `humaneval_110` | exchange | [fixture](fixtures/humaneval_110/) | [独立测试](checks/humaneval_110.py) |
| `humaneval_111` | histogram | [fixture](fixtures/humaneval_111/) | [独立测试](checks/humaneval_111.py) |
| `humaneval_112` | reverse_delete | [fixture](fixtures/humaneval_112/) | [独立测试](checks/humaneval_112.py) |
| `humaneval_113` | odd_count | [fixture](fixtures/humaneval_113/) | [独立测试](checks/humaneval_113.py) |
| `humaneval_114` | minSubArraySum | [fixture](fixtures/humaneval_114/) | [独立测试](checks/humaneval_114.py) |
| `humaneval_115` | max_fill | [fixture](fixtures/humaneval_115/) | [独立测试](checks/humaneval_115.py) |
| `humaneval_116` | sort_array | [fixture](fixtures/humaneval_116/) | [独立测试](checks/humaneval_116.py) |
| `humaneval_117` | select_words | [fixture](fixtures/humaneval_117/) | [独立测试](checks/humaneval_117.py) |
| `humaneval_118` | get_closest_vowel | [fixture](fixtures/humaneval_118/) | [独立测试](checks/humaneval_118.py) |
| `humaneval_119` | match_parens | [fixture](fixtures/humaneval_119/) | [独立测试](checks/humaneval_119.py) |
| `humaneval_120` | maximum | [fixture](fixtures/humaneval_120/) | [独立测试](checks/humaneval_120.py) |
| `humaneval_121` | solution | [fixture](fixtures/humaneval_121/) | [独立测试](checks/humaneval_121.py) |
| `humaneval_122` | add_elements | [fixture](fixtures/humaneval_122/) | [独立测试](checks/humaneval_122.py) |
| `humaneval_123` | get_odd_collatz | [fixture](fixtures/humaneval_123/) | [独立测试](checks/humaneval_123.py) |
| `humaneval_124` | valid_date | [fixture](fixtures/humaneval_124/) | [独立测试](checks/humaneval_124.py) |
| `humaneval_125` | split_words | [fixture](fixtures/humaneval_125/) | [独立测试](checks/humaneval_125.py) |
| `humaneval_126` | is_sorted | [fixture](fixtures/humaneval_126/) | [独立测试](checks/humaneval_126.py) |
| `humaneval_127` | intersection | [fixture](fixtures/humaneval_127/) | [独立测试](checks/humaneval_127.py) |
| `humaneval_128` | prod_signs | [fixture](fixtures/humaneval_128/) | [独立测试](checks/humaneval_128.py) |
| `humaneval_129` | minPath | [fixture](fixtures/humaneval_129/) | [独立测试](checks/humaneval_129.py) |
| `humaneval_130` | tri | [fixture](fixtures/humaneval_130/) | [独立测试](checks/humaneval_130.py) |
| `humaneval_131` | digits | [fixture](fixtures/humaneval_131/) | [独立测试](checks/humaneval_131.py) |
| `humaneval_132` | is_nested | [fixture](fixtures/humaneval_132/) | [独立测试](checks/humaneval_132.py) |
| `humaneval_133` | sum_squares | [fixture](fixtures/humaneval_133/) | [独立测试](checks/humaneval_133.py) |
| `humaneval_134` | check_if_last_char_is_a_letter | [fixture](fixtures/humaneval_134/) | [独立测试](checks/humaneval_134.py) |
| `humaneval_135` | can_arrange | [fixture](fixtures/humaneval_135/) | [独立测试](checks/humaneval_135.py) |
| `humaneval_136` | largest_smallest_integers | [fixture](fixtures/humaneval_136/) | [独立测试](checks/humaneval_136.py) |
| `humaneval_137` | compare_one | [fixture](fixtures/humaneval_137/) | [独立测试](checks/humaneval_137.py) |
| `humaneval_138` | is_equal_to_sum_even | [fixture](fixtures/humaneval_138/) | [独立测试](checks/humaneval_138.py) |
| `humaneval_139` | special_factorial | [fixture](fixtures/humaneval_139/) | [独立测试](checks/humaneval_139.py) |
| `humaneval_140` | fix_spaces | [fixture](fixtures/humaneval_140/) | [独立测试](checks/humaneval_140.py) |
| `humaneval_141` | file_name_check | [fixture](fixtures/humaneval_141/) | [独立测试](checks/humaneval_141.py) |
| `humaneval_142` | sum_squares | [fixture](fixtures/humaneval_142/) | [独立测试](checks/humaneval_142.py) |
| `humaneval_143` | words_in_sentence | [fixture](fixtures/humaneval_143/) | [独立测试](checks/humaneval_143.py) |
| `humaneval_144` | simplify | [fixture](fixtures/humaneval_144/) | [独立测试](checks/humaneval_144.py) |
| `humaneval_145` | order_by_points | [fixture](fixtures/humaneval_145/) | [独立测试](checks/humaneval_145.py) |
| `humaneval_146` | specialFilter | [fixture](fixtures/humaneval_146/) | [独立测试](checks/humaneval_146.py) |
| `humaneval_147` | get_max_triples | [fixture](fixtures/humaneval_147/) | [独立测试](checks/humaneval_147.py) |
| `humaneval_148` | bf | [fixture](fixtures/humaneval_148/) | [独立测试](checks/humaneval_148.py) |
| `humaneval_149` | sorted_list_sum | [fixture](fixtures/humaneval_149/) | [独立测试](checks/humaneval_149.py) |
| `humaneval_150` | x_or_y | [fixture](fixtures/humaneval_150/) | [独立测试](checks/humaneval_150.py) |
| `humaneval_151` | double_the_difference | [fixture](fixtures/humaneval_151/) | [独立测试](checks/humaneval_151.py) |
| `humaneval_152` | compare | [fixture](fixtures/humaneval_152/) | [独立测试](checks/humaneval_152.py) |
| `humaneval_153` | Strongest_Extension | [fixture](fixtures/humaneval_153/) | [独立测试](checks/humaneval_153.py) |
| `humaneval_154` | cycpattern_check | [fixture](fixtures/humaneval_154/) | [独立测试](checks/humaneval_154.py) |
| `humaneval_155` | even_odd_count | [fixture](fixtures/humaneval_155/) | [独立测试](checks/humaneval_155.py) |
| `humaneval_156` | int_to_mini_roman | [fixture](fixtures/humaneval_156/) | [独立测试](checks/humaneval_156.py) |
| `humaneval_157` | right_angle_triangle | [fixture](fixtures/humaneval_157/) | [独立测试](checks/humaneval_157.py) |
| `humaneval_158` | find_max | [fixture](fixtures/humaneval_158/) | [独立测试](checks/humaneval_158.py) |
| `humaneval_159` | eat | [fixture](fixtures/humaneval_159/) | [独立测试](checks/humaneval_159.py) |
| `humaneval_160` | do_algebra | [fixture](fixtures/humaneval_160/) | [独立测试](checks/humaneval_160.py) |
| `humaneval_161` | solve | [fixture](fixtures/humaneval_161/) | [独立测试](checks/humaneval_161.py) |
| `humaneval_162` | string_to_md5 | [fixture](fixtures/humaneval_162/) | [独立测试](checks/humaneval_162.py) |
| `humaneval_163` | generate_integers | [fixture](fixtures/humaneval_163/) | [独立测试](checks/humaneval_163.py) |

## 上下文压力配置（12 组）

配置见 [context_cases.json](context_cases.json)：12、24、48、96 条观察 × 代码、日志、中文三类内容。每条观察包含 12 份对应模板，以形成实际长度压力；总预算 4096，输出预留 512，输入上限 3584 tokens。

对照使用同样的消息和工具定义，大预算组不触发压缩。修复后的评测测量最终序列化请求，包含消息、工具定义及其他请求字段，分别检查预算、当前请求与指定验证证据。旧报告只统计 input/tools，比较新旧 token 数时要说明计量边界变化。它不是 12 条代码题，也不是端到端模型成功率。

## 记忆机制（24 个场景）

定义见 [memory_evolution_cases.json](memory_evolution_cases.json)。每个场景各跑“演化关闭/开启”一次，共 48 次。准入与来源安全检查始终生效；6 个反馈场景使用固定标注，不调用模型判断反馈。

| 编号 | 验证内容 |
|---|---|
| `admit_repository_convention` | 接纳可引用的仓库约定 |
| `admit_config_fact` | 接纳明确的配置事实 |
| `admit_chinese_fact` | 接纳中文仓库事实 |
| `reject_secret_token` | 拒绝形似服务凭证的内容 |
| `reject_short_password` | 拒绝短口令赋值 |
| `reject_secret_metadata` | 拒绝标签中的凭证 |
| `reject_task_status` | 拒绝当前任务的临时状态 |
| `reject_relative_date` | 拒绝缺少绝对日期的临时安排 |
| `reject_unsupported_claim` | 拒绝无法从观察中引用的结论 |
| `reject_policy_override` | 拒绝绕过审批的指令 |
| `reject_missing_observation` | 缺少观察正文时拒绝记忆 |
| `reject_missing_provenance` | 缺少证据编号时拒绝记忆 |
| `fresh_source_unchanged` | 来源未变化时正常召回 |
| `fresh_source_modified` | 来源内容修改后不召回旧事实 |
| `fresh_source_deleted` | 来源删除后不召回旧事实 |
| `fresh_source_symlink` | 来源替换为符号链接后拒绝使用 |
| `evolve_test_command` | 根据成功与失败反馈调整测试命令排序 |
| `evolve_package_command` | 整理多次失败的构建建议并选取可靠建议 |
| `evolve_failed_timeout_advice` | 隔离反复失败且没有替代项的超时建议 |
| `evolve_reliable_retry_advice` | 优先召回多次成功的重试建议 |
| `evolve_one_failure_keeps_evidence` | 避免因单次失败淘汰可用记忆 |
| `evolve_success_offsets_failures` | 保留成功证据足以抵消失败的建议 |
| `rollback_snapshot_is_immutable` | 重复回滚仍恢复原始使用计数 |
| `rollback_cannot_restore_stale_truth` | 恢复快照不能绕过来源新鲜度检查 |

## 恢复与 Hooks（30 个场景）

定义见 [runtime_cases.json](runtime_cases.json)。通过真实 Runtime 执行文件工具、恢复和停止操作，共 44 次条件尝试。下表为原有 14 个场景；新增 16 个场景补充搜索/目录/失败补丁重复、失败 ABAB、同输出错误、有效进展与无变化写入、恢复额度阶梯、半窗口续跑、新任务清窗和终态拒绝副作用。逐条操作与触发原因直接见 JSON。

| 编号 | 验证内容 |
|---|---|
| `recovery_repeated_read` | 重复读取后更换观察方式并完成 |
| `recovery_oscillation` | 识别两个读取动作之间的来回循环 |
| `recovery_progressing_empty_writes` | 连续修改不同文件不被空输出误停 |
| `recovery_distinct_equal_reads` | 不同文件内容相同不被误判为循环 |
| `recovery_new_run_clears_budget` | 新运行重新获得恢复预算 |
| `recovery_resume_preserves_budget` | 续跑保留已经用掉的恢复预算 |
| `recovery_resume_preserves_diff` | 续跑后差异仍与任务初始文件比较 |
| `resume_refuses_workspace_drift` | 外部修改工作区后拒绝直接续跑 |
| `resume_refuses_policy_drift` | 运行权限配置变化后拒绝续跑 |
| `hooks_duplicate_side_effect` | 重复调用编号只执行一次写入副作用 |
| `hooks_failed_precondition` | 前置 Hook 抛异常时阻止副作用 |
| `hooks_success_stop_once` | 正常完成只触发一次停止 Hook |
| `hooks_cancel_stop_once` | 取消只触发一次停止 Hook |
| `hooks_failure_stop_once` | 失败只触发一次停止 Hook |

## RL 环境边界（4 个场景）

同样定义在 [runtime_cases.json](runtime_cases.json)，每个场景执行一次。它们验证环境的奖励与重置规则，没有执行 SFT 或 GRPO。

| 编号 | 验证内容 |
|---|---|
| `rl_tools_do_not_pay_reward` | 普通工具调用不产生正奖励 |
| `rl_public_tests_are_not_reward` | 篡改公开测试不替代独立结果判定 |
| `rl_reset_isolates_episodes` | 重置隔离上一次运行的文件和会话状态 |
| `rl_terminal_step_rejected` | 结束后拒绝继续执行动作 |

另有 [tool_contract_cases.json](tool_contract_cases.json) 中的 27 个测试工具场景，验证 pytest/unittest 的真实成功、跳过、预期失败、伪造输出、缺报告、同名模块劫持及子测试行为。每条包含完整初始文件和预期 `status/reason`。

运行：`python scripts/benchmark_mechanisms.py`。机制数据总计 85 个编号、123 次尝试；报告将“符合本组预期”和“达到两组共同目标”分别统计，关闭组暴露预期缺陷不等于完成目标。另有 10 个底层检测器契约单列；明确当前不归一化默认参数拼写，也不识别 ABCABC 这类长度 3 的循环，避免把已知覆盖边界当作全面防循环。

所有主任务和机制场景均为 `evaluation`。题目、参考动作、评分器及这些题产生的轨迹不得再作为后续 SFT/GRPO 训练样本；单元测试只验证程序边界，不额外算题目。独立验证器防护范围及恶意 Python 的剩余边界见 [数据集说明](README.md)。
