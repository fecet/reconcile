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
- `TestHitchhike` — 搭便车发现与显式优先
- `TestCircular` — 循环依赖收敛

**辅助函数 `reconcile_case`** 封装了 reconcile 调用并返回具名 participant；后续通过 `.expect(workflow={...}, training={...})` 按名字声明预期。

## 共享模型

| 模型 | 服务测试 |
|------|---------|
| `TrainingSpec` | 几乎所有测试的依赖源 |
| `AdamWOptimizerSpec` | cross_object, validator |
| `WorkflowSpec` | cross_object, manual_override, model_fields_and_dump, nested_model_field_resolution, field_default_as_fallback |
| `BaseLoss` / `MSELoss` / `MAELoss` | subclass_resolution, subclass_ambiguity |
| `CompositeLoss(BaseLoss)` | composite_hitchhike_prefers_explicit |
| `CrossEntropyLoss(BaseLoss)` | multi_participant |
| `NeedsLoss` | subclass_ambiguity, composite_hitchhike_prefers_explicit |
