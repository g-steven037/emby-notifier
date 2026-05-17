import os
import re
import time
import json
import logging
import requests
import threading
import datetime
from flask import Flask, request, jsonify

# 强制启用实时日志输出
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置环境变量
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
TG_MONITOR_CHANNEL_ID = os.environ.get('TG_MONITOR_CHANNEL_ID') 
TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
EMBY_SERVER = os.environ.get('EMBY_SERVER', '').rstrip('/')
EMBY_API_KEY = os.environ.get('EMBY_API_KEY')

# 队列锁与全局大小缓存
QUEUE = {}
QUEUE_LOCK = threading.Lock()

TELEGRAM_SIZE_CACHE = {}
SIZE_CACHE_LOCK = threading.Lock()

# ================= 🛰️ 后台 Telegram 频道精准拦截与大小全抄引擎 =================
def parse_and_store_size(text):
    if not text: return
    try:
        tmdb_match = re.search(r'TMDB ID[：:]\s*(\d+)', text)
        size_match = re.search(r'(🧊\s*)?大小[：:]\s*(.*)', text)
        if not tmdb_match or not size_match: return
        
        tmdb_id = tmdb_match.group(1)
        size_str = size_match.group(2).strip() 
        is_movie = "电影" in text or "🎬" in text
        
        with SIZE_CACHE_LOCK:
            if is_movie:
                TELEGRAM_SIZE_CACHE[f"movie_{tmdb_id}"] = size_str
                logger.info(f"[📥 频道抓取] 成功缓存电影大小: TMDB={tmdb_id} -> {size_str}")
            else:
                season_match = re.search(r'S(\d+)', text, re.I)
                if season_match:
                    s = int(season_match.group(1))
                    range_match = re.search(r'E(\d+)\s*-\s*E(\d+)', text, re.I)
                    if range_match:
                        start, end = int(range_match.group(1)), int(range_match.group(2))
                        ep_list = list(range(start, end + 1))
                    else:
                        ep_list = [int(x) for x in re.findall(r'E(\d+)', text, re.I)]
                    
                    if ep_list:
                        for e in ep_list:
                            TELEGRAM_SIZE_CACHE[f"tv_{tmdb_id}_{s}_{e}"] = size_str
                        logger.info(f"[📥 频道抓取] 成功缓存剧集大小: TMDB={tmdb_id}, S{s:02d}E{ep_list} -> {size_str}")
    except Exception as e:
        logger.error(f"解析监控频道消息失败: {e}")

def telegram_polling_thread():
    if not TG_BOT_TOKEN:
        logger.error("未配置 TG_BOT_TOKEN，大小捕获监听启动失败！")
        return
    logger.info(f"📡 [监听器激活] 正在启动辅助频道消息捕获引擎... 目标过滤ID: {TG_MONITOR_CHANNEL_ID or '未指定(接收全部)'}")
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            res = requests.get(url, timeout=35).json()
            if res.get("ok"):
                for update in res.get("result", []):
                    offset = update["update_id"] + 1
                    channel_post = update.get("channel_post")
                    if channel_post:
                        channel_id = str(channel_post.get("chat", {}).get("id", ""))
                        if TG_MONITOR_CHANNEL_ID and channel_id != str(TG_MONITOR_CHANNEL_ID):
                            continue
                            
                        text = channel_post.get("text") or channel_post.get("caption") or ""
                        parse_and_store_size(text)
        except Exception as e:
            logger.warning(f"辅助监听轮询发生波动 (5秒后重试): {e}")
            time.sleep(5)
# ======================================================================

def send_tg_message(text, image_url=None):
    logger.info("========== 准备发送至 Telegram ==========\n" + text + "\n=========================================")
    if image_url:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
        payload = {"chat_id": TG_CHAT_ID, "photo": image_url, "caption": text, "parse_mode": "HTML"}
        try:
            if requests.post(url, json=payload, timeout=15).status_code == 200:
                return
        except: pass

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    requests.post(url, json=payload, timeout=10)

def get_tmdb_data(tmdb_id, item_type='tv'):
    if not TMDB_API_KEY or not tmdb_id: return None
    url = f"https://api.themoviedb.org/3/{item_type}/{tmdb_id}?api_key={TMDB_API_KEY}&language=zh-CN&append_to_response=credits"
    try: return requests.get(url, timeout=10).json()
    except: return None

def get_tmdb_season_detail(series_id, season_number):
    url = f"https://api.themoviedb.org/3/tv/{series_id}/season/{season_number}?api_key={TMDB_API_KEY}&language=zh-CN"
    try: return requests.get(url, timeout=10).json()
    except: return None

def format_s_e(ep_tuples):
    if not ep_tuples: return ""
    seasons = {}
    for s, e in ep_tuples:
        if s is None or e is None: continue
        seasons.setdefault(s, []).append(e)
    res = []
    for s in sorted(seasons.keys()):
        eps = sorted(list(set(seasons[s])))
        ranges, start, prev = [], eps[0], eps[0]
        for n in eps[1:]:
            if n == prev + 1: prev = n
            else:
                ranges.append(f"E{start:02d}-E{prev:02d}" if start != prev else f"E{start:02d}")
                start = prev = n
        ranges.append(f"E{start:02d}-E{prev:02d}" if start != prev else f"E{start:02d}")
        res.append(", ".join([f"S{s:02d}{r}" for r in ranges]))
    return " | ".join(res)

def get_existing_episodes(series_id):
    if not EMBY_SERVER or not EMBY_API_KEY: return set(), ""
    url = f"{EMBY_SERVER}/emby/Items?ParentId={series_id}&IncludeItemTypes=Episode&Recursive=true&Fields=IndexNumber,ParentIndexNumber,Path&api_key={EMBY_API_KEY}"
    existing, latest_path = set(), ""
    try:
        res = requests.get(url, timeout=10).json()
        for i in res.get('Items', []):
            s, e = i.get('ParentIndexNumber'), i.get('IndexNumber')
            if s and e and s > 0: existing.add((s, e))
            if i.get('Path') and not latest_path: latest_path = i.get('Path')
        return existing, latest_path
    except: return set(), ""

def extract_quality(filename):
    if not filename: return "未知"
    patterns = {"DV": r"(DV|Dolby\.Vision)", "HDR": r"(HDR|HDR10)", "Res": r"(2160p|1080p|720p)"}
    res_list = []
    if re.search(patterns["DV"], filename, re.I): res_list.append("DV")
    elif re.search(patterns["HDR"], filename, re.I): res_list.append("HDR")
    r = re.search(patterns["Res"], filename, re.I)
    if r: res_list.append(r.group())
    return " ".join(res_list) if res_list else "未知"

def process_series(series_id):
    with QUEUE_LOCK:
        if series_id not in QUEUE: return
        task = QUEUE.pop(series_id)
    
    item = task['data'].get('Item', {})
    series_name, year = item.get('SeriesName') or item.get('Name'), item.get('ProductionYear', '')
    imdb_id = item.get('ProviderIds', {}).get('Imdb', '')
    tmdb_id = item.get('SeriesProviderIds', {}).get('Tmdb') or item.get('ProviderIds', {}).get('Tmdb')

    if not tmdb_id and EMBY_SERVER:
        time.sleep(3)
        try:
            r = requests.get(f"{EMBY_SERVER}/emby/Items?Ids={series_id}&Fields=ProviderIds&api_key={EMBY_API_KEY}").json()
            tmdb_id = r['Items'][0].get('ProviderIds', {}).get('Tmdb') if r.get('Items') else None
        except: pass

    tmdb_info = get_tmdb_data(tmdb_id, 'tv')
    if not tmdb_info and TMDB_API_KEY:
        clean_n = re.sub(r'\s*\(.*?\)\s*', '', series_name).strip()
        try:
            sr = requests.get(f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={clean_n}&first_air_date_year={year}&language=zh-CN").json()
            if sr.get('results'): 
                tmdb_id = sr['results'][0]['id']
                tmdb_info = get_tmdb_data(tmdb_id, 'tv')
        except: pass

    existing_eps, api_path = get_existing_episodes(series_id)
    quality = extract_quality(task['latest_path'] or api_path)
    
    sizes_found = []
    with SIZE_CACHE_LOCK:
        for s_num, e_num in task['episodes']:
            sz = TELEGRAM_SIZE_CACHE.get(f"tv_{tmdb_id}_{s_num}_{e_num}")
            if sz and sz not in sizes_found: sizes_found.append(sz)
    file_size_str = ", ".join(sizes_found) if sizes_found else "未知"

    genre, premiere, total_eps, status_raw, actors, network, backdrop_url = "未知", "未知", "--", "未知", "未知", "未知", None
    expected_eps = set()
    
    added_seasons = sorted(list(set([s for s, e in task['episodes']])))
    current_s = added_seasons[0] if added_seasons else 1
    tmdb_total_val = 0
    current_season_total = 0

    sd = get_tmdb_season_detail(tmdb_id, current_s) if tmdb_id else None

    if tmdb_info:
        countries = tmdb_info.get('origin_country', [])
        country = countries[0] if countries else ""
        genres_list = [g.get('name') for g in tmdb_info.get('genres', [])]
        
        if "动画" in genres_list:
            if country in ["CN", "TW", "HK"]: genre = "国漫"
            elif country == "JP": genre = "日漫"
            elif country in ["US", "GB", "CA", "AU", "FR", "DE"]: genre = "欧美动漫"
            else: genre = "动漫"
        elif any(x in genres_list for x in ["真人秀", "谈话", "新闻"]):
            if country in ["CN", "TW", "HK"]: genre = "国产综艺"
            elif country == "KR": genre = "韩国综艺"
            elif country == "JP": genre = "日本综艺"
            elif country in ["US", "GB", "CA", "AU", "FR", "DE", "ES", "IT"]: genre = "欧美综艺"
            else: genre = "综艺"
        elif "纪录" in genres_list:
            if country in ["CN", "TW", "HK"]: genre = "国产纪录片"
            elif country in ["US", "GB", "CA", "AU", "FR", "DE", "ES", "IT"]: genre = "欧美纪录片"
            else: genre = "纪录片"
        else:
            if country in ["CN", "TW", "HK"]: genre = "国产剧"
            elif country == "KR": genre = "韩剧"
            elif country == "JP": genre = "日剧"
            elif country in ["US", "GB", "CA", "AU", "FR", "DE", "ES", "IT"]: genre = "欧美剧"
            else:
                genre = tmdb_info.get('genres', [{}])[0].get('name', '剧情') if tmdb_info.get('genres') else '剧情'

        premiere = tmdb_info.get('first_air_date', '未知')
        status_raw = "更新中" if tmdb_info.get('in_production') else "已完结"
        actors = " / ".join([a['name'] for a in tmdb_info.get('credits', {}).get('cast', [])[:3]])
        network = tmdb_info.get('networks', [])[0]['name'] if tmdb_info.get('networks') else "未知"
        tmdb_total_val = int(tmdb_info.get('number_of_episodes', 0))
        
        # ================= 🌟 理论回归：100% 锁定主信息 seasons 提取单季总集数 =================
        for s_data in tmdb_info.get('seasons', []):
            if s_data.get('season_number') == current_s:
                current_season_total = s_data.get('episode_count', 0)
                break
        # ==================================================================================
        
        total_eps = str(current_season_total) if current_season_total > 0 else "--"
        if tmdb_info.get('backdrop_path'): backdrop_url = f"https://image.tmdb.org/t/p/w1280{tmdb_info['backdrop_path']}"

        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        if status_raw == "已完结":
            if sd and sd.get('episodes'):
                for ep in sd['episodes']:
                    if ep.get('episode_number'): expected_eps.add((current_s, ep['episode_number']))
            elif current_season_total > 0:
                for e_num in range(1, current_season_total + 1): expected_eps.add((current_s, e_num))
        else:
            if sd and sd.get('episodes'):
                for ep in sd['episodes']:
                    e_num = ep.get('episode_number')
                    e_date = ep.get('air_date')
                    if e_num and e_date and e_date <= today_str:
                        expected_eps.add((current_s, e_num))

    is_fully_collected = (tmdb_total_val > 0 and len(existing_eps) >= tmdb_total_val)
    status_display = "已完结" if (status_raw == "已完结" or is_fully_collected) else "更新中"
    status_icon = "✅" if status_display == "已完结" else "🔄"
    
    next_ep_line = ""
    if status_display == "更新中":
        max_s = max([s for s, e in existing_eps]) if existing_eps else current_s
        max_e = max([e for s, e in existing_eps if s == max_s]) if existing_eps else 0
        next_ep_str = "待定"
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        
        if sd and sd.get('episodes'):
            future_eps = [ep for ep in sd['episodes'] if ep.get('air_date') and ep['air_date'] > today_str]
            if future_eps:
                next_date = min(ep['air_date'] for ep in future_eps)
                next_ep_nums = sorted([ep['episode_number'] for ep in future_eps if ep['air_date'] == next_date])
                if next_ep_nums:
                    if len(next_ep_nums) > 1:
                        ep_range_str = f"E{next_ep_nums[0]:02d}-E{next_ep_nums[-1]:02d}"
                    else:
                        ep_range_str = f"E{next_ep_nums[0]:02d}"
                    next_ep_str = f"S{current_s:02d}{ep_range_str} ({next_date[5:]})"
            else:
                current_season_collected = len([e for s, e in existing_eps if s == current_s])
                if current_season_total > 0 and current_season_collected >= current_season_total:
                    next_ep_str = "本季完结 (待更新)"
                else:
                    next_ep_str = f"S{current_s:02d}E{max_e+1:02d} (待定)"
        else:
            next_ep_str = f"S{max_s:02d}E{max_e+1:02d} (待定)"
            
        next_ep_line = f"🗓 下集：{next_ep_str}\n"

    # 季范围锁定
    missing_eps = expected_eps - existing_eps
    target_seasons = set(added_seasons)
    if not target_seasons:
        if item.get('Type') == 'Season' and item.get('IndexNumber'): target_seasons.add(item.get('IndexNumber'))
        elif item.get('Type') == 'Episode' and item.get('ParentIndexNumber'): target_seasons.add(item.get('ParentIndexNumber'))
            
    if target_seasons: missing_eps = { (s, e) for s, e in missing_eps if s in target_seasons }
    else: missing_eps = set()

    missing_line = f"⚠️ 缺集：{format_s_e(missing_eps)}\n" if missing_eps else ""
    
    raw_overview = tmdb_info.get('overview', item.get('Overview', '暂无简介')) or '暂无简介'
    clean_overview = re.sub('<[^<]+?>', '', raw_overview).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    display_overview = (clean_overview[:80] + "...") if len(clean_overview) > 80 else clean_overview

    msg = f"""📺 剧集入库：{series_name} ({year})
---------------------
📥 新增：{format_s_e(task['episodes']) or '剧集刷新'}
📚 类别：{genre}
📅 首映：{premiere}
{status_icon} 总集：共 {total_eps} 集 ({status_display})
{next_ep_line}👥 主演：{actors}
📡 平台：{network}
{missing_line}🖥 质量：{quality}
🧊 大小：{file_size_str}
🍿 TMDB ID：{tmdb_id or '未知'}

📝 简介：{display_overview}

<a href="https://www.themoviedb.org/tv/{tmdb_id}">🔗 TMDB</a> | <a href="https://www.douban.com/search?cat=1002&q={imdb_id or series_name}">✳️ 豆瓣</a> | <a href="https://www.imdb.com/title/{imdb_id}/">🌟 IMDb</a>"""
    send_tg_message(msg, backdrop_url)

def process_movie(data):
    item = data.get('Item', {})
    tmdb_id = item.get('ProviderIds', {}).get('Tmdb')
    ti = get_tmdb_data(tmdb_id, 'movie')
    quality = extract_quality(item.get('Path', ''))
    bp = f"https://image.tmdb.org/t/p/w1280{ti['backdrop_path']}" if ti and ti.get('backdrop_path') else None
    
    with SIZE_CACHE_LOCK:
        file_size_str = TELEGRAM_SIZE_CACHE.get(f"movie_{tmdb_id}", "未知")

    genre = "电影"
    if ti:
        countries = ti.get('production_countries', [])
        country = countries[0].get('iso_3166_1', '') if countries else ""
        genres_list = [g.get('name') for g in ti.get('genres', [])]
        
        if "动画" in genres_list:
            if country in ["CN", "TW", "HK"]: genre = "国漫"
            elif country == "JP": genre = "日漫"
            elif country in ["US", "GB", "CA", "AU", "FR", "DE"]: genre = "欧美动漫"
            else: genre = "动漫"
        elif "纪录" in genres_list:
            if country in ["CN", "TW", "HK"]: genre = "华语纪录片"
            elif country in ["US", "GB", "CA", "AU", "FR", "DE", "ES", "IT"]: genre = "欧美纪录片"
            else: genre = "纪录片"
        else:
            if country in ["CN", "TW", "HK"]: genre = "华语电影"
            elif country == "KR": genre = "韩国电影"
            elif country == "JP": genre = "日本电影"
            elif country in ["US", "GB", "CA", "AU", "FR", "DE", "ES", "IT"]: genre = "欧美电影"
            else:
                genre = ti.get('genres', [{}])[0].get('name', '电影') if ti.get('genres') else '电影'

    raw_overview = ti.get('overview', item.get('Overview', '暂无简介')) if ti else item.get('Overview', '暂无简介')
    clean_overview = re.sub('<[^<]+?>', '', raw_overview).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    display_overview = (clean_overview[:80] + "...") if len(clean_overview) > 80 else clean_overview
    
    msg = f"🎬 电影入库：{item.get('Name')} ({item.get('ProductionYear')})\n---------------------\n📚 类别：{genre}\n📅 首映：{ti.get('release_date','未知') if ti else '未知'}\n👥 主演：{' / '.join([a['name'] for a in ti.get('credits',{}).get('cast',[])[:3]]) if ti else '未知'}\n🖥 质量：{quality}\n🧊 大小：{file_size_str}\n🍿 TMDB ID：{tmdb_id}\n\n📝 简介：{display_overview}\n\n<a href='https://www.themoviedb.org/movie/{tmdb_id}'>🔗 TMDB</a> | <a href='https://www.douban.com/search?cat=1002&q={item.get('Name')}'>✳️ 豆瓣</a> | <a href='https://www.imdb.com/title/{item.get('ProviderIds',{}).get('Imdb')}/'>🌟 IMDb</a>"
    send_tg_message(msg, bp)

@app.route('/webhook', methods=['POST'])
def emby_webhook():
    d = request.json if request.is_json else json.loads(request.form.get('data', '{}'))
    
    try:
        pretty_json = json.dumps(d, indent=4, ensure_ascii=False)
        logger.info(f"\n==================== 收到 EMBY WEBHOOK 完整原始数据 ====================\n{pretty_json}\n=========================================================================")
    except Exception as e:
        logger.error(f"解析并打印原始 Webhook 失败: {e}")

    event, item = d.get('Event', ''), d.get('Item', {})
    if event not in ['library.new', 'Item.Added', 'ItemAdded']: return jsonify({"status": "ignored"}), 200
    if item.get('Type') == 'Movie':
        threading.Thread(target=process_movie, args=(d,)).start()
    elif item.get('Type') in ['Episode', 'Series', 'Season']:
        sid = item.get('SeriesId') if item.get('Type') in ['Episode', 'Season'] else item.get('Id')
        with QUEUE_LOCK:
            if sid not in QUEUE:
                QUEUE[sid] = {'episodes': set(), 'data': d, 'latest_path': item.get('Path', ''),
                              'timer': threading.Timer(10.0, process_series, args=[sid])}
                QUEUE[sid]['timer'].start()
            if item.get('ParentIndexNumber') and item.get('IndexNumber'):
                QUEUE[sid]['episodes'].add((item['ParentIndexNumber'], item['IndexNumber']))
            if item.get('Path') and item.get('Type') == 'Episode': QUEUE[sid]['latest_path'] = item['Path']
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    threading.Thread(target=telegram_polling_thread, daemon=True).start()
    app.run(host='0.0.0.0', port=18089)
