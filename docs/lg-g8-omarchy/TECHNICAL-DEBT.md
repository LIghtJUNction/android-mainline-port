# LG G8 临时措施与退出条件

本表统一记录为首次启动调试引入的临时措施。尚未实现的外设属于功能状态，不列为技术债。状态及下一项设备验收见 [STATUS.md](STATUS.md)。

| 临时措施及位置 | 原因与长期做法 | 当前影响 | 退出条件 |
| --- | --- | --- | --- |
| Android 已知 Image 的两指令 reboot reason 修复；`tools/lg-g8/local-sim/reboot-reason/` | LG ABL 的普通软件 fastboot 分支受限；借用现有空 LAF 的下载回退路径。后续应将已审计的重启映射纳入可维护的匹配内核源码构建，或使用已验证的标准入口。 | 限定精确 boot/kernel 哈希和被测 ABL/LAF 状态；ROM 更新不能直接复用。RAM 与落盘后的入口复测均通过，已返回 Android 并核对 root、槽位、marker 和分区哈希。 | 替代入口在同机完成软件进入 fastboot、返回 Android、普通重启及 recovery 回归后，再恢复或替换此补丁；保留原始备份。详见 [修复说明](../../tools/lg-g8/local-sim/reboot-reason/README.md)。 |
| stock FDT 给 ABL 处理、跳板另传 mainline FDT；`tools/lg-g8/local-sim/abl-handoff/` | mainline FDT 无 vendor overlay 所需 symbols；用于隔离 ABL DTBO 处理和 Linux 最终 DTB。长期需要明确真实装载、寄存器、缓存和 DTB 选择契约。 | 本机 CPU/QEMU 通过但手机失败；额外复制、重定位与固定地址增加交接不确定性，不能把它当已验证启动链。 | 取得可归属的早期真机执行证据，并由直接启动或已验证且可维护的交接路径替代；失败候选不得原样重试。 |
| tiny 调试内核关闭 DRM；外部工作区 `out/kernel-uefi-tiny/` | 当前只为隔离首次启动问题。显示功能需要单独在源码配置和板级驱动中完成。 | 当前镜像不能用于验证内屏与 Omarchy 图形界面。 | 最小启动和调试通道真机通过后，独立启用显示并完成原生接管、亮度和熄屏验收。 |
