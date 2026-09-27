# PixelBench

**AI 2D 游戏资产工作台。** 用本地 Qwen-Image 2.1（ComfyUI）驱动 2D 角色动画资产的完整管线：

```
建项目 → 建实体 → 传参考图 → 逐帧生成(3 候选) → 审阅采纳 → S5 归一化 → S6 打包 → S7 校验 → 导出 zip
```

> **独立工具，不隶属任何单个游戏。** 工作台里的"项目"对应一个游戏——《永蚀 / Ever Eclipse》只是项目列表里的一项，任何 2D 项目都可以新增（建项目 → 传风格参考 → 建实体即可开始）。

## 它解决什么

AI 出图快，但**出一套能用的动画资产**很难，卡在四件事：

| 痛点 | PixelBench 的做法 |
|---|---|
| 单张好看，**帧间不一致** | 参考图锚定：风格圣经 + 基础精灵一起进 prompt，约束同一角色 |
| 脚底/身高**飘** | S5 归一化：内容底部对齐 `ground_y`，统一 1024 透明画布（统一身高待补） |
| 生成图**不能直接进引擎** | S6 打包：统一画布 sprite sheet + `index.json`（含每帧 cell 坐标） |
| 出错**靠肉眼查** | S7 校验：不透明占比、脚底锚点、剪影 IoU，报告可点定位到具体帧 |

## 快速开始

依赖：

- Python 3.11+（本机开发于 3.13）。**服务端零第三方 web 依赖**（stdlib `ThreadingHTTPServer`）；S5–S7 图像处理需要 Pillow + numpy
- 本地 ComfyUI（默认 `http://127.0.0.1:8188`），加载 Qwen-Image 2.1 三件套模型
  - 不装 ComfyUI 也能跑：UI、S5–S7、导出照常可用，只有"生成"需要它（或 mock）

启动：

```powershell
python bench_server.py        # http://127.0.0.1:8321
```

环境变量：

| 变量 | 默认 | 作用 |
|---|---|---|
| `PORT` | `8321` | 服务端口 |
| `PIXELBENCH_MOCK` | — | `=1` 时 mock 生成（画合成帧，不需要 GPU），用于验证全流程 |
| `PIXELBENCH_COMFY` | `http://127.0.0.1:8188` | ComfyUI 地址 |

浏览器打开 http://127.0.0.1:8321 ：

1. 建**项目**（一个游戏，如 `ever_eclipse`）
2. 建**实体**（如 `knight`；默认动画 `idle / walk / atkA`、12fps，建完可改）
3. 上传**参考图**（`style_*.png` 风格圣经、`base_*.png` 基础精灵）
4. 点帧槽位 → 写 prompt → 勾选参考图 → **生成**（每帧产出 3 张候选，超出自动清旧）
5. 点候选缩略图**采纳** → 依次 **S5 归一化 / S6 打包 / S7 校验** → **导出 zip**

ComfyUI 离线时顶部横幅提示，其余功能照常。

## 产物结构

```
workspaces/
├── tasks.json                 任务表（落盘；重启后未完成任务变 recovered，自动找回待补）
└── <project>/                 一个项目 = 一个游戏
    ├── project.json           项目元信息（项目名/游戏名/备注/风格指针）
    └── <entity>/
        ├── spec.json          实体规格（动画列表/fps/备注）
        ├── refs/              风格圣经、基础精灵、其他参考
        ├── candidates/<anim>/<i>/   候选（每槽最多留 3 张）
        ├── <anim>_NNN.png     采纳帧（扁平命名 = 隐式帧序，S5-S7 直接读这里）
        ├── normalized/        S5 产物
        ├── sheets/            S6 产物：<entity>_<anim>.png + <entity>_index.json
        └── validation.json    S7 报告
```

导出的 `<entity>_index.json` 是引擎接口：每个动画给 sheet 相对路径、帧数、fps 和逐帧 cell（x/y/w/h）。

## 命令行用法（不进 UI）

`asset_tool.py` 操作实体目录（内含 `<anim>_NNN.png` 采纳帧）：

```powershell
python asset_tool.py normalize <frame.png> [-o <out.png>]   # S5 单帧：内容底部对齐 ground_y + 1024 画布
python asset_tool.py pack <entity_dir> -o <out_dir>         # S6 sheet + index.json
python asset_tool.py validate <entity_dir>                  # S7 校验，打印 PASS/FAIL + 问题列表
python asset_tool.py report <entity_dir>                    # S7 完整 JSON 报告
```

## S5 / S6 / S7 实际检查什么

- **S5 归一化**：裁到内容包围盒 → 内容**底部**对齐 `ground_y=896` → 补到 1024×1024 透明画布。（脚底锚点检测用中心带 30%–70% 宽度找最底不透明像素；等比缩放到统一身高 512px **已在 spec 定义、待实现**）
- **S6 打包**：按 `sheet_max_w=4096` 从左到右排布（超宽换行），每动画一张 sheet + cell 级索引
- **S7 校验**（M1 实际检查项）：
  - 每帧不透明占比 ∈ [2%, 85%]（防全透明/糊满）
  - 每帧存在中心脚底锚点
  - 每帧剪影 vs 首帧 IoU ≥ 0.55（防角色漂移/换形）
  - 脚底**漂移**阈值（6px）已在 `spec.json` 定义，M1 尚未实现检查 —— 待补

## 规格（spec.json）

| 项 | 值 |
|---|---|
| 风格 | HD 厚涂（赛璐璐偏厚涂，高饱和硬边，参考死亡细胞） |
| 帧画布 | 1024 × 1024，透明背景 |
| 脚底基准线 | y = 896 |
| 统一身高 | 512 px（待实现） |
| 画布边距 | 24 px（待实现） |
| sheet | 行高 1024，最大宽 4096 |
| 命名 | 采纳帧 `<anim>_NNN.png`（隐式帧序）；sheet `<entity>_<anim>.png` |
| 校验阈值 | 不透明 2%–85%、剪影 IoU ≥ 0.55、脚底漂移 ≤ 6px（待实现） |

## 架构

```
浏览器（单页 vanilla JS，零构建）
   │ fetch /api/* + 1.2s 轮询
   ▼
Python stdlib ThreadingHTTPServer :8321
   ├─ /api/projects                  项目 CRUD（一个项目 = 一个游戏）
   ├─ /api/projects/<p>/entities/…   实体/参考图/帧槽位（generate/采纳/放弃）
   ├─ /api/tasks                     任务进度（落盘，重启可恢复）
   ├─ normalize | pack | validate    纯 CPU，ComfyUI 挂了也能用
   └─ export                         zip 流式下载
   ▼
ComfyUI 127.0.0.1:8188（唯一 GPU 依赖；纯 HTTP：/prompt /history /view /upload/image）
```

关键取舍：

- **前端零构建**：单 HTML + 原生 JS，改完刷新即生效
- **全部状态落盘**：workspace 目录就是数据库，刷新页面不丢任何东西
- **CPU/GPU 分离**：S5–S7 与 UI 永远可用，只有生成依赖 ComfyUI

## 测试

```powershell
# 先起服务（mock 即可）：  $env:PIXELBENCH_MOCK="1"; python bench_server.py
python e2e_test.py       # API 全流程 E2E：建项目→建实体→生成→采纳→S5→S6→S7→导出
node ui_check.mjs        # 浏览器 UI 冒烟（需 npm i -D playwright-core + 本机 Edge/Chrome）
```

## 文档

- [PRD-PixelBench.md](PRD-PixelBench.md) —— 需求背景、方案、帧槽位状态机、上线计划、Git/PR 约定

## 状态

**M1 已交付**：项目/实体/参考图/帧槽位/生成/采纳/S5/S6/S7/导出 全链路可用（mock E2E + 浏览器实测通过）。
待办：真实模型出图的帧间一致性验证（GPU 空闲时）；S7 脚底漂移检查实现。
