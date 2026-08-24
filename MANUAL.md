# IO-S 操作手册 v0.1
## 智能体操作系统 — 用户指南

---

## 一、IO-S 是什么

**IO-S** 是一个运行在文件系统上的智能体操作系统。它不是Linux的替代品——它是在Linux之上、为AI Agent设计的认知运行时。

**一句话**：IO-S 让 AI Agent 拥有共享记忆（card）、互相通信（signal）、自我思考（dream）、被监控（monitor）的能力。

**不做什么**：不做完整的OS（不调度CPU、不管理内存、不驱动设备）。IO-S 只管 Agent 能做什么（syscall），不管 Agent 在什么机器上跑。

---

## 二、快速开始

### 2.1 启动

```bash
cd ~/io-s
python3 io-s status
```

输出：
```
  ╔══════════════════════════════════════════════════╗
  ║  IO-S v0.1 — 智能体操作系统                      ║
  ║  启动: 0s前 | D₀: 2.0±0.5 (fallback)           ║
  ╚══════════════════════════════════════════════════╝

  📇 卡片: 39 张
  🤖 Agent: 1 个运行中
  📊 内核: 16 个已注册系统调用
```

加别名，以后直接 `io-s`：

```bash
alias io-s='python3 ~/io-s/io-s'
# 建议加到 ~/.bashrc
```

### 2.2 第一个命令

```bash
io-s status          # 全系统状态
io-s card list       # 列出所有卡片
io-s card show fata-gates   # 看具体卡片内容
io-s syscalls        # 查看16个系统调用
io-s monitor         # 系统健康仪表盘
```

---

## 三、命令参考

### 3.1 `io-s status` — 全系统状态

显示：
- 卡片总数（记忆库规模）
- Agent 数（活跃Agent）
- 已注册 syscall 数
- D₀ 基线（认知信号健康度，IAH就绪时显示）
- 信号等待数

```bash
io-s status
```

### 3.2 `io-s card` — 记忆管理

| 子命令 | 用途 | 示例 |
|--------|------|------|
| `list` | 列出所有卡片 | `io-s card list` |
| `show <id>` | 看单卡内容 | `io-s card show fata-gates` |
| `append <id> <field> <content>` | 追加笔记/决策 | `io-s card append fata-gates notes "今日发现"` |

卡片存在 `~/.hermes/jiak/cards/` 下，但**不要手动修改**——用 `io-s card` 命令。

### 3.3 `io-s agent` — Agent管理

| 子命令 | 用途 | 示例 |
|--------|------|------|
| `ps` | 查看所有Agent | `io-s agent ps` |
| `spawn <goal>` | 创建Agent配置 | `io-s agent spawn "扫描arXiv论文"` |

**说明**: `agent spawn` 只创建配置，实际启动通过 Hermes delegate_task。IO-S 管理 Agent 的生命周期状态，不自己实现进程调度。

### 3.4 `io-s dream` — 认知发现引擎

| 子命令 | 用途 |
|--------|------|
| `run` | 运行双通道认知发现（关键词重叠+语义余弦）并写入RECALL |
| `report` | 查看最近发现 |
| `watch` | 启动后台监视模式（卡片变化自动触发→写入RECALL） |
| `help` | 显示帮助 |

**双通道架构**：
- **关键词重叠通道** — ≥2共享关键词，快（BM25级），发现表面关联
- **语义余弦通道** — embedding级（BGE-small-zh v0.5, 512维），cos≥0.7，**零关键词重叠发现**

核心价值：IO-S自动告诉你"你不知道你知道的事"——两张卡片的关联你从未意识到，但系统发现了。

**写入RECALL**: 语义通道发现的零关键词重叠对自动写入 `~/.hermes/jiak/RECALL.jsonl`，立存为jiak笔记。

### 3.5 `io-s monitor` — 系统健康

显示：
- 卡片健康：总数、冷卡率、超大卡数
- D₀ 认知信号：均值 ± 波动、EWMA基线、异常状态
- 语义结构：semantic head 占比

### 3.6 `io-s intel` — 首席情报Agent *(性能优化中)*

扫描近期活跃卡片 → 发现关联 → 生成快报 → 广播。展示完整的四syscall全链路。

### 3.7 `io-s syscalls` — 查看系统调用列表

```
IO-S 系统调用 (16 个):
  agent_kill, agent_list, agent_spawn, agent_status
  batch_commit
  card_append, card_archive, card_create, card_list, card_read, card_split
  dream_launch, dream_report
  signal_broadcast, signal_decide, signal_recv, signal_send
```

---

## 四、系统架构

```
用户 → CLI (io-s)
        │
    kernel.py (syscall注册 + 信号路由 + 心跳)
     ├── syscall/card.py   → jikA文件系统 (~/.hermes/jiak/)
     ├── syscall/signal.py → 文件系统总线 (~/.io-s/signals/)
     ├── syscall/dream.py  → Brain (双通道发现)
     ├── syscall/agent.py  → Agent生命周期 (~/.io-s/agents/)
     └── monitor/          → IAH健康传感器
```

### 4.1 数据文件位置

| 内容 | 路径 |
|------|------|
| 卡片（记忆） | `~/.hermes/jiak/cards/*.json` |
| 卡片索引 | `~/.hermes/jiak/index.json` |
| 活动日志 | `~/.hermes/jiak/RECALL.jsonl` |
| 信号 | `~/.io-s/signals/*.json` |
| Agent配置 | `~/.io-s/agents/*.json` |
| D₀基线 | `~/.io-s/d0_baseline.json` |

### 4.2 安全架构

IO-S 的零网络架构：
- signal syscall 走本地文件系统（不走HTTP）
- card syscall 只追加不覆写
- dream 只读不写
- 没有API端口暴露

这意味着：没有CORS、CSRF、注入攻击的入口。

---

## 五、批量操作（性能优化）

### 5.1 批量创建卡片

```python
# 逐张sync → 慢（39张卡时每次建卡→扫描全部→重写index）
k.dispatch("card_create", "card-1")  # ~2-5秒/张

# 批量模式 → 快（直接写JSON→最后一次性rebuild index）
k.dispatch("card_create", "card-1", batch=True)  # ~0.01秒/张
k.dispatch("card_create", "card-2", batch=True)
k.dispatch("batch_commit")  # 一次rebuild ~0.5秒
```

**什么时候用 batch**: 批量建卡（10+张）、流水线产出、情报Agent自动写卡。

**什么时候不用 batch**: 单张建卡、希望立即在index中可见。

### 5.2 分卡

单卡超过 20KB 建议分卡：

```bash
io-s card split fata-gates
```

分卡后生成 `fata-gates-split-<timestamp>`，主卡的 `overflow` 字段记录分卡ID。

---

## 六、扩展 IO-S

### 6.1 添加新syscall

1. 在 `syscall/` 下新建文件（或加到现有文件）
2. 编写函数，参数尽量简单（不需要LLM）
3. 在 `syscall/__init__.py` 的 `register_all` 中注册
4. 重启 `io-s`

```python
# syscall/hello.py
def hello(name: str = "world") -> dict:
    return {"ok": True, "message": f"Hello, {name}"}

# syscall/__init__.py
from .hello import hello
kernel.register("hello", hello)
```

### 6.2 添加新CLI命令

1. 在 `cli/__init__.py` 中添加子命令处理
2. 在 `main()` 的函数列表中添加路由

---

## 七、常见问题

### Q: io-s 命令没反应/超时

大卡（>100KB）操作较慢。先运行 `io-s monitor` 查看超大卡列表，然后分卡。

### Q: card append 很慢

jikA 的 append 是"读全卡→修改→写全卡"。超大卡（如 fata-gates 150KB）上尤其慢。建议：
- 先分卡（`io-s card split fata-gates`）
- 或拆笔记写入独立小卡

### Q: D₀ 显示 "fallback"

IAH（注意力测量工具）未在Python路径中。不影响核心功能——IO-S使用预估基线（D₀=2.0±0.5，GPT-2典型值）。IAH就位后自动切换。

### Q: dream report 显示空

Dreaming引擎未启动。先运行 `io-s dream start`，等待下一次扫描周期。

### Q: 如何卸载 IO-S？

IO-S 不安装系统级组件。删除 `~/io-s/` 目录即可。jikA 卡片数据不受影响（在 `~/.hermes/jiak/`）。

---

## 八、文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `~/io-s/io-s` | 8 | CLI入口 |
| `~/io-s/kernel.py` | 147 | 微内核：syscall注册+信号路由+心跳 |
| `~/io-s/syscall/__init__.py` | 31 | syscall注册中心 |
| `~/io-s/syscall/card.py` | 179 | 记忆系统调用（含batch模式） |
| `~/io-s/syscall/signal.py` | 115 | 信号路由IPC |
| `~/io-s/syscall/dream.py` | 96 | Brain认知引擎对接 |
| `~/io-s/syscall/agent.py` | 99 | Agent生命周期管理 |
| `~/io-s/monitor/sensors.py` | 160 | IAH健康传感器 |
| `~/io-s/monitor/baseline.py` | 100 | D₀基线跟踪 |
| `~/io-s/cli/__init__.py` | 265 | 7个子命令CLI |
| `~/io-s/app/chief.py` | 80 | 首席情报Agent |
| **总计** | **~1280** | |

---

> IO-S v0.1 — 2026-06-24
> 源码: ~/io-s/
> 数据: ~/.hermes/jiak/ + ~/.io-s/
