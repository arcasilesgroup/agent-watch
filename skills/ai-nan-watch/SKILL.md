---
name: ai-nan-watch
description: Run the /watch video-analysis pipeline against the NaN Builders cluster (api.nan.builders) — whisper-large-v3 for transcription and qwen3.6 (multimodal) as the analyzing model. Use whenever /watch is invoked from a session whose provider is nan (omp, pi, OpenCode, Zed configured with baseURL https://api.nan.builders/v1), whenever NAN_API_KEY exists, whenever the user mentions nan.builders together with videos or transcription, or whenever a watch run hits Whisper 429/524/25MB errors. This is the NaN adapter; for other providers add a sibling skill (e.g. ai-groq-watch) and leave the core `watch` skill untouched. Covers provider gotchas (base URL, model ids, User-Agent), rate-limit pacing, the audio chunking strategy, frame token budgets for qwen3.6's 256K context, and the workflow for very long videos.
---

# /watch on NaN Builders (provider adapter for the `watch` skill)

`watch` (the sibling skill in this repo) downloads a video, extracts frames with ffmpeg, transcribes audio, and then **the agent running the skill** reads the frames and transcript itself. When that agent is a NaN model, the pipeline needs exactly two things: the NaN Whisper backend for transcription fallbacks, and knowledge of the cluster's limits so runs don't die halfway.

Everything below was measured against the live API, not copied from a pricing page.

## Preflight (once per session)

```bash
python3 <this-skill-dir>/scripts/preflight.py
```

JSON snapshot: key resolvable + source, models the account can call, whether the installed `watch` scripts carry the `nan` backend. Exit 0 = go. If `key_source: null`, the user needs a key from https://cloud.nan.builders/ (user settings → API Keys) — ask before continuing; caption-full videos still work with `--no-whisper`.

## The three facts that break naive integrations

| Wrong | Right | Why |
|---|---|---|
| Base URL `https://nan.builders/v1` | `https://api.nan.builders/v1` | The marketing domain has no API; 404. LiteLLM sits on `api.` |
| Whisper model id `whisper-large-v3` (Groq's name) | `whisper` | The cluster registers large-v3 under the bare id; the Groq name 404s |
| Default `Python-urllib/x` User-Agent | any custom UA | Cloudflare WAF: 403 before auth. `watch`'s `whisper.py` already sends a custom UA |

Pointing your agent at NaN generally (not just watch): any tool that accepts base URL + API key works — see https://nan.builders/docs/getting-started.

## Using it through the watch scripts

`scripts/whisper.py` prefers `GROQ_API_KEY → OPENAI_API_KEY → NAN_API_KEY` when auto-selecting; force NaN for determinism:

```bash
python3 scripts/watch.py "$URL" --whisper nan --intent "..."
```

When the audio exceeds what the cluster's whisper box accepts (25 MB upload, long-audio proxy timeouts), the client splits it into ≤110 s chunks, uploads paced ≥6.5 s apart (the 10 rpm cap), and shifts every segment/word timestamp back onto the original timeline. The report consumer never sees the seams — but expect stderr like `[watch] nan whisper chunk 3/40…`.

## Rate limits — which ones actually bite

Documented global: **60 rpm, 5 concurrent, 1.5M tpm** per chat model. Whisper box: **10 rpm, 25 MB per upload**.

A normal `/watch` run is 1–2 whisper calls and 0 chat-API calls (frames go into the running agent's own session). Measured: multi-request bursts route fine; a 2-min clip transcribes in ~3 s; the 5-concurrent cap is never approached by a single watch run.

The limits only matter when:

- **no captions + long video** → see "Long videos";
- **batched watch runs** → keep whisper uploads ≤10/min total (the rpm guard handles one process; don't run several caption-less watches in parallel).

## qwen3.6 as the watcher — token budget

qwen3.6: 256K context, multimodal (images), XML tool calling. Measured on real pipeline frames: **144 image-tokens per 512px-wide frame** (~4× at 1024px). The default scan is ≤100 frames → ~14.4K image tokens + a few K of transcript: under 10% of context.

Practical rules:

- Fill the `report.md` markers in one pass for ≤10-min videos; split section-by-section for longer ones.
- Pure transcript reasoning over a very long VTT (~100K+ tokens) fits better in `deepseek-v4-flash` (1M context) — switch session models rather than forcing the flagship to summarize 13 hours.
- Never point a frame-reading pass at a text-only model. On NaN, `mimo-v2.5` also accepts audio and images natively.

## Long videos (the multi-hour question)

Each stage scales differently — keep them separate:

1. **Transcript.** If yt-dlp returns a caption track (most public YouTube, even hours-long streams), it's free: 0 whisper calls, one VTT. This is the only sane path for multi-hour content.
2. **No captions + multi-hour.** Possible but ugly: 64 kbps mono ≈ 0.48 MB/min → 13 h ≈ 374 MB ≈ 80+ chunked uploads at 10 rpm ≈ 40+ minutes of API time. Use `--start`/`--end` to bound the window you actually need (≤50 min stays under one 25 MB upload).
3. **Frames.** The budget caps at 100 regardless — by design, coverage over a 13-hour scan would be one frame per ~8 minutes. Denser visual coverage = focused re-runs on the 2–3 windows that matter, not more frames everywhere.
4. **Context.** 100 frames + a full transcript is nothing for 256K. If denser coverage is wanted, up to ~300–400 frames @512px still fits with margin for the report.

## Error decoding

- `524` on whisper → long audio; the script chunks automatically. Still 524s on a chunk → lower `WATCH_NAN_CHUNK_SECONDS` (e.g. 60).
- `429` → 10 rpm whisper cap or 60 rpm global; the script retries with `Retry-After`/backoff twice, then fails loudly. Space runs out manually for a minute.
- `403` on any call → missing custom User-Agent, or you pasted the key into the wrong host.
- Model not in `/v1/models` for your key → tier-gated (e.g. premium models); check `https://nan.builders/docs/models`.

## Privacy

Same guarantees as the base watch skill: only the extracted audio clip leaves the machine toward `api.nan.builders`, only when captions are missing and `--no-whisper` wasn't passed. Frames are read locally. The key travels only to `api.nan.builders`. Keep your key in `~/.config/watch/.env` (0600) or point `WATCH_NAN_KEY_FILE` at wherever you store it — never commit it.
