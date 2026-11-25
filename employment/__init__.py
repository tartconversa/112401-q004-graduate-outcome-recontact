"""毕业九十天就业质量对比与复访抽样推断包。

仅使用 Python 3.11 标准库。按届（cohort）处理，模块分工：

- load：CSV 读取与资料校验
- roster：学籍版本、主校认定与争议影响
- identity：代号映射的时点解析与重复代号合并
- classify：九十天观察窗内的证据分类（互斥结论）
- survey：复访框架、抽中机会、权重与跨轮覆盖
- estimate：覆盖、加权估计、部分识别区间与院校比较
- planning：经费约束下的抽访群组排序与派单计划
- changes：跨轮快照的结论变化归因与抽样机会退回
- snapshot：把以上环节按同一版口径串成一次届分析
- report：机器可读 JSON 与人工复核 Markdown
"""

__version__ = "1.0.0"
