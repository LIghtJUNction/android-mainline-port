# LG G8 USB2 诊断板树

`sm8150-lg-alphalm-usb2.dts` 是独立的 USB2-only 诊断配置，包含现有校准后板树，仅覆盖 USB1 的四个属性。它用于主线启动候选的 USB 依赖收敛。2026-10-27 已执行一次 RAM 启动，手机接受候选并随后回到原 Android，未取得 Linux USB/shell 证据；主机控制错误及观察缺口使本次不能判定 USB2 修改是否有效，见下文真机记录。

## 为什么需要单独配置

已审计基线的 DWC3 同时引用 HS PHY 和 QMP USB3 PHY，但后者为 disabled。所查本机驱动中，`dwc3_core_get_phy()` 即使限制 maximum-speed 仍请求 USB3 PHY；若已声明的 provider 尚未注册，PHY core 返回 `-EPROBE_DEFER`，DWC3 不会把它当作缺省 PHY 忽略。禁用 provider 通常不会创建平台设备，因此这是一条具体的等待依赖路径。尚无 LG 主线运行日志证明手机实际抵达了这条路径。

此配置将同一条 USB1 链路改成 USB2-only：

- DWC3 的 `maximum-speed = "high-speed"`，明确最高速度限制。
- `phys` 只保留既有 HS PHY，`phy-names` 只保留 `usb2-phy`，解除 USB3 provider 获取依赖。
- wrapper 新增 `qcom,select-utmi-as-pipe-clk`，选择驱动已实现的无 SSPHY 时钟路径。

基线中的 peripheral 模式、HS PHY 及供电、USB1 SID、RAM 和固件保留内存保持原值。QMP 与第二路 USB 控制器继续禁用。没有添加猜测的 LG USB3 供电、方向或地址声明，也未修改原板树。

## 源码依据

本机内核根目录为 `/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/kernel/linux`。该树没有已核实的上游 Git 身份，以下只固定实际读取文件的字节身份，不追认历史 Image 的完整构建来源。

| 相对路径 | 定位 | SHA-256 |
| --- | --- | --- |
| `drivers/usb/dwc3/core.c` | 1550 起获取 PHY，1599–1613 获取并处理 USB3 PHY 错误 | `bd8a8aabf199952717926b8ee79562e283ed9cd6a1ee485d22cd52167ad7ffcf` |
| `drivers/phy/phy-core.c` | 627–647：无引用返回 ENODEV，未注册 provider 返回 EPROBE_DEFER | `2535dc20688c48082b30f2498a05931897dc7b5db902bb02c5f5322d31c80117` |
| `drivers/usb/dwc3/dwc3-qcom-legacy.c` | 787–794：无 SSPHY 时选择 UTMI 时钟 | `155d7937357339bf4dc1bcf36f8cf7b47b13c268946412a6f2c077066baa0e41` |
| `arch/arm64/boot/dts/qcom/sm8150.dtsi` | USB1 wrapper、DWC3 与 PHY 定义 | `82887c779c9425ca23e49a81fe3ff8d8c7eec4d073edb305e6a9270371f3b7bc` |

相同 USB2-only 属性组合在本机 SDM845 OnePlus 和 Sony 板树中有示例；它们不作为 LG 接线或硬件验收证据。仅增加 maximum-speed 不足以解除已声明的 QMP 依赖。

## 本机验证

仓库根目录运行，`--out` 必须是新目录：

```sh
python -I tools/lg-g8/local-sim/tests/test_usb2_profile.py \
  --kernel-source /path/to/kernel/linux \
  --out out/usb2-profile-example
```

依赖 Clang、dtc、Python 3 和系统 libfdt。工具复用现有 FDT 验证与引用解析，分别编译基线和诊断 DTB，保留编译器/dtc 诊断，并做全量结构与语义比较：预期仅四个 USB 属性变化，RAM、固件保留区、SID 和供电声明一致。引用按 provider 路径与参数比较，不把自动编号变化当作硬件变化。全部实际 CPP 输入及验证工具在运行前后记录指纹；输出禁止位于外部源码树中。测试不操作设备，不生成 Android boot 镜像。

2026-10-27 实际运行上述验证器，输出在 `out/usb2-profile-20261027-2/`，退出 0，20 项检查全部通过，包含修改保留区、保留双 PHY、缺失 UTMI 选择三个拒绝反例。两份 DTB 均通过 libfdt 全量校验，语义差异恰为指定四属性，39 项源码依赖、验证代码和编译工具指纹前后一致。重复指定已有输出目录被拒绝，旧输出哈希未变。DTC 两次退出 0，原始诊断保留；未运行完整 binding/schema 校验。

| 产物 | SHA-256 |
| --- | --- |
| USB2 配置源码 | `a7eb106b9ef464909214b2676234cc7d6d2953bbb9344b50a2baeafb3ff4a96d` |
| 基线 DTB | `eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9` |
| USB2 DTB | `b65a8f4cbbfa4bfab9360f74bf1872fd6d2814aab506897c82c166bdfd080215` |
| `verification.json` | `214a12b670641f8a924296a216145e9f3beee931733e5f66e003e19753ec6cdf` |
| `source-hashes-before.json` 与 `source-hashes-after.json` | `229f4e285ed01077409c429b58d06a3037ecbb082512b99ce6941f6eb25d9bfa` |

初次 `out/usb2-profile-20261027-1/` 证据保留：当时验证器未将显示的 assigned-clocks、OPP 和 remote-endpoint 解析成引用，导致自动 phandle 重编号被误报为硬件变化。补齐引用语义后，在新目录完成最终验证；没有覆盖初次证据。该工具是本配置的固定基线比较器，不是任意板树的通用 binding 验证器。

交接侧核对了旧失败候选的实际 Android v0 封装：解压 shim、内嵌 Image/initramfs/DTB 与记录字节一致；四项 shim CPU 检查通过。其 Linux DTB 与基线仅相差 `/chosen` 的启动参数和 initrd 地址。Image 内嵌 IKCONFIG 提取后与仓库 tiny 配置逐字节一致，SHA-256 为 `c8ee45c025a01e35cf488e7ada4a60ef321b4bd0560af07ed6027ff3b8efceb2`；因此旧 Image 的 `QCOM_WDT=m`、`WATCHDOG_OPEN_TIMEOUT=0` 与 DRM 关闭状态有直接配置证据。没有发现只需重建相同基线即可解决的载荷错配。该轮核对保存在 `out/handoff-envelope-review-20261027-1/verification.json`，包括实际封装检查、四项 CPU 结果和命令摘录；报告明确是对已捕获执行结果的事后整理，不冒充原始测试脚本自动产物。现有 handoff 验证器只读取 shim 文件、未自动覆盖最终 boot 封装的测试缺口仍保留，手工核对只覆盖这份旧镜像。

编译和 libfdt 检查不证明 Linux 已执行、DWC3 probe、实际协商速度、DMA、USB shell、显示或恢复成功。旧失败候选 `lg-native-3` 不应原样重试；设备候选仍需固定 Image/initramfs/DTB 身份、一个明确实验变量、期限和实际可用的恢复路线。

## 2026-10-27 单次 RAM 测试

用户明确要求操作手机测试，并确认在旁可配合按键恢复后，准备并发送一次新候选 `out/abl-handoff/usb2-ram-20261027-1/boot.img`，大小 12,197,888 字节，SHA-256 为 `be3121d3545963e01ec05f8866bcaab964e86e1e13ba02fa007d07d32961dab4`。未写入、擦除分区或换槽。

最终 boot 封装直接解包的 56 项检查和四项 Unicorn CPU 交接检查全部通过；Image 和最终 initramfs 与旧候选逐字节一致，完整 bootargs、目的地址与 120 秒用户态重启策略保持不变，Linux DTB 的语义变化恰为上述四个 USB 属性。最终 Linux DTB SHA-256 为 `9ba32fd1355f4f3d7240413b7d83e22940bf2fd31b79514cac1a52e6c7ccf8a6`；DTB 增加 63 字节，对齐 payload 增加 64 字节，shim 长度和封装校验随之更新。构建 argv、输入指纹、CPU 和封装报告均保存在同一输出目录，旧材料未覆盖。

实际 `fastboot boot` 退出 0：发送约 0.277 秒，Booting 返回约 5.160 秒，命令总计约 5.443 秒。这只说明手机接受 RAM boot，不证明 Linux 已执行。

主机控制脚本在发送开始约 8.45 秒仍见原 fastboot USB 标识，错误地把它当作已返回的 fastboot，发出一次 `fastboot reboot`。该命令以 `Write to device failed (No such file or directory)` 失败，脚本随后停止。此前没有先观察到断开和稳定重新枚举，也没有重新核对协议；不能证明这次失败请求完全没有影响设备，因此它是本次实验的干扰因素。没有重发候选。

后续只读检查先确认目标 USB 已消失，被动观察器在候选发送约 95.459 秒后首次采样到 Android USB `1004:631f`。再取回主机内核 journal，并用同一主机单调时钟对齐候选发送起点，补到原 fastboot 实例于约 11.413 秒断开、Android 新实例于约 65.058 秒完成 USB 设备标识枚举。95.459 秒仅为脚本采样时点；65.058 秒是主机 USB 枚举事件，也不是内核复位时刻或 Android 启动完成耗时。主动脚本与被动观察器之间的采样缺口由 journal 补充了 USB 连接事件，但没有补出候选执行路径。用户报告黑屏后回到 Android，期间没有按键或拔插 USB。不归因于 watchdog、120 秒用户态计时器或任何特定复位机制。当前 Android 的启动原因属性为 bootloader，PMIC 日志含硬复位冷启动与此前 PS_HOLD 关机信息；这些仍不能归属到候选执行阶段或确定触发者。

本轮未取得 Linux gadget `18d1:4ee7` 或 ACM shell 的证据；观察缺口和主机控制错误也不足以排除短暂出现。回到 Android 后只读核对 model/device、启动完成、root、槽 `_b`、内核、修复 marker 与 boot_b 哈希均通过，boot_id 相对测试前已变化且回收过程中保持一致；boot_b 仍为已验收的 `fec418e5227a31927af27daf71d2225246f5adcf8dda38b5856200760b5205cd`。pstore 普通文件数为 0，不能据此判断主线未执行或 RAM 内容丢失。

结果为：未达到 Linux USB/shell 验收，USB2 修改效果仍未定；Android 回收与分区未变已验证。先修正主机状态判断，不自动重测，也不以这次结果否定或证明 USB2 路线。

私有设备证据根目录为 `/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/private/usb2-ram-device-20261027-1/`，不提交原始日志、boot_id 或设备序列号。`result.json` 保留脚本失败，`controller-issue.json` 解释控制错误，`passive-observer-result.json`、`postcheck-result.json` 与 `user-observation.json` 分别保存后续采样、Android 完整回收和人工观察；不把后续回收成功覆盖到原脚本结果。`postcheck-result.json` SHA-256 为 `6b396835e122a9b39a2eccff45675e2cde7b540bf7ab786ede6f0de6ac110fe5`，原失败 `result.json` 为 `7f7010d0a36222ba973572236d896fcfc5f15c3a9b5f89dbcfce70a0d7e0e03d`，人工观察记录为 `926e3ec503b48e6213562e6bc80eef5c4435df73825d1ff5bf20b117ce21af73`。后续主机 journal 原始 JSON 与对齐结果另存 `host-kernel-monotonic.jsonl` 和 `host-kernel-monotonic-summary.json`，追加保留而未修改最初观测。
