# 簡報與報告的建置腳本

| 檔案 | 用途 |
|---|---|
| `build-deck.mjs` | 以 Broadcom 官方範本產生客戶簡報（換文字、換圖、刪不用的頁） |
| `qa-deck.mjs` | 簡報結構檢查：關聯是否斷、孤兒備忘稿、rId 對應、content-type |
| `poc-report.mjs` | 產生 POC 驗證報告 `.docx` |
| `xlsx.mjs` | 讀 RVTools 匯出檔（不需 Excel 或 Python，自行解析 xlsx 的 XML） |

圖的原始檔與注意事項見 [`../portal/demo/assets/README.md`](../portal/demo/assets/README.md)。

```bash
npm install jszip docx        # build-deck 需要 jszip，poc-report 需要 docx
node build-deck.mjs
node qa-deck.mjs VSMC-VM-Portal-EN.pptx
node xlsx.mjs RVTools_export.xlsx vCluster 100   # 不帶 sheet 名稱會列出所有分頁
```

> 範本 `base.pptx` 與截圖 `img/` 不在版控內（二進位檔）。
> 範本來自 `kostenyang/BroadcomPPT`，圖依上面那份 README 重新產生。
