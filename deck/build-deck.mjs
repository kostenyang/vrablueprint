import fs from 'node:fs/promises';
import JSZip from 'jszip';

const zip = await JSZip.loadAsync(await fs.readFile('base.pptx'));

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const read = (p) => zip.file(p).async('string');
const put = (p, s) => zip.file(p, s);
const shapes = (xml) => [...xml.matchAll(/<p:(sp|graphicFrame|pic)>[\s\S]*?<\/p:\1>/g)];

function setText(xml, shapeIdx, paragraphs) {
  const sp = shapes(xml)[shapeIdx];
  if (!sp) throw new Error(`shape ${shapeIdx} 不存在`);
  const body = sp[0].match(/<p:txBody>[\s\S]*?<\/p:txBody>/)?.[0];
  if (!body) throw new Error(`shape ${shapeIdx} 沒有 txBody`);
  const firstP = body.match(/<a:p>[\s\S]*?<\/a:p>/)?.[0] ?? '';
  const pPr = firstP.match(/<a:pPr[^>]*\/>|<a:pPr[^>]*>[\s\S]*?<\/a:pPr>/)?.[0] ?? '';
  const rPr = firstP.match(/<a:rPr[^>]*\/>|<a:rPr[^>]*>[\s\S]*?<\/a:rPr>/)?.[0]
            ?? '<a:rPr lang="zh-TW" dirty="0"/>';
  const mkP = (line) => {
    let lvl = 0, txt = line;
    while (txt.startsWith('- ')) { lvl++; txt = txt.slice(2); }
    let p = pPr.replace(/\s*lvl="\d+"/, '');
    if (lvl > 0) p = p ? p.replace(/<a:pPr/, `<a:pPr lvl="${lvl}"`) : `<a:pPr lvl="${lvl}"/>`;
    return `<a:p>${p}<a:r>${rPr}<a:t>${esc(txt)}</a:t></a:r></a:p>`;
  };
  const cut = body.indexOf('<a:p>');
  const head = cut >= 0 ? body.slice(0, cut) : body.slice(0, body.length - '</p:txBody>'.length);
  return xml.replace(sp[0], sp[0].replace(body, head + paragraphs.map(mkP).join('') + '</p:txBody>'));
}

const removePics = (xml) => xml.replace(/<p:pic>[\s\S]*?<\/p:pic>/g, '');

function setTable(xml, rows) {
  const tbl = xml.match(/<a:tbl>[\s\S]*?<\/a:tbl>/)[0];
  const trs = [...tbl.matchAll(/<a:tr [\s\S]*?<\/a:tr>/g)].map(m => m[0]);
  if (rows.length > trs.length) throw new Error(`表格只有 ${trs.length} 列，需要 ${rows.length}`);
  // 範本那張表每一格的格式不一致（交錯底色、部分淡色、部分粗體）。
  // 沿用各格原本的格式會讓換完字之後看起來很亂，所以標題列與內文列
  // 各取一個基準格式，整列套用。
  const refOf = (tr, idx = 0) => {
    const tc = [...tr.matchAll(/<a:tc(?: [^>]*)?>[\s\S]*?<\/a:tc>/g)].map(m => m[0])[idx] ?? '';
    const p = tc.match(/<a:p>[\s\S]*?<\/a:p>/)?.[0] ?? '';
    return {
      pPr: p.match(/<a:pPr[^>]*\/>|<a:pPr[^>]*>[\s\S]*?<\/a:pPr>/)?.[0] ?? '',
      rPr: p.match(/<a:rPr[^>]*\/>|<a:rPr[^>]*>[\s\S]*?<\/a:rPr>/)?.[0] ?? '<a:rPr lang="en-US" dirty="0"/>',
    };
  };
  const headRef = refOf(trs[0]);
  const bodyRef = refOf(trs[1] ?? trs[0]);

  const rebuilt = rows.map((cells, r) => {
    let tr = trs[r];
    const ref = r === 0 ? headRef : bodyRef;
    const tcs = [...tr.matchAll(/<a:tc(?: [^>]*)?>[\s\S]*?<\/a:tc>/g)].map(m => m[0]);
    cells.forEach((val, c) => {
      const tc = tcs[c];
      if (!tc) return;
      const body = tc.match(/<a:txBody>[\s\S]*?<\/a:txBody>/)[0];
      const head = body.slice(0, body.indexOf('<a:p>'));
      const nb = `${head}<a:p>${ref.pPr}<a:r>${ref.rPr}<a:t>${esc(val)}</a:t></a:r></a:p></a:txBody>`;
      tr = tr.replace(tc, tc.replace(body, nb));
    });
    return tr;
  });
  return xml.replace(tbl, tbl.replace(trs.join(''), rebuilt.join('')));
}

/** 只留下第一個圖框，其餘刪掉（有些版型原本放了兩三張小圖）。 */
function keepFirstPic(xml) {
  const pics = [...xml.matchAll(/<p:pic>[\s\S]*?<\/p:pic>/g)].map(m => m[0]);
  pics.slice(1).forEach(p => { xml = xml.replace(p, ''); });
  return xml;
}

/** 換掉既有圖框的圖檔，依實際比例調整高度避免變形；必要時可指定新的垂直位置。 */
async function swapPicture(xml, slideNum, file, aspect, offsetYInch) {
  const rels = await read(`ppt/slides/_rels/slide${slideNum}.xml.rels`);
  const embed = xml.match(/<p:pic>[\s\S]*?r:embed="([^"]+)"/)?.[1];
  if (!embed) throw new Error(`slide${slideNum} 沒有圖框`);
  const target = rels.match(new RegExp(`Id="${embed}"[^>]*Target="([^"]+)"`))?.[1];
  put('ppt/' + target.replace('../', ''), await fs.readFile(file));
  const pic = xml.match(/<p:pic>[\s\S]*?<\/p:pic>/)[0];
  let fixed = pic;
  const ext = pic.match(/<a:ext cx="(\d+)" cy="(\d+)"\/>/);
  if (ext) {
    const cx = +ext[1];
    fixed = fixed.replace(ext[0], `<a:ext cx="${cx}" cy="${Math.round(cx / aspect)}"/>`);
  }
  if (offsetYInch != null) {
    const off = fixed.match(/<a:off x="(-?\d+)" y="(-?\d+)"\/>/);
    if (off) fixed = fixed.replace(off[0], `<a:off x="${off[1]}" y="${Math.round(offsetYInch * 914400)}"/>`);
  }
  return xml.replace(pic, fixed);
}

/** 給複製出來的投影片一份專屬圖檔。
 *  clone 會沿用原投影片的圖片關聯，若直接換檔會連原投影片的圖一起蓋掉。 */
let mediaSeq = 0;
async function swapPictureUnique(xml, slideNum, file, aspect, offsetYInch) {
  const relPath = `ppt/slides/_rels/slide${slideNum}.xml.rels`;
  const rels = await read(relPath);
  const embed = xml.match(/<p:pic>[\s\S]*?r:embed="([^"]+)"/)?.[1];
  if (!embed) throw new Error(`slide${slideNum} 沒有圖框`);

  const name = `custom${++mediaSeq}.png`;
  put(`ppt/media/${name}`, await fs.readFile(file));

  // 逐一比對每個 Relationship，屬性順序不固定，不能假設 Id 一定在 Target 前面
  let replaced = false;
  const newRels = rels.replace(/<Relationship\b[^>]*\/>/g, (tag) => {
    if (tag.match(/Id="([^"]+)"/)?.[1] !== embed) return tag;
    replaced = true;
    return tag.replace(/Target="[^"]*"/, `Target="../media/${name}"`);
  });
  if (!replaced) throw new Error(`slide${slideNum}: 找不到 ${embed} 的關聯，圖片不會被換掉`);
  put(relPath, newRels);

  const pic = xml.match(/<p:pic>[\s\S]*?<\/p:pic>/)[0];
  let fixed = pic;
  const ext = pic.match(/<a:ext cx="(\d+)" cy="(\d+)"\/>/);
  if (ext) fixed = fixed.replace(ext[0], `<a:ext cx="${ext[1]}" cy="${Math.round(+ext[1] / aspect)}"/>`);
  if (offsetYInch != null) {
    const off = fixed.match(/<a:off x="(-?\d+)" y="(-?\d+)"\/>/);
    if (off) fixed = fixed.replace(off[0], `<a:off x="${off[1]}" y="${Math.round(offsetYInch * 914400)}"/>`);
  }
  return xml.replace(pic, fixed);
}

let seq = 900;
async function cloneSlide(src) {
  const n = ++seq;
  put(`ppt/slides/slide${n}.xml`, await read(`ppt/slides/slide${src}.xml`));
  put(`ppt/slides/_rels/slide${n}.xml.rels`,
    (await read(`ppt/slides/_rels/slide${src}.xml.rels`))
      .replace(/<Relationship [^>]*Type="[^"]*\/notesSlide"[^>]*\/>/g, ''));
  put('[Content_Types].xml', (await read('[Content_Types].xml')).replace('</Types>',
    `<Override PartName="/ppt/slides/slide${n}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>`));
  return n;
}

/** 把圖放大到指定寬度並置中，位置與大小完全覆蓋範本原本的小圖框。 */
const IN = 914400;
const SLIDE_W = 13.33, SLIDE_H = 7.5, BOTTOM_MARGIN = 0.22;
async function placePicture(xml, slideNum, file, aspect, { widthIn = 10.4, topIn = 1.3, unique = false } = {}) {
  xml = unique
    ? await swapPictureUnique(xml, slideNum, file, aspect)
    : await swapPicture(xml, slideNum, file, aspect);

  // 先照寬度算，超出投影片底部就改以可用高度為準，避免圖被切掉
  let w = widthIn;
  const maxH = SLIDE_H - BOTTOM_MARGIN - topIn;
  if (w / aspect > maxH) w = maxH * aspect;

  const pic = xml.match(/<p:pic>[\s\S]*?<\/p:pic>/)[0];
  const cx = Math.round(w * IN);
  const cy = Math.round(cx / aspect);
  const x = Math.round((SLIDE_W - w) / 2 * IN);          // 水平置中
  const fixed = pic
    .replace(/<a:off x="(-?\d+)" y="(-?\d+)"\/>/, `<a:off x="${x}" y="${Math.round(topIn * IN)}"/>`)
    .replace(/<a:ext cx="(\d+)" cy="(\d+)"\/>/, `<a:ext cx="${cx}" cy="${cy}"/>`);
  return xml.replace(pic, fixed);
}

/** 把用不到的文字框整個移除，避免空版面留下佔位字。 */
function dropShape(xml, shapeIdx) {
  const sp = shapes(xml)[shapeIdx];
  return sp ? xml.replace(sp[0], '') : xml;
}

/** 這些版型本來是「左文字右圖」，拿掉圖之後右半邊會空著，
 *  所以把文字框拉寬到整頁；不動垂直位置。 */
function widenShape(xml, shapeIdx, widthIn = 11.6) {
  const sp = shapes(xml)[shapeIdx];
  if (!sp) return xml;
  const xfrm = sp[0].match(/<a:xfrm[\s\S]*?<\/a:xfrm>/)?.[0];
  if (!xfrm) return xml;
  const ext = xfrm.match(/<a:ext cx="(\d+)" cy="(\d+)"\/>/);
  if (!ext) return xml;
  const fixed = xfrm.replace(ext[0], `<a:ext cx="${Math.round(widthIn * IN)}" cy="${ext[2]}"/>`);
  return xml.replace(sp[0], sp[0].replace(xfrm, fixed));
}

// ================================================================ Slide content
const DATE = '2026-09-10';
const A = 1584 / 905;
const gotWhat = await cloneSlide(23);
const prereq  = await cloneSlide(24);
const mapping = await cloneSlide(8);
const rzOpts  = await cloneSlide(8);
const verify  = await cloneSlide(8);   // POC 驗證結果
const clusters= await cloneSlide(8);   // RVTools 18 個叢集
const checkl  = await cloneSlide(8);   // 上線前置作業  // resize: fixed vs changeable

// Picture slides: big image, short title, no body text.
// Text slides: at most four short lines, so PowerPoint never shrinks the font.
const plan = [
  [1, async (x) => {
    x = setText(x, 3, ['Requesting a Virtual Machine']);
    x = setText(x, 0, ['Self-service portal']);
    x = setText(x, 1, ['VSMC']);
    return setText(x, 2, [DATE]);
  }],

  [2, async (x) => setText(x, 0, ['When you need a machine…'])],

  [3, async (x) => {
    x = removePics(x);
    x = setText(x, 0, ['Today: you wait on four people']);
    return setText(x, 1, [
      'Your manager approves it.',
      'IT decides the cluster, network and size.',
      'IT builds it by hand and assigns an IP.',
      'Someone updates the ticket afterwards.',
    ]);
  }],

  // ---- form screenshot ----
  [16, async (x) => {
    x = await placePicture(x, 16, 'img/form-design.png', A, { widthIn: 11.4, topIn: 1.3 });
    x = setText(x, 0, ['Instead: one form']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  [23, async (x) => {
    x = removePics(x);
    x = setText(x, 0, ['Four things you choose']);
    x = setText(x, 1, ['All of them dropdowns']);
    x = widenShape(x, 2);
    return setText(x, 2, [
      'Operating system and size.',
      'Cluster — where it runs.',
      'Network zone — MES, FDC or OA.',
      'A zone works once its network is tagged; until then the request stops with a reason.',
    ]);
  }],

  [24, async (x) => {
    x = removePics(x);
    x = setText(x, 0, ['Three things you never fill in']);
    x = setText(x, 1, ['Taken from your company account']);
    x = widenShape(x, 2);
    return setText(x, 2, [
      'Requester, employee ID, department.',
      'Nothing to retype, so nothing to mistype.',
      'Nobody can request in your name.',
      'The ticket number is the only thing you type.',
    ]);
  }],

  [7, async (x) => setText(x, 0, ['After you submit…'])],

  // ---- flow diagram ----
  [8, async (x) => {
    x = await placePicture(x, 8, 'img/flow.png', A, { widthIn: 11.4, topIn: 1.3 });
    x = setText(x, 0, ['What happens after you submit']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  // ---- result screenshot ----
  [gotWhat, async (x) => {
    x = keepFirstPic(x);
    x = await placePicture(x, gotWhat, 'img/form-filled.png', A, { widthIn: 11.4, topIn: 1.3, unique: true });
    x = setText(x, 0, ['Minutes later: name, IP, spec']);
    x = setText(x, 1, ['']);
    return dropShape(x, 3);
  }],

  [25, async (x) => {
    x = removePics(x);
    x = setText(x, 0, ['What you entered stays with the machine']);
    x = setText(x, 1, ['']);
    x = setText(x, 2, ['Who you are']);
    x = setText(x, 3, ['Which ticket']);
    x = setText(x, 4, ['What it is for']);
    x = setText(x, 5, ['Requester, employee ID, department.', 'Someone to ask at review time.']);
    x = setText(x, 6, ['The request ticket.', 'An audit goes from ticket to machines.']);
    return setText(x, 7, ['Purpose and notes.', 'Still clear six months later.']);
  }],

  // ---- vRA screen ----
  [12, async (x) => {
    x = await placePicture(x, 12, 'img/bp-editor.png', A, { widthIn: 11.4, topIn: 1.3 });
    x = setText(x, 0, ['Behind it: the platform definition']);
    return dropShape(x, 1);
  }],

  // ---- full YAML ----
  [9, async (x) => {
    x = await placePicture(x, 9, 'img/yaml.png', A, { widthIn: 11.4, topIn: 1.3 });
    x = setText(x, 0, ['The whole definition is this short']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  // ---- mapping diagram ----
  [mapping, async (x) => {
    x = await placePicture(x, mapping, 'img/mapping.png', A, { widthIn: 12.4, topIn: 1.3, unique: true });
    x = setText(x, 0, ['How the form maps to the platform']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  [5, async (x) => {
    x = setText(x, 0, ['Where each field ends up']);
    x = setText(x, 1, ['']);
    x = setTable(x, [
      ['Form field', 'Filled by', 'Stored as', 'vCenter attribute', 'Platform change?'],
      ['Requester', 'From AD', 'VM attribute', 'customOwner', 'No'],
      ['Employee ID', 'From AD', 'VM attribute', 'customEmployeeId', 'No'],
      ['Department', 'From AD', 'VM attribute', 'customDepartment', 'No'],
      ['Request ticket', 'You', 'VM attribute', 'customTicket', 'No'],
      ['Purpose', 'You', 'VM attribute', 'customPurpose', 'No'],
      ['Notes', 'You', 'VM attribute', 'customNote', 'No'],
      ['Operating system', 'You', 'The machine', '—', 'Yes'],
      ['Size', 'You', 'The machine', '—', 'Yes'],
      ['Cluster', 'You', 'Where it runs', '—', 'Yes'],
      ['Network zone', 'You', 'Which network', '—', 'Yes'],
    ]);
    return dropShape(x, 3);
  }],

  [rzOpts, async (x) => {
    x = await placePicture(x, rzOpts, 'img/resize-options.png', A, { widthIn: 12.4, unique: true });
    x = setText(x, 0, ['Can the size be changed afterwards?']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  [verify, async (x) => {
    x = await placePicture(x, verify, 'img/verify.png', A, { widthIn: 12.4, unique: true });
    x = setText(x, 0, ['What we verified on the platform']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  [clusters, async (x) => {
    x = await placePicture(x, clusters, 'img/clusters.png', A, { widthIn: 12.4, unique: true });
    x = setText(x, 0, ['Which clusters do you want to offer?']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  [10, async (x) => setText(x, 0, ['Before you start…'])],

  [checkl, async (x) => {
    x = await placePicture(x, checkl, 'img/checklist.png', A, { widthIn: 12.4, unique: true });
    x = setText(x, 0, ['The setup checklist']);
    x = setText(x, 1, ['']);
    return dropShape(x, 2);
  }],

  [prereq, async (x) => {
    x = removePics(x);
    x = setText(x, 0, ['What the platform team sets up first']);
    x = setText(x, 1, ['One-off preparation']);
    x = widenShape(x, 2);
    return setText(x, 2, [
      'Tag every cluster and NSX VLAN segment to be offered.',
      'Each zone needs a tagged segment before it can be used.',
      'Add size and OS mappings, and an IP range per segment.',
      'Agree the ticket format and the list of details to record.',
    ]);
  }],

  [26, async (x) => x],
];
// ================================================================ Assemble
const order = [];
for (const [src, edit] of plan) {
  const path = `ppt/slides/slide${src}.xml`;
  put(path, await edit(await read(path)));
  order.push(src);
}

const all = Object.keys(zip.files)
  .filter(n => /^ppt\/slides\/slide\d+\.xml$/.test(n)).map(n => +n.match(/\d+/)[0]);
for (const n of all) {
  if (order.includes(n)) continue;
  zip.remove(`ppt/slides/slide${n}.xml`);
  zip.remove(`ppt/slides/_rels/slide${n}.xml.rels`);
}

const orphanNotes = [];
for (const n of Object.keys(zip.files).filter(p => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(p))) {
  const relPath = n.replace('notesSlides/', 'notesSlides/_rels/') + '.rels';
  const relFile = zip.file(relPath);
  if (!relFile) continue;
  const target = (await relFile.async('string')).match(/Target="\.\.\/slides\/(slide\d+\.xml)"/)?.[1];
  if (target && !zip.file(`ppt/slides/${target}`)) {
    zip.remove(n); zip.remove(relPath); orphanNotes.push(n);
  }
}

let ct = await read('[Content_Types].xml');
for (const n of all) {
  if (order.includes(n)) continue;
  ct = ct.replace(new RegExp(`<Override PartName="/ppt/slides/slide${n}\\.xml"[^>]*/>`, 'g'), '');
}
for (const n of orphanNotes) {
  ct = ct.replace(new RegExp(`<Override PartName="/${n.replace(/\//g, '\\/')}"[^>]*/>`, 'g'), '');
}
put('[Content_Types].xml', ct);

let rels = await read('ppt/_rels/presentation.xml.rels');
rels = rels.replace(/<Relationship [^>]*Type="[^"]*\/slide"[^>]*\/>/g, '');
rels = rels.replace('</Relationships>', order.map((n, i) =>
  `<Relationship Id="rIdSlide${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide${n}.xml"/>`
).join('') + '</Relationships>');
put('ppt/_rels/presentation.xml.rels', rels);

let pres = await read('ppt/presentation.xml');
pres = pres.replace(/<p:sldIdLst>[\s\S]*?<\/p:sldIdLst>/,
  `<p:sldIdLst>${order.map((_, i) => `<p:sldId id="${300 + i}" r:id="rIdSlide${i + 1}"/>`).join('')}</p:sldIdLst>`);
put('ppt/presentation.xml', pres);

const out = await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
await fs.writeFile('VSMC-VM-Portal-EN.pptx', out);
console.log(`OK  ${order.length} slides  ->  VSMC-VM-Portal-EN.pptx  (${(out.length / 1024 / 1024).toFixed(1)} MB)`);
