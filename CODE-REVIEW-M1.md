# 代码审查报告 — PixelBench M1

审查对象：`bench_server.py`（541→573 行）、`bench.html`（467→497 行）、`asset_tool.py`、`spec.json`、测试脚本
方法：安全专项审查 + 逻辑正确性审查 + 前端专项审查（三路独立，之后逐条复核）
结论提交：`eeb330e`

## 总览

| 级别 | 数量 | 状态 |
|---|---|---|
| Blocker | 1 | 已修 + 已验证 |
| Major | 11 | 已修 + 已验证 |
| Minor / Nit | 9 | 记录，未全修（见末节） |

骨架比预期扎实：`const` 闭包的循环变量陷阱不存在、轮询有双重 catch 不会死、动画 tab 用 `textContent` 安全、localStorage 恢复逻辑正确。问题集中在**采纳链路**和**未转义/未编码的外部字符串**。

## Blocker

**B1｜采纳按钮提交整个候选数组**（`bench.html` adopt 处理器）

`body: JSON.stringify({ candidate: cand })` 里的 `cand` 是该帧**全部候选的数组**，不是用户点中的那张。服务端 `os.path.join(..., cand)` 收到 list 直接 `TypeError` → 500。这是工作台主流程（点候选=采纳），完全不可用。

根因：`candFile` 参数只写进了 `state.selCand`，POST 时没用它。

修：提交 `candFile`；按钮仅在该候选存在时渲染；点击即 disable 防重复提交。

**为什么之前没发现**：`ui_check.mjs` 只测到"候选出现"就结束了，没点候选。已补真实点击测试。

## Major

| # | 位置 | 问题 | 修法 |
|---|---|---|---|
| M1 | `bench.html` 三处切换点 | `selCand` 从不清空，切动画/切帧后点帧体会带着旧候选值发出，造成 404 或**跨动画错引** | 项目/实体/动画三处重置 + 帧体点击不再复用陈旧值 |
| M2 | `selectFrame` / `addFrame` | `st.frames[state.anim]` 在 `status_data` 为 null 时抛 TypeError，async 处理器无 catch → 静默失败 | 可选链 + 提前 return |
| M3 | `api()` | fetch 无超时，一次挂死请求让轮询**永久停摆**（catch 静默，用户无感知） | `AbortSignal.timeout(8000)` |
| M5–M9 | `bench.html` 共 9 处 | 项目名/实体名/参考图名/候选名/校验报告文本**裸插 innerHTML**；校验报告文本由服务端生成、可能内嵌路径，是最危险注入面 | 新增 `esc()`，全部包住 |
| M10 | `FB()` | 裸拼相对路径，文件名含 `/` 会变子路径、含 `?` 截断 query | 逐段 `encodeURIComponent` |
| M11 | 底部 4 个按钮 | 未选实体时 `EB()` 拼出 `/entities/null` → 404 → 静默失败 | `needEntity()` 守卫 |
| M4 | 服务端 4 处 | 路径穿越：`files/` 静态路由、refs 上传、adopt、参考图路径都是 `os.path.join` 裸拼 | 新增 `safe_join()`，统一校验不逃出 base |
| M5s | `generate` 路由 | `int(seed)`/`int(steps)` 遇非法输入抛 500；`anim` 不校验存在性 | 白名单 + 类型校验，非法返回 400 |
| M6s | `save_tasks` | 非原子写，并发下可产生半截 JSON；下次启动 `json.load` 直接抛异常**让服务起不来** | 临时文件 + `os.replace` + 锁；加载失败降级为空表 |
| M7s | `export` 路由 | 归档内路径未校验 | 显式拒绝 `..`/绝对路径（防 zip-slip） |
| M8s | `spec` 更新 | 动画名不校验，可注入非法目录名 | 正则 `[A-Za-z_][A-Za-z0-9_]*` |

## 修复过程中新发现并修掉的问题

**M12｜typing 守卫过宽导致轮询永久停摆**（我按 M3 建议改动时引入的回归，浏览器实测抓到）

把守卫改成"焦点在 `#fparam` 内即暂停"后，点一次「生成」按钮→按钮获得焦点→被判定为"正在输入"→轮询**永远不再重渲染**，候选生成了也不显示。

修：守卫需同时满足"在 `#fparam` 内"**且**"是 INPUT/TEXTAREA/SELECT"；再加"任务结束后 4 秒内强制刷新"兜底（避免 `hasActive` 刚翻转那一轮漏掉结果）。

教训：静态审查给出的"更严格"建议可能引入功能回归 —— 所以每条修复都必须过真实浏览器验证，不能只靠单测。

## 未修的 Minor / Nit（记录在案）

- 缩略图 `?t=Date.now()` 每轮都变，有活跃任务时整批重下（量小，暂留）
- 点候选即采纳、无确认（有意设计，建议后续加 300ms 可撤销 toast）
- `selectProject` / `selectEntity(null)` 面板复位代码重复两份，可抽 `resetPanels()`
- boot 阶段连拉 3 次 `/api/projects`
- 服务端已有能力的**脚底漂移检查**（`foot_anchor_tolerance_px=6`）、**统一身高 512**、**任务自动找回**仍未实现（M1 已知缺口，README 已如实标注）

## 验证证据

```
test_review_fixes.py   15/15 PASS   （路径穿越×3、类型校验×4、采纳链路×3、S5/S6/S7、原子写）
e2e_test.py            PASS         （原全流程无回归）
浏览器实测              点候选=采纳 → 帧数 1→2、idle_000.png 落盘、零 console 错误、零 4xx
```

## 建议下一步

1. 补 `ui_check.mjs`：把"点候选采纳"纳入常规 UI 冒烟（本次 blocker 就是这样漏掉的）
2. 实现 S7 脚底漂移检查（阈值已在 spec，只差代码）
3. 真实模型出图后验证帧间一致性 —— 这才是 S5–S7 阈值是否合理的唯一判据
