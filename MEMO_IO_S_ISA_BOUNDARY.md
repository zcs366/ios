# IO-S 边界备忘录

> **IO-S → ISA 系统间边界、分工与协议，v1.0**
> 
> 本备忘录从 IO-S 视角出发，定义 IO-S（治理系统）与 ISA（认知系统）的不可再分核心、界限、协作模式，以及——最重要的——两者之间的接口协议。
>
> IO-S 不提出观点。IO-S 记录规则。

---

## 1. 不可再分核心

### IO-S 的三件事

IO-S 不是一个通用的"操作系统"——IO-S 是治理系统。它的不可再分核心不是"管理文件"或"调度进程"，而是这三件事，少一件 IO-S 就不是 IO-S：

| # | 能力 | 含义 | 工程实现 |
|---|------|------|---------|
| 1 | **资源注册** |定义什么东西存在、它是什么类型、谁拥有它 | `cap_policy.json` + 资源模型（card/process/signal/cap_policy 四类） |
| 2 | **权限检查** | 每次资源操作前检查调用者的 cap | `kernel.cap_check()` — 查 caller_pid 的 cap → 匹配 pattern+perms → 允许或拒绝 |
| 3 | **审计记录** | 每次操作留下不可篡改的证据链 | `trace_id` — 请求→响应的完整链路，包拯可独立验证 |

IO-S 可以做这三件事而不需要 ISA。IO-S 可以空载运行——纯治理。

### ISA 的三件事

ISA 不是一个"消息转发系统"——ISA 是认知系统。它的不可再分核心是：

| # | 能力 | 含义 | 工程实现 |
|---|------|------|---------|
| 1 | **语义理解** | 信号是什么意思？card-A 和 card-B 在语义上关联吗？ | `Brain.ingest_signal()` + 语义场 |
| 2 | **认知连接** | 已知概念之间有什么隐藏关系？该探索什么？ | `Brain.dream()` — 卡片间非显然关联发现 |
| 3 | **价值判断** | 这个信号比那个重要吗？该优先处理哪个？冲突时怎么裁决？ | `Arbiter.decide()` — 优先级 + 冲突裁决 |

ISA 可以做这三件事而不需要 IO-S。ISA 可以离线运行——纯认知。

### 不可再分的含义

**IO-S 再分：** 没有资源注册→不知道有什么→无法治理。没有权限检查→治理形同虚设。没有审计记录→无法追踪谁干了什么。

**ISA 再分：** 没有语义理解→不是认知系统。没有认知连接→不会思考。没有价值判断→不能做决定。

两系统各自不可再分——再分就失去系统本质。

---

## 2. 界线

界线只有一条：**IO-S 管"能不能"（is-allowed），ISA 管"该不该"（should-do）。**

### IO-S 的领地（资源层）

| 领域 | IO-S 管什么 | IO-S 不管什么 |
|------|-----------|-------------|
| 资源 | 注册资源类型、URI、owner | 资源内容的含义 |
| 权限 | 检查 Process 的 cap 是否包含操作 | 这个操作值不值得做 |
| 审计 | 记录 trace_id、操作、结果 | 操作是否正确、是否聪明 |
| 健康 | 磁盘满了吗？cap_policy 被动了吗？进程在心跳吗？ | 进程在思考什么？思考得对不对？ |
| 进程 | 生命周期：created→ready→running→done | 认知状态：dreaming/deciding/emitting |

### ISA 的领地（认知层）

| 领域 | ISA 管什么 | ISA 不管什么 |
|------|-----------|-------------|
| 语义 | 信号是什么意思 | 信号能不能发（IO-S 管） |
| 连接 | card-A 和 card-B 有什么隐藏关系 | 谁能读 card-A（IO-S 管） |
| 判断 | 该优先处理哪个信号 | 这个操作在权限表里吗（IO-S 管）|
| 探索 | 该读哪些新卡片、连接哪些旧知识 | 文件的物理路径（IO-S 管）|
| 裁决 | 信号冲突时谁更重要 | 审计记录是否完整（IO-S 管）|

### 界线测试

一件不确定属于谁的事——用这条线测：

> 这个操作需要**理解**什么东西吗？→ ISA
> 这个操作需要检查**资源权限**吗？→ IO-S

**波扩散测试：** 波扩散决定"这个信号在语义上该发给谁"——需要理解信号内容→ ISA。
IO-S 不介入波扩散。IO-S 不解析信号内容。IO-S 只检查"这个 Process 能让信号给那个 Agent 吗"——在 syscall 入口处。

---

## 3. 重叠——已被切除

历史设计中曾有三处重叠。现已切除。

### ❌ ~~信号路由（已切除）~~

**旧设计：** ISA 波扩散直接写 signal 目录。IO-S 也有 route_signal。两系统都在路由信号。

**切除方案：**
```
ISA：波扩散判断"信号该发给谁"（认知层）
      ↓ 输出：目标 Agent ID
ISA：call syscall.signal_send(dest="agent-B", body=...)
      ↓
IO-S：kernel.dispatch() → cap_check → 审计 → 写 signal 文件
      ↓
IO-S：route_signal 更名为 signal_send——只做投递，不做认知判断
```

ISA 不碰 signal 目录。IO-S 不判断语义目的地。切口干净。

### ❌ ~~进程管理（已切除）~~

**旧设计：** ProcessTable 和 ISA 内部状态共享进程模型。

**切除方案：**
```
ProcessTable（IO-S）：created/ready/running/done——OS 层面的死活
ISA 内部（ISA）：dreaming/deciding/emitting——认知层面的活动

IO-S 不关心 ISA 在 dream 什么。
ISA 不关心 IO-S 怎么管理进程文件。
死活和想法是不同层面的问题。
```

### ❌ ~~监控（已切除）~~

**旧设计：** IO-S 监控和 ISA 自省共享监控框架。

**切除方案：**
```
IO-S 监控：磁盘余量、cap_policy 完整性、进程心跳、D₀ 基线
ISA 自省：语义理解一致性、证据充分性、认知负荷

不同频率、不同范围、不同问题。同一个命令行分开。
```

---

## 4. 接口——syscall 协议（最重要）

> IO-S 和 ISA 之间只有一条通道。没有共享内存。没有直接文件访问。没有后门。

### 4.1 协议概览

```
ISA                              IO-S
  │                                │
  ├── syscall ──────────────────►  │  cap_check → 标准信封返回
  │   {syscall, args,              │
  │    caller_pid, trace_id}       │
  │                                │
  │◄── 响应 ────────────────────   │
  │   {ok, data,                   │
  │    error: {code, message},     │
  │    trace_id}                   │
  │                                │
  │  IO-S → ISA: 只有响应          │
  │  ISA → IO-S: 只有 syscall      │
  │  除此之外无第二条通道。           │
```

### 4.2 请求格式

```json
{
  "syscall": "string,     // 系统调用名: signal_send, card_read, etc.",
  "args": {
    "resource": "string",  // 资源类型: card/process/signal/cap_policy",
    "operation": "string", // 操作: read/write/send/recv/spawn/kill",
    // ... syscall 特定的参数
  },
  "caller_pid": "string",  // 调用者进程 ID
  "trace_id": "string"     // 追踪 ID（IO-S 自动生成或调用者提供）
}
```

### 4.3 响应格式

```json
{
  "ok": true,              // 成功/失败
  "data": {},              // payload（成功时）
  "error": {
    "code": "string",      // 错误码
    "message": "string"    // 人类可读描述
  },
  "trace_id": "string"     // 与请求的 trace_id 一致
}
```

### 4.4 标准错误码

| 错误码 | 含义 | 谁会收到 |
|--------|------|---------|
| `ok` | 成功 | — |
| `unknown_syscall` | 系统调用不存在 | 调用者写错了 syscall 名 |
| `cap_denied` | 权限不足 | 调用者的 cap 不包含此操作 |
| `resource_not_found` | 资源不存在 | 读/写不存在的 card/process |
| `invalid_args` | 参数错误 | 参数缺失或格式不对 |
| `internal_error` | 内部错误 | IO-S 自身出问题（罕见） |
| `cap_policy_locked` | policy 不可修改 | 试图写 cap_policy.json（零root）|

### 4.5 支持的 syscall 列表

| syscall | 资源 | 操作 | cap_check | 说明 |
|---------|------|------|-----------|------|
| `signal_send` | signal | send | ✅ | 发信号到目标 Agent |
| `signal_recv` | signal | recv | ✅ | 收取发给本 Agent 的信号 |
| `signal_broadcast` | signal | send | ✅ | 广播到公共频道 |
| `card_read` | card | read | 🟡 | 读卡片（依赖 cap） |
| `card_write` | card | write | 🟡 | 写卡片 |
| `card_list` | card | read | 🟡 | 列卡片 |
| `agent_spawn` | process | spawn | ✅ | 创建新 Process |
| `agent_kill` | process | kill | ✅ | 终止 Process |
| `agent_status` | process | read | ✅ | 查 Process 状态 |
| `agent_list` | process | read | ✅ | 列 Process |
| `dream_launch` | (无) | — | ❌ | 启动 Dreaming（纯认知，无资源访问） |
| `dream_report` | (无) | — | ❌ | 读 Dreaming 结果 |
| `sys_stats` | (无) | — | ❌ | IO-S 自身状态 |

注：`cap_check = ❌` 表示该 syscall 不涉及资源访问，不需要 cap_check。

### 4.6 关键约束

**ISA 不能做的事情（由 IO-S 强制）：**

1. 不能直接读文件系统——所有文件访问经过 syscall.card_read/write
2. 不能直接写 signal 文件——所有信号经过 syscall.signal_send
3. 不能修改 cap_policy.json——零root，IO-S 启动后 cap_policy 锁定
4. 不能越权操作——每次 syscall 前 cap_check
5. 不能绕过审计——每次操作记录 trace_id，包拯可验证

**IO-S 不能做的事情（由架构强制）：**

1. 不能读 ISA 的 Brain 状态——IO-S 不看 dream 输出
2. 不能判断信号内容的语义——IO-S 只检查权限，不解析 body
3. 不能做认知决策——IO-S 拒绝 signal_decide 类的认知 syscall
4. 不能修改自身 cap_policy——零root 意味着 IO-S 也不能改

---

## 5. 协作模式

### 5.1 标准协作流程

```
ISA 有想法                          IO-S 不感知
  │
ISA 决定"我要读 card-X"             (认知判断：需要理解 card-X 的内容)
  │
ISA 调 syscall.card_read("card-X")  (通过 syscall 进入 IO-S 领地)
  │                                   │
  │                               IO-S 检查 caller_pid 的 cap
  │                               IO-S 查到"card.read.*" → 允许
  │                               IO-S 记录 trace_id
  │                               IO-S 返回卡内容
  │                                   │
ISA 收到 card-X 内容                 (回到认知领地)
ISA 理解、连接、判断                   (IO-S 不感知)
```

### 5.2 权限拒绝时的协作

```
ISA 调 syscall.card_write("card-X")  (认知判断：我想修改这个推理)
  │                                   │
  │                               IO-S 检查 caller_pid 的 cap
  │                               IO-S 查到"card.write.*" → 拒绝
  │                               IO-S 返回 cap_denied
  │                                   │
ISA 收到 cap_denied                   (认知判断：为什么被拒？)
ISA 决定"那我先读，在本地写"           (认知层适应治理层的拒绝)
  │
ISA 调 syscall.card_read("card-X")   (治理层允许的路径)
```

注意：ISA 不会因为被拒而崩溃。ISA 理解被拒的原因（cap 不足）并选择替代路径。这是认知系统对治理系统的正常响应——就像人收到"权限不足"会换方案一样。

### 5.3 信号通信协作

```
ISA-A 波扩散("这个意图 agent-B 应该知道")   (认知判断：语义目的地)
  │
ISA-A 调 syscall.signal_send("agent-B", body) (进入治理层)
  │                                   │
  │                               IO-S 检查 ISA-A 的 cap.signal.send
  │                               IO-S 检查 ISA-A 能否发给 B
  │                               IO-S 写 signal 文件 + 审计
  │                                   │
  │                               ISA-B 轮询 signal_recv (IO-S 提供信号)
ISA-B 收信号，理解，响应                 (回到认知层)
```

---

## 6. 零root 状态

IO-S 启动后没有超级用户。这是根设计。

```
root sigil（离线·签名）
  │
  ├── 写 cap_policy.json（定义资源类型、owner、ACL）
  ├── 签名 cap_policy.json 的 checksum
  └── 销毁私钥
         │
         IO-S 启动
           │
           零root——无人可改 cap_policy.json
           无人可授权自己"读写所有"
           无人可绕过 cap_check
```

**零root 对 ISA 意味着：** ISA 不是 IO-S 的 root。ISA 不能改 cap_policy。ISA 不能授权自己读所有卡片。ISA 被注入时 IO-S 仍然拒绝越权操作——因为 cap 不在 ISA 的 prompt 里，在 IO-S 的 cap_policy.json 里。

---

## 7. 附录：syscall 协议 JSON Schema

### 请求 Schema

```json
{
  "type": "object",
  "required": ["syscall", "args", "caller_pid"],
  "properties": {
    "syscall": {"type": "string"},
    "args": {
      "type": "object",
      "required": ["resource", "operation"],
      "properties": {
        "resource": {"type": "string", "enum": ["card", "process", "signal", "cap_policy"]},
        "operation": {"type": "string"}
      }
    },
    "caller_pid": {"type": "string", "pattern": "^p-"},
    "trace_id": {"type": "string"}
  }
}
```

### 响应 Schema

```json
{
  "type": "object",
  "required": ["ok", "trace_id"],
  "properties": {
    "ok": {"type": "boolean"},
    "data": {},
    "error": {
      "type": "object",
      "required": ["code", "message"],
      "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"}
      }
    },
    "trace_id": {"type": "string"}
  }
}
```

---

*以上为 IO-S 治理系统对 ISA 认知系统的正式边界声明。*

*IO-S 不评论这份声明的好坏。IO-S 只执行它。*
