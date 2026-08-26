# SOP 01 · 创建 Mod 骨架

> 目标：建立 `mods/<mod_id>/` 文件夹并写入 `mod.mod_info`，让 mod 出现在游戏 Mods 菜单。
> 前置：SOP 00（已知游戏版本与 mods 目录）。
> 源文档：[getting-started](../TeamfightManager2Mod/docs/getting-started.md) ·
> [mod-package](../TeamfightManager2Mod/docs/mod-package.md)

---

## 1. 确定 mod id（文件夹名）

mod id = 文件夹名 = 后续一切的标识（DLL 注册 id、存档命名空间、资产路径前缀）。

**命名规则：**

- 小写 ASCII：`my_mod`、`new_champions`、`balance_pack`。
- 起一个**稳定**的名字：发布后不要改，存档、补丁、ban/pick 数据都会引用内容 id。
- 避免与基础游戏 id 及其他 mod 撞名；**唯一例外**是有意改版基础英雄时使用其精确 id（见 SOP 05）。
- 新内容 id 建议带 mod id 前缀便于阅读，如 `my_mod_fire_mage`。

## 2. 创建文件夹与 `mod.mod_info`

```text
mods/my_mod/
  mod.mod_info
```

`mod.mod_info` 是 mod 出现在 Mods 菜单的**唯一必需文件**。示例：

```json
{
  "name": "My Mod",
  "author": "Your Name",
  "version": "0.1.0",
  "description": "Adds a new champion.",
  "last_updated": "2026-05-14",
  "dependencies": [
    {
      "mod_id": "base",
      "version": ">=0.5.4"
    }
  ]
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `name` | Mods 菜单中显示的名称 |
| `author` | 作者名 |
| `version` | mod 版本，`0.1.0` 形式（语义化版本） |
| `description` | 给玩家看的简短描述 |
| `last_updated` | 日期或简短更新说明 |
| `dependencies` | 版本/依赖要求，不满足时 mod 被禁用并显示诊断信息 |

### `base` 依赖 = 游戏版本要求（重要）

`base` **不是**「依赖基础资源文件」，而是声明兼容的游戏版本：

- `major`：Early Access 期间为 `0`，正式版线为 `1`。
- `minor`：功能、内容、数据格式或兼容性行为变化时 +1。
- `patch`：仅修复性更新。

绝大多数 mod 都应写 `base` 依赖，玩家在过旧/过新的游戏版本上加载时会得到明确诊断。
改版基础英雄的 mod 应把 `base` 指向所依赖的英雄数据/SDK 行为所在的版本，
例如同 id JSON 改版或 `replace_champion` 需 `">=0.4.14"`。

### 普通 mod 依赖

`dependencies` 也可声明其他 mod（也决定加载顺序）：

```json
{ "mod_id": "service_provider", "version": ">=1.0.0, <2.0.0" }
```

- 玩家启用 mod 时，已安装的依赖会被自动一并启用并**先加载**。
- 依赖缺失或版本不匹配 → 本 mod 被禁用 + 诊断信息。
- 原生 mod 消费其他 mod 的运行时服务时，必须在此声明提供者。

## 3. 骨架目录结构（按需填充）

一个较完整的 mod 布局示例：

```text
mods/example/
  mod.mod_info            ← 必需
  mod.override_info       ← 资产合并/覆盖规则（SOP 06）
  thumbnail.png           ← Mods 菜单缩略图 / Workshop 预览后备
  preview.png             ← Workshop 预览图（可另用 preview.jpg）
  example.dll             ← 原生模块（Win；Linux 为 .so，macOS 为 .dylib）
  champion/
    eagle.data_champion   ← 数据英雄（SOP 02）
  text/
    champion.i18n         ← 文本（SOP 03）
  icons/
    eagle_skill.png       ← 图标（SOP 04）
  sprite/
    eagle.aseprite
    spell_icons#sheet.png       ← 手动精灵表
    spell_icons#data.sprite_sheet
  ui/
    layout/test_popup.ui  ← UI 布局
```

### 可选文件速查

| 文件 | 作用 |
|---|---|
| `thumbnail.png` | Mods 菜单图；Workshop 预览后备（也接受 `thumbnail.jpg`） |
| `preview.png` / `preview.jpg` | Workshop 专属预览图 |
| `mod.override_info` | merge/override 资产规则 |
| `<mod_id>.dll` / `.so` / `.dylib` | 原生模块，每平台一个；支持哪些平台完全由这些文件推导，无需声明 |
| `mod.workshop_id` | 上传器首次上传后生成，**保留**（更新同一物品的凭据），见 SOP 11 |
| `database_pack.workshop_id` | 数据库包的同类凭据 |

注意：**原生 mod 的注册 id 必须等于文件夹名**，防止从错误的 mod 文件夹加载模块。

## 4. 验证骨架

1. 启动（或重启）游戏。
2. 标题画面 → Mods 菜单：应能看到 `My Mod`（还不能生效，因为没有内容）。
3. 若菜单没有它或弹出诊断 → 按 [SOP 10](10-测试与故障排查.md) 排查
   （常见：`mod.mod_info` 不是合法 JSON、`version` 格式不对、没重启游戏）。

## 5. 下一步

| 你要做的东西 | 去哪份 SOP |
|---|---|
| 新英雄（推荐第一步） | [SOP 02 · 数据英雄](02-数据英雄.md) |
| 只改文本/翻译 | [SOP 03](03-文本与i18n.md) + [SOP 06](06-资产覆盖与合并.md) |
| 图标、精灵、动画 | [SOP 04](04-图标与精灵图.md) |
| 改版基础英雄 | [SOP 05](05-改版现有英雄.md) |
| Ban/Pick 立绘 | [SOP 07](07-BanPick立绘包.md) |
| 需要写代码的逻辑 | [SOP 08 · 原生 Stable Mod](08-原生Stable-Mod开发.md) |

## 6. 常见错误

| 症状 | 原因 |
|---|---|
| Mods 菜单不显示 | 文件夹不在 `mods/` 下；缺 `mod.mod_info`；JSON 非法；未重启游戏 |
| 显示但无法启用 | `base` 版本要求与当前游戏不符；依赖缺失或版本不匹配（看诊断弹窗） |
| 游戏自动禁用 mod | `mod.mod_info` / `mod.override_info` JSON 非法；原生 DLL 加载失败或注册 id 与文件夹名不一致 |
