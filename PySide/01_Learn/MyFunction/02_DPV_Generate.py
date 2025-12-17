import numpy as np


def generate_dpv_voltage_array(
        start_voltage,  # 起始电压(mV)
        end_voltage,  # 终止电压(mV)
        step_voltage,  # 电压步长(mV)
        pulse_amplitude,  # 脉冲幅度(mV)
        pulse_width,  # 脉冲宽度(ms)
        pulse_period,  # 脉冲周期(ms)
        sample_rate  # 采样率(Hz)
):
    """
    生成差分脉冲伏安法(DPV)的电压数组

    参数:
        start_voltage: 起始电压(mV)
        end_voltage: 终止电压(mV)
        step_voltage: 电压步长(mV)
        pulse_amplitude: 脉冲幅度(mV)
        pulse_width: 脉冲宽度(ms)
        pulse_period: 脉冲周期(ms)
        sample_rate: 采样率(Hz)

    返回:
        电压数组和C++格式的数组字符串
    """
    # 计算总步数和总时间
    total_steps = int(abs(end_voltage - start_voltage) / step_voltage) + 1
    samples_per_pulse = int(pulse_period * sample_rate / 1000)
    pulse_samples = int(pulse_width * sample_rate / 1000)
    total_samples = total_steps * samples_per_pulse

    # 初始化电压数组
    voltage_array = np.zeros(total_samples, dtype=np.float32)

    # 生成电压序列
    for step in range(total_steps):
        # 计算当前基础电压
        base_voltage = start_voltage + step * step_voltage
        if start_voltage > end_voltage:
            base_voltage = start_voltage - step * step_voltage

        # 填充一个周期内的电压值
        for sample in range(samples_per_pulse):
            index = step * samples_per_pulse + sample
            # 脉冲期间添加脉冲幅度，其余时间为基础电压
            if sample >= (samples_per_pulse - pulse_samples):
                voltage_array[index] = base_voltage + pulse_amplitude
            else:
                voltage_array[index] = base_voltage

    # 转换为C++数组格式
    cpp_array = "const float dpv_voltage_array[] = {\n    "
    for i, voltage in enumerate(voltage_array):
        cpp_array += f"{voltage:.2f}f"
        if i < len(voltage_array) - 1:
            cpp_array += ", "
            if (i + 1) % 10 == 0:  # 每10个元素换行
                cpp_array += "\n    "
    cpp_array += "\n};\n"
    cpp_array += f"const uint32_t dpv_array_length = {len(voltage_array)};\n"

    # 添加采样点信息（用于电流采样时刻判断）
    sample_points = []
    for step in range(total_steps):
        # 脉冲前采样点（脉冲开始前）
        pre_pulse_index = step * samples_per_pulse + (samples_per_pulse - pulse_samples - 1)
        # 脉冲后采样点（脉冲结束前）
        post_pulse_index = step * samples_per_pulse + (samples_per_pulse - 1)
        sample_points.append((pre_pulse_index, post_pulse_index))

    # 生成采样点的C++数组
    cpp_sample_points = "const uint32_t dpv_sample_points[][2] = {\n    "
    for i, (pre, post) in enumerate(sample_points):
        cpp_sample_points += f"{{{pre}, {post}}}"
        if i < len(sample_points) - 1:
            cpp_sample_points += ", "
            if (i + 1) % 5 == 0:  # 每5个元素换行
                cpp_sample_points += "\n    "
    cpp_sample_points += "\n};\n"

    return voltage_array, cpp_array + cpp_sample_points


# 示例参数设置
if __name__ == "__main__":
    # DPV参数配置
    start_voltage = 0  # 起始电压0mV
    end_voltage = 800  # 终止电压800mV
    step_voltage = 2  # 电压步长2mV
    pulse_amplitude = 50  # 脉冲幅度50mV
    pulse_width = 60  # 脉冲宽度60ms
    pulse_period = 1000  # 脉冲周期1000ms
    sample_rate = 1000  # 采样率1000Hz

    # 生成DPV电压数组
    voltage_array, cpp_code = generate_dpv_voltage_array(
        start_voltage,
        end_voltage,
        step_voltage,
        pulse_amplitude,
        pulse_width,
        pulse_period,
        sample_rate
    )

    # 打印一些基本信息
    print(f"生成的DPV数组长度: {len(voltage_array)}")
    print(f"总步数: {int(abs(end_voltage - start_voltage) / step_voltage) + 1}")
    print(f"每个脉冲周期的采样点数: {int(pulse_period * sample_rate / 1000)}")

    # 将C++代码保存到文件
    with open("dpv_voltage_array.h", "w", encoding="utf-8") as f:
        f.write("// DPV电压数组，用于查表法实现\n")
        f.write("// 生成参数:\n")
        f.write(f"// 起始电压: {start_voltage}mV, 终止电压: {end_voltage}mV, 步长: {step_voltage}mV\n")
        f.write(f"// 脉冲幅度: {pulse_amplitude}mV, 脉冲宽度: {pulse_width}ms, 脉冲周期: {pulse_period}ms\n")
        f.write(f"// 采样率: {sample_rate}Hz\n\n")
        f.write(cpp_code)

    print("DPV数组已保存到dpv_voltage_array.h文件")
