# IO-S PAL — 智能操作系统工程化计划
## 化蓝图，再去干

> 日期: 2026-06-24
> 基础: 搜神七刀(3609行) + 认知地图v1.0(10页/6Part) + ISA实验③①②(全PASS)
> 原则: 不造轮子，只拼轮子。已有的不动，只加缺失的。

---

## 一、资产清单（有什么）

| 资产 | 状态 | 行数 | 可复用为 |
|------|------|------|---------|
| **jikA文件系统** | ✅ 生产 | ~1200行 | io_s.card_* syscall |
| **Brain.dream()** | ✅ 生产 | ~780行 | io_s.dream_launch |
| **Brain.ingest_signal()** | ✅ 生产 | 内建 | io_s.signal_* 接收端 |
| **Brain.decide()** | ✅ 生产 | 内建 | io_s.signal_decide |
| **Brain.insight()/emit()** | ✅ 生产 | 内建 | io_s.signal_send |
| **IAH D₀测量** | ✅ 验证 | ~500行 | io_s.sys_stats 传感器 |
| **AGFT冻结协议** | ✅ 验证 | ~300行 | io_s.agent_spawn学习保护 |
| **24 syscall设计** | 📄 蓝图 | 823行(刀05) | io_s.syscall.* |
| **IAH监控矩阵** | 📄 蓝图 | 366行(刀07) | io_s.monitor.* |
| **认知地图** | 📄 蓝图 | 12页 | 全书结构 |
| **37张jikA卡片** | ✅ 生产 | — | IO-S文件系统的存量数据 |

---

## 二、IO-S内核架构（目标结构）

```
~/io-s/
├── __init__.py          # IO-S 版本 + 入口
├── kernel.py            # 内核主循环(信号路由+调度)
├── syscall/
│   ├── __init__.py      # syscall注册表
│   ├── agent.py         # agent_spawn/kill/status/sched
│   ├── signal.py        # signal_send/recv/broadcast/decide/wait
│   ├── card.py          # card_create/read/append/archive/split (wrap jikA)
│   ├── cap.py           # cap_grant/revoke/inspect
│   ├── ns.py            # ns_bind/open/close
│   ├── dream.py         # dream_launch (wrap Brain)
│   └── sys.py           # sys_stats/log/halt
├── monitor/
│   ├── __init__.py      # IAH监控主循环
│   ├── sensors.py       # 10指标传感器(D₀/card_health/...)
│   ├── baseline.py      # D₀基线维护(7天滚动)
│   ├── anomalies.py     # 8种异常检测
│   └── healing.py       # 5级自愈(ignore→log→warn→stall→freeze)
├── cli/
│   ├── __init__.py      # CLI入口
│   ├── cmd_status.py    # io-s status
│   ├── cmd_card.py      # io-s card <subcommand>
│   ├── cmd_agent.py     # io-s agent <subcommand>
│   └── cmd_monitor.py   # io-s monitor
├── tests/
│   └── test_*.py        # 每个syscall的测试
├── PAL.md               # 本文件
└── 认知地图_v1.0.md
```

依赖关系：
```
jikA(已有)──────→io_s.syscall.card──┐
Brain(已有)─────→io_s.syscall.dream─┤
                                     ├─→ IO-S Kernel ←─ CLI
IAH(已有)───────→io_s.monitor───────┘
AGFT(已有)──────→io_s.syscall.agent  (学习保护)
```

---

## 三、并行性分析（可同时做什么）

```
时间线：
Day1 (3h)
├── [P0] kernel.py  +  syscall/card.py     ← 独立于其他，先做
├── [P0] syscall/signal.py                   ← 独立于其他
└── [P0] syscall/agent.py + syscall/dream.py ← 独立于其他

Day2 (3h)
├── [P0] monitor/sensors.py + baseline.py    ← 依赖Day1的kernel
├── [P0] cli/cmd_status.py + cmd_card.py     ← 依赖Day1的card syscall
└── [P0] tests/test_signal.py                ← 依赖signal syscall

Day3 (2h)
├── [P1] syscall/cap.py + ns.py              ← 依赖kernel
├── [P1] monitor/anomalies.py + healing.py   ← 依赖Day2 monitor
├── [P1] cli/cmd_agent.py + cmd_monitor.py   ← 依赖Day1-2
└── [P1] tests/test_card.py + test_dream.py  ← 依赖Day1

Day4+ (可选，～3h)
├── [P2] io-s status 完整仪表盘
├── [P2] 第一个IO-S应用(研究情报Agent)
└── [P2] IO-S书稿第一章
```

---

## 四、P0任务表（Day1～Day2）

### P0_1: kernel.py — IO-S内核 (~80行)

```python
# io_s/kernel.py
# IO-S内核主循环：信号路由 + 调度 + syscall注册
# 
# 不自己实现进程调度——delegate_task就是IO-S的exec
# 不自己实现信号队列——文件系统(jikA)就是IO-S的IPC
#
# 核心职责:
#   1. syscall注册表 — dict[syscall_name → handler_fn]
#   2. 信号路由 — 谁向谁发送，通过什么路径
#   3. 心跳 — kernel自身健康检查
```

| 子任务 | 预计 | 依赖 |
|--------|------|------|
| syscall注册表 | 15min | 无 |
| 信号路由dispatcher | 20min | 无 |
| 心跳检查 | 10min | 无 |

### P0_2: syscall/card.py — 记忆系统调用 (~100行)

> 本质: wrap jikA的已有功能

| 子任务 | 预计 | 说明 |
|--------|------|------|
| card_create(path, keywords, summary) | 10min | json.dump |
| card_read(card_id) | 5min | json.load |
| card_append(card_id, section, entry) | 10min | 追加到notes/decisions |
| card_archive(card_id) | 5min | status→archived |
| card_split(card_id, at_keys) | 15min | 分卡(已有split逻辑) |

### P0_3: syscall/signal.py — 信号路由 (~120行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| signal_send(dest_card, body) | 15min | 写信号卡片到dest的inbox目录 |
| signal_recv(agent_id, timeout) | 10min | 轮询inbox目录(101ms已验证) |
| signal_broadcast(body, channel) | 10min | 写广播卡片到公共频道 |
| signal_decide(content) | 10min | 复用Brain.decide() |
| signal_wait(agent_id, signal_id) | 10min | 阻塞直到对端回应 |

### P0_4: syscall/agent.py — Agent生命周期 (~80行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| agent_spawn(goal, skills) | 15min | wrap delegate_task |
| agent_kill(agent_id) | 5min | 写STOP信号 |
| agent_status(agent_id) | 5min | 读agent状态卡片 |
| agent_sched(agent_id, policy) | 10min | 调度策略设置 |

### P0_5: syscall/dream.py — Brain对接 (~30行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| dream_launch(card_ids?) | 15min | wrap Brain.dream() |
| dream_report() | 10min | 返回最新发现 |

### P0_6: CLI基础 (cmd_status, cmd_card) (~120行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| cli/__init__.py (argparse) | 15min | 子命令路由 |
| io-s status | 20min | 卡片数/Agent数/D₀基线/延迟 |
| io-s card list | 15min | 列出所有卡片 |
| io-s card show <id> | 10min | 显示单卡内容 |
| io-s card append <id> | 10min | 追加笔记 |

**P0总计: ~8h 纯代码时间 / ~3h 串行 wall-clock** (大部分并行)

---

## 五、P1任务表（Day3）

### P1_1: syscall/cap.py — 能力安全 (~80行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| cap_grant(agent, resource, perm) | 15min | 写能力卡片 |
| cap_revoke(agent, resource) | 10min | 删除能力卡片 |
| cap_inspect(agent) | 10min | 读所有能力 |

### P1_2: syscall/ns.py — 命名空间 (~60行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| ns_bind(agent, card_ids) | 10min | Agent可见哪些卡片 |
| ns_open(agent) | 5min | 当前可见范围 |
| ns_close(agent) | 5min | 关闭命名空间 |

### P1_3: monitor/sensors.py — IAH传感器 (~100行)

> IAH监控植入IO-S内核的3个MVP指标

| 子任务 | 预计 | 说明 |
|--------|------|------|
| sensor_card_health() | 15min | 卡片数/冷卡率/分卡率 |
| sensor_d0_signal() | 15min | wrap IAH D₀扫描 |
| sensor_semantic_ratio() | 10min | semantic head占比 |

### P1_4: monitor/baseline.py — D₀基线 (~50行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| init_baseline() | 10min | 首跑D₀→首次基线 |
| update_baseline(d0_reading) | 10min | EWMA滚动更新 |
| report_baseline() | 5min | 当前值±浮动输出 |

### P1_5: CLI扩展 (~60行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| io-s agent ps | 10min | 列出活跃Agent |
| io-s agent spawn | 15min | 启动新Agent |
| io-s monitor | 15min | 显示监控仪表盘 |

### P1_6: 测试 (~80行)

| 子任务 | 预计 | 说明 |
|--------|------|------|
| test_card.py (3 test) | 15min | create+read+append |
| test_signal.py (3 test) | 15min | send+recv+broadcast |
| test_dream.py (2 test) | 10min | launch+report |

**P1总计: ~5h 代码 / ~2h 串行**

---

## 六、P2任务表（Day4+）

| 任务 | 预计 | 说明 |
|------|------|------|
| monitor/anomalies.py (3种异常) | 30min | head_drift/concentration/dimension_collapse |
| monitor/healing.py (3级: warn/stall/freeze) | 20min | 自愈策略实现 |
| io-s status 仪表盘(表格输出) | 20min | 卡片/D₀/Agent三栏 |
| io-s signal trace | 15min | 实时信号追踪 |
| 第一个IO-S应用 | 2h+ | 见下方 |

### 第一个IO-S应用：研究情报Agent

| 子任务 | 预计 | 说明 |
|--------|------|------|
| app/chief.py — IO-S的'会饮'应用 | 2h | 定时(每6h)扫描jikA→Brain.dream()发现→生成研究简报→写入IO-S卡片→广播给所有Agent |
| 展示IO-S四个syscall组的全链路调用 | 内建 | agent_spawn→signal_recv→card_create→dream_launch→signal_send |

---

## 七、总体时间线

```
Day1  (3h)  P0: kernel + card + signal + agent + dream syscall  → IO-S内核裸机可用
Day2  (3h)  P0: CLI(status+card) + tests + monitor/sensors      → CLI可操作IO-S
Day3  (2h)  P1: cap + ns + baseline + monitor扩展 + CLI扩展     → 安全+命名空间+监控
Day4+ （可选）P2: 应用 + 仪表盘 + 书稿第一章                     → 让人看到IO-S在做什么
```

**Day1结束时IO-S能做**：
```
$ io-s card list
  fata-gates | FATA路线图 | 37 notes | active
  jika-engine | jikA引擎 | 22 notes | active
  ...

$ io-s card append fata-gates "PAL启动"
  ✓ 已追加

$ io-s agent spawn "扫描今日研究热点" --skills "search-pipeline"
  ✓ Agent spawned: agent-20260624-001
```

**Day2结束时IO-S能做**：
```
$ io-s status
  Cards: 37 active, 3 archived
  Agents: 1 running
  D₀ baseline: 2.1 ± 0.3 (GPT-2)
  Signal latency: 101ms p50
  
$ io-s agent ps
  agent-20260624-001 | scanning | 12m | 2 signals
```

---

## 八、不做清单

| 不做 | 原因 |
|------|------|
| **不写完整的OS教材** | 书稿是P2以后的事，P0只造系统 |
| **不在Day1设计完美的安全模型** | cap+ns在P1加，Day1假设单用户 |
| **不重新实现delegate_task** | agent_spawn直接wrap Hermes的delegate |
| **不做IAH的10指标全实现** | MVP只做TOP3 sensor + 3种anomaly |
| **不写IAH的UI仪表盘** | Day2-CLI表格输出足够，UI留给P2 |
| **不做多机分布式** | 单机IO-S v0.1只跑在一个文件系统上 |

---

## 九、检验标准

| 阶段 | 通过条件 |
|------|---------|
| Day1通过 | `io-s card list` 能列出37张卡片，`io-s card append` 能追加笔记 |
| Day2通过 | `io-s status` 显示完整状态(卡片/D₀/延迟)，`io-s agent spawn` 能启动Agent |
| Day3通过 | `io-s agent ps` 能看到Agent运行，cap控制读权限，monitor显示基线 |
| IO-S可用 | 研究情报Agent(第一个应用)能运行并产出研究简报 |

---

> 这是PAL v1.0。读过觉得哪里要调就改地图再写。不改就Day1开干。
