import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const appRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(appRoot, "dist-app");
const dist = join(appRoot, "dist");
await rm(dist, { recursive: true, force: true });
await mkdir(join(dist, "public"), { recursive: true });
await mkdir(join(dist, "private"), { recursive: true });
await cp(source, join(dist, "public"), { recursive: true });
await cp(source, join(dist, "private"), { recursive: true });

const landing = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI软件自动点击器 · 英语学习助手</title>
  <style>
    body{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#f9f4e8,#e7f2f1);font-family:ui-sans-serif,"Microsoft YaHei",sans-serif;color:#17212b}
    main{width:min(720px,92vw);padding:28px;border-radius:22px;background:#fffdf7;box-shadow:0 20px 55px rgba(23,33,43,.14)}
    h1{font-size:clamp(30px,7vw,48px);margin:6px 0}
    p{line-height:1.6;color:#56636d}
    .cards{display:grid;gap:14px;margin-top:22px}
    a{display:block;padding:18px;border-radius:14px;text-decoration:none;color:inherit;border:1px solid #d8e1de;background:#fff}
    a:first-child{border-color:#b7d8cf;background:#eef8f5}
    strong{display:block;font-size:19px;margin-bottom:5px}
  </style>
</head>
<body>
  <main>
    <p>AI软件自动点击器</p>
    <h1>英语阅读写作助手</h1>
    <p>选择公开版或私人版。两个版本都可以安装到安卓桌面，并共享本地 Qwen 模型缓存。</p>
    <div class="cards">
      <a href="./public/"><strong>公开版</strong>无需密码，包含提炼后的阅读、写作方法摘要。</a>
      <a href="./private/"><strong>私人版</strong>需要课程密码，包含更完整的本地课程资料。</a>
    </div>
  </main>
</body>
</html>`;
await writeFile(join(dist, "index.html"), landing, "utf8");
await writeFile(join(dist, ".nojekyll"), "", "utf8");
console.log("Created public and private builds");
