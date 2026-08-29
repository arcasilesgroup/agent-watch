# Whisper providers

`/watch` transcribes caption-less audio through any **OpenAI-compatible** `POST /v1/audio/transcriptions` endpoint. Three presets ship built in; you can register your own or override a preset's fields without touching code.

Built-in presets (resolution order when nothing is forced: `groq → openai → nan`):

| Backend | Model id | Endpoint | Notes |
|---|---|---|---|
| `groq` | `whisper-large-v3` | `api.groq.com/openai/v1` | Preferred default: cheap, fast |
| `openai` | `whisper-1` | `api.openai.com/v1` | Standard pricing |
| `nan` | `whisper` | `api.nan.builders/v1` | Community cluster, free w/ membership. Client enforces 10 rpm pacing + 25 MB cap via auto-chunking |

## Configuration

All of it reads from the environment or `~/.config/watch/.env` (create it with `python3 scripts/setup.py`, chmod 600).

### 1. Pick a preset

```bash
export GROQ_API_KEY=...      # or OPENAI_API_KEY / NAN_API_KEY
python3 scripts/watch.py "$URL" --whisper groq
```

### 2. Override any preset field

Prefix `WATCH_<NAME>_`, uppercase backend name:

```bash
WATCH_NAN_MODEL=whisper-large-v3        # change the model id sent
WATCH_NAN_ENDPOINT=https://gpu.local:8443/v1/audio/transcriptions
WATCH_NAN_CHUNK_SECONDS=60              # smaller pieces for a slower box
WATCH_NAN_MAX_UPLOAD_MB=10              # tighter upload cap
WATCH_NAN_MIN_INTERVAL_S=12             # slower pacing (lower rpm)
```

### 3. Read the key from a file instead of an env var

For machines where secrets live in files (agent managers, keychain exports, mounted secret stores) — the `.env` then holds a *path*, never a secret:

```bash
WATCH_NAN_KEY_FILE=/run/secrets/nan_key
```

The path is also readable from inside `~/.config/watch/.env`. Key files group/world-readable trigger a stderr warning; keep them `chmod 600`.

### 4. Register your own provider

```bash
export WATCH_EXTRA_PROVIDERS=localbox,gateway
export WATCH_LOCALBOX_ENDPOINT=http://127.0.0.1:11434/v1/audio/transcriptions
export WATCH_LOCALBOX_MODEL=whisper-large-v3-turbo
export WATCH_LOCALBOX_AUTH=none            # local servers often need no key

export WATCH_GATEWAY_ENDPOINT=https://llm.corp.example/audio/transcriptions
export WATCH_GATEWAY_MODEL=whisper-corp
export GATEWAY_API_KEY=...
export WATCH_GATEWAY_MAX_UPLOAD_MB=50      # what the gateway accepts

python3 scripts/watch.py "$URL" --whisper localbox
```

| Var (`<NAME>` = your id, uppercased) | Required | Meaning |
|---|---|---|
| `WATCH_<NAME>_ENDPOINT` | ✅ | Full `/audio/transcriptions` URL |
| `WATCH_<NAME>_MODEL` | ✅ | Model id in the request body |
| `<NAME>_API_KEY` | if auth needed | Sent as `Authorization: Bearer …` |
| `WATCH_<NAME>_AUTH=none` | | Sends a placeholder key (auth-less local servers) |
| `WATCH_<NAME>_KEY_FILE` | | Read key from this file (0600) |
| `WATCH_<NAME>_MAX_UPLOAD_MB` | | Above this size → auto-chunk |
| `WATCH_<NAME>_CHUNK_SECONDS` | | Force chunking for audio longer than this (proxy timeouts) |
| `WATCH_<NAME>_MIN_INTERVAL_S` | | Minimum spacing between uploads (rpm caps) |

Works with: Ollama, faster-whisper servers, whisper.cpp, vLLM, Hugging Face Inference, ElevenLabs Scribe (if they expose the OpenAI shape — `response_format=verbose_json` + optional word timestamps is the contract; a provider that omits `segments` still yields a whole-audio transcript via the `text` fallback).

### 5. Disable Whisper entirely

`--no-whisper` — frames-only for caption-less sources. Most public YouTube videos ship auto-captions, so this rarely costs anything.

## Choosing well

- **Speed/price on the open internet:** Groq.
- **Already pay for OpenAI:** its preset is one key away.
- **Community/edge hosting (free-ish):** NaN — budget for its 10 rpm whisper cap on long, caption-less audio; for anything with captions it's irrelevant.
- **Air-gapped / privacy-sensitive:** a local faster-whisper or Ollama box with `AUTH=none`; the audio never leaves your LAN.
- **Corporate gateway:** register it as `<NAME>` above and point `WATCH_<NAME>_KEY_FILE` at the mounted secret.

The retry policy (429 backoff via `Retry-After`, transient-network retries, no retry on 4xx) is shared across all providers.
