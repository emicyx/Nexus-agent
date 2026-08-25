#!/bin/bash
# 补抓缺失战队名单（Liquipedia 限流时等待几分钟后运行）；完成后重跑 python kb_seed/build_doc.py
for team in T1 9z_Team All_Gamers Spacestation_Gaming Team_Secret Virtus.pro Weibo_Gaming VARREL Geekay_Esports; do
  curl -s --compressed -H "User-Agent: NexusKBResearch/1.0 (personal study project)" \
    "https://liquipedia.net/overwatch/api.php?action=parse&page=${team}&format=json&prop=text" \
    -o "team_json/${team}.json" && echo "fetched ${team}"
  sleep 6
done
python kb_seed/build_doc.py
