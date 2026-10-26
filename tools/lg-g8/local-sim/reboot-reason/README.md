# LG G8 Android 软件 fastboot 入口修复

这组工具只构建和离线验证本机已审计的 Android boot 镜像，不调用设备命令。它解决该 LG ABL 下 `reboot bootloader` 回到 Android 的问题。主线 Linux 是否执行，需要另行取得真机证据。

## 原因与修改范围

实际 ABL 的 LinuxLoader 含 `SM_FASTBOOT_DL disabled on user mode` 分支；LAF 下载路径接受 PMIC reason `0x27`。被测手机的 LAF A/B 原本就是全零，该下载路径回退到 bootloader fastboot。这个既有分区状态是修复成立的前提，工具不会修改 LAF。

`build.py` 严格限定输入 boot 和解压后的 Image 哈希，再检查两个原始 ARM64 指令：

| Image 偏移 | 原指令 | 新指令 | 含义 |
| --- | --- | --- | --- |
| `0xc1de94` | `mov w0, #2` | `mov w0, #0x27` | PMIC reason |
| `0xc1deb8` | `mov w8, #0x5500` | `mov w8, #0x5568` | 与原高半字组成 IMEM reason `0x77665568` |

真实内核使用 `strncmp(cmd, "bootloader", 10)`，因此 `bootloader` 前缀变体也进入该分支。其余重启请求的控制流不变。

重包保留 ramdisk 和 DTB 字节、分区尺寸与 AVB footer，重新压缩 kernel、对齐组件、计算 Android image ID，添加 `lgg8_reboot_fix=pon27-imem68-v1` 参数，并更新原有内联 boot hash descriptor。工具拒绝带认证块或算法非 NONE 的 vbmeta。`avbtool verify_image` 验证的是该 NONE 元数据和内容摘要，不是 OEM 签名认证。构建不涉及全局 vbmeta 分区。

## 精确输入与产物

| 内容 | SHA-256 |
| --- | --- |
| 原始 96 MiB boot_b | `951ad1eb3dd360e17a47e0fcdb8575a2df8119690325c1e72e203fd02a4a7bf2` |
| 原始解压 Image | `233d4a248c28fd55fce3926652fbc4155b8a6883fdf60dfc14906e2ba88e1a4e` |
| 修复 Image | `5f1062029b61e802d3d46e6a8e6ca6c850eaf0d575bb0727048caaea6a15f44f` |
| 已测试并安装的 boot_b | `fec418e5227a31927af27daf71d2225246f5adcf8dda38b5856200760b5205cd` |

它只适用于上述内容，不是通用 LG/SM8150 补丁。更换 ROM 或内核时必须重新审计，不能放宽哈希检查直接套用。gzip 压缩实现也可能影响完整镜像字节；每次构建必须记录新 manifest，不能只凭代码相同认定镜像相同。

## 本机复现

依赖 Python 3.11+、PATH 中的 `avbtool`，以及 `uv`。验证脚本的内联依赖固定为 Unicorn 2.1.4。输入使用已经与设备端 SHA-256 核对的原始备份；输出目录必须尚不存在。

```sh
python3 tools/lg-g8/local-sim/reboot-reason/build.py \
  /path/to/boot_b.before-reboot-fix.raw.img \
  --out out/reboot-fix/rebuild

uv run --script tools/lg-g8/local-sim/reboot-reason/verify.py \
  out/reboot-fix/rebuild/Image.original \
  --out out/reboot-fix/rebuild/cpu-verification.json
```

构建输出 `Image.original`、`Image.patched`、`boot.img`、`avb-verify.txt` 和 `manifest.json`。manifest 中 `device_booted=false`、`partition_writes=false` 只描述构建操作，不能用来推断后来的设备测试历史。

CPU 验证执行实际内核比较、分支和 IMEM store 指令；PMIC API 和 IO instrumentation 使用边界替身，IMEM 写模拟 RAM。8 项对照涵盖 bootloader、bootloader 前缀变体、recovery、rtc、空字符串、NULL、FOTA 和未知请求。它不模拟 PMIC、ABL 或整机重启。

## 真机证据与待验收项

源码起点 `547e2ed` 上新增本目录工具。2026-10-26 的私有日志位于主工作区的 `audit/reboot-route-20261026/`：

- `laf-kernel-ramtest-184155/`：对上述修复镜像执行 RAM 启动；Android 完成启动，marker 存在，原 boot_b 分区未变。随后标准软件 bootloader 重启在约 8.5 秒内进入 USB `18d1:4ee0`，协议返回 product=msmnile、unlocked=yes、current-slot=b。
- `persistent-fix-184517/`：将同一候选写入 boot_b，普通重启回 Android；boot_completed、root、slot、marker 和分区哈希均通过。没有写 ABL、LAF、DTBO、vbmeta 或 userdata。

接手后的只读复核再次确认安装哈希和 Android 状态。`out/reboot-fix/acceptance-rebuild-20261026/` 从原始备份重建，与已安装候选逐字节一致；`out/reboot-fix/acceptance-cpu-20261026.json` 的 8 项对照通过。

**落盘后的再次软件进入 fastboot 已通过。** 初次执行被自动审批拒绝；用户再次明确指令后，`adb reboot bootloader` 约 9.79 秒进入 fastboot，product=msmnile、unlocked=yes、current-slot=b 全部通过。随后 `fastboot reboot` 正常返回 Android，boot_completed=1、root、slot=_b、marker 与 boot_b 哈希均通过。私有结果在 `audit/persistent-reboot-acceptance-20261026/result.json`。本次没有刷写、擦除或换槽，不要重复刷入同一修复。

恢复材料在本机统一 Backups 目录，原始备份及其哈希必须保留。受 LF→CRLF 影响的历史导出不能直接刷回，参见 [导出纠错](../../../../docs/lg-g8-omarchy/EXPORT-REPAIR.md)。本修复的维护限制和退出条件记在 [统一技术债表](../../../../docs/lg-g8-omarchy/TECHNICAL-DEBT.md)。
