# Tests

## 结构

```
tests/
├── models/
│   ├── training.py   # 训练核心：TrainingSpec, Loss 层级, NeedsLoss
│   ├── parallel.py   # 并行配置：MoESpec, ParallelSpec, ScaleSpec
│   └── workflow.py   # 编排层：AdamWOptimizerSpec, WorkflowSpec
└── test_reconcile.py
```

## 约定

**模型定义在 `models/` 下，测试文件中最大程度复用。**
只有单个测试专属的临时模型才内联在测试方法内（如 ring 环形依赖模型）。

模型按领域分文件，模仿 susser-tod 的组织方式。新增模型优先放入现有文件。

**跨文件前向引用**自然融入领域分工：
- `training.py` ↔ `parallel.py` 互不 import，dependency 注解用字符串引用，由 reconcile 的 `ProviderIndex._ns` 在运行时解析
- `TrainingSpec.global_batch_size` 和 `ParallelSpec.local_batch_size` 构成自然的循环依赖，无需人工 circular 模型

**测试按关注点分类为 class：**

- `TestResolution` — 正常解析路径（含循环依赖种子收敛）
- `TestErrors` — 预期失败与错误信息（含循环检测）
- `TestFeatures` — 单项功能验证
- `TestHitchhike` — 搭便车发现与显式优先
- `TestCrossFileForwardRef` — PEP 649 裸注解跨文件解析（3.14+）

**辅助函数 `reconcile_case`** 封装了 reconcile 调用并返回具名 participant；后续通过 `.expect(workflow={...}, training={...})` 按名字声明预期。
