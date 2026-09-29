"""
Manual smoke test: print a real system report as formatted JSON.

Run with:
    PYTHONPATH=. python3 scripts/print_report.py
"""

import json

from monitoring.linux_monitor import LinuxMonitor

if __name__ == "__main__":
    monitor = LinuxMonitor(disk_path="/")
    report = monitor.get_full_report()
    print(json.dumps(report.model_dump(mode="json"), indent=2))
