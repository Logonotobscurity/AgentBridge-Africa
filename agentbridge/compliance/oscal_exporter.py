"""NIST OSCAL Assessment Results & POA&M generator.

Writes evidence to ``.venturalitica/runs/{run_id}/`` and validates against
the bundled OSCAL JSON v1.2.1 subset schemas.

Failed payment-limit, AML, or budget controls automatically produce a Plan
of Action and Milestones (``poam.oscal.json``) linking findings to tasks.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
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


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _run_dir(run_id: str, root: Path | None = None) -> Path:
    """Return a run-scoped directory without permitting path traversal."""
    if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id must be 1-128 safe URL/path characters")
    runs = ((root or Path.cwd()).resolve() / ".venturalitica" / "runs")
    base = runs / run_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    """Avoid presenting a truncated audit document after interruption."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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
    _write_json_atomic(ar_path, assessment_results)

    paths = {"assessment-results": str(ar_path)}
    poam_path = directory / "poam.oscal.json"
    failed = [f for f in parsed if not f.success]
    if failed:
        poam = _build_poam(run_id, failed, metadata_stamp=stamp)
        validate_oscal_document(poam, "poam")
        _write_json_atomic(poam_path, poam)
        paths["poam"] = str(poam_path)
    else:
        # A run ID may be re-evaluated. Never leave a stale remediation plan
        # beside a newly satisfied assessment.
        poam_path.unlink(missing_ok=True)
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
    if isinstance(raw, Mapping):
        return Finding(
            control_id=str(raw.get("control_id", "AB-GENERIC")),
            title=str(raw.get("title", "Control evaluation")),
            success=_coerce_success(raw.get("success", False)),
            rationale=str(raw.get("rationale", "No rationale supplied")),
            severity=str(raw.get("severity", "moderate")),
            related_task=raw.get("related_task"),
            uuid=str(raw.get("uuid", "")),
        )
    return Finding(
        control_id=str(getattr(raw, "control_id", "AB-GENERIC")),
        title=str(getattr(raw, "title", "Control evaluation")),
        success=_coerce_success(getattr(raw, "success", False)),
        rationale=str(getattr(raw, "rationale", raw)),
        severity=str(getattr(raw, "severity", "moderate")),
        related_task=getattr(raw, "related_task", None),
        uuid=str(getattr(raw, "uuid", "")),
    )


def _coerce_success(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError("finding.success must be a boolean")


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
