"""Saving an uploaded workbook: only under `data/clients/<id>/source/`, only .xlsx/.xlsm.

Checks, in order, before anything is written:

* the client id matches `^[a-z0-9_]{2,40}$`;
* the file name is reduced to a plain base name of safe characters (no folders, no `..`,
  no drive letters, no leading dot) and must end in `.xlsx` or `.xlsm`;
* the upload is at most 25 MB;
* it is a real zip with an Excel workbook part, at most 2,000 entries, at most 200 MB
  uncompressed, and no entry compressed more than 200 to 1 (a zip bomb is refused);
* the destination resolves inside the client's source folder, and an existing file with
  different content is never overwritten.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

from finance_engine.onboarding import check_client_id

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_RATIO = 200
ALLOWED_SUFFIXES = (".xlsx", ".xlsm")
_UNSAFE = re.compile(r"[^A-Za-z0-9 ._()-]+")


class UploadError(Exception):
    """The upload was refused. Nothing was written."""


def sanitise_filename(name: str) -> str:
    """A plain, safe base name, or UploadError."""
    base = re.split(r"[\\/]", str(name or ""))[-1]          # drop any folder part
    base = base.split(":")[-1]                               # and a drive letter
    base = _UNSAFE.sub("_", base).strip(" .")
    base = re.sub(r"_+", "_", base)
    if not base or base in (".", ".."):
        raise UploadError("The file has no usable name.")
    if not base.lower().endswith(ALLOWED_SUFFIXES):
        raise UploadError(f"{base!r} is not an Excel workbook: only .xlsx and .xlsm are "
                          f"accepted.")
    stem, dot, suffix = base.rpartition(".")
    if not stem.strip("_ "):
        raise UploadError("The file has no usable name.")
    if len(base) > 120:
        base = stem[:110].rstrip(" ._") + dot + suffix
    return base


def check_workbook_bytes(data: bytes) -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"The file is {len(data):,} bytes; the limit is "
                          f"{MAX_UPLOAD_BYTES:,} (25 MB).")
    if not data:
        raise UploadError("The file is empty.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadError("The file is not an Excel workbook (.xlsx/.xlsm files are zip "
                          "archives; this one is not).") from None
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise UploadError(f"The workbook has {len(entries)} parts; the limit is "
                              f"{MAX_ENTRIES}.")
        total = 0
        for info in entries:
            total += info.file_size
            if info.file_size > MAX_RATIO * max(info.compress_size, 1) \
                    and info.file_size > 1024 * 1024:
                raise UploadError(f"Part {info.filename!r} expands more than "
                                  f"{MAX_RATIO} to 1; refused as a possible zip bomb.")
        if total > MAX_UNCOMPRESSED_BYTES:
            raise UploadError(f"The workbook expands to {total:,} bytes; the limit is "
                              f"{MAX_UNCOMPRESSED_BYTES:,}.")
        names = {i.filename for i in entries}
        if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
            raise UploadError("The zip is not an Excel workbook (no xl/workbook.xml).")


def save_upload(data_dir: Path, client_id: str, filename: str, data: bytes) -> Path:
    """Write the upload to `data/clients/<id>/source/<safe name>` and return the path."""
    check_client_id(client_id)
    name = sanitise_filename(filename)
    check_workbook_bytes(data)
    folder = (Path(data_dir) / client_id / "source").resolve()
    dest = (folder / name).resolve()
    if dest.parent != folder:
        raise UploadError("The file name resolves outside the client's source folder.")
    if dest.exists():
        if hashlib.sha256(dest.read_bytes()).digest() == hashlib.sha256(data).digest():
            return dest                                    # the same file again
        raise UploadError(f"{name} already exists for {client_id} with different "
                          f"content. Rename the file; a source workbook is never "
                          f"overwritten.")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest
