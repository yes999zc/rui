# 项目：小爱音箱 Pro bridge 剥离为独立项目

来源：9 哥 2026-10-26「小爱音箱 Pro bridge 剥离成独立项目，也可以做了」。

## 背景
- 内核已无小爱（`tests/test_kernel_decoupling.py` 守护）；小爱只剩 `src/rui/plugs/xiaoai-lx06/`（in-tree plug）+ `adapter_aliases.py` 别名。
- Dot2 已经跑通「out-of-tree 仓库 + pinned 镜像 + 下游 Dockerfile」的模式（`rui-dot2-bridge`），可作为模板。

## 目标形态
- 新仓库（kebab-case，未定名，建议 `rui-xiaoai-bridge`）：只含 LX06（及后续小爱机型）的 adapter/协议实现 + `plugin.yaml` + devices 清单 + 自己的 CI/发布；
- rui 侧留 `plugs/` 扩展点与最小 stub，删 `adapter_aliases.py`（与 0.7 里程碑合并做）；
- 兼容窗口：bridge 声明 `rui_compat`，rui 侧只认 `device` contract；
- 发布顺序：bridge 先发兼容版本 → rui pin 住 → 再删 in-tree。

## 待 9 哥 决策
1. 仓库名与是否公开；
2. 是否把 `deploy/downstream` 那套（Dockerfile + 镜像 pin）直接复制，还是抽成共享模板；
3. 剥离后 in-tree plug 是保留为「参考实现」还是彻底删除。
