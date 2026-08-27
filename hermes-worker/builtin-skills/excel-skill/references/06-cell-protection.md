# 06 · 单元格保护与数据校验

> 模板发出去之前，要不要让用户改某些公式？怎么校验输入？

## 保护层级

Excel 的保护分三层：

1. **Workbook protection** — 防止用户增删 sheet
2. **Sheet protection** — 防止 / 允许编辑特定单元格
3. **Cell locked / unlocked** — 单元格级别的"是否允许改"

**关键陷阱**：Sheet protection 默认状态下，所有单元格的 `locked=True`。
即使你只想锁定某几个公式单元格，**也必须先把所有可输入单元格设为
`locked=False`**，再打开 Sheet protection。

## openpyxl 实现

```python
from openpyxl import Workbook
from openpyxl.styles import Protection

wb = Workbook()
ws = wb.active

# Step 1: 标记可输入区为 unlocked
for cell in ws['B2:B10']:
    for c in cell:
        c.protection = Protection(locked=False)

# Step 2: 启用 sheet protection（默认锁定所有 locked=True 的单元格）
ws.protection.sheet = True
ws.protection.password = 'optional-pw'   # 可选，明文 / 弱哈希
ws.protection.enable()

wb.save('protected.xlsx')
```

## 数据校验（Data Validation）

让用户只能输入特定范围 / 类型的值：

```python
from openpyxl.worksheet.datavalidation import DataValidation

# 整数 1~100
dv_int = DataValidation(type='whole', operator='between',
                        formula1=1, formula2=100,
                        showErrorMessage=True,
                        errorTitle='输入错误',
                        error='请输入 1 到 100 之间的整数')
ws.add_data_validation(dv_int)
dv_int.add('B2:B10')

# 下拉列表
dv_list = DataValidation(type='list',
                         formula1='"低,中,高"',   # 注意：list 在公式里要双引号包裹
                         allow_blank=True)
ws.add_data_validation(dv_list)
dv_list.add('C2:C10')

# 日期
dv_date = DataValidation(type='date',
                         operator='greaterThan',
                         formula1='2024-01-01')
ws.add_data_validation(dv_date)
dv_date.add('D2:D10')
```

## 常见做法

| 场景 | 推荐做法 |
|---|---|
| 财务模板的"假设输入" | unlocked + 黄色填充 + 数据校验范围 |
| 财务模板的"计算公式" | locked + sheet protection（明文密码或不设） |
| 模板的 KPI / Dashboard | locked，禁用编辑 |
| 多人协作的工作表 | 加 audit log（VBA）或用 Excel Online 的 comment / 历史 |

## 安全性说明

Excel 的 sheet protection **不是安全机制**：
- 密码用弱哈希，几秒可破
- 谁拿到 .xlsx 都能 unzip + 改 XML 解开

如果数据真敏感，应当：
- 把敏感数据 / 公式放服务端
- 客户端只看不可写的 PDF / 图表

把 protection 当成"防误操作"而不是"防恶意"。

## 调试

如果你保护后用户反馈"我连输入区都改不了"，先检查：

1. 输入区是不是真的设了 `locked=False`？
2. 设的时候 ws.protection 是不是 *先* 全部应用完 unlocked *再* 开 protection？
3. 用户是不是用了 Excel 但 sheet protection 弹窗输入了错误密码？
