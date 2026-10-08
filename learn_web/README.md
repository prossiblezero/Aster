# Aster 教程网站

网站从 `docs/learning/` 的 16 章 Markdown 和当前源码构建。仓库只维护构建器、样式、脚本和测试；生成的页面与源码副本不提交。

在仓库根目录运行：

```bash
python -m pip install -e '.[dev,docs]'
python learn_web/build.py
python -m pytest -q learn_web/tests
python -m http.server 8000 --directory learn_web
```

打开 <http://localhost:8000>。停止预览用 Ctrl+C。直接运行网站测试也会自动构建页面。

源文档导航见 [docs/README.md](../docs/README.md)，构建输出为本目录下的 `index.html`、`lessons/`、`source/` 和 `search.json`。构建不需要模型服务。
