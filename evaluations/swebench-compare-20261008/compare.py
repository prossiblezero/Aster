"""One isolated episode per agent and instance; official SWE-bench grades exported patches."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import random
import shlex
import subprocess
import tarfile
import time
from pathlib import Path

import docker

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / "artifacts/private" / HERE.name
DATASET = json.loads((PRIVATE / "dataset.json").read_text())
PYTHON = "/opt/aster-python/bin/python"
CODEX = Path("/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl")
AGENTS = ("aster", "pi", "codex")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def put(container, name, data):
    data = data.encode() if isinstance(data, str) else data
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        info = tarfile.TarInfo(Path(name).name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    container.put_archive(str(Path(name).parent), archive.getvalue())


def checked(container, command, **kwargs):
    result = container.exec_run(command, **kwargs)
    if result.exit_code:
        raise RuntimeError(f"Container setup failed: {result.output.decode(errors='replace')[-2000:]}")
    return result.output


def episode(agent, row, smoke=False):
    job = f"{agent}-{row['instance_id']}" if not smoke else f"smoke-{agent}"
    out = PRIVATE / "runs" / job
    if (out / "result.json").exists():
        return json.loads((out / "result.json").read_text())
    assert not (out / "launch.json").exists(), f"Inspect partial episode first: {job}"
    client = docker.from_env(timeout=1000)
    base_url = f"http://aster-bench-gateway:18080/{job}/v1"
    commands = {
        "django/django": "python tests/runtests.py --settings=test_sqlite --parallel 1 <public test label>",
        "sympy/sympy": "python bin/test -C --verbose <public test path>",
        "sphinx-doc/sphinx": "python -m pytest <public test path>",
        "pydata/xarray": "python -m pytest <public test path>",
    }
    prompt = row["problem_statement"] + (
        "\n\nThe repository is /testbed at the issue's base revision. Project dependencies are preinstalled; "
        "python on PATH is the project's interpreter. Internet access is unavailable. "
        "Inspect the public tests to choose relevant checks. Test command format: " + commands[row["repo"]]
        + ". Implement the issue, verify your changes, and summarize the result."
    )
    if smoke:
        prompt = ("In /testbed, create evaluation_smoke.py defining add(a, b) to return a + b. "
                  "Create test_evaluation_smoke.py using unittest with one assertion that add(2, 3) == 5. "
                  "Run that single test with python -m unittest test_evaluation_smoke, then finish.")
    limit = 180 if smoke else 900
    image = client.images.get(row["image"])
    launch = {"job": job, "agent": agent, "instance_id": row["instance_id"], "smoke": smoke,
              "model": "gpt-5.6-luna", "reasoning_effort": "medium", "seconds_limit": limit,
              "context_tokens": 64000, "output_tokens": 8192, "image_id": image.id,
              "image_digests": image.attrs.get("RepoDigests"), "prompt": prompt,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    save(out / "launch.json", launch)
    volumes = {
        str(PRIVATE / "runtime"): {"bind": "/opt/agent-code", "mode": "ro"},
        "/home/dzh/.conda/envs/aster": {"bind": "/opt/aster-python", "mode": "ro"},
        str(PRIVATE / "pi"): {"bind": "/opt/pi", "mode": "ro"},
        str(PRIVATE / "node-v22.22.0-linux-x64"): {"bind": "/opt/node", "mode": "ro"},
        str(CODEX): {"bind": "/opt/codex-runtime", "mode": "ro"},
    }
    container = client.containers.run(row["image"], "tail -f /dev/null", detach=True,
                                      name="aster-comparison-" + job, network="aster-swebench-20261008",
                                      volumes=volumes, working_dir="/testbed", user="root",
                                      nano_cpus=4_000_000_000, mem_limit="8g", pids_limit=512)
    try:
        # This is a fresh benchmark container; remove repository history before agent access.
        checked(container, ["bash", "-c", "git reset --hard " + shlex.quote(row["base_commit"])
                + " && git clean -fd && rm -rf .git && git init -q && git add ."
                  " && git -c user.name=Benchmark -c user.email=benchmark@localhost commit -qm base"
                  " && mkdir -p /agent-state/pi /root/.codex"])
        checked(container, ["test", "-x", "/opt/miniconda3/envs/testbed/bin/python"])
        put(container, "/agent-state/prompt.txt", prompt)
        put(container, "/agent-state/pi/models.json", json.dumps({"providers": {"benchmark": {
            "baseUrl": base_url, "api": "openai-responses", "apiKey": "benchmark",
            "models": [{"id": "gpt-5.6-luna", "name": "gpt-5.6-luna", "reasoning": True,
                        "input": ["text"], "contextWindow": 64000, "maxTokens": 8192,
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}))
        put(container, "/agent-state/pi/settings.json", json.dumps({"transport": "sse", "cacheWarming": "off"}))
        put(container, "/root/.codex/config.toml", '\n'.join([
            'model = "gpt-5.6-luna"', 'model_provider = "benchmark"',
            'model_reasoning_effort = "medium"', 'model_context_window = 64000',
            'model_auto_compact_token_limit = 54000', 'web_search = "disabled"',
            'approval_policy = "never"', 'sandbox_mode = "danger-full-access"',
            '[features]', 'multi_agent = false',
            '[model_providers.benchmark]', 'name = "benchmark"',
            f'base_url = "{base_url}"', 'env_key = "BENCH_API_KEY"', 'wire_api = "responses"',
        ]) + '\n')
        env = {"PATH": "/opt/miniconda3/envs/testbed/bin:/opt/node/bin:/opt/codex-runtime/codex-path:/usr/local/bin:/usr/bin:/bin",
               "PYTHONPATH": "/testbed", "BENCH_BASE_URL": base_url, "BENCH_API_KEY": "benchmark",
               "PI_CODING_AGENT_DIR": "/agent-state/pi", "PI_OFFLINE": "1",
               "TIKTOKEN_CACHE_DIR": "/opt/agent-code/tiktoken-cache",
               "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "TERM": "dumb"}
        if agent == "aster":
            command = [PYTHON, "/opt/agent-code/aster_episode.py"]
            env["PYTHONPATH"] = "/opt/agent-code"
        elif agent == "pi":
            command = ["/opt/node/bin/node", "/opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js",
                       "--mode", "json", "--provider", "benchmark", "--model", "gpt-5.6-luna", "--thinking", "medium",
                       "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates", "--offline", prompt]
        else:
            command = ["/opt/codex-runtime/bin/codex", "exec", "--json", "--ephemeral", "--ignore-rules",
                       "--dangerously-bypass-approvals-and-sandbox", "-C", "/testbed", prompt]
        argv = ["docker", "exec"]
        for key, value in env.items():
            argv += ["--env", f"{key}={value}"]
        argv += [container.id, "timeout", "--signal=TERM", "--kill-after=10", str(limit), *command]
        started = time.monotonic()
        print(json.dumps({"started": job}), flush=True)
        with (out / "agent.jsonl").open("wb") as output, (out / "stderr.log").open("wb") as error:
            process = subprocess.Popen(argv, stdout=output, stderr=error)
            try:
                code = process.wait(timeout=limit + 30)
            except subprocess.TimeoutExpired:
                container.kill()
                process.kill()
                code = process.wait()
                container.start()
        seconds = round(time.monotonic() - started, 3)
        # Export tracked and new source files, including candidate tests; official grading restores its tests.
        checked(container, ["bash", "-c", "git add -A -- . ':!.aster' ':!.tmp' ':!.pi' ':!.codex'"])
        patch = checked(container, ["git", "diff", "--cached", "--binary", "HEAD"])
        (out / "prediction.patch").write_bytes(patch)
        for name in ("/agent-state", "/testbed/.aster") if agent == "aster" else ("/agent-state",):
            try:
                stream, _ = container.get_archive(name)
                with (out / (Path(name).name + ".tar")).open("wb") as archive:
                    for chunk in stream:
                        archive.write(chunk)
            except docker.errors.NotFound:
                pass
        result = {"job": job, "agent": agent, "instance_id": row["instance_id"], "smoke": smoke,
                  "exit_code": code, "seconds": seconds, "timed_out": code in {124, 137},
                  "patch_sha256": hashlib.sha256(patch).hexdigest(), "patch_bytes": len(patch)}
        if smoke:
            test = container.exec_run(["/opt/miniconda3/envs/testbed/bin/python", "-m", "unittest", "test_evaluation_smoke"], workdir="/testbed")
            result["smoke_test_passed"] = test.exit_code == 0 and code == 0
            if agent == "aster" and result["smoke_test_passed"]:
                state = json.loads(checked(container, ["cat", "/agent-state/aster-result.json"]))
                result["smoke_test_passed"] = state["phase"] == "completed"
            (out / "smoke-test.log").write_bytes(test.output)
        save(out / "result.json", result)
        print(json.dumps(result), flush=True)
        return result
    finally:
        container.remove(force=True)
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("smoke", "run"))
    parser.add_argument("--agent", choices=AGENTS)
    args = parser.parse_args()
    agents = (args.agent,) if args.agent else AGENTS
    if args.mode == "smoke":
        for agent in agents:
            result = episode(agent, DATASET[0], smoke=True)
            assert result["smoke_test_passed"], result
    else:
        for agent in AGENTS:
            readiness = PRIVATE / "runs" / f"smoke-{agent}" / "result.json"
            assert readiness.exists() and json.loads(readiness.read_text())["smoke_test_passed"], f"Smoke check required: {agent}"
        jobs = [(a, r) for r in DATASET for a in agents]
        random.Random(20261008).shuffle(jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(episode, agent, row) for agent, row in jobs]
            for future in concurrent.futures.as_completed(futures):
                future.result()


if __name__ == "__main__":
    main()
