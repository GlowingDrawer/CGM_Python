import json
import sys
import time
import matplotlib
import numpy as np
import serial
from PySide6.QtWidgets import QMainWindow, QApplication, QVBoxLayout, QWidget
from PySide6.QtCore import QTimer
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

matplotlib.use("Qt5Agg")

# 配置中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

# 模拟参数
n = 1          # 电子转移数
D = 1e-5       # 扩散系数 (cm²/s)
C = 1e-3       # 浓度 (mol/cm³)
v = 0.1        # 扫描速率 (V/s)
A = 0.1        # 电极面积 (cm²)
R = 8.314      # 气体常数
T = 298        # 温度 (K)
F = 96485      # 法拉第常数

# 生成电位范围
E_start = 0.5   # 起始电位 (V)
E_end = -0.5    # 终止电位 (V)
E = np.linspace(E_start, E_end, 1000)
E_reverse = np.linspace(E_end, E_start, 1000)

# 计算峰电流 (Randles-Sevcik方程)
Ip = 2.69e5 * n**1.5 * A * D**0.5 * C * v**0.5

# 生成氧化/还原峰（简化模型）
I_forward = Ip * (np.exp((n*F)/(R*T)*(E - 0.2)) / (1 + np.exp((n*F)/(R*T)*(E - 0.2))))  # 还原峰
I_reverse = -Ip * (np.exp((n*F)/(R*T)*(0.2 - E_reverse)) / (1 + np.exp((n*F)/(R*T)*(0.2 - E_reverse))))  # 氧化峰

# 合并完整循环
E_full = np.concatenate((E, E_reverse))
I_full = np.concatenate((I_forward, I_reverse))


plt.figure(figsize=(8, 6))
plt.plot(E_full, I_full, 'b-', linewidth=2, label='CV曲线')
plt.xlabel('电位 (V)', fontsize=12)
plt.ylabel('电流 (A)', fontsize=12)
plt.title('循环伏安图', fontsize=14)
plt.grid(True, linestyle='--', alpha=0.6)

# 标记氧化还原峰
plt.annotate('还原峰', xy=(0.15, Ip), xytext=(0.3, Ip*1.2),
             arrowprops=dict(arrowstyle="->"))
plt.annotate('氧化峰', xy=(-0.15, -Ip), xytext=(-0.4, -Ip*1.2),
             arrowprops=dict(arrowstyle="->"))
plt.legend()
plt.show()