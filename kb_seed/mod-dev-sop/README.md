# Teamfight Manager 2 Mod 开发 SOP 总索引

本目录是基于官方 Mod 开发指南仓库
[`TeamfightManager2Mod`](../TeamfightManager2Mod/)
（`https://github.com/teamsamoyed/TeamfightManager2Mod.git`）整理的**外置标准作业流程（SOP）**。
每份 SOP 对应开发流程中的一个独立步骤，可按需单独查阅。

> 信息来源以官方文档为准；本 SOP 是流程化整理，不替代官方参考。
> 文中「源文档」链接均指向本仓库内的 `TeamfightManager2Mod/docs/` 原文。

---

## 一、开发流程总览

Mod 开发有一条主线和若干内容分支。**所有 Mod 都走主线**；内容分支按你要做的东西选择，
其中「数据英雄」是官方推荐的新手起点。

```text
┌─────────────────────────────────────────────────────────────────┐
│ 主线（所有 Mod 必经）                                             │
│                                                                   │
│  00 环境准备 ──► 01 创建 Mod 骨架 ──► [制作内容] ──► 10 测试排错   │
│                                                  │                │
│                                                  ▼                │
│                                            11 创意工坊发布         │
│                                                  │                │
│                                                  ▼                │
│                                            12 维护与迁移           │
└─────────────────────────────────────────────────────────────────┘

制作内容（分支，可组合）：
  02 数据英雄（.data_champion，推荐第一步，无需 SDK）
  03 文本与 i18n（名字、技能描述、多语言）
  04 图标与精灵图（PNG / 精灵表 / Aseprite 动画）
  05 改版现有英雄（同 id 替换机制）
  06 资产覆盖与合并（mod.override_info）
  07 Ban/Pick 立绘包（纯表现型 mod）
  08 原生 Rust Mod（Stable API，需要写代码时）
  09 Mod 存档数据（随存档保存的自定义数据，配合 08 使用）
```

两条典型路线：

- **纯数据/美术 Mod**（不需要编程）：`00 → 01 → 02/03/04/05/06/07 → 10 → 11 → 12`
- **原生代码 Mod**（JSON 表达不了逻辑时）：`00 → 01 → 08 (+02~06 配套资产) → 09 → 10 → 11 → 12`

判断标准：**优先用数据（JSON + 图片）实现；只有 JSON 效果不够用时才进入原生 Rust Mod**
（源文档：[README · Recommended First Steps](../TeamfightManager2Mod/README.md)）。

---

## 二、SOP 清单

| 编号 | SOP | 做什么 | 前置 | 源文档 |
|---|---|---|---|---|
| [00](00-环境准备.md) | 环境准备 | 定位游戏目录/版本、解包基础资源做参考、（可选）装 Rust 工具链与 SDK | 无 | getting-started / workshop-upload / stable-native-mods |
| [01](01-创建Mod骨架.md) | 创建 Mod 骨架 | 建 `mods/<mod_id>/` 文件夹、写 `mod.mod_info`、声明版本依赖 | 00 | getting-started / mod-package |
| [02](02-数据英雄.md) | 数据英雄制作 | 写 `.data_champion` JSON：新英雄或改版英雄的数值与技能 | 01 | data-champion / data-champion-schema/* |
| [03](03-文本与i18n.md) | 文本与本地化 | i18n 文件、合并进基础文本表、富文本标记 | 02 | asset-overrides-and-i18n |
| [04](04-图标与精灵图.md) | 图标与精灵图 | 静态 PNG、精灵表、Aseprite 动画、`#sheet/#anim/#data` 机制 | 02 | assets-and-sprite-sheets |
| [05](05-改版现有英雄.md) | 改版现有英雄 | 用基础英雄 id 替换其玩法（数据或原生两种方式） | 02（或 08） | override-existing-champions |
| [06](06-资产覆盖与合并.md) | 资产覆盖与合并 | `mod.override_info` 的 merge / override 规则 | 01 | asset-overrides-and-i18n |
| [07](07-BanPick立绘包.md) | Ban/Pick 立绘包 | 纯表现型大图立绘包（不动对战精灵） | 01 | banpick-illustration-packs |
| [08](08-原生Stable-Mod开发.md) | 原生 Rust Mod 开发（Stable API） | 搭建/构建/安装原生 mod，注册英雄、物品、钩子、UI、服务器逻辑 | 00（Rust 工具链） | stable-native-mods / stable-api-reference |
| [09](09-Mod存档数据.md) | Mod 存档数据 | 在游戏存档中读写自定义数据（含多人规则与迁移） | 08 | mod-save-data / stable-api-reference |
| [10](10-测试与故障排查.md) | 测试与故障排查 | 启用/重启/读诊断弹窗的标准循环 + 全量排错清单 | 任意 | troubleshooting |
| [11](11-创意工坊发布.md) | 创意工坊发布与更新 | TFM2ModUploader 发布/更新、数据库包、原生构建上传 | 10 验证通过 | workshop-upload |
| [12](12-维护与版本迁移.md) | 维护与版本迁移 | 游戏更新后的检查、classic → stable 迁移 | 11 | stable-native-mods / native-rust-mods |

---

## 三、贯穿全程的核心概念

这些概念在多份 SOP 中反复出现，先在此建立统一认识。

### 1. Mod 就是一个文件夹，文件夹名就是 mod id

- Mod 放在游戏 `mods/` 目录下：`mods/<mod_id>/`。
- `mod.mod_info` 是唯一必需文件，其他文件按需添加。
- **id 一旦发布就不要改**：存档、ban/pick 数据、平衡补丁都会引用它。

### 2. 资产路径（asset path）规则

Mod 内的文件按「去掉扩展名」的虚拟路径引用：

```text
mods/my_mod/icons/eagle_skill.png  →  asset/my_mod/icons/eagle_skill
mods/my_mod/text/champion.i18n     →  asset/my_mod/text/champion
```

- 写资产路径时**永远不带扩展名**。
- Aseprite / 精灵表会派生 `#sheet`（图集）、`#anim`（动画数据）、`#data`（静图标签）
  等子资产，详见 [SOP 04](04-图标与精灵图.md)。

### 3. `base` 依赖 = 游戏版本要求

`mod.mod_info` 中的 `{"mod_id": "base", "version": ">=0.4.14"}` 声明的是**兼容的游戏版本**，
不是依赖基础资源文件。玩家可在标题画面右下角查看当前游戏版本。
版本语义：Early Access 阶段 major 为 `0`；minor 变更代表功能/内容/格式变化；patch 仅为修复。

### 4. 时间单位是 tick，60 tick = 1 秒

所有 `duration` / `cooltime` / `tick` 类字段都是**模拟 tick**（60/s），不是毫秒。
例如 `cooltime: 240` = 4 秒冷却。

### 5. 诊断弹窗（Diagnostics Popup）是第一排错入口

Mod 加载失败时，游戏在标题画面弹出诊断信息，通常直接指出问题文件。
任何「没生效」先看它：详见 [SOP 10](10-测试与故障排查.md)。

### 6. 原生 Mod 有两条路径，只推荐一条

| 路径 | 状态 | 说明 |
|---|---|---|
| **Stable API**（`mod-api-stable`） | ✅ 推荐 | 一次构建跨游戏更新可用；Win/macOS/Linux；任意 Rust 工具链 |
| Classic（`mod-api`） | ⚠️ 已弃用 | 仅支持到游戏 0.5；0.6 起不再随游戏分发；Win 专用且每次更新需重编译 |

新项目一律用 Stable API；存量 classic mod 参见 [SOP 12](12-维护与版本迁移.md) 迁移。

### 7. 数值量级参考

游戏内数值是真实比例（世界坐标为 u64 定点整数，默认地图 960000×960000）：
`move_speed` 约 900+、基础攻击 `range` 约 60000、近战 range 约 12000。
写新内容时**从基础英雄数据复制量级**，不要凭空编数（可用 SOP 00 的解包功能查看基础数据）。

---

## 四、术语表

| 术语 | 含义 |
|---|---|
| mod id | Mod 文件夹名，也是 DLL 注册 id、存档命名空间的依据 |
| asset path | 去扩展名的虚拟资源路径，`asset/<mod_id>/...` |
| `.data_champion` | 数据英雄 JSON 文件 |
| `mod.mod_info` | Mod 元数据（名称/作者/版本/依赖） |
| `mod.override_info` | 资产合并/覆盖规则 |
| `database_pack.info` | 数据库共享包（Workshop 分发用，非游戏内 mod）的元数据 |
| i18n 文件 | 按语言分组的文本 JSON（`.i18n`） |
| tick | 模拟时间单位，60 tick/秒 |
| Stable API | 推荐的原生 mod 接口（`mod-api-stable` crate） |
| TFM2ModUploader | 游戏自带的创意工坊上传器，兼有解包基础资源功能 |
| Mod SDK | 原生编译所需 SDK；stable 版随游戏全平台分发（`mod-sdk-stable/`） |
| 改版（rework） | 用基础英雄同 id 覆盖其玩法的机制 |
| 数据库包 | 通过 Workshop 分享自定义数据库文件的打包格式 |
