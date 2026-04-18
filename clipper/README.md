# Podcast Clipper

AI scans a library of podcast videos + transcripts, proposes the most
clip-worthy moments for each episode with a virality score, lets you review
them in a web UI, and cuts the approved clips with `ffmpeg`.

## Pipeline

1. **Scan** — recursively pair each video (`.mp4/.mov/.mkv/.webm/.m4v/.avi`)
   with a sibling transcript (`.srt`, `.vtt`, Whisper `.json`, or `.txt`).
2. **Score** — Claude (with prompt caching on the scoring rubric) reads each
   transcript window and proposes ranked clip candidates with timestamps,
   a title, hook, rationale, virality score, and tags.
3. **Snap** — clip boundaries snap to word-level timestamps when available so
   cuts don't land mid-word.
4. **Review** — launch the FastAPI UI to preview each clip (the browser
   streams the source with HTTP Range), nudge timestamps, edit titles, and
   approve.
5. **Cut** — `ffmpeg` slices the source at approved timestamps. Optional 9:16
   vertical reformat and burned-in caption headline for social.

## Install

```bash
pip install -r requirements.txt
# ffmpeg must be on PATH
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

Score a library:

```bash
python -m clipper.cli score /path/to/podcast/library
```

Launch the review UI:

```bash
python -m clipper.cli serve
# → http://127.0.0.1:8765
```

In the UI, per clip you can:

- Preview the segment (player auto-seeks to the clip range).
- Edit start/end (seconds) and the title.
- Approve / Reject / Render (single clip) or render all approved at once.
- Toggle 9:16 + burned captions when rendering.

Rendered MP4s land in `.clipper/clips/`.

## Expected library layout

Either sibling files or a `transcripts/` subfolder:

```
library/
  ep001.mp4
  ep001.srt
  ep002.mp4
  transcripts/
    ep002.json      # Whisper output with word-level timings
```

## Notes

- Whisper JSON with word-level timestamps gives the cleanest cuts. Plain
  `.txt` works but produces only rough boundaries — prefer timed formats.
- The scorer uses `claude-sonnet-4-6` by default. Pass `--model` to override.
- For very long episodes the transcript is chunked into 15-minute windows
  with a 60 s overlap; the scoring rubric is sent once per window and cached.
- `ffmpeg` uses stream-copy when possible (fast, lossless). Re-encoding
  kicks in for vertical reformat or burned captions.
