# SOP 09 · Mod 存档数据（Mod Save Data）

> 目标：在**游戏存档**中读写属于单个存档位的小型自定义数据——mod 教程标记、
> 自定义进度、生成的状态、应随存档联盟走的设置。
> 前置：SOP 08（原生 mod 工程）。
> 源文档：[mod-save-data](../TeamfightManager2Mod/docs/mod-save-data.md) ·
> [stable-api-reference §6-7/§7-2](../TeamfightManager2Mod/docs/stable-api-reference.md)

---

## 1. 适用判断

| 数据 | 放哪 |
|---|---|
| 属于某一个存档（教程标记、自定义进度、随联盟走的设置） | **本 SOP**：mod 存档数据 |
| 大型资产、日志 | ❌ 不要放存档 |
| 全局偏好（对所有存档生效的 mod 配置） | ❌ 不要放存档，放 mod 自己的配置 |

mod 存档数据通过客户端 `StableClient` 的 `save_*` 系列访问（InGame 场景），
服务器侧经 `StableServerCtx` 同一套接口做**权威写入**。
（classic 路径对应 `ClientData::mod_save_*`，见文末。）

## 2. 基本用法（Stable API，客户端）

```rust
use mod_api_stable::*;

const MOD_ID: &str = "my_mod";   // = 文件夹名 = StableMod::new(...) 的 id

fn some_update_hook(ctx: &mut StableClient<'_>) {
    if !ctx.save_contains_key("initialized") {
        ctx.save_set_version(1);
        ctx.save_set_string("initialized", "true");
    }
}
```

命名空间由 mod id **自动划分**——不用也不能手动指定命名空间。

### 接口清单（客户端与服务器同构）

```rust
save_can_write()
save_version() / save_set_version(v)
save_keys() / save_contains_key(k)
save_get_bytes(k) / save_set_bytes(k, v)
save_get_string(k) / save_set_string(k, v)
save_remove_key(k)
```

## 3. 版本与迁移

每个命名空间有自己的版本号，**与 `mod.mod_info` 的包版本无关**，
只服务于你的存档数据格式：

```rust
let version = ctx.save_version();
if version < 1 {
    ctx.save_set_string("initialized", "true");
    ctx.save_set_version(1);
}
```

迁移守则：用版本号渐进迁移，不要一上来就重命名旧键。

## 4. 限制

| 项 | 上限 |
|---|---|
| mod id | 非空、≤128 字节、无 NUL |
| key | 非空、≤128 字节、无 NUL |
| 单 key 值 | ≤ 1 MiB |
| 字符串 | UTF-8 字节 |

用紧凑值。需要结构化数据时，自己序列化成 JSON 或二进制 blob 存进 bytes/string。

## 5. 多人行为（重要）

- 单人存档：正常读写。
- **多人联盟存档：只有主机能写**。非主机客户端先查 `save_can_write()`，
  或处理写接口的 `false` 返回值。
- 服务器（主机侧）拥有最终存档数据：校验写入、更新存档数据库、向其他客户端广播。
- `ModServerExtension` 直接写 `ctx.database.mod_save_data`（classic）/ `ctx.save_set_*`（stable）
  的变更已在权威服务器数据库上；客户端 UI 需要即时反应就 `emit_event(...)`，
  否则等下一次常规数据同步。
- 写接口返回 `true` 仅表示本地请求被接受并入队；返回 `false` 表示
  mod id/key/值/多人写权限不合法。

## 6. 服务器权威写入（Stable API）

```rust
fn handle_command(&self, ctx: &mut StableServerCtx<'_>, cmd: &StableCommand<'_>) -> CommandResultV1 {
    if cmd.command != "remember_note" { return CommandResultV1::Pass; }
    ctx.save_set_bytes("note", cmd.payload);
    ctx.emit_event(cmd.reply_target(), "note_saved", b"ok");
    CommandResultV1::Handled
}
```

（客户端侧 `send_command("remember_note", ...)` + `take_events()` 收回执，
完整命令/事件模型见 [SOP 08 §7.4](08-原生Stable-Mod开发.md)。）

## 7. 持久化验证流程

mod 存档数据存在**游戏存档**里。写入后：

1. 走正常的存档/自动存档流程。
2. 重新读取**同一存档位**。
3. `save_get_*` 能取回值 → 持久化成功。

## 8. 实践守则（官方建议）

- **不要每帧写同一个值**：先判断缺失或变化再写。
- 只用一个命名空间：你自己的 `MOD_ID`。
- 标记与计数器用小字符串值。
- 迁移用命名空间版本号，而非立即重命名旧键。
- 全局 mod 配置放存档之外。

## 9. classic 路径对照（迁移参考）

| classic（`mod_api` / `ClientData`） | stable（`StableClient` / `StableServerCtx`） |
|---|---|
| `data.mod_save_get_string(MOD_ID, k)` | `ctx.save_get_string(k)`（自动命名空间） |
| `data.mod_save_set_string(MOD_ID, k, v)` | `ctx.save_set_string(k, v)` |
| `data.mod_save_version(MOD_ID)` / `set_version` | `ctx.save_version()` / `save_set_version(v)` |
| `data.mod_save_keys/contains_key/remove_key/clear_namespace` | `ctx.save_keys()` / `save_contains_key` / `save_remove_key`（stable 无 clear_namespace） |
| `data.can_write_mod_save()` | `ctx.save_can_write()` |
| `ctx.database.mod_save_data`（服务器直写） | `ctx.save_set_*`（服务器上下文） |

classic 最常见的访问位置是 `ModExtension` 钩子里匹配 `Scene::InGame { data }`；
stable 对应「InGame 场景的客户端钩子」。细节差异见 [SOP 12](12-维护与版本迁移.md)。

## 10. 排错速查（详见 [SOP 10](10-测试与故障排查.md)）

| 症状 | 排查 |
|---|---|
| 数据不持久 | DLL 有诊断错误；不在 InGame 场景访问；mod id 与文件夹/注册 id 不一致；没走存档流程就读回 |
| 写入返回 false | key/mod id 空、超 128 字节、含 NUL；值超 1 MiB；多人中非主机 |
| 联机数据「丢了」 | 多人联盟只有主机写；确认由主机侧写入 |
| 值每帧被重置 | 每帧无条件覆写同一键 |
