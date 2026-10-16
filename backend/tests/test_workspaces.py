import io
import uuid

from PIL import Image
from sqlalchemy import select

from app.models import Membership, Workspace


def test_workspace_requires_authentication(client):
    assert client.get(f"/api/workspaces/{uuid.uuid4()}").status_code == 401


def test_workspace_setup_validates_country_and_name(client, signed_in, csrf):
    path = f"/api/workspaces/{signed_in['workspace']['id']}/setup"
    assert client.post(path, data={"name": " ", "country_code": "KE"}, headers=csrf).status_code == 422
    assert client.post(path, data={"name": "Workspace", "country_code": "ZZ"}, headers=csrf).status_code == 422
    assert client.post(path, data={"name": "Workspace", "country_code": "KE", "website": "javascript:alert(1)"}, headers=csrf).status_code == 422


def test_workspace_setup_without_optional_fields(client, signed_in, csrf):
    path = f"/api/workspaces/{signed_in['workspace']['id']}/setup"
    response = client.post(path, data={"name": "Petrix Analytics", "country_code": "KE", "industry": "Technology"}, headers=csrf)
    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["setup_completed"] is True
    assert workspace["website"] is None and workspace["avatar_url"] is None
    assert client.get("/api/auth/me").json()["next_path"] == "/app/agents"
    repeat = client.post(path, data={"name": "Petrix Analytics", "country_code": "KE"}, headers=csrf)
    assert repeat.status_code == 200
    assert repeat.json()["workspace"]["id"] == workspace["id"]


def test_setup_requires_csrf(client, signed_in):
    response = client.post(f"/api/workspaces/{signed_in['workspace']['id']}/setup", data={"name": "Workspace", "country_code": "KE"})
    assert response.status_code == 403


def test_cannot_access_another_workspace(client, app, signed_in, csrf):
    with app.state.session_factory() as db:
        workspace = Workspace(name="Someone else's workspace")
        db.add(workspace); db.commit(); other_id = workspace.id
    assert client.get(f"/api/workspaces/{other_id}").status_code == 404
    assert client.post(f"/api/workspaces/{other_id}/setup", data={"name": "Changed", "country_code": "KE"}, headers=csrf).status_code == 404


def test_member_cannot_complete_owner_setup(client, app, signed_in, csrf):
    with app.state.session_factory() as db:
        membership = db.scalar(select(Membership))
        membership.role = "member"; db.commit()
    response = client.post(f"/api/workspaces/{signed_in['workspace']['id']}/setup", data={"name": "Changed", "country_code": "KE"}, headers=csrf)
    assert response.status_code == 403


def test_avatar_validated_and_reencoded(client, signed_in, csrf):
    path = f"/api/workspaces/{signed_in['workspace']['id']}/setup"
    data = {"name": "Workspace", "country_code": "KE"}
    assert client.post(path, data=data, files={"avatar": ("bad.png", b"<svg></svg>", "image/png")}, headers=csrf).status_code == 422
    output = io.BytesIO(); Image.new("RGB", (600, 300), "orange").save(output, format="JPEG")
    response = client.post(path, data=data, files={"avatar": ("avatar.jpg", output.getvalue(), "image/jpeg")}, headers=csrf)
    assert response.status_code == 200
    avatar = client.get(response.json()["workspace"]["avatar_url"])
    assert avatar.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(avatar.content)).size == (256, 256)


def test_onboarding_lists_personal_account_country_options(client, signed_in):
    response = client.get("/api/onboarding/options")
    assert response.status_code == 200
    assert {"code": "KE", "name": "Kenya"} in response.json()["countries"]
