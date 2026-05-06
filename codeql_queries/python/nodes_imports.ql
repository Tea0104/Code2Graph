import python

// 查询 Python 导入语句节点，输出导入项的文件、名称和位置。

from Import i
select
  "Import" as kind,
  i.getLocation().getFile().getRelativePath() as file,
  i.toString() as name,
  i.getLocation().getStartLine() as startLine,
  i.getLocation().getEndLine() as endLine
