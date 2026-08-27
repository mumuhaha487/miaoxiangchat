# Project Plan

> Build sequence for excel-skill. Referenced by SKILL.md.

## Phase 1: Foundation (v0.1.0) -- Current

- [x] README, SKILL.md, WORKFLOW.md, INSTALL_CN.md
- [x] pyproject.toml with dependencies
- [x] .gitignore, LICENSE (MIT)
- [x] scripts/helpers/ (formulas.py, styling.py)
- [x] scripts/excel_lint.py (pitfall auditor)
- [x] tests/test_excel_lint.py
- [x] references/01-formulas-cheatsheet.md
- [x] references/02-pivot-tables.md
- [x] references/05-openpyxl-python.md
- [x] references/08-excel-pitfalls.md
- [x] scripts/generate_finance/three_statements.py
- [x] scripts/generate_fmcg/sales_vs_target.py
- [x] scripts/generate_ecommerce/gmv_dashboard.py
- [x] scripts/generate_internet/dau_mau_cohort.py

## Phase 2: Complete References

- [x] references/03-power-query.md -- Power Query / M language
- [x] references/04-vba-vs-python.md -- VBA vs Python decision matrix
- [x] references/06-cell-protection.md -- Cell protection + data validation
- [x] references/07-charts-and-formatting.md -- Charts + professional formatting

## Phase 3: Templates Directory

模板改为"按需生成"——`templates/<行业>/` 是生成器输出目录，不预存 .xlsx。

- [x] scripts/generate_finance/three_statements.py
- [x] scripts/generate_fmcg/sales_vs_target.py
- [x] scripts/generate_ecommerce/gmv_dashboard.py
- [x] scripts/generate_internet/dau_mau_cohort.py
- [x] scripts/generate_saas/unit_economics.py

## Phase 4: Utilities

- [x] scripts/recalc.py -- Formula recalculation via LibreOffice headless
- [x] scripts/analyze.py -- .xlsx health auditor (XA001…XA010)

## Phase 5: Tutorials and Examples

- [x] tutorials/01-beginner.md -- First Excel template with Python
- [x] examples/finance_dcf_walkthrough.md -- DCF end-to-end walkthrough
- [ ] tutorials/02-intermediate.md -- Formulas, charts, conditional formatting
- [ ] tutorials/03-advanced.md -- Multi-sheet dashboards, pivot tables
- [ ] more examples per industry
