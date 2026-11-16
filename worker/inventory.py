r"""Inventory the old sermon audio in a Google Shared Drive.

  python worker/inventory.py plan DRIVE_ID                      -> JSON list of top-level folders (for a job matrix)
  python worker/inventory.py folder DRIVE_ID FOLDER_ID OUT_CSV  -> one row per MP3 under that folder (zips are opened)
  python worker/inventory.py local ROOT OUT_CSV                 -> same, for a folder already on disk (testing)
  python worker/inventory.py merge IN_DIR OUT_DIR               -> inventory.csv (duplicates marked), batches.json, summary.txt

The audio hash skips ID3 tags, so a re-tagged copy of the same recording still counts as a duplicate.
"""
import csv, hashlib, json, os, pathlib, re, shutil, sys, tempfile, zipfile
from mutagen.mp3 import MP3

FIELDS = ["year_folder", "source_id", "source_path", "member", "date", "date_source", "title_from_name",
          "minutes", "mb", "kind_guess", "id3_title", "id3_artist", "id3_album", "id3_date", "id3_comment",
          "audio_md5", "duplicate_of", "note"]
JUNK = re.compile(r"(^|/)(\.DS_Store|__MACOSX/.*|\._.*|Thumbs\.db|desktop\.ini)$", re.I)


def audio_hash(p):
    data = pathlib.Path(p).read_bytes()
    start = 0
    if data[:3] == b"ID3":
        s = data[6:10]
        start = 10 + ((s[0] << 21) | (s[1] << 14) | (s[2] << 7) | s[3])
    end = len(data) - 128 if data[-128:-125] == b"TAG" else len(data)
    return hashlib.md5(data[start:end]).hexdigest()


def _tag(tags, key):
    v = tags.get(key) if tags else None
    return str(v.text[0]).strip() if v is not None and getattr(v, "text", None) else ""


def date_for(source_path, member):
    full = f"{source_path}/{member}" if member else source_path
    m = re.search(r"((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})", pathlib.PurePosixPath(full).name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "file name"
    m = re.search(r"((?:19|20)\d{2})[-_.](\d{2})[-_.](\d{2})", full)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "folder name"
    year = re.search(r"((?:19|20)\d{2}) Audio", full)
    month = re.search(r"(?:^|/)(\d{2})-\d+(?: \d+)?(?:\.zip)?(?:/|$)", full)
    if year and month:
        return f"{year.group(1)}-{month.group(1)}-??", "month only (zip/folder name)"
    return "", ""


def analyze(local, year_folder, source_id, source_path, member):
    name = pathlib.PurePosixPath(member or source_path).stem
    date, src = date_for(source_path, member)
    try:
        a = MP3(local)
        length, tags = a.info.length, a.tags
    except Exception as e:
        length, tags = 0, None
    if not date and tags:
        d = _tag(tags, "TDRC")
        if re.match(r"\d{4}-\d{2}-\d{2}", d):
            date, src = d[:10], "ID3 date tag"
    minutes = round(length / 60, 1)
    return {
        "year_folder": year_folder, "source_id": source_id, "source_path": source_path, "member": member,
        "date": date, "date_source": src,
        "title_from_name": re.sub(r"^\d{4}-?\d{2}-?\d{2}[_ -]*", "", name).replace("_", " ").replace("-", " ").strip(),
        "minutes": minutes, "mb": round(os.path.getsize(local) / 1e6, 1),
        "kind_guess": "unreadable" if not length else ("full service?" if minutes >= 60 else "sermon only?"),
        "id3_title": _tag(tags, "TIT2"), "id3_artist": _tag(tags, "TPE1"), "id3_album": _tag(tags, "TALB"),
        "id3_date": _tag(tags, "TDRC"),
        "id3_comment": next((str(c.text[0]) for c in (tags.getall("COMM") if tags else []) if c.text), ""),
        "audio_md5": audio_hash(local), "duplicate_of": "", "note": "",
    }


def other(year_folder, source_id, source_path, member, note):
    r = dict.fromkeys(FIELDS, "")
    r.update(year_folder=year_folder, source_id=source_id, source_path=source_path, member=member, note=note)
    return r


def rows_for_zip(zpath, year_folder, source_id, source_path):
    rows = []
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile:
        return [other(year_folder, source_id, source_path, "", "bad zip")]
    with zf, tempfile.TemporaryDirectory() as td:
        for info in zf.infolist():
            n = info.filename
            if info.is_dir() or JUNK.search(n):
                continue
            if not n.lower().endswith(".mp3"):
                rows.append(other(year_folder, source_id, source_path, n, "not an mp3"))
                continue
            out = os.path.join(td, "a.mp3")
            with zf.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            rows.append(analyze(out, year_folder, source_id, source_path, n))
            os.remove(out)
    return rows


def write(rows, out_csv):
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def cmd_plan(drive_id):
    from drive import children, FOLDER
    folders = [{"id": f["id"], "name": f["name"]} for f in children(drive_id, drive_id) if f["mimeType"] == FOLDER]
    print(json.dumps(sorted(folders, key=lambda f: f["name"])))


def cmd_folder(drive_id, folder_id, out_csv):
    from drive import children, walk, download
    year = next((f["name"] for f in children(drive_id, drive_id) if f["id"] == folder_id), folder_id)
    rows = []
    with tempfile.TemporaryDirectory() as td:
        for path, f in walk(folder_id, drive_id, year):
            low = f["name"].lower()
            if JUNK.search(path):
                continue
            if not (low.endswith(".zip") or low.endswith(".mp3")):
                rows.append(other(year, f["id"], path, "", f"not audio ({f['mimeType']})"))
                continue
            local = os.path.join(td, "z.zip" if low.endswith(".zip") else "a.mp3")
            print(f"download {path} ({int(f.get('size', 0)) / 1e6:.0f} MB)", flush=True)
            download(f["id"], local)
            rows += rows_for_zip(local, year, f["id"], path) if low.endswith(".zip") else \
                [analyze(local, year, f["id"], path, "")]
            os.remove(local)
    write(rows, out_csv)
    print(f"{year}: {sum(1 for r in rows if r['audio_md5'])} mp3s")


def cmd_local(root, out_csv):
    root = pathlib.Path(root)
    rows = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_dir() or JUNK.search(rel):
            continue
        year = rel.split("/")[0]
        if p.suffix.lower() == ".zip":
            rows += rows_for_zip(p, year, "local", rel)
        elif p.suffix.lower() == ".mp3":
            rows.append(analyze(p, year, "local", rel, ""))
        else:
            rows.append(other(year, "local", rel, "", "not audio"))
    write(rows, out_csv)
    print(f"{sum(1 for r in rows if r['audio_md5'])} mp3s")


def keeper_rank(r):
    # Prefer a copy with a title tag, then a full date, then one from a zip (the original batch), then the shortest path.
    return (not r["id3_title"], "?" in r["date"] or not r["date"], not r["source_path"].lower().endswith(".zip"),
            len(r["source_path"] + r["member"]), r["source_path"], r["member"])


def cmd_merge(in_dir, out_dir):
    rows = []
    for p in sorted(pathlib.Path(in_dir).rglob("*.csv")):
        rows += list(csv.DictReader(open(p, encoding="utf-8")))
    groups = {}
    for r in rows:
        if r["audio_md5"]:
            groups.setdefault(r["audio_md5"], []).append(r)
    for g in groups.values():
        g.sort(key=keeper_rank)
        label = g[0]["source_path"] + (f" > {g[0]['member']}" if g[0]["member"] else "")
        for r in g[1:]:
            r["duplicate_of"] = label
    rows.sort(key=lambda r: (r["date"] or "9999", r["year_folder"], r["source_path"], r["member"]))
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write(rows, out / "inventory.csv")

    unique = [r for r in rows if r["audio_md5"] and not r["duplicate_of"]]
    batches = {}
    for r in unique:
        b = batches.setdefault(r["source_id"], {"source_id": r["source_id"], "source_path": r["source_path"], "files": []})
        b["files"].append({"member": r["member"], "date": r["date"], "minutes": float(r["minutes"] or 0),
                           "audio_md5": r["audio_md5"]})
    (out / "batches.json").write_text(json.dumps(list(batches.values()), indent=1), encoding="utf-8")

    mp3 = [r for r in rows if r["audio_md5"]]
    lines = [f"MP3 files found: {len(mp3)}", f"Duplicates: {len(mp3) - len(unique)}", f"Unique recordings: {len(unique)}",
             f"Unique hours: {sum(float(r['minutes'] or 0) for r in unique) / 60:.1f}",
             f"With a full date: {sum(1 for r in unique if r['date'] and '?' not in r['date'])}",
             f"Month only: {sum(1 for r in unique if '?' in r['date'])}",
             f"No date: {sum(1 for r in unique if not r['date'])}",
             f"With an ID3 title: {sum(1 for r in unique if r['id3_title'])}",
             f"Full service? (60+ min): {sum(1 for r in unique if r['kind_guess'] == 'full service?')}",
             f"Unreadable: {sum(1 for r in unique if r['kind_guess'] == 'unreadable')}",
             f"Other files skipped: {sum(1 for r in rows if not r['audio_md5'])}", "", "Unique by year folder:"]
    for y in sorted({r["year_folder"] for r in unique}):
        u = [r for r in unique if r["year_folder"] == y]
        lines.append(f"  {y}: {len(u)} ({sum(float(r['minutes'] or 0) for r in u) / 60:.1f} h)")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"plan": cmd_plan, "folder": cmd_folder, "local": cmd_local, "merge": cmd_merge}[cmd](*args)
