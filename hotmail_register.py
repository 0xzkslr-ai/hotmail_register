#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hotmail/Outlook 邮箱自动注册脚本
基于 ruyipage (Firefox + WebDriver BiDi) — 原生 isTrusted 事件，绕过 webdriver 检测

经过真实页面端到端测试验证的完整注册流程：
  1. 个人数据导出许可 → 点击"同意并继续"
  2. 输入邮箱用户名 + 选择域名（@hotmail.com / @outlook.com）→ 下一步
  3. 创建密码 → 下一步
  4. 添加生日（年份输入框 + 月/日 Fluent UI 下拉）→ 下一步
  5. 添加姓名（#lastNameInput / #firstNameInput）→ 下一步
  6. 人机验证（hsprotect.net iframe 长按按钮）
     - 自动模式：ruyipage BiDi 在 iframe context 内执行原生长按
     - 手动模式：等待用户在浏览器窗口手动完成（默认备用）
     - 打码模式：2captcha API（需 --captcha-key）
  7. 等待注册完成

关键设计：每注册一个账号都使用全新的 Firefox 实例 + 独立 Profile，
避免 Cookie/Session/指纹复用被微软识别为同一浏览器。

依赖安装：
    pip install ruyiPage faker requests
    apt install firefox  (Ubuntu) 或指定 --firefox-path

用法：
    python hotmail_register.py                          # 注册 1 个账号
    python hotmail_register.py --count 3               # 注册 3 个账号
    python hotmail_register.py --username myname       # 指定用户名
    python hotmail_register.py --password MyPass@123   # 指定密码
    python hotmail_register.py --domain outlook.com    # 使用 outlook.com 域名
    python hotmail_register.py --output accounts.txt   # 指定输出文件
    python hotmail_register.py --headless              # 无头模式
    python hotmail_register.py --captcha-key YOUR_KEY  # 2captcha 全自动打码
    python hotmail_register.py --proxy http://user:pass@host:port
    python hotmail_register.py --firefox-path /usr/lib/firefox/firefox
    python hotmail_register.py --delay 30              # 账号间隔 30 秒（推荐）
"""

import argparse
import base64
import logging
import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
import time
from datetime import datetime

# ── 第三方依赖检查 ────────────────────────────────────────────────────────────
try:
    from faker import Faker
except ImportError:
    print("请先安装依赖: pip install ruyiPage faker requests")
    sys.exit(1)

try:
    from ruyipage import FirefoxOptions, FirefoxPage
    from ruyipage._functions.settings import Settings
    from ruyipage._bidi import browsing_context as bidi_context
    from ruyipage._bidi import input_ as bidi_input
except ImportError:
    print("请先安装 ruyiPage: pip install ruyiPage")
    sys.exit(1)

# ── 日志配置 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("hotmail_register.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────
SIGNUP_URL = (
    "https://signup.live.com/signup"
    "?cobrandid=ab0455a0-8d03-46b9-b18b-df2f57b9e44c"
    "&contextid=7354FDAB8CB435DB"
    "&opid=DDEDCCB4AA202B2D"
    "&bk=1775907821"
    "&lw=dob,flname,wld"
    "&uiflavor=web"
    "&fluent=2"
    "&client_id=00000000487A244A"
    "&lic=1"
    "&mkt=ZH-CN"
    "&lc=2052"
)

fake = Faker(["en_US"])


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def gen_password(length: int = 12) -> str:
    """生成符合微软密码要求的随机密码（大写+小写+数字+特殊字符）"""
    chars = (
        random.choices(string.ascii_uppercase, k=2)
        + random.choices(string.ascii_lowercase, k=4)
        + random.choices(string.digits, k=3)
        + random.choices("@#$%!&*", k=1)
        + random.choices(string.ascii_letters + string.digits, k=length - 10)
    )
    random.shuffle(chars)
    return "".join(chars)


def gen_account(username=None, password=None, domain="hotmail.com") -> dict:
    """生成随机账号信息"""
    first_name = fake.first_name()
    last_name = fake.last_name()
    if not username:
        base = (first_name + last_name).lower()
        base = "".join(c for c in base if c.isalnum())[:16]
        username = base + str(random.randint(100, 9999))
    if not password:
        password = gen_password()
    return {
        "email": f"{username}@{domain}",
        "username": username,
        "password": password,
        "first_name": first_name,
        "last_name": last_name,
        "birth_year": random.randint(1978, 2000),
        "birth_month": random.randint(1, 12),
        "birth_day": random.randint(1, 28),
        "domain": domain,
    }


def save_account(account: dict, output_file: str):
    """将成功注册的账号追加写入文件"""
    line = (
        f"邮箱: {account['email']} | "
        f"密码: {account['password']} | "
        f"姓名: {account['last_name']} {account['first_name']} | "
        f"生日: {account['birth_year']}-{account['birth_month']:02d}-{account['birth_day']:02d} | "
        f"注册时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(line)
    log.info(f"账号已保存: {account['email']}")


def find_firefox() -> str:
    """自动查找 Firefox 可执行文件路径"""
    for p in [
        "/usr/lib/firefox/firefox",
        "/usr/bin/firefox",
        "/snap/bin/firefox",
        "/usr/local/bin/firefox",
    ]:
        if os.path.exists(p):
            return p
    # 尝试 which
    try:
        result = subprocess.run(["which", "firefox"], capture_output=True, text=True)
        path = result.stdout.strip()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    return None


# ── 2captcha 打码 ─────────────────────────────────────────────────────────────

def solve_2captcha(api_key: str, page_url: str, timeout: int = 120):
    """调用 2captcha API 解决 PerimeterX/HUMAN 验证码，返回 token 或 None"""
    try:
        import requests
    except ImportError:
        log.warning("requests 未安装，无法使用 2captcha")
        return None
    log.info("提交 2captcha 任务...")
    try:
        r = requests.post("https://2captcha.com/in.php", data={
            "key": api_key, "method": "funcaptcha",
            "publickey": "PXzC5j78di", "pageurl": page_url, "json": 1,
        }, timeout=30)
        res = r.json()
        if res.get("status") != 1:
            log.warning(f"2captcha 提交失败: {res}")
            return None
        task_id = res["request"]
        log.info(f"2captcha 任务 ID: {task_id}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            r2 = requests.get(
                f"https://2captcha.com/res.php?key={api_key}&action=get&id={task_id}&json=1",
                timeout=30
            )
            res2 = r2.json()
            if res2.get("status") == 1:
                log.info("2captcha 解码成功")
                return res2["request"]
            if res2.get("request") != "CAPCHA_NOT_READY":
                log.warning(f"2captcha 错误: {res2}")
                return None
        log.warning("2captcha 超时")
    except Exception as e:
        log.warning(f"2captcha 异常: {e}")
    return None


# ── 核心注册类 ────────────────────────────────────────────────────────────────

class HotmailRegistrar:
    """
    单次注册器：每个实例对应一个独立的 Firefox 进程 + 独立 Profile。
    注册完成后调用 quit() 关闭浏览器并清理临时 Profile。
    """

    def __init__(self, args):
        self.args = args
        self.page = None
        self.driver = None
        self._profile_dir = None
        self._own_profile = True
        self._ff_process = None  # Firefox 进程句柄，用于精确退出

    def _init_browser(self):
        """初始化 Firefox + ruyipage，使用独立的临时 Profile"""
        import socket
        Settings.bidi_timeout = 30

        # ★ 关键修复：手动找空闲端口并预先设置，绕过 ruyipage 单例模式的 bug
        # ruyipage 的 Firefox 单例以 address 为 key，如果端口相同会复用旧实例
        free_port = None
        for port in range(9224, 9400):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(('127.0.0.1', port))
                    free_port = port
                    break
            except OSError:
                continue
        if free_port is None:
            free_port = 9224  # fallback

        opts = FirefoxOptions()
        opts.set_port(free_port)  # 预先设置端口，确保单例 key 唯一

        # Firefox 路径
        ff_path = self.args.firefox_path or find_firefox()
        if ff_path:
            opts.set_browser_path(ff_path)
            log.info(f"Firefox 路径: {ff_path}")
        else:
            log.warning("未找到 Firefox，将使用默认路径")

        if self.args.headless:
            opts.headless(True)

        # 代理设置
        if self.args.proxy:
            try:
                opts.set_proxy(self.args.proxy)
                log.info(f"代理: {self.args.proxy}")
            except Exception as e:
                log.warning(f"代理设置失败: {e}")

        log.info(f"启动 Firefox（端口 {free_port}）...")
        self.page = FirefoxPage(opts)
        self.driver = self.page._driver._browser_driver

        # 保存 Firefox 进程句柄
        try:
            ff_inst = getattr(self.page, '_firefox', None) or getattr(
                getattr(self.page, 'browser', None), None, None)
            if ff_inst and hasattr(ff_inst, '_process'):
                self._ff_process = ff_inst._process
        except Exception:
            pass

        log.info(f"Firefox 已启动（端口 {free_port}，全新 Profile）")

    def quit(self):
        """关闭浏览器，等待进程完全退出，清理临时 Profile"""
        ff_process = self._ff_process

        # 调用 ruyipage 的 quit
        try:
            if self.page:
                self.page.quit()
        except Exception:
            pass

        # 等待 Firefox 进程完全退出（最多 10 秒）
        if ff_process:
            try:
                ff_process.wait(timeout=10)
                log.debug("Firefox 进程已退出")
            except Exception:
                # 超时则强制杀掉特定 PID
                pid = getattr(ff_process, 'pid', None)
                if pid:
                    try:
                        subprocess.run(["kill", "-9", str(pid)], capture_output=True)
                        ff_process.wait(timeout=3)
                    except Exception:
                        pass

        # ★ 关键修复：清除 Firefox 单例缓存，确保下次创建全新实例
        # ruyipage 的 Firefox 类使用单例模式（_BROWSERS 字典），
        # 如果不清除，下次会复用已经 quit 的旧实例导致 WebSocket 失败
        try:
            from ruyipage._base.browser import Firefox as _Firefox
            with _Firefox._lock:
                # 删除所有已退出的实例
                dead_addrs = [addr for addr, inst in _Firefox._BROWSERS.items()
                              if not getattr(inst, '_initialized', False) or
                              (hasattr(inst, '_driver') and inst._driver and
                               not getattr(inst._driver, '_is_running', True))]
                for addr in dead_addrs:
                    _Firefox._BROWSERS.pop(addr, None)
                # 也删除全部（最安全）
                _Firefox._BROWSERS.clear()
                log.debug("已清除 Firefox 单例缓存")
        except Exception as e:
            log.debug(f"清除单例缓存失败: {e}")

        self.page = None
        self.driver = None
        self._ff_process = None
        log.debug("Firefox 已关闭")

    # ── 内部辅助 ──────────────────────────────────────────────────

    def _ss(self, name="debug"):
        """保存截图到当前目录"""
        path = f"reg_{name}_{int(time.time())}.png"
        try:
            data = self.page.screenshot(as_bytes=True)
            if data:
                with open(path, "wb") as f:
                    f.write(data)
                log.debug(f"截图: {path}")
        except Exception:
            pass

    def _wait(self, sel, timeout=12):
        """等待元素出现，返回元素或 None"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                el = self.page.ele(sel, timeout=2)
                if el and el.__class__.__name__ != "NoneElement":
                    return el
            except Exception:
                pass
            time.sleep(0.4)
        return None

    def _type(self, ele, text):
        """模拟人工逐字符输入，带随机延迟"""
        ele.click_self()
        time.sleep(random.uniform(0.2, 0.5))
        for ch in text:
            ele.input(ch, clear=False)
            time.sleep(random.uniform(0.06, 0.18))

    def _submit(self):
        """点击提交/下一步按钮"""
        for sel in ["css:button[type='submit']", "css:input[type='submit']"]:
            btn = self._wait(sel, timeout=4)
            if btn:
                btn.click_self()
                return
        log.warning("未找到提交按钮")

    def _get_contexts(self):
        """递归获取所有 browsing context"""
        all_ctxs = []
        seen = set()

        def recurse(ctx_id, depth):
            if depth > 5 or ctx_id in seen:
                return
            seen.add(ctx_id)
            try:
                result = bidi_context.get_tree(self.driver, root=ctx_id)
                for ctx in result.get("contexts", []):
                    cid = ctx["context"]
                    if cid not in seen:
                        all_ctxs.append((depth, ctx))
                        seen.add(cid)
                    for child in ctx.get("children", []):
                        ccid = child["context"]
                        if ccid not in seen:
                            all_ctxs.append((depth + 1, child))
                            seen.add(ccid)
                        recurse(ccid, depth + 2)
            except Exception:
                pass

        recurse(self.page._context_id, 0)
        return all_ctxs

    def _find_hs_context(self, max_wait=20):
        """
        找到 hsprotect iframe 的可见 context（有实际渲染内容）
        等待 iframe 加载完成，最多等待 max_wait 秒
        返回 context ID 字符串或 None
        """
        deadline = time.time() + max_wait
        while time.time() < deadline:
            all_ctxs = self._get_contexts()
            # 优先找 depth=1 且有实际截图内容的 hsprotect context
            for depth, ctx in all_ctxs:
                if "hsprotect" not in ctx.get("url", ""):
                    continue
                if depth != 1:
                    continue
                cid = ctx["context"]
                try:
                    result = bidi_context.capture_screenshot(self.driver, cid)
                    data = base64.b64decode(result.get("data", ""))
                    if len(data) > 1000:
                        log.info(f"找到可见 hsprotect context: {cid!r} ({len(data)} bytes)")
                        return cid
                except Exception:
                    pass
            log.debug("等待 hsprotect iframe 加载...")
            time.sleep(2)
        # 备用：返回第一个 hsprotect context
        all_ctxs = self._get_contexts()
        for depth, ctx in all_ctxs:
            if "hsprotect" in ctx.get("url", ""):
                log.info(f"使用备用 hsprotect context: {ctx['context']!r}")
                return ctx["context"]
        return None

    def _bidi_hold(self, ctx_id, x, y, hold_sec=10):
        """
        在指定 browsing context 内执行 BiDi 分步长按：
        mousedown → Python sleep → mouseup
        产生原生 isTrusted=true 事件
        """
        try:
            # 移动到目标位置
            move_action = [{
                "type": "pointer",
                "id": "mouse1",
                "parameters": {"pointerType": "mouse"},
                "actions": [
                    {"type": "pointerMove", "x": x, "y": y, "duration": 100},
                ]
            }]
            bidi_input.perform_actions(self.driver, ctx_id, move_action)
            time.sleep(0.3)

            # mousedown
            down_action = [{
                "type": "pointer",
                "id": "mouse1",
                "parameters": {"pointerType": "mouse"},
                "actions": [
                    {"type": "pointerDown", "button": 0},
                ]
            }]
            bidi_input.perform_actions(self.driver, ctx_id, down_action)

            # 保持按下状态（Python 层控制时间，不依赖 BiDi wait）
            log.info(f"  长按 {hold_sec}s ...")
            time.sleep(hold_sec)

            # mouseup
            up_action = [{
                "type": "pointer",
                "id": "mouse1",
                "parameters": {"pointerType": "mouse"},
                "actions": [
                    {"type": "pointerUp", "button": 0},
                ]
            }]
            bidi_input.perform_actions(self.driver, ctx_id, up_action)
            log.info("  长按释放")
            return True
        except Exception as e:
            log.warning(f"  BiDi 长按失败: {e}")
            return False

    def _handle_captcha(self):
        """
        处理人机验证码：
        1. 优先尝试 BiDi 原生长按（ruyipage 核心优势）
        2. 如配置 2captcha，提交打码任务
        3. 最终回退到手动模式
        返回 True 表示验证通过（或已提交手动处理）
        """
        # 等待 iframe 加载
        time.sleep(3)

        # 方式一：BiDi 原生长按
        ctx_id = self._find_hs_context(max_wait=20)
        if ctx_id:
            log.info("尝试 BiDi 长按验证码...")
            # 在 iframe context 内，按钮中心约在 (220, 45)
            coords = [(220, 45), (200, 45), (180, 45), (240, 45), (210, 50)]
            for x, y in coords:
                ok = self._bidi_hold(ctx_id, x, y, hold_sec=10)
                if ok:
                    # 等待服务端验证结果
                    time.sleep(4)
                    title = self.page.title or ""
                    url = self.page.url or ""
                    if "机器人" not in title:
                        log.info("  BiDi 长按验证码通过！")
                        return True
                    log.info(f"  BiDi 长按后仍在验证页: {title!r}")
                    # 短暂等待后重试
                    time.sleep(2)

        # 方式二：2captcha
        if self.args.captcha_key:
            log.info("尝试 2captcha 打码...")
            token = solve_2captcha(self.args.captcha_key, self.page.url or SIGNUP_URL)
            if token:
                log.info("2captcha 返回 token，尝试注入...")
                try:
                    self.page.run_js(
                        f"document.querySelector('[name=\"px-captcha\"]') && "
                        f"(document.querySelector('[name=\"px-captcha\"]').value = '{token}');"
                    )
                    time.sleep(2)
                    title = self.page.title or ""
                    if "机器人" not in title:
                        return True
                except Exception as e:
                    log.warning(f"token 注入失败: {e}")

        # 方式三：手动模式
        log.info("=" * 50)
        log.info("请在浏览器窗口中手动完成人机验证：")
        log.info("  长按'按住'按钮，直到进度条填满")
        log.info("完成后脚本将自动继续（最多等待 120 秒）")
        log.info("=" * 50)

        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(2)
            title = self.page.title or ""
            url = self.page.url or ""
            if "机器人" not in title:
                log.info("  手动验证完成！")
                return True
            remaining = int(deadline - time.time())
            if remaining % 20 == 0 and remaining > 0:
                log.info(f"  等待手动验证... 剩余 {remaining}s")

        log.warning("  手动验证超时")
        return False

    # ── 主注册流程 ────────────────────────────────────────────────

    def register_one(self, account: dict) -> bool:
        """
        执行一次完整的注册流程。
        返回 True 表示注册成功，False 表示失败。
        """
        try:
            # 打开注册页面
            self.page.get(SIGNUP_URL, wait="complete")
            time.sleep(random.uniform(2.0, 3.5))

            # ── 1. 同意数据许可 ────────────────────────────────────
            consent_btn = self._wait("#nextButton", timeout=10)
            if not consent_btn:
                # 尝试备用选择器
                consent_btn = self._wait("css:button[id='nextButton']", timeout=5)
            if not consent_btn:
                log.warning("  [1/5] 未找到同意按钮，尝试继续...")
            else:
                consent_btn.click_self()
                time.sleep(random.uniform(1.5, 2.5))
                log.info(f"  [1/5] 同意许可 ✓ ({self.page.title!r})")

            # ── 2. 输入用户名 ──────────────────────────────────────
            email_inp = self._wait("css:input[type='email']", timeout=12)
            if not email_inp:
                # 备用：name="MemberName"
                email_inp = self._wait("css:input[name='MemberName']", timeout=5)
            if not email_inp:
                log.error("  [2/5] 未找到用户名输入框")
                self._ss("no_email_input")
                return False

            self._type(email_inp, account["username"])
            time.sleep(0.5)

            # 选择域名
            domain_btn = self._wait("#domainDropdownId", timeout=5)
            if domain_btn:
                domain_btn.click_self()
                time.sleep(random.uniform(0.6, 1.0))
                target_domain = f"@{account['domain']}"
                for opt in self.page.eles("css:[role='option']"):
                    try:
                        if target_domain in opt.text:
                            opt.click_self()
                            break
                    except Exception:
                        pass
                time.sleep(0.5)

            self._submit()
            time.sleep(random.uniform(2.0, 3.0))
            title = self.page.title or ""
            url = self.page.url or ""
            log.info(f"  [2/5] 用户名 ✓ ({title!r})")

            # 检查用户名是否被拒绝（页面没有跳转，仍在创建账户页）
            if "创建你的 Microsoft 帐户" in title or "create" in title.lower():
                # 检查错误提示
                err_texts = []
                for err_sel in ["#MemberNameError", ".error", "[aria-live='assertive']"]:
                    try:
                        err_el = self.page.ele(f"css:{err_sel.lstrip('css:')}", timeout=2)
                        if err_el and err_el.text:
                            err_texts.append(err_el.text)
                    except Exception:
                        pass
                err_msg = " | ".join(err_texts) if err_texts else "未知错误"
                log.warning(f"  用户名被拒绝: {err_msg}，跳过")
                self._ss("username_rejected")
                return False

            # ── 3. 输入密码 ─────────────────────────────────────────────────────
            pwd_inp = self._wait("css:input[type='password']", timeout=10)
            if not pwd_inp:
                log.error("  [3/5] 未找到密码输入框")
                self._ss("no_pwd_input")
                return False

            self._type(pwd_inp, account["password"])
            time.sleep(0.5)
            self._submit()
            time.sleep(random.uniform(1.5, 2.5))
            log.info(f"  [3/5] 密码 ✓ ({self.page.title!r})")

            # ── 4. 填写生日 ───────────────────────────────────────
            year_inp = self._wait("css:input[name='BirthYear']", timeout=10)
            if not year_inp:
                log.error("  [4/5] 未找到生日输入框")
                return False

            year_inp.input(str(account["birth_year"]), clear=True)
            time.sleep(0.5)

            # 月份下拉（精确匹配，避免 "1" 匹配 "10月"）
            for sel in ["#BirthMonthDropdown", "css:button[name='BirthMonth']"]:
                mb = self._wait(sel, timeout=3)
                if mb:
                    mb.click_self()
                    time.sleep(random.uniform(0.7, 1.0))
                    target_month = f"{account['birth_month']}月"
                    for opt in self.page.eles("css:[role='option']"):
                        try:
                            if opt.text.strip() == target_month:
                                opt.click_self()
                                break
                        except Exception:
                            pass
                    time.sleep(0.5)
                    break

            # 日期下拉（精确匹配，避免 "1" 匹配 "10日"）
            for sel in ["#BirthDayDropdown", "css:button[name='BirthDay']"]:
                db = self._wait(sel, timeout=3)
                if db:
                    db.click_self()
                    time.sleep(random.uniform(0.7, 1.0))
                    target_day = f"{account['birth_day']}日"
                    for opt in self.page.eles("css:[role='option']"):
                        try:
                            if opt.text.strip() == target_day:
                                opt.click_self()
                                break
                        except Exception:
                            pass
                    time.sleep(0.5)
                    break

            self._submit()
            # 生日页和姓名页标题相同，等待姓名输入框出现来确认进入下一步
            time.sleep(random.uniform(2.0, 3.0))
            log.info(f"  [4/5] 生日 ✓ ({self.page.title!r})")

            # ── 5. 填写姓名 ────────────────────────────────────────
            last_inp = self._wait("#lastNameInput", timeout=10)
            if last_inp:
                last_inp.input(account["last_name"], clear=True)
                time.sleep(0.5)
                first_inp = self._wait("#firstNameInput", timeout=5)
                if first_inp:
                    first_inp.input(account["first_name"], clear=True)
                    time.sleep(0.5)
                self._submit()
                time.sleep(random.uniform(2.0, 3.0))
                title = self.page.title or ""
                log.info(f"  [5/5] 姓名 ✓ ({title!r})")

                # 检测帐户创建被阻止（IP 封禁）
                if "被阻止" in title or "blocked" in title.lower():
                    log.warning("  帐户创建被阻止（当前 IP 被微软封禁，请更换 IP 或稍后再试）")
                    self._ss("blocked")
                    return False

            # ── 6. 人机验证 ───────────────────────────────────────
            title = self.page.title or ""
            if "机器人" in title:
                log.info("  [6] 进入人机验证页面...")
                if not self._handle_captcha():
                    log.warning("  [6] 验证码未通过")
                    self._ss("captcha_failed")
                    return False

            # ── 7. 等待注册完成 ───────────────────────────────────
            log.info("  [7] 等待注册完成...")
            for i in range(30):
                time.sleep(1)
                url = self.page.url or ""
                title = self.page.title or ""
                if any(x in url for x in ["outlook.com", "oauth20_authorize", "login.live.com"]):
                    log.info(f"  ✅ 注册成功！({i+1}s)")
                    return True
                if "机器人" in title:
                    continue
                if i % 5 == 4:
                    log.debug(f"  等待中... {i+1}s | {title!r}")

            # 最后判断
            url = self.page.url or ""
            if "signup.live.com" not in url:
                log.info("  ✅ 注册可能成功（已离开注册页面）")
                return True

            log.warning(f"  注册超时，当前: {self.page.title!r}")
            self._ss("timeout")
            return False

        except Exception as e:
            log.error(f"  注册异常: {e}")
            import traceback
            traceback.print_exc()
            self._ss("exception")
            return False


# ── 命令行入口 ────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Hotmail/Outlook 邮箱自动注册脚本（ruyipage + Firefox BiDi）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--count", type=int, default=1, metavar="N",
                   help="注册账号数量（默认 1）")
    p.add_argument("--username", type=str, default=None,
                   help="指定用户名（仅 --count 1 时有效）")
    p.add_argument("--password", type=str, default=None,
                   help="指定密码（所有账号共用）")
    p.add_argument("--domain", type=str, default="hotmail.com",
                   choices=["hotmail.com", "outlook.com"],
                   help="邮箱域名（默认 hotmail.com）")
    p.add_argument("--output", type=str, default="accounts.txt",
                   help="账号保存文件（默认 accounts.txt）")
    p.add_argument("--headless", action="store_true",
                   help="无头模式（不显示浏览器窗口）")
    p.add_argument("--proxy", type=str, default=None,
                   help="代理地址，如 http://user:pass@host:port")
    p.add_argument("--captcha-key", type=str, default=None, dest="captcha_key",
                   help="2captcha API Key，用于全自动验证码处理")
    p.add_argument("--firefox-path", type=str, default=None, dest="firefox_path",
                   help="Firefox 可执行文件路径（默认自动查找）")
    p.add_argument("--delay", type=float, default=15.0,
                   help="多账号注册间隔秒数（默认 15，建议 30+）")
    return p.parse_args()


def main():
    args = parse_args()

    log.info("=" * 60)
    log.info("Hotmail 自动注册脚本 (ruyipage + Firefox BiDi)")
    log.info(f"计划注册: {args.count} 个账号 | 域名: {args.domain}")
    log.info(f"无头模式: {args.headless} | 2captcha: {'已配置' if args.captcha_key else '未配置'}")
    log.info(f"账号间隔: {args.delay}s | 输出文件: {args.output}")
    log.info("=" * 60)

    success = 0
    failed = 0

    for i in range(args.count):
        log.info(f"\n{'='*40}")
        log.info(f"[{i+1}/{args.count}] 注册第 {i+1} 个账号")

        # 生成账号信息
        username = args.username if (args.count == 1 and args.username) else None
        account = gen_account(username, args.password, args.domain)
        log.info(f"  账号: {account['email']}")
        log.info(f"  密码: {account['password']}")
        log.info(f"  姓名: {account['last_name']} {account['first_name']}")
        log.info(f"  生日: {account['birth_year']}-{account['birth_month']:02d}-{account['birth_day']:02d}")

        # ★ 每个账号使用独立的 Firefox 实例 + 独立 Profile
        # 先创建注册器（不启动 Firefox），然后在 register_one 内部启动
        registrar = HotmailRegistrar(args)
        ok = False
        try:
            # 在此处启动 Firefox（上一个已经在 finally 里 quit 完毕）
            # 如果 WebSocket 连接失败，最多重试 3 次
            for attempt in range(3):
                try:
                    registrar._init_browser()
                    # 用实际 BiDi 命令测试连接（获取页面标题）
                    test_title = registrar.page.title
                    log.info(f"  Firefox 连接测试通过: title={test_title!r}")
                    break
                except Exception as e:
                    log.warning(f"  Firefox 启动失败 ({attempt+1}/3): {e}")
                    try:
                        registrar.quit()
                    except Exception:
                        pass
                    time.sleep(6)
            else:
                log.error("  Firefox 多次启动失败，跳过此账号")
                ok = False
                continue
            ok = registrar.register_one(account)
        except KeyboardInterrupt:
            log.info("\n用户中断")
            registrar.quit()
            break
        except Exception as e:
            log.error(f"  注册器异常: {e}")
            import traceback; traceback.print_exc()
        finally:
            registrar.quit()
            # 等待端口完全释放
            time.sleep(4)

        if ok:
            save_account(account, args.output)
            success += 1
            log.info(f"  ✅ 注册成功")
        else:
            failed += 1
            log.warning(f"  ❌ 注册失败")

        # 账号间隔（最后一个不需要等待）
        if i < args.count - 1:
            delay = args.delay + random.uniform(0, 5)
            log.info(f"  等待 {delay:.1f}s 后注册下一个（让微软服务器冷却）...")
            time.sleep(delay)

    log.info("\n" + "=" * 60)
    log.info(f"注册完成: 成功 {success} 个，失败 {failed} 个")
    log.info(f"账号文件: {args.output}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
