"""Guards on ``.github/workflows/*.yml``.

Why this file exists
--------------------
``ci.yml`` carried

    if: ${{ secrets.EVOLUS_API_KEY != '' }}

The ``secrets`` context is not available in step-level ``if:`` expressions, so
GitHub rejected the *whole workflow* during validation: two pushes in a row
produced a run with **zero jobs** and ``conclusion: failure`` on both ``master``
commits. Nothing in the repository noticed, because the failure is not a test
result — the pipeline never started.

A YAML parser alone cannot catch this (the file is valid YAML), so these tests
check the expression-level rules GitHub enforces, and an ``actionlint`` step in
CI performs the full check when the binary is available.
"""

from __future__ import annotations

import os

import pytest
import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKFLOW_DIR = os.path.join(_REPO_ROOT, ".github", "workflows")


def _workflow_files():
    if not os.path.isdir(_WORKFLOW_DIR):
        return []
    names = sorted(
        n for n in os.listdir(_WORKFLOW_DIR) if n.endswith((".yml", ".yaml"))
    )
    return [os.path.join(_WORKFLOW_DIR, n) for n in names]


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _iter_conditions(doc):
    """Yield ``(location, expression)`` for every job-level and step-level if."""
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("if"), str):
            yield f"jobs.{job_id}.if", job["if"]
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("if"), str):
                name = step.get("name") or step.get("uses") or f"#{index}"
                yield f"jobs.{job_id}.steps[{index}] ({name}).if", step["if"]


def test_workflow_dir_exists():
    assert _workflow_files(), "no workflow files found; CI would never run"


@pytest.mark.parametrize("path", _workflow_files(), ids=os.path.basename)
def test_workflow_parses_and_declares_jobs(path):
    doc = _load(path)
    assert isinstance(doc, dict), f"{os.path.basename(path)} is not a mapping"
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, f"{os.path.basename(path)} declares no jobs"
    for job_id, job in jobs.items():
        assert isinstance(job, dict), f"{job_id} is not a mapping"
        assert job.get("runs-on"), f"{job_id} has no runs-on"
        assert job.get("steps"), f"{job_id} has no steps"


@pytest.mark.parametrize("path", _workflow_files(), ids=os.path.basename)
def test_no_secrets_context_inside_if_expressions(path):
    """``secrets`` is unavailable in ``if:``; using it invalidates the workflow.

    Fetch the value into ``env:`` and branch on ``env.NAME`` (step ``if:`` sees
    job/workflow env), or do the presence check inside the script.
    """
    offenders = [
        f"{os.path.basename(path)}: {location}: {expr}"
        for location, expr in _iter_conditions(_load(path))
        if "secrets." in expr
    ]
    assert not offenders, (
        "`secrets` cannot be referenced in an if-expression; GitHub rejects the "
        "entire workflow (run with zero jobs):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", _workflow_files(), ids=os.path.basename)
def test_job_level_if_does_not_use_step_context(path):
    """``steps`` is only available once a job's steps have run."""
    offenders = []
    for job_id, job in (_load(path).get("jobs") or {}).items():
        if isinstance(job, dict) and isinstance(job.get("if"), str):
            if "steps." in job["if"]:
                offenders.append(f"{os.path.basename(path)}: jobs.{job_id}.if")
    assert not offenders, (
        "job-level if cannot read the `steps` context:\n  " + "\n  ".join(offenders)
    )
