#!/usr/bin/env python3
"""Enforce Zabbix user hardening policy for Varuna shared infra.

Policy:
- Rename bootstrap admin user to operator username (preserves ownership).
- Ensure integration user exists.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, str(int(default)))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _required_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass
class ZabbixClient:
    api_url: str
    timeout_seconds: float
    _request_id: int = 1

    def _next_id(self) -> int:
        current = self._request_id
        self._request_id += 1
        return current

    def call(self, method: str, params: Any, auth: Optional[str] = None) -> Any:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._next_id(),
        }
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

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from Zabbix API during {method}.") from exc

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
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                time.sleep(max(step_seconds, 0.2))
        raise RuntimeError(f"Zabbix API not ready after {wait_seconds}s. last_error={last_error}")

    def login(self, username: str, password: str) -> Optional[str]:
        if not username or not password:
            return None
        for params in (
            {"username": username, "password": password},
            {"user": username, "password": password},
        ):
            try:
                token = self.call("user.login", params)
            except Exception:  # noqa: BLE001
                continue
            if token:
                return str(token)
        return None

    def login_first(self, creds: Iterable[Tuple[str, str]]) -> Tuple[str, str]:
        for username, password in creds:
            token = self.login(username, password)
            if token:
                print(f"login_ok username={username}", flush=True)
                return token, username
        raise RuntimeError("Unable to authenticate in Zabbix API with provided credentials.")


def _get_user(client: ZabbixClient, token: str, username: str) -> Optional[Dict[str, Any]]:
    rows = client.call(
        "user.get",
        {
            "output": ["userid", "username", "roleid"],
            "filter": {"username": [username]},
            "limit": 1,
        },
        auth=token,
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def _get_all_users(client: ZabbixClient, token: str) -> List[Dict[str, Any]]:
    rows = client.call(
        "user.get",
        {"output": ["userid", "username", "roleid"]},
        auth=token,
    )
    return rows if isinstance(rows, list) else []


def _ensure_user(
    client: ZabbixClient,
    token: str,
    *,
    username: str,
    password: str,
    roleid: str,
) -> None:
    existing = _get_user(client, token, username)
    if existing:
        print(f"user_exists username={username}", flush=True)
        return

    payload = {
        "username": username,
        "passwd": password,
        "roleid": str(roleid),
    }
    client.call("user.create", payload, auth=token)
    print(f"user_created username={username}", flush=True)


def main() -> int:
    api_url = str(
        os.getenv("ZABBIX_API_URL", "http://zabbix-web:8080/api_jsonrpc.php")
    ).strip()
    timeout_seconds = float(os.getenv("ZABBIX_API_TIMEOUT_SECONDS", "15"))
    wait_seconds = int(os.getenv("ZABBIX_HARDEN_WAIT_SECONDS", "180"))
    wait_step_seconds = float(os.getenv("ZABBIX_HARDEN_WAIT_STEP_SECONDS", "2"))
    roleid = str(os.getenv("ZABBIX_ENFORCED_ROLE_ID", "3")).strip() or "3"

    bootstrap_username = str(os.getenv("ZABBIX_BOOTSTRAP_USERNAME", "Admin")).strip()
    bootstrap_password = str(os.getenv("ZABBIX_BOOTSTRAP_PASSWORD", "zabbix")).strip()
    operator_username = str(os.getenv("ZABBIX_OPERATOR_USERNAME", "gabriel")).strip()
    operator_password = _required_env("ZABBIX_OPERATOR_PASSWORD")
    varuna_username = str(os.getenv("ZABBIX_VARUNA_USERNAME", "varuna")).strip()
    varuna_password = _required_env("ZABBIX_VARUNA_PASSWORD")

    client = ZabbixClient(api_url=api_url, timeout_seconds=timeout_seconds)
    client.wait_ready(wait_seconds=wait_seconds, step_seconds=wait_step_seconds)

    token, logged_as = client.login_first(
        (
            (operator_username, operator_password),
            (bootstrap_username, bootstrap_password),
        )
    )

    # If we logged in as bootstrap admin, rename it to the operator user.
    if logged_as == bootstrap_username:
        existing_operator = _get_user(client, token, operator_username)
        if existing_operator:
            # Operator already exists separately — delete it so we can rename Admin.
            client.call("user.delete", [str(existing_operator["userid"])], auth=token)
            print(f"user_deleted username={operator_username} userid={existing_operator['userid']} reason=free_username_for_rename", flush=True)

        client.call(
            "user.update",
            {
                "userid": "1",
                "username": operator_username,
                "current_passwd": bootstrap_password,
                "passwd": operator_password,
                "roleid": str(roleid),
            },
            auth=token,
        )
        print(f"admin_renamed from={bootstrap_username} to={operator_username}", flush=True)

        # Re-login as the renamed operator.
        token = client.login(operator_username, operator_password)
        if not token:
            raise RuntimeError(f"Cannot login as {operator_username} after rename.")
        logged_as = operator_username

    # Ensure integration user exists.
    _ensure_user(
        client,
        token,
        username=varuna_username,
        password=varuna_password,
        roleid=roleid,
    )

    users = _get_all_users(client, token)
    print(f"final_users=[{', '.join(u['username'] for u in users)}]", flush=True)

    try:
        client.call("user.logout", [], auth=token)
    except Exception:  # noqa: BLE001
        pass

    print(f"hardening_complete logged_as={logged_as}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"hardening_failed error={exc}", file=sys.stderr, flush=True)
        raise
