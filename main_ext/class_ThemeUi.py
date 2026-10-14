# SPDX-License-Identifier: MPL-2.0
import os, json
from core.app_paths import APP_ROOT, USER_STATE_DIR

# cache path
_USER_DIR = USER_STATE_DIR
_THEME_FILE = os.path.join(_USER_DIR, "theme.json")
_LEGACY_USER_DIR = os.path.join(APP_ROOT, "user")


_SETTINGS_FILE = os.path.join(_USER_DIR, "settings.json")


def _migrate_legacy_user_state():

    for filename in ("theme.json", "settings.json"):
        source = os.path.join(_LEGACY_USER_DIR, filename)
        target = os.path.join(_USER_DIR, filename)
        if os.path.isfile(target) or not os.path.isfile(source):
            continue
        try:
            os.makedirs(_USER_DIR, exist_ok=True)
            os.replace(source, target)
        except OSError:
            pass


_PALETTES = {

    # DEFAULT (original blue-dark)
    "default": {
        "label":       "Default",
        "accent_hex":  "#4d8ef0",   # swatch preview color

        # backgrounds
        "BG":          "#12131a",
        "BG2":         "#1a1b22",
        "BG3":         "#1a1b25",
        "BG4":         "#1e1f26",
        "BG_SELECTED": "#1e2030",
        "BG_DISABLED": "#14151c",
        "BG_DARK":     "#0f1018",
        "BG_INPUT":    "#16171f",
        "BG_MODAL":    "#22242e",
        "BG_TREE":     "#1a1b22",
        "BG_SCROLL":   "#2a2c3a",

        # text
        "FG":           "#c9d1d9",
        "FG_BRIGHT":    "#e8e8e8",
        "FG_LABEL":     "#dddddd",
        "FG2":          "#aaaaaa",
        "FG3":          "#888888",
        "FG_HOVER":     "#cccccc",
        "FG_BLUE_MUTED":"#8a95ad",
        "FG_BLUE":      "#7eb8f7",
        "FG_BLUE_LIGHT":"#aad4ff",
        "FG_MUTED":     "#666c80",
        "FG_DESC":      "#555e70",
        "FG_DIM":       "#4a5068",
        "FG_SYNC":      "#8faadc",
        "FG_DISABLED":  "#3a3f50",
        "FG_DISABLED2": "#2e3140",

        # accent rgb tuple (for rgba() helpers)
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#6fb7ff",
        "_gauge_warn":   "#e5a03c",
        "_gauge_danger": "#e06c75",
        "_accent_rgb": (77, 142, 240),
        # dark theme — white borders, black shadows
        "_border_rgb": (255, 255, 255),
        "_shadow_rgb": (0, 0, 0),
        "_is_light":   False,
    },

    # DARK (near-black charcoal)
    "dark": {
        "label":       "Dark",
        "accent_hex":  "#6c6c7a",

        "BG":          "#0a0a0d",
        "BG2":         "#111115",
        "BG3":         "#13131a",
        "BG4":         "#161619",
        "BG_SELECTED": "#1c1c26",
        "BG_DISABLED": "#0d0d11",
        "BG_DARK":     "#050507",
        "BG_INPUT":    "#0f0f14",
        "BG_MODAL":    "#18181e",
        "BG_TREE":     "#111115",
        "BG_SCROLL":   "#212128",

        "FG":           "#b8bcc8",
        "FG_BRIGHT":    "#d8dde8",
        "FG_LABEL":     "#c8ccd8",
        "FG2":          "#888898",
        "FG3":          "#656575",
        "FG_HOVER":     "#aaaabc",
        "FG_BLUE_MUTED":"#7a8298",
        "FG_BLUE":      "#7eb8f7",
        "FG_BLUE_LIGHT":"#aad4ff",
        "FG_MUTED":     "#505060",
        "FG_DESC":      "#404050",
        "FG_DIM":       "#303040",
        "FG_SYNC":      "#7090c0",
        "FG_DISABLED":  "#282830",
        "FG_DISABLED2": "#202028",

        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#8fa8c0",
        "_gauge_warn":   "#c9954e",
        "_gauge_danger": "#c96a72",
        "_accent_rgb": (108, 108, 122),
        "_border_rgb": (255, 255, 255),
        "_shadow_rgb": (0, 0, 0),
        "_is_light":   False,
    },

    # LIGHT (white/paper)
    "light": {
        "label":       "Light",
        "accent_hex":  "#4d8ef0",

        "BG":          "#f0f2f5",
        "BG2":         "#ffffff",
        "BG3":         "#f7f8fa",
        "BG4":         "#eef0f4",
        "BG_SELECTED": "#dde8fc",
        "BG_DISABLED": "#e8eaed",
        "BG_DARK":     "#e0e3e8",
        "BG_INPUT":    "#ffffff",
        "BG_MODAL":    "#ffffff",
        "BG_TREE":     "#f7f8fa",
        "BG_SCROLL":   "#d0d4dc",

        "FG":           "#1c1e26",
        "FG_BRIGHT":    "#0a0c12",
        "FG_LABEL":     "#2a2d38",
        "FG2":          "#4a4f60",
        "FG3":          "#6a7080",
        "FG_HOVER":     "#1a1c24",
        "FG_BLUE_MUTED":"#4a5878",
        "FG_BLUE":      "#2260c8",
        "FG_BLUE_LIGHT":"#1a50b8",
        "FG_MUTED":     "#8a90a0",
        "FG_DESC":      "#9a9fae",
        "FG_DIM":       "#aab0be",
        "FG_SYNC":      "#3c68b8",
        "FG_DISABLED":  "#c0c4cc",
        "FG_DISABLED2": "#d0d4da",

        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#2f86d8",
        "_gauge_warn":   "#c2620a",
        "_gauge_danger": "#dc2626",
        "_accent_rgb": (77, 142, 240),
        "_border_rgb": (0, 0, 0),
        "_shadow_rgb": (0, 0, 0),
        "_is_light":   True,
    },

    # DARK GREEN (Supabase)
    # BG: near-black navy seperti supabase.com, accent hijau #3ECF8E khas Supabase
    "dark_green": {
        "label":       "Dark Green",
        "accent_hex":  "#3ecf8e",

        "BG":          "#0f0f0f",
        "BG2":         "#1a1a1a",
        "BG3":         "#171717",
        "BG4":         "#1c1c1c",
        "BG_SELECTED": "#1a2e22",
        "BG_DISABLED": "#0c0c0c",
        "BG_DARK":     "#080808",
        "BG_INPUT":    "#1a1a1a",
        "BG_MODAL":    "#1c1c1c",
        "BG_TREE":     "#121212",
        "BG_SCROLL":   "#2a2a2a",

        "FG":           "#ededed",
        "FG_BRIGHT":    "#ffffff",
        "FG_LABEL":     "#e0e0e0",
        "FG2":          "#a0a0a0",
        "FG3":          "#6e6e6e",
        "FG_HOVER":     "#f0f0f0",
        "FG_BLUE_MUTED":"#3ecf8e",
        "FG_BLUE":      "#3ecf8e",
        "FG_BLUE_LIGHT":"#6edfae",
        "FG_MUTED":     "#666666",
        "FG_DESC":      "#555555",
        "FG_DIM":       "#444444",
        "FG_SYNC":      "#3ecf8e",
        "FG_DISABLED":  "#2a2a2a",
        "FG_DISABLED2": "#222222",

        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#4ade9c",
        "_gauge_warn":   "#e8b13a",
        "_gauge_danger": "#ef5f5f",
        "_accent_rgb": (62, 207, 142),
        "_border_rgb": (255, 255, 255),
        "_shadow_rgb": (0, 0, 0),
        "_is_light":   False,
    },
    # DARK PURPLE (Dracula-ish)
    "dark_purple": {
        "label":       "Purple",
        "accent_hex":  "#bd93f9",
        "BG":          "#1e1a2e", "BG2": "#252336", "BG3": "#221f33",
        "BG4":         "#28253c", "BG_SELECTED": "#312d4a", "BG_DISABLED": "#18162a",
        "BG_DARK":     "#14122a", "BG_INPUT": "#252336", "BG_MODAL": "#2a2740",
        "BG_TREE":     "#1e1a2e", "BG_SCROLL": "#3a3558",
        "FG":           "#f8f8f2", "FG_BRIGHT": "#ffffff", "FG_LABEL": "#f0f0ea",
        "FG2":          "#b0a8d0", "FG3":  "#7a7098", "FG_HOVER": "#ffffff",
        "FG_BLUE_MUTED":"#bd93f9", "FG_BLUE": "#bd93f9", "FG_BLUE_LIGHT": "#d4b8ff",
        "FG_MUTED":     "#6a6288", "FG_DESC": "#585075", "FG_DIM": "#443c60",
        "FG_SYNC":      "#bd93f9", "FG_DISABLED": "#2e2a44", "FG_DISABLED2": "#252038",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#50fa7b",
        "_gauge_warn":   "#ffb86c",
        "_gauge_danger": "#ff5555",
        "_accent_rgb": (189, 147, 249), "_border_rgb": (255, 255, 255),
        "_shadow_rgb": (0, 0, 0), "_is_light": False,
    },

    # DARK RED (Crimson)
    "dark_red": {
        "label":       "Crimson",
        "accent_hex":  "#e05c6a",
        "BG":          "#1a1014", "BG2": "#231419", "BG3": "#20121a",
        "BG4":         "#261519", "BG_SELECTED": "#3a1a22", "BG_DISABLED": "#140c10",
        "BG_DARK":     "#0e080c", "BG_INPUT": "#231419", "BG_MODAL": "#281820",
        "BG_TREE":     "#1a1014", "BG_SCROLL": "#3a2028",
        "FG":           "#f0dde0", "FG_BRIGHT": "#ffffff", "FG_LABEL": "#eedde2",
        "FG2":          "#c09098", "FG3":  "#886068", "FG_HOVER": "#ffffff",
        "FG_BLUE_MUTED":"#e05c6a", "FG_BLUE": "#e05c6a", "FG_BLUE_LIGHT": "#f08090",
        "FG_MUTED":     "#704848", "FG_DESC": "#5e3a3a", "FG_DIM": "#4a2c2c",
        "FG_SYNC":      "#e05c6a", "FG_DISABLED": "#2e1818", "FG_DISABLED2": "#240f0f",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#e5b8bd",
        "_gauge_warn":   "#f0913f",
        "_gauge_danger": "#ff3b47",
        "_accent_rgb": (224, 92, 106), "_border_rgb": (255, 255, 255),
        "_shadow_rgb": (0, 0, 0), "_is_light": False,
    },

    # DARK ORANGE (Monokai)
    "dark_orange": {
        "label":       "Monokai",
        "accent_hex":  "#fd971f",
        "BG":          "#1e1e1e", "BG2": "#272822", "BG3": "#232320",
        "BG4":         "#2b2b28", "BG_SELECTED": "#3a3830", "BG_DISABLED": "#191917",
        "BG_DARK":     "#141414", "BG_INPUT": "#272822", "BG_MODAL": "#2e2d28",
        "BG_TREE":     "#1e1e1e", "BG_SCROLL": "#404038",
        "FG":           "#f8f8f2", "FG_BRIGHT": "#ffffff", "FG_LABEL": "#f0f0e8",
        "FG2":          "#c0b898", "FG3":  "#807860", "FG_HOVER": "#ffffff",
        "FG_BLUE_MUTED":"#fd971f", "FG_BLUE": "#fd971f", "FG_BLUE_LIGHT": "#ffb84d",
        "FG_MUTED":     "#706848", "FG_DESC": "#5c5438", "FG_DIM": "#484030",
        "FG_SYNC":      "#fd971f", "FG_DISABLED": "#303028", "FG_DISABLED2": "#262620",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#a6e22e",
        "_gauge_warn":   "#fd971f",
        "_gauge_danger": "#f92672",
        "_accent_rgb": (253, 151, 31), "_border_rgb": (255, 255, 255),
        "_shadow_rgb": (0, 0, 0), "_is_light": False,
    },

    # DARK OCEAN (Nord-ish)
    "dark_ocean": {
        "label":       "Ocean",
        "accent_hex":  "#88c0d0",
        "BG":          "#1e2430", "BG2": "#242d3a", "BG3": "#212a38",
        "BG4":         "#273040", "BG_SELECTED": "#2e3f55", "BG_DISABLED": "#18202c",
        "BG_DARK":     "#141c28", "BG_INPUT": "#242d3a", "BG_MODAL": "#2a3445",
        "BG_TREE":     "#1e2430", "BG_SCROLL": "#354560",
        "FG":           "#d8dee9", "FG_BRIGHT": "#eceff4", "FG_LABEL": "#e0e5ef",
        "FG2":          "#9aa8bf", "FG3":  "#6a7888", "FG_HOVER": "#eceff4",
        "FG_BLUE_MUTED":"#88c0d0", "FG_BLUE": "#88c0d0", "FG_BLUE_LIGHT": "#a8d8e8",
        "FG_MUTED":     "#4a5870", "FG_DESC": "#3c4a5e", "FG_DIM": "#2e3a4e",
        "FG_SYNC":      "#88c0d0", "FG_DISABLED": "#28303e", "FG_DISABLED2": "#202830",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#a3be8c",
        "_gauge_warn":   "#d08770",
        "_gauge_danger": "#bf616a",
        "_accent_rgb": (136, 192, 208), "_border_rgb": (255, 255, 255),
        "_shadow_rgb": (0, 0, 0), "_is_light": False,
    },

    # LIGHT WARM (Warm Paper)
    "light_warm": {
        "label":       "Warm",
        "accent_hex":  "#c4612a",
        "BG":          "#faf7f2", "BG2": "#ffffff", "BG3": "#f5f2ec",
        "BG4":         "#ede8e0", "BG_SELECTED": "#f5e6d8", "BG_DISABLED": "#eeeae3",
        "BG_DARK":     "#e5e0d8", "BG_INPUT": "#ffffff", "BG_MODAL": "#ffffff",
        "BG_TREE":     "#f5f2ec", "BG_SCROLL": "#cec8be",
        "FG":           "#2c2420", "FG_BRIGHT": "#100c08", "FG_LABEL": "#3a2e28",
        "FG2":          "#5a4a40", "FG3":  "#7a6858", "FG_HOVER": "#1a1210",
        "FG_BLUE_MUTED":"#8a4a30", "FG_BLUE": "#c4612a", "FG_BLUE_LIGHT": "#a85020",
        "FG_MUTED":     "#9a8878", "FG_DESC": "#a89888", "FG_DIM": "#baa898",
        "FG_SYNC":      "#c4612a", "FG_DISABLED": "#ccc4bc", "FG_DISABLED2": "#d8d0c8",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#4b7a4e",
        "_gauge_warn":   "#c05f10",
        "_gauge_danger": "#b3261e",
        "_accent_rgb": (196, 97, 42), "_border_rgb": (0, 0, 0),
        "_shadow_rgb": (0, 0, 0), "_is_light": True,
    },

    # LIGHT ROSE (Pinkish)
    "light_rose": {
        "label":       "Rose",
        "accent_hex":  "#c0446a",
        "BG":          "#fff5f7", "BG2": "#ffffff", "BG3": "#fdf0f3",
        "BG4":         "#f8eaee", "BG_SELECTED": "#fce0e8", "BG_DISABLED": "#f0eaec",
        "BG_DARK":     "#ecdde2", "BG_INPUT": "#ffffff", "BG_MODAL": "#ffffff",
        "BG_TREE":     "#fdf0f3", "BG_SCROLL": "#d8c0c8",
        "FG":           "#2a1820", "FG_BRIGHT": "#0e080e", "FG_LABEL": "#3a2030",
        "FG2":          "#5a3848", "FG3":  "#7a5068", "FG_HOVER": "#180810",
        "FG_BLUE_MUTED":"#8a3050", "FG_BLUE": "#c0446a", "FG_BLUE_LIGHT": "#a03058",
        "FG_MUTED":     "#9a8090", "FG_DESC": "#aa90a0", "FG_DIM": "#c0a8b5",
        "FG_SYNC":      "#c0446a", "FG_DISABLED": "#d8c8ce", "FG_DISABLED2": "#e8d8de",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#1f8a6d",
        "_gauge_warn":   "#d4691f",
        "_gauge_danger": "#b3123a",
        "_accent_rgb": (192, 68, 106), "_border_rgb": (0, 0, 0),
        "_shadow_rgb": (0, 0, 0), "_is_light": True,
    },

    # LIGHT GREEN (Mint)
    "light_green": {
        "label":       "Mint",
        "accent_hex":  "#2a8a5a",
        "BG":          "#f2faf5", "BG2": "#ffffff", "BG3": "#edf7f1",
        "BG4":         "#e5f0ea", "BG_SELECTED": "#d8ede3", "BG_DISABLED": "#e8f0eb",
        "BG_DARK":     "#d8e8de", "BG_INPUT": "#ffffff", "BG_MODAL": "#ffffff",
        "BG_TREE":     "#edf7f1", "BG_SCROLL": "#b8d0c3",
        "FG":           "#18281e", "FG_BRIGHT": "#080f0a", "FG_LABEL": "#253022",
        "FG2":          "#405840", "FG3":  "#607860", "FG_HOVER": "#101810",
        "FG_BLUE_MUTED":"#286040", "FG_BLUE": "#2a8a5a", "FG_BLUE_LIGHT": "#207848",
        "FG_MUTED":     "#789080", "FG_DESC": "#88a090", "FG_DIM": "#a0b8a8",
        "FG_SYNC":      "#2a8a5a", "FG_DISABLED": "#c0d8c8", "FG_DISABLED2": "#d0e5d8",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#2a8a5a",
        "_gauge_warn":   "#c2740c",
        "_gauge_danger": "#c0392b",
        "_accent_rgb": (42, 138, 90), "_border_rgb": (0, 0, 0),
        "_shadow_rgb": (0, 0, 0), "_is_light": True,
    },

    # LIGHT PURPLE (Lavender)
    "light_purple": {
        "label":       "Lavender",
        "accent_hex":  "#6040c0",
        "BG":          "#f5f3ff", "BG2": "#ffffff", "BG3": "#f0eeff",
        "BG4":         "#ebe8f8", "BG_SELECTED": "#e0dbff", "BG_DISABLED": "#eceaf8",
        "BG_DARK":     "#dcd8f0", "BG_INPUT": "#ffffff", "BG_MODAL": "#ffffff",
        "BG_TREE":     "#f0eeff", "BG_SCROLL": "#c0b8e0",
        "FG":           "#1e1838", "FG_BRIGHT": "#0a0820", "FG_LABEL": "#2a2248",
        "FG2":          "#483878", "FG3":  "#685898", "FG_HOVER": "#100c28",
        "FG_BLUE_MUTED":"#4a3090", "FG_BLUE": "#6040c0", "FG_BLUE_LIGHT": "#5030a8",
        "FG_MUTED":     "#9088b8", "FG_DESC": "#a098c8", "FG_DIM": "#b8b0d8",
        "FG_SYNC":      "#6040c0", "FG_DISABLED": "#c8c0e8", "FG_DISABLED2": "#d8d0f0",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#5a4ad0",
        "_gauge_warn":   "#c26a10",
        "_gauge_danger": "#c0203f",
        "_accent_rgb": (96, 64, 192), "_border_rgb": (0, 0, 0),
        "_shadow_rgb": (0, 0, 0), "_is_light": True,
    },

    # LIGHT GRAY (Stone)
    "light_gray": {
        "label":       "Stone",
        "accent_hex":  "#4a5568",
        "BG":          "#f7f7f8", "BG2": "#ffffff", "BG3": "#f2f2f4",
        "BG4":         "#eaeaed", "BG_SELECTED": "#e2e4ea", "BG_DISABLED": "#ebebee",
        "BG_DARK":     "#dcdce0", "BG_INPUT": "#ffffff", "BG_MODAL": "#ffffff",
        "BG_TREE":     "#f2f2f4", "BG_SCROLL": "#c8c8d0",
        "FG":           "#1a1c22", "FG_BRIGHT": "#080a0e", "FG_LABEL": "#282b34",
        "FG2":          "#4a4f5e", "FG3":  "#6a6f7e", "FG_HOVER": "#10121a",
        "FG_BLUE_MUTED":"#38404e", "FG_BLUE": "#4a5568", "FG_BLUE_LIGHT": "#38455a",
        "FG_MUTED":     "#8a8f9e", "FG_DESC": "#9a9fae", "FG_DIM": "#aeb3c0",
        "FG_SYNC":      "#4a5568", "FG_DISABLED": "#c8cad0", "FG_DISABLED2": "#d8dadf",
        # gauge pemakaian ruang: lega → mulai penuh → hampir habis
        "_gauge_safe":   "#4a6fa5",
        "_gauge_warn":   "#b45309",
        "_gauge_danger": "#b91c1c",
        "_accent_rgb": (74, 85, 104), "_border_rgb": (0, 0, 0),
        "_shadow_rgb": (0, 0, 0), "_is_light": True,
    },
}


class _Theme:
    def __init__(self):
        self._name    = "default"
        self._palette = {}
        self._listeners = []          # callables → called after set()
        self._blur_enabled = True     # blur card/overlay effect — bisa dimatikan biar ringan
        self._effect_level = "medium"
        self._effect_level_listeners = []
        self._animations_enabled = True  # fade/slide transition — bisa dimatikan biar instan
        self._project_drive_filters = {}  # drive yang dimatikan; drive baru default aktif
        self._tree_view_mode = "tree"    # "tree" | "single_dir" — mode tampilan FileTree
        self._view_mode_listeners = []   # callables → called after set_tree_view_mode()
        self._apply_palette(_PALETTES["default"])

    # public API
    @property
    def name(self) -> str:
        return self._name

    @property
    def blur_enabled(self) -> bool:
        return self._blur_enabled

    def set_blur_enabled(self, enabled: bool):
        # Compatibility for existing integrations; the UI uses effect levels.
        self.set_effect_level("medium" if enabled else "low")

    @property
    def effect_level(self) -> str:
        return "low" if not self._blur_enabled else ("high" if self._effect_level == "high" else "medium")

    @property
    def live_blur_enabled(self) -> bool:
        return self.effect_level == "high"

    def set_effect_level(self, level: str):
        level = str(level).casefold()
        if level not in ("low", "medium", "high") or level == self.effect_level:
            return
        self._effect_level = level
        self._blur_enabled = level != "low"
        self._save_settings()
        for callback in list(self._effect_level_listeners):
            try:
                callback(level)
            except RuntimeError:
                self.remove_effect_level_listener(callback)

    def on_effect_level_change(self, callback):
        if callback not in self._effect_level_listeners:
            self._effect_level_listeners.append(callback)

    def remove_effect_level_listener(self, callback):
        if callback in self._effect_level_listeners:
            self._effect_level_listeners.remove(callback)

    @property
    def animations_enabled(self) -> bool:
        return self._animations_enabled

    def set_animations_enabled(self, enabled: bool):

        enabled = bool(enabled)
        if enabled == self._animations_enabled:
            return
        self._animations_enabled = enabled
        self._save_settings()

    @staticmethod
    def _project_drive_key(drive: str) -> str | None:
        value = str(drive or "").strip().upper()
        if value.startswith("WSL:") and value[4:].strip():
            return value
        if value and "A" <= value[0] <= "Z" and (len(value) == 1 or value[1] == ":"):
            return f"{value[0]}:"
        return None

    def project_drive_enabled(self, drive: str) -> bool:
        key = self._project_drive_key(drive)
        return key is None or key not in self._project_drive_filters

    def set_project_drive_enabled(self, drive: str, enabled: bool):
        key = self._project_drive_key(drive)
        if key is None:
            return
        old = self.project_drive_enabled(key)
        if enabled:
            self._project_drive_filters.pop(key, None)
        else:
            self._project_drive_filters[key] = False
        if old != bool(enabled):
            self._save_settings()

    @property
    def tree_view_mode(self) -> str:
        return self._tree_view_mode

    def set_tree_view_mode(self, mode: str):

        mode = "single_dir" if mode == "single_dir" else "tree"
        if mode == self._tree_view_mode:
            return
        self._tree_view_mode = mode
        self._save_settings()
        for cb in list(self._view_mode_listeners):
            try:
                cb(mode)
            except Exception:
                pass

    def on_view_mode_change(self, callback):

        if callback not in self._view_mode_listeners:
            self._view_mode_listeners.append(callback)

    def remove_view_mode_listener(self, callback):
        if callback in self._view_mode_listeners:
            self._view_mode_listeners.remove(callback)

    def panel_alpha(self, preferred: int) -> int:

        return 255 if not self._blur_enabled else preferred

    def panel_rgba(self, r: int, g: int, b: int, preferred_alpha: int) -> str:

        return f"rgba({r},{g},{b},{self.panel_alpha(preferred_alpha)})"

    @property
    def is_light(self) -> bool:
        return self._palette.get("_is_light", False)

    @property
    def accent_hex(self) -> str:
        return self._palette.get("accent_hex", "#4d8ef0")

    @property
    def safe_hex(self) -> str:

        return self._palette.get(
            "_gauge_safe",
            "#2f86d8" if self._palette.get("_is_light", False) else "#6fb7ff")

    @property
    def warning_hex(self) -> str:

        return self._palette.get(
            "_gauge_warn",
            "#c2620a" if self._palette.get("_is_light", False) else "#e5a03c")

    @property
    def danger_hex(self) -> str:

        return self._palette.get(
            "_gauge_danger",
            "#dc2626" if self._palette.get("_is_light", False) else "#e06c75")

    # ambang toleransi gauge — dipakai bareng oleh semua indikator kapasitas
    GAUGE_WARN_PCT   = 70   # >= 70% : mulai sesak  → oranye
    GAUGE_DANGER_PCT = 88   # >= 88% : hampir habis → merah

    def gauge_hex(self, pct: float) -> str:

        try:
            pct = float(pct)
        except (TypeError, ValueError):
            pct = 0.0
        if pct >= self.GAUGE_DANGER_PCT:
            return self.danger_hex
        if pct >= self.GAUGE_WARN_PCT:
            return self.warning_hex
        return self.safe_hex

    def palettes(self) -> dict:

        return {k: {"label": v["label"], "accent_hex": v["accent_hex"], "BG": v["BG"]}
                for k, v in _PALETTES.items()}

    def set(self, name: str):

        if name not in _PALETTES:
            return
        self._name = name
        self._apply_palette(_PALETTES[name])
        self._save()
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def load(self):

        _migrate_legacy_user_state()
        try:
            with open(_THEME_FILE, "r") as f:
                saved = json.load(f).get("theme", "default")
            if saved in _PALETTES:
                self._name = saved
                self._apply_palette(_PALETTES[saved])
        except Exception:
            pass

        try:
            with open(_SETTINGS_FILE, "r") as f:
                data = json.load(f)
            level = data.get("effect_level")
            if level not in ("low", "medium", "high"):
                level = "medium" if data.get("blur_enabled", True) else "low"
            self._effect_level = level
            self._blur_enabled = level != "low"
            self._animations_enabled = bool(data.get("animations_enabled", True))
            saved_drives = data.get("project_drive_filters", {})
            self._project_drive_filters = {}
            if isinstance(saved_drives, dict):
                for drive, enabled in saved_drives.items():
                    key = self._project_drive_key(drive)
                    if key is not None and enabled is False:
                        self._project_drive_filters[key] = False
            _tv_mode = data.get("tree_view_mode", "tree")
            self._tree_view_mode = _tv_mode if _tv_mode in ("tree", "single_dir") else "tree"
        except Exception:
            pass

    def on_change(self, callback):

        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    # rgba helpers (use current accent or fixed color)
    def accent(self, a: float) -> str:
        r, g, b = self._palette["_accent_rgb"]
        return f"rgba({r},{g},{b},{a})"

    def white(self, a: float) -> str:
        return f"rgba(255,255,255,{a})"

    def black(self, a: float) -> str:
        return f"rgba(0,0,0,{a})"

    def border_color(self, a: float) -> str:
        r, g, b = self._palette["_border_rgb"]
        return f"rgba({r},{g},{b},{a})"

    # fixed-color helpers (always same RGB, alpha varies)
    def blue(self, a: float)    -> str: return f"rgba(77,142,240,{a})"
    def red(self, a: float)     -> str: return f"rgba(220,70,70,{a})"
    def red_alt(self, a: float) -> str: return f"rgba(218,54,51,{a})"
    def green(self, a: float)   -> str: return f"rgba(46,160,67,{a})"
    def orange(self, a: float)  -> str: return f"rgba(200,120,58,{a})"
    def purple(self, a: float)  -> str: return f"rgba(176,122,224,{a})"
    def shadow(self, a: float)  -> str: return f"rgba(0,0,0,{a})"

    # preset combos (recomputed on each access via __getattr__)
    @property
    def BORDER(self)           -> str: return self.border_color(0.09)
    @property
    def BORDER_LIGHT(self)     -> str: return self.border_color(0.07)
    @property
    def BORDER_MID(self)       -> str: return self.border_color(0.10)
    @property
    def BORDER_STRONG(self)    -> str: return self.border_color(0.12)
    @property
    def BORDER_BLUE(self)      -> str: return self.accent(0.25)
    @property
    def BORDER_BLUE_FOCUS(self)-> str: return self.accent(0.55)
    @property
    def BORDER_BLUE_ACTIVE(self)->str: return self.accent(0.38)

    @property
    def BTN_BASE(self)         -> str: return self.border_color(0.05)
    @property
    def BTN_HOVER(self)        -> str: return self.border_color(0.09)
    @property
    def BTN_PRESSED(self)      -> str: return self.border_color(0.13)

    @property
    def BTN_BLUE_BASE(self)    -> str: return self.accent(0.18)
    @property
    def BTN_BLUE_HOVER(self)   -> str: return self.accent(0.30)
    @property
    def BTN_BLUE_PRESS(self)   -> str: return self.accent(0.48)

    @property
    def BTN_RED_BASE(self)     -> str: return self.red(0.14)
    @property
    def BTN_RED_HOVER(self)    -> str: return self.red(0.28)
    @property
    def BTN_RED_BORDER(self)   -> str: return self.red(0.38)
    @property
    def BTN_RED_PRESS(self)    -> str: return self.red(0.55)

    @property
    def ROW_HOVER(self)        -> str: return self.border_color(0.04)
    @property
    def ROW_SELECTED(self)     -> str: return self.accent(0.12)
    @property
    def ROW_RUNNING(self)      -> str: return self.accent(0.10)
    @property
    def ROW_DONE(self)         -> str: return self.green(0.07)
    @property
    def ROW_ERROR(self)        -> str: return self.red_alt(0.07)

    @property
    def GLOW_BLUE(self)        -> str: return self.accent(0.18)
    @property
    def GLOW_GREEN(self)       -> str: return self.green(0.24)
    @property
    def GLOW_RED(self)         -> str: return self.red_alt(0.24)
    @property
    def SHADOW(self)           -> str: return "rgba(0,0,0,0.35)"
    @property
    def SHADOW_DEEP(self)      -> str: return "rgba(0,0,0,0.55)"

    @property
    def SCROLL_HANDLE(self)    -> str: return self.BG_SCROLL
    @property
    def SCROLL_HOVER(self)     -> str: return self.accent(0.45)

    @property
    def DIVIDER(self)          -> str: return self.border_color(0.07)
    @property
    def DIVIDER_MID(self)      -> str: return self.border_color(0.06)

    # static accent aliases (kept for backwards compat with old T.BLUE)
    BLUE         = "#4d8ef0"
    BLUE_BRIGHT  = "#4da6ff"
    BLUE_MID     = "#3c78dc"
    BLUE_LIGHT   = "#64a0ff"
    GREEN        = "#4fbe6c"
    GREEN_BTN    = "#4db860"
    GREEN_SAVE   = "#3dba6f"
    GREEN_STATUS = "#6dba8a"
    GREEN_BADGE  = "#7fcf9a"
    GREEN_DONE   = "#2ea043"
    RED          = "#e05c52"
    RED_STATUS   = "#c0614a"
    RED_SYNC     = "#e06c75"
    RED_BTN      = "#e05555"
    RED_LIGHT    = "#f08080"
    RED_LIGHTER  = "#ff9999"
    RED_BADGE    = "#f97066"
    RED_STRONG   = "#ff6b6b"
    RED_FLASH    = "#ff4444"
    RED_ALT      = "#da3633"
    ORANGE       = "#c8783a"
    ORANGE_BTN   = "#f08030"
    PURPLE       = "#9d6fd4"
    GRAY_DARK    = "#2d2d2d"
    GRAY         = "#444"
    GRAY_MID     = "#555"
    GRAY_MID2    = "#666"
    GRAY_LIGHT   = "#9a9a9a"
    GRAY_LIGHTER = "#e5e5e5"

    # internal
    def _apply_palette(self, p: dict):
        self._palette = p
        for key, val in p.items():
            if not key.startswith("_") and key != "label" and key != "accent_hex":
                setattr(self, key, val)

    def _save(self):
        try:
            _migrate_legacy_user_state()
            os.makedirs(_USER_DIR, exist_ok=True)
            with open(_THEME_FILE, "w") as f:
                json.dump({"theme": self._name}, f)
        except Exception:
            pass

    def _save_settings(self):
        try:
            _migrate_legacy_user_state()
            os.makedirs(_USER_DIR, exist_ok=True)
            with open(_SETTINGS_FILE, "w") as f:
                json.dump({
                    "blur_enabled": self._blur_enabled,
                    "effect_level": self.effect_level,
                    "animations_enabled": self._animations_enabled,
                    "project_drive_filters": self._project_drive_filters,
                    "tree_view_mode": self._tree_view_mode,
                }, f)
        except Exception:
            pass

    # global stylesheet for AppUI
    def app_stylesheet(self) -> str:

        ac  = self.accent
        bc  = self.border_color
        btn_text  = self.FG_BLUE_MUTED
        btn_hover = self.FG_HOVER
        scroll_h  = self.SCROLL_HANDLE

        return f"""
QWidget {{
    background-color: {self.BG};
    color: {self.FG};
    font-family: 'Poppins';
    font-size: 13px;
}}
QLineEdit {{
    background-color: {self.BG3};
    border: 1px solid {bc(0.10)};
    border-radius: 7px;
    padding: 6px 10px;
    color: {self.FG};
}}
QLineEdit:focus {{
    border: 1px solid {ac(0.55)};
    background-color: {self.BG_SELECTED};
}}
QLineEdit:disabled {{
    color: {self.FG_DISABLED};
    background-color: {self.BG_DISABLED};
    border-color: {bc(0.04)};
}}
QRadioButton {{
    spacing: 8px;
    color: {self.FG};
}}
QRadioButton::indicator {{
    width: 14px; height: 14px;
    border-radius: 7px;
    border: 1px solid {bc(0.20)};
    background: transparent;
}}
QRadioButton::indicator:checked {{
    background-color: {ac(0.85)};
    border: 1px solid {ac(0.70)};
}}
QPushButton {{
    background: {bc(0.05)};
    border: 1px solid {bc(0.09)};
    border-radius: 7px;
    padding: 0 14px;
    height: 30px;
    color: {btn_text};
    font-size: 12px;
}}
QPushButton:hover {{
    background: {bc(0.09)};
    border-color: {bc(0.15)};
    color: {btn_hover};
}}
QPushButton:pressed {{ background: {bc(0.13)}; }}
QPushButton:disabled {{
    color: {self.FG_DISABLED2};
    background: {bc(0.02)};
    border-color: {bc(0.04)};
}}
QPushButton[role="primary"] {{
    background: {ac(0.18)};
    border: 1px solid {ac(0.38)};
    color: {self.FG_BLUE};
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{
    background: {ac(0.30)};
    border-color: {ac(0.55)};
    color: {self.FG_BLUE_LIGHT};
}}
QPushButton[role="primary"]:pressed {{ background: {ac(0.44)}; }}
QPushButton[role="primary"]:disabled {{
    background: {ac(0.07)};
    border-color: {ac(0.12)};
    color: {ac(0.30)};
}}
QScrollBar:vertical {{
    background: transparent; width: 4px; margin: 0; border: none;
}}
QScrollBar::handle:vertical {{
    background: {scroll_h}; border-radius: 2px; min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: {ac(0.45)}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0; border: none; background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent; height: 4px; margin: 0; border: none;
}}
QScrollBar::handle:horizontal {{
    background: {scroll_h}; border-radius: 2px; min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ac(0.45)}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    width: 0; border: none; background: transparent;
}}
"""


# Global singleton
theme = _Theme()
# backwards-compat alias
T = theme
