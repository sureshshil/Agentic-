"""List MP3s in a Google Drive folder and write word → file-ID mapping.

The folder should contain files named exactly like the Japanese word (e.g. 場合.mp3).
Outputs vocab_artifacts/vocab_word_audio_ids.json  {"word": "fileId", ...}

Usage: build_word_audio_ids.py [folder_id]
Default folder_id is the N3 word-audio folder.
"""

import json
import os
import sys
from pathlib import Path

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

ROOT = Path(__file__).resolve().parent.parent
SA_JSON = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    str(ROOT / "deploy" / "quixotic-sunset-471304-g9-31388d371f18.json"),
)
OUT = ROOT / "vocab_artifacts" / "vocab_word_audio_ids.json"

WORD_AUDIO_FOLDER = "1Cx9YeRwdwdpboq1itjQ25pMWGcKsLSnV"


def get_token() -> str:
    creds = service_account.Credentials.from_service_account_file(
        SA_JSON, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    creds.refresh(Request())
    return creds.token


def list_files(folder_id: str) -> list[dict]:
    tok = get_token()
    files, page_token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name)",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(
            "https://www.googleapis.com/drive/v3/files",
            params=params,
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        files += data.get("files", [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return files


def main():
    folder_id = sys.argv[1] if len(sys.argv) > 1 else WORD_AUDIO_FOLDER
    print(f"Listing files in folder: {folder_id}")
    files = list_files(folder_id)

    mapping = {}
    for f in files:
        name = f["name"]
        if name.endswith(".mp3"):
            word = name[:-4]
            mapping[word] = f["id"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(mapping, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote {len(mapping)} word → file-ID mappings → {OUT}")


if __name__ == "__main__":
    main()
