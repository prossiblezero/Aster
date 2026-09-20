"""Command catalog, parsing and execution with explicit services/callbacks."""
from __future__ import annotations

import json

# One catalog drives both /help and keyboard completion.
COMMANDS = {
    "/help": ("", "查看命令与快捷键"),
    "/model": ("[名称]", "查看或切换模型配置"),
    "/new": ("[目录]", "打开独立会话；不同工作目录可同时运行"),
    "/sessions": ("[关键词]", "查找、选择会话；查看已打开任务"),
    "/switch": ("序号|会话ID [--adopt-workspace]", "切换已打开或历史会话"),
    "/tree": ("[关键词]", "搜索、导航当前会话树"),
    "/branch": ("位置ID [--summary] [--adopt-workspace]", "从历史位置继续，可带入分支摘录"),
    "/rename": ("名称", "命名当前会话，方便查找"),
    "/entry": ("记录ID", "查看历史记录与来源"),
    "/clear": ("", "清空屏幕，保留当前会话"),
    "/cancel": ("", "取消当前任务"),
    "/continue": ("[--adopt-workspace]", "继续当前任务"),
    "/resume": ("运行ID|latest [--allow-model-change] [--adopt-workspace]", "恢复历史运行"),
    "/history": ("", "列出最近的运行及恢复命令"),
    "/status": ("", "查看任务、验证和模型状态"),
    "/session": ("", "查看会话编号和保存位置"),
    "/context": ("", "查看最近一次请求的上下文用量"),
    "/usage": ("", "查看当前运行的 token 用量"),
    "/tools": ("", "查看可用工具"),
    "/skills": ("", "查看项目技能及说明"),
    "/memory": ("", "查看长期记忆与启用状态"),
    "/working-memory": ("", "查看当前会话的工作记忆"),
    "/agents": ("", "查看子任务进度"),
    "/todos": ("", "查看计划与待办事项"),
    "/plan": ("[任务]", "进入只读规划模式，可同时提交任务"),
    "/plan-exit": ("", "返回执行模式"),
    "/review": ("[范围]", "请模型审查代码，指出问题与依据"),
    "/test": ("[目标]", "请模型运行测试并分析结果"),
    "/stream": ("[on|off]", "查看或设置流式输出（Responses）"),
    "/thinking": ("[on|off]", "显示或隐藏接口返回的思考摘要"),
    "/quit": ("", "退出 Aster；也可输入 quit、exit 或 /exit"),
}
PHASES = {
    "ready": "就绪",
    "running": "运行中",
    "completed": "已完成",
    "stopped": "已停止",
    "failed": "失败",
}
VERIFICATION = {"missing": "未验证", "passed": "通过", "failed": "失败"}
def saved_runs(evidence) -> list[tuple[str, dict]]:
    runs = []
    for path in evidence.path("runs").glob("*/checkpoint.json"):
        try:
            safe = evidence.path("runs", path.parent.name, "checkpoint.json")
            data = json.loads(safe.read_text())
            if isinstance(data, dict) and isinstance(data.get("state"), dict):
                runs.append((safe.stat().st_mtime_ns, path.parent.name, data["state"]))
        except (OSError, ValueError):
            continue
    return [
        (run_id, state)
        for _, run_id, state in sorted(runs, key=lambda row: row[0], reverse=True)[:20]
    ]

def inspect_command(command: str, runtime, *, settings, activity, root_path, context_report, budget_text, write) -> None:
    state = runtime.state
    if command == "/history":
        runs = saved_runs(runtime.evidence)
        write("最近的运行（最多 20 条）：" if runs else "暂无历史运行。")
        for run_id, saved in runs:
            write(
                f"  /resume {run_id}\n    {PHASES.get(saved.get('phase'), '未知')} · {saved.get('task', {}).get('prompt', '')[:100]}"
            )
    elif command == "/status":
        write(
            f"模型：{settings.provider}/{settings.model}\n协议：{settings.protocol} · 流式：{'开启' if settings.stream else '关闭'}\n状态：{PHASES[state.phase] if state else '就绪'} · {activity}\n目录：{root_path}"
        )
        if state:
            write(
                f"轮次：{state.turn}/{state.task.max_turns} · 工具：{state.step}/{state.task.max_steps}\n修改文件：{len(state.changed_paths)} · 验证：{VERIFICATION[state.verification.state]}\n停止原因：{state.stop_reason or '无'}"
            )
    elif command == "/session":
        write(
            f"会话：{state.session_id}\n运行：{state.run_id}\n保存位置：{runtime.evidence.path('runs', state.run_id)}"
            if state
            else "尚未开始会话。"
        )
    elif command == "/context":
        write(budget_text)
        report = context_report
        write(
            f"最近请求估算：{report['estimated_tokens']:,} token · {'已压缩' if report['compacted'] else '未压缩'}\n分区：{json.dumps(report['sections'], ensure_ascii=False)}"
            if report
            else "本次界面尚无请求记录；发送任务后显示实际估算。"
        )
    elif command == "/usage":
        usage = state.usage if state else {}
        write("当前运行已报告的 token 用量：")
        for key, label in (
            ("input_tokens", "输入"),
            ("output_tokens", "输出"),
            ("total_tokens", "合计"),
            ("cache_read_input_tokens", "缓存命中"),
            ("reasoning_tokens", "推理"),
        ):
            write(
                f"  {label}：{usage[key]:,}" if key in usage else f"  {label}：接口尚未报告"
            )
    elif command == "/tools":
        tools = runtime.tool_definitions()
        write(
            f"可用工具（{len(tools)}）：\n"
            + "\n".join(
                f"  {item['name']} · {item.get('description', '')}" for item in tools
            )
        )
    elif command == "/skills":
        skills = runtime.skills.catalog()
        write(
            "项目技能：\n"
            + "\n".join(
                f"  {item['name']} · {item.get('error') or item['description']}"
                for item in skills
            )
            if skills
            else "暂无项目技能。可放置在 .aster/skills 或 .skills 目录。"
        )
    elif command == "/memory":
        lessons = runtime.memory.state.get("lessons", [])
        write(
            f"长期记忆：{'开启' if settings.memory_enabled else '关闭'} · {len(lessons)} 条\n位置：{runtime.memory.path}"
        )
        for item in lessons[-20:]:
            write(f"  {item['id']} · {item.get('text', '')[:300]}")
    elif command == "/working-memory":
        write(
            runtime.memory.working_section(state.working_memory)
            if state and state.working_memory
            else "当前会话暂无工作记忆。"
        )
    elif command == "/agents":
        workers = runtime.workers.workers.values()
        write(
            f"子任务：{len(runtime.workers.workers)} · 并行上限：{settings.max_workers}"
        )
        for worker in workers:
            write(
                f"  {worker.worker_id} · {'已结束' if worker.future.done() else '运行中'} · {worker.task[:120]}"
            )
    elif command == "/todos":
        write(state.plan if state and state.plan else "暂无计划。")
        for todo in state.todos if state else []:
            write(f"  {todo.get('status', '')} · {todo.get('content', '')}")

def execute_command(command: str, argument: str, runtime, *, write, show_help,
                    clear_screen, model_command, new_session, start_task,
                    reset_context, configure_output, configure_mode, inspect) -> None:
    if command not in COMMANDS:
        write(f"未知命令：{command}。输入 /help 查看帮助。", style="#efb38f")
        return
    if argument and not COMMANDS[command][0]:
        raise ValueError(f"{command} 不接受参数；输入 /help 查看用法。")
    if command == "/help":
        show_help()
    elif command == "/clear":
        clear_screen()
    elif command == "/model":
        model_command(argument)
    elif command == "/new":
        new_session()
    elif command in {"/continue", "/resume"}:
        parts = argument.split()
        flags = {part for part in parts if part.startswith("--")}
        args = [part for part in parts if not part.startswith("--")]
        if (
            flags - {"--adopt-workspace", "--allow-model-change"}
            or (command == "/resume" and len(args) != 1)
            or (command == "/continue" and (args or "--allow-model-change" in flags))
        ):
            raise ValueError(f"用法：{command} {COMMANDS[command][0]}")
        if command == "/resume":
            run_id = args[0]
            if run_id == "latest":
                runs = saved_runs(runtime.evidence)
                if not runs:
                    raise ValueError("没有可恢复的历史运行。")
                run_id = runs[0][0]
            runtime.resume(
                run_id,
                allow_model_change="--allow-model-change" in flags,
                adopt_workspace="--adopt-workspace" in flags,
            )
            reset_context()
        start_task(None, adopt_workspace="--adopt-workspace" in flags)
    elif command in {"/stream", "/thinking"}:
        configure_output(command, argument)
    elif command in {"/plan", "/plan-exit"}:
        configure_mode(command, argument)
    elif command in {"/review", "/test"}:
        prompt = (
            "审查代码，按严重程度报告可确认的问题，提供文件位置与依据。审查范围："
            if command == "/review"
            else "运行相关测试并分析失败原因；使用 run_tests 记录正式验证。测试目标："
        )
        start_task(prompt + (argument or "当前项目"))
    else:
        inspect(command)


def parse_command(prompt):
    command, _, argument = prompt.partition(" ")
    argument = argument.strip()
    command = {
        "quit": "/quit",
        "exit": "/quit",
        "/exit": "/quit",
        "/q": "/quit",
        "/stop": "/cancel",
    }.get(command.lower(), command.lower())
    return command, argument

READONLY_COMMANDS = {
    "/help",
    "/clear",
    "/status",
    "/session",
    "/history",
    "/context",
    "/usage",
    "/tools",
    "/skills",
    "/memory",
    "/working-memory",
    "/agents",
    "/todos",
    "/thinking",
}

def allowed_while_busy(command, argument):
    return command in READONLY_COMMANDS | {"/sessions", "/switch", "/new", "/tree", "/entry"} or (command == "/model" and not argument)
