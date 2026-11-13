"""Agent-owned immutable upload files and conversation-owned delivery copies."""
import re
import uuid
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select

from app.models import AgentAttachment, WorkflowAsset
from app.workflows.attachments import metadata
from app.workflows.actions.send_attachment.files import link_metadata, validate_file
from app.workflows.numbering import walk


def asset_references(nodes):
    for node in walk(nodes):
        if node["kind"] != "action": continue
        if node["type"] == "send_attachment":
            for item in node["config"]["translations"].values():
                if item["source"] == "upload": yield str(item["asset_id"]), item["file_format"]
        elif node["type"] == "show_carousel":
            for item in node["config"]["translations"].values():
                for card in item["cards"]:
                    image = card.get("image")
                    if image and image["source"] == "upload": yield str(image["asset_id"]), "image"


def asset_ids(nodes):
    return {asset_id for asset_id, _ in asset_references(nodes)}


def asset_metadata(file, workspace_id, agent_id):
    return {"id": str(file.id), "name": file.file_name, "format": file.file_format, "content_type": file.content_type,
        "size_bytes": file.size_bytes, "url": "/api/workspaces/{}/agents/{}/workflow-assets/{}/file".format(workspace_id, agent_id, file.id)}


def validate_references(db, agent, nodes):
    ids = asset_ids(nodes)
    if not ids: return
    files = {str(row.id): row.file_format for row in db.execute(select(WorkflowAsset.id, WorkflowAsset.file_format).where(
        WorkflowAsset.agent_id == agent.id, WorkflowAsset.workspace_id == agent.workspace_id,
        WorkflowAsset.id.in_([uuid.UUID(value) for value in ids]))).all()}
    if set(files) != ids: raise HTTPException(422, "Choose an uploaded attachment belonging to this agent. A file may have been removed; upload it again.")
    if any(files[asset_id] != file_format for asset_id, file_format in asset_references(nodes)):
        raise HTTPException(422, "The uploaded file does not match the selected file format.")



class AttachmentStore:
    def __init__(self, db, workspace_id, agent_id, conversation_id):
        self.db, self.workspace_id, self.agent_id, self.conversation_id = db, workspace_id, agent_id, conversation_id
        self.carousel_images = {}

    def carousel_image(self, asset_id):
        # A reused image needs one immutable sent copy in this turn.
        if asset_id not in self.carousel_images:
            self.carousel_images[asset_id] = self.deliver(asset_id, "image", "upload")
        return dict(self.carousel_images[asset_id])

    def deliver(self, value, file_format, source):
        if source == "upload":
            file = self.db.scalar(select(WorkflowAsset).where(WorkflowAsset.id == uuid.UUID(value), WorkflowAsset.agent_id == self.agent_id,
                WorkflowAsset.workspace_id == self.workspace_id))
            if file is None or file.file_format != file_format: raise ValueError("The uploaded attachment is no longer available for this agent.")
            snapshot = AgentAttachment(conversation_id=self.conversation_id, file_name=file.file_name, content_type=file.content_type,
                size_bytes=file.size_bytes, data=file.data)
            self.db.add(snapshot); self.db.flush()
            return {**metadata(snapshot, self.workspace_id, self.agent_id), "format": file_format, "external": False}
        if value.startswith("/") and source == "script":
            prefix = "/api/workspaces/{}/agents/{}/tester/attachments/".format(self.workspace_id, self.agent_id)
            if not value.startswith(prefix) or not re.fullmatch(r"[a-f0-9-]{36}", value[len(prefix):]):
                raise ValueError("Use an HTTP(S) file URL or a file URL from this conversation's survey answer.")
            file = self.db.scalar(select(AgentAttachment).where(AgentAttachment.id == uuid.UUID(value[len(prefix):]),
                AgentAttachment.conversation_id == self.conversation_id))
            if file is None: raise ValueError("The survey attachment does not belong to this conversation.")
            validate_file(file.file_name, file.data, file_format)
            return {**metadata(file, self.workspace_id, self.agent_id), "format": file_format, "external": False}
        return link_metadata(value, file_format)


def file_response(file, request, inline=False):
    from fastapi import Response
    content_type = file.content_type if inline and file.content_type.startswith(("image/", "audio/", "video/")) and file.content_type != "image/svg+xml" else "application/octet-stream"
    headers = {"Cache-Control": "private, no-store", "Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": ("inline" if content_type != "application/octet-stream" else "attachment") + "; filename=\"attachment\"; filename*=UTF-8''" + quote(file.file_name, safe="")}
    start, end, status = 0, file.size_bytes - 1, 200
    value = request.headers.get("range")
    if value:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        try:
            if not match or not any(match.groups()): raise ValueError()
            if match[1]: start = int(match[1]); end = min(int(match[2]), end) if match[2] else end
            else: start = max(0, file.size_bytes - int(match[2]))
            if not 0 <= start <= end or start >= file.size_bytes: raise ValueError()
        except ValueError:
            return Response(status_code=416, headers={**headers, "Content-Range": "bytes */{}".format(file.size_bytes)})
        status = 206; headers["Content-Range"] = "bytes {}-{}/{}".format(start, end, file.size_bytes)
    return Response(file.data[start:end + 1], status_code=status, media_type=content_type, headers=headers)
