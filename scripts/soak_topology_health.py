#!/usr/bin/env python3
"""
Topology health soak runner.

Runs periodic checks against Varuna APIs to verify stale/unreachable gray-state
conditions from backend payloads over a long interval (default 2 hours).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request


DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_DURATION_SECONDS = 2 * 60 * 60
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_DETAIL_PROBE_SECONDS = 5 * 60


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def as_positive_seconds(value: Any, fallback: int = 300) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    if parsed <= 0:
        return fallback
    return parsed


def is_status_stale(olt: Dict[str, Any], now: datetime) -> bool:
    last_poll = parse_iso(olt.get("last_poll_at"))
    interval_seconds = as_positive_seconds(olt.get("polling_interval_seconds"), fallback=300)
    stale_after_ms = interval_seconds * 1000
    grace_ms = max(90_000, int(round(stale_after_ms * 0.5)))
    minimum_tolerance_ms = 10 * 60 * 1000
    stale_window_ms = max(stale_after_ms + grace_ms, minimum_tolerance_ms)

    if last_poll is None:
        last_discovery = parse_iso(olt.get("last_discovery_at"))
        if last_discovery is None:
            return False
        return (now - last_discovery).total_seconds() * 1000 > stale_window_ms

    return (now - last_poll).total_seconds() * 1000 > stale_window_ms


def derive_expected_health_state(olt: Dict[str, Any], now: datetime) -> Tuple[str, str]:
    snmp_reachable = olt.get("snmp_reachable")
    failure_count = int(olt.get("snmp_failure_count") or 0)
    if snmp_reachable is None:
        return "neutral", "checking"
    if snmp_reachable is False and failure_count >= 2:
        return "gray", "snmp_unreachable"
    if is_status_stale(olt, now):
        return "gray", "status_stale"
    return "non_gray", "fresh"


def normalize_results(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return [row for row in results if isinstance(row, dict)]
    return []


def sanitize_base_url(url: str) -> str:
    return url.rstrip("/")


def req_json(
    *,
    method: str,
    url: str,
    timeout_seconds: int,
    token: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Any:
    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Token {token}"
    req = request.Request(url=url, data=payload, method=method.upper(), headers=headers)
    with request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def obtain_token(
    *,
    base_url: str,
    username: str,
    password: str,
    timeout_seconds: int,
) -> str:
    data = req_json(
        method="POST",
        url=f"{base_url}/api/auth/login/",
        timeout_seconds=timeout_seconds,
        body={"username": username, "password": password},
    )
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("Login succeeded but no token was returned.")
    return token


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


@dataclass
class OltHealthSnapshot:
    olt_id: str
    name: str
    state: str
    reason: str
    snmp_reachable: Any
    snmp_failure_count: int
    last_poll_at: Optional[str]
    last_discovery_at: Optional[str]
    polling_interval_seconds: int
    stale: bool


class SoakRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_url = sanitize_base_url(args.base_url)
        self.token = (args.token or "").strip() or None
        self.started_at = now_utc()
        self.transitions: List[Dict[str, Any]] = []
        self.anomalies: List[Dict[str, Any]] = []
        self.samples = 0
        self.api_failures = 0
        self.last_state_by_olt: Dict[str, str] = {}
        self.last_reason_by_olt: Dict[str, str] = {}
        self.max_poll_age_seconds: Dict[str, float] = {}
        self.max_discovery_age_seconds: Dict[str, float] = {}
        self.seen_olts: Dict[str, str] = {}
        self.gray_samples_by_reason: Dict[str, int] = {"snmp_unreachable": 0, "status_stale": 0}
        self.detail_probe_interval = max(int(args.detail_probe_seconds), 0)
        self._last_detail_probe_at = 0.0

        ts = self.started_at.strftime("%Y%m%d-%H%M%S")
        run_id = args.run_id or f"soak-{ts}"
        self.output_dir = os.path.abspath(args.output_dir)
        ensure_dir(self.output_dir)
        self.log_path = os.path.join(self.output_dir, f"{run_id}.jsonl")
        self.summary_path = os.path.join(self.output_dir, f"{run_id}.summary.json")

    def _write_event(self, event: Dict[str, Any]) -> None:
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True))
            handle.write("\n")

    def _record_anomaly(self, *, kind: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        entry = {
            "at": utc_iso(now_utc()),
            "kind": kind,
            "message": message,
        }
        if extra:
            entry.update(extra)
        self.anomalies.append(entry)
        self._write_event({"type": "anomaly", **entry})

    def _record_transition(self, olt_id: str, name: str, prev_state: str, prev_reason: str, new_state: str, new_reason: str) -> None:
        entry = {
            "at": utc_iso(now_utc()),
            "olt_id": olt_id,
            "olt_name": name,
            "previous_state": prev_state,
            "previous_reason": prev_reason,
            "new_state": new_state,
            "new_reason": new_reason,
        }
        self.transitions.append(entry)
        self._write_event({"type": "transition", **entry})

    def _validate_required_fields(self, row: Dict[str, Any], olt_id: str, name: str) -> None:
        required = [
            "snmp_reachable",
            "last_snmp_check_at",
            "snmp_failure_count",
            "last_snmp_error",
            "polling_interval_seconds",
            "last_poll_at",
            "last_discovery_at",
        ]
        missing = [field for field in required if field not in row]
        if missing:
            self._record_anomaly(
                kind="missing_fields",
                message=f"OLT {name} ({olt_id}) missing required topology field(s).",
                extra={"olt_id": olt_id, "olt_name": name, "missing_fields": missing},
            )

    def _check_detail_consistency(self, list_rows: List[Dict[str, Any]]) -> None:
        if self.detail_probe_interval <= 0:
            return
        current = time.monotonic()
        if self._last_detail_probe_at and (current - self._last_detail_probe_at) < self.detail_probe_interval:
            return
        self._last_detail_probe_at = current

        for row in list_rows:
            olt_id = str(row.get("id") or "")
            if not olt_id:
                continue
            name = str(row.get("name") or f"OLT-{olt_id}")
            try:
                detail = req_json(
                    method="GET",
                    url=f"{self.base_url}/api/olts/{olt_id}/topology/",
                    timeout_seconds=self.args.timeout_seconds,
                    token=self.token,
                )
            except Exception as exc:
                self._record_anomaly(
                    kind="detail_fetch_error",
                    message=f"Failed to fetch topology detail for OLT {name} ({olt_id}).",
                    extra={"olt_id": olt_id, "olt_name": name, "error": str(exc)},
                )
                continue

            detail_olt = detail.get("olt") if isinstance(detail, dict) else None
            if not isinstance(detail_olt, dict):
                self._record_anomaly(
                    kind="detail_payload_error",
                    message=f"Topology detail payload missing olt block for OLT {name} ({olt_id}).",
                    extra={"olt_id": olt_id, "olt_name": name},
                )
                continue

            keys = ("snmp_reachable", "snmp_failure_count", "last_snmp_check_at", "last_snmp_error")
            mismatches = {}
            for key in keys:
                list_value = row.get(key)
                detail_value = detail_olt.get(key)
                if not self._values_match(key, list_value, detail_value):
                    mismatches[key] = {"list": list_value, "detail": detail_value}
            if mismatches:
                self._record_anomaly(
                    kind="list_detail_mismatch",
                    message=f"List/detail SNMP metadata mismatch for OLT {name} ({olt_id}).",
                    extra={"olt_id": olt_id, "olt_name": name, "mismatches": mismatches},
                )

    @staticmethod
    def _values_match(key: str, list_value: Any, detail_value: Any) -> bool:
        if key == "snmp_failure_count":
            try:
                return int(list_value or 0) == int(detail_value or 0)
            except (TypeError, ValueError):
                return False
        if key == "last_snmp_check_at":
            list_dt = parse_iso(list_value)
            detail_dt = parse_iso(detail_value)
            if list_dt and detail_dt:
                return abs((list_dt - detail_dt).total_seconds()) < 1e-6
        return list_value == detail_value

    def _snapshot_olt(self, row: Dict[str, Any], sample_time: datetime) -> OltHealthSnapshot:
        olt_id = str(row.get("id") or "")
        name = str(row.get("name") or f"OLT-{olt_id}")
        state, reason = derive_expected_health_state(row, sample_time)
        polling_interval = as_positive_seconds(row.get("polling_interval_seconds"), fallback=300)
        stale = is_status_stale(row, sample_time)

        return OltHealthSnapshot(
            olt_id=olt_id,
            name=name,
            state=state,
            reason=reason,
            snmp_reachable=row.get("snmp_reachable"),
            snmp_failure_count=int(row.get("snmp_failure_count") or 0),
            last_poll_at=row.get("last_poll_at"),
            last_discovery_at=row.get("last_discovery_at"),
            polling_interval_seconds=polling_interval,
            stale=stale,
        )

    def _update_age_metrics(self, snap: OltHealthSnapshot, sample_time: datetime) -> None:
        last_poll = parse_iso(snap.last_poll_at)
        if last_poll is not None:
            poll_age = max((sample_time - last_poll).total_seconds(), 0.0)
            prev = self.max_poll_age_seconds.get(snap.olt_id, 0.0)
            self.max_poll_age_seconds[snap.olt_id] = max(prev, poll_age)

        last_discovery = parse_iso(snap.last_discovery_at)
        if last_discovery is not None:
            disc_age = max((sample_time - last_discovery).total_seconds(), 0.0)
            prev = self.max_discovery_age_seconds.get(snap.olt_id, 0.0)
            self.max_discovery_age_seconds[snap.olt_id] = max(prev, disc_age)

    def _process_sample(self, rows: List[Dict[str, Any]], sample_time: datetime) -> None:
        per_sample_state_counts = {"gray": 0, "neutral": 0, "non_gray": 0}

        for row in rows:
            olt_id = str(row.get("id") or "")
            if not olt_id:
                self._record_anomaly(
                    kind="missing_olt_id",
                    message="Topology row is missing OLT id.",
                )
                continue

            name = str(row.get("name") or f"OLT-{olt_id}")
            self.seen_olts[olt_id] = name
            self._validate_required_fields(row, olt_id, name)

            snap = self._snapshot_olt(row, sample_time)
            self._update_age_metrics(snap, sample_time)
            per_sample_state_counts[snap.state] = per_sample_state_counts.get(snap.state, 0) + 1

            if snap.state == "gray":
                self.gray_samples_by_reason[snap.reason] = self.gray_samples_by_reason.get(snap.reason, 0) + 1

            prev_state = self.last_state_by_olt.get(snap.olt_id)
            prev_reason = self.last_reason_by_olt.get(snap.olt_id, "")
            if prev_state is not None and (prev_state != snap.state or prev_reason != snap.reason):
                self._record_transition(
                    snap.olt_id,
                    snap.name,
                    prev_state,
                    prev_reason,
                    snap.state,
                    snap.reason,
                )

            self.last_state_by_olt[snap.olt_id] = snap.state
            self.last_reason_by_olt[snap.olt_id] = snap.reason

        self._write_event(
            {
                "type": "sample",
                "at": utc_iso(sample_time),
                "sample_index": self.samples,
                "olt_count": len(rows),
                "state_counts": per_sample_state_counts,
            }
        )

    def _summary_payload(self) -> Dict[str, Any]:
        ended_at = now_utc()
        elapsed_seconds = max((ended_at - self.started_at).total_seconds(), 0.0)

        return {
            "started_at": utc_iso(self.started_at),
            "ended_at": utc_iso(ended_at),
            "elapsed_seconds": elapsed_seconds,
            "samples": self.samples,
            "api_failures": self.api_failures,
            "seen_olts": {olt_id: self.seen_olts[olt_id] for olt_id in sorted(self.seen_olts)},
            "last_state_by_olt": {olt_id: self.last_state_by_olt[olt_id] for olt_id in sorted(self.last_state_by_olt)},
            "last_reason_by_olt": {olt_id: self.last_reason_by_olt[olt_id] for olt_id in sorted(self.last_reason_by_olt)},
            "max_poll_age_seconds": {olt_id: self.max_poll_age_seconds.get(olt_id, 0.0) for olt_id in sorted(self.seen_olts)},
            "max_discovery_age_seconds": {
                olt_id: self.max_discovery_age_seconds.get(olt_id, 0.0) for olt_id in sorted(self.seen_olts)
            },
            "gray_samples_by_reason": dict(self.gray_samples_by_reason),
            "transition_count": len(self.transitions),
            "anomaly_count": len(self.anomalies),
            "anomalies": self.anomalies,
        }

    def run(self) -> int:
        if self.token is None:
            if not self.args.username or not self.args.password:
                raise RuntimeError("Missing credentials: provide --token or both --username and --password.")
            self.token = obtain_token(
                base_url=self.base_url,
                username=self.args.username,
                password=self.args.password,
                timeout_seconds=self.args.timeout_seconds,
            )

        self._write_event(
            {
                "type": "run_started",
                "at": utc_iso(self.started_at),
                "base_url": self.base_url,
                "duration_seconds": self.args.duration_seconds,
                "interval_seconds": self.args.interval_seconds,
                "detail_probe_seconds": self.detail_probe_interval,
            }
        )

        deadline = time.monotonic() + max(self.args.duration_seconds, 1)
        while True:
            if time.monotonic() >= deadline:
                break

            sample_started = now_utc()
            self.samples += 1
            try:
                payload = req_json(
                    method="GET",
                    url=f"{self.base_url}/api/olts/?include_topology=true",
                    timeout_seconds=self.args.timeout_seconds,
                    token=self.token,
                )
                rows = normalize_results(payload)
                if not rows:
                    self._record_anomaly(
                        kind="empty_topology",
                        message="No OLT rows returned by include_topology endpoint.",
                    )
                self._process_sample(rows, sample_started)
                self._check_detail_consistency(rows)
            except error.HTTPError as exc:
                self.api_failures += 1
                body = ""
                try:
                    body = exc.read().decode("utf-8")
                except Exception:
                    body = ""
                self._record_anomaly(
                    kind="http_error",
                    message=f"HTTP error while polling topology ({exc.code}).",
                    extra={"status_code": exc.code, "body": body[:800]},
                )
            except Exception as exc:
                self.api_failures += 1
                self._record_anomaly(
                    kind="request_error",
                    message="Unhandled error while polling topology.",
                    extra={"error": str(exc)},
                )

            sleep_seconds = max(self.args.interval_seconds, 1)
            time.sleep(sleep_seconds)

        summary = self._summary_payload()
        with open(self.summary_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=True, indent=2)
            handle.write("\n")

        self._write_event(
            {
                "type": "run_finished",
                "at": utc_iso(now_utc()),
                "summary_path": self.summary_path,
                "anomaly_count": summary["anomaly_count"],
                "transition_count": summary["transition_count"],
            }
        )

        if self.args.fail_on_anomaly and summary["anomaly_count"] > 0:
            return 2
        return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a topology stale/gray soak test against Varuna APIs.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL for backend API (default: %(default)s)")
    parser.add_argument("--username", default=os.getenv("VARUNA_SOAK_USER", ""), help="API username for login")
    parser.add_argument("--password", default=os.getenv("VARUNA_SOAK_PASSWORD", ""), help="API password for login")
    parser.add_argument("--token", default=os.getenv("VARUNA_SOAK_TOKEN", ""), help="Pre-issued API token")
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=DEFAULT_DURATION_SECONDS,
        help="Total soak duration in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Polling interval in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--detail-probe-seconds",
        type=int,
        default=DEFAULT_DETAIL_PROBE_SECONDS,
        help="How often to validate /api/olts/{id}/topology/ consistency; 0 disables checks (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/soak",
        help="Directory for run log and summary files (default: %(default)s)",
    )
    parser.add_argument("--run-id", default="", help="Optional run id prefix for output files")
    parser.add_argument(
        "--fail-on-anomaly",
        action="store_true",
        help="Exit with non-zero status when anomalies are detected",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        runner = SoakRunner(args)
        return runner.run()
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Soak run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
