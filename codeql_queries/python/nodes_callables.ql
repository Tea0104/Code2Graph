import python

// 查询 Python 函数和方法节点，输出调用体所在文件、名称和位置。

predicate callableNode(string kind, string file, string name, int startLine, int endLine) {
  exists(Function c |
    c.isMethod() and
    kind = "Method" and
    file = c.getLocation().getFile().getRelativePath() and
    name = c.getName() and
    startLine = c.getLocation().getStartLine() and
    endLine = c.getLocation().getEndLine()
  )
  or
  exists(Function c |
    not c.isMethod() and
    kind = "Function" and
    file = c.getLocation().getFile().getRelativePath() and
    name = c.getName() and
    startLine = c.getLocation().getStartLine() and
    endLine = c.getLocation().getEndLine()
  )
}

from string kind, string file, string name, int startLine, int endLine
where callableNode(kind, file, name, startLine, endLine)
select kind, file, name, startLine, endLine
