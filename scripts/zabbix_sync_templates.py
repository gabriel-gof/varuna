#!/usr/bin/env python3
"""Import Zabbix YAML templates from zabbix-templates/ on container startup.

Runs as a one-shot init container alongside zabbix-hardening.
Uses configuration.import with createMissing + updateExisting so templates
are created on first deploy and updated on subsequent deploys.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


TEMPLATES_DIR = Path(os.getenv("ZABBIX_TEMPLATES_DIR", "/opt/varuna/zabbix-templates"))


class ZabbixClient:
    def __init__(self, api_url: str, timeout_seconds: float) -> None:
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self._request_id = 1

    def call(self, method: str, params: Any, auth: Optional[str] = None) -> Any:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._request_id,
        }
        self._request_id += 1
        if auth is not None:
            payload["auth"] = auth

        request = Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json-rpc"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except URLError as exc:
            raise RuntimeError(f"HTTP error during {method}: {exc}") from exc

        parsed = json.loads(body)
        if "error" in parsed:
            raise RuntimeError(f"Zabbix API error in {method}: {parsed['error']}")
        return parsed.get("result")

    def wait_ready(self, wait_seconds: int, step_seconds: float) -> None:
        deadline = time.time() + max(wait_seconds, 1)
        last_error: Optional[str] = None
        while time.time() < deadline:
            try:
                result = self.call("apiinfo.version", [])
                print(f"zabbix_ready version={result}", flush=True)
                return
            except Exception as exc:
                last_error = str(exc)
                time.sleep(max(step_seconds, 0.2))
        raise RuntimeError(f"Zabbix API not ready after {wait_seconds}s. last_error={last_error}")

    def login(self, username: str, password: str) -> str:
        for params in (
            {"username": username, "password": password},
            {"user": username, "password": password},
        ):
            try:
                token = self.call("user.login", params)
                if token:
                    return str(token)
            except Exception:
                continue
        raise RuntimeError(f"Cannot authenticate as {username}")


def import_template(client: ZabbixClient, token: str, yaml_path: Path) -> bool:
    source = yaml_path.read_text(encoding="utf-8")
    try:
        client.call(
            "configuration.import",
            {
                "format": "yaml",
                "source": source,
                "rules": {
                    "templates": {"createMissing": True, "updateExisting": True},
                    "items": {"createMissing": True, "updateExisting": True},
                    "triggers": {"createMissing": True, "updateExisting": True},
                    "discoveryRules": {"createMissing": True, "updateExisting": True},
                    "graphs": {"createMissing": True, "updateExisting": True},
                    "templateDashboards": {"createMissing": True, "updateExisting": True},
                    "valueMaps": {"createMissing": True, "updateExisting": True},
                    "httptests": {"createMissing": True, "updateExisting": True},
                    "template_groups": {"createMissing": True},
                    "templateLinkage": {"createMissing": True},
                },
            },
            auth=token,
        )
        print(f"imported {yaml_path.name}", flush=True)
        return True
    except RuntimeError as exc:
        print(f"FAILED {yaml_path.name}: {exc}", file=sys.stderr, flush=True)
        return False


def main() -> int:
    api_url = os.getenv("ZABBIX_API_URL", "http://zabbix-web:8080/api_jsonrpc.php").strip()
    timeout_seconds = float(os.getenv("ZABBIX_API_TIMEOUT_SECONDS", "30"))
    wait_seconds = int(os.getenv("ZABBIX_TEMPLATE_SYNC_WAIT_SECONDS", "180"))
    wait_step = float(os.getenv("ZABBIX_TEMPLATE_SYNC_WAIT_STEP_SECONDS", "2"))

    username = os.getenv("ZABBIX_OPERATOR_USERNAME", "gabriel").strip()
    password = os.getenv("ZABBIX_OPERATOR_PASSWORD", "").strip()
    if not password:
        print("ZABBIX_OPERATOR_PASSWORD not set, skipping template sync", flush=True)
        return 0

    if not TEMPLATES_DIR.is_dir():
        print(f"templates dir not found: {TEMPLATES_DIR}", file=sys.stderr, flush=True)
        return 1

    yaml_files = sorted(TEMPLATES_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"no .yaml files in {TEMPLATES_DIR}", flush=True)
        return 0

    client = ZabbixClient(api_url=api_url, timeout_seconds=timeout_seconds)
    client.wait_ready(wait_seconds=wait_seconds, step_seconds=wait_step)
    token = client.login(username, password)
    print(f"login_ok username={username}", flush=True)

    ok = 0
    fail = 0
    for path in yaml_files:
        if import_template(client, token, path):
            ok += 1
        else:
            fail += 1

    try:
        client.call("user.logout", [], auth=token)
    except Exception:
        pass

    print(f"template_sync_complete imported={ok} failed={fail}", flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"template_sync_failed error={exc}", file=sys.stderr, flush=True)
        raise
