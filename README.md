# 岚桥材料实验室双模型评估台账 V1

工装工位交付有序试片清册与两个模型。操作员录入和更正预测，实验员登记最终判读，分析员在实验台关库后比较两模型在共同试片上的误差，次班质控员按报告号复核分母和出处。

## 一轮评估

1. 用 `sqlite3.connect` 打开实验台库，调用 `bench.initialize_source(conn)`。`trial.freeze.freeze_trial(conn, trial_id, specimens, alpha_model, beta_model)` 保存有序清册，返回带 `freeze_id` 的冻结。同一试验可多次冻结，原件用 `trial.freeze.read_frozen_trial` 领取。
2. `predictions.intake.record_prediction(conn, freeze_id, specimen_id, model_id, probability, source)` 返回带 `revision_id` 的预测修订。概率接受 `Fraction`、整数和可由 `Fraction` 精确解析的字符串，范围包含 0 和 1。
3. 用相同冻结号、片号和模型提交更正。`predictions.history.get_current_prediction` 查有效修订，`get_prediction_revision` 按原号查历史。空槽查询返回 `None`。
4. `bench.labels.record_label(conn, freeze_id, specimen_id, value, source)` 录入整数 0 或 1，返回固定标签。值和出处相同的重投复用原号，任一不同均报错。
5. `bench.handoff.issue_handoff(conn, freeze_id)` 在每片标签齐全后保存发行时的当前预测和全部标签，返回 `IssuedHandoff`。每次成功发行有独立 `handoff_id`。`bench.handoff.read_issued_handoff` 按号领取固定正文。
6. 打开独立分析库，调用 `analysis.initialize_analysis(conn)`。`analysis.intake.accept_handoff(conn, handoff_id, body_json)` 接收交接号和 JSON 文本，返回接纳号。`analysis.intake.read_acceptance` 按接纳号领取首纳规范正文和原交接号。对象键序、标签及预测数组的独立重排和等价分数写法均可规范化。片序、模型方向、值、出处和记录号均参与同号比较。
7. `archive.issue.issue_report(conn, handoff_id)` 按已接纳交接号签报。分析端遍历原清册，只有同片同时具备甲乙概率及标签才入评。单片损失为 `(p - y) ** 2`，分差为甲损失减乙损失的精确均值。正值表示乙误差较低，负值表示甲误差较低。
8. 签发返回 `report_id`、正文和同次回执。正文列出有序入评片、标签及其出处、两侧预测修订号及出处、配对数和 `Fraction` 分差。空配对签出 `0` 和 `None`，有配对且分差恰为零保留 `Fraction(0)`。
9. 重开分析库，用 `archive.readback.read_report(conn, report_id)` 领取固定正文及回执。复核时可凭接纳号查首纳预测值与标签。

两个库使用普通 SQLite 连接，连接由调用方关闭。初始化可重复运行，保留原有数据。写入口管理自己的事务，调用时连接须处于无活动事务状态。身份和出处为非空白的 UTF-8 文本，原字面值保留。清册片号互异，甲乙模型不同。非法录入、标签未齐、无效交接和同号异文报 `ValueError`。有效格式的未知冻结号、修订号、交接号、接纳号和报告号在对应查询入口报 `KeyError`。签存失败传播原异常，正文与回执一起回滚，可从原接纳号定位交接并重试。

V1 交接 JSON 包含版本 1、试验、冻结号、有序清册、甲乙模型、完整标签和占用的预测槽。预测概率使用 `[整数分子, 正分母]`。未出现的预测槽为空。来源记录、发行和签报分别保存其固定引用。示例用 X 的甲乙预测 1/4 和 3/4、标签 1，以及只有甲预测的 Y，得到一对，甲减乙为 1/2。

## 运行

使用 Python 3.11 标准库。平台物化后的正常回归入口为

```sh
python3 -B check_project.py
```

从录入、更正到离线签发和重开领取的演示为

```sh
python3 -B demo.py
```

演示使用临时工作库，输出用于核对分母、分差、出处与重领结果的 `POINTWISE_SCENARIO` 记录。
