# 07 · 图表与格式化

> openpyxl 能做哪些图表？格式化的"专业感"从哪里来？

## openpyxl 支持的图表类型

| 类型 | 用途 |
|---|---|
| `BarChart` | 柱状对比（多季度、多产品） |
| `LineChart` | 时间序列 |
| `PieChart` | 比例（最多 7 个类别，多了乱） |
| `ScatterChart` | 相关性 / 散点 |
| `AreaChart` | 堆积面积（趋势叠加） |
| `BubbleChart` | 三维信息（X, Y, size） |
| `RadarChart` | 多维度对比（少用） |
| `StockChart` | 蜡烛图（股票） |
| `SurfaceChart` | 3D 曲面（罕用） |

## 基本模板

```python
from openpyxl.chart import BarChart, Reference

chart = BarChart()
chart.type = 'col'                       # 'bar' 横向, 'col' 纵向
chart.style = 11                         # 1-48 内置样式
chart.title = '2024 季度销售'
chart.x_axis.title = '季度'
chart.y_axis.title = '金额（万元）'

# 数据：B2:E5（行为 series, 列为 category）
data = Reference(ws, min_col=2, max_col=5, min_row=1, max_row=5)
cats = Reference(ws, min_col=1, min_row=2, max_row=5)

chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)

ws.add_chart(chart, 'G2')                # 放置在 G2 锚点
```

## 格式化的"专业感" — 三件事

### 1. 数字格式

```python
# 货币（万元）
cell.number_format = '#,##0.00,"万"'

# 百分比
cell.number_format = '0.0%'

# 千分位
cell.number_format = '#,##0'

# 负数红色
cell.number_format = '#,##0;[Red]-#,##0'

# 日期
cell.number_format = 'yyyy-mm-dd'
```

### 2. 配色（避免"AI 蓝紫渐变"）

参考蓝绿专业配色（财务 / 数据分析风）：

| 用途 | HEX |
|---|---|
| 主色（标题底色） | `1F4E78` 深蓝 |
| 副色（section 底色） | `D9E1F2` 浅蓝 |
| 总计行底色 | `FCE4D6` 浅橙 |
| 输入区底色 | `FFF2CC` 浅黄 |
| 文本主色 | `1F4E78` 深蓝 |
| 边框灰 | `CCCCCC` |

```python
from openpyxl.styles import Font, PatternFill

HEADER = PatternFill('solid', fgColor='1F4E78')
HEADER_FONT = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
```

### 3. 字体规范

- 中文字体：`微软雅黑` / `思源黑体`（避免 `宋体`，老旧）
- 英文 / 数字：`Calibri` / `Arial`
- 大小：标题 11-12pt，正文 10pt，注释 9pt
- 加粗仅用于：表头、总计行、关键 KPI

## Conditional Formatting

```python
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule

# 三色阶（最低红 → 中间黄 → 最高绿）
ws.conditional_formatting.add(
    'B2:B100',
    ColorScaleRule(
        start_type='min', start_color='F8696B',
        mid_type='percentile', mid_value=50, mid_color='FFEB84',
        end_type='max', end_color='63BE7B',
    ),
)

# 数据条
from openpyxl.formatting.rule import DataBarRule
ws.conditional_formatting.add(
    'B2:B100',
    DataBarRule(start_type='min', end_type='max', color='5B9BD5'),
)
```

## 列宽 / 行高

```python
# 列宽（字符单位，1 ≈ 7px）
ws.column_dimensions['A'].width = 24

# 行高（pt）
ws.row_dimensions[1].height = 25

# 冻结窗格（第 1 行 + 第 1 列固定）
ws.freeze_panes = 'B2'
```

## 模板审计

每次写完模板后，自检：

- [ ] 数字列都加了 number_format 而不是 plain int / float？
- [ ] 表头加粗了？
- [ ] 输入区和公式区视觉上区分？
- [ ] 列宽自适应（不是默认 8.43）？
- [ ] 第一行 freeze 了？
- [ ] 图表的标题 / 轴标签 / 图例都填了？

跑 `scripts/analyze.py mytemplate.xlsx` 让工具检查 sheet 名、公式坑等问题。
