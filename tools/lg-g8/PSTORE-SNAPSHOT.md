# 启动期 pstore 只读快照 helper

[pstore-snapshot.c](pstore-snapshot.c) 用于复制内核已经导出的 `/sys/fs/pstore` 文件，候选接入点为 pstore 挂载后、已知首次 `start logd` 前的同步 init 命令。2026-10-27 已在 LG G8 当前 Android 14 的 Magisk 域中完成一次运行冒烟，返回完整空快照；这仅验证该运行环境与采集时点，未部署 init，也未验收早期启动权限、执行顺序或启动恢复。调查背景见 [pmsg 校准记录](../../docs/lg-g8-omarchy/PMSG-CALIBRATION.md)。

## 接口与输出

正式构建仅接受 `--out /dev/<本次唯一目录名>`。源路径固定为 `/sys/fs/pstore`，检查源文件系统为 pstore；输出的直接父目录必须为 `/dev`，并检查 tmpfs 和有效 UID 0。逐级打开目录，不跟随祖先或条目链接，拒绝 `.`、`..` 和非普通源文件。新输出目录以 `0700` 创建，所有文件以 `0600` 排他创建；已有目录或链接使操作失败，不覆盖历史证据。

输出包含：

- `status.json`：父进程写入的 schema version、state、complete、child_exit 及单调时钟起止毫秒。
- `manifest.jsonl`：子进程逐行记录 begin、源文件系统、文件结果、错误及 end。源文件名使用原始字节的十六进制 `name_hex`，不假设 UTF-8；`stored` 为本地文件名或 null。
- `record-000.bin` 等：实际复制的内容；每条 manifest 记录保存字节数、SHA-256、status 和 errno。短写后失败时，计数和摘要对应实际成功写入的前缀；读取、输出或关闭失败仍判为不完整。未创建输出文件时 `stored=null`，不能把空内容摘要当作读到了空源文件。
- `mountinfo`、`boot_id`：有界复制的 `/proc/self/mountinfo` 和 `/proc/sys/kernel/random/boot_id`，仅保存在私有证据中。单调时间不能跨 reboot 直接比较。

工具不修改 pstore 权限、不 unlink、不写入 pstore、pmsg 或活跃 console，不触发 reboot。复制前后比较文件的 dev、ino、size、mtime、ctime，并检查原名称仍指向相同状态；这有助于发现源文件变化，不构成跨目录的原子快照，也不能保证检出所有并发修改或删除。

## 完整性和期限

最多处理 32 个源条目，单文件上限 4 MiB，记录内容总量上限 8 MiB；失败条目也计入条目数，部分成功写入也计入总字节。mountinfo 另限 256 KiB，boot_id 另限 128 字节。超限、枚举/读取/输出错误、源变化及元数据失败均不返回成功。

只有进程退出码 0、可解析且 `complete=true` 的 `status.json`、完整可解析的 manifest、末尾 `end.complete=true` 且所有文件结果正常，以及回收后长度和摘要复核全部成立，才能接受为完整采集。manifest 的末尾成功声明可能先于其最终写入/关闭错误，不能单独使用；状态文件也可能在最后关闭失败前写出了 true，必须结合进程退出码及回收校验。缺少报告、截断 JSON、非零退出、超时或任一错误均是不完整，保留部分文件但不能据此宣布早期 pstore 为空。

成功枚举后零条目与成功读取到一个零字节文件不同；源目录不可访问也不能当作空目录。完整空快照仅说明这个采集时点没有可见导出文件，不能证明 RAM 内容丢失、Linux 未执行或用户态进行了删除。

父进程使用单调时钟预算，默认 5000 ms，并以约 10 ms 间隔非阻塞检查子进程。时钟起点在 main 开始处，但只有 fork 后监督循环主动检查期限；参数/目录/输出设置、fork 前操作及最终状态 I/O 没有独立超时，因此这不是整个程序严格的 5 秒硬上限，调度及完成检查顺序也会带来误差。超时发送 SIGKILL，状态为 timeout/incomplete，不阻塞等待可能处于不可中断睡眠的子进程。SIGKILL 不保证该子进程立即退出；超时后部分输出可能仍变化。子进程设置父退出时的 SIGKILL 并核对父 PID。

init 集成必须等待 helper 完成，并核对失败处理后继续启动；异步 service 或 boot_completed 后复制不满足候选观测位置。内核或最终 I/O 卡死仍需独立恢复手段，本工具不提供这种恢复保证。

## 本机验证与 Android 构建

仓库根目录执行本机测试（TMPDIR 指向本次专用临时目录）：

```sh
python -I tools/lg-g8/local-sim/tests/test_pstore_snapshot.py -v
```

测试自行构建正式版、`SNAPSHOT_TESTING` 版及 LD_PRELOAD 故障注入库。测试版额外允许 `--test-source`、`--test-delay-ms`、`--test-timeout-ms`；正式版拒绝这些参数。2026-10-27 的 13 项测试通过，覆盖空目录、SHA-256 填充边界/二进制名称、权限、拒绝覆盖和链接、FIFO、数量与容量边界、读取/枚举/源变化、输出创建失败、短写/关闭失败、超时及正式参数限制。

测试曾发现真实错误：输出先写入 7 字节再报错时，manifest 原先报告 0 字节和空摘要。现按每次成功 write 更新计数与摘要，并保留最先发生的文件错误；该故障路径已通过回归。测试使用主机普通文件系统，不模拟 pstorefs 或 Android 的权限与启动行为。

本机交叉编译命令（`<输出文件>` 必须为新的本机路径）：

```sh
/opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android34-clang \
  -std=c11 -Wall -Wextra -Werror -Oz -static \
  tools/lg-g8/pstore-snapshot.c -o <输出文件>
```

NDK r30（`30.0.16248370`）构建成功，产物是静态 AArch64 ELF，没有 PT_INTERP/PT_DYNAMIC。修复后源码 SHA-256 为 `7809a459476b0a2c0f834c5f9cfcf812bd77048836bbbda31809317b2e5407cf`；对应本次产物为 `4823baaa36918dfcd2629af19fb6b4dfd0802d378e0a4c18046ad259376b1075`。这些只是本次字节身份，不承诺其他编译路径或工具链产生相同产物。

产物的 `.note.android.ident` 为 API 37，独立本机调查和链接 map 将其追到通用 `sysroot/usr/lib/aarch64-linux-android/crtbegin_static.o`；该对象自身就含同一 note，SHA-256 为 `54a4064274bc41313f005e4865bc03f10fccdffa801c8c6ba619b29f3709b9bc`。android21/28/34 对照选择同一通用静态启动对象和 libc.a；编译目标 API 数字不会重建这些预编译库。NDK 自带构建规则也对静态 executable 使用低于最高 API 的 APP_PLATFORM 给出构建警告，而非运行兼容承诺。

因此准确描述为“NDK r30 静态 Bionic 构建，编译目标 API 34，已在当前 LG G8 Android 14 的 Magisk 域中完成一次运行冒烟，通用运行兼容性未验收”。API 37 note 本身不能判定 Android 14 不兼容；下述真机结果验证了该产物在这一具体环境中的启动、worker 和空 pstorefs 枚举路径。静态链接去掉了设备动态链接器及共享 libc 依赖，仍依赖内核接口、启动信息和安全策略；未完整审核所有可达静态库启动路径，不能给出精确最低 Android/Linux 版本。修改或删除 note 不会改变 libc 实现。本机 ELF 检查或其他静态 GNU 构建也不能代替真机验收。

## 当前 Android 14 运行冒烟

2026-10-27 在当前已完成启动的 LG G8 Android 14 上，以 root、SELinux `u:r:magisk:s0` 执行上述摘要对应的现有正式产物，未重建。最初 `adb push` 因目标暂存目录属于 root 且权限为 `0700` 而报 Permission denied，失败发生在 helper 执行前；随后确认目标可执行文件和输出目录尚不存在，通过 root 命令的标准输入传输产物，设置执行权限为 `0700`，核对远端 SHA-256 与本机产物一致后，才进行首次且唯一一次 helper 执行。这是传输失败后的继续操作，没有重跑 helper。

执行设置了外层 15 秒 SIGKILL 超时，实际约 0.115 秒结束，helper 退出码为 `0`。`status.json` 为 `state=complete`、`complete=true`、`child_exit=0`；manifest 确认源为 pstorefs，末尾 `end.complete=true`、`entries=0`、`bytes=0`，无文件错误。这是该时点的完整空快照（0 条记录），没有验证非空记录的真机复制，也不能据此推断早期 pstore 或 RAM 状态。

同一次启动内回收了 `boot_id`、`mountinfo`、`manifest.jsonl`、`status.json` 四份文件，各自的本机与远端长度、SHA-256 及 `0600` 权限均复核一致；执行前、快照与执行后的 boot_id 一致。验证后清理了设备上的临时可执行文件、四份输出及两个临时目录，并检查路径已不存在。私有原始内容未写入仓库。

证据保存在本机私有目录 `/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/private/pstore-runtime-smoke-20261027-f1e537a0a46e2529/`：`result.json` 记录最初传输失败，`continuation-result.json` 记录继续传输、唯一一次执行、回收验证和清理，后者 SHA-256 为 `42f5471334379283b70c2db8daf1b6ac08ec432f1d8287523715bfbf6ffa5893`。

本次未部署 init、未写 pmsg、未 reboot，也未生成启动镜像。Magisk 域中的晚期运行成功不保证早期 init 域具有所需权限，不保证先于 logd 或其他消费者执行，也不证明跨 reboot 或失败启动后的恢复能力。

## 接入与回收尚待核对

需核对实际 init 导入/APEX/厂商脚本集合、更早消费者、执行文件位置和标签、域转换、pstore 枚举/读取权限，以及 `/dev` 创建权限与后续挂载生命周期。当前 root shell 为 Magisk 域，其读取成功不代表 init helper 获准；目录遍历使用 O_RDONLY 打开祖先，也不能只按 SELinux search 权限估计需求。当前 `/system/bin` overlay 身份不能直接代表最早启动时的文件身份。

暂存不依赖 `/data`，但 tmpfs 不跨 reboot。应在同一次 Android 启动内回收，核对 boot_id、helper 退出和报告状态、每个文件的长度及 SHA-256，再与晚期 pstore 对照。Android 未返回、目录消失或超时遗留 worker 未停止时，不能声称完整回收。早期有 payload、晚期消失只缩小变化窗口，具体消费者仍需执行证据。

当前已完成源码、本机测试、交叉构建，以及上述一次当前 Android 14 Magisk 域中的运行冒烟和完整空快照回收；没有部署 init、生成启动镜像、写入 pmsg 或新增 reboot。早期启动接入仍待验证。启动修改及新的设备实验仍需具体可审查材料、恢复前提和相应授权；一次性 pmsg 校准授权已用于原失败实验。
