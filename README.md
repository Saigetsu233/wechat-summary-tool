# 微信群聊 AI 总结工具

用 AI 自动总结微信群聊记录，支持按群聊和时间范围筛选，一键生成结构化摘要。

---

## 功能介绍

- **自动读取微信数据**：自动找到微信数据库，无需手动导出聊天记录
- **选择群聊和时间范围**：支持任意群聊，自由选择开始和结束日期
- **跨分库读取**：自动合并 `message_0.db`、`message_1.db` 等全部消息分片，近期与历史消息不会漏读
- **准确识别发言人**：通过消息表的发送者 ID 与联系人数据库匹配备注名/昵称，避免把被提及者误认为发言人
- **AI 智能总结**：支持 DeepSeek 官方 API 和 NVIDIA API Catalog，生成结构化的群聊摘要
- **免费模型接入**：可使用 NVIDIA 原型阶段免费端点，并自由修改模型名
- **长记录不截断**：聊天内容自动分段提炼并汇总，避免只总结最后一部分
- **微信纯文本排版**：默认使用 emoji 和纯文本小标题，复制进微信群无需再整理 Markdown
- **自动管理临时空间**：系统盘空间不足时自动改用程序所在盘存放解密临时文件，退出时清理
- **自定义提示词**：可修改 AI 的总结风格和格式
- **结果导出**：支持复制到剪贴板或保存为 TXT 文件

---

## 使用前提

- Windows 系统（仅支持 Windows）
- 微信电脑版 4.0 / 4.1 已安装并**保持登录状态**（已适配 4.1 新密钥结构）
- Python 3.8 或以上版本
- 拥有 [DeepSeek API Key](https://platform.deepseek.com/) 或 [NVIDIA API Catalog](https://build.nvidia.com/) API Key

---

## 安装方法

### 第一步：安装 Python

如果还没有安装 Python，前往 [python.org](https://www.python.org/downloads/) 下载安装。  
安装时勾选 **"Add Python to PATH"**。

### 第二步：安装依赖库

打开命令提示符（Win+R，输入 `cmd`），运行：

```
pip install tkcalendar pycryptodome requests psutil
```

### 第三步：下载本项目

点击页面右上角绿色的 **Code** 按钮 → **Download ZIP**，解压到任意文件夹。

---

## 使用方法

### 第一步：选择 AI 服务并填写 API Key

打开 `wechat_gui.py`，在界面底部选择 **DeepSeek 官方** 或 **NVIDIA API Catalog**，然后填入对应 Key。两套 Key 分开保存，切换服务商时会自动切换。

> 在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 注册后即可获取 API Key，格式为 `sk-xxxxxxxx`。
> 本地 Key 会保存在 `config.json` 中；该文件已加入 `.gitignore`，请勿提交或分享。仓库中的 `config.example.json` 仅作格式示例。

#### NVIDIA 免费接口

1. 登录 [NVIDIA API Catalog](https://build.nvidia.com/)，打开带 **Free Endpoint** 的文本模型。
2. 点击模型页的 **Generate API Key**。
3. 在工具中选择“NVIDIA API Catalog”并粘贴 Key。

默认模型为截图中的 `deepseek-ai/deepseek-v4-pro-0813`。模型名可以直接编辑；如果 NVIDIA 返回模型不存在，请从当前模型页复制最新的 `model` 值。免费模型、频率、额度和可用性可能变化，以 NVIDIA 页面当时显示为准。

NVIDIA 免费端点高负载时可能需要数分钟。工具对 NVIDIA 单次请求最多等待 240 秒；如果读取超时，会在状态栏提示并自动重试一次。

### 第二步：运行工具

双击 `wechat_gui.py`，或在命令行运行：

```
python wechat_gui.py
```

### 第三步：初始化

确保微信电脑版已登录，点击「**自动初始化**」按钮。  
工具会自动找到微信数据库，并根据微信版本选择密钥提取方式后完成解密
（需要微信保持运行）。微信 4.1 使用只读 `Config.Cipher` 扫描，无需管理员权限。

> 如果自动检测失败，点击「手动选择文件夹」，在微信设置 → 文件管理中找到数据存储路径，选择形如 `wxid_xxxxxxxx` 的文件夹。

### 第四步：选择群聊和时间

- 从下拉框中选择要总结的群聊
- 选择开始和结束日期（默认最近 7 天）

### 第五步：生成总结

点击「**生成总结**」，等待 AI 处理完成，结果会显示在下方文本框中。  
可以点击「复制到剪贴板」或「保存为 TXT」导出结果。

---

## 界面说明

![界面示意](https://via.placeholder.com/600x400?text=界面截图)

| 区域 | 说明 |
|------|------|
| 第一步：初始化 | 读取微信数据库，获取群聊列表 |
| 第二步：选择群聊 | 从下拉框选择要总结的群 |
| 第三步：时间范围 | 选择起止日期 |
| 生成总结 | 调用 AI 生成摘要 |
| 第四步：AI 服务 | 选择 DeepSeek 或 NVIDIA，填写 Key 和可编辑的模型名 |

---

## 常见问题

**Q：点击初始化提示"未能提取到密钥"**  
A：新版已兼容微信 4.1 的 `Config.Cipher` 密钥结构。请确保微信电脑版已打开并登录；
如果微信刚完成升级，请完全退出微信、重新打开并登录，等待主界面加载后再初始化。

**Q：自动检测数据目录失败**  
A：点击「手动选择文件夹」。在微信电脑版 → 设置 → 文件管理，找到"微信文件的存储位置"，进入该目录，选中形如 `wxid_xxxxxxxx` 的文件夹。

**Q：API Key 从哪里获取？**  
A：DeepSeek Key 在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 创建；NVIDIA Key 在 [NVIDIA API Catalog](https://build.nvidia.com/) 的模型页点击“Generate API Key”创建。

**Q：DeepSeek 提示 402 余额不足怎么办？**
A：可以充值，或在界面中直接切换到 NVIDIA API Catalog。工具会对 401、402、403、404、429 等常见错误给出中文提示。

**Q：NVIDIA 提示 `Read timed out`怎么办？**
A：这表示已经连上 NVIDIA，但模型在等待时间内没有返回完整结果，通常不是 Key 错误。新版会最多等待 240 秒并自动重试一次；仍失败时请稍后再试，或换用其他 Free Endpoint 模型。

**Q：API Key 会泄露吗？**  
A：Key 只明文保存在你本机且已被 Git 忽略的 `config.json` 中，不会上传到本项目仓库；生成总结时，程序会将当前 Key 作为身份凭证发送给你选择的 API。

**Q：支持个人聊天（非群聊）吗？**  
A：暂不支持，目前只能总结群聊记录。

---

## 注意事项

- 本工具通过读取本地微信数据库工作，**不会登录你的微信账号**，也不会发送任何消息
- 生成 AI 总结时，所选时间范围内的文本消息会发送给你选择的 DeepSeek 或 NVIDIA API，请确认群成员同意并遵守当地隐私法规
- 仅支持 Windows 微信 4.0 / 4.1 版本；微信后续若再次调整内部数据库结构，可能需要同步升级提取器
- 请勿将本工具用于非法用途

---

## 依赖库

| 库 | 用途 |
|----|------|
| tkcalendar | 日期选择控件 |
| pycryptodome | 解密微信数据库 |
| requests | 调用 DeepSeek / NVIDIA API |
| psutil | 自动定位微信数据目录 |

---

## 许可证

本项目采用 [MIT License](LICENSE)。
