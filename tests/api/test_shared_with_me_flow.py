import pytest


pytestmark = pytest.mark.api_flow


def test_share_appears_in_recipient_shared_list_and_is_downloadable(api_client):
    owner = api_client.register_user("share-owner")
    recipient = api_client.register_user("share-recipient")

    upload_response = api_client.client.post(
        "/api/v1/files/upload",
        headers=owner.auth_headers,
        files={"file": ("shared-flow.txt", b"shared with me flow", "text/plain")},
    )
    assert upload_response.status_code == 201, upload_response.text
    file_metadata = upload_response.json()["file"]
    file_id = file_metadata["id"]
    api_client.created_files.append(file_id)

    lookup_response = api_client.client.get(
        "/api/v1/auth/users/lookup",
        headers=owner.auth_headers,
        params={"email": recipient.email},
    )
    api_client.assert_gateway_route_available(lookup_response, "/api/v1/auth/users/lookup")
    assert lookup_response.status_code == 200, lookup_response.text
    assert lookup_response.json()["id"] == recipient.id

    def shared_file_ids() -> set[str]:
        response = api_client.client.get(
            "/api/v1/files/shared",
            headers=recipient.auth_headers,
            params={"page": 1, "page_size": 50},
        )
        api_client.assert_gateway_route_available(response, "/api/v1/files/shared")
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["files"]}

    assert file_id not in shared_file_ids()

    share_response = api_client.client.post(
        f"/api/v1/files/{file_id}/share",
        headers=owner.auth_headers,
        json={
            "shared_with": lookup_response.json()["id"],
            "permission": "viewer",
            "shared_by": owner.id,
        },
    )
    assert share_response.status_code == 201, share_response.text

    api_client.poll_until(
        shared_file_ids,
        predicate=lambda ids: file_id in ids,
        description="shared file to appear in the recipient's shared list",
    )

    recipient_get_response = api_client.client.get(
        f"/api/v1/files/{file_id}",
        headers=recipient.auth_headers,
    )
    assert recipient_get_response.status_code == 200, recipient_get_response.text
    shared_view = recipient_get_response.json()
    assert shared_view["owner_id"] == owner.id
    assert any(
        share["shared_with"] == recipient.id for share in shared_view.get("shared_with", [])
    ), shared_view

    recipient_download_response = api_client.client.get(
        f"/api/v1/files/{file_id}/download",
        headers=recipient.auth_headers,
    )
    assert recipient_download_response.status_code == 200, recipient_download_response.text
    assert recipient_download_response.json()["url"]

    unshare_response = api_client.client.delete(
        f"/api/v1/files/{file_id}/share/{recipient.id}",
        headers=owner.auth_headers,
    )
    assert unshare_response.status_code == 204, unshare_response.text

    api_client.poll_until(
        shared_file_ids,
        predicate=lambda ids: file_id not in ids,
        description="unshared file to disappear from the recipient's shared list",
    )


def test_trashed_file_is_hidden_from_recipient_until_restored(api_client):
    owner = api_client.register_user("trash-share-owner")
    recipient = api_client.register_user("trash-share-recipient")

    upload_response = api_client.client.post(
        "/api/v1/files/upload",
        headers=owner.auth_headers,
        files={"file": ("trash-shared.txt", b"trash hides shares", "text/plain")},
    )
    assert upload_response.status_code == 201, upload_response.text
    file_id = upload_response.json()["file"]["id"]
    api_client.created_files.append(file_id)

    share_response = api_client.client.post(
        f"/api/v1/files/{file_id}/share",
        headers=owner.auth_headers,
        json={
            "shared_with": recipient.id,
            "permission": "viewer",
            "shared_by": owner.id,
        },
    )
    assert share_response.status_code == 201, share_response.text

    def shared_file_ids() -> set[str]:
        response = api_client.client.get(
            "/api/v1/files/shared",
            headers=recipient.auth_headers,
        )
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["files"]}

    api_client.poll_until(
        shared_file_ids,
        predicate=lambda ids: file_id in ids,
        description="shared file to appear in the recipient's shared list",
    )

    trash_response = api_client.client.post(
        f"/api/v1/files/{file_id}/trash",
        headers=owner.auth_headers,
    )
    assert trash_response.status_code == 200, trash_response.text

    api_client.poll_until(
        shared_file_ids,
        predicate=lambda ids: file_id not in ids,
        description="trashed file to disappear from the recipient's shared list",
    )

    restore_response = api_client.client.post(
        f"/api/v1/files/{file_id}/restore",
        headers=owner.auth_headers,
    )
    assert restore_response.status_code == 200, restore_response.text

    api_client.poll_until(
        shared_file_ids,
        predicate=lambda ids: file_id in ids,
        description="restored file to reappear in the recipient's shared list",
    )
