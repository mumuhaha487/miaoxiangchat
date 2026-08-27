---
name: excel-skill
description: Generate Excel workbooks (.xlsx) with industry-specific templates for finance, FMCG, e-commerce, and internet/SaaS scenarios. Use this skill when the user wants to (1) create an Excel template with formulas, formatting, and charts; (2) build a dashboard or report; (3) analyze data with pivot tables or Power Query; (4) automate Excel generation in Python via openpyxl; (5) audit AI-generated openpyxl code for common pitfalls (formula-as-string, data_only flag misuse). Provides progressive-disclosure access to formulas, pivot tables, Power Query, charts, openpyxl, pandas-excel, and 4 industry template libraries. Chinese-friendly. Designed to make AI-generated Excel code paste-and-run.
---

# Excel Skill

Generate Excel files / formulas / pivot tables / dashboards correctly the first time.

## When to Use This Skill

Yes:
- Creating .xlsx files with formulas, formatting, validation, charts
- Industry-specific templates: finance (DCF, three-statement), FMCG (sales vs target, RTM), e-commerce (GMV, RFM, funnel), internet (DAU/MAU, LTV/CAC, A/B test)
- Pivot tables and Power Query workflows
- Python automation of Excel generation (openpyxl, pandas-to-excel)
- Auditing AI-generated openpyxl code for common pitfalls
- Recalculating formulas in openpyxl-generated files (via LibreOffice macro)
- Style / formatting standards for professional reports

No:
- Real-time data integration (use Power BI / Tableau / Metabase instead)
- Complex statistical modeling (use Python pandas / R directly, then export)
- Pure data wrangling without Excel deliverable (use pandas)

## Detect User Intent First

| Pattern | Trigger keywords | Action |
|---|---|---|
| **Generate template** | "做一个/帮我生成 X 模板" / "create template for Y" | Pick from `templates/` matching industry, run `scripts/generate_*.py` |
| **Formula question** | "怎么写 X 公式" / "VLOOKUP / INDEX / SUMIFS" | Load `references/01-formulas-cheatsheet.md` |
| **Pivot table** | "透视表 / pivot table / 数据汇总" | Load `references/02-pivot-tables.md` |
| **Power Query** | "Power Query / 数据清洗 / M 语言" | Load `references/03-power-query.md` |
| **Dashboard / chart** | "Dashboard / 仪表盘 / 图表 / 配色" | Load `references/07-charts-and-formatting.md` |
| **Python openpyxl** | Mentions Python / openpyxl / 写脚本 | Load `references/05-openpyxl-python.md` |
| **pandas + Excel pitfalls** | "pandas read_excel / to_excel" / 公式变字符串 | Load `references/08-excel-pitfalls.md`（含 "formula gets stringified" 坑） |
| **VBA vs Python** | "VBA / 宏 / Macro / 该用 VBA 还是 Python" | Load `references/04-vba-vs-python.md` |
| **Cell protection / validation** | "锁单元格 / 数据校验 / 下拉" | Load `references/06-cell-protection.md` |
| **Code review** | User pastes openpyxl code | Run `scripts/excel_lint.py` |
| **Audit existing file** | "体检 / 诊断这份 Excel / 有没有坑" | Run `scripts/analyze.py file.xlsx` |

## Industry Routing (templates)

| User describes | Industry | Template directory |
|---|---|---|
| 财报 / 三表 / DCF / ROE / 投资组合 | Finance | `templates/finance/` |
| 销售达成 / RTM / 经销商 / SKU ABC / 促销 ROI | FMCG | `templates/fmcg/` |
| GMV / 转化率 / 客单价 / RFM / ROAS | E-commerce | `templates/ecommerce/` |
| DAU / MAU / 留存 / LTV / CAC / 漏斗 / A/B 测试 | Internet | `templates/internet/` |

If unsure, **ask which industry first**. Same metric (e.g., "转化率") means different things in e-commerce vs internet/SaaS.

## Things That Must Survive

When generating openpyxl code, NEVER lose:

1. **`data_only=True` misuse**: Loading with `data_only=True` then saving = formulas permanently lost. Only use for read-only analysis.
2. **Formulas in openpyxl are strings**: After `ws['A1'] = '=SUM(B1:B10)'`, the file has the formula but no calculated value. Tell the user to either:
   - Open in Excel (auto-recalc on open)
   - Run `python scripts/recalc.py file.xlsx` (uses LibreOffice macro)
3. **Number format must match value**: A 0.15 cell with format `'0.00%'` displays "15.00%". A "15.00%" string with default format displays as text.
4. **Locale in 公式**: Chinese Excel uses `；` not `,` as separator? **No.** Chinese Excel still uses `,` in formulas — `；` is the LIST_SEPARATOR for some MS Office locales but openpyxl always uses `,`.
5. **A1 vs RC**: Always use A1 notation in formulas. Do NOT generate `R1C1` style.
6. **Sheet name special characters**: Sheet names cannot contain `[ ] : * ? / \ '`, must be ≤31 chars.
7. **Chart references**: Charts need `Reference(ws, min_col, min_row, max_col, max_row)`. Forgetting `min_col` defaults to 1, often wrong.

## Default Workflow for "Make me an Excel"

```
1. Detect industry (finance / FMCG / e-commerce / internet)
2. Detect deliverable type (template / dashboard / analysis / report)
3. Find matching template script under templates/
4. Ask 2-3 clarifying questions:
   - 数据规模（行数、字段名）
   - 是否要图表 / Dashboard
   - 输出文件名 + 公式是否需要重算
5. Run / generate script, output .xlsx
6. If formulas were used: tell user about recalc.py or auto-recalc-on-open
7. If user gives code: run excel_lint.py mentally, point out pitfalls
```

## Files

| File | Role |
|---|---|
| `README.md` | Project intro |
| `WORKFLOW.md` | Cross-cutting workflow (clean → analyze → visualize → deliver) |
| `INSTALL_CN.md` | Chinese install + usage |
| `references/` | 8 progressive-disclosure knowledge files (formulas, pivot, Power Query, VBA-vs-Python, openpyxl, cell-protection, charts-and-formatting, pitfalls) |
| `templates/{finance,fmcg,ecommerce,internet,saas}/` | Output dirs for generated .xlsx (not pre-committed binaries) |
| `scripts/excel_lint.py` | Audit openpyxl **code** for common pitfalls (XL001…) |
| `scripts/analyze.py` | Audit an existing **.xlsx file** for health issues (XA001…XA010) |
| `scripts/recalc.py` | Recalculate formula caches via LibreOffice headless (requires LibreOffice installed) |
| `scripts/generate_*/` | 5 industry template generators (one script per vertical) |
| `scripts/helpers/` | Reusable styling / formula helpers |
| `tutorials/` | Beginner tutorial |
| `examples/` | End-to-end case study (finance DCF walkthrough) |

This skill is at version **0.1.0** as of 2026-04-19. Most templates and references are in active development. See PROJECT_PLAN.md for build sequence.
