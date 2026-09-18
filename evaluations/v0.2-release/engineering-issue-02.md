# 实验安装暴露的环境缺陷

lab-initial源码c74e445，操作员在38工具步/30模型轮、505.827秒主动Ctrl+C中断，已知usage 750666 tokens。主体实现与5项自测由Aster写成，shell自测曾通过；正式run_tests仍被openpyxl导入时读取/etc/mime.types的PermissionError阻塞。模型多次创建sitecustomize.py试图绕路。首测报告、失败、所有调用与成本保留，不能称为首次交付成功。

同时观察到pip的默认HOME/.cache被当工作区产物回写；新版把XDG/PIP缓存指向沙箱.tmp。修复仅给现有/etc/mime.types实际文件增加只读，不放开/etc目录/系统写入，不绕过禁网。pip安装过程中遍历宿主editable /home/dzh/Aster/src产生的冲突检查警告属于独立限制：安装exit0且依赖可用，不为消除提示放宽宿主源码访问。

一次框架修复后的恢复继续同一需求/服务/会话，剩余42工具步/15轮/994秒；候选临时补丁由Aster自己清理，操作员不修产品实现。该恢复明确属于工程干预，不归为服务故障恢复或首次自主成功。
