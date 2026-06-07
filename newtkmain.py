import sys
import os
import yaml
import requests
import time
import random
import threading
import concurrent.futures
import ctypes
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from urllib import parse as urlparse
from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions

# ================= 全局控制变量 =================
STOP_FLAG = False
app_instance = None

RUN_MODE = 1
BIT_API_URL = "http://127.0.0.1:54345"
GROUP_ID = ""
PROFILE_ID = ""
PROFILE_NAME = ""
CURRENT_JOB_ID = ""
WINDOW_COUNT = 3
COMMENT_TEXTS = []
FARMING_CONFIG = {}
TARGET_BOOST_CONFIG = {}
POST_CONFIG = {}  # 【新增】发帖模式全局配置

try:
    from automation.smart_comment import generate_comment as generate_smart_comment
    from automation.smart_comment import save_comment_record
except Exception:
    generate_smart_comment = None
    save_comment_record = None


# ================= 底层防假死 =================
def disable_quickedit_mode():
    if sys.platform != 'win32': return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        ENABLE_QUICK_EDIT_MODE = 0x0040
        if mode.value & ENABLE_QUICK_EDIT_MODE:
            mode.value &= ~ENABLE_QUICK_EDIT_MODE
            kernel32.SetConsoleMode(handle, mode)
    except Exception:
        pass


disable_quickedit_mode()


# ================= 工具函数 =================
def stoppable_sleep(seconds):
    end_time = time.time() + seconds
    while time.time() < end_time:
        if STOP_FLAG: return False
        time.sleep(min(0.1, end_time - time.time()))
    return True


def human_sleep(min_s=1.0, max_s=3.0):
    stoppable_sleep(random.uniform(min_s, max_s))


def get_dynamic_emoji_reply():
    pool = ["😀", "👍", "🔥", "🚀", "💯", "👏", "🙌", "👀", "🎯", "💡", "💖", "✨"]
    return "".join(random.choices(pool, k=random.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]))


def extract_tweet_context(tweet, page, fallback_url=""):
    tweet_text = ""
    author = ""
    try:
        tweet_text = str(getattr(tweet, "text", "") or "").strip()
    except Exception:
        tweet_text = ""
    try:
        author_ele = tweet.ele('tag:a@href^="/"', timeout=0.5)
        if author_ele:
            author = str(author_ele.text or author_ele.attr("href") or "").strip()
    except Exception:
        author = ""
    try:
        current_url = getattr(page, "url", "") or fallback_url
    except Exception:
        current_url = fallback_url
    return {
        "tweet_url": fallback_url or current_url,
        "tweet_text": tweet_text,
        "author": author,
    }


def build_reply_text(tweet, page, p_name="", fallback_url="", fallback_index=None):
    fallback_pool = COMMENT_TEXTS or [get_dynamic_emoji_reply()]
    if fallback_index is not None and COMMENT_TEXTS:
        fallback_pool = [COMMENT_TEXTS[fallback_index % len(COMMENT_TEXTS)]]
    context = extract_tweet_context(tweet, page, fallback_url)
    if generate_smart_comment:
        try:
            record = generate_smart_comment(
                tweet_text=context["tweet_text"],
                tweet_url=context["tweet_url"],
                author=context["author"],
                profile=p_name,
                fallback_pool=fallback_pool,
            )
            audit_comment(record, record.get("status") or "generated")
            if record.get("status") == "generated":
                logger.info(f"[{p_name}] 智能评论生成成功：{record.get('generated_comment', '')[:40]}")
            elif record.get("status") == "fallback_used":
                logger.warning(f"[{p_name}] {record.get('error') or '智能评论未生成，已使用评论库兜底'}")
            return record.get("generated_comment") or next(iter(fallback_pool)), record
        except Exception as exc:
            logger.warning(f"[{p_name}] 智能评论生成失败，使用评论库兜底: {exc}")
    text = next(iter(fallback_pool))
    record = {
        "profile": p_name,
        "tweet_url": context["tweet_url"],
        "tweet_text": context["tweet_text"],
        "author": context["author"],
        "generated_comment": text,
        "model": "",
        "status": "fallback_used",
        "error": "智能评论不可用，已使用评论库兜底",
    }
    audit_comment(record, "fallback_used")
    return text, record


def audit_comment(record, status, publish_result="", error=""):
    if not save_comment_record:
        return
    try:
        save_comment_record(record, status=status, publish_result=publish_result, error=error)
    except Exception as exc:
        logger.warning(f"评论留底写入失败: {exc}")


def safe_float(val, default=0.0):
    try:
        return float(val)
    except:
        return default


def safe_int(val, default=0):
    try:
        return int(val)
    except:
        return default


def get_manual_search_target():
    raw = FARMING_CONFIG.get("max_manual_searches")
    if raw is None or raw == "" or str(raw).lower() in {"none", "null", "不限", "无限"}:
        target = random.randint(2, 5)
        FARMING_CONFIG["max_manual_searches"] = target
        logger.info(f"本次模式一手动搜索目标随机设定为 {target} 次")
        return target
    return max(0, safe_int(raw, random.randint(2, 5)))


def dismiss_x_overlays(page, p_name=""):
    keywords = [
        "Not now", "Maybe later", "Skip", "Close", "Dismiss",
        "以后再说", "暂不", "跳过", "关闭", "不用了",
    ]
    blocker_keywords = ["Premium", "Subscribe", "Upgrade", "订阅", "升级", "会员"]
    dismissed = False
    try:
        body = page.ele("tag:body", timeout=1)
        body_text = (body.text or "") if body else ""
    except Exception:
        body_text = ""
    if body_text and not any(k in body_text for k in blocker_keywords + keywords):
        return False
    for text in keywords:
        try:
            btn = page.ele(f'tag:button@@text():{text}', timeout=0.5) or page.ele(f'text:{text}', timeout=0.5)
            if btn:
                btn.click(by_js=True)
                dismissed = True
                logger.info(f"[{p_name}] 已尝试关闭 X 弹窗: {text}")
                human_sleep(1.0, 1.8)
                break
        except Exception:
            continue
    if not dismissed:
        for selector in ('@data-testid=app-bar-close', '@aria-label=Close', '@aria-label=关闭', 'tag:button@@aria-label=Close'):
            try:
                btn = page.ele(selector, timeout=0.5)
                if btn:
                    btn.click(by_js=True)
                    dismissed = True
                    logger.info(f"[{p_name}] 已尝试点击关闭按钮处理 X 弹窗")
                    human_sleep(1.0, 1.8)
                    break
            except Exception:
                continue
    return dismissed


def read_yaml_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or (default or {})
    except Exception:
        return default or {}


def central_api_request(method, path, payload=None, query=None):
    import json as _json
    from urllib import request as _request

    cfg = read_yaml_file("automation_config.yaml", {})
    api = str(cfg.get("central_api") or "http://127.0.0.1:8766").rstrip("/")
    token = str(cfg.get("central_token") or "")
    if query:
        path = f"{path}?{urlparse.urlencode(query)}"
    data = None
    headers = {"X-Automation-Token": token}
    if payload is not None:
        data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = _request.Request(f"{api}{path}", data=data, headers=headers, method=method)
    opener = _request.build_opener(_request.ProxyHandler({}))
    with opener.open(req, timeout=20) as res:
        return _json.loads(res.read().decode("utf-8"))


# ================= 核心 RPA 业务逻辑 =================
def get_profiles_by_group(group_id, limit=5):
    url = f"{BIT_API_URL}/browser/list"
    try:
        res = requests.post(url, json={"groupId": group_id, "page": 0, "pageSize": limit}).json()
        if res.get('success'): return res['data']['list']
        return []
    except Exception:
        return []


def start_browser(profile_id, profile_name, delay_seconds=0):
    if not stoppable_sleep(delay_seconds): return None
    if STOP_FLAG: return None
    url = f"{BIT_API_URL}/browser/open"
    try:
        res = requests.post(url, json={"id": profile_id}).json()
        if res.get('success'): return res['data']
        return None
    except Exception:
        return None


def build_single_profile_item(profile_id, profile_name, delay_seconds=0):
    data = start_browser(profile_id, profile_name or profile_id, delay_seconds)
    if not data:
        return None
    port = data.get("http") or data.get("debuggingPort") or data.get("debug_port") or data.get("port")
    if isinstance(port, str) and ":" in port:
        port = port.rsplit(":", 1)[-1]
    try:
        port = int(port)
    except Exception:
        logger.error(f"[{profile_name or profile_id}] 未获取到调试端口: {data}")
        return None
    return {"profile": {"id": profile_id, "name": profile_name or profile_id}, "port": port}


def batch_arrange_windows(started_profiles):
    if STOP_FLAG: return
    logger.info("开始横向小窗重叠排列...")
    win_w, win_h = 450, 750
    step_x, step_y = 55, 25
    for index, item in enumerate(started_profiles):
        if STOP_FLAG: break
        try:
            co = ChromiumOptions()
            co.set_local_port(item['port'])
            page = ChromiumPage(co)
            page.set.window.size(win_w, win_h)
            try:
                screen_w = page.run_js('return window.screen.availWidth;')
                screen_h = page.run_js('return window.screen.availHeight;')
            except:
                screen_w, screen_h = 1920, 1080
            max_x = max(1, screen_w - win_w)
            max_y = max(1, screen_h - win_h)
            items_per_row = max(1, max_x // step_x)
            row_index = index // items_per_row
            col_index = index % items_per_row
            page.set.window.location(int(col_index * step_x), int((row_index * step_y) % max_y))
        except Exception:
            pass
    if not STOP_FLAG: logger.success("✅ 窗口横向重叠排列完毕！")


# ================= 1. 养号模式专属逻辑 =================
def interact_in_detail_page(tab, p_name, state=None):
    if STOP_FLAG: return
    read_time = random.uniform(FARMING_CONFIG["read_delay_min"], FARMING_CONFIG["read_delay_max"])
    logger.info(f"[{p_name}] 新标签页加载成功，模拟阅读 {int(read_time)} 秒...")
    if not stoppable_sleep(read_time * 0.4): return
    tab.scroll.down(random.randint(100, 200))
    if not stoppable_sleep(read_time * 0.6): return

    main_tweet = tab.ele('tag:article', timeout=5)
    if not main_tweet: return

    already_liked = main_tweet.ele('@data-testid=unlike', timeout=1)
    already_bookmarked = main_tweet.ele('@data-testid=removeBookmark', timeout=1)
    if already_liked or already_bookmarked:
        logger.warning(f"[{p_name}] 检测到该帖子已互动过，跳过操作。")
        return

    if state["total_likes"] < FARMING_CONFIG.get("max_likes", 50) and random.random() < FARMING_CONFIG["prob_like"]:
        like_btn = main_tweet.ele('@data-testid=like', timeout=2)
        if like_btn:
            like_btn.click(by_js=True)
            logger.info(f"[{p_name}] 对 [主贴] 执行了：点赞")
            state["likes"] += 1;
            state["total_likes"] += 1
            human_sleep(1.0, 2.0)
    if STOP_FLAG: return

    if state["total_bookmarks"] < FARMING_CONFIG.get("max_bookmarks", 30) and random.random() < FARMING_CONFIG[
        "prob_bookmark"]:
        bookmark_btn = main_tweet.ele('@data-testid=bookmark', timeout=2)
        if bookmark_btn:
            bookmark_btn.click(by_js=True);
            logger.info(f"[{p_name}] 对 [主贴] 执行了：收藏")
            state["total_bookmarks"] += 1;
            human_sleep(1.0, 2.0)
    if STOP_FLAG: return

    if state["total_retweets"] < FARMING_CONFIG.get("max_retweets", 20) and random.random() < FARMING_CONFIG[
        "prob_retweet"]:
        retweet_icon = main_tweet.ele('@data-testid=retweet', timeout=2)
        if retweet_icon:
            retweet_icon.click(by_js=True);
            human_sleep(1.0, 2.0)
            confirm = tab.ele('@data-testid=retweetConfirm', timeout=2)
            if confirm:
                confirm.click(by_js=True);
                logger.info(f"[{p_name}] 对 [主贴] 执行了：转帖")
                state["total_retweets"] += 1;
                human_sleep(1.5, 2.5)
    if STOP_FLAG: return

    if state["total_replies"] < FARMING_CONFIG.get("max_replies", 20) and random.random() < FARMING_CONFIG[
        "prob_reply"]:
        reply_icon = main_tweet.ele('@data-testid=reply', timeout=2)
        if reply_icon:
            reply_icon.click(by_js=True);
            human_sleep(1.5, 2.5)
            editor = tab.ele('@data-testid=tweetTextarea_0', timeout=3)
            if editor:
                editor.click();
                human_sleep(0.5, 1.0)
                reply_text, comment_record = build_reply_text(main_tweet, tab, p_name=p_name)
                tab.actions.type(reply_text);
                human_sleep(1.0, 2.0)
                submit_btn = tab.ele('@data-testid=tweetButton', timeout=2) or tab.ele('@data-testid=tweetButtonInline',
                                                                                       timeout=2)
                if submit_btn:
                    try:
                        submit_btn.click(by_js=True)
                        audit_comment(comment_record, "posted", "reply button clicked")
                    except Exception as exc:
                        audit_comment(comment_record, "failed", error=str(exc))
                        raise
                    logger.info(f"[{p_name}] 对 [主贴] 执行了：回复 -> {reply_text}")
                    state["total_replies"] += 1;
                    human_sleep(3.0, 4.0)
                else:
                    audit_comment(comment_record, "failed", error="reply submit button not found")
            close_modal = tab.ele('@data-testid=app-bar-close', timeout=1)
            if close_modal: close_modal.click(by_js=True)
    if STOP_FLAG: return

    if state["total_follows"] < FARMING_CONFIG.get("max_follows", 10) and random.random() < FARMING_CONFIG[
        "prob_follow"]:
        avatar_link_ele = main_tweet.ele('@data-testid=Tweet-User-Avatar', timeout=2)
        if avatar_link_ele:
            profile_a = avatar_link_ele.ele('tag:a') or avatar_link_ele.parent('tag:a')
            if profile_a:
                profile_url = profile_a.attr('href')
                if profile_url:
                    if not profile_url.startswith('http'): profile_url = f"https://x.com{profile_url}"
                    logger.info(f"[{p_name}] 跳转至作者主页: {profile_url}")
                    tab.get(profile_url);
                    tab.wait.load_start()
                    if not stoppable_sleep(random.uniform(2.0, 3.5)): return
                    tab.scroll.down(random.randint(150, 400))
                    if not stoppable_sleep(random.uniform(1.0, 2.5)): return
                    tab.scroll.up(random.randint(100, 250))
                    if not stoppable_sleep(random.uniform(1.0, 2.0)): return
                    follow_btn = tab.ele('tag:button@@text()=Follow', timeout=2) or tab.ele('tag:button@@text()=关注',
                                                                                            timeout=2)
                    if follow_btn:
                        follow_btn.click(by_js=True);
                        logger.success(f"[{p_name}] ✨ 已在主页关注该作者！")
                        state["total_follows"] += 1;
                        human_sleep(2.0, 3.5)


def process_feed(page, p_name, duration, module_name, state=None, history_viewed_urls=None):
    if STOP_FLAG: return
    logger.info(f"[{p_name}] 开启 [{module_name}] 模块，计划停留 {int(duration)} 秒")
    end_time = time.time() + duration
    if history_viewed_urls is None: history_viewed_urls = set()

    while time.time() < end_time:
        if STOP_FLAG: break

        if state and state["total_likes"] >= FARMING_CONFIG.get("max_likes", 50) and \
                state["total_bookmarks"] >= FARMING_CONFIG.get("max_bookmarks", 30) and \
                state["total_replies"] >= FARMING_CONFIG.get("max_replies", 20) and \
                state["total_follows"] >= FARMING_CONFIG.get("max_follows", 10) and \
                state["total_retweets"] >= FARMING_CONFIG.get("max_retweets", 20):
            break

        try:
            retry_btn = page.ele('tag:button@@text():重试', timeout=0.5) or page.ele('tag:button@@text():Retry',
                                                                                     timeout=0.5)
            if retry_btn and retry_btn.states.is_displayed:
                retry_btn.click(by_js=True);
                human_sleep(3.0, 5.0);
                continue
        except Exception:
            pass

        try:
            new_tweets = page.ele('@data-testid=userAvatars', timeout=0.5)
            if new_tweets and new_tweets.states.is_displayed:
                pill_btn = new_tweets.parent('tag:button')
                if pill_btn:
                    pill_btn.click(by_js=True)
                else:
                    new_tweets.click(by_js=True)
                human_sleep(2.0, 4.0)
        except Exception:
            pass

        tweets = page.eles('tag:article')
        for tweet in tweets:
            if STOP_FLAG: break
            try:
                if tweet.ele('text:回复', timeout=0.1) or tweet.ele('text:Replying to', timeout=0.1): continue
                time_ele = tweet.ele('tag:time', timeout=1)
                if not time_ele: continue
                parent_a = time_ele.parent('tag:a')
                tweet_url = parent_a.attr('href') if parent_a else None

                if tweet_url and tweet_url not in history_viewed_urls:
                    if not tweet_url.startswith('http'): tweet_url = f"https://x.com{tweet_url}"
                    history_viewed_urls.add(tweet_url)
                    time_ele.scroll.to_see(center=True);
                    human_sleep(0.5, 1.5)
                    tab = None
                    try:
                        tab = page.new_tab(tweet_url)
                        interact_in_detail_page(tab, p_name, state)
                    except Exception:
                        pass
                    finally:
                        if tab: tab.close()

                    if state and state.get("likes", 0) >= state.get("target_likes", 20):
                        pause_s = random.uniform(FARMING_CONFIG.get("pause_duration_min", 600),
                                                 FARMING_CONFIG.get("pause_duration_max", 1200))
                        logger.warning(f"[{p_name}] 🎯 潮汐休眠触发：休息 {int(pause_s / 60)} 分钟...")
                        if stoppable_sleep(pause_s):
                            state["likes"] = 0
                            state["target_likes"] = random.randint(FARMING_CONFIG.get("pause_after_likes_min", 15),
                                                                   FARMING_CONFIG.get("pause_after_likes_max", 25))
                            logger.success(f"[{p_name}] 🛌 休息结束！")
                    break
            except Exception:
                continue
        page.scroll.down(random.randint(600, 1200));
        human_sleep(1.0, 2.5)


def run_business_logic(profile, port):
    p_name = profile.get('name', '未命名窗口')
    try:
        co = ChromiumOptions()
        co.set_local_port(port)
        page = ChromiumPage(co)
        try:
            tabs_to_close = [t for t in page.tab_ids if t != page.tab_id]
            if tabs_to_close: page.close_tabs(tabs_to_close)
        except Exception:
            pass

        page.get('https://twitter.com/home')
        page.wait.load_start()
        dismiss_x_overlays(page, p_name)
        if page.ele('登录', timeout=3) or 'login' in page.url:
            logger.error(f"[{p_name}] 检测到未登录或跳转登录页，账号任务结束。")
            return

        manual_target = get_manual_search_target()
        initial_target = random.randint(FARMING_CONFIG.get("pause_after_likes_min", 15),
                                        FARMING_CONFIG.get("pause_after_likes_max", 25))
        state = {"likes": 0, "target_likes": initial_target, "total_likes": 0, "total_bookmarks": 0, "total_replies": 0,
                 "total_follows": 0, "total_retweets": 0, "manual_searches": 0, "manual_target": manual_target}
        loop_count = 1
        history_viewed_urls = set()

        while True:
            if STOP_FLAG: break
            if state["total_likes"] >= FARMING_CONFIG.get("max_likes", 50) and \
                    state["total_bookmarks"] >= FARMING_CONFIG.get("max_bookmarks", 30) and \
                    state["total_replies"] >= FARMING_CONFIG.get("max_replies", 20) and \
                    state["total_follows"] >= FARMING_CONFIG.get("max_follows", 10) and \
                    state["total_retweets"] >= FARMING_CONFIG.get("max_retweets", 20) and \
                    state["manual_searches"] >= state.get("manual_target", 0):
                logger.success(f"[{p_name}] 🎉 本窗口任务目标已全部达成！安全结束。")
                break

            disable_quickedit_mode()
            dismiss_x_overlays(page, p_name)
            logger.info(
                f"[{p_name}] >>> 循环 {loop_count} | 进度 -> 赞:{state['total_likes']} 藏:{state['total_bookmarks']} 评:{state['total_replies']} 转:{state['total_retweets']} 关:{state['total_follows']} 搜索:{state['manual_searches']}/{state.get('manual_target', 0)} <<<")

            explore_btn = page.ele('@data-testid=AppTabBar_Explore_Link')
            if explore_btn and state["manual_searches"] < state.get("manual_target", 0):
                explore_btn.click(by_js=True);
                human_sleep(2, 4)
                search_input = page.ele('@data-testid=SearchBox_Search_Input')
                if search_input:
                    search_input.click();
                    human_sleep(0.5, 1.5)
                    search_input.input(random.choice(FARMING_CONFIG["keywords"]), clear=True)
                    human_sleep(1.0, 2.0);
                    page.actions.type('\n');
                    state["manual_searches"] += 1
                    logger.info(f"[{p_name}] 执行了：手动搜索 -> {state['manual_searches']}/{state.get('manual_target', 0)}")
                    human_sleep(4, 6)
                    process_feed(page, p_name, random.uniform(FARMING_CONFIG["switch_interval_min"],
                                                              FARMING_CONFIG["switch_interval_max"]), "探索", state,
                                 history_viewed_urls)

            if STOP_FLAG: break
            home_btn = page.ele('@data-testid=AppTabBar_Home_Link')
            if home_btn:
                home_btn.click(by_js=True);
                human_sleep(2, 4)
                process_feed(page, p_name, random.uniform(FARMING_CONFIG["switch_interval_min"],
                                                          FARMING_CONFIG["switch_interval_max"]), "主页", state,
                             history_viewed_urls)
            loop_count += 1
    except Exception as e:
        logger.error(f"[{p_name}] 异常退出: {e}")


# ================= 2. 冲贴模式专属逻辑 =================
def boost_single_profile(index, item, target_urls):
    disable_quickedit_mode()
    port, p_name = item['port'], item['profile'].get('name', '未命名')
    logger.info(f"--> [账号: {p_name}] 冲贴线程已启动，待处理链接: {len(target_urls)} 条")

    try:
        co = ChromiumOptions()
        co.set_local_port(port)
        page = ChromiumPage(co)
        try:
            tabs_to_close = [t for t in page.tab_ids if t != page.tab_id]
            if tabs_to_close: page.close_tabs(tabs_to_close)
        except Exception:
            pass

        for u_idx, url in enumerate(target_urls):
            if STOP_FLAG: break
            url = url.strip()
            if not url: continue

            logger.info(f"[{p_name}] 正在执行目标 {u_idx + 1}/{len(target_urls)}: {url}")
            page.get(url)
            page.wait.url_change('about:blank', timeout=5)

            main_tweet = page.ele('tag:article', timeout=5)
            if not main_tweet: continue

            if not stoppable_sleep(random.uniform(TARGET_BOOST_CONFIG["read_delay_min"],
                                                  TARGET_BOOST_CONFIG["read_delay_max"]) * 0.4): break
            page.scroll.down(random.randint(200, 400))

            if random.random() < TARGET_BOOST_CONFIG["prob_like"]:
                like_btn = main_tweet.ele('@data-testid=like', timeout=2)
                if like_btn: like_btn.click(by_js=True); human_sleep(1.0, 2.5)

            if random.random() < TARGET_BOOST_CONFIG["prob_bookmark"]:
                bookmark_btn = main_tweet.ele('@data-testid=bookmark', timeout=2)
                if bookmark_btn: bookmark_btn.click(by_js=True); human_sleep(1.0, 2.5)

            if random.random() < TARGET_BOOST_CONFIG["prob_retweet"]:
                retweet_icon = main_tweet.ele('@data-testid=retweet', timeout=2)
                if retweet_icon:
                    retweet_icon.click(by_js=True);
                    human_sleep(1.0, 2.0)
                    confirm = page.ele('@data-testid=retweetConfirm', timeout=2)
                    if confirm: confirm.click(by_js=True); logger.success(f"[{p_name}] -> 转帖成功"); human_sleep(1.5,
                                                                                                                  2.5)

            if random.random() < TARGET_BOOST_CONFIG["prob_reply"]:
                reply_icon = main_tweet.ele('@data-testid=reply', timeout=2)
                if reply_icon:
                    reply_icon.click(by_js=True);
                    human_sleep(1.5, 2.5)
                    editor = page.ele('@data-testid=tweetTextarea_0', timeout=3)
                    if editor:
                        editor.click();
                        human_sleep(0.5, 1.0)
                        reply_text, comment_record = build_reply_text(
                            main_tweet,
                            page,
                            p_name=p_name,
                            fallback_url=url,
                            fallback_index=index,
                        )
                        page.actions.type(reply_text);
                        human_sleep(1.0, 2.0)
                        btn = page.ele('@data-testid=tweetButton', timeout=2) or page.ele(
                            '@data-testid=tweetButtonInline', timeout=2)
                        if btn:
                            try:
                                btn.click(by_js=True)
                                audit_comment(comment_record, "posted", "reply button clicked")
                            except Exception as exc:
                                audit_comment(comment_record, "failed", error=str(exc))
                                raise
                            logger.success(f"[{p_name}] -> 评论成功: {reply_text}"); human_sleep(3.0, 4.0)
                        else:
                            audit_comment(comment_record, "failed", error="reply submit button not found")
                    close_modal = page.ele('@data-testid=app-bar-close', timeout=1)
                    if close_modal: close_modal.click(by_js=True)

            if random.random() < TARGET_BOOST_CONFIG.get("prob_follow", 0.0):
                avatar_link_ele = main_tweet.ele('@data-testid=Tweet-User-Avatar', timeout=2)
                if avatar_link_ele:
                    profile_a = avatar_link_ele.ele('tag:a') or avatar_link_ele.parent('tag:a')
                    if profile_a:
                        profile_url = profile_a.attr('href')
                        if profile_url:
                            if not profile_url.startswith('http'): profile_url = f"https://x.com{profile_url}"
                            logger.info(f"[{p_name}] 冲贴跳转至主页(新建独立标签页): {profile_url}")
                            follow_tab = None
                            try:
                                follow_tab = page.new_tab(profile_url)
                                if not stoppable_sleep(random.uniform(2.0, 3.5)): break
                                follow_tab.scroll.down(random.randint(150, 400))
                                if not stoppable_sleep(random.uniform(1.0, 2.5)): break
                                follow_tab.scroll.up(random.randint(100, 250))
                                if not stoppable_sleep(random.uniform(1.0, 2.0)): break
                                follow_btn = follow_tab.ele('tag:button@@text()=Follow', timeout=2) or follow_tab.ele(
                                    'tag:button@@text()=关注', timeout=2)
                                if follow_btn:
                                    follow_btn.click(by_js=True)
                                    logger.success(f"[{p_name}] ✨ 冲贴关注成功！")
                                    human_sleep(2.0, 3.5)
                            except Exception as e:
                                logger.warning(f"[{p_name}] 冲贴关注时发生异常: {e}")
                            finally:
                                if follow_tab: follow_tab.close()

            if u_idx < len(target_urls) - 1 and not STOP_FLAG:
                interval = random.uniform(TARGET_BOOST_CONFIG.get("url_interval_min", 15.0),
                                          TARGET_BOOST_CONFIG.get("url_interval_max", 30.0))
                logger.warning(f"⏳ [{p_name}] 链接 {u_idx + 1} 完成，延时隐藏 {int(interval)} 秒后处理下一条...")
                if not stoppable_sleep(interval): break

        if not STOP_FLAG: logger.success(f"🏆 [{p_name}] 分配的所有冲贴任务已完成！")
    except Exception as e:
        logger.error(f"[{p_name}] 异常: {e}")


def execute_target_boost(started_profiles):
    target_urls = TARGET_BOOST_CONFIG.get("target_urls", [])
    if not target_urls:
        logger.error("冲贴目标链接为空，请在配置中添加！")
        return
    total_profiles = len(started_profiles)
    logger.info("=" * 50)
    logger.info(f"🚀 启动【集体多链接冲贴模式】 共配置了 {len(target_urls)} 条独立链接")

    threads = []
    for index, item in enumerate(started_profiles):
        if STOP_FLAG: break
        t = threading.Thread(target=boost_single_profile, args=(index, item, target_urls))
        t.start()
        threads.append(t)
        if index < total_profiles - 1 and not STOP_FLAG:
            pi_min = TARGET_BOOST_CONFIG.get("profile_interval_min", 10.0)
            pi_max = TARGET_BOOST_CONFIG.get("profile_interval_max", 25.0)
            stoppable_sleep(random.uniform(pi_min, pi_max))
    for t in threads: t.join()


# ================= 3. 发帖模式专属逻辑 =================
def execute_post_mode(started_profiles):
    """独立的推文内容自动发布引擎"""
    txt_path = POST_CONFIG.get("txt_path", "")
    img_folder = POST_CONFIG.get("img_folder", "")

    post_texts = []
    if txt_path and os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            post_texts = [line.strip() for line in f if line.strip()]

    valid_images = []
    if img_folder and os.path.exists(img_folder):
        valid_exts = {".jpg", ".jpeg", ".png", ".gif", ".mp4"}
        for fname in os.listdir(img_folder):
            if os.path.splitext(fname)[1].lower() in valid_exts:
                valid_images.append(os.path.join(img_folder, fname))

    logger.info("=" * 50)
    logger.info(f"🚀 启动【自动发帖模式】")
    logger.info(f"文本库载入: {len(post_texts)} 条 | 媒体库载入: {len(valid_images)} 个")
    logger.info("=" * 50)

    for index, item in enumerate(started_profiles):
        if STOP_FLAG: break
        disable_quickedit_mode()
        port, p_name = item['port'], item['profile'].get('name', '未命名')
        logger.info(f"--> [发帖准备] 轮到账号: [{p_name}] 出击")
        post_count = max(1, safe_int(POST_CONFIG.get("post_count", 1), 1))

        try:
            co = ChromiumOptions()
            co.set_local_port(port)
            page = ChromiumPage(co)

            try:
                tabs_to_close = [t for t in page.tab_ids if t != page.tab_id]
                if tabs_to_close: page.close_tabs(tabs_to_close)
            except Exception:
                pass

            for post_index in range(post_count):
                if STOP_FLAG: break
                page.get('https://x.com/compose/post')
                page.wait.url_change('about:blank', timeout=5)

                editor = page.ele('@data-testid=tweetTextarea_0', timeout=10)
                if not editor:
                    logger.warning(f"[{p_name}] 未能定位到发帖编辑框，可能页面未加载。")
                    continue

                text_to_post = random.choice(post_texts) if post_texts else get_dynamic_emoji_reply()
                editor.click()
                human_sleep(0.5, 1.0)
                page.actions.type(text_to_post)
                human_sleep(1.0, 2.0)

                img_count = random.randint(POST_CONFIG.get("img_count_min", 0), POST_CONFIG.get("img_count_max", 0))
                if img_count > 0 and valid_images:
                    chosen_imgs = random.sample(valid_images, min(img_count, len(valid_images)))
                    file_input = page.ele('@data-testid=fileInput', timeout=2)
                    if file_input:
                        logger.info(f"[{p_name}] 正在上传 {len(chosen_imgs)} 个媒体文件...")
                        file_input.input(chosen_imgs)
                        human_sleep(3.0 + len(chosen_imgs) * 2.0, 5.0 + len(chosen_imgs) * 2.0)

                submit_btn = page.ele('@data-testid=tweetButton', timeout=2)
                if submit_btn:
                    submit_btn.click(by_js=True)
                    logger.success(f"[{p_name}] 成功发布动态 {post_index + 1}/{post_count}: {text_to_post[:15]}...")
                    human_sleep(4.0, 5.0)

        except Exception as e:
            logger.error(f"[{p_name}] 发帖异常: {e}")

        # 账号轮询间隔防封
        if index < len(started_profiles) - 1 and not STOP_FLAG:
            interval = random.uniform(POST_CONFIG.get("profile_interval_min", 15.0),
                                      POST_CONFIG.get("profile_interval_max", 30.0))
            logger.warning(f"⏳ 延迟隐藏 {int(interval)} 秒后唤醒下一个账号...")
            if not stoppable_sleep(interval): break

    if not STOP_FLAG: logger.success("🏆 所有账号的发帖任务已圆满结束！")


# ================= 引擎守护者 =================
def bot_worker():
    global STOP_FLAG
    STOP_FLAG = False
    try:
        logger.success("=========== 引擎已启动 ===========")
        if CURRENT_JOB_ID:
            logger.info(f"当前自动化任务ID: {CURRENT_JOB_ID}")
        if PROFILE_ID:
            logger.info(f"单账号精准执行模式：profile={PROFILE_ID}")
            item = build_single_profile_item(PROFILE_ID, PROFILE_NAME, 0)
            profiles = [{"id": PROFILE_ID, "name": PROFILE_NAME or PROFILE_ID}]
            started_profiles = [item] if item else []
        else:
            profiles = get_profiles_by_group(GROUP_ID, limit=WINDOW_COUNT)
            started_profiles = []
        if not profiles:
            logger.error("未获取到任何窗口，请检查分组ID或API是否连通。")
            return

        if not PROFILE_ID:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(profiles)) as executor:
                future_to_profile = {executor.submit(start_browser, p['id'], p['name'], idx * 0.5): p for idx, p in
                                     enumerate(profiles)}
                for future in concurrent.futures.as_completed(future_to_profile):
                    if STOP_FLAG: break
                    p, res = future_to_profile[future], future.result()
                    if res and 'http' in res:
                        started_profiles.append({'profile': p, 'port': int(res['http'].split(':')[1])})
                        logger.success(f"[{p['name']}] 拉起成功！")

        if not started_profiles or STOP_FLAG:
            logger.error("未成功拉起任何浏览器窗口，任务结束。")
            return
        if not stoppable_sleep(2): return
        batch_arrange_windows(started_profiles)
        if not stoppable_sleep(2): return

        if RUN_MODE == 1:
            logger.info("开始模式 1: 全并发日常养号...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(started_profiles)) as executor:
                futures = [executor.submit(run_business_logic, item['profile'], item['port']) for item in
                           started_profiles]
                while not all(f.done() for f in futures):
                    if STOP_FLAG:
                        logger.warning("接收到停止信号，等待当前子任务安全释放...")
                        break
                    time.sleep(1)

        elif RUN_MODE == 2:
            logger.info("开始模式 2: 集体多链接轮询冲贴...")
            execute_target_boost(started_profiles)

        elif RUN_MODE == 3:
            logger.info("开始模式 3: 独立自编排发帖引擎...")
            execute_post_mode(started_profiles)

        if STOP_FLAG:
            logger.error("🛑 任务已被手动中止。")
        else:
            logger.success("✅ 任务全部正常结束，您可以关闭软件或重新启动。")
    except Exception as e:
        logger.error(f"线程异常: {e}")
    finally:
        if app_instance: app_instance.after(0, app_instance.reset_ui_state)


# ================= GUI 及 日志系统重定向 =================
# ================= GUI 及 日志系统重定向 =================
# ================= GUI 及 日志系统重定向 =================
# ================= GUI 及 日志系统重定向 =================
# 修改 newtkmain.py 里的 TkinterSink
class TkinterSink:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, message):
        try:
            # 将 Loguru 的 message 对象强转为字符串，避免跨线程处理对象生命周期问题
            msg_str = str(message)
            # 只有当文本框还存在时，才进行 after 操作
            if self.text_widget.winfo_exists():
                self.text_widget.after(0, self._append_text, msg_str)
        except Exception:
            # 报错就说明窗口正在关闭或已关闭，直接忽略，控制台仍会打印
            pass

    def _append_text(self, message):
        try:
            if self.text_widget.winfo_exists():
                self.text_widget.insert(tk.END, message)
                self.text_widget.see(tk.END)
        except Exception:
            pass


class AppGUI(tk.Toplevel):
    def __init__(self,root,exit_callback):
        super().__init__(root)
        global app_instance
        app_instance = self

        self.title("推特多开群控 RPA 引擎 V4.0 (全能内容矩阵版)")
        self.geometry("1180x900")
        self.minsize(980, 720)
        self.config_path = "config.yaml"
        self._score_task_refresh_after = None
        self._score_task_loading_plan_id = None
        self._score_plan_busy = False

        self.load_config()
        self.build_ui()
        self.exit_callback = exit_callback
        # logger.remove()
        logger.add(TkinterSink(self.log_text),
                   format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> - <level>{message}</level>")
        logger.info("界面初始化完成。配置已自动加载！")

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            self.config = {}

    def save_config(self, silent=False):
        try:
            self.config["RUN_MODE"] = self.run_mode_var.get()
            self.config["BIT_API_URL"] = self.entry_api.get()
            self.config["GROUP_ID"] = self.entry_group.get()
            self.config["WINDOW_COUNT"] = safe_int(self.entry_count.get(), 3)

            comments_raw = self.text_comments.get("1.0", tk.END).split("\n")
            self.config["COMMENT_TEXTS"] = [c.strip() for c in comments_raw if c.strip()]

            # 养号配置
            if "FARMING_CONFIG" not in self.config: self.config["FARMING_CONFIG"] = {}
            fc = self.config["FARMING_CONFIG"]
            fc["keywords"] = [k.strip() for k in self.entry_keywords.get().split(",") if k.strip()]
            fc["prob_like"] = safe_float(self.farm_plike.get(), 0.5)
            fc["prob_bookmark"] = safe_float(self.farm_pbook.get(), 0.3)
            fc["prob_reply"] = safe_float(self.farm_preply.get(), 0.2)
            fc["prob_follow"] = safe_float(self.farm_pfollow.get(), 0.1)
            fc["prob_retweet"] = safe_float(self.farm_pretweet.get(), 0.1)
            fc["max_likes"] = safe_int(self.farm_mlike.get(), 50)
            fc["max_bookmarks"] = safe_int(self.farm_mbook.get(), 30)
            fc["max_replies"] = safe_int(self.farm_mreply.get(), 20)
            fc["max_follows"] = safe_int(self.farm_mfollow.get(), 10)
            fc["max_retweets"] = safe_int(self.farm_mretweet.get(), 20)
            fc["switch_interval_min"] = safe_float(self.farm_sw_min.get(), 60.0)
            fc["switch_interval_max"] = safe_float(self.farm_sw_max.get(), 180.0)
            fc["read_delay_min"] = safe_float(self.farm_read_min.get(), 5.0)
            fc["read_delay_max"] = safe_float(self.farm_read_max.get(), 12.0)
            fc["pause_after_likes_min"] = safe_int(self.farm_pause_like_min.get(), 15)
            fc["pause_after_likes_max"] = safe_int(self.farm_pause_like_max.get(), 28)
            fc["pause_duration_min"] = safe_float(self.farm_pause_dur_min.get(), 600.0)
            fc["pause_duration_max"] = safe_float(self.farm_pause_dur_max.get(), 1200.0)

            # 冲贴配置
            if "TARGET_BOOST_CONFIG" not in self.config: self.config["TARGET_BOOST_CONFIG"] = {}
            tc = self.config["TARGET_BOOST_CONFIG"]
            raw_urls = self.text_target_urls.get("1.0", tk.END).split("\n")
            tc["target_urls"] = [u.strip() for u in raw_urls if u.strip()]
            tc["prob_like"] = safe_float(self.boost_plike.get(), 1.0)
            tc["prob_bookmark"] = safe_float(self.boost_pbook.get(), 0.8)
            tc["prob_retweet"] = safe_float(self.boost_pretweet.get(), 0.6)
            tc["prob_reply"] = safe_float(self.boost_preply.get(), 0.8)
            tc["prob_follow"] = safe_float(self.boost_pfollow.get(), 0.0)
            tc["profile_interval_min"] = safe_float(self.boost_pi_min.get(), 10.0)
            tc["profile_interval_max"] = safe_float(self.boost_pi_max.get(), 25.0)
            tc["url_interval_min"] = safe_float(self.boost_url_int_min.get(), 15.0)
            tc["url_interval_max"] = safe_float(self.boost_url_int_max.get(), 30.0)
            tc["read_delay_min"] = safe_float(self.boost_read_min.get(), 6.0)
            tc["read_delay_max"] = safe_float(self.boost_read_max.get(), 15.0)

            # 发帖配置
            if "POST_CONFIG" not in self.config: self.config["POST_CONFIG"] = {}
            pc = self.config["POST_CONFIG"]
            pc["txt_path"] = self.post_txt_entry.get()
            pc["img_folder"] = self.post_img_entry.get()
            pc["img_count_min"] = safe_int(self.post_img_min.get(), 0)
            pc["img_count_max"] = safe_int(self.post_img_max.get(), 1)
            pc["profile_interval_min"] = safe_float(self.post_pi_min.get(), 15.0)
            pc["profile_interval_max"] = safe_float(self.post_pi_max.get(), 30.0)

            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config, f, allow_unicode=True, sort_keys=False)

            if not silent: messagebox.showinfo("成功", "配置已手动保存至 config.yaml！")
        except Exception as e:
            if not silent: messagebox.showerror("错误", f"保存配置失败: {e}")

    def on_closing(self):
        self.stop_script()  # 停止正在运行的业务引擎
        self.save_config(silent=True)  # 静默保存配置
        # logger.remove()  # 彻底切断新窗口绑定的日志
        self.exit_callback()  # 调用 newkami 传过来的注销机制
        self.destroy()  # 销毁界面

    def reset_ui_state(self):
        self.btn_start.config(state=tk.NORMAL, text="🚀 启动脚本")
        self.btn_stop.config(state=tk.DISABLED, text="⏹️ 停止运行")

    def stop_script(self):
        global STOP_FLAG
        STOP_FLAG = True
        logger.warning("⚠️ 已发送停止信号，等待当前模块安全退出并释放资源...")
        self.btn_stop.config(state=tk.DISABLED, text="正在停止...")

    def apply_globals_and_start(self):
        self.save_config(silent=True)
        global RUN_MODE, BIT_API_URL, GROUP_ID, WINDOW_COUNT, COMMENT_TEXTS, FARMING_CONFIG, TARGET_BOOST_CONFIG, POST_CONFIG
        RUN_MODE = self.config.get("RUN_MODE", 1)
        BIT_API_URL = self.config.get("BIT_API_URL", "http://127.0.0.1:54345")
        GROUP_ID = self.config.get("GROUP_ID", "")
        WINDOW_COUNT = self.config.get("WINDOW_COUNT", 3)
        COMMENT_TEXTS = self.config.get("COMMENT_TEXTS", [])
        FARMING_CONFIG = self.config.get("FARMING_CONFIG", {})
        TARGET_BOOST_CONFIG = self.config.get("TARGET_BOOST_CONFIG", {})
        POST_CONFIG = self.config.get("POST_CONFIG", {})

        self.btn_start.config(state=tk.DISABLED, text="运行中...")
        self.btn_stop.config(state=tk.NORMAL, text="⏹️ 停止运行")
        threading.Thread(target=bot_worker, daemon=True).start()

    def build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        frame_top = tk.Frame(self)
        frame_top.pack(fill=tk.X, padx=10, pady=10)
        self.btn_save = ttk.Button(frame_top, text="💾 手动保存配置", command=lambda: self.save_config(silent=False))
        self.btn_save.pack(side=tk.LEFT, padx=5)
        self.btn_start = ttk.Button(frame_top, text="🚀 启动脚本", command=self.apply_globals_and_start)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(frame_top, text="⏹️ 停止运行", command=self.stop_script, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        tab_basic = ttk.Frame(notebook)
        tab_farm = ttk.Frame(notebook)
        tab_boost = ttk.Frame(notebook)
        tab_post = ttk.Frame(notebook)  # 新增发帖模式Tab
        tab_comments = ttk.Frame(notebook)
        tab_score_plan = ttk.Frame(notebook)
        tab_group_admin = ttk.Frame(notebook)

        notebook.add(tab_basic, text="⚙️ 基础设置")
        notebook.add(tab_farm, text="🌾 养号全量配置")
        notebook.add(tab_boost, text="🔥 冲贴全量配置")
        notebook.add(tab_post, text="📝 发帖矩阵配置")
        notebook.add(tab_comments, text="💭 文本评论库")
        notebook.add(tab_score_plan, text="📊 账号评分计划")
        notebook.add(tab_group_admin, text="🖥️ 分组账号管理")

        self.build_basic_tab(tab_basic)
        self.build_farm_tab(tab_farm)
        self.build_boost_tab(tab_boost)
        self.build_post_tab(tab_post)
        self.build_comments_tab(tab_comments)
        self.build_score_plan_tab(tab_score_plan)
        self.build_group_admin_tab(tab_group_admin)
        log_holder = tab_basic

        frame_log = tk.LabelFrame(log_holder, text="📝 运行日志")

    def build_basic_tab(self, parent):
        basic_paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        basic_paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        settings_holder = ttk.Frame(basic_paned)
        log_holder = ttk.Frame(basic_paned)
        basic_paned.add(settings_holder, weight=3)
        basic_paned.add(log_holder, weight=1)

        self.run_mode_var = tk.IntVar(value=self.config.get("RUN_MODE", 1))
        ttk.Radiobutton(settings_holder, text="模式 1: 全并发日常养号 (探索/主页刷流)", variable=self.run_mode_var,
                        value=1).pack(anchor=tk.W, padx=20, pady=10)
        ttk.Radiobutton(settings_holder, text="模式 2: 集中火力排队冲贴 (支持多链接轮询并发)", variable=self.run_mode_var,
                        value=2).pack(anchor=tk.W, padx=20, pady=5)
        ttk.Radiobutton(settings_holder, text="模式 3: 独立自编排发帖引擎 (随机读取本地图文库发布)", variable=self.run_mode_var,
                        value=3).pack(anchor=tk.W, padx=20, pady=10)

        f1 = ttk.Frame(settings_holder)
        f1.pack(fill=tk.X, padx=20, pady=15)
        ttk.Label(f1, text="比特 API 地址:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.entry_api = ttk.Entry(f1, width=30);
        self.entry_api.insert(0, self.config.get("BIT_API_URL", "http://127.0.0.1:54345"));
        self.entry_api.grid(row=0, column=1, padx=10)
        ttk.Label(f1, text="浏览器分组 ID:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.entry_group = ttk.Entry(f1, width=40);
        self.entry_group.insert(0, self.config.get("GROUP_ID", ""));
        self.entry_group.grid(row=1, column=1, padx=10)
        ttk.Label(f1, text="启动窗口数量:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.entry_count = ttk.Entry(f1, width=10);
        self.entry_count.insert(0, str(self.config.get("WINDOW_COUNT", 3)));
        self.entry_count.grid(row=2, column=1, sticky=tk.W, padx=10)

        frame_log = tk.LabelFrame(log_holder, text="运行日志")
        frame_log.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            frame_log,
            bg="black",
            fg="lightgreen",
            font=("Consolas", 10),
            height=10,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def build_farm_tab(self, parent):
        fc = self.config.get("FARMING_CONFIG", {})
        f_top = ttk.Frame(parent);
        f_top.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(f_top, text="搜索关键词 (英文逗号分隔):").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.entry_keywords = ttk.Entry(f_top, width=40);
        self.entry_keywords.insert(0, ",".join(fc.get("keywords", ["日本株", "Web3", "AI"])));
        self.entry_keywords.grid(row=0, column=1, sticky=tk.W)

        lf_prob = ttk.LabelFrame(f_top, text="触发概率 (0.0 - 1.0)")
        lf_prob.grid(row=1, column=0, columnspan=2, pady=5, sticky=tk.W + tk.E)
        ttk.Label(lf_prob, text="点赞:").grid(row=0, column=0, padx=5, pady=5);
        self.farm_plike = ttk.Entry(lf_prob, width=8);
        self.farm_plike.insert(0, str(fc.get("prob_like", 0.5)));
        self.farm_plike.grid(row=0, column=1)
        ttk.Label(lf_prob, text="收藏:").grid(row=0, column=2, padx=5, pady=5);
        self.farm_pbook = ttk.Entry(lf_prob, width=8);
        self.farm_pbook.insert(0, str(fc.get("prob_bookmark", 0.3)));
        self.farm_pbook.grid(row=0, column=3)
        ttk.Label(lf_prob, text="评论:").grid(row=0, column=4, padx=5, pady=5);
        self.farm_preply = ttk.Entry(lf_prob, width=8);
        self.farm_preply.insert(0, str(fc.get("prob_reply", 0.2)));
        self.farm_preply.grid(row=0, column=5)
        ttk.Label(lf_prob, text="关注:").grid(row=1, column=0, padx=5, pady=5);
        self.farm_pfollow = ttk.Entry(lf_prob, width=8);
        self.farm_pfollow.insert(0, str(fc.get("prob_follow", 0.1)));
        self.farm_pfollow.grid(row=1, column=1)
        ttk.Label(lf_prob, text="转帖:").grid(row=1, column=2, padx=5, pady=5);
        self.farm_pretweet = ttk.Entry(lf_prob, width=8);
        self.farm_pretweet.insert(0, str(fc.get("prob_retweet", 0.1)));
        self.farm_pretweet.grid(row=1, column=3)

        lf_max = ttk.LabelFrame(f_top, text="单窗口任务上限 (达标收工)")
        lf_max.grid(row=1, column=2, columnspan=2, padx=10, pady=5, sticky=tk.W + tk.E)
        ttk.Label(lf_max, text="最高点赞:").grid(row=0, column=0, padx=5, pady=5);
        self.farm_mlike = ttk.Entry(lf_max, width=8);
        self.farm_mlike.insert(0, str(fc.get("max_likes", 50)));
        self.farm_mlike.grid(row=0, column=1)
        ttk.Label(lf_max, text="最高收藏:").grid(row=0, column=2, padx=5, pady=5);
        self.farm_mbook = ttk.Entry(lf_max, width=8);
        self.farm_mbook.insert(0, str(fc.get("max_bookmarks", 30)));
        self.farm_mbook.grid(row=0, column=3)
        ttk.Label(lf_max, text="最高评论:").grid(row=0, column=4, padx=5, pady=5);
        self.farm_mreply = ttk.Entry(lf_max, width=8);
        self.farm_mreply.insert(0, str(fc.get("max_replies", 20)));
        self.farm_mreply.grid(row=0, column=5)
        ttk.Label(lf_max, text="最高关注:").grid(row=1, column=0, padx=5, pady=5);
        self.farm_mfollow = ttk.Entry(lf_max, width=8);
        self.farm_mfollow.insert(0, str(fc.get("max_follows", 10)));
        self.farm_mfollow.grid(row=1, column=1)
        ttk.Label(lf_max, text="最高转帖:").grid(row=1, column=2, padx=5, pady=5);
        self.farm_mretweet = ttk.Entry(lf_max, width=8);
        self.farm_mretweet.insert(0, str(fc.get("max_retweets", 20)));
        self.farm_mretweet.grid(row=1, column=3)

        lf_time = ttk.LabelFrame(parent, text="养号行为与防风控时间设置 (单位：秒 或 次)")
        lf_time.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(lf_time, text="模块停留(秒):").grid(row=0, column=0, padx=5, pady=5)
        self.farm_sw_min = ttk.Entry(lf_time, width=6);
        self.farm_sw_min.insert(0, str(fc.get("switch_interval_min", 60.0)));
        self.farm_sw_min.grid(row=0, column=1)
        ttk.Label(lf_time, text="至").grid(row=0, column=2);
        self.farm_sw_max = ttk.Entry(lf_time, width=6);
        self.farm_sw_max.insert(0, str(fc.get("switch_interval_max", 180.0)));
        self.farm_sw_max.grid(row=0, column=3)
        ttk.Label(lf_time, text="阅读延时(秒):").grid(row=1, column=0, padx=5, pady=5)
        self.farm_read_min = ttk.Entry(lf_time, width=6);
        self.farm_read_min.insert(0, str(fc.get("read_delay_min", 5.0)));
        self.farm_read_min.grid(row=1, column=1)
        ttk.Label(lf_time, text="至").grid(row=1, column=2);
        self.farm_read_max = ttk.Entry(lf_time, width=6);
        self.farm_read_max.insert(0, str(fc.get("read_delay_max", 12.0)));
        self.farm_read_max.grid(row=1, column=3)
        ttk.Label(lf_time, text="潮汐触发(次):").grid(row=0, column=4, padx=15, pady=5)
        self.farm_pause_like_min = ttk.Entry(lf_time, width=6);
        self.farm_pause_like_min.insert(0, str(fc.get("pause_after_likes_min", 15)));
        self.farm_pause_like_min.grid(row=0, column=5)
        ttk.Label(lf_time, text="至").grid(row=0, column=6);
        self.farm_pause_like_max = ttk.Entry(lf_time, width=6);
        self.farm_pause_like_max.insert(0, str(fc.get("pause_after_likes_max", 28)));
        self.farm_pause_like_max.grid(row=0, column=7)
        ttk.Label(lf_time, text="休眠时长(秒):").grid(row=1, column=4, padx=15, pady=5)
        self.farm_pause_dur_min = ttk.Entry(lf_time, width=6);
        self.farm_pause_dur_min.insert(0, str(fc.get("pause_duration_min", 600.0)));
        self.farm_pause_dur_min.grid(row=1, column=5)
        ttk.Label(lf_time, text="至").grid(row=1, column=6);
        self.farm_pause_dur_max = ttk.Entry(lf_time, width=6);
        self.farm_pause_dur_max.insert(0, str(fc.get("pause_duration_max", 1200.0)));
        self.farm_pause_dur_max.grid(row=1, column=7)

    def build_boost_tab(self, parent):
        tc = self.config.get("TARGET_BOOST_CONFIG", {})
        ttk.Label(parent, text="目标冲贴 URLs (一行一条):").grid(row=0, column=0, padx=10, pady=15, sticky=tk.NW)
        self.text_target_urls = scrolledtext.ScrolledText(parent, width=70, height=4)
        self.text_target_urls.grid(row=0, column=1, columnspan=3, sticky=tk.W, pady=10)

        urls = tc.get("target_urls", [])
        if isinstance(urls, str): urls = [urls]
        if urls: self.text_target_urls.insert(tk.END, "\n".join(urls))

        lf_prob = ttk.LabelFrame(parent, text="火力概率 (0.0 - 1.0)")
        lf_prob.grid(row=1, column=0, columnspan=2, padx=10, pady=5, sticky=tk.W + tk.E)
        ttk.Label(lf_prob, text="点赞:").grid(row=0, column=0, padx=10, pady=5);
        self.boost_plike = ttk.Entry(lf_prob, width=8);
        self.boost_plike.insert(0, str(tc.get("prob_like", 1.0)));
        self.boost_plike.grid(row=0, column=1)
        ttk.Label(lf_prob, text="收藏:").grid(row=0, column=2, padx=10, pady=5);
        self.boost_pbook = ttk.Entry(lf_prob, width=8);
        self.boost_pbook.insert(0, str(tc.get("prob_bookmark", 0.8)));
        self.boost_pbook.grid(row=0, column=3)
        ttk.Label(lf_prob, text="转帖:").grid(row=0, column=4, padx=10, pady=5);
        self.boost_pretweet = ttk.Entry(lf_prob, width=8);
        self.boost_pretweet.insert(0, str(tc.get("prob_retweet", 0.6)));
        self.boost_pretweet.grid(row=0, column=5)
        ttk.Label(lf_prob, text="评论:").grid(row=1, column=0, padx=10, pady=5);
        self.boost_preply = ttk.Entry(lf_prob, width=8);
        self.boost_preply.insert(0, str(tc.get("prob_reply", 0.8)));
        self.boost_preply.grid(row=1, column=1)
        ttk.Label(lf_prob, text="关注:").grid(row=1, column=2, padx=10, pady=5);
        self.boost_pfollow = ttk.Entry(lf_prob, width=8);
        self.boost_pfollow.insert(0, str(tc.get("prob_follow", 0.0)));
        self.boost_pfollow.grid(row=1, column=3)

        lf_time = ttk.LabelFrame(parent, text="排队与切换防封延时 (单位：秒)")
        lf_time.grid(row=1, column=2, columnspan=2, padx=10, pady=5, sticky=tk.W + tk.E)
        ttk.Label(lf_time, text="账号进场间隔:").grid(row=0, column=0, padx=5, pady=5);
        self.boost_pi_min = ttk.Entry(lf_time, width=6);
        self.boost_pi_min.insert(0, str(tc.get("profile_interval_min", 10.0)));
        self.boost_pi_min.grid(row=0, column=1)
        ttk.Label(lf_time, text="至").grid(row=0, column=2);
        self.boost_pi_max = ttk.Entry(lf_time, width=6);
        self.boost_pi_max.insert(0, str(tc.get("profile_interval_max", 25.0)));
        self.boost_pi_max.grid(row=0, column=3)
        ttk.Label(lf_time, text="单号链接切换延时:").grid(row=1, column=0, padx=5, pady=5);
        self.boost_url_int_min = ttk.Entry(lf_time, width=6);
        self.boost_url_int_min.insert(0, str(tc.get("url_interval_min", 15.0)));
        self.boost_url_int_min.grid(row=1, column=1)
        ttk.Label(lf_time, text="至").grid(row=1, column=2);
        self.boost_url_int_max = ttk.Entry(lf_time, width=6);
        self.boost_url_int_max.insert(0, str(tc.get("url_interval_max", 30.0)));
        self.boost_url_int_max.grid(row=1, column=3)
        ttk.Label(lf_time, text="详情阅读延时:").grid(row=2, column=0, padx=5, pady=5);
        self.boost_read_min = ttk.Entry(lf_time, width=6);
        self.boost_read_min.insert(0, str(tc.get("read_delay_min", 6.0)));
        self.boost_read_min.grid(row=2, column=1)
        ttk.Label(lf_time, text="至").grid(row=2, column=2);
        self.boost_read_max = ttk.Entry(lf_time, width=6);
        self.boost_read_max.insert(0, str(tc.get("read_delay_max", 15.0)));
        self.boost_read_max.grid(row=2, column=3)

    def build_post_tab(self, parent):
        pc = self.config.get("POST_CONFIG", {})

        # 文件选取辅助函数
        def select_txt_file():
            path = filedialog.askopenfilename(title="选择帖子文本库",
                                              filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if path:
                self.post_txt_entry.delete(0, tk.END)
                self.post_txt_entry.insert(0, path)
                self.save_config(silent=True)

        def select_img_folder():
            path = filedialog.askdirectory(title="选择媒体素材文件夹")
            if path:
                self.post_img_entry.delete(0, tk.END)
                self.post_img_entry.insert(0, path)
                self.save_config(silent=True)

        f_file = ttk.Frame(parent)
        f_file.pack(fill=tk.X, padx=20, pady=15)

        ttk.Label(f_file, text="文本内容(TXT文件):").grid(row=0, column=0, sticky=tk.W, pady=10)
        self.post_txt_entry = ttk.Entry(f_file, width=45)
        self.post_txt_entry.insert(0, pc.get("txt_path", ""))
        self.post_txt_entry.grid(row=0, column=1, padx=10)
        self.post_txt_entry.bind("<FocusOut>", lambda _e: self.save_config(silent=True))
        ttk.Button(f_file, text="浏览...", command=select_txt_file).grid(row=0, column=2)

        ttk.Label(f_file, text="媒体库(图片文件夹):").grid(row=1, column=0, sticky=tk.W, pady=10)
        self.post_img_entry = ttk.Entry(f_file, width=45)
        self.post_img_entry.insert(0, pc.get("img_folder", ""))
        self.post_img_entry.grid(row=1, column=1, padx=10)
        self.post_img_entry.bind("<FocusOut>", lambda _e: self.save_config(silent=True))
        ttk.Button(f_file, text="浏览...", command=select_img_folder).grid(row=1, column=2)

        lf_logic = ttk.LabelFrame(parent, text="调度参数")
        lf_logic.pack(fill=tk.X, padx=20, pady=10)

        ttk.Label(lf_logic, text="随机抽取图片数量:").grid(row=0, column=0, padx=10, pady=10)
        self.post_img_min = ttk.Entry(lf_logic, width=6);
        self.post_img_min.insert(0, str(pc.get("img_count_min", 0)));
        self.post_img_min.grid(row=0, column=1)
        ttk.Label(lf_logic, text="至").grid(row=0, column=2);
        self.post_img_max = ttk.Entry(lf_logic, width=6);
        self.post_img_max.insert(0, str(pc.get("img_count_max", 1)));
        self.post_img_max.grid(row=0, column=3)
        ttk.Label(lf_logic, text="个").grid(row=0, column=4, sticky=tk.W)

        ttk.Label(lf_logic, text="账号串行进场延时:").grid(row=1, column=0, padx=10, pady=10)
        self.post_pi_min = ttk.Entry(lf_logic, width=6);
        self.post_pi_min.insert(0, str(pc.get("profile_interval_min", 15.0)));
        self.post_pi_min.grid(row=1, column=1)
        ttk.Label(lf_logic, text="至").grid(row=1, column=2);
        self.post_pi_max = ttk.Entry(lf_logic, width=6);
        self.post_pi_max.insert(0, str(pc.get("profile_interval_max", 30.0)));
        self.post_pi_max.grid(row=1, column=3)
        ttk.Label(lf_logic, text="秒").grid(row=1, column=4, sticky=tk.W)

    def build_comments_tab(self, parent):
        ttk.Label(parent, text="在此输入自定义评论文本（每行一条，不支持空行）：").pack(anchor=tk.W, padx=10, pady=5)
        self.text_comments = scrolledtext.ScrolledText(parent, width=80, height=12)
        self.text_comments.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        existing_comments = self.config.get("COMMENT_TEXTS", [])
        if existing_comments: self.text_comments.insert(tk.END, "\n".join(existing_comments))

    def build_score_plan_tab(self, parent):
        score_paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        score_paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        upper = ttk.Frame(score_paned)
        lower = ttk.Frame(score_paned)
        score_paned.add(upper, weight=3)
        score_paned.add(lower, weight=2)

        upper.rowconfigure(1, weight=1)
        upper.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        lower.columnconfigure(0, weight=1)

        top = ttk.LabelFrame(upper, text="中央评分提示词")
        top.grid(row=0, column=0, sticky="nsew", padx=10, pady=(8, 4))
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)
        self.score_prompt_text = scrolledtext.ScrolledText(top, height=8, wrap=tk.WORD)
        self.score_prompt_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=6)
        prompt_btns = ttk.Frame(top)
        prompt_btns.grid(row=1, column=0, sticky=tk.W, padx=8, pady=(0, 6))
        ttk.Button(prompt_btns, text="刷新提示词", command=self.refresh_score_prompt).pack(side=tk.LEFT, padx=4)
        ttk.Button(prompt_btns, text="保存提示词到中央", command=self.save_score_prompt).pack(side=tk.LEFT, padx=4)

        mid = ttk.LabelFrame(upper, text="账号评分计划")
        mid.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 8))
        mid.rowconfigure(2, weight=1)
        mid.columnconfigure(0, weight=1)

        filters = ttk.Frame(mid)
        filters.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ttk.Label(filters, text="分组/别名:").pack(side=tk.LEFT)
        self.score_group_entry = ttk.Entry(filters, width=24)
        self.score_group_entry.insert(0, self.config.get("GROUP_ID", ""))
        self.score_group_entry.pack(side=tk.LEFT, padx=4)
        self.show_history_plans_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="显示历史失效计划", variable=self.show_history_plans_var).pack(side=tk.LEFT, padx=4)
        ttk.Button(filters, text="刷新计划", command=self.refresh_score_plans).pack(side=tk.LEFT, padx=4)
        ttk.Button(filters, text="生成所选计划调度", command=self.schedule_selected_score_plan).pack(side=tk.LEFT, padx=4)

        plan_actions = ttk.Frame(mid)
        plan_actions.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        ttk.Button(plan_actions, text="暂停所选计划", command=lambda: self.set_selected_score_plan_status("pause")).pack(side=tk.LEFT, padx=4)
        ttk.Button(plan_actions, text="恢复所选计划", command=lambda: self.set_selected_score_plan_status("resume")).pack(side=tk.LEFT, padx=4)
        ttk.Button(plan_actions, text="删除所选计划", command=lambda: self.set_selected_score_plan_status("delete")).pack(side=tk.LEFT, padx=4)

        plan_table_box = ttk.Frame(mid)
        plan_table_box.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        plan_table_box.rowconfigure(0, weight=1)
        plan_table_box.columnconfigure(0, weight=1)

        columns = ("plan", "account", "score", "status", "tasks")
        self.score_plan_tree = ttk.Treeview(plan_table_box, columns=columns, show="headings", height=7, selectmode="extended")
        for col, text, width in [
            ("plan", "计划ID", 70),
            ("account", "账号/Profile", 220),
            ("score", "评分", 70),
            ("status", "状态", 90),
            ("tasks", "任务统计", 220),
        ]:
            self.score_plan_tree.heading(col, text=text)
            self.score_plan_tree.column(col, width=width)
        plan_scrollbar = ttk.Scrollbar(plan_table_box, orient=tk.VERTICAL, command=self.score_plan_tree.yview)
        self.score_plan_tree.configure(yscrollcommand=plan_scrollbar.set)
        self.score_plan_tree.grid(row=0, column=0, sticky="nsew")
        plan_scrollbar.grid(row=0, column=1, sticky="ns")
        self.score_plan_tree.bind("<<TreeviewSelect>>", self.on_score_plan_select)

        task_frame = ttk.LabelFrame(lower, text="所选计划任务表")
        task_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=8)
        task_frame.rowconfigure(1, weight=1)
        task_frame.columnconfigure(0, weight=1)

        task_actions = ttk.Frame(task_frame)
        task_actions.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ttk.Button(task_actions, text="暂停所选任务", command=lambda: self.set_selected_schedule_status("pause")).pack(side=tk.LEFT, padx=4)
        ttk.Button(task_actions, text="恢复所选任务", command=lambda: self.set_selected_schedule_status("resume")).pack(side=tk.LEFT, padx=4)
        ttk.Button(task_actions, text="取消所选任务", command=lambda: self.set_selected_schedule_status("cancel")).pack(side=tk.LEFT, padx=4)

        task_table_box = ttk.Frame(task_frame)
        task_table_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        task_table_box.rowconfigure(0, weight=1)
        task_table_box.columnconfigure(0, weight=1)

        task_cols = ("task", "time", "status", "mode", "metrics")
        self.score_task_tree = ttk.Treeview(task_table_box, columns=task_cols, show="headings", height=8, selectmode="extended")
        for col, text, width in [
            ("task", "任务ID", 70),
            ("time", "执行时间", 150),
            ("status", "状态", 80),
            ("mode", "模式", 60),
            ("metrics", "指标", 520),
        ]:
            self.score_task_tree.heading(col, text=text)
            self.score_task_tree.column(col, width=width, minwidth=width, stretch=(col == "metrics"))
        task_scrollbar = ttk.Scrollbar(task_table_box, orient=tk.VERTICAL, command=self.score_task_tree.yview)
        task_xscrollbar = ttk.Scrollbar(task_table_box, orient=tk.HORIZONTAL, command=self.score_task_tree.xview)
        self.score_task_tree.configure(yscrollcommand=task_scrollbar.set, xscrollcommand=task_xscrollbar.set)
        self.score_task_tree.grid(row=0, column=0, sticky="nsew")
        task_scrollbar.grid(row=0, column=1, sticky="ns")
        task_xscrollbar.grid(row=1, column=0, sticky="ew")
        self.after(800, self.refresh_score_prompt)

    def run_score_api_async(self, work, on_success=None, on_error=None):
        def runner():
            try:
                result = work()
                self.after(0, lambda: on_success(result) if on_success else None)
            except Exception as exc:
                self.after(0, lambda exc=exc: on_error(exc) if on_error else logger.warning(f"账号评分计划请求失败: {exc}"))

        threading.Thread(target=runner, daemon=True).start()

    def set_score_plan_busy(self, busy, message=""):
        self._score_plan_busy = busy
        if message:
            logger.info(message)

    def on_score_plan_select(self, _event=None):
        if self._score_task_refresh_after:
            self.after_cancel(self._score_task_refresh_after)
        self._score_task_refresh_after = self.after(250, self.refresh_score_plan_tasks)

    def refresh_score_prompt(self):
        if not hasattr(self, "score_prompt_text"):
            return
        self.set_score_plan_busy(True, "正在刷新账号评分提示词...")

        def work():
            return central_api_request("GET", "/score-prompt")

        def done(data):
            self.score_prompt_text.delete("1.0", tk.END)
            self.score_prompt_text.insert(tk.END, data.get("prompt", ""))
            self.set_score_plan_busy(False)
            logger.info("账号评分提示词已从中央刷新。")

        def failed(exc):
            self.set_score_plan_busy(False)
            logger.warning(f"刷新账号评分提示词失败: {exc}")

        self.run_score_api_async(work, done, failed)

    def save_score_prompt(self):
        prompt = self.score_prompt_text.get("1.0", tk.END).strip()
        self.set_score_plan_busy(True, "正在保存账号评分提示词...")

        def work():
            central_api_request("POST", "/score-prompt", {"prompt": prompt})
            return True

        def done(_):
            self.set_score_plan_busy(False)
            messagebox.showinfo("成功", "账号评分提示词已保存到中央。")

        def failed(exc):
            self.set_score_plan_busy(False)
            messagebox.showerror("错误", f"保存提示词失败: {exc}")

        self.run_score_api_async(work, done, failed)

    def refresh_score_plans(self):
        group_id = self.score_group_entry.get().strip()
        current_only = "0" if self.show_history_plans_var.get() else "1"
        self.set_score_plan_busy(True, "正在刷新账号评分计划...")

        def work():
            return central_api_request(
                "GET",
                "/score-plans",
                query={"group_id": group_id, "limit": 200, "current_only": current_only},
            )

        def done(data):
            for item in self.score_plan_tree.get_children():
                self.score_plan_tree.delete(item)
            for item in self.score_task_tree.get_children():
                self.score_task_tree.delete(item)
            for plan in data.get("plans", []):
                summary = plan.get("task_summary") or {}
                task_text = " ".join(f"{k}:{v}" for k, v in summary.items() if k != "total") or "无"
                self.score_plan_tree.insert(
                    "",
                    tk.END,
                    iid=str(plan.get("id")),
                    values=(plan.get("id"), plan.get("account_id"), plan.get("score") or "-", plan.get("status"), task_text),
                )
            self.set_score_plan_busy(False)
            logger.info(f"已刷新账号评分计划: {data.get('count', 0)} 条")

        def failed(exc):
            self.set_score_plan_busy(False)
            messagebox.showerror("错误", f"刷新账号计划失败: {exc}")

        self.run_score_api_async(work, done, failed)

    def selected_score_plan_id(self):
        selected = self.score_plan_tree.selection()
        return int(selected[0]) if selected else None

    def selected_score_plan_ids(self):
        return [int(item) for item in self.score_plan_tree.selection()]

    def selected_score_task_id(self):
        selected = self.score_task_tree.selection()
        return int(selected[0]) if selected else None

    def selected_score_task_ids(self):
        return [int(item) for item in self.score_task_tree.selection()]

    def build_group_admin_tab(self, parent):
        paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        top = ttk.Frame(paned)
        bottom = ttk.Frame(paned)
        paned.add(top, weight=2)
        paned.add(bottom, weight=3)
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        top.rowconfigure(1, weight=1)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(top)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="刷新", command=self.refresh_group_admin).pack(side=tk.LEFT, padx=4)
        ttk.Label(toolbar, text="别名").pack(side=tk.LEFT, padx=(16, 4))
        self.admin_alias_entry = ttk.Entry(toolbar, width=14)
        self.admin_alias_entry.pack(side=tk.LEFT, padx=4)
        ttk.Label(toolbar, text="分组ID").pack(side=tk.LEFT, padx=(8, 4))
        self.admin_group_entry = ttk.Entry(toolbar, width=34)
        self.admin_group_entry.pack(side=tk.LEFT, padx=4)
        ttk.Label(toolbar, text="电脑").pack(side=tk.LEFT, padx=(8, 4))
        self.admin_node_entry = ttk.Entry(toolbar, width=12)
        self.admin_node_entry.insert(0, "PC-01")
        self.admin_node_entry.pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="绑定并同步", command=self.admin_bind_group).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="解绑别名", command=self.admin_unbind_alias).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="添加同步分组", command=self.admin_add_sync_group).pack(side=tk.LEFT, padx=4)

        worker_box = ttk.LabelFrame(top, text="电脑")
        worker_box.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        worker_box.rowconfigure(0, weight=1)
        worker_box.columnconfigure(0, weight=1)
        self.admin_worker_tree = ttk.Treeview(worker_box, columns=("node", "label", "status", "groups"), show="headings", height=7)
        for col, text, width in [("node", "电脑", 90), ("label", "名称", 140), ("status", "状态", 80), ("groups", "同步分组", 420)]:
            self.admin_worker_tree.heading(col, text=text)
            self.admin_worker_tree.column(col, width=width)
        worker_scroll = ttk.Scrollbar(worker_box, orient=tk.VERTICAL, command=self.admin_worker_tree.yview)
        self.admin_worker_tree.configure(yscrollcommand=worker_scroll.set)
        self.admin_worker_tree.grid(row=0, column=0, sticky="nsew")
        worker_scroll.grid(row=0, column=1, sticky="ns")

        group_box = ttk.LabelFrame(top, text="分组")
        group_box.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        group_box.rowconfigure(0, weight=1)
        group_box.columnconfigure(0, weight=1)
        self.admin_group_tree = ttk.Treeview(group_box, columns=("alias", "group", "active", "inactive", "nodes", "sync"), show="headings", height=7)
        for col, text, width in [
            ("alias", "别名", 110),
            ("group", "分组ID", 250),
            ("active", "账号", 60),
            ("inactive", "停用", 60),
            ("nodes", "账号电脑", 110),
            ("sync", "同步电脑", 110),
        ]:
            self.admin_group_tree.heading(col, text=text)
            self.admin_group_tree.column(col, width=width)
        group_scroll = ttk.Scrollbar(group_box, orient=tk.VERTICAL, command=self.admin_group_tree.yview)
        self.admin_group_tree.configure(yscrollcommand=group_scroll.set)
        self.admin_group_tree.grid(row=0, column=0, sticky="nsew")
        group_scroll.grid(row=0, column=1, sticky="ns")
        self.admin_group_tree.bind("<<TreeviewSelect>>", self.on_admin_group_select)

        account_toolbar = ttk.Frame(bottom)
        account_toolbar.grid(row=0, column=0, sticky="ew", pady=(6, 4))
        ttk.Button(account_toolbar, text="刷新账号", command=self.refresh_admin_accounts).pack(side=tk.LEFT, padx=4)
        ttk.Button(account_toolbar, text="停用所选账号", command=lambda: self.admin_set_account_status("inactive")).pack(side=tk.LEFT, padx=4)
        ttk.Button(account_toolbar, text="恢复所选账号", command=lambda: self.admin_set_account_status("active")).pack(side=tk.LEFT, padx=4)

        account_box = ttk.LabelFrame(bottom, text="账号")
        account_box.grid(row=1, column=0, sticky="nsew")
        account_box.rowconfigure(0, weight=1)
        account_box.columnconfigure(0, weight=1)
        self.admin_account_tree = ttk.Treeview(account_box, columns=("profile", "name", "group", "node", "status", "seen"), show="headings", height=10, selectmode="extended")
        for col, text, width in [
            ("profile", "Profile", 250),
            ("name", "名称", 160),
            ("group", "分组ID", 250),
            ("node", "电脑", 90),
            ("status", "状态", 80),
            ("seen", "最后同步", 150),
        ]:
            self.admin_account_tree.heading(col, text=text)
            self.admin_account_tree.column(col, width=width)
        account_scroll = ttk.Scrollbar(account_box, orient=tk.VERTICAL, command=self.admin_account_tree.yview)
        self.admin_account_tree.configure(yscrollcommand=account_scroll.set)
        self.admin_account_tree.grid(row=0, column=0, sticky="nsew")
        account_scroll.grid(row=0, column=1, sticky="ns")
        self.after(1000, self.refresh_group_admin)

    def refresh_group_admin(self):
        def work():
            return {
                "workers": central_api_request("GET", "/workers").get("workers", []),
                "groups": central_api_request("GET", "/groups").get("groups", []),
            }

        def done(data):
            for tree in (self.admin_worker_tree, self.admin_group_tree):
                for item in tree.get_children():
                    tree.delete(item)
            for worker in data.get("workers", []):
                meta = worker.get("meta") or {}
                groups = ",".join(meta.get("sync_group_ids") or [])
                self.admin_worker_tree.insert("", tk.END, iid=str(worker.get("node_id")), values=(worker.get("node_id"), worker.get("label"), worker.get("status"), groups))
            for group in data.get("groups", []):
                group_id = str(group.get("group_id"))
                self.admin_group_tree.insert("", tk.END, iid=group_id, values=(group.get("alias") or "-", group_id, group.get("account_count", 0), group.get("inactive_count", 0), group.get("node_ids") or "-", group.get("sync_node_ids") or "-"))
            logger.info("分组账号管理已刷新。")

        self.run_score_api_async(work, done, lambda exc: messagebox.showerror("错误", f"刷新分组账号失败: {exc}"))

    def on_admin_group_select(self, _event=None):
        selected = self.admin_group_tree.selection()
        if not selected:
            return
        group_id = selected[0]
        values = self.admin_group_tree.item(group_id, "values")
        if values:
            self.admin_alias_entry.delete(0, tk.END)
            self.admin_alias_entry.insert(0, "" if values[0] == "-" else values[0])
            self.admin_group_entry.delete(0, tk.END)
            self.admin_group_entry.insert(0, group_id)
        self.refresh_admin_accounts()

    def refresh_admin_accounts(self):
        group_id = self.admin_group_entry.get().strip()
        if not group_id:
            selected = self.admin_group_tree.selection()
            group_id = selected[0] if selected else ""
        if not group_id:
            return

        def work():
            return central_api_request("GET", "/accounts", query={"group_id": group_id, "limit": 500, "include_inactive": "1"})

        def done(data):
            for item in self.admin_account_tree.get_children():
                self.admin_account_tree.delete(item)
            for account in data.get("accounts", []):
                seen = "-"
                try:
                    seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(account.get("last_seen") or 0)))
                except Exception:
                    pass
                self.admin_account_tree.insert("", tk.END, iid=str(account.get("profile_id")), values=(account.get("profile_id"), account.get("profile_name"), account.get("group_id"), account.get("node_id"), account.get("status"), seen))

        self.run_score_api_async(work, done, lambda exc: logger.warning(f"刷新账号管理失败: {exc}"))

    def admin_bind_group(self):
        alias = self.admin_alias_entry.get().strip()
        group_id = self.admin_group_entry.get().strip()
        node_id = self.admin_node_entry.get().strip()
        if not alias or not group_id:
            messagebox.showwarning("提示", "请填写别名和分组ID。")
            return

        def work():
            central_api_request("POST", "/groups/alias", {"alias": alias, "group_id": group_id})
            if node_id:
                central_api_request("POST", "/worker-sync-groups", {"node_id": node_id, "group_id": group_id})
            return True

        self.run_score_api_async(work, lambda _: self.refresh_group_admin(), lambda exc: messagebox.showerror("错误", f"绑定失败: {exc}"))

    def admin_unbind_alias(self):
        alias = self.admin_alias_entry.get().strip()
        if not alias:
            messagebox.showwarning("提示", "请填写或选择别名。")
            return
        self.run_score_api_async(
            lambda: central_api_request("POST", "/groups/alias/delete", {"alias": alias}),
            lambda _: self.refresh_group_admin(),
            lambda exc: messagebox.showerror("错误", f"解绑失败: {exc}"),
        )

    def admin_add_sync_group(self):
        node_id = self.admin_node_entry.get().strip()
        group_id = self.admin_group_entry.get().strip()
        if not node_id or not group_id:
            messagebox.showwarning("提示", "请填写电脑和分组ID。")
            return
        self.run_score_api_async(
            lambda: central_api_request("POST", "/worker-sync-groups", {"node_id": node_id, "group_id": group_id}),
            lambda _: self.refresh_group_admin(),
            lambda exc: messagebox.showerror("错误", f"添加同步分组失败: {exc}"),
        )

    def admin_set_account_status(self, status):
        ids = list(self.admin_account_tree.selection())
        if not ids:
            messagebox.showwarning("提示", "请先选择账号。")
            return

        def work():
            for profile_id in ids:
                central_api_request("POST", f"/accounts/{profile_id}/status", {"status": status})
            return True

        self.run_score_api_async(work, lambda _: self.refresh_admin_accounts(), lambda exc: messagebox.showerror("错误", f"账号操作失败: {exc}"))

    def refresh_score_plan_tasks(self):
        plan_id = self.selected_score_plan_id()
        if not plan_id:
            return
        if self._score_task_loading_plan_id == plan_id:
            return
        self._score_task_loading_plan_id = plan_id
        for item in self.score_task_tree.get_children():
            self.score_task_tree.delete(item)
        self.score_task_tree.insert("", tk.END, iid=f"loading_{plan_id}", values=("", "加载中...", "", "", ""))

        def work():
            return central_api_request("GET", f"/score-plans/{plan_id}")

        def done(data):
            self._score_task_loading_plan_id = None
            if self.selected_score_plan_id() != plan_id:
                return
            for item in self.score_task_tree.get_children():
                self.score_task_tree.delete(item)
            for task in data.get("tasks", []):
                payload = task.get("payload") or {}
                metrics = payload.get("metrics") or {}
                metric_text = " ".join(f"{k}:{v}" for k, v in metrics.items())
                self.score_task_tree.insert(
                    "",
                    tk.END,
                    iid=str(task.get("id")),
                    values=(task.get("id"), time.strftime("%Y-%m-%d %H:%M", time.localtime(int(task.get("run_at")))), task.get("status"), payload.get("mode"), metric_text),
                )

        def failed(exc):
            self._score_task_loading_plan_id = None
            for item in self.score_task_tree.get_children():
                self.score_task_tree.delete(item)
            logger.warning(f"刷新计划任务失败: {exc}")

        self.run_score_api_async(work, done, failed)

    def schedule_selected_score_plan(self):
        plan_id = self.selected_score_plan_id()
        if not plan_id:
            messagebox.showwarning("提示", "请先选择一个账号计划。")
            return
        self.set_score_plan_busy(True, "正在生成所选计划调度...")

        def work():
            return central_api_request("POST", f"/score-plans/{plan_id}/schedule", {"max_days": 31})

        def done(data):
            self.set_score_plan_busy(False)
            messagebox.showinfo("成功", f"已生成调度任务 {data.get('scheduled_count', 0)} 个。")
            self.refresh_score_plan_tasks()

        def failed(exc):
            self.set_score_plan_busy(False)
            messagebox.showerror("错误", f"生成调度失败: {exc}")

        self.run_score_api_async(work, done, failed)

    def set_selected_score_plan_status(self, action):
        plan_ids = self.selected_score_plan_ids()
        if not plan_ids:
            messagebox.showwarning("提示", "请先选择一个或多个账号计划。")
            return
        if action == "delete":
            ok = messagebox.askyesno("确认删除", f"确认删除所选 {len(plan_ids)} 个计划吗？未执行任务会被取消。")
            if not ok:
                return
        self.set_score_plan_busy(True, f"正在处理 {len(plan_ids)} 个计划...")

        def work():
            for plan_id in plan_ids:
                central_api_request("POST", f"/score-plans/{plan_id}/{action}", {})
            return True

        def done(_):
            self.set_score_plan_busy(False)
            self.refresh_score_plans()
            self.refresh_score_plan_tasks()

        def failed(exc):
            self.set_score_plan_busy(False)
            messagebox.showerror("错误", f"计划操作失败: {exc}")

        self.run_score_api_async(work, done, failed)

    def set_selected_schedule_status(self, action):
        task_ids = self.selected_score_task_ids()
        if not task_ids:
            messagebox.showwarning("提示", "请先选择一个调度任务。")
            return
        self.set_score_plan_busy(True, f"正在处理 {len(task_ids)} 个任务...")

        def work():
            for task_id in task_ids:
                central_api_request("POST", f"/scheduled-tasks/{task_id}/{action}", {})
            return True

        def done(_):
            self.set_score_plan_busy(False)
            self.refresh_score_plan_tasks()

        def failed(exc):
            self.set_score_plan_busy(False)
            messagebox.showerror("错误", f"操作失败: {exc}")

        self.run_score_api_async(work, done, failed)


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    AppGUI(root, root.destroy)
    root.mainloop()
