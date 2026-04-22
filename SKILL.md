---
name: csi300-basis-monitor
description: 当用户想每天粗看沪深300中性对冲产品相关的 IF 升水、贴水、期现基差、年化基差和主力合约时使用。适用于“今天 IF 贴水多少”“沪深300对冲产品的基差拖累大不大”“给我看一下 IF 主力和近月基差”这类请求；默认读取沪深300现货与 IF 各活跃合约行情，输出主力标记、剩余到期天数和对多现货空 IF 中性产品的 carry 含义。
---

# CSI300 Basis Monitor

## Overview

这个 skill 用来快速回答沪深300中性对冲产品每天最常见的一个问题：`IF 现在到底是升水还是贴水，大概会给产品带来正 carry 还是负 carry。`

默认做法是直接运行脚本，拉取沪深300现货和 IF 活跃合约行情，计算：

- 基差点数：`期货价 - 现货价`
- 基差率
- 年化基差率
- 主力合约标记
- 对 `多现货 / 空 IF` 中性产品的大致含义

## Workflow

1. 默认直接跑脚本，输出 Markdown 表格：

```bash
python3 /Users/jaysonyu/Desktop/stock_skill/csi300-basis-monitor/scripts/fetch_if_basis.py
```

2. 如果只想看主力合约：

```bash
python3 /Users/jaysonyu/Desktop/stock_skill/csi300-basis-monitor/scripts/fetch_if_basis.py --main-only
```

3. 如果要把结果交给别的程序继续处理：

```bash
python3 /Users/jaysonyu/Desktop/stock_skill/csi300-basis-monitor/scripts/fetch_if_basis.py --format json
```

4. 如果本机报 SSL 证书链问题，显式跳过校验：

```bash
python3 /Users/jaysonyu/Desktop/stock_skill/csi300-basis-monitor/scripts/fetch_if_basis.py --insecure
```

## Interpretation Rules

- `基差 = 期货 - 现货`
- `基差 < 0`：贴水
- `基差 > 0`：升水
- 对 `多现货 / 空 IF` 的沪深300中性对冲产品：
  - `贴水` 一般意味着偏负 carry，贴水越深，基差拖累越大
  - `升水` 一般意味着偏正 carry

不要把这里的输出直接说成产品真实收益。这个 skill 只粗看股指期货基差影响，不覆盖：

- Alpha 收益
- 现货组合相对沪深300的跟踪误差
- 换月执行价格
- 交易成本、冲击成本、融券与申赎摩擦
- 分红和股息点位调整

## Contract Logic

脚本默认按中金所 IF 规则推导活跃合约月份：

- 当月
- 下月
- 随后两个季月

到期日默认按“合约月份第三个周五”近似处理，用于粗看剩余天数和年化基差。遇法定假日顺延这种特殊情况，不要手算；如果用户要求精确到交割规则或换月日，先读 `references/methodology.md`。

## Browser Fallback

如果脚本返回空结果、数据明显陈旧，或者 Sina 行情接口字段变化：

1. 先重跑脚本并保留 `--insecure`
2. 再读 `references/methodology.md`
3. 如果仍异常，再使用 `Computer Use` 打开浏览器做人工核对：
   - 中金所沪深300股指期货产品页
   - 中金所延时行情页
   - 可访问的行情页面或原始接口返回

浏览器核对只作为 fallback，不要在脚本还能稳定跑通时改成纯视觉流程。

## Output Contract

默认输出 Markdown，字段包括：

- 现货时间戳
- 沪深300现货点位
- IF 合约代码
- 是否主力
- 近似到期日
- 剩余天数
- 期货价
- 基差点数
- 基差率
- 年化基差率
- 持仓量
- 对中性产品的 carry 提示

如果用户只要一句话总结，先跑脚本，再用结果浓缩成：

- 当前主力 IF 是升水还是贴水
- 年化基差大概多少
- 对多现货空 IF 是偏正 carry 还是偏负 carry

## Reference

只有在以下情况再读参考文件：

- 用户追问“年化基差怎么算”
- 用户追问“为什么这里把贴水理解成负 carry”
- 需要核对合约月份、第三个周五规则或浏览器 fallback

对应参考文件：

- `references/methodology.md`
