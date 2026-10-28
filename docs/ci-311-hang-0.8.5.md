# CI `test (3.11)` 死锁：根因、修复与判定口径（0.8.5）

> 状态：**已修复**。0.8.5 发布阻塞项 G：`ci.yml` 的 `test (3.11)` 自 `563c139c`（0.8.3 的功能提交）
> 起每次 push 都永不结束，GitHub 上长期 `in_progress`；`test (3.12)` 与 `image` 一直绿。
> 根因是 **CPython 3.11 的 `asyncio.wait_for` 取消丢失**（3.12 用 `asyncio.timeout` 重写修掉了：
> [python/cpython#96764](https://github.com/python/cpython/issues/96764) / [PR #98518](https://github.com/python/cpython/pull/98518)，随 3.12 发布、未回移 3.11），触发点是 `_speak_loop`
> 里 `wait_for(queue.get(), 0.5)` + 生产 shutdown 的 `task.cancel()`；修复是把这一处改用
> 3.11 原生的 `asyncio.timeout`。测试代码**一行未改**（覆盖不削弱），`src` 改动 9 行。
>
> 修改文件：`src/rui/audio/process.py`（`_speak_loop`，唯一行为改动）、
> `.github/workflows/ci.yml`（job 级 `timeout-minutes`，防再挂）。

## 1. 症状与 CI 证据

最后一条绿 run 与第一条挂的 run 紧邻，窗口正好是新增 barge 用例的提交：

| run | commit | 事件 | `test (3.11)` | `test (3.12)` | `image` |
|---|---|---|---|---|---|
| [40688751816](https://github.com/yes999zc/rui/actions/runs/40688751816) | `54c6c3d7` | push main | **success** 12:33:18Z→12:37:52Z（4 m34 s） | success 12:33:17Z→12:38:42Z（5 m25 s） | success |
| [40688819552](https://github.com/yes999zc/rui/actions/runs/40688819552) | `563c139c` | push main | **in_progress** 12:33:48Z→（检查时 2 h35 m 仍无结论） | success 4 m39 s | success |
| [40704875816](https://github.com/yes999zc/rui/actions/runs/40704875816) | `1a2e680` | push main | **in_progress** 14:20:02Z→（检查时 48 m 仍无结论） | success 4 m46 s | success |

`gh run view 40688819552 --json jobs` 显示挂点精确到步骤：

```
test (3.11) | in_progress
  - Run uv run ruff check src tests scripts/coverage_report.py           completed success
  - Run uv run python -V | tee python-version.txt                        completed success
  - Run uv run pytest --cov=rui --cov-branch ... --junit-xml=...         in_progress   <- 永远停在这
  - Run uv run rui plug-test ...                                         pending
  - Run actions/upload-artifact@v4                                       pending
```

该 run 之前 12:33:15Z 的那条（`54c6c3d7`）三个 job 全 success；`563c139c` 只多了一个用例
`tests/test_segment_silence.py::test_a_barge_does_not_leave_segments_for_the_next_reply`。

## 2. 本地复现（3.11，独立环境）

约束：**不动仓库 `.venv`**，3.11 用 `UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311`。

### 2.1 单文件 + 挂起看门狗（拿栈）

```bash
cd <repo>
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv run --python 3.11 --extra dev python - <<'PY'
import faulthandler, sys
# 必须写到独立 file：pytest 默认 --capture=fd 会把 faulthandler 的 stderr 一起捕获，
# 进程 _exit(1) 时来不及回放，第一次复现因此只看到 4 个点、没有栈。
faulthandler.dump_traceback_later(40, exit=True, file=open('/tmp/fh-trace311.log', 'w'))
import pytest
sys.exit(pytest.main(['-q', '-s', 'tests/test_segment_silence.py']))
PY
```

输出：`....` 后卡死（4 个点 = 文件里第 1–4 个用例通过，**第 5 个就是 `563c139c` 新增的 barge 用例**），
40 s 后 `/tmp/fh-trace311.log`：

```
Timeout (0:00:40)!
Thread 0x00000001ec5f3f80 (most recent call first):
  File ".../python3.11/selectors.py", line 566 in select
  File ".../python3.11/asyncio/base_events.py", line 1898 in _run_once
  File ".../python3.11/asyncio/base_events.py", line 608 in run_forever
  File ".../python3.11/asyncio/runners.py", line 118 in run
  File ".../tests/test_segment_silence.py", line 248 in test_a_barge_does_not_leave_segments_for_the_next_reply
  File ".../_pytest/python.py", line 167 in pytest_pyfunc_call
  ...
```

第 248 行就是 `metrics = asyncio.run(scenario())`：事件循环还活着、`select` 空闲等待，说明**不是死循环**，
而是 `scenario()` 在等一个永远不会完成的 await。

### 2.2 dump asyncio 任务态（定位到具体 await）

用 `/tmp/dbg311.py` 把测试体单独跑起来、8 s 后从 loop 内 dump `asyncio.all_tasks()`：

```
task count: 3
Task-1 scenario()  test_segment_silence.py:246  await task    wait_for=<Task cancelling Task-2>
Task-2 _speak_loop()  [cancelling]  src/rui/audio/process.py:2831
        chunk = await asyncio.wait_for(self._speak_queue.get(), timeout=0.5)
        wait_for=<Future pending cb=[Task.task_wakeup()]>
Task-24 Queue.get()  [pending]  asyncio/queues.py:158  await getter
```

即：`finally: task.cancel(); await task` 里，**该 Task 永远停在 `cancelling` 状态**，
`await task` 永不返回 → pytest 永不结束 → job 永远 `in_progress`。

### 2.3 无 rui 依赖的最小复现（证明是 CPython 行为，不是 rui 逻辑）

`/tmp/waitfor_repro.py`（stdlib only，25 行）：

```python
async def worker(q):
    while True:
        try:
            await asyncio.wait_for(q.get(), timeout=0.5)
        except TimeoutError:
            continue

task = asyncio.create_task(worker(q))
await asyncio.sleep(0.05)   # 让它停在 wait_for(queue.get()) 里
q.put_nowait(b"x")          # 一个 chunk 到达……
await asyncio.sleep(0)      # ……与 cancel 落在同一个 event-loop tick
task.cancel()
await asyncio.wait_for(task, timeout=3.0)
```

| 解释器 | `wait_for(q.get(), 0.5)` | `async with asyncio.timeout(0.5): await q.get()` |
|---|---|---|
| CPython **3.11.14** | `HANG: task still cancelling after 3 s` | `cancelled cleanly` |
| CPython **3.12.14 / 3.12.15** | `cancelled cleanly` | `cancelled cleanly` |

再看任务内部状态（`/tmp/waitfor_mechanism.py`）：

```
3.11.14                                   3.12.15
  [main] cancel() -> True                   [main] cancel() -> True
  [worker] get #1 returned an item   <-            (取消被投递，没有"返回结果")
  [main] done=False cancelled=False          [main] done=True cancelled=True
         cancelling=1                               cancelling=1
  [main] <Task cancelling ... wait_for=<Future pending cb=[Task.task_wakeup()]>>
```

3.11 上取消被**吞掉**了：worker 正常拿到结果并继续跑（僵尸循环），`await task` 自然永远等不到。

## 3. 根因

### 3.1 CPython 3.11 的 `wait_for` 会吞掉"同一 tick"的取消（3.12 重写后消失）

3.11 的 `asyncio/tasks.py::wait_for` 是旧实现：裸 `waiter` future + `ensure_future` 内层 task +
`_cancel_and_wait`。它的取消分支是：

```python
    try:
        await waiter
    except exceptions.CancelledError:
        if fut.done():
            return fut.result()      # <- 内层已完成时取消被直接吞掉（3.12 重写前的旧实现）
        else:
            fut.remove_done_callback(cb)
            await _cancel_and_wait(fut, loop=loop)
            raise
```

`queue.put_nowait()` 会立刻 set 掉 `Queue.get()` 的 getter future（唤醒内层 task）。当这一下与
调用方 `task.cancel()` 落在**同一个 event-loop tick** 时，`fut.done()` 已经为真，取消被上面那行
`return fut.result()` 吃掉：调用方 Task 的 `cancelling()` 变成 1，却再也收不到 `CancelledError`，
于是继续在 `while` 里跑成僵尸。此时：

* 测试的 `finally: await task` → 永不返回；
* 生产 `handle()` 的 `finally`（`src/rui/audio/process.py:3934-3942`：`_stop.set()` 3935、`task.cancel()` 3938、`await task` 3941）→ 连接收尾永不完成。

3.12 把 `wait_for` 重写成：

```python
    async with timeouts.timeout(timeout):
        return await fut
```

没有内层 task/waiter 的取消转发，取消直接投递到被等待的 coroutine，正常传播（上面的任务态
`done=True cancelled=True`）。这次重写就是 [python/cpython#96764](https://github.com/python/cpython/issues/96764)
（标题 "Rewrite `asyncio.wait_for` using `asyncio.timeout`"）/ [PR #98518](https://github.com/python/cpython/pull/98518)（2023-02-16 合入 main，**随 3.12 发布**）；
**该重写没有回移到 3.11**，所以同一份代码 3.12 绿、3.11 挂。上游同样结论：
[psycopg #1413](https://github.com/psycopg/psycopg/issues/1413) 的原话是 3.11 上
"replace `wait_for` with a `Timeout()` + plain await"——本机复现（§2.3）是首要旁证。

### 3.2 为什么是 `563c139c` 这个提交开始挂

挂的用例是该提交新增的 `test_a_barge_does_not_leave_segments_for_the_next_reply`，它的收尾
（与生产 `handle()` 的 finally 同一 pattern）是：

```python
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
```

`563c139c` 让 `_flush_speech()` 追加 `self._reply_open = False`，于是清队后下一批 chunk 会**重新开段**
（走 `_track_segment_chunk` 的 `if not self._reply_open: self._reset_reply_segments()` 分支），
用例随后的 `_enqueue(...)` + `task.cancel()` 恰好把"`put_nowait` 唤醒 getter"和"cancel"压到同一 tick。
该文件前 4 个用例不在 `wait_for` 的 getter 上收尾，所以一直是绿的。

这是 event-loop tick 级竞态，但在这个用例的形状下 3.11 上**每次都能命中**：`HEAD` 干净副本上
连跑 6 次，6 次都在第 5 个用例挂死（§5.1 的 A/B 表）；CI 上 0.8.3、0.8.4 的 run 也全都停在同一处。

### 3.3 为什么修 `src` 而不是修测试收尾

把测试的 `task.cancel()` 换成"优雅停"（例如 `plane._stop.set()` 后等 loop 自己退）能让测试变绿，
但**生产 `handle()` 的 finally 用的就是这个 cancel pattern**：3.11 上它同样可能永久挂住一条连接
（handler 泄漏、关停被拖死）。改测试等于绕开生产真实路径，属于削弱覆盖。

## 4. 修复

`src/rui/audio/process.py::AudioPlane._speak_loop`（唯一行为改动，9 行含注释）：

```python
         while not self._stop.is_set():
             try:
-                chunk = await asyncio.wait_for(self._speak_queue.get(), timeout=0.5)
+                async with asyncio.timeout(0.5):
+                    chunk = await self._speak_queue.get()
             except TimeoutError:
```

* 行为等价：同一个 0.5 s 超时、同一个 `TimeoutError`（`except TimeoutError` 分支不用改）；
* `asyncio.timeout` 是 **3.11 起**的 stdlib（`pyproject.toml` 已 `requires-python >=3.11`），
  **不加依赖、不加版本分支、不改 pyproject**；
* 测试代码一行未改：`tests/test_segment_silence.py` 原样保留，继续钉住 segment ledger 与 barge 语义。

`.github/workflows/ci.yml`（防再挂，1 个 key + 注释）：`test` job 加 `timeout-minutes: 20`。
理由是这次事故的失败形态是"`in_progress` 一直不结束"（GitHub job 默认上限 360 min），
tag 门禁无法区分"慢"和"挂"；一条绿腿实测约 5 min（3.12 leg 12:33:48Z→12:38:27Z），
20 min 有 4 倍余量，真挂时能快速变红而不是再僵尸 6 小时。

## 5. 验证

环境隔离：3.11 用 `/tmp/rui-venv311`、3.12 用 `/tmp/rui-venv312`（`UV_PROJECT_ENVIRONMENT`），
**未使用仓库 `.venv`**。HEAD = `1a2e680`，工作区含其他 agent 的未提交改动（reminder/turn/volume 等），
本次只改 `src/rui/audio/process.py` 与 `ci.yml`。

### 5.1 只改 `process.py` 的 A/B：修复前 6/6 挂，修复后 3/3 绿

为了把"绿"归因到这一行改动（工作区里还有别的 agent 的未提交改动），在 `HEAD` 的**干净副本**上做 A/B：

```bash
rm -rf /tmp/hangcheck && mkdir -p /tmp/hangcheck
cd <repo> && git archive HEAD | tar -x -C /tmp/hangcheck   # HEAD = 1a2e680（修复前）
cd /tmp/hangcheck

run() {  # 45 s 看门狗；挂死则以 rc=1 退出并留下 /tmp/hangcheck/fh-*.log
  /tmp/rui-venv311/bin/python -c "
import faulthandler, pytest
faulthandler.dump_traceback_later(45, exit=True, file=open('/tmp/hangcheck/fh-$1.log','w'))
print('RESULT rc=%d' % pytest.main(['-q','-p','no:cacheprovider','tests/test_segment_silence.py']))
"
}

for i in 1 2 3 4 5 6; do run "pre-$i"; done     # 复制品里的 rui 来自副本 src（见 dump 路径）
cp <repo>/src/rui/audio/process.py src/rui/audio/process.py   # 只换这一个文件
for i in 1 2 3; do run "post-$i"; done
```

| 代码 | 次数 | 结果 |
|---|---|---|
| `HEAD` 干净副本（`wait_for` 原样） | 6 | **6/6 挂**：每次只出 `....`，`/tmp/hangcheck/fh-1..6.log` 全部 `Timeout (0:00:45)!` 且栈落在 `test_segment_silence.py", line 248 in test_a_barge_does_not_leave_segments_for_the_next_reply` |
| 同一副本，仅替换 `src/rui/audio/process.py` | 3 | **3/3 绿**：`..........  [100%]` / `RESULT rc=0` |

（副本里 `rui` 确实来自副本 `src`：dump 的栈路径是 `/private/tmp/hangcheck/src/rui/audio/process.py`。
所以这个 A/B 只差 `_speak_loop` 那一处。）

### 5.2 修复后的工作树：单文件 5/5 + 全量两端绿

```bash
for i in 1 2 3 4 5; do
  UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv run --python 3.11 --extra dev \
    pytest -q tests/test_segment_silence.py
done
# 5 次都是：..........  [100%]
```

焦点回归（speak loop + barge 相关 9 个文件）两端各一次：`82 passed`（3.11 = 15.14 s，3.12 = 15.33 s）。

### 5.3 Python 3.11 全量

```bash
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv run --python 3.11 --extra dev \
  python -c "import faulthandler,sys; faulthandler.dump_traceback_later(1800,exit=True,file=open('/tmp/ci311-fh.log','w')); import pytest; sys.exit(pytest.main(['-q','-o','addopts=']))" \
  > /tmp/ci311-full2.log 2>&1
```

```
1969 passed, 6 skipped in 144.06s (0:02:24)
PYTEST EXIT 0
```

（完整输出：`/tmp/ci311-full2.log`；`pytest --collect-only -o addopts= -q` = 1975 tests = 1969 + 6，一致。
另有一次更早的 `-qq` 运行同样退出码 0、146.7 s：`/tmp/ci311-full.log`——那一次树上有别的 agent
未完成的一处测试改动，跳过数是 1；两次都绿。）

### 5.4 Python 3.12 全量

```bash
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv312 uv run --python 3.12 --extra dev \
  python -c "import faulthandler,sys; faulthandler.dump_traceback_later(1800,exit=True,file=open('/tmp/ci312-fh.log','w')); import pytest; sys.exit(pytest.main(['-q','-o','addopts=']))" \
  > /tmp/ci312-full.log 2>&1
```

```
1969 passed, 6 skipped in 144.77s (0:02:24)
PYTEST EXIT 0
```

（两条腿都是 1975 collected = 1969 passed + 6 skipped；3.12 与 3.11 结果一致。）

### 5.5 ruff（两端环境）

```bash
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv run --python 3.11 --extra dev ruff check src tests scripts/coverage_report.py
# All checks passed!
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv312 uv run --python 3.12 --extra dev ruff check src tests scripts/coverage_report.py
# All checks passed!
```

## 6. 「tag 上四 workflow 绿」判定口径

0.8.5 门禁要求"tag 上四 workflow 绿"，四个 workflow 是 `ci` / `security` / `secret-scan` /
`docker-publish`（与 `docs/HANDOFF.md` 的 0.7.4、0.7.9 收尾口径一致）。它们的触发器**不一样**，
所以"tag 上绿"要按 SHA 合并判定：

| workflow | 触发 | 判定用的 run |
|---|---|---|
| `ci` | `push` branches `main`、`pull_request`（**没有 tag 触发**） | tag 指向的 commit 的 **main push run** |
| `security` | 所有 push + `tags: v*`、PR、周定时 | tag push run（另有 main push run） |
| `secret-scan` | 所有 push + PR | tag push run |
| `docker-publish` | `push` branches `main` + `tags: v*` | **main push run 与 tag push run 都要 success**（0.7.4 口径原文：「main + tag 两个 publish run 均 success」） |

判定命令（tag 打完、Release 之前跑）：

```bash
SHA=$(git rev-parse v0.8.5^{commit})
gh run list --commit "$SHA" --limit 20 \
  --json databaseId,workflowName,event,headBranch,status,conclusion,createdAt
# 四个 workflow 名各自的所有 run 都必须 status=completed 且 conclusion=success
```

三条硬口径：

1. **`in_progress` 不算绿**。本次事故的失败形态就是 `test (3.11)` 永远 `in_progress`（检查时已
   2 h35 m）。只有 `status=completed` 且 `conclusion=success` 才算绿；`cancelled`/`failure`/
   `skipped`/`neutral`/空都不算。
2. **`ci` 的两条矩阵腿都要绿**：`test (3.11)`（本次修复的对象）+ `test (3.12)`，外加 `image` job。
   只看 3.12 或只看 job 总数会把这次事故漏掉。
3. `ci` 没有 `workflow_dispatch`，所以不能事后补跑：**打 tag 前**先确认
   `git rev-parse <tag>^{commit}` 就是 main 上已有绿 `ci` 的那个 SHA（本次要求 main 上 3.11、3.12
   两条腿都 success），再打 tag。

## 7. 复现/验证速查

```bash
REPO=$(git rev-parse --show-toplevel)
cd "$REPO"

# 0) 环境（首次会 sync，独立于仓库 .venv）
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv sync --python 3.11 --extra dev
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv312 uv sync --python 3.12 --extra dev

# 1) 挂起复现：在**修复前**的代码上只应看到 4 个点后卡死（现工作树已修复 → 10 个点全绿）
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv run --python 3.11 --extra dev python - <<'PY'
import faulthandler, sys
faulthandler.dump_traceback_later(40, exit=True, file=open('/tmp/fh-trace311.log', 'w'))
import pytest
sys.exit(pytest.main(['-q', '-s', 'tests/test_segment_silence.py']))
PY
cat /tmp/fh-trace311.log        # 栈落在 test_a_barge_does_not_leave_segments_for_the_next_reply

# 2) 无 rui 依赖的机制对照（3.11 挂 / 3.12 不挂）
/tmp/rui-venv311/bin/python /tmp/waitfor_repro.py
/tmp/rui-venv312/bin/python /tmp/waitfor_repro.py

# 3) 修复后回归（-o addopts= 是为了让 pytest 打出 "N passed, M skipped in Xs"）
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv run --python 3.11 --extra dev pytest -q -o addopts=
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv312 uv run --python 3.12 --extra dev pytest -q -o addopts=
UV_PROJECT_ENVIRONMENT=/tmp/rui-venv311 uv run --python 3.11 --extra dev ruff check src tests scripts/coverage_report.py
```

## 8. 附录：定位用的两个一次性脚本

文档里引用的 `/tmp/dbg311.py`、`/tmp/waitfor_repro.py` 会随 tmp 清理消失，正文内联如下，便于复算。

### 8.1 `dbg311.py` —— dump 挂起时 asyncio 的任务栈（§2.2）

**必须在修复前的代码上跑**（例如 §5.1 的 `/tmp/hangcheck` 干净副本，`cd` 进去跑）；
在已修复的工作树上跑只会打印 `TEST RETURNED OK`（那是"修好了"，不是"没复现"）。
实际输出（`/tmp/doc-dbg-prefix2.log`）：

```
=== TASK DUMP ===
--- TASK Task-2 <Task cancelling ... coro=<AudioPlane._speak_loop() running at
              /private/tmp/hangcheck2/src/rui/audio/process.py:2831> wait_for=<Future pending cb=[Task.task_wakeup()]>>
  File ".../src/rui/audio/process.py", line 2831, in _speak_loop
    chunk = await asyncio.wait_for(self._speak_queue.get(), timeout=0.5)
--- TASK Task-1 ... coro=<test_a_barge_does_not_leave_segments_for_the_next_reply.<locals>.scenario()
              running at .../tests/test_segment_silence.py:246> wait_for=<Task cancelling name='Task-2' ...>
  File ".../tests/test_segment_silence.py", line 246, in scenario
    await task
--- TASK Task-24 <Task pending ... coro=<Queue.get() ...>> asyncio/queues.py:158  await getter
```

```python
"""把 barge 用例单独跑起来，8 s 后从 loop 内 dump asyncio.all_tasks() 的栈。"""
import asyncio, faulthandler, pathlib, sys, tempfile, threading, traceback

faulthandler.dump_traceback_later(40, exit=True, file=open("/tmp/fh-dbg311.log", "w"))
sys.path.insert(0, "src"); sys.path.insert(0, "tests")
import test_segment_silence as T

real_new_loop = asyncio.new_event_loop

def fake_run(coro, **kwargs):                      # 替掉 asyncio.run，拿到 loop
    loop = real_new_loop()
    asyncio.set_event_loop(loop)

    def dump():
        print("=== TASK DUMP ===", flush=True)
        for task in asyncio.all_tasks(loop):
            print("--- TASK", task.get_name(), repr(task), flush=True)
            for frame in task.get_stack():
                print("".join(traceback.format_stack(frame)), flush=True)

    timer = threading.Timer(8.0, lambda: loop.call_soon_threadsafe(dump))
    timer.daemon = True
    timer.start()
    return loop.run_until_complete(coro)

asyncio.run = fake_run
try:
    T.test_a_barge_does_not_leave_segments_for_the_next_reply(pathlib.Path(tempfile.mkdtemp()))
    print("TEST RETURNED OK", flush=True)
except BaseException as exc:
    print("TEST RAISED", repr(exc), flush=True)
```

### 8.2 `waitfor_repro.py` —— 无 rui 依赖的最小复现（§2.3）

```python
import asyncio, sys

async def worker(q):
    while True:
        try:
            await asyncio.wait_for(q.get(), timeout=0.5)
        except TimeoutError:
            continue

async def case_cancel_while_waiting():
    q = asyncio.Queue()
    task = asyncio.create_task(worker(q), name="worker")
    await asyncio.sleep(0.05)      # 停在 wait_for(queue.get()) 里
    q.put_nowait(b"x")             # chunk 到达……
    await asyncio.sleep(0)         # ……与 cancel 同一 tick
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=3.0)
    except asyncio.CancelledError:
        return "cancelled cleanly"
    except TimeoutError:
        return "HANG: task still cancelling after 3 s"

print(sys.version.split()[0], asyncio.run(case_cancel_while_waiting()))
```

把 `worker` 里的 `wait_for(q.get(), 0.5)` 换成 `async with asyncio.timeout(0.5): await q.get()`，
3.11.14 与 3.12.15 都会打印 `cancelled cleanly`。
