# 远古古城战利品预测器

**MC 26.1.2 Ancient City Loot Predictor** — 从世界种子出发，在打开箱子之前，精确预测远古古城每个箱子里装什么。

```
世界种子 → 古城定位 → Jigsaw 拼图模拟 → 箱子坐标 → LootTableSeed → 箱内物品（槽位级）
```

所有 RNG 均为对游戏反编译代码的精确复刻：

| 环节 | RNG 实现 | 验证精度 |
|------|---------|---------|
| Jigsaw 结构块放置 | `LegacyRandomSource`（Java LCG） | **557/557**（6 座古城，位置+包围盒完全一致） |
| LootTableSeed | `Xoroshiro128++`（setDecorationSeed / setFeatureSeed 链） | **158/159（99.4%）** |
| 箱内战利品 | `LootTable` / `LootPool` / `EnchantmentHelper` 全套复刻 | **100%**（槽位级一致，含附魔种类与等级） |

## ✨ 功能特性

- **古城定位**：纯 Python 实现远古古城定位（区域盐值+变体随机），无需第三方库；可选加载 `cubiomes_wrapper` 进行生物群系验证，剔除不会实际生成的候选
- **双模式预测**
  - 单古城模式：搜索附近古城，选中后完整模拟
  - 大范围寻找：默认 1000 区块（16000 方块）半径内扫描全部古城，可调至 10000 区块
- **战利品筛选**：按物品过滤命中结果；附魔书支持右键三级筛选（任意附魔书 → 指定附魔种类 → 指定等级）
- **精确附魔预测**：完整复刻 `EnchantmentHelper.selectEnchantment`，附魔书的附魔种类、等级、多附魔组合均可预测
- **Xaero 路径点导出**：一键将命中箱子导出为 Xaero's Minimap 路径点文件
- **配置持久化**：种子、坐标、筛选条件自动保存，重启恢复
- **CLI + GUI 双入口**：`ancient_city_predictor.py` 支持命令行批处理与 `--verify` 回归验证

## 🖼️ 界面预览

<!-- 在此插入截图: docs/screenshot_main.png -->

## 📦 环境要求

- Python **3.8+**（需包含 tkinter，Windows 官方安装包默认自带）
- 无强制第三方依赖

可选组件：

| 组件 | 用途 |
|------|------|
| `cubiomes_wrapper.dll/.so/.dylib` | 古城定位的生物群系验证（避免"候选不生成"） |

## 📂 数据文件准备（重要）

本仓库**不附带**游戏数据文件（版权归 Mojang 所有），需从你自己的 MC 26.1.2 客户端 jar（本质是 zip）中提取 `data/` 目录，按以下结构摆放：

```
项目根目录/
├── ancient_city_gui.py
├── ancient_city_predictor.py
├── loot_simulator.py
├── enchant_data.json        # 附魔数据（仓库自带，手工整理）
└── loot_tables/
    └── data/
        └── minecraft/
            ├── structure/              # ancient_city/*.nbt 结构模板
            ├── worldgen/
            │   └── template_pool/      # ancient_city/*.json 拼图池
            └── loot_table/
                └── chests/             # ancient_city_*.json 战利品表
```

提取方法：用压缩软件打开 `.minecraft/versions/26.1.2/26.1.2.jar`，将其中 `data/minecraft/` 下的上述三个目录复制到 `loot_tables/data/minecraft/`。

> ⚠️ 数据文件版本必须与 MC 26.1.2 匹配，跨版本混用会导致预测失败。

## 🚀 使用方法

### GUI（推荐）

```bash
python ancient_city_gui.py
```

1. 输入**世界种子** → 点击「确认种子」
2. 输入**玩家坐标**（方块坐标）→ 点击「确认坐标」
3. 点击「搜索附近古城」→ 自动定位并列出候选（含旋转、起始件、距离）
4. （可选）在「战利品筛选」中添加目标物品；附魔书条目**右键**选择附魔种类与等级
5. 「开始预测」或切到「大范围寻找」标签页
6. **双击**结果行查看箱子完整物品清单；「导出 Xaero 路径点」生成小地图标记

### CLI

```bash
# 预测指定古城
python ancient_city_predictor.py --seed <世界种子> --chunk <区块X> <区块Z>

# 仅输出结构块布局
python ancient_city_predictor.py --seed -7346913998703726680 --chunk 19513 1830 --pieces-only

# 回归验证（需 work/pieces_gt.json 基准数据）
python ancient_city_predictor.py --verify
```

## 🔍 工作原理

1. **古城定位**：按 24×24 区块的区域划分，用结构盐值推导每个区域唯一的古城原点，再用 `setLargeFeatureSeed` 推导旋转与起始件（city_center_1/2/3）
2. **Jigsaw 模拟**：从 city_anchor 锚点开始，完整复刻 `JigsawPlacement.Placer` 的 BFS 放置流程——拼图块打乱、旋转枚举、`canAttach` 校验、VoxelShape 碰撞检测，RNG 调用顺序与游戏逐位一致
3. **箱子提取**：从模拟出的结构模板 NBT 中读取全部容器方块，按 (y, x, z) 排序，按区块分组
4. **LootTableSeed**：每个区块独立走 `setDecorationSeed → setFeatureSeed(step=7) → nextLong` 链
5. **战利品模拟**：复刻 `LootPool` 掷骰、`EnchantmentHelper` 附魔选择、全部 LootItemFunction（set_count / set_damage / enchant_with_levels / enchant_randomly / set_potion）、`shuffleAndSplitItems` 槽位分配

## ❓ 常见问题

**Q：预测结果和游戏里实际不符？**

按顺序检查：
1. 该区块是否在**旧版本**存档中生成过——跨版本的结构不会迁移重算，预测基于当前版本 RNG
2. 箱子是否**已被打开过**——首次开启时战利品即固化
3. 数据文件版本是否为 26.1.2
4. 是否启用了 cubiomes 验证——未验证的候选可能因生物群系不符而不生成

**Q：纯 Python 定位的古城有的不存在？**

定位算法不含生物群系判定。编译 [cubiomes](https://github.com/Cubitect/cubiomes) 封装为 `cubiomes_wrapper.dll` 放到脚本目录即可启用验证。

**Q：158/159 那一个失配是什么？**

已知的边界情况（特定古城的一个箱子 LootTableSeed 偏差），不影响其余预测。欢迎提 issue 定位根因。

**Q：能在服务器上用吗？**

本工具仅适用于你拥有合法权限且服务器规则允许的场合。请遵守 EULA 与所在服务器规则，风险自负。

## ⚠️ 免责声明

本项目仅供学习研究 RNG 与结构生成原理，与 Mojang / Microsoft 无关。Minecraft 相关资产版权归 Mojang 所有。

## 📄 许可证

[MIT License](LICENSE)

## 🙏 致谢

- 反编译参考：Mojang 官方映射
- 生物群系验证：[cubiomes](https://github.com/Cubitect/cubiomes)
