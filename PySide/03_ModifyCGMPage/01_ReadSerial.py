import sys
import json
import serial
import serial.tools.list_ports
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from datetime import datetime
import struct

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QComboBox, QLineEdit,
    QSplitter, QFormLayout, QCheckBox, QFrame, QStackedWidget,
    QGridLayout, QFileDialog, QSpinBox, QPlainTextEdit, QGroupBox
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QObject
from PySide6.QtGui import QColor, QPainter, QTextCursor
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
import os
import csv


# =========================
# 1) Config / Protocol (单文件内置，避免循环导入)
# =========================

class FilterType(Enum):
    NONE = "无滤波"
    MOVING_AVG = "滑动平均"
    MEDIAN = "中值滤波"
    KALMAN = "卡尔曼滤波"


@dataclass
class UIConfig:
    ui_update_interval_ms: int = 50
    max_table_rows: int = 2000


@dataclass
class FilterConfig:
    filter_type: FilterType = FilterType.MOVING_AVG
    window_size: int = 5
    kalman_Q: float = 0.01
    kalman_R: float = 0.1


@dataclass
class SaveConfig:
    auto_save: bool = False
    save_interval_ms: int = 1000
    save_path: str = "./serial_data"


@dataclass
class ProtocolConfig:
    # 你设备的 JSON 字段名可能不同，可在这里扩展/修改
    time_keys: tuple = ("t", "time", "sec", "seconds", "timestamp")
    voltage_keys: tuple = ("voltage", "V", "v")
    uric_keys: tuple = ("uric", "uric_uA", "UA", "uA_uric")
    ascorbic_keys: tuple = ("ascorbic", "ascorbic_uA", "AA", "uA_ascorbic")
    glucose_keys: tuple = ("glucose", "glucose_mA", "GLU", "mA_glucose")

    # 工程量缩放（默认透传：若你的数据是 ADC 计数/原始量，需要在此设置倍率）
    time_unit: str = "auto"          # "auto" / "ms" / "s"
    voltage_scale: float = 1.0       # raw -> V
    uric_scale: float = 1.0          # raw -> uA
    ascorbic_scale: float = 1.0      # raw -> uA
    glucose_scale: float = 1.0       # raw -> mA


@dataclass
class AppConfig:
    ui: UIConfig = field(default_factory=UIConfig)
    filt: FilterConfig = field(default_factory=FilterConfig)
    save: SaveConfig = field(default_factory=SaveConfig)
    proto: ProtocolConfig = field(default_factory=ProtocolConfig)

    @staticmethod
    def _config_path() -> Path:
        return Path(__file__).with_name("app_config.json")

    @classmethod
    def load(cls):
        p = cls._config_path()
        if not p.exists():
            cfg = cls()
            cfg.save_to()
            return cfg

        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cfg = cls()
            cfg.save_to()
            return cfg

        cfg = cls()

        # ui
        ui = raw.get("ui", {})
        cfg.ui.ui_update_interval_ms = int(ui.get("ui_update_interval_ms", cfg.ui.ui_update_interval_ms))
        cfg.ui.max_table_rows = int(ui.get("max_table_rows", cfg.ui.max_table_rows))

        # filt
        flt = raw.get("filt", {})
        ft = flt.get("filter_type", cfg.filt.filter_type.value)
        cfg.filt.filter_type = _safe_filter_type(ft, default=cfg.filt.filter_type)
        cfg.filt.window_size = int(flt.get("window_size", cfg.filt.window_size))
        cfg.filt.kalman_Q = float(flt.get("kalman_Q", cfg.filt.kalman_Q))
        cfg.filt.kalman_R = float(flt.get("kalman_R", cfg.filt.kalman_R))

        # save
        sv = raw.get("save", {})
        cfg.save.auto_save = bool(sv.get("auto_save", cfg.save.auto_save))
        cfg.save.save_interval_ms = int(sv.get("save_interval_ms", cfg.save.save_interval_ms))
        cfg.save.save_path = str(sv.get("save_path", cfg.save.save_path))

        # proto
        pr = raw.get("proto", {})
        cfg.proto.time_unit = str(pr.get("time_unit", cfg.proto.time_unit))
        cfg.proto.voltage_scale = float(pr.get("voltage_scale", cfg.proto.voltage_scale))
        cfg.proto.uric_scale = float(pr.get("uric_scale", cfg.proto.uric_scale))
        cfg.proto.ascorbic_scale = float(pr.get("ascorbic_scale", cfg.proto.ascorbic_scale))
        cfg.proto.glucose_scale = float(pr.get("glucose_scale", cfg.proto.glucose_scale))

        return cfg

    def save_to(self):
        p = self._config_path()
        data = asdict(self)

        # Enum 序列化为 value
        data["filt"]["filter_type"] = self.filt.filter_type.value

        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_filter_type(v, default: FilterType) -> FilterType:
    # 既支持 value，也支持 name
    for ft in FilterType:
        if v == ft.value or v == ft.name:
            return ft
    return default


def _pick_first_key(d: dict, keys: tuple):
    for k in keys:
        if k in d:
            return d.get(k)
    return None


def _to_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def parse_frame(data: dict, cfg: AppConfig) -> dict:
    """
    将串口 JSON dict 归一化为统一字段：
    { "t": seconds, "voltage": V, "uric": uA, "ascorbic": uA, "glucose": mA }
    """
    pr = cfg.proto

    t_raw = _pick_first_key(data, pr.time_keys)
    v_raw = _pick_first_key(data, pr.voltage_keys)
    u_raw = _pick_first_key(data, pr.uric_keys)
    a_raw = _pick_first_key(data, pr.ascorbic_keys)
    g_raw = _pick_first_key(data, pr.glucose_keys)

    t = _to_float(t_raw, 0.0)

    # 时间单位处理
    if pr.time_unit == "ms":
        t_sec = t / 1000.0
    elif pr.time_unit == "s":
        t_sec = t
    else:
        # auto：经验判断，> 1e4 认为是 ms
        t_sec = t / 1000.0 if t > 1e4 else t

    frame = {
        "t": t_sec,
        "voltage": _to_float(v_raw, 0.0),
        "uric": _to_float(u_raw, 0.0),
        "ascorbic": _to_float(a_raw, 0.0),
        "glucose": _to_float(g_raw, 0.0),
    }
    return frame


def frame_to_engineering(frame: dict, cfg: AppConfig):
    """
    工程量换算（倍率默认 1.0=透传）
    返回：seconds, voltage(V), uric(uA), ascorbic(uA), glucose(mA)
    """
    pr = cfg.proto
    seconds = _to_float(frame.get("t"), 0.0)
    voltage = _to_float(frame.get("voltage"), 0.0) * pr.voltage_scale
    uric = _to_float(frame.get("uric"), 0.0) * pr.uric_scale
    ascorbic = _to_float(frame.get("ascorbic"), 0.0) * pr.ascorbic_scale
    glucose = _to_float(frame.get("glucose"), 0.0) * pr.glucose_scale
    return seconds, voltage, uric, ascorbic, glucose


# =========================
# 2) Serial Worker
# =========================

class SerialWorker(QObject):
    data_received = Signal(bytes)
    error_occurred = Signal(str)

    def __init__(self, serial_port, is_bluetooth=True):
        super().__init__()
        self.serial_port = serial_port
        self.running = False
        self.is_bluetooth = is_bluetooth
        self.base_interval = 20 if is_bluetooth else 5
        self.current_interval = self.base_interval

    def start(self):
        self.running = True
        self.read_data()

    def stop(self):
        self.running = False

    def read_data(self):
        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting:
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    self.data_received.emit(data)
                    self.current_interval = self.base_interval
                else:
                    self.current_interval = min(self.current_interval * 2, 500)
                QThread.msleep(self.current_interval)
            except Exception as e:
                self.error_occurred.emit(repr(e))
                self.running = False
                break


# =========================
# 3) Serial Page
# =========================

# =========================
# 3) Serial Page (修改版：支持二进制协议)
# =========================

class SerialPage(QWidget):
    data_received = Signal(dict)

    def __init__(self):
        super().__init__()
        self.serial_port = None
        self.worker = None
        self.worker_thread = None

        # 缓存区
        self.json_buffer = ""
        self.binary_buffer = bytearray()  # 【新增】二进制缓存

        self.is_connected = False
        self._is_bluetooth = False

        self.init_ui()
        self.refresh_ports()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # --- 状态栏 ---
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.StyledPanel)
        status_frame.setStyleSheet("background-color: #f5f5f5;")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 5, 10, 5)

        self.status_label = QLabel("状态: 未连接")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.status_label.setMinimumWidth(150)

        self.com_label = QLabel("当前设备: 无")
        self.com_label.setMinimumWidth(200)

        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.com_label)
        status_layout.addStretch()
        main_layout.addWidget(status_frame)
        main_layout.addSpacing(5)

        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setHandleWidth(5)

        upper_widget = QWidget()
        upper_layout = QVBoxLayout(upper_widget)
        upper_layout.setContentsMargins(0, 0, 0, 0)

        # --- 设备控制区 ---
        device_control_frame = QFrame()
        device_control_frame.setFrameShape(QFrame.StyledPanel)
        device_control_layout = QGridLayout(device_control_frame)
        device_control_layout.setContentsMargins(10, 10, 10, 10)
        device_control_layout.setSpacing(10)

        device_control_layout.addWidget(QLabel("串口号:"), 0, 0)
        self.port_combo = QComboBox()
        self.port_combo.setMinimumHeight(28)
        self.port_combo.setMinimumWidth(150)
        device_control_layout.addWidget(self.port_combo, 0, 1)

        device_control_layout.addWidget(QLabel("波特率:"), 0, 2)
        self.baudrate_combo = QComboBox()
        self.baudrate_combo.setMinimumHeight(28)
        self.baudrate_combo.setMinimumWidth(120)
        baudrates = ["1200", "2400", "4800", "9600", "19200", "38400",
                     "57600", "115200", "230400", "460800", "921600"]
        self.baudrate_combo.addItems(baudrates)
        self.baudrate_combo.setCurrentText("115200")
        device_control_layout.addWidget(self.baudrate_combo, 0, 3)

        device_control_layout.addWidget(QLabel("数据位:"), 0, 4)
        self.databit_combo = QComboBox()
        self.databit_combo.setMinimumHeight(28)
        self.databit_combo.setMinimumWidth(80)
        self.databit_combo.addItems(["5", "6", "7", "8"])
        self.databit_combo.setCurrentText("8")
        device_control_layout.addWidget(self.databit_combo, 0, 5)

        device_control_layout.addWidget(QLabel("停止位:"), 1, 0)
        self.stopbit_combo = QComboBox()
        self.stopbit_combo.setMinimumHeight(28)
        self.stopbit_combo.setMinimumWidth(80)
        self.stopbit_combo.addItems(["1", "1.5", "2"])
        self.stopbit_combo.setCurrentText("1")
        device_control_layout.addWidget(self.stopbit_combo, 1, 1)

        device_control_layout.addWidget(QLabel("校验位:"), 1, 2)
        self.parity_combo = QComboBox()
        self.parity_combo.setMinimumHeight(28)
        self.parity_combo.setMinimumWidth(120)
        self.parity_combo.addItems(["None", "Odd", "Even", "Mark", "Space"])
        self.parity_combo.setCurrentText("None")
        device_control_layout.addWidget(self.parity_combo, 1, 3)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setMinimumHeight(28)
        self.refresh_btn.setMinimumWidth(60)
        device_control_layout.addWidget(self.refresh_btn, 1, 4)

        self.connect_btn = QPushButton("打开串口")
        self.connect_btn.setMinimumHeight(28)
        self.connect_btn.setMinimumWidth(80)
        self.connect_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        device_control_layout.addWidget(self.connect_btn, 1, 5)

        options_layout = QHBoxLayout()
        self.auto_connect = QCheckBox("自动重连")
        self.save_params = QCheckBox("保存参数")
        options_layout.addWidget(self.auto_connect)
        options_layout.addWidget(self.save_params)
        device_control_layout.addLayout(options_layout, 2, 0, 1, 6)

        upper_layout.addWidget(device_control_frame)
        upper_layout.addSpacing(5)

        # --- 串口信息 ---
        info_frame = QFrame()
        info_frame.setFrameShape(QFrame.StyledPanel)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(10, 5, 10, 5)

        info_layout.addWidget(QLabel("串口信息:"))
        self.device_info = QTextEdit()
        self.device_info.setReadOnly(True)
        self.device_info.setMaximumHeight(60)
        self.device_info.setPlaceholderText("串口信息将显示在这里...")
        info_layout.addWidget(self.device_info)

        upper_layout.addWidget(info_frame)
        upper_layout.addSpacing(5)

        send_receive_splitter = QSplitter(Qt.Vertical)

        # --- 发送区 ---
        send_frame = QFrame()
        send_frame.setFrameShape(QFrame.StyledPanel)
        send_layout = QVBoxLayout(send_frame)
        send_layout.setContentsMargins(10, 10, 10, 10)

        send_title_layout = QHBoxLayout()
        send_title_layout.addWidget(QLabel("发送区"))
        send_title_layout.addStretch()

        send_btn_layout = QHBoxLayout()
        self.send_btn = QPushButton("发送")
        self.send_btn.setMinimumHeight(28)
        self.clear_send_btn = QPushButton("清空")
        self.clear_send_btn.setMinimumHeight(28)
        self.hex_send_check = QCheckBox("Hex发送")

        send_btn_layout.addWidget(self.send_btn)
        send_btn_layout.addWidget(self.clear_send_btn)
        send_btn_layout.addWidget(self.hex_send_check)
        send_title_layout.addLayout(send_btn_layout)

        self.send_text = QTextEdit()
        self.send_text.setMaximumHeight(80)
        self.send_text.setPlaceholderText("输入要发送的数据...")

        cmd_layout = QHBoxLayout()
        cmd_layout.addWidget(QLabel("快捷命令:"))
        self.cmd_start = QPushButton("START")
        self.cmd_pause = QPushButton("PAUSE")
        self.cmd_resume = QPushButton("RESUME")
        self.cmd_force_pause = QPushButton("ForcePause")

        for btn in [self.cmd_start, self.cmd_pause, self.cmd_resume, self.cmd_force_pause]:
            btn.setMinimumHeight(28)
            btn.setMinimumWidth(70)
            cmd_layout.addWidget(btn)

        send_layout.addLayout(send_title_layout)
        send_layout.addWidget(self.send_text)
        send_layout.addLayout(cmd_layout)

        send_receive_splitter.addWidget(send_frame)

        # --- 接收区 ---
        receive_frame = QFrame()
        receive_frame.setFrameShape(QFrame.StyledPanel)
        receive_layout = QVBoxLayout(receive_frame)
        receive_layout.setContentsMargins(10, 10, 10, 10)

        receive_title_layout = QHBoxLayout()
        receive_title_layout.addWidget(QLabel("接收区"))
        receive_title_layout.addStretch()

        receive_btn_layout = QHBoxLayout()
        self.clear_receive_btn = QPushButton("清空")
        self.clear_receive_btn.setMinimumHeight(28)

        self.hex_receive_check = QCheckBox("Hex显示")
        self.timestamp_check = QCheckBox("显示时间")
        self.auto_scroll_check = QCheckBox("自动滚动")
        self.auto_scroll_check.setChecked(True)

        # 【新增】二进制协议开关
        self.binary_mode_check = QCheckBox("二进制协议")
        self.binary_mode_check.setStyleSheet("color: blue; font-weight: bold;")
        self.binary_mode_check.setToolTip("选中后解析 15字节的二进制数据包 (Head:A5, Tail:5A)")

        receive_btn_layout.addWidget(self.clear_receive_btn)
        receive_btn_layout.addWidget(self.hex_receive_check)
        receive_btn_layout.addWidget(self.timestamp_check)
        receive_btn_layout.addWidget(self.auto_scroll_check)
        receive_btn_layout.addWidget(self.binary_mode_check)  # 添加到布局

        receive_title_layout.addLayout(receive_btn_layout)

        self.receive_text = QPlainTextEdit()
        self.receive_text.setReadOnly(True)
        self.receive_text.document().setMaximumBlockCount(1000)

        receive_layout.addLayout(receive_title_layout)
        receive_layout.addWidget(self.receive_text)

        send_receive_splitter.addWidget(receive_frame)
        send_receive_splitter.setSizes([220, 300])

        upper_layout.addWidget(send_receive_splitter)
        self.main_splitter.addWidget(upper_widget)
        main_layout.addWidget(self.main_splitter)

        # 信号连接
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.port_combo.currentIndexChanged.connect(self.on_port_selected)
        self.send_btn.clicked.connect(self.send_data)
        self.clear_send_btn.clicked.connect(lambda: self.send_text.clear())
        self.clear_receive_btn.clicked.connect(lambda: self.receive_text.clear())

        self.cmd_start.clicked.connect(lambda: self.send_shortcut("START"))
        self.cmd_pause.clicked.connect(lambda: self.send_shortcut("PAUSE"))
        self.cmd_resume.clicked.connect(lambda: self.send_shortcut("RESUME"))
        self.cmd_force_pause.clicked.connect(lambda: self.send_shortcut("ForcePause"))

    # ... (refresh_ports, on_port_selected, toggle_connection, open_serial, _async_open_serial, update_ui_connected_state, close_serial, start_worker, stop_worker 保持不变，为了节省篇幅省略，请直接复用原代码) ...
    # 为了完整性，这里列出没变的方法，你只需把原代码这部分保留即可。

    def refresh_ports(self):
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        if not ports:
            self.port_combo.addItem("无可用串口", None)
            self.device_info.clear()
            self.connect_btn.setEnabled(False)
            self.com_label.setText("当前设备: 无")
            return
        for p in ports:
            display_text = f"{p.device} ({p.description})"
            self.port_combo.addItem(display_text, p.device)
        self.port_combo.setCurrentIndex(0)
        self.on_port_selected(0)
        self.connect_btn.setEnabled(True)

    def on_port_selected(self, index):
        port_name = self.port_combo.itemData(index)
        if not port_name:
            self.device_info.clear()
            self.com_label.setText("当前设备: 无")
            return
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if p.device == port_name:
                info = f"端口: {p.device}\n描述: {p.description}\n硬件ID: {p.hwid}\n"
                self.device_info.setText(info)
                self.com_label.setText(f"当前设备: {p.device}")
                return

    def toggle_connection(self):
        if self.is_connected:
            self.close_serial()
        else:
            self.open_serial()

    def open_serial(self):
        port_name = self.port_combo.currentData()
        if not port_name:
            QMessageBox.warning(self, "错误", f"请选择有效的串口（当前：{self.port_combo.currentText()}）")
            return
        port_text = self.port_combo.currentText()
        self._is_bluetooth = ("Bluetooth" in port_text) or ("BTH" in port_text)
        QTimer.singleShot(0, lambda: self._async_open_serial())

    def _async_open_serial(self):
        try:
            port_name = self.port_combo.currentData()
            if not port_name:
                QMessageBox.warning(self, "错误", "端口选择无效")
                return
            baudrate = int(self.baudrate_combo.currentText())
            if self._is_bluetooth:
                baudrate = 9600
                self.baudrate_combo.setCurrentText("9600")
            databits = int(self.databit_combo.currentText())
            stopbits_text = self.stopbit_combo.currentText()
            if stopbits_text == "1":
                stopbits = serial.STOPBITS_ONE
            elif stopbits_text == "1.5":
                stopbits = getattr(serial, "STOPBITS_ONE_POINT_FIVE", serial.STOPBITS_ONE)
            else:
                stopbits = serial.STOPBITS_TWO
            parity_text = self.parity_combo.currentText()
            if parity_text == "None":
                parity = serial.PARITY_NONE
            elif parity_text == "Odd":
                parity = serial.PARITY_ODD
            elif parity_text == "Even":
                parity = serial.PARITY_EVEN
            elif parity_text == "Mark":
                parity = serial.PARITY_MARK
            else:
                parity = serial.PARITY_SPACE
            timeout = 2 if self._is_bluetooth else 0.1

            self.serial_port = serial.Serial(
                port=port_name, baudrate=baudrate, bytesize=databits,
                stopbits=stopbits, parity=parity, timeout=timeout
            )
            if self.serial_port.is_open:
                self.is_connected = True
                self.update_ui_connected_state()
                if self._is_bluetooth:
                    QTimer.singleShot(500, self.start_worker)
                else:
                    self.start_worker()
        except PermissionError as e:
            QMessageBox.warning(self, "权限错误", f"无法打开串口：\n{repr(e)}")
            self.serial_port = None
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开串口：\n{repr(e)}")
            self.serial_port = None

    def update_ui_connected_state(self):
        self.status_label.setText("状态: 已连接")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")
        self.connect_btn.setText("关闭串口")
        self.connect_btn.setStyleSheet("background-color: #f44336; color: white;")
        self.port_combo.setEnabled(False)
        self.baudrate_combo.setEnabled(False)
        self.databit_combo.setEnabled(False)
        self.stopbit_combo.setEnabled(False)
        self.parity_combo.setEnabled(False)
        self.refresh_btn.setEnabled(False)

    def close_serial(self):
        if self.serial_port and self.serial_port.is_open:
            self.stop_worker()
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.reset_connection_state()
        self.port_combo.setEnabled(True)
        self.baudrate_combo.setEnabled(True)
        self.databit_combo.setEnabled(True)
        self.stopbit_combo.setEnabled(True)
        self.parity_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)

    def start_worker(self):
        self.worker_thread = QThread()
        self.worker = SerialWorker(self.serial_port, is_bluetooth=self._is_bluetooth)
        self.worker.moveToThread(self.worker_thread)
        self.worker.data_received.connect(self.handle_data)
        self.worker.error_occurred.connect(self.handle_worker_error)
        self.worker_thread.started.connect(self.worker.start)
        self.worker_thread.start()

    def stop_worker(self):
        if self.worker and self.worker_thread:
            self.worker.stop()
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None

    # --- 【关键修改】处理数据入口 ---
    def handle_data(self, data: bytes):
        try:
            # 1. 显示部分
            display_text = ""

            if self.timestamp_check.isChecked():
                display_text += f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "

            # 如果开启了二进制协议，建议强制/自动按 Hex 显示，方便调试
            if self.hex_receive_check.isChecked() or self.binary_mode_check.isChecked():
                hex_str = ' '.join(f'{b:02X}' for b in data)
                display_text += hex_str
            else:
                raw_text = data.decode(errors='replace')
                display_text += raw_text

            self.receive_text.appendPlainText(display_text)

            if self.auto_scroll_check.isChecked():
                self.receive_text.moveCursor(QTextCursor.End)

            # 2. 解析部分 (分流)
            if self.binary_mode_check.isChecked():
                # --- 二进制路径 ---
                self.binary_buffer.extend(data)
                self.process_binary_buffer()
            else:
                # --- JSON 路径 ---
                # 只有未开启 Hex显示 时才尝试解析字符串 (避免把 Hex 字符串当 JSON 解)
                if not self.hex_receive_check.isChecked():
                    self.json_buffer += data.decode(errors='ignore')
                    self.process_json_buffer()

        except Exception as e:
            print(f"数据处理错误: {repr(e)}")

    # --- 【新增】二进制解析逻辑 ---
    def process_binary_buffer(self):
        """
        解析 C 结构体：
        Head(1) + Time(4) + Uric(2) + Ascorbic(2) + Glucose(2) + Code12(2) + Checksum(1) + Tail(1)
        Total = 15 Bytes
        """
        FRAME_LEN = 15
        HEAD = 0xA5
        TAIL = 0x5A

        while len(self.binary_buffer) >= FRAME_LEN:
            # 1. 检查帧头
            if self.binary_buffer[0] != HEAD:
                self.binary_buffer.pop(0)
                continue

            # 2. 检查帧尾
            if self.binary_buffer[FRAME_LEN - 1] != TAIL:
                # 可能是假头，丢弃头部继续找
                self.binary_buffer.pop(0)
                continue

            # 3. 提取候选帧
            frame_bytes = self.binary_buffer[:FRAME_LEN]

            # 4. 校验和 (Payload 累加)
            # Payload 范围：索引 1 到 12 (不含 13)
            # sum(frame_bytes[1:13])
            payload = frame_bytes[1:13]
            calc_sum = sum(payload) & 0xFF
            recv_sum = frame_bytes[13]

            if calc_sum == recv_sum:
                # === 校验通过，解析数据 ===
                try:
                    # < = Little Endian
                    # I = uint32 (Time)
                    # H = uint16 (Uric, Asc, Glu, Code12)
                    ms, uric, ascorbic, glucose, code12 = struct.unpack('<IHHHH', payload)

                    # 转换电压 (Code12 -> Voltage)
                    # 假设参考电压 3.3V, 12bit ADC (4095)
                    voltage_v = (code12 / 4095.0) * 3.3

                    # 构造标准字典
                    data_dict = {
                        "t": ms / 1000.0,  # 毫秒转秒
                        "voltage": voltage_v,
                        "uric": uric,
                        "ascorbic": ascorbic,
                        "glucose": glucose,
                        "receive_time": datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    }

                    # 发送给 DataMonitorPage
                    self.data_received.emit(data_dict)

                except Exception as e:
                    print(f"Binary Unpack Error: {e}")

                # 消费掉这帧数据
                del self.binary_buffer[:FRAME_LEN]
            else:
                # 校验失败
                print(f"Checksum Fail: Calc={calc_sum:02X}, Recv={recv_sum:02X}")
                self.binary_buffer.pop(0)

    # --- JSON 解析逻辑 (保持不变) ---
    def process_json_buffer(self):
        buf = self.json_buffer
        if not buf: return
        objs = []
        start = None
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(buf):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"': in_str = True; continue
            if ch == "{":
                if depth == 0: start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start is not None:
                        objs.append((start, i))
        last_consumed = -1
        for s, e in objs:
            json_str = buf[s:e + 1]
            last_consumed = e
            try:
                d = json.loads(json_str)
                d["receive_time"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self.data_received.emit(d)
            except json.JSONDecodeError:
                continue
        if last_consumed >= 0:
            self.json_buffer = buf[last_consumed + 1:]
        else:
            idx = buf.rfind("{")
            self.json_buffer = buf[idx:] if idx >= 0 else ""

    # ... (handle_worker_error, send_data, send_shortcut, reset_connection_state 保持不变) ...

    def handle_worker_error(self, error_msg):
        QMessageBox.warning(self, "接收错误", f"数据接收失败: {error_msg}")
        self.close_serial()
        if self.auto_connect.isChecked():
            self.status_label.setText("状态: 尝试自动重连...")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
            QTimer.singleShot(3000, self.open_serial)

    def send_data(self):
        if not self.is_connected or not self.serial_port or not self.serial_port.is_open:
            QMessageBox.warning(self, "错误", "请先打开串口")
            return
        text = self.send_text.toPlainText()
        if not text: return
        try:
            if self.hex_send_check.isChecked():
                text = text.replace(' ', '')
                data = bytes.fromhex(text)
            else:
                data = text.encode()
            self.serial_port.write(data)
        except Exception as e:
            QMessageBox.warning(self, "发送错误", f"发送失败: {repr(e)}")
            self.close_serial()

    def send_shortcut(self, cmd):
        self.send_text.setPlainText(cmd)
        self.hex_send_check.setChecked(False)
        self.send_data()

    def reset_connection_state(self):
        self.status_label.setText("状态: 未连接")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.connect_btn.setText("打开串口")
        self.connect_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self.is_connected = False


# =========================
# 4) Data Monitor Page
# =========================

class DataMonitorPage(QWidget):
    class KalmanFilter:
        def __init__(self, Q=0.01, R=0.1):
            self.Q = float(Q)
            self.R = float(R)
            self.x = None
            self.P = 0.1

        def update(self, z):
            z = float(z)
            if self.x is None:
                self.x = z
                return self.x
            P_pred = self.P + self.Q
            K = P_pred / (P_pred + self.R)
            self.x = self.x + K * (z - self.x)
            self.P = (1 - K) * P_pred
            return float(self.x)

        def reset(self):
            self.x = None
            self.P = 0.1

    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg

        self._pending_data = []
        self._ui_update_timer = QTimer(self)
        self._ui_update_timer.timeout.connect(self._flush_pending_data)
        self._ui_update_timer.start(int(self.cfg.ui.ui_update_interval_ms))

        self.max_time_data = 300
        self.glucose_time_data = []
        self.voltage_uric_data = []
        self.voltage_ascorbic_data = []
        self.voltage_glucose_data = []

        self.filter_buffers = {"uric": [], "ascorbic": [], "glucose": [], "voltage": []}
        self.kalman_filters = {}
        if self.cfg.filt.filter_type == FilterType.KALMAN:
            self._init_kalman_filters()

        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.timeout.connect(self.auto_save_data)
        self.cached_data = []
        self.csv_header = ["时间(秒)", "尿酸(uA)", "抗坏血酸(uA)", "葡萄糖（mA）", "电压(V)", "接收时间"]

        self.init_ui()
        self.apply_config(self.cfg)

    def apply_config(self, cfg: AppConfig):
        self.cfg = cfg
        self._ui_update_timer.setInterval(int(self.cfg.ui.ui_update_interval_ms))

        self.max_rows_spin.blockSignals(True)
        self.max_rows_spin.setValue(int(self.cfg.ui.max_table_rows))
        self.max_rows_spin.blockSignals(False)
        self._trim_table_rows()

        self.filter_type_combo.blockSignals(True)
        self.filter_type_combo.setCurrentText(self.cfg.filt.filter_type.value)
        self.filter_type_combo.blockSignals(False)

        self.window_size_spin.blockSignals(True)
        self.window_size_spin.setValue(int(self.cfg.filt.window_size))
        self.window_size_spin.blockSignals(False)

        self.kalman_Q_edit.blockSignals(True)
        self.kalman_Q_edit.setText(str(self.cfg.filt.kalman_Q))
        self.kalman_Q_edit.blockSignals(False)

        self.kalman_R_edit.blockSignals(True)
        self.kalman_R_edit.setText(str(self.cfg.filt.kalman_R))
        self.kalman_R_edit.blockSignals(False)

        is_kalman = self.cfg.filt.filter_type == FilterType.KALMAN
        self.window_size_spin.setEnabled(not is_kalman)
        self.kalman_param_group.setVisible(is_kalman)
        self._update_filter_status()

        for k in self.filter_buffers:
            self.filter_buffers[k].clear()
        self.kalman_filters.clear()
        if is_kalman:
            self._init_kalman_filters()

        if self.cfg.save.auto_save:
            self.auto_save_timer.stop()
            self.auto_save_timer.start(int(self.cfg.save.save_interval_ms))
            self.save_status_label.setText(f"保存状态：自动保存已启用（间隔{self.cfg.save.save_interval_ms}ms）")
            self.save_status_label.setStyleSheet("color: green;")
        else:
            self.auto_save_timer.stop()
            self.save_status_label.setText("保存状态：未启用自动保存")
            self.save_status_label.setStyleSheet("color: #666;")

        self._ensure_save_path_exists()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        save_ctrl_layout = QHBoxLayout()
        self.manual_save_btn = QPushButton("手动保存当前数据")
        self.manual_save_btn.clicked.connect(self.manual_save_data)

        self.clear_data_btn = QPushButton("清空监测数据")
        self.clear_data_btn.clicked.connect(self.clear_all_data)

        self.save_status_label = QLabel("保存状态：未启用自动保存")
        self.save_status_label.setStyleSheet("color: #666;")

        save_ctrl_layout.addWidget(self.manual_save_btn)
        save_ctrl_layout.addWidget(self.clear_data_btn)
        save_ctrl_layout.addStretch()
        save_ctrl_layout.addWidget(self.save_status_label)
        main_layout.addLayout(save_ctrl_layout)

        table_ctrl_layout = QHBoxLayout()
        table_ctrl_layout.addWidget(QLabel("表格最多显示行数:"))
        self.max_rows_spin = QSpinBox()
        self.max_rows_spin.setRange(100, 100000)
        self.max_rows_spin.setSingleStep(100)
        self.max_rows_spin.setValue(int(self.cfg.ui.max_table_rows))
        self.max_rows_spin.valueChanged.connect(self._update_max_rows)
        table_ctrl_layout.addWidget(self.max_rows_spin)
        table_ctrl_layout.addStretch()
        main_layout.addLayout(table_ctrl_layout)

        filter_ctrl_layout = QVBoxLayout()

        base_filter_layout = QHBoxLayout()
        base_filter_layout.addWidget(QLabel("滤波类型:"))
        self.filter_type_combo = QComboBox()
        self.filter_type_combo.addItems([ft.value for ft in FilterType])
        self.filter_type_combo.setCurrentText(self.cfg.filt.filter_type.value)
        self.filter_type_combo.currentTextChanged.connect(self.update_filter_config)
        base_filter_layout.addWidget(self.filter_type_combo)

        base_filter_layout.addWidget(QLabel("窗口大小:"))
        self.window_size_spin = QSpinBox()
        self.window_size_spin.setRange(3, 11)
        self.window_size_spin.setSingleStep(2)
        self.window_size_spin.setValue(int(self.cfg.filt.window_size))
        self.window_size_spin.valueChanged.connect(self.update_filter_config)
        base_filter_layout.addWidget(self.window_size_spin)
        base_filter_layout.addStretch()
        filter_ctrl_layout.addLayout(base_filter_layout)

        self.kalman_param_group = QGroupBox("卡尔曼滤波参数（Q=过程噪声，R=测量噪声）")
        kalman_param_layout = QHBoxLayout(self.kalman_param_group)

        kalman_param_layout.addWidget(QLabel("Q:"))
        self.kalman_Q_edit = QLineEdit(str(self.cfg.filt.kalman_Q))
        self.kalman_Q_edit.textChanged.connect(self.update_kalman_params)
        kalman_param_layout.addWidget(self.kalman_Q_edit)

        kalman_param_layout.addWidget(QLabel("R:"))
        self.kalman_R_edit = QLineEdit(str(self.cfg.filt.kalman_R))
        self.kalman_R_edit.textChanged.connect(self.update_kalman_params)
        kalman_param_layout.addWidget(self.kalman_R_edit)

        kalman_param_layout.addStretch()
        filter_ctrl_layout.addWidget(self.kalman_param_group)

        self.filter_status_label = QLabel("")
        filter_ctrl_layout.addWidget(self.filter_status_label)
        main_layout.addLayout(filter_ctrl_layout)

        chart_switch_layout = QHBoxLayout()
        self.time_chart_btn = QPushButton("时间-葡萄糖图表")
        self.volt_uric_btn = QPushButton("电压-尿酸图表")
        self.volt_ascorbic_btn = QPushButton("电压-抗坏血酸图表")
        self.voltage_glucose_btn = QPushButton("电压-葡萄糖图表")
        self.clear_chart_btn = QPushButton("清空图表")

        for btn in [self.time_chart_btn, self.volt_uric_btn, self.volt_ascorbic_btn, self.voltage_glucose_btn, self.clear_chart_btn]:
            btn.setMinimumHeight(30)

        self.time_chart_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")

        chart_switch_layout.addWidget(self.time_chart_btn)
        chart_switch_layout.addWidget(self.volt_uric_btn)
        chart_switch_layout.addWidget(self.volt_ascorbic_btn)
        chart_switch_layout.addWidget(self.voltage_glucose_btn)
        chart_switch_layout.addWidget(self.clear_chart_btn)
        main_layout.addLayout(chart_switch_layout)

        data_group = QGroupBox("实时数据（滤波后）")
        data_layout = QVBoxLayout(data_group)
        self.data_table = QTableWidget()
        self.data_table.setColumnCount(6)
        self.data_table.setHorizontalHeaderLabels(self.csv_header)
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.data_table.verticalHeader().setVisible(False)
        self.data_table.setEditTriggers(QTableWidget.NoEditTriggers)
        data_layout.addWidget(self.data_table)
        main_layout.addWidget(data_group, 1)

        self.chart_stack = QStackedWidget()
        main_layout.addWidget(self.chart_stack, 4)

        self.init_time_glucose_chart()
        self.init_volt_uric_chart()
        self.init_volt_ascorbic_chart()
        self.init_volt_glucose_chart()

        self.time_chart_btn.clicked.connect(lambda: self.switch_chart(0))
        self.volt_uric_btn.clicked.connect(lambda: self.switch_chart(1))
        self.volt_ascorbic_btn.clicked.connect(lambda: self.switch_chart(2))
        self.voltage_glucose_btn.clicked.connect(lambda: self.switch_chart(3))
        self.clear_chart_btn.clicked.connect(self.clear_charts)

        self.apply_config(self.cfg)

    def _init_kalman_filters(self):
        self.kalman_filters = {
            "uric": self.KalmanFilter(Q=self.cfg.filt.kalman_Q, R=self.cfg.filt.kalman_R),
            "ascorbic": self.KalmanFilter(Q=self.cfg.filt.kalman_Q, R=self.cfg.filt.kalman_R),
            "glucose": self.KalmanFilter(Q=self.cfg.filt.kalman_Q, R=self.cfg.filt.kalman_R),
            "voltage": self.KalmanFilter(Q=self.cfg.filt.kalman_Q, R=self.cfg.filt.kalman_R),
        }

    def update_kalman_params(self, *args):
        if self.cfg.filt.filter_type != FilterType.KALMAN:
            return
        try:
            q = float(self.kalman_Q_edit.text().strip())
            r = float(self.kalman_R_edit.text().strip())
            if q <= 0 or r <= 0:
                return
            self.cfg.filt.kalman_Q = q
            self.cfg.filt.kalman_R = r
            self._init_kalman_filters()
            self._update_filter_status()
        except Exception:
            pass

    def update_filter_config(self, *args):
        self.cfg.filt.filter_type = _safe_filter_type(self.filter_type_combo.currentText(), default=FilterType.MOVING_AVG)
        self.cfg.filt.window_size = int(self.window_size_spin.value())

        is_kalman = self.cfg.filt.filter_type == FilterType.KALMAN
        self.window_size_spin.setEnabled(not is_kalman)
        self.kalman_param_group.setVisible(is_kalman)

        for k in self.filter_buffers:
            self.filter_buffers[k].clear()
        self.kalman_filters.clear()
        if is_kalman:
            self._init_kalman_filters()

        self._update_filter_status()

    def _update_filter_status(self):
        if self.cfg.filt.filter_type == FilterType.KALMAN:
            self.filter_status_label.setText(f"当前：卡尔曼滤波（Q={self.cfg.filt.kalman_Q:.4f}, R={self.cfg.filt.kalman_R:.4f}）")
        else:
            self.filter_status_label.setText(f"当前：{self.cfg.filt.filter_type.value}（窗口{self.cfg.filt.window_size}）")

    def _apply_filter(self, key: str, raw_value: float) -> float:
        ft = self.cfg.filt.filter_type

        if ft == FilterType.KALMAN:
            if key not in self.kalman_filters:
                self._init_kalman_filters()
            return self.kalman_filters[key].update(raw_value)

        buf = self.filter_buffers[key]
        buf.append(float(raw_value))
        max_buf = int(self.cfg.filt.window_size) * 2
        if len(buf) > max_buf:
            del buf[:len(buf) - max_buf]

        if ft == FilterType.NONE:
            return float(raw_value)

        w = min(int(self.cfg.filt.window_size), len(buf))
        if w <= 0:
            return float(raw_value)

        if ft == FilterType.MOVING_AVG:
            return sum(buf[-w:]) / w

        if ft == FilterType.MEDIAN:
            s = sorted(buf[-w:])
            return s[len(s) // 2]

        return float(raw_value)

    def update_data(self, data: dict):
        self._pending_data.append(data)

    def _flush_pending_data(self):
        if not self._pending_data:
            return

        MAX_PER_TICK = 50
        batch = self._pending_data[:MAX_PER_TICK]
        self._pending_data = self._pending_data[MAX_PER_TICK:]

        for d in batch:
            self._process_single_data(d)

        idx = self.chart_stack.currentIndex()
        if idx == 0:
            self.update_time_glucose_chart()
        elif idx == 1:
            self.update_volt_uric_chart()
        elif idx == 2:
            self.update_volt_ascorbic_chart()
        elif idx == 3:
            self.update_volt_glucose_chart()

        if self.data_table.rowCount() > 0:
            self.data_table.scrollToBottom()

    def _process_single_data(self, data: dict):
        frame = parse_frame(data, self.cfg)
        seconds, voltage, uric_uA, ascorbic_uA, glucose_mA = frame_to_engineering(frame, self.cfg)
        receive_time = data.get("receive_time", "")

        uric_f = self._apply_filter("uric", uric_uA)
        ascorbic_f = self._apply_filter("ascorbic", ascorbic_uA)
        glucose_f = self._apply_filter("glucose", glucose_mA)
        voltage_f = voltage

        row = self.data_table.rowCount()
        self.data_table.insertRow(row)
        data_row = [
            round(seconds, 4),
            round(uric_f, 4),
            round(ascorbic_f, 4),
            round(glucose_f, 4),
            round(voltage_f, 4),
            receive_time
        ]
        for col, val in enumerate(data_row):
            self.data_table.setItem(row, col, QTableWidgetItem(str(val)))

        self._trim_table_rows()

        if self.cfg.save.auto_save:
            self.cached_data.append(data_row)

        self.glucose_time_data.append((seconds, glucose_f))
        self.voltage_uric_data.append((voltage_f, uric_f))
        self.voltage_ascorbic_data.append((voltage_f, ascorbic_f))
        self.voltage_glucose_data.append((voltage_f, glucose_f))

        self.glucose_time_data = [(t, g) for t, g in self.glucose_time_data if t >= (seconds - self.max_time_data)]
        max_volt_data = 2000
        self.voltage_uric_data = self.voltage_uric_data[-max_volt_data:]
        self.voltage_ascorbic_data = self.voltage_ascorbic_data[-max_volt_data:]
        self.voltage_glucose_data = self.voltage_glucose_data[-max_volt_data:]

    def _update_max_rows(self, value: int):
        self.cfg.ui.max_table_rows = int(value)
        self._trim_table_rows()

    def _trim_table_rows(self):
        max_rows = int(self.cfg.ui.max_table_rows)
        rc = self.data_table.rowCount()
        if rc > max_rows:
            extra = rc - max_rows
            for _ in range(extra):
                self.data_table.removeRow(0)

    def init_time_glucose_chart(self):
        yname = "葡萄糖（mA）"
        w = QWidget()
        lay = QVBoxLayout(w)

        self.time_glucose_chart = QChart()
        self.time_glucose_chart.setTitle("时间-葡萄糖监测数据（滤波后）")
        self.time_glucose_chart.legend().setVisible(True)
        self.time_glucose_chart.legend().setAlignment(Qt.AlignBottom)

        self.time_glucose_series = QLineSeries()
        self.time_glucose_series.setName(yname)
        self.time_glucose_series.setColor(QColor(255, 0, 0))
        self.time_glucose_chart.addSeries(self.time_glucose_series)

        self.time_glucose_axis_x = QValueAxis()
        self.time_glucose_axis_x.setTitleText("时间(秒)")
        self.time_glucose_axis_x.setRange(0, self.max_time_data)

        self.time_glucose_axis_y = QValueAxis()
        self.time_glucose_axis_y.setTitleText(yname)
        self.time_glucose_axis_y.setRange(0, 2000)

        self.time_glucose_chart.addAxis(self.time_glucose_axis_x, Qt.AlignBottom)
        self.time_glucose_chart.addAxis(self.time_glucose_axis_y, Qt.AlignLeft)
        self.time_glucose_series.attachAxis(self.time_glucose_axis_x)
        self.time_glucose_series.attachAxis(self.time_glucose_axis_y)

        view = QChartView(self.time_glucose_chart)
        view.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(view)
        self.chart_stack.addWidget(w)

    def init_volt_uric_chart(self):
        yname = "尿酸（uA）"
        w = QWidget()
        lay = QVBoxLayout(w)

        self.volt_uric_chart = QChart()
        self.volt_uric_chart.setTitle("电压-尿酸监测数据（滤波后）")
        self.volt_uric_chart.legend().setVisible(True)
        self.volt_uric_chart.legend().setAlignment(Qt.AlignBottom)

        self.volt_uric_series = QLineSeries()
        self.volt_uric_series.setName(yname)
        self.volt_uric_series.setColor(QColor(0, 0, 255))
        self.volt_uric_chart.addSeries(self.volt_uric_series)

        self.volt_uric_axis_x = QValueAxis()
        self.volt_uric_axis_x.setTitleText("电压(V)")
        self.volt_uric_axis_x.setRange(0, 5)

        self.volt_uric_axis_y = QValueAxis()
        self.volt_uric_axis_y.setTitleText(yname)
        self.volt_uric_axis_y.setRange(0, 2000)

        self.volt_uric_chart.addAxis(self.volt_uric_axis_x, Qt.AlignBottom)
        self.volt_uric_chart.addAxis(self.volt_uric_axis_y, Qt.AlignLeft)
        self.volt_uric_series.attachAxis(self.volt_uric_axis_x)
        self.volt_uric_series.attachAxis(self.volt_uric_axis_y)

        view = QChartView(self.volt_uric_chart)
        view.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(view)
        self.chart_stack.addWidget(w)

    def init_volt_ascorbic_chart(self):
        yname = "抗坏血酸（uA）"
        w = QWidget()
        lay = QVBoxLayout(w)

        self.volt_ascorbic_chart = QChart()
        self.volt_ascorbic_chart.setTitle("电压-抗坏血酸监测数据（滤波后）")
        self.volt_ascorbic_chart.legend().setVisible(True)
        self.volt_ascorbic_chart.legend().setAlignment(Qt.AlignBottom)

        self.volt_ascorbic_series = QLineSeries()
        self.volt_ascorbic_series.setName(yname)
        self.volt_ascorbic_series.setColor(QColor(0, 255, 0))
        self.volt_ascorbic_chart.addSeries(self.volt_ascorbic_series)

        self.volt_ascorbic_axis_x = QValueAxis()
        self.volt_ascorbic_axis_x.setTitleText("电压(V)")
        self.volt_ascorbic_axis_x.setRange(0, 5)

        self.volt_ascorbic_axis_y = QValueAxis()
        self.volt_ascorbic_axis_y.setTitleText(yname)
        self.volt_ascorbic_axis_y.setRange(0, 2000)

        self.volt_ascorbic_chart.addAxis(self.volt_ascorbic_axis_x, Qt.AlignBottom)
        self.volt_ascorbic_chart.addAxis(self.volt_ascorbic_axis_y, Qt.AlignLeft)
        self.volt_ascorbic_series.attachAxis(self.volt_ascorbic_axis_x)
        self.volt_ascorbic_series.attachAxis(self.volt_ascorbic_axis_y)

        view = QChartView(self.volt_ascorbic_chart)
        view.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(view)
        self.chart_stack.addWidget(w)

    def init_volt_glucose_chart(self):
        yname = "葡萄糖（mA）"
        w = QWidget()
        lay = QVBoxLayout(w)

        self.volt_glucose_chart = QChart()
        self.volt_glucose_chart.setTitle("电压-葡萄糖循环伏安")
        self.volt_glucose_chart.legend().setVisible(True)
        self.volt_glucose_chart.legend().setAlignment(Qt.AlignBottom)

        self.volt_glucose_axis_x = QValueAxis()
        self.volt_glucose_axis_x.setTitleText("电压(V)")
        self.volt_glucose_axis_x.setRange(0, 5)

        self.volt_glucose_axis_y = QValueAxis()
        self.volt_glucose_axis_y.setTitleText(yname)
        self.volt_glucose_axis_y.setRange(0, 2000)

        self.volt_glucose_chart.addAxis(self.volt_glucose_axis_x, Qt.AlignBottom)
        self.volt_glucose_chart.addAxis(self.volt_glucose_axis_y, Qt.AlignLeft)

        self.volt_glucose_series = QLineSeries()
        self.volt_glucose_series.setName(yname)
        self.volt_glucose_series.setColor(QColor(255, 165, 0))
        self.volt_glucose_chart.addSeries(self.volt_glucose_series)
        self.volt_glucose_series.attachAxis(self.volt_glucose_axis_x)
        self.volt_glucose_series.attachAxis(self.volt_glucose_axis_y)

        view = QChartView(self.volt_glucose_chart)
        view.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(view)
        self.chart_stack.addWidget(w)

    def switch_chart(self, index):
        self.chart_stack.setCurrentIndex(index)
        all_btns = [self.time_chart_btn, self.volt_uric_btn, self.volt_ascorbic_btn, self.voltage_glucose_btn]
        for btn in all_btns:
            btn.setStyleSheet("")
        if index == 0:
            self.time_chart_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")
        elif index == 1:
            self.volt_uric_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")
        elif index == 2:
            self.volt_ascorbic_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")
        elif index == 3:
            self.voltage_glucose_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")

    def update_time_glucose_chart(self):
        if not self.glucose_time_data:
            return
        self.time_glucose_series.clear()
        for t, g in self.glucose_time_data:
            self.time_glucose_series.append(t, g)

        max_time = max(t for t, _ in self.glucose_time_data)
        min_time = max(0, max_time - self.max_time_data)
        self.time_glucose_axis_x.setRange(min_time, max_time)

        max_g = max(g for _, g in self.glucose_time_data)
        min_g = min(g for _, g in self.glucose_time_data)
        margin = max(1, (max_g - min_g) * 0.1)
        self.time_glucose_axis_y.setRange(min_g - margin, max_g + margin)

    def update_volt_uric_chart(self):
        if not self.voltage_uric_data:
            return
        self.volt_uric_series.clear()
        for v, u in self.voltage_uric_data:
            self.volt_uric_series.append(v, u)

        max_v = max(v for v, _ in self.voltage_uric_data)
        min_v = min(v for v, _ in self.voltage_uric_data)
        self.volt_uric_axis_x.setRange(min_v - 0.1, max_v + 0.1)

        max_u = max(u for _, u in self.voltage_uric_data)
        min_u = min(u for _, u in self.voltage_uric_data)
        margin = max(1, (max_u - min_u) * 0.1)
        self.volt_uric_axis_y.setRange(min_u - margin, max_u + margin)

    def update_volt_ascorbic_chart(self):
        if not self.voltage_ascorbic_data:
            return
        self.volt_ascorbic_series.clear()
        for v, a in self.voltage_ascorbic_data:
            self.volt_ascorbic_series.append(v, a)

        max_v = max(v for v, _ in self.voltage_ascorbic_data)
        min_v = min(v for v, _ in self.voltage_ascorbic_data)
        self.volt_ascorbic_axis_x.setRange(min_v - 0.1, max_v + 0.1)

        max_a = max(a for _, a in self.voltage_ascorbic_data)
        min_a = min(a for _, a in self.voltage_ascorbic_data)
        margin = max(1, (max_a - min_a) * 0.1)
        self.volt_ascorbic_axis_y.setRange(min_a - margin, max_a + margin)

    def update_volt_glucose_chart(self):
        if not self.voltage_glucose_data:
            return
        self.volt_glucose_series.clear()
        for v, g in self.voltage_glucose_data:
            self.volt_glucose_series.append(v, g)

        max_v = max(v for v, _ in self.voltage_glucose_data)
        min_v = min(v for v, _ in self.voltage_glucose_data)
        self.volt_glucose_axis_x.setRange(min_v - 0.1, max_v + 0.1)

        max_g = max(g for _, g in self.voltage_glucose_data)
        min_g = min(g for _, g in self.voltage_glucose_data)
        margin = max(1, (max_g - min_g) * 0.1)
        self.volt_glucose_axis_y.setRange(min_g - margin, max_g + margin)

    def clear_charts(self):
        self.glucose_time_data.clear()
        self.voltage_uric_data.clear()
        self.voltage_ascorbic_data.clear()
        self.voltage_glucose_data.clear()

        self.time_glucose_series.clear()
        self.volt_uric_series.clear()
        self.volt_ascorbic_series.clear()
        self.volt_glucose_series.clear()

        self.time_glucose_axis_x.setRange(0, self.max_time_data)
        self.time_glucose_axis_y.setRange(0, 2000)
        self.volt_uric_axis_x.setRange(0, 5)
        self.volt_uric_axis_y.setRange(0, 2000)
        self.volt_ascorbic_axis_x.setRange(0, 5)
        self.volt_ascorbic_axis_y.setRange(0, 2000)
        self.volt_glucose_axis_x.setRange(0, 5)
        self.volt_glucose_axis_y.setRange(0, 2000)

    def clear_all_data(self):
        reply = QMessageBox.question(
            self, "确认清空",
            "确定要清空所有监测数据吗？\n（包含表格、图表、滤波缓存、待保存数据，不可恢复！）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.data_table.setRowCount(0)
        self.clear_charts()
        self._pending_data.clear()

        for k in self.filter_buffers:
            self.filter_buffers[k].clear()

        for f in self.kalman_filters.values():
            f.reset()

        self.cached_data.clear()
        QMessageBox.information(self, "清空成功", "所有监测数据已完全清空！")

    def _ensure_save_path_exists(self):
        p = self.cfg.save.save_path
        if not os.path.exists(p):
            try:
                os.makedirs(p)
            except Exception as e:
                QMessageBox.warning(self, "路径错误", f"无法创建保存路径：{repr(e)}\n将使用默认路径！")
                self.cfg.save.save_path = "./serial_data"
                os.makedirs(self.cfg.save.save_path, exist_ok=True)

    def _get_save_filename(self):
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.cfg.save.save_path, f"serial_data_{now}.csv")

    def _write_data_to_csv(self, data_rows, filename=None):
        if not data_rows:
            QMessageBox.warning(self, "无数据", "没有可保存的数据！")
            return False

        if not filename:
            filename = self._get_save_filename()
        else:
            if not filename.endswith(".csv"):
                filename += ".csv"

        try:
            with open(filename, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if os.path.getsize(filename) == 0:
                    writer.writerow(self.csv_header)
                writer.writerows(data_rows)
            return True
        except Exception as e:
            QMessageBox.warning(self, "保存错误", f"数据保存失败：{repr(e)}")
            return False

    def manual_save_data(self):
        all_data = []
        row_count = self.data_table.rowCount()
        for row in range(row_count):
            row_vals = []
            for col in range(6):
                item = self.data_table.item(row, col)
                row_vals.append(item.text() if item else "")
            all_data.append(row_vals)

        if not all_data:
            QMessageBox.warning(self, "无数据", "表格中没有可保存的数据！")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "保存数据到CSV文件",
            self._get_save_filename(),
            "CSV Files (*.csv);;All Files (*)"
        )
        if not filename:
            return

        if self._write_data_to_csv(all_data, filename):
            QMessageBox.information(self, "保存成功", f"数据已保存到：\n{filename}")

    def auto_save_data(self):
        if not self.cached_data:
            return
        if self._write_data_to_csv(self.cached_data):
            self.cached_data.clear()
            self.save_status_label.setStyleSheet("color: red;")
            QTimer.singleShot(500, lambda: self.save_status_label.setStyleSheet("color: green;"))


# =========================
# 5) Settings Page
# =========================

class SettingsPage(QWidget):
    config_changed = Signal(object)

    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.init_ui()
        self.apply_config(self.cfg)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        save_group = QGroupBox("数据保存设置")
        save_layout = QFormLayout(save_group)

        self.auto_save_check = QCheckBox("启用自动保存")
        self.auto_save_check.stateChanged.connect(self._on_ui_changed)
        save_layout.addRow(self.auto_save_check)

        self.save_interval_edit = QLineEdit()
        self.save_interval_edit.setPlaceholderText("输入自动保存间隔（毫秒），例如 1000")
        self.save_interval_edit.textChanged.connect(self._on_ui_changed)
        save_layout.addRow("自动保存间隔(ms):", self.save_interval_edit)

        path_layout = QHBoxLayout()
        self.save_path_edit = QLineEdit()
        self.select_path_btn = QPushButton("选择路径")
        self.select_path_btn.clicked.connect(self.select_save_path)
        path_layout.addWidget(self.save_path_edit)
        path_layout.addWidget(self.select_path_btn)
        save_layout.addRow("保存路径:", path_layout)

        main_layout.addWidget(save_group)
        main_layout.addStretch()

        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存设置")
        self.save_btn.clicked.connect(self.save_all_config)

        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.clicked.connect(self.reset_to_default)

        self.save_btn.setMinimumHeight(35)
        self.reset_btn.setMinimumHeight(35)

        btn_layout.addStretch()
        btn_layout.addWidget(self.reset_btn)
        btn_layout.addWidget(self.save_btn)
        main_layout.addLayout(btn_layout)

    def apply_config(self, cfg: AppConfig):
        self.cfg = cfg
        self.auto_save_check.blockSignals(True)
        self.save_interval_edit.blockSignals(True)
        self.save_path_edit.blockSignals(True)

        self.auto_save_check.setChecked(bool(self.cfg.save.auto_save))
        self.save_interval_edit.setText(str(int(self.cfg.save.save_interval_ms)))
        self.save_path_edit.setText(str(self.cfg.save.save_path))

        self.auto_save_check.blockSignals(False)
        self.save_interval_edit.blockSignals(False)
        self.save_path_edit.blockSignals(False)

    def _on_ui_changed(self, *args):
        self.cfg.save.auto_save = self.auto_save_check.isChecked()

        try:
            interval = int(self.save_interval_edit.text().strip())
            interval = max(100, interval)
        except Exception:
            interval = 1000
        self.cfg.save.save_interval_ms = interval

        p = self.save_path_edit.text().strip()
        if p:
            self.cfg.save.save_path = p

        self.config_changed.emit(self.cfg)

    def select_save_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择数据保存目录")
        if path:
            self.save_path_edit.setText(path)
            self._on_ui_changed()

    def save_all_config(self):
        self._on_ui_changed()
        QMessageBox.information(self, "提示", "设置已提交（将由主程序统一保存）")

    def reset_to_default(self):
        self.cfg.save.auto_save = False
        self.cfg.save.save_interval_ms = 1000
        self.cfg.save.save_path = "./serial_data"
        self.apply_config(self.cfg)
        self.config_changed.emit(self.cfg)
        QMessageBox.information(self, "提示", "保存设置已恢复默认值")


# =========================
# 6) Main Window
# =========================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("串口助手")
        self.setMinimumSize(1000, 800)

        self.cfg = AppConfig.load()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()

        self.serial_page = SerialPage()
        self.data_page = DataMonitorPage(self.cfg)
        self.settings_page = SettingsPage(self.cfg)

        self.stacked_widget.addWidget(self.serial_page)
        self.stacked_widget.addWidget(self.data_page)
        self.stacked_widget.addWidget(self.settings_page)

        self.bottom_nav = QWidget()
        self.bottom_nav.setStyleSheet("background-color: #f0f0f0; border-top: 1px solid #ccc;")
        nav_layout = QHBoxLayout(self.bottom_nav)
        nav_layout.setContentsMargins(10, 5, 10, 5)
        nav_layout.setSpacing(10)

        self.nav_btns = []
        self.add_nav_btn(nav_layout, "串口通信", 0)
        self.add_nav_btn(nav_layout, "数据监测", 1)
        self.add_nav_btn(nav_layout, "设置", 2)

        main_layout.addWidget(self.stacked_widget)
        main_layout.addWidget(self.bottom_nav)

        self.serial_page.data_received.connect(self.data_page.update_data)
        self.settings_page.config_changed.connect(self.on_config_changed)

        self.switch_page(0)

    def add_nav_btn(self, layout, text: str, index: int):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setMinimumHeight(36)
        btn.clicked.connect(lambda _=False, i=index: self.switch_page(i))
        layout.addWidget(btn)
        self.nav_btns.append(btn)

    def switch_page(self, index: int):
        self.stacked_widget.setCurrentIndex(index)
        self.set_nav_active(index)

    def set_nav_active(self, active_index: int):
        for i, btn in enumerate(self.nav_btns):
            is_active = (i == active_index)
            btn.setChecked(is_active)
            btn.setStyleSheet("background-color: #d0d0d0; font-weight: bold;" if is_active else "")

    def on_config_changed(self, cfg):
        self.cfg = cfg
        self.cfg.save_to()
        self.data_page.apply_config(cfg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
