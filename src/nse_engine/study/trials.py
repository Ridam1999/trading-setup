"""Append-only SQLite registry for event-study trials and holdout access."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nse_engine.setups.base import SetupConfig


class TrialRegistry:
    """Append-only SQLite store for every executed experiment."""

    def __init__(self, path: Path) -> None:
        """Create the registry schema and immutable-row triggers."""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS trials (
                    trial_id TEXT PRIMARY KEY,
                    timestamp_utc TEXT NOT NULL,
                    setup_name TEXT NOT NULL,
                    params_hash TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    universe TEXT NOT NULL,
                    split TEXT NOT NULL,
                    horizon INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    git_commit TEXT
                )"""
            )
            connection.execute(
                "CREATE TRIGGER IF NOT EXISTS trials_no_update BEFORE UPDATE ON trials "
                "BEGIN SELECT RAISE(ABORT, 'trial registry is append-only'); END"
            )
            connection.execute(
                "CREATE TRIGGER IF NOT EXISTS trials_no_delete BEFORE DELETE ON trials "
                "BEGIN SELECT RAISE(ABORT, 'trial registry is append-only'); END"
            )

    def append_trial(
        self,
        setup: SetupConfig,
        split: str,
        horizon: int,
        metrics: dict[str, Any],
    ) -> str:
        """Append one trial row with a stable parameter hash and git commit."""
        params = setup.model_dump(mode="json")
        serialized_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
        params_hash = hashlib.sha256(serialized_params.encode("utf-8")).hexdigest()
        trial_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO trials (
                    trial_id, timestamp_utc, setup_name, params_hash, params_json,
                    universe, split, horizon, metrics_json, git_commit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trial_id,
                    datetime.now(UTC).isoformat(),
                    setup.name,
                    params_hash,
                    serialized_params,
                    setup.universe,
                    split,
                    horizon,
                    json.dumps(metrics, sort_keys=True, allow_nan=True),
                    self._git_commit(),
                ),
            )
        return trial_id

    def record_holdout_unlock(self) -> str:
        """Append an audit row when the caller explicitly unlocks holdout access."""
        trial_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO trials (
                    trial_id, timestamp_utc, setup_name, params_hash, params_json,
                    universe, split, horizon, metrics_json, git_commit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trial_id,
                    datetime.now(UTC).isoformat(),
                    "__holdout_unlock__",
                    hashlib.sha256(b"holdout-unlock").hexdigest(),
                    "{}",
                    "all",
                    "holdout",
                    0,
                    json.dumps({"event": "holdout_unlocked"}),
                    self._git_commit(),
                ),
            )
        return trial_id

    def rows(self, include_unlocks: bool = False) -> list[dict[str, Any]]:
        """Return all trial records in insertion order."""
        query = "SELECT * FROM trials"
        if not include_unlocks:
            query += " WHERE setup_name != '__holdout_unlock__'"
        query += " ORDER BY rowid"
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query).fetchall()]

    def trial_count(self) -> int:
        """Count statistical experiment records, excluding holdout-access audits."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT count(*) FROM trials WHERE setup_name != '__holdout_unlock__'"
            ).fetchone()
        return int(row[0]) if row else 0

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with foreign keys enabled."""
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _git_commit() -> str | None:
        """Read the current commit id without shell evaluation."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip()
