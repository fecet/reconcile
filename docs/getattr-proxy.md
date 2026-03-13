# `__getattr__` Proxy 方案设计

当前 `reconcile()` 采用 push-based 迭代模型。本文档记录一种 pull-based 替代方案的可行性分析：通过劫持属性访问，在 dependency 字段被读取时自动触发解析。

## 当前模型

```text
reconcile(*participants)
         │
         ▼
    ┌─────────┐
    │  while  │◄──────────────────┐
    │  loop   │                   │
    └────┬────┘                   │
         │                        │
         ▼                        │
  for obj in models               │
  for dep in deps(obj)            │
         │                        │
         ├── field in fields_set? ─── skip
         │                        │
         ├── pool.call(dep.fn)    │
         │     │                  │
         │     ├── Unresolvable ──── skip
         │     │                  │
         │     └── result ────────── setattr(obj, field, result)
         │                        │   progress = True
         │                        │
         └── progress? ───────────┘
              no ──► break
```

特征：每轮遍历所有 model 的所有 dep，即使大部分已解析。复杂度 O(rounds × models × deps)。收敛依赖多轮迭代：某字段可能在第一轮返回 None（peer 未就绪），第二轮才成功。

## `__getattr__` proxy 方案

核心思路：将「主动推送」改为「按需拉取」。

```text
reconcile(*participants)
         │
         ▼
  ┌──────────────┐
  │  promote     │  obj.__class__ = ProxyClass
  │  + dict pop  │  obj.__dict__.pop(dep_field)
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │  trigger     │  for dep in deps: getattr(obj, dep.field_name)
  └──────┬───────┘
         │
         │  首次访问 dep_field:
         │  __dict__ 无此 key ──► __getattr__ 触发
         │    │
         │    ├── pool.call(dep.fn)
         │    │     dep.fn 内部访问其他 participant 的字段
         │    │     ──► 递归触发对方的 __getattr__
         │    │
         │    └── 写回 __dict__ ──► 后续访问直接命中 dict
         │
         ▼
  ┌──────────────┐
  │  demote      │  obj.__class__ = OriginalClass
  └──────┬───────┘
         │
         ▼
  cross-validate + field-validate (不变)
```

前提：单字段单 provider（见「迁移后的新契约」一节）。字段依赖边不是预先声明的元数据，而是在 provider 实际读取其他 dep 字段时动态暴露；`reconcile()` 期间通过解析栈检测回边。对无环的实际解析路径，每个 dep 字段最多解析一次，复杂度近似 O(total_deps)。

## 为何 `__getattr__` 优于 `__getattribute__`

Pydantic 将字段值存储在 `__dict__` 中。Python 的属性查找链：

```text
obj.x
  │
  ├── data descriptor on type? ──► descriptor.__get__
  │
  ├── key in obj.__dict__? ──► 直接返回 (C 层, 快)
  │
  ├── non-data descriptor on type? ──► descriptor.__get__
  │
  └── type.__getattr__(obj, 'x') ──► 自定义回退
```

`__getattribute__` 拦截上述整个链的入口，每次属性访问都经过 Python 函数调用，包括 Pydantic 内部的 `__class__`、`__dict__`、`__pydantic_fields_set__` 等。实测一次 setattr + 检查 `model_fields_set` 就产生 11 次 `__getattribute__` 调用。

`__getattr__` 只在 `__dict__` 查找失败时触发。字段一旦写回 `__dict__`，后续访问直接走 C 层 dict 查找，`__getattr__` 不再介入。

| 方案 | 字段已解析后的开销 |
|------|-------------------|
| 无拦截 (baseline) | 1.0x |
| `__getattr__` | 1.1x |
| `__getattribute__` (basic) | 5.9x |
| `__getattribute__` (full) | 8.0x |

## Pydantic V2 `__class__` swap 兼容性

`BaseModel.__slots__` 为 `('__dict__', '__pydantic_fields_set__', '__pydantic_extra__', '__pydantic_private__')`，使用标准 dict-based 存储。

实验结论：

- `obj.__class__ = SubClass` 在 SubClass 继承自原始类且**不添加新 `__slots__`** 时成功
- swap 后 `model_fields`、`model_fields_set`、`__dict__` 正常继承
- `Pool.deps(ProxyClass)` 正确发现 Dependency 对象
- swap 回原始类数据无损
- 添加 `__slots__`（如 `('_pool',)`）会触发 `TypeError: object layout differs`

约束：proxy 类不能定义额外 `__slots__`，所有 proxy 状态必须存储在 closure 中。

## 运行时字段依赖图与参与者环

参与者之间可以互相引用（participant 级别的环是正常的）。字段依赖边则在 `reconcile()` 期间按实际属性访问动态暴露：

```text
getattr(A, "x")
  │
  └──► A.x provider 读取 B.y
        │
        └──► 运行时生成边 A.x ──► B.y
```

这意味着：

- 不能在定义阶段可靠建图，因为 provider 函数体里的普通 Python 属性访问不是声明式元数据
- 也不必在 `reconcile()` 入口预扫描整张图，因为条件分支可能让某些边在本次运行中根本不会被访问
- 只对**实际走到的解析路径**做环检测；未访问到的潜在边不参与本次判断

运行时查环的核心是维护当前递归路径：

```text
resolution_stack

[] 
└──► 解析 A.x        stack = [A.x]
      └──► 读取 B.y  stack = [A.x, B.y]
            └──► 再读 A.x
                  ▲
                  └── 已在 stack 中 ──► cycle error
```

手动赋值会自然打断环，因为 seeded 字段保留在 `__dict__` 中，不会进入 proxy 解析路径：

```text
MutualA.value = 5 (seeded)   MutualB.value = unresolved dep

B.value provider 读取 A.value
  └──► A.value 直接命中 __dict__
        不进入 __getattr__
        不形成环
```

## 收敛模型

当一次实际解析路径无环时，`__getattr__` 递归解析等价于带 memoization 的 DFS 展开：

```text
getattr(obj, 'x')
  │
  └──► __getattr__ 触发
        │
        ├── x 已在 resolving_fields 中?
        │     └──► 是 → cycle error
        │
        ├── push x 到 resolution_stack / resolving_fields
        │
        ├── dep.fn 内部读取其他字段
        │     │
        │     ├── 常规字段 → __dict__ 命中，直接返回
        │     │
        │     └── dep 字段 → __dict__ 未命中
        │           │
        │           └──► 递归触发对方的 __getattr__
        │
        ├── 解析成功 → BaseModel.__setattr__ 写回
        │
        ├── Unresolvable → 恢复 saved default 到 __dict__
        │
        └── finally:
              pop x from resolution_stack / resolving_fields
```

不需要 while-loop，也不需要 progress 标记；但仍然需要运行时循环检测。单次 `reconcile()` 中，每个 dep 字段最多解析一次。

解析失败时将 saved default 放回 `__dict__`，后续访问直接返回 default 值，不再触发 `__getattr__`。

一旦检测到回边，应立即报错，而不是回退到 saved default 继续运行。这样循环依赖会作为显式配置错误暴露出来，而不是被隐式“打断”成某个顺序相关的结果。

多次 `reconcile()` 调用天然支持：每次调用重新 pop 未手动设置的 dep 字段，重新触发解析。

## 状态管理与异常安全

### 状态管理

proxy 不能加 `__slots__`，往 `__dict__` 放私有键会污染 Pydantic 模型。方案是用 closure 捕获所有 proxy 状态：

- `pool` — 类型到实例的映射
- `resolution_stack` — 当前递归路径，用于生成可读的环错误信息
- `resolving_fields` — 当前正在解析的字段集合，用于 O(1) 检测回边
- `saved_defaults` — 被 pop 的默认值

这些都在 `reconcile()` 栈帧上，随函数返回自动清理。

### 异常安全

`try/finally` 保证 demote：

```text
reconcile()
  │
  ├── try:
  │     promote all models
  │     trigger resolution
  │     cross-validate
  │     field-validate
  │
  └── finally:
        demote all models back
        restore unresolved defaults
```

实验证实：异常后 `__class__` swap 回原始类和 `__dict__` 恢复均正常。

### 字段写回

`__getattr__` 内通过 `BaseModel.__setattr__` 写回值可正确更新 `model_fields_set`。直接写 `self.__dict__[name]` 不会更新 `__pydantic_fields_set__`，需手动操作。

## 迁移后的新契约

将 README 的语义保证分为三类：保留、收紧、待定。

### 保留

| 语义保证 | 说明 |
|----------|------|
| `Field()` 表示 complete-time required | 不变 |
| 手动赋值优先于 provider | 不变（手动赋值的字段保留在 `__dict__`，不被 pop） |
| `Field(default=...)` 作为 fallback | 不变（saved default 在解析失败时恢复） |
| 无 target 的 `@dependency` 缺依赖时跳过 | 不变（Phase 2 逻辑不受 proxy 影响） |
| 类型按实例和继承匹配 | 不变（Pool 逻辑不变） |
| 歧义报错 | 不变 |
| 字段约束按 complete state 校验 | 不变（Phase 3 逻辑不变） |

不保证的内容也不受影响——partial model 的行为仍不是公开契约。

### 收紧：单字段单 provider

README 声明「一个字段只能有一个 provider」，但当前实现并未强制——同一字段可以注册多个 `@dependency(field)`，while-loop 按声明顺序依次尝试，首个成功的胜出（参见 `test_multiple_deps_on_factory_field`）。

`__getattr__` 方案要求在 `__getattr__` 触发时确定性地选择唯一 provider。若保留多 provider，需要在 proxy 中维护 field → ordered providers 的状态机，显著增加复杂度。

**建议：收紧为单字段单 provider，在 `Dependency.__set_name__` 阶段检测重复并报错。** 理由：

- README 已经声明了这条语义，收紧只是将声明变为强制
- 现有唯一的多 provider 测试 (`test_multiple_deps_on_factory_field`) 断言结果是 `in [A, B]`——语义模糊，不适合作为公开行为
- 单 provider 使 `__getattr__` 实现清晰：一个字段对应一个 closure，无需状态机

### 新增：`reconcile()` 阶段字段环报错

参与者之间允许互相引用，provider 也可以读取对方的 dep 字段；字段级别的环不在定义阶段预检查，而是在 `reconcile()` 期间按实际访问路径检测。

具体语义：

- 当 `__getattr__` 试图解析某字段时，先将 `(id(obj), field_name)` 压入 `resolution_stack`
- 若递归过程中再次访问栈中的同一字段，立即抛出 cycle error，并携带路径如 `A.x -> B.y -> A.x`
- 成功解析或失败回退 default 时，在 `finally` 中弹栈，避免异常污染后续状态
- 若某条边因为条件分支未被实际访问，本次 `reconcile()` 不会因为这条潜在边报错
- 若某个字段已手动赋值，它作为叶子节点直接从 `__dict__` 返回，可以自然打断原本可能形成的环

这使得 README 中「不应依赖 provider 的声明顺序」更接近真实契约：对于无环且 provider 行为确定的实际解析路径，结果不依赖 while-loop 的迭代顺序；对有环路径则直接报错，不再产生顺序相关的旋转结果。

现有的字段环测试应按新语义拆分：

- 无 seed 的环（如 NodeX/NodeY、Ring\*）改为断言 `reconcile()` 期报环错误
- 由手动赋值打断的环（如 `MutualA(value=5), MutualB()`）继续保留为成功案例

### 待定：`Optional[T] = None` 缺依赖时收到 `None`

README 声明这条语义保证，但当前 `Pool.resolve()` 不支持 union 类型解析——`resolve(TrainingSpec | None)` 会因找不到 `TrainingSpec | None` 键而抛 `KeyError`，导致整个 dep 被跳过（`Unresolvable`），函数根本不会被调用。也就是说，README 里的 `Optional[T] = None` 示例按当前实现并不能工作。

如果迁移时同步实现（在 `Pool.resolve()` 中拆解 union、对 `None` 分支返回 `None`），应标注为「同时引入的新语义」。如果不实现，应从 README 语义保证中移除或标注为 planned。

## 总结

| 维度 | 结论 | 风险 |
|------|------|------|
| Pydantic V2 `__class__` swap | 可行，不能加 `__slots__` | 低 |
| `__getattr__` vs `__getattribute__` | `__getattr__` + dict pop 性能远优 | — |
| 字段依赖图 | `reconcile()` 期间按实际访问路径检测回边并报错 | 低 |
| 单字段单 provider | 收紧契约，类定义阶段报错 | 低 |
| 收敛模型 | memoized DFS + 运行时查环，每个 dep 最多解析一次 | 低 |
| 性能 | 优于当前方案 | 低（正面） |
| 状态管理 | closure 捕获，不污染实例 | 低 |
| 异常安全 | try/finally demote | 低 |
