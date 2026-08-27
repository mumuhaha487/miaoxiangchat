# 04 · VBA vs Python（openpyxl/pandas）

> 什么时候继续用 VBA，什么时候改用 Python？给 AI agent 写代码时怎么选？

## 决策矩阵

| 场景 | 推荐 | 理由 |
|---|---|---|
| 一次性数据清洗 / 计算 / 生成 | Python | 速度、版本控制、可测试 |
| 嵌入 Excel 工作流，用户日常点按钮触发 | VBA | Python 需要预装 / 跨电脑分发难 |
| 团队共享、要进 git review | Python | VBA 二进制 .xlsm 无法 diff |
| 调外部 API、HTTP、数据库 | Python | requests / sqlalchemy 一行解决 |
| 自定义函数（UDF） | VBA / Office Scripts | Python 装 xlwings 也可，但门槛高 |
| Pivot / Chart 交互式重整 | Excel 原生 | 不要把 UI 行为搬到代码 |
| 大数据（>50 万行） | Python (pandas / polars) | VBA 单线程慢 |
| Outlook / 邮件自动化 | VBA / Power Automate | win32com 直连 Outlook |

## 共存模式（最常见）

把"重计算 / 数据清洗"放 Python，把"用户交互"留给 VBA：

```text
data.csv  ──[Python: openpyxl]──>  intermediate.xlsx
                                        │
                              用户打开 Excel
                                        │
                              点击按钮（VBA）→ 调用刷新 / 出图
```

## openpyxl 取代 VBA 的常见任务

| VBA 写法 | Python (openpyxl) |
|---|---|
| `Range("A1").Value = 100` | `ws['A1'] = 100` |
| `Range("A1:A10").Formula = "=SUM(B1:B10)"` | 循环 / list comprehension |
| `Sheets.Add` | `wb.create_sheet("name")` |
| `Range("A1").Font.Bold = True` | `cell.font = Font(bold=True)` |
| `Application.Calculation = xlManual` | openpyxl 不能改这个 — 让 Excel 打开时重算 |
| `Range("A1").NumberFormat = "0.00%"` | `cell.number_format = '0.00%'` |

## openpyxl 取代不了 VBA 的事

- 触发 Excel 重算（openpyxl 写入的公式在文件层面是字符串，必须 Excel/LibreOffice 打开重算或用 `scripts/recalc.py`）
- 录制宏 / 调用 Excel 内置 dialog
- 实时响应单元格 change 事件（Worksheet_Change）
- 写自定义函数（UDF）— 需要 xlwings / pyxll

## Office Scripts（新选择）

如果在 Microsoft 365 网页版 Excel，**Office Scripts**（TypeScript）正在取代 VBA：
- 跨平台（Web / Win / Mac 都能跑）
- 现代语法 + npm 生态
- 可以被 Power Automate 调用

但桌面 / 企业本地版用户还得用 VBA 或 Python。

## 给 AI agent 的判断规则

```text
if 任务 == 一次性数据处理:                    用 Python
elif 用户每天点按钮：                          用 VBA（嵌入 .xlsm）
elif 团队协作 + 要进 git：                     用 Python
elif Excel 365 网页版 + 团队都用 365：         用 Office Scripts
else:                                          先问"会装 Python 吗" → 是 → Python；否 → VBA
```
