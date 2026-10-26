# LG G8 with mainline Linux and Omarchy

当前状态（2026-10-27）：处于首次启动调试阶段。手机上的主线 Linux 与 Omarchy 尚未验收；没有足够证据证明主线内核已经执行。Android reboot 修复已在 `ea69a25` 提交推送；最小交接探针从该提交继续开发，各产物单独记录哈希。

| 项目 | 状态 | 证据与边界 |
| --- | --- | --- |
| Android 软件 fastboot 入口修复 | 真机通过（RAM 与落盘场景） | 临时启动场景约 8.5 秒进入 fastboot；已安装 boot_b 的首次验收约 9.79 秒，2026-10-26 21:12 复验为 9.947 秒。两次协议均返回 product=msmnile、unlocked=yes、current-slot=b，并返回 Android。 |
| 修复镜像写入 boot_b 后的 Android | 真机通过 | 交接记录及本次只读复核均确认 boot_completed=1、root 可用、slot=_b、marker 存在、分区哈希与候选一致。详见 [修复说明](../../tools/lg-g8/local-sim/reboot-reason/README.md)。 |
| 重启修复构建与 CPU 回归 | 离线通过 | 从已核对原始备份重新构建，与已安装候选逐字节一致；官方 avbtool 检查通过；8 项 Unicorn 对照通过。 |
| 通用 QEMU Linux / initramfs / USB | 离线通过 | 已运行 Linux 7.3、原 `/init`、虚拟 USB 双向传输。通用 virt 板和 EDK2 不模拟 SM8150/LG 主板。 |
| stock FDT → mainline FDT 跳板 | 真机失败 | 本机 CPU/QEMU 通过；手机接受 RAM boot 后没有 Linux USB 或可归属的 pstore。用户报告下载画面、左半屏花屏、随后重启。不能原样重复此候选。 |
| 最小 ARM64 ENTER/TARGET 交接探针 | 离线通过 | EP2 halt/PSCI 两种 4096 字节产物，各通过 17 个 CPU 场景及 12 组独立格式检查；已修复未对齐访存、PSCI EL3 非法 SMC 及记录缺少完整性校验。三项修复及补充的 4 字节对齐源地址修复均完成独立复核；halt 的 9 个、PSCI 的 7 个完整路径各通过 103 个有序 store 元素前缀快照检查及 100 次指令边界内存核对。硬件缓存持久化、真实复位保留与恢复仍未验证，尚无真机执行证据。详见 [探针说明](../../tools/lg-g8/local-sim/entry-probe/README.md)。 |
| pmsg 一次性校准 | 真机失败 | 2026-10-27 已执行一次：helper 完整写入 63 字节，正常 reboot 返回 Android，boot_b 哈希匹配；重启后 pstore 为空，payload 未读回。失败层尚未区分保留、识别/导出和用户态清理。详见 [校准结果](PMSG-CALIBRATION.md)。 |
| 看门狗与早期挂死恢复 | 有源码线索 | 已核对 ABL 成功路径主动启用 APSS watchdog，以及后续缓存/MMU、BootStats 和条件 SMC 的静态行为；中断状态测试位有疑点，动态接口行为仍未知。失败候选的 initramfs 无模块，120 秒计时器从用户态计时。最终硬件状态及超时恢复尚未验收，详见 [看门狗证据](WATCHDOG-RECOVERY.md)。 |
| 现存内核文件指纹采集 | 离线通过 | 13 个显式指定文件完成采集，Image / 历史 DTB 匹配旧 manifest；六项工具测试通过。当前配置与源码仍不能追认为历史构建输入，详见 [文件快照记录](WATCHDOG-RECOVERY.md#现存文件快照不追认历史构建来源)。 |
| warm reset 日志保留与读回 | 真机失败 | 本次 Android pmsg 校准写入成功、正常重启返回，但读回失败；空 pstore 不能单独证明 DRAM 内容丢失。console 和裸探针 PSCI 路径仍未验证，详见 [读回定位](PMSG-CALIBRATION.md#只读定位及证据边界)。 |
| 主线显示、触控及 Omarchy | 外部移植调研完成，G8 未验收 | 已核查同 SM8150 项目的启动、USB、显示与桌面记录，见 [移植对照](MAINLINE-PORTING-RESEARCH.md)；其他设备成果不代表 G8 已启动。当前 tiny kernel 关闭 DRM。 |

重要纠错：早期二进制备份经过 LF→CRLF 转换。撤销转换后，boot A/B、DTBO A/B、运行时 FDT、vbmeta A/B 都恢复为标准格式。旧记录中的 OEM wrapper、header 尾部偏移、FDT 端序异常、DTB 截断等结论撤回。原始 boot 的 hash descriptor 与内容不匹配，原因未知；修复构建会更新该镜像原有的 NONE hash descriptor，并单独验证。两者都不能称为 OEM 签名认证。

当前证据、修复方法及历史结论影响见 [EXPORT-REPAIR.md](EXPORT-REPAIR.md)。本机模拟入口见 [local-sim](../../tools/lg-g8/local-sim/README.md)。临时措施统一记在 [TECHNICAL-DEBT.md](TECHNICAL-DEBT.md)。原始备份保持原样，不提交镜像、唯一设备数据和原始日志。

## 当前验收与下一步

已完成从当前 Android 使用已安装修复进入 fastboot、协议 getvar 检查、返回 Android及 root、槽位、marker、boot_b 哈希复核。最初执行被自动审批拦截；用户再次明确指令后执行成功。不要再次刷入同一修复。

2026-10-26 21:12，完成新一轮 Android→fastboot→Android 复验；同次工作会话核对的仓库 HEAD 为 `c4ffefeac91dfef0ae4a680b48dc4ae6b1881372`，不代表重新构建或安装了内核。进入 fastboot 用时 9.947 秒，三项协议检查通过；往返前后均确认 boot_completed=1、root uid=0、槽位 b、内核 `4.14.348-openela-perf+`、marker `lgg8_reboot_fix=pon27-imem68-v1`，boot_b SHA-256 均为 `fec418e5227a31927af27daf71d2225246f5adcf8dda38b5856200760b5205cd`，boot_id 已变化。离开 Android 前电量 99%、33.3°C、USB 供电。shell-v2 与 root 退出码 37 精确传回；六字节二进制传输探测未执行。

2026-10-27 00:33，在集成仓 `e1075ba633c4b502d75a9b28eec178dce1131b8b` 上按用户明确授权执行一次 pmsg 校准。helper 完整追加 63 字节，正常 Android reboot 后 boot_id 改变，boot_completed=1、model=`LM-G820`、root uid=0、slot=`_b`、内核和 boot_b 哈希符合上述身份；before/after pstore 均为空，`passed=false`，脚本退出码 1。最早未满足条件是重启后的日志读回。此前自动审批拒绝只描述历史时点，已不再是当前阻塞。

随后只读核对同一 boot_id 的 Android 状态、带名称的 ramoops 参数、init 规则及系统启动归档元数据；pstore 仍空，未发现本次 pmsg 被搬运或删除的证据，亦未排除这些路径。设备最后实测为此次 reboot 后已完成启动的 Android，本会话设备操作已结束，没有后台设备任务。没有重试写入或 reboot，无分区刷写、擦除、换槽或候选启动；同机其他模拟器不操作。恢复材料保留在本机统一 Backups 目录，恢复须使用已校验原始 boot_b。

已只读采集并校验实际 `services.jar`、init 规则、`logcat`、`logd` 和 `liblog.so` 共七个文件。BootReceiver 已查归档路径没有直接删除源文件；原生普通 pmsg 读取没有发现直接删除，`logcat -L -c` 则有明确 pstore unlink 分支。后续采集核实 logcatd wrapper 不添加 `-c`，服务和 clear action 不能拼成该删除调用；当前属性仍不证明校准启动时的执行及删除结果。init 范围扩展到 179 个文件、171 个 rc，长度/摘要复核通过，但另有七个未采 import、APEX 顶层 rc 及更早服务/脚本缺口，不能排除其他清理。pstore mount 与首次 `start logd` 属于同一 `on init`，mount 在前，但尚不能声称早于全部消费者。

挂载后同步快照 [helper](../../tools/lg-g8/PSTORE-SNAPSHOT.md) 已实现，13 项主机边界测试通过，修复了短写后保存字节计数与摘要错误；NDK r30 静态 AArch64 构建通过。API 37 note 来自预编译启动对象，API 34 目标不保证 Android 14 运行兼容；未在手机执行或部署。完整采集须结合退出码、状态/manifest 和回收校验；默认 5 秒仅为 fork 后监督预算，不能作为整个步骤的严格期限。

下一步补齐未采 init/APEX 规则、更早服务/脚本及触发条件，核对 PATH、早期快照 helper 的 Android 运行兼容性、执行位置/标签、运行权限和暂存生命周期，形成可审查的启动修改与单次实验材料；不重跑同一校准。当前阻塞是日志读回链路尚未建立，现有证据不足以定位内容首次丢失的位置。实验身份、结果及边界见 [校准记录](PMSG-CALIBRATION.md)。console 保留/读回、裸探针 PSCI reset 和抵达 reset 前挂死的恢复仍需分别验证，尚不能启动无人值守的新镜像迭代。尚不生成或发送新的设备启动镜像。已失败候选 `lg-native-3` 的 SHA-256 为 `b183fe857ee8e933272dad01cbd5199fc4bc6b303adc11a37c7b0fe3c23b3b78`，只做过 RAM 启动，不能原样重试。

公开移植调查已形成 [固定来源对照](MAINLINE-PORTING-RESEARCH.md)：Hotdog 的 ABL 直接启动和 USB SMMU 调试、OnePlus 7 Pro 的 Arch/Hyprland 手机交互层，以及上游与 LG downstream 资料。下一步离线比较 LG 的 ABL/DTBO 契约、完整固件保留内存、framebuffer 带宽与 USB SMMU；外部作者的硬件记录不计入 G8 验收，不照搬板级地址或关闭 watchdog。研究不改变上述设备实验前提。

当前 EP2 本地探针报告为 `out/entry-probe/ep2-halt-2/cpu-store-prefix-v2.json` 与 `out/entry-probe/ep2-psci-2/cpu-store-prefix-v2.json`；2026-10-27 以既有 Image 完成有序 store 元素前缀检查，两份报告均记录验证器指纹，覆盖统计、报告哈希及模型限制见 [审计说明](../../tools/lg-g8/local-sim/entry-probe/README.md#有序写入前缀检查)。原 `cpu.json` 和 v1 报告保留；旧 `local-2`/`psci-1` 仅保留为修复前历史结果。该次离线前缀检查没有构建新镜像或访问手机；随后 pmsg 校准与设备状态见上文。Android 实际 console 位于 `[0xb0000000,0xb0040000)`；旧 mainline console 位于 Android attach 范围外，旧空 pstore 不能证明内核没有执行。来源 blob、布局、产物哈希及 CPU 验证边界集中记录在探针说明。

私有证据根目录为主工作区的 `audit/`：`reboot-route-20261026/laf-kernel-ramtest-184155/`、`reboot-route-20261026/persistent-fix-184517/`、`runtime-native-handoff-20261026-172439/`、`persistent-reboot-acceptance-20261026/`。最新往返的逐命令退出码、协议返回、耗时和 Android 前后检查保存在独立目录 `persistent-reboot-recheck-20261026T211204+0800/result.json`；旧验收结果未覆盖。该 JSON 未记录源码 HEAD，提交关联由同次工作树检查与本段记录补充，私有 `context.md` 说明证据范围。本地修复报告仍为 `out/reboot-fix/acceptance-rebuild-20261026/` 和 `out/reboot-fix/acceptance-cpu-20261026.json`。

本次 pmsg 证据为主工作区 `audit/pmsg-calibration-20261026-once-1/`（目录名沿用预先指定日期，实际执行为 2026-10-27）、`private/pmsg-calibration-20261027-once-1/`、`private/pmsg-readback-diagnosis-20261027-once-1/`、`private/android-pstore-consumers-20261027-readonly-1/` 和 `private/android-init-scope-20261027-readonly-1/`；包含敏感原文，不提交。本机验证归档为 `private/pstore-snapshot-20261027-local-1/`，保存原始 13 项成功测试结果、当前源码/产物身份及 NDK 调查材料，52 份文件的内容、长度与权限已核对；本次归档未重跑测试，修复前调查产物单独标明。证据指纹与各自范围见 [校准记录](PMSG-CALIBRATION.md#私有证据)。

以下保留 2026-10-25 的历史排查记录。历史的“仅本机”“无分区写入”和设备当时状态只适用于各自时点，不描述当前情况；关于异常 OEM 格式和损坏 runtime carveout 的推论已撤回，不能作为当前决策依据。

---

历史状态：构建/板级调试，尚未成功在手机上启动主线用户空间

目标设备是 LG G8，SoC 是 Qualcomm SM8150。已从连接设备读到 `LM-G820UM`、`alphalm_lao_com`、Android 14 和当前槽位 `_b`。活动 DTBO 索引 `8` 对应 `Alpha_lao, rev_1.0`。设备现在没有执行刷写或分区操作。

## 已确认的资料

- 当前仓库是移植流程和经验库，不包含 LG G8 的内核、设备树或启动镜像。
- Linux 内核上游目前有 SM8150 的 SoC 支持。上游没有 `lg,alphaplus` 设备树。
- `sm8150-mainlining/linux` 有 LG V50 的 `sm8150-lg-flash.dts`，可以复用 SM8150、PMIC、UFS、USB、Adreno 和部分 LG 显示代码。V50 的板级数值不能直接用于 G8。
- LG G8 的 Android 设备树在 LG 4.14 内核源代码中，设备代号是 `alphaplus`。它包含 G8 特有的面板、触摸、指纹、电源、音频和存储配置。
- Omarchy 官方稳定版当前是 4.0.6。4.5.0-rc1 提供 aarch64 ISO，但它是测试版，并且官方支持重点是 Snapdragon X 等笔记本。手机需要用 Arch Linux ARM 根文件系统集成 Omarchy 软件包，不能直接把 PC ISO 刷进手机。
- SM8150 的主线 SoC 支持已经覆盖 CPU、UFS、USB、显示、GPU、Wi-Fi 和蓝牙等基础部件。postmarketOS 的 SM8150 资料仍把音频列为部分支持，把调制解调器、相机列为未测试。SoC 支持不等于 G8 的每个外设已经支持。

## 当前判断

结论：有源码线索，可以开始做 G8 的设备树移植。第一版目标应是 `fastboot boot` 内存启动、内核串口或 USB 调试、内屏和触摸。通话、移动数据、相机、指纹、音频和休眠需要单独验收，不能从内核启动推断。

最大的阻塞已经解除。fastboot 直接返回 `unlocked: yes`、`product: msmnile`、当前槽位 `b`，因此可以安全使用 `fastboot boot` 做内存启动。Android 里的 locked 属性来自第三方 Pixel 系统，不能作为 bootloader 状态证据。仍需保留匹配的原厂启动镜像和分区备份。

## 已完成的本地构建

- 外部工作区是 `/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy`，内核基线为 Linux 7.3，配置已切到 ARM64 4 KiB 页、48-bit VA/PA。
- LG G8 板级 DTB `sm8150-lg-alphalm-board.dtb` 已编译，根节点使用 `qcom,board-id = <0x020c0008 0>`；DTB 编译只剩显示 graph 的结构警告。
- 内置 Alpine ARM64 调试 initramfs 已嵌入 Image，能在构建产物中列出 `/init`、BusyBox、musl loader 和 USB serial 脚本。
- 主机基准 `boot_b.img` 的 `fastboot boot` 能回到 Android，证明临时启动链可用。
- 多个主线候选（MTP 基线、LG 板级 DTB、内置 initramfs、48-bit 配置、gzip kernel）均未留下可观测的 Linux 用户空间；设备最终回到 fastboot 或回退到 Android。它们都没有写入任何分区。

## Pro 审查后的只读 ABI 审计

- `unpack_bootimg` 已确认 stock `boot_b.img` 和 v0-v10 都是 header v2、4096-byte page、1660-byte header；stock 的 `ramdisk_size=16341093`，v3-v10 的 `ramdisk_size=0`。
- stock DTB 段不是候选使用的裸 FDT：解包文件开头没有 `d00dfeed`，在偏移 `0x19f66` 才出现一个损坏/非标准的 FDT，`dtc` 报结构错误；文件中还出现第二个被 header `dtb_size` 截断的 FDT。候选 v0-v10 则直接放入裸 FDT。这使 DTB 容器/ABL handoff 成为当前最高优先级，不能继续把裸 DTB 当作已验证的 stock ABI。
- stock kernel 段虽然以 `1f8b08` 开头，但 `gzip -t`/标准解压失败；stock ramdisk 也没有标准 cpio/gzip/lz4 magic，熵约 7.98。原厂 boot 组件很可能带 LG/ROM 特有封装，不能把 `unpack_bootimg` 打印的字段直接当成“普通 gzip kernel + 普通 ramdisk”。
- `audit/bootimg-summary.tsv` 保留了每个镜像的 header、段大小、地址和 SHA256；审计期间没有启动新镜像、刷写分区或改槽位。
- 发现与当前设备完全匹配的 LG 4.14 设备树源：`sources/android_kernel_lge_sm8150/arch/arm64/boot/dts/lge/sm8150-alpha/sm8150-alpha_lao_com/sm8150-alpha_lao_com_rev-1.0-overlay.dts`，其中 `qcom,board-id = <0x020c0008 0>`；同一 vendor 树还给出了 Alpha 的准确 reserved-memory 数值，可作为下一版主线 DTB 的校准基线。
- v11 用 v10 的 gzip 内核/内置 initramfs，换回 stock DTB 段：`fastboot boot` 返回 OKAY，但设备 80 秒内始终留在 fastboot，没有 Android 回退、USB 重枚举或 udev 事件。
- v12/v13/v14 在相同主线内核和 Alpha DTB 下加入非空 external ramdisk（raw、gzip、以及不与 kernel/DTB 重叠的地址），ABL 都直接返回 `Failed to load/authenticate boot image: Load Error`。这说明当前普通 `mkbootimg` 生成的非空 ramdisk/认证语义仍不符合该 LG ABL；不能再把“空 ramdisk”单独当成唯一解释。


## stock DTB 容器实际边界

- 重新按原始 `boot_a/b.img` 搜索内部 `AVB0` 后发现，`unpack_bootimg` 按 header 的 `dtb_size=997696` 截出的窗口并不是完整 stock DTB/container。DTB 起点都是 `0x21b4000`；到内部 `AVB0` 前的实际观察窗口为 A 槽 1,106,210 字节、B 槽 1,106,735 字节，分别比对齐后的 header 窗口多 106,786/107,311 字节。
- B 槽真实窗口结构是：前置 106,342 字节高熵 wrapper；FDT at `0x19f66`、`totalsize=499919`；468 字节间隔；第二个 FDT at `0x94209`、`totalsize=497777`；尾部 2,229 字节；随后才是 704-byte 内部 `AVB0`。A 槽结构相同，FDT 起点和实际窗口相差 525 字节。
- 因此 v11 所谓“换回 stock DTB 段”实际只传了 header 宣称的截断窗口，不能再用它证明完整 OEM DTB handoff 已经被测试。完整窗口已只读保存到外部审计目录，尚未重包或上机。
- 审计目录：`/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/stock-boot-container-20261025/`。下一步仍是离线还原 LG packer/container 语义，不是直接把这个窗口送进设备。

## 新增的 stock DTB/DTBO 容器证据

- stock `boot_b` 的 997,696-byte DTB 段包含两个 `d00dfeed` 变体，起点为 `0x19f66` 和 `0x94209`，可见字符串分别指向 SM8150 v2/v1；按 header 的 `totalsize` 单独抽取后仍不能被 `dtc` 当作普通 FDT 解析。整段和 boot 镜像中都没有 `QCDT` magic。它不是单个裸 FDT，也不能直接用普通 `mkbootimg` 的 `--dtb` 语义替换。
- `dtbo_b.img` 外层有 14 项 `d7b7ab1e` DT table，但表中 offset 与后续真实 `d00dfeed` 起点逐项偏离约 1.36 KiB，内层 FDT 的 structure/string header 也不自洽。按真实 magic 起点离线恢复后，entry 8 是 `LG Electronics, Alpha_lao, rev_1.0`、`qcom,board-id = <0x020c0008 0>`，与运行时 `androidboot.dtbo_idx=8` 和根节点完全一致。
- 选中的 entry 8 含 `ramoops_region@b0000000`：`removed-dma-pool`、`no-map`、`reg = <0 0xb0000000 0 0x300000>`，以及 record `0x1000`、console `0x40000`、ftrace `0x40000`。运行时 vendor ramoops 另外报告 `mem-size=0x280000`、`pmsg-size=0x200000`；3 MiB 保留区、2.5 MiB attach 区和 2 MiB pstore block 不能互相替代。
- 证据目录：`/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/stock-dtb-variants-20261025/` 和 `audit/dtbo-repair-20261025/`。这些是离线解析结果，不授权新的设备启动。

运行时设备树已经从 Android root 只读导出并强制转换为 DTS，确认实际根节点是 `LG Electronics, Alpha_lao, rev_1.0`，活动板号为 `0x020c0008`，USB 设备控制器为 `0xa600000`。stock boot ABI、vendor DTB/ramdisk 封装和 v16 RAM-only 结果已经归档；v16 只得到 ABL accepted、没有 Linux evidence。没有新的早期观测面或已知同板可启动链之前，不再进行设备启动实验。

## 当前下一步

v16 已完成唯一获准的 RAM-only 设备实验：`fastboot boot` 返回 `Sending OKAY`/`Booting OKAY`，但设备在 60 秒内始终留在 Qualcomm fastboot，没有 Linux USB、udev 重枚举、ADB、pstore 或 init marker。状态分类是 **ABL accepted but no Linux evidence**，不能写成 kernel 已启动。现在停止所有 v17+ 设备实验；后续只做离线的 LG ABL/DTB 容器、vendor ramdisk 封装、精确 OEM/KDZ boot chain 和早期观测方法研究。Omarchy 用户态尚未开始真机验收。

v16 的完整结果在外部工作区 `pro-handoff/v16-test-result.md`，主机时间线在 `audit/v16-host-20261025T061543+0800/`。未执行任何分区写入、擦除、槽位切换或 vbmeta 修改。

## 设备状态

截至 2026-10-25，ADB 已检测到手机，读取结果为 `LM-G820UM`、`alphalm_lao_com`、SM8150、Android 14、槽位 `_b`。已只读备份 A/B 的 boot、dtbo、vbmeta 和运行时 FDT 到 `/home/lightjunction/Backups/lg-g8-alphalm-2026-10-25`。fastboot 已返回 `unlocked: yes`、`product: msmnile`、`current-slot: b`。已做的所有测试都是 `fastboot boot`，没有进行任何写设备操作。

## Pro v4 离线复核

Pro 5/5 已确认：stock header 的 `dtb_size=997696` 是两个逻辑 FDT `totalsize` 的总和，不是从 `dtb_start` 到内部 `AVB0` 的物理 wrapper 边界。B 槽实际观察窗口为 1,106,735 bytes，v11 使用的 997,696-byte 片段被物理截断，因此撤回“v11 已测试完整 stock DTB handoff”的说法。

- [Pro v4 完整复核](https://drive.google.com/file/d/1POia1deXXQUi_6gUMjfSvd_8cx6HQyeP/view?usp=drivesdk)
- [Pro v4 状态](https://drive.google.com/file/d/1GiJss4PUSmBgw9V8Vj-gEJ7U6Jzt3_LX/view?usp=drivesdk)

状态：`offline_pro_review_v4_complete__no_new_device_boot`。下一步只做 OEM wrapper/parser/packer/metadata 的离线 round-trip；不生成或启动新的设备镜像。

## stock boot logical/physical AVB 边界

- stock 外层 `AVBf` 报告 `original_image_size=36339712`、`vbmeta_offset=36339712`、`vbmeta_size=704`；这正好是 header 声明的逻辑 boot image 结束位置。
- 但 B 槽实际 `AVB0` 位于 `36447023`，比逻辑 vbmeta offset 晚 `107311` 字节；A 槽同类差值为 `106786`。单独抽取 704-byte `AVB0` 可以解析出 boot hash descriptor、stock salt 和 Alpha_lao fingerprint，但完整 stock 文件和按分区大小截断的文件都不能被标准 `avbtool info_image` 直接解析。
- 这确认 stock boot 存在 OEM wrapper/export boundary，但还不能单凭静态文件判断多出的字节是分区物理内容还是备份导出尾部；禁止把 wrapper 原样送入设备。
- 详细材料：`/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/stock-boot-boundary-20261025/`。

## OEM 容器解析器的第一版

- `tools/lg-g8/inspect-oem-boot.py` 已加入仓库。它只读解析 Android v2 header、逻辑 DTB 窗口、LG DTB wrapper、内层 `AVB0` 和末尾 `AVBf`，不会调用 fastboot。
- 它兼容当前备份中 A 槽 v2 header 尾部向后偏移 1 字节的实际形态；如果按固定 AOSP 偏移读取，会把 A 槽 `dtb_size` 误判成 `255410176`。
- 对原厂 `boot_a.img` 与 `boot_b.img` 做了原样拆解/重组，两个 `roundtrip.img` 均与原文件逐字节相同。该结果只证明边界模型和字节保留正确，不代表修改后的镜像已经可启动。
- 复现报告：`/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/oem-parser-20261025-v2/report.json`；提取文件在同目录 `extract/`。
- 当前仍不能把新 DTB 直接塞进这个 wrapper：两个 FDT 的物理起点、间隔、尾部和 header `dtb_size` 的关系已测出，但 LG ABL 的重定位/校验语义还没有被证明。设备继续保持 fastboot，不做新的 RAM boot。

## Pro v5 复核结果

- Pro 5/5 已读取 parser v2 的当前 revision，并确认 A/B `roundtrip_exact=true` 足以作为“机械边界无损”的基线。
- 这不等于已经证明 header 自定位的语义、首个 `AVB0` 是真实容器终点、最后一个 `AVBf` 是正确 footer、FDT 已被完整解析，或 ABL 会接受任何改过的 wrapper。
- tail 的暂定排序是：LG/OEM post-processing wrapper/container 最高；分区大小与导出边界交互次之；纯导出尾部单独解释的可能性较低但不能排除叠加。
- 两个 `d00dfeed` 的 FDT header 算术自洽，是“标准未压缩 FDT”的强候选；仍需对精确切片做完整 libfdt/dtc/token walker 检查。高熵本身不能叫作加密证据。
- 同尺寸 FDT splice 仍需保留 A/B 槽各自 header 尾部位置、prefix/gap/tail、inner AVB0 和未解释的 OEM 元数据；任何被 AVB descriptor 覆盖的 FDT 修改都需要正确密钥和离线重签，不能只保留 footer。
- Pro v5 完整复核：[30-response-v5.md](https://drive.google.com/file/d/1HkmPZzS6jF3Ay1uqnc9Cc38yBJBBkj7Z/view?usp=drivesdk)；状态：[99-status-v5.md](https://drive.google.com/file/d/1ddruKHxTpQKqVSeHhPFaD8-u4IFt4pXg/view?usp=drivesdk)。
- 结论：parser boundary 已建立，A/B byte-level round-trip 已成立，OEM wrapper 与 AVB 语义仍未闭合；继续 v17+ 盲测明确停止。

## FDT 精确切片校验结果

- 已按物理起点精确切出 boot_b 的两个 FDT：相对 `106342/606729`，长度 `499919/497777`。两个 header 的 `totalsize`、`off_dt_struct + size_dt_struct == off_dt_strings`、`off_dt_strings + size_dt_strings == totalsize` 都成立。
- 但完整结构 token 校验和 `dtc -I dtb -O dts` 都在相同位置失败：结构块绝对切片偏移 `176` 的属性 `length=13` 后出现 `nameoff=0x0a000000`，超过 strings 表；`dtc` 报 `String offset 167772160 overruns string table`。
- 因此当前准确分类是：**header 算术自洽，但结构体无效的 FDT/变换数据候选**。不能再把它称为已验证的标准 FDT，也不能仅凭高熵 prefix 断言“加密”。
- 复现工具：`tools/lg-g8/validate-stock-fdts.py`；报告：`/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/stock-fdt-validation-20261025/report.json`。该命令只读 stock 备份，没有设备操作。

## nameoff 字节序试验

- 按 Pro v6 的最小实验，在临时副本中测试了三种模式：原始解析、仅修复越界 `nameoff` 的 bswap32、所有 `nameoff` 统一 bswap32。
- 原始模式在 slice offset 176 以 `nameoff=0x0a000000` 失败；统一 bswap32 会更早在第一个正常 `nameoff=0x0f` 处失败；只修复越界 nameoff 可越过第一处，但在 offset 204 遇到 `token=0x03000000`，`dtc` 报 `Invalid opcode word 03000000`。
- 这说明“只交换一个 nameoff”不是解码器。当前最合理的描述是：存在可检验的字段/字节序变换假设，但变换规则尚未恢复；不能称为已知格式、加密或可用 DTB。
- 试验工具和报告：`tools/lg-g8/trial-fdt-nameoff-transform.py`、`/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/fdt-nameoff-trials-20261025/report.json`。所有结果都来自临时副本。

## magiskboot 对照结果

- 已从官方 Magisk v30.7 APK 提取 Linux x86_64 `magiskboot`，只读处理原厂 `boot_a/b.img`。
- `boot_b`：Magiskboot 识别为 Android header v2、kernel gzip、ramdisk raw、DTB 997696；`unpack -n -h` 能完整提取 kernel/ramdisk/逻辑 DTB。
- `boot_b` 自动解压模式报 `corrupt deflate stream`，说明原厂 kernel 的 gzip 表现不能按普通 gzip 直接解压。
- `boot_b` raw `repack` 的输出文件大小与原文件相同，但逻辑 DTB 窗口之后的 LG wrapper、inner AVB0 和外层 footer 内容被清零/重建，不能视为原厂容器的无损 round-trip。
- `boot_a` 被 Magiskboot 按固定字段偏移误读为 `dtb_size=255410176`，并报 `Corrupted boot image!`；这与自研解析器发现的 A 槽 header 尾部偏移 1 字节一致。
- `magiskboot dtb ... print` 对两个逻辑 DTB 都没有成功打印，进程异常退出；它没有恢复 LG 的非标准 DTB 表示。
- 结论：Magiskboot 适合确认普通 Android header、压缩和 raw 组件，但不能替代 LG OEM wrapper/AVB parser。所有 Magiskboot 输出只在外部 audit 目录，未送进设备。

## A/B FDT cursor trace

- 已按 Pro v7 要求对 A/B 两槽、两个 FDT 记录原始 4-byte bytes、BE/LE 数值、property value 起止和 align4 cursor。
- 四个 FDT 的前半段 cursor 都一致：offset 176 是 `PROP`，length `13`，value `188..201`，标准 align4 下一 cursor 为 `204`；因此 offset 204 的 `03 00 00 00` 不是前一个 property 的普通 padding 被误读造成的。
- 该结果增强了“分段字段序列化 / piecewise word-order / OEM 非标准 representation”的假设，但没有恢复 decoder。报告：`/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/fdt-cursor-trace-20261025/report.json`；工具：`tools/lg-g8/trace-fdt-cursor.py`。
