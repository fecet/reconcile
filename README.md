# reconcile

`reconcile` 是 Pydantic 的一个扩展层，用来表达“字段在最终完成态必需，但它的值来自其他对象”的场景。

它不把模型看成一次性初始化完成，而是显式分成两个阶段：

```text
Pydantic construction
        │
        ▼
partial model
- some required fields may be absent
- object is only for composition
        │
        ▼
reconcile(*participants)
- resolve field providers
- run cross-object validators
- finalize field constraints
        │
        ▼
complete model
- required fields are present
- semantic checks have run
- object is ready to use
```

## 设计理念

普通 Pydantic 假设字段的值在构造时就已经齐全。

`reconcile` 改写的是这个假设，而不是 `Field` 的意义：

- `Field()` 仍然表示“这个字段在最终模型里是必需的”
- `@dependency(field)` 表示这个字段允许在构造阶段暂缺，并由其他 participant 补齐
- `reconcile()` 是从 partial model 到 complete model 的完成步骤

如果一个字段需要在 complete state 中存在，但它的来源在模型外部，那么它就适合交给 `reconcile`。

## 核心概念

### participant

传给 `reconcile(*participants)` 的每个对象都是 participant。

- 它可以是提供值的模型
- 也可以是消费这些值的模型
- 一个对象可以同时扮演两种角色

### partial model

partial model 是“已经通过 Pydantic 构造，但还没有完成 reconcile”的对象。

- 它允许某些 derived required fields 暂时缺失
- 它适合被组装、组合、再传入 `reconcile`
- 它不是稳定的业务完成态

### complete model

complete model 是 `reconcile()` 完成后的对象。

- 必需字段已经补齐，或明确报错
- cross-object validator 已经运行
- 字段约束已经按最终值校验

## Public API

### `@dependency(field)`

声明一个字段 provider。

- 它为某个字段提供派生值
- 参数按类型从其他 participant 中解析
- 如果字段已经被手动赋值，provider 不会覆盖它
- 方法名本身不参与解析，字段 provider 通常直接命名为 `_`
- 常见用法见下方「详细示例」

### `@dependency`

声明一个 cross-object validator。

- 它不为字段产出值
- 它用来检查 participant 之间的语义关系
- 它在 reconcile 的 validator 阶段运行
- 常见用法见下方「详细示例」中的 `validate_warmup`
- 如果这次 `reconcile()` 没有传入所需 participant，这条 validator 会被跳过

### `reconcile(*participants)`

把一组 participant 从 partial state 推进到 complete state，并返回原顺序的对象元组。
- 详细调用方式见下方「详细示例」

## 内部流程

当前实现的内部结构是：

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

一次 `reconcile()` 调用的主流程是：

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

单个字段在 proxy 阶段的解析路径是：

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

## 语义保证

这些规则是 `reconcile` 的公开语义，而不是当前实现的偶然现象。

- `Field()` 表示 complete-time required，不等于 constructor-time required
- 手动赋值优先于 provider 派生
- `Field(default=...)` 和 `default_factory=...` 可以作为 unresolved fallback
- 无 target 的 `@dependency` 在缺依赖时默认跳过
- 类型解析支持按实例类型和继承关系匹配
- 多个候选同时匹配同一类型时会报歧义错误
- 一个字段只能有一个 provider
- 字段级循环依赖会在 `reconcile()` 期间按实际访问路径报错
- 字段约束按 complete state 的最终值校验

## 不保证的内容

`reconcile` 明确只保证 complete state 的语义。

- partial model 不承诺可安全序列化
- partial model 的 `model_dump()` 形状不是公开契约
- `reconcile` 不是通用 DI 容器
- 普通多分支 union 不会自动解析
- 不应依赖 provider 的声明顺序来获得行为

## 详细示例

```python
from pydantic import BaseModel, Field

from reconcile import dependency, reconcile


class TrainingSpec(BaseModel):
    num_steps: int = 2000
    lr: float = 3e-4
    scheduler_kind: str = "cosine"


class JobSpec(BaseModel):
    training: TrainingSpec = Field()  # Nested participant: filled with the TrainingSpec object itself.
    total_steps: int = Field()  # Required derived field: must exist after reconcile().
    effective_lr: float = Field(default=1e-4)  # Fallback default if the provider cannot run.
    scheduler_label: str = Field(default="constant")  # Another derived field with fallback default.
    warmup_steps: int = 100  # Normal local field checked by a cross-object validator.

    @dependency(training)
    def _(self, training: TrainingSpec) -> TrainingSpec:
        return training

    @dependency(total_steps)
    def _(self, training: TrainingSpec) -> int:
        return training.num_steps

    @dependency(effective_lr)
    def _(self, training: TrainingSpec) -> float:
        return training.lr

    @dependency(scheduler_label)
    def _(self, training: TrainingSpec) -> str:
        return training.scheduler_kind

    @dependency
    def validate_warmup(self, training: TrainingSpec) -> None:
        if self.warmup_steps >= training.num_steps:
            raise ValueError("warmup must be smaller than total steps")


training = TrainingSpec()
job = JobSpec()
reconcile(job, training)
```

## 适用场景

- 某个字段的值来自另一组配置模型
- 多个模型需要在“完成态”上做一致性检查
- 你希望保留 Pydantic 的字段约束和类型系统，但把最终补全放到第二阶段

## 不适用场景

- 值本来就应该在构造时直接传入
- 你需要通用服务定位或生命周期管理容器
- 你希望未完成对象也具备稳定的序列化和业务语义
