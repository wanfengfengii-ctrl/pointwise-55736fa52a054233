# 破坏性抽检交班

`README.md` 记录现有 V1 全标签交班。V2 面向只对抽中片做破坏性判读的班次。继续使用实验台库与分析库各自的初始化和连接约定。

## 实验台锁定与发行

- `bench.handoff.lock_sample(conn, freeze_id, design, draw)` 返回带 `draw_id` 和 `sample` 的对象，`sample` 是按冻结片序排列的元组。`design` 是有序序列，每项为 `(片号序列, 权重)`。权重与抽签值接受 `Fraction`、整数和可精确解析的有理字符串，布尔值与浮点数无效。权重均为正且总和为一。候选名单非空、等大小，名单内片号互异，同一片集合不得重复成为候选名单。
- 首次锁定须与该冻结的标签写入及预测更正有明确先后。首次锁定看到任何已登记的最终标签即拒绝。锁定总体为当时甲乙预测均齐备的全部片，保留原片序与双方的确切修订、值及出处。候选名单只能包含总体片，且总体中每片须在至少一个候选名单内。抽签值在 `[0,1)`，按候选名单次序落入左闭右开的累计权重区间，固定对应名单。锁定的设计、抽签值、总体、修订和实际名单不可变。
- 同一冻结至多锁定一次。比较重试时使用原锁定总体，按分数值与原候选顺序核对设计和抽签值。候选名单内部的输入顺序可不同。等价请求即使在录入标签、补报或更正预测后也返回原抽次与名单。异参请求报 `ValueError`。
- `bench.handoff.issue_sample_handoff(conn, freeze_id)` 返回 V1 形状的 `IssuedHandoff`，正文为 V2。对存在但尚未锁定的冻结、抽中标签未齐或其他非法请求报 `ValueError`。件内带且仅带实际抽中片的最终标签。沿用 `bench.labels.record_label`，原冻结上其他已登记标签不进入本件。用 `bench.handoff.read_issued_handoff(conn, handoff_id)` 按号领所存正文。

## 交接件与先后

V2 JSON 包含 `version`、`trial_id`、`freeze_id`、`specimens`、`models`、`labels`、`predictions`、`draw_id`、`design`、`draw` 和 `sample`。`version` 是整数 2。`specimens` 是锁定总体，按原冻结顺序。模型、标签和预测沿用 V1 的行字段、标识、出处、概率编码及去重规则。总体每片恰有甲乙两条锁定预测。`sample` 按总体原序列出实抽片，且必须与按设计和抽签值复核所得名单一致。`labels` 恰好覆盖 `sample`。`design` 是有序数组，每项是 `[片号数组, 权重]`。权重、抽签值沿用 `[整数分子, 正整数分母]` 的有理数编码。抽次号沿用 V1 标识规则。

沿用 `analysis.intake.accept_handoff(conn, handoff_id, body_json)` 首纳和 `read_acceptance(conn, acceptance_id)` 读回。V2 在分析库独立核验候选覆盖、抽签和实际名单，以及标签、预测、模型与片号的对应。JSON 对象键序、预测及标签行序、候选名单的内部片序、等价分数写法均可规范化。候选名单之间的次序参与同号比较。等价重投沿用首纳号，有效同号异文或非法正文拒收。中心使用可信台端交来的正文，不需要连接台端库。

## 离线报告

调用 `archive.issue.issue_sample_report(conn, acceptance_id)`，选择指定的 V2 首纳正文。以甲损失减乙损失为方向。对实抽片 `i`，用 V1 的 Brier 分差 `d_i` 和设计给出的入样率 `π_i`，将 `d_i / π_i` 求和后除以锁定总体片数，得到总体平均分差的点估计。用精确有理数计算。

方差资格检查遍及锁定总体的所有异片对。任一对在设计中从不同时入样，报告记 `joint_gap` 并保留点估计，方差值及方法为空。全部异片对有联合入样机会时记 `estimable`，出具精确设计无偏方差估计。实现须固定一种口径，给出方法名并在仓库注明可复核规则。对任意固定的总体二元标签，按设计概率对各可能实抽结果的方差估计求期望，须等于点估计的设计方差。实际签发只能使用首纳证据中的已测标签。可估的方差值保留有符号原值及精确零。

签发返回带 `report_id`、`body` 和 `receipt` 的对象。正文带 `acceptance_id`、`handoff_id`、`trial_id`、`freeze_id`、`alpha_model`、`beta_model`、`draw_id`、`population_count`、`sample`、`pairs`、`mean_difference`、`variance_status`、`variance_method` 和 `variance_estimate`。`pairs` 为实抽片原序的 V1 `ReportPair` 元组。两项数值用 `Fraction`，空方差用 `None`，有方差时方法为非空文本。复核所需的原预测值与抽样设计可循固定接纳号查首纳正文。

正文和回执同一次事务提交。签存失败传播原异常，回滚本次新增，可凭原接纳号重试。沿用 `archive.readback.read_report(conn, report_id)` 领取固定值及回执。V1 签发仍收 V1 交接号，V2 签发收 V2 接纳号。同一签发入口收到已存在但版本不符的材料报 `ValueError`。非法、错配及同号冲突报 `ValueError`，对应入口的有效格式未知号报 `KeyError`。保留 V1 接口、交接编码、历史报告、运行入口及完整原回归。

## 方差估计口径

`issue_sample_report` 在 `estimable` 时固定使用 Horvitz–Thompson 方差估计，`variance_method`
记为 `horvitz-thompson`。记锁定总体片数为 `N`，片 `i` 的一阶入样率 `π_i` 为含 `i` 的候选
名单权重之和，异片对 `(i, j)` 的联合入样率 `π_ij` 为同时含 `i`、`j` 的候选名单权重之和，
并取 `π_ii = π_i`。对实抽 `S`：

```
v = (1/N²) · Σ_{i∈S} Σ_{j∈S} [(π_ij − π_i·π_j) / π_ij] · (d_i·d_j) / (π_i·π_j)
```

可复核规则：对任意固定的总体分差向量 `d`（由固定二元标签与锁定预测决定），按设计概率对
各可能实抽结果求 `E[v]`，必等于点估计 `T = (1/N)·Σ_{i∈S} d_i/π_i` 的设计方差
`Var(T) = E[T²] − E[T]²`。`tests/test_sampling.py` 的 `TestVarianceUnbiasedness` 对
`fixtures/sampling.json` 中每个联合覆盖完整的设计枚举全部二元标签格局，用精确有理数核验
该恒等式。估计值可正可负，签发保留符号原值与精确零；任一异片对 `π_ij = 0` 时记
`joint_gap`，方差值与方法为空。

## 样例与运行

`fixtures/sampling.json` 为可手算的调用数据。`models` 按甲乙排列。给每个样例的 `paired_at_lock` 录入 `base_predictions`，省略该字段时对冻结清册中的全部片录入。`after_lock` 中每项为 `[片号, 模型号, 概率]`，在锁定后按项录入，随后录入 `labels`。给预测与标签登记非空且可区分的出处。`expected` 列出关键报告值。省略方差预期表示由实现所声明的方法决定。`all_pairs` 与 `whole_blocks` 的实抽和边际率相同，只有前者有完整联合覆盖。`unequal_pairs` 锁定后更正 X 并为 W 补齐预测，仍按三片总体签出 `4/9`。`census_draw` 的抽签结果唯一，方差估计为精确零。

沿现有 `.pointwise-runtime.json` 运行。清单版本为 `1`，`kind` 为 `cli`，`setup` 是准备命令的 argv 列表，`verify` 与 `demo` 各是一条 argv。让核验命令执行原回归和新增抽检回归，业务断言失败须非零退出。演示须保留 V1，另展示关实验台库后用实收件签发、同实抽异资格以及重开分析库按原号领取。
