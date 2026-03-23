# 📊 Polymarket Tracker

> 追蹤 Polymarket 上伊朗戰爭、利率、經濟通膨、金融市場等預測市場的即時機率儀表板。

**🔗 Live Dashboard:** `https://<your-username>.github.io/<repo-name>/`

---

## 功能

- **⚔️ 伊朗 / 戰爭** — 伊朗停火、政權倒台、美軍入伊等事件
- **🏦 利率 / 聯準會** — Fed 降息次數、FOMC 決策等
- **📊 經濟 / 通膨** — 衰退機率、CPI、關稅、失業率等
- **📈 金融市場** — 原油、黃金、S&P 500 等價格目標事件
- **🚨 近期重大波動** — 24小時內機率變動 ≥ 4% 的市場警告
- **📈 歷史走勢圖** — 每個事件點擊可展開完整歷史圖表

## 更新頻率

- **GitHub Actions** 每 30 分鐘自動抓取最新資料並推送至 `docs/data.json`
- **前端頁面** 每 5 分鐘從 `data.json` 重新讀取顯示

## 架構

```
polymarket-tracker/
├── fetch_markets.py          # 資料抓取腳本
├── docs/
│   ├── index.html            # GitHub Pages 前端
│   └── data.json             # 最新市場資料（自動更新）
└── .github/workflows/
    └── update.yml            # 每30分鐘觸發的 Actions workflow
```

## 本機執行

```bash
pip install requests
python fetch_markets.py
# 開啟 docs/index.html 即可預覽
```

## 設定 GitHub Pages

1. 在 GitHub 建立新 repo（或 fork 此 repo）
2. 推送所有檔案
3. 進入 **Settings → Pages**
4. Source 選 `Deploy from a branch`，Branch 選 `main`，Folder 選 `/docs`
5. 確認 **Actions → Update Polymarket Data** workflow 已啟用
6. 前往 `https://<username>.github.io/<repo>/` 查看

---

*資料來源：[Polymarket](https://polymarket.com) Gamma API*
