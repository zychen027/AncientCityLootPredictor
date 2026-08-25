#!/usr/bin/env python3
"""
MC 26.1.2 远古古城战利品预测器 — GUI
=====================================
深色主题的 tkinter 图形界面。

功能：
  - 种子输入 + 确认
  - 玩家坐标输入 + 确认（自动转换为区块坐标）
  - 附近古城搜索（纯 Python 定位，无需 cubiomes）
  - 战利品筛选（从 loot table 自动提取）
  - 实时日志面板
  - 结果输出表格
  - 配置自动保存/恢复
  - 附魔书右键分级选择（附魔种类 → 附魔等级）
  - 标签页模式：单古城预测 + 大范围寻找
"""

import os, sys, json, threading, math, ctypes

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# config.json 存放目录：Nuitka onefile 下 __file__ 和 sys.executable 都指向临时目录，
# 只有 sys.argv[0] 是用户实际运行的 exe 路径
_candidates = [
    os.path.dirname(os.path.abspath(sys.argv[0])),  # exe/脚本所在目录
    SCRIPT_DIR,                                      # 脚本所在目录
    os.path.expanduser('~'),                         # 用户主目录兜底
]
CONFIG_DIR = None
for _d in _candidates:
    if _d and os.path.isdir(_d):
        try:
            _test = os.path.join(_d, '.nuitka_write_test')
            with open(_test, 'w') as _f:
                _f.write('ok')
            os.remove(_test)
            CONFIG_DIR = _d
            break
        except (OSError, PermissionError):
            continue
if CONFIG_DIR is None:
    CONFIG_DIR = os.path.expanduser('~')

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

import ancient_city_predictor as acp


# ============================================================
# cubiomes 动态加载（可选，用于 biome 验证）
# ============================================================
_cubiomes_cache = None  # type: ctypes.CDLL | None

def _load_cubiomes():
    """尝试加载 cubiomes_wrapper 动态库。
    查找顺序: 脚本目录 → 当前目录
    文件名: cubiomes_wrapper.dll (Windows) / libcubiomes_wrapper.so (Linux)
    """
    global _cubiomes_cache
    if _cubiomes_cache is not None:
        return _cubiomes_cache

    candidates = []
    for d in [SCRIPT_DIR, os.getcwd()]:
        for name in ['cubiomes_wrapper.dll', 'libcubiomes_wrapper.so',
                      'libcubiomes_wrapper.dylib']:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                candidates.append(p)

    for path in candidates:
        try:
            lib = ctypes.CDLL(path)
            # 验证符号存在
            if not hasattr(lib, 'cubiomes_find_ancient_cities'):
                continue
            lib.cubiomes_init.restype = ctypes.c_int
            lib.cubiomes_init.argtypes = [ctypes.c_int, ctypes.c_uint64]
            lib.cubiomes_find_ancient_cities.restype = ctypes.c_int
            lib.cubiomes_find_ancient_cities.argtypes = [
                ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.c_int), ctypes.c_int,
            ]
            _cubiomes_cache = lib
            return lib
        except OSError:
            continue

    _cubiomes_cache = False  # 标记为已查找但未找到
    return None


# ============================================================
# 深色主题配色
# ============================================================
class DarkTheme:
    BG          = "#1e1e2e"
    BG_ALT      = "#181825"
    BG_ENTRY    = "#313244"
    BG_PANEL    = "#1e1e2e"
    BG_LIST     = "#11111b"
    FG          = "#cdd6f4"
    FG_DIM      = "#a6adc8"
    FG_BRIGHT   = "#f5e0dc"
    ACCENT      = "#89b4fa"
    ACCENT_BG   = "#313244"
    SUCCESS     = "#a6e3a1"
    WARNING     = "#f9e2af"
    ERROR       = "#f38ba8"
    BORDER      = "#45475a"
    TABLE_BG    = "#11111b"
    TABLE_SEL   = "#45475a"
    TABLE_ROW   = "#1e1e2e"
    TABLE_ALT   = "#181825"


def apply_dark_theme(root):
    """应用深色主题到 ttk 组件"""
    style = ttk.Style()
    style.theme_use('clam')

    # 基础样式
    style.configure('.', background=DarkTheme.BG, foreground=DarkTheme.FG,
                    bordercolor=DarkTheme.BORDER, lightcolor=DarkTheme.BORDER,
                    darkcolor=DarkTheme.BORDER, troughcolor=DarkTheme.BG_ALT,
                    focuscolor=DarkTheme.ACCENT)
    style.configure('TFrame', background=DarkTheme.BG)
    style.configure('TLabel', background=DarkTheme.BG, foreground=DarkTheme.FG)
    style.configure('TLabelframe', background=DarkTheme.BG, foreground=DarkTheme.FG_BRIGHT,
                    bordercolor=DarkTheme.BORDER)
    style.configure('TLabelframe.Label', background=DarkTheme.BG, foreground=DarkTheme.ACCENT)

    # 输入框
    style.configure('TEntry', fieldbackground=DarkTheme.BG_ENTRY, foreground=DarkTheme.FG_BRIGHT,
                    insertcolor=DarkTheme.FG, bordercolor=DarkTheme.BORDER)
    style.map('TEntry', fieldbackground=[('readonly', DarkTheme.BG_ENTRY)])

    # 按钮
    style.configure('TButton', background=DarkTheme.ACCENT_BG, foreground=DarkTheme.FG,
                    bordercolor=DarkTheme.BORDER, focusthickness=1)
    style.map('TButton',
              background=[('active', DarkTheme.BORDER), ('disabled', DarkTheme.BG_ALT)],
              foreground=[('disabled', DarkTheme.FG_DIM)])

    # 组合框
    style.configure('TCombobox', fieldbackground=DarkTheme.BG_ENTRY, foreground=DarkTheme.FG_BRIGHT,
                    background=DarkTheme.ACCENT_BG, arrowcolor=DarkTheme.FG,
                    bordercolor=DarkTheme.BORDER)
    root.option_add('*TCombobox*Listbox.background', DarkTheme.BG_ENTRY)
    root.option_add('*TCombobox*Listbox.foreground', DarkTheme.FG)
    root.option_add('*TCombobox*Listbox.selectBackground', DarkTheme.TABLE_SEL)
    root.option_add('*TCombobox*Listbox.selectForeground', DarkTheme.FG_BRIGHT)

    # Treeview（表格）
    style.configure('Treeview', background=DarkTheme.TABLE_BG, foreground=DarkTheme.FG,
                    fieldbackground=DarkTheme.TABLE_BG, bordercolor=DarkTheme.BORDER,
                    rowheight=24)
    style.map('Treeview',
              background=[('selected', DarkTheme.TABLE_SEL)],
              foreground=[('selected', DarkTheme.FG_BRIGHT)])
    style.configure('Treeview.Heading', background=DarkTheme.BG_ALT, foreground=DarkTheme.FG_BRIGHT,
                    bordercolor=DarkTheme.BORDER, relief='flat')
    style.map('Treeview.Heading', background=[('active', DarkTheme.BORDER)])

    # Scrollbar
    style.configure('TScrollbar', background=DarkTheme.BG_ALT, troughcolor=DarkTheme.BG,
                    bordercolor=DarkTheme.BORDER, arrowcolor=DarkTheme.FG)
    style.map('TScrollbar', background=[('active', DarkTheme.BORDER)])

    # PanedWindow
    style.configure('TPanedwindow', background=DarkTheme.BG)
    style.configure('Sash', background=DarkTheme.BORDER, bordercolor=DarkTheme.BORDER)

    # Notebook（标签页）
    style.configure('TNotebook', background=DarkTheme.BG, bordercolor=DarkTheme.BORDER)
    style.configure('TNotebook.Tab', background=DarkTheme.BG_ALT, foreground=DarkTheme.FG,
                    bordercolor=DarkTheme.BORDER, padding=[16, 6], font=('Microsoft YaHei UI', 10, 'bold'))
    style.map('TNotebook.Tab',
              background=[('selected', DarkTheme.BG_ENTRY), ('active', DarkTheme.BG)],
              foreground=[('selected', DarkTheme.FG_BRIGHT), ('active', DarkTheme.FG_BRIGHT)])

    # 进度条
    style.configure('TProgressbar', background=DarkTheme.ACCENT, troughcolor=DarkTheme.BG_ALT,
                    bordercolor=DarkTheme.BORDER)

    root.configure(bg=DarkTheme.BG)


# ============================================================
# 附魔中文名映射
# ============================================================
ENCH_CN = {
    'minecraft:swift_sneak': '迅捷潜行',
    'minecraft:mending': '经验修补',
    'minecraft:unbreaking': '耐久',
    'minecraft:efficiency': '效率',
    'minecraft:fortune': '时运',
    'minecraft:silk_touch': '精准采集',
    'minecraft:protection': '保护',
    'minecraft:fire_protection': '火焰保护',
    'minecraft:blast_protection': '爆炸保护',
    'minecraft:projectile_protection': '弹射物保护',
    'minecraft:thorns': '荆棘',
    'minecraft:respiration': '水下呼吸',
    'minecraft:aqua_affinity': '水下速掘',
    'minecraft:depth_strider': '深海探索者',
    'minecraft:feather_falling': '摔落保护',
    'minecraft:frost_walker': '冰霜行者',
    'minecraft:soul_speed': '灵魂疾行',
    'minecraft:sharpness': '锋利',
    'minecraft:smite': '亡灵杀手',
    'minecraft:bane_of_arthropods': '节肢杀手',
    'minecraft:knockback': '击退',
    'minecraft:fire_aspect': '火焰附加',
    'minecraft:looting': '抢夺',
    'minecraft:sweeping_edge': '横扫之刃',
    'minecraft:power': '力量',
    'minecraft:punch': '冲击',
    'minecraft:flame': '火矢',
    'minecraft:infinity': '无限',
    'minecraft:luck_of_the_sea': '海之眷顾',
    'minecraft:lure': '饵钓',
    'minecraft:loyalty': '忠诚',
    'minecraft:impaling': '穿刺',
    'minecraft:riptide': '激流',
    'minecraft:channeling': '引雷',
    'minecraft:multishot': '多重射击',
    'minecraft:quick_charge': '快速装填',
    'minecraft:piercing': '穿透',
    'minecraft:density': '致密',
    'minecraft:breach': '裂痕',
    'minecraft:lunge': '突进',
    'minecraft:wind_burst': '风爆',
    'minecraft:binding_curse': '绑定诅咒',
    'minecraft:vanishing_curse': '消失诅咒',
}

ROMAN = ['I', 'II', 'III', 'IV', 'V']


# ============================================================
# 战利品物品提取
# ============================================================
def extract_loot_items(data_dir):
    """从 loot table JSON 中提取所有可能物品，返回中文名映射。
    附魔书只显示一个"附魔书"条目，具体附魔类型和等级通过右键菜单选择。
    """
    loot_dir = os.path.join(data_dir, 'data', 'minecraft', 'loot_table', 'chests')

    # 物品中文名映射
    ITEM_CN = {
        'minecraft:amethyst_shard': '紫水晶碎片',
        'minecraft:baked_potato': '烤土豆',
        'minecraft:bone': '骨头',
        'minecraft:book': '书',
        'minecraft:candle': '蜡烛',
        'minecraft:coal': '煤炭',
        'minecraft:compass': '指南针',
        'minecraft:diamond_hoe': '钻石锄',
        'minecraft:diamond_horse_armor': '钻石马铠',
        'minecraft:diamond_leggings': '钻石护腿',
        'minecraft:disc_fragment_5': '唱片碎片5',
        'minecraft:echo_shard': '回响碎片',
        'minecraft:enchanted_golden_apple': '附魔金苹果',
        'minecraft:experience_bottle': '附魔之瓶',
        'minecraft:glow_berries': '发光浆果',
        'minecraft:golden_carrot': '金胡萝卜',
        'minecraft:iron_leggings': '铁护腿',
        'minecraft:lead': '拴绳',
        'minecraft:leather': '皮革',
        'minecraft:music_disc_13': '唱片13',
        'minecraft:music_disc_cat': '唱片cat',
        'minecraft:music_disc_otherside': '唱片otherside',
        'minecraft:packed_ice': '浮冰',
        'minecraft:potion': '药水',
        'minecraft:sculk': '幽匿块',
        'minecraft:sculk_catalyst': '幽匿催发体',
        'minecraft:sculk_sensor': '幽匿感测体',
        'minecraft:silence_armor_trim_smithing_template': '沉默盔甲纹锻造模板',
        'minecraft:snowball': '雪球',
        'minecraft:soul_torch': '灵魂火把',
        'minecraft:suspicious_stew': '迷之炖菜',
        'minecraft:ward_armor_trim_smithing_template': '监守盔甲纹锻造模板',
    }

    items = {}  # internal_name → display_name

    for fname in sorted(os.listdir(loot_dir)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(loot_dir, fname)
        with open(path) as f:
            data = json.load(f)
        for pool in data.get('pools', []):
            for entry in pool.get('entries', []):
                if entry.get('type') != 'minecraft:item':
                    continue
                n = entry.get('name', '')
                if n == 'minecraft:book':
                    # 检查是否有附魔函数
                    has_enchant = False
                    for func in entry.get('functions', []):
                        if func.get('function') in ('minecraft:enchant_randomly',
                                                    'minecraft:enchant_with_levels'):
                            has_enchant = True
                            break
                    if has_enchant:
                        # 附魔书 — 统一为一个条目，右键选择具体附魔
                        items['enchanted_book:any:any'] = '附魔书'
                    else:
                        # 普通的书
                        items[n] = ITEM_CN.get(n, '书')
                elif n not in items:
                    if n in ITEM_CN:
                        items[n] = ITEM_CN[n]
                    else:
                        items[n] = n.replace('minecraft:','').replace('_',' ').title()

    return items


ROT_CN = ['无旋转', '顺时针90°', '顺时针180°', '逆时针90°']


# ============================================================
# 主界面
# ============================================================
class AncientCityGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("远古古城战利品预测器")
        self.root.geometry("1200x900")
        self.root.minsize(1000, 700)
        self.root.configure(bg=DarkTheme.BG)

        # 状态
        self.world_seed = None
        self.player_x = None
        self.player_z = None
        self.selected_items = []  # list of internal names, index matches listbox
        self.loot_items = {}
        self.is_running = False
        self.world_dir = None
        self.city_candidates = []
        self.selected_city_idx = None

        self.data_dir = acp._DATA_DIR
        self.config_path = os.path.join(CONFIG_DIR, 'config.json')

        self._load_loot_items()
        self.available_enchants = self._load_available_enchants()
        self._build_ui()
        self._load_config()

    def _load_loot_items(self):
        try:
            self.loot_items = extract_loot_items(self.data_dir)
        except Exception:
            self.loot_items = {}

    def _load_available_enchants(self):
        """从 enchant_data.json 动态解析古城可用的附魔列表。
        返回: {ench_id: {'name': str, 'max_level': int}}
        """
        # enchant_data.json 在脚本目录(SCRIPT_DIR)，不在 loot_tables 数据目录
        data_path = os.path.join(SCRIPT_DIR, 'enchant_data.json')
        try:
            with open(data_path) as f:
                data = json.load(f)
        except Exception:
            return {}

        enchants = data.get('enchants', {})
        on_random_loot = data.get('on_random_loot', [])
        non_treasure = data.get('non_treasure', [])

        # 解析 on_random_loot → 展开所有附魔 ID
        available_ids = set()
        for item in on_random_loot:
            if item == '#minecraft:non_treasure':
                available_ids.update(non_treasure)
            else:
                available_ids.add(item)

        # swift_sneak 来自 loot table 的 enchant_randomly:swift_sneak 条目
        available_ids.add('minecraft:swift_sneak')

        # 构建附魔信息（只保留实际存在于 enchants 数据中的）
        available = {}
        for ench_id in sorted(available_ids):
            ench_data = enchants.get(ench_id)
            if not ench_data:
                continue
            ench_name = ENCH_CN.get(ench_id, ench_id.replace('minecraft:', ''))
            available[ench_id] = {
                'name': ench_name,
                'max_level': ench_data['max_level'],
            }

        return available

    # ========================================================
    # 配置保存/恢复
    # ========================================================
    def _load_config(self):
        """从 config.json 恢复上次配置"""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        self.seed_var.set(cfg.get('seed', ''))
        self.x_var.set(cfg.get('x', ''))
        self.z_var.set(cfg.get('z', ''))
        self.world_dir_var.set(cfg.get('world_dir', ''))
        self.range_var.set(str(cfg.get('range', '1000')))

        # 恢复已选物品
        for internal in cfg.get('selected_items', []):
            display = self._display_name_for_internal(internal)
            if display:
                self.selected_items.append(internal)
                self.selected_listbox.insert('end', display)

        # 恢复种子和坐标状态
        raw_seed = cfg.get('seed', '').strip()
        if raw_seed:
            try:
                self.world_seed = int(raw_seed)
                self.seed_status.config(text=f"已确认: {self.world_seed}",
                                        foreground=DarkTheme.SUCCESS)
            except ValueError:
                pass

        x_raw = cfg.get('x', '').strip()
        z_raw = cfg.get('z', '').strip()
        if x_raw and z_raw:
            try:
                self.player_x = int(x_raw)
                self.player_z = int(z_raw)
                chunk_x = math.floor(self.player_x / 16)
                chunk_z = math.floor(self.player_z / 16)
                self.coord_status.config(
                    text=f"方块({self.player_x}, {self.player_z}) → 区块({chunk_x}, {chunk_z})",
                    foreground=DarkTheme.SUCCESS)
            except ValueError:
                pass

        self._check_ready()

    def _save_config(self):
        """保存当前配置到 config.json"""
        cfg = {
            'seed': self.seed_var.get(),
            'x': self.x_var.get(),
            'z': self.z_var.get(),
            'world_dir': self.world_dir_var.get(),
            'selected_items': list(self.selected_items),
            'range': self.range_var.get(),
        }
        try:
            with open(self.config_path, 'w') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ========================================================
    # 附魔书显示名/内部名转换
    # ========================================================
    def _ench_book_display(self, internal):
        """根据内部名生成附魔书显示名"""
        parts = internal.split(':')
        # parts[0] = 'enchanted_book'
        ench_type = parts[1] if len(parts) > 1 else 'any'
        level = parts[2] if len(parts) > 2 else 'any'

        if ench_type == 'any':
            return '附魔书'

        ench_info = self.available_enchants.get(f'minecraft:{ench_type}')
        if not ench_info:
            return f'{ench_type}附魔书'
        ench_name = ench_info['name']

        if level == 'any':
            return f'{ench_name}附魔书'

        try:
            lv = int(level)
            lv_roman = ROMAN[lv - 1] if 1 <= lv <= 5 else str(lv)
        except ValueError:
            lv_roman = level
        return f'{ench_name}{lv_roman}附魔书'

    def _display_name_for_internal(self, internal):
        """获取任意内部名的显示名"""
        if internal.startswith('enchanted_book:'):
            return self._ench_book_display(internal)
        return self.loot_items.get(internal,
                                   internal.replace('minecraft:', '').replace('_', ' ').title())

    # ========================================================
    # UI 构建
    # ========================================================
    def _build_ui(self):
        # === 状态栏（先 pack bottom） ===
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief='sunken', anchor='w', padding=4)
        status_bar.pack(fill='x', side='bottom')

        # === 日志面板（shared, 在底部状态栏之上） ===
        log_frame = ttk.LabelFrame(self.root, text="日志", padding=4)
        log_frame.pack(fill='x', side='bottom', padx=8, pady=(4, 0))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap='word', height=5,
            bg=DarkTheme.BG_LIST, fg=DarkTheme.FG, insertbackground=DarkTheme.FG,
            relief='flat', highlightthickness=1,
            highlightbackground=DarkTheme.BORDER,
            highlightcolor=DarkTheme.ACCENT,
            font=('Consolas', 10), state='disabled')
        self.log_text.pack(fill='x')

        # === 顶部：输入区 ===
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill='x', side='top')

        # --- 种子行 ---
        seed_frame = ttk.LabelFrame(top, text="世界种子", padding=8)
        seed_frame.pack(fill='x', pady=(0, 4))

        ttk.Label(seed_frame, text="种子:").grid(row=0, column=0, sticky='w', padx=2)
        self.seed_var = tk.StringVar()
        self.seed_entry = ttk.Entry(seed_frame, textvariable=self.seed_var, width=40)
        self.seed_entry.grid(row=0, column=1, sticky='w', padx=4)

        self.seed_btn = ttk.Button(seed_frame, text="确认种子", command=self.on_confirm_seed)
        self.seed_btn.grid(row=0, column=2, padx=4)

        self.seed_status = ttk.Label(seed_frame, text="未设置", foreground=DarkTheme.FG_DIM)
        self.seed_status.grid(row=0, column=3, padx=8)

        # 世界存档目录 (在种子行右侧)
        ttk.Label(seed_frame, text="存档目录:").grid(row=0, column=4, padx=(16, 2))
        self.world_dir_var = tk.StringVar()
        ttk.Entry(seed_frame, textvariable=self.world_dir_var, width=25).grid(row=0, column=5, padx=2)
        ttk.Button(seed_frame, text="浏览...", command=self.on_browse_world).grid(row=0, column=6, padx=2)
        self.world_dir_var.trace_add('write', lambda *_: self._on_world_dir_change())

        # --- 坐标行 ---
        coord_frame = ttk.LabelFrame(top, text="玩家坐标（方块坐标）", padding=8)
        coord_frame.pack(fill='x', pady=(0, 4))

        ttk.Label(coord_frame, text="X:").grid(row=0, column=0, sticky='w', padx=2)
        self.x_var = tk.StringVar()
        ttk.Entry(coord_frame, textvariable=self.x_var, width=12).grid(row=0, column=1, padx=2)
        ttk.Label(coord_frame, text="Z:").grid(row=0, column=2, sticky='w', padx=2)
        self.z_var = tk.StringVar()
        ttk.Entry(coord_frame, textvariable=self.z_var, width=12).grid(row=0, column=3, padx=2)

        self.coord_btn = ttk.Button(coord_frame, text="确认坐标", command=self.on_confirm_coord)
        self.coord_btn.grid(row=0, column=4, padx=4)

        self.coord_status = ttk.Label(coord_frame, text="未设置", foreground=DarkTheme.FG_DIM)
        self.coord_status.grid(row=0, column=5, padx=8)

        # 搜索附近古城按钮
        self.search_btn = ttk.Button(coord_frame, text="搜索附近古城", command=self.on_search_cities)
        self.search_btn.grid(row=0, column=6, padx=(16, 4))

        # --- 筛选行 ---
        filter_frame = ttk.LabelFrame(top, text="战利品筛选（可选，留空显示全部。附魔书右键选择附魔种类和等级）", padding=8)
        filter_frame.pack(fill='x', pady=(0, 4))

        # 左侧：可选物品
        left_filter = ttk.Frame(filter_frame)
        left_filter.grid(row=0, column=0, sticky='nsew', padx=2)
        ttk.Label(left_filter, text="可选物品:").grid(row=0, column=0, columnspan=2)
        self.item_var = tk.StringVar()
        self.item_combo = ttk.Combobox(left_filter, textvariable=self.item_var,
                                       values=sorted(self.loot_items.values()),
                                       state='readonly', width=28)
        self.item_combo.grid(row=1, column=0, padx=2)
        self.add_btn = ttk.Button(left_filter, text="→", command=self.on_add_item, width=3)
        self.add_btn.grid(row=1, column=1, padx=2, pady=2)

        # 中间：按钮列
        mid_filter = ttk.Frame(filter_frame)
        mid_filter.grid(row=0, column=1, sticky='nsew', padx=2)
        self.del_btn = ttk.Button(mid_filter, text="←", command=self.on_del_item, width=3)
        self.del_btn.grid(row=1, column=0, padx=2, pady=2)
        self.clear_btn = ttk.Button(mid_filter, text="清空", command=self.on_clear_items)
        self.clear_btn.grid(row=2, column=0, padx=2, pady=2)

        # 右侧：已选物品
        right_filter = ttk.Frame(filter_frame)
        right_filter.grid(row=0, column=2, sticky='nsew', padx=2)
        ttk.Label(right_filter, text="已选筛选（右键附魔书选择附魔）:").grid(row=0, column=0)
        self.selected_listbox = tk.Listbox(right_filter, height=4, width=28,
                                          bg=DarkTheme.BG_LIST, fg=DarkTheme.FG,
                                          selectbackground=DarkTheme.TABLE_SEL,
                                          selectforeground=DarkTheme.FG_BRIGHT,
                                          relief='flat', highlightthickness=1,
                                          highlightbackground=DarkTheme.BORDER,
                                          highlightcolor=DarkTheme.ACCENT)
        self.selected_listbox.grid(row=1, column=0, padx=2)
        # 右键菜单
        self.selected_listbox.bind('<Button-3>', self._on_listbox_rightclick)

        filter_frame.grid_columnconfigure(0, weight=1)
        filter_frame.grid_columnconfigure(2, weight=1)

        # === Notebook（标签页） ===
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=4)

        # --- Tab 1: 单古城模式 ---
        tab_single = ttk.Frame(self.notebook)
        self.notebook.add(tab_single, text="单古城模式")

        # 运行行
        run_frame1 = ttk.Frame(tab_single)
        run_frame1.pack(fill='x', pady=(2, 4))
        self.run_btn = ttk.Button(run_frame1, text="开始预测", command=self.on_run, state='disabled')
        self.run_btn.pack(side='left')
        self.export_single_btn = ttk.Button(run_frame1, text="导出 Xaero 路径点",
                                            command=self.on_export_xaero_single)
        self.export_single_btn.pack(side='left', padx=(16, 0))
        ttk.Label(run_frame1, text="  （先搜索附近古城选中一个，或直接用玩家坐标预测）",
                  foreground=DarkTheme.FG_DIM).pack(side='left')

        # 结果树
        result_frame1 = ttk.LabelFrame(tab_single, text="结果", padding=4)
        result_frame1.pack(fill='both', expand=True)
        self.tree = self._create_results_tree(result_frame1, 'single')
        self._last_results_single = []

        # --- Tab 2: 大范围寻找 ---
        tab_range = ttk.Frame(self.notebook)
        self.notebook.add(tab_range, text="大范围寻找")

        # 范围输入 + 运行按钮
        range_frame = ttk.Frame(tab_range)
        range_frame.pack(fill='x', pady=(2, 4))
        ttk.Label(range_frame, text="搜索半径（区块）:").pack(side='left')
        self.range_var = tk.StringVar(value="1000")
        ttk.Entry(range_frame, textvariable=self.range_var, width=10).pack(side='left', padx=4)
        ttk.Label(range_frame, text=f"  （默认 1000 区块 = {1000*16} 方块半径）",
                  foreground=DarkTheme.FG_DIM).pack(side='left')
        self.run_range_btn = ttk.Button(range_frame, text="开始大范围预测",
                                        command=self.on_run_range, state='disabled')
        self.run_range_btn.pack(side='left', padx=(16, 4))
        self.export_range_btn = ttk.Button(range_frame, text="导出 Xaero 路径点",
                                           command=self.on_export_xaero_range)
        self.export_range_btn.pack(side='left', padx=(4, 0))
        self._last_results_range = []

        # 结果树
        result_frame2 = ttk.LabelFrame(tab_range, text="结果（双击查看详情）", padding=4)
        result_frame2.pack(fill='both', expand=True)
        self.tree_range = self._create_results_tree(result_frame2, 'range')

    def _create_results_tree(self, parent, tree_id):
        """创建结果表格，返回 Treeview 实例。tree_id='single' 或 'range'"""
        result_inner = ttk.Frame(parent)
        result_inner.pack(fill='both', expand=True)

        columns = ('idx', 'x', 'y', 'z', 'seed', 'loot_table', 'items')
        tree = ttk.Treeview(result_inner, columns=columns, show='headings', height=25)
        if tree_id == 'single':
            tree.bind('<Double-1>', self._on_tree_double_click_single)
            tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        else:
            tree.bind('<Double-1>', self._on_tree_double_click_range)

        tree.heading('idx', text='#')
        tree.heading('x', text='X')
        tree.heading('y', text='Y')
        tree.heading('z', text='Z')
        tree.heading('seed', text='LootTableSeed')
        tree.heading('loot_table', text='战利品表')
        tree.heading('items', text='物品 (双击查看详情)')

        tree.column('idx', width=40, anchor='center')
        tree.column('x', width=70, anchor='center')
        tree.column('y', width=50, anchor='center')
        tree.column('z', width=70, anchor='center')
        tree.column('seed', width=180, anchor='w')
        tree.column('loot_table', width=100, anchor='w')
        tree.column('items', width=350, anchor='w')

        tree_scroll = ttk.Scrollbar(result_inner, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll_h = ttk.Scrollbar(result_inner, orient='horizontal', command=tree.xview)
        tree.configure(xscrollcommand=tree_scroll_h.set)
        tree.pack(side='left', fill='both', expand=True)
        tree_scroll.pack(side='right', fill='y')
        tree_scroll_h.pack(side='bottom', fill='x')
        return tree

    # ========================================================
    # 日志
    # ========================================================
    def log(self, msg):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', str(msg) + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
        self.root.update_idletasks()

    # ========================================================
    # 附魔书右键选择（对话框方案，比 tk.Menu 更可靠）
    # ========================================================
    def _on_listbox_rightclick(self, event):
        """右键点击已选物品列表 — 如果是附魔书，弹出选择对话框"""
        idx = self.selected_listbox.nearest(event.y)
        if idx < 0 or idx >= self.selected_listbox.size():
            return
        # 选中该项
        self.selected_listbox.selection_clear(0, 'end')
        self.selected_listbox.selection_set(idx)
        self.selected_listbox.activate(idx)

        if idx >= len(self.selected_items):
            return
        internal = self.selected_items[idx]
        if not internal.startswith('enchanted_book:'):
            return

        parts = internal.split(':')
        ench_type = parts[1] if len(parts) > 1 else 'any'
        level = parts[2] if len(parts) > 2 else 'any'

        if ench_type == 'any':
            self._show_ench_type_dialog(idx)
        else:
            self._show_ench_level_dialog(idx, ench_type, level)

    def _show_ench_type_dialog(self, idx):
        """弹出附魔种类选择对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("选择附魔种类")
        dialog.geometry("300x500")
        dialog.configure(bg=DarkTheme.BG)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        ttk.Label(dialog, text="请选择附魔种类（左键双击选中）:").pack(pady=(8, 4))

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill='both', expand=True, padx=8, pady=4)

        listbox = tk.Listbox(list_frame, height=20, width=32,
                             bg=DarkTheme.BG_LIST, fg=DarkTheme.FG,
                             selectbackground=DarkTheme.TABLE_SEL,
                             selectforeground=DarkTheme.FG_BRIGHT,
                             relief='flat', highlightthickness=1,
                             highlightbackground=DarkTheme.BORDER,
                             highlightcolor=DarkTheme.ACCENT,
                             font=('Microsoft YaHei', 10))
        listbox.pack(side='left', fill='both', expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')

        ench_ids = sorted(self.available_enchants.keys(),
                          key=lambda e: self.available_enchants[e]['name'])
        for ench_id in ench_ids:
            info = self.available_enchants[ench_id]
            listbox.insert('end', f'{info["name"]}（最高{ROMAN[info["max_level"]-1]}）')

        def on_confirm():
            sel = listbox.curselection()
            if not sel:
                return
            ench_id = ench_ids[sel[0]]
            dialog.destroy()
            self._select_ench_type(idx, ench_id)

        listbox.bind('<Double-Button-1>', lambda e: on_confirm())

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill='x', pady=(4, 8), padx=8)
        ttk.Button(btn_frame, text="确定", command=on_confirm).pack(side='left', padx=4)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side='right', padx=4)

        # 居中到主窗口
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        listbox.focus_set()

    def _show_ench_level_dialog(self, idx, ench_type, current_level):
        """弹出附魔等级选择对话框"""
        ench_info = self.available_enchants.get(f'minecraft:{ench_type}')
        if not ench_info:
            return
        max_lv = ench_info['max_level']
        ench_name = ench_info['name']

        num_levels = max_lv + 1  # 各等级 + 任意等级
        listbox_height = min(num_levels, 10)

        dialog = tk.Toplevel(self.root)
        dialog.title(f"选择{ench_name}附魔等级")
        dialog.configure(bg=DarkTheme.BG)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        ttk.Label(dialog, text=f"{ench_name}（最高 {ROMAN[max_lv-1]}）").pack(pady=(8, 4))

        listbox = tk.Listbox(dialog, height=listbox_height, width=24,
                             bg=DarkTheme.BG_LIST, fg=DarkTheme.FG,
                             selectbackground=DarkTheme.TABLE_SEL,
                             selectforeground=DarkTheme.FG_BRIGHT,
                             relief='flat', highlightthickness=1,
                             highlightbackground=DarkTheme.BORDER,
                             highlightcolor=DarkTheme.ACCENT,
                             font=('Microsoft YaHei', 10))
        listbox.pack(padx=8, pady=4)

        levels = []
        for lv in range(1, max_lv + 1):
            prefix = '✓ ' if current_level == str(lv) else '  '
            listbox.insert('end', f'{prefix}等级 {ROMAN[lv-1]}')
            levels.append(lv)
        prefix_any = '✓ ' if current_level == 'any' else '  '
        listbox.insert('end', f'{prefix_any}任意等级')
        levels.append('any')

        def on_confirm():
            sel = listbox.curselection()
            if not sel:
                return
            level = levels[sel[0]]
            dialog.destroy()
            self._select_ench_level(idx, level)

        listbox.bind('<Double-Button-1>', lambda e: on_confirm())

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill='x', pady=4, padx=8)
        ttk.Button(btn_frame, text="确定", command=on_confirm).pack(side='left', padx=4)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side='right', padx=4)

        ttk.Button(dialog, text="← 更换附魔种类",
                   command=lambda: [dialog.destroy(), self._show_ench_type_dialog(idx)]).pack(pady=(4, 8))

        # 居中到主窗口
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        listbox.focus_set()

    def _select_ench_type(self, idx, ench_id):
        """选择附魔类型 — 更新内部名和显示名"""
        if idx < 0 or idx >= len(self.selected_items):
            return
        ench_key = ench_id.replace('minecraft:', '')
        new_internal = f'enchanted_book:{ench_key}:any'
        self.selected_items[idx] = new_internal
        self.selected_listbox.delete(idx)
        self.selected_listbox.insert(idx, self._ench_book_display(new_internal))
        self._save_config()

    def _select_ench_level(self, idx, level):
        """选择附魔等级 — 更新内部名和显示名"""
        if idx < 0 or idx >= len(self.selected_items):
            return
        internal = self.selected_items[idx]
        parts = internal.split(':')
        ench_type = parts[1] if len(parts) > 1 else 'any'
        if ench_type == 'any':
            return
        lv_str = str(level) if level != 'any' else 'any'
        new_internal = f'enchanted_book:{ench_type}:{lv_str}'
        self.selected_items[idx] = new_internal
        self.selected_listbox.delete(idx)
        self.selected_listbox.insert(idx, self._ench_book_display(new_internal))
        self._save_config()

    # ========================================================
    # 事件处理
    # ========================================================
    def on_browse_world(self):
        d = filedialog.askdirectory(title="选择世界存档目录（包含 region/ 的目录）")
        if d:
            self.world_dir_var.set(d)

    def _on_world_dir_change(self):
        self.world_dir = self.world_dir_var.get().strip() or None
        self._save_config()

    def on_confirm_seed(self):
        raw = self.seed_var.get().strip()
        if not raw:
            messagebox.showwarning("输入", "请输入世界种子。")
            return
        try:
            seed = int(raw)
            self.world_seed = seed
            self.seed_status.config(text=f"已确认: {seed}", foreground=DarkTheme.SUCCESS)
            self.log(f"世界种子已确认: {seed}")
            self._check_ready()
            self._save_config()
        except ValueError:
            messagebox.showerror("错误", "种子必须是整数。")

    def on_confirm_coord(self):
        x_raw = self.x_var.get().strip()
        z_raw = self.z_var.get().strip()
        if not x_raw or not z_raw:
            messagebox.showwarning("输入", "请输入 X 和 Z 坐标。")
            return
        try:
            self.player_x = int(x_raw)
            self.player_z = int(z_raw)
            chunk_x = math.floor(self.player_x / 16)
            chunk_z = math.floor(self.player_z / 16)
            self.coord_status.config(
                text=f"方块({self.player_x}, {self.player_z}) → 区块({chunk_x}, {chunk_z})",
                foreground=DarkTheme.SUCCESS)
            self.log(f"坐标已确认: 方块({self.player_x}, {self.player_z}) → 区块({chunk_x}, {chunk_z})")
            self._check_ready()
            self._save_config()
        except ValueError:
            messagebox.showerror("错误", "坐标必须是整数。")

    def on_search_cities(self):
        """搜索附近的远古古城候选位置（含 cubiomes biome 验证）"""
        if self.world_seed is None:
            messagebox.showwarning("提示", "请先确认世界种子。")
            return
        if self.player_x is None:
            messagebox.showwarning("提示", "请先确认玩家坐标。")
            return

        self.notebook.select(0)  # 切换到单古城 tab
        self.log(f"\n搜索附近古城（种子={self.world_seed}, 中心=({self.player_x}, {self.player_z})）...")
        self.status_var.set("搜索中...")

        def do_search():
            candidates = acp.find_nearby_ancient_cities(
                self.world_seed, self.player_x, self.player_z, search_radius_chunks=200)

            cubiomes_lib = _load_cubiomes()
            if cubiomes_lib is not None:
                self.root.after(0, lambda: self.log(
                    f"cubiomes 已加载，验证生物群系（{len(candidates)} 个候选）..."))
                MC_VERSION = 38  # MC_26_2_S8 = MC 26.1.2
                cubiomes_lib.cubiomes_init(MC_VERSION, self.world_seed)

                MAX = 200
                results_arr = (ctypes.c_int * (MAX * 6))()
                count = cubiomes_lib.cubiomes_find_ancient_cities(
                    self.world_seed, self.player_x, self.player_z, 200,
                    results_arr, MAX)

                cities = []
                for i in range(count):
                    bx = results_arr[i*6+0]
                    bz = results_arr[i*6+1]
                    cx = results_arr[i*6+2]
                    cz = results_arr[i*6+3]
                    rot = results_arr[i*6+4]
                    start = results_arr[i*6+5]
                    dist = int(math.sqrt((bx - self.player_x)**2 + (bz - self.player_z)**2))
                    cities.append({
                        'block_x': bx, 'block_z': bz,
                        'chunk_x': cx, 'chunk_z': cz,
                        'rotation': rot, 'start': start,
                        'distance': dist,
                    })
                cities.sort(key=lambda c: c['distance'])
                self.root.after(0, lambda: self.log(
                    f"  biome 验证后: {len(cities)} 个确认古城"))
            else:
                cities = candidates
                self.root.after(0, lambda: self.log(
                    f"  未找到 cubiomes 库，跳过 biome 验证"))
                self.root.after(0, lambda: self.log(
                    f"  提示: 编译 cubiomes_wrapper.dll 可启用 biome 验证"))

            self.city_candidates = cities

            def update_ui():
                self.tree.delete(*self.tree.get_children())
                verified_tag = "（biome 已验证）" if cubiomes_lib else "（未验证生物群系，部分候选可能不会生成）"
                self.log(f"找到 {len(cities)} 个古城{verified_tag}:")
                for i, c in enumerate(cities):
                    rot_cn = ROT_CN[c['rotation']]
                    self.tree.insert('', 'end', iid=f'city_{i}',
                        values=(
                            i + 1,
                            c['block_x'], -27, c['block_z'],
                            f"rot={c['rotation']} start={c['start']}",
                            f"city_center_{c['start']}",
                            f"距离={c['distance']}m {rot_cn}",
                        ))
                    self.log(f"  [{i+1}] 方块({c['block_x']},{c['block_z']}) "
                             f"区块({c['chunk_x']},{c['chunk_z']}) "
                             f"旋转={rot_cn} 起始={c['start']} 距离={c['distance']}m")
                # 自动选中最近的古城
                if cities:
                    self.tree.selection_set('city_0')
                    self.tree.focus('city_0')
                    self.selected_city_idx = 0
                    self.log(f"已自动选中最近古城 #1 ({cities[0]['distance']}m)")
                    self.log(f"点击「开始预测」将使用此古城坐标。")
                self.status_var.set(f"找到 {len(cities)} 个古城{verified_tag}")

            self.root.after(0, update_ui)

        threading.Thread(target=do_search, daemon=True).start()

    def _on_tree_select(self, event):
        """用户在表格中选择了一行"""
        sel = self.tree.selection()
        if sel and sel[0].startswith('city_'):
            self.selected_city_idx = int(sel[0].replace('city_', ''))
            if self.city_candidates and self.selected_city_idx < len(self.city_candidates):
                c = self.city_candidates[self.selected_city_idx]
                self.log(f"已选择古城 #{self.selected_city_idx+1}: "
                         f"方块({c['block_x']},{c['block_z']}) 距离={c['distance']}m")

    def _check_ready(self):
        if self.world_seed is not None and self.player_x is not None:
            self.run_btn.config(state='normal')
            self.run_range_btn.config(state='normal')

    def on_add_item(self):
        sel = self.item_combo.get()
        if not sel:
            return
        for internal, display in self.loot_items.items():
            if display == sel:
                if internal not in self.selected_items:
                    self.selected_items.append(internal)
                    self.selected_listbox.insert('end', display)
                break
        self._save_config()

    def on_del_item(self):
        idx = self.selected_listbox.curselection()
        if not idx:
            return
        idx = idx[0]
        if idx < len(self.selected_items):
            self.selected_items.pop(idx)
        self.selected_listbox.delete(idx)
        self._save_config()

    def on_clear_items(self):
        self.selected_listbox.delete(0, 'end')
        self.selected_items.clear()
        self._save_config()

    def on_run(self):
        if self.is_running:
            return
        self.is_running = True
        self.run_btn.config(state='disabled', text="预测中...")
        self.status_var.set("正在预测...")
        self.tree.delete(*self.tree.get_children())
        self.notebook.select(0)  # 确保在单古城 tab
        threading.Thread(target=self._run_prediction, daemon=True).start()

    def _run_prediction(self):
        try:
            seed = self.world_seed

            # 优先使用选中的古城坐标
            if (self.selected_city_idx is not None and
                self.city_candidates and
                self.selected_city_idx < len(self.city_candidates)):
                city = self.city_candidates[self.selected_city_idx]
                chunk_x = city['chunk_x']
                chunk_z = city['chunk_z']
                self.root.after(0, lambda: self.log(
                    f"\n使用选中古城 #{self.selected_city_idx+1}: "
                    f"方块({city['block_x']},{city['block_z']}) 区块({chunk_x},{chunk_z})"))
            else:
                chunk_x = math.floor(self.player_x / 16)
                chunk_z = math.floor(self.player_z / 16)
                self.root.after(0, lambda: self.log(
                    f"\n使用玩家坐标: 区块({chunk_x},{chunk_z})"))

            target_set = set(self.selected_items) if self.selected_items else None

            # Step 1: Jigsaw
            self.root.after(0, lambda: self.log(f"[1/4] 模拟拼图放置，区块({chunk_x}, {chunk_z})..."))
            pieces = acp.simulate_ancient_city(seed, chunk_x, chunk_z)
            self.root.after(0, lambda: self.log(f"      放置了 {len(pieces)} 个结构块"))

            # Step 2: 从模拟结果提取箱子（不需要 region 文件）
            self.root.after(0, lambda: self.log("[2/4] 从结构模板提取箱子坐标..."))
            chests = acp.extract_chests_from_pieces(pieces, seed)
            self.root.after(0, lambda: self.log(f"      找到 {len(chests)} 个箱子"))

            # Step 3: LootTableSeed 已在 step 2 中按 chunk 分配
            self.root.after(0, lambda: self.log("[3/4] LootTableSeed 已分配（按区块 RNG）"))

            # Step 4: 模拟战利品
            self.root.after(0, lambda: self.log("[4/4] 模拟战利品内容..."))
            results = []
            for chest in chests:
                lt_name = chest['loot_table'].replace('minecraft:chests/', '')
                lt_path = os.path.join(acp.LOOT_TABLE_DIR, lt_name + '.json')
                if not os.path.exists(lt_path):
                    continue
                slot_items, final_items = acp.simulate_loot(lt_path, chest['seed'])
                chest['items'] = final_items
                if target_set:
                    # 匹配目标物品（支持附魔书三级匹配）
                    found = []
                    for item in final_items:
                        item_id = item[0]
                        item_count = item[1]
                        item_enchs = item[2] if len(item) > 2 else []

                        # 检查普通物品
                        if item_id in target_set:
                            found.append((item_id, item_count))
                            continue

                        # 检查附魔书
                        if item_id == 'minecraft:enchanted_book' and item_enchs:
                            for ench in item_enchs:
                                ench_id = ench['id'].replace('minecraft:', '')
                                ench_level = ench['level']
                                # 精确匹配: enchanted_book:swift_sneak:3
                                specific = f'enchanted_book:{ench_id}:{ench_level}'
                                # 附魔类型匹配: enchanted_book:swift_sneak:any
                                any_level = f'enchanted_book:{ench_id}:any'
                                # 任意附魔书: enchanted_book:any:any
                                any_ench = 'enchanted_book:any:any'

                                if specific in target_set or \
                                   any_level in target_set or \
                                   any_ench in target_set:
                                    found.append((item_id, item_count))
                                    break
                    if found:
                        results.append({
                            'x': chest['x'], 'y': chest['y'], 'z': chest['z'],
                            'seed': chest['seed'], 'loot_table': lt_name,
                            'items': found,
                        })
                else:
                    found = [(item[0], item[1]) for item in final_items]
                    if found:
                        results.append({
                            'x': chest['x'], 'y': chest['y'], 'z': chest['z'],
                            'seed': chest['seed'], 'loot_table': lt_name,
                            'items': found,
                        })

            self.root.after(0, lambda: self.log(f"\n预测完成。"))
            self.root.after(0, lambda: self.log(f"  结构块: {len(pieces)}"))
            self.root.after(0, lambda: self.log(f"  箱子: {len(chests)}"))
            self.root.after(0, lambda: self.log(f"  命中箱子: {len(results)}"))

            self.root.after(0, lambda: self._populate_results(results, self.tree))
            self.root.after(0, lambda: self._log_item_stats(results))
            self.root.after(0, lambda: self.status_var.set(
                f"预测完成 — {len(results)} 个命中箱子"))

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            self.root.after(0, lambda: self.log(f"错误:\n{err}"))
            self.root.after(0, lambda: self.status_var.set("发生错误，请查看日志"))
        finally:
            self.root.after(0, lambda: self._reset_run_button())

    def _populate_results(self, results, tree):
        """填充单古城结果表格"""
        tree.delete(*tree.get_children())
        self._last_results_single = results
        for i, r in enumerate(results):
            items_str = ', '.join(
                f"{name.replace('minecraft:','')}x{count}" for name, count in r['items'])
            lt_display = r['loot_table'].replace('ancient_city_', 'ac_')
            tree.insert('', 'end', values=(
                i + 1,
                r['x'], r['y'], r['z'],
                r['seed'],
                lt_display,
                items_str,
            ))

    def _on_tree_double_click_single(self, event):
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        values = self.tree.item(item_id, 'values')
        if not values:
            return
        results = getattr(self, '_last_results_single', [])
        if not results:
            self.log("[提示] 请先运行预测，再双击结果行查看详情")
            return
        self._show_chest_detail_by_values(values, results)

    def _on_tree_double_click_range(self, event):
        item_id = self.tree_range.identify_row(event.y)
        if not item_id:
            return
        values = self.tree_range.item(item_id, 'values')
        if not values:
            return
        results = getattr(self, '_last_results_range', [])
        if not results:
            self.log("[提示] 请先运行预测，再双击结果行查看详情")
            return
        self._show_chest_detail_by_values(values, results)

    def _show_chest_detail_by_values(self, values, results):
        """根据行 values 弹出完整物品列表"""
        try:
            idx = int(values[0]) - 1
        except (ValueError, IndexError):
            return
        if idx < 0 or idx >= len(results):
            return
        r = results[idx]
        lines = []
        lines.append(f"坐标: ({r['x']}, {r['y']}, {r['z']})")
        lines.append(f"战利品表: {r['loot_table']}")
        lines.append(f"Seed: {r['seed']}")
        lines.append("-" * 40)
        for name, count in r['items']:
            lines.append(f"  {name.replace('minecraft:','')}  x{count}")
        if not r['items']:
            lines.append("  (空)")
        top = tk.Toplevel(self.root)
        top.title(f"箱子 #{idx+1} 详细信息")
        top.geometry("400x500")
        top.transient(self.root)
        top.grab_set()
        txt = scrolledtext.ScrolledText(top, font=('Consolas', 10), wrap='word')
        txt.pack(fill='both', expand=True, padx=8, pady=8)
        txt.insert('1.0', '\n'.join(lines))
        txt.config(state='disabled')
        ttk.Button(top, text="关闭", command=top.destroy).pack(pady=8)

    # ========================================================
    # 大范围寻找
    # ========================================================
    def on_run_range(self):
        if self.is_running:
            return
        if self.world_seed is None:
            messagebox.showwarning("提示", "请先确认世界种子。")
            return
        if self.player_x is None or self.player_z is None:
            messagebox.showwarning("提示", "请先确认玩家坐标。")
            return
        try:
            radius = int(self.range_var.get().strip())
            if radius < 1 or radius > 10000:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "搜索半径请输入 1-10000 的整数。")
            return

        self.is_running = True
        self.run_range_btn.config(state='disabled', text="大范围预测中...")
        self.status_var.set("正在大范围搜索古城...")
        self.tree_range.delete(*self.tree_range.get_children())
        self._range_results = []
        # 切换到大范围 tab
        self.notebook.select(1)
        threading.Thread(target=self._run_range_prediction, args=(radius,), daemon=True).start()

    def _run_range_prediction(self, radius):
        try:
            seed = self.world_seed
            px, pz = self.player_x, self.player_z

            self.root.after(0, lambda: self.log(
                f"\n=== 大范围寻找 ==="))
            self.root.after(0, lambda: self.log(
                f"种子: {seed}, 中心: ({px},{pz}), 半径: {radius} 区块"))

            # Step 1: 查找范围内所有古城
            self.root.after(0, lambda: self.log("[1/3] 搜索范围内古城..."))
            cubiomes_lib = _load_cubiomes()

            if cubiomes_lib:
                MAX = 5000
                results_arr = (ctypes.c_int * (MAX * 6))()
                count = cubiomes_lib.cubiomes_find_ancient_cities(
                    seed, px, pz, radius, results_arr, MAX)
                cities = []
                for i in range(count):
                    bx = results_arr[i*6]
                    bz = results_arr[i*6+1]
                    cx = results_arr[i*6+2]
                    cz = results_arr[i*6+3]
                    rot = results_arr[i*6+4]
                    start = results_arr[i*6+5]
                    dist = int(math.sqrt((bx - px)**2 + (bz - pz)**2))
                    cities.append({
                        'block_x': bx, 'block_z': bz,
                        'chunk_x': cx, 'chunk_z': cz,
                        'rotation': rot, 'start': start,
                        'distance': dist,
                    })
                cities.sort(key=lambda c: c['distance'])
                self.root.after(0, lambda: self.log(
                    f"  找到 {len(cities)} 个已验证古城（cubiomes biome 验证）"))
            else:
                # Python 回退方案
                candidates = acp.find_nearby_ancient_cities(
                    seed, px, pz, search_radius_chunks=radius)
                cities = []
                for c in candidates:
                    bx, bz = c[0], c[1]
                    cx, cz = c[4], c[5]
                    rot, start = c[2], c[3]
                    dist = int(math.sqrt((bx - px)**2 + (bz - pz)**2))
                    cities.append({
                        'block_x': bx, 'block_z': bz,
                        'chunk_x': cx, 'chunk_z': cz,
                        'rotation': rot, 'start': start,
                        'distance': dist,
                    })
                cities.sort(key=lambda c: c['distance'])
                self.root.after(0, lambda: self.log(
                    f"  找到 {len(cities)} 个候选古城（未验证生物群系）"))

            if not cities:
                self.root.after(0, lambda: self.log("  范围内未找到古城"))
                self.root.after(0, lambda: self.status_var.set("未找到古城"))
                return

            # Step 2: 对每个古城模拟战利品
            self.root.after(0, lambda: self.log(
                f"[2/3] 模拟 {len(cities)} 个古城的战利品..."))
            target_set = set(self.selected_items) if self.selected_items else None
            all_results = []

            for ci, city in enumerate(cities):
                cx = city['chunk_x']
                cz = city['chunk_z']
                dist = city['distance']
                self.root.after(0, lambda ci=ci, dist=dist, cx=cx, cz=cz: self.log(
                    f"  [{ci+1}/{len(cities)}] 古城 ({cx},{cz}) 距离 {dist}m"))

                try:
                    pieces = acp.simulate_ancient_city(seed, cx, cz)
                    chests = acp.extract_chests_from_pieces(pieces, seed)

                    for chest in chests:
                        lt_name = chest['loot_table'].replace('minecraft:chests/', '')
                        lt_path = os.path.join(acp.LOOT_TABLE_DIR, lt_name + '.json')
                        if not os.path.exists(lt_path):
                            continue
                        slot_items, final_items = acp.simulate_loot(lt_path, chest['seed'])

                        if target_set:
                            found = []
                            for item in final_items:
                                item_id = item[0]
                                item_count = item[1]
                                item_enchs = item[2] if len(item) > 2 else []
                                if item_id in target_set:
                                    found.append((item_id, item_count))
                                    continue
                                if item_id == 'minecraft:enchanted_book' and item_enchs:
                                    for ench in item_enchs:
                                        ench_id = ench['id'].replace('minecraft:', '')
                                        ench_level = ench['level']
                                        specific = f'enchanted_book:{ench_id}:{ench_level}'
                                        any_level = f'enchanted_book:{ench_id}:any'
                                        any_ench = 'enchanted_book:any:any'
                                        if specific in target_set or \
                                           any_level in target_set or \
                                           any_ench in target_set:
                                            found.append((item_id, item_count))
                                            break
                            if found:
                                all_results.append({
                                    'x': chest['x'], 'y': chest['y'], 'z': chest['z'],
                                    'seed': chest['seed'], 'loot_table': lt_name,
                                    'items': found,
                                    'city_idx': ci, 'city_dist': dist,
                                })
                        else:
                            found = [(item[0], item[1]) for item in final_items]
                            if found:
                                all_results.append({
                                    'x': chest['x'], 'y': chest['y'], 'z': chest['z'],
                                    'seed': chest['seed'], 'loot_table': lt_name,
                                    'items': found,
                                    'city_idx': ci, 'city_dist': dist,
                                })
                except Exception as e:
                    self.root.after(0, lambda cx=cx, cz=cz, e=e: self.log(
                        f"    古城 ({cx},{cz}) 出错: {e}"))

            # Step 3: 显示结果
            self.root.after(0, lambda: self.log(
                f"[3/3] 完成。共 {len(cities)} 个古城, {len(all_results)} 个命中箱子"))

            self.root.after(0, lambda: self._populate_range_results(all_results))
            self.root.after(0, lambda: self._log_item_stats(all_results))
            self.root.after(0, lambda: self.status_var.set(
                f"大范围预测完成 — {len(all_results)} 个命中箱子"))

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            self.root.after(0, lambda: self.log(f"错误:\n{err}"))
            self.root.after(0, lambda: self.status_var.set("发生错误，请查看日志"))
        finally:
            self.root.after(0, lambda: self._reset_run_button())

    def _populate_range_results(self, results):
        """填充大范围结果表格"""
        self._last_results_range = results
        self.tree_range.delete(*self.tree_range.get_children())
        for i, r in enumerate(results):
            items_str = ', '.join(
                f"{name.replace('minecraft:','')}x{count}" for name, count in r['items'])
            lt_display = r['loot_table'].replace('ancient_city_', 'ac_')
            # 在 loot_table 列加上距离信息
            lt_with_dist = f"{lt_display} ({r.get('city_dist', '?')}m)"
            self.tree_range.insert('', 'end', values=(
                i + 1,
                r['x'], r['y'], r['z'],
                r['seed'],
                lt_with_dist,
                items_str,
            ))

    def _log_item_stats(self, results):
        """日志末尾输出物品统计"""
        if not results:
            return
        item_stats = {}  # {short_name: {'total': int, 'chests': set()}}
        for r in results:
            for name, count in r['items']:
                short = name.replace('minecraft:', '')
                if short not in item_stats:
                    item_stats[short] = {'total': 0, 'chests': set()}
                item_stats[short]['total'] += count
                item_stats[short]['chests'].add((r['x'], r['y'], r['z']))

        self.log("=== 物品统计 ===")
        for item, data in sorted(item_stats.items(), key=lambda x: -x[1]['total']):
            self.log(f"  {item}: {data['total']} 个 ({len(data['chests'])} 个箱子)")
        self.log(f"  合计: {sum(d['total'] for d in item_stats.values())} 个物品, "
                 f"{len(results)} 个箱子")

    def on_export_xaero_single(self):
        self._export_xaero(getattr(self, '_last_results_single', []))

    def on_export_xaero_range(self):
        self._export_xaero(getattr(self, '_last_results_range', []))

    def _export_xaero(self, results):
        """导出 Xaero's Minimap 路径点文件"""
        if not results:
            messagebox.showwarning("提示", "没有可导出的结果，请先运行预测。")
            return

        path = filedialog.asksaveasfilename(
            title="导出 Xaero 路径点文件",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile="ancient_city_waypoints.txt")
        if not path:
            return

        lines = [
            "sets:gui.xaero_default:xaeroplus.gui.pearl_waypoints_set",
            "#",
            "#waypoint:name:initials:x:y:z:color:disabled:type:set:rotate_on_tp:tp_yaw:visibility_type:destination",
            "#",
        ]
        color = 11  # 黄色
        for i, r in enumerate(results):
            x, y, z = r['x'], r['y'], r['z']
            item_names = [name.replace('minecraft:', '') for name, _ in r['items'][:3]]
            name = '+'.join(item_names) if item_names else f'chest{i+1}'
            if len(name) > 20:
                name = name[:20]
            initials = ''.join([n[0] for n in item_names[:2]]) if len(item_names) >= 2 else name[0]
            y_val = str(y) if y is not None else '~'
            lines.append(
                f"waypoint:{name}:{initials}:{x}:{y_val}:{z}:{color}:false:0:"
                f"gui.xaero_default:false:0:0:false")

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            self.log(f"已导出 {len(results)} 个路径点到: {path}")
            messagebox.showinfo("导出成功", f"已导出 {len(results)} 个路径点\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _reset_run_button(self):
        self.is_running = False
        self.run_btn.config(state='normal', text="开始预测")
        self.run_range_btn.config(state='normal', text="开始大范围预测")


# ============================================================
# 入口
# ============================================================
def main():
    root = tk.Tk()
    apply_dark_theme(root)
    app = AncientCityGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
