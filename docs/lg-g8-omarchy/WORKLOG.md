# LG G8 调试结论变更

按结论变化记录，当前验收与下一项设备实验以 [STATUS.md](STATUS.md#当前验收与下一步) 为准。原始设备资料留在私有工作区，不提交。

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
