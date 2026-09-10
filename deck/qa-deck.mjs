import fs from 'node:fs/promises';
import JSZip from 'jszip';

const zip = await JSZip.loadAsync(await fs.readFile(process.argv[2] ?? 'VSMC-VM-Portal.pptx'));
const names = Object.keys(zip.files);
let bad = 0;

// 1) 每個 slide rels 指到的檔案都要存在
for (const n of names.filter(n => /_rels\/.*\.rels$/.test(n))) {
  const xml = await zip.file(n).async('string');
  const dir = n.replace(/_rels\/[^/]+$/, '');
  for (const m of xml.matchAll(/Target="([^"]+)"(?:\s+TargetMode="External")?/g)) {
    const t = m[1];
    if (/^https?:|^\.\.\/media\/|TargetMode/.test(t)) continue;
    const resolved = new URL(t, 'file:///' + dir).pathname.slice(1);
    if (!zip.file(resolved) && !/^\.\./.test(t)) { console.log(`MISSING ${n} -> ${t}`); bad++; }
  }
}

// 2) notesSlide 若指向已刪除的 slide 會讓檔案打不開
const notes = names.filter(n => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(n));
for (const n of notes) {
  const rels = zip.file(n.replace('notesSlides/', 'notesSlides/_rels/') + '.rels');
  if (!rels) continue;
  const xml = await rels.async('string');
  const target = xml.match(/Target="\.\.\/slides\/(slide\d+\.xml)"/)?.[1];
  if (target && !zip.file(`ppt/slides/${target}`)) { console.log(`ORPHAN NOTE ${n} -> ${target}`); bad++; }
}

// 3) presentation.xml 的 rId 都要在 rels 裡
const pres = await zip.file('ppt/presentation.xml').async('string');
const prels = await zip.file('ppt/_rels/presentation.xml.rels').async('string');
const relIds = new Set([...prels.matchAll(/Id="([^"]+)"/g)].map(m => m[1]));
for (const m of pres.matchAll(/r:id="([^"]+)"/g)) {
  if (!relIds.has(m[1])) { console.log(`DANGLING rId ${m[1]} in presentation.xml`); bad++; }
}

// 4) Content_Types 要涵蓋每個 slide
const ct = await zip.file('[Content_Types].xml').async('string');
for (const n of names.filter(n => /^ppt\/slides\/slide\d+\.xml$/.test(n))) {
  if (!ct.includes(`PartName="/${n}"`)) { console.log(`NO CONTENT-TYPE ${n}`); bad++; }
}

// 5) XML well-formed 粗檢：標籤配對
for (const n of names.filter(n => n.endsWith('.xml'))) {
  const x = await zip.file(n).async('string');
  if (!x.startsWith('<?xml')) { console.log(`NOT XML ${n}`); bad++; }
}

console.log(bad === 0 ? 'QA PASS — 沒有發現結構問題' : `QA: ${bad} 個問題`);
