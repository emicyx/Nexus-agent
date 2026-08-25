import json, re, os, glob, datetime

TEAMS = [
    ("Team_Liquid",      "Team Liquid",      "NA · 合作战队"),
    ("Dallas_Fuel",      "Dallas Fuel",      "NA · 合作战队（2026 新加入）"),
    ("Disguised",        "Disguised",        "NA · 合作战队（2026 新加入）"),
    ("Spacestation_Gaming","Spacestation Gaming","NA · 合作战队"),
    ("Twisted_Minds",    "Twisted Minds",    "EMEA · 合作战队（2025 世界冠军）"),
    ("Virtus.pro",       "Virtus.pro",       "EMEA · 合作战队"),
    ("Team_Peps",        "Team Peps",        "EMEA · 合作战队（2026 新加入）"),
    ("Team_Falcons",     "Team Falcons",     "亚洲（韩国）· 合作战队"),
    ("Crazy_Raccoon",    "Crazy Raccoon",    "亚洲（日本）· 合作战队"),
    ("T1",               "T1",               "亚洲（韩国）· 合作战队"),
    ("ZETA_DIVISION",    "ZETA DIVISION",    "亚洲（日本）· 合作战队"),
    ("Weibo_Gaming",     "Weibo Gaming",     "中国 · 合作战队（2026 新设）"),
    ("JD_Gaming",        "JD Gaming",        "中国 · 合作战队（2026 新设）"),
    ("All_Gamers",       "All Gamers",       "中国 · 合作战队（2026 新设）"),
    ("VARREL",           "VARREL",           "亚洲（日本赛区代表）"),
    ("9z_Team",          "9z Team",          "南美 · 区域资格晋级 EWC"),
    ("Team_Secret",      "Team Secret",      "区域资格晋级 EWC"),
    ("Geekay_Esports",   "Geekay Esports",   "区域资格晋级 EWC"),
]
SKIP = ('Overwatch','Main_Page','Portal','Special','List_of','Countries','index.php','Champions_Series','Esports','FACEIT','World_Cup')

def extract(page, display):
    path = f"team_json/{page}.json"
    if not os.path.exists(path) or os.path.getsize(path) < 20000:
        return None
    try:
        html = json.load(open(path, encoding='utf-8'))['parse']['text']['*']
    except Exception:
        return None
    i = html.find('id="Active"')
    if i == -1: i = html.find('id="Player_Roster"')
    if i == -1: return None
    j = html.find('id="Former"', i)
    seg = html[i:j if j != -1 else i+16000]
    ids = []
    for href, title in re.findall(r'<a href="/overwatch/([^":/]+)"[^>]*title="([^"]+)"', seg):
        if href.startswith(SKIP) or href.lower() in (display.lower(), 'overview') or href in ids:
            continue
        if re.match(r'^[A-Za-z0-9_.\-]+$', href):
            ids.append(href)
    return ids

raw, missing = {}, []
for page, display, region in TEAMS:
    ids = extract(page, display)
    if ids: raw[display] = {"region": region, "players": ids[:10]}
    else:   missing.append(display); raw[display] = {"region": region, "players": None}

json.dump(raw, open("kb_seed/rosters_raw.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)

today = "2026-08-25"
lines = [f"# OWCS 2026 职业战队与选手知识库",
f"> 抓取日期：{today}｜来源：Liquipedia（MediaWiki API，遵守 api-terms-of-use）+ 官方公告｜选手名单随转会变动，以 Liquipedia 实时页面为准",
"", "## 一、2026 赛季合作战队体系（14 支）", "",
"2026 年 OWCS 合作战队从 9 支扩至 11+ 支，并首次设立中国赛区（由暴雪与网易联合运营）。",
"合作战队可直接晋级 Stage 1，但在 Stage 2/3 需参加升降级赛保级。",
"", "| 赛区 | 战队 | 备注 |", "|---|---|---|",
"| 北美 NA（4） | Team Liquid | 续约 |", "| 北美 NA（4） | Dallas Fuel | 2026 新加入 |", "| 北美 NA（4） | Disguised | 2026 新加入 |", "| 北美 NA（4） | Spacestation Gaming | 续约 |",
"| 欧洲/中东 EMEA（3） | Twisted Minds | 续约，2025 世界总决赛冠军 |", "| 欧洲/中东 EMEA（3） | Virtus.pro | 续约 |", "| 欧洲/中东 EMEA（3） | Team Peps | 2026 新加入 |",
"| 亚洲（4） | Team Falcons | 韩国赛区，沙特背景 |", "| 亚洲（4） | Crazy Raccoon | 日本 |", "| 亚洲（4） | T1 | 韩国 |", "| 亚洲（4） | ZETA DIVISION | 日本 |",
"| 中国（3） | Weibo Gaming 微博电竞 | 2026 新设 |", "| 中国（3） | JD Gaming 京东电竞 | 2026 新设 |", "| 中国（3） | All Gamers | 2026 新设 |",
"", "## 二、2026 季中冠军赛（Midseason Championship，即 Esports World Cup 2026 预选关联赛事）", "",
"2026-07-29 ~ 08-02 于沙特举行，16 支参赛队伍：Crazy Raccoon、Virtus.pro、Dallas Fuel、ZETA DIVISION、Geekay Esports、Weibo Gaming、Spacestation Gaming、T1、JD Gaming、Twisted Minds、All Gamers、Team Liquid、Team Falcons、VARREL、9z Team、Team Secret。",
"", "## 三、战队现役选手名单（Active Roster）", ""]
for page, display, region in TEAMS:
    d = raw[display]
    if d["players"]:
        lines.append(f"### {display}（{d['region'].split(' · ')[0]}）")
        lines.append(f"现役选手：{'、'.join(d['players'])}")
        lines.append("")
    else:
        lines.append(f"### {display}（{d['region'].split(' · ')[0]}）")
        lines.append("现役选手：⏳ 待补抓（数据源限流），重跑 kb_seed/fetch_missing_teams.sh 后更新")
        lines.append("")
lines += ["## 四、赛制要点", "",
"- 战队名单上限 8 名选手，参赛最低年龄 17 岁。",
"- NA/EMEA：FACEIT 公开预选赛（瑞士轮+双败）→ 6 队单循环常规赛 → 前 4 进双败季后赛；后 2 名参加升降级赛（对 FACEIT 大师组前 2），可被降级。",
"- 亚洲：韩国 9 队 / 日本 8 队 / 太平洋 6 队各自循环+单败决赛，晋级亚洲 Stage 冠军赛。",
"- 中国：8 队瑞士轮 → 6 队循环 → 前 4 双败季后赛。",
"- 2026 国际线下赛事：Champions Clash（5/22-24）、Midseason Championship（7/29-8/2）、World Finals（12/2-6，众筹奖池）。",
"- Stage 3 季后赛：2026-10-30 ~ 11-01。",
"", "## 五、2025 世界总决赛冠军", "",
"Twisted Minds 在 2025 世界总决赛 4:1 击败 Al Qadsiah 夺冠（其 2026 阵容以上方现役名单为准）。", "",
"## 数据来源", "- https://esports.overwatch.com/news/owcs-2026-season-competitive-details",
"- https://esports.overwatch.com/news/owcs-2026-preseason-bootcamp-viewers-guide",
"- https://esports.overwatch.com/news/overwatch-esports-2026-announcement",
"- https://liquipedia.net/overwatch/Overwatch_Champions_Series/2026 及各战队页面（API 抓取）",
"- https://esportsinsider.com/2026/02/owcs-china-2026-partner-teams"]
open("kb_seed/owcs-2026-players-teams.md","w",encoding="utf-8").write("\n".join(lines))
content = "\n".join(lines)
json.dump({"name":"OWCS 2026 职业选手与战队","source_type":"web","content":content}, open("kb_seed/payload.json","w",encoding="utf-8"), ensure_ascii=False)
ok = [k for k,v in raw.items() if v["players"]]
print("OK teams:", len(ok), ok)
print("MISSING:", missing)
print("doc chars:", len(content))
