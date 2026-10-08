# 选题审计

## 目标与规则

本集针对“父任务是否能把真实仓库 issue 拆成互不重复的调查，再整合为一个可验证修复”设计。先从 SWE-bench Verified `test` 的 500 条实例建立候选池，再按问题语义、模块边界、隐藏回归可验证性和运行依赖人工复核。最终固定 20 题：simple 6、medium 10、hard 4（30%/50%/20%）。

- `simple`：一个主要实现组件、一个根因路径，作为委派开销负控制；`expected_workers=0`。
- `medium`：至少两个可独立读代码或复现的单元，并有共享 API、fixture 或集成边界；`expected_workers=2`。
- `hard`：跨包或算法/schema/语法耦合，至少两个独立根因假设，父任务必须统一兼容性和回归检查；`expected_workers` 为 2 或 3。

patch 文件数、失败测试数只用于预检和审计，不单独决定难度。分解单元按 base revision 的模块边界和 issue 描述写出，不能由 gold patch 动态生成。`gold_patch_files` 和测试文件列表留在 grader 元数据中，runner 不得传给模型。

## 固定选择

### Simple controls

- `django__django-13551`：auth password-reset token 与 email 变化；单一 token 模块。
- `django__django-14608`：FormSet non-form error CSS class；单一 formset 渲染路径。
- `django__django-14855`：custom Admin Site 的 readonly ForeignKey URL；单一 admin helper。
- `astropy__astropy-7336`：`quantity_input` 返回类型注解为 `None`；单一 decorator 边界。
- `sphinx-doc__sphinx-7462`：空 tuple 类型注解；解析器与 Python domain 是同一调用链。
- `sympy__sympy-20916`：Greek subscript unicode pprint；单一 conventions 映射。

### Medium coordination candidates

- `django__django-11532`：邮件构造与 Unicode 域名编码。
- `django__django-13121`：表达式类型与 SQLite/MySQL duration SQL。
- `django__django-16256`：related manager 异步 API 与 contenttypes 特化。
- `sphinx-doc__sphinx-10673`：toctree directive、collector、environment adapter。
- `pytest-dev__pytest-8399`：Python fixture discovery 与 unittest `setUpClass`。
- `django__django-15629`：related field collation metadata 与 schema backend。
- `django__django-16263`：annotation reference discovery 与 count-query compilation。
- `sphinx-doc__sphinx-9461`：Python domain property 表示与 autodoc inspection。
- `django__django-13344`：ASGI middleware chain 与内置 middleware response awaitability。
- `sympy__sympy-14248`：LaTeX/pretty/string 三种 printer 的 MatrixSymbol subtraction。

### Hard coordination candidates

- `sympy__sympy-16597`：generated assumptions、核心推理及 power/indexed/printer 下游。
- `astropy__astropy-13398`：ITRS/observed 坐标转换数学、frame 注册和数值回归。
- `pydata__xarray-6992`：Dataset coordinate-name invariant 与 Index registry。
- `sphinx-doc__sphinx-7590`：C-family lexer 与 C/C++ domain user-defined literal grammar。

每个实例的 `decomposition.units`、`evidence_paths` 和 `integration_paths` 是以上审计的机器可读版本。

## 未选候选与原因

- HumanEval/MBPP/LiveCodeBench：单函数或竞赛题，没有真实多模块仓库边界，不能证明 Worker 协同。
- SWE-bench Lite 的大多数题：官方筛选偏向单文件 patch，作为本集主集会削弱协同问题的覆盖。
- `sympy__sympy-13091`：虽然修改文件数多，但主要是同一机械式 rich-comparison 规则，容易把重复搜索误当成协同。
- `pytest__pytest-10051`、`sphinx__sphinx-7748` 等单文件窄回归：留作后续控制池，首版不重复 simple 控制比例。
- `pylint-dev__pylint-4551`：协同结构有价值，但 GPL-2 许可和 pyreverse 环境增加首版分发与复现负担，保留为后续扩展候选。
- Terminal-Bench、SWE-smith、SWT-Bench：依赖 Docker/Harbor 或合成任务环境，先不把环境复杂度混入 Aster Worker 能力。

## 固定后不得改变的事项

模型评测开始后不得根据结果删题、改难度、改变 unit 或选择性重试。solo 与 worker 必须使用相同 base workspace、prompt、模型预算和隐藏 verifier；服务故障、环境失败、取消和真正失败要分别记录。
