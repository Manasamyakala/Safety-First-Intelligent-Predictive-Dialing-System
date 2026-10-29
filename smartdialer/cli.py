"""SmartDialer Command-Line Interface (CLI)."""

from __future__ import annotations
import argparse
import asyncio
import sys

from smartdialer.load_test.runner import run_load_test
from smartdialer.simulation.scenarios import SCENARIOS
from smartdialer.simulation.simulator import Simulator
from smartdialer.simulation.visualizer import Visualizer


async def run_simulation_cmd(scenario_key: str) -> None:
    config = SCENARIOS.get(scenario_key.upper())
    if not config:
        print(f"Error: Unknown scenario '{scenario_key}'.")
        print(f"Available scenarios: {', '.join(SCENARIOS.keys())}")
        sys.exit(1)

    print(f"\nInitializing {config.name}...")
    sim = Simulator(config)
    report = await sim.run()
    Visualizer.print_report(report)


async def run_all_demo() -> None:
    print("\n" + "=" * 90)
    print(" SMARTDIALER COMPREHENSIVE SUITE DEMO")
    print(" Running Core Scenarios (A, B, C, D) followed by Distributed Failure Cases")
    print("=" * 90)

    demo_keys = ["A", "B", "C", "D", "FAIL_WORKER_CRASH", "FAIL_PROVIDER_OUTAGE", "FAIL_AGENT_DROP", "FAIL_CHAOS_EVENTS"]
    for key in demo_keys:
        config = SCENARIOS[key]
        sim = Simulator(config)
        report = await sim.run()
        Visualizer.print_report(report)
        await asyncio.sleep(0.3)


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartDialer CLI: Progressive and Predictive dialer engine.")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # simulate command
    sim_parser = subparsers.add_parser("simulate", help="Run a simulation scenario")
    sim_parser.add_argument(
        "--scenario",
        "-s",
        type=str,
        default="A",
        help=f"Scenario key: {', '.join(SCENARIOS.keys())}",
    )

    # demo command
    subparsers.add_parser("demo", help="Run full end-to-end demo of all scenarios and failure cases")

    # load-test command
    load_parser = subparsers.add_parser("load-test", help="Run high-concurrency multi-worker load test")
    load_parser.add_argument("--workers", "-w", type=int, default=8, help="Number of concurrent workers")
    load_parser.add_argument("--agents", "-a", type=int, default=100, help="Number of agents")
    load_parser.add_argument("--borrowers", "-b", type=int, default=1000, help="Number of borrowers")
    load_parser.add_argument("--iterations", "-i", type=int, default=12, help="Iterations per worker")

    args = parser.parse_args()

    if args.command == "simulate":
        asyncio.run(run_simulation_cmd(args.scenario))
    elif args.command == "demo":
        asyncio.run(run_all_demo())
    elif args.command == "load-test":
        asyncio.run(
            run_load_test(
                num_workers=args.workers,
                num_agents=args.agents,
                num_borrowers=args.borrowers,
                iterations_per_worker=args.iterations,
            )
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
