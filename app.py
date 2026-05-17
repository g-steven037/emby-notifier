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
TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
EMBY_SERVER = os.environ.get('EMBY_SERVER', '').rstrip('/')
EMBY_API_KEY = os.environ.get('EMBY_API_KEY')

# 聚合队列锁
QUEUE = {}
QUEUE_LOCK = threading.Lock()

# ================= 💾 文件大小格式化与高精度探测引擎 =================
def format_size(bytes_size):
    if not bytes_size: return "未知"
    try:
        bytes_size = float(bytes_size)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_size < 1024:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024
    except: pass
    return "未知"

def get_strm_size(strm_path):
    if not strm_path or not os.path.exists(strm_path):
        return "未知"
    try:
        with open(strm_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        if not content: return "未知"
        
        # 形式 1: HTTP 智能探测 (兼容 CD2 / Alist 直链)
        if content.startswith(('http://', 'https://')):
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            # 策略 A：尝试轻量级 HEAD 请求
            try:
                res = requests.head(content, headers=headers, allow_redirects=True, timeout=4)
                size = res.headers.get('Content-Length')
                if size and int(size) > 1024: # 略过几百字节的提示性网页
                    return format_size(size)
            except: pass
            
            # 策略 B：若 HEAD 被封禁或失效，降级使用 Range 请求第 0 字节 (极为精准且省流量)
            headers['Range'] = 'bytes=0-0'
            res_get = requests.get(content, headers=headers, allow_redirects=True, timeout=4)
            cr = res_get.headers.get('Content-Range')
            if cr and '/' in cr:
                return format_size(cr.split('/')[-1])
            size = res_get.headers.get('Content-Length')
            if size: return format_size(size)

        # 形式 2: 本地绝对路径探测 (/CloudNAS/...)
        elif content.startswith('/'):
            if os.path.exists(content):
                return format_size(os.path.getsize(content))
            else:
                logger.warning(f"strm指向的本地路径在容器内未找到，请检查映射: {content}")
    except Exception as e:
        logger.error(f"解析 strm 文件真实大小失败 ({strm_path}): {e}")
    return "未知"
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
    
    # 动态抓取 strm 文件内部的真实大小
    strm_file_path = task['latest_path'] or api_path
    file_size_str = get_strm_size(strm_file_path) if (strm_file_path and strm_file_path.endswith('.strm')) else "未知"
    quality = extract_quality(strm_file_path)
    
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
        
        if sd:
            current_season_total = sd.get('episode_count', 0)
        else:
            for s_data in tmdb_info.get('seasons', []):
                if s_data.get('season_number') == current_s:
                    current_season_total = s_data.get('episode_count', 0)
        
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
{missing_line}🖥 质量：{quality} | 💾 大小：{file_size_str}
🍿 TMDB ID：{tmdb_id or '未知'}

📝 简介：{display_overview}

<a href="https://www.themoviedb.org/tv/{tmdb_id}">🔗 TMDB</a> | <a href="https://www.douban.com/search?cat=1002&q={imdb_id or series_name}">✳️ 豆瓣</a> | <a href="https://www.imdb.com/title/{imdb_id}/">🌟 IMDb</a>"""
    send_tg_message(msg, backdrop_url)

def process_movie(data):
    item = data.get('Item', {})
    tmdb_id = item.get('ProviderIds', {}).get('Tmdb')
    ti = get_tmdb_data(tmdb_id, 'movie')
    
    movie_path = item.get('Path', '')
    if movie_path and movie_path.endswith('.strm'):
        file_size_str = get_strm_size(movie_path)
    else:
        file_size_str = format_size(item.get('Size'))
        
    quality = extract_quality(movie_path)
    bp = f"https://image.tmdb.org/t/p/w1280{ti['backdrop_path']}" if ti and ti.get('backdrop_path') else None
    
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
    
    msg = f"🎬 电影入库：{item.get('Name')} ({item.get('ProductionYear')})\n---------------------\n📚 类别：{genre}\n📅 首映：{ti.get('release_date','未知') if ti else '未知'}\n👥 主演：{' / '.join([a['name'] for a in ti.get('credits',{}).get('cast',[])[:3]]) if ti else '未知'}\n🖥 质量：{quality} | 💾 大小：{file_size_str}\n🍿 TMDB ID：{tmdb_id}\n\n📝 简介：{display_overview}\n\n<a href='https://www.themoviedb.org/movie/{tmdb_id}'>🔗 TMDB</a> | <a href='https://www.douban.com/search?cat=1002&q={item.get('Name')}'>✳️ 豆瓣</a> | <a href='https://www.imdb.com/title/{item.get('ProviderIds',{}).get('Imdb')}/'>🌟 IMDb</a>"
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
    app.run(host='0.0.0.0', port=18089)
