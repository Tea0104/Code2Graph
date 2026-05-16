import python
import Extents

// 查询 Python 函数和方法节点，输出调用体所在文件、名称和位置。

predicate callableNode(string kind, string file, string name, int startLine, int endLine) {
  exists(RangeFunction c |
    c.isMethod() and
    kind = "Method" and
    c.hasLocationInfo(file, startLine, _, endLine, _) and
    name = c.getName()
  )
  or
  exists(RangeFunction c |
    not c.isMethod() and
    kind = "Function" and
    c.hasLocationInfo(file, startLine, _, endLine, _) and
    name = c.getName()
  )
}

from string kind, string file, string name, int startLine, int endLine
where callableNode(kind, file, name, startLine, endLine)
select kind, file, name, startLine, endLine
