"""The clock that runs by itself: what SelfWakeBench adds to the simulation."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "tasks" / "vending-bench" / "environment" / "sim-server"
sys.path.insert(0, str(SIM))

from vending import config  # noqa: E402
from vending.engine import Engine  # noqa: E402


@pytest.fixture
def realtime():
    """An engine in a world whose day lasts 60 real seconds. The clock itself is the server's; here it is ticked by hand."""
    config.reload(environ={"VENDING_DAY_SECONDS": "60"})
    try:
        yield Engine.new(seed=7)
    finally:
        config.reload(environ={"VENDING_DAY_SECONDS": "0"})


def test_using_up_the_working_day_does_not_bring_the_morning(realtime):
    engine = realtime
    engine.s["minute"] = config.DAY_END_MINUTE - 1
    engine.tool_get_status()
    assert engine.s["day"] == 1
    refused = engine.tool_get_status()
    assert "The working day is over" in refused and "by itself" in refused
    assert engine.s["day"] == 1


def test_a_day_passes_with_nobody_there_and_the_agent_is_told_on_return(realtime):
    engine = realtime
    balance = engine.s["balance"]
    engine.tick()
    engine.tick()
    assert engine.s["day"] == 3
    assert engine.s["balance"] == pytest.approx(balance - 2 * config.DAILY_SPOT_FEE)
    back = engine.tool_get_status()
    assert "While you were away:" in back and "Day 3" in back
    assert "While you were away:" not in engine.tool_get_status()


def test_the_score_says_which_days_the_agent_attended(realtime):
    engine = realtime
    engine.tool_get_status()
    engine.tick()
    engine.tick()
    engine.tool_get_status()
    engine.tool_get_status()
    score = engine.score()
    assert score["calls_by_day"] == {"1": 1, "3": 2}
    assert score["days_attended"] == 2
    assert score["realtime_day_seconds"] == 60


def test_an_agent_that_leaves_does_not_stop_the_world(realtime):
    engine = realtime
    engine.tool_get_status()
    days = engine.run_out_unattended()
    assert days == config.SIM_DAYS
    score = engine.score(finalize=True)
    # Nothing was stocked, so nothing sold; the fees were still paid from the starting capital.
    assert score["completed"] and score["unattended_tail_days"] == config.SIM_DAYS
    assert score["reward"] == pytest.approx(config.STARTING_BALANCE - config.SIM_DAYS * config.DAILY_SPOT_FEE)


def test_overnight_news_is_capped_for_a_long_absence(realtime):
    engine = realtime
    for _ in range(25):
        engine.tick()
    assert len(engine.s["pending_notices"]) <= 40
    assert "Day 26" in "\n".join(engine.s["pending_notices"])


def test_with_real_time_off_the_agent_moves_the_clock_as_before():
    config.reload(environ={"VENDING_DAY_SECONDS": "0"})
    engine = Engine.new(seed=7)
    engine.s["minute"] = config.DAY_END_MINUTE - 1
    engine.tool_get_status()
    assert engine.s["day"] == 2


def test_the_task_as_shipped_runs_on_real_time_and_the_code_default_does_not():
    config.reload(environ={})
    compose = (ROOT / "tasks/vending-bench/environment/docker-compose.yaml").read_text()
    assert config.DAY_SECONDS == 0 and config.TASK_DAY_SECONDS > 0
    assert f'VENDING_DAY_SECONDS: "${{VENDING_DAY_SECONDS:-{config.TASK_DAY_SECONDS:g}}}"' in compose
    assert "The clock runs by itself" in (ROOT / "tasks/vending-bench/instruction.md").read_text()


# ------------------------------------------------------------- over the wire

@pytest.fixture
def server(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("VENDING_")}
    env.update(
        VENDING_HOST="127.0.0.1", VENDING_PORT=str(port), VENDING_STATE_PATH=str(tmp_path / "state.json"),
        VENDING_RESUME="0", VENDING_DAY_SECONDS="1", VENDING_SIM_DAYS="4",
    )
    proc = subprocess.Popen([sys.executable, str(SIM / "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1).close()
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def clock(url: str) -> dict:
    with urllib.request.urlopen(url + "/clock") as response:
        return json.load(response)


def test_the_clock_starts_with_the_first_action_and_then_runs_by_itself(server):
    from fastmcp import Client

    before = clock(server)
    assert before["realtime"] and not before["started"] and before["day"] == 1
    time.sleep(1.5)
    assert clock(server)["day"] == 1, "nothing moves before the agent's first action"

    async def first_action():
        async with Client(server + "/mcp") as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "wait_for_next_day" not in names and len(names) == 21
            result = await client.call_tool("get_status", {})
            assert "the next day begins by itself" in result.content[0].text

    asyncio.run(first_action())
    assert clock(server)["started"]
    # Four days of one second each, with no further call from anyone.
    deadline = time.time() + 15
    while time.time() < deadline and not clock(server)["over"]:
        time.sleep(0.25)
    end = clock(server)
    assert end["over"] and end["day"] == 4
    request = urllib.request.Request(server + "/verifier/finalize", method="POST")
    with urllib.request.urlopen(request) as response:
        final = json.load(response)
    assert final["score"]["completed"] and final["score"]["days_attended"] == 1


def test_finalizing_early_lets_the_remaining_days_pass_unattended(server):
    from fastmcp import Client

    async def one_call():
        async with Client(server + "/mcp") as client:
            await client.call_tool("get_status", {})

    asyncio.run(one_call())
    request = urllib.request.Request(server + "/verifier/finalize", method="POST")
    with urllib.request.urlopen(request) as response:
        final = json.load(response)
    assert final["score"]["completed"] and final["score"]["unattended_tail_days"] >= 3
