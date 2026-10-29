"""Visualizer: Terminal reporting and ASCII table formatting for simulation results."""

from __future__ import annotations
from smartdialer.simulation.simulator import SimulationReport


class Visualizer:
    @staticmethod
    def print_report(report: SimulationReport) -> None:
        print("\n" + "=" * 95)
        print(f" SIMULATION REPORT: {report.scenario_name.upper()} ({report.mode})")
        print("=" * 95)

        # Header table
        print(
            f"{'Tick':<5} | {'Avail':<5} | {'Conn':<5} | {'Ring':<5} | {'Prop':<5} | "
            f"{'Appr':<5} | {'Safety Action':<20} | {'Ans%':<5} | {'Util%':<6} | {'Notes'}"
        )
        print("-" * 95)

        for s in report.history:
            print(
                f"{s.tick:<5} | "
                f"{s.available_agents:<5} | "
                f"{s.connected_agents:<5} | "
                f"{s.ringing_calls:<5} | "
                f"{s.proposed_dials:<5} | "
                f"{s.approved_dials:<5} | "
                f"{s.safety_action:<20} | "
                f"{s.estimated_answer_rate * 100:>4.0f}% | "
                f"{s.agent_utilization_pct:>5.1f}% | "
                f"{s.notes}"
            )

        print("-" * 95)
        print(" EXECUTIVE SUMMARY METRICS:")
        print(f"  * Total Outbound Calls Initiated : {report.total_calls_initiated}")
        print(f"  * Total Calls Completed          : {report.total_calls_completed}")
        print(f"  * Total Abandoned Calls          : {report.total_abandoned_calls}")
        print(f"  * Compliance Abandonment Rate    : {report.abandonment_rate_pct:.2f}% (Limit: < 3.0%)")
        print(f"  * Average Agent Utilization      : {report.avg_agent_utilization_pct:.1f}%")
        print(f"  * Peak Concurrent Calls          : {report.peak_concurrent_calls}")
        print(f"  * Safety Controller Decisions    : {dict(report.safety_actions_count)}")

        if report.abandonment_rate_pct <= 3.0:
            print("  [PASS] COMPLIANCE STATUS: STRICTLY COMPLIANT (Under 3% abandonment ceiling)")
        else:
            print("  [FAIL] COMPLIANCE STATUS: NON-COMPLIANT")
        print("=" * 95 + "\n")

