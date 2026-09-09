"""Keep every test run's diagnostic files out of real application history."""

import pytest


@pytest.fixture(autouse=True)
def isolated_run_logs(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPETITIVE_SCORING_RUN_LOG_DIR", str(tmp_path / "run-logs"))
    monkeypatch.setenv("COMPETITIVE_SCORING_DOCUMENT_CACHE_DIR", str(tmp_path / "document-cache"))
