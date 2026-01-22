import serial
import serial.tools.list_ports

# 用户手动指定的端口
manual_port = "COM28"  # 修改为你的实际端口


# 检查端口是否存在
def check_port_exists(target_port):
    available_ports = [port.device for port in serial.tools.list_ports.comports()]
    if target_port not in available_ports:
        print(f"错误: 端口 {target_port} 不存在！")
        return False
    return True


if not check_port_exists(manual_port):
    exit(1)


# 检查是否为CH340设备
def is_ch340_device(port_name):
    for port in serial.tools.list_ports.comports():
        if port.device == port_name:
            if port.vid == 0x1A86 and port.pid == 0x7523:  # CH340的VID/PID
                return True
    return False


if not is_ch340_device(manual_port):
    print(f"错误: {manual_port} 不是CH340设备！")
    exit(1)

# 尝试打开串口
try:
    ser = serial.Serial(
        port=manual_port,
        baudrate=9600,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=1
    )
except serial.SerialException as e:
    print(f"无法打开串口: {e}")
    exit(1)
a=[0 ,1 ,2];
print(type(a))
# 发送数据（你的原始代码）
data = bytes([2,200, 80,68,49])
packet = bytes([0xFF]) + data + bytes([0xFE])
ser.write(packet)
print(f"Sent: {packet.hex(' ')}")

# 关闭串口
ser.close()

