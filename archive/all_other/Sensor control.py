# Froggy Eco Monitor - 4 plots, simulation mode, Arduino serial reader
# Reads lines like:
# [SCD41] CO2: 812 ppm | Temp: 22.34 °C | RH: 41.20 % | [O₂] 20.73 %vol

import os, re, sys, time, math, random, queue, threading
from collections import deque

# --- Optional/3rd party imports ---
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image, ImageTk, ImageDraw, ImageFont  # <-- added ImageDraw, ImageFont

HAS_SERIAL = True
try:
    import serial
    import serial.tools.list_ports
except Exception:
    HAS_SERIAL = False

HAS_MPL = True
try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except Exception:
    HAS_MPL = False


# ---------------- Emoji → Image helper (for the frog) ----------------
def emoji_img(size, text):
    """
    Render a color emoji as a Tk PhotoImage using the Segoe UI Emoji font.
    `size` is in pixels (square image).
    """
    # Try Segoe UI Emoji (Windows). If not available, fall back to default font.
    try:
        # pixels = points * 96 / 72 for 96-DPI Windows
        font = ImageFont.truetype("seguiemj.ttf", size=int(round(size * 72 / 96, 0)))
    except OSError:
        font = ImageFont.load_default()

    im = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(im)
    # Center the emoji in the square
    draw.text((size / 2, size / 2), text, embedded_color=True, font=font, anchor="mm")

    return ImageTk.PhotoImage(im)


# ---------------- Parsing: matches your Arduino output ----------------
PATTERN = re.compile(
    r"CO2:\s*(\d+)\s*ppm\s*\|\s*Temp:\s*([-\d\.]+)\s*°C\s*\|\s*RH:\s*([-\d\.]+)\s*%\s*\|\s*\[(?:O₂|O2|O)\]\s*([-\d\.]+)",
    re.IGNORECASE
)


# ---------------- Serial Reader Thread ----------------
class SerialReader(threading.Thread):
    def __init__(self, port, baud=115200, out_queue=None):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.q = out_queue or queue.Queue()
        self._stop = threading.Event()
        self.ser = None

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(1.2)  # Uno/Mega reset grace
            self.ser.reset_input_buffer()
        except Exception as e:
            self.q.put(("status", f" Could not open {self.port}: {e}"))
            return

        self.q.put(("status", f" Connected to {self.port}"))

        while not self._stop.is_set():
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").strip()
                m = PATTERN.search(line)
                if m:
                    co2 = float(m.group(1))
                    temp = float(m.group(2))
                    rh   = float(m.group(3))
                    o2   = float(m.group(4))
                    self.q.put(("data", {"co2": co2, "temp": temp, "rh": rh, "o2": o2}))
                else:
                    if line:
                        self.q.put(("raw", line))  # debug/ignore
            except Exception as e:
                self.q.put(("status", f"⚠️ Serial error: {e}"))
                break

        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except:
            pass
        self.q.put(("status", "🔌 Disconnected"))


# ---------------- Simulator Thread (no hardware needed) ----------------
class SimReader(threading.Thread):
    def __init__(self, out_queue=None, rate_hz=1.0):
        super().__init__(daemon=True)
        self.q = out_queue or queue.Queue()
        self._stop = threading.Event()
        self.rate = max(0.1, float(rate_hz))
        self.t0 = time.time()
        self.base_co2 = 650 + random.uniform(-40, 40)
        self.base_temp = 22.5 + random.uniform(-1, 1)
        self.base_rh = 42 + random.uniform(-5, 5)
        self.base_o2 = 20.7 + random.uniform(-0.2, 0.2)

    def stop(self):
        self._stop.set()

    def run(self):
        self.q.put(("status", "🧪 Simulation running"))
        dt = 1.0 / self.rate
        while not self._stop.is_set():
            t = time.time() - self.t0
            co2 = self.base_co2 + 30*math.sin(2*math.pi*0.015*t) + 8*math.sin(2*math.pi*0.21*t) + random.gauss(0,3)
            co2 = max(380, co2)
            temp = self.base_temp + 0.25*math.sin(2*math.pi*0.01*t) + random.gauss(0,0.03)
            rh = self.base_rh - 0.35*(temp - self.base_temp) + random.gauss(0,0.2)
            rh = max(15, min(85, rh))
            o2 = self.base_o2 + 0.02*math.sin(2*math.pi*0.02*t) + random.gauss(0,0.01)

            self.q.put(("data", {"co2": float(co2), "temp": float(temp), "rh": float(rh), "o2": float(o2)}))
            time.sleep(dt)
        self.q.put(("status", "🧪 Simulation stopped"))


# ---------------- Helpers ----------------
def list_ports():
    if not HAS_SERIAL:
        return []
    return [p.device for p in serial.tools.list_ports.comports()]


# ---------------- GUI ----------------
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")


class EcoApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Froggy Eco Monitor 🌿🐸")
        self.geometry("980x650")
        self.minsize(900, 600)

        self.reader = None
        self.q = queue.Queue()
        self.t0 = time.time()

        # thresholds
        self.T_GOOD = 800
        self.T_WARN = 1500

        # ---- Top bar ----
        top = ctk.CTkFrame(self, corner_radius=12)
        top.pack(fill="x", padx=12, pady=(12, 8))

        self.status_var = ctk.StringVar(value="Not connected")  # define early

        self.port_var = ctk.StringVar(value="SIMULATE (no hardware)")
        self.port_menu = ctk.CTkOptionMenu(
            top,
            values=["SIMULATE (no hardware)"] + list_ports(),
            variable=self.port_var,
            width=240
        )
        self.port_menu.pack(side="left", padx=(10, 6), pady=8)

        self.refresh_btn = ctk.CTkButton(top, text="↻ Refresh", command=self.refresh_ports, width=90)
        self.refresh_btn.pack(side="left", padx=6)

        self.connect_btn = ctk.CTkButton(top, text="Connect", command=self.toggle_connect, width=100)
        self.connect_btn.pack(side="left", padx=10)

        self.status_lbl = ctk.CTkLabel(top, textvariable=self.status_var)
        self.status_lbl.pack(side="left", padx=16)

        # ---- Main ----
        main = ctk.CTkFrame(self, corner_radius=12)
        main.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Left side (cards + plots)
        left = ctk.CTkFrame(main, corner_radius=12)
        left.pack(side="left", fill="both", expand=True, padx=12, pady=12)

        cards = ctk.CTkFrame(left, corner_radius=12)
        cards.pack(fill="x", padx=12, pady=(12, 8))

        self.co2_var  = ctk.StringVar(value="—")
        self.temp_var = ctk.StringVar(value="—")
        self.rh_var   = ctk.StringVar(value="—")
        self.o2_var   = ctk.StringVar(value="—")
        self.status_air = ctk.StringVar(value="Status: —")

        self._make_card(cards, "CO₂ (ppm)",      self.co2_var,  color='palegreen').pack(side="left", expand=True, fill="x", padx=6, pady=6)
        self._make_card(cards, "Temp (°C)",      self.temp_var, color='lightcoral').pack(side="left", expand=True, fill="x", padx=6, pady=6)
        self._make_card(cards, "Humidity (%)",   self.rh_var,   color='violet').pack(side="left", expand=True, fill="x", padx=6, pady=6)
        self._make_card(cards, "O₂ (%vol)",      self.o2_var,   color='lightsteelblue').pack(side="left", expand=True, fill="x", padx=6, pady=6)

        self.status_label = ctk.CTkLabel(left, textvariable=self.status_air, font=ctk.CTkFont(size=16, weight="bold"))
        self.status_label.pack(anchor="w", padx=18, pady=(0, 8))

        # --- 2x2 plots ---
        self.plot_frame = ctk.CTkFrame(left, corner_radius=12)
        self.plot_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas = None

        # Series buffers (shared time base)
        self.ts          = deque(maxlen=600)
        self.co2_series  = deque(maxlen=600)
        self.temp_series = deque(maxlen=600)
        self.rh_series   = deque(maxlen=600)
        self.o2_series   = deque(maxlen=600)

        if HAS_MPL:
            self.fig = Figure(figsize=(7.5, 4.2), dpi=100)
            self.ax_co2  = self.fig.add_subplot(221)
            self.ax_temp = self.fig.add_subplot(222)
            self.ax_rh   = self.fig.add_subplot(223)
            self.ax_o2   = self.fig.add_subplot(224)

            for ax, xlab, ylab in [
                (self.ax_co2,  "",         "CO₂ (ppm)"),
                (self.ax_temp, "",         "Temp (°C)"),
                (self.ax_rh,   "Time (s)", "RH (%)"),
                (self.ax_o2,   "Time (s)", "O₂ (%vol)"),
            ]:
                ax.grid(True, linestyle="--", alpha=0.35)
                ax.set_ylabel(ylab)
                if xlab:
                    ax.set_xlabel(xlab)

            (self.l_co2,)  = self.ax_co2.plot([], [], lw=1.8, color='palegreen')
            (self.l_temp,) = self.ax_temp.plot([], [], lw=1.8, color='lightcoral')
            (self.l_rh,)   = self.ax_rh.plot([], [], lw=1.8, color='violet')
            (self.l_o2,)   = self.ax_o2.plot([], [], lw=1.8, color='lightsteelblue')

            self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
            self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)
        else:
            ctk.CTkLabel(self.plot_frame, text="(Install matplotlib for live plots)").pack(pady=16)

        # Right side (mascots)
        right = ctk.CTkFrame(main, corner_radius=12, width=260)
        right.pack(side="right", fill="y", padx=12, pady=12)

        ctk.CTkLabel(right, text="Mascots", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(12, 8))

        # Frog + plant labels (frog will use emoji images)
        self.frog_label = ctk.CTkLabel(right, text="", width=160, height=160)
        self.frog_label.pack(pady=(6, 12))

        #self.plant_label = ctk.CTkLabel(right, text="🌱", font=ctk.CTkFont(size=40))
        #self.plant_label.pack(pady=(4, 12))

        self.tip_var = ctk.StringVar(value="—")
        ctk.CTkLabel(right, textvariable=self.tip_var, wraplength=220, justify="left").pack(padx=8, pady=(6, 12))

        # load emoji-based images
        self._load_images()

        # Set initial mascot state
        self._set_frog_image("happy")
        #self._set_plant_image("healthy")

        # initial ports fill
        self.refresh_ports()

        # queue pump
        self.after(60, self._pump_queue)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- UI helpers ----
    def _make_card(self, parent, title, var, color="#E8F8EF"):
        f = ctk.CTkFrame(parent, corner_radius=12, fg_color=color)
        ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=12)).pack(pady=(8, 0))
        ctk.CTkLabel(f, textvariable=var, font=ctk.CTkFont(size=26, weight="bold")).pack(pady=(2, 10))
        return f

    def refresh_ports(self):
        ports = ["SIMULATE (no hardware)"] + (list_ports() if HAS_SERIAL else [])
        self.port_menu.configure(values=ports)
        cur = self.port_var.get()
        if cur in ports:
            self.port_var.set(cur)
        else:
            self.port_var.set(ports[0] if ports else "SIMULATE (no hardware)")
        self.status_var.set("Select a port and Connect (or choose SIMULATE)")

    def toggle_connect(self):
        if self.reader:
            self.reader.stop()
            self.reader = None
            self.connect_btn.configure(text="Connect")
            self.status_var.set("Disconnecting…")
            return

        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Port", "Pick a port or SIMULATE")
            return

        if port.startswith("SIMULATE"):
            self.reader = SimReader(out_queue=self.q, rate_hz=1.0)
        else:
            if not HAS_SERIAL:
                messagebox.showerror("pyserial missing", "Install pyserial or use SIMULATE")
                return
            self.reader = SerialReader(port=port, baud=115200, out_queue=self.q)

        self.reader.start()
        self.connect_btn.configure(text="Disconnect")
        self.status_var.set(f"Connecting to {port}…")

    def _pump_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "raw":
                    # uncomment to peek: self.status_var.set(f"Raw: {payload[:48]}")
                    pass
                elif kind == "data":
                    self._update_readings(payload)
        except queue.Empty:
            pass
        except Exception as e:
            import traceback; traceback.print_exc()
        self.after(60, self._pump_queue)

    def _update_readings(self, d):
        co2  = d.get("co2")
        temp = d.get("temp")
        rh   = d.get("rh")
        o2   = d.get("o2")

        if co2  is not None: self.co2_var.set(f"{co2:.0f}")
        if temp is not None: self.temp_var.set(f"{temp:.2f}")
        if rh   is not None: self.rh_var.set(f"{rh:.1f}")
        if o2   is not None: self.o2_var.set(f"{o2:.2f}")

        # update plots
        if HAS_MPL and (co2 is not None):
            t = time.time() - self.t0
            self.ts.append(t)
            self.co2_series.append(co2)
            if temp is not None: self.temp_series.append(temp)
            if rh   is not None: self.rh_series.append(rh)
            if o2   is not None: self.o2_series.append(o2)

            xt     = list(self.ts)
            y_co2  = list(self.co2_series)
            y_temp = list(self.temp_series)
            y_rh   = list(self.rh_series)
            y_o2   = list(self.o2_series)

            self.l_co2.set_data(xt, y_co2)
            if y_temp: self.l_temp.set_data(xt[:len(y_temp)], y_temp)
            if y_rh:   self.l_rh.set_data(xt[:len(y_rh)],   y_rh)
            if y_o2:   self.l_o2.set_data(xt[:len(y_o2)],   y_o2)

            def autoscale(ax, x, y, pad_frac=0.08, pad_abs=0.02):
                if len(x) < 2 or len(y) < 2:
                    return
                ax.set_xlim(x[0], x[-1] + 0.1)
                ymin, ymax = min(y), max(y)
                if ymin == ymax:
                    ymin -= 1
                    ymax += 1
                pad = max((ymax - ymin) * pad_frac, pad_abs * max(abs(ymin), abs(ymax), 1))
                ax.set_ylim(ymin - pad, ymax + pad)

            autoscale(self.ax_co2,  xt,                y_co2)
            if y_temp: autoscale(self.ax_temp, xt[:len(y_temp)], y_temp)
            if y_rh:   autoscale(self.ax_rh,   xt[:len(y_rh)],   y_rh)
            if y_o2:   autoscale(self.ax_o2,   xt[:len(y_o2)],   y_o2)

            if self.canvas:
                self.canvas.draw_idle()

        # theme/mascots
        if co2 is not None:
            self._apply_theme_for_co2(co2)

    def _apply_theme_for_co2(self, co2):
        if co2 < self.T_GOOD:
            bg = "#00FF44"
            status = "Status: Fresh air"
            #tip = "Fred the frog is happy 🐸"
            frog_state = "happy"
            #plant_state = "healthy"
        elif co2 < self.T_WARN:
            bg = "#E01B1B"
            status = "Status: Getting stuffy"
            tip = "Consider opening a window or turning on ventilation."
            frog_state = "neutral"
            #plant_state = "ok"
        else:
            bg = "#BA6AFF"
            status = "Status: High CO₂ — ventilate!"
            tip = "Open doors/windows or increase airflow to reduce CO₂."
            frog_state = "dizzy"
            #plant_state = "wilt"

        self.configure(fg_color=bg)
        self.status_air.set(status)
        self.tip_var.set(tip)
        self._set_frog_image(frog_state)
        #self._set_plant_image(plant_state)

    # ---- Images ----
    def _load_img_safe(self, path, size):
        try:
            im = Image.open(path).convert("RGBA")
            if size:
                im = im.resize(size, Image.LANCZOS)
            return ImageTk.PhotoImage(im)
        except Exception:
            return None

    def _load_images(self):
        # Use emoji-based frogs instead of PNGs
        self.imgs = {
            "frog_happy":   emoji_img(140, "🦉"),        # happy frog
            "frog_neutral": emoji_img(140, "😐🐸"),      # neutral + frog
            "frog_dizzy":   emoji_img(140, "😵‍💫🐸"),    # dizzy frog
            # Plants: use text fallback only (no image), so set to None
            #"plant_healthy": None,
            #"plant_ok":      None,
            #"plant_wilt":    None,
        }

    def _set_frog_image(self, state):
        key = f"frog_{state}"
        img = self.imgs.get(key)
        if img:
            self.frog_label.configure(image=img, text="")
            self.frog_label.image = img
        else:
            # text fallback if image not available
            fallback = {"happy": "🐸", "neutral": "😐🐸", "dizzy": "😵‍💫🐸"}.get(state, "🐸")
            self.frog_label.configure(image=None, text=fallback)

    # def _set_plant_image(self, state):
    #     key = f"plant_{state}"
    #     img = self.imgs.get(key)
    #     if img:
    #         self.plant_label.configure(image=img, text="")
    #         self.plant_label.image = img
    #     else:
    #         # text fallback for plants
    #         fallback = {"healthy": "🌱", "ok": "🌿", "wilt": "🥀"}.get(state, "🌱")
    #         self.plant_label.configure(image=None, text=fallback)

    def on_close(self):
        try:
            if self.reader:
                self.reader.stop()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = EcoApp()
    app.mainloop()
