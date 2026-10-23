# NOTICE

## 上游项目

本项目是 **DeepSeek Remote Bridge**，一个独立的 DeepSeek Harness 远程桥。
它建立在 MIT 许可的 [Codex Remote Bridge](https://github.com/dizzynote-cell/codex-remote-bridge)
（作者 xiyannan）之上：网页结构、移动端布局、Markdown 安全渲染、附件待发区与桥接思路
复用了该项目，执行层则按 DeepSeek Harness 官方 SDK 协议独立实现。

原始版权与许可声明保留在 `LICENSE` 中。

## 第三方资产

| 文件 | 项目 | 许可 |
|---|---|---|
| `web/lib/markdown-it-15.0.3.min.js` | [Markdown-It](https://github.com/markdown-it/markdown-it) 15.0.3 | MIT，见 `web/lib/markdown-it.LICENSE.txt` |

## DeepSeek Harness

DeepSeek Harness 及其运行时、SDK、账户服务、模型与商标均属 DeepSeek。
本项目只通过官方接口调用本机已安装的 Harness，不分发其安装包、运行时二进制或用户数据，
也不代表 DeepSeek 官方出品。