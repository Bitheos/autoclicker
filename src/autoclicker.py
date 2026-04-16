import objc
import time
import os
import sys
import ctypes
import ctypes.util
from Cocoa import (
    NSApplication, NSObject, NSStatusBar, NSVariableStatusItemLength,
    NSPopover, NSView, NSButton, NSComboBox, NSTextField, NSRect, NSSize,
    NSViewController, NSTimer, NSColor, NSGradient, NSFont,
    NSSegmentedControl, NSImage, NSWorkspace, NSAlert, NSURL,
    NSProcessInfo
)
from PyObjCTools import AppHelper
from Quartz import (
    CGEventCreateMouseEvent, CGEventPost, kCGHIDEventTap,
    kCGEventLeftMouseDown, kCGEventLeftMouseUp,
    kCGEventRightMouseDown, kCGEventRightMouseUp,
    kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGMouseButtonCenter,
    CGEventGetLocation, CGEventCreate,
    kCGMouseButtonLeft, kCGMouseButtonRight
)
from pynput import keyboard


# ---------------------------------------------------------------------------
# RUTA DE RECURSOS
# ---------------------------------------------------------------------------
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# ---------------------------------------------------------------------------
# PERMISOS DE ACCESIBILIDAD (via ctypes, sin segfault en PyInstaller)
# ---------------------------------------------------------------------------
def _load_ax_library():
    lib_path = ctypes.util.find_library("ApplicationServices")
    if not lib_path:
        lib_path = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    try:
        return ctypes.CDLL(lib_path)
    except OSError:
        return None


def check_accessibility_permissions():
    lib = _load_ax_library()
    if lib is None:
        return False
    try:
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception as e:
        print(f"check_accessibility_permissions: {e}")
        return False


def request_accessibility_permissions_prompt():
    try:
        cf_path = ctypes.util.find_library("CoreFoundation") or \
                  "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        cf  = ctypes.CDLL(cf_path)
        ax  = _load_ax_library()
        if ax is None:
            return False

        cf.CFStringCreateWithCString.restype  = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryCreate.restype  = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype  = None

        key_str   = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", 0x08000100)
        true_val  = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        key_cb    = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        val_cb    = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")

        keys_arr   = (ctypes.c_void_p * 1)(key_str)
        values_arr = (ctypes.c_void_p * 1)(ctypes.c_void_p(true_val.value))

        options = cf.CFDictionaryCreate(
            None, keys_arr, values_arr, 1,
            ctypes.addressof(key_cb), ctypes.addressof(val_cb),
        )

        ax.AXIsProcessTrustedWithOptions.restype  = ctypes.c_bool
        ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        result = ax.AXIsProcessTrustedWithOptions(options)

        cf.CFRelease(options)
        cf.CFRelease(key_str)
        return bool(result)
    except Exception as e:
        print(f"request_accessibility_permissions_prompt: {e}")
        return False


# ---------------------------------------------------------------------------
# APP NAP — deshabilitar para que macOS no suspenda el proceso en background
# ---------------------------------------------------------------------------
def disable_app_nap():
    """
    Llama a NSProcessInfo.beginActivityWithOptions:reason: con flags que
    impiden que macOS aplique App Nap, throttling de timers o suspensión
    por inactividad al proceso.

    Sin esto, tras ~2 min en background sin ventanas visibles macOS puede
    congelar los NSTimers y el thread del keyboard listener, haciendo que
    la app parezca muerta aunque el proceso siga en memoria.

    La referencia devuelta DEBE guardarse en self para que el GC no la libere.
    Si se libera, macOS cancela la actividad y vuelve a aplicar App Nap.
    """
    try:
        # Combinación de flags documentados en NSProcessInfo.h:
        # NSActivityBackground          (1 << 8)   — app en background legítimamente activa
        # NSActivityLatencyCritical     (0xFF00000) — timers de alta frecuencia sin throttle
        # NSActivityUserInitiated       (0x00FFFFFF)— iniciado por el usuario, no suspender
        # NSActivityIdleSystemSleepDisabled (1<<20) — no suspender el sistema por esta app
        flags = (1 << 8) | 0xFF00000 | 0x00FFFFFF | (1 << 20)

        activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            flags,
            "Autoclicker runs continuously in background"
        )
        print("App Nap deshabilitado.")
        return activity
    except Exception as e:
        print(f"disable_app_nap error: {e}")
        return None


# ---------------------------------------------------------------------------
# LAUNCH AGENT — persistencia robusta entre reinicios y tras sleep/wake
# ---------------------------------------------------------------------------
LAUNCH_AGENT_LABEL = "com.personal.autoclicker"
LAUNCH_AGENT_PATH  = os.path.expanduser(
    f"~/Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist"
)

def get_app_executable():
    """
    Devuelve la ruta al ejecutable.
    - En .app compilado con PyInstaller: ruta al binario dentro del bundle
    - En desarrollo: ruta al script Python
    """
    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    else:
        return os.path.abspath(sys.argv[0])

def get_uid():
    """UID del usuario actual, necesario para launchctl bootstrap en macOS 12+."""
    import pwd
    return pwd.getpwnam(os.environ.get("USER", os.environ.get("LOGNAME", ""))).pw_uid

def is_launch_agent_installed():
    return os.path.exists(LAUNCH_AGENT_PATH)

def install_launch_agent():
    """
    Crea el plist del Launch Agent con configuración robusta y lo registra
    con launchctl usando la API moderna (bootstrap), compatible con macOS 12+.

    Claves clave del plist:
    - KeepAlive / SuccessfulExit false  → relanza aunque el proceso salga con código 0
    - KeepAlive / Crashed true          → relanza explícitamente tras crash
    - AbandonProcessGroup true          → launchd no mata hijos al matar el padre,
                                          lo que permite que detecte la muerte del proceso
                                          correctamente tras un sleep/wake
    - ThrottleInterval 10               → espera 10 s antes de relanzar (evita
                                          bucles rápidos si hay un error al arrancar)
    - ProcessType Interactive           → le dice a macOS que NO aplique throttling
                                          de background a este proceso
    """
    exe = get_app_executable()
    log = os.path.expanduser("~/Documents/autoclicker_log.txt")

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCH_AGENT_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
    </array>

    <!-- Arrancar cuando launchd carga el agente (login o bootstrap) -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Mantener vivo en todos los escenarios de muerte del proceso -->
    <key>KeepAlive</key>
    <dict>
        <!-- Relanzar si el proceso termina con exit code 0 (salida "limpia") -->
        <key>SuccessfulExit</key>
        <false/>
        <!-- Relanzar explícitamente si el proceso crashea -->
        <key>Crashed</key>
        <true/>
    </dict>

    <!-- Sin esto launchd puede no detectar la muerte del proceso tras sleep -->
    <key>AbandonProcessGroup</key>
    <true/>

    <!-- Esperar 10 s antes de relanzar (evita bucles si hay error al iniciar) -->
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <!-- Interactive = no throttle de background, equivale a foreground app -->
    <key>ProcessType</key>
    <string>Interactive</string>

    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>"""

    os.makedirs(os.path.dirname(LAUNCH_AGENT_PATH), exist_ok=True)
    with open(LAUNCH_AGENT_PATH, "w") as f:
        f.write(plist)

    # launchctl load está deprecated desde macOS 12 y es ignorado silenciosamente.
    # La API correcta es bootstrap/bootout con el dominio de GUI del usuario.
    uid = get_uid()
    domain = f"gui/{uid}"

    # Si ya estaba cargado (rebuild), lo descargamos primero
    os.system(f"launchctl bootout {domain}/{LAUNCH_AGENT_LABEL} 2>/dev/null")
    ret = os.system(f"launchctl bootstrap {domain} '{LAUNCH_AGENT_PATH}'")
    if ret == 0:
        print(f"Launch Agent instalado y activo (dominio {domain})")
    else:
        # Fallback: algunos sistemas más viejos aún usan load
        os.system(f"launchctl load -w '{LAUNCH_AGENT_PATH}' 2>/dev/null")
        print(f"Launch Agent instalado con fallback load")

def uninstall_launch_agent():
    """Descarga y elimina el Launch Agent usando la API moderna."""
    if not os.path.exists(LAUNCH_AGENT_PATH):
        return
    uid = get_uid()
    domain = f"gui/{uid}"
    os.system(f"launchctl bootout {domain}/{LAUNCH_AGENT_LABEL} 2>/dev/null")
    # Fallback por compatibilidad
    os.system(f"launchctl unload '{LAUNCH_AGENT_PATH}' 2>/dev/null")
    os.remove(LAUNCH_AGENT_PATH)
    print("Launch Agent eliminado.")


# ---------------------------------------------------------------------------
# DELEGATE NUMÉRICO
# ---------------------------------------------------------------------------
class NumberOnlyDelegate(NSObject):
    def control_textShouldBeginEditing_(self, control, fieldEditor):
        return True
    def control_isValidObject_(self, control, object):
        return True
    def control_textView_doCommandBySelector_(self, control, textView, commandSelector):
        return False
    def controlTextDidChange_(self, notification):
        tf      = notification.object()
        current = tf.stringValue()
        filtered = ''.join(c for c in current if c.isdigit() or c == '.')
        if filtered.count('.') > 1:
            parts    = filtered.split('.')
            filtered = parts[0] + '.' + ''.join(parts[1:])
        if filtered != current:
            tf.setStringValue_(filtered)


# ---------------------------------------------------------------------------
# VISTA CON GRADIENTE
# ---------------------------------------------------------------------------
class GradientView(NSView):
    def drawRect_(self, dirtyRect):
        dark  = NSColor.colorWithRed_green_blue_alpha_(0.17, 0.17, 0.17, 1.0)
        black = NSColor.colorWithRed_green_blue_alpha_(0.05, 0.05, 0.05, 1.0)
        NSGradient.alloc().initWithColors_([dark, black]).drawInRect_angle_(self.bounds(), 90.0)

    def mouseDown_(self, event):
        if self.window():
            self.window().makeFirstResponder_(None)
        objc.super(GradientView, self).mouseDown_(event)


# ---------------------------------------------------------------------------
# CONTROLADOR PRINCIPAL
# ---------------------------------------------------------------------------
class AutoClickerController(NSObject):

    def applicationDidFinishLaunching_(self, notification):
        print(f"Iniciando — {time.ctime()}")

        # ── Deshabilitar App Nap ──────────────────────────────────────────
        # Guardar referencia en self: si se pierde, macOS cancela la actividad
        self._activity_token = disable_app_nap()

        self.running                = False
        self.is_terminating         = False
        self.pressed_keys           = set()
        self.target_hotkey          = {keyboard.Key.f8}
        self.hotkey_text            = "F8"
        self.is_setting_hotkey      = False
        self.last_mouse_pos         = (0, 0)
        self.last_mouse_move_time   = time.time()
        self.mouse_watchdog_timer   = None
        self.click_timer            = None
        self.start_delay_timer      = None
        self.auto_stop_timer        = None
        self.countdown_timer        = None
        self.last_toggle_time       = 0
        self.countdown_seconds_left = 0
        self.keyboard_listener      = None
        self._perm_poll_count       = 0
        self.number_delegate        = NumberOnlyDelegate.alloc().init()

        self.has_permissions = self._init_permissions()

        # ── Status bar ───────────────────────────────────────────────────
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        image = NSImage.alloc().initByReferencingFile_(resource_path("icon.png"))
        if image.isValid():
            image.setSize_(NSSize(18, 18))
            image.setTemplate_(True)
            self.status_item.button().setImage_(image)
        self.status_item.button().setTitle_("" if self.has_permissions else "⚠️")
        self.status_item.button().setAction_("togglePopover:")
        self.status_item.button().setTarget_(self)

        # ── Popover ──────────────────────────────────────────────────────
        self.popover = NSPopover.alloc().init()
        self.popover.setBehavior_(1)
        self._popover_height = 430 if self.has_permissions else 460
        self.popover.setContentSize_(NSSize(300, self._popover_height))

        self.main_view = GradientView.alloc().initWithFrame_(
            NSRect((0, 0), (300, self._popover_height))
        )
        self.setupHeaderUI()
        self.setupTabsUI()
        self.setupDynamicViews()
        self.setupFooterUI()

        vc = NSViewController.alloc().init()
        vc.setView_(self.main_view)
        self.popover.setContentViewController_(vc)

        # ── Notificaciones sleep/wake ────────────────────────────────────
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(
            self, "handleSystemSleep:", "NSWorkspaceWillSleepNotification", None
        )
        nc.addObserver_selector_name_object_(
            self, "handleSystemWake:", "NSWorkspaceDidWakeNotification", None
        )

        # ── Timers de mantenimiento ──────────────────────────────────────
        self.mouse_watchdog_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, "checkMouseMovement:", None, True
        )
        # Health check cada 30 s: si el listener murió, lo reinicia
        self._health_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            30.0, self, "checkListenerHealth:", None, True
        )

        self.toggleSmartOptions_(None)

        if self.has_permissions:
            self.start_keyboard_listener()
            # Instalar Launch Agent si no está instalado
            if not is_launch_agent_installed():
                install_launch_agent()
        else:
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.4, self, "openPopoverOnStart:", None, False
            )
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                2.0, self, "pollForPermissions:", None, True
            )

    # -----------------------------------------------------------------------
    # HEALTH CHECK
    # -----------------------------------------------------------------------
    def checkListenerHealth_(self, timer):
        """Cada 30 s verifica que el listener siga vivo y lo reinicia si no."""
        if not self.has_permissions or self.is_terminating:
            return
        if self.keyboard_listener is None or not self.keyboard_listener.is_alive():
            print("Health check: listener muerto, reiniciando...")
            self.start_keyboard_listener()

    # -----------------------------------------------------------------------
    # PERMISOS
    # -----------------------------------------------------------------------
    def _init_permissions(self):
        if check_accessibility_permissions():
            print("Permisos ya concedidos.")
            return True
        print("Sin permisos. Lanzando prompt...")
        request_accessibility_permissions_prompt()
        result = check_accessibility_permissions()
        print(f"Estado tras prompt: {result}")
        return result

    def openPopoverOnStart_(self, timer):
        btn = self.status_item.button()
        self.popover.showRelativeToRect_ofView_preferredEdge_(btn.bounds(), btn, 3)

    def pollForPermissions_(self, timer):
        self._perm_poll_count += 1
        if self._perm_poll_count > 60:
            timer.invalidate()
            return
        if check_accessibility_permissions():
            timer.invalidate()
            self.has_permissions = True
            print("Permisos detectados.")
            self._on_permissions_granted()

    def _on_permissions_granted(self):
        self.status_item.button().setTitle_("")
        self.status_label.setStringValue_(f"Ready — {self.hotkey_text}")
        self.status_label.setTextColor_(NSColor.greenColor())
        self.toggle_button.setEnabled_(True)
        self.btn_set_hotkey.setEnabled_(True)
        self.mode_switch.setEnabled_(True)
        self.btn_pick_key.setEnabled_(True)
        self.btn_retry_permissions.setHidden_(True)
        self.popover.setContentSize_(NSSize(300, 430))
        self.main_view.setFrameSize_(NSSize(300, 430))
        self.start_keyboard_listener()
        if not is_launch_agent_installed():
            install_launch_agent()

    def retryPermissions_(self, sender):
        print("Reintentando permisos...")
        result = request_accessibility_permissions_prompt()
        if not result:
            result = check_accessibility_permissions()
        if result:
            self.has_permissions = True
            self._on_permissions_granted()
        else:
            url = NSURL.URLWithString_(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            )
            NSWorkspace.sharedWorkspace().openURL_(url)

    # -----------------------------------------------------------------------
    # HELPERS UI
    # -----------------------------------------------------------------------
    def create_label(self, parent, x, y, w, text, font_size=13):
        lbl = NSTextField.alloc().initWithFrame_(NSRect((x, y), (w, 24)))
        lbl.setStringValue_(text)
        lbl.setEditable_(False)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setTextColor_(NSColor.whiteColor())
        if font_size != 13:
            lbl.setFont_(NSFont.systemFontOfSize_(font_size))
        parent.addSubview_(lbl)
        return lbl

    def create_input(self, parent, x, y, w, default_val):
        inp = NSTextField.alloc().initWithFrame_(NSRect((x, y), (w, 24)))
        inp.setStringValue_(default_val)
        inp.setDelegate_(self.number_delegate)
        parent.addSubview_(inp)
        return inp

    def create_combo(self, parent, x, y, w, items):
        combo = NSComboBox.alloc().initWithFrame_(NSRect((x, y), (w, 24)))
        combo.addItemsWithObjectValues_(items)
        combo.setEditable_(False)
        parent.addSubview_(combo)
        return combo

    # -----------------------------------------------------------------------
    # HEADER UI
    # -----------------------------------------------------------------------
    def setupHeaderUI(self):
        v = self.main_view
        h = self._popover_height

        self.create_label(v, 20, h - 50,  40,  "Click")
        self.input_clicks = self.create_input(v, 60, h - 50, 40, "20")
        self.create_label(v, 105, h - 50, 80,  "times per")
        self.combo_time = self.create_combo(v, 170, h - 50, 80, ["second", "minute"])
        self.combo_time.selectItemAtIndex_(0)

        self.create_label(v, 20,  h - 85,  140, "Start after")
        self.input_start_delay = self.create_input(v, 95, h - 85, 40, "0")
        self.create_label(v, 140, h - 85,  80,  "seconds")

        self.create_label(v, 20,  h - 120, 140, "Stop after")
        self.input_stop_delay = self.create_input(v, 95, h - 120, 40, "0")
        self.create_label(v, 140, h - 120, 120, "seconds (0 = ∞)")

        self.create_label(v, 20, h - 160, 60, "Hotkey:")
        self.btn_set_hotkey = NSButton.alloc().initWithFrame_(NSRect((80, h - 160), (180, 24)))
        self.btn_set_hotkey.setTitle_(self.hotkey_text)
        self.btn_set_hotkey.setBezelStyle_(1)
        self.btn_set_hotkey.setAction_("startSettingHotkey:")
        self.btn_set_hotkey.setTarget_(self)
        self.btn_set_hotkey.setEnabled_(self.has_permissions)
        v.addSubview_(self.btn_set_hotkey)

        self.status_label = NSTextField.alloc().initWithFrame_(NSRect((20, h - 200), (190, 24)))
        if self.has_permissions:
            self.status_label.setStringValue_(f"Ready — {self.hotkey_text}")
            self.status_label.setTextColor_(NSColor.greenColor())
        else:
            self.status_label.setStringValue_("Waiting for permissions...")
            self.status_label.setTextColor_(NSColor.orangeColor())
        self.status_label.setEditable_(False)
        self.status_label.setBezeled_(False)
        self.status_label.setDrawsBackground_(False)
        self.status_label.setFont_(NSFont.boldSystemFontOfSize_(11))
        v.addSubview_(self.status_label)

        self.toggle_button = NSButton.alloc().initWithFrame_(NSRect((210, h - 202), (70, 32)))
        self.toggle_button.setTitle_("Start")
        self.toggle_button.setBezelStyle_(1)
        self.toggle_button.setAction_("toggleState:")
        self.toggle_button.setTarget_(self)
        self.toggle_button.setEnabled_(self.has_permissions)
        v.addSubview_(self.toggle_button)

        self.btn_retry_permissions = NSButton.alloc().initWithFrame_(NSRect((20, h - 232), (260, 24)))
        self.btn_retry_permissions.setTitle_("Grant Accessibility permissions")
        self.btn_retry_permissions.setBezelStyle_(1)
        self.btn_retry_permissions.setAction_("retryPermissions:")
        self.btn_retry_permissions.setTarget_(self)
        self.btn_retry_permissions.setEnabled_(not self.has_permissions)
        self.btn_retry_permissions.setHidden_(self.has_permissions)
        v.addSubview_(self.btn_retry_permissions)

        sep_y = h - 242 if not self.has_permissions else h - 212
        sep = NSView.alloc().initWithFrame_(NSRect((10, sep_y), (280, 1)))
        sep.setWantsLayer_(True)
        sep.layer().setBackgroundColor_(NSColor.grayColor().CGColor())
        v.addSubview_(sep)

    # -----------------------------------------------------------------------
    # TABS
    # -----------------------------------------------------------------------
    def setupTabsUI(self):
        h     = self._popover_height
        sep_y = h - 242 if not self.has_permissions else h - 212
        tab_y = sep_y - 42

        self.mode_switch = NSSegmentedControl.alloc().initWithFrame_(NSRect((20, tab_y), (260, 30)))
        self.mode_switch.setSegmentCount_(2)
        self.mode_switch.setLabel_forSegment_("Mouse Mode", 0)
        self.mode_switch.setLabel_forSegment_("Keyboard Mode", 1)
        self.mode_switch.setSelectedSegment_(0)
        self.mode_switch.setTarget_(self)
        self.mode_switch.setAction_("changeMode:")
        self.mode_switch.setEnabled_(self.has_permissions)
        self.main_view.addSubview_(self.mode_switch)

    # -----------------------------------------------------------------------
    # DYNAMIC VIEWS
    # -----------------------------------------------------------------------
    def setupDynamicViews(self):
        self.mouse_view = NSView.alloc().initWithFrame_(NSRect((0, 50), (300, 100)))

        self.create_label(self.mouse_view, 20, 55, 110, "Click using the")
        self.combo_mouse = self.create_combo(self.mouse_view, 130, 55, 80, ["left", "middle", "right"])
        self.combo_mouse.selectItemAtIndex_(0)
        self.mouse_view.addSubview_(self.combo_mouse)
        self.create_label(self.mouse_view, 215, 55, 60, "button")

        self.chk_smart_move = NSButton.alloc().initWithFrame_(NSRect((20, 28), (260, 24)))
        self.chk_smart_move.setButtonType_(3)
        self.chk_smart_move.setTitle_("Click only if mouse is not moving")
        self.chk_smart_move.setAction_("toggleSmartOptions:")
        self.chk_smart_move.setTarget_(self)
        self.mouse_view.addSubview_(self.chk_smart_move)

        self.lbl_smart_1      = self.create_label(self.mouse_view, 40, 4, 30, "for")
        self.input_smart_time = self.create_input(self.mouse_view, 70, 4, 40, "1")
        self.lbl_smart_2      = self.create_label(self.mouse_view, 115, 4, 100, "seconds")
        self.main_view.addSubview_(self.mouse_view)

        self.keyboard_view = NSView.alloc().initWithFrame_(NSRect((0, 50), (300, 100)))
        self.keyboard_view.setHidden_(True)
        self.create_label(self.keyboard_view, 20, 55, 120, "Press key to send:")

        self.btn_pick_key = NSButton.alloc().initWithFrame_(NSRect((150, 55), (120, 24)))
        self.btn_pick_key.setTitle_("Select key")
        self.btn_pick_key.setBezelStyle_(1)
        self.btn_pick_key.setAction_("startSettingKeyboardKey:")
        self.btn_pick_key.setTarget_(self)
        self.btn_pick_key.setEnabled_(self.has_permissions)
        self.keyboard_view.addSubview_(self.btn_pick_key)

        self.lbl_selected_key = self.create_label(self.keyboard_view, 20, 22, 260, "Selected: NONE")
        self.lbl_selected_key.setTextColor_(NSColor.whiteColor())

        self.selected_keyboard_key   = None
        self.is_setting_keyboard_key = False
        self.main_view.addSubview_(self.keyboard_view)

    def changeMode_(self, sender):
        sel = self.mode_switch.selectedSegment()
        self.mouse_view.setHidden_(sel != 0)
        self.keyboard_view.setHidden_(sel != 1)

    # -----------------------------------------------------------------------
    # FOOTER
    # -----------------------------------------------------------------------
    def setupFooterUI(self):
        btn = NSButton.alloc().initWithFrame_(NSRect((110, 14), (80, 30)))
        btn.setTitle_("Quit")
        btn.setBordered_(False)
        btn.setBezelStyle_(1)
        btn.setAction_("quitApp:")
        btn.setTarget_(self)
        self.main_view.addSubview_(btn)

    # -----------------------------------------------------------------------
    # POPOVER
    # -----------------------------------------------------------------------
    def togglePopover_(self, sender):
        btn = self.status_item.button()
        if self.popover.isShown():
            self.popover.performClose_(sender)
        else:
            self.popover.showRelativeToRect_ofView_preferredEdge_(btn.bounds(), btn, 3)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.01, self, "removeFocusFromInputs:", None, False
            )

    def removeFocusFromInputs_(self, timer):
        try:
            win = self.popover.contentViewController().view().window()
            if win:
                win.makeFirstResponder_(None)
        except Exception as e:
            print(f"removeFocusFromInputs: {e}")

    # -----------------------------------------------------------------------
    # SMART MOVE
    # -----------------------------------------------------------------------
    def toggleSmartOptions_(self, sender):
        checked = (self.chk_smart_move.state() == 1)
        color   = NSColor.whiteColor() if checked else NSColor.grayColor()
        self.input_smart_time.setEnabled_(checked and self.has_permissions)
        self.input_smart_time.setTextColor_(color)
        self.lbl_smart_1.setTextColor_(color)
        self.lbl_smart_2.setTextColor_(color)

    def checkMouseMovement_(self, timer):
        pos = CGEventGetLocation(CGEventCreate(None))
        if (pos.x, pos.y) != self.last_mouse_pos:
            self.last_mouse_pos       = (pos.x, pos.y)
            self.last_mouse_move_time = time.time()

    # -----------------------------------------------------------------------
    # HOTKEY
    # -----------------------------------------------------------------------
    def startSettingHotkey_(self, sender):
        if self.running or not self.has_permissions:
            return
        self.is_setting_hotkey = True
        self.pressed_keys.clear()
        self.btn_set_hotkey.setTitle_("Press keys...")
        self.status_label.setStringValue_("Listening...")
        self.status_label.setTextColor_(NSColor.yellowColor())

    def updateHotkeyUI_(self, text):
        self.hotkey_text = text
        self.btn_set_hotkey.setTitle_(text)
        self.status_label.setStringValue_(f"Ready — {text}")
        self.status_label.setTextColor_(NSColor.greenColor())
        self.is_setting_hotkey = False

    def updateKeyboardKeyUI_(self, keyname):
        self.lbl_selected_key.setStringValue_(f"Selected: {keyname}")
        self.btn_pick_key.setTitle_(keyname)

    def get_key_name(self, key):
        if hasattr(key, 'name'):
            name = key.name
            if name.endswith(('_r', '_l')):
                name = name.rsplit('_', 1)[0]
            return name.title()
        if hasattr(key, 'char') and key.char:
            return key.char.upper()
        return str(key)

    def format_hotkey_string(self, key_set):
        mods, others = [], []
        for k in key_set:
            name = self.get_key_name(k)
            (mods if name in ('Cmd', 'Ctrl', 'Alt', 'Shift') else others).append(name)
        return "+".join(sorted(mods) + sorted(others))

    def is_modifier(self, key):
        return hasattr(key, 'name') and key.name in (
            'cmd', 'ctrl', 'alt', 'shift', 'cmd_r', 'ctrl_r', 'alt_r', 'shift_r'
        )

    # -----------------------------------------------------------------------
    # CONTROL DE EJECUCIÓN
    # -----------------------------------------------------------------------
    def toggleState_(self, sender):
        if not self.has_permissions:
            self.retryPermissions_(None)
            return
        if self.running:
            self.stopClicker_(None)
        else:
            self.startProcess_(None)

    def startProcess_(self, sender=None):
        if not self.has_permissions or self.running or self.is_setting_hotkey:
            return
        self.running = True
        self.toggle_button.setTitle_("Stop")

        try:
            start_delay = float(self.input_start_delay.stringValue())
        except ValueError:
            start_delay = 0

        if start_delay > 0:
            self.countdown_seconds_left = start_delay
            self.updateCountdownLabel_(None)
            self.countdown_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, "updateCountdownLabel:", None, True
            )
            self.start_delay_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                start_delay, self, "beginClickingActual:", None, False
            )
        else:
            self.beginClickingActual_(None)

    def updateCountdownLabel_(self, timer):
        if self.countdown_seconds_left > 0:
            self.status_label.setStringValue_(f"Starting in {int(self.countdown_seconds_left)}s...")
            self.status_label.setTextColor_(NSColor.yellowColor())
            self.countdown_seconds_left -= 1
        else:
            if self.countdown_timer:
                self.countdown_timer.invalidate()
                self.countdown_timer = None

    def beginClickingActual_(self, timer):
        if self.start_delay_timer: self.start_delay_timer.invalidate()
        if self.countdown_timer:   self.countdown_timer.invalidate()
        if not self.running:
            return

        self.status_label.setStringValue_(f"RUNNING ({self.hotkey_text} stops)")
        self.status_label.setTextColor_(NSColor.redColor())

        try:
            stop_delay = float(self.input_stop_delay.stringValue())
        except ValueError:
            stop_delay = 0

        if stop_delay > 0:
            self.auto_stop_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                stop_delay, self, "stopClicker:", None, False
            )

        try:
            cps = float(self.input_clicks.stringValue())
            if cps <= 0: cps = 1
            interval = 1.0 / cps if self.combo_time.stringValue() == "second" else 60.0 / cps
            self.click_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                interval, self, "performAction:", None, True
            )
        except Exception as e:
            print(f"Error al iniciar timer: {e}")
            self.stopClicker_(None)

    def stopClicker_(self, sender=None):
        self.running = False
        self.toggle_button.setTitle_("Start")
        for t in (self.click_timer, self.start_delay_timer, self.auto_stop_timer, self.countdown_timer):
            if t: t.invalidate()
        self.status_label.setStringValue_(f"Ready — {self.hotkey_text}")
        self.status_label.setTextColor_(NSColor.greenColor())

    def startSettingKeyboardKey_(self, sender):
        if self.running or not self.has_permissions:
            return
        self.is_setting_keyboard_key = True
        self.lbl_selected_key.setStringValue_("Selected: ... (press a key)")

    # -----------------------------------------------------------------------
    # ACCIÓN PRINCIPAL
    # -----------------------------------------------------------------------
    def performAction_(self, timer):
        if not self.running or self.is_terminating or not self.has_permissions:
            return

        mode = self.mode_switch.selectedSegment()

        if mode == 0:
            if self.chk_smart_move.state() == 1:
                try:
                    idle = float(self.input_smart_time.stringValue())
                    if idle < 0: idle = 0
                except ValueError:
                    idle = 1.0
                if time.time() - self.last_mouse_move_time < idle:
                    if "Paused" not in self.status_label.stringValue():
                        self.status_label.setStringValue_("Paused (Mouse moved)")
                        self.status_label.setTextColor_(NSColor.orangeColor())
                    return
                elif "Paused" in self.status_label.stringValue():
                    self.status_label.setStringValue_(f"RUNNING ({self.hotkey_text} stops)")
                    self.status_label.setTextColor_(NSColor.redColor())

            try:
                pos = CGEventGetLocation(CGEventCreate(None))
                x, y = pos.x, pos.y
                btn_str = self.combo_mouse.stringValue()
                if btn_str == "right":
                    d, u, b = kCGEventRightMouseDown, kCGEventRightMouseUp, kCGMouseButtonRight
                elif btn_str == "middle":
                    d, u, b = kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGMouseButtonCenter
                else:
                    d, u, b = kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGMouseButtonLeft
                CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, d, (x, y), b))
                CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, u, (x, y), b))
            except Exception as e:
                print(f"Click error: {e}")

        elif mode == 1:
            if not self.selected_keyboard_key:
                return
            try:
                kb = keyboard.Controller()
                kb.press(self.selected_keyboard_key)
                kb.release(self.selected_keyboard_key)
            except Exception as e:
                print(f"Keyboard action error: {e}")

    # -----------------------------------------------------------------------
    # KEYBOARD LISTENER
    # -----------------------------------------------------------------------
    def stop_keyboard_listener(self):
        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
            except Exception:
                pass
            self.keyboard_listener = None

    def start_keyboard_listener(self):
        self.stop_keyboard_listener()

        def on_press(key):
            if self.is_terminating:
                return False
            self.pressed_keys.add(key)

            if self.is_setting_hotkey:
                try:
                    temp = self.format_hotkey_string(self.pressed_keys)
                    if not self.is_modifier(key):
                        self.target_hotkey = self.pressed_keys.copy()
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "updateHotkeyUI:", temp, False
                        )
                        self.pressed_keys.clear()
                except Exception as e:
                    print(f"hotkey set error: {e}")
                return

            if self.is_setting_keyboard_key:
                try:
                    name = self.get_key_name(key)
                    self.selected_keyboard_key   = key
                    self.is_setting_keyboard_key = False
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "updateKeyboardKeyUI:", name, False
                    )
                except Exception as e:
                    print(f"keyboard key set error: {e}")
                return

            try:
                if self.target_hotkey.issubset(self.pressed_keys):
                    now = time.time()
                    if now - self.last_toggle_time > 0.5:
                        self.last_toggle_time = now
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "toggleClickerFromHotkey:", None, False
                        )
            except Exception:
                pass

        def on_release(key):
            self.pressed_keys.discard(key)

        self.keyboard_listener        = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()

    def handleSystemSleep_(self, notification):
        print(f"Sistema suspendido — {time.ctime()}")
        self.stop_keyboard_listener()
        if self.running:
            self.stopClicker_(None)

    def handleSystemWake_(self, notification):
        print(f"Sistema despierto — {time.ctime()}")
        # 1 s de margen para que el sistema termine de despertar
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, "restartKeyboardListenerAfterWake:", None, False
        )

    def restartKeyboardListenerAfterWake_(self, timer):
        if self.is_terminating:
            return
        try:
            self.start_keyboard_listener()
            print("Listener reiniciado tras wake.")
        except Exception as e:
            print(f"Error al reiniciar listener: {e}")

    def toggleClickerFromHotkey_(self, obj):
        if self.has_permissions:
            self.toggleState_(None)

    # -----------------------------------------------------------------------
    # QUIT
    # -----------------------------------------------------------------------
    def quitApp_(self, sender):
        self.is_terminating = True
        for t in (self.mouse_watchdog_timer, getattr(self, '_health_timer', None)):
            if t: t.invalidate()
        # Al salir manualmente desinstalamos el Launch Agent para que no
        # reaparezca la próxima vez que el usuario inicie sesión sin querer
        uninstall_launch_agent()
        self.cleanup()
        NSApplication.sharedApplication().terminate_(sender)

    def cleanup(self):
        self.stopClicker_(None)
        self.stop_keyboard_listener()


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def setup_logging():
    log_path = os.path.expanduser("~/Documents/autoclicker_log.txt")
    try:
        f = open(log_path, "w")
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass
    print(f"Log iniciado: {time.ctime()}")


if __name__ == "__main__":
    setup_logging()
    app = NSApplication.sharedApplication()
    dlg = AutoClickerController.alloc().init()
    app.setDelegate_(dlg)
    AppHelper.runEventLoop()