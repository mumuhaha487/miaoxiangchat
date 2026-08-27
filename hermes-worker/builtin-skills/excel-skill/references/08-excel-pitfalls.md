# 08 — Excel 与 openpyxl 最容易踩的坑

> 这一份是整个项目最重要的 reference。AI 生成的 Excel 代码 80% 的问题都在这里列出。

## ★ 顶级坑 #1：openpyxl 里的公式只是字符串

```python
from openpyxl import Workbook
wb = Workbook()
ws = wb.active
ws['A1'] = 100
ws['A2'] = 200
ws['A3'] = '=SUM(A1:A2)'
wb.save('test.xlsx')
```

**问题**：用 `load_workbook('test.xlsx')` 读 A3，会得到字符串 `'=SUM(A1:A2)'`，**不是 300**。

**为什么**：openpyxl 不带计算引擎。它只是写入公式字符串到 .xlsx 的 XML 里。Excel 打开时**会自动重算**，所以人用 Excel 打开看到 300，但程序读 .xlsx 文件还是字符串。

**3 种解决方案**：

1. **让 Excel 自己重算**（最简单）
   - 用户用 Excel 打开 → 自动重算 → 保存 → 之后 `data_only=True` 才能读到值
   - 缺点：要人工干预一次

2. **用 `recalc.py`（脚本自动）**
   ```bash
   python scripts/recalc.py output.xlsx
   ```
   需要本地装 LibreOffice。脚本会调用 LibreOffice 的命令行模式打开 → 重算 → 保存。

3. **直接写计算后的值**（最稳妥但失去 Excel 公式的灵活性）
   ```python
   ws['A3'] = sum([100, 200])  # 直接写 300，不写公式
   ```

## ★ 顶级坑 #2：`data_only=True` 会让公式永久丢失

```python
from openpyxl import load_workbook

# 你想读公式计算后的值
wb = load_workbook('file.xlsx', data_only=True)
ws = wb.active
print(ws['A3'].value)  # 这能读到值

# 你不小心改了点什么并保存
ws['B1'] = 'new'
wb.save('file.xlsx')

# 灾难：file.xlsx 里所有公式被替换成它们的当前值
# 公式永久丢失，没有 undo
```

**正确做法**：

```python
# 如果只是读：load → 读 → 不要 save
wb = load_workbook('file.xlsx', data_only=True)
value = wb['Sheet1']['A3'].value

# 如果要修改并保留公式：load 时不带 data_only
wb = load_workbook('file.xlsx')  # 默认 data_only=False，保留公式
ws = wb.active
ws['B1'] = 'new'
wb.save('file.xlsx')  # 公式保留

# 如果既要读值又要修改：用两个 wb 对象
wb_read = load_workbook('file.xlsx', data_only=True)
value = wb_read['Sheet1']['A3'].value

wb_write = load_workbook('file.xlsx')  # 不要 data_only
wb_write['Sheet1']['B1'] = 'new'
wb_write.save('file.xlsx')
```

## ★ 顶级坑 #3：pandas `to_excel` 没法写公式

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': ['=A1*2', '=A2*2', '=A3*2']})
df.to_excel('test.xlsx', index=False)

# 打开 test.xlsx，B 列显示的是字符串 "=A1*2"，不是公式
# 因为 pandas 把它们当字符串处理
```

**正确做法**（用 openpyxl 引擎）：

```python
import pandas as pd
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl import Workbook

df = pd.DataFrame({'A': [1, 2, 3]})
wb = Workbook()
ws = wb.active

# 先写数据
for r in dataframe_to_rows(df, index=False, header=True):
    ws.append(r)

# 单独写公式列
for row in range(2, 5):  # 假设 header 在第 1 行
    ws[f'B{row}'] = f'=A{row}*2'

wb.save('test.xlsx')
```

## 坑 #4：Sheet 名特殊字符

```python
ws = wb.create_sheet(title='2024/Q1')  # ❌ 包含 /
ws = wb.create_sheet(title='Sales [PRC]')  # ❌ 包含 [
ws = wb.create_sheet(title='X' * 35)  # ❌ 超过 31 字符
```

**禁止字符**：`/ \ ? * [ ] '`  
**长度限制**：≤ 31 字符  
**正确**：`'2024Q1'`、`'Sales-PRC'`、`'X' * 31`

## 坑 #5：日期写入显示成数字

```python
import datetime
ws['A1'] = datetime.date(2024, 1, 15)
# Excel 打开看到的是 45306（Excel 的内部日期序列号）
```

**正确**：设置 number_format

```python
from openpyxl.styles import NamedStyle

ws['A1'] = datetime.date(2024, 1, 15)
ws['A1'].number_format = 'yyyy-mm-dd'
# 现在显示 2024-01-15
```

## 坑 #6：百分比显示错误

```python
ws['A1'] = 0.15
ws['A1'].number_format = '0.00%'
# 显示 15.00%（正确）

ws['B1'] = '15%'  # 字符串
ws['B1'].number_format = '0.00%'  # 没用，因为是字符串
# 显示 "15%"（文本，不能参与计算）
```

**规则**：百分比的值是 **小数（0.15 = 15%）**，不是字符串。

## 坑 #7：列宽不会自动适配内容

```python
ws['A1'] = 'This is a very long header that should be visible'
# Excel 打开后，列 A 默认宽度，header 被截断
```

**解决**：手动设置列宽

```python
ws.column_dimensions['A'].width = 50

# 或者用循环根据内容长度自动设置
from openpyxl.utils import get_column_letter
for col_idx in range(1, ws.max_column + 1):
    col_letter = get_column_letter(col_idx)
    max_len = max(len(str(cell.value or '')) for cell in ws[col_letter])
    ws.column_dimensions[col_letter].width = max_len + 2
```

## 坑 #8：公式跨 sheet 引用要带单引号

```python
# 引用 Sheet1!A1 - sheet 名没空格
ws['A1'] = '=Sheet1!A1'  # OK

# 引用 'Sales 2024'!A1 - sheet 名有空格，必须加单引号
ws['A1'] = "='Sales 2024'!A1"  # OK
ws['A1'] = '=Sales 2024!A1'  # ❌ Excel 打开报错
```

## 坑 #9：`number_format` vs `style.number_format`

```python
# 方式 1：直接设
ws['A1'].number_format = '0.00%'  # OK

# 方式 2：用 NamedStyle（复用样式时推荐）
from openpyxl.styles import NamedStyle
percent_style = NamedStyle(name='percent', number_format='0.00%')
wb.add_named_style(percent_style)
ws['A1'].style = 'percent'

# 错误用法
ws['A1'].style.number_format = '0.00%'  # ❌ AttributeError
```

## 坑 #10：合并单元格只在左上写值

```python
ws.merge_cells('A1:C1')
ws['A1'] = '标题'  # OK
ws['B1'] = '不会显示'  # 实际上 .value 还在但 Excel 不显示
ws['C1'] = '不会显示'  # 同上

# 拆分时，只有 A1 保留值，B1 C1 是 None
ws.unmerge_cells('A1:C1')
print(ws['A1'].value)  # '标题'
print(ws['B1'].value)  # None
```

## 坑 #11：图表必须用 `Reference` 而不是 list

```python
from openpyxl.chart import BarChart, Reference

chart = BarChart()
data = Reference(ws, min_col=2, min_row=1, max_col=2, max_row=10)
cats = Reference(ws, min_col=1, min_row=2, max_col=1, max_row=10)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
ws.add_chart(chart, 'D1')
```

**常见错误**：
- 忘记 `min_col`（默认 1，不是你想的）
- `titles_from_data=True` 时 data 必须包含 header 行
- 类别和数据的 row 范围必须**一致**（除了 header）

## 坑 #12：xlsm 不能直接读 VBA 代码

openpyxl **不支持读写 VBA 代码**。如果你想保留 .xlsm 的宏：

```python
wb = load_workbook('with_macro.xlsm', keep_vba=True)
# 修改其他 cell
wb.save('with_macro.xlsm')
# 宏被保留
```

**`keep_vba=True` 必须传**——否则保存后宏消失。

## 坑 #13：`PatternFill` 必须设 `fill_type`

```python
from openpyxl.styles import PatternFill

ws['A1'].fill = PatternFill('solid', fgColor='FFFF00')  # ✅ 黄色填充
ws['A1'].fill = PatternFill(fgColor='FFFF00')  # ❌ 看不到颜色
```

`fill_type` 默认是 None，必须显式设置（通常是 `'solid'`）。

## 坑 #14：openpyxl 打开 .xlsx 慢

大文件（10 万行+）用 `read_only=True` 模式：

```python
wb = load_workbook('large.xlsx', read_only=True)
for row in ws.iter_rows(values_only=True):
    process(row)
# 注意：read_only 模式不能修改 / 保存
```

写大文件用 `write_only=True`：

```python
wb = Workbook(write_only=True)
ws = wb.create_sheet()
for row_data in data_generator():
    ws.append(row_data)
wb.save('large.xlsx')
```

## 坑 #15：Chinese 字符在某些字体下显示为方块

Excel 默认字体（Calibri）**不支持中文字符**。如果 Excel 装在英文环境，中文可能显示为方块。

**解决**：显式设置中文字体

```python
from openpyxl.styles import Font

cn_font = Font(name='微软雅黑', size=11)  # 或 'Microsoft YaHei' / '宋体' / 'SimHei'
ws['A1'].font = cn_font
ws['A1'] = '销售额'
```

---

## AI 生成代码的 lint 清单

`scripts/excel_lint.py` 自动检查这些（部分实现）：

- [ ] 用了 `data_only=True` 又调用了 `wb.save()` → 会丢公式
- [ ] 公式包含中文逗号 `，` → 改成英文 `,`
- [ ] sheet 名含禁止字符 → 改名
- [ ] sheet 名超 31 字符 → 截断
- [ ] 用了 PatternFill 但没设 fill_type → 加 `'solid'`
- [ ] 含中文但没设字体 → 提示改成微软雅黑
- [ ] 用了 pandas `to_excel` 写带公式的 DataFrame → 警告用 openpyxl
- [ ] 操作 .xlsm 但没 `keep_vba=True` → 警告

---

## 必读文档

- [openpyxl 官方文档](https://openpyxl.readthedocs.io)
- [Anthropic 官方 xlsx skill](https://github.com/anthropics/skills/tree/main/skills/xlsx)
- [Microsoft Excel 公式与函数参考](https://support.microsoft.com/zh-cn/office/excel-%E5%87%BD%E6%95%B0-%E5%88%86%E7%B1%BB%E5%88%97%E8%A1%A8-5f91f4e9-7b42-46d2-9bd1-63f26a86c0eb)
