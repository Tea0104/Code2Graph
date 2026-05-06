import python

// 查询 Python 函数参数节点，输出参数的文件、名称和行号。

from Parameter p
select
  "Parameter" as kind,
  p.getLocation().getFile().getRelativePath() as file,
  p.getName() as name,
  p.getLocation().getStartLine() as startLine,
  p.getLocation().getEndLine() as endLine
