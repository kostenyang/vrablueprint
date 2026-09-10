import fs from 'node:fs/promises';
import {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle, PageBreak,
  LevelFormat, convertInchesToTwip,
} from 'docx';

// US Letter 版面；表格寬度統一，欄寬總和必須等於表寬
const PAGE_W = 12240, MARGIN = 1080;
const BODY_W = PAGE_W - MARGIN * 2;   // 10080 dxa

const INK = '1C2530', MUTED = '6B7684', ACCENT = '005C8A', WARN = '8A6100', OK = '1A7F5A';

const P = (text, opts = {}) => new Paragraph({
  spacing: { after: opts.after ?? 120, line: 276 },
  alignment: opts.align,
  children: [new TextRun({ text, size: opts.size ?? 21, color: opts.color ?? INK,
                           bold: opts.bold, italics: opts.italics,
                           font: opts.mono ? 'Consolas' : undefined })],
});

const Mono = (text, opts = {}) => new Paragraph({
  spacing: { after: opts.after ?? 100, line: 260 },
  indent: { left: convertInchesToTwip(0.25) },
  children: [new TextRun({ text, size: 18, font: 'Consolas', color: opts.color ?? ACCENT })],
});

const Bullet = (text, level = 0) => new Paragraph({
  numbering: { reference: 'bullets', level },
  spacing: { after: 80, line: 276 },
  children: [new TextRun({ text, size: 21, color: INK })],
});

const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 },
  children: [new TextRun({ text: t, size: 28, bold: true, color: ACCENT })] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 },
  children: [new TextRun({ text: t, size: 23, bold: true, color: INK })] });

/** 提醒框：靠段落底線做出區塊感，不要用表格當分隔線 */
const Note = (text) => new Paragraph({
  spacing: { before: 120, after: 160, line: 276 },
  border: { left: { style: BorderStyle.SINGLE, size: 18, color: 'F3BA16', space: 10 } },
  indent: { left: convertInchesToTwip(0.12) },
  children: [new TextRun({ text, size: 20, color: WARN })],
});

function table(headers, rows, widths) {
  const cell = (text, { head = false, w, color, bold } = {}) => new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: head ? { type: ShadingType.CLEAR, fill: 'EEF1F4' } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [new Paragraph({
      spacing: { after: 0, line: 260 },
      children: [new TextRun({ text: String(text), size: 19, bold: head || bold,
                               color: color ?? (head ? MUTED : INK) })],
    })],
  });
  return new Table({
    width: { size: BODY_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, cantSplit: true,
        children: headers.map((h, i) => cell(h, { head: true, w: widths[i] })) }),
      ...rows.map(r => new TableRow({
        cantSplit: true,
        children: r.map((c, i) => {
          const pass = String(c) === '通過';
          return cell(c, { w: widths[i], color: pass ? OK : undefined, bold: pass });
        }),
      })),
    ],
  });
}

const doc = new Document({
  numbering: {
    config: [{
      reference: 'bullets',
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 460, hanging: 240 } } } },
        { level: 1, format: LevelFormat.BULLET, text: '–', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 820, hanging: 240 } } } },
      ],
    }],
  },
  sections: [{
    properties: { page: { size: { width: PAGE_W, height: 15840 },
                          margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
    children: [
      // ---------------------------------------------------------------- 封面
      new Paragraph({ spacing: { before: 1400, after: 100 }, alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: '自助式 VM 申請入口', size: 44, bold: true, color: ACCENT })] }),
      new Paragraph({ spacing: { after: 700 }, alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: 'POC 驗證報告', size: 30, color: MUTED })] }),
      P('客戶：VSMC', { align: AlignmentType.CENTER, color: MUTED, after: 60 }),
      P('驗證環境：VMware Aria Automation 8.18.1', { align: AlignmentType.CENTER, color: MUTED, after: 60 }),
      P('日期：2026-09-10', { align: AlignmentType.CENTER, color: MUTED, after: 60 }),
      new Paragraph({ children: [new PageBreak()] }),

      // ---------------------------------------------------------------- 一
      H1('一、驗證目的與範圍'),
      P('本次 POC 目的在於確認一項架構決策是否成立：讓使用者透過網頁表單自助申請虛擬機時，'
        + '申請資訊（申請人、員工編號、申請單號、用途等）能否在不修改藍圖的前提下增減欄位，'
        + '並且最終落到 vCenter 的自訂屬性上，供日後查詢與稽核使用。'),
      P('驗證範圍涵蓋三層：'),
      Bullet('藍圖層 — 欄位設計、放置決策、部署是否成功'),
      Bullet('平台層 — 屬性是否完整傳遞、能否回寫 vCenter、能否反查'),
      Bullet('前端層 — 申請表單的欄位驗證與流程'),
      Note('本報告所列結果均為實機執行結果，非設計推論。凡未實測者，於「已知限制」一節明確標示。'),

      // ---------------------------------------------------------------- 二
      H1('二、驗證環境'),
      table(
        ['項目', '內容'],
        [
          ['自動化平台', 'VMware Aria Automation 8.18.1'],
          ['虛擬化平台', 'vCenter Server 8.0 U3'],
          ['網路', 'NSX VLAN segment（既有網段，非 overlay）'],
          ['作業系統範本', 'Ubuntu 20.04（Linux 熱加測試用）'],
          ['叢集名稱來源', '客戶 RVTools 匯出檔（2026-08-31），共 18 個叢集'],
          ['驗證方式', '全程以 REST API 執行，並以 vCenter 端獨立複核'],
        ],
        [2600, 7480],
      ),

      // ---------------------------------------------------------------- 三
      H1('三、驗證項目與結果總表'),
      table(
        ['#', '驗證項目', '結果'],
        [
          ['1', '藍圖以單一 attributes 物件承載所有自訂屬性', '通過'],
          ['2', '藍圖未宣告的欄位仍能完整傳遞至虛擬機', '通過'],
          ['3', '規格（size）對應 flavor mapping', '通過'],
          ['4', '叢集（cluster）以標記決定放置位置', '通過'],
          ['5', '網域區隔（zone）以標記決定網段', '通過'],
          ['6', '網域區隔無對應網段時應中止且回報原因', '通過'],
          ['7', '端到端部署並取得靜態 IP', '通過'],
          ['8', '申請資訊寫入 vCenter 自訂屬性', '通過'],
          ['9', '依自訂屬性反查機器（「我的機器」）', '通過'],
          ['10', '事後調整規格（Day-2 Resize）不需關機', '通過'],
          ['11', '申請表單必填與格式驗證', '通過'],
          ['12', '藍圖層級 metadata 可否由 API 查詢', '不支援（見 5.1）'],
        ],
        [700, 7180, 2200],
      ),


      // ---------------------------------------------------------------- 四
      H1('四、詳細驗證紀錄'),

      H2('4.1　藍圖欄位設計'),
      P('藍圖僅宣告五個 input：四個決策欄位與一個 attributes 物件。'
        + '所有描述性欄位以該物件整包傳入，藍圖不需要知道其中有哪些鍵值。'),
      Mono('inputs:  os / size / cluster / zone / attributes (type: object)'),
      P('測試時刻意在 attributes 內加入一個藍圖從未定義的鍵 costCenter，'
        + '部署完成後該鍵仍原樣出現在虛擬機屬性上，證實此設計可在不修改藍圖的情況下增減欄位。'),
      Note('vRA 回傳時會將該物件序列化為 JSON 字串，讀取端需自行解析。此行為已於程式中處理。'),

      H2('4.2　端到端部署'),
      P('以最終版藍圖送出一次完整申請，輸入與結果如下：'),
      Mono('inputs: os=ubuntu, size=small, cluster=linux-test, zone=oa'),
      Mono('attributes: requester / employeeId / department / ticket / purpose / purposeNote'),
      P('結果：'),
      Bullet('部署狀態 CREATE_SUCCESSFUL'),
      Bullet('取得靜態 IP 10.0.0.215，ping 正常'),
      Bullet('size=small 換算為 1 vCPU / 2048 MB，與 flavor mapping 一致'),
      Bullet('cluster=linux-test 落在對應叢集；zone=oa 落在對應網段'),
      Bullet('六個自訂屬性全數原樣回傳，含 AD 帶入的 department'),

      H2('4.3　放置決策的正反驗證'),
      P('為確認網域區隔確實由標記決定、而非碰巧落點正確，另做反向測試：'
        + '指定一個沒有任何網段掛上對應標記的區隔（FDC），系統於放置階段即中止，回報訊息為'),
      Mono('Could not find any profile to match network of type EXISTING with constraints [zone:fdc]', { color: WARN }),
      P('此結果具有兩層意義：其一，標記比對確實生效；其二，設定不完整時系統會明確拒絕，'
        + '不會退而求其次將機器放到其他網段。對於 MES／FDC／OA 的網段隔離要求而言，此行為是必要的。'),

      H2('4.4　vCenter 自訂屬性寫入與反查'),
      P('已確認 vRA 的自訂屬性不會自動同步至 vCenter：由藍圖部署出來的虛擬機，'
        + 'vCenter 端自訂屬性為空。因此改由申請入口在部署完成後主動寫入。'),
      P('本次以 vCenter 既有欄位進行驗證（未新建任何自訂屬性定義）：'),
      table(
        ['申請欄位', 'vCenter 自訂屬性', '寫入值'],
        [
          ['requester', 'customOwner', 'kosten.yang@vsmc.local'],
          ['department', 'customDepartment', 'Information Technology'],
          ['purpose / ticket', 'APSystem', 'test / REQ0012345'],
        ],
        [2600, 3200, 4280],
      ),
      P('寫入後由 vCenter 端獨立讀回，數值一致。接著以 customOwner 反查，'
        + '正確取得該使用者名下的機器清單，證實「我的機器」與稽核查詢可完全以 vCenter 為依據，'
        + '中介層不需要自行維護資料庫。', { after: 60 }),

      H2('4.5　事後調整規格（Day-2 Resize）'),
      P('於已上線的虛擬機執行 Resize 動作，將規格由 1 vCPU / 2 GB 調整為 2 vCPU / 4 GB：'),
      Bullet('約 30 秒完成，虛擬機全程維持 poweredOn，未重新開機'),
      Bullet('vCenter 端複核確認硬體確實變更（numCPU=2、memoryMB=4096）'),
      Bullet('該動作雖宣告接受 cpuCount／totalMemoryMB，實測傳入 flavor 名稱亦可，由平台換算'),
      P('最後一點的實務意義在於：調整規格可沿用申請表單同一份規格選單，'
        + '使用者無須輸入數字，也不會出現不合理的組合。'),

      H2('4.6　申請表單'),
      P('表單依欄位定義檔動態產生，驗證規則亦由該檔驅動。測試結果：'),
      table(
        ['測試情境', '預期', '實際結果'],
        [
          ['必填欄位留白送出', '擋下並提示', '通過'],
          ['申請單號格式不符', '擋下並說明格式', '通過'],
          ['正常送出', '顯示進度並回報機器名稱與 IP', '通過'],
          ['選擇無對應網段的區隔', '失敗並說明原因', '通過'],
          ['調整規格（可調版）', '規格更新且不需重開機', '通過'],
        ],
        [3600, 3400, 3080],
      ),


      // ---------------------------------------------------------------- 五
      H1('五、已知限制與待確認事項'),

      H2('5.1　藍圖層級 metadata 無法由 API 查詢'),
      P('藍圖頂層的 metadata 區塊需 formatVersion 2 才支援，內容會被完整保存，'
        + '但實測 blueprint、versions 與 catalog 三支 API 均不回傳該區塊，'
        + '取用者只能自行下載藍圖內容再解析 YAML，亦無法用於搜尋或篩選。'),
      P('因此本設計未將任何需要被程式取用的資訊放在 metadata，'
        + '改以 attributes 物件承載，並回寫至 vCenter 自訂屬性。'),

      H2('5.2　自訂屬性僅涵蓋經由本入口申請的機器'),
      P('屬性由申請入口在部署完成後寫入，因此若有人直接於平台部署藍圖，該機器不會有自訂屬性。'
        + '若客戶要求不論從何處部署皆須寫入，需改以平台的擴充動作（ABX）或 Orchestrator 實作，'
        + '代價是平台端需額外維護一組動作與事件訂閱。'),

      H2('5.3　尚未於客戶環境驗證之項目'),
      Bullet('NSX segment 是否出現在平台的網路清單中：本次驗證使用的是一般 vDS port group，'
        + '客戶環境為 NSX VLAN segment，需確認其可被平台辨識並打上標記'),
      Bullet('AD 整合：本次以固定測試資料模擬，實際需確認員工編號所在的 AD 屬性名稱'
        + '（employeeID 或 employeeNumber，亦可能為自訂欄位）'),
      Bullet('Windows 範本的規格熱加：本次以 Linux 範本驗證成功，'
        + 'Windows 通常需先啟用 CPU 與記憶體 hot-add，建議各標準範本各測一次'),
      Bullet('本報告所述之申請入口程式尚未於實際主機執行，僅驗證其所依賴的平台行為'),

      H2('5.4　待客戶決定事項'),
      Bullet('自訂屬性欄位清單：目前為六項，實際需要哪些由客戶決定'),
      Bullet('vCenter 自訂屬性名稱：建議對應客戶既有欄位，避免新建'),
      Bullet('規格是否開放事後調整；若開放，是否設定上限或簽核門檻'),
      Bullet('申請單號格式，以便設定驗證規則'),

      // ---------------------------------------------------------------- 六
      H1('六、上線前置作業'),
      P('平台端需完成下列設定，申請入口方能正常運作：'),
      table(
        ['項目', '說明'],
        [
          ['叢集標記', '每個開放申請的叢集掛上 cluster:<值> 標記'],
          ['網段標記', '每個開放的網域區隔掛上 zone:<值> 標記，未標記者無法申請'],
          ['規格對照', '建立 flavor mapping，決定各級規格的 vCPU 與記憶體'],
          ['範本對照', '建立 image mapping，決定各作業系統對應的範本'],
          ['IP 位址範圍', '各網段設定網段資訊並建立 IP range，供平台自動配發'],
          ['VMware Tools', '各範本需安裝，靜態 IP 方能透過 guest customization 寫入'],
          ['自訂屬性欄位', '確認 vCenter 上對應欄位已存在，或允許自動建立'],
          ['服務帳號', '建議另建專用帳號，勿使用管理者帳號'],
        ],
        [2600, 7480],
      ),

      // ---------------------------------------------------------------- 七
      H1('七、結論'),
      P('本次 POC 驗證的核心問題已獲得肯定答案：申請資訊可在不修改藍圖的前提下增減，'
        + '並完整落到 vCenter 自訂屬性供後續查詢與稽核。'),
      P('架構上的關鍵在於區分兩類欄位 —— 決定機器樣貌與位置的「決策欄位」各自對應一個藍圖輸入；'
        + '描述機器由誰、為何而開的「屬性欄位」則合併為單一物件傳遞。'
        + '此區分使得日後新增欄位的成本僅為修改一份設定檔，藍圖與程式均無須異動。'),
      P('放置決策的正反測試亦確認：設定不完整時系統會明確拒絕而非誤放，'
        + '此行為對 MES／FDC／OA 的網段隔離要求而言是必要條件。'),
      Note('後續建議：先確認第五節「待客戶決定事項」四項，'
        + '再於客戶環境完成第六節前置作業，即可進入實際導入階段。'),
    ],
  }],
});

await fs.writeFile('VSMC-POC-Report.docx', await Packer.toBuffer(doc));
console.log('OK -> VSMC-POC-Report.docx');
