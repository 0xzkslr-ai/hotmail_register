# Outlook / Hotmail 注册脚本

本仓库用于管理 `hotmail_register.py` 脚本及其说明文档。仓库策略为：**只追踪脚本文件、`README.md` 和 `.gitignore`**，运行生成物、日志、账号输出文件和虚拟环境均不纳入版本控制。

## 当前目录结构

```text
.
├─ hotmail_register.py   # 主脚本
├─ README.md             # 使用说明
└─ .gitignore            # Git 忽略规则
```

## 环境配置

### 运行环境

- Python：建议 `3.13+`
- 浏览器：Firefox
- 操作系统：Windows PowerShell 已验证可用

### 依赖包

脚本中直接使用的核心依赖：

- `ruyiPage`
- `Faker`
- `requests`

当前本地虚拟环境中可见版本参考：

- `Python 3.13.12`
- `ruyiPage 1.0.13`
- `Faker 40.13.0`
- `requests 2.33.1`

### 创建虚拟环境

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install ruyiPage Faker requests
```

如果 PowerShell 禁止激活脚本，可临时执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Firefox 配置

请先安装 Firefox。若脚本无法自动定位 Firefox，可在运行时显式指定：

```powershell
python .\hotmail_register.py --firefox-path "C:\Program Files\Mozilla Firefox\firefox.exe"
```

### 可选配置

- 需要全自动验证码处理时，提供 `--captcha-key`
- 需要走代理时，提供 `--proxy http://user:pass@host:port`
- 默认会生成 `hotmail_register.log`、`accounts.txt`、`reg_*.png` 等运行文件，这些都已被 `.gitignore` 忽略

## 用法

### 基本运行

```powershell
python .\hotmail_register.py
```

### 常用示例

注册多个账号：

```powershell
python .\hotmail_register.py --count 3
```

指定用户名、密码和域名：

```powershell
python .\hotmail_register.py --username myname --password "MyPass@123" --domain outlook.com
```

指定输出文件：

```powershell
python .\hotmail_register.py --output accounts.txt
```

无头模式运行：

```powershell
python .\hotmail_register.py --headless
```

使用代理与 2captcha：

```powershell
python .\hotmail_register.py --proxy http://user:pass@host:port --captcha-key YOUR_KEY
```

多账号时设置注册间隔：

```powershell
python .\hotmail_register.py --count 5 --delay 30
```

## 输出说明

- `accounts.txt`：保存注册成功的账号信息
- `hotmail_register.log`：运行日志
- `reg_*.png`：异常或调试截图

以上文件默认不追踪。
