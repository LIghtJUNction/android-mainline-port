# LG G8 看门狗与自动恢复证据

2026-10-26，基于集成仓 `dc95bad170bfe58d9231814d7790be293fe6b3d5` 之后的只读本地调查。主线 Linux / Omarchy 尚未在手机验收，当前仍不能开始无人值守的新镜像迭代。本次没有设备命令、寄存器访问、配置修改、镜像构建或候选启动。

## 目标、路线与完成标准

目标是让候选在进入 Linux 前后挂死时都能自动复位，并回到可由主机控制的 Android / fastboot 状态。现有 Android 软件 fastboot 往返已经真机通过，证据见 [当前状态](STATUS.md#当前验收与下一步)；它不覆盖候选已离开 Android 后的挂死。

沿三段链路取证，避免混用不同实现的结论：

| 阶段 | 已核实的证据 | 尚缺的证据 |
| --- | --- | --- |
| 运行中的 Android | vendor `watchdog_v2.c`、运行配置与 runtime FDT | 离开 Android 后各固件阶段的实际硬件状态 |
| ABL 到 Linux 入口 | 本机 stock LinuxLoader 成功路径显式设置并启用 APSS watchdog | 失败候选实际经过的路径、最终入口状态、超时后的启动去向 |
| Linux 驱动接管 | 当前 `qcom-wdt.c` 的 probe 行为；实际候选 DTB / initramfs；当前构建配置 | 真机驱动成功 probe、接管前后挂死均可自动恢复 |

完成标准需要指定镜像与新实验标识的真机记录：能归属的早期执行日志、实际复位时序、复位后 Android / fastboot 协议可用，以及日志能可靠读回。单凭代码中的 enable、一次重启现象或模拟器通过不算完成。未来实验一次只改一个变量，先满足记录保留与恢复前提，再安排候选。

## Android：独立于通用 watchdog 框架的 vendor 驱动

厂商源固定在提交 `d4f335e13d73c4aa2af85581a6ec11b670ee5b94`；`drivers/soc/qcom/watchdog_v2.c` blob 为 `94089d32884a66142cbd0c92c22b23e1e15252f9`。导出的运行内核配置包含：

```text
# CONFIG_WATCHDOG is not set
CONFIG_QCOM_WATCHDOG_V2=y
CONFIG_QCOM_FORCE_WDOG_BITE_ON_PANIC=y
# CONFIG_QCOM_WDOG_IPI_ENABLE is not set
```

此驱动不依赖通用 Linux `CONFIG_WATCHDOG` 框架，因此不能由 `CONFIG_WATCHDOG=n` 推断 Android 未启用硬件看门狗，也不能直接套用通用 `/dev/watchdog` 操作。

已保存的 runtime FDT SHA-256 为 `75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf`。节点 `/soc/qcom,wdt@17c10000` 的 compatible 是 `qcom,msm-watchdog`，reg 为 `<0x17c10000 0x1000>`，没有 status 属性；`qcom,bark-time=16000`、`qcom,pet-time=9360`，单位毫秒。base `sm8150.dtsi` 的 bark=11000 不能替代本机运行值。节点含 `qcom,wakeup-enable`，没有 `qcom,userspace-watchdog`。

驱动默认 enable=1，按定时器唤醒高优先级内核线程喂狗。初始化将 `user_pet_enabled` 设为 false；用户态 pet 接口还受 DT 属性控制。`WDT_HZ=32765`，初始化以 bark 值设预警计数，并将硬件复位计数设为 bark 加 3 秒。结合本机 runtime bark=16 秒，只能静态推算这段初始化会设置约 19 秒的 bite；没有测量其实际时序，更不能将其当作 ABL 的超时值。

suspend、panic 和显式 disable 有不同处理，其中 disable 可以调用 SCM 安全服务。已检查的驱动没有 `.shutdown` 或普通 reboot notifier；其他重启代码和固件仍可能改变硬件状态，不能由这一缺项推断看门狗连续继承。

## ABL：退出 UEFI 服务后显式启用 APSS watchdog

以下证据来自已有本地提取物，未重新执行提取或修改手机固件。源码来源记录位于私有工作区 `audit/reboot-route-20261026/abl-extraction.json` 与 `HANDOFF-CLAUDE-2026-10-26.md`。记录描述 A/B ABL 哈希相同、从 ELF32 签名容器的 LZMA 段（偏移 `0x3078`）提取 LinuxLoader；仅为反汇编调整 PE debug directory 的版本单独列出。本次独立重算下表文件指纹，没有重演整个提取过程。

| 本地证据文件 | 字节数 | SHA-256 |
| --- | --- | --- |
| `abl-b.img` | 1048576 | `bfcb70eaabf680e7e0a4e19b20a56fcac85c22991e8553d24ee4fefc993b8f7c` |
| `LinuxLoader.efi` | 774144 | `1075dcb1a75626c55f5874e163b5972665f1c291f4acb34737d2de2afa607ce7` |
| `LinuxLoader.analysis.efi` | 774144 | `33a55bf8876ec5e79bc58be26f6916085629997653a8808ed0c54733a817f42f` |
| `LinuxLoader.dis` | 5087742 | `6a59fae6534d7673128de701fa1bb0f42d2b087ba209ca50c287b0380557ef4e` |
| `abl-decompressed.bin` | 774344 | `68d8d0f77e1835817e0235d52dc56570e772aff027a607643184c456998ae7d2` |

调查时文件位于本机临时目录 `/tmp/re/lg-g8-abl-sgpcu9rm/`，该路径不保证长期保留。原始固件、反汇编和设备日志不随本文分发；复核应按上述哈希匹配已有私有材料。下列代码地址是反汇编中的镜像地址，不能直接当作手机运行时绝对 PC。

`0x303ec` 调用 `0x2b9f0`；该封装在 `0x2bae4` 经服务表偏移 `0xe8` 间接调用，参数来自 ImageHandle 与 memory-map key，结构对应 `ExitBootServices`。服务表来源可追至 SystemTable+`0x60`。`0x303f0` 检查返回值最高位；成功路径在 `0x303f4–0x30430` 执行：

| MMIO 地址 | 写入值 | 寄存器及调用位置 |
| --- | --- | --- |
| `0x17c10010` | `0x000b7fbb` | BARK_TIME，`0x30408` |
| `0x17c10014` | `0x000cffb2` | BITE_TIME，`0x30418` |
| `0x17c10008` | `1` | EN，`0x30424` |
| `0x17c10004` | `1` | RST，`0x30430` |

四次调用均进入 `0x666f8`，实际存储在 `0x66740: str w19, [x20]`。之后存在通向 ARM64 入口的路径：`0x305f8–0x3060c` 载入保存的入口指针和 DTB，设置 x1–x3=0 后 `blr x8`。核心指令已由主线程独立核对，不仅依赖子 agent 报告。

这是 LinuxLoader 主动重新配置和启用看门狗的静态证据。此前只讨论“是否继承 Android 看门狗”的假设不完整。写入之后依次调用 `0x2bbb8`、`0x3e568`（w0=1）、`0x4af70`，并按返回 flag 条件调用 `0x4b1d8`；本次补查的行为见下节。直接指令与部分尾链已有识别，动态接口和安全固件实现仍未穷尽，因此以下两点仍未证明：

- Linux 入口瞬间看门狗仍启用，且在完整交接过程中保持预期配置。
- 失败的 `lg-native-3` 实际经过这些写入，并由看门狗触发了观察到的重启。

不能仅按寄存器常量宣布 ABL 超时秒数，也没有证据确认 bite 后会进入 Android、download、crashdump 或其他状态。

另一路 Fastboot 初始化在 `0x60dc8–0x60de8` 通过 BootServices+`0x100` 发起结构对应 `SetWatchdogTimer(0, 0x10000, 0, NULL)` 的调用；字符串含 `Fastboot: Couldn't disable watchdog timer: %r`。它说明 Fastboot 请求关闭 UEFI watchdog timer，不能否定后续 Linux 启动路径的 APSS MMIO enable。当前没有证明这两个接口控制的是同一机制。

复核可按以上镜像地址在 `LinuxLoader.dis` 定位；四次写入与 ARM64 入口均位于已独立读取的行 48370–48544。服务表来源（行 87074–87089）、UEFI watchdog 调用（行 98114–98122）与 `strings.txt` 行 4062–4064 由只读子 agent 提供，核心写入、入口、store helper 和文件哈希已独立核对。

### 启用后的交接子调用：已识别行为与边界

以下补查沿用上表的同一份 LinuxLoader，不涉及固件修改或真机执行。

`0x2bbb8` 的主路径屏蔽 IRQ/FIQ 和 SError，执行数据缓存 set/way 维护与指令缓存失效，按当前异常级别清除 SCTLR.C、SCTLR.I、SCTLR.M，并执行 TLB 失效和 barrier。缓存遍历 `0x52028` 使用 CLIDR_EL1、CSSELR_EL1、CCSIDR_EL1，回调 `0x51bc0` / `0x51bd0` 分别执行 `dc cisw` / `dc csw`；`0x5602c` 尾链执行 `ic iallu; dsb sy; isb`。这些指令说明交接还包含缓存、MMU 与地址转换状态处理，不能将四次 watchdog 写入视为入口前的最后动作。

缓存封装还调用 `0x52310`：`mrs x0, DAIF; tst w0, #0x2`，再返回布尔值；`0x68910` 据此决定是否走到 `0x51b44` 的 `DAIFClr #3`。原始编码已核对：读取与测试分别为 `0xd53b4220`、`0x721f001f`。测试位是位 1，而架构 DAIF 中 IRQ/FIQ 掩码位为位 7/6，因此不能把该封装直接解释成正确保存并恢复 IRQ/FIQ 状态。这里只记录静态指令与语义疑点，没有证明失败候选执行过该路径或由此失败。

`0x3e568` 查询 `Shared_IMEM_Base` 和 `MPM2_SLP_CNTR_ADDR`。其服务表来自入口参数：PE entry RVA=`0x1000`，经 `0x10a8` 保存 ImageHandle / SystemTable；`0x110c` 调用 `0x13e0` 将 x20/x19 恢复到 x0/x1，随后 `0x1110` 调用 `0x561e0`，把 `[x1+0x58]` 保存到 `0xb13e8`。固件内嵌 `UefiRuntimeServicesTableLib.c` 路径及 `gRT != ((void *) 0)` 断言，与 UEFI RuntimeServices 布局相符。查询在 `0x3e664` / `0x3e6f4` 经该表+`0x48` 间接调用，结构对应 `GetVariable`。w0=1 的交接调用在取得有效指针且读取到非零计数时，向取得的 Shared_IMEM_Base+`0x6b4` 写入该计数。运行时变量值、最终地址及接口实现未知，不能由变量名称排除其副作用。

`0x4af70` 读取 `0xb1391` 的字节 flag。`0x4af80` 的 `GetVmData` 路径通过 SCM 协议间接接口，以 w1=`0x02000609` 发起调用；在状态成功且取得非零结果时，`0x4b09c` 可以将 flag 设为 1，因此镜像初值为 0 不能证明条件调用不发生。直接调用点包括 `0x23c0` 和 `0x4b2a0`；后者所属函数 `0x4b290` 的上层调用点 `0x2e1dc` 已先要求 flag 非零，不能据此证明首次设置发生的时刻。尚未完整追溯 `0x23c0` 的启动上下文，也没有真实接口返回值。

flag 非零时，`0x4b1d8` 经 `0x56ea0` 执行 `smc #0`。首参数由写 w8 再存 x8 形成，是零扩展的 `0x0000000086000000`；其他参数不能假定全零。返回日志称 `returned Smc call to disable Uart logging from Hyp: 0x%x`，只支持识别调用意图，安全固件实际行为仍未知。该路径和 BootStats 还使用 `0x4c398`；其正文包含间接调用、尾跳及其他子调用，不能当作没有副作用的简单内存分配函数。日志后端、异常路径和动态协议也没有穷尽。

这些结果缩小了启用后到入口之间的静态未知范围，仍不能证明入口瞬间的 watchdog EN、计数、访问权限或最终配置，不能给出实测 timeout 和复位去向。后续真机验收仍须按本文完成标准取得可归属证据。

## 失败候选：用户态计时器和缺少模块

候选身份取自 `out/abl-handoff/lg-native-3/manifest.json`，该 manifest 的 scope 是构建时声明，不能替代另行记录的真机结果：

| 内容 | SHA-256 |
| --- | --- |
| `boot.img` | `b183fe857ee8e933272dad01cbd5199fc4bc6b303adc11a37c7b0fe3c23b3b78` |
| kernel Image，25016832 字节 | `bb475e12ce81e2e599a643317d50b0a8603d1eec573ca41be09fa029307163a7` |
| `initramfs.cpio` | `35d50e6e7af76f11fb0f72c7690edfab203fe760b43246319ea3566564fb2067` |
| `linux.dtb` | `01566693e00b8f7635fa8abbd861d760e9be086b43b8f5a6297cffb7d1f1c6cf` |

当前私有工作区 `out/kernel-uefi-tiny/arch/arm64/boot/Image` 与 manifest 的 kernel 长度和哈希完全一致。当前 tiny 和 v50 配置都有：

```text
CONFIG_WATCHDOG=y
CONFIG_WATCHDOG_CORE=y
# CONFIG_WATCHDOG_NOWAYOUT is not set
CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED=y
CONFIG_WATCHDOG_OPEN_TIMEOUT=0
CONFIG_QCOM_WDT=m
```

tiny 还含 `CONFIG_INITRAMFS_SOURCE=""`、`# CONFIG_WATCHDOG_SYSFS is not set`；当前输出没有 `drivers/watchdog/qcom-wdt.ko`。manifest 未记录配置哈希，因此当前 `.config` 不能被宣称为这些 kernel 字节的已锁定构建输入。

实际候选 CPIO 按 newc 格式直接解析，未解包或执行，包含 29 项，无 `.ko` 或 `lib/modules`。已读取其中 `/init` 与 `/probe-init`，两者没有模块加载；后者的恢复代码为：

```sh
(/bin/busybox sleep 120; echo LGG8-HANDOFF-AUTO-REBOOT >/dev/kmsg; /bin/busybox reboot -f -n) &
exec /init
```

计时仅在 `/probe-init` 开始执行后生效，依赖用户态、调度和 reboot 路径。它不覆盖 ABL、payload 复制、Linux 入口或更早挂死。`panic=10` 同样要求内核到达 panic 处理路径。缺少 Linux 模块也不能反向证明固件留下的硬件看门狗关闭。

实际候选 DTB 的 `/soc@0/watchdog@17c10000` 含 `compatible = "qcom,apss-wdt-sm8150", "qcom,kpss-wdt"`，reg=`<0 0x17c10000 0 0x1000>`，没有 status 属性。节点没有被该属性禁用，但节点存在不等于驱动已接管。

## 主线驱动：probe 之后才有接管证据

本地 `drivers/watchdog/qcom-wdt.c` 的匹配表接受 fallback `qcom,kpss-wdt`。probe 先取得 MMIO、输入时钟和频率等资源；默认 timeout 为不超过 max_timeout 的 30 秒，并接受 DT 的 timeout 设置。只有读到硬件 EN 已运行时，才调用 start 关闭、重设 bark/bite、重新使能，并标记 `WDOG_HW_RUNNING`，随后向 watchdog core 注册。

这段代码说明“已运行的看门狗被驱动发现后如何接管”。把 `CONFIG_QCOM_WDT=m` 改为 `y` 只能使驱动可能更早进入 probe；不覆盖 probe 之前的执行，也不能保证资源获取成功或复位后回到 Android。本次没有修改配置，不能把这项建议写成自动恢复已解决。

源码目录没有 Git 元数据。Makefile 标记 7.3.0，生成 release 为 `7.3.0-sm8150`，编译信息标记 clang/LLD 23.1.3；这些都是本地文件标签，不能替代已核实的上游提交。相关指纹如下，路径相对私有工作区：

| 文件 | SHA-256 |
| --- | --- |
| `kernel/linux/drivers/watchdog/qcom-wdt.c` | `ae514edc824eb09e1d9a2e2d36051a481d09666798a5adf86c55767e48c25ebc` |
| `kernel/linux/arch/arm64/boot/dts/qcom/sm8150.dtsi` | `82887c779c9425ca23e49a81fe3ff8d8c7eec4d073edb305e6a9270371f3b7bc` |
| `out/kernel-uefi-tiny/.config` | `c8ee45c025a01e35cf488e7ada4a60ef321b4bd0560af07ed6027ff3b8efceb2` |
| `out/kernel-v50/.config` | `2453288a29469507adf5fc1e235e1b4ff1d4eb7115af8bfa67490ad616f8e9ab` |
| `audit/reboot-route-20261026/vendor-boot-b.config` | `0e28ed4033e38d0e35a288e60da786248c17e9187a09b5a73b70fd30f1721ec7` |

## 现存文件快照：不追认历史构建来源

2026-10-26，在集成仓 `47d7b8316762542f0bdfd9a81191aab63202211e` 基础上，使用新增的 [采集工具](../../tools/lg-g8/snapshot-kernel-evidence.py) 只读采集 13 个显式指定文件：当前 tiny Image、tiny/v50 配置、历史候选 DTB、watchdog 驱动、SoC DTSI、两个 alphalm DTS、Makefile，以及四个生成元数据文件。采集前后检查未发现这些文件字节变化，没有递归采集源码或 include 依赖。

当前 Image 为 25016832 字节、候选 DTB 为 95765 字节，SHA-256 均与上文 `lg-native-3` manifest 声明相等；tiny/v50 配置、watchdog 驱动和 SoC DTSI 的哈希也与上表一致。比较只说明现存字节与历史 manifest 声明一致，未认证该声明，也未证明当前配置或源码是历史 Image / DTB 的构建输入。旧 manifest 未修改。

私有报告为集成仓根目录 `private/kernel-evidence/snapshot-20261026T151922913315Z.json`，权限 `0600`，由根 `.gitignore` 排除，不随源码提交。报告 SHA-256 为 `c50e939ac07fb0f07bc531a9e7ba6a49e942fda8d498449d960393040baa0f59`；其中记录的采集器 SHA-256 为 `77fd0f21673d07c6717ee87587da72b9c36522eac8467e535755f7600a093dd4`，旧 manifest SHA-256 为 `c4a3f6662c5a9cf8784d2923866f4f816495ef7cc6aaf43f2b52d39c2157cccf`。报告保存完整选定文件指纹和绝对路径；使用与限制见 [工具说明](../../tools/lg-g8/local-sim/README.md#现存内核文件指纹)。

本机六项测试通过，覆盖匹配/差异、拒绝覆盖、异常输入、FIFO 不阻塞、链接保护及报告权限。采集没有生成内核或启动镜像，也没有访问手机；仍不构成历史来源、可复现构建、格式合法性或自动恢复验收。

## 下一项设备实验与阻塞

2026-10-27，用户明确批准后已执行 [一次性 pmsg 保留校准](PMSG-CALIBRATION.md)：63 字节完整写入、正常 Android reboot 返回且 boot_b 哈希匹配，但 pstore 为空，读回失败。此前命令启动前的自动审批拒绝属于历史时点，当前阻塞是日志读回链路未建立。没有重复写入或 reboot，也没有新候选启动。

已核对实际 BootReceiver 与三个原生组件的相关路径：普通读取没有发现直接删除，`logcat -L -c` 有显式 pstore unlink，但无本次调用及删除成功的证据。已确认 pstore mount 早于首次 logd，挂载后同步快照方案只完成设计，详见 [消费者调查与方案](PMSG-CALIBRATION.md#实际-android-消费者与启动顺序)。下一步补齐完整 init 导入集合、更早服务、logcatd 实体与触发条件，再验证 helper 的权限、暂存生命周期和本机边界行为；本次空读回尚不能区分保留、识别/导出和用户态清理。console / EP2 保留、裸探针 PSCI reset，以及抵达 reset 前挂死后的恢复仍需分别验证，不能将完整 pmsg 写入或上述静态看门狗证据当作全自动迭代验收。设备最后实测为本次 reboot 后的同一 Android 启动，启动完成、root 可用、槽位 b、内核符合预期；没有后台设备任务。结论变化见 [工作日志](WORKLOG.md)，临时用户态计时器见 [技术债](TECHNICAL-DEBT.md)。
