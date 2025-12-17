# GUI框架核心模块
import math

import time
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QFileDialog, QMessageBox, QStatusBar,
    QTabWidget, QToolBar, QFormLayout, QDoubleSpinBox,
)
from PySide6.QtCore import Qt, QObject, QThread, Signal, QIODevice
from PySide6.QtGui import QPainter
import serial

# 绘图可视化库
import pyqtgraph as pg
from pyqtgraph import PlotWidget, PlotDataItem

# 科学计算与数据处理
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter, find_peaks  # 数据平滑与峰值检测

# 系统与文件操作
import sys
import json
import struct  # 二进制数据打包[doc_1存储格式]


class ElectrochemicalWorkstation(QMainWindow):
    def __init__(self):
        super().__init__()
        # 模块初始化
        self.comm = CommunicationModule()
        self.plot = PlotModule()
        self.data_processor = DataProcessor()
        self.storage = StorageModule()

        # UI构建
        self.init_ui()
        self.setup_connections()

    def init_ui(self):
        """主界面布局设计"""
        self.setWindowTitle("PyEChem Workstation")
        self.tabs = QTabWidget()

        # 实时监控页
        self.monitor_tab = QWidget()
        self.plot_widget = self.plot.get_plot_widget()
        self.status_bar = QStatusBar()

        # 参数设置页
        self.param_tab = ParameterSettings()

        self.tabs.addTab(self.monitor_tab, "实时监控")
        self.tabs.addTab(self.param_tab, "参数设置")
        self.setCentralWidget(self.tabs)


class CommunicationModule(QObject):
    data_received = Signal(tuple)  # 发射 (adc_value, range_code)

    def __init__(self, port="COM1", baud=115200):
        super().__init__()
        self.serial = serial.Serial(port, baudrate=baud, timeout=0.1)
        if not self.serial.is_open:
            raise IOError(f"无法打开串口 {port}")
        self.buffer = bytearray()

    def has_data(self):
        """检查串口缓冲区是否有数据"""
        return self.serial.in_waiting > 0

    def connect_device(self, port, baudrate=115200):
        """连接下位机设备"""
        try:
            # 如果已有串口对象且未关闭，则先关闭
            if self.serial.is_open:
                self.serial.close()
            # 打开新的串口
            self.serial = serial.Serial(port, baudrate=baudrate, timeout=0.1)
            if not self.serial.is_open:
                raise IOError(f"无法打开串口 {port}")
            print(f"成功连接到串口 {port}")
            return True
        except Exception as e:
            print(f"连接串口失败: {e}")
            return False

    def _handle_data(self):
        """处理14位数据格式[doc_1表4.2]"""
        while self.serial.in_waiting >= 2:  # 确保缓冲区至少有2字节
            data = self.serial.read(2)  # 每次读取2字节
            if len(data) == 2:
                # 解析AD转换数据
                adc_value = (data[0] << 4) | (data[1] >> 4)  # 12位AD值
                range_code = data[1] & 0x03  # 量程标记
                self.data_received.emit((adc_value, range_code))  # 发射信号

    def start_reading(self):
        """启动数据读取循环"""
        import threading
        def read_loop():
            while self.serial.is_open:
                self._handle_data()

        # 使用线程异步读取数据
        threading.Thread(target=read_loop, daemon=True).start()

    def close_port(self):
        """关闭串口"""
        if self.serial.is_open:
            self.serial.close()
            print("串口已关闭")


class PlotModule:
    def __init__(self):
        self.plot_widget = pg.PlotWidget()
        # 设置背景颜色为白色
        self.plot_widget.setBackground('w')  # 白色背景
        self.plot_widget.setAntialiasing(True)  # 启用抗锯齿

        # 定义字体样式
        font_styles = {"color": "#000", "font-size": "12pt"}  # 黑色字体，字号12pt

        # 设置标题和标签样式
        self.plot_widget.setTitle("实时监控", **font_styles)
        self.plot_widget.setLabel('left', '电流 (A)', **font_styles)
        self.plot_widget.setLabel('bottom', '电压 (V)', **font_styles)

        # 设置网格线为黑色
        # self.plot_widget.showGrid(x=True, y=True, alpha=1.0)  # 显示网格线，alpha=1 表示不透明
        self.plot_widget.getAxis('bottom').setGrid(255)  # 设置 X 轴网格线颜色为黑色 (255 = 0xFF)
        self.plot_widget.getAxis('left').setGrid(255)  # 设置 Y 轴网格线颜色为黑色

        self.plot_widget.addLegend()

        # 默认范围和曲线
        self.x_min, self.x_max = -5.0, 5.0  # 默认电压范围
        self.current_range = 1e-3  # 默认电流范围
        self.curve = self.plot_widget.plot([], [], pen='b', name="CV曲线")  # 蓝色曲线

    def get_plot_widget(self):
        """返回绘图部件"""
        return self.plot_widget

    def update_plot(self, voltage, current):
        """动态更新曲线"""
        if not hasattr(self, "x_data"):
            self.x_data, self.y_data = [], []
        # 更新数据
        self.x_data.append(voltage)
        self.y_data.append(current)
        # 更新曲线
        self.curve.setData(self.x_data, self.y_data)
        # 自动缩放调整
        if len(self.x_data) > 1:
            self._adjust_axes()

    def _adjust_axes(self):
        """自动调整坐标轴范围"""
        x_min, x_max = min(self.x_data), max(self.x_data)
        y_min, y_max = min(self.y_data), max(self.y_data)
        # 设置新的范围，增加一定缓冲区域
        self.plot_widget.setXRange(x_min - 0.1 * abs(x_min), x_max + 0.1 * abs(x_max))
        self.plot_widget.setYRange(y_min - 0.1 * abs(y_min), y_max + 0.1 * abs(y_max))


class DataProcessor:
    def __init__(self):
        self.window_size = 15
        self.poly_order = 2

    def smooth_data(self, raw_data):
        """Savitzky-Golay滤波实现"""
        return savgol_filter(raw_data, self.window_size, self.poly_order)

    def detect_peaks(self, data):
        """判峰算法实现[doc_1判峰逻辑]"""
        peaks, _ = find_peaks(data, prominence=0.1, width=5)
        return {
            'peak_count': len(peaks),
            'positions': peaks,
            'currents': data[peaks]
        }


class StorageModule:
    def save_mcv(self, filename, params, data):
        """自定义存储格式实现[doc_1表4.4]"""
        with open(filename, 'wb') as f:
            # 写入文件头
            f.write(struct.pack('B', params['method_code']))  # 实验方法
            f.write(struct.pack('I', len(data)))  # 数据个数

            # 写入参数区
            param_bytes = json.dumps(params).encode('utf-8')
            f.write(struct.pack('I', len(param_bytes)))  # 参数长度
            f.write(param_bytes)

            # 写入数据区
            for point in data:
                f.write(struct.pack('ff', point[0], point[1]))  # 电压+电流


class ParameterSettings(QWidget):
    def __init__(self):
        super().__init__()
        self._init_cv_params()

    def _init_cv_params(self):
        """CV参数设置[doc_2信号发生器配置]"""
        layout = QFormLayout()

        self.start_volt = QDoubleSpinBox()
        self.start_volt.setRange(-5.0, 5.0)
        self.end_volt = QDoubleSpinBox()
        self.scan_rate = QDoubleSpinBox()
        self.scan_rate.setRange(0.01, 1.0)

        layout.addRow("初始电位(V)", self.start_volt)
        layout.addRow("终止电位(V)", self.end_volt)
        layout.addRow("扫描速率(V/s)", self.scan_rate)

        self.setLayout(layout)


def init_monitor_tab(self):
    """实时数据显示布局[doc_1绘图需求]"""
    layout = QVBoxLayout()

    # 曲线显示区域
    self.plot_widget = PlotModule()

    # 控制工具栏
    toolbar = QToolBar()
    self.start_btn = QPushButton("开始实验")
    self.stop_btn = QPushButton("停止实验")

    toolbar.addWidget(self.start_btn)
    toolbar.addWidget(self.stop_btn)

    layout.addWidget(toolbar)
    layout.addWidget(self.plot_widget)

    self.monitor_tab.setLayout(layout)


class WorkerThread(QThread):
    data_updated = Signal(float, float)

    def __init__(self, comm):
        super().__init__()
        self.comm = comm

    def run(self):
        while True:
            if self.comm.has_data():
                adc, range_code = self.comm.read_data()
                voltage = (adc / 4095 * 3 - 1.5) * 2  # [doc_1公式4-1]
                current = voltage / self._get_resistance(range_code)
                self.data_updated.emit(voltage, current)

    def _get_resistance(self, code):
        """量程转换[doc_1表5.1]"""
        return {
            0: 2000,  # 2kΩ
            1: 20000,  # 20kΩ
            2: 30000,  # 30kΩ
            3: 40000  # 40kΩ
        }.get(code, 2000)


def test_communication():
    simulator = VirtualDevice()
    workstation = ElectrochemicalWorkstation()

    # 模拟发送测试数据包
    test_data = [(2048, 0), (3000, 1), (4095, 3)]
    expected_voltages = [0.0, 1.024, 3.0]

    for data, expected in zip(test_data, expected_voltages):
        voltage = workstation.data_processor.convert_adc(*data)
        assert abs(voltage - expected) < 0.001


def test_plot_performance():
    plot = PlotModule()

    # 测试5000个数据点渲染时间
    start = time.time()
    for i in range(5000):
        plot.update_plot(i / 100, math.sin(i / 100))

    assert (time.time() - start) < 1.0


class ElectrochemicalWorkstation(QMainWindow):
    def __init__(self):
        super().__init__()
        # 模块初始化
        self.comm = CommunicationModule(port="COM1", baud=115200)
        self.plot = PlotModule()
        self.data_processor = DataProcessor()
        self.storage = StorageModule()
        # UI构建
        self.init_ui()
        self.setup_connections()

    def init_ui(self):
        """主界面布局设计"""
        self.setWindowTitle("PyEChem Workstation")
        self.tabs = QTabWidget()

        # 实时监控页
        self.monitor_tab = QWidget()
        self.plot_widget = self.plot.get_plot_widget()
        self.status_bar = QStatusBar()

        # 参数设置页
        self.param_tab = ParameterSettings()
        self.tabs.addTab(self.monitor_tab, "实时监控")
        self.tabs.addTab(self.param_tab, "参数设置")

        # 监控页布局
        layout = QVBoxLayout()
        toolbar = QToolBar()
        self.start_btn = QPushButton("开始实验")
        self.stop_btn = QPushButton("停止实验")
        toolbar.addWidget(self.start_btn)
        toolbar.addWidget(self.stop_btn)
        layout.addWidget(toolbar)
        layout.addWidget(self.plot_widget)
        self.monitor_tab.setLayout(layout)

        self.setCentralWidget(self.tabs)
        self.setStatusBar(self.status_bar)

    def setup_connections(self):
        """信号与槽连接"""
        self.start_btn.clicked.connect(self.start_experiment)
        self.stop_btn.clicked.connect(self.stop_experiment)

        # 启动数据读取线程
        self.worker_thread = WorkerThread(self.comm)
        self.worker_thread.data_updated.connect(self.update_plot)

    def start_experiment(self):
        """开始实验"""
        try:
            if not self.comm.serial.is_open:
                port = "COM1"  # 默认串口号
                baudrate = 115200
                if not self.comm.connect_device(port, baudrate):
                    QMessageBox.critical(self, "错误", f"无法打开串口 {port}")
                    return

            self.status_bar.showMessage("实验正在进行...")
            self.worker_thread.start()  # 启动工作线程
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def stop_experiment(self):
        """停止实验"""
        self.worker_thread.terminate()  # 停止工作线程
        self.comm.close_port()
        self.status_bar.showMessage("实验已停止")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def update_plot(self, voltage, current):
        """更新绘图"""
        self.plot.update_plot(voltage, current)

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        self.stop_experiment()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 创建电化学工作站实例
    workstation = ElectrochemicalWorkstation()

    # 显示窗口
    workstation.show()

    # 模拟测试数据（用于调试）
    def simulate_data():
        if hasattr(workstation, "worker_thread") and workstation.worker_thread.isRunning():
            adc_values = [2048, 3000, 4095]
            range_codes = [0, 1, 3]
            for adc, code in zip(adc_values, range_codes):
                voltage = (adc / 4095 * 3 - 1.5) * 2  # 转换电压
                resistance = workstation.worker_thread._get_resistance(code)
                current = voltage / resistance  # 计算电流
                workstation.plot.update_plot(voltage, current)
            time.singleShot(1000, simulate_data)  # 每秒模拟一次数据

    # 启动模拟数据生成
    simulate_data()

    sys.exit(app.exec())