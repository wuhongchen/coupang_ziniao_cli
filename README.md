# Coupang 紫鸟经营数据 CLI

通过本机紫鸟 / ZClaw 已授权店铺浏览器采集 Coupang Wing 和广告中心数据，导出 Excel、页面快照和商品头图，校验后按店铺、运行日期、数据分类保存。

仓库只包含通用代码和虚构测试样例；不提供店铺配置、业务数据、密钥或登录态。广告功能仅下载数据，不调价、不改预算、不增删词、不发布广告；不创建定时任务、不直调 Coupang 页面外数据接口。

## 安装

需要 Python 3.10+、已安装的 `ziniao-cli`、运行中的紫鸟客户端及合法店铺权限。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export WING_OPS_PYTHON="$PWD/.venv/bin/python"
export WING_EXPORT_PYTHON="$WING_OPS_PYTHON"
OPS="$PWD/operations-cli/wing-ops"
"$OPS" catalog
"$OPS" doctor
"$OPS" stores
```

## 配置与多店铺

以下所有变量均由使用者在自己的环境中提供，不能把真实值提交到 Git。

```bash
./export-cli/wing-export --output-root "$DATA_ROOT" --configure \
  --browser-id "$BROWSER_ID" --store-name "$STORE_NAME" \
  --expected-title "$EXPECTED_WING_TITLE" --ads-identity "$MERCHANT_CODE" \
  --download-dir "$DOWNLOAD_DIR"
"$OPS" --output-root "$DATA_ROOT" --store-id "$BROWSER_ID" check-store
```

`BROWSER_ID` 是 `stores` 返回的紫鸟数字浏览器 ID，不是商品 ID。每店使用独立 `DATA_ROOT`，店铺身份不允许被后续命令覆盖。`DOWNLOAD_DIR` 必须是紫鸟实际自动保存目录，不猜测其他账号路径。

API Key 不接收命令行明文，可选择本机已有配置，或用以下一种方式提供：

```bash
"$OPS" --output-root "$DATA_ROOT" --ziniao-api-key-env MY_ZINIAO_KEY check-store
"$OPS" --output-root "$DATA_ROOT" --ziniao-api-key-stdin check-store
"$OPS" --output-root "$DATA_ROOT" --ziniao-config-dir "$CONFIG_DIR" check-store
```

动态 Key 使用验证过的临时隔离目录，结束清理。动态 Key 与配置目录不能同时指定。时区通过全局 `--timezone "$TIMEZONE"` 或环境变量 `WING_TIMEZONE` 传入；省略时沿用执行机器本地时区。子命令及归档继承本次设置。平台韩国业务日及归因定义不随本地运行时区改变。

## 显式传入采集范围

不再内置类目、广告查询天数、销售分析天数或费用下载间隔。缺参数时失败，不猜测下载范围。

| 指令 | 功能 | 必填范围参数 |
| --- | --- | --- |
| `sales-days` | 按日期选择器逐日导出销售及生成增长分析 | `--end`、`--days` |
| `advertising` | 逐日商品关键词广告原表 | `--start`、`--end` |
| `bills` | 本月/上月广告账单与结算费用 | `--fee-interval-days` |
| `analytics` | 商品目录、指定类目、选项关键词流量及已有数据分析 | `--category-id`、`--start`、`--end` |
| `collect category` | 指定类目页面可见榜单 | `--category-id` |
| `collect keywords-traffic` | 已知选项关键词、渠道和流量 | `--start`、`--end` |
| `collect ads-keywords` | 广告逐日商品关键词报表 | `--start`、`--end` |
| `collect fee-*` | 单类费用原表 | `--fee-interval-days` |

```bash
"$OPS" --output-root "$DATA_ROOT" sales-days --end "$END_DATE" --days "$SALES_DAYS"
"$OPS" --output-root "$DATA_ROOT" advertising --start "$START_DATE" --end "$END_DATE"
"$OPS" --output-root "$DATA_ROOT" bills --date "$RUN_DATE" --fee-interval-days "$FEE_INTERVAL_DAYS"
"$OPS" --output-root "$DATA_ROOT" analytics --start "$START_DATE" --end "$END_DATE" --category-id "$CATEGORY_ID"
"$OPS" --output-root "$DATA_ROOT" collect sales-daily --date "$BUSINESS_DATE"
"$OPS" --output-root "$DATA_ROOT" collect sales-window --start "$START_DATE" --end "$END_DATE"
"$OPS" --output-root "$DATA_ROOT" collect inventory
"$OPS" --output-root "$DATA_ROOT" collect images --all
"$OPS" --output-root "$DATA_ROOT" collect reviews
"$OPS" --output-root "$DATA_ROOT" analyze
"$OPS" --output-root "$DATA_ROOT" archive
```

`--date` 是运行/检查日，不是任意账期。费用只下载当前唯一可见结算批次；多个批次停止，不猜测。费用间隔为成功下载后的滚动正整数天数。广告账单仍按页面本月/上月选择；广告归因字段为平台原始定义，不能通过参数伪造其他归因窗口。

`--days` 取正整数，销售采集限制在单次可控范围内。已完成销售轮次可用 `--refresh` 保留旧历史后重下。修改查询范围不能复用原查询的完成标记。

原 `sales-seven` 已改为 `sales-days --days "$SALES_DAYS"`；原费用 `--period` 已改为 `--fee-interval-days "$FEE_INTERVAL_DAYS"`。旧脚本须同步更新。

## 全部独立采集功能

- 销售与库存：`sales-daily`、`sales-window`、`inventory`、`returns`、`movement`。
- 广告：`ads-keywords`、`ads-bill-current`、`ads-bill-previous`。
- 页面与图片：`category`、`products`、`keywords-traffic`、`reviews`、`images`。
- 费用：`fee-sales`、`fee-fulfillment`、`fee-storage`、`fee-return`、`fee-removal`、`fee-removal-delivery`、`fee-compensation`、`fee-extra`、`fee-review`。

`inventory` 是本地文件盘点；`collect inventory` 才是在线库存下载。`ads download` / `ads collect` 是广告下载别名，范围参数同 `collect ads-keywords`。`import` 导入已有核心报表，需传 `--file`、`--kind`、`--start` 和 `--end`。`analyze` 只解释已有数据，不代表已采齐。

## 保存路径

```text
<DATA_ROOT>/
  state/
    store_profile.json             # 本地配置，不提交
    report_pipeline.sqlite3        # 原表索引和已解析批次
    reports/                       # 校验过的原始报表
    functions/<运行日期>/           # 独立功能断点
    full_exports/                  # 页面快照和采集依据
    analyses/                      # Excel / JSON 分析
  data/stores/<浏览器ID>/
    store.json
    products.json / options.json
    images/
    catalog.json / catalog.csv
    <运行日期>/<运行批次>/<数据分类>/
    workbench/latest.json          # 最新发布指针
    workbench/revisions/           # 不可变快照
  data/unassigned/                 # 无法核验归属的文件
```

原始下载先进入紫鸟设置目录，校验后复制到 `state` 和店铺归档。运行日期与业务日期分别保留。成功采集自动归档并更新发布指针，兼容工作台可读取更新；完整工作台和登录服务不包含在本仓库。

## 容错与口径

在线动作前核验客户端、授权、登录和账户身份。同店并发锁及断点避免重复点击；文件稳定性与 SHA256 验证后发布。登录失效、验证码、结构变动、下载目录错误、多个结算批次或报告歧义时保留失败，正常恢复后使用同指令续跑。不绕过权限、不换通道重放下载。

明确无权限单列跳过；搜索词展示门槛不足标数据不足。缺失不补零，已核验零值和暂定分别保留。广告曝光与全渠道访问口径不同，归因可能回补且包含跨商品购买。账单与报表差额不强行对平；缺成本不输出实际净利润。

## 验证

```bash
python3 -m compileall -q backend operations-cli
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests
```

迁移环境仍需实际核验授权、登录、目录和原始文件。`state/`、`data/`、凭据、数据库和运行日志已排除提交；不要将真实配置放进其他未忽略路径。

## 修复记录

### 断点兼容与独立包依赖

- 修复已完成核心报表断点因增加 `provisional` 等采集后派生字段而被误判为日期冲突的问题。恢复时只比较请求的报表类型、开始日和结束日；店铺身份校验与原始文件完整性校验继续生效，不同查询范围仍拒绝复用。
- 补齐头图采集所需的 `backend/image_cache.py`，避免独立安装后出现缺少模块的错误。保留图片类型校验、大小限制与重定向限制。
- 新增回归测试：同范围派生字段兼容、不同范围拒绝复用，以及全部已发布能力模块的导入检查。完整测试集共33项。

升级代码后直接以原店铺目录和原参数续跑；不删除或手改断点，不移动业务数据，不重新配置密钥。此修复不改变命令参数，也不增加平台写入能力。
