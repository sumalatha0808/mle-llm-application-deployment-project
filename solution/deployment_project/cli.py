"""Reference solution commands."""

import argparse
import json

from solution.deployment_project.config import ROOT
from solution.deployment_project.evaluation import (
    load_cases,
    render_report,
    run_evaluation,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect-data", "evaluate"])
    args = parser.parse_args()
    if args.command == "inspect-data":
        load_profile = json.loads(
            (ROOT / "data" / "operations" / "load_profile.json").read_text()
        )
        print(f"evaluation_cases={len(load_cases())}")
        print(f"load_requests={load_profile['requests']}")
        print(f"load_concurrency={load_profile['concurrency']}")
        return

    report = render_report(run_evaluation())
    path = ROOT / "reports" / "generated" / "evaluation.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report)
    print(f"Wrote {path}")
