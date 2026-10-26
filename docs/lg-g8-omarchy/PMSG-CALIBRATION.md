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

## 私有证据

以下路径相对主工作区 `/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy`，均不提交：

- `audit/pmsg-calibration-20261026-once-1/`：预先指定的输出目录沿用日期名，实际执行日期为 2026-10-27。包含 plan、payload、writer、传输检查、write 返回、reboot intent、before/after manifest 与日志、result。`result.json` SHA-256 为 `aaf9a917c22057254e44b1e99e4c84ef11c3f228d4539c7b081eaa1ffc6bf429`。
- `private/pmsg-calibration-20261027-once-1/`：包装执行记录与 stdout/stderr，记录开始、结束、脚本身份和退出码。
- `private/pmsg-readback-diagnosis-20261027-once-1/`：只读命令结果、源码摘取、BootReceiver 日志及启动归档元数据；`report.json` 与 `followup-report.json` 记录查询退出码、时间和输出哈希。最初两项扫描返回 1 的情况保留，后续定向元数据查询均返回 0。

## 下一步与停止边界

先核对实际运行 Android 版本中的 pstore 消费和删除实现，并设计在这些消费者之前采集旧日志的只读观测点，以区分内核未导出与用户态清理。参考源码和归档元数据尚不能定位实际首次丢失位置；若仍需新增实验，必须先记录单一变量、预期、期限和恢复路径。此次一次性校准不自动扩展为第二次写入或 reboot。

设备最后一次实测仍为同一轮 reboot 后的 Android，启动完成、root 可用、槽位 b、内核符合预期；没有后台设备实验。console / EP2 读回和抵达 reset 前的挂死恢复仍需独立证据，不发送现有 EP2，也不原样重试失败的 `lg-native-3`。
