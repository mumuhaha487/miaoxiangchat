# 01 — Excel 公式速查（按场景而非字母顺序）

> 不是 Microsoft 官方那种"按 A-Z 排"的难找。  
> 按"你想干什么"组织，每条都有最常用的实战示例。

## 1. 查找类（最常用）

### `XLOOKUP`（推荐，2020+ 版本）

```excel
=XLOOKUP(查找值, 查找数组, 返回数组, [找不到时返回], [匹配模式], [搜索模式])

# 实战：根据产品 ID 找产品名
=XLOOKUP(A2, products!A:A, products!B:B, "未找到")

# 反向查找（XLOOKUP 不需要 INDEX/MATCH）
=XLOOKUP("张三", names_col, ids_col)

# 模糊查找最大 ≤ 值（[匹配模式]=1）
=XLOOKUP(85, scores, grades, "F", -1)  # 找 ≤ 85 的最大成绩
```

### `VLOOKUP`（旧版本兼容）

```excel
=VLOOKUP(查找值, 表区域, 列号, [近似匹配])

# 必须把第 4 个参数设为 FALSE（精确匹配）！90% 的 bug 都是这里
=VLOOKUP(A2, products, 2, FALSE)

# 限制：只能从左到右查找
```

### `INDEX + MATCH`（VLOOKUP 替代品，更灵活）

```excel
# 反向查找（VLOOKUP 做不到）
=INDEX(返回列, MATCH(查找值, 查找列, 0))

# 实战：根据姓名找员工 ID（姓名在 B 列，ID 在 A 列）
=INDEX(A:A, MATCH("张三", B:B, 0))
```

### `XLOOKUP` vs `VLOOKUP` vs `INDEX/MATCH`

| 场景 | 推荐 |
|---|---|
| Excel 2021+ / Microsoft 365 | XLOOKUP |
| 老版本 + 需要反向查找 | INDEX/MATCH |
| 老版本 + 单向查找 | VLOOKUP（注意第 4 参数） |

## 2. 求和 / 计数 类

### `SUMIFS` / `COUNTIFS` / `AVERAGEIFS`

```excel
=SUMIFS(求和列, 条件列1, 条件1, 条件列2, 条件2, ...)

# 实战：销售大于 1 万 且 区域是华东 的总和
=SUMIFS(D:D, D:D, ">10000", C:C, "华东")

# 注意：条件用引号包，比较运算符也在引号里
=SUMIFS(D:D, A:A, ">="&DATE(2024,1,1), A:A, "<"&DATE(2024,2,1))

# COUNTIFS 同理
=COUNTIFS(C:C, "华东", D:D, ">10000")
```

### `SUMPRODUCT`（万能数组公式）

```excel
# 多条件求和（不用数组公式语法）
=SUMPRODUCT((C:C="华东")*(D:D>10000)*D:D)

# 加权平均
=SUMPRODUCT(B:B, C:C) / SUM(C:C)
```

## 3. 日期 / 时间 类

```excel
# 当前日期 / 时间
=TODAY()           # 2024-01-15
=NOW()             # 2024-01-15 14:30:25

# 计算天数差
=DAYS(end_date, start_date)
=end_date - start_date  # 直接减

# 月份 / 季度
=MONTH(A1)         # 1-12
=QUARTER(A1)       # ❌ 不存在！手动算
=ROUNDUP(MONTH(A1)/3, 0)  # 1-4

# 字符串转日期
=DATEVALUE("2024-01-15")

# 月末日期
=EOMONTH(A1, 0)    # 当月末
=EOMONTH(A1, 1)    # 下月末

# 工作日（跳过周末和法定假日）
=NETWORKDAYS(start, end, [holidays])
=WORKDAY(start, days, [holidays])  # start 之后 N 个工作日
```

## 4. 文本类

```excel
# 拼接（推荐 TEXTJOIN，对空值友好）
=TEXTJOIN(",", TRUE, A1:A10)  # TRUE 表示忽略空单元格

# 旧用法
=A1 & "-" & B1
=CONCATENATE(A1, "-", B1)

# 提取
=LEFT(A1, 3)
=RIGHT(A1, 3)
=MID(A1, 2, 5)  # 从位置 2 开始取 5 个字符

# 查找位置
=FIND("@", A1)  # 区分大小写
=SEARCH("@", A1)  # 不区分大小写

# 替换
=SUBSTITUTE(A1, "old", "new")
=REPLACE(A1, 1, 3, "ABC")  # 从位置 1 替换 3 个字符为 ABC

# 去空格
=TRIM(A1)  # 仅去首尾 + 中间多余空格

# 大小写
=UPPER(A1) / =LOWER(A1) / =PROPER(A1)

# 长度
=LEN(A1)

# 数字转文本（带格式）
=TEXT(0.15, "0.00%")  # "15.00%"
=TEXT(TODAY(), "yyyy-mm-dd")
=TEXT(1234567, "#,##0")  # "1,234,567"
```

## 5. 逻辑 / 条件类

### `IF` / `IFS`（多条件）

```excel
# 简单 IF
=IF(A1>=60, "及格", "不及格")

# 嵌套 IF（不推荐，难读）
=IF(A1>=90, "优", IF(A1>=80, "良", IF(A1>=60, "中", "差")))

# IFS（推荐）
=IFS(A1>=90, "优", A1>=80, "良", A1>=60, "中", TRUE, "差")
# TRUE 作为最后兜底

# IFERROR：处理错误
=IFERROR(A1/B1, 0)  # B1 是 0 时返回 0 而不是 #DIV/0!
=IFERROR(VLOOKUP(...), "未找到")
```

### `AND` / `OR` / `NOT`

```excel
=IF(AND(A1>0, B1>0), "都正", "有非正")
=IF(OR(A1="否", B1="否"), "有否定", "全部肯定")
```

## 6. 数字 / 数学类

```excel
# 四舍五入
=ROUND(3.567, 2)    # 3.57
=ROUNDUP(3.123, 1)  # 3.2 向上
=ROUNDDOWN(3.789, 1)  # 3.7 向下
=INT(3.7)           # 3 整数部分
=TRUNC(3.789, 1)    # 3.7 截断

# 余数 / 取模
=MOD(7, 3)  # 1

# 绝对值
=ABS(-5)  # 5

# 最大 / 最小
=MAX(A:A)
=MIN(A:A)
=LARGE(A:A, 3)  # 第 3 大
=SMALL(A:A, 2)  # 第 2 小

# 排名
=RANK.EQ(A1, A:A, 0)  # 0 降序，1 升序

# 随机
=RAND()         # 0-1 随机
=RANDBETWEEN(1, 100)  # 1-100 整数随机
```

## 7. 统计 / 分位数类

```excel
=AVERAGE(A:A)
=MEDIAN(A:A)
=MODE.SNGL(A:A)
=STDEV.S(A:A)   # 样本标准差
=STDEV.P(A:A)   # 总体标准差
=VAR.S(A:A)
=PERCENTILE.INC(A:A, 0.25)  # 25 分位数
=QUARTILE.INC(A:A, 1)       # 第 1 四分位
=COUNT(A:A)     # 数字
=COUNTA(A:A)    # 非空（含文本）
=COUNTBLANK(A:A)
```

## 8. 数组公式（动态数组，2021+）

```excel
# UNIQUE：去重
=UNIQUE(A:A)

# SORT
=SORT(A1:B100, 2, -1)  # 按第 2 列降序

# FILTER
=FILTER(A:C, B:B="华东")  # 筛选区域=华东

# SEQUENCE
=SEQUENCE(10)         # 1-10
=SEQUENCE(5, 5)       # 5×5 矩阵

# RANDARRAY
=RANDARRAY(10, 3, 0, 100, TRUE)  # 10×3 矩阵 [0,100] 整数
```

## 9. 财务类

```excel
# 净现值
=NPV(贴现率, 现金流1, 现金流2, ...)

# 内部收益率
=IRR(现金流范围)

# 等额本息还款
=PMT(rate, nper, pv, [fv], [type])
=PMT(0.05/12, 360, -1000000)  # 100 万房贷，30 年，年利率 5%

# 债券价格
=PRICE(...)
=YIELD(...)  # 收益率
```

## 10. 错误处理

```excel
=IFERROR(原公式, 错误时返回)
=IFNA(原公式, "找不到时返回")  # 仅 #N/A
=ISERROR(A1) / =ISNA(A1) / =ISBLANK(A1)
```

## 行业速查

### 金融
- DCF 现值：`=NPV(WACC, FCF_range)`
- 复利终值：`=FV(rate, nper, 0, -pv)`
- 股息折现：`=PV(rate, nper, 0, -future_dividend)`

### 电商
- 转化率：`=COUNTIFS(行为列, "支付") / COUNTIFS(行为列, "曝光")`
- 客单价：`=SUMIFS(金额, 类型, "已支付") / COUNTIFS(类型, "已支付")`
- ROAS：`=GMV / 投放金额`
- RFM 评分：`=R评分*100 + F评分*10 + M评分`

### 互联网
- DAU 移动平均：`=AVERAGE(OFFSET(A1, ROW()-7, 0, 7, 1))`  # 7 日均
- 留存率：`=COUNTIFS(注册日, 日期, 活跃日, ">="&日期+1) / COUNTIF(注册日, 日期)`
- LTV/CAC：`=平均生命周期收入 / 客户获取成本`

### 快消
- 销售达成率：`=实际 / 目标`
- 渗透率：`=有效门店 / 总门店`
- ABC 分类：用 RANK + IF
  ```excel
  =IF(RANK(A1, A:A) <= 累计 80%, "A",
     IF(RANK(A1, A:A) <= 累计 95%, "B", "C"))
  ```

---

## AI 经常编错的公式

| AI 常编 | 正确 |
|---|---|
| `=QUARTER(A1)` | `=ROUNDUP(MONTH(A1)/3, 0)` |
| `=COUNTUNIQUE(A:A)` | `=COUNTA(UNIQUE(A:A))` |
| `=AVERAGEIF(D:D, ">10000")` 但忘了第 3 参数 | `=AVERAGEIF(D:D, ">10000", 求和列)` |
| `=SUMIFS` 条件用了 `=` 号 | 直接传值，比较运算符要在条件字符串里：`">10000"` |
| `=DATE(2024-1-15)` | `=DATE(2024, 1, 15)` |
| `=IF(AND(A1>0)(B1>0), ..., ...)` | `=IF(AND(A1>0, B1>0), ..., ...)` |
