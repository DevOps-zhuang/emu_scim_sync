import time
from typing import Dict, Optional

import requests


class GitHubScimClient:
    def __init__(self, base_url: str, pat: str, user_agent: str, timeout_seconds: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {pat}",
                "Accept": "application/scim+json",
                "Content-Type": "application/scim+json",
                "User-Agent": user_agent,
            }
        )

    def _request(
        self,
        method: str,
        url: str,
        json_body: Optional[Dict] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        max_retries = 4
        backoff = 1.0
        last_resp: Optional[requests.Response] = None

        for attempt in range(max_retries):
            resp = self.session.request(
                method,
                url,
                json=json_body,
                params=params,
                timeout=self.timeout_seconds,
            )
            last_resp = resp

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            return resp

        if last_resp is None:
            raise RuntimeError("SCIM request failed without response")
        return last_resp

    def _find_first_resource(self, resource_type: str, filter_expression: str) -> Optional[Dict]:
        url = f"{self.base_url}/{resource_type}"
        resp = self._request("GET", url, params={"filter": filter_expression})
        resp.raise_for_status()
        resources = resp.json().get("Resources", [])
        return resources[0] if resources else None

    def find_user_by_external_id(self, external_id: str) -> Optional[Dict]:
        return self._find_first_resource("Users", f'externalId eq "{external_id}"')

    def find_user_by_username(self, username: str) -> Optional[Dict]:
        return self._find_first_resource("Users", f'userName eq "{username}"')

    def find_group_by_external_id(self, external_id: str) -> Optional[Dict]:
        return self._find_first_resource("Groups", f'externalId eq "{external_id}"')

    def create_user(self, payload: Dict) -> Dict:
        url = f"{self.base_url}/Users"
        resp = self._request("POST", url, json_body=payload)
        resp.raise_for_status()
        return resp.json()

    def patch_user(self, scim_user_id: str, operations: list[Dict]) -> Dict:
        url = f"{self.base_url}/Users/{scim_user_id}"
        payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": operations,
        }
        resp = self._request("PATCH", url, json_body=payload)
        resp.raise_for_status()
        if resp.text:
            return resp.json()
        return {}

    def delete_user(self, scim_user_id: str) -> None:
        url = f"{self.base_url}/Users/{scim_user_id}"
        resp = self._request("DELETE", url)
        resp.raise_for_status()

    def create_group(self, payload: Dict) -> Dict:
        url = f"{self.base_url}/Groups"
        resp = self._request("POST", url, json_body=payload)
        resp.raise_for_status()
        return resp.json()

    def patch_group(self, scim_group_id: str, operations: list[Dict]) -> Dict:
        url = f"{self.base_url}/Groups/{scim_group_id}"
        payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": operations,
        }
        resp = self._request("PATCH", url, json_body=payload)
        resp.raise_for_status()
        if resp.text:
            return resp.json()
        return {}

    def delete_group(self, scim_group_id: str) -> None:
        url = f"{self.base_url}/Groups/{scim_group_id}"
        resp = self._request("DELETE", url)
        resp.raise_for_status()
