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
板级检查还需要干净的运行时 FDT，默认读取 `out/transport-clean/runtime-fdt.dtb`；
历史导出受损的情况先按 [二进制导出纠错](../../../docs/lg-g8-omarchy/EXPORT-REPAIR.md) 修复。
也可用 `--runtime-fdt` 指定已经验证过的输入。

需要 Python 3.10+、QEMU AArch64、AArch64 EDK2 固件、Clang/LLD、LLVM objcopy/objdump、
make、uv、dtc/libfdt、patch、avbtool，以及该内核已完成的构建目录。
标准 Shell 测试读取板级固件已经构建的 `Build/SurfaceDuo1Pkg/RELEASE_CLANGPDB/AARCH64/Shell.efi`，
可用 `--shell` 改路径。BootShim 的 Unicorn 2.1.4 依赖由 uv 放在隔离缓存中。
本次使用 QEMU 11.1.2、现有 Linux **7.3.0-sm8150**；不会重编完整内核。
USB 测试只编译两个外部模块，并核对原有 Image 和 `.config` 的哈希未变化。

## 找到并修复的问题

输入 `lg-g8-direct-efi-stub-probe.img` 的初始文件系统中有 `/init` 和动态链接的 BusyBox，
却缺少 BusyBox 指定的 `/lib/ld-musl-aarch64.so.1` 和 musl libc。
原包在模拟器中报 `Failed to execute /init (error -2)`，随后 `No working init found`。
内核已经进入了 Linux；不能把这种故障当作内核没有执行。

基线修复包加入匹配的 ARM64 musl 库和早期控制台设备节点，保留原来的 `/init` 和 BusyBox。
基线测试包另外加入检查脚本；检查脚本最终仍让原 `/init` 成为 PID 1。
虚拟 USB 变体再加入 `dummy_hcd` 和 `cdc-acm`，让 Linux 真实的主机、设备和 ACM 驱动完成双向字节传输。
这两个模块和检查脚本不会进入导出的干净运行包。

原来的 `/init` 只建立 USB 接口，然后一直 sleep，无法提供终端。
最终导出的运行包改用 [native-init](native-init/README.md)：为 USB 和控制台提供无密码本地诊断 shell，
退出后重建会话，不启动网络服务。测试会真正关闭、重开虚拟串口，再检查第二个 shell 能读到第一会话写入的文件。
命令的计算结果不出现在发送文本中，避免把终端回显当作执行成功。

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
| 固件自带标准 Shell | 直接使用板级构建的同一份 Shell.efi，通过同目录 startup.nsh 启动内核；检查原 `/init`、UEFI 和虚拟 USB 双向收发 |
| 外部 DTB 交接 | 标准 Shell 启动同一内核，EFI stub 读取匹配 QEMU 的外部 DTB；运行中内核的 model 必须为该文件独有标记 |
| 可交互诊断 shell | 标准 Shell、外部 QEMU DTB、新 `/init` 和 getty 一起运行；通过 USB 执行 UID/架构/算术/文件命令，退出、关闭重开串口后再次执行并确认会话 PID 改变 |
| LG 板级设备树 | 应用真实板级补丁并编译，要求全部 runtime 固定保留内存被覆盖、无候选重叠、三段 RAM/板号/PSCI/USB 和 cmd-db 匹配 |
| 导出修复回归 | 5 项本机测试，覆盖原生 CR/LF 的保留、拒绝部分转换和坏 FDT、重复运行及拒绝覆盖不同输出 |
| BootShim | Unicorn 执行两个实际 UEFI 镜像里的 112 字节跳板，20 次检查 3 MiB 搬运、跳转和寄存器；12 项坏输入由主机预检拒绝 |
| UEFI fastboot | 原 C 控制流加 EFI/USB 边界替身，31 项预期结果；包括正常结果和故障复现 |
| 标准 ESP profile | 真实 EDK2 parser/Clang 验证三个实验宏关闭、默认诊断 profile 不变；另从实际压缩 FD 解出 FFS 核对标准 BDS 的打包情况 |

BootShim 检查停在 UEFI 的第一条指令前。QEMU 使用通用 `virt` 主板和 QEMU 的 EDK2，
不是 LG 的 ABL、设备树或那份板级 UEFI。标准 Shell 二进制相同，固件本身不同。
几组检查分别验证各段软件，不能当作整条 LG 启动链已经通过。
QEMU 没有 LG G8/SM8150 主板模型，不能验证它的内存保留区、显示、触摸、UFS、电源和物理 USB。
因此，结果证明本机的软件启动和修复有效，不能据此宣称 LG G8 或 Omarchy 已适配完成。

## 现存内核文件指纹

`../snapshot-kernel-evidence.py` 只读显式指定的本地文件，不构建、不执行输入，也不连接设备。用于记录采集时的 Image、配置、DTB，以及通过 `--file LABEL=PATH` 追加的选定源码或生成元数据。可选 `--candidate-manifest` 比较既有 handoff manifest 的 `kernel_sha256` 与 `files["linux.dtb"]`；不匹配记为 `false`，命令仍成功，不改写原声明。

从仓库根目录运行（将输入路径换成需要采集的实际文件）：

```sh
mkdir -p private/kernel-evidence
python3 -I tools/lg-g8/snapshot-kernel-evidence.py \
  --kernel /path/to/Image --config /path/to/.config --dtb /path/to/linux.dtb \
  --file selected-source=/path/to/qcom-wdt.c \
  --candidate-manifest out/abl-handoff/lg-native-3/manifest.json \
  --out private/kernel-evidence/new-snapshot.json
python3 -I tools/lg-g8/local-sim/tests/test_kernel_evidence.py
```

输出父目录必须存在。报告含绝对路径，保存到 Git 忽略的仓库根 `private/`；工具以 `0600` 权限新建文件，拒绝覆盖现有文件或符号链接，拒绝 FIFO 等非普通输入。六项本地测试覆盖身份比对、差异记录、原文件保护、重复标签、异常输入和输出链接保护。

SHA-256 相等只支持文件字节身份一致。目录、时间戳、版本标签及当前配置不能追认历史构建输入；manifest 声明未经身份认证。本工具不覆盖完整源码或 include 依赖，不证明上游提交、可复现构建、文件格式或真机启动。前后检查可以发现普通文件变化，不构成原子文件系统快照。已采集结果与历史候选的关系见 [看门狗证据](../../../docs/lg-g8-omarchy/WATCHDOG-RECOVERY.md#现存文件快照不追认历史构建来源)。

## 输出

默认输出在仓库的 `out/local-sim/`：

- `suite.json`：本轮命令、返回值、数量和总判定；失败会保留失败状态。
- `linux/result.json` 和 `*.log`：QEMU 启动串口记录及输入哈希。
- `linux/esp/`：测试使用的虚拟 ESP；包含检查脚本，属于模拟测试输入。
- `software/kernel.efi`、`software/initramfs.cpio`、`software/board.dtb`、`software/manifest.json`：同一内核、修复后的干净运行包和校准后的 LG 板树。
- `phone-probe-esp/`：上述文件加标准 Shell 和手机参数的 startup.nsh；这是待设备验收的诊断 ESP 文件夹。
- `dtb/report.json`：手机板树的独立静态检查结果；没有把它放到不匹配的 QEMU virt 主板上运行。
- `external-dtb/report.json`：外部 DTB 加载机制；其中生成的是 QEMU DTB，不能装到手机。
- `native-init/report.json`、`native-init/qemu.log`：真正的 USB shell 命令、会话 PID 和重连结果。
- `uefi-profile/report.json`、`uefi-profile/bds-audit.json`：构建分支和实际 FD 打包证据。
- `bootshim/report.json`：逐个镜像、DTB 和搬运路径的结果。
- `fastboot/results.json`：31 个用例的结果，旁边保留各项日志和源码 manifest。

`software/` 和 `phone-probe-esp/` 没有模拟脚本或虚拟 USB 模块，也没有完整发行版根文件系统。
它们不是可直接刷入手机的分区镜像。当前板级固件构建配置还有强制 fastboot 的实验开关，
且内置 DTB 是占位文件；必须使用校准后的外部板树，并满足 EFI stub 对 `dtb=` 的加载条件。
Android header 在这里仅用于提取内核和 initramfs，
未在 QEMU 中执行 LG ABL 对 header 和加载地址的处理。

独立生成标准 Shell ESP 可用 `python3 tools/lg-g8/uefi_esp.py --help`。
它使用现成的 Shell，不增加自定义启动协议。`startup.nsh` 放在 Shell 同目录，
通过 Shell 提供的 `%homefilesystem%` 找内核，避免猜测 FS0/FS1。
Linux 的 `initrd=` 和 `dtb=` 使用 ESP 根目录起始的 EFI 路径。
外部 DTB 会替代固件提供的树，且受 Secure Boot 状态限制；这些机制见
[EFI stub 文档](https://docs.kernel.org/admin-guide/efi-stub.html)。

恢复既有 BDS/Shell/ESP 路由的补丁和本机分支验证见 [uefi-profile](uefi-profile/README.md)。
现成的 `SurfaceDuo1NoSb.dsc` 可配合该 profile，但已有 PK、BootOrder/BootNext 和真实硬件状态仍需区分；
本工具没有读写它们，也没有全量重编或启动 LG 板级固件。

各部分也能单独运行，参见 [BootShim](bootshim/README.md)、[fastboot](fastboot/README.md)
和 `run_linux.py --help`。单次虚拟启动通常几秒到十几秒；整套耗时受模块准备、缓存和本机负载影响。
