# Excel Skill

> 给 AI agent 用的 Excel 工具包：结构化知识库 + 5 大行业模板生成器 + 健康度诊断 + Python 自动化脚本。

让 Cursor / Claude Code / Codex 等 AI agent 直接按需求生成能用的 Excel 文件、公式、透视表、Dashboard，省去反复查文档。

## v2 新增

| 模块 | 作用 |
|---|---|
| `scripts/analyze.py` | 给现有 .xlsx 做"健康度诊断"（10 类常见坑：中文逗号、`#REF!`、整列引用、sheet 名违规等）|
| `scripts/generate_saas/unit_economics.py` | 第 5 个行业 vertical：SaaS LTV / CAC / Cohort / P&L Projection |
| `tests/test_generators.py` | 5 个 generator 的集成测试（跑完产出能被 openpyxl 读回 + 无 #REF!） |
| `tests/test_analyze.py` | `analyze.py` 检查项测试（含合并单元格回归） |
| `references/04-vba-vs-python.md` | VBA vs Python 决策矩阵 |
| `references/06-cell-protection.md` | 单元格保护 + 数据校验 |
| `references/07-charts-and-formatting.md` | 图表类型 + 专业配色（避免 "AI 蓝紫" 既视感） |
| `.github/workflows/test.yml` | CI 跑全套 pytest |

---

## 这个项目要解决什么

我（项目作者）和很多金融/快消/电商/互联网从业者一样，工作离不开 Excel，但又不是 Excel 重度老用户：

- 知道有公式但记不住具体语法（`SUMIFS` 还是 `SUMIF`？`INDEX/MATCH` 还是 `XLOOKUP`？）
- 知道 Power Query 牛但学习曲线陡（界面操作 + M 语言）
- 想用 Python `openpyxl` 自动化但每次都查文档
- 行业里大家都在用某些"约定俗成"的报表样式（金融三表、电商漏斗、快消 RTM 等），但 Google 出来的模板大多不对版

让 AI 帮我处理这些是最快的——但 AI 经常：
- 编错的公式参数（"`VLOOKUP` 第三个参数应该是 0 还是 FALSE"）
- 用 `pandas.read_excel + to_excel` 时**把所有公式变成字符串**（最经典的坑）
- 生成的 openpyxl 代码在「formula 在 openpyxl 里只是字符串，需要重算」这点上踩坑

这个项目就是给 AI 一份**结构化知识库 + 行业模板库 + 一组可复用脚本**，让它一次给对。

---

## 它和已有项目的关系

| 项目 | Stars | 我们怎么用 |
|---|---|---|
| [Anthropic 官方 xlsx skill](https://github.com/anthropics/skills) | - | 通用 xlsx 操作的标准实现，我们 reference 它 |
| [`claude-office-skills/skills`](https://github.com/claude-office-skills/skills) | - | 通用 openpyxl 知识，我们做行业化补充 |
| [`davepoon/buildwithclaude`](https://github.com/davepoon/buildwithclaude) | 3K | 含 `recalc.py` 公式重算，我们 fork 思路 |
| [`sartrus/modelling-team-skill`](https://github.com/sartrus/modelling-team-skill) | - | 三 agent 团队建财务模型，我们参考它的 Architect/Coder/Challenger 分层 |
| [`LondonMarket/Financial-Model-Excel-Template`](https://github.com/LondonMarket/Financial-Model-Excel-Template) | 9 | DCF 模板，我们的金融行业部分 reference |
| 熊猫办公 / 各模板站 | - | 中文社区主流模板，我们梳理"哪些场景用哪些模板" |

**核心差异**：
- **行业垂直**——金融、快消、电商、互联网 4 大领域，每个都有专属模板和 KPI 字典
- **中文优先**——文档、注释、模板字段都是中文
- **AI 友好**——面向 LLM 的 progressive disclosure 知识库，按问题路由到对应 reference，而非线性长教程

---

## 5 大行业覆盖

### 金融（Finance）
- 三表（资产负债 / 利润 / 现金流）联动模板
- DCF 估值模型
- 股票投资组合跟踪
- 财务比率分析（ROE 杜邦分解）

### 快消（FMCG / CPG）
- 销售达成 vs 目标分析
- RTM (Route-to-Market) 渠道分析
- 经销商进销存表
- SKU ABC 分类 + Pareto 分析
- 促销活动 ROI 计算

### 电商（E-commerce）
- GMV / DAU / 转化率 / 客单价 仪表盘
- 销售漏斗（曝光 → 点击 → 加购 → 下单 → 支付）
- ROI / ROAS 投放分析
- 用户分层（RFM 模型）
- SKU 销售排名 + 长尾分析

### 互联网（Internet）
- DAU / MAU / 留存曲线
- 漏斗分析（注册 → 激活 → 留存 → 付费）
- LTV / CAC / 回本周期
- A/B 测试结果分析
- 增长指标 dashboard

### SaaS（Unit Economics）
- Assumptions → Cohort 留存 → Unit Economics → P&L Projection → KPI Dashboard 五表联动
- LTV / CAC payback / LTV-CAC ratio / Magic Number
- 生成器：`scripts/generate_saas/unit_economics.py`

---

## 三大能力

### 1. AI 友好的 Excel 知识库（progressive disclosure）

```
references/
├── 01-formulas-cheatsheet.md       # 公式速查（按场景而非字母顺序）
├── 02-pivot-tables.md              # 透视表常用模式
├── 03-power-query.md               # Power Query 入门 + M 语言常用片段
├── 04-vba-vs-python.md             # VBA vs Python 决策矩阵
├── 05-openpyxl-python.md           # Python 自动化生成 Excel
├── 06-cell-protection.md           # 单元格保护 + 数据校验
├── 07-charts-and-formatting.md     # 图表类型 + 专业配色
└── 08-excel-pitfalls.md            # 最重要：AI / 人最容易踩的坑
```

`SKILL.md` 是入口，按用户问题路由到对应的 reference。

### 2. 行业模板生成器（按需生成，不存二进制）

每个行业一个 Python 生成器脚本，跑脚本即产出带公式 / 样式 / 图表的 .xlsx
（`templates/<行业>/` 目录是输出位置，仓库里不预存 .xlsx 二进制）：

```
scripts/
├── generate_finance/three_statements.py   # 三表联动（5 sheet）
├── generate_fmcg/sales_vs_target.py        # 销售达成 vs 目标 + SKU ABC（4 sheet）
├── generate_ecommerce/gmv_dashboard.py     # GMV / 留存 / LTV-CAC（4 sheet）
├── generate_internet/dau_mau_cohort.py     # DAU/MAU / 留存矩阵 / 粘性（4 sheet）
└── generate_saas/unit_economics.py         # SaaS 单位经济五表联动（5 sheet）
```

脚本生成而非手工二进制意味着你可以：
- 看 .py 脚本知道结构怎么搭
- 用 `--output` / 各行业参数（行数、ARPU、churn 等）重新生成
- 复用 `helpers/` 里的样式与公式逻辑做新模板

### 3. Python 工具脚本

```
scripts/
├── excel_lint.py              # 检查 AI 生成的 openpyxl 代码常见坑（XL001…）
├── analyze.py                 # 给现有 .xlsx 做健康度诊断（XA001…XA010）
├── recalc.py                  # 用 LibreOffice headless 重算公式缓存值（需装 LibreOffice）
├── generate_*/                # 5 个行业的模板生成脚本
└── helpers/
    ├── styling.py             # 通用样式（标题色 / 千分位 / 百分比 / 边框）
    └── formulas.py            # 常用公式生成器
```

> `recalc.py` 需要本机安装 LibreOffice（提供 `soffice` 命令）；没装时会明确报错，
> 此时直接用 Excel 打开生成的文件也会自动重算。

---

## 30 秒上手

### 不用 IDE，命令行直接生成

```bash
git clone https://github.com/gaaiyun/excel-skill.git
cd excel-skill
pip install -r requirements.txt

# 生成一个金融三表模板
python scripts/generate_finance/three_statements.py --output my_company.xlsx

# 生成一个电商 GMV dashboard 模板
python scripts/generate_ecommerce/gmv_dashboard.py --output gmv.xlsx
```

### 在 Cursor / Claude Code 里直接说

```
用 excel-skill 帮我做一个电商 SKU 销售排名 + ABC 分析的模板，输入 100 个 SKU
```

```
我有一份销售数据，按月份和产品类目，帮我用 Power Query 做一个透视分析
```

```
帮我审一下这段 openpyxl 代码（贴代码），看有没有公式被字符串化的问题
```

---

## 学习路径建议

### 入门
1. 读 [`tutorials/01-beginner.md`](./tutorials/01-beginner.md) — 上手必备
2. 读 [`references/01-formulas-cheatsheet.md`](./references/01-formulas-cheatsheet.md) — 公式速查
3. 跑一个行业生成器（如 `python scripts/generate_finance/three_statements.py -o demo.xlsx`），看 .py 脚本理解 openpyxl 怎么搭

### 进阶
4. 读 [`references/03-power-query.md`](./references/03-power-query.md) — Power Query 替代复杂公式
5. 读 [`references/07-charts-and-formatting.md`](./references/07-charts-and-formatting.md) — 图表与专业配色

### 自动化
6. 读 [`references/05-openpyxl-python.md`](./references/05-openpyxl-python.md) — Python 批量生成 Excel
7. 读 [`references/08-excel-pitfalls.md`](./references/08-excel-pitfalls.md)，并用 `scripts/analyze.py` 体检你的文件
8. 完整案例见 [`examples/finance_dcf_walkthrough.md`](./examples/finance_dcf_walkthrough.md)

---

## 项目结构

```
excel-skill/
├── README.md                  你正在看
├── SKILL.md                   Cursor / Claude Code skill 入口
├── WORKFLOW.md                跨场景工作流（清洗→分析→可视化→交付）
├── INSTALL_CN.md              Windows / 中文用户指南
├── LICENSE / .gitignore / requirements.txt / pyproject.toml
├── references/                8 个核心知识文档
├── templates/                 5 个行业的生成输出目录（由脚本按需生成 .xlsx）
├── scripts/                   生成器 + analyze / excel_lint / recalc + helpers
├── tutorials/                 入门教程
└── examples/                  完整案例（finance DCF 全流程）
```

---

## License

MIT。详见 [LICENSE](./LICENSE)。

---

## Credits

- **Anthropic 官方 xlsx skill** — openpyxl 操作标准
- **claude-office-skills / buildwithclaude** — 通用 Excel skill 设计参考
- **sartrus/modelling-team-skill** — 三 agent 财务建模启发
- **熊猫办公 / 各 Excel 模板站** — 中文行业模板参考
- **LondonMarket Financial-Model-Excel-Template** — DCF 模板参考
