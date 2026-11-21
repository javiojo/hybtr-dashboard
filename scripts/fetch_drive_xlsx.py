#!/usr/bin/env python3
"""
Download the newest .xlsx from a Google Drive folder using a read-only
service account. The JSON key is read from the GDRIVE_SA_KEY environment
variable (a full service-account JSON document, the GitHub Actions secret
of the same name) or from --key-file.

If the secret is absent, exit 0 and print SKIP=no-secret so the workflow
can continue without failing.

This script never creates a Google Cloud project or credentials; it only
uses ones that already exist. See docs/DRIVE_SETUP.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_FOLDER_ID = "1GCtlKEnOMtL-Y4vuL97h3QVQamJOKE0W"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def load_key(key_file: Path | None) -> dict | None:
    raw = os.environ.get("GDRIVE_SA_KEY", "").strip()
    if not raw and key_file:
        if not key_file.is_file():
            return None
        raw = key_file.read_text().strip()
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        print("ERROR: GDRIVE_SA_KEY is not valid JSON", file=sys.stderr)
        sys.exit(2)
    if not isinstance(info, dict) or "private_key" not in info or "client_email" not in info:
        print("ERROR: GDRIVE_SA_KEY is not a service-account JSON key", file=sys.stderr)
        sys.exit(2)
    return info


def newest_xlsx(service, folder_id: str) -> dict | None:
    query = (
        f"'{folder_id}' in parents and trashed = false and ("
        f"mimeType = '{XLSX_MIME}' or name contains '.xlsx')"
    )
    resp = (
        service.files()
        .list(
            q=query,
            fields="files(id,name,modifiedTime,mimeType,size)",
            orderBy="modifiedTime desc",
            pageSize=50,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
    )
    files = resp.get("files", [])
    # Prefer real Excel workbooks; ignore Google Sheets shortcuts / leftovers.
    xlsx = [
        f
        for f in files
        if f.get("mimeType") == XLSX_MIME or str(f.get("name", "")).lower().endswith(".xlsx")
    ]
    return xlsx[0] if xlsx else None


def download_file(service, file_id: str, dest: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    dest.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with dest.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", DEFAULT_FOLDER_ID))
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--key-file", type=Path, default=None)
    args = p.parse_args(argv)

    info = load_key(args.key_file)
    if info is None:
        print("SKIP=no-secret")
        print("No GDRIVE_SA_KEY present; not contacting Drive.")
        return 0

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print(
            "ERROR: google-auth and google-api-python-client are required "
            "to download from Drive. The workflow installs them when the "
            "secret is present.",
            file=sys.stderr,
        )
        return 2

    creds = service_account.Credentials.from_service_account_info(info, scopes=[DRIVE_SCOPE])
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    meta = newest_xlsx(service, args.folder_id)
    if meta is None:
        print("SKIP=no-xlsx")
        print(f"No .xlsx files found in Drive folder {args.folder_id}")
        return 0

    # Keep only the basename so a crafted Drive file name cannot escape output-dir.
    safe_name = Path(meta["name"]).name
    dest = args.output_dir / safe_name
    download_file(service, meta["id"], dest)
    print(f"NAME={safe_name}")
    print(f"MODIFIED={meta.get('modifiedTime', '')}")
    print(f"PATH={dest}")
    print(f"Downloaded {safe_name} (modified {meta.get('modifiedTime', '?')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
