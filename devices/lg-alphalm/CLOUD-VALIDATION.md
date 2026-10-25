# OpenAI Codex Cloud 构建与模拟验证

2026-10-26，OpenAI 托管的云端环境完成了一轮实际编译和 QEMU 验证。
项目工具采用 `d58e37a15855e2ea9f8e6b28f1bbd47b7ee67cd1`，
内核采用官方 Linux 7.3 源码，编译器为 Clang/LLD 19。
配置来自本目录 `configs/linux-7.3-tiny.config`，经过 `olddefconfig`；
编译器引起的配置差异单独保存为 `artifacts/kernel-config.diff`。

本文件记录云端命令结果、云端复算的哈希与网页中可读取的报告。
完整二进制包和原始日志保存在私有云端快照；网页附件导出失败，**尚未下载到本机独立校验**。
这里不能代替原始日志，也不能作为 LG G8 已启动的证明。

## 本轮实际产物

| 文件 | 云端复算的 SHA-256 |
| --- | --- |
| `Image`，Linux 7.3.0-sm8150 | `d5533f72863a18f2ab7db39eea8f702987c63c2d538416439eaf1ee87c481008` |
| `lg-alphalm.dtb` | `eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9` |
| `SM8150_EFI-standard-esp-NoSb.fd` | `6ece4bb4d98eeaf1617af3684657b2583d878e637599c625a5a4d815336933db` |
| 同次固件构建的 `Shell.efi` | `447339945cac38fdbd8cd8efb1800bbb910a6f159d58cc20fb5c6044d33bfb55` |
| `initramfs-native.cpio` | `2a4db903a20d7cd104518877b3f9679799129c475973b90027fec4f41026947a` |
| `alpine-clean.cpio` | `5cdbb3f8ec61c79602cd2f9e25b136292b93dd94309822f5598ec0ca12f96866` |

还编译了与该 Image 匹配的 `dummy_hcd.ko`、`cdc-acm.ko`，仅供 QEMU 使用。
模块构建前后的 Image 和 `.config` 哈希一致。
Alpine 基础文件来自官方 3.22.7 ARM64 minirootfs；其 BusyBox 与匹配的 musl 一起保留。
本轮干净输入没有旧 `/init`，验证报告的 `source_init_sha256` 为 `null`，实际覆盖了 `d58e37a` 的修复。

LG DTB 与此前记录的候选哈希一致。DTC 告警保留在构建日志中，没有把告警隐藏后称为全部通过。
固件使用 `SurfaceDuo1NoSb.dsc`、`RELEASE_CLANGPDB`、`lg-alpha` 和标准 ESP profile；
实际生成了 3 MiB FD 与 Shell。固件仍使用上游已有的部分二进制组件，并非全部组件均从源码生成。

整体归档为 `/workspace/lg-g8-cloud-results.tar.gz`，云端确认大小 **25,819,358 字节**，SHA-256：

```text
ddf2f207e1fb73e2457964ec0e35fdac71ced391cee047ce139a450db245982a
```

归档包含产物、脚本、输入来源与哈希、配置差异和完整日志；目前它属于云端留存的产物，尚不是已交付的本机文件。

## 实际运行的路径

```text
QEMU virt/TCG + 通用 QEMU EDK2 固件
  → 本轮 LG 固件构建生成的同一份标准 Shell.efi
  → 本轮 Linux Image 的 EFI stub
  → 匹配 QEMU virt 的外部 DTB
  → 原生 /init
  → dummy_hcd/cdc-acm 虚拟 USB
  → getty 和实际交互式 shell
```

原始报告记录两次会话：

```text
NATIVE_RESULT:0:aarch64:9193:79
USB_CLIENT_REOPENED
NATIVE_RECONNECT:5941:9193:117
NATIVE_SHELL_PASS
```

第一条返回 root UID、ARM64 架构和实际算术结果，并写入 `/run` 文件。
退出后，客户端关闭并重开串口，新会话 PID 从 79 变成 117，并读取上一会话写入的数据。
期望的计算结果没有直接写在发送的命令原文中。

`native-init/verify.py` 的七项检查全部为真：实际 USB shell 执行与重连、会话 PID 不同、
结果不来自发送文本回显、BusyBox 未改变、干净 cpio 无测试载荷、标准 Shell/UEFI 路径成立、
外部 QEMU DTB 确实被选中。
干净运行 cpio 和手机候选 ESP 不包含虚拟 USB 模块或测试脚本；QEMU DTB 与 LG DTB 分开保存。

## 原始证据位置

以下路径位于云端 `/workspace/lg-g8-cloud/`：

- `artifacts/SOURCES.json`：公开输入、来源与校验信息。
- `artifacts/reports/native-usb-shell.json`：七项检查、输入输出哈希、命令与会话 PID。
- `logs/qemu-native-usb-shell.log`：完整启动和 USB 会话记录。
- `logs/kernel-resume.log`、`logs/kernel-finish.log`：内核成功续编和 DTB 构建记录。
- `logs/firmware-build.log`：实际 UEFI 编译记录。
- `artifacts/firmware-bds-audit.json`：新 FD 解包检查；包含标准 BdsDxe/LinuxLoader，未包含 QcomBds/config FFS。

首次工具准备和编译失败的日志也有保留。
已解决 Bison 数据路径、编译时重复解包工具造成的 SIGBUS、Mono/NuGet 的路径和系统 CA 信任问题。
后续复现须先完成依赖安装，再固定工具目录，避免编译过程中覆盖编译器。
本轮没有验证 GPG 签名，也没有完成第二次逐字节一致的全量重建。
构建入口与要求见 [CODEX-CLOUD.md](CODEX-CLOUD.md)。

## 尚未验证的部分

**QEMU 没有运行 LG 板级 FD，也没有模拟 SM8150。**
LG FD 只完成了编译和打包检查；实际 QEMU 系统固件是通用 EDK2。
LG ABL 到 UEFI 的可达性、物理 USB、显示、触控、UFS、供电和运行时 Secure Boot 状态仍未验证。
当前 tiny 配置关闭 DRM，不能用于证明显示驱动正常。
没有手机操作，没有 adb/fastboot、刷写、格式化或 USB 透传，也没有完成 Omarchy 验收。
