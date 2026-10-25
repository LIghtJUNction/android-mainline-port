# 最小原生 Linux 诊断 shell

这里的 `/init` 会挂载 proc、sysfs、devtmpfs、devpts、configfs 和临时文件系统，
创建 USB ACM 串口，并在 USB 串口和活动控制台上运行 BusyBox getty 与真正的交互式 `/bin/sh`。
退出 shell 后，getty 会重新建立会话。它不启动网络服务。

**这是没有密码验证的本地 root 诊断 shell，不是发行版成品。**
USB 串口客户端连接后按一次 Enter 才会进入 shell；这是有意让客户端先设置串口为 raw 模式，
避免刚打开串口时双方回显造成循环。默认速率 115200，8N1。
`exit` 退出会话，重新连接并按 Enter 可以进入新会话。

在现有本机工作目录布局下运行：

```sh
python3 tools/lg-g8/local-sim/native-init/verify.py
```

也可以通过 `--initramfs`、`--kernel`、`--usb-module-dir` 和 `--out` 指定路径。
默认读取仓库根目录 `out/local-sim/software/` 的干净 initramfs、同一份 Linux 7.3 内核，
以及 `out/local-sim/usb-modules/` 的匹配模块。
原始 initramfs、原 `/init` 和内核文件不会覆盖。

默认使用 QEMU 直接启动。加 `--shell /path/to/Shell.efi` 可测试标准 UEFI Shell 路线，
`--firmware` 可指定 QEMU 的 EDK2 固件。完整 `run_all.py` 使用这一 UEFI 路线，
并通过 `--qemu-dtb` 提供前一阶段生成的外部 QEMU 设备树，检查正在运行的内核确实选中它。
这个参数只接受明确标记的虚拟主板 DTB，不能填手机 DTB。

`out/initramfs-native.cpio` 是可复用的干净运行包，只替换 `/init`、加入 `bin/diag-login` 和 root 目录，
保留原 BusyBox 和匹配的 musl 运行库；不包含测试脚本或虚拟 USB 模块。
`out/initramfs-test.cpio` 另加测试脚本与 `dummy_hcd`/`cdc-acm`，只供 QEMU 使用。

验证运行真实 ARM64 Linux 和 getty，让同一内核的 USB 主机和设备驱动枚举出 `ttyACM0` 与 `ttyGS0`。
测试从主机端串口发送 shell 命令，检查：

1. `id -u` 为 0，`uname -m` 为 aarch64，`317*29` 求值为 9193，并能写入 `/run` 文件。
2. 通过 USB 发送 `exit`，关闭主机端串口文件描述符，重新打开串口并按 Enter。
3. 新会话有不同 PID，`457*13` 求值为 5941，且能读取上一会话写入的 9193。
4. PID 1 仍是原生 `/init`；getty applet 确实存在。
5. 使用 UEFI 模式时，标准 Shell 和内核 EFI 环境都必须出现；指定外部 QEMU DTB 时也检查实际 model。

9193 和 5941 不出现在发送的命令原文中，避免将终端回显误判为命令执行。
原始命令、进程 PID、逐项检查和输入/输出 SHA-256 保存在 `out/report.json`，
完整串口和 USB 会话记录在 `out/qemu.log`。
测试客户端还实现了最小的终端光标位置回应，以配合 BusyBox 的交互式行编辑。

这证明诊断用户空间和 USB 软件路径可用，未模拟 LG G8 的 PHY、供电和中断，
也没有操作手机、运行 adb/fastboot、透传 USB 或上传任何文件。
