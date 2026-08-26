# SOP 08 · 原生 Rust Mod 开发（Stable API）

> 目标：用 Rust 编写原生 mod——JSON 表达不了的逻辑：自定义模拟、带回调的物品、
> UI 行为、服务器钩子、AI 钩子、自定义模式。
> 前置：SOP 00（Rust 工具链 + `mod-sdk-stable/`）；SOP 01（mod 骨架概念）。
> 源文档：[stable-native-mods](../TeamfightManager2Mod/docs/stable-native-mods.md) ·
> [stable-api-reference](../TeamfightManager2Mod/docs/stable-api-reference.md)
> （每个函数的完整签名/单位/示例以 reference 为准）

> ⚠️ 只用 Stable API 写新项目。classic（`mod-api`）路径已弃用：仅支持到游戏 0.5，
> 0.6 起不再随游戏分发。存量迁移见 [SOP 12](12-维护与版本迁移.md)。

---

## 1. Stable API 的四个核心优势

1. **一次构建，跨游戏更新可用**：旧模块跑在新游戏上、新 SDK 构建的模块也能跑在旧游戏上。
2. **任意 Rust 工具链**：stable/nightly 皆可，无钉死编译器、无 SDK 依赖下载。
3. **全平台**：Windows / macOS / Linux。
4. **更小、契约化的 API**：通过版本化接口调用游戏，而非直接触碰内部类型。

## 2. 第一个 Stable Mod（搭建 → 构建 → 安装）

### 2.1 建工程

1. 把 SDK 里的 `template/` 复制到任意位置并重命名（**文件夹名 = 模块文件名**）。
2. 保持 `mod-api-stable` 在模板旁边（模板以 `../mod-api-stable` 相对路径引用它）。

```text
任意目录/
  mod-api-stable/
  my_mod/            ← 复制自 template/
    Cargo.toml
    src/lib.rs
```

`Cargo.toml` 是普通库 crate，`crate-type = ["cdylib"]`，
依赖 `mod-api-stable`（path 依赖）。可自由添加 crates.io 依赖（如 `rand`）。

### 2.2 写入口

```rust
use mod_api_stable::{declare_stable_mod, LogLevel, StableHost, StableMod};

fn init(host: &StableHost) -> StableMod {
    host.log(LogLevel::Info, "hello from my stable mod");

    let mut decl = StableMod::new("my_mod_id");   // 必须等于 mod 文件夹名
    // 在这里注册内容（见第 5 节）
    decl
}

declare_stable_mod!(init);
```

### 2.3 构建与安装

```bash
cargo build --release
```

把产物从 `target/release/` 复制进 `mods/<my_mod_id>/`，**按平台重命名**：

| 构建平台 | cargo 产物 | 复制为 |
|---|---|---|
| Windows | `my_mod.dll` | `my_mod.dll` |
| Linux | `libmy_mod.so` | `my_mod.so`（去掉 `lib` 前缀） |
| macOS | `libmy_mod.dylib` | `my_mod.dylib` |

文件名必须等于 mod id。同目录再放一份 `mod.mod_info`（SOP 01）。
启用 mod → 重启游戏 → 验证（SOP 10）。

### 2.4 用上传器代替手工构建（可选）

`TFM2ModUploader` 检测到 mod 的 `Cargo.toml` 依赖 `mod-api-stable` 即识别为 stable mod：
用你的默认工具链构建、自动命名产物，且在 `mod-api-stable` 缺失时自动安装
（从上传器旁的 `mod-sdk-stable` 或 `TFM2_STABLE_SDK_DIR` 指向的位置查找）。详见 SOP 11。

## 3. 单位与全局约定（写代码前必读）

| 事物 | 值 |
|---|---|
| 模拟 tick | **60 tick/秒**；所有模拟时间都用它 |
| 世界坐标 | `u64` 定点整数；默认地图 960000×960000；英雄半径 10000、视野 130000 |
| 地图网格 | 墙/草丛/区域为 30×30 格（一格 32000 世界单位） |
| 屏幕坐标 | `f32`；`"UI"`/`"Default"` 渲染图为 1920×1080；`"Game"` 为对局世界空间 |
| 颜色 | `u32` 打包 `0xRRGGBBAA`（不透明白 = `0xffffffff`） |
| UI 节点路径 | 点分层选择器：`"body.popup.buttons.close"` |
| JSON 文档路径 | 点分字段路径，数字索引数组：`"towers.0.pos"`；**空路径 = 整份文档** |
| 文档写入 | 整份文档经游戏 schema 回环校验，非法写入**原子拒绝**返回 `false` |
| 数值量级 | `move_speed` ~900、`hp` ~900、`attack` ~80、普攻 range ~60000、近战 ~12000；从 `champion_brief`/英雄表复制量级 |

## 4. 三条铁律

1. **确定性**：所有触碰模拟的回调（效果/被动/物品/比赛钩子/玩家 AI）必须
   同输入同输出。随机数**只能**从给你的 `rng_seed` 派生
   （自带 `rand`，`StdRng::seed_from_u64(rng_seed)`）。禁时钟、禁全局可变态、
   禁 HashMap 迭代序依赖、禁模拟回调里开线程。模拟契约不携带浮点
   （draft 评分是唯一 f32 例外，它在确定性区之外）。
2. **panic = mod 被禁用**（游戏继续运行）。用 `?`/默认值处理失败，不要 `unwrap`。
3. **context 与句柄只在回调期间有效**：`StableSim`、`StableClient`、`StrV1`、
   entity/player 句柄一律不存储、不跨线程；每次回调重新获取。

兼容性两条：
- **未知枚举码返回 `None`** → 直接跳过不认识的（游戏更新会新增场景/标签等）。
- 对旧游戏调用新 API → 优雅失败（`None`/`false`/空），不崩溃。因此**永远可以用最新 SDK 构建**。
- 若 mod 在旧游戏上毫无意义，可声明最低版本（节制使用）：
  `declare_stable_mod!(init, requires = 2);`（requires=2 → 需游戏 0.5.4+）。

## 5. 能注册什么（`StableMod` builder 一览）

| 调用 | 实现 | 作用 |
|---|---|---|
| `add_champion(...)` | `StableChampion`（+ `StableAction`、`StableEffectType`、`StablePassive`） | 新英雄；**复用现有 id 即改版**（SOP 05） |
| `add_item(...)` | `StableItem` | 带运行时回调的物品 |
| `add_native_effect(name, ...)` | `StableEffectType` | 命名效果：`.data_champion` 里经 `DataEffectDef::Native` 按名引用，也可被 `queue_effect`/`spawn_projectile` 使用 |
| `set_extension(...)` | `StableExtension` | 客户端生命周期钩子：标题/游戏 UI、存档数据、mod 命令/事件 |
| `set_server_extension(...)` | `StableServerExtension` | 权威服务器钩子：按存档写数据、客户端事件 |
| `add_draft_score_hook(...)` | `StableDraftHook` | 调整 ban/pick 评分，或直接决定禁/选（含候选身份） |
| `add_item_build_hook(...)` | `StableItemBuildHook` | 调整/决定 AI 最终出装——**让自己的物品真正被做出来**的途径（0.5.4+） |
| `add_player_input_ai(...)` | `StablePlayerAi` | 替换逐 tick 玩家输入 |
| `set_map_customizer(...)` | `StableMapCustomizer` | 每场比赛前编辑新构建的地图（塔/野怪营/兵线/水晶/泉水/墙草网格） |
| `set_match_hook(...)` | `StableMatchHook` | 在现有模式之上做自定义比赛逻辑：开始/tick 钩子 + 自有结束条件与胜负 |

`init` 期间还可用 `host.register_service(...)` / `host.query_service(...)`
在原生 mod 之间发布/消费运行时服务（消费方要在 `mod.mod_info` 声明依赖，SOP 01）。

## 6. 三个上下文（context）

| Context | 谁收到 | 能做什么 |
|---|---|---|
| `StableSim` | 效果 `apply`、被动/物品回调、比赛钩子（玩家 AI 经 `ctx.sim()` 拿**只读**视图，变更调用无效） | 实体/玩家/伤害/治疗/护盾/buff/CC/投射物/击杀日志/调试绘制/直接变更（HP/位置/属性/金币、`force_end`）/单位与投射物生成/延迟效果队列/队伍策略文档 |
| `StableClient` | 客户端扩展钩子 | 场景（含 NewGame 选项）、完整 UI 控制、渲染覆盖绘制、音频、原始按键、i18n、按 mod 存档数据、mod 命令/事件、管理数据读取（15 类记录、英雄信息、时钟、交手记录、当前画面） |
| `StableServerCtx` | 服务器扩展钩子 | 按 mod 存档命名空间、客户端事件下发、设置与记录文档读写、新闻注入、管理事件订阅、强制转会 |

**可用性矩阵**（摘）：render 钩子里 UI/场景/数据**只读**（写操作返回 false），但只有那里能 `draw_*`；
UI 事件处理器里只有 UI 与资产可用；存档/网络/管理数据在客户端钩子里仅 InGame 场景可用。

## 7. 常见开发套路

### 7.1 自定义英雄 / 物品 / 命名效果

- `StableChampion`：`id/name/skill_icon/category/tags/stat/growth/attack/skill/skill2`
  必备（`ult`、`passive` 可选）；`lane_prior()`（0.5.4+）给新英雄分路先验
  （0–100，50 中性）——新英雄没有对局历史前，ban/pick 模型对其分路一无所知。
- 视觉与数据英雄同一套资产规则（SOP 02 §7、SOP 04）——按 id 资产驱动。
- `StableAction` → `StableEffectSpec { range, growth_range, start_timing, casting, target, attack_type, effect }`；
  `StableEffectType::apply(sim, rng_seed, caster_id, input)` 是执行体，配套
  `expected_*` 系列报告方法（报告越准，AI 越会用你的技能）。
- `StableItem`：`key/icon/price/tier/stat()/next_tier()/previous_tier()/tags()/category()`
  + 全套回调（`on_attack`、`on_base_attack`、`on_damaged`、`on_kill`、`on_assist`、
  `on_dead`、`on_cc`、`on_skill_hit`、`on_upgrade`/`on_upgraded_from` 携带 u64 透传层数 等）。
  ⚠️ `on_attack` 技能命中也触发；只想要普攻用 `on_base_attack` 或按 `attack_type` 过滤。

### 7.2 模拟战斗调用（`StableSim`）

- `deal_damage(attacker, target, ad, ap, AttackTypeV1)`：走游戏完整管线
  （抗性/暴击/统计/击杀助攻/物品 buff 触发/吸血反伤/伤害数字）。**几乎总用它**。
  `ad`/`ap` 是**减免前**数值。要自管伤害数学才用 `deal_damage_raw`（无减免无统计无击杀）。
- 护盾不是 buff：`entity_add_shield` / `entity_clear_shield`；总量用 `entity.shield()` 读。
- `entity_set_hp(0)` 是**静默**击杀（无统计/击杀日志）；该计入时用 `deal_damage`。
- 生成：`spawn_unit(...)`（食尸鬼式 AI 追击附近敌人）、`spawn_projectile(name, effect_name, &ProjectileSpawnV1)`、
  `queue_effect(name, ..., delay_ticks)`（0.5.4 起 delay 从现在起算）。
- `on_kill`/`on_damaged`/`on_assist` 里的 `entity` 参数是**自己的英雄**；
  被击杀方单独作为 `victim` 传入。

### 7.3 UI / 渲染 / 音频 / 热键（`StableClient`）

- 节点树根为 `"body"`；`ui_child_names("")` 自顶向下探索真实树，`ui_runner_name(path)` 认控件类型。
- **创建**：`ui_spawn_source(parent, ".ui 源文本")`（与 `.ui` 资产同一语法，支持全部
  runner 种类——17 种引擎基础 + 约 50 种游戏专用）；`ui_spawn_template` 加载预建模板。
- **修改**：`ui_set_properties(path, "size: 24.; visible: true;")` 批量应用属性行；
  `ui_set_text`/`ui_set_visible` 覆盖最常用两种。
- **交互**：`ui_register_click`（按钮）；`ui_register_path_events` + `ui_current_event`
  收一路径上的**所有**事件（JSON payload）。注册是永久的，闭包须 `'static`。
- **控件状态**：checkbox/slider/text_edit/dropdown 均有读写 API（`ui_state_json` 通用）。
- **绘制**（仅 render 钩子，先 `can_draw()` 探测）：`draw_rect/circle/line/text/svg/sprite`
  到 `"UI"`/`"Default"`（1920×1080）或 `"Game"`（对局世界空间）。看不见就提高 z（5000+）。
- **音频**：`play_sound(path, volume)`、`play_bgm(...)`、`stop_bgm()`；自己 mod 内的音频文件自动加载。
- **热键**：`key_pressed("F5")` / `input_events()`；这是 UI 消费前的原始输入，
  注意结合场景判断玩家是否正在打字。

### 7.4 服务器逻辑（`StableServerCtx`）

- 生命周期：`on_server_start`、`before/after_management_tick`、`handle_command`。
- 命令往返：客户端 `send_command(cmd, payload)` → 服务器 `handle_command` 内
  `cmd.reply_target()` 选回复目标（Player > Team > Broadcast）→ `emit_event` →
  客户端 `take_events()` 收。
- 设置文档：`setting_get/set_*(SettingTargetV1::GameSetting, "kill_gold")`——
  影响本存档后续比赛。
- 记录文档：`team_*`/`athlete_*` 快捷 + `record_get/set_json(RecordKindV1, ...)` 通用。
  ⚠️ 跨记录不变量自负其责；比赛结果用比赛钩子的 `force_end`、转会用 `force_transfer`，
  不要裸改记录。
- 新闻：`news_push(team_id, title, content, author)`（接受字面量或 i18n 键）。
- 管理事件（拉取模式）：`management_events_after(cursor)` 消费
  `MatchFinished` / `TransferCompleted` / `SeasonRollover`（payload_json 携带明细；
  主机保留最近 256 条，游标 `seq` 存进 mod 存档数据，见 SOP 09）。
- 强制转会：`force_transfer(athlete_id, to_team_id, fee)`（跳过预算检查/分期/回租条款等，属刻意行为）。

### 7.5 自定义模式 = 四件事组合

设置 JSON（规则数值，服务器钩子改）+ 地图定制器（几何与物件）+ 比赛钩子
（胜负条件、逐 tick 逻辑）+ 模拟变更（执行你的规则）。
`StableMatchHook`：`on_match_start` / `on_match_tick` / `check_match_end -> Option<bool>`；
无人注册比赛钩子时游戏与原版逐位一致（不消耗随机数）。
地图定制器限制：寻路/视野由原始地形**离线烘焙**，大改墙/草会干扰移动 AI；
物件摆放（塔/营/水晶/泉水/兵线）完全实时可用。

### 7.6 AI 钩子

- `StableDraftHook`：`score_ban/score_pick`（`Pass | Add(δ) | Replace(s)` 调分）与
  `decide_ban/decide_pick`（返回 `Some(champion_id)` **直接锁定**该禁/选，绕过评分与
  top-k 随机；池外 id 被忽略回落正常流程；多个 decide 后者胜）。
- `StableItemBuildHook`（0.5.4+）：`score_item` 调分 / `decide_build` 全量决定最终装备；
  索引随时因 mod 集变化，**永远** `ctx.item_index("my_mod_blade")` 按 key 解析，不要硬编码数字。
- `StablePlayerAi`：`matches()` 圈定挂载对象；`think(ctx, base_input)` 每 tick 调用，
  返回 `None` 保持内置输入、`Some` 替换；必须是纯决策函数。`ctx.sim()` 可读整个模拟。
  构造输入用 `InputV1::move_to/return_home/action(...)`，先 `ctx.is_valid_input()` 校验。

## 8. 平台支持

- **支持哪些平台 = 你放了哪些二进制**，无任何声明字段：
  无原生模块（纯数据）→ 全平台；`my_mod.dll` → Win；`+ .so` → 加 Linux；`+ .dylib` → 三平台。
  只有 `.dll/.so/.dylib` 算数，`.bat/.ps1` 不算。
- 每个目标平台**只能在该平台上链接**：macOS 要 Mac（Apple SDK 不可再分发）、
  Linux 要 Linux 机器（或自装交叉链接如 cargo-zigbuild/WSL/VM）、Windows 实际也要 Win。
- 多平台二进制并排放同一 mod 文件夹 = 一个创意工坊物品；各玩家游戏只加载匹配
  自己系统的那份。改哪个平台就重传哪个，其余继续有效。
- 玩家在未支持的平台启用时，诊断弹窗会明确告知该 mod 没有其系统版本并列出所支持平台；
  其存档与其他 mod 不受影响。
- 上传器只构建**运行它的平台**；Workshop 物品描述自动获得 `Runs on:` 行。

## 9. 调试手段

| 手段 | 说明 |
|---|---|
| 日志 | `host.log(LogLevel::..., msg)`；日志文件 `%APPDATA%\TeamSamoyed\TeamfightManager2\data\log.log`（panic 导致 mod 被禁时，panic 信息也记录在此） |
| 模拟可视化 | 任意模拟回调里 `debug_draw_line/circle`（世界坐标，叠画在对局视图上） |
| UI 探索 | `ui_child_names("")` 向下走树 + `ui_runner_name` 认节点，比猜路径快 |
| 失败探测 | 几乎所有调用返回 `Option`/`bool`——「没反应」先查返回值：`false`/`None` 通常是上下文不对（可用性矩阵）、路径不对、或写入被 schema 拒绝 |

## 10. 开发循环与验证

1. 改 `src/lib.rs` → `cargo build --release`。
2. 复制/替换 `mods/<mod_id>/<mod_id>.dll`。
3. **重启游戏**（DLL 只在启动时加载；元数据与启动资产同理）。
4. 看诊断弹窗 + `log.log`。
5. 进对局/对应场景验证行为。

完整排错清单见 [SOP 10](10-测试与故障排查.md)。

## 11. 常见陷阱（官方 FAQ 摘录）

- 句柄/context 跨回调存储 → 优雅失败不是 UB，但行为是「拿不到」；每次重取。
- `unwrap` 进钩子 → panic 禁用 mod。
- 确定性违规（时钟、HashMap 序、可变静态量、模拟回调内开线程）→ 毁回放与多人同步。
- `entity_set_hp` vs `deal_damage`：前者静默、后者计统计。
- 护盾塞进 `BuffV1` 无效——护盾是独立吸收层。
- 装备索引不稳定：build 钩子内按 key 解析。
- 新英雄没 `lane_prior` → draft AI 视五路等概率。
- 覆盖层看不见 → 提高 z。
- `Match*` 记录 id 分类别（Normal #4 ≠ Practice #4）；服务器侧 `Match` 是单张平表。
- 客户端钩子拿到的是墙钟 `dt_micros`，模拟里是 tick——两套钟不要混用。

## 12. 下一步

- 存数据 → [SOP 09](09-Mod存档数据.md)
- 发布 → [SOP 11](11-创意工坊发布.md)
- 逐函数查阅 → [Stable API Reference](../TeamfightManager2Mod/docs/stable-api-reference.md)
