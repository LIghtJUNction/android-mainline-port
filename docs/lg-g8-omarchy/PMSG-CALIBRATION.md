# Android pmsg 保留与读回校准

## 当前结论

2026-10-27，在集成仓 `e1075ba633c4b502d75a9b28eec178dce1131b8b` 上执行已明确批准的一次校准：静态 AArch64 helper 经 `/dev/pmsg0` 完整追加 63 字节，正常 Android reboot 后返回 Android，但重启后的 pstore 目录为空。校准为**真机失败**，最早未满足的验收条件是日志读回；尚不能区分内容未保留、启动时记录未被识别或导出，以及采集前被用户态删除。

没有重试写入或重启，没有刷分区、换槽、构建或启动新候选。pmsg 结果不能判断主线 Linux 是否执行，也不验收 console / EP2、裸 PSCI reset 或早期挂死恢复。

## 实验身份、判据与结果

此前自动审批在命令启动前拒绝操作，属于当时的审批阻塞。用户随后明确批准指定设备上的一次 helper 上传/root 执行、一次 pmsg 追加、一次正常 reboot 和返回后的读回；本次执行已取代“尚未执行”的当前状态，历史拒绝不删除。

私有脚本为主工作区 `tools/calibrate-pmsg.py`，SHA-256 为 `b86a84f468966d8b6fa1cb23d2630551c1a3929fabfbe92af35c14bc4bd72def`。实际上传 helper 的 SHA-256 为 `79dc6d4df4128a9aaf09331a6b0438c895669b13d65be57d8f1cbe475071dd99`。私有 plan 在执行前记录唯一 nonce、payload 字节和哈希、镜像身份、240 秒共同期限与恢复路线；输出目录拒绝覆盖，写入失败或短写不重试。恢复使用本机统一 Backups 中已校验的原始 boot_b。

| 检查 | 实际结果 |
| --- | --- |
| 目标及传输前置核对 | 指定真机身份、shell-v2、root 退出码 37 和六字节二进制传输检查通过；恢复备份和设备状态满足脚本前置条件。 |
| helper 传输 | 长度与 SHA-256 匹配计划。 |
| pmsg 追加 | 仅一次 write，返回值以 8 字节小端有符号整数传回，值为 63，等于完整 payload 长度；stderr 为 0 字节。 |
| 正常 Android reboot | before/after boot_id 不同，boot_completed=1；model=`LM-G820`、slot=`_b`、root uid=0、kernel=`4.14.348-openela-perf+`。 |
| 已安装启动分区 | 返回后 boot_b SHA-256 仍为 `fec418e5227a31927af27daf71d2225246f5adcf8dda38b5856200760b5205cd`；Android reboot 修复源码提交为 `ea69a25767dd605c9d1b43256ed34299ddd25220`。 |
| 证据完整性 | before/after manifest 中记录的文件长度与 SHA-256 核对通过；两次 pstore 文件数均为 0。 |
| 读回判据 | 要求 `pmsg-ramoops-*` 中完整新 payload 恰好一次；实际 `occurrences=[]`、`passed=false`，脚本退出码 1。 |

包装程序时间为 `2026-10-27T00:33:05.422136+08:00` 至 `2026-10-27T00:33:46.039530+08:00`，约 40.6 秒是整个脚本的运行时间，不是单独测得的 reboot 时长。错误为 `complete payload did not appear exactly once; calibration inconclusive`。

## 只读定位及证据边界

校准后的同一次启动中再次核对 boot_id、Android 状态及 pstore；目录仍空，`/dev/pmsg0` 为字符节点。带名称读取的 ramoops 参数为 console_size=`262144`、ftrace_size=`262144`、pmsg_size=`2097152`、mem_address=`2952790016`（`0xb0000000`）、mem_size=`2621440`（`0x280000`）、mem_type=`0`、record_size=`4096`、ecc=`0`、dump_oops=`1`。按厂商分配顺序，pmsg 位于 `[0xb0080000,0xb0280000)`。

该次 after/dmesg 记录 `console [pstore0] enabled`、`pstore: Registered ramoops as persistent store backend` 和 `ramoops: attached 0x280000@0xb0000000, ecc: 0/0`。这些证明后端注册及 attach，不能证明旧内容保留或成功导出；有效/坏签名等部分初始化诊断属于 `pr_debug`，没有相应输出不能排除分支。

只读从厂商 Git 提交 `d4f335e13d73c4aa2af85581a6ec11b670ee5b94` 提取源码；参考仓 HEAD 同为该提交，文件未展开到稀疏工作树，也未修改参考仓。静态路径如下：

- `ram_core.c:483` 的 `persistent_ram_post_init()` 检查签名和 start/size；有效非空记录经 `persistent_ram_save_old()` 复制到 `old_log`。无效记录重新设签名并 zap，空记录不保存旧日志；内存分配失败也会缺少旧日志。
- `ram.c:638` 的 `ramoops_init_prz()` 在 `persistent_ram_new()` 返回后 zap 当前环形头部。pmsg 调用此路径，但它在保存旧日志之后执行；不能单凭这个 zap 解释导出为空。
- `ram.c:241` 的读路径从旧 pmsg 取内容，`ram_core.c:283` 的旧日志保存不会删除 pstore 文件。`inode.c:183` 的 unlink 路径调用 backend erase；`ram.c:484` 对 pmsg erase 会同时释放旧日志并 zap 当前环形头部。后者提供一种可能路径，没有证明本次发生了 unlink。

| 提取文件 | SHA-256 |
| --- | --- |
| `fs/pstore/ram.c` | `7cb32bc0cd428e274e3d4ea4682cc7d2a2cd5ff08b21fb6e0c738f786efb5492` |
| `fs/pstore/ram_core.c` | `03c673bb78849bbfb52ae86ccea9ed28fd9621317466fa9d33eb2e46d3103e83` |
| `fs/pstore/inode.c` | `a9f4451b9601f74cc658b747b5c7de781b011ed914dde34eeea8a327323feeaa` |
| `fs/pstore/platform.c` | `97b30b78bb975898f5113578aff650e2c96e6e80ef4de9e4ad42b7173ee8b223` |

这些是参考源码身份，不是对已安装内核机器码的等价认证。

定向扫描 init 规则，仅找到 `/system/etc/init/hw/init.rc` 的 pstore mount、chown、chmod 和 `/proc/last_kmsg` 权限设置，没有发现删除语句。该多目录扫描返回 1，部分预设目录不存在；不能据此排除其他消费者或清理程序。常见 `/data/misc/pstore`、`/data/dontpanic` 路径未列出文件。

只读查看系统启动日志归档元数据：DropBox 有本次 `2026-10-27 00:33` 的 `SYSTEM_BOOT`，最新 `SYSTEM_LAST_KMSG` 为 `2026-10-26 16:31`，没有本次对应的该类归档。BootReceiver 时间戳 XML 有旧 console 条目，没有 pmsg 条目；定向 logcat 未发现 pmsg 搬运或删除证据。BootReceiver 的 trace_pipe ENOENT 不证明 pmsg 处理失败。这些检查只覆盖已查位置与现存日志，不能排除其他去向、未记录的删除、配额淘汰或早于采集的行为。没有读取无关应用 DropBox 内容。

## 实际 Android 消费者与启动顺序

2026-10-27 00:54 左右，在校准后同一次 Android 启动中只读采集七个明确指定的系统文件；采集前后 boot_id 相同。设备传输校验及离线重算的长度、SHA-256 均匹配。原文和反汇编留在私有目录，下面仅列文件身份与结论。

| 系统文件 | 字节数 | SHA-256 |
| --- | --- | --- |
| `/system/framework/services.jar` | 21181871 | `8d2712f75fb539246e8aafd46a4b6db839a722a9bac76381eea2f0f51a133c10` |
| `/system/etc/init/hw/init.rc` | 58173 | `78b459c187690a5beee343d48fbe9313b68e9991ca231273afdc343da8e00284` |
| `/system/etc/init/logd.rc` | 1006 | `26378c0ea5f8d424616760c16522d6c3f300daaca75929720dd6b8383cab6e56` |
| `/system/etc/init/logcatd.rc` | 2495 | `bbc8cfbc09753346af822ccb99b9c5160f4235537043c593fd925052d47b0295` |
| `/system/bin/logcat` | 174544 | `7426b1be6b47f8225625deeb9beab64af5b3edfeab4d98695cf18e6b14f9267e` |
| `/system/bin/logd` | 678320 | `7cb1a450e018e7acd47d05fe0298e42672289136d674ef1b8a5726d20e47c703` |
| `/system/lib64/liblog.so` | 63032 | `b3cd12ed15930737a5d0c784fa3dbe141909660d1275f58b9c4cd8dfca8e5f55` |

### BootReceiver：已核对的归档和直接删除目标

`services.jar` 中三个 DEX 均通过 `dexdump -c` checksum 校验和 `dexdump -d` 反汇编。实际 `BootReceiver.logBootEvents()` 在首次启动分支归档 `/proc/last_kmsg`、`/sys/fs/pstore/console-ramoops` 和 `console-ramoops-0`，tag 为 `SYSTEM_LAST_KMSG`。调用链为 `addLastkToDropBox()` → `addFileWithFootersToDropBox()` → `FileUtils.readTextFile()` → `addTextToDropBox()`，受 DropBox tag 是否启用、文件时间戳等条件控制。

主体 DEX 中对应地址为 `0x1fbb0e–0x1fbb3a` 的路径及归档调用，`0x1fb74c` 的归档封装调用，以及 `0x1fb526` 的文本读取。这些是离线 DEX 地址，不是手机运行时 PC。所查归档方法没有直接删除源文件的调用，也没有在已查字面量中找到 pmsg 路径；不能据此排除动态路径、其他模块或间接调用。

主体中的直接 `File.delete()` 调用分别清理 `addAugmentedProtoToDropbox()` 创建的临时 tombstone 文件（`0x1fb3f6`、`0x1fb478`）和 `logSystemServerShutdownTimeMetrics()` 的 `/data/system/shutdown-metrics.txt`（`0x1fc080`）。不能把这些调用归因为 pstore 删除；本次也没有 BootReceiver 删除 pmsg 的执行证据。

### 原生组件：普通读取和显式删除是独立分支

已离线分析上表中的实际 `logcat`、`logd` 和 `liblog.so`，并独立复核关键分支、路径字符串与 mini-debug 符号；没有执行这些设备二进制。下列地址均为 ELF 虚拟地址，不能直接当作手机运行时 PC。两个可执行文件各含自己的 `PmsgRead`，动态依赖表未列出 `liblog.so`，因此分别核对，不能只用共享库代表它们。

- **直接删除需要明确的参数组合。** `logcat` 的 `-L` 分支在 `0x10984–0x1098c` 设置 pstore 模式，`-c` 在 `0x11610` 设置 clear 状态。`0x12028–0x12064` 检查模式、排除不兼容选项并要求 clear；同时指定 `-f` 会在 `0x1207c` 调用 `error(1, ...)` 退出。合法的 `-L -c` 调用在 `0x12088` 对 `/sys/fs/pstore/pmsg-ramoops-0` 调用 `unlink`，随后退出，未检查该调用返回值。静态存在这条路径不能证明本次执行过，更不能证明删除成功。
- **普通读取没有发现直接删除。** 三份 ELF 的 `PmsgRead` 分别位于 `0x256fc`、`0x3d3ec`、`0xb41c`，均以 `O_RDONLY|O_CLOEXEC`（`0x80000`）打开上述 pmsg 文件，之后读取、定位并由 `PmsgClose` 关闭；这些已查函数没有直接 `unlink`。共享库 `0xb764–0xb770` 在无剩余 header 时返回 `-EAGAIN`，不完整 header 返回 `-EIO`；读取结束不能当作删除证据。共享库文件读取封装还存在外部回调，其副作用未排除。
- **logd 启动时存在可达的读取链。** `main.cfi` 在 `0x17878` 调用 `LogTags::LogTags()`，构造函数在 `0x33c90` 调用 `ReadPersistEventLogTags()`。后者分配 mode=`0x80000800` 的 logger list，尝试打开 log ID 2、6，再在 `0x33910` 调用读取接口，进入自己的 `PmsgRead`。分配失败、两个 logger 均未打开或读取返回非正值会跳过或结束；这证明代码可达，没有证明本次成功读到了旧日志。
- **其他 clear 不能混同。** `-c -f` 及旧 `--id` 成功读出且发生变化时的输出清理针对输出文件和轮转文件；ID 变化不补足 pstore 删除所需的 `-c`。`android_logger_clear` 发送 `clear %u` 到 logd 控制 socket，也不是上述 pstore `unlink`。

尚缺本次实际执行轨迹、调用参数和删除结果，也未穷尽其他组件、动态依赖与内核行为，不能据此将空 pstore 归因于用户态。

### init：pstore 挂载早于首次 logd 启动

下列行号属于上表实际采集的 `init.rc`：

| 位置 | 行为 | 所属 action |
| --- | --- | --- |
| 437 | `mount pstore pstore /sys/fs/pstore nodev noexec nosuid` | 行 101 的 `on init` |
| 438–445 | pstore 目录和 console/pmsg 文件的 chown、chmod | 同一 `on init` |
| 477 | 首次 `start logd` | 同一 `on init` |
| 744、746–747 | 加载持久属性，`start logd`、`start logd-reinit` | 行 683 的 `on post-fs-data` |
| 1204、1206 | `class_start hal`、`class_start core` | `on boot` |
| 1208–1210 | `class_start main`、`class_start late_start` | `on nonencrypted` |

`logcatd.rc:55` 的服务命令含 `-L`，但不含 `-c`；其启动受 logpersist 属性条件控制。`logcatd.rc:42` 的 clear action 含 `-c -f /data/misc/logd/logcat`，不含 `-L`。两者是不同调用，不能组合成已发生的 pstore 清除。`/system/bin/logcatd` 实体及本次 logpersist 属性和服务执行状态尚未核实，不能将上述 `logcat` 实现直接视为该服务实际执行的实现；原生读取与显式删除的静态核对结果见上一节。

该顺序提供了早于已知 `start logd` 的候选观测位置。主 init 还导入硬件、vendor、USB 和 zygote 规则；`early-init` 中已有 `ueventd`、`apexd-bootstrap` 等执行点。尚未核对完整导入集合和所有更早服务，不能宣布该位置早于全部消费者。

## 更早只读观测方案（设计，未实施）

目标是在 pstore 首次挂载后立即保全内核已导出的文件，再与 Android 返回后的状态比较。它只能观测内核导出之后的链路；即使早期快照为空，也不能直接证明 DRAM 内容丢失。

1. **先补齐启动条件。** 只读核对实际展开的 init 导入集合、其中更早启动的服务、`logcatd` 的实体身份与属性触发状态。确认选定 action 的执行顺序、helper 可执行位置和 SELinux 权限后，再准备有明确字节差异的实施材料。此处还没有可部署的 init 修改。
2. **在挂载后同步采集。** 候选位置是上述 `mount pstore` 之后、首次 `start logd` 之前，优先紧接挂载语句。拟使用 init 等待结束的同步 helper，不能用异步 service 或 `boot_completed` 采集代替。必须验证挂载成功及文件系统类型；挂载失败单独记错，不能伪装成空目录。
3. **暂存于启动期内存。** 此时不依赖 `/data`。拟在经确认可用的 `/dev` tmpfs 中创建本次专用、root 所有的 `0700` 目录，文件以 `0600`、排他创建；该存储的实际挂载、标签及后续生命周期仍待核实。不沿用固定旧目录，不覆盖历史证据，不修改 pstore 权限，不 unlink、不写入任何 pstore 文件或活跃 console。
4. **限制采集并记录结果。** helper 只接收确定的 pstore 挂载点及新输出目录；源端不跟随链接，拒绝非普通文件及目录遍历。拟定上限为 32 个文件、单文件 4 MiB、总计 8 MiB，父进程以单调时钟限制整个步骤为 5 秒；超限或超时标记不完整。逐项记录名称、读取长度、SHA-256、错误及采集起止时间，保留 mount 信息和私有 boot_id。枚举失败、读取失败、零字节文件和成功枚举后零文件须区分；只有完整采集才能用于“早期为空”的判断。超时或 helper 失败后继续正常启动，不能为取证无限阻塞 Android。
5. **同一次启动回收对照。** Android/ADB 返回后先取走早期快照，再采集一次当前 pstore；核对 boot_id 一致、helper 完成状态，以及主机和设备端每个文件的长度和哈希。先保存在新私有目录并验证，再处理设备端临时输出；不自动 reboot 或重试写入。tmpfs 不跨再次重启保留，若本次 Android 未返回，该快照可能无法回收，须如实记为无结论。

判读边界：早期文件中存在完整新 payload，而稍后源文件消失，可以把变化缩小到两次采集之间，但还需执行证据归因具体消费者；文件存在而 payload 不在，继续查旧记录内容及导出链；挂载成功、完整早期快照为空，只能说明该时点没有可见导出文件，仍不能区分保留、头部识别、旧日志保存、导出失败或更早清理。无论哪一项都不验收 console / EP2 或早期挂死恢复。

该方案需要改变启动期执行路径，当前只完成设计，没有部署、构建新启动镜像或新增设备重启。实施前还需完成上述条件核对、helper 的本机边界验证，以及该次启动修改和实验的明确授权；此前一次 pmsg 写入/reboot 的授权已经用于原校准。

## 私有证据

以下路径相对主工作区 `/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy`，均不提交：

- `audit/pmsg-calibration-20261026-once-1/`：预先指定的输出目录沿用日期名，实际执行日期为 2026-10-27。包含 plan、payload、writer、传输检查、write 返回、reboot intent、before/after manifest 与日志、result。`result.json` SHA-256 为 `aaf9a917c22057254e44b1e99e4c84ef11c3f228d4539c7b081eaa1ffc6bf429`。
- `private/pmsg-calibration-20261027-once-1/`：包装执行记录与 stdout/stderr，记录开始、结束、脚本身份和退出码。
- `private/pmsg-readback-diagnosis-20261027-once-1/`：只读命令结果、源码摘取、BootReceiver 日志及启动归档元数据；`report.json` 与 `followup-report.json` 记录查询退出码、时间和输出哈希。最初两项扫描返回 1 的情况保留，后续定向元数据查询均返回 0。
- `private/android-pstore-consumers-20261027-readonly-1/`：七份实际系统文件与采集 manifest；`dex-offline-1/` 保存三个 DEX 的校验、反汇编及 BootReceiver 摘录，`native-analysis-1/` 保存三个 ELF 的符号、反汇编、动态依赖及 mini-debug 分析。采集前后为同一次 Android 启动，原始材料均不提交。

## 下一步与停止边界

实际 BootReceiver、原生读取/显式删除和已采集 init 顺序的静态调查已完成上述范围，更早快照只完成设计。下一步补齐实际 init 导入集合与更早服务、logcatd 实体及触发条件，确认 helper 的运行权限、暂存生命周期并完成本机边界验证，形成可审查的启动修改与单次实验材料。现有证据仍不能定位首次丢失位置；新增实验须预先记录单一变量、预期、期限和恢复路径。此次一次性校准不自动扩展为第二次写入或 reboot。

设备最后一次实测仍为同一轮 reboot 后的 Android，启动完成、root 可用、槽位 b、内核符合预期；没有后台设备实验。console / EP2 读回和抵达 reset 前的挂死恢复仍需独立证据，不发送现有 EP2，也不原样重试失败的 `lg-native-3`。
