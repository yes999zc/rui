# Turn latency, measured

Every number here comes from `scripts/smoke_turn.py`: one real turn through the configured
providers with no microphone and no speaker — ASR on a 1.75 s wav, LLM, TTS, reply PCM
written to a file. That isolates the vendors from the device path, so a slow turn can be
blamed on the right component instead of guessed at.

Machine: Mac Studio (arm64, 24 cores, 64 GB), Shenyang, China Unicom.
`first response` = end of speech → first PCM ready to play.

**Decisions (2026-10-21):**

- **ASR runs locally, cloud as fallback.** The cloud endpoint is not just slower, it is
  unpredictable; a local voice model is the only way to promise a first response at all.
- **TTS stays API-only** (SiliconFlow `CosyVoice2-0.5B:alex`). No local voice model — the
  voice was picked by ear, and shipping a TTS model to keep it is not worth it.
- **LLM stays API.**

## Local ASR on the NAS (Intel N100, 4 cores, fnOS)

Same 1.75 s clip, `sherpa-onnx` SenseVoice int8, warm runs after one model load:

| threads | model load | warm median | warm max | rtf |
|---|---|---|---|---|
| 1 | 1308 ms | 362 ms | 364 ms | 0.207 |
| 2 | 1322 ms | 210 ms | 213 ms | 0.120 |
| 4 | 1765 ms | 159 ms | 171 ms | 0.091 |

Transcription correct in every run (`今天天气怎么样？`). Default is `threads: 2`: 210 ms for a
command-length utterance while leaving two cores for the rest of the NAS. There is no
run-to-run spread to speak of, which is the whole point — compare the cloud row below.

## Three identical turns, SiliconFlow only (all API)

| run | ASR | LLM first token | TTS first byte | first response | reply |
|---|---|---|---|---|---|
| 1 | 384 ms | 722 ms | 2950 ms | **4056 ms** | 我需要知道你的位置才能告诉你天气呢。 |
| 2 | 2464 ms | 690 ms | 10523 ms | **13677 ms** | 建议您打开窗户看看外边，或者问下手机天气应用哦。 |
| 3 | 1204 ms | 918 ms | 2272 ms | **4394 ms** | 我不清楚天气情况，你可以再具体点问我。 |

Same wav, same key, same afternoon. The median turn is ~4.4 s to first audio; the tail is 13.7 s.

## Stage by stage

| stage | provider | measured | note |
|---|---|---|---|
| ASR | SiliconFlow `FunAudioLLM/SenseVoiceSmall` | 384 / 1204 / 2464 ms, plus single samples of 2.1 / 5.7 / 10.6 s and a **20 s timeout** | 1.75 s clip; `curl` behaves the same, so it is the endpoint, not our client |
| LLM | SiliconFlow `deepseek-ai/DeepSeek-V3` | first token 690–1439 ms, 26 chars in 1.5 s | the stable stage |
| LLM | SiliconFlow `Qwen/Qwen2.5-7B-Instruct` | first token 2436 ms | slower than V3 here |
| LLM | SiliconFlow `Qwen/Qwen3-8B` | first token 10647 ms | unusable for voice |
| TTS | SiliconFlow `CosyVoice2-0.5B:alex` (stream) | first byte 1.1 s (`curl` TTFB) to **10.5 s** in-turn | the largest and least stable line item |

Two conclusions the table supports:

1. **TTS is now the bottleneck**, and its tail is worse than its median. `curl` against
   `/audio/speech` shows 1.10 s TTFB for a 3 s sentence, but inside a turn the same request
   has taken 2.3–10.5 s to first byte.
2. **ASR needs a retry budget, not a longer timeout.** A 20 s wait is worse than two 6–8 s
   attempts; `retries` now exists per provider for exactly this.

## What the numbers already forced in the code

- Keep-alive HTTP clients per provider. Cold TLS per stage cost 200–400 ms; ASR went from
  2.1–10.6 s down to 384–2464 ms on the same clip purely from connection reuse.
- Per-provider `retries` (default 0, used on ASR) plus a chain that falls through to the next
  provider when one cannot even start.
- Clause-level TTS flush (`，` after 10 chars). Waiting for `。` keeps the speaker silent
  until the whole answer exists.
- `max_tokens` on the LLM. An unconstrained model answered the weather question with a
  six-item markdown list, 24 s of speech, emoji included.
- A spoken-reply system prompt: Chinese, one or two sentences, no lists, no markdown.
- `TurnTrace.mark` is first-write-wins. Per-sentence TTS re-marked `tts_first` on every
  sentence and the reported latency drifted to the last one.

## Open question this measurement raises

With TTS held to API-only, the 4.4 s median / 13.7 s tail will dominate the user experience.
讯飞 (keys already on this machine) has a Chinese TTS websocket endpoint with the voice 9哥
asked for by name (弯弯小何) and a typical sub-500 ms first frame; that is the obvious single
change that moves first response under 2 s. It needs a decision, not more measuring.

## Not measured yet

- Wake → end of speech (VAD trailing silence) on real speech, 20 turns.
- Barge-in silence: target ≤ 300 ms from the user talking over the speaker to the speaker
  going quiet. Needs the device and the `stop_play` path under load.
- rtF of the full chain (AEC + KWS + VAD + ASR + TTS) under an 80 % volume music bed.

## 20 rounds through the whole pipeline (no room, real vendors)

`scripts/measure_turn_pipeline.py`: a fake speaker dials the real adapter, streams the wake
word and then "今天天气怎么样" as 8 ch frames at device pace with a zero reference (a person
talking to a silent speaker), and the reply comes back as PCM frames. Everything between is
the production path: AEC → KWS → turn gate → local ASR → SiliconFlow LLM → SiliconFlow TTS →
playback. 20 consecutive rounds, same request.

```
rounds 20, wake 20/20, transcript 20/20
first response (wake -> first reply audio): p50 8713 ms, min 5641 ms, max 23541 ms
ASR (local SenseVoice)          : 467-627 ms every round
LLM first token                 : 1455-1776 ms every round
TTS first byte                  : 3685, 3851, 4276, 4479, 4508, 4678, 5804, 5918, 6334, 6361,
                                  7135, 7240, 10261, 12113, 13085, 14025, 14866, 16117, 18866, 21559
```

The shape is unambiguous: ASR and LLM are flat and predictable, **TTS first byte is the
entire spread** — 3.7 s at its best, 21.6 s at its worst, and it degrades as rounds go on,
which looks like vendor-side queueing rather than anything this code does (each request is a
single sentence over one keep-alive connection, and a `curl` against the same endpoint shows
1.1 s TTFB when the service is idle).

Consequences for the design:

- Speech recognition belongs on the box: local ASR is 467-627 ms including per-process load
  and does not move.
- A voice assistant cannot spend a median of 8.7 s before it makes a sound. SiliconFlow TTS
  as the only TTS entry is the thing to change, not the rest of the pipeline.
- Nothing in this run exercised acoustics. The wake word arrived as a clean digital stream
  with no music bed and no echo path.

## What could not be measured today

The acoustic round (wake word played into the room, 80 % volume music bed) did not run: the
Mac's default output (VX2762-4K over DDC) went silent mid-session, `afplay` began returning
`AudioQueueStart failed (0xe00002ec)`, and switching output devices did not bring it back.
Recovering it needs `coreaudiod` restarted with sudo, which this session does not have. The
mic-side evidence is in the plane: `voice_rms_db` sat at -53 dBFS while `afplay` reported
success, i.e. nothing reached the room. That is a host audio fault, not a house-ear fault,
and the 18/18 wake measurement from the hardware window stands as the acoustic data point.
