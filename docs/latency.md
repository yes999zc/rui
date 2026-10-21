# Turn latency, measured

Numbers below come from `scripts/smoke_turn.py`, which runs one real turn through the
configured providers with no microphone and no speaker: ASR on a 1.75 s wav, LLM, TTS, reply
PCM written to a file. That isolates the three vendors from the device path, so a bad turn
can be blamed on the right component.

Machine: Mac Studio (arm64, 24 cores, 64 GB). Network: Shenyang, China Unicom.
`first response` = end of speech → first PCM ready to play.

## One turn, local ASR + SiliconFlow LLM/TTS

```
transcript : 今天天气怎么样？
reply      : 我现在查不到天气信息，你得自己看一下窗外。
asr        : local-asr  520 ms   (first call: model load; warm 51–54 ms)
llm        : sf-llm     first token 1439 ms after wake
tts        : sf-tts     first byte at 4748 ms
first response: 4748 ms  (4.31 s of speech produced)
```

## Stage by stage

| stage | provider | measured | note |
|---|---|---|---|
| ASR | local sherpa SenseVoice int8 | 51–54 ms warm, 520 ms cold (rtf 0.03) | 1.75 s clip, 2 threads |
| ASR | SiliconFlow `FunAudioLLM/SenseVoiceSmall` | 460 ms, 779 ms, 2.1 s, 5.7 s, **timeout at 20 s** | same clip, same afternoon |
| LLM | SiliconFlow `deepseek-ai/DeepSeek-V3` | first token 834–1439 ms, 26 chars in 1.5 s | variance is mostly time-to-first-token |
| LLM | SiliconFlow `Qwen/Qwen2.5-7B-Instruct` | first token 2436 ms | slower than V3 here |
| LLM | SiliconFlow `Qwen/Qwen3-8B` | first token 10647 ms | unusable for voice |
| TTS | SiliconFlow `CosyVoice2-0.5B:alex` (stream) | first byte 1.10 s, 3 s of audio in 1.30 s | `curl` TTFB; non-stream total 2.3–2.7 s |

Two things this table settled:

1. **ASR belongs on the box.** The cloud endpoint returned 460 ms once and a 20 s timeout
   later for the identical clip. Local SenseVoice is 51 ms warm, deterministic, and free.
   Cloud ASR stays in the chain as a fallback, not as the default.
2. **TTS is now the largest single line item** (~1.1 s of the ~4.7 s). A local Chinese TTS
   (`matcha-icefall-zh-baker` is already on disk, `vits-melo-tts-zh_en` too) would remove
   most of it, at the cost of the `alex` voice 9哥 picked. That is a product decision, not a
   measurement one.

## What the numbers already forced in the code

- Keep-alive HTTP clients per provider instance. Cold TLS per stage cost 200–400 ms; ASR went
  from 2.1–10.6 s to 460–780 ms on the same clip purely from connection reuse.
- Clause-level TTS flush (`，` after 10 chars). Waiting for `。` means the speaker stays silent
  until the whole answer exists.
- `max_tokens` on the LLM. An unconstrained model answered the weather question with a
  six-item markdown list, 24 s of speech, emoji included.
- A spoken-reply system prompt: Chinese, one or two sentences, no lists, no markdown.
- `TurnTrace.mark` is first-write-wins. Per-sentence TTS re-marked `tts_first` on every
  sentence and the reported latency drifted to the last one.

## Not measured yet

- Wake → end of speech (VAD trailing silence) on real speech, 20 turns.
- Barge-in silence: target ≤ 300 ms from the user talking over the speaker to the speaker
  going quiet. Needs the device, and it needs the `stop_play` path exercised under load.
- rtF of the full chain (AEC + KWS + VAD + ASR + TTS) under an 80 % volume music bed.
