# 图表布局改造方案

> ← 返回 [架构说明](architecture.md)
>
> **状态：✅ 已实施（2026-10-12）。** 本文既是设计也是实施记录；
> 实施中实测出的三个坑记在 2.12 节，改这块代码前先看。

用户 2026-10-12 在参考截图（2104 × 1321）上标注了两条箭头，本文分两部分：
第一部分记录**当前布局**是什么样，第二部分记录**计划中的方案**。

---

# 一、当前的布局是什么样的

## 1.1 结构

```
chart-frame (grid: header 48px / body；列 1fr | 280px)
├─ chart-frame-header (48px, grid 1fr | 280px)
│  ├─ header-left (grid 1fr 1fr 1fr)
│  │   ├─ sec-1  代码 名称 报价 Tag 爱心按钮      ← 截图内容，约 700px 宽
│  │   ├─ sec-2  周期 Select + 复权 Select
│  │   └─ sec-3  周期后缀 + 设置齿轮
│  └─ header-right  Radio 筹码 / 基本面
└─ chart-frame-body
   └─ chart-row (absolute inset:0, flex row, 高 1272)
      ├─ kline-container (flex 1 = 1790px, border-right)
      │   ├─ 主图 pane (602px，图例 MA20 在左上)
      │   └─ 4 个副图 (160×4) + X 轴 (30) = 670
      └─ chart-right-column (280px)
         ├─ chart-right-top  (602px)  筹码区：canvas + 获利/套牢
         └─ chart-right-bottom (670px) 空
```

头部和 K 线区是上下关系；右列和 K 线区是左右关系。

`header-right` 里那个 Radio（筹码 / 基本面）**同时承担两个角色**：切换器 + 右上面板的标题。
筹码面板本身（`ChipDistribution`）内部没有任何标题文字，只有画布和一行「获利 / 套牢」摘要。

## 1.2 两条线分别是什么

用户标注的两条线都不是独立的装饰元素，而是**布局边界重合出来的位置**：

| 线 | 实际是什么 |
|---|---|
| **红线**（横，y ≈ 650） | 主图 pane 底边 **与** `chart-right-top` 底边重合。CSS 注释里写死了这条约束：「筹码 = kline 总高 − 副图区高，正好和主图同高」 |
| **蓝线**（竖，x ≈ 1822） | `kline-container` 的 `border-right`，**与** 头部 `sec-3` 的 `border-right` 对齐（`chart-frame-header` 用 subgrid 保证） |

所以移动红线 = 改变主图与副图的高度配比，两侧同步生效；
移动蓝线 = 改变 `--chart-right-w`，头部和 body 一起让位。

## 1.3 尺寸现状

| 项 | 值 | 来源 |
|---|---|---|
| chart-row 高 | 1272 | 100vh − padding 16×2 − border 1×2 − header 48 |
| 主图 pane | 602 | 1272 − (4×160 + 30) |
| 副图区总高 | 670 | `--sub-charts-total` = 4×160 + 30 |
| 每副图 | 160 | `--sub-chart-h` |
| X 轴 | 30 | CSS 假设值，klinecharts 实际是 `xAxis.size: 'auto'` 自算 |
| 右列宽 | 280 | `--chart-right-w` |
| K 线区宽 | 1790 | 反推 |
| 头部高 | 48 | `--chart-header-h` |

参考截图量测：红箭头顶端 y ≈ 548，蓝箭头左端 x ≈ 1621，红线现位 y ≈ 650，蓝线现位 x ≈ 1822。
图片是 1:1（dpr=1）——主图实测 650−48 = 602，与 CSS 算出的 602 完全闭合，可以确认。

## 1.4 四象限现状占比

分母 = chart-row 2070 × 1272 = 2,633,040 px²。

| 象限 | 尺寸 | 占比 |
|---|---|---|
| 左上 主图 | 1790 × 602 | 40.9% |
| 右上 筹码区 | 280 × 602 | 6.4% |
| 左下 副图区 | 1790 × 670 | 45.5% |
| 右下 空 | 280 × 670 | 7.1% |

右列整体只占 13.5%，主图占 40.9%。

## 1.5 控制点现状（两处坑）

| 控制点 | 位置 | 说明 |
|---|---|---|
| 副图高度 | `MarketChart.css:5` `--sub-chart-h: 160px` | CSS 侧 |
| 副图高度 | `MarketChart.tsx:578` `setPaneOptions({ height: 160 })` | **klinecharts 侧，硬编码** |
| 右列宽 | `ChartFrame.tsx:33` `rightWidth = 280` | **真正生效**，以 inline style 注入 |
| 右列宽 | `ChartFrame.css:17` `--chart-right-w: 280px` | **死代码**，被上一条 inline 覆盖 |
| 右列宽 | `MarketChart.css:81` `var(--chart-right-w, 280px)` | fallback |

> 坑 1：副图高度有 CSS 和 klinecharts 两条独立路径，只改 CSS 会让右列底边与副图错位。
>
> 坑 2：`--chart-right-w` 看着在 CSS 里，实际由 `ChartFrame.tsx:33` 的 inline style 决定，
> 改 CSS 那两处不会有任何效果。

## 1.6 现状已有的技术约束

这几条改动前后都成立，动手前必须知道：

1. **klinecharts 的指标图例画在 canvas 上**。`MA20: 20.00` 那行、以及 VOL / MACD / KDJ
   各自的图例，都不是 DOM 元素。
2. **主图右侧有价格 y 轴**，宽度由 klinecharts 运行时算，没有固定值。
3. **`.kline-container` 上挂着 `onKeyDown`**，←/→ 用来切 K 线，任何放进去的控件都会吃到这两个键。
4. **`ChipDistribution` 用 `ResizeObserver`**（`ChipDistribution.tsx:145`）跟随容器宽度自适应，
   画布绘制逻辑是自适应的，容器变宽不会失真，但横向比例会变。
5. **头部 `header-left` 是三等分 grid**（`1fr 1fr 1fr`），每段等宽。sec-1 里的内容按截图量宽约 **700px**，
   而整个 header-left 只有 1790px，三段各约 596px——sec-1 已经是被挤满的。

## 1.7 现状遗留的疑点

- **14px 偏差**：从截图反推出的右列当前宽度是 **266px**，但 CSS 写的是 **280px**，差 14px 原因未确认。
- **`--x-axis-h: 30px` 是 CSS 里的假设值**，klinecharts 那边是 `size: 'auto'` 自算的。
  垂直方向这次恰好对上，但没验证过相等。

---

# 二、目前计划的方案

## 2.1 目标（六步）

| 步 | 动作 | 量变 |
|---|---|---|
| 1 | 红线上移 | 主图 602 → **548px**，副图区 670 → **770px**（每副图 160 → **185px**） |
| 2 | 蓝线左移 | 右列宽 280 → **466px**，K 线区 1790 → **1604px** |
| 3 | 头部行取消，48px **收回给 K 线区** | chart-frame 不再有独立 header 行 |
| 4 | 头部内容拆分安置 | 标识/报价 → 筹码区右列；日线/复权/设置 → 主图 MA 行最右侧 |
| 5 | 删除「筹码 / 基本面」切换 | `ChartHeaderRight` 整个组件移除，右上面板恒为筹码，**无标题** |
| 6 | 两根线比例可配置 | 设置弹窗加两项比例，默认值 = 第 1 / 2 步算出的结果 |

**第 3 步是第 1 步的前提**：收回 48px 后 K 线区高 1272 → 1320，
所以主图目标高度不是 502 而是 **548**，副图区也随之为 770 而非 722。

**第 5 步是第 3 步的连带结果**：header 取消后 Radio 没有容身之处。
筹码面板靠图形本身就能辨识是筹码分布，不需要标题，所以切换器和标题一起删。

**第 6 步把第 1 / 2 步的数字从硬编码降级成默认值**，详见 2.6 节。

## 2.2 目标结构

```
chart-frame (grid: 只有 body，取消 header 行)
└─ chart-frame-body
   └─ chart-row (absolute inset:0, flex row, 高 1320)
      ├─ kline-container (flex 1 = 1604px, border-right)
      │   ├─ overlay 右上  [日线 ▾][复权 ▾][⚙]   ← 贴 "MA20: xx" 同一行右端
      │   ├─ 主图 pane (548px，图例 MA20 在左上)
      │   └─ 4 个副图 (高度平分) + X 轴 (30) = 770
      └─ chart-right-column (466px)
         ├─ chart-right-top (550px，宽度平分两列，无标题)
         │   ├─ 左 50%  ChipDistribution (canvas + 获利/套牢)
         │   └─ 右 50%  代码 名称 报价 Tag 爱心        ← 从原 sec-1 搬来
         └─ chart-right-bottom (770px，空)
```

## 2.3 尺寸对照

| 项 | 改造前 | 改造后（理论） | 控制点 |
|---|---|---|---|
| chart-row 高 | 1272 | 1320 | 取消 header 行 |
| 主图 pane | 602 | 548 | 副图区高度反推 |
| 副图区总高 | 670 | 770 | `--sub-chart-h` × 4 + `--x-axis-h` |
| 每副图 | 160 | 185 | `--sub-chart-h` + `setPaneOptions` |
| X 轴 | 30 | **24** | 实算 24，不是原先假设的 30 |
| 右列宽 | 280 | 466 | `--chart-right-w` |
| K 线区宽 | 1790 | 1604 | 反推 |
| 头部高 | 48 | 0 | 收回给 K 线区 |

⚠️ 这张表是**按参考截图（chart-row 2070 × 1320）推的理论值**，不是实测——
绝对像素随视口变，真实验收请看 §2.10。实测视口（2104×1321）下默认比例给出的是
主图 **527**、副图各 **183**、右列 **381**（不是表里的 548 / 185 / 466）。
X 轴那一列原先写「30 → 30 不动」，**24** 是 klinecharts `getAutoSize()` 的实算值
（1 + 3 + 4 + 4 + 12），已由 §2.12 坑 3 定案。

> 这张表里的 185 / 466 是**默认值，不是硬编码**。设置弹窗里的两项比例会覆盖它们，见 2.6。

## 2.4 四象限改后占比

分母 = chart-row 2070 × 1320 = 2,732,400 px²。红线在 local y=550，蓝线在 local x=1604。

| 象限 | 尺寸 | 面积 | 占比 | 对比现在 |
|---|---|---|---|---|
| 左上 主图 | 1604 × 550 | 882,200 | **32.3%** | 40.9% |
| 右上 头部内容列 | 466 × 550 | 256,300 | **9.4%** | 6.4% |
| 左下 副图区 | 1604 × 770 | 1,235,080 | **45.2%** | 45.5% |
| 右下 空 | 466 × 770 | 358,820 | **13.1%** | 7.1% |

右列整体 13.5% → **22.5%**，主图 40.9% → **32.3%**。

## 2.5 改动清单

### 尺寸（3 处，缺一不可）

| 文件 | 行 | 现状 | 改后 |
|---|---|---|---|
| `web/src/pages/MarketChart.css` | 5 | `--sub-chart-h: 160px` | `185px` |
| `web/src/pages/MarketChart.tsx` | 578 | `setPaneOptions({ id, height: 160 })` | `185` |
| `web/src/components/ChartFrame.tsx` | 33 | `rightWidth = 280` | `466` |

`ChartFrame.css:17` 和 `MarketChart.css:81` 跟着改成 466 保持一致（一个是死代码，一个是 fallback）。

### 取消 header 行

| 文件 | 行 | 改动 |
|---|---|---|
| `ChartFrame.css` | 22 | `grid-template-rows: var(--chart-header-h) 1fr` → `1fr` |
| `ChartFrame.tsx` | 41-44 | 去掉 `--chart-header-h` 注入 |
| `ChartFrame.tsx` | 35-42 | `.chart-frame-header` 及两个子节点变死代码，可删 |
| `MarketChart.tsx` | 1195-1216 | `headerLeft` / `headerRight` 两个节点拆散重排 |

### 筹码区两列

| 文件 | 行 | 改动 |
|---|---|---|
| `MarketChart.tsx` | 1265-1283 | `chart-right-top` 内部加一层两列容器 |
| `MarketChart.css` | 87-103 | `chart-right-top` 由 `flex: 1` 改两列 grid |

左列仍是 `<ChipDistribution>`，右列放原 sec-1 内容。

### MA 行右侧 overlay（新增）

在 `.kline-container` 内加一个绝对定位 overlay，承载 周期 Select + 复权 Select + 设置齿轮。
主图 pane 顶部就是容器顶部（`candle_pane` 是第一个 pane），overlay 钉 `top: 0` 即可与图例同行。

### 组件拆分

| 现有 | 拆成 |
|---|---|
| `ChartHeaderLeft` sec-1 | `SymbolInfo`：代码 / 名称 / 报价 / 爱心（**「个股行情 / 指数行情」Tag 已删**，见下） |
| `ChartHeaderLeft` sec-2 + sec-3 | `ChartTools`：周期 Select + 复权 Select + 齿轮 + 设置 Modal |
| `ChartHeaderRight` | **整个组件删除**，见下 |

### 删除筹码 / 基本面切换

header 取消后 Radio 没有容身之处，且筹码区靠图形本身就能辨识，不需要标题，
故切换器、标题、基本面占位一并清理：

| 文件 | 行 | 改动 |
|---|---|---|
| `ChartHeader.tsx` | 49-52 / 342-363 | 删除 `ChartHeaderRightProps` 与 `ChartHeaderRight` |
| `ChartHeader.tsx` | 2 / 11 | 移除 `Radio` / `RightPanelMode` import |
| `chartOptions.ts` | 51 | 删除 `RightPanelMode` 类型 |
| `MarketChart.tsx` | 459-461 | 删除 `rightPanelMode` state |
| `MarketChart.tsx` | 16 | import 只留 `ChartHeaderLeft` |
| `MarketChart.tsx` | 1211-1216 | 删除 `headerRight` prop |
| `MarketChart.tsx` | 1266-1282 | 三元判断去掉，`chart-right-top` 直接渲染 `<ChipDistribution>` |
| `MarketChart.css` | 105-126 | 删除 `.fundamentals-placeholder` 及其子选择器 |

删完后右列只有一种模式，**基本面功能整体推迟**，不再保留占位区。

### 版面比例设置

| 文件 | 改动 |
|---|---|
| `web/src/utils/layout.ts`（新建） | 比例常量与 2.6 的反推公式（**存取走 `app_config`，不是 localStorage**，见 §2.6「存放位置」） |
| `web/src/components/ChartHeader.tsx` | 设置 Modal 增加「版面」分组（两个 Slider） |
| `web/src/pages/MarketChart.tsx` | 订阅比例 → 算 px → 写 inline 变量 + `setPaneOptions` + 容器 resize 重算 |
| `web/src/pages/MarketChart.css` | `--sub-chart-h` 默认值改为 185 |
| `web/src/config/api.ts` | `AppConfig` 加 `top_ratio` / `right_ratio`，走 `GET`/`PUT /api/config` |

依赖：齿轮按钮要先改到 MA 行 overlay 里，设置入口才落在新位置。

## 2.6 版面比例设置（新增）

两根线不再写死像素，改由「设置」弹窗里的两个比例驱动。

### 两项比例

| 设置项 | 含义 | 默认 | 换算出的像素 |
|---|---|---|---|
| 横线距上比例 | 红线位置 / K 线区高度 | **42%** | 主图 554px、副图各 184px |
| 竖线距右比例 | 蓝线位置 / K 线区宽度 | **22.5%** | 右列 466px |

默认值就是第 1、2 步算出来的结果（42% 换出副图 184px，比 2.3 表里的 185 少 1px，
在取整误差范围内）。调设置只是覆盖默认值。

### 反推公式

比例定的是**主图占 K 线区的多少**，不是副图的像素高度，所以要反推：

```
dist       = H − X_AXIS_H − 4 × SEPARATOR_SIZE     // 可分配高度
sub_h      = round(dist × (1 − r_h) / 4)           // 4 个副图均分
main_h     = dist − 4 × sub_h
right_w    = W × r_v                               // W = .chart-row 宽
```

其中 `X_AXIS_H = 24`（klinecharts `xAxis.size:'auto'` 实算：axisLine 1 + tickLine 3
+ marginStart 4 + marginEnd 4 + tickText 12），`SEPARATOR_SIZE = 1`，共 **4 条** pane 分隔线。

⚠️ **分母是「可分配高度」`dist`，不是 K 线区总高 `H`。** 早先这里写成
`sub_h = (sub_total − x_axis_h) / 4` 且 `x_axis_h = 30`，照着实现会算错：

```
H = 1287, r_h = 0.42
  旧式（错）: dist' = 1287 − 30 = 1257 → sub_h = (1257 − 30)/4 = 306.75??  实算主图 540.5、副图 179.1
  正确     : dist  = 1287 − 24 − 4 = 1259 → sub_h = round(1259×0.58/4) = 183
                                                 → main_h = 1259 − 4×183 = 527   ✅ 与实测一致
```

`right_w` 的基准 `.chart-row` 宽**不能**用 K 线区宽（那是循环引用），见 §2.12 坑 2。

两项**互相正交**：r_h 只决定高度分配，r_v 只决定宽度分配，调一边不影响另一边。

### 均分规则

比例定的是两块区域**在哪里切开**，切完之后各部分之间按「均分」占满，与比例无关。
改比例时这两条始终成立，不需要额外分支：

| 区域 | 均分方式 | 实现 |
|---|---|---|
| **左下（4 个副图）** | 高度平分，X 轴与 4 条分隔线**先扣掉不参与分配** | `sub_h = round(dist × (1 − r_h) / 4)` |
| **右上（筹码 + 头部内容）** | 宽度平分，两列各 50% | `grid-template-columns: 1fr 1fr`，不设固定 px |

所以调 r_h 时 4 个副图始终等高；调 r_v 时右上两列始终等宽，右侧内容不会因为
`r_v` 变化而多出或少掉一块固定宽度的留白。

> ⚠️ 均分宽度把右上一分为二后，**每列只有 W × r_v / 2**。按 2.3 的参考口径
> （chart-row 2070 宽、r_v=22.5%）右列 466px → 各 233px；实测视口下 chart-row 1694 宽，
> 右列 381px → 各 190.5px。**绝对像素随视口变，判据看比例。**
> 头部内容原本量宽约 700px，塞进这么窄一列需要压字号、收间距，可能还要去掉部分字段。
> 筹码横条同理，窄栏下价格区间会很窄。这条是 2.8 待确认 #2 的前提。

### 一个必须处理的耦合

副图高度是**跟着窗口高度变的**。窗口缩放时比例不变、副图像素高度会变，
所以 resize 之后必须重新调 `chart.setPaneOptions({ height })`——
klinecharts 自己的 ResizeObserver 只按已存的 pane 高度重排，不会重算我们设的值。

不做处理会踩成回环：「setPaneOptions 改布局 → 触发容器 resize → 再算一遍 → 再 setPaneOptions」。
两道保险：resize 用 rAF 合并成一帧；算出的 px 与当前值相差 < 1px 时直接跳过 setPaneOptions。

### 取值范围

| 项 | 范围 | 理由 |
|---|---|---|
| r_h | 25% ~ 65% | 下限保证主图容得下 MA 图例 + overlay；上限保证副图不被压到读不出刻度 |
| r_v | 15% ~ 45% | 下限保证筹码横条可读；上限保证 K 线区不被压到不足一半 |

用 Slider 的 min/max 挡住越界，不做手动输入框。

### 存放位置

**后端 `app_config` 表**，键 `top_ratio` / `right_ratio`（2026-10-13 由 localStorage 迁入）。

理由：localStorage 换浏览器/清缓存就没了，且前端默认值与后端配置会成为**两处真相**。
`app_config` 本来就是给前端显示类设置持久化用的 key-value 表，
`GET /api/config` 只返回库里真实存在的键、没配过的由前端用自己的默认值兜底。

> ⚠️ 本节 2026-10-13 之前写的是「localStorage，键 `zx.layout`，存 `{ mainRatio, rightRatio }`。
> **不进后端 `app_config`**」—— 实现已经改掉了，这段是历史残留，勿照着做。

配套约定（读 `server/src/server/app_config.py` 与 `web/src/config/api.ts`）：
Slider 必须拆 `onChange`（本地预览）/ `onChangeComplete`（落库），
否则一次拖动会发几十个 `PUT /api/config`。

### 设置弹窗形态

齿轮按钮（改后落在 MA 行右侧 overlay 里）→「设置」Modal 现有「现价线」一项，
再加分隔线和新的一组：

```
现价线  [✓]
──────────────
版面
横线距上  [=====●───────]  42%    （554px）
竖线距右  [───────●=====]  22.5%   （466px）
```

Slider 右侧同时显示换算出来的像素，让人知道实际生效值。

Modal 走 portal 挂在 body 上，**不在 `.kline-container` 内**，
所以 Slider 的方向键不会触发「←/→ 切 K 线」——与 overlay 里那两个 Select 情况相反，不用额外处理。

### 与布局改造的关系

第 1、2 步的 185 / 466 从硬编码改为这两项比例的默认值：

- `--chart-right-w`：由 `MarketChart` 算出右列 px，传给 `ChartFrame` **已有的** `rightWidth` prop
  （不需要新增机制），它的 inline style 自然覆盖 `ChartFrame.css:17`。
- `--sub-chart-h`：写在 `.market-chart-page` 的 inline style 上，CSS 里保留 185 作首帧兜底。
- 副图高：同一个值同时喂给 CSS 变量和 `chart.setPaneOptions`。

## 2.7 方案引入的技术约束

1. **overlay 只能是浮层**。图例是 canvas 画的（见 1.6-1），日线/复权/齿轮插不进那条 DOM。
   视觉上和图例同一行，但不是同一条渲染链。
2. **右对齐要避开 y 轴**。overlay 的 `right` 偏移必须让开价格刻度那条轴，
   轴宽运行时才知道，固定值对不上——用 `convertToPixel` 或读轴 canvas 实测宽度做运行时偏移。
3. **方向键会和 K 线打架**。Select 下拉打开后按 ←/→ 选选项，事件冒泡到 `.kline-container`
   会把十字线一起挪走。必须在两个 Select 和齿轮上 `stopPropagation`，否则选周期时图表乱跳。
4. **overlay 需要 `pointer-events: auto`**，同时确认不挡住十字线 hover——它只占右上角一小块。
5. **头部内容必须改成单行横排**。原来三等分时每段 596px 已经挤满，
   右上再按宽度平分后每列只剩 233px，不可能继续竖排分段。

## 2.8 待确认

| # | 事项 | 默认 |
|---|---|---|
| 1 | 右列 550px 高，头部内容只占顶部 48px，下面空约 500px | 接受留白，暂无填充计划（基本面已整体移除） |
| 2 | 头部内容横向是否够放（约 700px 内容 vs 均分后的 233px 列宽） | 必须改单行横排 + 压字号收间距，可能还要去掉部分字段 |

## 2.9 落地前的测量（先量后改）

1.7 里那条 14px 偏差必须先解决，否则目标值 466 会整体偏 14px。

```powershell
# 起前端（vite 绑 IPv6，必须用 localhost 访问）
cd D:\repo\03_pers-proj\zx-stock\web
npx vite --port 8212 --strictPort
npm install --no-save playwright-core   # 用完卸载
```

量 `.chart-row` / `.kline-container` / `.chart-right-column` / `.chart-right-top` 的
`getBoundingClientRect`，以及各 pane canvas 的 y / h。同时确认 `--x-axis-h: 30px`
与 klinecharts `xAxis.size: 'auto'` 的实际值是否真的相等。

## 2.10 验收

比例化之后，**绝对像素随视口变，验收判据也改成比例**。实测数据（视口 2104×1321，
`/watchlist?code=SH.600570`，chart-row 1694 × 1287）：

| 判据 | 期望 | 实测 |
|---|---|---|
| header 行已删除 | 是 | ✅ |
| 筹码/基本面 Radio 已删除 | 是 | ✅ |
| 4 个副图等高 | 是 | ✅ 183/183/183/183 |
| 右上两列等宽 | 是 | ✅ 191/191 |
| 右列宽 ÷ chart-row 宽 | = r_v | ✅ 381/1694 = 22.5% |
| 主图底 = 右列上半底 | 差 ≤ 2px | ✅ 544 = 544 |
| 副图区总高 + 4 条分隔线 | = 右列下半高 | ✅ |
| overlay 右边缘 | 让开 y 轴刻度 | ✅ 1641 < 绘图区右缘 1643 |
| 浮窗顶 | 主图图例下方 | ✅ 容器顶 +28px |
| 拖动 Slider 后重算 | 红线仍与右列底对齐 | ✅ |

最后 `npx tsc -b --force`。本次改动**零新增错误**；现存 3 个错误
（`MarketChart.tsx` 的 `ApiKLine` / `setExDividendClickHandler(null)`、
`exDividendMarkers.ts:186`）属并行会话的除权除权功能，未代改。

## 2.11 本次不做

配色统一（VOL/MACD/BOLL 改蜡烛红绿）、涨停绿/跌停鲜艳红、区域统计弹窗后续字段、
筹码面板内部布局调整、基本面功能本身。均不在本方案范围内。

其中**基本面**是「本次直接删除占位、整体推迟」，不是保留待改。

### 实施后追加删除的两处（2026-10-13）

| 删掉的 | 位置 | 原因 |
|---|---|---|
| 橙色周期后缀徽章（"1D"） | `ChartTools` | 周期已经写在左边 Select 里，徽章是重复信息，还把 overlay 从 196px 撑到 233px |
| 「个股行情 / 指数行情」Tag | `SymbolInfo` | 右列只有一半宽，Tag 挤位置不划算；指数/个股从 code 前缀就能看出来 |

连带清理：`ChartHeader.tsx` 的 `Tag`、`INDEX_CODE_RE` import，
`ChartHeader.css` 的 `.period-suffix`、`.symbol-info .ant-tag` 两条规则。

> 实测遗留：右列压到 191px 后，筹码图顶部的价格标签和「获利 / 套牢」摘要行
> 会挤在一起（`.chip-summary` 是 absolute top:0，画布自己也在顶上画价格）。
> 这属于筹码面板内部布局，按约定本次不动，需要时单独处理。

## 2.12 实施中实测出的三个坑

### 坑 1：自定义属性在**声明它的那一层**替换 var()，不是在使用点

`:root` 里写 `--sub-charts-total: calc(4 * var(--sub-chart-h) + var(--x-axis-h))`，
直觉上以为覆盖 `.market-chart-page` 上的 `--sub-chart-h` 就会让 total 自动变。
**不会。** 按 CSS Variables 规范，自定义属性的计算值在**声明它的元素**上就把
`var()` 替换掉，`:root` 上算出来的是 `calc(4 * 185px + 24px)`，之后原样继承下去。

表现为：`.chart-right-bottom` 的高度死死跟着 `:root` 的兜底值走，改比例时
红线移动、右列底边不动，**差 4~8px**。

**修法**：`--sub-charts-total` 也整个值 inline 写出去
（`utils/layout.ts` 的 `subChartsTotalHeight`），不指望 CSS 的 calc 跟随。

### 坑 2：右列宽的比例基准不能用 K 线区自己的宽度

`rightWidth = klineContainer.clientWidth × r_v` 是**循环引用** —— 右列宽本身就是
从 chart-row 宽切出来的，拿剩下的那块再乘一次比例，结果只占 chart-row 的 `r_v × (1 − r_v)`。

实测：设 22.5%，右列实际只占 **18.4%**（22.5% × 77.5% = 17.4%，加取整）。

**修法**：比例基准取 `.chart-row` 的宽度，ResizeObserver 同时观察
`.kline-container`（高度）和 `.chart-row`（宽度）两个元素。

### 坑 3：横线位置只能由副图高反推，设主图高会被静默忽略

读 `dist/index.esm.js:14804` 才发现：klinecharts 分配 pane 高度时，
**优先把 `id === CANDLE` 的 pane 选成 flexible pane 吸收全部余数**，其余 pane 按
`options.height` 固定。所以 `setPaneOptions({ id: 'candle_pane', height })` 不报错、
也不生效，横线位置完全由 4 个副图高决定。

副图区总高还必须算上 **4 条 pane 分隔线**（`separator.size = 1`）和实测的
X 轴高度 **24px**（不是 CSS 里原先假设的 30），少算就会让右列底边高 4px。
