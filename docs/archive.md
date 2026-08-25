# 历史代码归档

正式 `main` 只保留当前论文 Pipeline。整理前的代码没有删除，保存在：

```text
branch: archive/pre-pipeline-cleanup-20260825
commit: c9fb1da
```

归档包括旧 Neo4j/CodeQL 流程、历史 retriever、coverage 动态映射、实验脚本、
ground truth 审核数据、生成结果和阶段性文档。

服务器归档：

```text
worktree: /home/user/neo4j/archives/worktrees/Code2Graph-pre-pipeline-cleanup-20260825
bundle:   /home/user/neo4j/archives/Code2Graph-pre-pipeline-cleanup-20260825.bundle
```

本地归档：

```text
worktree: D:\Code\Code2Graph-archive-20260825
branch:   archive/local-pre-pipeline-cleanup-20260825
commit:   c5db126
bundle:   D:\Code\Code2Graph-workspace\Code2Graph-local-pre-pipeline-cleanup-20260825.bundle
```

本地数据集、虚拟环境、历史索引和输出统一保存在：

```text
D:\Code\Code2Graph-workspace
```

查阅单个历史文件：

```bash
git show archive/pre-pipeline-cleanup-20260825:path/to/file
```

恢复单个文件到当前工作区：

```bash
git restore --source archive/pre-pipeline-cleanup-20260825 -- path/to/file
```

恢复后应重新运行主测试，确认历史组件没有重新引入旧依赖或生成产物。
