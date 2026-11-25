# 可进入抽样推断的答复与权重

所有带抽中机会的随机复访都在此留痕（含未接通与重复答复）；
权重为该学生在已发生各轮的合成累积机会倒数，unknown 表示
该答复未产生事实定性，但其抽中机会仍计入覆盖过程。
标“已退回”的机会因身份/主校更正无法映射新分母，不参与推断。

| 复访 | 轮次 | 框架 | 学生 | 答复 | 本次机会 | 累积权重 | 归类 | 状态 |
|---|---|---|---|---|---|---|---|---|
| V1 | R1 | F1 | G03 | unreachable | 2/3 | — | working_below_standard | 已退回，不参与推断 |
| V2 | R1 | F2 | G05 | already-replied | 3/7 | — | qualified_employment | 有效 |
| V3 | R1 | F2 | G08 | reachable-no-proof | 3/7 | — | unknown | 有效 |
| V4 | R1 | F2 | G10 | reachable-no-work | 3/7 | 2.333333333333333 | not_working | 有效 |
| V6 | R1 | F1 | G12 | reachable-no-proof | 2/3 | — | unknown | 有效 |
