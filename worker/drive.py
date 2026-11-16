"""Google Drive helpers: service-account auth from the GOOGLE_SA_KEY env var, Shared Drive listing, download.

Errors never include the key's content.
"""
import json, os, time, random
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
_session = None


def session():
    global _session
    if _session:
        return _session
    raw = os.environ.get("GOOGLE_SA_KEY")
    if not raw:
        raise SystemExit("GOOGLE_SA_KEY is not set")
    try:
        info = json.loads(raw)
    except Exception:
        raise SystemExit("GOOGLE_SA_KEY is not JSON. It must be the whole downloaded key file, starting with {") from None
    if not info.get("client_email") or not info.get("private_key"):
        raise SystemExit("GOOGLE_SA_KEY is missing client_email or private_key")
    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"])
    except Exception:
        raise SystemExit("GOOGLE_SA_KEY could not be loaded (bad private_key?)") from None
    _session = AuthorizedSession(creds)
    return _session


def _get(url, **kw):
    for attempt in range(8):
        try:
            r = session().get(url, timeout=120, **kw)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code < 400:
            return r
        retry = r is None or r.status_code == 429 or r.status_code >= 500 or (
            r.status_code == 403 and "ateLimitExceeded" in r.text)
        if not retry:
            raise SystemExit(f"Drive error {r.status_code}: {r.text[:300]}")
        time.sleep(min(60, 2 ** attempt) + random.random())
    raise SystemExit(f"Drive kept failing: {url}")


def children(folder_id, drive_id):
    out, token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, size, md5Checksum)",
            "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "corpora": "drive", "driveId": drive_id,
        }
        if token:
            params["pageToken"] = token
        j = _get(f"{API}/files", params=params).json()
        out += j.get("files", [])
        token = j.get("nextPageToken")
        if not token:
            return out


def walk(folder_id, drive_id, path=""):
    """Yield (path, file) for every non-folder file under folder_id."""
    for f in sorted(children(folder_id, drive_id), key=lambda f: f["name"]):
        p = f"{path}/{f['name']}" if path else f["name"]
        if f["mimeType"] == FOLDER:
            yield from walk(f["id"], drive_id, p)
        else:
            yield p, f


def download(file_id, dest):
    for attempt in range(5):
        try:
            with session().get(f"{API}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
                               stream=True, timeout=600) as r:
                if r.status_code >= 400:
                    raise IOError(f"HTTP {r.status_code}")
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            return
        except Exception as e:
            if attempt == 4:
                raise SystemExit(f"download failed for {file_id}: {e}")
            time.sleep(2 ** attempt + random.random())
