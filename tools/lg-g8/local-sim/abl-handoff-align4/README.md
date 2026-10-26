# 完整载荷 shim 的 4 字节对齐离线模型

此目录独立构建 ARM64 复制 shim，允许源 Image 仅按 4 字节对齐。常量使用两次 32 位读取组合为 64 位地址，复制使用 32 位 LDR/STR；不替换 `abl-handoff`，不生成 Android boot.img，也不访问设备。实际 ABL 装载地址及 SCTLR.A 状态仍未知，模拟发现的未对齐路径不能直接解释此前真机失败。

依赖 Python 3.11+、clang、ld.lld、llvm-objcopy、llvm-nm 和 uv。CPU 模拟依赖固定为 Unicorn 2.1.4。从仓库根目录运行，输出必须使用新目录：

```sh
uv run --script tools/lg-g8/local-sim/abl-handoff-align4/verify.py \
  --out out/abl-handoff-align4/example-fresh
PYTHONOPTIMIZE=1 uv run --script tools/lg-g8/local-sim/abl-handoff-align4/verify.py \
  --out out/abl-handoff-align4/example-optimized-fresh
```

验证器构建合成载荷并执行实际机器码。76 个正向场景覆盖 EL1/EL2、SCTLR.A 开关、源地址模 16 的四种字对齐、前后向重叠复制、源等于目标、代码重叠拒绝、初始 DAIF 未屏蔽以及高于 4 GiB 的地址。拒绝路径还执行 128 条指令，确认始终停在 blocked 自循环且没有改写内存。4 个负对照分别恢复旧常量读取和旧成对复制，要求捕获未对齐访问。

构建将源码和载荷快照到独立输出目录，manifest 记录源码、载荷、对象、ELF、Image 及构建器 SHA-256。验证时用归档输入重新构建并逐字节比较对象、ELF 与 Image，再检查 Image 内嵌载荷；修改归档源码必须导致验证失败。检查使用显式异常，Python 优化模式不会取消验收。目标地址限制为 2 MiB 对齐，载荷必须非空且长度为 4 的倍数，地址范围不得溢出。DTB 地址按 ARM64 `Documentation/arch/arm64/booting.rst` 的入口约定要求 8 字节对齐；四项额外反例覆盖仅 4 字节对齐、非字对齐、负数及 64 位溢出，并确认拒绝发生在创建输出之前。合计 9 项非法构建输入被拒绝。

`results.json` 记录场景、负对照、构建拒绝数、模拟器版本和验证器摘要。2026-10-27 普通模式及优化模式均通过上述 76 个正向场景和 4 个负对照；同轮 local-sim 单元回归 78 项通过，包括 EP2 离线读回的 7 项测试。

这些是小型合成载荷测试，`full_payload_run=false`、`hardware_ready=false`。缓存检查只核对清理地址轨迹，不模拟缓存一致性或持久化；不验证 watchdog、复制耗时、真实 ABL、Linux 执行、固件 reset 或挂死恢复。此前私有补丁约 29 MB 载荷的结果不属于此目录当前构建的验收证据。不得把此目录产物直接用于手机。
