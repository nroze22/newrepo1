# Podcast Clipper

AI scans a library of podcast videos (YouTube URLs or local files), proposes
the most clip-worthy moments for each episode with a virality score, lets you
review them in a web UI, and cuts the approved clips with `ffmpeg`.

## Pipeline

1. **Ingest** — pull YouTube URLs (via `yt-dlp`, with auto-captions when
   available) or local files into a workdir.
2. **Transcribe (fallback)** — if a video lacks a transcript, extract audio
   and call OpenAI Whisper with word-level timestamps.
3. **Score** — Claude (with prompt-cached rubric) reads each transcript
   window and proposes ranked clip candidates with timestamps, a title, hook,
   rationale, virality score, and tags.
4. **Snap** — clip boundaries snap to word-level timestamps when available so
   cuts don't land mid-word.
5. **Review** — FastAPI UI to preview each clip (browser streams the source
   with HTTP Range), nudge timestamps, edit titles, and approve.
6. **Cut** — `ffmpeg` slices the source at approved timestamps. Optional
   9:16 vertical reformat and burned-in caption headline.

## Install

```bash
pip install -r requirements.txt
# ffmpeg must be on PATH
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...        # only needed for Whisper fallback
```

## One-shot flow

```bash
# Pull + score a mix of YouTube and local sources
clipper run \
    https://youtube.com/watch?v=EXAMPLE \
    ~/podcasts/ep042.mp4

# Open the review UI → approve or reject each clip
clipper serve
# → http://127.0.0.1:8765

# Headless render of everything you approved
clipper render --approved --vertical --captions
```

All commands respect `--workdir` (default `.clipper/`); inside it you'll find
`sources/` (ingested videos + transcripts), `clipper.db` (candidate state),
and `clips/` (rendered MP4s).

## Individual commands

```bash
clipper ingest <url-or-path> [<url-or-path> ...]   # download + transcribe only
clipper score [<library-dir>]                      # score workdir or an existing dir
clipper render --approved [--vertical] [--captions]
clipper serve [--port 8765]
clipper list
```

`clipper ingest` accepts:
- **YouTube URLs** (`https://youtube.com/...`, `https://youtu.be/...`) — downloads
  1080p mp4 and pulls the uploader's subtitles / auto-captions if available.
- **Local video files** (`.mp4 .mov .mkv .webm .m4v .avi`) — copied into
  `sources/`; a sibling `.srt` / `.vtt` / `.json` / `.txt` is picked up
  automatically, or Whisper is called.

## Expected local library layout

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

- Whisper JSON with word-level timestamps gives the cleanest cuts.
- The scorer uses `claude-sonnet-4-6` by default. Pass `--model` to override.
- Long episodes are chunked into 15-minute windows with a 60 s overlap; the
  scoring rubric is sent once per window and cached.
- Whisper fallback extracts mono 32 kbps mp3, splits into <24 MB chunks, and
  merges the word-level timings with correct offsets.
- `ffmpeg` uses stream-copy when possible (fast, lossless). Re-encoding
  kicks in only for vertical reformat or burned captions.
