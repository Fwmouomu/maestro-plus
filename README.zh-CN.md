# maestro-plus

**为 AI Agent 驱动的移动端 UI 测试，补上多设备池、断言、以及失败自动诊断 —— 建立在 Maestro 官方 MCP server 之上。**

Maestro 的 MCP server 给了 Agent 眼睛和手：能读屏、能点击、能跑 flow。它刻意停在这里 —— 单设备、原子操作、对结果不置可否。

`maestro-plus` 补上官方留白的部分。它不替换官方 server：这里的每一个工具都是官方能力之上的编排层。上游长出新功能时，本项目跟着变强，而不是被淘汰。

[English](README.md)

---

## 能力矩阵

| 能力 | 官方 MCP | maestro-plus |
| --- | --- | --- |
| 列出已连设备 | 有 | 有 |
| 设备池（含健康度与占用状态） | 无 | 有 —— `list_device_pool` |
| 跑单个 flow | 有 | 有 |
| N 条 flow 跨 M 台设备并行 | 无 | 有 —— `run_parallel` |
| 读取当前屏幕 | 有 | 有 |
| 对结果做断言（元素 / 文本 / 视觉） | 无 | 有 —— `run_and_assert`、`assert_visual` |
| 判定一次失败到底是谁的问题 | 无 | 有 —— `debug_failure` |
| 把一次探索变成可回放的 flow | 无（Studio 闭源） | 有 —— `explore_and_record` |
| 工具链自检 | 无 | 有 —— `health_check` |
| 官方 MCP 不可用时仍能工作 | 无 | 有 —— adb 降级路径 |

最值得装的两个是 `run_parallel` 和 `debug_failure`。跨设备并行是 Maestro Cloud 的收入来源，开源 CLI 里永远不会有；失败归因是所有人都不愿意自动化的脏活，正因如此才值得自动化。

## 安装

### 作为 MCP server

```json
{
  "mcpServers": {
    "maestro-plus": {
      "command": "npx",
      "args": ["-y", "maestro-plus"]
    }
  }
}
```

npm 包只是一个启动器：定位 Python 解释器、拉起真正的 server、把 stdin/stdout 交出去。除此之外没有任何逻辑。

### Python 原生

```bash
uvx maestro-plus
```

需要 Python 3.10+。

## 前置依赖

`maestro-plus` 调用你本来就装了的工具，不捆绑它们。

| 工具 | 用于 | 安装 |
| --- | --- | --- |
| Maestro CLI | 所有会跑 flow 的工具 | `curl -Ls "https://get.maestro.mobile.dev" \| bash` |
| adb | 设备发现、降级路径、日志采集 | Android platform-tools |
| ffmpeg | 仅 `explore_and_record` 的视频录制 | 包管理器 |

先跑 `health_check`，它会告诉你缺什么、哪些工具会因此降级。

## 工具

- **`health_check`** —— 检查 adb、Maestro CLI、设备可达性，返回逐工具的可用性与降级映射。
- **`list_device_pool`** —— 列出每台设备的序列号、型号、Android 版本、健康状况，以及是否已被别的 run 占用。
- **`run_parallel`** —— 接收一组 flow 与一组设备，租用设备并行执行，返回逐 flow、逐设备的独立结果。一台设备上的抖动不会掩盖另一台上的真实失败。同时返回实测墙钟耗时、串行估算与加速比。
- **`run_and_assert`** —— 跑 flow，然后对留下的屏幕状态求值一组断言，一次调用拿到结论。替代 Agent 现在那个「跑 → 截图 → 读层级 → 描述所见 → 判断 → 重复」的循环。
- **`assert_visual`** —— 按可配置容差比对指定屏幕区域与基线图，返回差异比例与产物路径。基线可显式刷新，这是它不变成 flaky 源头的原因。
- **`debug_failure`** —— 旗舰工具。收集失败步骤、失败前后的截图、Maestro 自己在失败瞬间的抓屏、相关日志片段、失败时的界面层级与焦点窗口，然后给出结构化诊断，把「应用缺陷」和「测试缺陷」分开 —— 这是 QA 工程师真正会先问的问题。
- **`explore_and_record`** —— 把 Agent 已经执行过的步骤转成可回放的 Maestro flow，并从留下的屏幕**自动生成断言**（这才是录制里最难、最容易写坏的部分），最后实跑一遍证明它能重放。

## 架构

```
src/maestro_plus/
  server.py            # MCPServer 实例与完整的工具注册
  pool.py              # 设备租约：同一时刻一台设备只有一个所有者
  tools/               # 按关注点拆分的工具实现
  backends/
    maestro_cli.py     # 主路径：封装 Maestro 命令行
    adb.py             # 设备发现、证据采集、降级路径
  evidence.py          # 截图去重、日志裁剪、层级查询
  report.py            # 诊断包与 HTML 报告渲染
```

两条设计规则：

1. **绝不重复实现官方工具。** 上游有的就包一层。有一条测试会在有人忘记时直接让构建失败。
2. **永远降级，绝不失败。** 官方 MCP 不可用时走 adb + Maestro CLI，而不是返回错误。

## 已知限制

详见 [docs/limitations.md](docs/limitations.md)。要点：

- 设备租约是进程内的，两台 server 之间互不可见。
- 降级路径上的 `uiautomator dump` 约每次一秒，是恢复机制而非默认路径。
- 没有无障碍树就没有诊断 —— WebView、Flutter 画布、自绘界面读不到。
- 归因是启发式的，真实失败里 `unknown` 会比你希望的多，请读 findings 而不是只看标签。
- 目前仅支持 Android。
- README 里那张评估表的数字还没填 —— 那是准确的，不是疏漏。

## 许可证

MIT，见 [LICENSE](LICENSE)。
