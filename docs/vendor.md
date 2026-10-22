# Yielding to 小爱

The speaker still runs its own assistant. "小爱同学" wakes the vendor, it answers, and our
KWS can fire on the same audio - two assistants, one sentence. The old project hit this on
2026-10-21 (`[dev] vendor wake (RecognizeResult) -> barge` followed by our own `[asr]` turn)
and fixed it by yielding; house-ear keeps that rule.

## The rule

| event | what we do |
|---|---|
| vendor wake (final recognition) | yield: no session, no cue, open session goes quiet without a farewell, window opens |
| any utterance inside the window | dropped, counted, logged |
| our own wake word | window cleared at once: "你好小瑞" takes over immediately |
| vendor `StopCapture` | ignored: it is the dialog ending and would re-trigger us mid-session |

The window is `session.vendor_quiet_s` (default 12 s, the old build's value).

## How it travels

```
client (fork)  --{"Event": {"event": "instruction", ...}}-->  xiaoai adapter
         parse_vendor_signal() -> VendorSignal(kind="wake", text=...)
                 |
                 v
  audio plane  --VendorSignalMsg over IPC-->  control plane
                 |                                  |
         (audio path untouched)          session/vendor.VendorWindow
                                         muted() gates every turn
```

The audio plane only relays. The decision belongs to the session, because that is the only
layer that knows whether a turn is open.

## Verifying on the device

The offline half is covered by `tests/test_vendor.py` (four frame shapes, partial vs final,
StopCapture, the window and the counters). The device half needs the real window:

1. switch the speaker to house-ear per `HANDOFF.md` §8 (port 4412, restore to 4399 after);
2. say **小爱同学，今天天气怎么样** once;
3. expected: one answer (the vendor's), a `xiaoai: vendor wake (...), we yield` line, and
   **no** `[asr]` / `turn:` line of ours;
4. then say **你好小瑞，现在几点** within the window: our answer must arrive immediately,
   proving the window did not become a lockout.

`GET /health` reports `vendor.vendor_wakes`, `vendor.yielded_turns` and `vendor.muted_for`,
so a silent speaker can be explained without reading logs.

## Verified on the device (2026-10-22 08:33–08:36)

First real-device run of the yield path, LX06 on `:4412`, restored to `:4399` afterwards.

```
08:33:57  xiaoai: vendor wake ('今天天气怎么样'), we yield
          → no [asr], no turn: the vendor answered alone
08:34:16  kws: wake: 你好小瑞     ← our wake word, inside the 12 s window, took over at once
GET /health → vendor_wakes=1, yielded_turns=0
```

`yielded_turns=0` is the correct reading here, not a miss: no session of ours was open
when the vendor woke, so there was nothing to mute. What matters is the pair of lines
above - the vendor's dialog was left alone, and our own wake word was not blocked by the
open window.

Two things the same run found, both outside this document:

- 「嗯。」 after the wake word became a whole turn (`reply: 嗯，你说。`). A 200 ms minimum
  speech length is not enough to keep a noise from becoming a request; that belongs with
  S5c's session rules.
- The vendor's own answer was not transcribed by us: the first `[asr]` line comes 20 s
  later, after our wake word. The AEC kept the room's audio separate from the vendor's
  speech well enough for that.

## Takeover: "你好小瑞" while 小爱 is answering (closed 2026-10-22 19:30)

The scenario 9哥 tested: say 小爱同学 and ask a question, 小爱 starts answering, then say
你好小瑞. Expected: one answer at a time. 小爱's dialog is left behind and the line is ours.

What makes it work, in the order a signal arrives:

| layer | mechanism | commit |
|---|---|---|
| protocol | `StartCapture` counts as a vendor wake. It arrives before the final `RecognizeResult`, which was too late: both assistants answered one sentence (18:51) | `2d0134c` |
| control plane | `on_vendor`: a vendor wake aborts our synthesis (`runner.abort()`), sends `StopAudio`, turns the light off and opens the 12 s window | `6d746b1` |
| control plane | text backstop: a transcript containing 小爱同学 yields too, for client builds that miss `StartCapture` | `b3da6ec` (call fixed) |
| control plane | `on_wake` → `VendorWindow.ours_spoke()`: our wake word clears the window, the same as the old build's `VENDOR["quiet_until"] = 0.0` (`assistant_v3.py:3288`) | — |

Device log (`he-serve-b2n.log`, 9哥 on the speaker):

```
19:30:15.279  xiaoai: vendor wake ('今天星期几'), we yield
19:30:15.569  event playing Playing          ← 小爱 answers
19:30:17.423  kws: wake: 你好小瑞            ← window cleared
19:30:18.922  event playing Idle
19:30:23.284  turn: {'transcript': ..., 'reply': ...}   ← one answer, ours
```

Replay it without anyone at the speaker (2026-10-22 19:48, automated): wake the vendor
over SSH with `ubus call pnshelper event_notify '{"src":1,"event":0}'` (the client's
`wake_up` helper, `speaker.rs:152`) — old project's `devshell.py` carries the credentials.
Expect, within ~10 s: `vendor wake (...), we yield` → `led: off` → a `stop_play` in the
device log → vendor `Playing` then `Idle`, and **no** `turn:` of ours in between;
`/health` `vendor_wakes` increments by one.

### Known gaps (each has a debugged answer in the old build)
| # | gap | evidence | old build |
|---|---|---|---|
| V1 | We do not actively stop 小爱 when 你好小瑞 fires. Today it finishes its sentence because its replies are short | 19:30 log: vendor `Idle` 1.5 s after our wake | `vendor_stop_player()` `assistant_v3.py:2248`: `ubus call mediaplayer player_play_operation '{"action":"pause","media":"common"}'`, only when `mphelper mute_stat` is 1/3 (`player_stop` does not exist, rc=3). The heavier route is `abort_xiaoai()` (client `speaker.rs:143`, `/etc/init.d/mico_aivs_lab restart`) |
| V2 | The vendor's recognizer sometimes transcribes **our** wake word. `RecognizeResult('你好小瑞')` then opens the window and drops our own turn | 19:03:40 `vendor wake ('你好小瑞')` → 19:03:46 `dropping our turn` | Ignore a vendor final text that contains one of `runner.wake_words` |
| V3 | While the vendor player is playing, our gate still listens and can transcribe 小爱's voice as a request | 18:56:55 our turn transcribed `到17摄氏度，南方向。` (the vendor's weather answer) | Mic gated while `DEVICE["playing"] == "playing"` and fresh (<90 s): `assistant_v3.py:3113-3116`; our wake clears the gate |
| V4 | Music handoff. A vendor wake does not pause our song; nothing gives it back | No music path yet (D1) | `vendor_take_music()` `:2171` (pause + `resume=True`), `resume_music_after_vendor()` `:2154` on Playing→Idle, `vendor_silence_watchdog()` `:2187` 4 s for a mis-wake; pause/resume/stop words in the vendor text; `test_vendor_handoff.py`, `test_vendor_stop.py` |
| V5 | Hardware play key (114) starts the vendor's Mi Music list on top of ours | Old-build measurement 2026-10-19 | `vendor_stop_player()` before and just after our play starts |

V1–V3 belong together: they are the same "vendor is playing" state, read from the
`playing` event that `PlayingMonitor` already reports. V4–V5 land with music (D1).

## Not done yet

- The old build also compared a transcript against the acknowledgement text
  (`_is_ack_echo`) to catch a cue that its own VAD segmented late. Text-level dedupe is not
  ported yet. `SpokenLog.is_echo` covers our replies, not the cue.
