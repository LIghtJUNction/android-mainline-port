# LG G8 Alpha 板级候选

`sm8150-lg-alphalm-board.dts` 是完整的校准后板级源码，保留原 BSD-3-Clause 声明和作者信息。
SPDX 许可证正文见 [LICENSES/BSD-3-Clause](LICENSES/BSD-3-Clause)。
目标为已导出运行时设备树的 `LG Electronics, Alpha_lao, rev_1.0`、board-id `0x020c0008 0`。
它包含 RAM、固件保留区和 USB 的已验证静态修复，**尚未证明在 LG G8 上启动，也没有完成显示、触控或 Omarchy 验收**。

与运行时 FDT 的区间核对、原候选差异及复现工具见
[DTB 验证](../../tools/lg-g8/local-sim/dtb/README.md)。编译后的本次候选 DTB SHA-256 为：

```text
eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9
```

## 构建

需要匹配的 ARM64/SM8150 内核源码及 dt-bindings/PMIC DTS，不能独立于它们编译。
本次工作区的 Makefile 版本为 7.3.0；该源码树没有 Git 元数据，不能提供可信的基线 commit。
不要把目录名、内核版本号或一次编译成功当成完整来源证明。

在仓库根目录，可以只用该内核的头文件和 DTS include 编译本文件，原内核树不需要修改：

```sh
LGG8_KERNEL_SOURCE=/path/to/kernel/linux
mkdir -p out/lg-alphalm
clang -E -P -x assembler-with-cpp -nostdinc -undef -D__DTS__ \
  -I "$LGG8_KERNEL_SOURCE/arch/arm64/boot/dts/qcom" \
  -I "$LGG8_KERNEL_SOURCE/include" \
  -I "$LGG8_KERNEL_SOURCE/scripts/dtc/include-prefixes" \
  devices/lg-alphalm/sm8150-lg-alphalm-board.dts \
  > out/lg-alphalm/board.preprocessed.dts
dtc -I dts -O dtb -o out/lg-alphalm/board.dtb out/lg-alphalm/board.preprocessed.dts
```

加入内核的常规构建时，还需按该内核的 QCOM DTB Makefile 注册文件。
本次复现用到的主要 SoC/PMIC 输入哈希：

| 相对内核路径 | SHA-256 |
| --- | --- |
| `arch/arm64/boot/dts/qcom/sm8150.dtsi` | `82887c779c9425ca23e49a81fe3ff8d8c7eec4d073edb305e6a9270371f3b7bc` |
| `arch/arm64/boot/dts/qcom/pm8150.dtsi` | `d9a1f3fb854bb4b5bcf1bc372f634cc3a83923d38d5b765b1b1cdccc6f4a1c40` |
| `arch/arm64/boot/dts/qcom/pm8150b.dtsi` | `bbca66e6618c1f6003c80753273059c17ba15be425787fece9a73b1e6108ab3a` |
| `arch/arm64/boot/dts/qcom/pm8150l.dtsi` | `f22dbe783426acf874045ac361edefa78aebbb618ce6f52763509a26695d3a50` |

这些不是完整依赖清单；头文件或内核 DTS 变化仍可能改变结果。
当前 tiny 内核关闭了 DRM，因此本机 USB shell 验证不能证明本文件的显示节点正确。
固件 splash 内存保留也不表示 Linux 显示驱动已接管屏幕。

另有独立的 [USB2 诊断板树](USB2-DIAGNOSTIC.md)：仅覆盖 USB1 的 PHY 引用、速度上限和 UTMI 时钟选择，用于解除禁用 QMP PHY 的消费依赖。原板树保持不变；诊断配置已进行一次 RAM 测试，未取得 Linux USB/shell 证据，手机已回到原 Android。主机控制错误及观察缺口使修改效果仍未定，完整记录见诊断说明。

## 离线启动检查与看门狗配置

[boot 内存契约审计](../../tools/lg-g8/BOOT-CONTRACT.md) 核对已安装的 Android 修复 boot 中的载荷、RAM 和全部固定固件保留区；它不是新主线候选镜像，另行比较的 `patched.dtb` 仅为外部假设布局。Android header 的地址不能替代实际 ABL 装载证据；无冲突也不能证明可启动。

[USB、DMA 与显示离线契约](../../tools/lg-g8/HARDWARE-CONTRACT.md) 记录 USB1 SID 声明一致、候选引用禁用的 USB3 QMP PHY，以及当前配置/FDT 未建立显示输出路径的边界。主线 Linux/Omarchy、USB、显示、pstore 读回及早期挂死恢复均未验收。

[early-watchdog.config](configs/early-watchdog.config) 在 [linux-7.3-tiny.config](configs/linux-7.3-tiny.config) 后合并，将高通看门狗驱动编入内核。它已通过 Kconfig 解析和 ARM64 驱动对象编译，尚未构建完整内核或启动镜像。当前 `WATCHDOG_OPEN_TIMEOUT=0` 允许内核在用户态接管前持续发送心跳，不提供启动失败后的有限复位期限；验证记录和限制见 [看门狗说明](../../docs/lg-g8-omarchy/WATCHDOG-RECOVERY.md#内置驱动配置对象编译通过恢复仍未验收)。

```sh
# LGG8_KERNEL_SOURCE 指向上述匹配的源码；输出目录必须新建。
mkdir out/early-watchdog-example
sh "$LGG8_KERNEL_SOURCE/scripts/kconfig/merge_config.sh" -m \
  -O out/early-watchdog-example \
  devices/lg-alphalm/configs/linux-7.3-tiny.config \
  devices/lg-alphalm/configs/early-watchdog.config
make -C "$LGG8_KERNEL_SOURCE" O="$PWD/out/early-watchdog-example" \
  ARCH=arm64 LLVM=1 olddefconfig
make -C "$LGG8_KERNEL_SOURCE" O="$PWD/out/early-watchdog-example" \
  ARCH=arm64 LLVM=1 -j4 drivers/watchdog/qcom-wdt.o
```

## 启动软件

[本机验证套件](../../tools/lg-g8/local-sim/README.md)会编译并核对这份源码，
将其与同一份内核、可交互的诊断 initramfs 和标准 UEFI Shell 组成 `out/local-sim/phone-probe-esp/`。
该目录是未安装到设备的诊断 ESP 文件夹，不是分区镜像或完整发行版。
真实板级 UEFI、Secure Boot/BootOrder、PHY、供电与外设仍有待验证。

本轮 OpenAI 云端的实际编译、QEMU USB shell 验证、产物哈希与交付状态见
[CLOUD-VALIDATION.md](CLOUD-VALIDATION.md)。
