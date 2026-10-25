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

## 启动软件

[本机验证套件](../../tools/lg-g8/local-sim/README.md)会编译并核对这份源码，
将其与同一份内核、可交互的诊断 initramfs 和标准 UEFI Shell 组成 `out/local-sim/phone-probe-esp/`。
该目录是未安装到设备的诊断 ESP 文件夹，不是分区镜像或完整发行版。
真实板级 UEFI、Secure Boot/BootOrder、PHY、供电与外设仍有待验证。
