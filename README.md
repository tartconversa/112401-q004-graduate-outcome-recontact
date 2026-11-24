# 毕业去向区间估计与复访抽样交接

各校用毕业生代号交换九十天去向演练资料。收入为月固定收入的元值，周工时为已观察到的合同工时。不同观察来源可能互相冲突。

## 资料约定

CSV 使用 UTF-8，首行为列名。多值字段以竖线分隔。空单元格表示来源未填。

- register.csv。该届学籍名单。main_status=disputed 时列出可成立的主校。version 记录条目修订。
  字段为 `student`、`school_options`、`program`、`graduated`、`main_status`、`version`。
- crosswalk.csv。来源代号与学籍认定。distinct 和 duplicate 说明后到的代号关系结论，applies_to 记录涉及的轮次。
  字段为 `mapping`、`external`、`student`、`relation`、`asof`、`applies_to`。
- outcomes.csv。来源原话转成证据条目。scope 对应观察起止日期，studies 表示继续读书，可与就业并存。 九十天窗口从毕业次日计起。至少一段连续的可证期间须同时达到工时及收入门槛，继续读书不抵消符合条件的工作。
  字段为 `evidence`、`external`、`source`、`start`、`end`、`hours`、`income`、`studies`、`received`。
- policies.csv。分地区比较口径。days 为观察窗内最低有证据持续日数。threshold_hours 与 threshold_income 是分类下限。
  字段为 `policy`、`area`、`window_days`、`days`、`threshold_hours`、`threshold_income`。
- frames.csv。复访组源框架，codes 为下发时的匿名联系单元，cost 是一次联系所需话务额度，kind 区分候选和指定复查。 随机拨号在线下按冻结分层名册做无放回等概率抽取。capacity 是演练中该层已批或可批的最大拨号数。R1 配额为 8 单位，R2 为 9 单位，先扣当轮指定复查的费用。R2 的随机框是在已知 R1 回执后形成。
  字段为 `round`、`frame`、`area`、`stratum`、`codes`、`cost`、`kind`、`capacity`、`cutoff`。
- visits.csv。已通知的复访及接通回执。denom/numer 记录来源抽中机会，空白表示非概率复核。
  字段为 `visit`、`round`、`frame`、`code`、`kind`、`numer`、`denom`、`dispatched`、`reply`、`replied`。

## 检查演练资料

使用 Python 3.11.2 标准库，无需安装第三方依赖。在项目根目录运行下列命令可检查 CSV 读取、资料关联及样例记录完整性。

```bash
python3 -m unittest discover -s tests -v
```
