# 簡報用圖的原始檔

這裡的每個 HTML 都是**簡報裡某一張圖的來源**。做成網頁而不是直接畫圖，是為了讓內容可以改 ——
叢集清單變了、驗證項目多一項、欄位增減，改 HTML 重新截圖就好，不用重畫。

---

## 對應關係

| 檔案 | 產生的圖 | 用在哪一頁 |
|---|---|---|
| [`diagram.html`](diagram.html) | `flow.png` | 送出申請之後發生什麼事（四段流程） |
| [`mapping.html`](mapping.html) | `mapping.png` | **表單欄位 ↔ 藍圖欄位對應**，含實際 API payload |
| [`yaml.html`](yaml.html) | `yaml.png` | 藍圖全文（兩欄排版，`attributes` 兩處反白） |
| [`resize-options.html`](resize-options.html) | `resize-options.png` | 規格能不能事後調整（左右對照） |
| [`verify.html`](verify.html) | `verify.png` | POC 驗證結果 12 項 ＋ 實測數字 |
| [`clusters.html`](clusters.html) | `clusters.png` | 客戶環境的 18 個叢集，問要開放哪些 |
| [`checklist.html`](checklist.html) | `checklist.png` | 上線前置作業檢核表 |

另外三張圖是**實機截圖**，不在這裡：申請表單、表單完成畫面、vRA 藍圖編輯畫面。

---

## 怎麼重新產圖

每個檔案都設計成**填滿 1584 × 905 的視窗**（比例 1.75，對應投影片放圖的區域）。
用任何瀏覽器開起來、視窗調成該尺寸截圖即可。

若要自動化，本專案是用 Chrome 的遠端偵錯介面：

```bash
node cdp-live.mjs --steps '[
  {"url":"file:///.../verify.html"},
  {"wait":2000},
  {"shot":"E:/8/deploy/deck/img/verify.png"}
]'
```

> **重點：內容要撐滿版面。** 這幾張圖第一版都留了大片空白，縮進投影片之後字就變得很小。
> 判斷方式很簡單 —— 截圖出來如果上下有明顯白邊，就把字級或間距調大再截一次。

---

## 怎麼重建簡報

[`../../../deck/build-deck.mjs`](../../../deck/build-deck.mjs) 會拿 Broadcom 官方範本，
把文字換掉、把上面這些圖放進去，輸出 `.pptx`。

```bash
node build-deck.mjs        # 產生簡報
node qa-deck.mjs out.pptx  # 結構檢查
```

### 幾個踩過的坑，改的時候會遇到

**複製投影片會共用圖片關聯。** 直接換圖會把來源投影片的圖一起蓋掉 ——
複製出來的投影片要用 `swapPictureUnique()`，它會另外寫一份圖檔並改寫關聯。

**rels 的屬性順序不固定。** `Id` 不一定排在 `Target` 前面，用正則硬抓會靜默失敗
（圖沒換到，但也不報錯）。現在改成逐一解析每個 `<Relationship>`，換不到就丟例外。

**範本表格每一格的格式不一樣。** 有交錯底色、部分淡色、部分粗體。
沿用各格原本的格式會讓換完字之後看起來很亂，所以標題列與內文列各取一個基準格式整列套用。

**版型是「左文字右圖」。** 把圖拿掉之後右半邊會空著，記得把文字框拉寬（`widenShape()`）。

**圖會超出投影片底部。** 只照寬度算高度很容易超過 7.5 吋，`placePicture()` 會在超出時
改以可用高度為準。

---

## 一定要用眼睛看過

結構檢查只能確認檔案沒壞，**看不出版面好不好**。上面那些問題全部是 render 成圖之後才發現的：

```bash
soffice --headless --convert-to pdf --outdir render out.pptx
pdftoppm -jpeg -r 70 render/out.pdf slide
```

`soffice.exe` 若是用 scoop 裝的，會在 `~/scoop/apps/libreoffice/current/LibreOffice/program/`，
不在 shims 裡，要用完整路徑。
