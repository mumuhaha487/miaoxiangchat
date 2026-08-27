# 05 — openpyxl 全速参考

> Python 操作 .xlsx 的事实标准。这份文档**只列你 90% 时候需要的**，避免被官方文档的复杂结构淹没。

## 安装

```bash
pip install openpyxl
```

可选：`pip install pillow`（如果要插入图片）。

## 5 行 Hello World

```python
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws['A1'] = 'Hello'
ws.append(['Row', 'of', 'data'])
wb.save('hello.xlsx')
```

---

## 工作簿 / 工作表

```python
from openpyxl import Workbook, load_workbook

# 新建
wb = Workbook()

# 加载已有
wb = load_workbook('file.xlsx')

# 加载只取数值（忽略公式，用于纯读取）
wb = load_workbook('file.xlsx', data_only=True)
# ⚠️ 警告：用 data_only=True 加载后再 wb.save()，公式会永久丢失！

# 加载 .xlsm（保留宏）
wb = load_workbook('file.xlsm', keep_vba=True)

# 当前活动 sheet
ws = wb.active

# 创建新 sheet
ws2 = wb.create_sheet('Sales')                    # 末尾添加
ws3 = wb.create_sheet('Summary', 0)               # 第 0 个位置
ws4 = wb.create_sheet('Detail', -1)               # 倒数第 1 个

# 通过名字访问
ws = wb['Sales']

# 列出所有 sheet 名
print(wb.sheetnames)

# 重命名
ws.title = '销售明细'

# 删除
del wb['Summary']
# 或
wb.remove(wb['Summary'])

# 复制 sheet
wb.copy_worksheet(ws)

# 保存
wb.save('out.xlsx')
```

---

## 单元格读写

### 写入

```python
# 三种等价写法
ws['A1'] = 'Hello'
ws.cell(row=1, column=1, value='Hello')
ws.cell(row=1, column=1).value = 'Hello'

# 批量按行追加
ws.append(['Name', 'Age', 'Salary'])
ws.append(['Alice', 30, 50000])
ws.append(['Bob', 25, 45000])

# 批量按字典追加（key 是列号或字母）
ws.append({1: 'Charlie', 2: 28, 3: 48000})
ws.append({'A': 'Dave', 'B': 35, 'C': 60000})
```

### 读取

```python
# 单元格
val = ws['A1'].value
val = ws.cell(row=1, column=1).value

# 整行（值列表）
for row in ws.iter_rows(min_row=2, values_only=True):
    print(row)  # tuple

# 整列
for col in ws.iter_cols(values_only=True):
    print(col)

# 整个 sheet 转 list of list
all_rows = list(ws.values)

# 转 pandas DataFrame
import pandas as pd
df = pd.DataFrame(ws.values)
df.columns = df.iloc[0]   # 第一行作表头
df = df[1:]
```

### 公式

```python
# 写入公式（开头要加 =）
ws['D1'] = '=SUM(A1:C1)'
ws['D2'] = '=AVERAGE(A2:C2)'
ws['D3'] = '=IF(B3>10000,"高","低")'

# ⚠️ 公式只是字符串，openpyxl 不计算
# 要看到计算结果，要么用 Excel 打开（自动重算），要么用 LibreOffice macro
# 见 scripts/recalc.py
```

---

## 样式

```python
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, NamedStyle

# 字体
ws['A1'].font = Font(
    name='微软雅黑',
    size=12,
    bold=True,
    italic=False,
    color='FFFFFF',     # 6 位 hex，不要 #
    underline='single', # 'single' / 'double' / 'singleAccounting'
)

# 填充（背景色）
ws['A1'].fill = PatternFill(
    fill_type='solid',  # ⚠️ 必须传，否则颜色不显示！
    fgColor='2F5496',
)
# 简写：PatternFill('solid', fgColor='2F5496')   # 第 1 个位置参数就是 fill_type

# 对齐
ws['A1'].alignment = Alignment(
    horizontal='center',  # 'left' / 'center' / 'right'
    vertical='center',    # 'top' / 'center' / 'bottom'
    wrap_text=True,       # 自动换行
)

# 边框
thin = Side(style='thin', color='CCCCCC')
ws['A1'].border = Border(left=thin, right=thin, top=thin, bottom=thin)

# 数字格式
ws['B1'].number_format = '#,##0'              # 千分位
ws['B2'].number_format = '#,##0.00'           # 千分位 + 2 位小数
ws['C1'].number_format = '0.00%'              # 百分比
ws['D1'].number_format = '¥#,##0;[红色]-¥#,##0'  # 货币 + 负数红色
ws['E1'].number_format = 'yyyy-mm-dd'         # 日期
ws['E2'].number_format = 'yyyy"年"mm"月"dd"日"'  # 中文日期
```

### 命名样式（复用）

```python
# 创建命名样式
header = NamedStyle(name='header')
header.font = Font(bold=True, color='FFFFFF')
header.fill = PatternFill('solid', fgColor='2F5496')
header.alignment = Alignment(horizontal='center')

# 注册
wb.add_named_style(header)

# 应用
ws['A1'].style = 'header'
ws['B1'].style = 'header'
```

### 行高 / 列宽

```python
ws.row_dimensions[1].height = 30
ws.column_dimensions['A'].width = 20
ws.column_dimensions['B'].width = 15

# 自动列宽（手算）
from openpyxl.utils import get_column_letter
for col_idx, col in enumerate(ws.columns, 1):
    max_len = max((len(str(c.value)) if c.value else 0) for c in col)
    ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2
```

### 合并单元格

```python
ws.merge_cells('A1:D1')
ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=4)

# 取消合并
ws.unmerge_cells('A1:D1')
```

---

## 图表

```python
from openpyxl.chart import LineChart, BarChart, PieChart, Reference

# 1. 准备数据：A 列是 x（categories），B-D 列是 y（series）
# 2. 选数据范围

chart = BarChart()
chart.title = '月度销售'
chart.x_axis.title = '月份'
chart.y_axis.title = '销售额（万元）'
chart.style = 10  # 1-48，预设样式

# 数据：B-D 列，从第 1 行（含 series 名）到第 13 行
data = Reference(ws, min_col=2, min_row=1, max_col=4, max_row=13)
chart.add_data(data, titles_from_data=True)

# 类目：A 列，第 2-13 行
cats = Reference(ws, min_col=1, min_row=2, max_row=13)
chart.set_categories(cats)

# 大小（cm）
chart.height = 8
chart.width = 16

# 插入到表中
ws.add_chart(chart, 'F2')
```

### 折线图

```python
chart = LineChart()
chart.title = 'DAU 趋势'
data = Reference(ws, min_col=2, min_row=1, max_col=2, max_row=31)
chart.add_data(data, titles_from_data=True)
ws.add_chart(chart, 'D1')
```

### 饼图

```python
chart = PieChart()
chart.title = '渠道占比'
data = Reference(ws, min_col=2, min_row=2, max_row=6)
labels = Reference(ws, min_col=1, min_row=2, max_row=6)
chart.add_data(data)
chart.set_categories(labels)
ws.add_chart(chart, 'D1')
```

---

## 条件格式

```python
from openpyxl.formatting.rule import (
    CellIsRule, ColorScaleRule, DataBarRule, IconSetRule,
)
from openpyxl.styles import PatternFill, Font

# 红绿配色：< 0.8 红，>= 1.0 绿
ws.conditional_formatting.add(
    'D2:D100',
    CellIsRule(operator='lessThan', formula=['0.8'],
               fill=PatternFill('solid', fgColor='FFC7CE'),
               font=Font(color='9C0006'))
)
ws.conditional_formatting.add(
    'D2:D100',
    CellIsRule(operator='greaterThanOrEqual', formula=['1.0'],
               fill=PatternFill('solid', fgColor='C6EFCE'),
               font=Font(color='006100'))
)

# 颜色刻度（热力图）
ws.conditional_formatting.add(
    'B2:M13',
    ColorScaleRule(
        start_type='min', start_color='FFC7CE',
        mid_type='percentile', mid_value=50, mid_color='FFEB9C',
        end_type='max', end_color='C6EFCE',
    )
)

# 数据条
ws.conditional_formatting.add(
    'C2:C100',
    DataBarRule(start_type='min', end_type='max', color='638EC6')
)
```

---

## 数据验证（下拉列表）

```python
from openpyxl.worksheet.datavalidation import DataValidation

# 下拉列表
dv = DataValidation(type='list', formula1='"北京,上海,广州,深圳"')
dv.error = '必须从列表中选择'
dv.errorTitle = '输入错误'
ws.add_data_validation(dv)
dv.add('B2:B100')

# 数值范围
dv2 = DataValidation(type='whole', operator='between', formula1=0, formula2=100)
ws.add_data_validation(dv2)
dv2.add('C2:C100')
```

---

## 冻结窗格

```python
ws.freeze_panes = 'B2'   # 冻结 A 列 + 第 1 行
ws.freeze_panes = 'A2'   # 只冻结第 1 行（最常见）
```

---

## 隐藏行/列 / 分组

```python
ws.row_dimensions[5].hidden = True
ws.column_dimensions['B'].hidden = True

# 分组（折叠）
ws.row_dimensions.group(2, 5, hidden=False)
ws.column_dimensions.group('B', 'D', hidden=False)
```

---

## 保护

```python
# 保护工作表
ws.protection.password = 'mypassword'
ws.protection.sheet = True

# 单独允许某些单元格编辑
from openpyxl.styles import Protection
ws['B2'].protection = Protection(locked=False)
```

---

## 8 大常见坑

### 坑 1：data_only=True 后 save() = 公式永久丢失

```python
wb = load_workbook('file.xlsx', data_only=True)
ws['A1'] = 'changed'
wb.save('file.xlsx')   # ❌ 所有公式没了！
```

**修复**：只读时才用 `data_only=True`，要保存就别用。

### 坑 2：公式在 openpyxl 里只是字符串

```python
ws['A1'] = '=SUM(B1:B10)'
wb.save('out.xlsx')
# 现在 .xlsx 里 A1 公式是 SUM(B1:B10)，但 cached value 是 None
# 需要 Excel 打开自动重算，或用 recalc.py
```

### 坑 3：PatternFill 没传 fill_type 颜色不显示

```python
# ❌ 不显示颜色
ws['A1'].fill = PatternFill(fgColor='2F5496')

# ✅ 必须有 fill_type
ws['A1'].fill = PatternFill('solid', fgColor='2F5496')
ws['A1'].fill = PatternFill(fill_type='solid', fgColor='2F5496')
```

### 坑 4：行列号是 1-based

`row=1, column=1` 是 A1，不是 A0。`ws.cell(0, 0)` 会报错。

### 坑 5：sheet 名禁止字符

```python
# ❌
wb.create_sheet('Sales[2024]')  # [ ] 禁止
wb.create_sheet('销售/明细')     # / 禁止
wb.create_sheet('a' * 32)        # > 31 字符

# ✅
wb.create_sheet('Sales 2024')
wb.create_sheet('销售明细')
```

### 坑 6：跨 sheet 引用 sheet 名带空格要单引号

```python
# ❌ 公式无效
ws['A1'] = '=SalesData!B2'      # 当 sheet 名是 'Sales Data' 时

# ✅
ws['A1'] = "='Sales Data'!B2"   # 单引号包住 sheet 名
```

### 坑 7：中文字符在英文环境字体回退

如果你写中文但 `Font(name='Calibri')`，在装了字体的机器上没事，没装的会乱码。**保险做法**：

```python
ws['A1'].font = Font(name='微软雅黑', size=11)   # Windows
# Mac: 'PingFang SC'
# Linux: 'Noto Sans CJK SC'
```

### 坑 8：图表 Reference 忘了 min_col

```python
# ❌ 默认 min_col=1，常常不是你想要的
chart.add_data(Reference(ws, min_row=1, max_row=10))

# ✅ 显式指定
chart.add_data(Reference(ws, min_col=2, min_row=1, max_col=2, max_row=10))
```

---

## pandas + openpyxl 组合使用

```python
import pandas as pd
from openpyxl import load_workbook

# pandas 处理数据，openpyxl 加格式
with pd.ExcelWriter('out.xlsx', engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='Data', index=False)

# 然后再用 openpyxl 打开加样式
wb = load_workbook('out.xlsx')
ws = wb['Data']
for cell in ws[1]:  # 第 1 行（表头）
    cell.font = Font(bold=True)
wb.save('out.xlsx')
```

详见 `references/06-pandas-excel.md`。
