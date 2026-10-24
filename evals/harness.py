"""Golden trajectory eval harness — no network."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.bridge.nodes import run_trajectory


def main() -> None:
    traj_dir = Path(__file__).parent / "trajectories"
    results = []
    for p in sorted(traj_dir.glob("*.yaml")):
        spec = yaml.safe_load(p.read_text(encoding="utf-8"))
        out = run_trajectory(spec)
        ok = out.get("success") == spec["expected"]["success"]
        results.append({"id": spec["id"], "ok": ok, "out": out})
        print(f"{spec['id']}: {'PASS' if ok else 'FAIL'}")
    out_path = Path(__file__).parent / "results" / "latest.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    failed = [r for r in results if not r["ok"]]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
