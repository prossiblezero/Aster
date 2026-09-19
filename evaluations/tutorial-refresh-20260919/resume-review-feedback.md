大部分内容已通过独立语义复核，00–07的原发现已关闭，08/10/11/12/13/14/15的实质问题也已修正。仅处理以下第09章的两处事实问题并重新生成/正式验证网站；不要扩大范围或重查整个仓库。

1. docs/learning/09-checkpoint-resume.md约第92行，把“有效预算”与权限、沙箱一起列为allow_model_change不能改变的设置，是错误的。src/aster/session.py:safety_identity 的model_fields明确包含context_tokens、output_tokens，因此显式模型切换可改变这两个预算，随后要通过新预算的上下文预检。不可变的是该集合之外的安全/能力设置。请核对源码后修正这句话，区分模型上下文/输出预算与任务的steps/turns或安全设置。
2. 同章约35–44行仍把直接安装checkpoint.history称为“真实入口的核心路径”，但Runtime.resume实际先取history，model_changed时canonicalize，再_preview_context、archive，最终安装转换后的history。不要展示跳过转换的路径并称真实源码；最小改法是只保留真实load_checkpoint调用节选，并在正文说明之后的历史转换、预算预检、归档校验和安装步骤；也可展示与源码一致的简化有效Python并明确标识。不要复制大段核心函数。

保留全部现有实验断言、required和tokenizer。你上一轮为当前流程图把站点边数从97同步为98并保留等价比较是允许的。两处修正后，用run_shell单独执行python learn_web/build.py，再用run_tests单独执行python -m pytest -q learn_web/tests/test_site.py。不要将成功run_shell或以前的正式测试当作当前工作区的验证。
