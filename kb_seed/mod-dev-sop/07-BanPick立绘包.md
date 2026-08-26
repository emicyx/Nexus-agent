# SOP 07 · Ban/Pick 立绘包（Illustration Pack）

> 目标：替换 Ban/Pick 大图展示，而不动对战精灵、英雄池网格与小 ban 图标。
> 特点：可选的纯数据 mod；本地与创意工坊皆可；**不需要** SDK / DLL / `mod.override_info`。
> 前置：SOP 01。
> 源文档：[banpick-illustration-packs](../TeamfightManager2Mod/docs/banpick-illustration-packs.md)

---

## 1. 文件夹结构

```text
mods/my_portrait_pack/
  mod.mod_info
  banpick_illustrations/
    fighter.png        ← 文件名 = 英雄 id（扩展名会被去掉）
    knight.png
    pyromancer.png
```

`fighter.png` 映射到英雄 id `fighter`。不支持子目录和变体——一份命名契约就是全部。

## 2. `mod.mod_info`（注意 `mod_type`）

```json
{
  "name": "My Ban/Pick Portrait Pack",
  "author": "Author Name",
  "version": "1.0.0",
  "mod_type": "banpick_illustration",
  "dependencies": [
    { "mod_id": "base", "version": ">=0.5.2" }
  ]
}
```

`mod_type: "banpick_illustration"` 声明**纯表现型**包。游戏会在认定其纯表现之前做校验：
包含可执行代码、`mod.override_info`、英雄数据、设置、UI 布局或其他功能性资产的包
仍是普通会话 mod（立绘仍生效，但多人/存档兼容规则按普通 mod 走）。

## 3. 图片规则

| 项 | 要求 |
|---|---|
| 格式 | sRGB PNG |
| 推荐尺寸 | **512 × 640** 像素 |
| 硬上限 | 任一边 ≤ 1024 像素（超限图片在校验后被忽略并移出资产表） |
| 构图 | 脸部与关键剪影居中（游戏会**居中裁切**适配各展示面） |
| 命名 | 精确 `<champion_id>.png` |

生效位置：

- ✅ 大型选/禁确认展示（pick/ban confirmation showcase）
- ✅ 飞向确认选位的立绘
- ✅ 确认后的两侧选位
- ❌ 英雄池网格、紧凑 ban 位、对战精灵、战绩、tooltip 等继续用基础资产

图片缺失/损坏/超限时，UI 渲染当前基础像素精灵——**没有**单独的立绘兜底图。

**内存提示**：资产管线在启动时解码全部 PNG。发布全花名册立绘时坚持 512×640，
不要发布无谓的大图。

## 4. 优先级与多人规则

- 启用的 mod 按解析出的 mod 顺序处理；多个包提供同一英雄 id 时**后处理者胜**。
- 显式 `mod.override_info` 重映射在自动立绘映射**之后**应用，保持最终优先级。
- 通过校验的纯表现立绘包**不参与**玩法/存档/大厅的 mod 签名：
  同一多人会话中各玩家可使用不同立绘包；包本身不增加任何存档字段、网络包、模拟量。

## 5. 操作步骤

1. 按 SOP 01 建 `mods/my_portrait_pack/`，写含 `mod_type` 的 `mod.mod_info`。
2. 逐个英雄放置 `banpick_illustrations/<champion_id>.png`（512×640，居中构图）。
3. 重启游戏，进 Ban/Pick 流程查看大图展示位。
4. 验证纯表现判定：确认包内没有任何功能性文件。
5. 发布：走 [SOP 11](11-创意工坊发布.md)（普通 mod 上传流程）。

## 6. 原生 SDK 辅助（可选参考）

立绘包不需要原生代码。原生 mod 作者若需相同的稳定路径与裁切逻辑，
`mod_api` 重新导出了：
`BANPICK_ILLUSTRATION_DIRECTORY`、`BANPICK_ILLUSTRATION_VIRTUAL_DIRECTORY`、
`BANPICK_ILLUSTRATION_MAX_DIMENSION`、`banpick_illustration_asset_path(...)`、
`banpick_illustration_source_asset_path(...)`、`banpick_illustration_cover_rect(...)`、
`resolve_banpick_illustration(...)`。
这些辅助不改变 `ModRegistration` 或原生 `API_VERSION`，存量原生 mod 无需为此重编译。

## 7. 排错速查

| 症状 | 排查 |
|---|---|
| 某英雄立绘不生效 | 文件名与英雄 id 不完全一致；图片超过 1024 边长；PNG 损坏 |
| 包被当成普通 mod | 包内混入了功能性资产（代码 / override_info / 英雄数据等） |
| 想覆盖的图不变化 | 该位置不在生效范围内（英雄池网格/小图标不归立绘包管）；考虑 `mod.override_info`（SOP 06） |
