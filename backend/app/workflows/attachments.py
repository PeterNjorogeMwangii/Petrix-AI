"""Bounded survey upload storage and plain metadata used by chat variables."""
import re

from fastapi import HTTPException

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_BATCH_BYTES = 20 * 1024 * 1024
MAX_FILES = 10


async def read_uploads(files):
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(422, "Choose between 1 and 10 files.")
    items, total = [], 0
    for file in files:
        content = await file.read(MAX_FILE_BYTES + 1)
        if not content:
            raise HTTPException(422, "Choose a file that is not empty.")
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, "Each file must be 10 MiB or smaller.")
        total += len(content)
        if total > MAX_BATCH_BYTES:
            raise HTTPException(413, "Upload no more than 20 MiB at a time.")
        name = re.split(r"[\\/]", file.filename or "attachment")[-1]
        name = "".join(character for character in name if ord(character) >= 32 and ord(character) != 127).strip()
        name = (name or "attachment")[:255]
        media_type = file.content_type or "application/octet-stream"
        if len(media_type) > 120 or not re.fullmatch(r"[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+\-]+", media_type):
            media_type = "application/octet-stream"
        items.append({"file_name": name, "content_type": media_type, "size_bytes": len(content), "data": content})
    return items


def metadata(file, workspace_id, agent_id):
    return {"id": str(file.id), "name": file.file_name, "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "url": "/api/workspaces/{}/agents/{}/tester/attachments/{}".format(workspace_id, agent_id, file.id)}
