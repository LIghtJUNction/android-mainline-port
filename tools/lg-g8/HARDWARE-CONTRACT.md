# LG G8 USB、DMA 与显示离线契约

2026-10-27：已校验的 LG 运行时 FDT 独立声明 USB1 SID `0x140`、mask `0`，候选 DTB 已有相同声明。没有依据再复制 OnePlus 的 SID 补丁。候选 DWC3 引用禁用的 USB3 QMP PHY；这是具体的未满足依赖声明，尚未复现其启动影响。两份 FDT 都没有 simple-framebuffer；候选配置又关闭 DRM，目前这些输入不能建立显示输出路径。

本次只读本机现有证据，未访问设备、生成候选镜像、修改外部内核或修改板级 DTS。离线审计退出码 0 仅表示报告成功生成，不表示硬件验收通过。

## 输入与来源边界

下列路径以仓库根目录为基准。运行时 FDT 的预期哈希由既有采集证据固定；候选是既有 `patched.dtb`，不是本工具重新构建的产物。

| 输入 | 路径 | SHA256 |
| --- | --- | --- |
| LG 运行时 FDT | `out/transport-clean/runtime-fdt.dtb` | `75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf` |
| 候选 DTB | `out/local-sim/dtb/patched.dtb` | `eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9` |
| 候选配置 | `devices/lg-alphalm/configs/linux-7.3-tiny.config` | `c8ee45c025a01e35cf488e7ada4a60ef321b4bd0560af07ed6027ff3b8efceb2` |
| 仓库板级源文件，未修改 | `devices/lg-alphalm/sm8150-lg-alphalm-board.dts` | `69e9fbf81f2b1cbfaf2caec089225de54b11e0a0f1a50dbf583fd63874ba46e1` |

这些哈希不证明配置、DTB 与任何已构建或正在运行的镜像对应。候选配置不用于推断 stock 内核配置。

读取的外部本机源码根目录为 `/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/kernel/linux`。未核实该目录的上游 Git 身份，也未执行其中的脚本或构建。源码只用于解释对应声明，最终报告记录逐文件哈希：

| 相对外部源码根目录的路径 | SHA256 |
| --- | --- |
| `arch/arm64/boot/dts/qcom/sm8150.dtsi` | `82887c779c9425ca23e49a81fe3ff8d8c7eec4d073edb305e6a9270371f3b7bc` |
| `drivers/iommu/iommu.c` | `9803f65168c7207d1647bf0c61647d1544b7f00ce5e77a97f7b41f15bdd6c9f2` |
| `drivers/iommu/arm/arm-smmu/arm-smmu.c` | `6345148d8448a1edcf49998cc353b8131741af20484b5aa38a13325d1dd03e68` |
| `drivers/usb/dwc3/dwc3-qcom-legacy.c` | `155d7937357339bf4dc1bcf36f8cf7b47b13c268946412a6f2c077066baa0e41` |
| `drivers/usb/dwc3/core.c` | `bd8a8aabf199952717926b8ee79562e283ed9cd6a1ee485d22cd52167ad7ffcf` |
| `drivers/gpu/drm/msm/msm_mdss.c` | `092b2bbd3b57a03468663c3267a6bf7a47b4e8b79d656933a9a55b642d656f4d` |
| `lib/cmdline.c` | `1af5ad98fface0b59694e7565c33be2e37c089cc9a1a841285414ee94546ca9d` |
| `kernel/params.c` | `b2afd93cb1300890343947abcdc955c1eedc5294f1abcf0a3145203cf46817c4` |
| `drivers/of/fdt.c` | `a1d085707781c1a8356a8ef5bb9c8164e6ad6108dcb59582dfc7f2b348e81ff6` |
| `drivers/of/of_reserved_mem.c` | `df750fd2023c11c41991c34cc6f1d40bcd53458c19492f15b23fa0e59e125d50` |

## 复现

使用 Python 3 和系统 libfdt；工具复用 `local-sim/dtb/verify.py` 的完整 `fdt_check_full` 校验及解析函数，不运行其构建/修改入口。以下命令从任意可信工作目录执行，不进入外部输入目录。

```sh
repo=/home/lightjunction/Documents/GITHUB/android-mainline-port
kernel_src=/home/lightjunction/Documents/Codex/2026-10-25-lg-g8-omarchy/kernel/linux
python -I "$repo/tools/lg-g8/audit-hardware-contract.py" \
  --runtime "$repo/out/transport-clean/runtime-fdt.dtb" \
  --runtime-sha256 75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf \
  --candidate "$repo/out/local-sim/dtb/patched.dtb" \
  --candidate-sha256 eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9 \
  --config "$repo/devices/lg-alphalm/configs/linux-7.3-tiny.config" \
  --source "soc-dtsi=$kernel_src/arch/arm64/boot/dts/qcom/sm8150.dtsi" \
  --source "iommu-core=$kernel_src/drivers/iommu/iommu.c" \
  --source "arm-smmu=$kernel_src/drivers/iommu/arm/arm-smmu/arm-smmu.c" \
  --source "dwc3-legacy=$kernel_src/drivers/usb/dwc3/dwc3-qcom-legacy.c" \
  --source "dwc3-core=$kernel_src/drivers/usb/dwc3/core.c" \
  --source "mdss=$kernel_src/drivers/gpu/drm/msm/msm_mdss.c" \
  --source "cmdline-parser=$kernel_src/lib/cmdline.c" \
  --source "kernel-params=$kernel_src/kernel/params.c" \
  --source "of-fdt=$kernel_src/drivers/of/fdt.c" \
  --source "of-reserved-memory=$kernel_src/drivers/of/of_reserved_mem.c" \
  --source "board=$repo/devices/lg-alphalm/sm8150-lg-alphalm-board.dts" \
  --out "$repo/out/hardware-contract-audit-20261027/report-review.json"
```

实际已生成上述复审报告，权限 `0600`，SHA256 为 `1b54bf4bee85814dc651eed0e2a03e9565f91e517d61e48a8e8de26eed5ea528`。复跑必须将 `--out` 改成新的未使用路径；工具以 `O_EXCL` 创建，拒绝覆盖旧证据。`out/` 被 Git 忽略，不提交原始诊断报告。初次 `report.json` 与实现阶段 `report-final.json` 均保留；本文依据修正命令行和保留区边界后的 `report-review.json`。

报告中的输入指纹不等于工具身份；既有 `report-review.json` 未记录审计器及其复用解析器的指纹，不能仅凭该报告确认生成时使用的是当前工具与解析器版本。

可选 `--extra-cmdline-file PATH` 读取待核对的补充命令行，来源和哈希进入报告；不自动认定它就是最终命令行，也不将它用于 stock 策略。`--source LABEL=PATH` 只记录源码哈希，不执行源码。所有输入在审计前和输出前核对哈希。

## USB1 与 DMA 设备

SID 是 SMMU 区分设备请求的流标识；mask 是两单元 ARM SMMU specifier 中的匹配掩码。DT 中的连接声明不等于设备已成功附着到运行中的地址转换域。

| 项目 | LG 运行时 FDT | 候选 DTB |
| --- | --- | --- |
| wrapper | `/soc/ssusb@a600000`，`qcom,dwc-usb3-msm` | `/soc@0/usb@a6f8800`，`qcom,sm8150-dwc3` / `qcom,dwc3` |
| DWC3 | `/soc/ssusb@a600000/dwc3@a600000` | `/soc@0/usb@a6f8800/usb@a600000` |
| 连接所在节点 | wrapper | DWC3 子节点 |
| SMMU provider | `/soc/apps-smmu@0x15000000` | `/soc@0/iommu@15000000` |
| SID / mask | `0x140 / 0` | `0x140 / 0` |
| DWC3 `linux,sysdev_is_parent` | 存在 | 不存在 |
| DWC3 `dr_mode` | `otg` | `peripheral` |
| DWC3 `maximum-speed` | `super-speed` | 未声明 |
| PHY 属性 | `usb-phy`，两个无参数 phandle | `phys`，HS 无参数、QMP 参数 `0` |
| HS provider | `/soc/hsphy@88e2000`，可用 | `/soc@0/phy@88e2000`，可用 |
| USB3 QMP provider | `/soc/ssphy@88e8000`，可用 | `/soc@0/phy@88e8000`，`disabled` |

两个 wrapper、DWC3 及 SMMU provider 的祖先状态检查均可用。这里的“可用”只表示 DT status 及祖先未禁用，不是驱动 probe 结果。候选 QMP provider 是已解析成功但禁用的依赖，不是悬空 phandle。

本机 `dwc3-qcom-legacy.c:917` 匹配 `qcom,dwc3`，其 703 行起查找 `snps,dwc3` 子节点并通过 `of_platform_populate()` 创建设备。另一个 `dwc3-qcom.c` 的集成式 probe 匹配不同 compatible，不能用它断言这里的子节点属性失效。

`core.c:1762` 按 `linux,sysdev_is_parent` 选择 `dwc->sysdev`；event-ring DMA 分配使用这个设备。因此运行树的父节点连接与候选子节点连接具有合理的声明关系，属性位置差异本身不足以证明错误。没有核实 stock 驱动源码身份或实际 DMA 附着，不能把这段本地候选代码当作 stock 执行证据。

候选 wrapper 还声明 `cfg_noc/core/iface/sleep/mock_utmi/xo` clocks 和 `usb-ddr/apps-usb` interconnect。报告解析 provider、参数和祖先状态；不测量 clock rate 或实际带宽请求。候选启用 DWC3 gadget、QCOM glue、HS/QMP PHY 及 SM8150 interconnect 驱动配置。这些配置仍不证明 USB 枚举、event ring 写入、网络或 SSH 成功。

USB2 wrapper 在两份树中禁用；LG 运行树为它声明 SID `0x160`，不是本次 USB1 修改依据。未据此启用第二路控制器。

## IOMMU 策略

候选配置声明 `CONFIG_IOMMU_DEFAULT_DMA_STRICT=y`，passthrough 和 lazy 默认关闭，`CONFIG_CMDLINE=""`。候选 FDT 没有相关命令行覆盖，本次也未提供 extra cmdline。因此报告给出的条件性默认策略是 **translated、strict=true**。translated 指 DMA 地址经过 IOMMU 映射；strict 指 DMA 取消映射时的严格失效策略。二者不是同一个开关。

这个结论以配置和命令行确实用于该启动为前提；不证明实际构建身份、最终 bootloader 命令行、设备专用策略、fallback 或运行域。报告始终将实际设备 domain 标为 unknown。

运行时 FDT 的白名单仅提取到有效 `swiotlb` 选项，没有提供 stock 配置，因此 stock 默认策略保持 unknown。`CONFIG_ARM_SMMU_DISABLE_BYPASS_BY_DEFAULT=y` 也不能替代 domain 证据：它控制未匹配流的 bypass 行为，与已附着设备默认域不同。

工具遇到缺失/冲突默认配置、重复或畸形选中配置、非空编译命令行、启用 `CMDLINE_FORCE/EXTEND` 但未核实架构合并规则、无效或冲突 IOMMU 参数时保留 unknown，不猜测最后生效值。包括 `CMDLINE_FORCE=y`、编译命令行为空的情况：本机 `drivers/of/fdt.c` 会丢弃 DT bootargs，不能继续拿其中的参数推断策略。

DT bootargs 必须是单一 NUL 结尾字符串；尾部 NUL 不会污染最后一个参数，嵌入 NUL 等畸形输入不会被认定为已知策略。参数分词遵循本机 `lib/cmdline.c` 的双引号规则和 `kernel/params.c` 的 `--` 终止规则，字符串值里的 IOMMU 文本及 `--` 后的 init 参数均不作为策略开关；不平衡引号保留 unknown。未白名单的参数和值不进入报告。

## 显示、framebuffer 与带宽

LG `/soc/qcom,mdss_mdp@ae00000` 和候选 `/soc@0/display-subsystem@ae00000` 都声明普通显示 SID `0x800`、mask `0x420`。LG secure SDE context 的 `0x801` 不应替代普通显示连接来比较。候选 MDSS、DPU、DSI0 和 DSI0 PHY 状态启用，DSI1 路径禁用；这不能证明实际选中的面板、面板初始化或 scanout。

LG splash 与 display-dump 固定区间重合，均为 `0x9d200000 + 0x1200000`；候选也保留该范围。但两份 FDT 均无 simple-framebuffer。仅凭这段 carveout 无法推断 width、height、stride、像素格式或正在扫描的缓冲区。不得将它直接变成可用 framebuffer 声明。

候选配置启用 `FB_SIMPLE/FB/SYSFB`，关闭 `SYSFB_SIMPLEFB` 和 `DRM`。在已检查的配置/FDT 组合中，既没有 simplefb DT 节点，又不能依靠 DRM 驱动原生 MDSS/DSI。运行中内核和 bootloader 显示状态仍未验证。

LG MDSS 子节点的 `qcom,msm-bus,vectors-KBps` 是声明的带宽用例表，不是当前投票。本机 `msm_mdss.c:242` 起先设置非零 interconnect bandwidth，再在 255 行启用 clocks，说明两种依赖都需核对；没有依据移植其他手机的频率或带宽数值。未确认 bootloader 是否保留相关 clocks、power 或带宽状态。

工具支持今后对实际 simple-framebuffer 声明核对父总线 address/size cell 宽度、geometry、stride、常见格式、allocation 大小、固定 reservation 覆盖及依赖；只在祖先总线 `ranges` 均为 identity 时认定物理范围。禁用的 reserved-memory 子节点不计入覆盖；父容器 cell 宽度必须匹配根节点，空 `reg`、零长度和越过 64 位地址空间的区间均报告错误。本机 `of_reserved_mem.c` 只使用固定 `reg` 的第一个 tuple，多 tuple 输入保留未知而不将全部 tuple 算作保护范围。存在 reservation 解析错误时，framebuffer 的 `reservation_coverage_known=false`，不能单凭空 gaps 判定覆盖已建立。它不实施任意 bus translation，也不替代完整 RAM/装载区间审计、内核运行时 reservation 成功证据或 binding 校验。

## 本地验证与后续证据

```sh
python -I /home/lightjunction/Documents/GITHUB/android-mainline-port/tools/lg-g8/local-sim/tests/test_hardware_contract.py -v
```

实现阶段 12 项测试全部通过；复审补充边界测试后最终 16 项全部通过。覆盖 absent 与有效 IOMMU、悬空/重复 phandle、畸形 specifier、禁用祖先/provider、不同 PHY 参数宽度、legacy `usb-phy`、DMA 设备选择声明、非 ARM provider 不误解为 SID/mask、策略冲突/unknown、DT bootargs 终止边界、Linux 参数引号与 `--`、空强制命令行/畸形选中配置、敏感值过滤、32-bit reg 与 framebuffer 范围/stride、禁用或畸形保留区、64 位区间溢出，以及实际 CLI 的 libfdt 校验、哈希拒绝、独占输出、0600 权限和输入目录模块投毒防护。

实际最终审计：两份 `fdt_check_full=0`；运行树选出 47 个相关节点，候选 16 个；两份均无 phandle 索引错误、所选引用解析错误或固定 reservation 解析错误。这些数字覆盖工具选取范围，不是全树驱动或硬件认证。未运行 CI。

本次没有可由独立 LG 证据证明的精确板级修正，所以保留 DTS。下一步所缺证据是 QMP 电源/复用与预期 USB 速度路径、实际 SMMU attach/domain 和 DMA 数据流，以及可信的显示布局与 clock/interconnect 状态；应在既定设备操作授权和恢复条件下另行验证，不能由本报告自动进入启动实验。公开材料只保留必要声明和哈希，不发布序列号、MAC、校准内容、完整 bootargs 或原始设备日志。
