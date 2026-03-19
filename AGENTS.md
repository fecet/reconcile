# AGENTS.md

本文件面向这个仓库的维护者，包括人类开发者和 AI agent。

`README.md` 面向使用者，解释公开语义和基本用法。
`AGENTS.md` 面向实现和维护，记录内部结构、开发约定和修改时需要同时关注的文件。

## 快速约定

- 对外语义以 `README.md` 为准
- 内部结构和维护约定以本文件为准
- 测试组织和共享模型约定以 `tests/README.md` 为准
- 运行测试使用 `pixi run test`
- 字段 provider 的方法名通常直接用 `_`
- cross-object validator 使用描述性英文名称

## 仓库结构

```text
reconcile/
├── AGENTS.md          <- 人类 + AI 维护文档
├── README.md          <- 用户文档
├── src/reconcile/
│   ├── __init__.py    <- public API re-export
│   ├── core.py        <- 核心 reconcile 实现
│   ├── mypy.py        <- mypy plugin
│   └── sentinel.py    <- sentinel helper
├── tests/
│   ├── README.md      <- 测试组织约定
│   ├── models/        <- 共享测试模型
│   └── test_reconcile.py
```

## 核心类型

```text
reconcile(*participants)
        │
        ▼
┌──────────────────────────────┐
│      ReconcileSession        │
│ - pool                       │
│ - states: State[]            │
│ - resolution_stack           │
└──────────────┬───────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌──────────────┐   ┌──────────────────┐
│     Pool     │   │      State       │
│ type -> obj  │   │ - obj            │
│ resolution   │   │ - cls            │
└──────────────┘   │ - slots{}        │
                   └────────┬─────────┘
                            │
                            ▼
                   ┌──────────────────┐
                   │    FieldSlot     │
                   │ - field_name     │
                   │ - providers[]    │
                   │ - saved_default  │
                   │ - required       │
                   │ - result         │
                   └──────────────────┘

_deps(cls) (cached):
- field_providers
- cross_validators
```

职责分层：

- `Pool`
  只负责按类型解析 participant，并处理歧义错误
- `ReconcileSession`
  只负责一次 `reconcile()` 调用的全局流程和环检测状态
- `State`
  负责把 `obj`、原始 `cls` 和待解析 `slots` 绑成具名状态，避免位置式 tuple
- `FieldSlot`
  负责单个待解析字段的 provider、fallback 默认值、required 语义和解析状态
- `_deps(cls)`
  负责按类缓存字段 provider 和 cross-object validator

## reconcile 流程

```text
reconcile(*participants)
        │
        ▼
ReconcileSession.__init__()
        │
        ├── hitchhike discovery (BFS)
        │     └── scan model_fields_set for BaseModel instances
        │
        ├── build Pool(all discovered participants)
        │
        └── build State for each BaseModel
        │
        ▼
ReconcileSession.run()
        │
        ├── promote phase
        │     ├── pop unresolved dep fields
        │     └── owner.__class__ = ProxyClass
        │
        ├── resolve phase
        │     └── _resolve_slot(slot)
        │
        ├── commit phase
        │     └── write resolved value or fallback default
        │
        ├── demote phase
        │     └── owner.__class__ = owner_cls
        │
        ├── run_cross_validators()
        │     └── cached _deps(cls)[1]
        │
        ├── validate_fields()
        │     ├── required field check
        │     └── final TypeAdapter validation
        │
        └── finally
              ├── demote phase
              └── restore default fallback
```

关键时序：

- fallback 默认值在 Pydantic 构造阶段就已经存在于实例中
- promote phase 只是把未手动设置的 dep 字段从 `__dict__` 中临时 `pop` 出来
- provider 成功时先写入 `FieldSlot.result`，commit phase 再真正写回对象
- provider 返回 `None` 会保留 `None`，provider 缺依赖或抛出 `Unresolvable` 时会落到 `UNRESOLVED`
- proxy 读取到 `UNRESOLVED` 时返回 `saved_default`，commit phase 再把 fallback 默认值写回对象
- `finally` 中的 default restore 是异常安全兜底，不是主路径第一次设置默认值

## proxy 字段解析路径

```text
ProxyClass.__getattr__(name)
        │
        ├── field in slots ?
        │     └── no  -> cls.__getattr__
        │
        ├── slot.result is RESOLVING ?
        │     └── yes -> Cycle detected from resolution_stack
        │
        ├── slot.result = RESOLVING
        ├── push slot to resolution_stack
        │
        ├── pool.try_call(provider)
        │     └── provider may recursively read other dep fields
        │
        ├── slot.result = value | UNRESOLVED
        │
        └── finally
              └── pop slot from resolution_stack
```

## 维护约定

- 如果修改公开语义，同时更新 `README.md`
- 如果修改内部结构或流程，同时更新本文件
- 如果修改测试组织方式或共享模型，同时更新 `tests/README.md`
- 如果修改字段解析、环检测、fallback、手动覆盖优先级，必须补测试
- 如果只是内部重构，不应改变 `README.md` 的公开语义描述

## 常用命令

```bash
pixi run test
```

## 何时更新哪些文档

```text
公开 API / 语义变化   ──► README.md
内部实现 / 维护约定   ──► AGENTS.md
测试结构 / 模型复用   ──► tests/README.md
```
