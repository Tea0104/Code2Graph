import python
import Extents

// 查询 Python 类节点，输出类名、文件和位置。

from RangeClass c, string file, int startLine, int endLine
where
  file = c.getLocation().getFile().getRelativePath() and
  startLine = c.getLocation().getStartLine() and
  endLine = c.getLocation().getEndLine()
select
  "Class" as kind,
  file,
  c.getName() as name,
  startLine,
  endLine
