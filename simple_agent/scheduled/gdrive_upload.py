"""Upload a local file into a Google Drive folder shared with the vocab
service account. Same key as the Doc sink, Drive scope instead of Docs.

Usage: gdrive_upload.py <local_path> <folder_id> [drive_name]
"""

import json
import os
import sys

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

SA_JSON = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    os.path.join(os.path.dirname(__file__), "..", "deploy",
                 "quixotic-sunset-471304-g9-31388d371f18.json"),
)
_TIMEOUT = 60


def token() -> str:
    creds = service_account.Credentials.from_service_account_file(
        SA_JSON, scopes=["https://www.googleapis.com/auth/drive"])
    creds.refresh(Request())
    return creds.token


def upload(local_path: str, folder_id: str, name: str | None = None) -> dict:
    name = name or os.path.basename(local_path)
    ct = "audio/mpeg" if local_path.endswith(".mp3") else "application/octet-stream"
    meta = {"name": name, "parents": [folder_id]}
    with open(local_path, "rb") as fh:
        data = fh.read()
    r = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType": "multipart", "supportsAllDrives": "true",
                "fields": "id,name,webViewLink,size,parents"},
        headers={"Authorization": f"Bearer {token()}"},
        files={
            "metadata": ("metadata", json.dumps(meta), "application/json"),
            "file": (name, data, ct),
        },
        timeout=_TIMEOUT,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


if __name__ == "__main__":
    info = upload(sys.argv[1], sys.argv[2],
                  sys.argv[3] if len(sys.argv) > 3 else None)
    print(json.dumps(info, indent=2))
