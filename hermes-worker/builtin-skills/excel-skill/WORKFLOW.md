# Excel Skill 工作流

> 「用 AI + Excel 高效完成一个真实任务」的工作流图谱：按任务找路径，而非线性教程。

按你正在做的事情，找到对应的工作流。

## 工作流地图

```
要做啥？
  │
  ├─ 我有原始数据，要清洗 ──────→ 工作流 1：数据清洗
  ├─ 我有干净数据，要分析 ──────→ 工作流 2：数据分析
  ├─ 我要做 Dashboard ─────────→ 工作流 3：Dashboard
  ├─ 我要 Python 自动化 ────────→ 工作流 4：Python 自动化
  ├─ 我要做行业模板 ───────────→ 工作流 5：行业模板
  └─ 我要审查别人/AI 的代码 ────→ 工作流 6：代码审查
```

---

## 工作流 1：数据清洗

**典型场景**：销售给你 100 行的乱七八糟 Excel，你需要清洗成可分析的格式。

```
1. 打开数据，扫一眼问题在哪
   - 表头是不是在第 1 行（不是的话先解决）
   - 有没有合并单元格（取消）
   - 有没有空行（删除）
   - 有没有"标题行"混在数据里（筛选剔除）

2. 字段类型转换
   - 日期：=DATEVALUE("2024-01-01") 或选中列 → 数据 → 分列 → 日期
   - 数字：选中列 → 数据 → 分列 → 常规
   - 文本拆分（如"张三 13800000000"）：分列 → 分隔符（空格）

3. 去重
   - 选数据 → 数据 → 删除重复值
   - 或：用 COUNTIF 标记，再筛选删除

4. 缺失值处理
   - 数值缺失：用 0 填充 / 用平均值填充 / 删除整行
   - 文本缺失：用"未填写"填充 / 删除

5. Power Query（推荐复杂场景）
   - 数据 → 获取数据 → 从文件
   - PQ 编辑器里所有操作可重复 / 可保存
   - 详见 references/03-power-query.md
```

**工具选择**：
- 数据 ≤ 10000 行 → 直接 Excel 操作
- 数据 > 10000 行 → Power Query 或 Python pandas
- 来源是数据库 → SQL 直接清洗，再导出

---

## 工作流 2：数据分析

**典型场景**：清洗后的数据，要做汇总 / 排名 / 占比 / 增长率。

```
1. 把数据转成"表"（Ctrl + T）
   - 这样新加行自动被透视表 / 公式识别

2. 80% 场景：直接用透视表
   - 见 references/02-pivot-tables.md
   - 拖几下就出汇总，比写 SUMIFS 快

3. 20% 场景：用公式
   - SUMIFS / COUNTIFS：多条件求和/计数
   - XLOOKUP / INDEX-MATCH：跨表查找
   - 见 references/01-formulas-cheatsheet.md

4. 增长率 / 占比 / 排名
   - 同比 = (本期 - 去年同期) / ABS(去年同期)
   - 占比 = 单项 / 总计
   - 排名 = RANK.EQ(值, 范围)

5. 复杂分析（A/B 测试 / 假设检验 / 回归）
   - Excel 数据分析工具：文件 → 选项 → 加载项 → 分析工具库
   - 或用 Python（详见 internet/dau_mau_cohort.py）
```

---

## 工作流 3：Dashboard

**典型场景**：要做一个能给老板看的"销售看板"或"运营周报"。

```
1. 三层结构（给 Dashboard 用）
   ┌────────────────┐
   │ Sheet 1: 数据  │ ← 原始数据，定期更新
   ├────────────────┤
   │ Sheet 2: 计算  │ ← 公式 / 透视表，做汇总
   ├────────────────┤
   │ Sheet 3: 看板  │ ← 用户看的，只展示
   └────────────────┘

2. KPI 卡片（5-8 个核心指标）
   - 大字 + 单位
   - 同比 / 环比变化（颜色区分上升/下降）
   - 见 ecommerce/gmv_dashboard.py 的 sheet 2

3. 图表选择
   - 趋势 → 折线图
   - 对比 → 柱状图
   - 占比 → 饼图（≤ 5 项）/ 条形图（> 5 项）
   - 相关 → 散点图
   - 热度 → 颜色刻度透视表
   见 references/04-charts-and-dashboards.md

4. 切片器（Slicer）
   - 插入 → 切片器 → 选要筛选的字段
   - 报表连接 → 关联多个透视表
   - 用户点切片器 → 多张图同时切换

5. 美化
   - 全表去掉网格线（视图 → 取消"网格线"）
   - 配色统一（用品牌色）
   - 标题清晰
   - KPI 区域加边框 / 浅色背景
```

---

## 工作流 4：Python 自动化

**典型场景**：每月都要生成一份相同结构的 Excel 报表。

```
1. 第一次手工做一份理想模板（注意保留样式 / 公式）

2. 用 openpyxl 复制模板
   from openpyxl import load_workbook
   wb = load_workbook('template.xlsx')

3. 填数据
   ws = wb['Data']
   for i, row in enumerate(data, start=2):
       for j, val in enumerate(row, start=1):
           ws.cell(row=i, column=j, value=val)

4. 调用业务公式（已经写在模板里）

5. 保存
   wb.save(f'report_{month}.xlsx')

6. 让用户打开（自动重算公式）
   或：python scripts/recalc.py report_2024-04.xlsx

完整示例见：scripts/generate_finance/three_statements.py
```

详细 openpyxl API 见 [references/05-openpyxl-python.md](./references/05-openpyxl-python.md)。

---

## 工作流 5：行业模板

**典型场景**：你是新来的电商运营，要建一份「日报」模板。

```
1. 找你行业的现成模板
   templates/ecommerce/      电商
   templates/finance/        金融
   templates/fmcg/           快消
   templates/internet/       互联网

2. 跑模板生成器
   python scripts/generate_ecommerce/gmv_dashboard.py --output my_daily.xlsx

3. 改字段名 / 加你需要的列
   - 多数模板的字段在脚本顶部的常量里
   - 改完重新运行就能再生成

4. 添到日常工作流
   - 每天倒入数据 → 公式自动算 → 看 Dashboard
```

---

## 工作流 6：代码审查

**典型场景**：AI 给你一段 openpyxl 代码，不知道靠不靠谱。

```bash
python scripts/excel_lint.py the_code.py
```

会自动检查 10 类常见坑：
- data_only=True 后 save 导致公式丢失
- pandas to_excel 写带公式 DataFrame
- 公式中混入中文逗号
- sheet 名超 31 字符或含禁止字符
- PatternFill 没传 fill_type
- 等

详见 [references/08-excel-pitfalls.md](./references/08-excel-pitfalls.md)。

---

## 跨工作流约定

### 命名规范

- Sheet 名用「数字 + 中文」：`1.数据`、`2.汇总`、`3.看板`
- 输入区：浅黄背景 (#FFF2CC)
- 输出区：浅灰背景 (#E7E6E6)
- 表头：行业品牌色 + 白字

### 文件名规范

- 模板：`{行业}_{用途}_template.xlsx`
- 实例：`{行业}_{用途}_{年月}.xlsx`
- 不要 `report final final2 vfinal.xlsx`

### 不要做的事

- ❌ 在数据 sheet 里加图表 / 公式 → 数据更新会乱
- ❌ 公式直接引用绝对值 → 数据搬家全炸
- ❌ 合并单元格放数据 → 透视表炸
- ❌ 颜色编码业务规则（"红色 = 紧急"）→ 别人色弱看不出
- ❌ 把所有公式硬编码在一个超长公式里 → 后人维护噩梦

---

## 学习路径建议

```
1 周：必备公式 + 透视表 + 基础格式
  → tutorials/01-excel-basics.md

2-4 周：Power Query + Dashboard
  → references/03-power-query.md + tutorials/03-dashboard-design.md

1-2 月：Python openpyxl 自动化
  → references/05-openpyxl-python.md + scripts/

3 月+：行业深度模板 + 业务建模
  → templates/{你的行业}/
```
