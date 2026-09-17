# Aster 教学网站

这是 Aster 学习课程的纯静态站点。正文由仓库根目录的 `docs/learning/00-*.md` 到 `15-*.md` 构建，生成结果包含 16 个章节页、课程引用文件的站内副本、带行号的 Python 源码页和搜索索引。构建过程不联网，也不依赖 Graphviz。

## 本地构建与预览

先进入 Aster 仓库根目录并激活项目环境：

```bash
conda activate aster
python website/build.py
python -m http.server 8000 --directory website
```

打开 <http://localhost:8000/>。直接通过 `file://` 打开生成的 HTML 时，正文和导航可以正常使用；完整搜索功能需要通过上述静态 HTTP 服务预览。站点中的 URL 都是相对当前页面的路径，因此本地预览和 GitHub Pages 项目子路径使用同一份构建结果，不需要设置或替换 `base`。

构建器需要 `markdown-it-py` 和 `pygments`。项目开发环境尚未安装它们时可执行：

```bash
python -m pip install 'markdown-it-py==4.2.0' 'pygments==2.21.0'
```

## 测试

每次修改构建器或课程来源后重新生成并测试：

```bash
conda activate aster
python website/build.py
python -m pytest -q website/tests
```

测试会遍历包括源码页在内的全部 HTML，检查本地 `href`/`src`、页面锚点、重复 ID、越界和根绝对路径，并逐块核对课程的普通代码、Mermaid 图和参考答案。构建器只向 `website/` 写入文件；课程引用采用站内副本，不会复制整个数据集。

## GitHub Pages 部署

仓库管理员先在 GitHub 的 **Settings > Pages > Build and deployment > Source** 中选择 **GitHub Actions**。不要选择 branch 发布，因为 GitHub Pages 的 branch 模式不能把任意 `website/` 子目录配置为发布根。

将 [`pages.yml.example`](pages.yml.example) 复制为仓库的 `.github/workflows/pages.yml`。模板会：

1. 使用固定 Python 版本和固定的站点构建依赖；
2. 运行 `python website/build.py`；
3. 运行 `python -m pytest -q website/tests`，测试失败时停止发布；
4. 通过 `configure-pages`、`upload-pages-artifact` 和 `deploy-pages` 发布 `website/`。

工作流仅需要读取仓库内容以及写入 Pages 和 OIDC token 的权限。模板在 `main` 和 `master` 分支推送时触发，不需要改站点 `base`。`.nojekyll` 会随 `website/` 一起发布。

源码页显示原始仓库相对路径，例如 `src/aster/runtime.py`，并为每行提供可链接的 `#L<number>` 锚点。它们是构建生成的教学副本；源码修改应在仓库原文件中完成，再重新构建站点。网页不执行真实 Agent，教程中的 Python 实验仍需在 Aster 环境运行。
