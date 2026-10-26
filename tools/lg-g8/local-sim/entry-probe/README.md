# LG G8 最小交接探针

目标：分别取得 ABL 进入探针的 ENTER、复制后的目标地址实际执行的 TARGET，并保存原始 x0–x3、CurrentEL、SCTLR、DAIF 和实际代码地址。它不启动 Linux，不验收显示或 Omarchy。

## 路线与完成标准

1. 根据已校验 runtime FDT 核对 Android console 区域，独立实现 persistent RAM 记录编码、有效性检查和环形重组。
2. 编译位置无关 ARM64 探针，执行实际机器码，核对阶段、nonce、原始状态、复制结果、边界保护和 reset 请求。
3. 独立审查后，先设计 Android 写入→正常 warm reset→Android 读回的保留校准。读回需同时验证传输、记录格式、nonce 和内容；这项校准本身尚未完成。
4. 只有恢复方式和读回校准明确后才安排新 RAM 启动实验；每次实验使用新的 nonce，提前记录镜像哈希和预期。此工具不访问设备。

离线通过要求有效/空/满环/坏签名/start 越界/size 越界记录判定正确，并检测内容损坏、非法捕获状态及 ENTER/TARGET 不一致；halt 在 EL1/EL2/EL3、PSCI 在 EL1/EL2 的实际探针机器码保存输入并抵达目标地址，PSCI 在 EL3 必须于写日志前拒绝。所有数据读写检查自然对齐；包含实际设置 SCTLR.A=1、源 Image 仅按 4 字节或 8 字节对齐的场景。MMU-on 或代码/DTB 重叠时不能越界写目标。CPU 模拟中的 cache 操作不代表真机缓存一致性，SMC 拦截不代表固件接受 reset。

## 取证依据与边界

2026-10-26，源码起点 `ea69a25`。runtime FDT 的 SHA-256 为 `75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf`。`/reserved-memory/ramoops_region@b0000000` 保留 3 MiB；`/soc/ramoops` attach 2.5 MiB，record=0x1000、console=0x40000、ftrace=0x40000、pmsg=0x200000、ecc=0。按 vendor 分配顺序，dmesg 无剩余空间，console 位于 `[0xb0000000,0xb0040000)`。

旧 `lg-native-3` 的 mainline console 位于 `[0xb02c0000,0xb0300000)`，Android 不 attach 该区。它没有可归属的 pstore，不足以证明内核未执行，不能原样重试。

只读分析厂商内核提交 `d4f335e13d73c4aa2af85581a6ec11b670ee5b94` 的 `fs/pstore/ram_core.c`（blob `a323ed939aa85a4d3582b7285d8ecf83c4f302a7`）得到：12 字节小端头部为 sig/start/size，sig=`0x43474244`（console 类型的额外 sig=0），capacity=zone-size−12；size≤capacity 且 start≤size 才有效。有效非空数据在 post_init 中 save_old 后返回；无效记录才重新设签名并 zap。console 后续 attach 的 zap 与 post_init 是两步。本目录的 Python 是独立实现；厂商函数编译执行曾被自动审批拒绝，没有执行验证，不能称为与手机二进制等价。

实际 ABL 镜像 SHA-256 为 `bfcb70eaabf680e7e0a4e19b20a56fcac85c22991e8553d24ee4fefc993b8f7c`。其正常 ARM64 交接路径静态可见缓存维护、MMU disable、TLBI 和 x0=DTB/x1–x3=0；这不是失败实验的实际 PC 轨迹，不能由此断定失败时执行了该路径。

## 记录与恢复

记录格式为 `LGG8EP2\0`，每条 136 字节：128 字节 body、4 字节 IEEE CRC32、4 字节必须为零的 reserved。ENTER 包含 32 个十六进制字符的 nonce、原始 x0–x3、CurrentEL、SCTLR、DAIF、入口地址和源 stage2 地址；TARGET 包含同 nonce 和实际 stage2 PC。解码同时检查 CRC、状态语义、代码地址、阶段顺序及两条记录的共同字段。persistent RAM payload 从 console+12 开始，模板复制和 64 位字段拆成自然对齐的 32 位访问，CRC 按字节读取。先清理数据到 PoC，再提交 header 并清理 header。TARGET 追加到 ENTER 后，数据长度由 136 增至 272 字节，含头部总范围分别为 148 和 284 字节；突发断电仍可能留下部分提交，缺失或损坏记录必须标为无结论。

halt 支持 EL1/EL2/EL3，PSCI 仅支持 EL1/EL2；PSCI 在 EL3 写日志前拒绝，避免非法 SMC。两种变体均要求 SCTLR.M=0、SCTLR.EE=0，不修改 SCTLR。写入前检查代码范围与 console、传入 DTB（保守预留 2 MiB）不重叠，复制前检查目标与代码/DTB不重叠。目标固定为 `0xaa000000`，构建时必须通过 runtime RAM 和保留区检查。代码不使用栈，也不修改持久分区；console 的上次启动日志会被该实验覆盖，真机实验前须先采集旧日志。

默认 `--reset halt`，到 TARGET 后停在 WFE；它不是自动恢复方案，也不能直接作为无人值守启动候选。`--reset psci` 在提交 TARGET 后发出 `SMC SYSTEM_RESET (0x84000009)`，若返回则停在 WFE。runtime `/psci` 的 method=smc 只提供调用方式线索；reset 是否支持、是否清内存、bootloader 是否保留本区及 Android 是否能读回仍未真机验证。探针没有 watchdog，也不能保护抵达 reset 之前的挂死。

即使真机取得 ENTER+TARGET，也只证明这个微小 payload 的路径，不能外推到完整 Linux Image 的复制几何、解压或执行。

## 保留校准计划（尚未执行）

先用 Android 管理的 `/dev/pmsg0` 检查正常 warm reset 的保留与导出链。厂商同一提交的 `fs/pstore/pmsg.c`（blob `24db02de1787453d4ad838443dd39e4a7e948484`）将写入转给 pstore `write_user`；`fs/pstore/ram.c`（blob `dac81bcaed0055604a13fbe325a3362378281008`）再调用 `persistent_ram_write_user(mprz, ...)`。按本机布局，pmsg 位于 `[0xb0080000,0xb0280000)`，与探针写入的 console 分开。源码线索不能替代实际节点、运行内核和重启后的读回证据。

1. 只读确认指定真机的 Android、root、槽位、电量、温度、pstore 挂载、已有日志及 `/dev/pmsg0` 节点和权限。先保存现有 pstore 和相关内核日志，记录各文件长度与 SHA-256；不清空日志、不裸写活跃 console、不假设 `/dev/mem` 可用。离开 Android 前核对恢复材料和独立审查结论。
2. 私有实验记录预先写明唯一 nonce、短 payload 的原始字节、长度、SHA-256、正常 Android reboot 路径、等待期限和恢复步骤。通过二进制安全的通道传输并在设备侧核对长度和哈希，再经 `/dev/pmsg0` 追加一次；短写或失败即停止。本步骤尚无可执行设备脚本；节点权限已只读确认，但实际写入、短写处理和重启读回尚未验证。
3. 正常重启返回 Android 后，立即采集新的 pstore、挂载和相关内核日志。对导出文件同时核对设备端/主机端长度与哈希，在 `pmsg-ramoops-*` 中要求出现一次完整、逐字节一致的新 payload；旧 nonce、部分内容或仅节点存在都不算通过。超时或缺失只记该校准失败/无结论，不推导探针是否执行。
4. pmsg 成功仅覆盖该区域、该运行内核和正常 Android 重启路径。console 的 12 字节头部及探针记录读取仍需另行校准；裸探针 PSCI reset 的 RAM 保留与恢复也必须独立验证。没有这些证据和抵达 reset 前挂死的恢复方案，不能将探针送手机。

## 已完成的本地验证

2026-10-26，在 `ea69a25` 后的本地工作树构建 EP2 两种 4096 字节 Image。两者各通过 17 个实际 ARM64 指令场景和 12 组独立 codec 检查。覆盖各 EL、实际设置 SCTLR.A=1、源 Image 仅按 4/8 字节对齐、源代码重定位、MMU/EE 拒绝、代码/console/DTB/目标重叠及 DTB 范围溢出；PSCI 的 EL3 场景要求 console 和目标均不变。两条记录全部 2176 个单比特翻转均被拒绝；重算 CRC 后的非法状态、地址及跨记录不一致也被拒绝。报告范围明确排除硬件 cache coherency 和固件 reset。MMU/EE 拒绝是在 MRS 后注入捕获值，没有真的开启地址转换或大端模式。

| 变体 | nonce | Image SHA-256 | 本机报告 |
| --- | --- | --- | --- |
| halt | `ade8e3e41766f2780ab99e511cec4be1` | `abc48046b902f37e91f7c6c95b9c204f18476c7d0a82389fd9aba33c9128d011` | `out/entry-probe/ep2-halt-2/cpu.json` |
| psci | `14de72f38572aaf891711ef99ad3c40d` | `8a767960263b3caa184aa186a8c78083b3967b7804066f37ee00876c00635cc7` | `out/entry-probe/ep2-psci-2/cpu.json` |

两次构建使用不同 nonce，哈希差异同时包含 nonce 与 reset 代码差异。halt 的 stage2 为 256 字节，PSCI 为 288 字节。独立审查指出的三项问题已修复并通过上述回归：console+12 上的未对齐 64 位访存改成 32 位访问；PSCI 在 EL3 写日志前拒绝，验证器也禁止拦截 EL3 SMC 为成功；记录升级为带 CRC32、语义及跨记录检查的 EP2。独立复核确认三项修复成立，随后发现仅按 4 字节对齐的源 Image 在 stage2 复制时仍有 64 位未对齐读取；现已将该复制也改为 32 位 LDR/STR，并加入 4/8 字节源对齐场景。针对性独立复核分别重跑两种产物的这两个场景，四次均通过，未发现该改动的剩余正确性问题。执行级提交中断时序尚未验证：未覆盖在 payload、CRC 或 header 各次更新之间停止 CPU 再读回，因此这些结果不能证明断电一致性。

历史产物保留：`ep2-halt-1`（SHA-256 `007d1eb8b8318a5d0e84a947aa00ca16816c2537de83d503d6d3f6b7b7489d80`）和 `ep2-psci-1`（`d17ca08583b5a240c81554ce6c917fa0bf561f23fd8e9c068502e897eb5bc891`）各通过 15 CPU/12 codec，并经独立复跑；它们早于 stage2 源对齐修复，不代表当前源码。`local-1` 为首次链接失败现场，后续使用 `--image-base=0`；`local-2`（SHA-256 `a18cc932acd2e403d33aa1ac09b0c44cb331cecd126b1bd9b6797c82ae555f4b`）与 `psci-1`（`6d9ff5b09b6d562d0fe3e6864736b98b4d3edc895bf3a8a048feb97f4c70e48f`）是修复前各 12 CPU/9 codec 的旧结果，不证明当前 EP2。旧 PSCI EL3 的成功被 SMC hook 掩盖，不能算有效支持证据。验证器曾把源地址等于目标地址时的 halt 误认为搬移后执行，现已按实际进入目标的轨迹区分，相关拒绝场景通过。

最近只读前置检查确认 Android boot_completed=1、root 可用、槽位 b、内核 `4.14.348-openela-perf+`、电量 89%、32°C、USB 供电；`/dev/pmsg0` 为 `c-w--w--w- root log`，pstore 已挂载且为空。随后 EP2 修复没有设备操作；尚未进行 pmsg 写入或重启读回，未生成 boot.img，也没有真机 ENTER/TARGET 证据。

## 本地使用

依赖 clang、ld.lld、llvm-objcopy、llvm-nm、libfdt、Python 3.11+。CPU 验证用 uv 固定 Unicorn 2.1.4。输出必须使用新目录。

```sh
python3 -I tools/lg-g8/local-sim/entry-probe/build.py \
  --memory-dtb out/transport-clean/runtime-fdt.dtb \
  --nonce 0123456789abcdef0123456789abcdef \
  --out out/entry-probe/example-fresh
uv run --script tools/lg-g8/local-sim/entry-probe/verify.py \
  out/entry-probe/example-fresh --out out/entry-probe/example-fresh/cpu.json
```

构建只生成 raw ARM64 Image、ELF、符号和 manifest，不包装可直接上机的 boot.img。该阶段尚未建立恢复和保留校准，不应将本地测试产物直接送到手机。离线自测会固定合成测试输入以便复现；真机必须换新 nonce。
