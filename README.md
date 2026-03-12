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
- 方法名本身不参与解析，建议使用描述性的英文名称

```python
class Scheduler(BaseModel):
    num_steps: int = Field()

    @dependency(num_steps)
    def derive_num_steps(self, training: TrainingSpec) -> int:
        return training.num_steps
```

在这个例子里，`num_steps` 在 complete model 中必需，但允许在构造阶段暂缺。

### `@dependency`

声明一个 cross-object validator。

- 它不为字段产出值
- 它用来检查 participant 之间的语义关系
- 它在 reconcile 的 validator 阶段运行

```python
class Optimizer(BaseModel):
    lr: float = 1e-3

    @dependency
    def _lr_positive(self, training: TrainingSpec) -> None:
        if self.lr <= 0:
            raise ValueError("lr must be positive")
```

### `reconcile(*participants)`

把一组 participant 从 partial state 推进到 complete state，并返回原顺序的对象元组。

```python
scheduler, training, optimizer = reconcile(
    Scheduler(),
    TrainingSpec(num_steps=2000),
    Optimizer(lr=1e-3),
)
```

## 语义保证

这些规则是 `reconcile` 的公开语义，而不是当前实现的偶然现象。

- `Field()` 表示 complete-time required，不等于 constructor-time required
- 手动赋值优先于 provider 派生
- `Field(default=...)` 和 `default_factory=...` 可以作为 unresolved fallback
- 无 target 的 `@dependency` 在缺依赖时默认跳过
- `Optional[T] = None` 会在缺依赖时显式收到 `None`
- 类型解析支持按实例类型和继承关系匹配
- 多个候选同时匹配同一类型时会报歧义错误
- 一个字段只能有一个 provider
- 字段约束按 complete state 的最终值校验

## 不保证的内容

`reconcile` 明确只保证 complete state 的语义。

- partial model 不承诺可安全序列化
- partial model 的 `model_dump()` 形状不是公开契约
- `reconcile` 不是通用 DI 容器
- 普通多分支 union 不会自动解析
- 不应依赖 provider 的声明顺序来获得行为

## 最小示例

### 外部实例化字段

```python
from pydantic import BaseModel, Field

from reconcile import dependency, reconcile


class TrainingSpec(BaseModel):
    num_steps: int = 1000


class AdamWOptimizerSpec(BaseModel):
    lr: float = 1e-3


class LinearWarmupSchedulerSpec(BaseModel):
    warmup_steps: int = 0
    num_steps: int = Field()
    lr: float = Field()

    @dependency(num_steps)
    def derive_num_steps(self, training: TrainingSpec) -> int:
        return training.num_steps

    @dependency(lr)
    def derive_lr(self, optimizer: AdamWOptimizerSpec) -> float:
        return optimizer.lr


scheduler, *_ = reconcile(
    LinearWarmupSchedulerSpec(warmup_steps=100),
    TrainingSpec(num_steps=2000),
    AdamWOptimizerSpec(lr=1e-3),
)

assert scheduler.num_steps == 2000
assert scheduler.lr == 1e-3
```

### 可选依赖与 fallback

```python
from pydantic import BaseModel, Field

from reconcile import dependency, reconcile


class TrainingSpec(BaseModel):
    num_steps: int = 1000


class OptionalDeps(BaseModel):
    label: str = Field()

    @dependency
    def check(self, training: TrainingSpec | None = None) -> None:
        if training is not None and training.num_steps < 0:
            raise ValueError("negative steps")

    @dependency(label)
    def derive_label(self, training: TrainingSpec | None = None) -> str:
        if training is None:
            return "default"
        return f"steps={training.num_steps}"


(obj,) = reconcile(OptionalDeps())
assert obj.label == "default"
```

## 适用场景

- 某个字段的值来自另一组配置模型
- 多个模型需要在“完成态”上做一致性检查
- 你希望保留 Pydantic 的字段约束和类型系统，但把最终补全放到第二阶段

## 不适用场景

- 值本来就应该在构造时直接传入
- 你需要通用服务定位或生命周期管理容器
- 你希望未完成对象也具备稳定的序列化和业务语义
