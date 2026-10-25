# Codex Cloud 构建交接

目标仍是 LG G8 的主线 Linux 适配。用户授权使用 **OpenAI 托管的 Codex Cloud** 算力和互联网。
不要使用用户的本机、GitHub Actions、Codespaces、个人 VPS 或其他云账户来承担编译。
不进行手机操作，不运行 adb/fastboot，不透传 USB，不读取或上传原始设备备份。

## 当前证据

- 同一 Linux 7.3.0-sm8150 内核已在本机 QEMU 中运行。
- 缺失 musl 的旧 initramfs 故障已复现并修复。
- 标准 EDK2 Shell、外部 QEMU DTB、新 `/init`、USB getty 和实际 shell 命令已一起验证。
- shell 退出后关闭、重开 ttyACM0，新会话能读取旧会话写入的数据。
- `devices/lg-alphalm/sm8150-lg-alphalm-board.dts` 与修复后 runtime FDT 的全部固定保留区匹配，无候选重叠。
- 真实 LG 板级固件尚未在 QEMU 中运行；QEMU virt 不能代替 SM8150 硬件验收。

## 在云端真正执行的工作

1. 记录实际 CPU/架构、可用内存/cgroup 限额、磁盘和工具版本；不要打印环境变量或凭据。
2. 从官方来源取得 Linux 7.3 源码，记录 URL 和 SHA-256。当前本机源码没有 Git 元数据，不能伪造其 commit。
3. 用 `configs/linux-7.3-tiny.config` 作为配置基线，执行 olddefconfig，记录编译器自动引起的变化。
   复制本目录完整板级 DTS，编译 Image 和该 DTB。使用 LLVM ARM64 工具链，按云端实际资源选择并发数。
   不能将本机 Image 的旧 SHA-256 冒充本轮云端产物的哈希。
4. 用 `tools/lg-g8/local-sim/build_usb_modules.py` 为本轮内核编译匹配的 dummy_hcd/cdc-acm。
5. 从官方 Alpine ARM64 minirootfs 构建最小干净 cpio，保留 BusyBox、匹配的 musl 库、必要目录及 applet 链接。
   不需要原手机 ramdisk，也不需要原有 `/init`。`native-init/verify.py` 会加入真正的诊断 `/init` 与 diag-login。
6. 固件源码见本目录 `uefi/source.json`。在指定 commit 上初始化需要的公开子模块，
   先应用 `uefi/diagnostic-baseline.patch`，再应用 `tools/lg-g8/local-sim/uefi-profile/standard-esp.patch`。
   前一个补丁已在临时副本上验证能逐字节重建本机的五个源文件。
   如果运行 `uefi-profile/validate.py`，应在应用 standard ESP 补丁之前运行；它会在临时副本里应用该补丁。
   完成 Stuart setup/update 后，从固件根目录使用实际 NoSb 构建入口：

   ```sh
   python3 Platforms/SurfaceDuo1Pkg/PlatformBuildNoSb.py \
     TARGET=RELEASE TARGET_DEVICE=lg-alpha \
     BLD_RELEASE_LG_ALPHA_STANDARD_ESP=TRUE \
     MAX_CONCURRENT_THREAD_NUMBER=4 --SKIPPOSTBUILD
   ```

   `BLD_RELEASE_` 参数由 Stuart 转成底层 EDK2 的 `-D LG_ALPHA_STANDARD_ESP=TRUE`。
   顶层 `build_uefi.py` 不接受 `-D`，不要直接给它加该参数。
   `--SKIPPOSTBUILD` 只跳过 Android boot.img 打包；FD 与 Shell.efi 仍须实际编译。
   内核与固件构建顺序执行，避免同时各用四个编译作业。不要仅做分支解析就说完成了编译。
7. 在云端安装 QEMU AArch64 和相应 EDK2/QEMU 固件。用新编译的内核、模块和同一 Shell 跑实际模拟：
   标准 Shell → EFI stub → 匹配 QEMU virt 的外部 DTB → `/init` → USB shell。
   可用 `native-init/verify.py --shell ... --firmware ... --qemu-dtb ...`；
   同时显式传入本轮的 `--kernel`、`--initramfs`、`--usb-module-dir` 和 `--out`。
   不调用依赖历史本机镜像和备份的 `run_all.py`。
   外部 QEMU DTB 按 `verify_external_dtb.py` 的 dumpdtb 参数生成并设置相同 model 标记。
   不得把 LG 的 DTB 放进 QEMU virt 来冒充设备仿真。
8. 实际执行 UID/架构/算术/文件读写，退出并重开串口，保留不同会话 PID、原始日志和逐项结果。
   真正命令结果不能直接包含在发送命令的原文里，以排除终端回显。
9. 导出干净诊断 cpio 和标准 Shell ESP：内核、LG DTB、initramfs、Shell、startup.nsh。
   干净包不能包含 dummy 模块或测试脚本。测试用 QEMU DTB 不能混入手机候选。
10. 保存产物、完整构建日志和 SHA-256 manifest，作为 Codex 可下载产物返回。
    只将可复现的源码/构建脚本/必要的文字报告作为代码变更；不要把镜像提交到 Git。

如果官方源码、工具链、下载或平台构建有实质失败，保留准确错误和已完成产物，继续解决可处理部分。
不要将只写了脚本、任务已提交、单一 parser 通过或文字描述当作已编译、已启动。

## 固有边界

`SurfaceDuo1NoSb.dsc` 关闭自动默认密钥，但已有 PK 仍可能影响 Secure Boot。
BootOrder/BootNext 和 LG PHY/供电/中断状态未读取。不得声称这些被通用 QEMU 验证。
Omarchy、显示和触控也没有完成验收。云端任务产物属于待硬件验证的候选。

本机绝对路径不是云端输入。所有新脚本应接受路径参数或自行获取公开依赖，
不用本机 `/home/lightjunction/...`、备份文件或不存在的输出目录来跳过实际构建。
