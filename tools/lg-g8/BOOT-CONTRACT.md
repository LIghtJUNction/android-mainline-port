# 离线 boot 内存契约审计

`audit-boot-contract.py` 将移植研究中的“完整固件保留区并集”检查扩展到
kernel、initramfs 和 DTB 的装载范围。它只读取明确传入的本机文件，不操作手机、
不构建镜像。本次 boot 输入是已安装的 Android 修复 boot，不是新主线候选镜像；
`--candidate-dtb` 指单独比较的 DTB，不表示它已装入该 boot。
依赖 Python 3 和系统 libfdt；测试另需 dtc。

```sh
python -I tools/lg-g8/local-sim/tests/test_boot_contract.py -v

# 首次运行使用新目录；再次审计换目录或报告文件名。
mkdir out/boot-contract-example
python -I tools/lg-g8/audit-boot-contract.py \
  --boot /path/to/installed-android-repair-boot.img \
  --candidate-dtb /path/to/one-exact-candidate.dtb \
  --reference-fdt out/transport-clean/runtime-fdt.dtb \
  --expect-reference-sha256 75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf \
  --report out/boot-contract-example/report.json
```

`--reference-fdt` 可重复，所有参考树分别检查；若使用预期哈希参数，须按参考树顺序
各提供一个。固定来源哈希验证输入身份，不能认证设备采集过程。
`--kernel-load`、`--ramdisk-load`、`--dtb-load` 可显式提供假设物理地址，支持 `0x`。
未提供时使用 Android header 字段，并在报告中注明仅为假设。
**header 地址不等于已观察到的 ABL 放置地址**；例如 firmware 可能用这些字段判断格式、
改写地址、选择另一棵 DTB 或使用其他解压目的地。

## 检查范围

- 仅支持本项目 Android boot v2、1660 字节 header、2048/4096/8192/16384 页大小，
  无 second-stage 和 recovery_dtbo。拒绝其他版本、非零遗留地址、截断 section 或页尾。
  partition 尾部/AVB 元数据作为不解释的字节计数记录，不检查签名。
- 对 boot DTB section 中每棵连续 FDT 和全部明确传入的树调用现有
  `local-sim/dtb/verify.py` 的 `libfdt fdt_check_full`。要求 v17、精确 totalsize，
  不靠 magic 搜索挑选疑似树。候选必须是单棵 DTB；与 boot 中逐字节匹配的索引列出，
  多树选择和外部候选接入仍未知。libfdt 通过后额外拒绝重复节点路径和重复属性名，
  防止字典 parser 静默覆盖前一个内存定义；libfdt 全量验证本身不检查这些语义。
- 支持根地址/大小各 1 或 2 cells、根下 memory 节点、空 `ranges` 的直接
  reserved-memory 子节点，以及 FDT memreserve。拒绝嵌套或地址翻译歧义。
  reserved-memory 必须显式声明与根一致的两个 cell 属性，不能用继承值替代。
  静态子节点只接受一个 `reg` 元组：内核版本对多元组的处理可能不同，不能把后续元组
  当作有保证的固件保护；多个子节点的覆盖并集仍正常支持。
  所有启用的固定保留区都参与早期装载碰撞检查，包括 reusable/CMA；disabled 子节点跳过。
  disabled 的 reserved-memory 父节点直接拒绝，避免父 status 与早期扫描实现的差异漏掉保留区。
  `no-map` 和 `reusable` 须为空布尔属性且不能同时出现。
  动态 `size` 和无 `reg` 的保留节点列为未知。
- 出现 `linux,usable-memory` 或 `linux,usable-memory-range` 时拒绝输入，尚未建模这些
  对有效 RAM 的限制，不能按更宽的 `reg` 范围判定装载有效。
- 按半开区间 `[start,end)` 求覆盖，允许多个相邻候选区覆盖一个参考区。
  同时检查全部固定区并集和永久保留区并集；reusable 不能冒充永久固件保留区。
  `no-map`、访问权限和固件所有权的语义一致性不在此覆盖判定之内。
- 对每棵候选/参考树检查载荷是否在 RAM 内、是否与保留区交叠，再检查载荷两两交叠。
  保留区自身交叠单独报告，重复描述不能自动认定为错误。
  候选扩出参考树明确 RAM 的范围会报问题；缺少参考 RAM 单独列出。
- 原始 arm64 Image 读取 `text_offset`、`image_size`、flags；内核检查范围取文件与
  runtime 大小的并集，避免漏掉 BSS。校验地址减 `text_offset` 的 2 MiB 对齐。
  非标准 flags、大小为零的旧 Image 和靠近 RAM 起点的放置要求列为未知。
  旧 Image 的 `text_offset` 字节序未规定，按协议使用 `0x80000`，不直接按小端解析。
  文件尾可能包含附加数据，整个文件仍按装载占用计算。
  所选 DTB 检查 8 字节地址对齐和 2 MiB 大小上限；已知 Image 运行跨度时，检查
  Image/initrd 是否同处一个起点按 1 GiB 对齐、最大 32 GiB 的窗口。
  DTB 周边 2 MiB 映射属性与平台物理地址宽度仍未核实。
- gzip/xz/lz4/zstd 和未知内核格式不解压，不用压缩文件长度推算解压后的运行范围。
  文件载荷范围可比较，运行范围、staging 和解压 scratch 继续未知。
  initramfs 检查的是传入字节占用，解包后分配未知。
- 外部 DTB 场景取候选树长度与原 boot DTB 表长度的较大者作为载荷范围，
  bootloader 修改后的 FDT 容量增长仍未知。stock 零长度 RAM 占位不会被当作空 RAM：
  覆盖结果为 `null`，等待 bootloader fixup 证据。
- 不读取内核 build config，也不推导最终生效的 cmdline；`mem=` / `memmap=`、
  其他启动参数和 config 对内存的改变没有认证。未进入显式保留项的 framebuffer 或其他
  活跃固件 buffer 不在区间模型内，不能把“固定保留区覆盖”当成完整固件生命周期证明。

每份报告使用独占创建（`open("x")`），拒绝覆盖已有文件，并记录全部输入、工具和复用
parser 的 SHA-256。输入只读取为不可变快照，报告不包含 cmdline、属性原始值或私有日志。
报告包含本机路径和内存节点，仍应按本地诊断材料管理；不直接公开 DTB 或设备原始日志。

退出码：0 表示已知范围检查无冲突，1 表示声明场景有范围冲突，2 表示无效/不支持的输入
或报告创建失败。无冲突时 `status` 仍为 `incomplete`，`bootability` 始终为
`not established`。未证明 ABL 交接、内核执行、驱动、USB 网络或恢复可用。

## 2026-10-27 已安装 Android 修复 boot 与外部 DTB 核对

本次独占证据目录为 `out/boot-contract-20261027-f6fb6105/`，Git 忽略。
boot 输入是已安装的 Android 修复镜像 `out/reboot-fix/acceptance-rebuild-20261026/boot.img`，
不是新主线候选镜像。本次只提取其中两棵 DTB 进行审计，没有重新生成镜像。
另将已有 `out/local-sim/dtb/patched.dtb` 作为**外部假设布局**比较，
不表示它已经进入该 Android 修复 boot 镜像，也不建立主线启动结论。

输入哈希：

| 文件 | SHA-256 |
| --- | --- |
| 已有 boot.img | `fec418e5227a31927af27daf71d2225246f5adcf8dda38b5856200760b5205cd` |
| 已校验 runtime FDT | `75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf` |
| boot 内 DTB 0 | `15a0592d06ed6e3f2165e7e60ec68a07070cf0f7413bb2d9ca4520a4385eedfa` |
| boot 内 DTB 1 | `32d435acac29c40ae6f5d11a28a97a1ca625ca3431cc2e682215165560c45863` |
| 已有 patched.dtb | `eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9` |

确认的静态结果：

- 内核为 gzip，运行大小和解压放置未知。按 header 字段直接解释，kernel/ramdisk 和
  ramdisk/DTB 各有一处交叠，三个载荷均在 runtime RAM 外。这否定的是“直接把 header
  字段当真实物理地址”的解释，不能证明 ABL 实际放置错误。
- 两棵嵌入 DTB 都是完整有效的 FDT，但 `/memory` 为零长度占位；与 runtime 比较各有
  5 段固定保留区覆盖缺口，涉及 SLPI、CDSP、QSEECom、CDSP secure、ramoops。
  bootloader overlays/fixups 可能改变它们，不能据原始树推断最终有效 DTB。
- 已有 patched DTB 覆盖 runtime 全部固定及永久保留区并集，三段 RAM 与 runtime 一致，
  自身固定保留区无交叠。它不在该 boot 的 DTB 表中；套用相同 header 地址假设仍有
  载荷地址问题，不能因此称镜像可启动。
- boot、runtime 和 patched 三份原输入在审计后 SHA-256 均不变。

证据保留了开发过程的初次报告；`*-final-v2-report.json` 是完成占位 RAM 与外部 DTB
范围处理后的最终审计。输入边界、BSS、拆分覆盖、一字节缺口、永久/reusable、memreserve、
动态/disabled、1-cell、RAM 占位、压缩/未知内核、外部 DTB 增长和报告不覆盖等由本机合成测试验证。

独立复审后测试扩展为 31 项，补充了缺失 reserved-memory cells、多 `reg` 元组、
父节点 disabled、布尔属性语法、Linux usable-memory 限制、libfdt 可接受的重复项、
旧 Image 字节序、DTB 对齐/大小和 initrd 窗口反例。三个已有输入场景已用修复后的工具
重新执行，独占证据目录为 `out/boot-contract-review-20261027-f6fb6105/`，
精确 argv、退出码、报告哈希和原输入哈希在该目录的 `verification.json`。
三次仍返回 1，静态布局结论未变；原输入哈希全部保持一致。

复审的内核语义对照来自本机现有源码，只用于发现实现差异，没有核实其上游 Git 身份：
`drivers/of/of_reserved_mem.c` SHA-256 为
`df750fd2023c11c41991c34cc6f1d40bcd53458c19492f15b23fa0e59e125d50`，
`drivers/of/fdt.c` 为
`a1d085707781c1a8356a8ef5bb9c8164e6ad6108dcb59582dfc7f2b348e81ff6`，
`Documentation/arch/arm64/booting.rst` 为
`3ac50b9d3d96602fbea26bd72c81f924c81162f5330d27de6e60807fbd860a86`。
这不认证当前候选内核版本或运行配置，也不作为硬件成功证据。
