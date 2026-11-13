"""Validated media types and immutable, bounded workflow-file uploads."""
import io
from pathlib import PurePosixPath
import re
from urllib.parse import unquote, urlsplit
import warnings
import zipfile

from PIL import Image

MAX_FILE_BYTES = 20 * 1024 * 1024
FORMATS = {
    "image": {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"},
    "audio": {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav"},
    "video": {"mp4": "video/mp4", "avi": "video/x-msvideo", "mov": "video/quicktime"},
    "document": {"pdf": "application/pdf", "csv": "text/csv", "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xls": "application/vnd.ms-excel", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "ppt": "application/vnd.ms-powerpoint", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
}


def clean_name(value):
    value = re.split(r"[\\/]", value or "attachment")[-1]
    value = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127).strip()
    return (value or "attachment")[:255]


def extension(name):
    return PurePosixPath(name).suffix.lower().lstrip(".")


def validate_url(value):
    try:
        parts = urlsplit(value)
        if len(value) > 2048: raise ValueError()
        if not value.startswith(("https://", "http://")) or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        if parts.fragment or any(char.isspace() or ord(char) < 32 for char in value) or any(char in value for char in "\\{}"):
            raise ValueError()
        if parts.port is not None and not 1 <= parts.port <= 65535: raise ValueError()
        parts.hostname.encode("idna")
    except (ValueError, UnicodeError):
        raise ValueError("Enter a complete HTTP or HTTPS file URL without credentials, fragments, or placeholders.") from None
    return value


def link_metadata(url, file_format):
    validate_url(url)
    name = clean_name(unquote(PurePosixPath(urlsplit(url).path).name))
    suffix = extension(name)
    known = {value for formats in FORMATS.values() for value in formats}
    if suffix in known and suffix not in FORMATS[file_format]:
        raise ValueError("The file URL extension does not match the selected file format.")
    if suffix not in FORMATS[file_format]:
        suffix = next(iter(FORMATS[file_format])); name = "attachment." + suffix
    return {"id": url, "name": name, "url": url, "content_type": FORMATS[file_format][suffix],
        "size_bytes": None, "format": file_format, "external": True}


def validate_file(name, data, file_format):
    suffix = extension(name)
    if file_format not in FORMATS or suffix not in FORMATS[file_format]:
        raise ValueError("Choose a file with an extension supported by the selected file format.")
    if not data: raise ValueError("Choose a file that is not empty.")
    if len(data) > MAX_FILE_BYTES: raise ValueError("The maximum file size is 20 MiB.")
    try:
        if file_format == "image":
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    expected = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "gif": "GIF"}[suffix]
                    if image.format != expected or image.width * image.height > 40_000_000: raise ValueError()
                    image.verify()
        elif suffix == "pdf":
            if not data.lstrip().startswith(b"%PDF-"): raise ValueError()
        elif suffix in {"doc", "xls", "ppt"}:
            if not data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")): raise ValueError()
        elif suffix in {"docx", "xlsx", "pptx"}:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(item.file_size for item in entries) > 200 * 1024 * 1024: raise ValueError()
                names = set(archive.namelist()); folder = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}[suffix]
                if "[Content_Types].xml" not in names or not any(path.startswith(folder) for path in names): raise ValueError()
        elif suffix == "csv":
            if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")): raise ValueError()
        elif suffix == "wav":
            if data[:4] != b"RIFF" or data[8:12] != b"WAVE": raise ValueError()
        elif suffix == "avi":
            if data[:4] != b"RIFF" or data[8:12] != b"AVI ": raise ValueError()
        elif suffix == "mp3":
            if not data.startswith(b"ID3") and not (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224): raise ValueError()
        elif suffix in {"m4a", "mp4"}:
            if data[4:8] != b"ftyp": raise ValueError()
        elif suffix == "mov":
            if data[4:8] not in {b"ftyp", b"moov", b"mdat", b"wide", b"free"}: raise ValueError()
    except (ValueError, OSError, SyntaxError, zipfile.BadZipFile, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError("The file content does not match its extension, or the file is invalid or too large to preview.") from None
    return FORMATS[file_format][suffix]
