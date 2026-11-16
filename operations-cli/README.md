统一说明请见仓库根目录 [README](../README.md)。

### 火箭选项销售价

在已绑定店铺的中文 Wing 价格管理页查询单个火箭选项，按韩元金额降低销售价。将紫鸟 CLI 加入 PATH，使用已安装项目依赖的 Python 运行：

```sh
python operations-cli/rocket_price.py --output-root "$DATA_ROOT" --option-id "$OPTION_ID" --expect "$OLD_PRICE" --to "$NEW_PRICE"
```

默认仅预览。追加 `--apply` 后核对旧售价、选中唯一选项、提交差额并重新搜索回读。返回值包含售价、页面最终购买价和时间。若已达目标，直接返回。促销锁价返回 `blocked_promotion`；其他未核实结果返回 `unverified`，应先重新查询。平台可能延迟生效。
