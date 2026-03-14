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
├── src/reconcile.py   <- 单文件核心实现
├── tests/
│   ├── README.md      <- 测试组织约定
│   ├── models/        <- 共享测试模型
│   └── test_reconcile.py
└── docs/
    └── getattr-proxy.md  <- 方案演进记录
```

## 核心类型

```text
reconcile(*participants)
        │
        ▼
┌──────────────────────────────┐
│      ReconcileSession        │
│ - pool                       │
│ - models: ReconcileModel[]   │
│ - resolution_stack           │
│ - resolving_fields           │
└──────────────┬───────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌──────────────┐   ┌──────────────────┐
│     Pool     │   │  ReconcileModel  │
│ type -> obj  │   │ - owner          │
│ resolution   │   │ - owner_cls      │
└──────────────┘   │ - fields[]       │
                   └────────┬─────────┘
                            │
                            ▼
                   ┌──────────────────┐
                   │ ReconcileField   │
                   │ - field_name     │
                   │ - provider       │
                   │ - saved_default  │
                   └──────────────────┘
```

职责分层：

- `Pool`
  只负责按类型解析 participant，并处理歧义错误
- `ReconcileSession`
  只负责一次 `reconcile()` 调用的全局流程和环检测状态
- `ReconcileModel`
  负责单个 participant 的运行时状态，包括 proxy 切换和默认值恢复
- `ReconcileField`
  负责单个待解析字段的 provider、fallback 和结果写回

## reconcile 流程

```text
reconcile(*participants)
        │
        ▼
ReconcileSession.run()
        │
        ├── promote_models()
        │     └── ReconcileModel.promote()
        │           ├── pop unresolved dep fields
        │           └── owner.__class__ = ProxyClass
        │
        ├── resolve_fields()
        │     └── ReconcileModel.resolve_fields()
        │           └── getattr(owner, field_name)
        │
        ├── demote_models()
        │     └── owner.__class__ = owner_cls
        │
        ├── run_cross_validators()
        │     └── @dependency without target
        │
        ├── validate_fields()
        │     ├── required field check
        │     └── final TypeAdapter validation
        │
        └── finally
              ├── demote_models()
              └── restore_defaults()
```

关键时序：

- fallback 默认值在 Pydantic 构造阶段就已经存在于实例中
- `promote_models()` 只是把未手动设置的 dep 字段从 `__dict__` 中临时 `pop` 出来
- provider 成功时写入结果
- provider 返回 `None` 或缺依赖时，`ReconcileField.apply_resolution()` 立即恢复 `saved_default`
- `finally -> restore_defaults()` 是异常安全兜底，不是主路径第一次设置默认值

## proxy 字段解析路径

```text
ProxyClass.__getattr__(name)
        │
        ├── field in model.fields ?
        │     └── no  -> owner_cls.__getattr__
        │
        ├── field in resolving_fields ?
        │     └── yes -> Cycle detected
        │
        ├── push field to resolution_stack
        │
        ├── pool.try_call(provider)
        │     └── provider may recursively read other dep fields
        │
        ├── ReconcileField.apply_resolution(result)
        │     ├── result is None -> restore saved_default
        │     └── else           -> setattr(owner, field, result)
        │
        └── finally
              ├── remove field from resolving_fields
              └── pop field from resolution_stack
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
方案探索 / 历史记录    ──► docs/getattr-proxy.md
```
