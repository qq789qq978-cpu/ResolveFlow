# 架构图源文件与重绘

`resolveflow.mmd`是主图内容来源，`mermaid-config.json`控制样式；SVG和PNG由同一源文件导出。详细解释和代码入口见[ARCHITECTURE.md](../../ARCHITECTURE.md)。SVG便于缩放，PNG便于分享；当前中文字体使用Microsoft YaHei，换平台应安装Noto Sans CJK SC等中文字体并再次目视核对。

本次使用Mermaid CLI 11.12.0及本机Chrome，仅渲染本地文档。工具依赖安装在被忽略的`work/step41`中，不改变应用依赖、镜像或运行环境。需要Node.js、pnpm及一个本机Chromium浏览器。示例（PowerShell，浏览器路径按实际安装修改）：

```powershell
New-Item -ItemType Directory -Path work/step41 -Force
pnpm --dir work/step41 add --save-exact --ignore-scripts @mermaid-js/mermaid-cli@11.12.0
'{"executablePath":"C:/Program Files/Google/Chrome/Application/chrome.exe","args":["--disable-background-networking"]}' | Set-Content -Encoding utf8 work/step41/puppeteer.json
node work/step41/node_modules/@mermaid-js/mermaid-cli/src/cli.js -i docs/architecture/resolveflow.mmd -o docs/architecture/resolveflow.svg -c docs/architecture/mermaid-config.json -p work/step41/puppeteer.json -b white -w 2200 -H 1800
node work/step41/node_modules/@mermaid-js/mermaid-cli/src/cli.js -i docs/architecture/resolveflow.mmd -o docs/architecture/resolveflow.png -c docs/architecture/mermaid-config.json -p work/step41/puppeteer.json -b white -w 2200 -H 1800 -s 1.5
```

安装时`--ignore-scripts`跳过浏览器自动下载，渲染使用指定的本机浏览器。临时工具链不是应用的锁定依赖；CLI传递依赖或浏览器版本变化可能改变布局，不承诺逐字节再现。修改主图后同时导出两种格式，检查中文、箭头、标签重叠及边界标注，并更新4.1验证记录中的源文件/导出哈希。勿手改导出的SVG来制造与Mermaid内容不一致的图。
