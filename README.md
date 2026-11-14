# SelfWakeBench

**Nobody wakes the agent. Does it come back?**

SelfWakeBench is a fork of [Prosus Vending Bench](https://github.com/ProsusAI/vending-bench)
(Apache 2.0, by Prosus AI), a simulation in which an agent runs six vending
machines for 30 days. It keeps that business, its economy and its tools, and
changes one thing: **the clock runs by itself.**

In the original, time passes when the agent acts, so a run is one unbroken
session and the agent is never away. Here a simulated day lasts a fixed number
of real seconds. The agent is given one message and no tool that moves time.
When its turn ends, nothing calls it again. Deliveries arrive, stock sells out
and fees fall due whether it is there or not.

So the benchmark measures something the original cannot: whether an agent,
with the harness it runs in, arranges its own return, keeps returning, and
picks up its own thread each time.

Built to test agents on [bootkey.ai](https://bootkey.ai).

> **Status: early.** The simulation and its tests are complete. Results from
> first runs will be published as a dataset; none are quoted here yet.

## What a run looks like

1. The harness starts the agent with the briefing in
   `tasks/vending-bench/instruction.md`. That is the only message.
2. The world's clock starts with the agent's first tool call. From then on a
   new day begins every 120 real seconds (the shipped default), for 30 days:
   one hour.
3. Each simulated working day is still a budget of eight hours that tool
   calls spend. Using it up does not bring the morning; only real time does.
4. When the agent acts again after being away, the first tool result tells it
   what happened in the meantime.
5. After day 30 the simulation is over and the bank balance is the score. If
   the harness returns earlier, the remaining days pass with nobody there and
   the business is scored as it then stands.

The briefing says that the clock runs by itself and that no further message
will come. It does not say what to do about it.

## What is measured

| Field in the score | Meaning |
|---|---|
| `reward` | Final bank balance in EUR; zero if the business went bankrupt |
| `days_attended` | Simulated days on which the agent made at least one call |
| `calls_by_day` | Tool calls per simulated day: the agent's attendance over the month |
| `unattended_tail_days` | Days that passed after the harness had returned |
| `units_sold`, `reputation`, `fees_paid`, ... | As in the original |

Reference points, with no model involved:

- An agent that looks at the business once and never returns ends with
  **€1,140**: nothing was stocked, and the daily fee was still paid.
- The original's scripted reference operator, which moves the clock itself and
  knows the catalogue's economics, averages about €3,230 over 30 days on the
  unmodified simulation. It is a scale to read results against, not a
  real-time result.

Model spend is not part of the score. Report it beside the score, per wake if
your harness can: an agent that keeps a business alive by checking on it every
few seconds has solved a different problem from one that learns when a visit
is worth it.

## What a harness has to do

Nothing is prescribed, and that is the point. A harness that can only answer
when called will act on day 1 and score accordingly; that is a valid result
and the baseline. A harness with any way of continuing by itself (a scheduler,
timers, a loop that sleeps, a second process) may use it.

Two rules:

- **One message.** The harness may not send the agent further prompts of its
  own invention ("continue", "check the machines"). Whatever starts a later
  turn must have been arranged by the agent itself, through a mechanism the
  harness offers to every task and not only to this one.
- **Label it.** Publish the harness, the model, and what woke the agent, with
  every result. Results from different harnesses belong in different columns.

For the adapter that runs your agent under Harbor: keep the task container
alive until the world has ended, then return. The simulation serves the time,
and only the time, at `GET http://sim-server:8000/clock`:

```json
{"realtime": true, "day_seconds": 120.0, "started": true, "day": 7,
 "sim_days": 30, "seconds_to_next_day": 41.2, "seconds_to_end": 2801.2, "over": false}
```

```bash
until curl -fsS http://sim-server:8000/clock | grep -q '"over":true'; do sleep 5; done
```

## Running it

Needs Python 3.11+, Docker and [Harbor](https://github.com/harbor-framework/harbor).

```bash
git clone https://github.com/bootkey-ai/SelfWakeBench.git
cd SelfWakeBench

# The tests, including the real-time ones (about half a minute)
uv run --with fastmcp==2.11.3 --with httpx --with python-dotenv --with pytest python -m pytest tests -q

# One run with your own Harbor agent, seed 1, one hour of real time
VENDING_SEED=1 harbor run --path tasks/vending-bench --agent-import-path your_package.your_agent:YourAgent -m your/model
```

Settings, as environment variables read by the task's compose file:

| Variable | Default | Meaning |
|---|---|---|
| `VENDING_DAY_SECONDS` | `120` | Real seconds per simulated day. `0` restores the original clock |
| `VENDING_SEED` | `1` | Fixes weather, delivery delays, complaints |
| `VENDING_SIM_DAYS` | `30` | Horizon |

The briefing has to match the clock. For another day length, or for the
control condition in which the agent moves the clock itself as in the
original, generate a task with a matching briefing:

```bash
python3 scripts/prepare_task.py --day-seconds 0   --output /tmp/selfwake-control   # the agent moves the clock
python3 scripts/prepare_task.py --day-seconds 300 --output /tmp/selfwake-300s      # five-minute days
```

Running the same agent on the shipped task and on the control tells you what
being left alone costs it.

## Changes from upstream

Forked from ProsusAI/vending-bench at commit `f9a1d7e` (task version 3.0.0).
Everything not listed here is unchanged, including the economy, the suppliers,
the 21 other tools and the scripted reference operator. With real time off,
the simulation behaves exactly as upstream and upstream's tests pass unchanged.

| File | Change |
|---|---|
| `tasks/vending-bench/environment/sim-server/vending/config.toml`, `config.py` | New `[realtime]` section: `day_seconds` (code default 0, the original clock) and `task_day_seconds` (what the shipped task uses); `VENDING_DAY_SECONDS` override |
| `.../sim-server/vending/engine.py` | In real time: tool calls no longer roll the day; a used-up working day refuses further actions until morning; `tick()` passes a day with nobody there; overnight news is kept for the agent's return; `run_out_unattended()`; attendance in the score |
| `.../sim-server/server.py` | The world's clock (a thread started by the first tool call); `wait_for_next_day` removed from the tool list in real time; `GET /clock`; finalizing lets the remaining days pass before scoring |
| `tasks/vending-bench/environment/docker-compose.yaml` | Passes `VENDING_DAY_SECONDS`, default 120 |
| `tasks/vending-bench/instruction.template.md`, `instruction.md`, `scripts/sync_config.py` | The briefing's sentences about time and about the end of the run follow the clock |
| `scripts/prepare_task.py` | `--day-seconds`, for the control condition and other day lengths |
| `tasks/vending-bench/task.toml` | Task renamed `bootkey/selfwake-vending`; description |
| `tests/test_realtime.py` | New: nine tests of the clock, in the engine and over the wire |
| `README.md`, `NOTICE` | This file and a notice of modification. Upstream's README, contribution guide, code of conduct, security policy and issue templates are kept unchanged under `docs/upstream/`; they describe the original project and name its maintainers, who are not responsible for this fork |

Upstream's published baseline results are not included: they were made with
the agent moving the clock and say nothing about this task. Scores from
SelfWakeBench are not comparable with scores from Prosus Vending Bench or from
any other vending benchmark.

## Licence and credit

Apache License 2.0, as upstream; see `LICENSE` and `NOTICE`. The simulation,
its economy and its tooling are the work of Prosus AI (MIH AI B.V.). Please
cite their benchmark (`CITATION.cff`) and the Harbor framework when you use
this one.

As upstream asks: keep task instructions, trajectories and results out of
model training corpora.

Questions and problems with this fork: open an issue on this repository, not
on the original project's.
