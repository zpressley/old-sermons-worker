# old-sermons-worker

GitHub Actions jobs that inventory and transcribe a church's old sermon audio stored in a Google Shared Drive.
This repo holds code only: no audio, transcripts or keys. Results are run artifacts.

## Setup
- Repository secret `GOOGLE_SA_KEY`: the whole JSON key file of a Google service account that is a member of the source Shared Drive.

## Workflows (Actions tab → Run workflow)
1. **Inventory old sermon audio**: lists every year folder, opens each zip, reads MP3 tags and length,
   hashes the audio (ignoring tags) to find duplicates. Artifact `inventory`: `inventory.csv`, `batches.json`, `summary.txt`.
2. **Transcribe sermon openings**: give it the inventory run's ID. It transcribes the opening of each unique recording
   with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on the CPU, 35 minutes of recordings 60+ minutes long
   (whole services) and 12 minutes of shorter ones (sermon only). Start with `limit_batches` = 3 for a timing test.
   Artifact `transcripts`: one `.txt` per recording (named by audio hash) and `timing.csv`.

## Local testing
```
pip install -r requirements.txt
cd worker
python inventory.py local <folder> out.csv
python transcribe.py files out_dir some.mp3
```
