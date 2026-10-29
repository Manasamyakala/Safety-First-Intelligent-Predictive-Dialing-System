"""Lightweight zero-dependency Web UI Dashboard for visual demonstration."""

from __future__ import annotations
import asyncio
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

from smartdialer.simulation.scenarios import SCENARIOS
from smartdialer.simulation.simulator import Simulator

LATEST_REPORT = None
RUNNING = False

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SmartDialer Real-Time Simulator Dashboard</title>
    <style>
        :root {
            --bg: #0f172a; --card: #1e293b; --text: #f8fafc;
            --accent: #38bdf8; --green: #22c55e; --yellow: #eab308; --red: #ef4444;
            --border: #334155;
        }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 24px; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
        h1 { margin: 0; font-size: 24px; font-weight: 700; color: var(--accent); }
        .subtitle { color: #94a3b8; font-size: 14px; margin-top: 4px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
        .card-title { font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
        .card-value { font-size: 28px; font-weight: 700; margin-top: 8px; }
        .controls { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 24px; background: var(--card); padding: 16px; border-radius: 8px; border: 1px solid var(--border); }
        button { background: #2563eb; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; transition: background 0.15s; }
        button:hover { background: #1d4ed8; }
        button.danger { background: #dc2626; }
        button.danger:hover { background: #b91c1c; }
        table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 8px; overflow: hidden; border: 1px solid var(--border); font-size: 13px; }
        th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }
        th { background: #182234; color: #94a3b8; font-size: 12px; }
        .badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
        .badge-green { background: rgba(34, 197, 94, 0.2); color: var(--green); }
        .badge-yellow { background: rgba(234, 179, 8, 0.2); color: var(--yellow); }
        .badge-red { background: rgba(239, 68, 68, 0.2); color: var(--red); }
        .badge-blue { background: rgba(56, 189, 248, 0.2); color: var(--accent); }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>SmartDialer Simulation Dashboard</h1>
            <div class="subtitle">Progressive & Predictive Pacing with Unbypassable Safety Controller</div>
        </div>
        <div id="status-badge" class="badge badge-green">Engine Idle</div>
    </div>

    <div class="controls">
        <button onclick="runScenario('A')">Run Scenario A (20% Ans)</button>
        <button onclick="runScenario('B')">Run Scenario B (50% Ans)</button>
        <button onclick="runScenario('C')">Run Scenario C (70% Ans)</button>
        <button onclick="runScenario('D')">Run Scenario D (Volatile)</button>
        <button class="danger" onclick="runScenario('FAIL_WORKER_CRASH')">Inject Worker Crash</button>
        <button class="danger" onclick="runScenario('FAIL_PROVIDER_OUTAGE')">Inject Provider Outage</button>
        <button class="danger" onclick="runScenario('FAIL_AGENT_DROP')">Inject Sudden Agent Drop</button>
    </div>

    <div class="grid">
        <div class="card"><div class="card-title">Total Calls Placed</div><div class="card-value" id="val-placed">-</div></div>
        <div class="card"><div class="card-title">Completed Calls</div><div class="card-value" style="color:var(--green)" id="val-completed">-</div></div>
        <div class="card"><div class="card-title">Abandoned Calls</div><div class="card-value" style="color:var(--red)" id="val-abandoned">-</div></div>
        <div class="card"><div class="card-title">Abandonment Rate</div><div class="card-value" id="val-abandon-rate">-</div></div>
        <div class="card"><div class="card-title">Avg Agent Utilization</div><div class="card-value" style="color:var(--accent)" id="val-utilization">-</div></div>
    </div>

    <h3>Tick Execution Telemetry</h3>
    <table>
        <thead>
            <tr>
                <th>Tick</th>
                <th>Available</th>
                <th>Connected</th>
                <th>Ringing</th>
                <th>Proposed</th>
                <th>Approved</th>
                <th>Safety Action</th>
                <th>Est Answer %</th>
                <th>Agent Util %</th>
                <th>Notes</th>
            </tr>
        </thead>
        <tbody id="telemetry-body">
            <tr><td colspan="10" style="text-align:center; color:#64748b; padding:24px;">Select a scenario above to start simulation.</td></tr>
        </tbody>
    </table>

    <script>
        async function runScenario(key) {
            document.getElementById('status-badge').className = 'badge badge-yellow';
            document.getElementById('status-badge').innerText = 'Simulating ' + key + '...';
            await fetch('/api/run?scenario=' + key);
            poll();
        }

        async function poll() {
            try {
                const res = await fetch('/api/report');
                const data = await res.json();
                if (!data || !data.scenario_name) return;

                document.getElementById('val-placed').innerText = data.total_calls_initiated;
                document.getElementById('val-completed').innerText = data.total_calls_completed;
                document.getElementById('val-abandoned').innerText = data.total_abandoned_calls;
                document.getElementById('val-abandon-rate').innerText = data.abandonment_rate_pct.toFixed(2) + '%';
                document.getElementById('val-utilization').innerText = data.avg_agent_utilization_pct.toFixed(1) + '%';

                document.getElementById('status-badge').className = 'badge badge-green';
                document.getElementById('status-badge').innerText = data.scenario_name + ' Complete';

                const tbody = document.getElementById('telemetry-body');
                tbody.innerHTML = '';
                data.history.forEach(s => {
                    let badgeClass = 'badge-blue';
                    if (s.safety_action === 'REDUCE') badgeClass = 'badge-yellow';
                    if (s.safety_action === 'REJECT') badgeClass = 'badge-red';
                    if (s.safety_action === 'FALLBACK_PROGRESSIVE') badgeClass = 'badge-yellow';

                    const row = `<tr>
                        <td>${s.tick}</td>
                        <td>${s.available_agents}</td>
                        <td>${s.connected_agents}</td>
                        <td>${s.ringing_calls}</td>
                        <td>${s.proposed_dials}</td>
                        <td><strong>${s.approved_dials}</strong></td>
                        <td><span class="badge ${badgeClass}">${s.safety_action}</span></td>
                        <td>${(s.estimated_answer_rate * 100).toFixed(0)}%</td>
                        <td>${s.agent_utilization_pct.toFixed(1)}%</td>
                        <td style="color:#cbd5e1; font-size:11px;">${s.notes || ''}</td>
                    </tr>`;
                    tbody.innerHTML += row;
                });
            } catch (e) {
                console.error(e);
            }
        }
    </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif parsed.path == "/api/run":
            query = urllib.parse.parse_qs(parsed.query)
            scenario_key = query.get("scenario", ["A"])[0]
            threading.Thread(target=self._execute_sim_background, args=(scenario_key,), daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "scenario": scenario_key}).encode("utf-8"))

        elif parsed.path == "/api/report":
            global LATEST_REPORT
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if LATEST_REPORT:
                data = {
                    "scenario_name": LATEST_REPORT.scenario_name,
                    "mode": LATEST_REPORT.mode,
                    "total_calls_initiated": LATEST_REPORT.total_calls_initiated,
                    "total_calls_completed": LATEST_REPORT.total_calls_completed,
                    "total_abandoned_calls": LATEST_REPORT.total_abandoned_calls,
                    "abandonment_rate_pct": LATEST_REPORT.abandonment_rate_pct,
                    "avg_agent_utilization_pct": LATEST_REPORT.avg_agent_utilization_pct,
                    "history": [s.__dict__ for s in LATEST_REPORT.history],
                }
                self.wfile.write(json.dumps(data).encode("utf-8"))
            else:
                self.wfile.write(json.dumps({}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def _execute_sim_background(self, scenario_key: str):
        global LATEST_REPORT, RUNNING
        config = SCENARIOS.get(scenario_key.upper(), SCENARIOS["A"])
        sim = Simulator(config)
        LATEST_REPORT = asyncio.run(sim.run())

    def log_message(self, format, *args):
        # Silence default HTTP server console noise
        pass


def start_dashboard(port: int = 8000):
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"\n SmartDialer Interactive Web Dashboard live at: http://127.0.0.1:{port}")
    print(" Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n Dashboard stopped.")
        server.server_close()
