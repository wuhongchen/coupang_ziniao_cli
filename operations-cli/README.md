统一说明请见仓库根目录 [README](../README.md)。

### 火箭选项销售价

改价前自动读取目标火箭选项的调价模式。固定售价用 `--to` 指定，自动调价须同时提供 `--min-price` 和 `--max-price`，使用 Wing 表单中该选项的最低价和设定价格字段。金额以韩元为单位。

```sh
python operations-cli/rocket_price.py set --output-root "$DATA_ROOT" --option-id "$OPTION_ID" --expect "$CURRENT_PRICE" --to "$NEW_PRICE"
python operations-cli/rocket_price.py set --output-root "$DATA_ROOT" --option-id "$OPTION_ID" --expect "$CURRENT_PRICE" --min-price "$MIN_PRICE" --max-price "$MAX_PRICE"
```

默认仅预览。追加 `--apply` 提交，然后重新搜索同一选项，固定售价核对开关与售价，自动调价核对开关与两个边界。省略子命令时同样先判断模式。平台可能延迟生效；若返回 `unverified`，先查询再决定是否重试。

查询指定火箭选项的自动调价开关、上下限、销售价和最终购买价：

```sh
python operations-cli/rocket_price.py auto-status --output-root "$DATA_ROOT" --option-id "$OPTION_ID"
```

关闭同一个选项的自动调价并重新搜索验证：

```sh
python operations-cli/rocket_price.py auto-off --output-root "$DATA_ROOT" --option-id "$OPTION_ID" --expect "$CURRENT_PRICE" --apply
```

去掉 `--apply` 可预览。若已关闭，返回 `already_disabled`。实际提交后，只有重新搜索读到开关已关闭且售价仍与预期一致才返回 `verified`。返回的 `minimum` 和 `maximum` 是页面显示值；关闭后即使仍显示区间，该区间也不再参与自动调价。页面未展示区间时返回 null。
