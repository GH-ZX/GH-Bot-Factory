/* Serve the actual static dependency graph for browser acceptance tests. */
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
exports.createServer=root=>http.createServer((req,res)=>{
  const url=new URL(req.url,'http://local');
  const match=url.pathname.match(/^\/(admin|miniapp|shared|setup|build)\/([a-zA-Z0-9_.-]*)$/);
  if(!match){res.writeHead(404);return res.end();}
  const app=match[1]==='build'?'configurator':match[1];
  const filename=match[2]||'index.html';
  const file=path.join(root,'apps',app,'static',filename);
  if(!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end();}
  const mime={'.js':'text/javascript','.css':'text/css','.json':'application/json','.html':'text/html','.svg':'image/svg+xml','.png':'image/png'};
  res.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');
  res.end(fs.readFileSync(file));
});
