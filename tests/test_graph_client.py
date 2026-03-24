from src.graph_client import EntraGraphClient


def test_resolve_groups_by_display_names_accepts_distribution_groups(monkeypatch):
    client = EntraGraphClient(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        token_url="https://login.partner.microsoftonline.cn/tenant/oauth2/v2.0/token",
        graph_base_url="https://microsoftgraph.chinacloudapi.cn/v1.0",
    )

    captured = {}

    def fake_get_paginated(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return [{"id": "group-1", "displayName": "研发部全员"}]

    monkeypatch.setattr(client, "_get_paginated", fake_get_paginated)

    resolved = client.resolve_groups_by_display_names(["研发部全员"])

    assert [group.id for group in resolved] == ["group-1"]
    assert captured["url"] == "https://microsoftgraph.chinacloudapi.cn/v1.0/groups"
    assert captured["params"] == {
        "$filter": "displayName eq '研发部全员' and (securityEnabled eq true or mailEnabled eq true)",
        "$select": "id,displayName",
    }


def test_resolve_groups_by_display_names_rejects_ambiguous_matches(monkeypatch):
    client = EntraGraphClient(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        token_url="https://login.partner.microsoftonline.cn/tenant/oauth2/v2.0/token",
        graph_base_url="https://microsoftgraph.chinacloudapi.cn/v1.0",
    )

    monkeypatch.setattr(
        client,
        "_get_paginated",
        lambda url, params=None: [
            {"id": "group-1", "displayName": "研发部全员"},
            {"id": "group-2", "displayName": "研发部全员"},
        ],
    )

    try:
        client.resolve_groups_by_display_names(["研发部全员"])
    except ValueError as error:
        assert str(error) == "Entra group name is ambiguous: 研发部全员"
    else:
        raise AssertionError("Expected ambiguous group name error")