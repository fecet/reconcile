# Tests

## 结构

```
tests/
├── models/          # 共享模型定义
│   ├── training.py  # 训练相关模型及派生类型
│   └── circular.py  # 循环依赖场景模型
└── test_reconcile.py
```

## 约定

**模型定义在 `models/` 下，测试文件中最大程度复用。**
只有单个测试专属的临时模型才内联在测试方法内。

模型按领域分文件（`training.py`、`circular.py`），不按功能拆分（不为 loss、optional 等单独建文件）。新增模型优先放入现有文件。

**测试按关注点分类为 class：**

- `TestResolution` — 正常解析路径
- `TestErrors` — 预期失败与错误信息
- `TestFeatures` — 单项功能验证
- `TestCircular` — 循环依赖收敛

**辅助函数 `assert_reconciled`** 封装了 reconcile 调用和字段断言，用 `expect={idx: {field: value}}` 声明预期。

## 共享模型

| 模型 | 服务测试 |
|------|---------|
| `TrainingSpec` | 几乎所有测试的依赖源 |
| `AdamWOptimizerSpec` | cross_object, validator |
| `LinearWarmupSchedulerSpec` | cross_object, manual_override, model_fields_and_dump |
| `BaseLoss` / `MSELoss` / `MAELoss` | subclass_resolution, subclass_ambiguity |
| `CrossEntropyLoss(BaseLoss)` | multi_participant |
| `NeedsLoss` | subclass_ambiguity |
| `DataLoaderSpec` | field_constraints_validated, field_default_as_fallback |
| `ScheduleSpec` / `JobSpec` | nested_model_field_resolution |
