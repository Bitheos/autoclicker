import objc
import time
import math
from Cocoa import (
    NSApplication, NSObject, NSStatusBar, NSVariableStatusItemLength,
    NSPopover, NSView, NSButton, NSComboBox, NSTextField, NSRect, NSSize,
    NSViewController, NSTimer, NSColor, NSGradient, NSFont, NSBezelStyle,
    NSSegmentedControl
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

# --- DELEGATE PARA VALIDAR NÚMEROS ---
class NumberOnlyDelegate(NSObject):
    def control_textShouldBeginEditing_(self, control, fieldEditor):
        return True
    
    def control_isValidObject_(self, control, object):
        return True
    
    def control_textView_doCommandBySelector_(self, control, textView, commandSelector):
        return False
    
    def controlTextDidChange_(self, notification):
        text_field = notification.object()
        current_text = text_field.stringValue()
        
        # Permitir solo números y punto decimal
        filtered = ''.join(c for c in current_text if c.isdigit() or c == '.')
        
        # Permitir solo un punto decimal
        if filtered.count('.') > 1:
            parts = filtered.split('.')
            filtered = parts[0] + '.' + ''.join(parts[1:])
        
        if filtered != current_text:
            text_field.setStringValue_(filtered)

# --- VISTA CON GRADIENTE DE FONDO ---
class GradientView(NSView):
    def drawRect_(self, dirtyRect):
        # Un gris muy oscuro casi negro para el fondo
        dark_gray = NSColor.colorWithRed_green_blue_alpha_(0.17, 0.17, 0.17, 1.0)
        black = NSColor.colorWithRed_green_blue_alpha_(0.05, 0.05, 0.05, 1.0)
        gradient = NSGradient.alloc().initWithColors_([dark_gray, black])
        gradient.drawInRect_angle_(self.bounds(), 90.0)
    
    def mouseDown_(self, event):
        # Remover foco al hacer clic en el fondo
        if self.window():
            self.window().makeFirstResponder_(None)
        objc.super(GradientView, self).mouseDown_(event)

class AutoClickerController(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.running = False
        self.is_terminating = False
        
        # --- VARIABLES DE CONTROL ---
        self.pressed_keys = set()
        self.target_hotkey = {keyboard.Key.f8}
        self.hotkey_text = "F8"
        self.is_setting_hotkey = False
        
        # Mouse Watchdog
        self.last_mouse_pos = (0, 0)
        self.last_mouse_move_time = time.time()
        self.mouse_watchdog_timer = None

        # Timers
        self.click_timer = None
        self.start_delay_timer = None
        self.auto_stop_timer = None
        self.countdown_timer = None 

        self.last_toggle_time = 0
        self.countdown_seconds_left = 0
        
        # Delegate para validación de números
        self.number_delegate = NumberOnlyDelegate.alloc().init()

        # --- UI SETUP ---
        status_bar = NSStatusBar.systemStatusBar()
        self.status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
        self.status_item.button().setTitle_("🖱️ AutoClicker")
        self.status_item.button().setAction_("togglePopover:")
        self.status_item.button().setTarget_(self)

        self.popover = NSPopover.alloc().init()
        self.popover.setBehavior_(1)
        # Aumentamos el alto para que quepa todo cómodamente
        self.popover.setContentSize_(NSSize(300, 400))

        # Vista Principal (Contenedor Global)
        self.main_view = GradientView.alloc().initWithFrame_(NSRect((0, 0), (300, 400)))
        
        # --- CONSTRUCCIÓN DE UI ---
        self.setupHeaderUI()      # Parte superior fija
        self.setupTabsUI()        # El selector Mouse/Keyboard
        self.setupDynamicViews()  # Los contenedores cambiantes
        self.setupFooterUI()      # Botón de salir

        view_controller = NSViewController.alloc().init()
        view_controller.setView_(self.main_view)
        self.popover.setContentViewController_(view_controller)

        # Listeners
        self.start_keyboard_listener()
        
        # Iniciar estado visual
        self.toggleSmartOptions_(None)
        
        # Watchdog de movimiento
        self.mouse_watchdog_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, "checkMouseMovement:", None, True
        )

    # --- HELPERS UI ---
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
        # NO establecer refusesFirstResponder para permitir edición cuando se hace clic
        # Agregar delegate para validar números
        inp.setDelegate_(self.number_delegate)
        parent.addSubview_(inp)
        return inp
    
    def create_combo(self, parent, x, y, w, items):
        combo = NSComboBox.alloc().initWithFrame_(NSRect((x, y), (w, 24)))
        combo.addItemsWithObjectValues_(items)
        # Hacer el combo NO editable (solo selección)
        combo.setEditable_(False)
        parent.addSubview_(combo)
        return combo

    # ---------------------------------------------------------
    # 1. HEADER UI (Siempre visible en la parte superior)
    # ---------------------------------------------------------
    def setupHeaderUI(self):
        v = self.main_view
        
        # -- VELOCIDAD (Y: 410) --
        self.create_label(v, 20, 350, 40, "Click")
        self.input_clicks = self.create_input(v, 60, 350, 40, "20")
        self.create_label(v, 105, 350, 80, "times per")
        self.combo_time = self.create_combo(v, 170, 350, 80, ["second", "minute"])
        self.combo_time.selectItemAtIndex_(0)
        v.addSubview_(self.combo_time)

        # -- START DELAY (Y: 375) --
        self.create_label(v, 20, 315, 140, "Start after ")
        self.input_start_delay = self.create_input(v, 90, 315, 40, "0")
        self.create_label(v, 135, 315, 80, "seconds")
        
        # -- STOP DELAY (Y: 340) --
        self.create_label(v, 20, 280, 140, "Stop after ")
        self.input_stop_delay = self.create_input(v, 90, 280, 40, "0")
        self.create_label(v, 135, 280, 120, "seconds (0 = ∞)")

        # -- HOTKEY (Y: 300) --
        self.create_label(v, 20, 240, 60, "Hotkey:")
        self.btn_set_hotkey = NSButton.alloc().initWithFrame_(NSRect((80, 240), (180, 24)))
        self.btn_set_hotkey.setTitle_(f"{self.hotkey_text}")
        self.btn_set_hotkey.setBezelStyle_(1)
        self.btn_set_hotkey.setAction_("startSettingHotkey:")
        self.btn_set_hotkey.setTarget_(self)
        v.addSubview_(self.btn_set_hotkey)

        self.status_label = NSTextField.alloc().initWithFrame_(NSRect((20, 200), (190, 24)))
        self.status_label.setStringValue_(f"Ready - {self.hotkey_text}")
        self.status_label.setEditable_(False)
        self.status_label.setBezeled_(False)
        self.status_label.setDrawsBackground_(False)
        self.status_label.setTextColor_(NSColor.colorWithRed_green_blue_alpha_(0.6, 0.6, 1.0, 1.0))
        self.status_label.setFont_(NSFont.boldSystemFontOfSize_(11))
        v.addSubview_(self.status_label)

        # -- STATUS & START BUTTON (Y: 250) --
        self.toggle_button = NSButton.alloc().initWithFrame_(NSRect((210, 200), (70, 32)))
        self.toggle_button.setTitle_("Start")
        self.toggle_button.setBezelStyle_(1) 
        self.toggle_button.setAction_("toggleState:")
        self.toggle_button.setTarget_(self)
        v.addSubview_(self.toggle_button)

        # Separator
        sep = NSView.alloc().initWithFrame_(NSRect((10, 190), (280, 1)))
        sep.setWantsLayer_(True)
        sep.layer().setBackgroundColor_(NSColor.grayColor().CGColor())
        v.addSubview_(sep)
        
    # ---------------------------------------------------------
    # 2. TABS / SWITCHER UI
    # ---------------------------------------------------------
    def setupTabsUI(self):
        # Segmented Control para cambiar modos
        self.mode_switch = NSSegmentedControl.alloc().initWithFrame_(NSRect((20, 150), (260, 30)))
        self.mode_switch.setSegmentCount_(2)
        self.mode_switch.setLabel_forSegment_("Mouse Mode", 0)
        self.mode_switch.setLabel_forSegment_("Keyboard Mode", 1)
        self.mode_switch.setSelectedSegment_(0)
        self.mode_switch.setTarget_(self)
        self.mode_switch.setAction_("changeMode:")
        self.main_view.addSubview_(self.mode_switch)

    # ---------------------------------------------------------
    # 3. DYNAMIC VIEWS (Mouse vs Keyboard)
    # ---------------------------------------------------------
    def setupDynamicViews(self):
        # Contenedor para Mouse (Visible por defecto)
        self.mouse_view = NSView.alloc().initWithFrame_(NSRect((0, 50), (300, 100)))
        
        self.create_label(self.mouse_view, 20, 55, 110, "Click using the")
        self.combo_mouse = self.create_combo(self.mouse_view, 130, 55, 80, ["left", "middle", "right"])
        self.combo_mouse.selectItemAtIndex_(0)
        self.mouse_view.addSubview_(self.combo_mouse)
        self.create_label(self.mouse_view, 215, 55, 60, "button")

        # Smart Options en Mouse View
        self.chk_smart_move = NSButton.alloc().initWithFrame_(NSRect((20, 22), (260, 24)))
        self.chk_smart_move.setButtonType_(3) # Switch
        self.chk_smart_move.setTitle_("Click only if mouse is not moving")
        self.chk_smart_move.setAction_("toggleSmartOptions:")
        self.chk_smart_move.setTarget_(self)
        self.mouse_view.addSubview_(self.chk_smart_move)

        self.lbl_smart_1 = self.create_label(self.mouse_view, 40, 0, 30, "for")
        self.input_smart_time = self.create_input(self.mouse_view, 70, 0, 40, "1")
        self.lbl_smart_2 = self.create_label(self.mouse_view, 115, 0, 100, "seconds")
        
        self.main_view.addSubview_(self.mouse_view)

        # Contenedor para Keyboard (Oculto por defecto)
        self.keyboard_view = NSView.alloc().initWithFrame_(NSRect((0, 50), (300, 100)))
        self.keyboard_view.setHidden_(True)
        
        # Placeholder en Keyboard view
        # --- Keyboard Key Selection ---
        self.create_label(self.keyboard_view, 20, 55, 120, "Press key to send:")

        self.btn_pick_key = NSButton.alloc().initWithFrame_(NSRect((150, 55), (120, 24)))
        self.btn_pick_key.setTitle_("Select key")
        self.btn_pick_key.setBezelStyle_(1)
        self.btn_pick_key.setAction_("startSettingKeyboardKey:")
        self.btn_pick_key.setTarget_(self)
        self.keyboard_view.addSubview_(self.btn_pick_key)

        # Label que muestra la tecla seleccionada
        self.lbl_selected_key = self.create_label(self.keyboard_view, 20, 20, 260, "Selected: NONE")
        self.lbl_selected_key.setTextColor_(NSColor.whiteColor())

        self.selected_keyboard_key = None
        self.is_setting_keyboard_key = False

        self.main_view.addSubview_(self.keyboard_view)

    def changeMode_(self, sender):
        selected = self.mode_switch.selectedSegment()
        if selected == 0:
            self.mouse_view.setHidden_(False)
            self.keyboard_view.setHidden_(True)
        else:
            self.mouse_view.setHidden_(True)
            self.keyboard_view.setHidden_(False)

    # ---------------------------------------------------------
    # 4. FOOTER UI
    # ---------------------------------------------------------
    def setupFooterUI(self):
        quit_button = NSButton.alloc().initWithFrame_(NSRect((110, 10), (80, 30)))
        quit_button.setTitle_("❌")
        quit_button.setBordered_(False)
        quit_button.setBezelStyle_(1)
        quit_button.setAction_("quitApp:")
        quit_button.setTarget_(self)
        self.main_view.addSubview_(quit_button)

    def togglePopover_(self, sender):
        button = self.status_item.button()
        if self.popover.isShown():
            self.popover.performClose_(sender)
        else:
            self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, 3)
            # Remover el foco de cualquier control al abrir
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.01, self, "removeFocusFromInputs:", None, False
            )

    def removeFocusFromInputs_(self, timer):
        """Remueve el foco de todos los inputs al abrir el popover"""
        try:
            window = self.popover.contentViewController().view().window()
            if window:
                window.makeFirstResponder_(None)
        except Exception as e:
            print(f"Error removing focus: {e}")

    # --- LOGICA SMART OPTIONS ---
    def toggleSmartOptions_(self, sender):
        is_checked = (self.chk_smart_move.state() == 1)
        color = NSColor.whiteColor() if is_checked else NSColor.grayColor()
        
        self.input_smart_time.setEnabled_(is_checked)
        self.input_smart_time.setTextColor_(color)
        self.lbl_smart_1.setTextColor_(color)
        self.lbl_smart_2.setTextColor_(color)

    def checkMouseMovement_(self, timer):
        current_pos = CGEventGetLocation(CGEventCreate(None))
        x, y = current_pos.x, current_pos.y
        if (x, y) != self.last_mouse_pos:
            self.last_mouse_pos = (x, y)
            self.last_mouse_move_time = time.time()

    # --- LOGICA HOTKEY ---
    def startSettingHotkey_(self, sender):
        if self.running: return
        self.is_setting_hotkey = True
        self.pressed_keys.clear() 
        self.btn_set_hotkey.setTitle_("Press keys...")
        self.status_label.setStringValue_("Listening...")
        self.status_label.setTextColor_(NSColor.yellowColor())

    def updateHotkeyUI_(self, text_representation):
        self.hotkey_text = text_representation
        self.btn_set_hotkey.setTitle_(f"{self.hotkey_text}")
        self.status_label.setStringValue_(f"Ready - {self.hotkey_text}")
        self.status_label.setTextColor_(NSColor.greenColor())
        self.is_setting_hotkey = False
        
    def updateKeyboardKeyUI_(self, keyname):
        self.lbl_selected_key.setStringValue_(f"Selected: {keyname}")
        self.btn_pick_key.setTitle_(f"{keyname}")

    def get_key_name(self, key):
        if hasattr(key, 'name'):
            name = key.name
            if name.endswith('_r') or name.endswith('_l'):
                name = name.split('_')[0]
            return name.title()
        elif hasattr(key, 'char') and key.char:
            return key.char.upper()
        return str(key)

    def format_hotkey_string(self, key_set):
        modifiers = []
        others = []
        for k in key_set:
            name = self.get_key_name(k)
            if name in ['Cmd', 'Ctrl', 'Alt', 'Shift']:
                modifiers.append(name)
            else:
                others.append(name)
        modifiers.sort()
        others.sort()
        return "+".join(modifiers + others)

    def is_modifier(self, key):
        if hasattr(key, 'name'):
            return key.name in ['cmd', 'ctrl', 'alt', 'shift', 'cmd_r', 'ctrl_r', 'alt_r', 'shift_r']
        return False

    # --- CONTROL DE EJECUCIÓN ---
    def toggleState_(self, sender):
        if self.running:
            self.stopClicker_(None)
        else:
            self.startProcess_(None)

    def startProcess_(self, sender=None):
        if self.running or self.is_setting_hotkey: return
        self.running = True
        self.toggle_button.setTitle_("Stop")
        
        try:
            start_delay = float(self.input_start_delay.stringValue())
        except ValueError: start_delay = 0

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
        if self.countdown_timer: self.countdown_timer.invalidate()
        
        if not self.running: return

        self.status_label.setStringValue_(f"RUNNING ({self.hotkey_text} stops)")
        self.status_label.setTextColor_(NSColor.redColor())

        try:
            stop_delay = float(self.input_stop_delay.stringValue())
        except ValueError: stop_delay = 0
            
        if stop_delay > 0:
            self.auto_stop_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                stop_delay, self, "stopClicker:", None, False
            )

        try:
            clicks_per_unit = float(self.input_clicks.stringValue())
            if clicks_per_unit <= 0: clicks_per_unit = 1
            
            if self.combo_time.stringValue() == "second":
                interval = 1.0 / clicks_per_unit
            else:
                interval = 60.0 / clicks_per_unit
            
            self.click_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                interval, self, "performAction:", None, True
            )
        except Exception as e:
            print(f"Error: {e}")
            self.stopClicker_(None)

    def stopClicker_(self, sender=None):
        self.running = False
        self.toggle_button.setTitle_("Start")
        
        if self.click_timer: 
            self.click_timer.invalidate()
            self.click_timer = None
        if self.start_delay_timer:
            self.start_delay_timer.invalidate()
            self.start_delay_timer = None
        if self.auto_stop_timer:
            self.auto_stop_timer.invalidate()
            self.auto_stop_timer = None
        if self.countdown_timer:
            self.countdown_timer.invalidate()
            self.countdown_timer = None

        self.status_label.setStringValue_(f"Ready - {self.hotkey_text}")
        self.status_label.setTextColor_(NSColor.greenColor())
        
    def startSettingKeyboardKey_(self, sender):
        if self.running:
            return
        self.is_setting_keyboard_key = True
        self.lbl_selected_key.setStringValue_("Selected: ... (press a key)")

    # --- ACCIÓN PRINCIPAL (Click o Tecla) ---
    def performAction_(self, timer):
        if not self.running or self.is_terminating: return

        # Detectamos el modo actual
        mode = self.mode_switch.selectedSegment() # 0 = Mouse, 1 = Keyboard

        # MODO MOUSE
        if mode == 0:
            # Chequeo de movimiento inteligente
            if self.chk_smart_move.state() == 1:
                try:
                    idle_threshold = float(self.input_smart_time.stringValue())
                    if idle_threshold < 0: idle_threshold = 0
                except ValueError: idle_threshold = 1.0
                
                time_since_move = time.time() - self.last_mouse_move_time
                if time_since_move < idle_threshold:
                    if "Waiting" not in self.status_label.stringValue():
                        self.status_label.setStringValue_("Paused (Mouse moved)")
                        self.status_label.setTextColor_(NSColor.orangeColor())
                    return
                else:
                     if "Paused" in self.status_label.stringValue():
                        self.status_label.setStringValue_(f"RUNNING ({self.hotkey_text} stops)")
                        self.status_label.setTextColor_(NSColor.redColor())

            try:
                current_pos = CGEventGetLocation(CGEventCreate(None))
                x, y = current_pos.x, current_pos.y
                button_type = self.combo_mouse.stringValue()
                
                if button_type == "right":
                    down, up, btn = kCGEventRightMouseDown, kCGEventRightMouseUp, kCGMouseButtonRight
                elif button_type == "middle":
                    down, up, btn = kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGMouseButtonCenter
                else:
                    down, up, btn = kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGMouseButtonLeft

                event_down = CGEventCreateMouseEvent(None, down, (x, y), btn)
                event_up = CGEventCreateMouseEvent(None, up, (x, y), btn)
                CGEventPost(kCGHIDEventTap, event_down)
                CGEventPost(kCGHIDEventTap, event_up)
            except Exception: pass

        # MODO KEYBOARD
        elif mode == 1:
            # Si no hay tecla seleccionada, no hacer nada
            if not self.selected_keyboard_key:
                return

            try:
                kb = keyboard.Controller()
                kb.press(self.selected_keyboard_key)
                kb.release(self.selected_keyboard_key)
            except Exception as e:
                print("Keyboard error:", e)


    # --- LISTENERS KEYBOARD ---
    def start_keyboard_listener(self):
        def on_press(key):
            if self.is_terminating: return False
            self.pressed_keys.add(key)
            
            if self.is_setting_hotkey:
                try:
                    temp_str = self.format_hotkey_string(self.pressed_keys)
                    if not self.is_modifier(key):
                        self.target_hotkey = self.pressed_keys.copy()
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "updateHotkeyUI:", temp_str, False
                        )
                        self.pressed_keys.clear()
                except Exception as e: print(e)
                return
            
            # --- Detectar tecla para modo Keyboard ---
            if self.is_setting_keyboard_key:
                try:
                    keyname = self.get_key_name(key)
                    self.selected_keyboard_key = key
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "updateKeyboardKeyUI:", keyname, False
                    )
                    self.is_setting_keyboard_key = False
                except Exception as e:
                    print("Error selecting keyboard key:", e)
                return


            try:
                if self.pressed_keys == self.target_hotkey:
                    current_time = time.time()
                    if current_time - self.last_toggle_time > 0.5:
                        self.last_toggle_time = current_time
                        self.performSelectorOnMainThread_withObject_waitUntilDone_("toggleClickerFromHotkey:", None, False)
            except Exception: pass
            
        def on_release(key):
            try: self.pressed_keys.remove(key)
            except KeyError: pass

        self.keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()

    def toggleClickerFromHotkey_(self, obj):
        self.toggleState_(None)

    def quitApp_(self, sender):
        self.is_terminating = True
        if self.mouse_watchdog_timer: self.mouse_watchdog_timer.invalidate()
        self.cleanup()
        NSApplication.sharedApplication().terminate_(sender)

    def cleanup(self):
        self.stopClicker_(None)
        if self.keyboard_listener:
            try: self.keyboard_listener.stop()
            except Exception: pass

if __name__ == "__main__":
    app = NSApplication.sharedApplication()
    delegate = AutoClickerController.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()