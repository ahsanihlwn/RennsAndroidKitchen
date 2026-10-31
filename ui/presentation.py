# SPDX-License-Identifier: MPL-2.0
"""State and commands for the native Qt Quick launcher, without QWidget objects.

Persistence and project jobs belong to the application controller. This model
emits commands and accepts replacement data; the visual preview deliberately
does not run commands that could modify the user's projects or settings.
"""
from __future__ import annotations

from urllib.parse import quote

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QColor

from core import ui_lang
from core.flag import APP_NAME, APP_VERSION, edition_label, window_title
from main_ext.class_ThemeUi import _PALETTES, theme, BLUR_RADIUS_DEFAULT, BLUR_RADIUS_MAXIMUM, blur_radius_value
from ui.rows import RowsModel


def color_alpha(value, alpha):
    color = QColor(value)
    color.setAlphaF(alpha)
    return color.name(QColor.HexArgb)


class LauncherModel(QObject):
    changed = Signal()
    colorsChanged = Signal()
    stringsChanged = Signal()
    settingsChanged = Signal()
    profileChanged = Signal()
    locationChanged = Signal()
    busyChanged = Signal()
    startupChanged = Signal()
    deletingPathChanged = Signal()
    panelBorderChanged = Signal()
    pageChanged = Signal()
    projectsChanged = Signal()
    trashChanged = Signal()
    drivesChanged = Signal()
    presentationRequested = Signal(str, "QVariantMap")
    command = Signal(str, "QVariantMap")

    def __init__(self, *, palette="default", language="en", projects=(),
                 profile=None, location="", settings=None, drives=(), parent=None):
        super().__init__(parent)
        self._palette_name = palette if palette in _PALETTES else "default"
        self._language = language if language in ui_lang.SUPPORTED else "en"
        self._projects = [dict(item) for item in projects]
        self._profile = dict(profile or {})
        self._location = location
        self._settings = {"effects": "medium", "blurRadius": BLUR_RADIUS_DEFAULT, "animations": True, "treeMode": "tree",
                          **(settings or {})}
        radius = blur_radius_value(self._settings['blurRadius'])
        self._settings['blurRadius'] = radius if radius is not None else BLUR_RADIUS_DEFAULT
        self._page = "projects"
        self._previous = "projects"
        self._query = ""
        self._trash = []
        self._default_location = ""
        self._busy = False
        self._drives = [dict(row) for row in drives]
        self._excluded_drives = set()
        self._startup_active = False
        self._deleting_path = ""
        self._animate_rows = True
        self._color_cache = None
        self._string_cache = None
        self.appearance_interceptor = None
        self._project_rows = RowsModel(self.projects, self)
        self._trash_rows = RowsModel(parent=self)
        self._drive_rows = RowsModel(self._drives, self)
        self.projectsChanged.connect(self._sync_projects)
        self.trashChanged.connect(self._sync_trash)
        self.drivesChanged.connect(lambda: self._drive_rows.replace(self._drives))
        self.colorsChanged.connect(self.panelBorderChanged.emit)
        self.settingsChanged.connect(self.panelBorderChanged.emit)

    def _sync_projects(self):
        self._project_rows.replace(self.projects, animate=self._animate_rows)

    def _sync_trash(self):
        self._trash_rows.replace(self.trashProjects, animate=self._animate_rows)

    @Property(QObject, constant=True)
    def projectRows(self):
        return self._project_rows

    @Property(QObject, constant=True)
    def trashRows(self):
        return self._trash_rows

    @Property(QObject, constant=True)
    def driveRows(self):
        return self._drive_rows

    @Property(bool, notify=startupChanged)
    def startupActive(self):
        return self._startup_active

    @Property(str, constant=True)
    def splashOutline(self):
        from ui.splash import outline_svg
        return outline_svg()

    def setStartupActive(self, active):
        active = bool(active)
        if active == self._startup_active:
            return
        self._startup_active = active
        self.startupChanged.emit()
        self.changed.emit()

    @Property(bool, notify=projectsChanged)
    def hasProjects(self):
        return bool(self._projects or self._trash)

    @Property(bool, constant=True)
    def privateEnabled(self):
        from core.rennsproject_bridge import private_bundle_enabled
        return private_bundle_enabled()

    @Property(str,constant=True)
    def monospaceFamily(self):
        from PySide6.QtGui import QFontDatabase
        return QFontDatabase.systemFont(QFontDatabase.FixedFont).family()

    @Property(str, notify=deletingPathChanged)
    def deletingPath(self):
        return self._deleting_path

    def setDeletingPath(self, path):
        if path == self._deleting_path:
            return
        self._deleting_path = path
        self.deletingPathChanged.emit()
        self.changed.emit()

    @Property("QVariantMap", constant=True)
    def splashBrand(self):
        from PySide6.QtGui import QIcon
        from icons.handler import icon
        size = QIcon(icon("rennsproject_logo")).pixmap(400,60).size()
        return {"width": round(20*size.width()/max(1,size.height())), "sourceWidth": size.width(), "sourceHeight": size.height()}

    @Property(str, notify=pageChanged)
    def page(self):
        return self._page

    @Slot(str)
    def navigate(self, page):
        if page not in ("projects", "create", "settings") or page == self._page:
            return
        if page == "settings":
            self._previous = self._page
        self._page = page
        self.pageChanged.emit()

    @Slot()
    def back(self):
        self.navigate(self._previous if self._page == "settings" else "projects")

    @Property("QVariantMap", notify=colorsChanged)
    def colors(self):
        if self._color_cache is not None:
            return self._color_cache
        palette = _PALETTES[self._palette_name]
        result = {key: value for key, value in palette.items() if isinstance(value, str)}
        result['treeArrow'] = palette['FG'] if palette.get('_is_light',False) else '#ffffff'
        neutral = QColor(*palette["_border_rgb"]).name()
        result['menuBorder']=color_alpha(neutral,45/255)
        accent = palette["accent_hex"]
        for name, alpha in (("b015", .012), ("b02", .02), ("b03", .03), ("b04", .04), ("b05", .05), ("b06", .06),
                            ("b07", .07), ("b08", .08), ("b09", .09), ("b10", .10),
                            ("b12", .12), ("b13", .13), ("b14", .14), ("b15", .15), ("b16", .16), ("b18", .18), ("b20", .20), ("b22", .22), ("b25", .25), ("b28", .28), ("b30", .30), ("b35", .35), ("b45", .45), ("b50", .50), ("b55", .55), ("b80", .80), ("b045", .045)):
            result[name] = color_alpha(neutral, alpha)
        for name, alpha in (("a05", .05), ("a07", .07), ("a08", .08), ("a12", .12), ("a14", .14), ("a18", .18), ("a20", .20), ("a24", .24), ("a25", .25), ("a28", .28), ("a30", .30), ("a36", .36),
                            ("a35", .35), ("a38", .38), ("a40", .40), ("a42", .42), ("a45", .45),
                            ("a10", .10), ("a15", .15), ("a32", .32), ("a44", .44), ("a48", .48), ("a50", .50), ("a55", .55), ("a60", .60), ("a65", .65), ("a70", .70), ("a80", .80), ("a90", 230/255)):
            result[name] = color_alpha(accent, alpha)
        result["neutralToggle"] = color_alpha(palette["FG_MUTED"], 200/255)
        result["offToggle"] = color_alpha(neutral, 30/255)
        result["placeholder"] = color_alpha(palette["FG"], 128/255)
        result["RED_LIGHT"] = "#f08080"
        result.update(GRAY_MID="#555555", GRAY_MID2="#666666", RED_STRONG="#ff6b6b", RED_FLASH="#ff4444")
        for key in ("PURPLE","RED_LIGHTER","RED_BTN","GREEN_STATUS","RED_STATUS","RED_SYNC"):
            result[key] = getattr(theme,key)
        for alpha in (10,14,18,22,28,32,38,42,48,55,60):
            result["red"+str(alpha)] = color_alpha("#dc4646",alpha/100)
        result["popupPanel"] = color_alpha(palette["BG2"], 120/255)
        result["popupCard"] = "#0affffff"
        self._color_cache = result
        return result

    @Slot(str,result=str)
    def authIcon(self,name):
        from ui.auth_icons import ICONS
        svg=ICONS.get(name,"").replace("{C}",self.colors["FG_MUTED" if name=="eye" else "FG_BLUE"])
        return "data:image/svg+xml;utf8,"+quote(svg,safe="")

    @Property(str, notify=panelBorderChanged)
    def panelBorder(self):
        # One outline policy for foreground panels, including live effect/theme
        # changes. Neutral RGB still comes from the selected palette.
        return self.colors['b05' if self._settings['effects']=='high' else 'b10']

    @Property("QVariantMap", notify=stringsChanged)
    def strings(self):
        if self._string_cache is not None:
            return self._string_cache
        # Use the same translations, without mutating the language singleton.
        result = {key: values.get(self._language, values.get("en", key))
                  for key, values in ui_lang._STRINGS.items()}
        result.update(appName=APP_NAME, appVersion=APP_VERSION, edition=edition_label(),
                      windowTitle=window_title(),
                      themeName=_PALETTES[self._palette_name]["label"],
                      languageName=ui_lang.SUPPORTED[self._language], languageKey=self._language)
        self._string_cache = result
        return result

    @Property("QVariantList", notify=projectsChanged)
    def projects(self):
        query = self._query.casefold().strip()
        rows = [row for row in self._projects if row.get("drive", "").casefold() not in self._excluded_drives
                and (not query or query in " ".join(str(row.get(key, "")) for key in
                ("project_name", "path", "port1", "port2")).casefold())]
        return sorted(rows, key=lambda row: not row.get("pinned", False))

    @Slot(str)
    def search(self, query):
        if self._query != query:
            self._query = query
            self._animate_rows = False
            self.projectsChanged.emit()
            self.trashChanged.emit()
            self._animate_rows = True

    @Slot("QVariantList")
    def replaceProjects(self, rows):
        rows = [dict(row) for row in rows]
        if rows != self._projects:
            self._projects = rows
            self.projectsChanged.emit()

    @Property("QVariantMap", notify=profileChanged)
    def profile(self):
        return self._profile

    @Property(str, notify=locationChanged)
    def location(self):
        return self._location

    @Property(str, notify=locationChanged)
    def defaultLocation(self):
        return self._default_location

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    def setBusy(self, busy):
        busy = bool(busy)
        if busy == self._busy:
            return
        self._busy = busy
        self.busyChanged.emit()
        self.changed.emit()

    def replaceLocation(self, location, default_location):
        if (location, default_location) == (self._location, self._default_location):
            return
        self._location = location
        self._default_location = default_location
        self.locationChanged.emit()
        self.changed.emit()

    def replaceProfile(self, profile):
        if profile == self._profile:
            return
        self._profile = dict(profile)
        self.profileChanged.emit()
        self.changed.emit()

    def refreshPreferences(self, palette, language, settings):
        palette_changed = palette != self._palette_name
        strings_changed = palette_changed or language != self._language
        settings_changed = any(self._settings.get(key) != value for key, value in settings.items())
        if not (palette_changed or strings_changed or settings_changed):
            return
        self._palette_name = palette
        self._language = language
        self._settings.update(settings)
        if palette_changed:
            self._color_cache = None
            self.colorsChanged.emit()
        if strings_changed:
            self._string_cache = None
            self.stringsChanged.emit()
        if settings_changed:
            self.settingsChanged.emit()
        self.changed.emit()

    @Property("QVariantList", notify=trashChanged)
    def trashProjects(self):
        query = self._query.casefold().strip()
        return [row for row in self._trash if not query or query in
                (str(row.get("project_name", "")) + " " + str(row.get("path", ""))).casefold()]

    @Property(int, notify=trashChanged)
    def trashCount(self):
        return len(self._trash)

    def replaceTrash(self, rows):
        rows = [dict(row) for row in rows]
        if rows == self._trash:
            return
        self._trash = rows
        self.trashChanged.emit()
        self.projectsChanged.emit()

    @Property("QVariantList", notify=drivesChanged)
    def drives(self):
        return self._drives

    def replaceDrives(self, rows, excluded=()):
        rows = [dict(row) for row in rows]
        excluded = {key.casefold() for key in excluded}
        if rows != self._drives:
            self._drives = rows
            self.drivesChanged.emit()
        if excluded != self._excluded_drives:
            self._excluded_drives = excluded
            self.projectsChanged.emit()

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self):
        return self._settings

    @Property("QVariantList", constant=True)
    def languages(self):
        return [{"key": key, "label": value} for key, value in ui_lang.SUPPORTED.items()]

    @Property("QVariantList", constant=True)
    def palettes(self):
        return [{"key": key, **value} for key, value in _PALETTES.items()]

    @Property(str, notify=colorsChanged)
    def paletteName(self):
        return self._palette_name

    @Slot(str)
    def choosePalette(self, name):
        if name in _PALETTES and name != self._palette_name:
            def commit():
                self._palette_name = name
                self._color_cache = None
                self._string_cache = None
                self.colorsChanged.emit()
                self.stringsChanged.emit()
                self.changed.emit()
                self.command.emit("theme", {"name": name})
            if self.appearance_interceptor is not None:
                self.appearance_interceptor("theme",commit)
            else:commit()

    @Slot(str)
    def chooseLanguage(self, code):
        if code in ui_lang.SUPPORTED and code != self._language:
            def commit():
                self._language = code
                self._string_cache = None
                self.stringsChanged.emit()
                self.changed.emit()
                self.command.emit("language", {"code": code})
            if self.appearance_interceptor is not None:
                self.appearance_interceptor("language",commit)
            else:commit()

    @Slot(str, "QVariant")
    def chooseSetting(self, key, value):
        allowed = {"effects": ("low", "medium", "high"), "animations": (True, False),
                   "treeMode": ("tree", "single_dir")}
        if key == 'blurRadius':
            value = blur_radius_value(value)
            if value is None:
                return
        elif key not in allowed or value not in allowed[key]:
            return
        if self._settings.get(key) == value:
            return
        self._settings[key] = value
        self.settingsChanged.emit()
        self.changed.emit()
        self.command.emit("setting", {"key": key, "value": value})

    @Property(int, constant=True)
    def blurRadiusMaximum(self):
        return BLUR_RADIUS_MAXIMUM

    @Property(int, notify=settingsChanged)
    def effectiveBlurRadius(self):
        if self._settings['effects'] == 'medium':
            return 18
        return 6 if self._settings['blurRadius'] < 12 else 17

    @Slot(str, "QVariantMap")
    def dispatch(self, command, arguments):
        self.command.emit(command, arguments)

    @Slot(str, str, result=str)
    def icon(self, name, color=""):
        # The QML image provider tints the original artwork; no UI rasterization.
        return "image://rak/" + quote(name, safe="") + "?color=" + quote(color, safe="")
