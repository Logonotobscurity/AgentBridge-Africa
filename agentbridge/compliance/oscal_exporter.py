"""NIST OSCAL Assessment Results & POA&M generator.

Writes evidence to ``.venturalitica/runs/{run_id}/`` and validates against
the bundled OSCAL JSON v1.2.1 subset schemas.

Failed payment-limit, AML, or budget controls automatically produce a Plan
of Action and Milestones (``poam.oscal.json``) linking findings to tasks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

OSCAL_VERSION = "1.2.1"
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


@dataclass
class Finding:
    control_id: str
    title: str
    success: bool
    rationale: str
    severity: str = "moderate"
    related_task: str | None = None
    uuid: str = ""

    def __post_init__(self) -> None:
        if not self.uuid:
            self.uuid = str(uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_dir(run_id: str, root: Path | None = None) -> Path:
    base = (root or Path.cwd()) / ".venturalitica" / "runs" / run_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def export_oscal_results(
    run_id: str,
    findings: list[Finding] | list[Any],
    *,
    root: Path | None = None,
    extra_props: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Persist schema-validated Assessment Results, and a POA&M when needed."""
    parsed = [_coerce_finding(f) for f in findings]
    stamp = _now()
    result_uuid = str(uuid4())
    metadata = {
        "title": "AgentBridge Assessment Run",
        "last-modified": stamp,
        "version": "1.0.0",
        "oscal-version": OSCAL_VERSION,
        "props": [
            {"name": "run-id", "value": run_id},
            *[{"name": k, "value": str(v)} for k, v in (extra_props or {}).items()],
        ],
    }
    oscal_findings = []
    for f in parsed:
        oscal_findings.append(
            {
                "uuid": f.uuid,
                "title": f.title,
                "description": f.rationale,
                "target": {
                    "type": "objective-id",
                    "target-id": f.control_id,
                    "status": {
                        "state": "satisfied" if f.success else "not-satisfied",
                    },
                },
            }
        )
    assessment_results = {
        "assessment-results": {
            "uuid": str(uuid4()),
            "metadata": metadata,
            "results": [
                {
                    "uuid": result_uuid,
                    "title": f"AgentBridge run {run_id}",
                    "description": "Continuous control monitoring for payment, AML, and budget gates.",
                    "start": stamp,
                    "findings": oscal_findings,
                }
            ],
        }
    }
    directory = _run_dir(run_id, root)
    ar_path = directory / "assessment-results.oscal.json"
    validate_oscal_document(assessment_results, "assessment-results")
    ar_path.write_text(json.dumps(assessment_results, indent=2))

    paths = {"assessment-results": str(ar_path)}
    failed = [f for f in parsed if not f.success]
    if failed:
        poam = _build_poam(run_id, failed, metadata_stamp=stamp)
        validate_oscal_document(poam, "poam")
        poam_path = directory / "poam.oscal.json"
        poam_path.write_text(json.dumps(poam, indent=2))
        paths["poam"] = str(poam_path)
    return paths


def _build_poam(run_id: str, failed: Iterable[Finding], *, metadata_stamp: str) -> dict[str, Any]:
    items = []
    for f in failed:
        items.append(
            {
                "uuid": str(uuid4()),
                "title": f"Remediate {f.control_id}: {f.title}",
                "description": (
                    f.related_task
                    or f"Resolve finding '{f.rationale}' for control {f.control_id}."
                ),
                "props": [
                    {"name": "severity", "value": f.severity},
                    {"name": "control-id", "value": f.control_id},
                    {"name": "run-id", "value": run_id},
                ],
                "related-findings": [{"finding-uuid": f.uuid}],
            }
        )
    return {
        "plan-of-action-and-milestones": {
            "uuid": str(uuid4()),
            "metadata": {
                "title": f"AgentBridge POA&M for run {run_id}",
                "last-modified": metadata_stamp,
                "version": "1.0.0",
                "oscal-version": OSCAL_VERSION,
            },
            "poam-items": items,
        }
    }


def _coerce_finding(raw: Any) -> Finding:
    if isinstance(raw, Finding):
        return raw
    success = bool(getattr(raw, "success", False))
    rationale = str(getattr(raw, "rationale", raw))
    return Finding(
        control_id=str(getattr(raw, "control_id", "AB-GENERIC")),
        title=str(getattr(raw, "title", "Control evaluation")),
        success=success,
        rationale=rationale,
        severity=str(getattr(raw, "severity", "moderate")),
        related_task=getattr(raw, "related_task", None),
    )


def validate_oscal_document(document: dict[str, Any], kind: str) -> None:
    """Validate against bundled subset schemas. Raises ValueError on mismatch."""
    schema_name = {
        "assessment-results": "assessment-results.schema.json",
        "poam": "poam.schema.json",
    }[kind]
    schema_path = SCHEMA_DIR / schema_name
    schema = json.loads(schema_path.read_text())
    try:
        import jsonschema  # type: ignore
    except ImportError:
        _validate_required(document, schema)
        return
    jsonschema.validate(instance=document, schema=schema)


def _validate_required(document: dict[str, Any], schema: dict[str, Any]) -> None:
    """Tiny required-key walker used when jsonschema is not installed."""

    def walk(node: Any, sch: dict[str, Any], path: str) -> None:
        if sch.get("type") == "object" and isinstance(node, dict):
            for key in sch.get("required", []):
                if key not in node:
                    raise ValueError(f"OSCAL schema missing {path}.{key}")
            props = sch.get("properties", {})
            for key, child in node.items():
                if key in props:
                    walk(child, props[key], f"{path}.{key}")
        elif sch.get("type") == "array" and isinstance(node, list) and "items" in sch:
            for i, item in enumerate(node):
                walk(item, sch["items"], f"{path}[{i}]")

    walk(document, schema, "$")
