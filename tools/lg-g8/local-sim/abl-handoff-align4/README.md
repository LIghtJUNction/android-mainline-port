# 4 字节对齐源地址的离线 handoff shim

该目录是独立的 ARM64 复制跳板实验，不替换 `abl-handoff` 或任何已用过的候选，不生成手机 boot.img。旧实现的 64 位常量读取及成对复制在源地址只有 4 字节对齐时可能未对齐；这里用两个 32 位读取重建常量，并按 4 字节复制。实际 LG ABL 装载地址、SCTLR.A 和故障原因仍未知，不能将该问题追认为真机失败原因。

构建要求非空、长度为 4 字节倍数的载荷，以及不回绕且按 2 MiB 对齐的目标地址；DTB 地址按 ARM64 `Documentation/arch/arm64/booting.rst` 的入口约定须为 8 字节对齐的 64 位数值。目标范围与执行中 shim 重叠时停在 `blocked`；允许安全的载荷原地、前向或反向重叠复制。完成后清理目标数据缓存、失效指令缓存、屏蔽 DAIF，以 x0=DTB、x1–x3=0 跳转目标。该动作并不验证载荷自身的 Linux 启动契约。

依赖 Python 3.11+、clang、ld.lld、llvm-objcopy 和 llvm-nm。CPU 验证器通过 uv 固定 Unicorn 2.1.4。所有输出使用新目录。

```sh
python3 -I tools/lg-g8/local-sim/abl-handoff-align4/build.py \
  --payload path/to/payload.bin --out out/abl-handoff-align4/example-build
uv run --script tools/lg-g8/local-sim/abl-handoff-align4/verify.py \
  --out out/abl-handoff-align4/example-check
PYTHONOPTIMIZE=1 uv run --script tools/lg-g8/local-sim/abl-handoff-align4/verify.py \
  --out out/abl-handoff-align4/example-check-optimized
```

构建归档源码、载荷、对象、ELF、Image 及 SHA-256 manifest。验证器检查固定合成制品的文件摘要、构建器指纹，再从归档源码和载荷重建并逐字节比较对象、ELF、Image；检查不依赖 `assert`，并包含源码篡改拒绝检查。它尚未提供外部完整载荷的命令行验收入口，不能把本套件结果推广到任意历史构建。

2026-10-27 本机普通与 `PYTHONOPTIMIZE=1` 两次运行均通过 76 项合成执行场景、4 项负对照及 9 项非法构建输入检查。覆盖 EL1/EL2、SCTLR.A 开/关、源地址模 16 的 0/4/8/12、安全重叠、拒绝覆盖 shim、64 位高地址及初始 DAIF 未屏蔽场景。成功路径检查载荷、边界哨兵、shim、缓存清理地址序列、交接寄存器、SP、DAIF 与 SCTLR.A；拒绝路径检查连续 128 条指令均留在 blocked 且无内存改变。旧实现与只修常量读取的实现分别在 EL1/EL2 构成负对照，验证未对齐访问会被捕获。

报告位于 `out/abl-handoff-align4/acceptance-20261027-2/results.json` 和 `out/abl-handoff-align4/acceptance-optimized-20261027-2/results.json`，不提交构建产物。模型使用固定的缓存行参数和内存访问 hook，不模拟真实缓存一致性、MMU/固件环境、watchdog、时间、复位或硬件恢复；`hardware_ready=false`。本次验收没有运行完整 Linux 载荷或操作手机。
