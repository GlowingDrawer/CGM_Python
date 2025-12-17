import sys
import json
import asyncio
import qasync
import serial
import serial.tools.list_ports
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem,
                               QHeaderView, QMessageBox, QGroupBox, QComboBox, QLineEdit, QTextEdit,
                               QSplitter, QFormLayout, QCheckBox, QFrame, QStackedWidget,
                               QGridLayout)
from PySide6.QtCore import Qt, Signal, Slot, QSize, QTimer, QThread, QObject
from PySide6.QtGui import QFont, QColor, QPainter, QIcon, QTextCursor
from PySide6.QtCharts import (QChart, QChartView, QLineSeries, QValueAxis,
                              QScatterSeries, QLegend)


GlucoseTableName = "葡萄糖电信号"


class SerialWorker(QObject):
    """串口工作线程，优化：批量读取数据，减少信号发射频率"""
    data_received = Signal(bytes)  # 批量发射数据（而非单条）
    error_occurred = Signal(str)

    def __init__(self, serial_port):
        super().__init__()
        self.serial_port = serial_port
        self.running = False
        self.read_batch_size = 1024  # 批量读取大小（可调整）

    def start(self):
        self.running = True
        self.read_data()

    def stop(self):
        self.running = False

    def read_data(self):
        while self.running and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting >= self.read_batch_size:
                    # 批量读取数据，减少信号发射次数
                    data = self.serial_port.read(self.read_batch_size)
                    self.data_received.emit(data)
                elif self.serial_port.in_waiting > 0:
                    # 数据不足批量时，短延时后再读（避免频繁空等）
                    QThread.msleep(5)
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    self.data_received.emit(data)
                else:
                    # 无数据时延长延时，降低CPU占用
                    QThread.msleep(20)
            except Exception as e:
                self.error_occurred.emit(str(e))
                self.running = False
                break


class SerialPage(QWidget):
    """串口页面：优化UI控件+批量更新，解决卡顿"""
    data_received = Signal(dict)

    def __init__(self):
        super().__init__()
        self.serial_port = None
        self.worker = None
        self.worker_thread = None
        self.json_buffer = ""
        # UI批量更新缓冲区+定时器
        self.ui_update_buffer = []  # 接收文本缓冲区
        self.ui_update_timer = QTimer(self)
        self.ui_update_timer.setInterval(50)  # 50ms更新一次UI（可调整）
        self.ui_update_timer.timeout.connect(self.batch_update_ui)
        self.ui_update_timer.start()  # 启动批量更新定时器
        self.init_ui()
        self.refresh_ports()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 顶部状态栏
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

        # 上部：设备和控制区
        upper_widget = QWidget()
        upper_layout = QVBoxLayout(upper_widget)
        upper_layout.setContentsMargins(0, 0, 0, 0)

        # 设备选择和连接控制区
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
        baudrates = ["1200", "2400", "4800", "9600", "19200", "38400",
                     "57600", "115200", "230400", "460800", "921600"]
        self.baudrate_combo.addItems(baudrates)
        self.baudrate_combo.setCurrentText("115200")
        device_control_layout.addWidget(self.baudrate_combo, 0, 3)

        # 数据位/停止位/校验位
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

        # 设备信息显示区域（仍用QTextEdit，数据量少）
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

        # 发送区域
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

        self.send_text = QPlainTextEdit()  # 优化：用QPlainTextEdit
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

        # 接收区：优化为QPlainTextEdit+自动行数限制
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

        # 核心优化：QPlainTextEdit替代QTextEdit，设置最大行数
        self.receive_text = QPlainTextEdit()
        self.receive_text.setReadOnly(True)
        self.receive_text.document().setMaximumBlockCount(1000)  # 自动保留1000行，超量自动删除

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

    def batch_update_ui(self):
        """批量更新接收区UI，减少重绘次数"""
        if not self.ui_update_buffer:
            return
        # 批量拼接缓冲区内容，一次写入
        batch_text = "".join(self.ui_update_buffer)
        self.receive_text.appendPlainText(batch_text)
        # 自动滚动
        if self.auto_scroll_check.isChecked():
            self.receive_text.moveCursor(QTextCursor.End)
        # 清空缓冲区
        self.ui_update_buffer.clear()

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
                    info = f"端口: {port.device}\n描述: {port.description}\n硬件ID: {port.hwid}\n"
                    self.device_info.setText(info)
                    self.com_label.setText(f"当前设备: {port.device}")
                    return

    def toggle_connection(self):
        """切换连接状态（打开/关闭串口）"""
        if self.is_connected:
            self.close_serial()
        else:
            self.open_serial()

    def open_serial(self):
        """打开串口"""
        if self.port_combo.count() == 0 or not self.port_combo.itemData(0):
            QMessageBox.warning(self, "错误", "请选择有效的串口")
            return

        try:
            port_name = self.port_combo.itemData(self.port_combo.currentIndex())
            baudrate = int(self.baudrate_combo.currentText())
            databits = int(self.databit_combo.currentText())

            # 转换停止位/校验位
            stopbits_text = self.stopbit_combo.currentText()
            stopbits = serial.STOPBITS_ONE if stopbits_text == "1" else \
                serial.STOPBITS_ONE_POINT_FIVE if stopbits_text == "1.5" else serial.STOPBITS_TWO

            parity_text = self.parity_combo.currentText()
            parity = serial.PARITY_NONE if parity_text == "None" else \
                serial.PARITY_ODD if parity_text == "Odd" else \
                serial.PARITY_EVEN if parity_text == "Even" else \
                serial.PARITY_MARK if parity_text == "Mark" else serial.PARITY_SPACE

            # 打开串口
            self.serial_port = serial.Serial(
                port=port_name, baudrate=baudrate, bytesize=databits,
                stopbits=stopbits, parity=parity, timeout=0.1
            )

            if self.serial_port.is_open:
                self.is_connected = True
                self.status_label.setText("状态: 已连接")
                self.status_label.setStyleSheet("color: green; font-weight: bold;")
                self.connect_btn.setText("关闭串口")
                self.connect_btn.setStyleSheet("background-color: #f44336; color: white;")

                # 禁用参数修改
                self.port_combo.setEnabled(False)
                self.baudrate_combo.setEnabled(False)
                self.databit_combo.setEnabled(False)
                self.stopbit_combo.setEnabled(False)
                self.parity_combo.setEnabled(False)
                self.refresh_btn.setEnabled(False)

                # 启动接收线程
                self.start_worker()

        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开串口: {str(e)}")
            self.serial_port = None

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
        """处理接收到的数据，先存入缓冲区，批量更新UI"""
        try:
            display_text = ""
            raw_data = ""

            # 处理时间戳
            if self.timestamp_check.isChecked():
                timestamp = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "
                display_text += timestamp
            else:
                timestamp = ""

            # 处理Hex/文本显示
            if self.hex_receive_check.isChecked():
                hex_str = ' '.join(f'{b:02X}' for b in data)
                display_text += hex_str + "\n"  # 每条数据换行
            else:
                raw_text = data.decode(errors='replace')
                display_text += raw_text.replace('\n', '\n' + timestamp)  # 换行后补时间戳
                raw_data = raw_text

            # 将数据加入UI缓冲区（不直接更新）
            self.ui_update_buffer.append(display_text)

            # 非Hex模式下处理JSON解析（解析逻辑不阻塞UI）
            if not self.hex_receive_check.isChecked() and raw_data:
                self.json_buffer += raw_data
                self.process_json_buffer()

        except Exception as e:
            print(f"数据处理错误: {e}")

    def process_json_buffer(self):
        """处理JSON缓冲区，提取完整JSON对象"""
        while True:
            start_idx = self.json_buffer.find('{')
            if start_idx == -1:
                self.json_buffer = ""
                return

            end_idx = self.json_buffer.find('}', start_idx)
            if end_idx == -1:
                self.json_buffer = self.json_buffer[start_idx:]
                return

            # 提取并解析JSON
            json_str = self.json_buffer[start_idx:end_idx + 1]
            self.json_buffer = self.json_buffer[end_idx + 1:]
            try:
                json_data = json.loads(json_str)
                json_data["receive_time"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self.data_received.emit(json_data)
            except json.JSONDecodeError:
                continue

    def handle_worker_error(self, error_msg):
        """处理工作线程错误"""
        QMessageBox.warning(self, "接收错误", f"数据接收失败: {error_msg}")
        self.close_serial()

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
                text = text.replace(' ', '')
                data = bytes.fromhex(text)
            else:
                data = text.encode()

            self.serial_port.write(data)
        except Exception as e:
            QMessageBox.warning(self, "发送错误", f"发送失败: {str(e)}")
            self.close_serial()

    def send_shortcut(self, cmd):
        """发送快捷命令"""
        self.send_text.setPlainText(cmd)
        self.hex_send_check.setChecked(False)
        self.send_data()

    def reset_connection_state(self):
        """重置连接状态"""
        self.status_label.setText("状态: 未连接")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.connect_btn.setText("打开串口")
        self.connect_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self.is_connected = False


class DataMonitorPage(QWidget):
    """数据监测页面：优化图表更新+电压值循环变色绘制"""

    def __init__(self):
        super().__init__()
        self.max_time_data = 300  # 保留最近300秒的数据
        self.glucose_time_data = []  # 时间-葡萄糖电信号数据缓存
        self.voltage_glucose_data = []  # 电压-葡萄糖电信号数据缓存
        # 图表批量更新参数（积累5个点再更新，减少重绘）
        self.chart_batch_count = 5
        self.glucose_batch_cache = []
        self.voltage_batch_cache = []
        # 电压值循环颜色列表（可自定义添加颜色）
        self.color_list = [
            QColor(255, 0, 0),    # 红
            QColor(0, 255, 0),    # 绿
            QColor(0, 0, 255),    # 蓝
            QColor(255, 255, 0),  # 黄
            QColor(255, 0, 255),  # 紫
            QColor(0, 255, 255)   # 青
        ]
        self.current_color_idx = 0  # 当前使用的颜色索引
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # 图表切换区域
        chart_switch_layout = QHBoxLayout()
        self.time_chart_btn = QPushButton(f"时间-{GlucoseTableName}图表")
        self.voltage_chart_btn = QPushButton(f"电压-{GlucoseTableName}图表")
        self.time_chart_btn.setMinimumHeight(30)
        self.voltage_chart_btn.setMinimumHeight(30)
        self.time_chart_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")
        chart_switch_layout.addWidget(self.time_chart_btn)
        chart_switch_layout.addWidget(self.voltage_chart_btn)
        main_layout.addLayout(chart_switch_layout)

        # 数据表格区域（批量更新表格）
        data_group = QGroupBox("实时数据")
        data_layout = QVBoxLayout(data_group)
        self.data_table = QTableWidget()
        self.data_table.setColumnCount(4)
        self.data_table.setHorizontalHeaderLabels(["时间(秒)", f"{GlucoseTableName}值", "电压", "接收时间"])
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.data_table.verticalHeader().setVisible(False)
        self.data_table.setEditTriggers(QTableWidget.NoEditTriggers)
        data_layout.addWidget(self.data_table)
        main_layout.addWidget(data_group, 1)

        # 图表展示区域
        self.chart_stack = QStackedWidget()
        main_layout.addWidget(self.chart_stack, 2)

        # 初始化两个图表
        self.init_time_chart()
        self.init_voltage_chart()

        # 连接按钮信号
        self.time_chart_btn.clicked.connect(lambda: self.switch_chart(0))
        self.voltage_chart_btn.clicked.connect(lambda: self.switch_chart(1))

    def init_time_chart(self):
        """初始化时间-葡萄糖图表（优化：减少重绘）"""
        time_chart_widget = QWidget()
        time_layout = QVBoxLayout(time_chart_widget)

        self.time_chart = QChart()
        self.time_chart.setTitle(f"时间-{GlucoseTableName}监测数据")
        self.time_chart.legend().setVisible(True)
        self.time_chart.legend().setAlignment(Qt.AlignBottom)

        self.time_glucose_series = QLineSeries()
        self.time_glucose_series.setName(f"{GlucoseTableName}值")
        self.time_glucose_series.setColor(QColor(255, 0, 0))
        self.time_chart.addSeries(self.time_glucose_series)

        self.time_axis_x = QValueAxis()
        self.time_axis_x.setTitleText("时间(秒)")
        self.time_axis_x.setMin(0)
        self.time_axis_x.setMax(self.max_time_data)

        self.time_axis_y = QValueAxis()
        self.time_axis_y.setTitleText(f"{GlucoseTableName}值")
        self.time_axis_y.setMin(0)
        self.time_axis_y.setMax(2000)

        self.time_chart.addAxis(self.time_axis_x, Qt.AlignBottom)
        self.time_chart.addAxis(self.time_axis_y, Qt.AlignLeft)
        self.time_glucose_series.attachAxis(self.time_axis_x)
        self.time_glucose_series.attachAxis(self.time_axis_y)

        self.time_chart_view = QChartView(self.time_chart)
        self.time_chart_view.setRenderHint(QPainter.Antialiasing)
        time_layout.addWidget(self.time_chart_view)

        self.chart_stack.addWidget(time_chart_widget)


    def init_voltage_chart(self):
        """初始化电压-葡萄糖值图表"""
        voltage_chart_widget = QWidget()
        voltage_layout = QVBoxLayout(voltage_chart_widget)

        self.voltage_chart = QChart()
        self.voltage_chart.setTitle(f"电压-{GlucoseTableName}关系图")
        self.voltage_chart.legend().setVisible(True)
        self.voltage_chart.legend().setAlignment(Qt.AlignBottom)

        self.voltage_glucose_series = QLineSeries()
        self.voltage_glucose_series.setName(f"{GlucoseTableName}")
        self.voltage_glucose_series.setColor(QColor(0, 0, 255))

        self.voltage_chart.addSeries(self.voltage_glucose_series)

        self.voltage_axis_x = QValueAxis()
        self.voltage_axis_x.setTitleText("电压值")
        self.voltage_axis_x.setMin(0)
        self.voltage_axis_x.setMax(5)

        self.voltage_axis_y = QValueAxis()
        self.voltage_axis_y.setTitleText(f"{GlucoseTableName}值")
        self.voltage_axis_y.setMin(0)
        self.voltage_axis_y.setMax(2000)

        self.voltage_chart.addAxis(self.voltage_axis_x, Qt.AlignBottom)
        self.voltage_chart.addAxis(self.voltage_axis_y, Qt.AlignLeft)

        self.voltage_glucose_series.attachAxis(self.voltage_axis_x)
        self.voltage_glucose_series.attachAxis(self.voltage_axis_y)

        self.voltage_chart_view = QChartView(self.voltage_chart)
        self.voltage_chart_view.setRenderHint(QPainter.Antialiasing)
        voltage_layout.addWidget(self.voltage_chart_view)

        self.chart_stack.addWidget(voltage_chart_widget)

    # def init_voltage_chart(self):
    #     """初始化电压-葡萄糖图表（核心：散点图+循环变色）"""
    #     voltage_chart_widget = QWidget()
    #     voltage_layout = QVBoxLayout(voltage_chart_widget)
    #
    #     self.voltage_chart = QChart()
    #     self.voltage_chart.setTitle("电压-葡萄糖关系图（循环变色）")
    #     self.voltage_chart.legend().setVisible(True)
    #     self.voltage_chart.legend().setAlignment(Qt.AlignBottom)
    #
    #     # 1. 线图：显示整体趋势（固定颜色）
    #     self.voltage_line_series = QLineSeries()
    #     self.voltage_line_series.setName("趋势线")
    #     self.voltage_line_series.setColor(QColor(128, 128, 128))  # 灰色趋势线
    #     self.voltage_line_series.setOpacity(0.6)  # 半透明，突出散点
    #
    #     # 2. 散点图：每个电压数据点循环变色（核心）
    #     self.voltage_scatter_series = QScatterSeries()
    #     self.voltage_scatter_series.setName("电压数据点")
    #     self.voltage_scatter_series.setMarkerSize(8)  # 散点大小
    #
    #     # 添加线图和散点图到图表
    #     self.voltage_chart.addSeries(self.voltage_line_series)
    #     self.voltage_chart.addSeries(self.voltage_scatter_series)
    #
    #     # 坐标轴设置
    #     self.voltage_axis_x = QValueAxis()
    #     self.voltage_axis_x.setTitleText("电压值")
    #     self.voltage_axis_x.setMin(0)
    #     self.voltage_axis_x.setMax(5)
    #
    #     self.voltage_axis_y = QValueAxis()
    #     self.voltage_axis_y.setTitleText("葡萄糖值")
    #     self.voltage_axis_y.setMin(0)
    #     self.voltage_axis_y.setMax(2000)
    #
    #     # 关联坐标轴
    #     self.voltage_line_series.attachAxis(self.voltage_axis_x)
    #     self.voltage_line_series.attachAxis(self.voltage_axis_y)
    #     self.voltage_scatter_series.attachAxis(self.voltage_axis_x)
    #     self.voltage_scatter_series.attachAxis(self.voltage_axis_y)
    #
    #     self.voltage_chart_view = QChartView(self.voltage_chart)
    #     self.voltage_chart_view.setRenderHint(QPainter.Antialiasing)
    #     voltage_layout.addWidget(self.voltage_chart_view)
    #
    #     self.chart_stack.addWidget(voltage_chart_widget)

    def switch_chart(self, index):
        """切换图表显示"""
        self.chart_stack.setCurrentIndex(index)
        # 更新按钮样式
        if index == 0:
            self.time_chart_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")
            self.voltage_chart_btn.setStyleSheet("")
        else:
            self.time_chart_btn.setStyleSheet("")
            self.voltage_chart_btn.setStyleSheet("background-color: #ccc; font-weight: bold;")

    def update_data(self, data):
        """优化：批量更新表格和图表数据"""
        # 1. 先将数据加入缓存
        seconds = data.get("Seconds", 0)
        glucose = data.get("Glucose", 0)
        voltage = data.get("Volt", 0)
        receive_time = data.get("receive_time", "")

        # 过滤无效数据
        if glucose <= 0 or voltage <= 0:
            return

        # 加入批量缓存
        self.glucose_batch_cache.append((seconds, glucose, receive_time))
        self.voltage_batch_cache.append((voltage, glucose))

        # 2. 缓存满5个点，批量更新UI（可调整batch_count）
        if len(self.glucose_batch_cache) >= self.chart_batch_count:
            self.batch_update_table()
            self.batch_update_charts()
            # 清空缓存
            self.glucose_batch_cache.clear()
            self.voltage_batch_cache.clear()

    def batch_update_table(self):
        """批量更新数据表格"""
        for seconds, glucose, receive_time in self.glucose_batch_cache:
            row = self.data_table.rowCount()
            self.data_table.insertRow(row)
            self.data_table.setItem(row, 0, QTableWidgetItem(str(seconds)))
            self.data_table.setItem(row, 1, QTableWidgetItem(str(glucose)))
            # 从电压缓存中匹配对应电压值（假设顺序一致）
            voltage = self.voltage_batch_cache[row - self.data_table.rowCount() + len(self.glucose_batch_cache)][0]
            self.data_table.setItem(row, 2, QTableWidgetItem(str(voltage)))
            self.data_table.setItem(row, 3, QTableWidgetItem(receive_time))
        # 滚动到底部
        self.data_table.scrollToBottom()

    def batch_update_charts(self):
        """批量更新两个图表"""
        # 更新时间-葡萄糖图表
        self.time_glucose_series.clear()
        # 合并历史数据和新缓存数据
        all_time_data = self.glucose_time_data + [(s, g) for s, g, _ in self.glucose_batch_cache]
        # 过滤超期数据
        latest_second = max(s for s, g in all_time_data) if all_time_data else 0
        self.glucose_time_data = [(s, g) for s, g in all_time_data if s >= latest_second - self.max_time_data]
        # 批量添加数据
        for s, g in self.glucose_time_data:
            self.time_glucose_series.append(s, g)
        # 调整X轴范围
        if self.glucose_time_data:
            min_s = min(s for s, g in self.glucose_time_data)
            max_s = max(s for s, g in self.glucose_time_data)
            self.time_axis_x.setRange(min_s, max_s + 5)
            # 调整Y轴范围
            min_g = min(g for s, g in self.glucose_time_data)
            max_g = max(g for s, g in self.glucose_time_data)
            self.time_axis_y.setRange(max(0, min_g - 100), max_g + 100)

        # 更新电压-葡萄糖图表（核心：循环变色）
        self.voltage_line_series.clear()
        self.voltage_scatter_series.clear()
        # 合并历史数据和新缓存数据
        self.voltage_glucose_data += self.voltage_batch_cache
        # 批量添加线图数据
        for v, g in self.voltage_glucose_data:
            self.voltage_line_series.append(v, g)
        # 批量添加散点数据（循环变色）
        for v, g in self.voltage_glucose_data:
            # 切换颜色（每个数据点换色，也可改为每批换色）
            current_color = self.color_list[self.current_color_idx]
            self.voltage_scatter_series.append(v, g)
            # 设置当前点的颜色（通过修改series的brush，每次append后更新）
            self.voltage_scatter_series.setBrush(current_color)
            self.voltage_scatter_series.setPen(current_color)
            # 更新颜色索引（循环）
            self.current_color_idx = (self.current_color_idx + 1) % len(self.color_list)
        # 调整电压图表坐标轴
        if self.voltage_glucose_data:
            min_v = min(v for v, g in self.voltage_glucose_data)
            max_v = max(v for v, g in self.voltage_glucose_data)
            self.voltage_axis_x.setRange(min_v - 0.1, max_v + 0.1)
            min_g = min(g for v, g in self.voltage_glucose_data)
            max_g = max(g for v, g in self.voltage_glucose_data)
            self.voltage_axis_y.setRange(max(0, min_g - 100), max_g + 100)


class SettingsPage(QWidget):
    """设置页面（保持不变）"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # 通用设置
        general_group = QGroupBox("通用设置")
        general_layout = QFormLayout(general_group)
        self.save_path = QLineEdit("./data_logs")
        self.log_interval = QLineEdit("1000")
        self.language = QComboBox()
        self.language.addItems(["中文", "English"])
        general_layout.addRow("数据保存路径:", self.save_path)
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

        # 其他选项
        options_layout = QVBoxLayout()
        self.auto_save = QCheckBox("自动保存数据")
        self.minimize_tray = QCheckBox("最小化到托盘")
        self.check_update = QCheckBox("启动时检查更新")
        self.check_update.setChecked(True)
        options_layout.addWidget(self.auto_save)
        options_layout.addWidget(self.minimize_tray)
        options_layout.addWidget(self.check_update)

        # 按钮区域
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存设置")
        self.reset_btn = QPushButton("恢复默认")
        self.save_btn.setMinimumHeight(35)
        self.reset_btn.setMinimumHeight(35)
        btn_layout.addStretch()
        btn_layout.addWidget(self.reset_btn)
        btn_layout.addWidget(self.save_btn)

        main_layout.addWidget(general_group)
        main_layout.addWidget(display_group)
        main_layout.addLayout(options_layout)
        main_layout.addStretch()
        main_layout.addLayout(btn_layout)


class MainWindow(QMainWindow):
    """主窗口（保持不变）"""
    TableName = "传感器电信号数据监测"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{self.TableName}")
        self.setMinimumSize(1000, 800)

        # 主布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 页面容器
        self.stacked_widget = QStackedWidget()
        self.serial_page = SerialPage()
        self.data_page = DataMonitorPage()
        self.settings_page = SettingsPage()
        self.stacked_widget.addWidget(self.serial_page)
        self.stacked_widget.addWidget(self.data_page)
        self.stacked_widget.addWidget(self.settings_page)

        # 底部导航栏
        self.bottom_nav = QWidget()
        self.bottom_nav.setStyleSheet("background-color: #f0f0f0; border-top: 1px solid #ccc;")
        nav_layout = QHBoxLayout(self.bottom_nav)
        nav_layout.setContentsMargins(0, 5, 0, 5)
        self.nav_btns = []
        self.add_nav_btn(nav_layout, "串口通信", 0)
        self.add_nav_btn(nav_layout, "数据监测", 1)
        self.add_nav_btn(nav_layout, "设置", 2)

        # 组装主布局
        main_layout.addWidget(self.stacked_widget)
        main_layout.addWidget(self.bottom_nav)

        # 连接数据信号
        self.serial_page.data_received.connect(self.data_page.update_data)

        # 初始页面
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
            btn.setStyleSheet("background-color: #ccc; font-weight: bold;" if i == index else "")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    event_loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    window = MainWindow()
    window.show()

    with event_loop:
        event_loop.run_forever()