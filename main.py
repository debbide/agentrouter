#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from seleniumbase import SB


USER_ENV_FILE = str(Path.home() / ".config" / "browser-automation-panel" / "scripts.env")
TASK_RESULT_PATH = (os.environ.get("TASK_RESULT_PATH") or "").strip()
TASK_SCREENSHOT_PATH = (os.environ.get("TASK_SCREENSHOT_PATH") or "").strip()
SCRIPT_REVISION = "2026-06-24-profile-mode"

SITE_URL = "https://agentrouter.org"
LOGIN_URL = "https://agentrouter.org/login"
WALLET_URL = "https://agentrouter.org/console/topup"
LOGIN_TEXT = "使用 GitHub 继续"
WAIT_AFTER_CLICK = 90.0
READY_WAIT = 2.0
USE_UC = False
TG_CHAT_ID = ""
TG_TOKEN = ""
TG_PROXY = ""

PAGE_LOAD_TIMEOUT = 20
SCRIPT_TIMEOUT = 15

# 调试截图推送间隔（秒），等待登录期间每隔此时间推一张截图
DEBUG_SCREENSHOT_INTERVAL = 15


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def load_env_file(env_file_path: str) -> bool:
    path = Path(env_file_path)
    try:
        if not path.exists():
            log(f"env file not found: {env_file_path}")
            return False
        loaded_any = False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded_any = True
        log(f"env file loaded: {env_file_path}")
        return loaded_any
    except Exception as exc:
        log(f"env file load failed: {env_file_path}: {exc}")
        return False


def refresh_config() -> None:
    global SITE_URL, LOGIN_URL, WALLET_URL, LOGIN_TEXT, WAIT_AFTER_CLICK, READY_WAIT, USE_UC
    global TG_CHAT_ID, TG_TOKEN, TG_PROXY

    SITE_URL = (os.environ.get("AGENTROUTER_SITE_URL") or "https://agentrouter.org").strip().rstrip("/")
    LOGIN_URL = (os.environ.get("AGENTROUTER_LOGIN_URL") or f"{SITE_URL}/login").strip()
    WALLET_URL = (os.environ.get("AGENTROUTER_WALLET_URL") or f"{SITE_URL}/console/topup").strip()
    LOGIN_TEXT = (os.environ.get("AGENTROUTER_LOGIN_TEXT") or "使用 GitHub 继续").strip()
    WAIT_AFTER_CLICK = float((os.environ.get("AGENTROUTER_WAIT_AFTER_CLICK") or "90").strip() or "90")
    READY_WAIT = float((os.environ.get("AGENTROUTER_READY_WAIT") or "2").strip() or "2")
    USE_UC = False  # 硬锁，不从环境变量读取
    TG_CHAT_ID = (os.environ.get("TG_CHAT_ID") or os.environ.get("CHAT_ID") or "").strip()
    TG_TOKEN = (
        os.environ.get("TG_BOT_TOKEN")
        or os.environ.get("TG_TOKEN")
        or os.environ.get("BOT_TOKEN")
        or ""
    ).strip()
    TG_PROXY = (
        os.environ.get("TG_PROXY")
        or os.environ.get("TG_PROXY_URL")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or ""
    ).strip()


def host_from_url(url: str) -> str:
    return urlparse(url or "").netloc.lower()


def path_from_url(url: str) -> str:
    path = urlparse(url or "").path.rstrip("/")
    return path or "/"


def is_target_host(url: str) -> bool:
    host = host_from_url(url)
    target_host = host_from_url(SITE_URL)
    return bool(host and target_host and (host == target_host or host.endswith("." + target_host)))


def current_url_safe(sb: SB) -> str:
    try:
        return sb.get_current_url() or ""
    except Exception as exc:
        return f"<unavailable: {exc}>"


def normalize_socks_proxy(proxy: str) -> str:
    value = (proxy or "").strip()
    for prefix in ("socks5h://", "socks5://", "http://", "https://"):
        if value.lower().startswith(prefix):
            return value[len(prefix):]
    return value


# ─────────────────────────────────────────────
# TG 文字推送
# ─────────────────────────────────────────────

def send_tg_message_via_curl(text: str) -> bool:
    if not TG_PROXY:
        return False
    proxy = normalize_socks_proxy(TG_PROXY)
    if not proxy:
        return False
    cmd = [
        "curl", "-sS", "--max-time", "25",
        "--socks5-hostname", proxy,
        "-X", "POST",
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        "--data-urlencode", f"chat_id={TG_CHAT_ID}",
        "--data-urlencode", f"text={text}",
        "--data-urlencode", "disable_web_page_preview=true",
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        body = (proc.stdout or "").strip()
        if '"ok":true' in body.replace(" ", ""):
            log(f"TG text push sent via curl+socks ({proxy})")
            return True
        log(f"TG curl response not ok: {body[:220]}")
    except Exception as exc:
        log(f"TG curl push failed: {exc}")
    return False


def send_tg_message(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        log("TG not configured, skipping text push")
        return
    message = (text or "").strip()
    if not message:
        return
    if send_tg_message_via_curl(message):
        return
    try:
        payload = urllib.parse.urlencode(
            {"chat_id": TG_CHAT_ID, "text": message, "disable_web_page_preview": "true"}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        log("TG text push sent")
    except Exception as exc:
        log(f"TG text push failed: {exc}")


# ─────────────────────────────────────────────
# ✅ 新增：截图推送到 TG（sendPhoto）
# ─────────────────────────────────────────────

def send_tg_photo(image_path: str, caption: str = "") -> None:
    """把本地截图文件以 sendPhoto 方式推送到 TG。"""
    if not TG_TOKEN or not TG_CHAT_ID:
        log("TG not configured, skipping photo push")
        return
    if not image_path or not Path(image_path).exists():
        log(f"screenshot file not found, skipping photo push: {image_path}")
        return
    log(f"TG photo push: {image_path}")
    # 优先走 curl（支持 socks 代理）
    proxy = normalize_socks_proxy(TG_PROXY) if TG_PROXY else ""
    curl_cmd = ["curl", "-sS", "--max-time", "40"]
    if proxy:
        curl_cmd += ["--socks5-hostname", proxy]
    curl_cmd += [
        "-X", "POST",
        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
        "-F", f"chat_id={TG_CHAT_ID}",
        "-F", f"photo=@{image_path}",
    ]
    if caption:
        curl_cmd += ["-F", f"caption={caption[:1000]}"]
    try:
        proc = subprocess.run(curl_cmd, check=True, capture_output=True, text=True, timeout=45)
        body = (proc.stdout or "").strip()
        if '"ok":true' in body.replace(" ", ""):
            log("TG photo push sent via curl")
            return
        log(f"TG photo curl not ok: {body[:200]}")
    except Exception as exc:
        log(f"TG photo curl failed: {exc}")
    # 回退：urllib multipart
    try:
        boundary = "----SBDebugBoundary"
        img_data = Path(image_path).read_bytes()
        body_parts = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{TG_CHAT_ID}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="debug.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + img_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        if caption:
            cap_part = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n\r\n'
                f"{caption[:1000]}\r\n"
            ).encode("utf-8")
            body_parts = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                f"{TG_CHAT_ID}\r\n"
            ).encode("utf-8") + cap_part + (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="debug.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode("utf-8") + img_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
            data=body_parts, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            resp.read()
        log("TG photo push sent via urllib")
    except Exception as exc:
        log(f"TG photo push (urllib) failed: {exc}")


def debug_screenshot_and_push(sb: SB, label: str) -> str | None:
    """截图 → 保存到临时文件 → 推送到 TG，返回文件路径。"""
    ts = datetime.now().strftime("%H%M%S")
    tmp_path = f"/tmp/debug_{ts}_{label.replace(' ', '_')}.png"
    try:
        sb.save_screenshot(tmp_path)
        log(f"调试截图已保存: {tmp_path}")
    except Exception as exc:
        log(f"调试截图保存失败: {exc}")
        return None
    caption = f"🔍 [{label}]\n🕒 {datetime.now().strftime('%H:%M:%S')}\n🔗 {current_url_safe(sb)}"
    send_tg_photo(tmp_path, caption=caption)
    return tmp_path


# ─────────────────────────────────────────────
# 结果卡片 & 文件写入
# ─────────────────────────────────────────────

def build_tg_card(ok: bool, data: dict | None = None, error: str = "") -> str:
    data = data or {}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "✅ 成功" if ok else "❌ 失败"
    lines = [
        "🤖 AgentRouter 签到通知",
        "",
        f"🕒 运行时间: {now_str}",
        f"📊 结果: {status}",
        f"💰 签到前余额: {data.get('balanceBeforeText') or '未读取'}",
        f"💵 签到后余额: {data.get('balanceAfterText') or '未读取'}",
        f"📈 余额变动: {data.get('balanceDeltaText') or '未读取'}",
        f"🧪 判定依据: {data.get('reason') or ('OK' if ok else 'FAILED')}",
    ]
    if data.get("url"):
        lines.append(f"🔗 最终页面: {data['url']}")
    if error:
        lines.append(f"⚠️ 异常: {error[:240]}")
    return "\n".join(lines)


def write_result(ok: bool, error: str | None = None, data: dict | None = None, screenshot_path: str | None = None) -> None:
    if not TASK_RESULT_PATH:
        return
    payload = {
        "ok": ok,
        "screenshotPath": screenshot_path or TASK_SCREENSHOT_PATH or None,
        "data": data or {},
    }
    if error:
        payload["error"] = error
    path = Path(TASK_RESULT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_screenshot(sb: SB, path: str | None = None) -> str | None:
    shot = path or TASK_SCREENSHOT_PATH
    if not shot:
        return None
    try:
        p = Path(shot)
        p.parent.mkdir(parents=True, exist_ok=True)
        sb.save_screenshot(str(p))
        return str(p)
    except Exception as exc:
        log(f"screenshot failed: {exc}")
        return None


# ─────────────────────────────────────────────
# 浏览器参数
# ─────────────────────────────────────────────

def normalize_sb_proxy(proxy: str) -> str:
    value = proxy.strip()
    for prefix in ("socks5h://", "socks5://", "https://", "http://"):
        if value.lower().startswith(prefix):
            return value[len(prefix):]
    return value


def build_sb_args() -> dict:
    chrome_path = (os.environ.get("BROWSER_CHROME_PATH") or "").strip()
    user_data_dir = (os.environ.get("BROWSER_USER_DATA_DIR") or "").strip()
    proxy = (os.environ.get("BROWSER_PROXY") or "").strip()
    locale = (os.environ.get("BROWSER_LOCALE") or "").strip()

    args = {"test": True, "headed": True}  # USE_UC 已硬锁为 False
    if chrome_path:
        args["binary_location"] = chrome_path
    if user_data_dir:
        args["user_data_dir"] = user_data_dir
    if proxy:
        args["proxy"] = normalize_sb_proxy(proxy)

    chromium_args = [
        "--hide-crash-restore-bubble",
        "--disable-session-crashed-bubble",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if proxy:
        chromium_args.append(f"--proxy-server={proxy}")
    if locale:
        args["locale_code"] = locale
        chromium_args.append(f"--lang={locale}")
    args["chromium_arg"] = ",".join(chromium_args)
    return args


# ─────────────────────────────────────────────
# Chrome profile crash-state patch
# ─────────────────────────────────────────────

def patch_json_path(obj: dict, dotted_key: str, value) -> None:
    cur = obj
    parts = dotted_key.split(".")
    for key in parts[:-1]:
        next_obj = cur.get(key)
        if not isinstance(next_obj, dict):
            next_obj = {}
            cur[key] = next_obj
        cur = next_obj
    cur[parts[-1]] = value


def patch_json_file(path: Path, updates: dict[str, object]) -> bool:
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return False
        for key, val in updates.items():
            patch_json_path(data, key, val)
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return True
    except Exception as exc:
        log(f"patch json failed: {path}: {exc}")
        return False


def normalize_profile_crash_state(user_data_dir: str) -> None:
    if not user_data_dir:
        return
    base = Path(user_data_dir)
    files = [
        (base / "Default" / "Preferences", {"profile.exit_type": "Normal", "profile.exited_cleanly": True}),
        (base / "Local State", {"profile.exit_type": "Normal", "profile.exited_cleanly": True}),
    ]
    patched = sum(1 for file_path, updates in files if patch_json_file(file_path, updates))
    log(f"profile crash-state patched files: {patched}")


def dismiss_chrome_crash_prompt() -> None:
    try:
        subprocess.run(["xdotool", "key", "Escape"], check=True)
        time.sleep(0.2)
        subprocess.run(["xdotool", "key", "Escape"], check=True)
        log("crash prompt dismiss keys sent")
    except Exception as exc:
        log(f"crash prompt dismiss skipped: {exc}")


# ─────────────────────────────────────────────
# 页面操作工具
# ─────────────────────────────────────────────

def open_url(sb: SB, url: str, label: str) -> None:
    log(f"open {label}: {url}")
    sb.open(url)
    time.sleep(READY_WAIT)
    log(f"{label} URL: {current_url_safe(sb)}")


def browser_fetch_json(sb: SB, path: str, timeout: int = 15) -> dict:
    sb.driver.set_script_timeout(timeout)
    return sb.driver.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        fetch(path, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { 'Accept': 'application/json' }
        }).then(async (resp) => {
          const text = await resp.text();
          let body = null;
          try { body = JSON.parse(text); } catch (_) {}
          done({ ok: true, status: resp.status, url: resp.url, body, text });
        }).catch((err) => {
          done({ ok: false, error: String(err) });
        });
        """,
        path,
    )


def is_waf_text(text: str) -> bool:
    value = str(text or "")
    return "CF_APP_WAF" in value or "为了更好的访问体验，请进行验证" in value or "AliyunCaptcha" in value


def logout_via_api(sb: SB) -> None:
    if not is_target_host(current_url_safe(sb)):
        open_url(sb, SITE_URL, "site before logout")
    result = browser_fetch_json(sb, "/api/user/logout")
    body = result.get("body") if isinstance(result, dict) else None
    log(f"logout API status={result.get('status') if isinstance(result, dict) else 'unknown'} body={body}")
    if not (isinstance(body, dict) and body.get("success")):
        raise RuntimeError(f"logout API failed: {result}")
    time.sleep(1)


def locate_github_login_control(sb: SB) -> dict:
    result = sb.driver.execute_script(
        r"""
        const loginText = arguments[0];
        const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        const hrefOf = (el) => el.href || el.getAttribute('href') || '';
        const controls = Array.from(document.querySelectorAll('button,a,[role="button"]'));
        const candidates = [];
        for (const el of controls) {
          if (!visible(el)) continue;
          if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
          const text = textOf(el);
          const href = hrefOf(el);
          const hasGithubLogo = !!el.querySelector("img[aria-label='github_logo'], svg[class*='github'], .semi-icon-github_logo");
          const exactText = text === loginText;
          const githubText = /github/i.test(text) || text.includes('GitHub');
          const githubHref = /github/i.test(href);
          if (!exactText && !githubText && !githubHref && !hasGithubLogo) continue;
          const r = el.getBoundingClientRect();
          candidates.push({ el, text, href, hasGithubLogo, exactText, githubText, githubHref, area: Math.max(1, r.width * r.height) });
        }
        candidates.sort((a, b) => {
          const score = (item) =>
            (item.exactText ? 1000 : 0) +
            (item.githubText ? 500 : 0) +
            (item.githubHref ? 300 : 0) +
            (item.hasGithubLogo ? 100 : 0);
          return score(b) - score(a) || b.area - a.area;
        });
        const pick = candidates[0];
        if (!pick) {
          return { found: false, candidates: candidates.map((item) => ({ text: item.text, href: item.href })) };
        }
        const target = pick.el;
        target.scrollIntoView({ block: 'center', inline: 'center' });
        const r = target.getBoundingClientRect();
        const borderX = Math.max(0, ((window.outerWidth || 0) - (window.innerWidth || 0)) / 2);
        const topChrome = Math.max(0, (window.outerHeight || 0) - (window.innerHeight || 0) - borderX);
        return {
          found: true,
          text: textOf(target),
          href: hrefOf(target),
          viewportX: r.left + r.width / 2,
          viewportY: r.top + r.height / 2,
          screenX: Math.round((window.screenX || 0) + borderX + r.left + r.width / 2),
          screenY: Math.round((window.screenY || 0) + topChrome + r.top + r.height / 2)
        };
        """,
        LOGIN_TEXT,
    )
    return result if isinstance(result, dict) else {"found": False, "raw": result}


def webdriver_click_github_login(sb: SB) -> None:
    element = sb.driver.execute_script(
        r"""
        const loginText = arguments[0];
        const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        const controls = Array.from(document.querySelectorAll('button,a,[role="button"]'));
        return controls.find((el) => visible(el) && (textOf(el) === loginText || /github/i.test(textOf(el)))) || null;
        """,
        LOGIN_TEXT,
    )
    if not element:
        raise RuntimeError("GitHub login control not found for WebDriver click")
    sb.driver.execute_script("arguments[0].click();", element)


def click_github_login(sb: SB) -> None:
    deadline = time.time() + 20
    last_result = None
    while time.time() < deadline:
        last_result = locate_github_login_control(sb)
        if last_result.get("found"):
            break
        time.sleep(0.5)
    if not (isinstance(last_result, dict) and last_result.get("found")):
        raise RuntimeError(f"GitHub login control not found: {last_result}")

    log(f"GitHub login control: text={last_result.get('text')} href={last_result.get('href')}")
    try:
        x = str(int(last_result["screenX"]))
        y = str(int(last_result["screenY"]))
        log(f"xdotool click GitHub login: x={x} y={y}")
        subprocess.run(["xdotool", "mousemove", x, y], check=True, timeout=5)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], check=True, timeout=5)

        time.sleep(1.5)
        if "login" in current_url_safe(sb):
            log("xdotool 似乎未生效，强制启用 webdriver 兜底点击...")
            webdriver_click_github_login(sb)
        return
    except Exception as exc:
        log(f"xdotool GitHub click failed, fallback to WebDriver click: {exc}")
    webdriver_click_github_login(sb)


def page_text_sample(sb: SB, limit: int = 5000) -> str:
    try:
        return str(
            sb.driver.execute_script(
                "return (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, arguments[0]);",
                int(limit),
            )
            or ""
        )
    except Exception:
        return ""


def parse_money_text(text: str) -> float | None:
    match = re.search(r"\$\s*(+(?:\.+)?)", str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def read_balance_from_page(sb: SB) -> dict:
    try:
        payload = sb.driver.execute_script(
            r"""
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const text = norm(document.body && (document.body.innerText || document.body.textContent || ''));
            const labelIndex = text.indexOf('当前余额');
            const sample = labelIndex >= 0 ? text.slice(labelIndex, labelIndex + 120) : text.slice(0, 500);
            const match = sample.match(/\$\s*(+(?:\.+)?)/) || text.match(/\$\s*(+(?:\.+)?)/);
            return { balanceText: match ? match[0] : '', balanceAmount: match ? match[1] : '', sample };
            """
        )
        if isinstance(payload, dict) and payload.get("balanceText"):
            return payload
    except Exception as exc:
        log(f"read balance from page failed: {exc}")
    return {"balanceText": "", "balanceAmount": "", "sample": ""}


def open_wallet_and_read_balance(sb: SB) -> dict:
    open_url(sb, WALLET_URL, "wallet")
    deadline = time.time() + 25
    last_url = ""
    while time.time() < deadline:
        url = current_url_safe(sb)
        if url != last_url:
            log(f"wallet URL: {url}")
            last_url = url
        if path_from_url(url) == "/login":
            return {"loggedIn": False, "balanceText": "", "balanceAmount": ""}
        balance = read_balance_from_page(sb)
        if balance.get("balanceText"):
            balance["loggedIn"] = True
            log(f"balance page read: {balance.get('balanceText')}")
            return balance
        time.sleep(1)
    return {"loggedIn": is_logged_in_by_url(sb), "balanceText": "", "balanceAmount": ""}


def is_logged_in_by_url(sb: SB) -> bool:
    url = current_url_safe(sb)
    return is_target_host(url) and path_from_url(url).startswith("/console")


def switch_to_best_target_tab(sb: SB) -> None:
    try:
        handles = list(sb.driver.window_handles)
    except Exception:
        return
    best = None
    best_score = -1
    for handle in handles:
        try:
            sb.driver.switch_to.window(handle)
            url = current_url_safe(sb)
        except Exception:
            continue
        if not is_target_host(url):
            continue
        score = 1
        path = path_from_url(url)
        if path.startswith("/console") or path.startswith("/oauth"):
            score = 10
        elif path == "/login":
            score = 5
        if score > best_score:
            best = handle
            best_score = score
    if best:
        try:
            sb.driver.switch_to.window(best)
        except Exception:
            pass


def wait_for_login_success(sb: SB) -> None:
    """等待登录完成，期间每隔 DEBUG_SCREENSHOT_INTERVAL 秒截图推送到 TG。"""
    deadline = time.time() + WAIT_AFTER_CLICK
    last_url = ""
    last_screenshot_at = time.time()
    screenshot_index = 0

    # ✅ 点击后立即拍第一张，看按钮点击是否触发了跳转
    debug_screenshot_and_push(sb, f"after_click_{screenshot_index:02d}")
    screenshot_index += 1

    while time.time() < deadline:
        switch_to_best_target_tab(sb)
        url = current_url_safe(sb)

        # URL 发生变化时立即截图
        if url != last_url:
            log(f"waiting login URL: {url}")
            last_url = url
            debug_screenshot_and_push(sb, f"url_change_{screenshot_index:02d}")
            screenshot_index += 1

        if is_logged_in_by_url(sb):
            log(f"login confirmed by console URL: {url}")
            return

        if is_waf_text(page_text_sample(sb)):
            debug_screenshot_and_push(sb, f"waf_detected_{screenshot_index:02d}")
            raise RuntimeError("login flow hit WAF verification page")

        # 定时截图（每 DEBUG_SCREENSHOT_INTERVAL 秒）
        if time.time() - last_screenshot_at >= DEBUG_SCREENSHOT_INTERVAL:
            debug_screenshot_and_push(sb, f"heartbeat_{screenshot_index:02d}")
            screenshot_index += 1
            last_screenshot_at = time.time()

        time.sleep(1)

    # 超时前最后一张
    debug_screenshot_and_push(sb, f"timeout_{screenshot_index:02d}")
    raise RuntimeError(f"timed out waiting for login success; current={current_url_safe(sb)}")


def compute_result(data: dict) -> bool:
    before_num = parse_money_text(data.get("balanceBeforeText") or "")
    after_num = parse_money_text(data.get("balanceAfterText") or "")
    if before_num is not None and after_num is not None:
        delta = after_num - before_num
        data["balanceDelta"] = delta
        data["balanceDeltaText"] = f"{delta:+.2f}"
        if delta > 0:
            data["reason"] = f"签到成功，余额增加 {data['balanceDeltaText']}"
        elif delta == 0:
            data["reason"] = "今日可能已签到，余额未变化"
        else:
            data["reason"] = f"登录完成，但余额减少 {data['balanceDeltaText']}"
        return True
    if data.get("balanceAfterText"):
        data["balanceDeltaText"] = "N/A"
        data["reason"] = f"登录成功，当前余额 {data.get('balanceAfterText')}"
        return True
    data["reason"] = "未读取到登录后的余额"
    return False


# ─────────────────────────────────────────────
# 代理预检 & Cookie 注入
# ─────────────────────────────────────────────

def check_github_login_status(sb: SB) -> None:
    """打开 github.com 截图推送到 TG，直观确认 GitHub 登录状态。"""
    log("检查 GitHub 登录状态...")
    try:
        sb.driver.set_page_load_timeout(15)
        sb.open("https://github.com")
        sb.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        time.sleep(2)

        # 用 JS 读取登录状态
        login_info = sb.driver.execute_script("""
            const meta = document.querySelector('meta[name="user-login"]');
            const avatar = document.querySelector('.avatar-user, [data-login]');
            return {
                userLogin: meta ? meta.getAttribute('content') : null,
                hasAvatar: !!avatar,
                title: document.title
            };
        """)
        if isinstance(login_info, dict) and login_info.get("userLogin"):
            msg = f"✅ GitHub 已登录: @{login_info['userLogin']}"
        else:
            msg = f"❌ GitHub 未登录 (title={login_info.get('title') if isinstance(login_info, dict) else '?'})"
        log(msg)
        debug_screenshot_and_push(sb, f"github_status")
        send_tg_message(f"🔍 GitHub 登录状态检查\n{msg}")
    except Exception as exc:
        log(f"GitHub 状态检查失败: {exc}")
    finally:
        sb.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)



    proxy = (os.environ.get("BROWSER_PROXY") or "").strip()
    if not proxy:
        log("未配置 BROWSER_PROXY，跳过代理预检")
        return
    log(f"代理预检中: {proxy}")
    try:
        sb.driver.set_page_load_timeout(10)
        sb.open("https://api.ipify.org")
        ip_text = page_text_sample(sb, 50).strip()
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip_text):
            parts = ip_text.split(".")
            masked = f"***.***.{parts[2]}.{parts[3]}"
            log(f"【代理检查】出口 IP: {masked} ✅ 代理连通正常")
        else:
            raise RuntimeError(f"ipify 响应异常: {ip_text!r}")
    except Exception as exc:
        raise RuntimeError(f"代理节点不可达，终止任务防止浏览器挂死: {exc}") from exc
    finally:
        sb.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)


def inject_github_cookie(sb: SB) -> None:
    gh_cookie = (os.environ.get("GH_COOKIE") or "").strip()
    if not gh_cookie:
        return

    # ✅ 有 Chrome Profile 时跳过注入：Profile 里已有登录态，注入反而可能干扰
    profile_dir = (os.environ.get("BROWSER_USER_DATA_DIR") or "").strip()
    if profile_dir and Path(profile_dir).exists():
        log(f"检测到 Chrome Profile 目录，跳过 GH_COOKIE 注入（Profile 优先）: {profile_dir}")
        return

    log("未检测到 Chrome Profile，使用 GH_COOKIE 注入...")
    try:
        sb.driver.set_page_load_timeout(15)
        sb.open("https://github.com/404")
        sb.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    except Exception as exc:
        log(f"⚠️  GitHub 页面加载超时/失败（代理可能不通），跳过 Cookie 注入: {exc}")
        sb.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        return

    sb.driver.add_cookie({
        "name": "user_session",
        "value": gh_cookie,
        "domain": "github.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    })
    sb.driver.add_cookie({
        "name": "__Host-user_session_same_site",
        "value": gh_cookie,
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Strict",
    })
    log("GitHub Cookie 注入完成！")


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

def main() -> None:
    user_loaded = load_env_file(USER_ENV_FILE)
    env_file_from_var = (os.environ.get("AGENTROUTER_ENV_FILE") or "").strip()
    if env_file_from_var and env_file_from_var != USER_ENV_FILE:
        load_env_file(env_file_from_var)
    elif not user_loaded:
        log("no external env file loaded; using current process env only")
    refresh_config()

    profile_dir = (os.environ.get("BROWSER_USER_DATA_DIR") or "").strip()
    data = {
        "siteUrl": SITE_URL,
        "loginUrl": LOGIN_URL,
        "loginText": LOGIN_TEXT,
        "profileDir": profile_dir,
        "scriptRevision": SCRIPT_REVISION,
    }
    screenshot_path = None

    try:
        log("AgentRouter API-first check-in task started")
        log(f"SCRIPT_REVISION: {SCRIPT_REVISION}")
        log(f"SITE_URL: {SITE_URL}")
        log(f"LOGIN_URL: {LOGIN_URL}")
        log(f"AGENTROUTER_USE_UC: {int(USE_UC)}")
        log(f"BROWSER_USER_DATA_DIR: {profile_dir}")
        log(f"BROWSER_CHROME_PATH: {(os.environ.get('BROWSER_CHROME_PATH') or '').strip()}")
        normalize_profile_crash_state(profile_dir)

        with SB(**build_sb_args()) as sb:
            log("browser started")

            # 全局超时保护
            sb.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            sb.driver.set_script_timeout(SCRIPT_TIMEOUT)

            dismiss_chrome_crash_prompt()

            # 代理预检
            check_proxy_connectivity(sb)

            # GH_COOKIE 注入（带超时保护）
            inject_github_cookie(sb)

            # ✅ 启动后先去 github.com 截图，直接确认 GitHub 登录状态
            check_github_login_status(sb)

            open_url(sb, SITE_URL, "site")

            before_balance = open_wallet_and_read_balance(sb)
            data["startLoggedIn"] = bool(before_balance.get("loggedIn"))
            data["balanceBeforeText"] = before_balance.get("balanceText") or ""
            data["balanceBeforeAmount"] = before_balance.get("balanceAmount") or ""
            if data["startLoggedIn"]:
                log(f"already logged in, balance before: {data['balanceBeforeText'] or 'not found'}")
                logout_via_api(sb)
            else:
                log("session appears logged out")

            open_url(sb, LOGIN_URL, "login")

            # ✅ 打开登录页后截图，确认页面是否正常渲染
            debug_screenshot_and_push(sb, "login_page_loaded")

            if is_logged_in_by_url(sb):
                log("login URL redirected to logged-in session; logging out once more")
                logout_via_api(sb)
                open_url(sb, LOGIN_URL, "login after forced logout")

            click_github_login(sb)

            # wait_for_login_success 内部会自动截图推送
            wait_for_login_success(sb)

            after_balance = open_wallet_and_read_balance(sb)
            data["balanceAfterText"] = after_balance.get("balanceText") or ""
            data["balanceAfterAmount"] = after_balance.get("balanceAmount") or ""
            data["url"] = current_url_safe(sb)
            screenshot_path = save_screenshot(sb)

        ok = compute_result(data)
        write_result(ok, error=None if ok else data.get("reason"), data=data, screenshot_path=screenshot_path)
        send_tg_message(build_tg_card(ok, data=data, error="" if ok else data.get("reason", "")))
        if not ok:
            raise RuntimeError(data.get("reason") or "check-in failed")
        log(f"check-in completed: {data.get('reason')}")
    except Exception as exc:
        error = str(exc)
        log(f"task failed: {error}")
        write_result(False, error=error, data=data, screenshot_path=screenshot_path)
        send_tg_message(build_tg_card(False, data=data, error=error))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
