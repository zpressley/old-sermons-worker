r"""Transcribe the opening of each unique recording with faster-whisper (CPU).

  python worker/transcribe.py chunk BATCHES_JSON N I OUT_DIR   -> share I of N (balanced by audio length), from Drive
  python worker/transcribe.py files OUT_DIR file1.mp3 ...       -> local files (testing)

Window: recordings of 60+ minutes are probably whole services (songs, reading, long prayer, then the sermon),
so they get LONG_MINUTES (default 35); shorter ones are probably sermon-only and get SHORT_MINUTES (default 12).
Model: WHISPER_MODEL (default base.en). Writes OUT_DIR/<audio_md5 or file stem>.txt and OUT_DIR/timing-<I>.csv.
"""
import csv, json, os, pathlib, sys, tempfile, time, zipfile, shutil

import av
_av_open = av.open
def _open_compat(*a, **kw):  # PyAV 15+ dropped metadata_errors, which faster-whisper 1.2.1 still passes
    kw.pop("metadata_errors", None)
    return _av_open(*a, **kw)
av.open = _open_compat
from faster_whisper import WhisperModel, decode_audio

LONG = float(os.environ.get("LONG_MINUTES", 35))
SHORT = float(os.environ.get("SHORT_MINUTES", 12))
MODEL = os.environ.get("WHISPER_MODEL", "base.en")
_model = None


def model():
    global _model
    if _model is None:
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    return _model


def transcribe(local, out_txt, header):
    t = time.time()
    audio = decode_audio(str(local), sampling_rate=16000)
    full_min = len(audio) / 16000 / 60
    window = LONG if full_min >= 60 else SHORT
    audio = audio[: int(window * 60 * 16000)]
    segs, _ = model().transcribe(audio, language="en", beam_size=1, vad_filter=True)
    lines = [f"[{int(s.start // 60):02d}:{int(s.start % 60):02d}] {s.text.strip()}" for s in segs]
    head = "\n".join(f"# {k}: {v}" for k, v in header.items())
    pathlib.Path(out_txt).write_text(f"{head}\n# model: {MODEL}, first {window:g} of {full_min:.1f} min\n\n"
                                     + "\n".join(lines) + "\n", encoding="utf-8")
    took = time.time() - t
    print(f"{took:6.1f}s  {window:g}/{full_min:.0f} min  {header.get('member') or header.get('file')}", flush=True)
    return {"seconds": round(took, 1), "window_min": window, "full_min": round(full_min, 1)}


def split(batches, n):
    shares = [[] for _ in range(n)]
    load = [0.0] * n
    for b in sorted(batches, key=lambda b: -sum(min(f["minutes"], LONG) for f in b["files"])):
        i = load.index(min(load))
        shares[i].append(b)
        load[i] += sum(min(f["minutes"], LONG) for f in b["files"])
    return shares


def cmd_chunk(batches_json, n, i, out_dir):
    from drive import download
    n, i = int(n), int(i)
    mine = split(json.load(open(batches_json, encoding="utf-8")), n)[i]
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    timing = []
    with tempfile.TemporaryDirectory() as td:
        for b in mine:
            src = os.path.join(td, "src")
            print(f"download {b['source_path']}", flush=True)
            download(b["source_id"], src)
            for f in b["files"]:
                local = src
                if f["member"]:
                    local = os.path.join(td, "a.mp3")
                    with zipfile.ZipFile(src) as zf, zf.open(f["member"]) as s, open(local, "wb") as d:
                        shutil.copyfileobj(s, d)
                header = {"source": b["source_path"], "member": f["member"], "date": f["date"], "audio_md5": f["audio_md5"]}
                t = transcribe(local, out / f"{f['audio_md5']}.txt", header)
                timing.append({**header, **t})
            os.remove(src)
    with open(out / f"timing-{i}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source", "member", "date", "audio_md5", "seconds", "window_min", "full_min"])
        w.writeheader()
        w.writerows(timing)


def cmd_files(out_dir, *files):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in files:
        transcribe(f, out / (pathlib.Path(f).stem + ".txt"), {"file": f})


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"chunk": cmd_chunk, "files": cmd_files}[cmd](*args)
