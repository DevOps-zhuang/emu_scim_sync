import time
from typing import Dict, List, Optional

import requests

from .models import ResolvedGroup, SourceUser


class EntraGraphClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        token_url: str,
        graph_base_url: str,
        timeout_seconds: int = 30,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self.graph_base_url = graph_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._access_token: Optional[str] = None

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://microsoftgraph.chinacloudapi.cn/.default",
            "grant_type": "client_credentials",
        }
        resp = self._request("POST", self.token_url, data=payload)
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError("Failed to get Graph access token")
        self._access_token = token
        return token

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        max_retries = 4
        backoff = 1.0
        last_resp: Optional[requests.Response] = None

        for attempt in range(max_retries):
            resp = requests.request(
                method,
                url,
                headers=self._headers() if method.upper() == "GET" else None,
                params=params,
                data=data,
                timeout=self.timeout_seconds,
            )
            last_resp = resp

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue

            return resp

        if last_resp is None:
            raise RuntimeError("Graph request failed without response")
        return last_resp

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def _get_paginated(self, url: str, params: Optional[Dict[str, str]] = None) -> List[Dict]:
        items: List[Dict] = []
        next_url: Optional[str] = url
        current_params: Optional[Dict[str, str]] = params
        while next_url:
            resp = self._request("GET", next_url, params=current_params)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
            current_params = None
        return items

    @staticmethod
    def _escape_odata_string(value: str) -> str:
        return value.replace("'", "''")

    def resolve_security_groups_by_display_names(self, group_names: List[str]) -> List[ResolvedGroup]:
        resolved_groups: List[ResolvedGroup] = []

        for configured_name in group_names:
            params = {
                "$filter": f"displayName eq '{self._escape_odata_string(configured_name)}' and securityEnabled eq true",
                "$select": "id,displayName",
            }
            raw_groups = self._get_paginated(f"{self.graph_base_url}/groups", params=params)

            if not raw_groups:
                raise ValueError(f"Entra group not found: {configured_name}")

            if len(raw_groups) > 1:
                raise ValueError(f"Entra group name is ambiguous: {configured_name}")

            raw_group = raw_groups[0]
            group_id = raw_group.get("id") or ""
            display_name = raw_group.get("displayName") or configured_name
            if not group_id:
                raise ValueError(f"Entra group resolved without id: {configured_name}")

            resolved_groups.append(
                ResolvedGroup(
                    configured_name=configured_name,
                    id=group_id,
                    display_name=display_name,
                )
            )

        return resolved_groups

    def list_users_in_group(self, group_id: str) -> List[SourceUser]:
        select_fields = "id,userPrincipalName,displayName,mail,department,accountEnabled"
        raw_users = self._get_paginated(
            f"{self.graph_base_url}/groups/{group_id}/members/microsoft.graph.user",
            params={"$select": select_fields},
        )

        users: List[SourceUser] = []
        for item in raw_users:
            user_id = item.get("id") or ""
            upn = item.get("userPrincipalName") or ""
            if not user_id or not upn:
                continue
            users.append(
                SourceUser(
                    id=user_id,
                    user_principal_name=upn,
                    display_name=item.get("displayName") or upn,
                    mail=item.get("mail"),
                    department=item.get("department"),
                    account_enabled=bool(item.get("accountEnabled", True)),
                )
            )
        return users
