import python

// 查询 Python 类节点，输出类名、文件和位置。

from Class c
select
  "Class" as kind,
  c.getLocation().getFile().getRelativePath() as file,
  c.getName() as name,
  c.getLocation().getStartLine() as startLine,
  c.getLocation().getEndLine() as endLine
