// Create the Cloud Templates (blueprints) from ../blueprints/*.yaml
// Usage:  source env.sh && node 20-create-blueprints.js       (reads PROJECT_ID from scripts/.project)
const https = require('https');
const fs = require('fs');
const path = require('path');

const url = new URL(process.env.VRA_URL);
const HOST = url.hostname, PORT = url.port || 443;
const agent = new https.Agent({ rejectUnauthorized: false });
const PROJECT_ID = (fs.existsSync(path.join(__dirname, '.project'))
  ? fs.readFileSync(path.join(__dirname, '.project'), 'utf8').trim().split('=')[1]
  : process.env.PROJECT_ID);

function req(method, p, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    if (data) headers['Content-Length'] = Buffer.byteLength(data);
    const r = https.request({ host: HOST, port: PORT, method, path: p, headers, agent }, res => {
      let b = ''; res.on('data', c => b += c); res.on('end', () => resolve({ status: res.statusCode, body: b }));
    });
    r.on('error', reject); if (data) r.write(data); r.end();
  });
}
async function token() {
  let r = await req('POST', '/csp/gateway/am/api/login?access_token',
    { username: process.env.VRA_USER, password: process.env.VRA_PASS, domain: process.env.VRA_DOMAIN || 'System Domain' });
  const rt = JSON.parse(r.body).refresh_token;
  r = await req('POST', '/iaas/api/login', { refreshToken: rt });
  return JSON.parse(r.body).token;
}
(async () => {
  const tok = await token();
  const dir = path.join(__dirname, '..', 'blueprints');
  for (const file of fs.readdirSync(dir).filter(f => f.endsWith('.yaml'))) {
    const name = path.basename(file, '.yaml');
    const content = fs.readFileSync(path.join(dir, file), 'utf8');
    const r = await req('POST', '/blueprint/api/blueprints', { name, projectId: PROJECT_ID, content }, tok);
    let id = ''; try { id = JSON.parse(r.body).id; } catch (e) {}
    console.log(`${name}: HTTP ${r.status} ${id || r.body.slice(0, 140)}`);
  }
})();
