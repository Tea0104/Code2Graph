import python
import Extents

// 查询 Python 类节点，输出类名、文件和位置。

from RangeClass c, string file, int startLine, int endLine
where c.hasLocationInfo(file, startLine, _, endLine, _)
select
  "Class" as kind,
  file,
  c.getName() as name,
  startLine,
  endLine
