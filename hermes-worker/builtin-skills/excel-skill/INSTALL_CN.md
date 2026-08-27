# 中文安装与使用指南

## 1. 项目位置

| 路径 | 说明 |
|---|---|
| `G:\excel skill\` | 主项目目录 |

## 2. 环境准备

需要 Python 3.10+。

```powershell
cd "G:\excel skill"
pip install -r requirements.txt
```

核心依赖：
- `openpyxl` — 必装，所有模板生成都用它
- `pandas` — 数据预处理可选
- `xlsxwriter` — 复杂图表可选

## 3. 注册到 Cursor / Claude Code

```powershell
cmd /c mklink /J "C:\Users\$env:USERNAME\.cursor\skills\excel-skill" "G:\excel skill"
cmd /c mklink /J "C:\Users\$env:USERNAME\.claude\skills\excel-skill" "G:\excel skill"
```

## 4. 在 Cursor / Claude Code 里使用

```
用 excel-skill 帮我生成一个电商 GMV 仪表盘模板，30 天数据
```

```
帮我审一下这段 openpyxl 代码（贴代码）
```

```
Excel 里 SUMIFS 怎么用？
```

## 5. 命令行直接用

### 生成电商 GMV 仪表盘

```powershell
python scripts\generate_ecommerce\gmv_dashboard.py --output gmv.xlsx --days 30
```

会生成 4 个 sheet 的 .xlsx：
1. 数据录入：30 天约 3000 条 sample 订单
2. KPI 看板：今日/本周/本月 GMV / 订单数 / 客单价 + 30 天趋势图
3. 类目分析：销售排名 + ABC 分类 + 柱状图
4. 销售漏斗：曝光→点击→加购→下单→支付 + 漏斗图

### 检查 openpyxl 代码

```powershell
python scripts\excel_lint.py my_excel_code.py
```

会抓：
- ❌ XL001：`load_workbook(data_only=True)` 后 save → 公式永久丢失
- ❌ XL003：公式含中文逗号
- ❌ XL004：sheet 名含禁止字符
- ⚠️ XL002：`pandas.to_excel` 写公式
- ⚠️ XL006：`PatternFill` 没设 `fill_type`
- ⚠️ XL007：`.xlsm` 没 `keep_vba=True`
- ...

### 重算公式（让 openpyxl 写的公式真正生效）

```powershell
# 需要本地装 LibreOffice
python scripts\recalc.py gmv.xlsx
```

或者用户用 Excel 打开 gmv.xlsx，所有公式自动重算。

## 6. 4 大行业模板速览

```powershell
python scripts\generate_finance\three_statements.py --output 三表.xlsx       # 金融三表
python scripts\generate_fmcg\sales_vs_target.py --output 销售达成.xlsx       # 快消
python scripts\generate_ecommerce\gmv_dashboard.py --output gmv.xlsx        # 电商 (已实现)
python scripts\generate_internet\dau_cohort.py --output dau.xlsx            # 互联网
```

> 当前 v0.1.0：电商 GMV dashboard 已实现，其他 3 个行业模板在 v0.2.0 路线图里。

## 7. 故障排查

### 生成的 .xlsx 公式不显示结果

Excel / WPS 打开会自动重算，看到的就是结果。
程序读 .xlsx 文件想读到结果：先用 Excel 打开保存，然后 `load_workbook(file, data_only=True)`。
完全自动化：装 LibreOffice，跑 `python scripts\recalc.py file.xlsx`。

### 中文显示乱码

Excel 在英文环境的默认字体不支持中文。模板已经设了「微软雅黑」，但你环境没装这个字体的话还是会乱。换个中文系统字体：

```python
from openpyxl.styles import Font
cell.font = Font(name='SimHei', size=11)  # 或 'Microsoft YaHei' / '宋体'
```

### Cursor 看不到 skill

```powershell
ls "C:\Users\$env:USERNAME\.cursor\skills\excel-skill"
```

应该列出主项目内容。如果是空，重做 junction 步骤（参考第 3 节）。

## 8. 进一步学习

- [`SKILL.md`](./SKILL.md) — Cursor / Claude Code skill 完整说明
- [`README.md`](./README.md) — 项目介绍 + 与同类项目对比
- [`references/01-formulas-cheatsheet.md`](./references/01-formulas-cheatsheet.md) — 公式速查
- [`references/08-excel-pitfalls.md`](./references/08-excel-pitfalls.md) — ★ 必读，AI/人最容易踩的坑
