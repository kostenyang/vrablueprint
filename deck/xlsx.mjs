import fs from 'node:fs/promises';
import JSZip from 'jszip';

const [file, sheetName, limit] = [process.argv[2], process.argv[3], +(process.argv[4] ?? 15)];
const zip = await JSZip.loadAsync(await fs.readFile(file));

const wb = await zip.file('xl/workbook.xml').async('string');
const rels = await zip.file('xl/_rels/workbook.xml.rels').async('string');
const relMap = Object.fromEntries(
  [...rels.matchAll(/Id="([^"]+)"[^>]*Target="([^"]+)"/g)].map(m => [m[1], m[2].replace(/^\/?xl\//, '')]));
const sheets = [...wb.matchAll(/<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"/g)]
  .map(m => ({ name: m[1], path: 'xl/' + relMap[m[2]] }));

const target = sheets.find(s => s.name === sheetName);
if (!target) { console.log('sheets:', sheets.map(s => s.name).join(', ')); process.exit(0); }

const ssXml = zip.file('xl/sharedStrings.xml') ? await zip.file('xl/sharedStrings.xml').async('string') : '';
const shared = [...ssXml.matchAll(/<si>([\s\S]*?)<\/si>/g)]
  .map(m => [...m[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map(x => x[1]).join(''));

const xml = await zip.file(target.path).async('string');
const rows = [...xml.matchAll(/<row[^>]*>([\s\S]*?)<\/row>/g)].slice(0, limit);

const colIndex = (ref) => {
  const letters = ref.match(/^[A-Z]+/)[0];
  return [...letters].reduce((n, ch) => n * 26 + (ch.charCodeAt(0) - 64), 0) - 1;
};

for (const r of rows) {
  const cells = [];
  for (const m of r[1].matchAll(/<c\b([^>]*)>([\s\S]*?)<\/c>|<c\b([^>]*)\/>/g)) {
    const attrs = m[1] ?? m[3] ?? '';
    const inner = m[2] ?? '';
    const ref = attrs.match(/r="([A-Z]+\d+)"/)?.[1];
    const type = attrs.match(/t="(\w+)"/)?.[1];
    let val = inner.match(/<v>([\s\S]*?)<\/v>/)?.[1] ?? '';
    if (type === 's') val = shared[+val] ?? '';
    else if (type === 'inlineStr') val = inner.match(/<t[^>]*>([\s\S]*?)<\/t>/)?.[1] ?? '';
    if (ref) cells[colIndex(ref)] = val;
  }
  console.log([...cells].map(c => c ?? '').join(' | '));
}
