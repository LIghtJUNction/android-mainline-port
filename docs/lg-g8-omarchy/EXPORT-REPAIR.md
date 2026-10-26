# 二进制导出损坏的纠错记录

2026-10-26 通过本机检查确认：早期备份中的每个 LF 字节都变成了 CRLF。
严格撤销转换 `raw.replace(b'\r\n', b'\n')` 后，重新执行 LF→CRLF 能逐字节恢复原文件。
这与终端的换行转换一致；当前没有恢复原始导出命令，不能确定是哪次调用启用了终端转换。

修复不仅依赖换行统计。独立校验结果如下：

| 输入 | 修复后验证 |
| --- | --- |
| boot A/B | 恢复为 96 MiB；标准 v2 header；内核和 ramdisk 的 gzip CRC/长度通过，ramdisk cpio 能完整解析；两个连接的裸 FDT 通过 libfdt 和 dtc |
| DTBO A/B | 恢复为 24 MiB；标准 DT table 的 14 项都通过 libfdt 和 dtc；官方 avbtool 的 SHA256 descriptor 校验通过；两槽内容相同 |
| runtime FDT | 860267 bytes，等于 totalsize；完整 libfdt/dtc 通过 |
| vbmeta A/B | 恢复为 65536 bytes；官方 avbtool 能正常解析 |

boot 的 AVB footer 位置和 vbmeta 边界恢复正常，但它的 hash descriptor 与当前内容不匹配。
源系统是第三方 Android；具体是谁、在什么时候改动了 hash 覆盖内容尚未确认。
这是与导出转换分开的事实，不能用格式恢复代替 AVB 内容校验。
这些图像报告的算法为 NONE，也不能称为有效的 OEM 签名认证。

## 撤回的旧判断

以下现象都由受损导出产生，不能再用作 LG 特殊格式的证据：

- boot A 槽 v2 header 尾部偏移一个字节。
- DTB 前存在高熵 OEM wrapper，DTB 窗口被 header 截断。
- 两个 FDT 的 nameoff/token 需要混合字节序解码。
- stock gzip 无法标准解压，因此包含 OEM 压缩或加密。
- runtime modem 保留区为 `0x0d0a0000`；正确大小是 `0x0a000000`，与原候选相同。

历史 Pro 审查和本地解析器使用了这些受损输入，其相关推论随之失效。
精确字节 round-trip 当时只证明保留了坏输入，并没有证明输入正确。

## 复现

在仓库根目录运行，输入使用原始导出，输出使用单独目录：

```sh
python3 tools/lg-g8/repair-tty-export.py \
  ~/Backups/lg-g8-alphalm-2026-10-25/boot_a.img \
  ~/Backups/lg-g8-alphalm-2026-10-25/boot_b.img \
  ~/Backups/lg-g8-alphalm-2026-10-25/dtbo_a.img \
  ~/Backups/lg-g8-alphalm-2026-10-25/dtbo_b.img \
  ~/Backups/lg-g8-alphalm-2026-10-25/runtime-fdt.dtb \
  ~/Backups/lg-g8-alphalm-2026-10-25/vbmeta_a.img \
  ~/Backups/lg-g8-alphalm-2026-10-25/vbmeta_b.img \
  --out out/transport-clean
```

工具只读取本机文件，不使用设备。需要 Python 3、libfdt/dtc 和 avbtool。
它拒绝不符合完整 LF→CRLF 变换的输入，不覆盖原文件；重复运行只复用字节相同的修复输出。
报告、dtc 警告和官方 AVB 输出保留在输出目录。原始备份和修复后的图像都不提交到 Git。
以后导出二进制必须先验证传输方式，不能仅凭 `exec-out` 的名字判断。
2026-10-26 在当前手机实测：普通 `adb exec-out` 保留原始字节，但
`adb exec-out "su -c '...'"` 的 root 会话带有 `onlcr`，仍会把 LF 改成 CRLF。
`adb shell -T "su -c '...'"` 通过了混合 LF/CRLF 探针；用它重新导出的
96 MiB `boot_b` 与手机端直接计算的 SHA-256 一致。
所有命令都应指定目标设备序列号。实际使用前先传输包含独立 LF 和原生 CRLF
的已知字节串，再核对导出文件的设备端哈希、格式、压缩校验和分区尺寸。

## 与 Linux 适配的关系

正确 runtime FDT 仍显示若干真实的板级保留区差异：XBL/AOP、CDSP、QSEE 和 CDSP secure。
主线候选也漏保留了固件 splash 区，并启用了 runtime 已关闭的第二个 USB 控制器。
[板级修复与验证](../../tools/lg-g8/local-sim/dtb/README.md)以干净 runtime 为输入，
按区间并集验证保留内存覆盖，避免把 runtime 中重叠的聚合描述直接复制成重叠节点。

这纠正了本机适配输入，不代表已经验证 LG 硬件。之后的手机 RAM 实验仍未取得可归属到主线 Linux 的执行证据；Android 的软件 fastboot 修复另有成功记录。当前验收范围和失败结果见 [STATUS.md](STATUS.md)。
