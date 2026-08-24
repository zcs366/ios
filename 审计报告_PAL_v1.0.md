# 🏗️ 鲁班·工程审计报告
## IO-S PAL v1.0 — 逐项诊断

> 审计人：鲁班（工程师导师＋技术装备部长）
> 日期：2026-06-22
> 审计范围：~/io-s/PAL.md 全文及对应现有资产代码
> 原则：信代码不信文档，指向具体设计/代码行

---

## 一、现有资产核查（信代码）

PAL声明 | 代码核查 | 实际行数 | 判定
---|---|---|---
jikA文件系统 ~1200行 | jiak_api.py(545) + jika_vector.py(176) + plugins/jika/__init__.py(110) = **831行** | ~830行 | ⚠️ 差~370行，不是1200行
Brain.dream() ~780行 | brain.py 全文 783行，其中dream约50行，start_dreaming约100行 | 783行 | ✅ 行数吻合
Brain.ingest_signal() | brain.py L206-237, 30行 | 内建 | ✅
Brain.decide() | brain.py L257-327, 70行 | 内建 | ✅
Brain.insight()/emit() | brain.py L331-381, 50行 | 内建 | ✅
IAH D₀测量 ~500行 | iah/core.py(349) + cli.py(151) + schema.py(50) + texts.py(26) = **576行** | ~576行 | ✅ 基本吻合
AGFT冻结协议 ~300行 | SKILL.md 238行（文档）+ references 约600行 | 文档238行 | ⚠️ 行数标为代码行数，实际是markdown协议文档
24 syscall设计 | 存在于刀05（823行蓝图），但代码侧**不存在任何syscall** | 0行代码 | ⚠️ 纯蓝图
37张jikA卡片 | `ls ~/.hermes/jiak/cards/*.json | wc -l` = **37** | 37 | ✅ 确认

**关键发现：PAL说"jikA ~1200行"，代码实际~830行。差量约370行可能来自其他jika组件（index.yaml等配置），但技术上jikA文件系统的核心代码是830行，不是1200行。**

---

## 二、P0六个syscall组逐项诊断

### P0_1: kernel.py — IO-S内核 (~80行)

**打标：✅ 可行，但依赖图有误**

| 子任务 | 预计 | 判断 | 说明 |
|--------|------|------|------|
| syscall注册表 | 15min | ✅ | dict[str→handler]，3行代码 |
| 信号路由dispatcher | 20min | ✅ | 纯调度，无依赖 |
| 心跳检查 | 10min | ✅ | 简单健康检查 |

**匠石边界：代码层。** 纯调度框架，不涉及LLM调用，完全正确。

**真实工作量：实际约50行。** 信号路由在PAL中描述为"不自己实现进程调度——delegate_task就是IO-S的exec；不自己实现信号队列——文件系统(jikA)就是IO-S的IPC"——这个设计决策**正确**。

**依赖图矛盾：** PAL依赖图显示：
```
jikA──────→io_s.syscall.card──┐
Brain─────→io_s.syscall.dream─┤
                                ├─→ IO-S Kernel ←─ CLI
IAH───────→io_s.monitor───────┘
AGFT──────→io_s.syscall.agent
```

但实际依赖方向应该反过来：**Kernel是所有syscall的入口**，card/dream/monitor/agent都是kernel的子模块。PAL的依赖图把kernel画成了终结点（被依赖），但实际上它是调度中心（依赖别人）。微调。不影响可行性。

---

### P0_2: syscall/card.py — 记忆系统调用 (~100行)

**打标：✅ 可行性最高，装备充分**

| 子任务 | 预计 | 判断 | 说明 |
|--------|------|------|------|
| card_create(path, keywords, summary) | 10min | ✅ | jiak_api.write_card() 直接wrap |
| card_read(card_id) | 5min | ✅ | jiak_api.read_card() 直接wrap |
| card_append(card_id, section, entry) | 10min | ✅ | jiak_api.append_card_field() 直接wrap |
| card_archive(card_id) | 5min | ✅ | update_card_field(status→archived) |
| card_split(card_id, at_keys) | 15min | ⚠️ | 已有split逻辑吗？jikA没有split的API |

**匠石边界：代码层。** 零LLM调用，纯JSON读写。完美。

**card_split风险：** PAL说"已有split逻辑"，但在 jiak_api.py 和 brain.py 中**找不到任何split实现**。card_split是新功能，需要自己实现——复制→修改→删除源。估计20min，不是15min。

**真实工作量：~100行，PAL估算合理。** 唯一需要纠正：card_split标为"新实现，非已有"。

---

### P0_3: syscall/signal.py — 信号路由 (~120行)

**打标：✅ 可行，但设计有隐患**

| 子任务 | 预计 | 判断 | 说明 |
|--------|------|------|------|
| signal_send(dest_card, body) | 15min | ✅ | 写卡片到dest的inbox目录 |
| signal_recv(agent_id, timeout) | 10min | ⚠️ | **轮询模式有竞态风险** |
| signal_broadcast(body, channel) | 10min | ✅ | 写公共频道 |
| signal_decide(content) | 10min | ✅ | wrap Brain.decide() |
| signal_wait(agent_id, signal_id) | 10min | ⚠️ | **阻塞等待没有实现机制** |

**匠石边界：代码层+轻量模型层。** signal_decide是唯一涉及模型调用的——准确地说它wrap Brain.decide()，而Brain.decide()是纯规则（查重、关键词重叠、紧急词检测、目标相关性），**完全不调用LLM**。这个边界设计得非常好。

**设计隐患：**
1. **signal_recv 轮询机制：** 用101ms轮询间隔验证过，但100ms轮询在文件系统上会产生大量IO。37张卡片×多Agent×100ms = 持续的磁盘IO。在WSL上尤其严重——AGFT已经踩过"WSL磁盘IO坑⑥：GPU显存分配挤出页面缓存"的坑。
2. **signal_wait 阻塞语义：** PAL说"阻塞直到对端回应"，但文件系统上没有阻塞语义（不能block on file change）。要么spin-loop（CPU燃烧），要么用threading.Event模拟（增加线程管理复杂度）。PAL没有考虑这个实现细节。
3. **inbox目录不存在：** jikA没有"inbox"目录概念。这是signal层自己的设计。需要一个统一的信号存储目录约定。

**真实工作量：~150行（比PAL估多30行）。** 需要额外处理inbox目录创建、轮询的退避策略、signal_wait的Event实现。

---

### P0_4: syscall/agent.py — Agent生命周期 (~80行)

**打标：⚠️ 可行性中等，存在关键依赖**

| 子任务 | 预计 | 判断 | 说明 |
|--------|------|------|------|
| agent_spawn(goal, skills) | 15min | ⚠️ | **依赖Hermes delegate_task接口** |
| agent_kill(agent_id) | 5min | ✅ | 写STOP信号卡片 |
| agent_status(agent_id) | 5min | ✅ | 读agent状态卡片 |
| agent_sched(agent_id, policy) | 10min | ❌ | **调度策略实现完全不明确** |

**匠石边界：代码层+Hermes适配层。** agent_spawn本质是"让Hermes启动一个子Agent"，不走LLM。但：

**问题1：delegate_task接口是否可用？**
PAL说"agent_spawn直接wrap Hermes的delegate"——但在整个代码库中**没有找到delegate_task的公开API或MCP工具**。Hermes CLI（`hermes-lan`相关命令）有LAN命令执行，但那不是本地的sub-agent spawn。agent_spawn需要一个：
- 要么MCP工具（如`hermes_lan_command_exec`但需要connection_id）
- 要么Python子进程（subprocess.Popen）
- 要么Hermes内部API

PAL说"不重新实现delegate_task，直接wrap"——但如果当前运行时没有delegate_task工具可用，这意味着agent_spawn**在进入Day1之前需要先造一个Hermes的delegate能力**。这是关键外部依赖。

**问题2：agent_sched调度策略：** PAL说"调度策略设置"——但什么是调度策略？什么策略可用？FIFO? Priority? Round-robin? 没有写。这是PAL里最模糊的一个子任务。如果只是存储policy字符串（如"sched=round-robin"）那很简单，但如果要实际实现调度逻辑，那就远不只10min。

**真实工作量：** 
- agent_spawn（假设delegate_task可用）：15min ✅
- agent_kill：5min ✅  
- agent_status：5min ✅
- agent_sched：3min（存储策略字符串）或 2h+（如果实现调度引擎）
- **总：~30min或2h+，取决于sched的含义**

**建议：砍掉agent_sched，或者降低为"存储字符串标志"。**

---

### P0_5: syscall/dream.py — Brain对接 (~30行)

**打标：✅ 可行性高，装备充分**

| 子任务 | 预计 | 判断 | 说明 |
|--------|------|------|------|
| dream_launch(card_ids?) | 15min | ✅ | wrap Brain.dream() + start_dreaming() |
| dream_report() | 10min | ✅ | 读brain_dream.jsonl |

**匠石边界：完美。** 代码层到Brain的桥接，Brain.dream()自身有关键词匹配+余弦相似度两条通道。如果vector不可用降级为纯关键词。

**装备检查：** Brain已经实现了完整的dream pipeline（brain.py L385-452）：
- 关键词重叠通道 ✅
- 语义向量余弦通道 ✅（依赖jika_vector）
- LLM增强洞察通道 ✅（依赖外部LLM端点，可选）
- start_dreaming后台线程 ✅

**真实工作量：~20行。** dream_launch基本就是一行`Brain(agent_id).start_dreaming(...)`。PAL估30min略保守。

---

### P0_6: CLI基础 (~120行)

**打标：✅ 可行，但范围需调整**

| 子任务 | 预计 | 判断 | 说明 |
|--------|------|------|------|
| cli/__init__.py (argparse) | 15min | ✅ | 标准argparse路由 |
| io-s status | 20min | ⚠️ | 需要sensor数据（D₀基线在P1） |
| io-s card list | 15min | ✅ | jiak_api.load_index() 直接输出 |
| io-s card show <id> | 10min | ✅ | jiak_api.read_card() 直接输出 |
| io-s card append <id> | 10min | ✅ | jiak_api.append_card_field() 直接输出 |

**匠石边界：纯代码层。** 零LLM调用。

**依赖冲突：** io-s status 需要D₀基线，但PAL把D₀基线放在P1（monitor/baseline.py）。这意味着**Day1结束时io-s status无法显示D₀基线**——而Day1通过条件说"io-s card list 能列出37张卡片"。P1和Day1通过条件之间没有冲突，但io-s status在Day2之前只能显示卡片数和Agent数，不能显示D₀。

**真实工作量：~100行（比PAL估少20行）。** 因为大部分是jiak_api的thin wrapper。

---

## 三、架构可行性

### 3.1 依赖图分析

PAL依赖图（修正后）：

```
kernel.py (调度中心)
├── syscall/card.py  ← depends on jiak_api (已有)
├── syscall/signal.py ← depends on jikA文件系统 (已有) + inbox目录约定 (新建)
├── syscall/agent.py  ← depends on delegate_task (外部依赖,未验证)
├── syscall/dream.py  ← depends on Brain (已有)
├── syscall/cap.py (P1) ← 新设计
└── syscall/ns.py (P1)  ← 新设计

monitor/
├── sensors.py ← depends on IAH (已有)
├── baseline.py ← depends on D₀扫描 (已有)
└── anomalies.py (P2) ← 新设计

CLI/
├── cmd_status ← depends on kernel + sensors + baseline
├── cmd_card   ← depends on jiak_api
└── cmd_agent  ← depends on agent syscall
```

**总体依赖关系合理。** 没有循环依赖，card/signal/agent/dream四个syscall组可以并行开发。

### 3.2 card/signal/agent/dream 四个独立syscall组的边界

| 边界 | 判断 | 说明 |
|------|------|------|
| card ↔ signal | ✅ 清晰 | card = 静态度据，signal = 动态通信 |
| card ↔ dream | ✅ 清晰 | card提供数据，dream消费数据 |
| signal ↔ agent | ✅ 清晰 | signal = 通信协议，agent = 生命周期 |
| agent ↔ dream | ✅ 清晰 | agent拥有Brain，Brain拥有dream |
| card ↔ agent | ✅ 清晰 | agent通过signal间接访问card |

**边界设计优秀。** 这四个syscall组确实独立，可以并行开发。

### 3.3 kernel主循环设计

```
loop:
  1. 处理syscall请求 (dispatch)
  2. 心跳检查
```

PAL说"不自己实现进程调度——delegate_task就是IO-S的exec；不自己实现信号队列——文件系统(jikA)就是IO-S的IPC"。

**这是正确的架构决策。** IO-S kernel不需要是OS kernel——它是元调度层。真正的agent执行由Hermes delegate_task完成，真正IPC由jikA文件系统完成。kernel只做：
1. syscall name → handler 的路由
2. 自身健康检查

**但是：** kernel.py的"主循环"到底是什么？在PAL中只说了"信号路由 + 调度 + syscall注册"。但没有说这个循环运行时是什么形态：
- 是一个Python while True 守护进程？
- 是一个CLI命令执行后的同步处理？
- 是一个cron定时任务？

如果是一个守护进程（while True），那需要与Hermes同生命周期管理。如果只是CLI命令的dispatch层，那根本不需要"循环"——只是一个函数调用。**这个模糊点需要在Day1开工前澄清。**

---

## 四、减法建议（砍什么/推什么）

### 强烈建议砍掉

| 任务 | 理由 |
|------|------|
| **agent_sched** | 模糊、与Hermes delegate_task的关系不明确。如果只是存储policy字符串，3行代码解决，但10min估值有意义。如果真的要做调度，以PAL现有的描述程度远远不够。**方案：存储policy字符串 → 降为P2** |
| **signal_wait** | 文件系统上没有阻塞await原语。实现方案需要Event/spin-loop，影响架构简洁性。**方案：降为P2，用signal_recv + callback模式替代阻塞等待** |
| **cap.py（P1）能力安全** | 在MVP单用户场景下，能力安全没有实际使用场景。PAL自己的"不做清单"说"不在Day1设计完美的安全模型"。那cap在P1也过早。**方案：降为P2** |
| **ns.py（P1）命名空间** | 同样，单用户不需要命名空间隔离。**方案：降为P2** |

### 建议推迟

| 任务 | 从 | 到 | 理由 |
|------|-----|-----|------|
| monitor/anomalies.py + healing.py | P1 Day3 | P2 Day4+ | 10指标全实现被PAL自己列入"不做清单"，TOP3 sensor（card_health, D₀, semantic_ratio）已够 |
| monitor/baseline.py | P1 Day3 | P0 Day2 | **D₀基线是io-s status的必要组件**，放在P1导致Day2状态不完整。应该提前到Day2 |

### 调整后的MVP范围

**Day1（3h，不变）**：kernel + card + signal + agent（去掉sched）+ dream

**Day2（3h，调整）**：
- CLI(status+card) ← 加入D₀基线（从P1提前）
- 去掉 cap + ns（推到P2）
- 去掉 anomalies + healing（推到P2）

**Day3（可选）**：monitor + app首席Agent

**砍前vs砍后对比：**

| 维度 | 砍前 | 砍后 |
|------|------|------|
| P0任务数 | 6组(~530行) | 6组(~450行，去掉sched/wait) |
| P1任务数 | 6组(~430行) | 3组(~200行，移走cap/ns/anomalies/healing) |
| 核心依赖 | 6个 | 6个（但agent依赖delegate_task风险暴露） |
| Day1完工标准 | card list + card append | 同 **不变** |
| Day2完工标准 | 完整状态+agent spawn | 同 **不变**（D₀基线提前保证） |
| **估算总工时** | **~13h代码 / ~5h串行** | **~8h代码 / ~3.5h串行** |

---

## 五、装备判断（够不够？缺什么？）

### 现有装备

| 装备 | 状态 | 可用于 | 够用？ |
|------|------|--------|-------|
| **jikA文件系统**(jiak_api.py 545行) | ✅ 生产 | card_create/read/append/archive | ✅ **完全够用**——card.py本质是jiak_api的薄wrap |
| **Brain**(brain.py 783行) | ✅ 生产 | dream_launch, signal_decide | ✅ **完全够用**——70%功能直接对接 |
| **IAH D₀**(iah/core.py 349行) | ✅ 验证 | D₀传感器, 基线, 异常检测 | ✅ **够用**——sensor_d0_signal直接wrap |
| **AGFT协议**(SKILL.md 238行) | ✅ 验证 | agent_spawn的学习保护概念 | ⚠️ **勉强够用**——AGFT是微调协议，不是agent spawn的运行时保护。PAL用它做"agent_spawn学习保护"可能需要额外适配层 |

### 缺什么

| 装备缺口 | 需要的模块 | 影响 | 补救 |
|----------|-----------|------|------|
| **delegate_task API** | agent_spawn的核心依赖 | ❌ 致命——没有这个agent_spawn无法实现 | 需要检查Hermes是否有MCP工具或内部API可以提供 |
| **inbox目录约定** | signal通信的存储路径 | ⚠️ 中等——影响signal实现 | 在signal.py中定义常量即可，~5行 |
| **inotify/fswatch** | signal_recv的高效实现 | ⚠️ 低——PAL用轮询(101ms)已验证 | 但建议改fswatch减少IO |
| **信号协议格式** | signal send/recv的字段约定 | ⚠️ 中等——需要设计共识 | 在signal.py中定义dataclass即可，~20行 |

---

## 六、匠石边界核查（代码层 vs 模型层）

### 核心原则核查

| syscall | 代码层 | 模型层 | 边界判定 |
|---------|--------|--------|---------|
| kernel.py | **100%代码层** | 无 | ✅ 正确 |
| card.py | **100%代码层** | 无 | ✅ 正确 |
| signal.py | **80%代码层**，decide是纯规则 | decide wrap Brain.decide()（不调LLM） | ✅ 正确 |
| agent.py | **100%代码层**（如果delegate_task可用） | 无 | ✅ 正确 |
| dream.py | **50%代码层（关联发现）** | **50%模型层**（LLM增强洞察），但可选 | ✅ 正确——_llm_dream是可选fallback，不阻塞核心流程 |
| cap.py/ns.py | **100%代码层** | 无 | ✅ 正确 |
| monitor | **100%代码层** | 无 | ✅ 正确 |
| CLI | **100%代码层** | 无 | ✅ 正确 |

**整体评价：匠石边界切割非常干净。** 这是PAL做得最好的部分。所有LLM调用都封装在Brain里，IO-S syscall层不直接调LLM。没有任何一个syscall在"必须走LLM才能工作"的路径上。

### 危险检查（可能烂用模型的地方）

**未发现。** 极好。Card/signal/agent/dream四个syscall组都不强制依赖LLM。dream的LLM洞察是可选增强，不是核心功能。

---

## 七、最终建议

### 砍前 vs 砍后

```
砍前：P0=6组(530行/3h) + P1=6组(430行/2h) + P2=4任务(2h+) = 16任务/~13h代码
砍后：P0=6组(450行/3h) + P1=3组(200行/1h) + P2=7任务(3h+) = 16任务/~8h代码
                                                        ↑ 6组被移动，总任务数不变
```

### 一句话总结

**IO-S PAL是一份质量极高的工程化方案——架构边界合理、装备复用充分、匠石分割干净——唯一的致命风险是agent_spawn依赖的delegate_task接口尚未在当前运行时确认可用，其他的调整（砍sched、推cap/ns、提baseline到Day2）都是微调而不是重建。**

### Day1开工前必做的三件事

1. **确认 delegate_task 可用性**——在Hermes当前版本中找到sub-agent spawn的入口。如果没有，需要在Day1前创建一个最小的delegate包装器（~30行 subprocess.Popen + 信号文件管理）
2. **裁掉 agent_sched**——改为存储字符串policy标志，推迟到P2
3. **把 D₀ baseline 提到 Day2**——否则 io-s status 在Day2展示不完整

---

*报告结束。工具已经入库，图纸已经刻在石板上。开工不等人。*
