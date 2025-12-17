import sys
import json
import asyncio
import qasync
import serial
import serial.tools.list_ports
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel, QTextEdit, QTableWidget, QTableWidgetItem,
                               QHeaderView, QMessageBox, QGroupBox, QComboBox, QLineEdit,
                               QSplitter, QFormLayout, QCheckBox, QFrame, QStackedWidget,
                               QGridLayout, QFileDialog, QSpinBox, QPlainTextEdit)
from PySide6.QtCore import Qt, Signal, Slot, QSize, QTimer, QThread, QObject
from PySide6.QtGui import QFont, QColor, QPainter, QIcon, QTextCursor
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
import os  # 新增：用于路径处理
import csv  # 新增：用于CSV文件写入




class SerialWorker(QObject):
    """串口工作线程，处理数据接收"""
    data_received = Signal(bytes)
    error_occurred = Signal(str)

    def __init__(self, serial_port, is_bluetooth=True):
        super().__init__()
        self.serial_port = serial_port
        self.running = False
        self.is_bluetooth = is_bluetooth  # 标记是否为蓝牙串口
        # 基础轮询间隔：蓝牙50ms，物理串口10ms
        self.base_interval = 20 if is_bluetooth else 5
        self.current_interval = self.base_interval

    def start(self):
        self.running = True
        self.read_data()

    def stop(self):
        self.running = False

    def read_data(self):
        while self.running and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting:
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    self.data_received.emit(data)
                    self.current_interval = self.base_interval  # 有数据，恢复基础间隔
                else:
                    # 无数据，延长间隔（最大不超过500ms，避免响应过慢）
                    self.current_interval = min(self.current_interval * 2, 500)
                QThread.msleep(self.current_interval)  # 动态间隔轮询
            except Exception as e:
                self.error_occurred.emit(str(e))
                self.running = False
                break


class SerialPage(QWidget):
    """串口页面，带设备选择下拉框和参数设置，风格接近ATK-XCOM"""
    data_received = Signal(dict)

    def __init__(self):
        super().__init__()
        self.serial_port = None
        self.worker = None
        self.worker_thread = None
        self.json_buffer = ""  # JSON数据缓冲区
        self.init_ui()
        self.refresh_ports()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 顶部状态栏 - 更紧凑的设计
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

        # 主分割器
        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setHandleWidth(5)

        # 上部：设备和控制区 - 采用ATK-XCOM风格布局
        upper_widget = QWidget()
        upper_layout = QVBoxLayout(upper_widget)
        upper_layout.setContentsMargins(0, 0, 0, 0)

        # 设备选择和连接控制区 - 类似ATK-XCOM的紧凑布局
        device_control_frame = QFrame()
        device_control_frame.setFrameShape(QFrame.StyledPanel)
        device_control_layout = QGridLayout(device_control_frame)
        device_control_layout.setContentsMargins(10, 10, 10, 10)
        device_control_layout.setSpacing(10)

        # 串口选择
        device_control_layout.addWidget(QLabel("串口号:"), 0, 0)
        self.port_combo = QComboBox()
        self.port_combo.setMinimumHeight(28)
        self.port_combo.setMinimumWidth(150)
        device_control_layout.addWidget(self.port_combo, 0, 1)

        # 波特率选择
        device_control_layout.addWidget(QLabel("波特率:"), 0, 2)
        self.baudrate_combo = QComboBox()
        self.baudrate_combo.setMinimumHeight(28)
        self.baudrate_combo.setMinimumWidth(120)
        # 常见波特率
        baudrates = ["1200", "2400", "4800", "9600", "19200", "38400",
                     "57600", "115200", "230400", "460800", "921600"]
        self.baudrate_combo.addItems(baudrates)
        self.baudrate_combo.setCurrentText("115200")  # 默认115200
        device_control_layout.addWidget(self.baudrate_combo, 0, 3)

        # 数据位选择
        device_control_layout.addWidget(QLabel("数据位:"), 0, 4)
        self.databit_combo = QComboBox()
        self.databit_combo.setMinimumHeight(28)
        self.databit_combo.setMinimumWidth(80)
        self.databit_combo.addItems(["5", "6", "7", "8"])
        self.databit_combo.setCurrentText("8")
        device_control_layout.addWidget(self.databit_combo, 0, 5)

        # 停止位选择
        device_control_layout.addWidget(QLabel("停止位:"), 1, 0)
        self.stopbit_combo = QComboBox()
        self.stopbit_combo.setMinimumHeight(28)
        self.stopbit_combo.setMinimumWidth(80)
        self.stopbit_combo.addItems(["1", "1.5", "2"])
        self.stopbit_combo.setCurrentText("1")
        device_control_layout.addWidget(self.stopbit_combo, 1, 1)

        # 校验位选择
        device_control_layout.addWidget(QLabel("校验位:"), 1, 2)
        self.parity_combo = QComboBox()
        self.parity_combo.setMinimumHeight(28)
        self.parity_combo.setMinimumWidth(120)
        self.parity_combo.addItems(["None", "Odd", "Even", "Mark", "Space"])
        self.parity_combo.setCurrentText("None")
        device_control_layout.addWidget(self.parity_combo, 1, 3)

        # 刷新和连接按钮
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setMinimumHeight(28)
        self.refresh_btn.setMinimumWidth(60)
        device_control_layout.addWidget(self.refresh_btn, 1, 4)

        self.connect_btn = QPushButton("打开串口")
        self.connect_btn.setMinimumHeight(28)
        self.connect_btn.setMinimumWidth(80)
        self.connect_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        device_control_layout.addWidget(self.connect_btn, 1, 5)

        # 选项区域
        options_layout = QHBoxLayout()
        self.auto_connect = QCheckBox("自动重连")
        self.save_params = QCheckBox("保存参数")
        options_layout.addWidget(self.auto_connect)
        options_layout.addWidget(self.save_params)
        device_control_layout.addLayout(options_layout, 2, 0, 1, 6)

        upper_layout.addWidget(device_control_frame)
        upper_layout.addSpacing(5)

        # 设备信息显示区域
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

        # 发送和接收区域分割
        send_receive_splitter = QSplitter(Qt.Vertical)

        # 发送区域 - 类似ATK-XCOM的布局
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

        # 快捷命令区
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

        # 接收区 - ATK-XCOM风格
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

        receive_btn_layout.addWidget(self.clear_receive_btn)
        receive_btn_layout.addWidget(self.hex_receive_check)
        receive_btn_layout.addWidget(self.timestamp_check)
        receive_btn_layout.addWidget(self.auto_scroll_check)
        receive_title_layout.addLayout(receive_btn_layout)

        self.receive_text = QPlainTextEdit()
        self.receive_text.setReadOnly(True)
        # 让 Qt 自动限制最大行数（block 数）
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

        # 快捷命令连接
        self.cmd_start.clicked.connect(lambda: self.send_shortcut("START"))
        self.cmd_pause.clicked.connect(lambda: self.send_shortcut("PAUSE"))
        self.cmd_resume.clicked.connect(lambda: self.send_shortcut("RESUME"))
        self.cmd_force_pause.clicked.connect(lambda: self.send_shortcut("ForcePause"))

        self.is_connected = False

    def refresh_ports(self):
        """刷新串口列表"""
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()

        if not ports:
            self.port_combo.addItem("无可用串口")
            self.device_info.clear()
            self.connect_btn.setEnabled(False)
            return

        for port in ports:
            # 显示格式: 端口名 (描述)
            display_text = f"{port.device} ({port.description})"
            self.port_combo.addItem(display_text, port.device)

        self.port_combo.setCurrentIndex(0)
        self.on_port_selected(0)
        self.connect_btn.setEnabled(True)

    def on_port_selected(self, index):
        """处理端口选择变化"""
        if index >= 0 and self.port_combo.count() > 0 and self.port_combo.itemData(index):
            port_name = self.port_combo.itemData(index)
            ports = serial.tools.list_ports.comports()

            for port in ports:
                if port.device == port_name:
                    info = f"端口: {port.device}\n"
                    info += f"描述: {port.description}\n"
                    info += f"硬件ID: {port.hwid}\n"
                    self.device_info.setText(info)
                    self.com_label.setText(f"当前设备: {port.device}")
                    return

    def toggle_connection(self):
        """切换连接状态（打开/关闭串口）"""
        if self.is_connected:
            self.close_serial()
        else:
            self.open_serial()

    from PySide6.QtCore import QTimer

    def open_serial(self):
        if self.port_combo.count() == 0 or not self.port_combo.itemData(0):
            QMessageBox.warning(self, "错误", "请选择有效的串口")
            return

        # 1. 先判断是否为蓝牙串口（通过端口描述识别，如包含"Bluetooth"）
        port_text = self.port_combo.currentText()
        is_bluetooth = "Bluetooth" in port_text or "BTH" in port_text  # 适配不同系统的蓝牙端口描述

        # 2. 异步执行连接逻辑（避免阻塞主线程）
        QTimer.singleShot(0, lambda: self._async_open_serial(is_bluetooth))

    def _async_open_serial(self, is_bluetooth):
        """异步执行串口打开逻辑，单独处理蓝牙连接"""
        try:
            # 获取串口参数（蓝牙默认参数优化：若为蓝牙，强制默认9600bps，可根据模块调整）
            port_name = self.port_combo.itemData(self.port_combo.currentIndex())
            baudrate = int(self.baudrate_combo.currentText())
            if is_bluetooth:
                # 强制蓝牙使用默认参数（避免参数不匹配导致重试）
                baudrate = 9600
                self.baudrate_combo.setCurrentText("9600")  # 同步UI显示

            databits = int(self.databit_combo.currentText())

            # 转换停止位
            stopbits_text = self.stopbit_combo.currentText()
            if stopbits_text == "1":
                stopbits = serial.STOPBITS_ONE
            elif stopbits_text == "1.5":
                stopbits = serial.STOPBITS_ONE_POINT_FIVE
            else:  # 2
                stopbits = serial.STOPBITS_TWO

            # 转换校验位
            parity_text = self.parity_combo.currentText()
            if parity_text == "None":
                parity = serial.PARITY_NONE
            elif parity_text == "Odd":
                parity = serial.PARITY_ODD
            elif parity_text == "Even":
                parity = serial.PARITY_EVEN
            elif parity_text == "Mark":
                parity = serial.PARITY_MARK
            else:  # Space
                parity = serial.PARITY_SPACE

            # 3. 蓝牙连接添加超时（物理串口无需，蓝牙需延长超时）
            timeout = 2 if is_bluetooth else 0.1  # 蓝牙超时2s，物理串口0.1s
            self.serial_port = serial.Serial(
                port=port_name,
                baudrate=baudrate,
                bytesize=databits,
                stopbits=stopbits,
                parity=parity,
                timeout=timeout  # 关键：蓝牙延长超时
            )

            # 4. 蓝牙连接后延迟启动线程（等待握手完成）
            if self.serial_port.is_open:
                self.is_connected = True
                self.update_ui_connected_state()  # 单独抽离UI更新逻辑
                if is_bluetooth:
                    # 蓝牙延迟500ms启动线程，等待数据链路就绪
                    QTimer.singleShot(500, self.start_worker)
                else:
                    self.start_worker()  # 物理串口立即启动

        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开串口: {str(e)}")
            self.serial_port = None

    def update_ui_connected_state(self):
        """抽离UI更新逻辑，避免代码冗余"""
        self.status_label.setText("状态: 已连接")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")
        self.connect_btn.setText("关闭串口")
        self.connect_btn.setStyleSheet("background-color: #f44336; color: white;")
        # （其余禁用参数控件逻辑不变，省略...）

    def close_serial(self):
        """关闭串口"""
        if self.serial_port and self.serial_port.is_open:
            self.stop_worker()
            self.serial_port.close()

        self.reset_connection_state()

        # 启用参数修改
        self.port_combo.setEnabled(True)
        self.baudrate_combo.setEnabled(True)
        self.databit_combo.setEnabled(True)
        self.stopbit_combo.setEnabled(True)
        self.parity_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)

    def start_worker(self):
        """启动数据接收线程"""
        self.worker_thread = QThread()
        self.worker = SerialWorker(self.serial_port)
        self.worker.moveToThread(self.worker_thread)

        self.worker.data_received.connect(self.handle_data)
        self.worker.error_occurred.connect(self.handle_worker_error)
        self.worker_thread.started.connect(self.worker.start)

        self.worker_thread.start()

    def stop_worker(self):
        """停止数据接收线程"""
        if self.worker and self.worker_thread:
            self.worker.stop()
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None

    def handle_data(self, data: bytes):
        """处理接收到的数据，增加缓冲区机制处理连续JSON数据"""
        try:
            display_text = ""
            raw_data = ""

            # 处理显示文本和原始数据
            if self.timestamp_check.isChecked():
                timestamp = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "
                display_text += timestamp
            else:
                timestamp = ""

            if self.hex_receive_check.isChecked():
                # Hex显示
                hex_str = ' '.join(f'{b:02X}' for b in data)
                display_text += hex_str
            else:
                # 文本显示
                raw_text = data.decode(errors='replace')
                display_text += raw_text
                raw_data = raw_text  # 保存原始文本用于JSON解析

            # 添加到接收区
            self.receive_text.appendPlainText(display_text)

            # 自动滚动
            if self.auto_scroll_check.isChecked():
                self.receive_text.moveCursor(QTextCursor.End)

            # 非Hex模式下处理JSON数据缓冲区
            if not self.hex_receive_check.isChecked():
                # 将新接收的原始数据添加到缓冲区
                self.json_buffer += raw_data
                self.process_json_buffer()

        except Exception as e:
            print(f"数据处理错误: {e}")

    def process_json_buffer(self):
        """处理JSON缓冲区，提取并解析完整的JSON对象"""
        while True:
            # 查找JSON起始位置
            start_idx = self.json_buffer.find('{')
            if start_idx == -1:
                self.json_buffer = ""
                return

            # 从起始位置开始查找结束符
            end_idx = self.json_buffer.find('}', start_idx)
            if end_idx == -1:
                self.json_buffer = self.json_buffer[start_idx:]
                return

            # 提取完整的JSON片段
            json_str = self.json_buffer[start_idx:end_idx + 1]
            self.json_buffer = self.json_buffer[end_idx + 1:]

            # 尝试解析JSON
            try:
                json_data = json.loads(json_str)
                json_data["receive_time"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self.data_received.emit(json_data)
                print(f"解析到完整JSON: {json_data}")

            except json.JSONDecodeError as e:
                print(f"JSON解析错误: {e}，错误片段: {json_str}")
                continue

    def handle_worker_error(self, error_msg):
        """处理工作线程错误"""
        QMessageBox.warning(self, "接收错误", f"数据接收失败: {error_msg}")
        self.close_serial()

        # 如果勾选了自动重连，尝试重新连接
        if self.auto_connect.isChecked():
            self.status_label.setText("状态: 尝试自动重连...")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
            QTimer.singleShot(3000, self.open_serial)

    def send_data(self):
        """发送数据"""
        if not self.is_connected or not self.serial_port or not self.serial_port.is_open:
            QMessageBox.warning(self, "错误", "请先打开串口")
            return

        text = self.send_text.toPlainText()
        if not text:
            return

        try:
            if self.hex_send_check.isChecked():
                # Hex发送
                text = text.replace(' ', '')
                data = bytes.fromhex(text)
            else:
                # 文本发送
                data = text.encode()

            # 发送数据
            self.serial_port.write(data)
        except Exception as e:
            QMessageBox.warning(self, "发送错误", f"发送失败: {str(e)}")
            self.close_serial()

    def send_shortcut(self, cmd):
        """发送快捷命令"""
        self.send_text.setPlainText(cmd)
        self.hex_send_check.setChecked(False)  # 快捷命令默认文本模式
        self.send_data()

    def reset_connection_state(self):
        """重置连接状态"""
        self.status_label.setText("状态: 未连接")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.connect_btn.setText("打开串口")
        self.connect_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self.is_connected = False


class DataMonitorPage(QWidget):
    """数据监测页面（集成卡尔曼滤波）"""

    def adc_value_transform_to_current(self, adc_value, gain):
        # 电压-电流转换（原有逻辑，保留）
        voltage = (adc_value - self.ref_volt * self.adc_value_per_volt) / self.adc_value_per_volt
        current = voltage / gain
        return current

    def __init__(self):
        super().__init__()

        # 新增：用于缓冲从串口来的数据，减少UI更新频率
        self._pending_data = []  # 缓存待处理JSON数据
        self._ui_update_timer = QTimer(self)
        self._ui_update_timer.setInterval(50)  # 每50ms刷新一次UI（20 FPS）
        self._ui_update_timer.timeout.connect(self._flush_pending_data)
        self._ui_update_timer.start()

        # 增益配置（原有逻辑，保留）
        self.adc_value_per_volt = 1240.9091
        self.ref_volt = 1.5
        self.time_gain = 1000
        self.uric_gain = 20400 / 1000000  # uA
        self.ascorbic_gain = 4700 / 1000000  # uA
        self.glucose_gain = 200 / 1000  # mA

        self.max_time_data = 300  # 保留最近300秒的数据
        # 数据缓存（原有逻辑，保留）
        self.glucose_time_data = []  # 时间-葡萄糖数据
        self.voltage_uric_data = []  # 电压-尿酸数据
        self.voltage_ascorbic_data = []  # 电压-抗坏血酸数据
        self.voltage_glucose_data = []  # 电压-葡萄糖数据

        # ---------------------- 新增：卡尔曼滤波初始化 ----------------------
        # 滤波配置（扩展卡尔曼选项及参数）
        self.filter_config = {
            "filter_type": "滑动平均",  # 可选：无滤波/滑动平均/中值滤波/卡尔曼滤波
            "window_size": 5,           # 滑动平均/中值滤波的窗口大小
            "kalman_params": {          # 卡尔曼滤波参数
                "Q": 0.01,  # 过程噪声协方差（小=信任预测，大=适应动态变化）
                "R": 0.1    # 测量噪声协方差（小=信任测量，大=抑制噪声）
            }
        }
        # 各指标的滤波缓存（滑动平均/中值滤波用）
        self.filter_buffers = {
            "uric": [],          # 尿酸缓存
            "ascorbic": [],      # 抗坏血酸缓存
            "glucose": [],       # 葡萄糖缓存
            "voltage": []        # 电压缓存
        }
        # 各指标的卡尔曼滤波器实例（每个指标独立）
        self.kalman_filters = {}  # 格式：{指标名: 卡尔曼滤波器实例}

        # 保存相关初始化（原有逻辑，保留）
        self.save_config = {
            "auto_save": False,
            "save_interval": 1000,
            "save_path": "./serial_data"
        }
        self.auto_save_timer = QTimer()
        self.auto_save_timer.timeout.connect(self.auto_save_data)
        self.cached_data = []
        self.csv_header = ["时间(秒)", "尿酸(uA)", "抗坏血酸(uA)", "葡萄糖（mA）", "电压(V)", "接收时间"]

        # ★ 新增：表格最多显示的行数（只影响界面，不影响图表和缓存）
        self.max_table_rows = 2000

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # 保存控制按钮组（原有逻辑，保留）
        save_ctrl_layout = QHBoxLayout()
        self.manual_save_btn = QPushButton("手动保存当前数据")
        self.manual_save_btn.clicked.connect(self.manual_save_data)

        # ★ 新增：清空监测数据按钮
        self.clear_data_btn = QPushButton("清空监测数据")
        self.clear_data_btn.clicked.connect(self.clear_all_data)

        self.save_status_label = QLabel("保存状态：未启用自动保存")
        self.save_status_label.setStyleSheet("color: #666;")

        save_ctrl_layout.addWidget(self.manual_save_btn)
        save_ctrl_layout.addWidget(self.clear_data_btn)
        save_ctrl_layout.addStretch()
        save_ctrl_layout.addWidget(self.save_status_label)
        main_layout.addLayout(save_ctrl_layout)


        # ★ 新增：表格显示设置——只显示最近 N 条数据
        table_ctrl_layout = QHBoxLayout()
        table_ctrl_layout.addWidget(QLabel("表格最多显示行数:"))

        self.max_rows_spin = QSpinBox()
        self.max_rows_spin.setRange(100, 100000)   # 范围可自己调
        self.max_rows_spin.setSingleStep(100)
        self.max_rows_spin.setValue(self.max_table_rows)
        self.max_rows_spin.valueChanged.connect(self._update_max_rows)

        table_ctrl_layout.addWidget(self.max_rows_spin)
        table_ctrl_layout.addStretch()
        main_layout.addLayout(table_ctrl_layout)


        # ---------------------- 新增：滤波设置（含卡尔曼参数） ----------------------
        filter_ctrl_layout = QVBoxLayout()
        # 1. 基础滤波配置（类型+窗口大小）
        base_filter_layout = QHBoxLayout()
        base_filter_layout.addWidget(QLabel("滤波类型:"))
        self.filter_type_combo = QComboBox()
        # 新增"卡尔曼滤波"选项
        self.filter_type_combo.addItems(["无滤波", "滑动平均", "中值滤波", "卡尔曼滤波",])
        self.filter_type_combo.setCurrentText(self.filter_config["filter_type"])
        self.filter_type_combo.currentTextChanged.connect(self.update_filter_config)
        base_filter_layout.addWidget(self.filter_type_combo)

        base_filter_layout.addWidget(QLabel("窗口大小:"))
        self.window_size_spin = QSpinBox()
        self.window_size_spin.setRange(3, 11)
        self.window_size_spin.setSingleStep(2)
        self.window_size_spin.setValue(self.filter_config["window_size"])
        self.window_size_spin.valueChanged.connect(self.update_filter_config)
        # 卡尔曼滤波时禁用窗口大小
        self.window_size_spin.setEnabled(self.filter_config["filter_type"] != "卡尔曼滤波")
        base_filter_layout.addWidget(self.window_size_spin)

        base_filter_layout.addStretch()
        filter_ctrl_layout.addLayout(base_filter_layout)

        # 2. 卡尔曼滤波参数组（默认隐藏，选择卡尔曼时显示）
        self.kalman_param_group = QGroupBox("卡尔曼滤波参数（Q=过程噪声，R=测量噪声）")
        kalman_param_layout = QHBoxLayout(self.kalman_param_group)
        # 过程噪声Q
        kalman_param_layout.addWidget(QLabel("Q:"))
        self.kalman_Q_edit = QLineEdit(str(self.filter_config["kalman_params"]["Q"]))
        self.kalman_Q_edit.setPlaceholderText("如0.01（越小越信任预测）")
        self.kalman_Q_edit.textChanged.connect(self.update_kalman_params)
        kalman_param_layout.addWidget(self.kalman_Q_edit)

        # 测量噪声R
        kalman_param_layout.addWidget(QLabel("R:"))
        self.kalman_R_edit = QLineEdit(str(self.filter_config["kalman_params"]["R"]))
        self.kalman_R_edit.setPlaceholderText("如0.1（越小越信任测量）")
        self.kalman_R_edit.textChanged.connect(self.update_kalman_params)
        kalman_param_layout.addWidget(self.kalman_R_edit)

        kalman_param_layout.addStretch()
        filter_ctrl_layout.addWidget(self.kalman_param_group)
        # 初始隐藏卡尔曼参数组
        self.kalman_param_group.setVisible(self.filter_config["filter_type"] == "卡尔曼滤波")

        # 3. 滤波状态提示
        self.filter_status_label = QLabel(
            f"当前：{self.filter_config['filter_type']}（窗口{self.filter_config['window_size']}，Q={self.filter_config['kalman_params']['Q']:.4f}, R={self.filter_config['kalman_params']['R']:.4f}）"
        )
        filter_ctrl_layout.addWidget(self.filter_status_label)
        main_layout.addLayout(filter_ctrl_layout)

        # 图表切换区域（原有逻辑，保留）
        chart_switch_layout = QHBoxLayout()
        self.time_chart_btn = QPushButton("时间-葡萄糖图表")
        self.volt_uric_btn = QPushButton("电压-尿酸图表")
        self.volt_ascorbic_btn = QPushButton("电压-抗坏血酸图表")
        self.voltage_glucose_btn = QPushButton("电压-葡萄糖图表")

        # ⭐ 新增：清空图表按钮
        self.clear_chart_btn = QPushButton("清空图表")

        btn_style = {"minimumHeight": 30}
        for btn in [self.time_chart_btn, self.volt_uric_btn, self.volt_ascorbic_btn, self.voltage_glucose_btn]:
            btn.setStyleSheet("")
            btn.setMinimumHeight(btn_style["minimumHeight"])
        self.time_chart_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")

        chart_switch_layout.addWidget(self.time_chart_btn)
        chart_switch_layout.addWidget(self.volt_uric_btn)
        chart_switch_layout.addWidget(self.volt_ascorbic_btn)
        chart_switch_layout.addWidget(self.voltage_glucose_btn)
        # ⭐ 新增：把清空按钮放在最右侧
        chart_switch_layout.addWidget(self.clear_chart_btn)
        main_layout.addLayout(chart_switch_layout)

        # 数据表格区域（原有逻辑，保留）
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

        # 图表容器（原有逻辑，保留）
        self.chart_stack = QStackedWidget()
        main_layout.addWidget(self.chart_stack, 4)

        # 初始化四种图表（原有逻辑，保留）
        self.init_time_glucose_chart()
        self.init_volt_uric_chart()
        self.init_volt_ascorbic_chart()
        self.init_volt_glucose_chart()

        # 连接图表切换信号（原有逻辑，保留）
        self.time_chart_btn.clicked.connect(lambda: self.switch_chart(0))
        self.volt_uric_btn.clicked.connect(lambda: self.switch_chart(1))
        self.volt_ascorbic_btn.clicked.connect(lambda: self.switch_chart(2))
        self.voltage_glucose_btn.clicked.connect(lambda: self.switch_chart(3))
        # ⭐ 新增：清空图表按钮的信号连接
        self.clear_chart_btn.clicked.connect(self.clear_charts)

    def clear_charts(self):
        """
        只清空图表和图表相关缓存：
        - 不动表格
        - 不动滤波缓存
        - 不动待保存数据
        """
        # 1. 清空图表数据缓存
        self.glucose_time_data.clear()
        self.voltage_uric_data.clear()
        self.voltage_ascorbic_data.clear()
        self.voltage_glucose_data.clear()

        # 2. 清空前三张图的曲线（这三个 series 一直存在，没有被 removeAllSeries 过）
        self.time_glucose_series.clear()
        self.volt_uric_series.clear()
        self.volt_ascorbic_series.clear()

        # 3. 对“电压-葡萄糖”图要特别小心，因为 update_volt_glucose_chart 里用过 removeAllSeries()
        #    这里不能再假定 self.volt_glucose_series 还活着
        #    统一把当前 chart 里的所有 series 删掉，然后重新加一条空的占位线
        for s in list(self.volt_glucose_chart.series()):
            self.volt_glucose_chart.removeSeries(s)

        from PySide6.QtCharts import QLineSeries  # 确保有这个导入（文件顶部其实已经有了就不用再加）

        self.volt_glucose_series = QLineSeries()
        self.volt_glucose_series.setName("葡萄糖（mA）")
        self.volt_glucose_chart.addSeries(self.volt_glucose_series)
        self.volt_glucose_series.attachAxis(self.volt_glucose_axis_x)
        self.volt_glucose_series.attachAxis(self.volt_glucose_axis_y)

        # 4. 重置各轴范围（和初始化时保持一致）
        self.time_glucose_axis_x.setRange(0, self.max_time_data)
        self.time_glucose_axis_y.setRange(0, 2000)

        self.volt_uric_axis_x.setRange(0, 5)
        self.volt_uric_axis_y.setRange(0, 2000)

        self.volt_ascorbic_axis_x.setRange(0, 5)
        self.volt_ascorbic_axis_y.setRange(0, 2000)

        self.volt_glucose_axis_x.setRange(0, 5)
        self.volt_glucose_axis_y.setRange(0, 2000)

    def _update_max_rows(self, value: int):
        """更新表格最大显示行数，并立刻裁剪多余的旧行"""
        self.max_table_rows = value
        row_count = self.data_table.rowCount()
        if row_count > self.max_table_rows:
            extra = row_count - self.max_table_rows
            # 删除最旧的 extra 行（从顶部开始）
            for _ in range(extra):
                self.data_table.removeRow(0)


    # ---------------------- 原有图表初始化方法（保留，无修改） ----------------------
    def init_time_glucose_chart(self):
        table_title_yaxis = "葡萄糖（mA）"
        time_chart_widget = QWidget()
        time_layout = QVBoxLayout(time_chart_widget)

        self.time_glucose_chart = QChart()
        self.time_glucose_chart.setTitle("时间-葡萄糖监测数据（滤波后）")
        self.time_glucose_chart.legend().setVisible(True)
        self.time_glucose_chart.legend().setAlignment(Qt.AlignBottom)

        self.time_glucose_series = QLineSeries()
        self.time_glucose_series.setName(table_title_yaxis)
        self.time_glucose_series.setColor(QColor(255, 0, 0))
        self.time_glucose_chart.addSeries(self.time_glucose_series)

        self.time_glucose_axis_x = QValueAxis()
        self.time_glucose_axis_x.setTitleText("时间(秒)")
        self.time_glucose_axis_x.setMin(0)
        self.time_glucose_axis_x.setMax(self.max_time_data)

        self.time_glucose_axis_y = QValueAxis()
        self.time_glucose_axis_y.setTitleText(table_title_yaxis)
        self.time_glucose_axis_y.setMin(0)
        self.time_glucose_axis_y.setMax(2000)

        self.time_glucose_chart.addAxis(self.time_glucose_axis_x, Qt.AlignBottom)
        self.time_glucose_chart.addAxis(self.time_glucose_axis_y, Qt.AlignLeft)
        self.time_glucose_series.attachAxis(self.time_glucose_axis_x)
        self.time_glucose_series.attachAxis(self.time_glucose_axis_y)

        self.time_glucose_chart_view = QChartView(self.time_glucose_chart)
        self.time_glucose_chart_view.setRenderHint(QPainter.Antialiasing)
        time_layout.addWidget(self.time_glucose_chart_view)

        self.chart_stack.addWidget(time_chart_widget)

    def init_volt_uric_chart(self):
        table_title_yaxis = "尿酸（uA）"
        uric_chart_widget = QWidget()
        uric_layout = QVBoxLayout(uric_chart_widget)

        self.volt_uric_chart = QChart()
        self.volt_uric_chart.setTitle("电压-尿酸监测数据（滤波后）")
        self.volt_uric_chart.legend().setVisible(True)
        self.volt_uric_chart.legend().setAlignment(Qt.AlignBottom)

        self.volt_uric_series = QLineSeries()
        self.volt_uric_series.setName(table_title_yaxis)
        self.volt_uric_series.setColor(QColor(0, 0, 255))
        self.volt_uric_chart.addSeries(self.volt_uric_series)

        self.volt_uric_axis_x = QValueAxis()
        self.volt_uric_axis_x.setTitleText("电压(V)")
        self.volt_uric_axis_x.setMin(0)
        self.volt_uric_axis_x.setMax(5)

        self.volt_uric_axis_y = QValueAxis()
        self.volt_uric_axis_y.setTitleText(table_title_yaxis)
        self.volt_uric_axis_y.setMin(0)
        self.volt_uric_axis_y.setMax(2000)

        self.volt_uric_chart.addAxis(self.volt_uric_axis_x, Qt.AlignBottom)
        self.volt_uric_chart.addAxis(self.volt_uric_axis_y, Qt.AlignLeft)
        self.volt_uric_series.attachAxis(self.volt_uric_axis_x)
        self.volt_uric_series.attachAxis(self.volt_uric_axis_y)

        self.volt_uric_chart_view = QChartView(self.volt_uric_chart)
        self.volt_uric_chart_view.setRenderHint(QPainter.Antialiasing)
        uric_layout.addWidget(self.volt_uric_chart_view)

        self.chart_stack.addWidget(uric_chart_widget)

    def init_volt_ascorbic_chart(self):
        table_title_yaxis = "抗坏血酸（uA）"
        ascorbic_chart_widget = QWidget()
        ascorbic_layout = QVBoxLayout(ascorbic_chart_widget)

        self.volt_ascorbic_chart = QChart()
        self.volt_ascorbic_chart.setTitle("电压-抗坏血酸监测数据（滤波后）")
        self.volt_ascorbic_chart.legend().setVisible(True)
        self.volt_ascorbic_chart.legend().setAlignment(Qt.AlignBottom)

        self.volt_ascorbic_series = QLineSeries()
        self.volt_ascorbic_series.setName(table_title_yaxis)
        self.volt_ascorbic_series.setColor(QColor(0, 255, 0))
        self.volt_ascorbic_chart.addSeries(self.volt_ascorbic_series)

        self.volt_ascorbic_axis_x = QValueAxis()
        self.volt_ascorbic_axis_x.setTitleText("电压(V)")
        self.volt_ascorbic_axis_x.setMin(0)
        self.volt_ascorbic_axis_x.setMax(5)

        self.volt_ascorbic_axis_y = QValueAxis()
        self.volt_ascorbic_axis_y.setTitleText(table_title_yaxis)
        self.volt_ascorbic_axis_y.setMin(0)
        self.volt_ascorbic_axis_y.setMax(2000)

        self.volt_ascorbic_chart.addAxis(self.volt_ascorbic_axis_x, Qt.AlignBottom)
        self.volt_ascorbic_chart.addAxis(self.volt_ascorbic_axis_y, Qt.AlignLeft)
        self.volt_ascorbic_series.attachAxis(self.volt_ascorbic_axis_x)
        self.volt_ascorbic_series.attachAxis(self.volt_ascorbic_axis_y)

        self.volt_ascorbic_chart_view = QChartView(self.volt_ascorbic_chart)
        self.volt_ascorbic_chart_view.setRenderHint(QPainter.Antialiasing)
        ascorbic_layout.addWidget(self.volt_ascorbic_chart_view)

        self.chart_stack.addWidget(ascorbic_chart_widget)

    def init_volt_glucose_chart(self):
        table_title_yaxis = "葡萄糖（mA）"
        glucose_chart_widget = QWidget()
        glucose_layout = QVBoxLayout(glucose_chart_widget)

        self.volt_glucose_chart = QChart()
        self.volt_glucose_chart.setTitle("电压-葡萄糖循环伏安")
        self.volt_glucose_chart.legend().setVisible(True)
        self.volt_glucose_chart.legend().setAlignment(Qt.AlignBottom)

        self.volt_glucose_series = QLineSeries()
        self.volt_glucose_series.setName(table_title_yaxis)
        self.volt_glucose_series.setColor(QColor(255, 165, 0))
        self.volt_glucose_chart.addSeries(self.volt_glucose_series)

        self.volt_glucose_axis_x = QValueAxis()
        self.volt_glucose_axis_x.setTitleText("电压(V)")
        self.volt_glucose_axis_x.setMin(0)
        self.volt_glucose_axis_x.setMax(5)

        self.volt_glucose_axis_y = QValueAxis()
        self.volt_glucose_axis_y.setTitleText(table_title_yaxis)
        self.volt_glucose_axis_y.setMin(0)
        self.volt_glucose_axis_y.setMax(2000)

        self.volt_glucose_chart.addAxis(self.volt_glucose_axis_x, Qt.AlignBottom)
        self.volt_glucose_chart.addAxis(self.volt_glucose_axis_y, Qt.AlignLeft)
        self.volt_glucose_series.attachAxis(self.volt_glucose_axis_x)
        self.volt_glucose_series.attachAxis(self.volt_glucose_axis_y)

        self.volt_glucose_chart_view = QChartView(self.volt_glucose_chart)
        self.volt_glucose_chart_view.setRenderHint(QPainter.Antialiasing)
        glucose_layout.addWidget(self.volt_glucose_chart_view)

        self.chart_stack.addWidget(glucose_chart_widget)

    def switch_chart(self, index):
        self.chart_stack.setCurrentIndex(index)
        all_btns = [
            self.time_chart_btn, self.volt_uric_btn,
            self.volt_ascorbic_btn, self.voltage_glucose_btn
        ]
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

    # ---------------------- 新增：卡尔曼滤波器内部类 ----------------------
    class KalmanFilter:
        """简化版卡尔曼滤波器（适配传感器数据）"""
        def __init__(self, Q=0.01, R=0.1):
            self.Q = Q  # 过程噪声协方差
            self.R = R  # 测量噪声协方差
            self.x = None  # 状态估计值（初始为None，等待第一个测量值）
            self.P = 0.1   # 初始误差协方差

        def update(self, z):
            """卡尔曼滤波核心：预测+更新"""
            # 初始化：第一个测量值直接作为初始状态
            if self.x is None:
                self.x = z
                return self.x

            # 1. 预测步骤
            x_pred = self.x  # 状态预测（简化模型：无状态转移）
            P_pred = self.P + self.Q  # 误差协方差预测

            # 2. 更新步骤
            K = P_pred / (P_pred + self.R)  # 卡尔曼增益
            self.x = x_pred + K * (z - x_pred)  # 状态修正
            self.P = (1 - K) * P_pred  # 误差协方差修正

            return self.x

        def reset(self):
            """重置滤波器状态（切换配置时调用）"""
            self.x = None
            self.P = 0.1

    # ---------------------- 新增：卡尔曼参数更新与滤波配置 ----------------------
    def init_kalman_filters(self):
        """为所有指标初始化卡尔曼滤波器"""
        Q = self.filter_config["kalman_params"]["Q"]
        R = self.filter_config["kalman_params"]["R"]
        self.kalman_filters = {
            "uric": self.KalmanFilter(Q=Q, R=R),
            "ascorbic": self.KalmanFilter(Q=Q, R=R),
            "glucose": self.KalmanFilter(Q=Q, R=R),
            "voltage": self.KalmanFilter(Q=Q, R=R)
        }

    def update_kalman_params(self):
        """更新卡尔曼Q/R参数，重置滤波器"""
        try:
            # 验证参数为正数
            new_Q = float(self.kalman_Q_edit.text().strip())
            new_R = float(self.kalman_R_edit.text().strip())
            if new_Q <= 0 or new_R <= 0:
                raise ValueError("Q和R必须为正数")
            # 更新配置
            self.filter_config["kalman_params"]["Q"] = new_Q
            self.filter_config["kalman_params"]["R"] = new_R
            # 重置滤波器（参数变更需重新初始化）
            self.init_kalman_filters()
            self.update_filter_status()
        except ValueError as e:
            # 输入无效时恢复默认值
            default_Q = 0.01
            default_R = 0.1
            QMessageBox.warning(self, "参数错误", f"无效输入：{str(e)}\n已恢复默认值（Q={default_Q}, R={default_R}）")
            self.filter_config["kalman_params"]["Q"] = default_Q
            self.filter_config["kalman_params"]["R"] = default_R
            self.kalman_Q_edit.setText(str(default_Q))
            self.kalman_R_edit.setText(str(default_R))
            self.init_kalman_filters()
            self.update_filter_status()

    def update_filter_config(self):
        """更新滤波类型/窗口大小，重置缓存/滤波器"""
        old_type = self.filter_config["filter_type"]
        new_type = self.filter_type_combo.currentText()
        new_window = self.window_size_spin.value()

        # 1. 更新配置
        self.filter_config["filter_type"] = new_type
        self.filter_config["window_size"] = new_window

        # 2. UI控件联动
        self.window_size_spin.setEnabled(new_type != "卡尔曼滤波")
        self.kalman_param_group.setVisible(new_type == "卡尔曼滤波")

        # 3. 重置缓存/滤波器
        if new_type != old_type:
            # 重置滑动平均/中值缓存
            for key in self.filter_buffers.keys():
                self.filter_buffers[key].clear()
            # 初始化/清空卡尔曼滤波器
            if new_type == "卡尔曼滤波":
                self.init_kalman_filters()
            else:
                self.kalman_filters.clear()

        # 4. 更新状态提示
        self.update_filter_status()

    def update_filter_status(self):
        """更新滤波状态提示文本"""
        if self.filter_config["filter_type"] == "卡尔曼滤波":
            status = f"当前：卡尔曼滤波（Q={self.filter_config['kalman_params']['Q']:.4f}, R={self.filter_config['kalman_params']['R']:.4f}）"
        else:
            status = f"当前：{self.filter_config['filter_type']}（窗口{self.filter_config['window_size']}）"
        self.filter_status_label.setText(status)

    # ---------------------- 原有滤波算法（滑动平均/中值）保留 ----------------------
    def _sliding_average_filter(self, buffer, window_size):
        if len(buffer) < window_size:
            return sum(buffer) / len(buffer)
        return sum(buffer[-window_size:]) / window_size

    def _median_filter(self, buffer, window_size):
        if len(buffer) < window_size:
            sorted_buf = sorted(buffer)
            return sorted_buf[len(sorted_buf) // 2]
        sorted_buf = sorted(buffer[-window_size:])
        return sorted_buf[window_size // 2]

    # ---------------------- 修改：_apply_filter（新增卡尔曼分支） ----------------------
    def _apply_filter(self, indicator_key, raw_value):
        """对单个指标应用滤波（支持卡尔曼）"""
        filter_type = self.filter_config["filter_type"]

        # 1. 卡尔曼滤波
        if filter_type == "卡尔曼滤波":
            # 确保滤波器已初始化
            if indicator_key not in self.kalman_filters:
                self.init_kalman_filters()
            return self.kalman_filters[indicator_key].update(raw_value)

        # 2. 滑动平均/中值滤波
        buffer = self.filter_buffers[indicator_key]
        buffer.append(raw_value)
        # 限制缓存大小（2倍窗口）
        max_buf_size = self.filter_config["window_size"] * 2
        if len(buffer) > max_buf_size:
            buffer = buffer[-max_buf_size:]
        self.filter_buffers[indicator_key] = buffer

        if filter_type == "无滤波":
            return raw_value
        elif filter_type == "滑动平均":
            return self._sliding_average_filter(buffer, self.filter_config["window_size"])
        elif filter_type == "中值滤波":
            return self._median_filter(buffer, self.filter_config["window_size"])
        return raw_value

    # ---------------------- 原有update_data方法（逻辑不变，滤波自动适配） ----------------------
    def update_data(self, data):
        """串口页面传来的JSON数据入口：先缓存，稍后批量处理"""
        self._pending_data.append(data)

    def _flush_pending_data(self):
        """定时器回调：批量处理缓存的数据，并更新UI"""
        if not self._pending_data:
            return

        MAX_PER_TICK = 50
        batch = self._pending_data[:MAX_PER_TICK]
        self._pending_data = self._pending_data[MAX_PER_TICK:]

        # 1. 先批量处理数据（表格&缓存）
        for data in batch:
            self._process_single_data(data)

        # 2. 只更新当前显示的那张图表，减少重绘开销
        current_idx = self.chart_stack.currentIndex()
        if current_idx == 0:
            self.update_time_glucose_chart()
        elif current_idx == 1:
            self.update_volt_uric_chart()
        elif current_idx == 2:
            self.update_volt_ascorbic_chart()
        elif current_idx == 3:
            self.update_volt_glucose_chart()

        # 3. 批量更新完再滚动一次，减少开销
        if self.data_table.rowCount() > 0:
            self.data_table.scrollToBottom()

    def _process_single_data(self, data):
        """接收JSON数据→解析→滤波→更新表格/图表缓存（不立即重画图）"""
        # 1. 解析原始数据
        seconds_value = data.get("Seconds", 0) / self.time_gain
        uric_raw = self.adc_value_transform_to_current(data.get("Uric", 0), self.uric_gain)
        ascorbic_raw = self.adc_value_transform_to_current(data.get("Ascorbic", 0), self.ascorbic_gain)
        glucose_raw = self.adc_value_transform_to_current(data.get("Glucose", 0), self.glucose_gain)
        voltage_raw = self.ref_volt - data.get("Volt", 0) / self.adc_value_per_volt
        receive_time = data.get("receive_time", "")

        # 2. 滤波
        uric_filtered = self._apply_filter("uric", uric_raw)
        ascorbic_filtered = self._apply_filter("ascorbic", ascorbic_raw)
        glucose_filtered = self._apply_filter("glucose", glucose_raw)
        voltage_filtered = voltage_raw  # 如果也想滤波可以用 _apply_filter

        # 3. 更新表格（这里仍然是每条数据一个 row，如果觉得表格太卡可以加采样）
        row = self.data_table.rowCount()
        self.data_table.insertRow(row)
        data_row = [
            round(seconds_value, 4),
            round(uric_filtered, 4),
            round(ascorbic_filtered, 4),
            round(glucose_filtered, 4),
            round(voltage_filtered, 4),
            receive_time
        ]
        for col, val in enumerate(data_row):
            self.data_table.setItem(row, col, QTableWidgetItem(str(val)))

        # ★ 新增：只保留最近 max_table_rows 行
        if self.data_table.rowCount() > self.max_table_rows:
            extra = self.data_table.rowCount() - self.max_table_rows
            for _ in range(extra):
                self.data_table.removeRow(0)

        # 4. 缓存待保存数据
        if self.save_config["auto_save"] or self.manual_save_btn.isDown():
            self.cached_data.append(data_row)

        # 5. 更新图表缓存（不画图，只存点）
        seconds = seconds_value
        uric = uric_filtered
        ascorbic = ascorbic_filtered
        glucose = glucose_filtered
        voltage = voltage_filtered

        self.glucose_time_data.append((seconds, glucose))
        self.voltage_uric_data.append((voltage, uric))
        self.voltage_ascorbic_data.append((voltage, ascorbic))
        self.voltage_glucose_data.append((voltage, glucose))

        # 裁剪缓存
        self.glucose_time_data = [(t, g) for t, g in self.glucose_time_data
                                  if t >= (seconds - self.max_time_data)]
        max_volt_data = 2000
        self.voltage_uric_data = self.voltage_uric_data[-max_volt_data:]
        self.voltage_ascorbic_data = self.voltage_ascorbic_data[-max_volt_data:]
        self.voltage_glucose_data = self.voltage_glucose_data[-max_volt_data:]


    # ---------------------- 原有图表更新与保存方法（保留，无修改） ----------------------
    def update_time_glucose_chart(self):
        if not self.glucose_time_data:
            return
        self.time_glucose_series.clear()
        for t, g in self.glucose_time_data:
            self.time_glucose_series.append(t, g)
        max_time = max(t for t, g in self.glucose_time_data)
        min_time = max(0, max_time - self.max_time_data)
        self.time_glucose_axis_x.setRange(min_time, max_time)
        max_glucose = max(g for t, g in self.glucose_time_data)
        min_glucose = min(g for t, g in self.glucose_time_data)
        margin = max(1, (max_glucose - min_glucose) * 0.1)
        self.time_glucose_axis_y.setRange(min_glucose - margin, max_glucose + margin)

    def update_volt_uric_chart(self):
        if not self.voltage_uric_data:
            return
        self.volt_uric_series.clear()
        for v, u in self.voltage_uric_data:
            self.volt_uric_series.append(v, u)
        max_volt = max(v for v, u in self.voltage_uric_data)
        min_volt = min(v for v, u in self.voltage_uric_data)
        self.volt_uric_axis_x.setRange(min_volt - 0.1, max_volt + 0.1)
        max_uric = max(u for v, u in self.voltage_uric_data)
        min_uric = min(u for v, u in self.voltage_uric_data)
        margin = max(1, (max_uric - min_uric) * 0.1)
        self.volt_uric_axis_y.setRange(min_uric - margin, max_uric + margin)

    def update_volt_ascorbic_chart(self):
        if not self.voltage_ascorbic_data:
            return
        self.volt_ascorbic_series.clear()
        for v, a in self.voltage_ascorbic_data:
            self.volt_ascorbic_series.append(v, a)
        max_volt = max(v for v, a in self.voltage_ascorbic_data)
        min_volt = min(v for v, a in self.voltage_ascorbic_data)
        self.volt_ascorbic_axis_x.setRange(min_volt - 0.1, max_volt + 0.1)
        max_ascorbic = max(a for v, a in self.voltage_ascorbic_data)
        min_ascorbic = min(a for v, a in self.voltage_ascorbic_data)
        margin = max(1, (max_ascorbic - min_ascorbic) * 0.1)
        self.volt_ascorbic_axis_y.setRange(min_ascorbic - margin, max_ascorbic + margin)

    def update_volt_glucose_chart(self):
        """将电压-葡萄糖数据按圈拆分，每圈一条线，颜色自动循环"""
        if not self.voltage_glucose_data:
            return

        # 1. 用辅助函数按电压方向反转切圈
        cycles = self._build_cv_cycles(dv_threshold=0.002, min_points=10)
        if not cycles:
            # 如果实在切不出圈，就退回到画一条线（避免图空白）
            self.volt_glucose_chart.removeAllSeries()
            series = QLineSeries()
            series.setName("Glucose")
            for v, g in self.voltage_glucose_data:
                series.append(v, g)
            self.volt_glucose_chart.addSeries(series)
            series.attachAxis(self.volt_glucose_axis_x)
            series.attachAxis(self.volt_glucose_axis_y)

            max_volt = max(v for v, g in self.voltage_glucose_data)
            min_volt = min(v for v, g in self.voltage_glucose_data)
            self.volt_glucose_axis_x.setRange(min_volt - 0.1, max_volt + 0.1)
            max_glucose = max(g for v, g in self.voltage_glucose_data)
            min_glucose = min(g for v, g in self.voltage_glucose_data)
            margin = max(1, (max_glucose - min_glucose) * 0.1)
            self.volt_glucose_axis_y.setRange(min_glucose - margin, max_glucose + margin)
            return

        # 2. 先清空原来的 series（包括初始化时那条占位的 self.volt_glucose_series）
        self.volt_glucose_chart.removeAllSeries()

        # 颜色循环表，可以按喜好改
        colors = [
            QColor(255, 0, 0),  # 红
            QColor(0, 0, 255),  # 蓝
            QColor(0, 128, 0),  # 绿色
            QColor(255, 165, 0),  # 橙色
            QColor(128, 0, 128),  # 紫色
            QColor(0, 139, 139),  # 青色
        ]

        # 3. 统计所有点的范围，用于设轴
        all_volt = []
        all_glucose = []
        for cycle in cycles:
            for v, g in cycle:
                all_volt.append(v)
                all_glucose.append(g)

        max_volt = max(all_volt)
        min_volt = min(all_volt)
        max_glucose = max(all_glucose)
        min_glucose = min(all_glucose)
        margin = max(1, (max_glucose - min_glucose) * 0.1)

        self.volt_glucose_axis_x.setRange(min_volt - 0.1, max_volt + 0.1)
        self.volt_glucose_axis_y.setRange(min_glucose - margin, max_glucose + margin)

        # 4. 为每一圈创建一条 series 并着色
        for idx, cycle in enumerate(cycles):
            series = QLineSeries()
            series.setName(f"第 {idx + 1} 圈")
            series.setColor(colors[idx % len(colors)])
            for v, g in cycle:
                series.append(v, g)

            self.volt_glucose_chart.addSeries(series)
            series.attachAxis(self.volt_glucose_axis_x)
            series.attachAxis(self.volt_glucose_axis_y)

    def _build_cv_cycles(self, dv_threshold=0.002, min_points=10):
        """
        根据电压变化方向的反转，把 self.voltage_glucose_data 切成多圈。
        返回：cycles = [ [(v1, g1), (v2, g2), ...], [...], ... ]
        dv_threshold: 电压变化太小就当成噪声，不触发反转
        min_points:   少于这个点数的“圈”丢弃
        """
        data = self.voltage_glucose_data
        if len(data) < 2:
            return []

        cycles = []
        current_cycle = []

        prev_v, prev_g = data[0]
        current_cycle.append((prev_v, prev_g))
        current_dir = None  # +1 上扫，-1 下扫

        for i in range(1, len(data)):
            v, g = data[i]
            dv = v - prev_v

            # 过滤很小的抖动
            if abs(dv) < dv_threshold:
                current_cycle.append((v, g))
                prev_v, prev_g = v, g
                continue

            step_dir = 1 if dv > 0 else -1

            if current_dir is None:
                # 第一次确定方向
                current_dir = step_dir
                current_cycle.append((v, g))
            else:
                if step_dir == current_dir:
                    # 方向没变，正常推进这一圈
                    current_cycle.append((v, g))
                else:
                    # 🚩方向反转了：视为新一圈开始
                    if len(current_cycle) >= min_points:
                        cycles.append(current_cycle)
                    # 新圈从上一个点 + 当前点开始，保证曲线连续
                    current_cycle = [(prev_v, prev_g), (v, g)]
                    current_dir = step_dir

            prev_v, prev_g = v, g

        # 最后一圈丢进去
        if len(current_cycle) >= min_points:
            cycles.append(current_cycle)

        return cycles


    def update_save_config(self, new_config):
        self.save_config = new_config
        if self.save_config["auto_save"]:
            self.save_status_label.setText(f"保存状态：自动保存已启用（间隔{self.save_config['save_interval']}ms）")
            self.save_status_label.setStyleSheet("color: green;")
            self.auto_save_timer.stop()
            self.auto_save_timer.start(self.save_config["save_interval"])
        else:
            self.save_status_label.setText("保存状态：未启用自动保存")
            self.save_status_label.setStyleSheet("color: #666;")
            self.auto_save_timer.stop()
        self._ensure_save_path_exists()

    def _ensure_save_path_exists(self):
        if not os.path.exists(self.save_config["save_path"]):
            try:
                os.makedirs(self.save_config["save_path"])
            except Exception as e:
                QMessageBox.warning(self, "路径错误", f"无法创建保存路径：{str(e)}\n将使用默认路径！")
                self.save_config["save_path"] = "./serial_data"
                os.makedirs(self.save_config["save_path"], exist_ok=True)

    def _get_save_filename(self):
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.save_config["save_path"], f"serial_data_{now}.csv")

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
            QMessageBox.warning(self, "保存错误", f"数据保存失败：{str(e)}")
            return False

    def manual_save_data(self):
        all_data = []
        row_count = self.data_table.rowCount()
        for row in range(row_count):
            data_row = []
            for col in range(6):
                item = self.data_table.item(row, col)
                data_row.append(item.text() if item else "")
            all_data.append(data_row)

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

        # ---------------------- 新增：清空所有监测数据的核心方法 ----------------------

    def clear_all_data(self):
        """清空表格、图表、滤波缓存、待保存数据（含确认机制）"""
        reply = QMessageBox.question(
            self, "确认清空",
            "确定要清空所有监测数据吗？\n（包含表格、图表、滤波缓存、待保存数据，不可恢复！）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # 1. 清空表格数据
        self.data_table.setRowCount(0)

        # 2. 清空图表 & 图表缓存（复用安全版 clear_charts，避免重复逻辑和崩溃）
        self.clear_charts()

        # 3. 清空待处理缓存（防止又刷出旧数据）
        self._pending_data.clear()

        # 4. 清空滤波缓存
        for key in self.filter_buffers.keys():
            self.filter_buffers[key].clear()

        # 5. 重置卡尔曼滤波器
        if self.kalman_filters:
            for filter_obj in self.kalman_filters.values():
                filter_obj.reset()

        # 6. 清空待保存的数据缓存
        self.cached_data.clear()

        QMessageBox.information(self, "清空成功", "所有监测数据已完全清空！")


class SettingsPage(QWidget):
    """设置页面"""

    save_config_updated = Signal(dict)

    def __init__(self):
        super().__init__()
        self.save_config = {  # 保存配置默认值
            "auto_save": False,
            "save_interval": 1000,  # 单位：ms
            "save_path": "./serial_data"  # 默认保存路径
        }
        self.init_ui()
        self.load_default_config()  # 加载默认配置

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # ---------------------- 自动保存设置组 ----------------------
        save_group = QGroupBox("数据保存设置")
        save_layout = QFormLayout(save_group)

        # 自动保存开关
        self.auto_save_check = QCheckBox("启用自动保存")
        self.auto_save_check.stateChanged.connect(self.on_config_change)
        save_layout.addRow(self.auto_save_check)

        # 保存间隔（ms）
        self.save_interval_edit = QLineEdit(str(self.save_config["save_interval"]))
        self.save_interval_edit.setPlaceholderText("输入自动保存间隔（毫秒）")
        self.save_interval_edit.textChanged.connect(self.on_config_change)
        save_layout.addRow("自动保存间隔(ms):", self.save_interval_edit)

        # 保存路径（显示+选择按钮）
        path_layout = QHBoxLayout()
        self.save_path_edit = QLineEdit(self.save_config["save_path"])
        self.select_path_btn = QPushButton("选择路径")
        self.select_path_btn.clicked.connect(self.select_save_path)  # 连接外部方法
        path_layout.addWidget(self.save_path_edit)
        path_layout.addWidget(self.select_path_btn)
        save_layout.addRow("保存路径:", path_layout)

        # 通用设置（修复：原self.save_path与保存配置的save_path_edit重名，改为self.log_save_path）
        general_group = QGroupBox("通用设置")
        general_layout = QFormLayout(general_group)

        self.log_save_path = QLineEdit("./data_logs")  # 重命名，避免与保存配置冲突
        self.log_interval = QLineEdit("1000")
        self.language = QComboBox()
        self.language.addItems(["中文", "English"])

        general_layout.addRow("日志保存路径:", self.log_save_path)  # 对应重命名的变量
        general_layout.addRow("日志间隔(ms):", self.log_interval)
        general_layout.addRow("语言:", self.language)

        # 显示设置
        display_group = QGroupBox("显示设置")
        display_layout = QVBoxLayout(display_group)

        self.theme = QComboBox()
        self.theme.addItems(["浅色主题", "深色主题", "跟随系统"])

        self.font_size = QComboBox()
        self.font_size.addItems(["小", "中", "大"])

        display_layout.addWidget(QLabel("主题:"))
        display_layout.addWidget(self.theme)
        display_layout.addSpacing(10)
        display_layout.addWidget(QLabel("字体大小:"))
        display_layout.addWidget(self.font_size)
        display_layout.addSpacing(10)

        # 其他选项（修复：原self.auto_save与自动保存开关重名，改为self.auto_save_log）
        options_layout = QVBoxLayout()
        self.auto_save_log = QCheckBox("自动保存日志")  # 重命名，避免与保存配置冲突
        self.minimize_tray = QCheckBox("最小化到托盘")
        self.check_update = QCheckBox("启动时检查更新")
        self.check_update.setChecked(True)

        options_layout.addWidget(self.auto_save_log)  # 对应重命名的变量
        options_layout.addWidget(self.minimize_tray)
        options_layout.addWidget(self.check_update)

        # 按钮区域（连接外部方法）
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存设置")
        self.save_btn.clicked.connect(self.save_all_config)  # 连接外部方法
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.clicked.connect(self.reset_to_default)  # 连接外部方法
        self.save_btn.setMinimumHeight(35)
        self.reset_btn.setMinimumHeight(35)
        btn_layout.addStretch()
        btn_layout.addWidget(self.reset_btn)
        btn_layout.addWidget(self.save_btn)

        # 调整布局顺序：保存设置放在最上方，逻辑更清晰
        main_layout.addWidget(save_group)
        main_layout.addWidget(general_group)
        main_layout.addWidget(display_group)
        main_layout.addLayout(options_layout)
        main_layout.addStretch()
        main_layout.addLayout(btn_layout)

    # ---------------------- 以下方法移出init_ui，作为类的顶层方法 ----------------------
    def select_save_path(self):
        """选择保存路径"""
        path = QFileDialog.getExistingDirectory(self, "选择数据保存目录")
        if path:
            self.save_path_edit.setText(path)
            self.save_config["save_path"] = path
            self.save_config_updated.emit(self.save_config)

    def on_config_change(self):
        """实时更新保存配置（开关/间隔）"""
        self.save_config["auto_save"] = self.auto_save_check.isChecked()
        # 验证间隔输入是否为数字
        try:
            interval = int(self.save_interval_edit.text().strip())
            self.save_config["save_interval"] = max(100, interval)  # 最小100ms，避免频繁写入
        except ValueError:
            self.save_config["save_interval"] = 1000  # 输入无效时用默认值
        self.save_config_updated.emit(self.save_config)

    def save_all_config(self):
        """保存所有设置（含保存配置）"""
        self.on_config_change()  # 确保最新配置已更新
        QMessageBox.information(self, "提示", "设置已保存！")

    def load_default_config(self):
        """加载默认配置到UI"""
        self.auto_save_check.setChecked(self.save_config["auto_save"])
        self.save_interval_edit.setText(str(self.save_config["save_interval"]))
        self.save_path_edit.setText(self.save_config["save_path"])

    def reset_to_default(self):
        """恢复默认配置"""
        self.save_config = {
            "auto_save": False,
            "save_interval": 1000,
            "save_path": "./serial_data"
        }
        self.load_default_config()
        self.save_config_updated.emit(self.save_config)
        QMessageBox.information(self, "提示", "已恢复默认设置！")


class MainWindow(QMainWindow):
    """主窗口，包含底部导航栏和多页面切换"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("串口助手")
        self.setMinimumSize(1000, 800)

        # 创建主Widget和布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 创建页面容器
        self.stacked_widget = QStackedWidget()

        # 创建各个页面
        self.serial_page = SerialPage()
        self.data_page = DataMonitorPage()
        self.settings_page = SettingsPage()

        # 添加页面到容器
        self.stacked_widget.addWidget(self.serial_page)
        self.stacked_widget.addWidget(self.data_page)
        self.stacked_widget.addWidget(self.settings_page)

        # 创建底部导航栏
        self.bottom_nav = QWidget()
        self.bottom_nav.setStyleSheet("background-color: #f0f0f0; border-top: 1px solid #ccc;")
        nav_layout = QHBoxLayout(self.bottom_nav)
        nav_layout.setContentsMargins(0, 5, 0, 5)

        # 导航按钮
        self.nav_btns = []
        self.add_nav_btn(nav_layout, "串口通信", 0)
        self.add_nav_btn(nav_layout, "数据监测", 1)
        self.add_nav_btn(nav_layout, "设置", 2)

        # 添加到主布局
        main_layout.addWidget(self.stacked_widget)
        main_layout.addWidget(self.bottom_nav)

        # 连接信号
        self.serial_page.data_received.connect(self.data_page.update_data)

        # 设置初始页面
        self.set_nav_active(0)

    def add_nav_btn(self, layout, text, index):
        btn = QPushButton(text)
        btn.setMinimumHeight(40)
        btn.setFlat(True)
        btn.clicked.connect(lambda: self.switch_page(index))
        layout.addWidget(btn)
        layout.setStretch(index, 1)
        self.nav_btns.append(btn)

    def switch_page(self, index):
        self.stacked_widget.setCurrentIndex(index)
        self.set_nav_active(index)

    def set_nav_active(self, index):
        for i, btn in enumerate(self.nav_btns):
            if i == index:
                btn.setStyleSheet("background-color: #ccc; font-weight: bold;")
            else:
                btn.setStyleSheet("")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


