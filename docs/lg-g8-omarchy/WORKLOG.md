# LG G8 调试结论变更

按结论变化记录，当前验收与下一项设备实验以 [STATUS.md](STATUS.md#当前验收与下一步) 为准。原始设备资料留在私有工作区，不提交。

## 2026-10-27：主线 USB2 诊断配置

重新编译仓库基线板树，逐字节复现已审计的 `eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9`，记录 33 项实际 CPP 输入指纹。旧失败候选的 Linux DTB 与该基线只差 `/chosen` 的启动参数和 initrd 地址；实际 boot 封装的解压 shim、Image/initramfs/DTB 字节链一致，四项 shim CPU 检查通过。Image 的 IKCONFIG 提取后与仓库 tiny 配置完全一致，补上失败 Image 与配置的直接关联；这不认证全部源码和历史构建环境，也不证明手机实际进入 shim。

本机 PHY core 先查 provider，再检查 status；禁用 QMP 通常不注册 provider，现有 DWC3 USB3 引用可因此返回 EPROBE_DEFER。DWC3 不因 maximum-speed 自动跳过该获取，单纯限速不足。新增独立 [USB2 诊断板树](../../devices/lg-alphalm/USB2-DIAGNOSTIC.md)，包含校准基线，仅覆盖四项属性：只消费 HS PHY、只保留 usb2-phy 名称、限制 high-speed、wrapper 选择 UTMI 时钟。未猜改 USB3 供电/地址/SID，原板树和外部源码保持不变。

早期 Android 快照调查在执行路径/专用域尚未闭合处收束，未部署；它保留为日志辅助，不继续作为主线开发的扩展调查。新增 USB2 配置属于主线候选准备，没有生成或发送新 Android boot 镜像，没有设备操作，主线启动与 USB 枚举仍未验收。完整编译和 20 项语义/反例检查通过，包含保留区越界修改、保留双 PHY 和缺失 UTMI 三项拒绝反例；39 项实际输入与工具指纹前后一致，重复输出目录拒绝且原产物不变。最终 USB2 DTB 为 `b65a8f4cbbfa4bfab9360f74bf1872fd6d2814aab506897c82c166bdfd080215`，报告在 `out/usb2-profile-20261027-2/verification.json`；初次引用编号误报证据保留在 `-1/`，已修正比较器，未掩盖为首次全部通过。详细结果见上述诊断说明；基线证据在 `out/mainline-next-baseline-20261027-1/`，旧交接核对事后归档在 `out/handoff-envelope-review-20261027-1/verification.json`，均被 Git 忽略。旧 handoff 验证器未自动验证最终 boot 封装的缺口单独保留，本轮没有顺带修改既有未跟踪工具。

## 2026-10-27：真机快照运行测试与 init/APEX 补采

已在 LG G8 当前 Android 14 的 Magisk root 域首次执行已有正式 pstore 快照产物，未重建 helper。执行前只读确认 model/device、槽 b、启动完成、root、内核、修复 marker 与 boot_b 哈希，并校验本机有效恢复备份。最初 adb push 因 root 的 0700 目录拒绝 shell 写入而失败，helper 尚未执行；保留失败记录后改用 root 标准输入传输，哈希一致后首次执行。helper 退出 0，status/manifest 完整，pstorefs 枚举为 0 条，四份回收文件的长度、SHA-256 和权限核对通过，boot_id 未变；校验后清理并确认该 nonce 的远端临时文件已移除。

该结果建立当前 Android/Magisk 环境下的空快照运行能力，不能推断非空记录复制、早期 init 域权限、启动顺序、跨 reboot 保留或 console/EP2 恢复。API 37 CRT note 未阻止本次产物执行，但不因此声明通用最低系统版本。没有部署 init、写 pmsg、重启或生成新启动镜像。

另只读检查七个遗漏 import 和当前 APEX 顶层 rc，共 23 个目标：17 个成功文件（16 个 APEX 路径及 boringssl_self_test 规则），其余六个原遗漏 import 当前 MISSING。成功文件长度与前后哈希核对通过，采集前后为同次启动；当前路径缺失不证明更早启动时缺失。更早 ueventd、bootstrap APEX 配置及原生服务的副作用仍未排除。并行重跑 boot 31 项和 hardware 16 项本机测试，47 项全部通过，未运行 CI。

私有证据为主工作区 `private/runtime-test-preflight-20261027-1/`、`private/pstore-runtime-smoke-20261027-f1e537a0a46e2529/`、`private/android-init-gaps-20261027-readonly-1/` 与 `private/contract-regression-20261027-1/`；原始日志和设备信息不提交。详情及报告指纹见 [读回记录](PMSG-CALIBRATION.md#私有证据)。下一步核对 bootstrap 时的规则集合和 helper 早期路径/标签/域权限，再形成同步观测的具体修改材料；主线 Linux/Omarchy 仍未在手机验收。

## 2026-10-27：完成 boot 内存与硬件离线契约实施及独立复审

完成 [boot 内存契约](../../tools/lg-g8/BOOT-CONTRACT.md) 和 [USB、DMA 与显示契约](../../tools/lg-g8/HARDWARE-CONTRACT.md) 的本机实施、独立复审及真实输入复跑。复审补齐输入和内核语义边界后，最终分别为 31 项与 16 项测试全部通过；这些是工具的本机验证结果，不是启动或硬件验收。boot 工具对不支持或歧义输入拒绝推断，硬件工具对配置、命令行及保留区证据不足保留 unknown；报告独占创建并记录输入指纹。

审计对象 `out/reboot-fix/acceptance-rebuild-20261026/boot.img` 是已安装 Android 的修复 boot，不是新的主线候选；已有 `out/local-sim/dtb/patched.dtb` 仅作为外部假设布局，不在该 boot 的两棵 DTB 表中。复审后对两棵嵌入 DTB 和该外部布局的三个场景重新审计，均退出 1：按 header 字段直接假设物理地址时，kernel/ramdisk、ramdisk/DTB 交叠，三个载荷均在 runtime RAM 外。这否定的是地址解释，不能证明 ABL 实际装载错误。gzip 内核运行范围、解压放置及最终 DTB 选择/fixup 仍未知；嵌入树的零长度 RAM 占位不能认定为空 RAM，两棵树各有五段固定保留区覆盖缺口。patched 布局覆盖 runtime 固定及永久保留区并集、三段 RAM 一致，也不证明镜像可启动。

boot 复审最终报告位于 `out/boot-contract-review-20261027-f6fb6105/{embedded-0-review-report.json,embedded-1-review-report.json,patched-prospective-review-report.json}`，精确命令、退出码、报告与输入哈希见同目录 `verification.json`；初次实施证据保留于 `out/boot-contract-20261027-f6fb6105/`。原 boot、runtime FDT 和 patched DTB 输入哈希均未变。硬件最终报告为 `out/hardware-contract-audit-20261027/report-review.json`，权限 `0600`，两份 FDT 完整校验均为 0；工具退出 0 只表示报告生成成功。此前报告保留，以上 `out/` 目录均被 Git 忽略，原始诊断与敏感资料不提交。

硬件核对确认 LG runtime 与候选 USB1 均声明 SID `0x140`、mask `0`，没有依据复制其他手机的 SID 补丁；候选 DWC3 引用已禁用的 USB3 QMP PHY，是尚未验证实际影响的依赖缺口。候选配置下的 translated、strict=true 仅为条件性默认 IOMMU 策略，实际设备 domain 与 stock 策略仍 unknown。两份树均无 simple-framebuffer，候选配置关闭 DRM；保留 splash 区间不足以建立显示路径。未据此修改板级 DTS，也未把 USB 枚举、DMA、显示或带宽状态写成已验证。

[watchdog 记录](WATCHDOG-RECOVERY.md#内置驱动配置对象编译通过恢复仍未验收) 中的独立配置片段仅将 `QCOM_WDT=m` 改为 `y`，Kconfig 合并、olddefconfig 和 ARM64 `drivers/watchdog/qcom-wdt.o` 编译均退出 0；自动能力符号另新增 `CONFIG_CC_CAN_LINK=y`。证据在 `out/early-watchdog-20261027-1/{manifest.json,build.log,config-review.json}`。这只是对象编译范围；`WATCHDOG_HANDLE_BOOT_ENABLED=y`、`WATCHDOG_OPEN_TIMEOUT=0` 保持原值，后者可让 core 在用户态首次打开前持续发送心跳，即使用户态卡住也可能不复位。驱动 probe 前挂死、接管成功、有限期限与复位去向均未验收，不能称自动恢复已解决。

本轮未访问设备，未生成完整 Image 或 boot 镜像，未修改 DTS、timeout 或外部源码，未运行或等待 CI。主线 Linux / Omarchy、USB、显示、pstore / console / EP2 读回及早期挂死恢复仍未验收。此前一次 pmsg 校准的写入与正常 reboot 成功、读回失败，以及更早的审批阻塞均保留为各自历史状态；本轮离线实施不构成重试或新候选实验的授权。

## 2026-10-27：对照其他手机的主线 Linux 与桌面移植

通过网页发现项目，再用 gh 核对固定提交、实际补丁、设备树、构建配方和调试记录，并行调查同 SM8150、相邻代 LG 和其他手机移植。形成 [公开移植对照](MAINLINE-PORTING-RESEARCH.md)，重点核查 OnePlus 7T Pro 的 ABL 直接启动与 USB SMMU 调试，以及 OnePlus 7 Pro 的 Arch Linux ARM、Hyprland 和共享手机交互层。完整 Omarchy/theme-repository 安装在后者仍属未来工作；这些外部作者记录不是本会话 G8 真机验收。

研究支持先离线核对 LG 的 ABL/DTB/DTBO 契约、完整固件保留内存并集、bootloader framebuffer 的 clock/interconnect，以及 USB 的 SMMU 连接和运行域。Hotdog 早期入口补丁关闭 APSS watchdog，后续补丁还恢复过早期移除的 UFS SMMU，不能抽取早期 workaround 当最终方案。地址、电源、板号与 SID 均需核对 LG 实际板型。

本次未执行外部刷写脚本、联系维护者或操作手机，没有构建新内核/启动镜像、重跑 pmsg 校准或部署快照 helper。G8 主线 Linux / Omarchy、日志读回与早期挂死恢复仍未验收，既有设备实验前提不变。

## 2026-10-27：实现只读快照 helper，明确 init 调查范围

后续只读采集获得 179 个文件、171 个 rc，采集前后为同一次 Android 启动，逐项长度和 SHA-256 复核通过。核实实际 logcatd 为 shell wrapper，不添加 `-c`；当前属性和静态路径不能证明校准启动时执行或删除成功。唯一显式 MISSING 为 `/init.qcom.rc`，但导入集合不完整：另有七个未采 import、APEX 顶层 rc 和更早服务/厂商脚本缺口，不能把未采集当作不存在，也不能宣称观测点早于全部消费者。

新增 [pstore 快照 helper](../../tools/lg-g8/PSTORE-SNAPSHOT.md)，固定只读 `/sys/fs/pstore`、排他创建 `/dev` tmpfs 新目录，限量保存实际成功复制的内容、摘要及状态。13 项主机测试通过，覆盖链接/覆盖保护、数量/容量边界和故障路径；故障注入发现并修复了先保存 7 字节后 ENOSPC 时计数与摘要仍报告空内容的错误。成功采集需联合核对进程退出码、状态、manifest 与回收后的长度/摘要；5 秒只是 fork 后监督预算，超时 worker 也未必立即退出，不构成完整启动期限或恢复保证。

NDK r30 以 API 34 目标静态交叉构建通过，无 PT_INTERP/PT_DYNAMIC。API 37 note 已追到公共预编译启动对象；android21/28/34 对照使用同一公共对象和 libc.a，不能由目标数字推出 Android 14 运行兼容性。源码/测试/产物指纹和具体限制见工具说明，调查与验证材料保存在私有目录。

本次仅有系统文件只读采集和本机实现/验证，没有上传或执行 helper、部署 init、生成启动镜像、新增 pmsg 写入或 reboot。后续仍需补规则、权限、执行位置与生命周期，再形成具体实验材料；原一次性校准授权已经用完。主线 Linux / Omarchy、console / EP2、裸 PSCI 和早期挂死恢复仍未验收。以下各节保留当时的设计和验证状态。

## 2026-10-27：核对实际 pstore 消费者并设计更早快照

在校准后的同一次 Android 启动中，只读采集实际 `services.jar`、三份 init 规则、`logcat`、`logd` 和 `liblog.so`，七份文件的长度与 SHA-256 均核对通过；三个 DEX 的 checksum 和反汇编检查通过。BootReceiver 已查归档路径没有直接删除源文件，直接 `File.delete()` 目标是临时 tombstone 和 shutdown metrics，不能归因为 pmsg 删除。

实际原生组件的普通 `PmsgRead` 以只读方式打开旧 pmsg，已查读取/关闭函数没有直接 unlink；logd 启动时存在可达的旧事件标签读取链。`logcat -L -c` 有显式删除 pmsg 的分支，但已采集规则中的 logcatd 服务和 clear action 分别缺少 `-c`、`-L`，不能拼成一次删除。关键机器码、符号和路径已独立复核，仍无本次实际删除的证据，亦未穷尽其他消费者。

已确认 pstore mount 在同一 `on init` 中早于首次 `start logd`，据此设计挂载后同步采集到启动期 tmpfs、限时限量、同次启动回收并对照的方案。完整导入规则、更早服务、logcatd 实体、SELinux 和暂存生命周期仍待核对；方案没有实现或部署。文件指纹、静态地址、快照设计与私有证据位置见 [校准记录](PMSG-CALIBRATION.md)。本次调查仅有系统文件只读采集和离线分析，没有新增设备写入、reboot、fastboot 往返或候选启动；主线 Linux / Omarchy、console / EP2 和早期挂死恢复仍未验收。

## 2026-10-27：一次 pmsg 完整写入及正常 reboot 成功，读回失败

在 `e1075ba633c4b502d75a9b28eec178dce1131b8b` 上执行明确授权的一次校准，私有脚本及实际 helper 均核对身份。helper 经 `/dev/pmsg0` 完整追加 63 字节，正常 reboot 后 boot_id 改变，Android 启动完成、root 可用、槽位 b、内核及 boot_b 哈希符合预期；前后 pstore 均为空，payload 出现次数为零，脚本退出 1。最早未满足的是读回条件，不是写入或返回 Android。约 40.6 秒为整个脚本运行时间，不是独立 reboot 耗时。

同一次启动的后续只读检查核对 ramoops 参数、厂商旧日志保存/导出/删除路径、init 权限规则和系统启动归档元数据。未找到本次 pmsg 搬运或删除证据，但不能排除早于采集的清理，也不能确定内容在 reboot 中丢失。参考源码身份、脚本/helper 哈希、已安装分区哈希、私有证据路径与限制统一记在 [校准记录](PMSG-CALIBRATION.md)，不提交原始敏感日志。

此前审批阻塞保留为历史，当前阻塞改为日志读回链路未建立。没有重试写入或 reboot，没有刷分区、换槽、构建或启动新候选；设备最后实测为此次 reboot 后的同一 Android 启动，无后台设备任务。唯一下一步是核对实际运行版本的 pstore 消费实现，并设计消费者之前的只读采集以区分内核未导出与用户态清理。主线 Linux / Omarchy、console / EP2、裸 PSCI reset 与早期挂死恢复仍未验收。以下各节的未执行和审批状态仅描述各自历史时点。

## 2026-10-27：补齐 EP2 有序写入前缀验证

在 `4dd141d` 后的本地工作树扩展验证器，Unicorn 2.1.4 执行既有 halt / PSCI 探针机器码，每个 console store 元素后重放影子内存并由独立 codec 检查；下一指令边界核对整个 console 的实际模拟内存。两种既有 Image 各通过 17 CPU / 12 codec；其中 halt 的 9 个、PSCI 的 7 个完整路径各检查 103 个快照（62 次拒绝、40 次有效 ENTER、1 次有效 ENTER+TARGET），完成 100 次指令边界内存核对。覆盖两条记录的 body、CRC、reserved 和 header，包括被拒绝的 TARGET header 半更新状态 `(0x43474244, 272, 136)`。所有接受结果逐字段匹配预期捕获状态。

新报告为 `out/entry-probe/ep2-halt-2/cpu-store-prefix-v2.json` 与 `out/entry-probe/ep2-psci-2/cpu-store-prefix-v2.json`，均记录验证器 SHA-256 `fecfccdf902049e2e19ee5ab441f10e2032a33176b96a2eb9aa540dfeac9621a`；Image 和报告指纹见 [探针说明](../../tools/lg-g8/local-sim/entry-probe/README.md#有序写入前缀检查)。旧 Image、`cpu.json` 和 v1 报告保留。

模型假设每个 store 元素完整且按 hook 顺序可见，STP 两个元素间的状态由重放合成；初始 console 为 `0xcc`，未新增旧有效记录等初始内容场景。不模拟硬件缓存一致性、缓存行持久化、撕裂写入、断电、真实固件 reset 或 reset 后 RAM 保留，不能作为断电一致性或自动恢复验收。

本次没有构建新 Image / 启动镜像或访问手机。主线 Linux / Omarchy、console 保留和早期挂死恢复仍未验收；唯一下一项设备实验仍是受此前自动审批拒绝阻塞的一次性 pmsg 校准，需要指定操作的明确批准。最新已知手机状态仍为既有 fastboot 往返后返回 Android，本次未重新测量，无后台设备任务。

## 2026-10-26：采集现存内核文件指纹

新增只读采集工具，记录显式指定文件的路径、大小和 SHA-256，并可与既有 handoff manifest 比较 Image / DTB。工具拒绝覆盖文件或链接、拒绝非普通输入，以 `0600` 创建报告；仓库根 `private/` 已加入忽略规则。六项本机边界测试通过。

实际采集了 13 个现存文件；当前 tiny Image 和历史候选 DTB 均匹配 `lg-native-3` manifest 声明。当前配置和选定源码只获得本次文件身份，没有被追认为历史构建输入。旧 manifest 未改，报告仅保存在私有目录，哈希与证据位置见 [文件快照记录](WATCHDOG-RECOVERY.md#现存文件快照不追认历史构建来源)。

本次未访问手机、构建内核或生成候选镜像。主线 Linux / Omarchy、日志保留与早期挂死恢复仍未验收；下一项设备实验仍是被此前自动审批阻塞的一次性 pmsg 校准，最新已知手机状态为既有往返后返回 Android，本次未重新测量，无后台设备任务。

## 2026-10-26：补齐 ABL 启用看门狗后的静态交接行为

在已有 `42dbd0d` 记录基础上，沿用同一组已记录哈希的 LinuxLoader 本地材料，补查启用后的缓存/MMU 处理、BootStats 变量查询与条件安全调用。入口参数准备已追至 SystemTable+`0x58` 的 RuntimeServices 初始化；查询布局及 `gRT` 字符串支持 BootStats 的接口识别。缓存封装读取 DAIF 后测试位 1，不能按预期名称将其解释为正确保存和恢复 IRQ/FIQ 状态。

`GetVmData` 成功路径可以置位控制 SMC 的 flag，不能用镜像初值排除调用；SMC 首参数为零扩展的 `0x0000000086000000`。日志只表明关闭 Hyp UART 日志的意图，动态接口与安全固件行为仍未知。代码位置与限制见 [交接子调用证据](WATCHDOG-RECOVERY.md#启用后的交接子调用已识别行为与边界)。这些补证没有证明最终 watchdog 状态、失败候选的真实路径或超时恢复去向，无人值守迭代仍未通过验收。

本次只做本地取证和文档更新，没有访问手机、修改固件或构建候选。一次性 pmsg 校准仍受此前自动审批拒绝阻塞；最新已知设备状态仍是既有往返完成后返回 Android，本次未重新测量，无后台设备任务。

## 2026-10-26：明确看门狗交接证据与恢复缺口

只读本地调查确认 stock LinuxLoader 的成功路径在 `ExitBootServices` 返回后显式配置并启用 APSS watchdog，因此仅追问是否继承 Android 看门狗不足以描述启动链。尚未证明失败候选经过该路径、最终 Linux 入口的硬件状态或超时复位后的启动去向。

实际 `lg-native-3` initramfs 没有内核模块，120 秒 reboot 仅在 `/probe-init` 执行后启动；当前 tiny / v50 配置中的高通驱动为模块，但配置没有被候选 manifest 锁定为构建输入。将驱动改为内置仍不能证明接管前挂死可恢复。文件指纹、代码位置及限制集中记录在 [看门狗与自动恢复证据](WATCHDOG-RECOVERY.md)，临时计时器的退出条件记入 [技术债](TECHNICAL-DEBT.md)。

本次未访问手机、修改配置、构建镜像或启动候选。软件 fastboot 往返的既有验收继续有效；pmsg 校准仍受此前自动审批拒绝阻塞。无人值守候选迭代与主线 Linux / Omarchy 真机验收均未完成。
