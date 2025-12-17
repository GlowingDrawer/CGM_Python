# -------------------------- 关键修改：后端设置必须放在最前面 --------------------------
# 1. 先设置 Matplotlib 后端为 Qt6Agg（适配 PySide6），必须在导入 plt 前执行！
import matplotlib

matplotlib.use('Qt5Agg')  # 核心：指定与 PySide6 兼容的后端，避免调用 backend_interagg

# 2. 之后再导入其他模块（顺序不能乱）
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

# -----------------------------------------------------------------------------------

# 配置中文字体（保留你的原有设置，确保中文正常显示）
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False


def generate_dpv_potential_curve(
        e_start=-0.5,  # 起始电位 (V)
        e_end=0.5,  # 终止电位 (V)
        step_size=0.01,  # 电位步长 (V)
        pulse_amplitude=0.05,  # 脉冲振幅 (V)
        pulse_width=0.05,  # 脉冲宽度 (s)
        pulse_period=0.2,  # 脉冲周期 (s)
        sampling_time=0.001  # 采样时间间隔 (s)
):
    """生成DPV测试中工作电极的电势随时间变化曲线"""
    # 计算总步数和总时间
    num_steps = int(np.ceil((e_end - e_start) / step_size)) + 1
    total_time = num_steps * pulse_period

    # 生成时间数组
    time = np.arange(0, total_time, sampling_time)
    potential = np.zeros_like(time)

    # 计算每个时间段的电势
    for i in range(num_steps):
        # 计算当前步的基础电位
        base_potential = e_start + i * step_size

        # 计算当前步的时间范围
        start_idx = int(i * pulse_period / sampling_time)
        end_idx = int((i + 1) * pulse_period / sampling_time)
        if end_idx > len(time):
            end_idx = len(time)

        # 计算脉冲开始和结束的索引
        pulse_start_idx = start_idx
        pulse_end_idx = int(start_idx + pulse_width / sampling_time)
        if pulse_end_idx > end_idx:
            pulse_end_idx = end_idx

        # 设置脉冲期间的电势（基础电位 + 脉冲振幅）
        potential[pulse_start_idx:pulse_end_idx] = base_potential + pulse_amplitude

        # 设置脉冲后的电势（恢复到基础电位）
        if pulse_end_idx < end_idx:
            potential[pulse_end_idx:end_idx] = base_potential

    return time, potential


def plot_potential_curve(time, potential):
    """绘制工作电极的电势随时间变化曲线"""
    plt.figure(figsize=(12, 6))
    plt.plot(time, potential, 'b-', linewidth=1.2)

    # 设置图表标题和轴标签
    plt.title('DPV测试中工作电极的电势变化曲线', fontsize=14, pad=20)
    plt.xlabel('时间 (s)', fontsize=12)
    plt.ylabel('工作电极电势 (V)', fontsize=12)

    # 设置网格
    plt.grid(True, linestyle='--', alpha=0.7)

    # 设置刻度间隔
    ax = plt.gca()
    ax.xaxis.set_major_locator(MultipleLocator(1.0))  # x轴主刻度间隔1s
    ax.xaxis.set_minor_locator(MultipleLocator(0.2))  # x轴次刻度间隔0.2s
    ax.yaxis.set_major_locator(MultipleLocator(0.2))  # y轴主刻度间隔0.2V

    # 添加图例说明DPV特征（修复之前的bullet符号问题，中文宋体/雅黑支持该符号）
    plt.text(0.02, 0.98,
             '• 基础电位线性扫描\n• 叠加固定振幅的脉冲\n• 脉冲后恢复至基础电位',
             transform=ax.transAxes,
             verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.show()  # 此时后端已为Qt6Agg，可正常弹出交互式窗口


# 生成并绘制DPV电势曲线
if __name__ == "__main__":
    # 可选：验证后端是否正确设置（运行后查看控制台输出）
    print("当前Matplotlib后端：", matplotlib.get_backend())  # 应输出 Qt6Agg

    time, potential = generate_dpv_potential_curve(
        e_start=-0.5,
        e_end=0.5,
        step_size=0.01,
        pulse_amplitude=0.05,
        pulse_width=0.05,
        pulse_period=0.2,
        sampling_time=0.001
    )
    plot_potential_curve(time, potential)