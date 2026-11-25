# 毕业去向区间估计与复访抽样交接

各校用毕业生代号交换九十天去向演练资料。收入为月固定收入的元值，周工时为已观察到的合同工时。不同观察来源可能互相冲突。

## 资料约定

CSV 使用 UTF-8，首行为列名。多值字段以竖线分隔。空单元格表示来源未填。

- register.csv。该届学籍名单。main_status=disputed 时列出可成立的主校。version 记录条目修订。 received 是收到该版学籍记录的日期。
  字段为 `student`、`school_options`、`program`、`graduated`、`main_status`、`version`、`received`。
- crosswalk.csv。来源代号与学籍认定。distinct 和 duplicate 说明后到的代号关系结论，applies_to 记录涉及的轮次。
  字段为 `mapping`、`external`、`student`、`relation`、`asof`、`applies_to`。
- outcomes.csv。来源原话转成证据条目。scope 对应观察起止日期，studies 表示继续读书，可与就业并存。 九十天窗口从毕业次日计起。至少一段连续的可证期间须同时达到工时及收入门槛，继续读书不抵消符合条件的工作。
  字段为 `evidence`、`external`、`source`、`start`、`end`、`hours`、`income`、`studies`、`received`。
- policies.csv。分地区比较口径。days 为观察窗内最低有证据持续日数。threshold_hours 与 threshold_income 是分类下限。
  字段为 `policy`、`area`、`window_days`、`days`、`threshold_hours`、`threshold_income`。
- frames.csv。复访组源框架，codes 为下发时的匿名联系单元，cost 是一次联系所需话务额度，kind 区分候选和指定复查。 随机拨号在线下按冻结分层名册做无放回等概率抽取。capacity 是演练中该层已批或可批的最大拨号数。R1 配额为 8 单位，R2 为 9 单位，先扣当轮指定复查的费用。R2 的随机框是在已知 R1 回执后形成。
  字段为 `round`、`frame`、`area`、`stratum`、`codes`、`cost`、`kind`、`capacity`、`cutoff`。
- visits.csv。已通知的复访及接通回执。numer/denom 记录来源抽中机会，空白表示非概率复核。
  字段为 `visit`、`round`、`frame`、`code`、`kind`、`numer`、`denom`、`dispatched`、`reply`、`replied`。
- schools.csv。主校与适用地区口径，可跨多届复用。
  字段为 `school`、`area`。

## 检查演练资料

使用 Python 3.11 标准库，无需安装第三方依赖。在项目根目录运行下列命令可检查 CSV 读取、资料关联及样例记录完整性。

```bash
python3 -m unittest discover -s tests -v
```

## 按届分析入口

`employment` 包把一次届分析按同一版口径串成快照，三个子命令对应
"收答复 → 更新对比 → 派下一轮"的工作循环。隔离模式（`-I`）下需把
项目根加入 `sys.path`，普通环境可直接用 `python3 -m employment ...`。

```bash
# 1. R1 冻结时点（只有框架、尚无派单）
python3 -m employment run --data samples --asof 2026-09-22 --out out/r1

# 2. R1 答复收齐后更新，并与冻结快照做变化归因
python3 -m employment run --data samples --asof 2026-09-27 \
    --prev-asof 2026-09-22 --out out/r2

# 3. 生成 R2 抽访计划（指定复查先扣预算，余额按影响/效率分配）
python3 -m employment plan --data samples --asof 2026-09-27 \
    --round R2 --key-schools A --out out/r2

# 4. 解释单个学生跨轮的真实抽中过程（条件合成，不平均各轮）
python3 -m employment coverage --data samples --asof 2026-09-27 \
    --student G04 --through-round R2 --r2-draws F4=2,F5=4
```

## 口径与推断规则

- **分母**：仅含截至时点主校已确认的学籍学生；`disputed` 且无确认版本者
  不进任何院校/专业分母，其候选院校与受影响分母在
  `disputed_main_school` 与 `comparison.md` 中列出。
- **观察窗**：自毕业次日起 90 天；证据先按窗裁剪，连续可证期间须同时
  达到当地工时、收入门槛且持续不少于 `policy.days`，读书不抵消工作。
- **互斥结论**：每人每版口径只出一个标签
  （qualified / below / studying / not_working / unknown），冲突逐条
  留痕；联系不上与无佐证保持 unknown，**未知不记失业**。
- **权重**：随机复访按冻结框架的实拨/名册数得到抽中机会并核对留痕；
  指定复查（numer/denom 空白）无概率，只作个案核实。跨轮按条件过程
  合成累积机会（排除式或独立重抽），不平均各轮结果。
- **区间**：部分识别界（未知人群达标率 0~1）叠加抽样标准误；区间重合
  或差值含 0 的比较一律保留"无法判定先后"。
- **身份合并**：crosswalk 按时点解析；派单后才确认的重复代号会把证据
  重新归并并记录事件。若新主校地区与旧框架冻结地区不一致，该
  (学生, 框架) 机会退回，不参与权重，退回原因与下一轮补救依据写入
  `changes.md`。

## 输出视图

每次 `run` 在输出目录生成机器可读 JSON 与五张人工复核视图：
`frozen_rosters.md`（冻结名单与抽中机会）、`case_verification.md`
（只作个案核实的联系）、`inference_replies.md`（可进入推断的答复与
退回标注）、`uncovered.md`（未覆盖人群与原因）、`comparison.md`
（覆盖、区间、缺失来源与比较结论）；提供 `--prev-asof` 时另出
`changes.md`，把每次结论变化归为数据补齐、身份/主校复核、口径复核、
抽样覆盖判断变化四类。
