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

## Not done yet

- The window does not pause our music (the old build paused the song for a vendor dialog so
  the vendor's own AEC could hear the user). house-ear has no music path yet; when S5 adds
  one, the vendor wake must pause it.
- The old build also compared a transcript against the acknowledgement text
  (`_is_ack_echo`) to catch a cue that its own VAD segmented late. Text-level dedupe is not
  ported yet: it belongs with S5b, where turn quality is already being fixed.
