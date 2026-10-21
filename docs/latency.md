# Turn latency, measured

Every number here comes from `scripts/smoke_turn.py`: one real turn through the configured
providers with no microphone and no speaker — ASR on a 1.75 s wav, LLM, TTS, reply PCM
written to a file. That isolates the vendors from the device path, so a slow turn can be
blamed on the right component instead of guessed at.

Machine: Mac Studio (arm64, 24 cores, 64 GB), Shenyang, China Unicom.
`first response` = end of speech → first PCM ready to play.

**Decision: all stages are API calls.** No local ASR, no local TTS — a NAS deployment is
this container plus a key, and the same config works on any box.

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
