import os
import re
import time
import json
import logging
import requests
import threading
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
    
    genre, premiere, total_eps, status, actors, network, backdrop_url = "未知", "未知", "--", "未知", "未知", "未知", None
    expected_eps = set()

    if tmdb_info:
        genre = tmdb_info.get('genres', [{}])[0].get('name', '未知')
        premiere = tmdb_info.get('first_air_date', '未知')
        status = "更新中" if tmdb_info.get('in_production') else "已完结"
        actors = " / ".join([a['name'] for a in tmdb_info.get('credits', {}).get('cast', [])[:3]])
        network = tmdb_info.get('networks', [])[0]['name'] if tmdb_info.get('networks') else "未知"
        total_eps = str(tmdb_info.get('number_of_episodes', '--'))
        if tmdb_info.get('backdrop_path'): backdrop_url = f"https://image.tmdb.org/t/p/w1280{tmdb_info['backdrop_path']}"

        last_ep = tmdb_info.get('last_episode_to_air')
        ls, le = (last_ep['season_number'], last_ep['episode_number']) if last_ep else (999, 999)
        for s_data in tmdb_info.get('seasons', []):
            s_num = s_data.get('season_number', 0)
            if s_num > 0:
                for e_num in range(1, s_data.get('episode_count', 0) + 1):
                    if status == "已完结" or (s_num < ls) or (s_num == ls and e_num <= le):
                        expected_eps.add((s_num, e_num))

    next_ep_str = "已完结"
    max_s = max([s for s, e in existing_eps]) if existing_eps else 1
    max_e = max([e for s, e in existing_eps if s == max_s]) if existing_eps else 0
    found_n = False
    nt = tmdb_info.get('next_episode_to_air') if tmdb_info else None
    
    if nt and (nt['season_number'] > max_s or (nt['season_number'] == max_s and nt['episode_number'] > max_e)):
        next_ep_str = f"S{nt['season_number']:02d}E{nt['episode_number']:02d} ({nt['air_date'][5:]})"
        found_n = True
    
    if not found_n and status == "更新中" and tmdb_id:
        try:
            tmdb_total_val = int(tmdb_info.get('number_of_episodes', 0))
            if len(existing_eps) < tmdb_total_val:
                sd = get_tmdb_season_detail(tmdb_id, max_s)
                for ep in sd.get('episodes', []) if sd else []:
                    if ep['episode_number'] > max_e and ep.get('air_date'):
                        next_ep_str = f"S{max_s:02d}E{ep['episode_number']:02d} ({ep['air_date'][5:]})"
                        found_n = True; break
                if not found_n: next_ep_str = f"S{max_s:02d}E{max_e+1:02d} (待定)"
            else: next_ep_str = "本季完结 (待更新)"
        except: pass

    missing_line = f"⚠️ 缺集：{format_s_e(expected_eps - existing_eps)}\n" if (expected_eps - existing_eps) else ""
    
    # 简介字数控制逻辑：限制为 80 字
    raw_overview = tmdb_info.get('overview', item.get('Overview', '暂无简介')) or '暂无简介'
    clean_overview = re.sub('<[^<]+?>', '', raw_overview).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if len(clean_overview) > 80:
        display_overview = clean_overview[:80] + "..."
    else:
        display_overview = clean_overview

    msg = f"""📺 剧集入库：{series_name} ({year})
---------------------
📥 新增：{format_s_e(task['episodes']) or '剧集刷新'}
📚 类别：{genre}
📅 首映：{premiere}
🔄 总集：共 {total_eps} 集 ({status})
🗓 下集：{next_ep_str}
👥 主演：{actors}
📡 平台：{network}
{missing_line}🖥 质量：{quality}
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
    
    # 电影简介同样限制为 80 字
    raw_overview = ti.get('overview', item.get('Overview', '暂无简介')) if ti else item.get('Overview', '暂无简介')
    clean_overview = re.sub('<[^<]+?>', '', raw_overview).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if len(clean_overview) > 80:
        display_overview = clean_overview[:80] + "..."
    else:
        display_overview = clean_overview

    msg = f"🎬 电影入库：{item.get('Name')} ({item.get('ProductionYear')})\n---------------------\n📚 类别：{ti.get('genres',[{}])[0].get('name','未知') if ti else '未知'}\n📅 首映：{ti.get('release_date','未知') if ti else '未知'}\n👥 主演：{' / '.join([a['name'] for a in ti.get('credits',{}).get('cast',[])[:3]]) if ti else '未知'}\n🖥 质量：{quality}\n🍿 TMDB ID：{tmdb_id}\n\n📝 简介：{display_overview}\n\n<a href='https://www.themoviedb.org/movie/{tmdb_id}'>🔗 TMDB</a> | <a href='https://www.douban.com/search?cat=1002&q={item.get('Name')}'>✳️ 豆瓣</a> | <a href='https://www.imdb.com/title/{item.get('ProviderIds',{}).get('Imdb')}/'>🌟 IMDb</a>"
    send_tg_message(msg, bp)

@app.route('/webhook', methods=['POST'])
def emby_webhook():
    d = request.json if request.is_json else json.loads(request.form.get('data', '{}'))
    event, item = d.get('Event', ''), d.get('Item', {})
    if event not in ['library.new', 'Item.Added', 'ItemAdded']: return jsonify({"status": "ignored"}), 200
    if item.get('Type') == 'Movie':
        threading.Thread(target=process_movie, args=(d,)).start()
    elif item.get('Type') in ['Episode', 'Series', 'Season']:
        sid = item.get('SeriesId') if item.get('Type') == 'Episode' else item.get('Id')
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
