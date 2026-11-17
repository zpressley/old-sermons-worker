r"""Copy each recording, as its own MP3, from the source Shared Drive into folders in the Sermons Shared Drive.

  python worker/upload.py folders PLAN_CSV DEST_DRIVE_ID PARENT_NAME ROOT_NAME OUT_JSON
      -> finds PARENT_NAME (e.g. "Sermon Audio"), makes ROOT_NAME under it and every folder named in the plan
  python worker/upload.py chunk BATCHES_JSON PLAN_CSV FOLDERS_JSON N I OUT_CSV
      -> share I of N: download each source zip/MP3 once, upload the planned files

PLAN_CSV columns: audio_md5, folder, filename. Files are uploaded private (Shared Drive members only).
Each upload's description holds "audio_md5: <hash>", and a file already in its folder under the same name is
skipped, so a rerun picks up where a failed run stopped.
"""
import csv, json, os, sys, tempfile, zipfile, shutil

from drive import children, download, ensure_folder, find_folders, upload


def read_plan(p):
    return {r["audio_md5"]: r for r in csv.DictReader(open(p, encoding="utf-8"))}


def cmd_folders(plan_csv, drive_id, parent_name, root_name, out_json):
    found = find_folders(parent_name, drive_id)
    if len(found) != 1:
        top = sorted(f["name"] for f in children(drive_id, drive_id))
        raise SystemExit(f"expected one folder named {parent_name!r}, found {len(found)}. Top level: {top}")
    root = ensure_folder(root_name, found[0]["id"], drive_id)
    names = sorted({r["folder"] for r in read_plan(plan_csv).values()})
    ids = {n: ensure_folder(n, root, drive_id) for n in names}
    json.dump({"root": root, "folders": ids}, open(out_json, "w"))
    print(f"{root_name}: {len(ids)} folders")


def cmd_chunk(batches_json, plan_csv, folders_json, n, i, out_csv):
    n, i = int(n), int(i)
    plan = read_plan(plan_csv)
    fids = json.load(open(folders_json))["folders"]
    drive_id = os.environ["DEST_DRIVE_ID"]
    batches = [b for b in json.load(open(batches_json)) if any(f["audio_md5"] in plan for f in b["files"])][i::n]
    existing = {}
    rows = []
    with tempfile.TemporaryDirectory() as td:
        for b in batches:
            src = os.path.join(td, "src")
            print(f"download {b['source_path']}", flush=True)
            download(b["source_id"], src)
            for f in b["files"]:
                p = plan.get(f["audio_md5"])
                if not p:
                    continue
                folder = fids[p["folder"]]
                if folder not in existing:
                    existing[folder] = {c["name"]: c["id"] for c in children(folder, drive_id)}
                if p["filename"] in existing[folder]:
                    fid = existing[folder][p["filename"]]
                    print(f"  exists {p['folder']}/{p['filename']}", flush=True)
                else:
                    local = src
                    if f["member"]:
                        local = os.path.join(td, "a.mp3")
                        with zipfile.ZipFile(src) as zf, zf.open(f["member"]) as s, open(local, "wb") as d:
                            shutil.copyfileobj(s, d)
                    fid = upload(local, p["filename"], folder, f"audio_md5: {f['audio_md5']}")
                    existing[folder][p["filename"]] = fid
                    print(f"  uploaded {p['folder']}/{p['filename']}", flush=True)
                rows.append({"audio_md5": f["audio_md5"], "folder": p["folder"], "filename": p["filename"], "file_id": fid})
            os.remove(src)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["audio_md5", "folder", "filename", "file_id"])
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"folders": cmd_folders, "chunk": cmd_chunk}[cmd](*args)
