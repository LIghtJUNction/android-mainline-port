# 手机主线 Linux 移植对照：LG G8 可借鉴什么

调查日期：2026-10-27。通过网页发现项目，再用 `gh` 核对公开仓库、固定提交、构建配方、设备树、补丁和作者调试记录；并行调查覆盖其他 SM8150 手机及相邻代 LG。没有执行外部刷写脚本、联系维护者或操作手机。

结论：SM8150 手机已有直接从 Android Bootloader（ABL）启动主线方向内核、建立 USB SSH 和运行图形桌面的公开作者记录。OnePlus 7 Pro 还有 Arch Linux ARM、Hyprland 和手机交互层的实现。但这些不能证明 LG G8 已启动主线内核，也不能证明完整 Omarchy 已在这些手机上验收。本次未核实到可直接用于 LM-G820UM 的完整主线移植或专用公开 ABL 修复；这不是“全网不存在”的结论。

## 证据如何使用

- **上游 Linux**：已经进入 Linus 主树的代码，可用作 SoC 驱动和 binding 基础；不表示 LG 板级硬件全部支持。
- **外部主线补丁树**：基于较新 Linux 的设备适配；补丁不一定已进入上游，也不一定能独立复现。
- **Android downstream**：厂商/Lineage 的旧内核，适合查接线、电源、固件内存和板级差异，不能称为主线成果。
- **作者记录与本机核查**：README、调试文档和源码是不同证据。下面的外部硬件结果均为作者记录；本会话未独立复现。Halium/libhybris 或 Android 上的 chroot 也不能代替主线内核启动验收。

重点项目正文、若干补丁与构建配方已直接核查；扩展项目由并行研究核查，不代表已逐行复审其全部源码。固定提交用于保留所查版本，不将它们当作项目最新状态。

## 最接近目标的两个项目

| 项目与固定版本 | 作者记录的结果 | 对 G8 的价值与限制 |
| --- | --- | --- |
| [Sr-0w/hotdog-linux-bringup](https://github.com/Sr-0w/hotdog-linux-bringup/tree/47087509c4289c2580c54f5433e55366b2e00445)，OnePlus 7T Pro HD1913，SM8150/855+ | ABL 直接启动 Linux 6.16、postmarketOS/Plasma Mobile；调试文档记录 USB SSH，后期 README 还记录显示、GPU、触屏、音频等进展及回归 | 同 SoC 的启动交接、DTBO 契约、固件保留内存和 USB SMMU 调试最有参考价值。属于外部补丁树，显示不稳定、Bluetooth 回归等问题仍在；不复制其板号、物理地址或恢复行为。 |
| [Cube-Oakley/omarchy-mobile](https://github.com/Cube-Oakley/omarchy-mobile/tree/f702f688288e59e3566f113d411ac7d18e4d0201)，OnePlus 7 Pro，SM8150 | Arch Linux ARM、Hyprland、Quickshell、触屏桌面，以及多项硬件功能的作者记录 | 可借鉴共享交互层与设备适配层划分。作者明确完整 Omarchy/theme-repository 安装仍属未来工作，当前系统是 Arch Linux ARM 加 mobile layer；不能表述成完整 Omarchy 已完成。 |

### Hotdog：从最后成功步骤定位 USB

[2026-08-03 调试记录](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/docs/evidence/2026-08-03-direct-mainline-usb.md) 将失败缩小到首次 EP0 endpoint command：V36 已有 PHY、DWC3、UDC、configfs/NCM，但仍不能证明数据通路可用；V39 最后标记是 `HOTDOG_V39_EP_CMD_DEPCMD_WRITE_DONE`，尚未完成首次轮询。

V40 为 DWC3 添加 `iommus = <&apps_smmu 0x140 0>`，但 `iommu.passthrough=1` 仍使用 identity domain，event ring 没有按预期写入。V41 改为 `iommu.passthrough=0`，使用 translated domain 后，作者记录 NCM/ACM、ping 与 SSH 成功。这说明要同时核对 SMMU 连接和运行中的地址转换域，不能停在“驱动已注册”。SID `0x140` 是该设备记录，尚未在 LG G8 核实。

V42 去掉高频诊断并保留成功 DTB/initramfs/cmdline，作者记录约 19 秒到 SSH；该版没有自动 reboot/recovery action。V43 从固定基础提交及公开补丁重建，作者检查没有未声明的 generic USB/IOMMU 源码差异，记录约 15 秒到新内核 SSH。文档只引用本地原始硬件日志目录，未公开这些原始日志；该阶段完整 pmbootstrap 复现 DTB/initramfs/rootfs 仍是后续工作。不能把作者摘要当作本会话复现。

### Hotdog：检查补丁顺序和完整内存图

其 [APKBUILD](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/APKBUILD) 固定基础为 `403b56c33e2ccdda25d90378970a5e5b928dee19`，包含一串按顺序应用的设备补丁。已核查配方前部和下列补丁，不声称全部功能补丁都已审阅：

- [0002 direct-entry](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0002-arm64-hotdog-direct-entry.patch)：修改 ARM64 Image 入口，在多个阶段画 framebuffer 标记，并在 `__cpu_setup` 后关闭 APSS watchdog。这是早期实验，后面还有入口修订；其地址、显示模式和关闭看门狗的做法不能复制到 G8。G8 的早期挂死恢复尚未验收，关掉 watchdog 还可能失去潜在恢复路径。
- [0015 hardware-contract](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0015-arm64-dts-hotdog-source-v43-hardware-contract.patch)：用 symbols 兼容过滤后的 vendor DTBO，并配置板号、simple-framebuffer、ramoops、regulator、USB 等。应复用核对方法，板号与电源节点仍须来自 LG 实际板型。该阶段移除 UFS iommus，但后续 [0029](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0029-arm64-dts-hotdog-restore-ufs-smmu.patch) 恢复 UFS SMMU，不能把早期 workaround 当最终设计。
- [0032 complete-stock-reservations](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0032-arm64-dts-hotdog-complete-stock-reservations.patch)：作者报告遗漏固件保留区，且在遗漏 CDSP 区发现 Linux page-cache folios，随后补齐两段 `no-map`。应比较全部固件保留区间的并集和装载区间，不能只检查 ramoops；该设备地址和作者 DDR 取证不构成 G8 的证据。

### Omarchy-mobile：先打通硬件，再复用用户态

[bringup 记录](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/bringup.md) 展示 ABL/DTBO 兼容问题：该机历史 ABL 不支持 `fastboot boot`，多个 Image/header/重打包尝试失败；主线缺少大量 vendor symbols，stock DTBO 应用出现 `FDT_ERR_NOTFOUND`。其历史解决方式涉及 transformed DTB 和 filtered stock DTBO，不是仅改 header 或 board-id。不能据此要求 LG 升级固件、擦除 DTBO 或使用 OnePlus wrapper/偏移。

[构建脚本](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/scripts/build_sm8150_kernel.sh) 选择外部 SM8150 树 `v6.17.0-sm8150`，启用 QCOM_WDT，并禁用 RAID6_PQ_BENCHMARK；作者注释称后者是该机已知 direct-boot hang。这只是 G8 排查候选项，不证明 G8 卡在该 initcall。脚本存在强制 checkout 和部分补丁失败后继续的行为，未执行，也不原样作为严格可复现基线。

[共享 mobile layer](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/overlay/mobile/README.md) 提供手势、任务切换、通知、锁屏、电源界面和屏幕键盘；battery、modem、suspend、charging、camera 留给 device adapter。它依赖具体版本的 Hyprland/Quickshell，插件也需匹配。安装器写用户配置，不负责安装系统包或刷写硬件。外部安装器的备份位置不沿用到本项目；本项目恢复材料仍统一放本机 `~/Backups`。

## 其他移植与 LG 板级资料

| 来源 | 类型与结果范围 | 可借鉴内容 / 不能外推的部分 |
| --- | --- | --- |
| [haintrainn/nethunter-pro-guacamole](https://github.com/haintrainn/nethunter-pro-guacamole/tree/c2af9effc2455adc4b4f5eab833a3450d5ff9e86) | OnePlus 7 Pro，外部 SM8150 主线方向树与构建记录 | DTB 匹配、bootloader framebuffer、Android 12 固件内存、simpledrm 的 interconnect 带宽和 RMTFS guard pages；不是 G8 内存模板。 |
| [M1DNYT3/sm8150-mainline-pmOS](https://github.com/M1DNYT3/sm8150-mainline-pmOS/commit/cdc069fa2a33fbcbc931a16a28bdf808969034be) | Xiaomi Mi 9，外部树 | 最小 reserved-memory、UART、simplefb、按键、regulator、USB 组织方式；PON/reboot 数值与地址不可复制。 |
| [zuotang/xiaomi9_linux](https://github.com/zuotang/xiaomi9_linux/blob/6bdb8bde5a8905881812b83b13d14a291ef9e72a/docs/mi9-mainline-drm-progress.md) | Xiaomi Mi 9，从 simplefb/USB 到 DRM/UFS/touch 的作者进展记录 | 分阶段验收有参考价值；引用的 panel 源文件缺失，未核实为完整可直接复现补丁集。 |
| [上游 Sony Kumano](https://github.com/torvalds/linux/blob/fb2c3f44a7e14627b9dca8e6b568e6cdc234b40b/arch/arm64/boot/dts/qcom/sm8150-sony-xperia-kumano.dtsi) 与 [SM8150 SoC](https://github.com/torvalds/linux/blob/fb2c3f44a7e14627b9dca8e6b568e6cdc234b40b/arch/arm64/boot/dts/qcom/sm8150.dtsi) | 上游代码，固定为本次查询的提交 | UART、USB peripheral、simplefb、ramoops、DTB 匹配。Sony 文件禁用 UFS 并有警告，不能当启用 UFS 模板；framebuffer 模式也不等于原生面板模式。 |
| [上游 LG G7/V35 common](https://github.com/torvalds/linux/blob/fb2c3f44a7e14627b9dca8e6b568e6cdc234b40b/arch/arm64/boot/dts/qcom/sdm845-lg-common.dtsi) 与 [OnePlus 6/6T common](https://github.com/torvalds/linux/blob/fb2c3f44a7e14627b9dca8e6b568e6cdc234b40b/arch/arm64/boot/dts/qcom/sdm845-oneplus-common.dtsi) | 相邻代 SDM845，上游 | LG 电源/UART、splash、ramoops/ECC、USB2；不能当 SM8150 板级支持证明。LG 文件对 UFS 的限制及 I/O error 注释仍须尊重。 |
| [Lineage LG SM8150](https://github.com/LineageOS/android_kernel_lge_sm8150/tree/d4f335e13d73c4aa2af85581a6ec11b670ee5b94/arch/arm64/boot/dts/lge) 的 [Alpha storage](https://github.com/LineageOS/android_kernel_lge_sm8150/blob/d4f335e13d73c4aa2af85581a6ec11b670ee5b94/arch/arm64/boot/dts/lge/sm8150-alpha/sm8150-alpha_common/sm8150-alpha_common-storage.dtsi) | Android 4.14 downstream，非主线成果 | 查 LG 真实 UFS 电源和 Alpha common include 链。具体 LM-G820UM revision 的有效 overlay 仍需与已采实际 FDT/DTBO 核对。 |
| [Bentlybro/gts6l-mainline-linux boot method](https://github.com/Bentlybro/gts6l-mainline-linux/blob/41fa9b85bfb55f9ee671a1dcb8161a5d90a2d542/docs/BOOT_METHOD.md) 与 [UFS](https://github.com/Bentlybro/gts6l-mainline-linux/blob/41fa9b85bfb55f9ee671a1dcb8161a5d90a2d542/docs/UFS.md) | Samsung SM8150 平板，ABL→stock 容器→Aloha UEFI→EFI stub | 多级启动方法及 UFS clock 诊断参考；不能证明 LG 需要相同 trailer、UEFI 中间层或 clock workaround。 |
| [MDSS DSI panel generator](https://github.com/msm8916-mainline/linux-mdss-dsi-panel-driver-generator) | 从 downstream DT 生成 DRM panel 草稿的工具 | 可辅助后续面板驱动准备，仍需人工审阅初始化序列；不解决 ABL、DPU/DSI、clock、power 或 GPIO。工具未执行。 |

网页检索覆盖 GitHub 之外的 postmarketOS/Nura 线索；部分 wiki 跳转后正文被 Anubis 拒绝，未绕过，也未把无法取得的内容当作已核实结果。现存 Nura 草稿和本地包另有来源核验任务，本报告不据其文件存在就认证可用移植。

## G8 下一步应怎样调整

研究支持优先做下面的离线对照；它们没有扩大设备实验授权，也没有产生新的启动镜像。

1. **锁定实际板型和启动契约。** 对已校验的 LG stock boot、DTBO、运行时 FDT 和 ABL 路径逐项比较 DTB 选择、symbols/fixup、overlay 目标、Image 入口与装载地址，保留中间产物哈希。先定位最早不满足的契约，不沿用已撤回的损坏导出推论。
2. **完整比较内存区间。** 求 stock 固件 reserved-memory 的并集，检查与内核、initramfs、DTB、framebuffer 及候选 carveout 的交叠。用 LG 有效配置核对，不把其他设备的 CDSP、ramoops 或 splash 地址移植过来。
3. **按最小可观测路径启用设备。** 显示先核对 bootloader framebuffer 格式、范围和 clock/interconnect 保持条件，再做原生 DSI；USB 同时核对电源、PHY、DWC3、SMMU SID/连接及实际 domain。PHY/UDC 注册不是网络成功，SSH 也不是显示或触控验收。
4. **保留日志与恢复前提。** 先完成现有 pmsg 读回定位和早期快照的规则/权限/生命周期核对，再分别验证 console/EP2、裸 PSCI 及更早挂死恢复。Hotdog 的 framebuffer 阶段标记可作为设计参考，不能直接采用其地址或关闭 G8 watchdog。当前没有依据启动无人值守候选循环。
5. **硬件可用后接用户态。** 以 Arch Linux ARM 和可复现软件版本为基础，评估 omarchy-mobile 的交互层；G8 battery、charging、suspend 等独立适配与验收。最终还须按实际安装内容区分 Arch+mobile layer 和完整 Omarchy。

G8 当前真实验收、失败记录和设备操作边界继续以 [STATUS.md](STATUS.md#当前验收与下一步) 为准。此次研究没有证明 G8 主线内核已执行，没有重跑 pmsg 校准、上传快照 helper、修改 init、生成或发送启动镜像。
