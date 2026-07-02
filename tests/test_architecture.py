"""Module-boundary contract: web -> (acquire|library) -> (sources|dedupe|tags) -> core.

Runs import-linter as a subprocess so `uv run pytest` is the single gate.
"""
import subprocess


def test_import_contracts():
    result = subprocess.run(
        ["uv", "run", "lint-imports"], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, f"import-linter violations:\n{result.stdout}\n{result.stderr}"
