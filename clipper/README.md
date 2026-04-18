# Podcast Clipper

A multi-agent clipping studio for podcast creators. It ingests YouTube URLs
or local files, runs a team of specialized AI agents over every episode —
Producer → Scouts → Editor → Critic → Packager — and lets you review and
cut the approved clips with `ffmpeg`.

## Agent architecture

- **Producer (the brain)** — reads the full episode and writes an *Episode
  Brief*: domain, expert persona adopted for that space, narrative arc,
  brand POV, target audience, 3–6 *clip archetypes* tailored to this
  specific episode, and what to avoid (sponsor reads, inside refs, etc.).
- **Coordinator** — orchestrates the pipeline, fans out scouts in parallel,
  merges results, enforces archetype diversity, and emits live progress.
- **Scouts** — one per transcript window, each armed with the brief. They
  propose candidate clips aligned with the brief's archetypes.
- **Editor** — refines the finalist set: tightens boundaries, rewrites
  rationales, dedupes by theme, rebalances across archetypes.
- **Critic** — final gate. Checks coverage, representation, and quality.
  Can drop or flag clips. Writes a one-sentence coverage note.
- **Packager** — for every finalist: 3 title variants, captions for
  TikTok/Reels/Shorts/X/LinkedIn, hashtags, a thumbnail moment with a
  reason, a "why it works" note, and an audience-appeal line.

## Pipeline

1. **Ingest** — `yt-dlp` pulls YouTube at 1080p mp4 with uploader subs
   preferred over auto-captions; local files are copied in; either gets
   Whisper auto-transcription if no captions exist.
2. **Run agents** — Producer → parallel Scouts → Editor → Critic → Packager.
   Every stage uses prompt caching on the shared context (rubric + brief).
3. **Snap** — clip boundaries snap to word-level timestamps so cuts don't
   land mid-word.
4. **Review** — web UI shows the Episode Brief at the top of each episode
   plus every clip's full social package. Approve / reject / edit with an
   optional reason to train a taste profile that flows back into the
   Producer and Scouts on subsequent runs.
5. **Cut** — `ffmpeg` slices the source at approved timestamps. Optional
   9:16 vertical reformat and burned caption headlines.

## Install

```bash
pip install -r requirements.txt
# ffmpeg must be on PATH
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...        # only needed for Whisper fallback
```

## Two ways to drive it

**Web UI (recommended)** — open the app and drive everything from there:

```bash
clipper serve
# → http://127.0.0.1:8765
```

From the UI you can:

- **Add source** — paste YouTube URLs or upload a local video; ingest +
  transcribe + score runs as a background job with live progress.
- **Library** — browse each episode's ranked candidates with in-place video
  preview seeked to the clip range.
- **Review** — approve / reject / edit each clip, optionally with a short
  reason. Every action is captured as a feedback signal.
- **Preferences** — distill your feedback history into a "taste profile"
  that gets injected into future scoring prompts. You can edit it by hand.
- **Re-score** — re-run scoring on any episode with the current taste
  profile applied.
- **Render** — single clips or all-approved; toggle 9:16 and burned captions.
- **Jobs drawer** — persistent panel showing every running job with a live
  log stream (SSE) and progress bar.

**CLI** — the same flow, scriptable:

```bash
clipper run https://youtube.com/watch?v=EXAMPLE ~/podcasts/ep042.mp4
clipper serve                          # launch the review UI
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

## Models

Different roles run on different tiers by default:

| Role | Default model | Why |
|------|---------------|-----|
| Producer | `claude-opus-4-7` | Deep reading of the full episode, strategic synthesis |
| Scout | `claude-sonnet-4-6` | Many parallel calls; needs to be fast and cheap |
| Editor | `claude-opus-4-7` | Tight judgement on boundaries and diversity |
| Critic | `claude-opus-4-7` | Final gate, quality and representation |
| Packager | `claude-sonnet-4-6` | Fluent writing per clip, at volume |

Override per-role via the `models` kwarg on `produce_clips`, or the `--model`
CLI flag (which adjusts the Scout tier).

## YouTube tips

- Private, members-only, or age-gated videos require cookies. Export them
  from your browser to a Netscape-format `cookies.txt` and set
  `YT_COOKIES=/path/to/cookies.txt`.
- Uploader subs are preferred over auto-captions; if neither exists, Whisper
  is called automatically (needs `OPENAI_API_KEY`).
- Downloads stream their progress into the Jobs drawer in real time.

## Notes

- Whisper JSON with word-level timestamps gives the cleanest cuts.
- Long episodes are chunked into 15-minute windows with a 60 s overlap; the
  scoring rubric and episode brief are cached across windows.
- Whisper fallback extracts mono 32 kbps mp3, splits into <24 MB chunks, and
  merges word-level timings with correct offsets.
- `ffmpeg` uses stream-copy when possible (fast, lossless). Re-encoding
  kicks in only for vertical reformat or burned captions.
