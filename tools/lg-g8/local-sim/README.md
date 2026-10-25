# LG G8 的本机模拟验证

这套工具让现有镜像中的 ARM64 Linux 真正在 QEMU 中执行，而不是只检查文件格式。
不连接手机，不使用 adb/fastboot，不透传 USB，也不上传文件。

在本项目现有工作目录布局下，一条命令运行全部检查：

```sh
python3 tools/lg-g8/local-sim/run_all.py
```

输入默认来自 `~/Documents/Codex/2026-10-25-lg-g8-omarchy`，固件源码默认来自
仓库旁边的 `../lg-g8-sources-20261025/mu_aloha_platforms`。
这些镜像和源码不包含在本仓库中。换目录可用 `--workspace`、`--edk2-source`；
每个输入也有单独参数，运行 `--help` 查看。

需要 Python 3.10+、QEMU AArch64、AArch64 EDK2 固件、Clang/LLD、LLVM objcopy/objdump、
make、uv，以及该内核已完成的构建目录。BootShim 的 Unicorn 2.1.4 依赖由 uv 放在隔离缓存中。
本次使用 QEMU 11.1.2、现有 Linux **7.3.0-sm8150**；不会重编完整内核。
USB 测试只编译两个外部模块，并核对原有 Image 和 `.config` 的哈希未变化。

## 找到并修复的问题

输入 `lg-g8-direct-efi-stub-probe.img` 的初始文件系统中有 `/init` 和动态链接的 BusyBox，
却缺少 BusyBox 指定的 `/lib/ld-musl-aarch64.so.1` 和 musl libc。
原包在模拟器中报 `Failed to execute /init (error -2)`，随后 `No working init found`。
内核已经进入了 Linux；不能把这种故障当作内核没有执行。

修复包加入匹配的 ARM64 musl 库和早期控制台设备节点，保留原来的 `/init` 和 BusyBox。
测试包另外加入检查脚本；检查脚本最终仍让原 `/init` 成为 PID 1。
虚拟 USB 变体再加入 `dummy_hcd` 和 `cdc-acm`，让 Linux 真实的主机、设备和 ACM 驱动完成双向字节传输。
这两个模块和检查脚本不会进入导出的干净运行包。

原始 Android 镜像 SHA-256：

```text
e465adf63f1b9c48bec3832f2a32eb9b6eea016781ee553745afdea70402dacd
```

解压后的内核 SHA-256，修复前后不变：

```text
bb475e12ce81e2e599a643317d50b0a8603d1eec573ca41be09fa029307163a7
```

## 检查覆盖范围

| 检查 | 实际执行和判定 |
| --- | --- |
| 原包回归 | 同一内核和原 initramfs 复现缺少解释器导致的启动失败；此项 PASS 表示复现了故障 |
| 修复包直接启动 | QEMU 启动同一内核，原 `/init` 运行并建立 configfs USB gadget |
| 标准 UEFI 启动 | QEMU EDK2 从虚拟 FAT/ESP 加载小型标准 EFI 程序，由它调用内核 EFI stub；原 `/init` 运行 |
| UEFI + 虚拟 USB | 同上，加上内核虚拟 USB 控制器；检查 ACM 枚举和两方向内容相同的传输 |
| BootShim | Unicorn 执行两个实际 UEFI 镜像里的 112 字节跳板，20 次检查 3 MiB 搬运、跳转和寄存器；12 项坏输入由主机预检拒绝 |
| UEFI fastboot | 原 C 控制流加 EFI/USB 边界替身，31 项预期结果；包括正常结果和故障复现 |

BootShim 检查停在 UEFI 的第一条指令前。QEMU 使用通用 `virt` 主板和 QEMU 的 EDK2，
不是 LG 的 ABL、设备树或那份板级 UEFI。几组检查分别验证各段软件，不能当作整条 LG 启动链已经通过。
QEMU 没有 LG G8/SM8150 主板模型，不能验证它的内存保留区、显示、触摸、UFS、电源和物理 USB。
因此，结果证明本机的软件启动和修复有效，不能据此宣称 LG G8 或 Omarchy 已适配完成。

## 输出

默认输出在仓库的 `out/local-sim/`：

- `suite.json`：本轮命令、返回值、数量和总判定；失败会保留失败状态。
- `linux/result.json` 和 `*.log`：QEMU 启动串口记录及输入哈希。
- `linux/esp/`：测试使用的虚拟 ESP；包含检查脚本，属于模拟测试输入。
- `software/kernel.efi`、`software/initramfs.cpio`、`software/manifest.json`：同一内核和修复后的干净运行包。
- `bootshim/report.json`：逐个镜像、DTB 和搬运路径的结果。
- `fastboot/results.json`：31 个用例的结果，旁边保留各项日志和源码 manifest。

`software/` 没有模拟脚本或虚拟 USB 模块，也没有手机所需的 DTB、启动打包或完整根文件系统。
它不是可直接刷入手机的成品镜像。Android header 在这里仅用于提取内核和 initramfs，
未在 QEMU 中执行 LG ABL 对 header 和加载地址的处理。

各部分也能单独运行，参见 [BootShim](bootshim/README.md)、[fastboot](fastboot/README.md)
和 `run_linux.py --help`。单次虚拟启动通常几秒到十几秒；整套耗时受模块准备、缓存和本机负载影响。
