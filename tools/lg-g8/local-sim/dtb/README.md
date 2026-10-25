# Alpha DTB 与真实固件保留区的本机核对

`align-alphalm-runtime.patch` 针对现有 Linux 7.3 源码中的
`arch/arm64/boot/dts/qcom/sm8150-lg-alphalm-board.dts`。
它修复已从干净运行时 FDT 确认的地址/大小差异，并关闭该手机运行时未启用的第二个 USB 控制器。
它不改变外部内核源码，不操作手机，也没有用通用 QEMU 设备树冒充手机设备树。

```sh
python3 tools/lg-g8/local-sim/dtb/verify.py \
  --kernel-source /path/to/kernel/linux \
  --runtime /path/to/validated-clean-runtime-fdt.dtb \
  --baseline-dtb /path/to/original/probe.dtb
```

需要 Python 3、Clang、dtc、libfdt 和 patch。工具只在自身被 Git 忽略的 `out/` 工作副本应用补丁，
分别预处理、编译修改前和修改后的 DTS，再用 libfdt 完整验证。
`--baseline-dtb` 可省略；提供时还要求重新编译的修改前 DTB 与既有文件逐字节一致。
`--runtime` 必须已通过完整结构验证；工具不会擅自修复输入文件。

| 区域 | 修改前 | 修改后，对齐干净运行时 FDT |
| --- | --- | --- |
| XBL/AOP 聚合区 | XBL `0x85d00000 + 0x140000`，另有两个 AOP/cmd-db 区 | XBL `0x85e00000 + 0x100000`，保留 AOP `0x85f00000 + 0x20000` 和 cmd-db `0x85f20000 + 0x20000` |
| CDSP | `0x99b00000 + 0x1400000` | `0x99b00000 + 0x3700000` |
| QSEECom | `0x9e400000 + 0x1400000` | `0x9e400000 + 0x1c00000` |
| CDSP secure | `0xa4c00000 + 0x3c00000` | `0xa4c00000 + 0x4000000` |
| 固件 splash/display dump | 未保留 | 一个去重的 `no-map` 区：`0x9d200000 + 0x1200000` |
| USB2 控制器 | `okay`，其 DWC3 子节点指定 host | 父控制器 `disabled`；USB1 保持 `okay` 和 peripheral |

运行时 XBL/AOP 节点将 `0x85e00000..0x85f40000` 整体保留。
主线的 AOP 和 cmd-db 分别使用这个区间的尾部两段，因此补丁通过三个不重叠区间覆盖同一并集，
保留 cmd-db 的 compatible、地址和 phandle，避免直接复制聚合区后与已有子区间重叠。

运行时 splash 和 display-dump 两个节点指向完全相同的区间。补丁只建立一个保留区，并加上 `no-map`，
避免早期内核把固件仍可能用于显示 DMA 的内存当作普通内存映射和分配。
这不表示显示驱动已经适配，也不证明固件停止了扫描输出。

工具比较全部固定 `reg` 保留区的区间覆盖，而不是要求节点同名。
动态申请大小、没有固定 `reg` 的节点，以及 `reusable`/CMA 区域单独列出。
它报告运行时自己已有的重叠，并要求修改后的候选区间没有重叠。
运行时三个 RAM 区间、唯一的 memory 节点、PSCI SMC 和 USB1 的设备模式也必须通过核对。

本次实际结果：

- 干净 runtime FDT SHA-256：`75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf`。
- 修改前 DTB SHA-256：`9813678623aad354f61da6b54016b9a1b7842c3800ca07cba7961363f3bc42ea`；重新编译与现有 probe 完全相同。
- 修改后 DTB SHA-256：`eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9`。
- 固定区间缺口由 6 条变为 0 条；原来的 6 条中显示范围被 runtime 的两个节点重复描述。
- 新 DTB 无保留区重叠；外部源码 SHA 不变；三段 RAM、PSCI、USB 和 cmd-db 检查全部通过。

此前直接解析备份或有 `cpio: skipped junk` 的文件树，会误读出 modem 大小 `0x0d0a0000`。
撤销导出时的 LF→CRLF 转换后，真实值为 `0x0a000000`，与原主线 DTB 相同，因此补丁不修改 modem。

当前 `out/kernel-uefi-tiny/.config` 中，DWC3/QCOM gadget、configfs ACM、QUSB2、QMP USB、
SM8150 GCC 和 interconnect、SCM、SMEM、cmd-db、RPMh/RPMh power-domain 和 regulator 都内建为 `y`。
`CONFIG_DRM` 关闭，DWC3 HOST/DUAL_ROLE 关闭，`CONFIG_PANIC_TIMEOUT=0`，pstore RAM 内建。
这证明所检查的配置支持最小 USB 软件路径；它不能代替实际 PHY、供电、中断或显示验证。

详细地址、来源哈希、各项判定、动态区域和重叠记录在 `out/report.json`；
dtc 原始诊断在 `out/*.dtc.stderr`。这些是静态内存合同和结构证据，不能宣称 LG G8 已经启动。
