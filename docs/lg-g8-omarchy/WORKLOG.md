# LG G8 调试结论变更

按结论变化记录，当前验收与下一项设备实验以 [STATUS.md](STATUS.md#当前验收与下一步) 为准。原始设备资料留在私有工作区，不提交。

## 2026-10-26：明确看门狗交接证据与恢复缺口

只读本地调查确认 stock LinuxLoader 的成功路径在 `ExitBootServices` 返回后显式配置并启用 APSS watchdog，因此仅追问是否继承 Android 看门狗不足以描述启动链。尚未证明失败候选经过该路径、最终 Linux 入口的硬件状态或超时复位后的启动去向。

实际 `lg-native-3` initramfs 没有内核模块，120 秒 reboot 仅在 `/probe-init` 执行后启动；当前 tiny / v50 配置中的高通驱动为模块，但配置没有被候选 manifest 锁定为构建输入。将驱动改为内置仍不能证明接管前挂死可恢复。文件指纹、代码位置及限制集中记录在 [看门狗与自动恢复证据](WATCHDOG-RECOVERY.md)，临时计时器的退出条件记入 [技术债](TECHNICAL-DEBT.md)。

本次未访问手机、修改配置、构建镜像或启动候选。软件 fastboot 往返的既有验收继续有效；pmsg 校准仍受此前自动审批拒绝阻塞。无人值守候选迭代与主线 Linux / Omarchy 真机验收均未完成。
